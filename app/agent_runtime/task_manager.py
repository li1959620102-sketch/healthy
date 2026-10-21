# -*- coding: utf-8 -*-
"""统一 Agent 任务管理器。

这是六个业务 Agent 共享的最小执行骨架：创建任务、保存计划、推进状态、写检查点、
记录事件。它不替代业务 Agent 的 LangGraph，而是为 LangGraph 提供可持久化的外壳。
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from app.agent_runtime.checkpoint import checkpoint_store
from app.agent_runtime.contract import (
    AgentEvent, AgentPlan, AgentResult, AgentStatus, AgentTask, AgentStep,
)
from app.agent_runtime.trace import trace_store

logger = logging.getLogger("AgentTaskManager")
_DB_DISABLED_UNTIL = 0.0


def db_persistence_allowed() -> bool:
    return time.time() >= _DB_DISABLED_UNTIL


def _disable_db_persistence() -> None:
    global _DB_DISABLED_UNTIL
    _DB_DISABLED_UNTIL = time.time() + 30.0


class AgentTaskManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: Dict[str, AgentTask] = {}
        self._tool_results: Dict[tuple, Any] = {}
        self._task_costs: Dict[str, float] = {}
        self._task_tool_calls: Dict[str, int] = {}
        self._execution_locks: Dict[str, str] = {}

    def create(self, agent_key: str, goal: str, *, user_id: Optional[str] = None,
               parent_task_id: Optional[str] = None, context: Optional[dict] = None,
               max_retries: int = 2) -> AgentTask:
        task_id = "task_" + uuid.uuid4().hex
        task_context = copy.deepcopy(context or {})
        if "cost_budget_usd" not in task_context:
            raw_budget = os.getenv("AGENT_COST_BUDGET_USD", "")
            if raw_budget:
                try:
                    task_context["cost_budget_usd"] = max(0.0, float(raw_budget))
                except ValueError:
                    pass
        if "sandbox_max_tool_calls" not in task_context:
            try:
                task_context["sandbox_max_tool_calls"] = max(1, int(os.getenv("AGENT_SANDBOX_MAX_TOOL_CALLS", "100")))
            except ValueError:
                task_context["sandbox_max_tool_calls"] = 100
        if "sandbox_max_runtime_seconds" not in task_context:
            try:
                task_context["sandbox_max_runtime_seconds"] = max(1, int(os.getenv("AGENT_SANDBOX_MAX_RUNTIME_SECONDS", "900")))
            except ValueError:
                task_context["sandbox_max_runtime_seconds"] = 900
        task_context.setdefault("sandbox_started_at", time.time())
        task = AgentTask(
            task_id=task_id, agent_key=agent_key, goal=goal, user_id=user_id,
            parent_task_id=parent_task_id, context=task_context,
            max_retries=max(0, int(max_retries)), trace_id=trace_store.new_trace_id(),
        )
        with self._lock:
            self._tasks[task_id] = task
        self._persist_task(task)
        self._event(task, "task.created", {"agent": agent_key, "goal": goal})
        self._checkpoint(task)
        return copy.deepcopy(task)

    def get(self, task_id: str) -> Optional[AgentTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return copy.deepcopy(task)
        # 进程重启后允许从 MySQL 读取任务元数据；完整计划恢复将在下一阶段补充。
        if not db_persistence_allowed():
            return None
        try:
            from app.models.mysql.platform import AgentTask as AgentTaskModel
            from app.utils.db import get_session
            session = get_session()
            row = session.query(AgentTaskModel).filter_by(task_id=task_id).first()
            session.close()
            if row is None:
                return None
            restored = AgentTask(
                task_id=row.task_id, agent_key=row.agent_key, goal=row.goal,
                user_id=row.user_id, parent_task_id=row.parent_task_id,
                status=AgentStatus(row.status), current_step=row.current_step,
                context=row.context_snapshot_json or {}, result=row.result_json,
                error_code=row.error_code, error_message=row.error_message,
                retry_count=row.retry_count or 0, max_retries=row.max_retries or 2,
                trace_id=row.trace_id,
            )
            plan_json = row.plan_json or {}
            if plan_json:
                restored.plan = AgentPlan(
                    goal=plan_json.get("goal") or row.goal,
                    planner=plan_json.get("planner", "restored"),
                    confidence=float(plan_json.get("confidence", 0.0)),
                    reason=plan_json.get("reason", "从持久化任务恢复"),
                    steps=[AgentStep(
                        step_id=item.get("step_id", f"step-{idx}"),
                        order=int(item.get("order", idx)),
                        action=item.get("action", "恢复任务步骤"),
                        tool_name=item.get("tool_name"),
                        status=item.get("status", "pending"),
                        input_snapshot=item.get("input_snapshot") or {},
                        output_snapshot=item.get("output_snapshot"),
                        attempt=int(item.get("attempt", 0)),
                        error_code=item.get("error_code"),
                        error_message=item.get("error_message"),
                    ) for idx, item in enumerate(plan_json.get("steps") or [], 1)],
                )
            with self._lock:
                self._tasks[task_id] = restored
            return copy.deepcopy(restored)
        except Exception as exc:
            logger.debug("Agent 任务读取失败：%s", exc)
            return None

    def set_status(self, task_id: str, status: AgentStatus, **fields: Any) -> AgentTask:
        with self._lock:
            task = self._require(task_id)
            task.status = status
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            snapshot = copy.deepcopy(task)
        self._event(task, "task.status", {"status": status.value})
        self._persist_task(task)
        self._checkpoint(task)
        return snapshot

    def set_plan(self, task_id: str, plan: AgentPlan) -> AgentTask:
        with self._lock:
            task = self._require(task_id)
            task.plan = copy.deepcopy(plan)
            task.status = AgentStatus.PLANNED
            snapshot = copy.deepcopy(task)
        self._event(task, "task.planned", {"step_count": len(plan.steps)})
        self._persist_task(task)
        for step in plan.steps:
            self._persist_step(task, step)
        self._checkpoint(task)
        return snapshot

    def update_step(self, task_id: str, step: AgentStep, *,
                    output: Any = None, error: Optional[Exception] = None) -> AgentTask:
        with self._lock:
            task = self._require(task_id)
            task.current_step = step.step_id
            if task.plan:
                for item in task.plan.steps:
                    if item.step_id == step.step_id:
                        item.status = step.status
                        item.attempt = step.attempt
                        item.output_snapshot = copy.deepcopy(output)
                        if error:
                            item.error_code = getattr(error, "code", type(error).__name__)
                            item.error_message = str(error)
                        break
            snapshot = copy.deepcopy(task)
        event = "step.failed" if error else "step.updated"
        self._event(task, event, {"step_id": step.step_id, "status": step.status,
                                  "attempt": step.attempt})
        self._persist_task(task)
        self._persist_step(task, step)
        self._checkpoint(task)
        return snapshot

    def complete(self, task_id: str, result: Any) -> AgentResult:
        task = self.set_status(task_id, AgentStatus.COMPLETED, result=copy.deepcopy(result))
        self._event(task, "task.completed", {})
        return self.result(task)

    def fail(self, task_id: str, error: Exception, *, status: AgentStatus = AgentStatus.FAILED) -> AgentResult:
        task = self.set_status(task_id, status,
                               error_code=getattr(error, "code", type(error).__name__),
                               error_message=str(error))
        self._event(task, "task.failed", {"error": str(error)})
        return self.result(task)

    async def cancel(self, task_id: str, reason: str = "用户取消任务") -> AgentResult:
        """取消任务，并对已完成且注册了补偿动作的步骤逆序补偿。"""
        task = self._require(task_id)
        from app.agent_runtime.compensation import compensate
        compensations = []
        for step in reversed(task.plan.steps if task.plan else []):
            if step.status == "completed":
                compensations.append(await compensate(step.tool_name, {
                    "task_id": task_id, "step_id": step.step_id,
                    "input_snapshot": step.input_snapshot,
                    "output_snapshot": step.output_snapshot,
                }))
        self._event(task, "task.compensation", {"items": compensations})
        self.set_status(task_id, AgentStatus.CANCELLED,
                        error_code="AGENT_TASK_CANCELLED", error_message=reason,
                        result={"compensations": compensations})
        return self.result(self._require(task_id))

    def result(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent_key=task.agent_key, status=task.status,
            result=copy.deepcopy(task.result),
            steps=copy.deepcopy(task.plan.steps if task.plan else []),
            trace_id=task.trace_id,
            error=({"code": task.error_code, "message": task.error_message}
                    if task.error_code else None),
        )

    def trace(self, task_id: str) -> List[dict]:
        task = self._require(task_id)
        return trace_store.list(task.trace_id or "")

    def record_tool_call(self, task_id: str, step_id: str, tool_name: str,
                         arguments: dict, result: Any = None, *, success: bool,
                         latency_ms: int = 0, error_message: Optional[str] = None,
                         permission_result: str = "allowed", model_name: Optional[str] = None,
                         prompt_tokens: Optional[int] = None,
                         completion_tokens: Optional[int] = None,
                         total_tokens: Optional[int] = None,
                         estimated_cost: Optional[float] = None) -> None:
        """记录工具调用，内存 trace 与数据库审计表同时写入。"""
        task = self._require(task_id)
        if estimated_cost is not None:
            with self._lock:
                self._task_costs[task_id] = self._task_costs.get(task_id, 0.0) + float(estimated_cost)
        with self._lock:
            self._task_tool_calls[task_id] = self._task_tool_calls.get(task_id, 0) + 1
        if success:
            with self._lock:
                self._tool_results[(task_id, step_id, tool_name)] = copy.deepcopy(result)
        self._event(task, "tool.call", {
            "step_id": step_id, "tool_name": tool_name, "success": success,
            "attempts": arguments.get("_attempts", 1), "latency_ms": latency_ms,
            "error": error_message,
        })
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentToolCall
            from app.utils.db import get_session
            session = get_session()
            session.add(AgentToolCall(
                task_id=task_id, step_id=step_id, tool_name=tool_name,
                arguments_json={k: v for k, v in arguments.items() if k != "_attempts"},
                result_json=result, permission_result=permission_result,
                latency_ms=latency_ms, model_name=model_name,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=total_tokens, estimated_cost=estimated_cost,
                success=success, error_message=error_message,
            ))
            session.commit()
            session.close()
        except Exception as exc:
            logger.debug("Agent 工具调用落库失败：%s", exc)
            _disable_db_persistence()

    def task_cost(self, task_id: str) -> float:
        """返回当前任务已确认的显式成本；没有上报时保持为 0，不猜测价格。"""
        with self._lock:
            return round(float(self._task_costs.get(task_id, 0.0)), 6)

    def task_tool_calls(self, task_id: str) -> int:
        """返回任务已记录的工具调用次数，用于任务级沙箱配额。"""
        with self._lock:
            return int(self._task_tool_calls.get(task_id, 0))

    def acquire_execution_lock(self, task_id: str, ttl_seconds: int = 300) -> Optional[str]:
        """获取任务执行锁；Redis 可用时跨 worker 互斥，本地集合作为单进程兜底。"""
        import uuid as _uuid
        token = "lock_" + _uuid.uuid4().hex
        try:
            from app.utils.redis_client import redis_client
            result = redis_client.set_nx(f"agent:task-lock:{task_id}", token, ttl_seconds)
            if result is False:
                return None
            if result is True:
                return token
        except Exception:
            pass
        with self._lock:
            if task_id in self._execution_locks:
                return None
            self._execution_locks[task_id] = token
        return token

    def release_execution_lock(self, task_id: str, token: Optional[str]) -> None:
        if not token:
            return
        try:
            from app.utils.redis_client import redis_client
            if redis_client.get(f"agent:task-lock:{task_id}") == token:
                redis_client.delete(f"agent:task-lock:{task_id}")
        except Exception:
            pass
        with self._lock:
            if self._execution_locks.get(task_id) == token:
                self._execution_locks.pop(task_id, None)

    def replayed_tool_result(self, task_id: str, step_id: str, tool_name: str):
        """读取任务中已经成功的工具结果，供安全重放工具复用。"""
        with self._lock:
            key = (task_id, step_id, tool_name)
            if key in self._tool_results:
                return copy.deepcopy(self._tool_results[key])
        if not db_persistence_allowed():
            return None
        try:
            from app.models.mysql.platform import AgentToolCall
            from app.utils.db import get_session
            session = get_session()
            row = (session.query(AgentToolCall)
                   .filter_by(task_id=task_id, step_id=step_id,
                              tool_name=tool_name, success=True)
                   .order_by(AgentToolCall.id.desc()).first())
            value = row.result_json if row is not None else None
            session.close()
            if row is not None:
                with self._lock:
                    self._tool_results[key] = copy.deepcopy(value)
            return value
        except Exception:
            return None

    def save_node_checkpoint(self, task_id: str, step_id: str, status: str, output: Any = None) -> None:
        """保存 LangGraph 内部节点检查点，供进程重启后的节点重放使用。"""
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentStep
            from app.utils.db import get_session
            session = get_session()
            row = session.query(AgentStep).filter_by(task_id=task_id, step_id=step_id).first()
            if row is None:
                row = AgentStep(task_id=task_id, step_id=step_id,
                                step_order=0, action="LangGraph 节点")
                session.add(row)
            row.status = status
            row.output_snapshot = output
            session.commit()
            session.close()
        except Exception:
            _disable_db_persistence()

    def load_node_checkpoint(self, task_id: str, step_id: str):
        if not db_persistence_allowed():
            return None
        try:
            from app.models.mysql.platform import AgentStep
            from app.utils.db import get_session
            session = get_session()
            row = session.query(AgentStep).filter_by(task_id=task_id, step_id=step_id).first()
            value = {"status": row.status, "output": row.output_snapshot} if row else None
            session.close()
            return value
        except Exception:
            return None

    def _require(self, task_id: str) -> AgentTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Agent 任务不存在：{task_id}")
        return task

    def _event(self, task: AgentTask, event_type: str, payload: dict) -> None:
        event = AgentEvent(task_id=task.task_id, trace_id=task.trace_id or "",
                           event_type=event_type, payload=payload)
        trace_store.append(event)
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentEvent as AgentEventModel
            from app.utils.db import get_session
            session = get_session()
            session.add(AgentEventModel(
                task_id=event.task_id, trace_id=event.trace_id,
                span_id=event.span_id, event_type=event.event_type,
                payload_json=event.payload,
            ))
            session.commit()
            session.close()
        except Exception as exc:  # 数据库不可用时保留内存 trace，不阻断业务任务
            logger.debug("Agent 事件落库失败：%s", exc)
            _disable_db_persistence()

    @staticmethod
    def _json_task(task: AgentTask) -> dict:
        """把 dataclass 状态转换为 SQLAlchemy JSON 可接受的值。"""
        value = asdict(task)
        value["status"] = task.status.value
        if task.plan:
            value["plan"] = asdict(task.plan)
            value["plan"]["steps"] = [asdict(item) for item in task.plan.steps]
        return value

    def _persist_task(self, task: AgentTask) -> None:
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentTask as AgentTaskModel
            from app.utils.db import get_session
            value = self._json_task(task)
            session = get_session()
            row = session.query(AgentTaskModel).filter_by(task_id=task.task_id).first()
            if row is None:
                row = AgentTaskModel(task_id=task.task_id)
                session.add(row)
            row.agent_key = task.agent_key
            row.parent_task_id = task.parent_task_id
            row.user_id = task.user_id
            row.goal = task.goal
            row.status = task.status.value
            row.current_step = task.current_step
            row.plan_json = value.get("plan")
            row.context_snapshot_json = task.context
            row.result_json = task.result
            row.error_code = task.error_code
            row.error_message = task.error_message
            row.retry_count = task.retry_count
            row.max_retries = task.max_retries
            row.trace_id = task.trace_id
            session.commit()
            session.close()
        except Exception as exc:
            logger.debug("Agent 任务落库失败：%s", exc)
            _disable_db_persistence()

    @staticmethod
    def _persist_step(task: AgentTask, step: AgentStep) -> None:
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentStep as AgentStepModel
            from app.utils.db import get_session
            session = get_session()
            row = (session.query(AgentStepModel)
                   .filter_by(task_id=task.task_id, step_id=step.step_id).first())
            if row is None:
                row = AgentStepModel(task_id=task.task_id, step_id=step.step_id)
                session.add(row)
            row.step_order = step.order
            row.action = step.action
            row.tool_name = step.tool_name
            row.status = step.status
            row.input_snapshot = step.input_snapshot
            row.output_snapshot = step.output_snapshot
            row.attempt = step.attempt
            row.error_code = step.error_code
            row.error_message = step.error_message
            session.commit()
            session.close()
        except Exception:
            # 任务主状态已有内存检查点；数据库异常不应让业务响应变成 500。
            _disable_db_persistence()
            return

    def _checkpoint(self, task: AgentTask) -> None:
        checkpoint_store.save(task.task_id, asdict(task))


task_manager = AgentTaskManager()
