# -*- coding: utf-8 -*-
"""主智能体 · 编排执行层：识别意图 → 规划链路 → 分派子智能体 → 结构化汇总。

与 `plan_service.dispatch()` 的分工（两者都在，别搞混）：
  · `dispatch()`  只出**调度方案说明**——命中哪些、按什么顺序、为什么，纯规则；
  · 本模块        真**执行**：按方案逐个调用子智能体的真实能力并汇总结果。

三条不可让步的约束（对应项目红线）：

1. **高危不进链路**。命中医疗干预/诊断/报告解读/孕婴时，在**分类阶段**就返回拒答，
   一次子智能体都不调用。这比"先路由、让下游各自拦"强：下游可能没拦，
   而且拦了也已经把数据送出去了。

2. **不让 LLM 二次编造**。汇总只做**结构化拼接**——每个子通道的原始返回、引用、
   耗时、降级原因原样带出。LLM 只在 `dietitian` 通道内部生成答案（那是它自己的职责，
   那一层已有拒答 + 相似度阈值 + 强制引用三重边界）。

3. **失败如实上报**。子通道抛异常就标 `ok=false` 并把原因带出来，绝不吞掉、
   绝不换一个看起来合理的结果顶上去。**降级要降得诚实**——
   "暂时查不到但这是检索到的资料" 和 "返回空白" 是两个完全不同的产品体验。

另外：**所有同步阻塞调用一律包 `asyncio.to_thread`**。子通道调的多是同步服务
（向量检索、嵌入、聚合查询），直接在协程里调会冻住整个事件循环——这个坑踩过一次，
表现是全站假死而不是单接口变慢。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.errors import BizError
from app.services import intent_service

logger = logging.getLogger("OrchestratorService")


# --------------------------------------------------------------------------- #
# 子智能体执行器
#
# 每个执行器签名统一为 (text, user) -> Dict[str, Any]，
# 返回 {"summary": str, "data": Any, "degraded": bool, "note": str}。
# 抛异常由上层统一捕获成 ok=false——**执行器内部不吞异常**。
# --------------------------------------------------------------------------- #
async def _run_dietitian(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """营养问答：走 RAG 链路（高危已在分类阶段拦掉，这里仍保留拒答兜底）。"""
    from app.services.chat_query_service import chat_query_service

    chunks: List[str] = []
    refs: List[Dict[str, Any]] = []
    refused = False
    async for ev in chat_query_service.stream(text, "dietitian"):
        kind = ev.get("event")
        if kind == "reference":
            refs = (ev.get("data") or {}).get("docs") or []
        elif kind == "delta":
            chunks.append((ev.get("data") or {}).get("text") or "")
        elif kind == "done":
            refused = bool((ev.get("data") or {}).get("refused"))
    answer = "".join(chunks).strip()
    if refused:
        return {"summary": "该问题属于营养师智能体不回答的范围，已给出就医指引。",
                "data": {"references": []}, "degraded": True,
                "note": "营养问答链路拒答（分类阶段未拦住，链路内兜底拦下）"}
    return {
        "summary": answer,
        "data": {"answer": answer, "references": refs},
        "degraded": not answer,
        "note": "" if answer else "知识库未检索到足够依据，已如实返回空答案而非编造",
    }


async def _run_data(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """数据分析：受控 Text2SQL（模板 + 参数绑定，LLM 不生成 SQL）。"""
    from app.services.analytics_service import analytics_service

    def _q():
        return analytics_service.query(user or {}, text)

    r = await asyncio.to_thread(_q)
    rows = r.get("rows") or []
    return {
        "summary": f"已按受控模板执行统计，返回 {r.get('row_count', len(rows))} 行。",
        "data": r,
        "degraded": False,
        "note": "",
    }


async def _run_dish(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """菜品推荐：知识库检索 + 规则库过滤忌口（不调 LLM 自由发挥）。"""
    from app.services.plan_service import plan_service

    def _d():
        return plan_service.recommend_dishes(text)

    r = await asyncio.to_thread(_d)
    dishes = r.get("dishes") or []
    return {
        "summary": f"按体质与忌口匹配到 {len(dishes)} 道菜，附知识库来源 {len(r.get('references') or [])} 条。",
        "data": r, "degraded": not dishes,
        "note": "" if dishes else "规则库与知识库均未命中，已如实返回空列表",
    }


async def _run_diet(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """食养方案：从规则库取周期食谱骨架（确定性，不调模型）。"""
    from app.services.plan_service import plan_service

    def _p():
        return plan_service.build_recipes("通用", days=3)

    r = await asyncio.to_thread(_p)
    recipes = r if isinstance(r, list) else (r or {}).get("recipes") or []
    return {
        "summary": f"生成了 {len(recipes)} 天的食养食谱骨架（规则库生成，确定可复现）。",
        "data": {"recipes": recipes}, "degraded": not recipes,
        "note": "" if recipes else "规则库为空",
    }


async def _run_steward(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """跟踪提醒：本周打卡执行情况。"""
    from app.services.checkin_service import checkin_service

    def _s():
        return checkin_service.stats(user)

    r = await asyncio.to_thread(_s)
    days = r.get("days") or []
    done = sum(1 for d in days if (d.get("checked") if isinstance(d, dict) else False))
    return {
        "summary": f"本周打卡完成 {done}/{len(days) or 7} 天，连续 {r.get('streak', 0)} 天。",
        "data": r, "degraded": False, "note": "",
    }


async def _run_assessment(text: str, user: Optional[dict]) -> Dict[str, Any]:
    """健康评估：读当前健康上下文（体质 / 重点风险 / 忌口），作为后续方案基线。"""
    from app.services.dashboard_service import dashboard_service

    def _h():
        return dashboard_service.health_context(user or {})

    r = await asyncio.to_thread(_h)
    has_profile = bool(r.get("constitution") or r.get("risks") or r.get("avoid"))
    return {
        "summary": (f"当前体质 {r.get('constitution') or '未评估'}，"
                    f"重点风险 {len(r.get('risks') or [])} 项，忌口 {len(r.get('avoid') or [])} 项。"),
        "data": r, "degraded": not has_profile,
        "note": "" if has_profile else "尚未建立健康档案，后续方案缺少基线",
    }


EXECUTORS = {
    "dietitian": _run_dietitian,
    "data": _run_data,
    "dish": _run_dish,
    "diet": _run_diet,
    "steward": _run_steward,
    "assessment": _run_assessment,
}


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
async def _run_step(agent: str, text: str, user: Optional[dict]) -> Dict[str, Any]:
    """执行单个子智能体；任何异常都转成 ok=false，不吞。"""
    fn = EXECUTORS.get(agent)
    t0 = time.perf_counter()
    if fn is None:
        return {"agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": 0,
                "note": f"子智能体 {agent} 未接入执行器"}
    try:
        out = await fn(text, user)
        return {
            "agent": agent, "ok": True,
            "degraded": bool(out.get("degraded")),
            "summary": out.get("summary", ""), "data": out.get("data"),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "note": out.get("note", ""),
        }
    except BizError as e:
        return {"agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "note": f"业务拒绝 {e.code}：{e.message}"}
    except Exception as e:                                   # noqa: BLE001
        logger.warning(f"子智能体 {agent} 执行失败：{e!r}")
        return {"agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "note": f"执行失败：{e}"}


def _aggregate(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """结构化汇总。**不做二次生成**——只把各通道的结果按顺序摆出来。

    刻意不把多个通道的答案交给模型"总结成一段"：那等于让模型在
    多个来源之间做二次演绎，一处失真就会被当成结论传播出去。
    """
    ok = [s for s in steps if s.get("ok")]
    failed = [s for s in steps if not s.get("ok")]
    degraded = [s for s in steps if s.get("ok") and s.get("degraded")]
    refs: List[Dict[str, Any]] = []
    for s in ok:
        data = s.get("data") or {}
        for r in (data.get("references") or []) if isinstance(data, dict) else []:
            refs.append(r)
    # 引用去重（同一 doc_id 可能被多个通道命中）
    seen, uniq = set(), []
    for r in refs:
        k = str(r.get("doc_id") or r.get("source") or id(r))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    if not ok:
        summary = "所有子通道均未成功，已如实返回失败原因，不编造结果。"
    elif failed:
        summary = (f"{len(ok)} 个子智能体返回结果，{len(failed)} 个失败"
                   f"（{', '.join(s['agent'] for s in failed)}）；失败部分见各步骤 note。")
    elif degraded:
        summary = f"{len(ok)} 个子智能体已返回，其中 {len(degraded)} 个如实降级（结果为空）。"
    else:
        summary = f"{len(ok)} 个子智能体全部正常返回。"

    return {
        "summary": summary,
        "ok_count": len(ok), "failed_count": len(failed),
        "degraded_count": len(degraded),
        "references": uniq,
        "citations_note": "汇总仅做结构化拼接，不经模型二次生成；引用均来自各子通道自身的检索结果",
    }


async def orchestrate(text: str, user: Optional[dict], *,
                      max_agents: int = 3, execute: bool = True) -> Dict[str, Any]:
    """主智能体完整编排：识别 → 规划 → 分派 → 汇总。

    `execute=False` 时只出方案不执行（用于预览与评测分类器本身）。
    """
    t0 = time.perf_counter()
    result = intent_service.classify(text)
    plan = intent_service.plan_agents(result, max_agents=max_agents)

    out: Dict[str, Any] = {
        "request": text,
        "intent": {
            "primary": result.get("primary", ""),
            "blocked": result.get("blocked", False),
            "refuse_reason": result.get("refuse_reason", ""),
            "confidence": result.get("confidence", 0.0),
            "margin": result.get("margin", 0.0),
            "low_confidence": result.get("low_confidence", False),
            "fallback": result.get("fallback", False),
            "ranked": result.get("ranked", []),
            "explain": intent_service.explain(result),
            "classifier": "确定性规则分类器（关键词加权 + 阈值 + 顺序仲裁），不调用大模型",
        },
        "plan": plan,
        "steps": [],
        "executed": False,
        "strategy": "先评估身体状况 → 再生成食养方案 → 再落地菜品菜单 → 再提醒跟踪 → 再答疑 → 最后统计",
    }

    # 高危：在分类阶段就停，一次子智能体都不调用
    if result.get("blocked"):
        out["result"] = {
            "summary": result.get("refuse_text", ""),
            "ok_count": 0, "failed_count": 0, "degraded_count": 0,
            "references": [],
            "citations_note": "高危问题未进入业务链路，未检索、未调用模型",
        }
        out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        return out

    if not execute:
        out["result"] = {"summary": "仅预览调度方案，未执行任何子智能体。",
                         "ok_count": 0, "failed_count": 0, "degraded_count": 0,
                         "references": [],
                         "citations_note": "execute=false，未执行"}
        out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        return out

    steps: List[Dict[str, Any]] = []
    for item in plan:
        steps.append(await _run_step(item["agent"], text, user))

    out["steps"] = steps
    out["executed"] = True
    out["result"] = _aggregate(steps)
    out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    return out
