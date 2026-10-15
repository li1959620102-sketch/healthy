# -*- coding: utf-8 -*-
"""C 组 · 营养师路由（依据设计_01 §二 C）。

绑定机制：邀请码绑定 / 咨询完成自动绑定 / 管理员指派；同期仅 1 名营养师（DB 层强制）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_DIETITIAN
from app.core.errors import BizError, ErrCode
from app.core.responses import success
from app.schemas.platform import BindByCodeReq, DietitianApplyReq, DietitianUpdateReq
from app.services.dietitian_service import dietitian_service

router = APIRouter(prefix="/nutritionists", tags=["营养师"])


@router.post("/apply", summary="提交营养师资质", description="提交证书与工作证明，进入待审核。")
async def apply(body: DietitianApplyReq, user: dict = Depends(get_current_user)):
    return success(data=dietitian_service.apply(user, body.model_dump(exclude_none=True)),
                   message="资质已提交，等待审核")


@router.get("/me", summary="我的营养师信息", description="含审核状态、擅长领域与客户数。")
async def me(user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.get_me(user))


@router.put("/me", summary="维护专业信息", description="更新擅长领域与服务案例，不影响审核状态。")
async def update_me(body: DietitianUpdateReq,
                    user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.update_me(user, body.model_dump(exclude_none=True)))


@router.get("/search", summary="搜索营养师", description="按擅长领域筛选已通过审核的营养师。")
async def search(specialty: Optional[str] = Query(None, description="擅长领域，如 孕期营养"),
                 limit: int = Query(20, ge=1, le=100),
                 _: dict = Depends(get_current_user)):
    return success(data=dietitian_service.search(specialty, limit=limit))


@router.post("/me/invite-code", summary="生成绑定邀请码", description="审核通过后生成；绑定成功即失效。")
async def invite_code(user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.new_invite_code(user))


@router.get("/me/customers", summary="客户列表", description="可传 level 筛选，如 needs_improvement 只看待改善客户；含标签/食养执行率/最近咨询。")
async def customers(level: Optional[str] = Query(None, description="按最近报告等级筛选"),
                    user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.customers(user, level))


@router.get("/me/overview", summary="工作台概览", description="KPI 聚合：管辖客户 / 高风险待跟进 / 今日咨询 / 满意度（原型营养师工作台）。")
async def overview(user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.overview(user))


@router.get("/me/customers/export", summary="导出客户报表", description="导出为 CSV / XLSX，入 MinIO 并返回 7 天有效的签名 URL（原型「导出客户报表」）。")
async def export_customers(fmt: str = Query("csv", description="导出格式 csv / xlsx"),
                           user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.export_customers(user, fmt), message="报表已生成")


@router.post("/me/customers/bind", summary="凭邀请码绑定营养师", description="用户视角：用营养师提供的邀请码建立绑定。")
async def bind(body: BindByCodeReq, user: dict = Depends(get_current_user)):
    return success(data=dietitian_service.bind_by_code(user, body.invite_code), message="绑定成功")


@router.delete("/me/customers/{customer_uid}", summary="解除绑定",
               description="营养师解除与指定客户的关系；普通用户调用 /nutritionists/me/unbind 解绑自己的营养师。")
async def unbind_customer(customer_uid: str,
                          user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.unbind(user, customer_uid), message="已解除绑定")


@router.post("/me/unbind", summary="解绑我的营养师", description="用户主动解除当前绑定的营养师。")
async def unbind_self(user: dict = Depends(get_current_user)):
    return success(data=dietitian_service.unbind(user), message="已解绑")


@router.get("/me/customers/{customer_uid}/reports", summary="客户测评记录", description="仅可查看已绑定给自己（或管理员）的客户报告。")
async def customer_reports(customer_uid: str,
                           user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=dietitian_service.customer_reports(user, customer_uid))


@router.get("/{dietitian_id}", summary="营养师公开信息", description="仅展示已通过审核的营养师。")
async def public_info(dietitian_id: str, _: dict = Depends(get_current_user)):
    return success(data=dietitian_service.public_info(dietitian_id))
