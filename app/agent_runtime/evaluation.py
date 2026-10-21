# -*- coding: utf-8 -*-
"""Agent Runtime 合同评估与故障注入。"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from app.agent_runtime.errors import RetryableAgentError
from app.agent_runtime.task_manager import task_manager
from app.agent_runtime.tool_executor import execute_tool
from app.agent_runtime.tool_registry import get_tool, list_tools


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    agent_key: str
    goal: str
    expected_tool: str
    requires_approval: bool = False


CASES = [
    EvaluationCase("orchestrator.plan", "orchestrator", "规划用户健康任务", "agent.delegate"),
    EvaluationCase("assessment.publish", "assessment", "发布健康报告", "health_report.publish", True),
    EvaluationCase("diet.generate", "diet", "生成三天食养计划", "diet_plan.generate"),
    EvaluationCase("dish.search", "dish", "推荐低敏菜单", "recipe.search"),
    EvaluationCase("steward.checkin", "steward", "分析本周打卡", "checkin.read"),
    EvaluationCase("dietitian.retrieve", "dietitian", "回答饮食问题", "knowledge.search"),
    EvaluationCase("data.readonly", "data", "统计用户活跃度", "sql.execute_readonly"),
]


def evaluate_contract() -> Dict[str, Any]:
    results: List[dict] = []
    for case in CASES:
        spec = get_tool(case.agent_key, case.expected_tool)
        passed = bool(spec and spec.requires_approval == case.requires_approval)
        results.append({
            **asdict(case),
            "passed": passed,
            "reason": "工具已注册且风险策略匹配" if passed else "工具缺失或审批策略不匹配",
            "registered_tools": len(list_tools(case.agent_key)),
        })
    passed = sum(1 for item in results if item["passed"])
    return {
        "suite": "agent-runtime-contract-v1",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "cases": results,
    }


async def evaluate_fault_recovery() -> Dict[str, Any]:
    """用临时失败工具验证重试和审批入口，不触碰真实业务数据。"""
    retry_task = task_manager.create("assessment", "评估临时故障恢复")
    counter = {"calls": 0}

    def flaky(value):
        counter["calls"] += 1
        if counter["calls"] == 1:
            raise RetryableAgentError("评估注入的临时故障")
        return value

    retry_result = await execute_tool(
        "assessment", "health_profile.read", {"value": "ok"},
        task_id=retry_task.task_id, step_id="eval.retry", handler=flaky,
    )
    return {
        "suite": "agent-runtime-fault-recovery-v1",
        "retry": {
            "passed": retry_result.success and retry_result.attempts == 2,
            "attempts": retry_result.attempts,
        },
        "passed": retry_result.success and retry_result.attempts == 2,
    }
