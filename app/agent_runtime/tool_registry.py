# -*- coding: utf-8 -*-
"""Agent 工具注册表。

工具先注册再调用，后续所有真实 DB、知识库、模型和通知调用都必须经过这里。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    mode: str = "read"
    risk_level: str = "low"
    requires_approval: bool = False
    timeout_seconds: int = 10
    max_retries: int = 2
    replay_safe: bool = False


_TOOLS: Dict[str, List[ToolSpec]] = {
    "orchestrator": [
        ToolSpec("intent.classify", "识别目标和风险", timeout_seconds=3),
        ToolSpec("agent.delegate", "委派子任务", mode="delegate"),
        ToolSpec("agent.aggregate", "汇总子任务结果", timeout_seconds=5),
    ],
    "assessment": [
        ToolSpec("health_profile.read", "读取健康档案", replay_safe=True),
        ToolSpec("assessment_record.read", "读取历史测评", replay_safe=True),
        ToolSpec("health_risk.calculate", "计算健康风险", timeout_seconds=5, replay_safe=True),
        ToolSpec("health_report.publish", "发布健康报告", mode="write", risk_level="high", requires_approval=True),
    ],
    "diet": [
        ToolSpec("health_context.read", "读取饮食相关健康上下文", replay_safe=True),
        ToolSpec("food_knowledge.search", "检索食养知识", replay_safe=True),
        ToolSpec("recipe_library.search", "检索菜谱", replay_safe=True),
        ToolSpec("diet_plan.generate", "生成候选食养计划", mode="model", timeout_seconds=20),
        ToolSpec("diet_plan.publish", "发布食养计划", mode="write", risk_level="medium", requires_approval=True),
    ],
    "dish": [
        ToolSpec("user_restrictions.read", "读取过敏和忌口", replay_safe=True),
        ToolSpec("recipe.search", "检索候选菜品", replay_safe=True),
        ToolSpec("allergy.check", "检查过敏风险", replay_safe=True),
        ToolSpec("menu.export", "导出菜单", mode="write", risk_level="medium", requires_approval=True),
    ],
    "steward": [
        ToolSpec("checkin.read", "读取打卡记录", replay_safe=True),
        ToolSpec("plan_progress.read", "读取计划进度", replay_safe=True),
        ToolSpec("risk_threshold.read", "读取提醒阈值", replay_safe=True),
        ToolSpec("notification.send", "发送提醒", mode="write", risk_level="medium", requires_approval=True),
    ],
    "dietitian": [
        ToolSpec("conversation.read", "读取会话上下文", replay_safe=True),
        ToolSpec("health_context.read", "读取健康上下文", replay_safe=True),
        ToolSpec("knowledge.search", "检索知识库", replay_safe=True),
        ToolSpec("llm.generate", "生成回答", mode="model", timeout_seconds=30),
        ToolSpec("answer.safety_check", "检查回答安全性"),
        ToolSpec("human_consultation.create", "转人工营养师", mode="write", risk_level="high", requires_approval=False),
    ],
    "data": [
        ToolSpec("metric_catalog.read", "读取指标白名单", replay_safe=True),
        ToolSpec("data_permission.check", "检查数据权限", replay_safe=True),
        ToolSpec("sql.validate", "校验只读 SQL", replay_safe=True),
        ToolSpec("sql.execute_readonly", "执行只读查询", timeout_seconds=20, replay_safe=True),
        ToolSpec("data.export", "导出分析结果", mode="write", risk_level="high", requires_approval=True),
    ],
}

# 兼容第一阶段的 LangGraph 适配器：业务图内部的多个服务调用会在第二阶段
# 逐个拆成独立工具；当前先把整张业务图作为一个可追踪的逻辑工具登记。
for _agent in ("assessment", "diet", "dish", "steward", "dietitian", "data"):
    _TOOLS[_agent].insert(0, ToolSpec(
        f"{_agent}.execute", f"执行 {_agent} 业务 Agent 图", timeout_seconds=60,
    ))


def list_tools(agent_key: str) -> List[dict]:
    return [asdict(item) for item in _TOOLS.get(agent_key, [])]


def get_tool(agent_key: str, tool_name: str) -> Optional[ToolSpec]:
    return next((item for item in _TOOLS.get(agent_key, []) if item.name == tool_name), None)


def runtime_capabilities(agent_key: str) -> dict:
    tools = list_tools(agent_key)
    from app.agent_runtime.default_handlers import bind_default_handlers
    bind_default_handlers()
    from app.agent_runtime.tool_executor import handler_registered
    for item in tools:
        item["handler_registered"] = handler_registered(item["name"])
    return {
        "agent": agent_key,
        "implemented_contract": [
            "plan", "tool", "checkpoint", "retry", "trace", "memory",
            "approval_resume", "cost_budget", "compensation_hook",
            "distributed_lock", "provider_price_catalog", "sandbox_quota",
            "business_specific_rollback",
        ],
        "pending_contract": [],
        "tools": tools,
        "tool_count": len(tools),
        "approval_tools": [item["name"] for item in tools if item["requires_approval"]],
    }
