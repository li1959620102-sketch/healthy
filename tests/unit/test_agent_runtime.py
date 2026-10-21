# -*- coding: utf-8 -*-
"""统一 Agent Runtime 的最小行为验收。"""
import asyncio
import uuid

from app.agent_runtime.contract import AgentPlan, AgentStatus, AgentStep
from app.agent_runtime.errors import RetryableAgentError
from app.agent_runtime.approval import ApprovalManager
from app.agent_runtime.runner import run_graph_task
from app.agent_runtime.runner import resume_task
from app.agent_runtime.task_manager import AgentTaskManager, task_manager
from app.agent_runtime.tool_executor import execute_tool
from app.agent_runtime.memory import MemoryManager
from app.agent_runtime.checkpoint import checkpointed_node


class _FakeGraph:
    async def ainvoke(self, payload):
        return {"step": {"agent": "fake", "ok": True, "data": payload}}


def test_task_manager_records_plan_and_trace():
    manager = AgentTaskManager()
    task = manager.create("fake", "读取测试资料", user_id="u-1")
    manager.set_plan(task.task_id, AgentPlan(
        goal=task.goal,
        steps=[AgentStep("read", 1, "读取资料", tool_name="fixture.read")],
    ))

    current = manager.get(task.task_id)
    assert current.status == AgentStatus.PLANNED
    assert current.plan.steps[0].tool_name == "fixture.read"
    assert {event["event_type"] for event in manager.trace(task.task_id)} >= {
        "task.created", "task.planned",
    }


def test_graph_task_returns_runtime_metadata():
    result = asyncio.run(run_graph_task("fake", "执行测试图", _FakeGraph()))

    assert result["step"]["ok"] is True
    assert result["agent_task"]["status"] == AgentStatus.COMPLETED.value
    assert result["agent_task"]["task_id"].startswith("task_")
    assert result["agent_task"]["trace_id"].startswith("trace_")


def test_tool_executor_retries_transient_failure():
    task = task_manager.create("assessment", "重试工具", user_id="u-1")
    calls = {"count": 0}

    def flaky(value):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RetryableAgentError("临时失败")
        return {"value": value}

    result = asyncio.run(execute_tool(
        "assessment", "health_profile.read", {"value": 1},
        task_id=task.task_id, handler=flaky,
    ))
    assert result.success is True
    assert result.attempts == 2
    assert calls["count"] == 2
    assert any(e["event_type"] == "tool.call" for e in task_manager.trace(task.task_id))


def test_approval_state_machine_pauses_and_rejects_task():
    task = task_manager.create("assessment", "需要人工确认", user_id="u-2")
    approvals = ApprovalManager()
    item = approvals.request(task.task_id, "publish", "health_report.publish", "high")
    assert task_manager.get(task.task_id).status == AgentStatus.WAITING_APPROVAL
    approvals.decide(item.approval_id, False, "operator-1", "风险证据不足")
    current = task_manager.get(task.task_id)
    assert item.status == "rejected"
    assert current.status == AgentStatus.FAILED


def test_approved_tool_can_resume_existing_task():
    class ApprovalGraph:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, payload):
            self.calls += 1
            from app.agent_runtime.tool_executor import execute_tool
            result = await execute_tool(
                "assessment", "health_report.publish", {"report_id": 1},
                task_id=payload["runtime_task_id"], step_id="publish",
                user={"sub": "u-3", "role": "admin"},
                handler=lambda report_id: {"published": report_id},
            )
            return {"step": {"agent": "assessment", "ok": result.success}}

    async def scenario():
        graph = ApprovalGraph()
        first = await run_graph_task(
            "assessment", "发布报告", graph,
            user={"sub": "u-3", "role": "admin"},
            plan=AgentPlan(goal="发布报告", steps=[AgentStep(
                "publish", 1, "发布报告", tool_name="health_report.publish",
            )]),
        )
        assert first["approval_required"] is True
        task_id = first["agent_task"]["task_id"]
        from app.agent_runtime.approval import approval_manager
        approval = approval_manager.for_task(task_id)[-1]
        approval_manager.decide(approval.approval_id, True, "operator-1", "已核验")
        approval_manager.resume(approval.approval_id)
        result = await resume_task(task_id)
        assert result["step"]["ok"] is True

    asyncio.run(scenario())


def test_memory_is_partitioned_by_type_and_agent():
    memory = MemoryManager()
    memory.remember("dietitian", "preference", {"avoid": ["花生"]}, user_id="u-4")
    memory.remember("data", "tool_result", {"rows": 2}, user_id="u-4")

    context = memory.context(user_id="u-4", agent_key="dietitian")
    assert "preference" in context
    assert "tool_result" not in context
    assert context["preference"][0]["content"]["avoid"] == ["花生"]


def test_replay_safe_tool_reuses_successful_result():
    task = task_manager.create("assessment", "恢复读取", user_id="u-5")
    calls = {"count": 0}

    def read_profile():
        calls["count"] += 1
        return {"constitution": "平和"}

    first = asyncio.run(execute_tool(
        "assessment", "health_profile.read", {},
        task_id=task.task_id, step_id="profile", handler=read_profile,
    ))
    second = asyncio.run(execute_tool(
        "assessment", "health_profile.read", {},
        task_id=task.task_id, step_id="profile", handler=read_profile,
    ))
    assert first.success is True
    assert second.replayed is True
    assert calls["count"] == 1


def test_graph_node_checkpoint_skips_completed_node():
    calls = {"count": 0}

    def node(state):
        calls["count"] += 1
        return {"value": calls["count"]}

    wrapped = checkpointed_node("assessment.load", node)
    state = {"runtime_task_id": "task_checkpoint_test_" + uuid.uuid4().hex}
    first = asyncio.run(wrapped(state))
    second = asyncio.run(wrapped(state))
    assert first == {"value": 1}
    assert second == {"value": 1}
    assert calls["count"] == 1


def test_cost_budget_blocks_next_tool_call():
    task = task_manager.create(
        "assessment", "预算门禁", user_id="u-budget",
        context={"cost_budget_usd": 0.01},
    )
    task_manager.record_tool_call(
        task.task_id, "model", "health_profile.read", {},
        success=True, estimated_cost=0.01,
    )
    result = asyncio.run(execute_tool(
        "assessment", "health_profile.read", {},
        task_id=task.task_id, handler=lambda: {"should": "not-run"},
    ))
    assert result.success is False
    assert result.error == "AGENT_COST_BUDGET_EXCEEDED"


def test_cancel_task_records_compensation_attempts():
    manager = AgentTaskManager()
    task = manager.create("assessment", "取消任务", user_id="u-cancel")
    manager.set_plan(task.task_id, AgentPlan(
        goal=task.goal,
        steps=[AgentStep("write", 1, "写入", tool_name="fixture.write", status="completed")],
    ))
    result = asyncio.run(manager.cancel(task.task_id, "测试取消"))
    assert result.status == AgentStatus.CANCELLED
    assert result.result["compensations"][0]["reason"] == "no_compensation_registered"


def test_model_price_catalog_estimates_explicit_cost(monkeypatch):
    monkeypatch.setenv(
        "AGENT_MODEL_PRICES_JSON",
        '{"qwen":{"prompt_per_1k_usd":0.001,"completion_per_1k_usd":0.002}}',
    )
    from app.agent_runtime.pricing import estimate_cost
    assert estimate_cost("qwen", {"prompt_tokens": 1000, "completion_tokens": 500}) == 0.002


def test_task_execution_lock_is_exclusive():
    manager = AgentTaskManager()
    task = manager.create("diet", "锁测试")
    first = manager.acquire_execution_lock(task.task_id)
    assert first
    assert manager.acquire_execution_lock(task.task_id) is None
    manager.release_execution_lock(task.task_id, first)
    second = manager.acquire_execution_lock(task.task_id)
    assert second
    manager.release_execution_lock(task.task_id, second)


def test_tool_executor_enforces_sandbox_quota():
    task = task_manager.create("dietitian", "配额测试", context={"sandbox_max_tool_calls": 1})
    first = asyncio.run(execute_tool(
        "dietitian", "answer.safety_check", {"text": "ok"},
        task_id=task.task_id, handler=lambda text: {"ok": True},
    ))
    second = asyncio.run(execute_tool(
        "dietitian", "answer.safety_check", {"text": "ok"},
        task_id=task.task_id, handler=lambda text: {"ok": True},
    ))
    assert first.success is True
    assert second.success is False
    assert second.error == "AGENT_SANDBOX_QUOTA_EXCEEDED"
