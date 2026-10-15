# -*- coding: utf-8 -*-
"""L 组 · 食养食疗计划路由（原型新增 F2，依据设计_01 §二 L）。

食谱骨架由规则生成（确定性），方案总述由 LLM 撰写且失败可降级 `state=basic`。
"""
from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import PlanCreateReq, PlanUpdateReq
from app.services.plan_service import plan_service

router = APIRouter(prefix="/diet-plans", tags=["食养计划"])


@router.post("", summary="生成食养方案", description="基于报告或体质生成；食谱骨架规则生成，方案总述由 AI 撰写（失败自动降级为基础版）。")
async def create(body: PlanCreateReq, user: dict = Depends(get_current_user)):
    return success(data=await plan_service.create_plan(user, body.model_dump(exclude_none=True)))


@router.get("", summary="我的方案列表", description="按生成时间倒序。")
async def list_mine(user: dict = Depends(get_current_user)):
    return success(data=plan_service.list_mine(user))


@router.get("/{plan_id}", summary="方案详情", description="含每日早/午/晚食谱、功效与注意事项。")
async def detail(plan_id: int, user: dict = Depends(get_current_user)):
    return success(data=plan_service.detail(user, plan_id))


@router.put("/{plan_id}", summary="手动调整方案", description="可改标题、体质、针对问题、忌口与总述。")
async def update(plan_id: int, body: PlanUpdateReq, user: dict = Depends(get_current_user)):
    return success(data=await plan_service.update(user, plan_id, body.model_dump(exclude_none=True)))


@router.get("/{plan_id}/shopping-list", summary="采购清单", description="按食材聚合去重并剔除忌口（原型 diet 页「采购清单」）。")
async def shopping_list(plan_id: int, user: dict = Depends(get_current_user)):
    return success(data=plan_service.shopping_list(user, plan_id))


@router.post("/{plan_id}/regenerate", summary="重新生成总述", description="食谱骨架不变，仅重新生成 AI 方案总述。")
async def regenerate(plan_id: int, user: dict = Depends(get_current_user)):
    return success(data=await plan_service.regenerate(user, plan_id))
