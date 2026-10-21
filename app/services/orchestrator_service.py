# -*- coding: utf-8 -*-
"""主智能体 · 编排执行层（对外门面）。

本模块只做三件事：
  1. 暴露 `orchestrate()`——一次完整的「识别 → 规划 → 分派 → 汇总」；
  2. 保留 `EXECUTORS` / `_run_step()` 这两个原有接口，供单测替换执行器做故障注入；
  3. 把耗时、意图、方案、步骤、汇总组装成稳定的对外结构。

**真正的执行逻辑已经全部搬到 LangGraph 图里**：
  · `app/agents/supervisor.py`  主图（supervisor，用 Send 扇出子图）
  · `app/agents/subgraphs.py`   六个子智能体各自的图（dietitian 内含 self-RAG 循环）

注意 `EXECUTORS` 里**不再有另一份业务实现**——每一项都是"调用对应子图"的适配器。
如果这里再写一遍 `_run_dish` 之类的函数，就会出现"流式问答走 A 逻辑、编排走 B 逻辑"
的分叉，改一处忘另一处，最后没人敢动。这一层只做适配。

与 `plan_service.dispatch()` 的分工（两者都在，别搞混）：
  · `dispatch()` 只出**调度方案说明**——命中哪些、按什么顺序、为什么，纯规则；
  · 本模块       真**执行**：按方案逐个调用子智能体的真实能力并汇总结果。

三条不可让步的约束（对应项目红线）：

1. **高危不进链路**。命中医疗干预/诊断/报告解读/孕婴时，在**分类阶段**就返回拒答，
   一次子智能体都不调用。

2. **不让 LLM 二次编造**。汇总只做**结构化拼接**——每个子通道的原始返回、引用、
   耗时、降级原因原样带出。

3. **失败如实上报**。子通道抛异常就标 `ok=false` 并把原因带出来。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from app.agents.aggregate import aggregate_steps
from app.agents.subgraphs import get_subgraph, subgraph_names
from app.agents.supervisor import STRATEGY, get_supervisor_graph
from app.agent_runtime.contract import AgentPlan, AgentStep
from app.agent_runtime.runner import run_graph_task
from app.core.errors import BizError, ErrCode

logger = logging.getLogger("OrchestratorService")


# --------------------------------------------------------------------------- #
# 子智能体执行器（薄适配层，指向 LangGraph 子图）
# --------------------------------------------------------------------------- #
def _make_executor(agent: str) -> Callable[[str, Optional[dict]], Any]:
    async def executor(text: str, user: Optional[dict]) -> Dict[str, Any]:
        graph = get_subgraph(agent)
        if graph is None:
            raise BizError(ErrCode.AGENT_UNKNOWN, f"子智能体 {agent} 未注册执行图")
        out = await graph.ainvoke({"text": text, "user": user})
        step = out.get("step") or {}
        return {
            "summary": step.get("summary", ""),
            "data": step.get("data"),
            "degraded": bool(step.get("degraded")),
            "note": step.get("note", ""),
        }

    return executor


EXECUTORS: Dict[str, Callable[[str, Optional[dict]], Any]] = {
    name: _make_executor(name) for name in subgraph_names()
}


# --------------------------------------------------------------------------- #
# 单步执行（保留原接口：单测靠它做故障注入）
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


# 兼容别名：汇总逻辑已抽到 app/agents/aggregate.py，这里保留旧名字
_aggregate = aggregate_steps


# --------------------------------------------------------------------------- #
# 完整编排
# --------------------------------------------------------------------------- #
async def orchestrate(text: str, user: Optional[dict], *,
                      max_agents: int = 3, execute: bool = True) -> Dict[str, Any]:
    """主智能体完整编排：识别 → 规划 → 分派 → 汇总。

    `execute=False` 时只出方案不执行（用于预览与评测分类器本身）。
    执行引擎是 `app/agents/supervisor.py` 的 LangGraph 图。
    """
    t0 = time.perf_counter()
    # 统一接入 Agent Runtime：主图仍由 LangGraph 执行，但任务、步骤、检查点和
    # trace 由 Runtime 记录。后续六个子 Agent 复用同一个入口，不再各自造状态。
    state = await run_graph_task(
        "orchestrator", text, get_supervisor_graph(), user=user,
        context={"max_agents": max_agents, "execute": execute},
        plan=AgentPlan(
            goal=text,
            confidence=1.0,
            reason="LangGraph supervisor 兼容接入 Agent Runtime",
            steps=[AgentStep(step_id="supervisor.execute", order=1,
                             action="执行意图识别、计划、分派和汇总",
                             tool_name="agent.delegate")],
        ),
        graph_input={
            "execute": execute,
            "max_agents": max_agents,
            "steps": [],
        },
    )

    intent = state.get("intent") or {}
    plan = state.get("plan") or []
    blocked = bool(intent.get("blocked"))

    # 步骤按调度方案顺序呈现（执行是并行的，呈现必须有确定次序）
    order = {item.get("agent"): idx for idx, item in enumerate(plan)}
    steps = sorted(state.get("steps") or [],
                   key=lambda s: order.get(s.get("agent"), len(order)))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    payload = {
        "request": text,
        "intent": intent,
        "plan": plan,
        "steps": steps,
        "executed": bool(execute and not blocked),
        "strategy": STRATEGY,
        "result": state.get("result") or aggregate_steps([]),
        "elapsed_ms": elapsed_ms,
        "engine": "langgraph-supervisor",
    }
    # run_graph_task 已将任务标识写入 state，保留在对外结果中，便于前端查询任务时间线。
    if isinstance(state, dict) and state.get("agent_task"):
        payload["agent_task"] = state["agent_task"]
    return payload
