# -*- coding: utf-8 -*-
"""Redis 客户端（第一步 · 后端编码）。

定位：**可选依赖**。平台核心链路（认证 / 测评 / 报告 / 知识库）全部走 MySQL，
Redis 只承载「可丢失、可重建」的加速与计数类数据（如游客配额计数、幂等键快照）。

因此本模块的设计原则是**优雅降级**：
  - 连接失败 / 未配置 / 服务端不可用，一律返回 None 或安全默认值，**绝不抛异常**；
  - 首次连接失败后进入短暂熔断（默认 5 秒），避免每个请求都去等超时；
  - 健康检查 `health()` 供 `/ready` 探针调用，结果标记为 non-required。

环境变量（见 .env）：
    REDIS_URL / REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_SOCKET_CONNECT_TIMEOUT
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger("RedisClient")

_CIRCUIT_BREAK_SECONDS = 5.0   # 连接失败后的熔断时长


class RedisClient:
    """懒加载 + 熔断的 Redis 访问封装。"""

    def __init__(self) -> None:
        self._client = None
        self._disabled_until = 0.0
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 连接
    # ------------------------------------------------------------------ #
    def _build(self):
        """按 REDIS_URL 优先、host/port/db 兜底建立客户端。"""
        import redis  # 延迟导入：redis 包缺失也不影响服务启动

        timeout = float(getattr(settings, "REDIS_SOCKET_CONNECT_TIMEOUT", 5) or 5)
        url = (getattr(settings, "REDIS_URL", None) or "").strip().strip('"').strip("'")
        if url:
            return redis.Redis.from_url(
                url,
                socket_connect_timeout=timeout,
                socket_timeout=float(getattr(settings, "REDIS_SOCKET_TIMEOUT", 30) or 30),
                decode_responses=True,
            )
        return redis.Redis(
            host=getattr(settings, "REDIS_HOST", "127.0.0.1"),
            port=int(getattr(settings, "REDIS_PORT", 6379) or 6379),
            db=int(getattr(settings, "REDIS_DB", 0) or 0),
            socket_connect_timeout=timeout,
            socket_timeout=float(getattr(settings, "REDIS_SOCKET_TIMEOUT", 30) or 30),
            decode_responses=True,
        )

    def client(self):
        """返回可用客户端；熔断期内或连接失败返回 None。"""
        if time.time() < self._disabled_until:
            return None
        if self._client is None:
            try:
                self._client = self._build()
            except Exception as e:
                self._trip(f"初始化失败：{e}")
                return None
        return self._client

    def _trip(self, reason: str) -> None:
        """触发熔断：丢弃坏连接并静默一段时间。"""
        self._last_error = reason
        self._disabled_until = time.time() + _CIRCUIT_BREAK_SECONDS
        self._client = None
        logger.warning(f"Redis 不可用，已降级（{_CIRCUIT_BREAK_SECONDS:g}s 内不再重试）：{reason}")

    # ------------------------------------------------------------------ #
    # 基础操作（全部返回安全默认值）
    # ------------------------------------------------------------------ #
    def ping(self) -> bool:
        c = self.client()
        if c is None:
            return False
        try:
            return bool(c.ping())
        except Exception as e:
            self._trip(str(e))
            return False

    def get(self, key: str) -> Optional[str]:
        c = self.client()
        if c is None:
            return None
        try:
            return c.get(key)
        except Exception as e:
            self._trip(str(e))
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        c = self.client()
        if c is None:
            return False
        try:
            c.set(key, value, ex=ttl)
            return True
        except Exception as e:
            self._trip(str(e))
            return False

    def incr_with_ttl(self, key: str, ttl: int) -> Optional[int]:
        """计数 +1，首次设置过期；返回计数结果，失败返回 None（调用方按放行处理）。"""
        c = self.client()
        if c is None:
            return None
        try:
            n = c.incr(key)
            if n == 1 and ttl:
                c.expire(key, ttl)
            return int(n)
        except Exception as e:
            self._trip(str(e))
            return None

    def delete(self, *keys: str) -> int:
        c = self.client()
        if c is None:
            return 0
        try:
            return int(c.delete(*keys))
        except Exception as e:
            self._trip(str(e))
            return 0

    def get_json(self, key: str) -> Optional[Any]:
        raw = self.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        try:
            return self.set(key, json.dumps(value, ensure_ascii=False), ttl)
        except Exception as e:
            logger.warning(f"Redis set_json 序列化失败：{e}")
            return False

    # ------------------------------------------------------------------ #
    # 健康检查（供 /ready）
    # ------------------------------------------------------------------ #
    def health(self) -> str:
        """返回 "ok" / "unavailable: <原因>" / "unconfigured"。"""
        if not (getattr(settings, "REDIS_URL", None) or getattr(settings, "REDIS_HOST", None)):
            return "unconfigured"
        if self.ping():
            return "ok"
        return f"unavailable: {self._last_error or '连接失败'}"


redis_client = RedisClient()
