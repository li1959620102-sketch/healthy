# -*- coding: utf-8 -*-
"""主智能体 · supervisor 图：识别意图 → 规划链路 → 扇出调度 → 结构化汇总。

## 图长什么样

    stamp → classify → plan ─┬─(高危)──────────────→ refuse  → END
                             ├─(仅预览)────────────→ preview → END
                             └─(Send × N)──────────→ 子图们 → aggregate → END

`Send` 是这里的关键：调度方案是**运行时**决定的（1~3 个子智能体，看意图），
而图的拓扑必须在编译期就固定，两者本来是矛盾的。`Send` 解决的正是这一点——
拓扑里固定放 6 个子图节点，运行时用 `Send` 只派发命中的那几个，
既保证"只跑该跑的"，又不必为每种组合各画一张图。

## 并行还是串行

扇出的子智能体是**并行**执行的。理由：它们之间没有数据依赖（都只吃原始问题），
串行只会把各自的耗时相加。实测只有 `dietitian` 会调大模型，因此并行不会造成
多个大模型请求同时打到 Ollama 上。

但**结果顺序必须稳定**：并行完成顺序不可控，若直接按完成顺序返回，同一句话
每次问出来的步骤次序都不一样，前端展示和回归用例都无法断言。
所以 `aggregate` 里按 `plan` 的 order 重排，做到"执行并行、呈现有序"。

## 三条红线（与原有实现一致，这里只是换了个执行引擎）

1. **高危不进链路**：分类阶段就 `refuse`，一次子智能体都不调用；
2. **不做二次生成**：汇总只拼接，见 `aggregate.py`；
3. **失败如实上报**：子图抛异常 → 该 step 记 ok=false 并带原因，绝不换一个
   看起来合理的结果顶上去。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.agents.aggregate import aggregate_steps, blocked_result
from app.agents.state import AgentState
from app.agents.subgraphs import get_subgraph, subgraph_names
from app.agent_runtime.contract import AgentPlan, AgentStep
from app.agent_runtime.runner import run_graph_task
from app.agent_runtime.checkpoint import checkpointed_node

logger = logging.getLogger("SupervisorGraph")

STRATEGY = "先评估身体状况 → 再生成食养方案 → 再落地菜品菜单 → 再提醒跟踪 → 再答疑 → 最后统计"


# --------------------------------------------------------------------------- #
# 节点
# --------------------------------------------------------------------------- #
def _stamp(state: AgentState) -> Dict[str, Any]:
    return {"started_at": time.perf_counter()}


def _classify(state: AgentState) -> Dict[str, Any]:
    from app.services import intent_service

    text = state.get("text") or ""
    result = intent_service.classify(text)
    return {
        "intent": {
            "primary": result.get("primary", ""),
            "blocked": result.get("blocked", False),
            "refuse_reason": result.get("refuse_reason", ""),
            "refuse_text": result.get("refuse_text", ""),
            "confidence": result.get("confidence", 0.0),
            "margin": result.get("margin", 0.0),
            "low_confidence": result.get("low_confidence", False),
            "fallback": result.get("fallback", False),
            "ranked": result.get("ranked", []),
            "explain": intent_service.explain(result),
            "classifier": "确定性规则分类器（关键词加权 + 阈值 + 顺序仲裁），不调用大模型",
        },
        "classify_ms": int((time.perf_counter() - (state.get("started_at") or time.perf_counter())) * 1000),
    }


def _plan(state: AgentState) -> Dict[str, Any]:
    from app.services import intent_service

    intent = state.get("intent") or {}
    # 用原始分类结果重排（explain 是展示用的，不参与规划）
    result = {
        "primary": intent.get("primary", ""),
        "blocked": intent.get("blocked", False),
        "confidence": intent.get("confidence", 0.0),
        "ranked": intent.get("ranked", []),
        "fallback": intent.get("fallback", False),
    }
    plan = intent_service.plan_agents(result, max_agents=int(state.get("max_agents") or 3))
    return {"plan": plan}


def _refuse(state: AgentState) -> Dict[str, Any]:
    intent = state.get("intent") or {}
    return {"result": blocked_result(intent.get("refuse_text", ""))}


def _preview(state: AgentState) -> Dict[str, Any]:
    return {"result": {
        "summary": "仅预览调度方案，未执行任何子智能体。",
        "ok_count": 0, "failed_count": 0, "degraded_count": 0,
        "references": [], "citations_note": "execute=false，未执行",
    }}


def _make_subagent_node(agent: str):
    """把一个子图包装成主图节点。

    这里**不用** `add_node(agent, subgraph)` 直接嵌子图：子图 state 与主图 state
    不同（见 state.py 的分层说明），直接嵌会让子图把父图的字段带回来，
    配合 reducer 就会重复累加。包一层函数后，父子边界清清楚楚——
    进去只给 text/user，出来只交一条 step。
    """

    async def node(state: AgentState) -> Dict[str, Any]:
        graph = get_subgraph(agent)
        if graph is None:
            return {"steps": [{
                "agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": 0, "note": f"子智能体 {agent} 未注册执行图",
            }]}
        t0 = time.perf_counter()
        try:
            out = await run_graph_task(
                agent,
                state.get("text") or "",
                graph,
                user=state.get("user"),
                plan=AgentPlan(
                    goal=state.get("text") or "",
                    confidence=1.0,
                    reason="业务子图兼容接入 Agent Runtime",
                    steps=[AgentStep(step_id=f"{agent}.execute", order=1,
                                     action=f"执行 {agent} 业务子图",
                                     tool_name=f"{agent}.execute")],
                ),
            )
            step = out.get("step") or {
                "agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": 0, "note": "子图未产出 step（实现缺陷）",
            }
            return {"steps": [step]}
        except Exception as e:                       # noqa: BLE001
            # 子图内部不吞异常，在这里统一落成 ok=false。降级要降得诚实：
            # 用户看到的是"这个通道没成功 + 原因"，而不是一个空白结果。
            logger.warning(f"子智能体 {agent} 执行失败：{e!r}")
            return {"steps": [{
                "agent": agent, "ok": False, "degraded": True, "summary": "",
                "data": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "note": f"执行失败：{type(e).__name__}: {e}",
            }]}

    node.__name__ = f"node_{agent}"
    return node


def _aggregate(state: AgentState) -> Dict[str, Any]:
    order = {item.get("agent"): idx for idx, item in enumerate(state.get("plan") or [])}
    steps = sorted(state.get("steps") or [],
                   key=lambda s: order.get(s.get("agent"), len(order)))
    return {"result": aggregate_steps(steps)}


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
def _after_plan(state: AgentState):
    """plan 之后的三岔口：高危 / 仅预览 / 扇出执行。

    返回 `Send` 列表时 LangGraph 会并行跑这些分支，全部完成后统一进入 aggregate。
    """
    intent = state.get("intent") or {}
    if intent.get("blocked"):
        return "refuse"
    if not state.get("execute"):
        return "preview"
    plan = state.get("plan") or []
    if not plan:
        # 规划为空不能什么都不发——那样图会停在这里，调用方永远拿不到响应
        return "aggregate"
    payload = {"text": state.get("text"), "user": state.get("user")}
    return [Send(item["agent"], payload) for item in plan]


# --------------------------------------------------------------------------- #
# 图构建
# --------------------------------------------------------------------------- #
_GRAPH = None
_LOCK = threading.Lock()


def build_supervisor_graph():
    g = StateGraph(AgentState)
    g.add_node("stamp", checkpointed_node("orchestrator.stamp", _stamp))
    g.add_node("classify", checkpointed_node("orchestrator.classify", _classify))
    g.add_node("make_plan", checkpointed_node("orchestrator.make_plan", _plan))
    g.add_node("refuse", checkpointed_node("orchestrator.refuse", _refuse))
    g.add_node("preview", checkpointed_node("orchestrator.preview", _preview))
    g.add_node("aggregate", checkpointed_node("orchestrator.aggregate", _aggregate))

    for name in subgraph_names():
        g.add_node(name, checkpointed_node(f"orchestrator.delegate.{name}", _make_subagent_node(name)))
        g.add_edge(name, "aggregate")

    g.set_entry_point("stamp")
    g.add_edge("stamp", "classify")
    g.add_edge("classify", "make_plan")
    g.add_conditional_edges(
        "make_plan", _after_plan,
        ["refuse", "preview", "aggregate", *subgraph_names()],
    )
    g.add_edge("refuse", END)
    g.add_edge("preview", END)
    g.add_edge("aggregate", END)
    return g.compile()


def get_supervisor_graph():
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    with _LOCK:
        if _GRAPH is None:
            _GRAPH = build_supervisor_graph()
            logger.info("supervisor 图已编译：stamp/classify/plan + %s + aggregate",
                        ",".join(subgraph_names()))
    return _GRAPH


async def run_supervisor(text: str, user: Optional[dict], *,
                         max_agents: int = 3, execute: bool = True) -> Dict[str, Any]:
    """执行一次完整编排，返回主图最终状态（含 intent / plan / steps / result）。"""
    graph = get_supervisor_graph()
    return await graph.ainvoke({
        "text": text,
        "user": user,
        "execute": execute,
        "max_agents": max_agents,
        "steps": [],
    })
