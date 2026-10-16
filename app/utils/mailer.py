# -*- coding: utf-8 -*-
"""邮件发送封装（SMTP，标准库 smtplib + email.message，无第三方依赖）。

职责单一：把验证码文本经 SMTP 发出去。验证码的生命周期（生成/限频/校验）
在 `app/services/email_code_service.py`，本模块不碰缓存。

配置（.env，缺任一项即视为「未配置」—— 发送端会返回明确错误，绝不伪造成功）：
    MAIL_HOST=smtp.example.com
    MAIL_PORT=465
    MAIL_USER=no-reply@example.com
    MAIL_PASSWORD=...
    MAIL_FROM=no-reply@example.com   # 可留空，回退为 MAIL_USER
    MAIL_USE_SSL=true                # 465：SSL 直连
    MAIL_USE_TLS=false               # 587：STARTTLS
    MAIL_TIMEOUT=10

与短信网关（`app/utils/sms.py`）保持同一套对外契约：
`is_configured()` / `missing_config()` / `send_code(target, code)`，
失败一律抛 `BizError`，成功返回可记录的请求标识。这样「发码服务」不必关心
通道差异——短信与邮箱走同一条流程编排。
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict

from app.config import settings
from app.core.errors import BizError, ErrCode
from app.utils.security import mask_email

logger = logging.getLogger("MailClient")

_REQUIRED = ("MAIL_HOST", "MAIL_USER", "MAIL_PASSWORD")

# 邮件正文：纯文本 + 简短 HTML。不引外部资源、不放追踪像素——
# 这是发给用户的验证码邮件，越简单越不容易被判垃圾邮件。
_SUBJECT = "【食养智慧健康管理平台】邮箱验证码"
_TEXT = """您好，

您的邮箱验证码是：{code}

验证码 {minutes} 分钟内有效，请勿转发给他人。
如非本人操作，请忽略本邮件。

—— 食养智慧健康管理平台
"""

_HTML = """<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f7f6;font-family:-apple-system,'Segoe UI',sans-serif">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:28px">
    <h2 style="margin:0 0 16px;font-size:17px;color:#1f2d24">邮箱验证码</h2>
    <p style="margin:0 0 16px;font-size:13px;line-height:1.8;color:#4a5a50">
      您好，您的邮箱验证码是：
    </p>
    <div style="font-size:28px;font-weight:700;letter-spacing:6px;color:#1f7a4d;
                background:#eef7f1;border-radius:8px;padding:14px 18px;text-align:center">{code}</div>
    <p style="margin:16px 0 0;font-size:12px;line-height:1.8;color:#8a978f">
      验证码 {minutes} 分钟内有效，请勿转发给他人。如非本人操作，请忽略本邮件。
    </p>
  </div>
</body></html>
"""


def is_configured() -> bool:
    """邮件网关是否已配置（缺任一必填项即 False）。"""
    return all(str(getattr(settings, k, "") or "").strip() for k in _REQUIRED)


def missing_config() -> list:
    """返回缺失的配置项名，便于排查。"""
    return [k for k in _REQUIRED if not str(getattr(settings, k, "") or "").strip()]


def from_address() -> str:
    """发件人地址：MAIL_FROM 优先，留空回退 MAIL_USER。"""
    return str(getattr(settings, "MAIL_FROM", "") or "").strip() or str(settings.MAIL_USER).strip()


def send_code(email: str, code: str) -> Dict[str, Any]:
    """向邮箱发送验证码邮件。

    成功返回 {"to": 掩码后的收件人}；失败/未配置抛 BizError（绝不伪造成功）。
    """
    if not is_configured():
        raise BizError(
            ErrCode.EMAIL_SEND_FAILED,
            "邮件服务未配置（缺 " + "、".join(missing_config()) + "），请联系管理员",
        )

    provider = (getattr(settings, "MAIL_PROVIDER", "smtp") or "smtp").lower()
    if provider != "smtp":
        raise BizError(ErrCode.EMAIL_SEND_FAILED, f"暂不支持的邮件服务商：{provider}")

    minutes = max(1, int(getattr(settings, "EMAIL_CODE_TTL", 300)) // 60)
    msg = EmailMessage()
    msg["Subject"] = _SUBJECT
    msg["From"] = formataddr(("食养智慧健康管理平台", from_address()))
    msg["To"] = email
    msg.set_content(_TEXT.format(code=code, minutes=minutes))
    msg.add_alternative(_HTML.format(code=code, minutes=minutes), subtype="html")

    host = str(settings.MAIL_HOST).strip()
    port = int(getattr(settings, "MAIL_PORT", 465))
    timeout = int(getattr(settings, "MAIL_TIMEOUT", 10))
    use_ssl = bool(getattr(settings, "MAIL_USE_SSL", True))
    use_tls = bool(getattr(settings, "MAIL_USE_TLS", False))
    context = ssl.create_default_context()

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            if use_tls and not use_ssl:
                server.starttls(context=context)
            server.login(str(settings.MAIL_USER).strip(), str(settings.MAIL_PASSWORD).strip())
            server.send_message(msg)
        return {"to": mask_email(email)}
    except smtplib.SMTPAuthenticationError as e:
        # 鉴权失败是配置问题，不是网络抖动：重试多少次都一样，
        # 因此明确提示管理员，别让用户以为"稍后重试就好"。
        logger.warning("SMTP 鉴权失败：%s", e)
        raise BizError(ErrCode.EMAIL_SEND_FAILED, "邮件服务鉴权失败，请联系管理员检查发件配置")
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        logger.warning("邮件发送失败：%s", e)
        raise BizError(ErrCode.EMAIL_SEND_FAILED, "验证码邮件发送失败，请稍后重试")
    except Exception as e:  # noqa: BLE001
        logger.warning("邮件发送未知异常：%s", e)
        raise BizError(ErrCode.EMAIL_SEND_FAILED, "验证码邮件发送失败，请稍后重试")
