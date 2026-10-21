# -*- coding: utf-8 -*-
"""Agent 事件和调用链记录。"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from typing import Dict, List

from app.agent_runtime.contract import AgentEvent


class AgentTraceStore:
    """第一阶段的内存 trace 存储，后续映射到 agent_event 表。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: Dict[str, List[dict]] = {}

    def new_trace_id(self) -> str:
        return "trace_" + uuid.uuid4().hex

    def append(self, event: AgentEvent) -> None:
        with self._lock:
            self._events.setdefault(event.trace_id, []).append(asdict(event))

    def list(self, trace_id: str) -> List[dict]:
        with self._lock:
            cached = list(self._events.get(trace_id, []))
        if cached:
            return cached
        # 跨进程查询：当前 worker 没有内存缓存时，从事件审计表恢复时间线。
        try:
            from app.models.mysql.platform import AgentEvent as AgentEventModel
            from app.utils.db import get_session
            session = get_session()
            rows = (session.query(AgentEventModel)
                    .filter_by(trace_id=trace_id)
                    .order_by(AgentEventModel.id.asc()).all())
            events = [{
                "task_id": row.task_id, "trace_id": row.trace_id,
                "event_type": row.event_type, "payload": row.payload_json or {},
                "span_id": row.span_id,
            } for row in rows]
            session.close()
            if events:
                with self._lock:
                    self._events[trace_id] = list(events)
            return events
        except Exception:
            return []


trace_store = AgentTraceStore()
