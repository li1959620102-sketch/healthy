# -*- coding: utf-8 -*-
"""场景扩展路由：社交宴请 / 旅行美食智荐 / OCR 体检报告。

一期均为「留桩 + 交互」：数据结构与状态齐备，依赖第三方或未达标模型的能力
（目的地 POI、OCR 引擎）返回 enabled=false + 明确 note，**不伪造结果**。
"""
from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import (BanquetCreateReq, BanquetUpdateReq,
                                  TravelCreateReq)
from app.services.scene_service import scene_service

banquet_router = APIRouter(prefix="/banquets", tags=["社交宴请助手"])
travel_router = APIRouter(prefix="/travel-plans", tags=["旅行美食智荐"])
ocr_router = APIRouter(prefix="/ocr-reports", tags=["OCR 体检报告"])


# ----------------------------- 社交宴请助手 ----------------------------- #
@banquet_router.post("", summary="新建宴请菜单", description="录入宾客慢病/过敏/体质，按忌口过滤生成菜单并标注风险（原型「新建宴请」）。")
async def create_banquet(body: BanquetCreateReq, user: dict = Depends(get_current_user)):
    return success(data=scene_service.create_banquet(user, body.model_dump(exclude_none=True)),
                   message="宴请菜单草稿已创建")


@banquet_router.get("", summary="宴请列表", description="本人创建的宴请草稿。")
async def list_banquets(limit: int = Query(20, description="返回条数"),
                        user: dict = Depends(get_current_user)):
    return success(data=scene_service.list_banquets(user, limit))


@banquet_router.get("/{menu_id}", summary="宴请详情", description="含菜单、宾客约束与逐菜风险标注。")
async def get_banquet(menu_id: int, user: dict = Depends(get_current_user)):
    return success(data=scene_service.get_banquet(user, menu_id))


@banquet_router.put("/{menu_id}", summary="编辑宴请（协同）", description="可改主题、人数、预算、状态与宾客清单。")
async def update_banquet(menu_id: int, body: BanquetUpdateReq,
                         user: dict = Depends(get_current_user)):
    return success(data=scene_service.update_banquet(user, menu_id, body.model_dump(exclude_none=True)))


# ----------------------------- 旅行美食智荐 ----------------------------- #
@travel_router.post("", summary="规划旅行饮食", description="出行前健康评估 + 通用出行建议；目的地餐厅/美食推荐待接 POI（留桩）。")
async def create_travel(body: TravelCreateReq, user: dict = Depends(get_current_user)):
    return success(data=scene_service.create_travel(user, body.model_dump(exclude_none=True)),
                   message="旅行饮食计划已创建")


@travel_router.get("", summary="旅行计划列表", description="本人创建的旅行饮食计划。")
async def list_travel(limit: int = Query(20, description="返回条数"),
                      user: dict = Depends(get_current_user)):
    return success(data=scene_service.list_travel(user, limit))


@travel_router.get("/{plan_id}", summary="旅行计划详情", description="含健康注意与出行建议（目的地推荐留桩）。")
async def get_travel(plan_id: int, user: dict = Depends(get_current_user)):
    return success(data=scene_service.get_travel(user, plan_id))


# ----------------------------- OCR 体检报告 ----------------------------- #
@ocr_router.post("", summary="上传体检/病历报告", description="存入对象存储并创建解析任务；OCR 引擎待接入（留桩+交互）。")
async def upload_ocr(file: UploadFile = File(..., description="体检报告图片/PDF"),
                     user: dict = Depends(get_current_user)):
    return success(data=scene_service.upload_ocr(user, file.file, file.filename or "report.jpg"),
                   message="已上传，解析引擎接入后自动解析")


@ocr_router.get("", summary="OCR 报告列表", description="本人上传的报告与解析状态。")
async def list_ocr(limit: int = Query(20, description="返回条数"),
                   user: dict = Depends(get_current_user)):
    return success(data=scene_service.list_ocr(user, limit))


@ocr_router.get("/{report_id}", summary="OCR 报告详情", description="状态与提取指标；引擎未接入时 indicators 为空并附说明。")
async def get_ocr(report_id: int, user: dict = Depends(get_current_user)):
    return success(data=scene_service.get_ocr(user, report_id))
