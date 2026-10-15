# -*- coding: utf-8 -*-
"""H 组 · 人工咨询路由（依据设计_01 §二 H）。

状态机：pending（待接入）→ active（进行中）→ closed / timeout
转人工无营养师在线时（评审 M4）：仍进入待接入队列并给系统提示，不直接失败。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_DIETITIAN
from app.core.responses import success
from app.schemas.platform import (
    AcceptConsultReq, CloseConsultReq, ConsultCreateReq, RateConsultReq, SendMsgReq,
)
from app.services.consultation_service import consultation_service

router = APIRouter(prefix="/consultations", tags=["咨询"])


@router.post("", summary="发起 1v1 咨询", description="有专属营养师则直接指派，否则进入待接入队列。")
async def create(body: ConsultCreateReq, user: dict = Depends(get_current_user)):
    return success(data=consultation_service.create(user, body.question or "", body.type))


@router.get("", summary="咨询列表", description="用户看自己的；营养师看分配给自己的。")
async def list_mine(limit: int = Query(50, ge=1, le=200), user: dict = Depends(get_current_user)):
    return success(data=consultation_service.list_mine(user, limit))


@router.get("/pending", summary="待接入队列", description="营养师/管理员查看尚未接入的咨询。")
async def pending(limit: int = Query(50, ge=1, le=200),
                  user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=consultation_service.pending_queue(user, limit))


@router.post("/{consultation_id}/accept", summary="接入咨询", description="营养师接入待接入的咨询。")
async def accept(consultation_id: int, body: AcceptConsultReq = None,
                 user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=consultation_service.accept(user, consultation_id), message="已接入")


@router.get("/{consultation_id}/messages", summary="消息列表", description="仅相关方（本人/营养师/管理员）可见。")
async def messages(consultation_id: int, limit: int = Query(200, ge=1, le=1000),
                   user: dict = Depends(get_current_user)):
    return success(data=consultation_service.messages(user, consultation_id, limit))


@router.post("/{consultation_id}/messages", summary="发送消息", description="一期仅文本；已结束的咨询不可发送。")
async def send(consultation_id: int, body: SendMsgReq, user: dict = Depends(get_current_user)):
    return success(data=consultation_service.send(user, consultation_id, body.content))


@router.post("/{consultation_id}/close", summary="结束咨询", description="结束后若已有营养师，自动建立绑定关系。")
async def close(consultation_id: int, body: CloseConsultReq = None,
                user: dict = Depends(get_current_user)):
    reason = (body.reason if body and body.reason else "会话结束")
    return success(data=consultation_service.close(user, consultation_id, reason))


@router.post("/{consultation_id}/rating", summary="满意度评价", description="仅发起咨询的用户可评价，1-5 分。")
async def rating(consultation_id: int, body: RateConsultReq,
                 user: dict = Depends(get_current_user)):
    return success(data=consultation_service.rate(user, consultation_id, body.rating, body.comment or ""))
