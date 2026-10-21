# -*- coding: utf-8 -*-
"""O 组 · 舌象图片路由（原型新增，依据设计_01 §二 O）。

链路：上传 → 存对象存储 → 建记录（uploaded）→ 后台辨识（recognizing）
→ 落结果（recognized / failed）→ 前端轮询详情。

**辨识为什么是异步的**：qwen2.5vl:3b 在纯 CPU 上单张约 30~40 秒，同步返回
必然撞 HTTP 超时，而且会长时间占住一个 worker。因此上传与触发都只"排队"，
结果一律通过 `GET /tongue/{id}` 读取。

**结果语义**：`status=recognized` 不等于"识别出了舌头"。
没看到舌体时同样是 recognized，但 `recognized_result.visible=false`——
这是正常的负结果，前端应引导重拍，而不是显示系统错误。
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile

from app.core.deps import get_current_user
from app.core.responses import success
from app.services.plan_service import plan_service

router = APIRouter(prefix="/tongue", tags=["舌象"])


@router.post("/upload", summary="上传舌象图片",
             description="校验格式与大小后存入对象存储；上传成功后自动排队做 AI 辨识，"
                         "辨识结果用 GET /tongue/{image_id} 轮询获取（单张约 30~40 秒）。")
async def upload(background: BackgroundTasks,
                 file: UploadFile = File(..., description="舌象图片（JPG/PNG/WEBP/BMP，≤8MB）"),
                 record_id: Optional[int] = Form(None, description="关联测评记录"),
                 user: dict = Depends(get_current_user)):
    data = await asyncio.to_thread(
        plan_service.upload_tongue, user, file.file, file.filename or "tongue.jpg", record_id
    )
    if data.get("status") == "uploaded":
        # 后台线程里跑，不阻塞本次响应
        background.add_task(plan_service.recognize_tongue, data["image_id"])
    return success(data=data, message=data.get("note") or "上传成功")


@router.get("/{image_id}", summary="舌象详情",
            description="查看舌图与辨识状态（uploaded / recognizing / recognized / failed）；"
                        "recognized_result 中含可见特征、置信度与免责说明。")
async def detail(image_id: int, user: dict = Depends(get_current_user)):
    return success(data=plan_service.get_tongue(user, image_id))


@router.post("/{image_id}/recognize", summary="重新触发 AI 辨识",
             description="对已上传的舌图重新排队辨识（例如上次因模型不可用失败）。"
                         "同样异步执行，结果用详情接口轮询。")
async def recognize(image_id: int, background: BackgroundTasks,
                    force: bool = Query(False, description="正在辨识中也强制重跑"),
                    user: dict = Depends(get_current_user)):
    # 先做归属校验：别让 A 用户触发 B 用户的图
    plan_service.get_tongue(user, image_id)
    background.add_task(plan_service.recognize_tongue, image_id, force=force)
    return success(data={"image_id": image_id, "status": "queued",
                         "poll": f"/api/v1/tongue/{image_id}"},
                   message="已开始辨识，请稍候轮询详情")
