# -*- coding: utf-8 -*-
"""六个子智能体的 LangGraph 图定义。

## 为什么每个子智能体都要有图，而不是只在主图上包一层

主图只解决"谁来做"，解决不了"做得对不对"。真正需要图的是**单个子智能体内部
存在分支与循环**的地方，最典型的是营养问答：

    检索 → 依据够不够？ → 不够：改写查询重检（最多 2 次） → 还不够：如实拒答
                        → 够：生成 → 带引用返回

这条链里有条件分支（goto）、有回边（retry loop）、有终止条件（attempts）。
用 if/else 也能写，但写出来是一坨难以测试的嵌套；写成图之后：
  · 每个节点单独可测（喂 state 进去，断言输出的 state）；
  · 循环有显式边界（attempts < SELFRAG_MAX_ATTEMPTS），不会漏出死循环；
  · 每条路径都汇聚到 `finalize`，对外只有一种返回结构，调用方无需关心内部绕了几圈。

其余五个子智能体结构更简单（加载 → 判定 → 汇总），但仍按同样方式建图，
目的是**统一契约**：主图只需要"一张图 + 一个 step 输出"，新增子智能体时不必
为主图写特例分支。

## 共同约定

  · 所有阻塞调用（数据库、向量库、模型）一律 `asyncio.to_thread`；
  · 节点只返回自己改动的字段，不改别人的；
  · 出口唯一：`finalize` 把内部中间量收敛成一条 step；
  · 异常不吞：子图内不 try/except 包住业务调用，让父图统一记 ok=false。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, StateGraph

from app.agents.state import SubAgentState
from app.core.refusal import check_refusal, refusal_message
from app.core.rules import (
    QUERY_STOPWORDS,
    SELFRAG_MAX_ATTEMPTS,
    SELFRAG_MIN_DOCS,
    SELFRAG_MIN_TOP_SCORE,
)
from app.agent_runtime.tool_executor import execute_tool
from app.agent_runtime.checkpoint import checkpointed_node

logger = logging.getLogger("AgentGraph")

# 子图最多取几条依据（与 /chat/query 的 top_k 保持一致，避免同问题两套召回）
RETRIEVE_TOP_K = 4


# --------------------------------------------------------------------------- #
# 公共节点
# --------------------------------------------------------------------------- #
def _finalizer(agent: str) -> Callable[[SubAgentState], Dict[str, Any]]:
    """出口节点：把子图内部状态收敛成对外唯一的 step 结构。"""

    def node(state: SubAgentState) -> Dict[str, Any]:
        t0 = state.get("started_at") or time.perf_counter()
        return {
            "step": {
                "agent": agent,
                "ok": True,
                "degraded": bool(state.get("degraded")),
                "summary": state.get("summary") or "",
                "data": state.get("data"),
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "note": state.get("note") or "",
            }
        }

    return node


def _stamp(state: SubAgentState) -> Dict[str, Any]:
    """入口节点：打开始时间。所有子图的第一个节点，保证 elapsed_ms 有意义。"""
    return {"started_at": state.get("started_at") or time.perf_counter()}


# --------------------------------------------------------------------------- #
# 1. 健康测评智能体：读健康基线
# --------------------------------------------------------------------------- #
async def _assessment_load(state: SubAgentState) -> Dict[str, Any]:
    from app.services.dashboard_service import dashboard_service

    call = await execute_tool(
        "assessment", "health_profile.read", {"user": state.get("user") or {}},
        task_id=state.get("runtime_task_id"), step_id="assessment.read_profile",
        user=state.get("user"), handler=dashboard_service.health_context,
    )
    r = call.result or {}
    has_profile = bool(r.get("constitution") or r.get("risks") or r.get("avoid"))
    return {
        "data": r,
        "degraded": not has_profile,
        "summary": (f"当前体质 {r.get('constitution') or '未评估'}，"
                    f"重点风险 {len(r.get('risks') or [])} 项，忌口 {len(r.get('avoid') or [])} 项。"),
        "note": "" if has_profile else "尚未建立健康档案，后续方案缺少基线",
    }


def build_assessment_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("assessment.stamp", _stamp))
    g.add_node("load", checkpointed_node("assessment.load", _assessment_load))
    g.add_node("finalize", checkpointed_node("assessment.finalize", _finalizer("assessment")))
    g.set_entry_point("stamp")
    g.add_edge("stamp", "load")
    g.add_edge("load", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 2. 食养方案智能体：规则库生成周期食谱
# --------------------------------------------------------------------------- #
async def _diet_plan(state: SubAgentState) -> Dict[str, Any]:
    from app.services.plan_service import plan_service

    call = await execute_tool(
        "diet", "diet_plan.generate", {"scene": "通用", "days": 3},
        task_id=state.get("runtime_task_id"), step_id="diet.generate_plan",
        user=state.get("user"),
        handler=plan_service.build_recipes,
    )
    r = call.result
    recipes = r if isinstance(r, list) else (r or {}).get("recipes") or []
    # 注意：build_recipes 返回的是**餐次**列表（3 天 × 早/午/晚 = 9 条），
    # 不是"天"。直接把 len 说成天数会显示"生成了 9 天"，与实际不符。
    days = len({x.get("day_no") for x in recipes if isinstance(x, dict) and x.get("day_no")})
    return {
        "data": {"recipes": recipes},
        "degraded": not recipes,
        "summary": (f"生成了 {days or '?'} 天、共 {len(recipes)} 餐的食养食谱骨架"
                    f"（规则库生成，确定可复现）。"),
        "note": "" if recipes else "规则库为空",
    }


def build_diet_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("diet.stamp", _stamp))
    g.add_node("plan", checkpointed_node("diet.plan", _diet_plan))
    g.add_node("finalize", checkpointed_node("diet.finalize", _finalizer("diet")))
    g.set_entry_point("stamp")
    g.add_edge("stamp", "plan")
    g.add_edge("plan", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 3. 智能荐菜智能体：知识库检索 + 忌口过滤
# --------------------------------------------------------------------------- #
async def _dish_recommend(state: SubAgentState) -> Dict[str, Any]:
    from app.services.plan_service import plan_service

    call = await execute_tool(
        "dish", "recipe.search", {"query": state.get("text") or ""},
        task_id=state.get("runtime_task_id"), step_id="dish.search_recipe",
        user=state.get("user"), handler=plan_service.recommend_dishes,
    )
    r = call.result or {}
    dishes = r.get("dishes") or []
    return {
        "data": r,
        "degraded": not dishes,
        "summary": (f"按体质与忌口匹配到 {len(dishes)} 道菜，"
                    f"附知识库来源 {len(r.get('references') or [])} 条。"),
        "note": "" if dishes else "规则库与知识库均未命中，已如实返回空列表",
    }


def build_dish_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("dish.stamp", _stamp))
    g.add_node("recommend", checkpointed_node("dish.recommend", _dish_recommend))
    g.add_node("finalize", checkpointed_node("dish.finalize", _finalizer("dish")))
    g.set_entry_point("stamp")
    g.add_edge("stamp", "recommend")
    g.add_edge("recommend", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 4. 跟踪提醒智能体：打卡执行情况
# --------------------------------------------------------------------------- #
async def _steward_stats(state: SubAgentState) -> Dict[str, Any]:
    from app.services.checkin_service import checkin_service

    call = await execute_tool(
        "steward", "checkin.read", {"user": state.get("user")},
        task_id=state.get("runtime_task_id"), step_id="steward.read_checkin",
        user=state.get("user"), handler=checkin_service.stats,
    )
    r = call.result or {}
    days = r.get("days") or []
    done = sum(1 for d in days if (d.get("checked") if isinstance(d, dict) else False))
    return {
        "data": r,
        "degraded": False,
        "summary": f"本周打卡完成 {done}/{len(days) or 7} 天，连续 {r.get('streak', 0)} 天。",
        "note": "",
    }


def build_steward_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("steward.stamp", _stamp))
    g.add_node("stats", checkpointed_node("steward.stats", _steward_stats))
    g.add_node("finalize", checkpointed_node("steward.finalize", _finalizer("steward")))
    g.set_entry_point("stamp")
    g.add_edge("stamp", "stats")
    g.add_edge("stats", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 5. 数据分析智能体：受控 Text2SQL
# --------------------------------------------------------------------------- #
async def _data_query(state: SubAgentState) -> Dict[str, Any]:
    from app.services.analytics_service import analytics_service

    call = await execute_tool(
        "data", "sql.execute_readonly",
        {"user": state.get("user") or {}, "question": state.get("text") or ""},
        task_id=state.get("runtime_task_id"), step_id="data.execute_query",
        user=state.get("user"), handler=analytics_service.query,
    )
    r = call.result or {}
    rows = r.get("rows") or []
    return {
        "data": r,
        "degraded": False,
        "summary": f"已按受控模板执行统计，返回 {r.get('row_count', len(rows))} 行。",
        "note": "",
    }


def build_data_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("data.stamp", _stamp))
    # 节点名不能叫 "query"：那是 SubAgentState 的字段名，LangGraph 会拒绝
    # （节点名与 state key 同属一个命名空间，重名会直接抛 ValueError）
    g.add_node("run_query", checkpointed_node("data.run_query", _data_query))
    g.add_node("finalize", checkpointed_node("data.finalize", _finalizer("data")))
    g.set_entry_point("stamp")
    g.add_edge("stamp", "run_query")
    g.add_edge("run_query", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 6. 营养师智能体：self-RAG（检索 → 质量评估 → 改写重检 → 生成 / 如实拒答）
# --------------------------------------------------------------------------- #
def refine_query(text: str) -> str:
    """把口语问句压成检索词。

    用户问「高血压的人平时吃什么都比较好」，向量检索对这种填充词很敏感；
    剥掉"的人 / 平时 / 比较"之后，「高血压 吃」的召回明显更准（这是确定性的
    文本处理，不调模型——查询改写如果交给模型，就等于用一个可能出错的环节
    去修另一个可能出错的环节，出问题时无法归因）。
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    try:
        import jieba
    except ImportError:                      # pragma: no cover
        return raw
    words = [w.strip() for w in jieba.cut(raw) if w.strip()]
    kept = [w for w in words if w not in QUERY_STOPWORDS and len(w) >= 2]
    # 全被过滤掉（比如整句都是口语）时退回原句，不制造空查询
    return " ".join(kept) if kept else raw


async def _dietitian_guard(state: SubAgentState) -> Dict[str, Any]:
    """第一道闸：拒答策略。命中后一次检索都不做。"""
    text = state.get("text") or ""
    refused, reason = check_refusal(text)
    if refused:
        return {
            "refused": True,
            "refusal_text": refusal_message(reason),
            "summary": refusal_message(reason),
            "degraded": True,
            "data": {"references": [], "refused": True},
            "note": f"拒答策略命中：{reason}（未检索、未调用模型）",
        }
    return {"refused": False, "query": text, "attempts": 0}


async def _dietitian_retrieve(state: SubAgentState) -> Dict[str, Any]:
    from app.services.chat_query_service import chat_query_service

    q = state.get("query") or state.get("text") or ""
    call = await execute_tool(
        "dietitian", "knowledge.search", {"question": q, "top_k": RETRIEVE_TOP_K},
        task_id=state.get("runtime_task_id"), step_id="dietitian.retrieve",
        user=state.get("user"), handler=chat_query_service.retrieve,
    )
    docs = call.result or []
    top = max((float(d.get("score") or 0.0) for d in docs), default=0.0)
    return {
        "docs": docs,
        "top_score": top,
        "attempts": int(state.get("attempts") or 0) + 1,
    }


async def _dietitian_refine(state: SubAgentState) -> Dict[str, Any]:
    """回边：把口语问句压成检索词，再检一次。"""
    return {"query": refine_query(state.get("text") or "")}


async def _dietitian_generate(state: SubAgentState) -> Dict[str, Any]:
    from app.services.chat_query_service import (
        DIETITIAN_SYSTEM_PROMPT,
        build_dialog_messages,
        chat_query_service,
    )

    docs = state.get("docs") or []
    context = "\n".join(f"【{d.get('title')}】\n{d.get('fragment')}" for d in docs)
    memory_context = "\n".join(
        f"[{kind}] {item.get('content')}"
        for kind, items in (state.get("memory_context") or {}).items()
        for item in (items or [])
    )
    dialog = build_dialog_messages(
        DIETITIAN_SYSTEM_PROMPT, context, state.get("text") or "",
        memory_context=memory_context,
    )
    system_text = dialog[0]["content"]
    user_text = "\n\n".join(m["content"] for m in dialog[1:])

    call = await execute_tool(
        "dietitian", "llm.generate", {"system_text": system_text, "user_text": user_text},
        task_id=state.get("runtime_task_id"), step_id="dietitian.generate",
        user=state.get("user"), handler=chat_query_service.complete,
    )
    answer = call.result
    references = [
        {"doc_id": d.get("doc_id"), "title": d.get("title"),
         "fragment": d.get("fragment"), "source": d.get("source") or ""}
        for d in docs
    ]

    if not answer:
        # 模型不可用：如实降级为检索片段，不编一段答案顶上
        fallback = ("（模型服务暂不可用，以下为知识库检索片段，仅供参考）\n"
                    + "\n".join(f"- {d.get('title')}：{d.get('fragment')}" for d in docs))
        return {
            "summary": fallback,
            "data": {"answer": fallback, "references": references, "degraded_reason": "llm_unavailable"},
            "degraded": True,
            "note": "模型调用失败或超时，已降级返回检索原文",
        }

    return {
        "summary": answer,
        "data": {"answer": answer, "references": references,
                 "retrieval": {"attempts": state.get("attempts"), "top_score": state.get("top_score")}},
        "degraded": not answer.strip(),
        "note": "",
    }


async def _dietitian_no_answer(state: SubAgentState) -> Dict[str, Any]:
    """检索两轮仍无达标依据：如实说不知道，不生成。"""
    return {
        "summary": ("这个问题超出了我当前知识库（药食同源食材、慢病饮食原则、特殊人群饮食）的范围，"
                    "我先不随意回答，以免误导您。您可以换个问法，或先做一次健康风险测评。"),
        "data": {"references": [], "retrieval": {"attempts": state.get("attempts"),
                                                 "top_score": state.get("top_score")}},
        "degraded": True,
        "note": f"检索 {state.get('attempts')} 轮未达阈值（最高分 {state.get('top_score')}），已如实拒答",
    }


def _dietitian_route(state: SubAgentState) -> str:
    """检索之后的三岔口：够用就生成，不够就改写重检，重检也不够就拒答。"""
    docs = state.get("docs") or []
    top = float(state.get("top_score") or 0.0)
    if len(docs) >= SELFRAG_MIN_DOCS and top >= SELFRAG_MIN_TOP_SCORE:
        return "generate"
    if int(state.get("attempts") or 0) < SELFRAG_MAX_ATTEMPTS:
        return "refine"
    return "no_answer"


def build_dietitian_graph():
    g = StateGraph(SubAgentState)
    g.add_node("stamp", checkpointed_node("dietitian.stamp", _stamp))
    g.add_node("guard", checkpointed_node("dietitian.guard", _dietitian_guard))
    g.add_node("retrieve", checkpointed_node("dietitian.retrieve", _dietitian_retrieve))
    g.add_node("refine", checkpointed_node("dietitian.refine", _dietitian_refine))
    g.add_node("generate", checkpointed_node("dietitian.generate", _dietitian_generate))
    g.add_node("no_answer", checkpointed_node("dietitian.no_answer", _dietitian_no_answer))
    g.add_node("finalize", checkpointed_node("dietitian.finalize", _finalizer("dietitian")))

    g.set_entry_point("stamp")
    g.add_edge("stamp", "guard")
    # 拒答：直接收敛，不检索不生成
    g.add_conditional_edges(
        "guard",
        lambda s: "finalize" if s.get("refused") else "retrieve",
        {"finalize": "finalize", "retrieve": "retrieve"},
    )
    g.add_conditional_edges(
        "retrieve", _dietitian_route,
        {"generate": "generate", "refine": "refine", "no_answer": "no_answer"},
    )
    g.add_edge("refine", "retrieve")          # 回边：self-RAG 的重检
    g.add_edge("generate", "finalize")
    g.add_edge("no_answer", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# 注册表（懒编译：导入模块时不建图，避免应用启动阶段多花时间）
# --------------------------------------------------------------------------- #
_BUILDERS: Dict[str, Callable[[], Any]] = {
    "assessment": build_assessment_graph,
    "diet": build_diet_graph,
    "dish": build_dish_graph,
    "steward": build_steward_graph,
    "data": build_data_graph,
    "dietitian": build_dietitian_graph,
}

_GRAPH_CACHE: Dict[str, Any] = {}
_GRAPH_LOCK = threading.Lock()


def get_subgraph(agent: str):
    """取编译好的子图（进程内单例）。未知 agent 返回 None。"""
    if agent in _GRAPH_CACHE:
        return _GRAPH_CACHE[agent]
    builder = _BUILDERS.get(agent)
    if builder is None:
        return None
    with _GRAPH_LOCK:
        if agent not in _GRAPH_CACHE:
            _GRAPH_CACHE[agent] = builder()
    return _GRAPH_CACHE[agent]


def subgraph_names() -> List[str]:
    return list(_BUILDERS.keys())
