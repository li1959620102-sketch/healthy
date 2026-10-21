# -*- coding: utf-8 -*-
"""统一工具执行器：权限、重试、耗时和调用审计都在这里完成。"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agent_runtime.contract import ToolCallResult
from app.agent_runtime.errors import AgentApprovalRequired, RetryableAgentError
from app.agent_runtime.approval import approval_manager
from app.agent_runtime.policy import authorize
from app.agent_runtime.task_manager import task_manager
from app.agent_runtime.tool_registry import get_tool
from app.agent_runtime.telemetry import telemetry
from app.agent_runtime.pricing import estimate_cost

ToolHandler = Callable[..., Any]
_HANDLERS: Dict[str, ToolHandler] = {}


def register_handler(tool_name: str, handler: ToolHandler) -> None:
    """注册跨 Agent 复用的工具实现。"""
    _HANDLERS[tool_name] = handler


def handler_registered(tool_name: str) -> bool:
    return tool_name in _HANDLERS


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (RetryableAgentError, TimeoutError, ConnectionError))


async def execute_tool(agent_key: str, tool_name: str, arguments: Optional[dict] = None,
                       *, task_id: Optional[str] = None, step_id: str = "tool.execute",
                       user: Optional[dict] = None, handler: Optional[ToolHandler] = None,
                       max_retries: Optional[int] = None) -> ToolCallResult:
    """执行一个注册工具。

    handler 参数用于现有业务服务的兼容接入；工具名、权限和调用记录仍统一经过
    Runtime。后续业务服务稳定后，可把 handler 移入注册表并删除兼容参数。
    """
    args = dict(arguments or {})
    spec = get_tool(agent_key, tool_name)
    if task_id:
        task = task_manager.get(task_id)
        if task:
            max_calls = int((task.context or {}).get("sandbox_max_tool_calls", 100))
            if task_manager.task_tool_calls(task_id) >= max_calls:
                message = f"Agent 沙箱工具调用配额已用尽：{max_calls}"
                task_manager.record_tool_call(
                    task_id, step_id, tool_name, args, success=False,
                    permission_result="sandbox_quota_exceeded", error_message=message,
                )
                return ToolCallResult(tool_name, False, error="AGENT_SANDBOX_QUOTA_EXCEEDED")
            max_runtime = float((task.context or {}).get("sandbox_max_runtime_seconds", 900))
            started_at = float((task.context or {}).get("sandbox_started_at", time.time()))
            if time.time() - started_at >= max_runtime:
                message = f"Agent 沙箱运行时配额已用尽：{int(max_runtime)} 秒"
                task_manager.record_tool_call(
                    task_id, step_id, tool_name, args, success=False,
                    permission_result="sandbox_quota_exceeded", error_message=message,
                )
                return ToolCallResult(tool_name, False, error="AGENT_SANDBOX_RUNTIME_EXCEEDED")
        budget = (task.context or {}).get("cost_budget_usd") if task else None
        if budget is not None and task_manager.task_cost(task_id) >= float(budget):
            message = f"Agent 成本预算已用尽：{task_manager.task_cost(task_id):.6f} / {float(budget):.6f} USD"
            task_manager.record_tool_call(
                task_id, step_id, tool_name, args, success=False,
                permission_result="cost_budget_exceeded", error_message=message,
            )
            return ToolCallResult(tool_name, False, error="AGENT_COST_BUDGET_EXCEEDED")
    decision = authorize(agent_key, tool_name, user)
    if not decision.allowed:
        if task_id:
            task_manager.record_tool_call(task_id, step_id, tool_name, args,
                                          success=False, permission_result="denied",
                                          error_message=decision.reason)
        raise PermissionError(decision.reason)
    already_approved = bool(task_id and approval_manager.is_approved(task_id, step_id, tool_name))
    if decision.requires_approval and not already_approved:
        approval = approval_manager.request(
            task_id, step_id, tool_name, risk_level=(spec.risk_level if spec else "medium"),
            requested_by=str((user or {}).get("sub")) if user else None,
        ) if task_id else None
        if task_id:
            task_manager.record_tool_call(task_id, step_id, tool_name, args,
                                          success=False, permission_result="approval_required",
                                          error_message=f"等待人工审批：{approval.approval_id if approval else ''}")
        raise AgentApprovalRequired(f"工具需要人工审批：{tool_name}")

    fn = handler or _HANDLERS.get(tool_name)
    if fn is None:
        raise LookupError(f"工具未注册执行实现：{tool_name}")
    # 兼容现有业务图传入的 handler：首次真实调用后登记同一实现，后续调用不再
    # 依赖调用方临时传入，便于运行时能力盘点和统一治理。
    if handler is not None:
        _HANDLERS.setdefault(tool_name, handler)
    if task_id and spec and spec.replay_safe:
        cached = task_manager.replayed_tool_result(task_id, step_id, tool_name)
        if cached is not None:
            task = task_manager.get(task_id)
            if task is not None:
                task_manager._event(task, "tool.replayed", {
                    "step_id": step_id, "tool_name": tool_name,
                })
            return ToolCallResult(tool_name, True, cached, attempts=0, replayed=True)
    attempts_limit = max(0, int(max_retries if max_retries is not None else (spec.max_retries if spec else 0)))
    started = time.perf_counter()
    error: Optional[Exception] = None
    for attempt in range(attempts_limit + 1):
        try:
            call_args = dict(args)
            call_args.pop("_attempts", None)
            if inspect.iscoroutinefunction(fn):
                operation = fn(**call_args)
            else:
                operation = asyncio.to_thread(fn, **call_args)
            timeout = float(spec.timeout_seconds if spec and spec.timeout_seconds else 0)
            value = await asyncio.wait_for(operation, timeout=timeout) if timeout > 0 else await operation
            elapsed = int((time.perf_counter() - started) * 1000)
            result = ToolCallResult(tool_name, True, value, attempts=attempt + 1, latency_ms=elapsed)
            usage = value.get("_usage") if isinstance(value, dict) else None
            model_name = (usage or {}).get("model_name")
            estimated_cost = (usage or {}).get("estimated_cost")
            if estimated_cost is None:
                estimated_cost = estimate_cost(model_name, usage)
            telemetry.record_tool(agent_key, tool_name, success=True, elapsed_ms=elapsed,
                                 attempts=attempt + 1,
                                 prompt_tokens=(usage or {}).get("prompt_tokens"),
                                 completion_tokens=(usage or {}).get("completion_tokens"),
                                 estimated_cost=estimated_cost)
            if task_id:
                task_manager.record_tool_call(task_id, step_id, tool_name,
                                              {**args, "_attempts": attempt + 1}, value,
                                              success=True, latency_ms=elapsed,
                                              model_name=model_name,
                                              prompt_tokens=(usage or {}).get("prompt_tokens"),
                                              completion_tokens=(usage or {}).get("completion_tokens"),
                                              total_tokens=(usage or {}).get("total_tokens"),
                                              estimated_cost=estimated_cost)
            return result
        except Exception as exc:  # noqa: BLE001
            error = exc
            if not _retryable(exc) or attempt >= attempts_limit:
                break
            await asyncio.sleep(min(0.25 * (2 ** attempt), 2.0))
    elapsed = int((time.perf_counter() - started) * 1000)
    message = str(error) if error else "工具执行失败"
    if task_id:
        task_manager.record_tool_call(task_id, step_id, tool_name,
                                      {**args, "_attempts": attempts_limit + 1},
                                      success=False, latency_ms=elapsed,
                                      error_message=message)
    telemetry.record_tool(agent_key, tool_name, success=False, elapsed_ms=elapsed,
                         attempts=attempts_limit + 1)
    return ToolCallResult(tool_name, False, error=message,
                          attempts=attempts_limit + 1, latency_ms=elapsed)
