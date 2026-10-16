# -*- coding: utf-8 -*-
"""D 组 · 测评模板路由（依据设计_01 §二 D）。

版本不可变：编辑（PUT）不改动原行，而是生成新版本行，
保证历史报告始终能追溯到发起时锁定的那一份模板。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import get_current_user, get_optional_user, require_role, ROLE_ADMIN, ROLE_DIETITIAN
from app.core.errors import BizError, ErrCode
from app.core.responses import success
from app.dao.assessment_dao import TemplateDAO, session_scope
from app.schemas.platform import TemplateAuditReq, TemplateCreateReq, TemplateUpdateReq
from app.services.admin_service import admin_service

router = APIRouter(prefix="/templates", tags=["测评模板"])


def _ip(request: Request) -> Optional[str]:
    return (request.headers.get("X-Forwarded-For")
            or (request.client.host if request.client else None))


def _brief(t) -> dict:
    return {
        "id": t.id,
        "code": t.code,
        "version": int(t.version) if t.version is not None else None,
        "name": t.name,
        "type": t.type,
        "status": t.status,
        "description": t.description,
        "created_by": t.created_by,
        "published_at": t.published_at,
        "created_at": t.created_at,
    }


@router.post("", summary="创建测评模板", description="营养师/管理员创建模板，初始为草稿状态。")
async def create_template(body: TemplateCreateReq,
                          user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    with session_scope() as s:
        exist = TemplateDAO.latest_version(body.code, session=s)
        if exist:
            raise BizError(ErrCode.TEMPLATE_VERSION_CONFLICT,
                           "模板编码已存在，请使用编辑接口生成新版本")
        tpl = TemplateDAO.create({
            "code": body.code,
            "version": 1,
            "name": body.name,
            "type": body.type,
            "description": body.description,
            "dimension_config": body.dimension_config,
            "question_config": body.question_config,
            "status": "draft",
            "created_by": str(user.get("sub")),
        }, session=s)
        return success(data=_brief(tpl))


@router.get("", summary="已发布模板列表", description="用户可见的模板列表，可按类型筛选；游客可访问。")
async def list_templates(type_: Optional[str] = Query(None, alias="type", description="risk/nutrition/constitution/tongue"),
                         _: Optional[dict] = Depends(get_optional_user)):
    with session_scope() as s:
        rows = TemplateDAO.list_published(type_=type_, session=s)
        return success(data={"items": [_brief(t) for t in rows], "total": len(rows)})


@router.get("/mine", summary="我创建的模板", description="营养师/管理员查看本人创建的全部模板（含草稿）。")
async def list_mine(user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    with session_scope() as s:
        rows = TemplateDAO.list_mine(str(user.get("sub")), session=s)
        return success(data={"items": [_brief(t) for t in rows], "total": len(rows)})


@router.get("/{template_id}", summary="模板详情", description="含维度配置与题目配置。")
async def get_template(template_id: int, _: dict = Depends(get_current_user)):
    with session_scope() as s:
        tpl = TemplateDAO.get(template_id, session=s)
        if not tpl:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        data = _brief(tpl)
        data.update({
            "dimension_config": tpl.dimension_config,
            "question_config": tpl.question_config,
        })
        return success(data=data)


@router.get("/{template_id}/versions", summary="模板版本列表", description="同一 code 下的全部历史版本。")
async def list_versions(template_id: int,
                        user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    with session_scope() as s:
        tpl = TemplateDAO.get(template_id, session=s)
        if not tpl:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        rows = TemplateDAO.list_versions(tpl.code, session=s)
        return success(data={"code": tpl.code, "items": [_brief(t) for t in rows], "total": len(rows)})


@router.put("/{template_id}", summary="编辑模板（生成新版本）", description="原版本保持不变，返回新版本模板；仅创建者可编辑。")
async def update_template(template_id: int, body: TemplateUpdateReq,
                          user: dict = Depends(get_current_user)):
    with session_scope() as s:
        tpl = TemplateDAO.get(template_id, session=s)
        if not tpl:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        if str(tpl.created_by) != str(user.get("sub")) and user.get("role") != ROLE_ADMIN:
            raise BizError(ErrCode.PERMISSION_DENIED, "仅模板创建者可编辑")
        new_tpl = TemplateDAO.new_version(tpl, body.model_dump(exclude_none=True), session=s)
        return success(data=_brief(new_tpl))


@router.post("/{template_id}/submit-audit", summary="提交审核", description="草稿提交审核，进入待审核状态。")
async def submit_audit(template_id: int, user: dict = Depends(get_current_user)):
    with session_scope() as s:
        tpl = TemplateDAO.get(template_id, session=s)
        if not tpl:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        if str(tpl.created_by) != str(user.get("sub")) and user.get("role") != ROLE_ADMIN:
            raise BizError(ErrCode.PERMISSION_DENIED, "仅模板创建者可提交审核")
        if tpl.status not in ("draft",):
            raise BizError(ErrCode.PARAM_INVALID, f"当前状态（{tpl.status}）不可提交审核")
        updated = TemplateDAO.submit_audit(template_id, session=s)
        return success(data=_brief(updated))


@router.post("/{template_id}/audit", summary="审核模板", description="管理员审核通过或驳回；通过后进入已发布；审核动作记入审计日志。")
async def audit_template(template_id: int, body: TemplateAuditReq, request: Request,
                         user: dict = Depends(require_role(ROLE_ADMIN))):
    with session_scope() as s:
        updated = TemplateDAO.audit(template_id, str(user.get("sub")),
                                    body.approve, body.remark or "", session=s)
        if not updated:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        brief = _brief(updated)
    admin_service.log_template_audit(
        user, template_id, "template.audit",
        {"approve": body.approve, "remark": body.remark or "", "status": brief.get("status")},
        _ip(request))
    return success(data=brief, message="审核通过" if body.approve else "已驳回")


@router.post("/{template_id}/offline", summary="下线模板", description="管理员下线；历史报告仍可查看，进行中的测评允许完成；动作记入审计日志。")
async def offline_template(template_id: int, request: Request,
                           user: dict = Depends(require_role(ROLE_ADMIN))):
    with session_scope() as s:
        updated = TemplateDAO.offline(template_id, session=s)
        if not updated:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")
        brief = _brief(updated)
    admin_service.log_template_audit(user, template_id, "template.offline",
                                     {"status": brief.get("status")}, _ip(request))
    return success(data=brief, message="已下线")
