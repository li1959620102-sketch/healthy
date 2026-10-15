# -*- coding: utf-8 -*-
"""打卡路由（原型：首页「本周打卡 5/7」/ 食养页「今日打卡 · 执行率」）。"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import CheckinReq
from app.services.checkin_service import checkin_service

router = APIRouter(prefix="/checkins", tags=["打卡"])


@router.post("", summary="打卡", description="默认今日打卡；同日同类型重复调用为幂等更新，不重复计数。")
async def checkin(body: CheckinReq, user: dict = Depends(get_current_user)):
    return success(data=checkin_service.checkin(
        user, body.check_type, body.plan_id, body.note, body.checkin_date),
        message="打卡成功")


@router.get("", summary="我的打卡记录", description="返回最近 N 天的打卡明细。")
async def list_checkins(days: int = Query(30, description="回溯天数 1-365，越界按 30 处理"),
                        user: dict = Depends(get_current_user)):
    return success(data=checkin_service.list_mine(user, days))


@router.get("/stats", summary="打卡统计", description="本周执行率（分母固定 7，与原型 5/7=71% 一致）、今日是否已打卡、连续打卡天数。")
async def checkin_stats(user: dict = Depends(get_current_user)):
    return success(data=checkin_service.stats(user))
