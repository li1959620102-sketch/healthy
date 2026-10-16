# -*- coding: utf-8 -*-
"""验证码通道服务：短信与邮箱共用同一套「发码 / 限频 / 验码」流程。

为什么要抽出来：手机号注册与邮箱注册（FR-U-01）在流程上完全同构——
生成 6 位数字码 → 60s 重发限频 → 落缓存（TTL）→ 经网关下发 →
校验时计失败次数、达上限作废、通过即作废（一次性）。
两处各写一遍必然出现"改了一处忘了另一处"（本项目已多次踩过双口径的坑：
错误码分段、审计动作名、手机号/邮箱字段都因此返工过），故收敛到本模块，
由调用方注入通道差异（哈希函数、校验函数、网关、限频/有效期设置名）。

缓存键（Redis；可丢失不致命——丢了只是需要重发）：
    {channel}:limit:{hash}   重发限频标记（TTL = 重发间隔）
    {channel}:code:{hash}    验证码（TTL = 有效期）
    {channel}:try:{hash}     校验失败次数（TTL = 有效期，达上限作废重发）

规则常量集中在 app/core/rules.py；本模块只做流程编排。
"""
from __future__ import annotations

import logging
import secrets
from typing import Callable, Dict

from app.config import settings
from app.core import rules
from app.core.errors import BizError, ErrCode
from app.utils.redis_client import redis_client

logger = logging.getLogger("VerifyCodeService")


class VerifyCodeService:
    """通道无关的验证码服务：`channel` 决定缓存键前缀与文案，其余流程一致。"""

    def __init__(
        self,
        *,
        channel: str,                 # "sms" / "email"，用于缓存键前缀与日志
        label: str,                   # 文案里的通道名，如 "短信" / "邮件"
        target_label: str,            # 被验证对象的称呼，如 "手机号" / "邮箱"
        hash_fn: Callable[[str], str],
        check_fn: Callable[[str], rules.CheckResult],
        gateway,                      # app.utils.sms / app.utils.mailer
        demo_flag: str,               # 演示模式开关的设置名
        rate_code: int,               # 发送过频的业务码
        failed_code: int,             # 网关未配置/发送失败的业务码
        ttl_setting: str,
        interval_setting: str,
        attempts_setting: str,
    ):
        self.channel = channel
        self.label = label
        self.target_label = target_label
        self._hash = hash_fn
        self._check = check_fn
        self.gateway = gateway
        self.demo_flag = demo_flag
        self.rate_code = rate_code
        self.failed_code = failed_code
        self.ttl_setting = ttl_setting
        self.interval_setting = interval_setting
        self.attempts_setting = attempts_setting

    # ---- 缓存键 ---- #
    def _k_limit(self, h: str) -> str:
        return f"{self.channel}:limit:{h}"

    def _k_code(self, h: str) -> str:
        return f"{self.channel}:code:{h}"

    def _k_try(self, h: str) -> str:
        return f"{self.channel}:try:{h}"

    # ---- 发送 ---- #
    def send_code(self, target: str) -> Dict:
        """向目标（手机号/邮箱）发送验证码。

        网关未配置时进入演示模式（验证码直接返回给前端展示），仅限
        开发环境且显式开关打开；生产环境一律拒绝，绝不伪造发送成功。
        """
        chk = self._check(target)
        if not chk.ok:
            raise BizError(ErrCode.PARAM_INVALID, chk.message)
        target = str(target).strip()
        h = self._hash(target)

        ttl = int(getattr(settings, self.ttl_setting))
        interval = int(getattr(settings, self.interval_setting))

        # 1) 重发限频
        if redis_client.get(self._k_limit(h)):
            raise BizError(self.rate_code, "发送太频繁，请 1 分钟后再试")

        # 2) 生成 6 位数字验证码并落缓存
        code = "".join(secrets.choice("0123456789") for _ in range(rules.VERIFY_CODE_LEN))
        if not redis_client.set(self._k_code(h), code, ttl=ttl):
            raise BizError(ErrCode.SYSTEM_ERROR, "验证码服务不可用，请稍后重试")
        redis_client.set(self._k_try(h), "0", ttl=ttl)

        # 3) 下发：已配置网关 → 真实发送（失败作废刚写入的码）；未配置 → 演示模式
        if self.gateway.is_configured():
            # 网关是第三方能力：网络超时/鉴权失败/限流都会抛异常。
            # 不兜住就会变成 500，且验证码已写进 Redis —— 用户 60 秒内重试还会
            # 被自己触发的限频挡住，等于双重伤害。故失败即作废刚写的码并转成
            # 可理解的业务错误（EX-01）。
            try:
                self.gateway.send_code(target, code)
            except BizError:
                self._clear(h)
                raise
            except Exception as e:                       # noqa: BLE001
                logger.warning("%s网关下发失败（已作废本次验证码）：%s", self.label, e, exc_info=True)
                self._clear(h)
                raise BizError(self.failed_code, "验证码发送失败，请稍后重试")
            redis_client.set(self._k_limit(h), "1", ttl=interval)
            return {"expires_in": ttl, "interval": interval}

        if not getattr(settings, self.demo_flag) or settings.ENV == "production":
            self._clear(h)
            # 注意：此处刻意用 SYSTEM_ERROR(90000) 而非通道的「发送失败」码——
            # 语义不同：网关没配是"服务端未就绪"（重试无用，得找管理员），
            # 而网关调用失败是"本次发送失败"（可以稍后重试）。
            # 该口径已由既有用例锁定（test_phone_auth），不要顺手"统一"掉。
            raise BizError(ErrCode.SYSTEM_ERROR, f"{self.label}服务未配置，请联系管理员")

        logger.warning("%s网关未配置，验证码走显式开发演示模式：%s",
                       self.label, _mask_target(self.target_label, target))
        redis_client.set(self._k_limit(h), "1", ttl=interval)
        return {"expires_in": ttl, "interval": interval,
                "demo_mode": True, "demo_code": code}

    # ---- 校验 ---- #
    def verify_code(self, target: str, code: str) -> None:
        """校验验证码；不匹配 / 过期 / 超限一律抛 BizError(20003)。"""
        chk = self._check(target)
        if not chk.ok:
            raise BizError(ErrCode.PARAM_INVALID, chk.message)
        vchk = rules.check_verify_code(code)
        if not vchk.ok:
            raise BizError(ErrCode.VERIFY_CODE_INVALID, vchk.message)
        target = str(target).strip()
        h = self._hash(target)

        max_try = int(getattr(settings, self.attempts_setting))
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

    def _clear(self, h: str) -> None:
        redis_client.delete(self._k_code(h), self._k_try(h), self._k_limit(h))

    # 兼容既有调用口径（`_clear_sms` 用例直接按 "sms:*" 键清理）
    @staticmethod
    def _clear_keys(channel: str, h: str) -> None:
        redis_client.delete(f"{channel}:code:{h}", f"{channel}:try:{h}", f"{channel}:limit:{h}")


def _mask_target(target_label: str, target: str) -> str:
    """日志脱敏：手机号留前 3 后 4，邮箱留前 2 位本地名与域名。"""
    if "@" in target:
        local, _, domain = target.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    if len(target) >= 7:
        return target[:3] + "****" + target[-4:]
    return "***"
