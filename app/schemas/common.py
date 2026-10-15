from typing import Optional, Any

from pydantic import BaseModel, Field, model_validator


class ApiResponse(BaseModel):
    """统一 API 响应（设计基线 B0-1）。

    结构：{code, message, data, request_id}
      - code=0 表示成功；非 0 表示业务错误（分段见 app/core/errors.ErrCode）
      - message 为人类可读信息
      - data 业务数据（成功时返回，错误或降级时可为 None / 含 state 字段）
      - request_id 请求追踪 ID，便于排查

    兼容旧调用：传入 status=200 自动转 code=0；status=4xx/5xx 转对应错误码，
    避免在迁移过渡期改坏既有路由。
    """

    code: int = Field(0, description="业务码：0=成功，非0=错误（见错误码表）")
    message: str = Field("success", description="响应信息：成功/错误描述")
    data: Optional[Any] = Field(None, description="业务数据：成功时返回具体数据")
    request_id: Optional[str] = Field(None, description="请求追踪ID，便于链路排查")
    page: Optional[Any] = Field(None, description="分页信息（兼容旧调用，建议移入 data）")

    @model_validator(mode="before")
    @classmethod
    def _compat_status(cls, data):
        if isinstance(data, dict) and "status" in data and "code" not in data:
            st = data.pop("status")
            data["code"] = 0 if st == 200 else st
        return data

    model_config = {
        # 允许从 ORM 对象属性解析（对应旧版 orm_mode=True）
        "from_attributes": True,
        # 允许 data/page 为任意类型（嵌套字典、列表等）
        "arbitrary_types_allowed": True,
    }
