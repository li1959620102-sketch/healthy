# -*- coding: utf-8 -*-
"""从请求头中解析当前登录用户。"""
from typing import Optional

from app.exceptions.base_api_exception import AuthenticationException
from app.services.user_service import UserService


def parse_bearer(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization 头中提取令牌"""
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def current_user(authorization: Optional[str]) -> dict:
    """必须登录：解析失败时抛 401"""
    token = parse_bearer(authorization)
    if not token:
        raise AuthenticationException("缺少登录令牌，请先登录")
    user = UserService.get_by_token(token)
    if not user:
        raise AuthenticationException("登录已失效，请重新登录")
    return user


def optional_user(authorization: Optional[str]) -> Optional[dict]:
    """可选登录：解析失败返回 None，不抛异常"""
    token = parse_bearer(authorization)
    if not token:
        return None
    return UserService.get_by_token(token)
