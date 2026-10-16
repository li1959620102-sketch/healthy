# -*- coding: utf-8 -*-
"""主智能体 · 意图识别（确定性规则分类器）。

**为什么不用大模型做分类** —— 这是本项目最关键的一次取舍，理由写在
`app/core/rules.py` 的 `INTENT_RULES` 注释里，这里只说结论：
路由层必须做到「同一问句永远同一结果、误判率可量化、命中原因可摊开」，
这三条规则分类天然满足，LLM 分类三条都做不到。

因此本模块的对外契约是：
  1. **确定性**：同输入必同输出，不依赖任何模型、不依赖随机数、不读时钟；
  2. **可评测**：`ranked` 里每个意图都带 score 与 evidence，可直接算混淆矩阵；
  3. **高危前置**：医疗干预类问题在**分类之前**就短路，不进入任何业务链路。

第 3 条复用 `app/core/refusal.py` 的拒答规则——**同一套规则、同一份事实源**。
如果这里另写一套关键词，高危分类和问答拒答就会漂移，出现
「路由说不是高危、进了问答链路又被拒答」这种自相矛盾的行为。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core import rules

logger = logging.getLogger("IntentService")


def _score_intent(text: str) -> Dict[str, Dict[str, Any]]:
    """对每个意图打分，返回 {intent: {score, strong_hit, weak_hit, critical_hit}}。"""
    result: Dict[str, Dict[str, Any]] = {}
    for rule in rules.INTENT_RULES:
        critical = [kw for kw in rule.get("critical", []) if kw in text]
        strong = [kw for kw in rule["strong"] if kw in text]
        weak = [kw for kw in rule["weak"] if kw in text]
        score = (len(critical) * rules.INTENT_CRITICAL_WEIGHT
                 + len(strong) * rules.INTENT_STRONG_WEIGHT
                 + len(weak) * rules.INTENT_WEAK_WEIGHT)
        if score > 0:
            result[rule["key"]] = {"score": score, "critical_hit": critical,
                                   "strong_hit": strong, "weak_hit": weak,
                                   "order": rule["order"]}
    return result


def _baseline_hit(text: str) -> List[str]:
    """指标类基线信号命中项（血压/血糖/BMI…）。

    指标描述的是**用户是谁**，不是**用户要什么**。命中它应该让健康评估进入
    执行链路（先了解基线再给建议），但不该抢走主意图——
    否则「高血压人群的早餐怎么吃」会被判成健康评估，而用户明明在问营养知识。
    """
    return [t for t in rules.INTENT_BASELINE_TERMS if t in text]


def classify(text: str, *, check_refusal_fn=None) -> Dict[str, Any]:
    """识别一句用户输入的意图。

    返回结构（字段稳定，前端与评测脚本都依赖它）：

        {
          "blocked": bool,          # 命中高危 → 后续不执行任何子智能体
          "refuse_reason": str,     # blocked 时的拒答类别
          "refuse_text": str,       # 面向用户的话术
          "primary": str,           # 主意图 key；blocked 时为空
          "confidence": float,      # 主意图份额 = 最高分 / 总分
          "margin": float,          # 与第二名的分差，衡量区分度
          "low_confidence": bool,   # 份额过低 → 已走兜底
          "fallback": bool,         # 是否走了兜底
          "ranked": [               # 所有过阈值的意图，按分降序
              {"intent","name","agent","score","confidence","evidence":[...]}
          ]
        }

    `check_refusal_fn` 允许单测注入桩；生产走 `app.core.refusal.check_refusal`。
    """
    raw = (text or "").strip()

    # ---- 1. 高危前置：先判该不该进业务链路，再谈路由 ----
    if check_refusal_fn is None:
        from app.core.refusal import check_refusal
        check_refusal_fn = check_refusal
    blocked, reason = check_refusal_fn(raw) if raw else (False, "")

    base: Dict[str, Any] = {
        "blocked": bool(blocked), "refuse_reason": reason or "",
        "refuse_text": "", "primary": "", "confidence": 0.0, "margin": 0.0,
        "low_confidence": False, "fallback": False, "ranked": [],
        "baseline_hit": [],
    }
    if blocked:
        from app.core.refusal import refusal_message
        base["refuse_text"] = refusal_message(reason)
        return base

    if not raw:
        # 空输入：明确走兜底，并把 primary 填上——否则调用方拿到空字符串，
        # 只能靠 if 判断兜底，等于把"意图不明"和"识别失败"混成一个状态。
        base["fallback"] = True
        base["low_confidence"] = True
        base["primary"] = rules.INTENT_FALLBACK
        return base

    # ---- 2. 打分 ----
    scored = _score_intent(raw)
    baseline_hit = _baseline_hit(raw)
    base["baseline_hit"] = baseline_hit
    ranked: List[Dict[str, Any]] = []
    for key, hit in scored.items():
        if hit["score"] < rules.INTENT_MIN_SCORE:
            continue
        ranked.append({
            "intent": key,
            "name": rules.INTENT_NAMES.get(key, key),
            "agent": rules.INTENT_TO_AGENT.get(key, key),
            "score": hit["score"],
            "confidence": 0.0,          # 稍后按总分归一
            "evidence": hit["critical_hit"] + hit["strong_hit"] + hit["weak_hit"],
            "evidence_kinds": {
                "critical": hit["critical_hit"],
                "strong": hit["strong_hit"], "weak": hit["weak_hit"],
            },
            "order": hit["order"],
        })
    ranked.sort(key=lambda x: (-x["score"], x["order"]))

    # ---- 3. 基线信号：不抢主意图，但进链路 ----
    # 指标词命中时，若健康评估是靠指标词拿到的主意图（有别的意图也过了阈值），
    # 就把它降级为链路首步（order 最小，本来就在最前），主意图让给真正的诉求。
    if baseline_hit and len(ranked) > 1:
        demoted = [x for x in ranked
                   if x["intent"] == "assessment"
                   and not ({"测评", "评测", "问卷", "体质辨识", "健康档案",
                              "风险评估"} & set(x["evidence"]))]
        if demoted and ranked[0]["intent"] == "assessment":
            rest = [x for x in ranked if x["intent"] != "assessment"]
            ranked = ([rest[0]] + demoted + rest[1:]) if rest else ranked

    # ---- 4. 置信度：份额 + 区分度 ----
    total = sum(x["score"] for x in ranked) or 1.0
    for x in ranked:
        x["confidence"] = round(x["score"] / total, 4)

    if not ranked:
        # 一个意图都没过阈值 → 意图不明。不硬猜，走营养问答兜底
        # （它有拒答兜底、不会给出有害输出），并把「不确定」如实标出来。
        base["fallback"] = True
        base["low_confidence"] = True
        base["primary"] = rules.INTENT_FALLBACK
        if baseline_hit:
            # 只命中指标词：用户说的是"我血糖高要注意什么"，要的是**建议**不是**测量**，
            # 所以兜底走营养问答而不是健康评估。理由是营养问答有拒答兜底，
            # 就算猜错了也不会给出有害输出；反过来把测量请求送去问答才会真的答非所问。
            base["ranked"] = [{
                "intent": rules.INTENT_FALLBACK,
                "name": rules.INTENT_NAMES[rules.INTENT_FALLBACK],
                "agent": rules.INTENT_TO_AGENT[rules.INTENT_FALLBACK],
                "score": float(len(baseline_hit)),
                "confidence": 1.0,
                "evidence": baseline_hit,
                "evidence_kinds": {"critical": [], "strong": [], "weak": baseline_hit},
                "order": 5,
            }]
        return base

    top = ranked[0]
    margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
    low = top["confidence"] < rules.INTENT_LOW_CONFIDENCE

    base["ranked"] = ranked
    base["margin"] = round(margin, 2)
    base["confidence"] = top["confidence"]
    if low:
        # 多个意图打平且都不突出 → 不硬猜，走兜底但保留 ranked 供运营观察
        base["fallback"] = True
        base["low_confidence"] = True
        base["primary"] = rules.INTENT_FALLBACK
    else:
        base["primary"] = top["intent"]
    return base


def plan_agents(result: Dict[str, Any], *, max_agents: int = 3) -> List[Dict[str, Any]]:
    """把识别结果转成**执行计划**：按链路顺序决定调用哪几个子智能体。

    与 `plan_service.dispatch()` 的区别：那边只按关键词命中出「方案说明」，
    这边基于打分后的意图产出**可执行序列**，并显式带上置信度与是否兜底，
    让执行层能判断"这一步是不是猜的"。
    """
    if result.get("blocked"):
        return []
    ranked = result.get("ranked") or []
    if not ranked:
        key = rules.INTENT_FALLBACK
        return [{"intent": key, "agent": rules.INTENT_TO_AGENT[key],
                 "name": rules.INTENT_NAMES[key], "confidence": 0.0,
                 "fallback": True, "reason": "未识别到明确意图，按通用营养问答兜底"}]

    picked = ranked[:max_agents] if not result.get("low_confidence") else ranked[:1]
    out: List[Dict[str, Any]] = []
    for x in sorted(picked, key=lambda i: i["order"]):
        out.append({
            "intent": x["intent"], "agent": x["agent"], "name": x["name"],
            "confidence": x["confidence"], "fallback": False,
            "reason": f"命中 {x['evidence']}（分 {x['score']}，"
                      f"置信 {round(x['confidence'] * 100)}%）",
        })
    return out


def explain(result: Dict[str, Any]) -> str:
    """一句话说明这次为什么这么路由——给用户看，也给运营看。"""
    if result.get("blocked"):
        return f"命中高危意图（{result.get('refuse_reason')}），已在入口拦截，不进入业务链路"
    if result.get("low_confidence"):
        return "意图识别置信度不足，已按通用营养问答兜底"
    ranked = result.get("ranked") or []
    if not ranked:
        return "未识别到明确意图，按通用营养问答兜底"
    top = ranked[0]
    return (f"主意图 {top['name']}（置信 {round(top['confidence'] * 100)}%，"
            f"命中 {top['evidence']}）；"
            + ("单意图直达" if len(ranked) == 1
               else f"另有 {len(ranked) - 1} 个次要意图一并纳入链路"))
