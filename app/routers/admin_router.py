# -*- coding: utf-8 -*-
"""J 组 · 管理路由 + P 组 · 运营大屏（依据设计_01 §二 J / P）。

敏感操作（禁用账号、资质审核、改评分/智能体参数）统一写操作日志。
运营大屏 `/admin/dashboard/ops` 对管理员与运营(operator)开放。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.core.responses import success
from app.schemas.platform import (AdminConfigReq, AlertThresholdReq, AuditReq,
                                  DashboardLayoutReq, RolePermissionReq, UserStatusReq)
from app.services.admin_service import admin_service
from app.services.scene_service import scene_service

router = APIRouter(prefix="/admin", tags=["管理"])


def _ip(request: Request) -> Optional[str]:
    return request.headers.get("X-Forwarded-For") or (
        request.client.host if request.client else None)


@router.get("/users", summary="用户列表", description="支持按角色、状态、关键字筛选；不返回密文字段。")
async def users(role: Optional[str] = Query(None, description="user/dietitian/admin"),
                status: Optional[str] = Query(None, description="active/disabled"),
                keyword: Optional[str] = Query(None, description="账号或姓名模糊匹配"),
                limit: int = Query(50, ge=1, le=200),
                _: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.users(role, status, keyword, limit))


@router.put("/users/{user_id}/status", summary="禁用/解封用户", description="状态：active / disabled；操作记入审计日志。")
async def set_status(user_id: str, body: UserStatusReq, request: Request,
                     user: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.set_user_status(user, user_id, body.status, _ip(request)))


@router.get("/nutritionists/audits", summary="资质审核列表", description="待审核的营养师资质申请。")
async def nutritionist_audits(_: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.nutritionist_audits())


@router.post("/nutritionists/{user_id}/audit", summary="审核营养师资质", description="通过后该营养师可被搜索与绑定。")
async def audit_nutritionist(user_id: str, body: AuditReq, request: Request,
                             user: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.audit_nutritionist(
        user, user_id, body.approve, body.reason or "", _ip(request)))


@router.get("/templates/audits", summary="模板审核列表", description="待审核的测评模板（审核动作在 /templates/{id}/audit）。")
async def template_audits(_: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.template_audits())


@router.get("/templates", summary="全量模板列表", description="管理员视角的测评模板全量清单，可按状态筛选（draft/pending/published/offline）。")
async def templates(status: Optional[str] = Query(None, description="draft/pending/published/offline"),
                    limit: int = Query(200, ge=1, le=500),
                    _: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.templates(status, limit))


@router.get("/config", summary="系统参数列表", description="含智能体参数、评分权重与阈值（scoring.*）等。")
async def get_config(_: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.list_configs())


@router.put("/config", summary="设置系统参数", description="修改 scoring.* / agent.* / scene.* 会自动留痕，便于追溯评分版本。")
async def set_config(body: AdminConfigReq, request: Request,
                     user: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.set_config(user, body.config_key, body.config_value,
                                                 body.description, _ip(request)))


@router.get("/alerts/thresholds", summary="预警阈值", description="运营预警阈值（FR-V-10）：默认值、当前生效值与合法区间；通知渠道待网关接入。")
async def get_alerts(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    return success(data=admin_service.alert_thresholds())


@router.put("/alerts/thresholds", summary="保存预警阈值", description="越界值自动收敛到合法区间；写入 alert.thresholds 并记审计日志。")
async def set_alerts(body: AlertThresholdReq, request: Request,
                     user: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.set_alert_thresholds(user, body.thresholds, _ip(request)),
                   message="预警阈值已保存")


@router.get("/roles", summary="角色权限矩阵", description="需求 §3.1 RBAC：角色清单、能力清单与当前开关（未保存时由默认矩阵兜底）。")
async def get_roles(_: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.roles())


@router.put("/roles", summary="调整角色权限", description="调整单个「角色 × 能力」开关，整表回写 role.permissions 并记审计日志。")
async def set_role(body: RolePermissionReq, request: Request,
                   user: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.set_role_permission(
        user, body.capability, body.role, body.allowed, _ip(request)), message="权限已更新")



@router.get("/overview", summary="运营概览", description="用户/测评/报告/咨询等核心指标汇总。")
async def overview(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    return success(data=admin_service.overview())


@router.get("/operation-logs", summary="操作日志", description="管理员敏感操作审计，保留 ≥1 年。")
async def operation_logs(action: Optional[str] = Query(None, description="按动作筛选"),
                         limit: int = Query(100, ge=1, le=500),
                         _: dict = Depends(require_role(ROLE_ADMIN))):
    return success(data=admin_service.logs(action, limit))


@router.get("/dashboard/ops", summary="运营可视化大屏", description="P 组：聚合统计 + 等级分布 + 近 7 日趋势，供大屏展示。")
async def ops_dashboard(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    return success(data=admin_service.ops_dashboard())


@router.get("/dashboard/ops/config", summary="大屏方案配置", description="组件布局 / 刷新周期 / 主题 / 异常高亮；无配置时返回默认布局（原型大屏设置）。")
async def get_ops_config(user: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    return success(data=scene_service.get_layout(user))


@router.put("/dashboard/ops/config", summary="保存大屏方案", description="保存拖拽组装后的组件布局与设置（原型「保存方案」），动作记入审计日志。")
async def save_ops_config(body: DashboardLayoutReq, request: Request,
                          user: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    payload = body.model_dump(exclude_none=True)
    data = scene_service.save_layout(user, payload)
    admin_service.log_dashboard_update(user, {"fields": sorted(payload.keys())}, _ip(request))
    return success(data=data, message="方案已保存")


@router.post("/dashboard/ops/snapshot", summary="导出大屏截图（留桩）", description="需服务端无头浏览器渲染，待接入；当前返回布局与数据快照，不伪造图片。")
async def ops_snapshot(user: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    return success(data=scene_service.export_snapshot(user))
