# -*- coding: utf-8 -*-
"""智能体中心与五大健康场景生产路由。

配置驱动：目录与场景均读 `system_config`（`agent.catalog` / `scene.config`），
缺省使用内置定义；管理员可通过 `PUT /admin/config` 维护，无需改代码。
"""
from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import DispatchReq
from app.services.plan_service import plan_service

router = APIRouter(prefix="", tags=["智能体中心"])


@router.get("/agents", summary="智能体目录", description="主智能体 + 六个子智能体列表（配置驱动）。")
async def agents(_: dict = Depends(get_current_user)):
    return success(data=plan_service.agents())


@router.post("/agents/dispatch", summary="主智能体调度", description="输入一句需求，由主智能体按规则规划：调用哪些子智能体、按什么顺序、为什么（规则引擎，不经大模型）。")
async def dispatch(body: DispatchReq, _: dict = Depends(get_current_user)):
    return success(data=plan_service.dispatch(body.text), message="调度方案已生成")


@router.get("/agents/{key}", summary="智能体详情", description="查看单个智能体的能力说明与启用状态。")
async def agent_detail(key: str, _: dict = Depends(get_current_user)):
    return success(data=plan_service.agent_detail(key))


@router.get("/scenes", summary="五大健康场景", description="控糖/慢病/高血压/孕期/痰湿等场景入口配置。")
async def scenes(_: dict = Depends(get_current_user)):
    return success(data=plan_service.scenes())
