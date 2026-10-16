# -*- coding: utf-8 -*-
"""鉴权依赖（设计基线 B0-3）：JWT 2h + refresh 7d + 游客 + 角色校验。

角色枚举：user / nutritionist / operator / admin
  - operator（运营大屏）是否独立角色待 CHK-08 确认，此处先纳入以支持运营视角。
  - 游客 guest：匿名体验用，权限受限，调用 LLM 类接口须走限流（M1 待补）。

使用方式：
  from app.core.deps import get_current_user, get_optional_user, require_role, ROLE_ADMIN
  @router.get("/x")
  def x(u: dict = Depends(get_current_user)): ...
  @router.get("/admin/x")
  def x(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))): ...
"""
from typing import Optional
from fastapi import Depends, Header

from app.utils.security import decode_token
from app.core.errors import BizError, ErrCode

ROLE_USER = "user"
ROLE_DIETITIAN = "dietitian"   # P1-5 修复（2026-09-11）：注册白名单产出 "dietitian"，原 "nutritionist" 无任何路径产出
LEGACY_ROLE_DIETITIAN = "nutritionist"  # 历史兼容：dietitian_service 仍容忍旧值
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
GUEST_ROLE = "guest"


def _parse_auth(authorization: Optional[str], allowed_types: tuple[str, ...] = ()) -> Optional[dict]:
    """从 Authorization: Bearer <token> 解析 payload；格式错误、已登出或缺失返回 None。"""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    payload = decode_token(token)
    if payload and allowed_types and payload.get("type") not in allowed_types:
        return None
    if payload:
        # 登出黑名单：令牌签名合法但已被主动失效（IT-A-08）。
        # 用函数内导入避免 utils ↔ core 的模块级循环导入。
        from app.utils.token_blacklist import is_revoked
        if is_revoked(token):
            return None
    return payload


def _assert_user_active(payload: dict) -> None:
    """校验令牌对应用户**仍然存在且未被停用**。

    2026-10-16 补测发现：此前鉴权只验签名，不看账号状态，
    于是"管理员禁用用户"与"用户自己注销"都拦不住已经签发的旧令牌——
    被禁用的营养师照样能拉客户名单、注销账号照样能读自己的报告
    （用例 IT-N-10 / IT-AD-01 / IT-U-06 均命中）。

    真实场景中每张令牌都对应一个 sys_user 行；查不到即视为账号已被清理，同样拒绝。
    这是主键等值查询，开销远低于业务查询本身。
    """
    from app.dao.user_dao import UserDAO

    user = UserDAO.get_by_user_id(str(payload.get("sub") or ""))
    if not user:
        raise BizError(ErrCode.TOKEN_INVALID, "账号不存在或已注销")
    if user.status != "active":
        raise BizError(ErrCode.PERMISSION_DENIED, "账号已被停用")


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict:
    """解析登录用户；缺失、非法或账号已停用抛 BizError(10001/10003/10005)。"""
    if not authorization:
        raise BizError(ErrCode.UNAUTHENTICATED, "未登录或缺少 Authorization 头")
    payload = _parse_auth(authorization, ("access",))
    if not payload:
        raise BizError(ErrCode.TOKEN_INVALID, "令牌无效或已过期")
    _assert_user_active(payload)
    return payload


def get_optional_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[dict]:
    """游客/匿名可用：拿不到就返回 None，不报错（用于游客试测、公开查询）。"""
    return _parse_auth(authorization, ("access", "guest"))


def resolve_sse_user(request) -> Optional[dict]:
    """SSE 通道鉴权（设计评审 B1）：优先 ?ticket= 短票，回退 Bearer，游客返回 None。

    EventSource 不支持自定义请求头，因此短票走查询参数；非法短票直接报 10003。
    """
    ticket = request.query_params.get("ticket")
    if ticket:
        payload = decode_token(ticket)
        if payload and payload.get("type") == "stream":
            return payload
        raise BizError(ErrCode.TOKEN_INVALID, "短票无效或已过期")
    auth = request.headers.get("Authorization")
    if auth:
        return _parse_auth(auth, ("access", "guest"))
    return None


def require_role(*roles: str):
    """构造角色校验依赖，例：require_role(ROLE_ADMIN, ROLE_OPERATOR)。"""
    def _dep(payload: dict = Depends(get_current_user)):
        if payload.get("role") not in roles:
            raise BizError(ErrCode.PERMISSION_DENIED, "无权限访问该资源")
        return payload
    return _dep


def require_approved_dietitian():
    """营养师业务接口依赖：角色为营养师**且资质已通过审核**（admin 放行）。

    与 require_role 的区别：require_role 只看令牌里的角色字符串，
    而"是不是合格营养师"取决于后台审核结果（dietitian_profile.audit_status），
    必须在服务端逐次校验，不能信任客户端身份声明。
    """
    def _dep(payload: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
        if payload.get("role") == ROLE_ADMIN:
            return payload
        from app.services.dietitian_service import dietitian_service
        dietitian_service.ensure_approved(payload)
        return payload
    return _dep
