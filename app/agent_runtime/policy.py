# -*- coding: utf-8 -*-
"""统一 Agent 工具权限和风险策略入口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.agent_runtime.tool_registry import get_tool


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


def authorize(agent_key: str, tool_name: str, user: Optional[dict] = None) -> PolicyDecision:
    spec = get_tool(agent_key, tool_name)
    if spec is None:
        return PolicyDecision(False, reason=f"Agent {agent_key} 未注册工具 {tool_name}")
    role = str((user or {}).get("role") or "user")
    if spec.mode == "write" and spec.risk_level == "high" and role not in {"admin", "dietitian", "doctor"}:
        return PolicyDecision(False, reason="当前角色无权执行高风险写操作")
    return PolicyDecision(True, requires_approval=spec.requires_approval,
                          reason="工具已注册并通过角色策略")
