# -*- coding: utf-8 -*-
"""Agent Runtime 的稳定数据契约。

这些类型是跨 Agent 的接口，不包含任何具体业务规则。业务 Agent 只负责提供
计划和工具执行函数，运行时负责状态、检查点、错误和观测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RETRYING = "retrying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


@dataclass
class AgentStep:
    step_id: str
    order: int
    action: str
    tool_name: Optional[str] = None
    status: str = "pending"
    input_snapshot: Dict[str, Any] = field(default_factory=dict)
    output_snapshot: Any = None
    attempt: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class AgentPlan:
    goal: str
    steps: List[AgentStep] = field(default_factory=list)
    planner: str = "deterministic"
    confidence: float = 0.0
    reason: str = ""


@dataclass
class AgentTask:
    task_id: str
    agent_key: str
    goal: str
    user_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    status: AgentStatus = AgentStatus.CREATED
    current_step: Optional[str] = None
    plan: Optional[AgentPlan] = None
    context: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2
    trace_id: Optional[str] = None


@dataclass
class AgentEvent:
    task_id: str
    trace_id: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    span_id: Optional[str] = None


@dataclass
class ToolCallResult:
    tool_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    attempts: int = 1
    latency_ms: int = 0
    replayed: bool = False


@dataclass
class AgentResult:
    task_id: str
    agent_key: str
    status: AgentStatus
    result: Any = None
    steps: List[AgentStep] = field(default_factory=list)
    trace_id: Optional[str] = None
    requires_human: bool = False
    error: Optional[Dict[str, Any]] = None
