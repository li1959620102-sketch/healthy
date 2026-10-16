# -*- coding: utf-8 -*-
"""A 组 · 认证路由（函数式，依据设计_01 §二 A / §1.4）。

覆盖范围：注册 / 登录 / 刷新 / 登出 / 游客令牌 / 短信验证码（占位）。
统一响应结构见 app.schemas.common.ApiResponse；错误码见 app.core.errors.ErrCode。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.platform import (
    RegisterReq, LoginReq, RefreshReq, SmsSendReq,
)
from app.services.user_service import UserService
from app.services.sms_service import sms_service
from app.core.responses import success, error
from app.core.errors import ErrCode, BizError
from app.core.deps import get_current_user
from app.utils.security import (
    create_guest_token, refresh_access_token, create_stream_ticket,
    ACCESS_EXPIRE_SECONDS, GUEST_EXPIRE_SECONDS, STREAM_EXPIRE_SECONDS,
)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", summary="手机号/账号注册",
             description="双通道：①手机号 + 短信验证码 + 密码（推荐）；②账号 + 密码（保留兼容）。手机号密文存储。")
async def register(body: RegisterReq):
    has_phone = bool(body.phone)
    if has_phone != bool(body.verify_code):
        raise BizError(ErrCode.PARAM_INVALID, "手机号注册需同时提供短信验证码")
    if has_phone and body.username:
        raise BizError(ErrCode.PARAM_INVALID, "手机号注册无需填写账号，登录直接用手机号")
    if not has_phone and not body.username:
        raise BizError(ErrCode.PARAM_INVALID, "请填写手机号或登录账号")
    try:
        if has_phone:
            user = UserService.register_by_phone(
                phone=body.phone, verify_code=body.verify_code, password=body.password,
                real_name=body.real_name, role="user",
            )
        else:
            user = UserService.register(
                username=body.username, password=body.password,
                real_name=body.real_name, role="user",
            )
        return success(data=user)
    except (BizError, ValueError) as e:
        if isinstance(e, BizError):
            raise
        msg = str(e)
        # 注：ErrCode 无 USER_CONFLICT，冲突统一用 USER_EXISTS(20002)
        code = ErrCode.PARAM_INVALID if "长度" in msg or "角色" in msg else ErrCode.USER_EXISTS
        raise BizError(code, msg)


@router.post("/login", summary="密码登录",
             description="双通道：username+password 或 phone+password。返回访问令牌与刷新令牌（双令牌）。")
async def login(body: LoginReq):
    try:
        result = UserService.login(username=body.username or "", password=body.password,
                                   phone=body.phone or "")
        return success(data=result)
    except BizError:
        raise
    except Exception as e:
        raise BizError(ErrCode.AUTH_FAILED, str(e))


@router.post("/token/refresh", summary="刷新访问令牌", description="用刷新令牌换取新的访问令牌。")
async def token_refresh(body: RefreshReq):
    new_access = refresh_access_token(body.refresh_token)
    return success(data={
        "access_token": new_access,
        "token_type": "Bearer",
        "expires_in": ACCESS_EXPIRE_SECONDS,
    })


@router.post("/logout", summary="登出",
             description="把当前访问令牌加入失效名单（TTL=剩余有效期），旧令牌立即不可再用。")
async def logout(authorization: Optional[str] = Header(None, alias="Authorization"),
                 _: dict = Depends(get_current_user)):
    """登出：无状态 JWT 无法"收回"已签发令牌，故显式登记黑名单。

    2026-10-16 补实现（原为"客户端丢弃即可"）：多设备场景下，
    设备 A 登出后设备 B 上被窃取的令牌仍可用，补测用例 IT-A-08 暴露了该风险。
    黑名单条目随令牌自然过期自动清理（Redis TTL），不产生长期存储。
    """
    from app.utils.token_blacklist import revoke

    token = (authorization or "").partition(" ")[2].strip()
    revoked = revoke(token)
    return success(message="已登出", data={"revoked": bool(revoked)})


@router.post("/guest", summary="获取游客令牌", description="匿名体验用，30 分钟有效，可发起游客试测。")
async def guest_token():
    token = create_guest_token()
    return success(data={
        "guest_token": token,
        "expires_in": GUEST_EXPIRE_SECONDS,
    })


@router.post("/sms/send", summary="发送短信验证码",
             description="向手机号发送 6 位验证码（5 分钟有效，60s 限频）。短信网关未配置时返回明确错误，不伪造发送成功。")
async def sms_send(body: SmsSendReq):
    data = sms_service.send_code(body.phone)
    return success(message="验证码已发送", data=data)


@router.post("/stream-ticket", summary="获取 SSE 短票", description="用于智能查询流式接口：EventSource 无法带 Authorization 头，改将短票置于 URL ?ticket=。")
async def stream_ticket(user: dict = Depends(get_current_user)):
    """签发 60 秒有效的流式短票（设计评审 B1 解决方案）。"""
    ticket = create_stream_ticket(user.get("sub"), user.get("username"), user.get("role"))
    return success(data={"ticket": ticket, "expires_in": STREAM_EXPIRE_SECONDS})
