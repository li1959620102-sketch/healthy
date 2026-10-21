# -*- coding: utf-8 -*-
"""Agent 业务补偿动作注册表。"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional

CompensationHandler = Callable[..., Any]
_HANDLERS: Dict[str, CompensationHandler] = {}


def register_compensation(tool_name: str, handler: CompensationHandler) -> None:
    _HANDLERS[tool_name] = handler


def registered_compensations() -> list[str]:
    return sorted(_HANDLERS)


async def compensate(tool_name: Optional[str], payload: dict) -> dict:
    if not tool_name:
        return {"attempted": False, "reason": "step_without_tool"}
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return {"attempted": False, "reason": "no_compensation_registered", "tool": tool_name}
    try:
        value = handler(**payload)
        if inspect.isawaitable(value):
            value = await value
        return {"attempted": True, "success": True, "tool": tool_name, "result": value}
    except Exception as exc:  # noqa: BLE001
        return {"attempted": True, "success": False, "tool": tool_name, "error": str(exc)}
