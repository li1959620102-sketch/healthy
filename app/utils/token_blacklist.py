# -*- coding: utf-8 -*-
"""访问令牌主动失效（登出黑名单）。

背景
----
平台使用无状态 JWT。登出后服务端**无法收回**已签发的令牌——只要还没过期，
旧令牌依然能通过签名校验。多设备场景下这是真实风险：设备 A 登出后，
设备 B（或已被窃取的）令牌照用不误。用例 IT-A-08 正是验这一点。

做法
----
登出时把令牌**指纹**（SHA-256 前 32 位，不落原文）写入 Redis，
TTL 取该令牌的剩余有效期——令牌自然过期后黑名单条目自动清理，不留长期存储。
校验放在鉴权依赖层（`app/core/deps.py`）而非 `decode_token` 内部：
后者要保持纯函数（无 IO），便于单元测试与多轮复用。

降级
----
Redis 不可用时 `is_revoked` 返回 False（放行）。理由与全站一致：
Redis 是可丢失依赖，不能因为缓存不可用把全部用户挡在门外；
此时回落到"无状态 JWT"的原有安全水位，不构成新的风险面。
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

from app.utils.redis_client import redis_client
from app.utils.security import decode_token

PREFIX = "jwt:revoked:"


def fingerprint(token: str) -> str:
    """令牌指纹：避免把含签名的令牌原文写进缓存。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def remaining_seconds(token: str) -> int:
    """令牌剩余有效期（秒）；无法解析返回 0。"""
    payload = decode_token(token)
    if not payload:
        return 0
    return max(0, int(payload.get("exp", 0)) - int(time.time()))


def revoke(token: str, ttl: Optional[int] = None) -> bool:
    """把令牌加入黑名单，TTL 默认取剩余有效期。"""
    if not token:
        return False
    if ttl is None:
        ttl = remaining_seconds(token)
    if ttl <= 0:
        return False                      # 已过期，无需登记
    return redis_client.set(PREFIX + fingerprint(token), "1", ttl=int(ttl))


def is_revoked(token: str) -> bool:
    """令牌是否已登出失效；Redis 不可用时返回 False（降级放行）。"""
    if not token:
        return False
    return bool(redis_client.get(PREFIX + fingerprint(token)))
