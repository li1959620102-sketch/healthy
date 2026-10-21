# -*- coding: utf-8 -*-
"""Agent 检查点存储。

第一阶段默认使用进程内存，接口已经固定。第二阶段接入 MySQL 后，业务代码无需
改变调用方式；这样先把执行契约落下来，再替换存储实现。
"""
from __future__ import annotations

import copy
import inspect
import threading
from typing import Any, Dict, Optional


class InMemoryCheckpointStore:
    """开发和单元测试使用的线程安全检查点存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def save(self, task_id: str, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self._items[task_id] = copy.deepcopy(snapshot)

    def load(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self._items.get(task_id)
            return copy.deepcopy(value) if value is not None else None

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._items.pop(task_id, None)


checkpoint_store = InMemoryCheckpointStore()


class NodeCheckpointStore:
    """按任务和 LangGraph 节点保存输出，支持节点级重放。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[tuple, Dict[str, Any]] = {}

    def save(self, task_id: str, step_id: str, status: str, output: Any = None) -> None:
        with self._lock:
            self._items[(task_id, step_id)] = {
                "status": status, "output": copy.deepcopy(output),
            }

    def load(self, task_id: str, step_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self._items.get((task_id, step_id))
            return copy.deepcopy(value) if value is not None else None


node_checkpoint_store = NodeCheckpointStore()


def checkpointed_node(step_id: str, fn):
    """包装同步/异步 LangGraph 节点，成功后缓存节点输出。"""
    async def wrapped(state):
        task_id = state.get("runtime_task_id") if isinstance(state, dict) else None
        replayable = not step_id.endswith(".stamp")
        if task_id and replayable:
            cached = node_checkpoint_store.load(task_id, step_id)
            if cached is None:
                try:
                    from app.agent_runtime.task_manager import task_manager
                    cached = task_manager.load_node_checkpoint(task_id, step_id)
                    if cached:
                        node_checkpoint_store.save(task_id, step_id,
                                                   cached.get("status", "completed"),
                                                   cached.get("output"))
                except Exception:
                    cached = None
            if cached and cached.get("status") == "completed":
                return cached.get("output") or {}
            node_checkpoint_store.save(task_id, step_id, "running")
        try:
            result = fn(state)
            if inspect.isawaitable(result):
                result = await result
            if task_id and replayable:
                node_checkpoint_store.save(task_id, step_id, "completed", result)
                try:
                    from app.agent_runtime.task_manager import task_manager
                    task_manager.save_node_checkpoint(task_id, step_id, "completed", result)
                except Exception:
                    pass
            return result or {}
        except Exception as exc:
            if task_id:
                node_checkpoint_store.save(task_id, step_id, "failed",
                                           {"error": str(exc)})
            raise
    wrapped.__name__ = getattr(fn, "__name__", step_id)
    return wrapped
