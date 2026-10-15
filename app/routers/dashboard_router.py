# -*- coding: utf-8 -*-
"""I 组 · 数据看板路由（骨架，依据设计_01 §二 I）。

第一批为骨架：接口形态与结构已定，数据来源于 E/F 组（测评/报告，第二批）。
有数据时直接查库返回；无数据时返回空结构 + note，不报错。
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc

from app.schemas.platform import (
    TrendPoint, DashboardTrendResp, DashboardDimensionsResp, DashboardInsightResp,
)
from app.core.responses import success
from app.core.errors import BizError, ErrCode
from app.core.deps import get_current_user
from app.services.dashboard_service import dashboard_service
from app.utils.db import get_session
from app.models.mysql.platform import AssessmentReport, AssessmentTemplate

router = APIRouter(prefix="/dashboard", tags=["数据看板"])


@router.get("/personal/trend", summary="测评得分趋势", description="按时间返回得分趋势；数据来源于测评报告。")
async def personal_trend(
    template_id: int = Query(None, description="模板 ID（默认同类型最新）"),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    points: List[TrendPoint] = []
    session = get_session()
    try:
        q = session.query(AssessmentReport).filter_by(user_id=user_id)
        if template_id:
            q = q.filter_by(template_id=template_id)
        for r in q.order_by(AssessmentReport.generated_at).all():
            points.append(TrendPoint(
                date=str(r.generated_at)[:10] if r.generated_at else "",
                score=float(r.score) if r.score is not None else None,
                level=r.level,
            ))
        return success(data=DashboardTrendResp(
            template_id=template_id, points=points,
            note="测评数据来源于 E/F 组（第二批），当前为空" if not points else "",
        ).model_dump())
    finally:
        session.close()


@router.get("/personal/dimensions", summary="各维度得分对比", description="最新报告的维度得分。")
async def personal_dimensions(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    session = get_session()
    try:
        latest = session.query(AssessmentReport).filter_by(user_id=user_id).order_by(
            desc(AssessmentReport.generated_at)).first()
        dims = latest.dimension_scores if latest and latest.dimension_scores else []
        return success(data=DashboardDimensionsResp(
            dimensions=dims,
            note="维度得分来源于 assessment_report（第二批）" if not dims else "",
        ).model_dump())
    finally:
        session.close()


@router.get("/personal/insight", summary="智能解读", description="取最近一份报告已生成的 AI 分析文本；无报告时返回空并附说明。")
async def personal_insight(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    session = get_session()
    try:
        latest = (session.query(AssessmentReport).filter_by(user_id=user_id)
                  .order_by(desc(AssessmentReport.generated_at)).first())
        text = latest.analysis_text if latest else None
        return success(data=DashboardInsightResp(
            insight=text,
            note="" if text else "尚无报告分析文本，完成一次测评并生成报告后可用",
        ).model_dump())
    finally:
        session.close()


@router.get("/personal/risks", summary="结构化风险提醒", description="按维度得分归为高/中关注，供首页「今日风险提醒」渲染。")
async def personal_risks(user: dict = Depends(get_current_user)):
    return success(data=dashboard_service.risks(user))


@router.get("/personal/overview", summary="首页 KPI 聚合", description="健康综合指数 / 本周打卡（分母 7）/ 待关注风险数与 Top3 / 食养计划周次。")
async def personal_overview(user: dict = Depends(get_current_user)):
    return success(data=dashboard_service.overview(user))
