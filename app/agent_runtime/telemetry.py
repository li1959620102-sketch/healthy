# -*- coding: utf-8 -*-
"""Agent 级运行指标、工具指标和成本统计。"""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict, deque
from typing import Any, Dict, Optional


class AgentTelemetry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks = Counter()
        self._task_latency = defaultdict(lambda: deque(maxlen=512))
        self._tools = Counter()
        self._tool_latency = defaultdict(lambda: deque(maxlen=512))
        self._tool_errors = Counter()
        self._tokens = Counter()
        self._cost = Counter()

    def record_task(self, agent_key: str, status: str, elapsed_ms: int) -> None:
        with self._lock:
            self._tasks[(agent_key, "total")] += 1
            self._tasks[(agent_key, status)] += 1
            self._task_latency[agent_key].append(int(elapsed_ms))

    def record_tool(self, agent_key: str, tool_name: str, *, success: bool,
                    elapsed_ms: int, attempts: int = 1,
                    prompt_tokens: Optional[int] = None,
                    completion_tokens: Optional[int] = None,
                    estimated_cost: Optional[float] = None) -> None:
        key = (agent_key, tool_name)
        with self._lock:
            self._tools[key] += 1
            self._tool_latency[key].append(int(elapsed_ms))
            if not success:
                self._tool_errors[key] += 1
            if attempts > 1:
                self._tools[(agent_key, "retry")] += attempts - 1
            if prompt_tokens is not None:
                self._tokens[(agent_key, "prompt")] += int(prompt_tokens)
            if completion_tokens is not None:
                self._tokens[(agent_key, "completion")] += int(completion_tokens)
            if estimated_cost is not None:
                self._cost[agent_key] += float(estimated_cost)

    @staticmethod
    def _p95(values) -> float:
        values = sorted(values)
        if not values:
            return 0.0
        return float(values[min(len(values) - 1, int(len(values) * 0.95))])

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            agents = {}
            keys = {agent for agent, _ in self._tasks.keys()}
            keys.update(agent for agent, _ in self._tools.keys())
            for agent in sorted(keys):
                total = self._tasks[(agent, "total")]
                completed = self._tasks[(agent, "completed")]
                agents[agent] = {
                    "tasks": total,
                    "completed": completed,
                    "failed": self._tasks[(agent, "failed")],
                    "waiting_approval": self._tasks[(agent, "waiting_approval")],
                    "success_rate": round(completed / total, 4) if total else 0.0,
                    "p95_ms": self._p95(self._task_latency[agent]),
                    "prompt_tokens": self._tokens[(agent, "prompt")],
                    "completion_tokens": self._tokens[(agent, "completion")],
                    "estimated_cost": round(self._cost[agent], 6),
                }
            tools = []
            for (agent, tool), count in self._tools.items():
                if tool == "retry":
                    continue
                errors = self._tool_errors[(agent, tool)]
                tools.append({
                    "agent": agent, "tool": tool, "calls": count,
                    "errors": errors,
                    "error_rate": round(errors / count, 4) if count else 0.0,
                    "p95_ms": self._p95(self._tool_latency[(agent, tool)]),
                })
            return {
                "generated_at": time.time(),
                "agents": agents,
                "tools": tools,
                "total_estimated_cost": round(sum(self._cost.values()), 6),
                "cost_basis": "仅统计显式上报的 token/成本；未上报时不伪造精确成本",
            }


telemetry = AgentTelemetry()
