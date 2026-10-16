# -*- coding: utf-8 -*-
"""邮箱验证码服务（通道配置层，FR-U-01）。

与短信验证码同构：流程编排在 `app/services/verify_code_service.py`，
本模块只声明"邮箱"这一个通道的参数。

缓存键：email:limit:{email_hash} / email:code:{email_hash} / email:try:{email_hash}
"""
from __future__ import annotations

from app.core import rules
from app.core.errors import ErrCode
from app.services.verify_code_service import VerifyCodeService
from app.utils import mailer as mail_gateway
from app.utils.security import email_hash

email_code_service = VerifyCodeService(
    channel="email",
    label="邮件",
    target_label="邮箱",
    hash_fn=email_hash,
    check_fn=rules.check_email,
    gateway=mail_gateway,
    demo_flag="MAIL_DEMO_MODE",
    rate_code=ErrCode.EMAIL_RATE_LIMITED,
    failed_code=ErrCode.EMAIL_SEND_FAILED,
    ttl_setting="EMAIL_CODE_TTL",
    interval_setting="EMAIL_RESEND_INTERVAL",
    attempts_setting="EMAIL_CODE_MAX_ATTEMPTS",
)
