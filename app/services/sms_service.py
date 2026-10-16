# -*- coding: utf-8 -*-
"""短信验证码服务：生成 / 限频 / 校验（发短信走 app/utils/sms.py）。

存储（Redis，可丢失不致命——丢了只是需要重发）：
    sms:limit:{phone_hash}  60s 重发限频标记（TTL 60）
    sms:code:{phone_hash}   验证码（TTL 300s）
    sms:try:{phone_hash}    校验失败次数（TTL 300s，达上限作废重发）

规则常量集中在 app/core/rules.py；本模块只做流程编排。
"""
from __future__ import annotations

import logging
import secrets
from typing import Dict

from app.config import settings
from app.core.errors import BizError, ErrCode
from app.core import rules
from app.utils.redis_client import redis_client
from app.utils.security import phone_hash as phone_hmac
from app.utils import sms as sms_gateway

logger = logging.getLogger("SmsService")


class SmsService:
    """手机号验证码：发码 / 验码。"""

    # ---- 键 ---- #
    @staticmethod
    def _k_limit(h: str) -> str:
        return f"sms:limit:{h}"

    @staticmethod
    def _k_code(h: str) -> str:
        return f"sms:code:{h}"

    @staticmethod
    def _k_try(h: str) -> str:
        return f"sms:try:{h}"

    # ---- 发送 ---- #
    def send_code(self, phone: str) -> Dict:
        """向手机号发送验证码；网关未配置时进入演示模式（验证码直接返回给前端展示）。

        演示模式仅用于内部演示/试用：响应带 demo_mode=True，前端明示“演示模式”。
        配置好 .env 的 SMS_* 后自动切回真实短信，无需改代码。
        """
        chk = rules.check_phone(phone)
        if not chk.ok:
            raise BizError(ErrCode.PARAM_INVALID, chk.message)
        phone = str(phone).strip()
        h = phone_hmac(phone)

        ttl = int(getattr(settings, "SMS_CODE_TTL", 300))
        interval = int(getattr(settings, "SMS_RESEND_INTERVAL", 60))

        # 1) 60s 重发限频
        if redis_client.get(self._k_limit(h)):
            raise BizError(ErrCode.SMS_RATE_LIMITED, "发送太频繁，请 1 分钟后再试")

        # 2) 生成 6 位数字验证码并落缓存
        code = "".join(secrets.choice("0123456789") for _ in range(rules.VERIFY_CODE_LEN))
        if not redis_client.set(self._k_code(h), code, ttl=ttl):
            raise BizError(ErrCode.SYSTEM_ERROR, "验证码服务不可用，请稍后重试")
        redis_client.set(self._k_try(h), "0", ttl=ttl)

        # 3) 下发：已配置网关 → 真实短信（失败作废刚写入的码）；未配置 → 演示模式
        if sms_gateway.is_configured():
            # 网关是第三方 SDK：网络超时/鉴权失败/限流都会抛异常。
            # 不兜住就会变成 500，且验证码已写进 Redis —— 用户 60 秒内重试还会
            # 被自己触发的限频挡住，等于双重伤害。故失败即作废刚写的码并转成
            # 可理解的业务错误（EX-01）。
            try:
                sms_gateway.send_code(phone, code)
            except BizError:
                self._clear(h)
                raise
            except Exception as e:                       # noqa: BLE001
                logger.warning("短信网关下发失败（已作废本次验证码）：%s", e, exc_info=True)
                self._clear(h)
                raise BizError(ErrCode.SYSTEM_ERROR, "验证码发送失败，请稍后重试")
            redis_client.set(self._k_limit(h), "1", ttl=interval)
            return {"expires_in": ttl, "interval": interval}

        if not settings.SMS_DEMO_MODE or settings.ENV == "production":
            self._clear(h)
            raise BizError(ErrCode.SYSTEM_ERROR, "短信服务未配置，请联系管理员")

        logger.warning("短信网关未配置，验证码走显式开发演示模式：%s", phone[:3] + "****" + phone[-4:])
        redis_client.set(self._k_limit(h), "1", ttl=interval)
        return {"expires_in": ttl, "interval": interval,
                "demo_mode": True, "demo_code": code}

    # ---- 校验 ---- #
    def verify_code(self, phone: str, code: str) -> None:
        """校验验证码；不匹配 / 过期 / 超限一律抛 BizError(20003)。"""
        chk = rules.check_phone(phone)
        if not chk.ok:
            raise BizError(ErrCode.PARAM_INVALID, chk.message)
        vchk = rules.check_verify_code(code)
        if not vchk.ok:
            raise BizError(ErrCode.VERIFY_CODE_INVALID, vchk.message)
        phone = str(phone).strip()
        h = phone_hmac(phone)

        max_try = int(getattr(settings, "SMS_CODE_MAX_ATTEMPTS", 5))
        stored = redis_client.get(self._k_code(h))
        if stored is None:
            raise BizError(ErrCode.VERIFY_CODE_INVALID, "验证码已失效，请重新获取")

        used = int(redis_client.get(self._k_try(h)) or 0)
        if used >= max_try:
            self._clear(h)
            raise BizError(ErrCode.VERIFY_CODE_INVALID, "错误次数过多，请重新获取验证码")

        if not secrets.compare_digest(str(code).strip(), str(stored)):
            used += 1
            remaining = max_try - used
            redis_client.set(self._k_try(h), str(used), ttl=300)
            hint = f"验证码不正确（还可尝试 {remaining} 次）" if remaining > 0 else "错误次数过多，请重新获取验证码"
            if remaining <= 0:
                self._clear(h)
            raise BizError(ErrCode.VERIFY_CODE_INVALID, hint)

        # 校验通过：立即作废（一次性）
        self._clear(h)

    @staticmethod
    def _clear(h: str) -> None:
        redis_client.delete("sms:code:" + h, "sms:try:" + h, "sms:limit:" + h)


sms_service = SmsService()
