# -*- coding: utf-8 -*-
"""统一响应构造工具（设计基线 B0-1）。

所有接口返回均经本模块，保证 {code, message, data, request_id} 结构一致。
"""
import uuid
from typing import Any, Optional

from app.schemas.common import ApiResponse
from app.core.errors import BizError


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def success(
    data: Any = None,
    message: str = "success",
    request_id: Optional[str] = None,
) -> ApiResponse:
    """成功响应，code 固定为 0。"""
    return ApiResponse(
        code=0,
        message=message,
        data=data,
        request_id=request_id or new_request_id(),
    )


def error(
    code: int,
    message: str,
    data: Any = None,
    request_id: Optional[str] = None,
) -> ApiResponse:
    """错误/降级响应，code 为非零业务码。"""
    return ApiResponse(
        code=code,
        message=message,
        data=data,
        request_id=request_id or new_request_id(),
    )


def from_biz(exc: BizError, request_id: Optional[str] = None) -> ApiResponse:
    """将 BizError 转为统一错误响应。"""
    return error(exc.code, exc.message, exc.data, request_id)
