# -*- coding: utf-8 -*-
"""短信发送封装（腾讯云短信）。

职责单一：把验证码文本经短信网关发出去。验证码的生命周期（生成/限频/校验）
在 `app/services/sms_service.py`，本模块不碰缓存。

配置（.env，缺任一项即视为「未配置」—— 发送端会返回明确错误，绝不伪造成功）：
    SMS_PROVIDER=tencent
    SMS_SECRET_ID=...
    SMS_SECRET_KEY=...
    SMS_SDK_APP_ID=...        # 短信控制台的应用 SDKAppID
    SMS_SIGN_NAME=...         # 已审核通过的短信签名
    SMS_TEMPLATE_ID=...       # 模板需含一个 {1} 参数（验证码）

依赖：pip install tencentcloud-sdk-python-common tencentcloud-sdk-python-sms
（延迟导入：未安装时仅在实际发送时报错，不影响应用启动。）
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

from app.config import settings
from app.core.errors import BizError, ErrCode

logger = logging.getLogger("SmsClient")

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def is_configured() -> bool:
    """短信网关是否已配置（缺任一必填项即 False）。"""
    keys = ("SMS_SECRET_ID", "SMS_SECRET_KEY", "SMS_SDK_APP_ID", "SMS_SIGN_NAME", "SMS_TEMPLATE_ID")
    return all(str(getattr(settings, k, "") or "").strip() for k in keys)


def missing_config() -> list:
    """返回缺失的配置项名，便于排查。"""
    keys = ("SMS_SECRET_ID", "SMS_SECRET_KEY", "SMS_SDK_APP_ID", "SMS_SIGN_NAME", "SMS_TEMPLATE_ID")
    return [k for k in keys if not str(getattr(settings, k, "") or "").strip()]


def send_code(phone: str, code: str) -> Dict[str, Any]:
    """向国内手机号发送验证码短信（模板参数 {1}=验证码）。

    成功返回 {"request_id": ...}；失败/未配置抛 BizError（绝不伪造成功）。
    """
    if not _PHONE_RE.match(phone or ""):
        raise BizError(ErrCode.PARAM_INVALID, "手机号格式不正确")
    if not is_configured():
        raise BizError(
            ErrCode.SMS_SEND_FAILED,
            "短信服务未配置（缺 " + "、".join(missing_config()) + "），请联系管理员",
        )

    provider = (getattr(settings, "SMS_PROVIDER", "tencent") or "tencent").lower()
    if provider != "tencent":
        raise BizError(ErrCode.SMS_SEND_FAILED, f"暂不支持短信服务商：{provider}")

    try:
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        from tencentcloud.sms.v20210111.sms_client import SmsClient
        from tencentcloud.sms.v20210111 import models as sms_models
    except ImportError as e:
        raise BizError(ErrCode.SMS_SEND_FAILED, "短信 SDK 未安装，请联系管理员") from e

    try:
        cred = credential.Credential(
            str(settings.SMS_SECRET_ID).strip(), str(settings.SMS_SECRET_KEY).strip())
        client = SmsClient(cred, "ap-guangzhou")
        req = sms_models.SendSmsRequest()
        req.PhoneNumberSet = ["+86" + phone.strip()]
        req.SmsSdkAppId = str(settings.SMS_SDK_APP_ID).strip()
        req.SignName = str(settings.SMS_SIGN_NAME).strip()
        req.TemplateId = str(settings.SMS_TEMPLATE_ID).strip()
        req.TemplateParamSet = [code]
        resp = client.SendSms(req)
        status = resp.SendStatusSet[0]
        if getattr(status, "Code", "") != "Ok":
            logger.warning("短信发送失败：%s %s", status.Code, status.Message)
            raise BizError(ErrCode.SMS_SEND_FAILED,
                           f"短信发送失败（{status.Code}），请稍后重试或联系管理员")
        return {"request_id": getattr(status, "SerialNo", "") or ""}
    except BizError:
        raise
    except TencentCloudSDKException as e:
        logger.warning("腾讯云短信异常：%s", e)
        raise BizError(ErrCode.SMS_SEND_FAILED, "短信网关异常，请稍后重试")
    except Exception as e:  # noqa: BLE001
        logger.warning("短信发送未知异常：%s", e)
        raise BizError(ErrCode.SMS_SEND_FAILED, "短信发送失败，请稍后重试")
