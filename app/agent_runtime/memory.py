# -*- coding: utf-8 -*-
"""分层 Agent 记忆管理。

记忆类型与任务状态、会话历史、工具结果、知识库引用严格分开，调用方按需读取，
避免把无关历史全部塞进模型上下文。
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.agent_runtime.task_manager import db_persistence_allowed

logger = logging.getLogger("AgentMemory")


@dataclass
class MemoryItem:
    memory_id: str
    agent_key: str
    memory_type: str
    content: Dict[str, Any]
    user_id: Optional[str] = None
    task_id: Optional[str] = None
    source: str = "agent"
    confidence: float = 1.0
    expires_at: Optional[float] = None


class MemoryManager:
    ALLOWED_TYPES = {"task_context", "conversation", "profile", "preference", "tool_result", "summary", "reference"}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, MemoryItem] = {}

    def remember(self, agent_key: str, memory_type: str, content: Dict[str, Any], *,
                 user_id: Optional[str] = None, task_id: Optional[str] = None,
                 source: str = "agent", confidence: float = 1.0,
                 ttl_seconds: Optional[int] = None) -> MemoryItem:
        if memory_type not in self.ALLOWED_TYPES:
            raise ValueError(f"不支持的记忆类型：{memory_type}")
        item = MemoryItem(
            memory_id="mem_" + uuid.uuid4().hex,
            agent_key=agent_key,
            memory_type=memory_type,
            content=copy.deepcopy(content),
            user_id=user_id,
            task_id=task_id,
            source=source,
            confidence=max(0.0, min(1.0, float(confidence))),
            expires_at=(time.time() + ttl_seconds if ttl_seconds else None),
        )
        with self._lock:
            self._items[item.memory_id] = item
        self._persist(item)
        return copy.deepcopy(item)

    def recall(self, *, user_id: Optional[str] = None, task_id: Optional[str] = None,
               agent_key: Optional[str] = None, memory_types: Optional[Iterable[str]] = None,
               limit: int = 20) -> List[MemoryItem]:
        wanted = set(memory_types or self.ALLOWED_TYPES)
        now = time.time()
        with self._lock:
            items = [item for item in self._items.values()
                     if (not user_id or item.user_id == user_id)
                     and (not task_id or item.task_id == task_id)
                     and (not agent_key or item.agent_key in {agent_key, "shared"})
                     and item.memory_type in wanted
                     and (item.expires_at is None or item.expires_at > now)]
        # 进程重启后从 MySQL 补齐用户记忆，随后放入内存缓存，避免把长期记忆误当成
        # 仅存在于当前 worker 的短期状态。
        if len(items) < max(1, int(limit)) and db_persistence_allowed():
            try:
                from app.models.mysql.platform import AgentMemory
                from app.utils.db import get_session
                from datetime import timezone
                session = get_session()
                query = session.query(AgentMemory)
                if user_id:
                    query = query.filter(AgentMemory.user_id == user_id)
                if task_id:
                    query = query.filter(AgentMemory.task_id == task_id)
                if agent_key:
                    query = query.filter(AgentMemory.agent_key.in_([agent_key, "shared"]))
                query = query.filter(AgentMemory.memory_type.in_(wanted))
                rows = query.order_by(AgentMemory.updated_at.desc()).limit(max(1, int(limit)) * 2).all()
                session.close()
                with self._lock:
                    for row in rows:
                        expiry = row.expires_at.replace(tzinfo=timezone.utc).timestamp() if row.expires_at else None
                        if expiry is not None and expiry <= now:
                            continue
                        memory_id = f"dbmem_{row.id}"
                        if memory_id not in self._items:
                            self._items[memory_id] = MemoryItem(
                                memory_id=memory_id, agent_key=row.agent_key,
                                memory_type=row.memory_type, content=copy.deepcopy(row.content_json or {}),
                                user_id=row.user_id, task_id=row.task_id, source=row.source or "agent",
                                confidence=float(row.confidence or 0.0), expires_at=expiry,
                            )
                    items = [item for item in self._items.values()
                             if (not user_id or item.user_id == user_id)
                             and (not task_id or item.task_id == task_id)
                             and (not agent_key or item.agent_key in {agent_key, "shared"})
                             and item.memory_type in wanted
                             and (item.expires_at is None or item.expires_at > now)]
            except Exception as exc:
                logger.debug("Agent 记忆持久化读取失败：%s", exc)
        items.sort(key=lambda item: (item.confidence, item.memory_id), reverse=True)
        return copy.deepcopy(items[:max(1, int(limit))])

    def context(self, *, user_id: Optional[str] = None, task_id: Optional[str] = None,
                agent_key: Optional[str] = None, limit: int = 12) -> Dict[str, List[dict]]:
        """按记忆类型返回模型或 Agent 可消费的上下文。"""
        grouped: Dict[str, List[dict]] = {}
        for item in self.recall(user_id=user_id, task_id=task_id, agent_key=agent_key, limit=limit):
            grouped.setdefault(item.memory_type, []).append({
                "memory_id": item.memory_id,
                "content": item.content,
                "source": item.source,
                "confidence": item.confidence,
            })
        return grouped

    def context_text(self, *, user_id: Optional[str] = None, agent_key: Optional[str] = None,
                     limit: int = 8) -> str:
        context = self.context(user_id=user_id, agent_key=agent_key, limit=limit)
        lines: List[str] = []
        for kind, items in context.items():
            for item in items:
                lines.append(f"[{kind}] {item['content']}")
        return "\n".join(lines)

    def forget(self, memory_id: str) -> bool:
        with self._lock:
            removed = self._items.pop(memory_id, None) is not None
        if memory_id.startswith("dbmem_") and db_persistence_allowed():
            try:
                from app.models.mysql.platform import AgentMemory
                from app.utils.db import get_session
                session = get_session()
                row = session.query(AgentMemory).filter_by(id=int(memory_id[6:])).first()
                if row is not None:
                    session.delete(row)
                    session.commit()
                    removed = True
                session.close()
            except Exception as exc:
                logger.debug("Agent 记忆删除落库失败：%s", exc)
        return removed

    @staticmethod
    def _persist(item: MemoryItem) -> None:
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentMemory
            from app.utils.db import get_session
            session = get_session()
            session.add(AgentMemory(
                user_id=item.user_id, task_id=item.task_id, agent_key=item.agent_key,
                memory_type=item.memory_type, content_json=item.content,
                source=item.source, confidence=item.confidence,
                expires_at=datetime.fromtimestamp(item.expires_at) if item.expires_at else None,
            ))
            session.commit()
            session.close()
        except Exception as exc:
            logger.debug("Agent 记忆落库失败：%s", exc)


memory_manager = MemoryManager()
