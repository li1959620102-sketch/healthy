# -*- coding: utf-8 -*-
"""把现有一次性 LangGraph 调用包成可追踪 Agent 任务。"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agent_runtime.contract import AgentPlan, AgentStatus, AgentStep
from app.agent_runtime.errors import AgentApprovalRequired
from app.agent_runtime.task_manager import task_manager
from app.agent_runtime.policy import authorize
from app.agent_runtime.approval import approval_manager
from app.agent_runtime.memory import memory_manager
from app.agent_runtime.telemetry import telemetry

_RESUMERS: Dict[str, Callable[[], Awaitable[Dict[str, Any]]]] = {}


async def run_graph_task(agent_key: str, goal: str, graph: Any, *,
                         user: Optional[dict] = None,
                         context: Optional[dict] = None,
                         plan: Optional[AgentPlan] = None,
                         graph_input: Optional[dict] = None,
                         task_id: Optional[str] = None) -> Dict[str, Any]:
    """执行一个现有子图，同时记录任务、步骤、结果和 trace。

    这是第一阶段的兼容适配器：业务图仍由 LangGraph 执行，Runtime 先接管任务
    生命周期；后续再把工具权限、审批和 MySQL 持久化接入同一入口。
    """
    task = task_manager.get(task_id) if task_id else None
    if task is None:
        task_context = dict(context or {})
        task_context.setdefault("memory", memory_manager.context(
            user_id=str((user or {}).get("sub")) if user else None,
            agent_key=agent_key,
        ))
        task = task_manager.create(agent_key, goal,
                                   user_id=str((user or {}).get("sub")) if user else None,
                                   context=task_context)
    if plan is None:
        plan = AgentPlan(goal=goal, confidence=1.0, reason="现有业务图兼容接入",
                         steps=[AgentStep(step_id="graph.execute", order=1,
                                          action="执行业务 Agent 图")])
    if task.plan is None:
        task_manager.set_plan(task.task_id, plan)
    else:
        plan = task.plan
    _RESUMERS[task.task_id] = lambda: run_graph_task(
        agent_key, goal, graph, user=user, context=context, plan=plan,
        graph_input=graph_input, task_id=task.task_id,
    )
    step = plan.steps[0]
    lock_token = task_manager.acquire_execution_lock(task.task_id)
    if lock_token is None:
        raise RuntimeError("Agent 任务正在其他 worker 执行中")
    decision = authorize(agent_key, step.tool_name, user) if step.tool_name else None
    started = time.perf_counter()
    try:
        if decision is not None and not decision.allowed:
            task_manager.fail(task.task_id, RuntimeError(decision.reason))
            raise PermissionError(decision.reason)
        if (decision is not None and decision.requires_approval
                and not approval_manager.is_approved(task.task_id, step.step_id, step.tool_name or "")):
            approval = approval_manager.request(
                task.task_id, step.step_id, step.tool_name or "agent.step",
                risk_level="high", requested_by=str((user or {}).get("sub")) if user else None,
            )
            return {
                "agent_task": {
                    "task_id": task.task_id, "trace_id": task.trace_id,
                    "status": AgentStatus.WAITING_APPROVAL.value,
                    "current_step": step.step_id,
                },
                "approval_required": True,
                "approval_id": approval.approval_id,
            }
        step.status = "running"
        step.attempt = 1
        task_manager.set_status(task.task_id, AgentStatus.RUNNING)
        task_manager.update_step(task.task_id, step)
        payload = dict(graph_input or {})
        payload.setdefault("text", goal)
        payload.setdefault("user", user)
        payload.setdefault("runtime_task_id", task.task_id)
        payload.setdefault("memory_context", task.context.get("memory", {}))
        out = await graph.ainvoke(payload)
        step.status = "completed"
        task_manager.update_step(task.task_id, step, output=out.get("step") if isinstance(out, dict) else out)
        result = task_manager.complete(task.task_id, out)
        # 只保存结构化摘要和引用元数据，不把完整模型输出无条件写入长期记忆。
        if isinstance(out, dict):
            memory_manager.remember(
                agent_key, "summary",
                {"goal": goal, "status": "completed", "step": out.get("step")},
                user_id=str((user or {}).get("sub")) if user else None,
                task_id=task.task_id, source="agent_runtime", ttl_seconds=86400,
            )
        _RESUMERS.pop(task.task_id, None)
        data = result.result or {}
        if isinstance(data, dict):
            data = dict(data)
            data["agent_task"] = {
                "task_id": result.task_id, "trace_id": result.trace_id,
                "status": result.status.value, "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        telemetry.record_task(agent_key, AgentStatus.COMPLETED.value,
                              int((time.perf_counter() - started) * 1000))
        return data
    except Exception as exc:
        if isinstance(exc, AgentApprovalRequired):
            task_manager.set_status(task.task_id, AgentStatus.WAITING_APPROVAL,
                                    current_step=step.step_id)
            telemetry.record_task(agent_key, AgentStatus.WAITING_APPROVAL.value,
                                  int((time.perf_counter() - started) * 1000))
            current = task_manager.get(task.task_id)
            return {
                "agent_task": {
                    "task_id": task.task_id, "trace_id": task.trace_id,
                    "status": AgentStatus.WAITING_APPROVAL.value,
                    "current_step": step.step_id,
                },
                "approval_required": True,
            }
        step.status = "failed"
        task_manager.update_step(task.task_id, step, error=exc)
        task_manager.fail(task.task_id, exc)
        telemetry.record_task(agent_key, AgentStatus.FAILED.value,
                              int((time.perf_counter() - started) * 1000))
        raise
    finally:
        task_manager.release_execution_lock(task.task_id, lock_token)


async def resume_task(task_id: str) -> Dict[str, Any]:
    """从审批暂停点恢复当前任务的执行入口。"""
    callback = _RESUMERS.get(task_id)
    if callback is not None:
        return await callback()

    # 进程重启后的恢复：从 MySQL 读取任务和计划，重新装载对应 Agent 图。
    task = task_manager.get(task_id)
    if task is None:
        raise KeyError(f"任务没有可恢复的执行上下文：{task_id}")
    if task.agent_key == "orchestrator":
        from app.agents.supervisor import get_supervisor_graph
        graph = get_supervisor_graph()
    else:
        from app.agents.subgraphs import get_subgraph
        graph = get_subgraph(task.agent_key)
    if graph is None:
        raise KeyError(f"Agent 图不存在，无法恢复：{task.agent_key}")
    return await run_graph_task(
        task.agent_key, task.goal, graph,
        user={"sub": task.user_id} if task.user_id else None,
        context=task.context, plan=task.plan, task_id=task.task_id,
        graph_input={"execute": task.context.get("execute", True),
                     "max_agents": task.context.get("max_agents", 3)},
    )
