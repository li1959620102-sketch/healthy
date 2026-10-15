# -*- coding: utf-8 -*-
"""消息通知路由（原型：顶栏「🔔 3」消息中心）。"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.responses import success
from app.services.notification_service import notification_service

router = APIRouter(prefix="/notifications", tags=["消息通知"])


@router.get("", summary="消息列表", description="返回消息列表与未读数（顶栏红点用 unread）。")
async def list_notifications(
    unread_only: bool = Query(False, description="只看未读"),
    limit: int = Query(50, description="最多返回条数 1-200"),
    user: dict = Depends(get_current_user),
):
    return success(data=notification_service.list_mine(user, unread_only, limit))


@router.post("/read-all", summary="全部标记已读", description="把当前用户的未读消息一次性置为已读。")
async def mark_all_read(user: dict = Depends(get_current_user)):
    return success(data=notification_service.mark_all_read(user), message="已全部标记为已读")


@router.post("/{notification_id}/read", summary="标记已读", description="仅本人消息可标记；非本人返回 404 防枚举。")
async def mark_read(notification_id: int, user: dict = Depends(get_current_user)):
    return success(data=notification_service.mark_read(user, notification_id))
