# -*- coding: utf-8 -*-
"""模型 token 价格目录。

价格必须由部署环境注入，代码不猜测供应商价格；缺失价格时只记录 token，不伪造成本。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional


def estimate_cost(model_name: Optional[str], usage: Optional[dict[str, Any]]) -> Optional[float]:
    if not model_name or not usage:
        return None
    raw = os.getenv("AGENT_MODEL_PRICES_JSON", "").strip()
    if not raw:
        return None
    try:
        catalog = json.loads(raw)
        price = catalog.get(model_name) or catalog.get("default")
        if not isinstance(price, dict):
            return None
        prompt = float(usage.get("prompt_tokens") or 0)
        completion = float(usage.get("completion_tokens") or 0)
        return round(
            prompt / 1000.0 * float(price.get("prompt_per_1k_usd", 0))
            + completion / 1000.0 * float(price.get("completion_per_1k_usd", 0)),
            8,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
