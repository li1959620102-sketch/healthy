# -*- coding: utf-8 -*-
"""用户注册 / 登录 / 当前登录信息。"""
import logging
from typing import Optional

from fastapi import Header, status
from pydantic import BaseModel, Field

from app.exceptions.base_api_exception import (
    AuthenticationException,
    ConflictException,
    ValidationException,
)
from app.routers.base_router import BaseRouter
from app.schemas.common import ApiResponse
from app.services.user_service import UserService
from app.utils.auth_helper import current_user

logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    username: str = Field(..., description="登录账号")
    password: str = Field(..., description="密码，至少 6 位")
    real_name: Optional[str] = Field(None, description="姓名")
    sex: Optional[str] = Field("其他", description="性别：男/女/其他")
    age: Optional[int] = Field(None, description="年龄")
    role: Optional[str] = Field("user", description="角色：user/dietitian/admin")

    class Config:
        json_schema_extra = {
            "example": {
                "username": "demo",
                "password": "123456",
                "real_name": "李女士",
                "sex": "女",
                "age": 38,
                "role": "user",
            }
        }


class LoginRequest(BaseModel):
    username: str = Field(..., description="登录账号")
    password: str = Field(..., description="密码")

    class Config:
        json_schema_extra = {"example": {"username": "demo", "password": "123456"}}


class UserRouter(BaseRouter):
    """用户账号路由"""

    def __init__(self):
        super().__init__()
        self.router = self._register_routes()

    def _register_routes(self):
        self.router.post(
            "/register",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="注册新用户",
            description="注册账号并返回用户信息（密码以密文存储）",
            tags=["用户与登录"],
        )(self.register)

        self.router.post(
            "/login",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="账号登录",
            description="校验账号密码，返回登录令牌 token",
            tags=["用户与登录"],
        )(self.login)

        self.router.get(
            "/me",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="获取当前登录用户",
            description="请求头携带 Authorization: Bearer <token>",
            tags=["用户与登录"],
        )(self.me)

        return self.router

    async def register(self, request: RegisterRequest) -> ApiResponse:
        try:
            user = UserService.register(
                username=request.username.strip(),
                password=request.password,
                real_name=(request.real_name or "").strip(),
                role=request.role or "user",
                sex=request.sex or "其他",
                age=request.age,
            )
            return ApiResponse(status=200, message="注册成功", data=user)
        except (ValidationException, ConflictException) as e:
            return ApiResponse(status=e.status_code, message=str(e.detail), data={})

    async def login(self, request: LoginRequest) -> ApiResponse:
        try:
            result = UserService.login(request.username.strip(), request.password)
            return ApiResponse(status=200, message="登录成功", data=result)
        except AuthenticationException as e:
            return ApiResponse(status=401, message=str(e.detail), data={})

    async def me(self, authorization: Optional[str] = Header(None, description="Bearer <token>")) -> ApiResponse:
        try:
            user = current_user(authorization)
            return ApiResponse(status=200, message="success", data=user)
        except AuthenticationException as e:
            return ApiResponse(status=401, message=str(e.detail), data={})
