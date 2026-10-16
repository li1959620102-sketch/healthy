# -*- coding: utf-8 -*-
"""短信验证码服务（通道配置层）。

流程编排（生成 / 限频 / 校验 / 一次性作废）已收敛到
`app/services/verify_code_service.py`（与邮箱验证码共用同一条流程）；
本模块只声明"短信"这一个通道的参数：哈希函数、校验规则、网关、设置项名。

缓存键：sms:limit:{phone_hash} / sms:code:{phone_hash} / sms:try:{phone_hash}
"""
from __future__ import annotations

from app.config import settings                     # noqa: F401  （保留模块级名，既有用例 monkeypatch 依赖）
from app.core import rules
from app.core.errors import ErrCode
from app.services.verify_code_service import VerifyCodeService
from app.utils import sms as sms_gateway           # noqa: F401  （同上）
from app.utils.security import phone_hash as phone_hmac

sms_service = VerifyCodeService(
    channel="sms",
    label="短信",
    target_label="手机号",
    hash_fn=phone_hmac,
    check_fn=rules.check_phone,
    gateway=sms_gateway,
    demo_flag="SMS_DEMO_MODE",
    rate_code=ErrCode.SMS_RATE_LIMITED,
    failed_code=ErrCode.SMS_SEND_FAILED,
    ttl_setting="SMS_CODE_TTL",
    interval_setting="SMS_RESEND_INTERVAL",
    attempts_setting="SMS_CODE_MAX_ATTEMPTS",
)
