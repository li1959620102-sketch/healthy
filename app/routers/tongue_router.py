# -*- coding: utf-8 -*-
"""O 组 · 舌象图片路由（原型新增，依据设计_01 §二 O）。

⚠️ AI 辨识**待 S4 Spike 验证后才启用**：当前仅做上传存储与状态管理
（status=uploaded / failed），`recognizable=false`，不做任何体质判定。
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.deps import get_current_user
from app.core.responses import success
from app.services.plan_service import plan_service

router = APIRouter(prefix="/tongue", tags=["舌象"])


@router.post("/upload", summary="上传舌象图片", description="存入对象存储；AI 辨识待 S4 验证后启用，当前仅保存。")
async def upload(file: UploadFile = File(..., description="舌象图片"),
                 record_id: Optional[int] = Form(None, description="关联测评记录"),
                 user: dict = Depends(get_current_user)):
    data = plan_service.upload_tongue(user, file.file, file.filename or "tongue.jpg", record_id)
    return success(data=data,
                   message="上传成功" if data["status"] == "uploaded" else "已记录，存储稍后重试")


@router.get("/{image_id}", summary="舌象详情", description="查看舌图与辨识状态；S4 未达标时无辨识结果。")
async def detail(image_id: int, user: dict = Depends(get_current_user)):
    return success(data=plan_service.get_tongue(user, image_id))
