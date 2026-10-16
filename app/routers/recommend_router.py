# -*- coding: utf-8 -*-
"""M 组 · 智能荐菜路由（原型新增，依据设计_01 §二 M）。

荐菜走「知识库检索 + 规则过滤忌口」，不让 LLM 自由发挥（红线：推荐结果必须可追溯）。
"""
from typing import List, Optional

import asyncio

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_optional_user
from app.core.responses import success
from app.schemas.platform import RecommendReq
from app.services.plan_service import plan_service

router = APIRouter(prefix="/recommend", tags=["智能荐菜"])


@router.post("/dishes", summary="智能荐菜", description="按体质/慢病/忌口推荐食材与菜谱，附知识库引用；游客可用。")
async def dishes(body: RecommendReq, _: Optional[dict] = Depends(get_optional_user)):
    # plan_service 内部是同步的向量检索（阻塞调用）→ 挪到线程池，避免冻结事件循环
    return success(data=await asyncio.to_thread(
        plan_service.recommend_dishes,
        body.query or "", body.avoid_foods, body.constitution))


@router.get("/ingredients", summary="食材库检索", description="检索知识库中的食材营养与功效（依赖 K 组食材语料已灌入）。")
async def ingredients(keyword: str = Query(..., description="食材关键字"),
                      top_k: int = Query(8, ge=1, le=20),
                      _: Optional[dict] = Depends(get_optional_user)):
    return success(data=await asyncio.to_thread(plan_service.search_ingredients, keyword, top_k))
