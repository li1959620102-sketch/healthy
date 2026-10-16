# -*- coding: utf-8 -*-
"""I 组 · 数据看板路由（骨架，依据设计_01 §二 I）。

第一批为骨架：接口形态与结构已定，数据来源于 E/F 组（测评/报告，第二批）。
有数据时直接查库返回；无数据时返回空结构 + note，不报错。
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc

from app.schemas.platform import (
    TrendPoint, DashboardTrendResp, DashboardDimensionsResp, DashboardInsightResp,
    DashboardAttributionResp,
)
from app.core.responses import success
from app.core.errors import BizError, ErrCode
from app.core.deps import get_current_user
from app.services.dashboard_service import dashboard_service
from app.utils.db import get_session
from app.models.mysql.platform import AssessmentReport, AssessmentTemplate

router = APIRouter(prefix="/dashboard", tags=["数据看板"])


def _parse_range(start_date: Optional[str], end_date: Optional[str]):
    """解析看板日期区间（BV-20）。

    约定：日期格式 YYYY-MM-DD；**起 > 止 或格式非法一律明确报错**，
    不把非法区间透传到 SQL（否则不同数据库行为不一致，容易出现 500 或空结果两可）。
    结束日期按"含当日"处理（补到当天 23:59:59.999999）。
    """
    def _p(value: Optional[str], label: str):
        if value in (None, ""):
            return None
        try:
            return datetime.strptime(str(value).strip(), "%Y-%m-%d")
        except ValueError:
            raise BizError(ErrCode.PARAM_INVALID, f"{label}格式不正确，应为 YYYY-MM-DD")

    lo = _p(start_date, "起始日期")
    hi = _p(end_date, "结束日期")
    if lo and hi and lo > hi:
        raise BizError(ErrCode.PARAM_INVALID, "起始日期不能晚于结束日期")
    if hi:
        hi = hi + timedelta(days=1) - timedelta(microseconds=1)
    return lo, hi


def _template_names(session, ids) -> dict:
    """按模板 ID 批量取名称，避免趋势循环里逐条查库（N+1）。"""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = session.query(AssessmentTemplate.id, AssessmentTemplate.name).filter(
        AssessmentTemplate.id.in_(ids)).all()
    return {r.id: r.name for r in rows}


def _trend_table(points: List[TrendPoint]) -> dict:
    """趋势的等价数据表（IT-D-07）。

    折线图对读屏软件不可读，这张表是同一份数据的无障碍替代，不是附属品。
    """
    return {
        "columns": [
            {"key": "date", "name": "测评日期"},
            {"key": "template_name", "name": "模板"},
            {"key": "template_version", "name": "模板版本"},
            {"key": "score", "name": "总分"},
            {"key": "standardized_score", "name": "标准化百分制"},
            {"key": "level", "name": "等级"},
        ],
        "rows": [p.model_dump() for p in points],
    }


def _trend_summary(points: List[TrendPoint]) -> str:
    """文字版结论（IT-D-07）：把折线要说的事用一句话讲清，供读屏与图表未渲染时使用。"""
    if not points:
        return "暂无测评记录，完成一次测评后这里会显示得分变化。"
    scores = [p.standardized_score for p in points if p.standardized_score is not None]
    if len(scores) < 2:
        last = points[-1]
        return (f"共 {len(points)} 次测评，最近一次 {last.date}，"
                f"{last.template_name or ''} 百分制 {last.standardized_score if last.standardized_score is not None else '—'} 分。")
    delta = scores[-1] - scores[0]
    word = "上升" if delta > 0 else ("下降" if delta < 0 else "持平")
    return (f"共 {len(points)} 次测评，标准化百分制从 {scores[0]:.0f} 分变为 {scores[-1]:.0f} 分，"
            f"整体{word} {abs(delta):.0f} 分。")


@router.get("/personal/trend", summary="测评得分趋势", description="按时间返回得分趋势；数据来源于测评报告。可按日期区间筛选（起>止 明确报错，不 500）。每个点自带模板名/版本/标准化百分制（跨模板可比性，IT-D-02），并附等价数据表与文字结论（IT-D-07）。")
async def personal_trend(
    template_id: int = Query(None, description="模板 ID（默认同类型最新）"),
    start_date: str = Query(None, description="起始日期 YYYY-MM-DD（含）"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD（含）"),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    lo, hi = _parse_range(start_date, end_date)
    points: List[TrendPoint] = []
    session = get_session()
    try:
        q = session.query(AssessmentReport).filter_by(user_id=user_id)
        if template_id:
            q = q.filter_by(template_id=template_id)
        if lo:
            q = q.filter(AssessmentReport.generated_at >= lo)
        if hi:
            q = q.filter(AssessmentReport.generated_at <= hi)
        rows = q.order_by(AssessmentReport.generated_at).all()
        # 模板名一次性查出来映射（报告表只冗余了 template_id/version，没存名字）
        names = _template_names(session, {r.template_id for r in rows})
        for r in rows:
            points.append(TrendPoint(
                date=str(r.generated_at)[:10] if r.generated_at else "",
                score=float(r.score) if r.score is not None else None,
                level=r.level,
                template_id=r.template_id,
                template_name=names.get(r.template_id) or f"模板{r.template_id}",
                template_version=r.template_version,
                standardized_score=(float(r.standardized_score)
                                   if r.standardized_score is not None else None),
            ))
        payload = DashboardTrendResp(
            template_id=template_id, points=points,
            note="" if points else "测评数据来源于 E/F 组（第二批），当前为空",
        ).model_dump()
        payload["table"] = _trend_table(points)
        payload["summary"] = _trend_summary(points)
        return success(data=payload)
    finally:
        session.close()


@router.get("/personal/dimensions", summary="各维度得分对比", description="最新报告的维度得分，附等价数据表与文字结论（IT-D-07）。")
async def personal_dimensions(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    session = get_session()
    try:
        latest = session.query(AssessmentReport).filter_by(user_id=user_id).order_by(
            desc(AssessmentReport.generated_at)).first()
        dims = latest.dimension_scores if latest and latest.dimension_scores else []
        payload = DashboardDimensionsResp(
            dimensions=dims,
            note="" if dims else "维度得分来源于 assessment_report（第二批）",
        ).model_dump()
        payload["table"] = {
            "columns": [{"key": "name", "name": "维度"},
                        {"key": "score", "name": "得分"},
                        {"key": "full_score", "name": "满分"}],
            "rows": dims,
        }
        payload["summary"] = _dims_summary(dims)
        return success(data=payload)
    finally:
        session.close()


def _dims_summary(dims: List[dict]) -> str:
    """维度对比的文字版结论：点名最弱维度与分值，不让用户去读图。"""
    if not dims:
        return "暂无维度得分。"
    scored = [(d.get("name") or "未命名维度", float(d.get("score") or 0)) for d in dims]
    low = min(scored, key=lambda x: x[1])
    if len(scored) == 1:
        return f"共 1 个维度：{low[0]} {low[1]:.0f} 分。"
    return (f"共 {len(scored)} 个维度，得分最低的是「{low[0]}」{low[1]:.0f} 分，"
            f"最高的是「{max(scored, key=lambda x: x[1])[0]}」{max(x[1] for x in scored):.0f} 分。")


_LEVEL_TEXT = {"excellent": "优秀", "good": "良好",
               "needs_improvement": "待改善"}


@router.get("/personal/attribution", summary="两次测评归因",
            description="对比两份报告的维度得分，归因结论 = **分差最大维度**（规则计算，"
                        "不经模型；IT-D-05 / A7-3）。base_id 与 target_id 必须是本人报告。")
async def personal_attribution(
    base_id: int = Query(..., description="基线报告 ID（较早）"),
    target_id: int = Query(..., description="目标报告 ID（较新）"),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    session = get_session()
    try:
        rows = session.query(AssessmentReport).filter(
            AssessmentReport.id.in_([base_id, target_id]),
            AssessmentReport.user_id == user_id,          # 越权与不存在同码，防枚举
        ).all()
        if len(rows) != 2:
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "报告不存在")
        base = next((r for r in rows if r.id == base_id), None)
        target = next((r for r in rows if r.id == target_id), None)

        def _dmap(r):
            return {(d.get("name") or "未命名维度"): float(d.get("score") or 0)
                    for d in (r.dimension_scores or [])}

        b, t = _dmap(base), _dmap(target)
        shared = sorted(set(b) & set(t))
        if not shared:
            return success(data=DashboardAttributionResp(
                base_id=base_id, target_id=target_id,
                summary="两次测评的维度不可比（模板维度不一致），无法归因。").model_dump())

        # 归因只看**变化幅度**，不预设"分数越高越好"：某些维度分高代表风险高
        deltas = [{"name": n, "base": b[n], "target": t[n], "delta": round(t[n] - b[n], 2)}
                  for n in shared]
        # 先按分差绝对值降序、再按维度名升序：并列时结果稳定可复现，
        # 不依赖数据库/字典的偶然顺序（否则同一份数据两次归因可能给出不同维度）
        deltas.sort(key=lambda d: (-abs(d["delta"]), d["name"]))
        top = deltas[0]
        tied = [d["name"] for d in deltas if abs(abs(d["delta"]) - abs(top["delta"])) < 1e-9]
        direction = "上升" if top["delta"] > 0 else ("下降" if top["delta"] < 0 else "持平")
        if len(tied) > 1:
            # 并列时不做"随便挑一个"，如实告知并列并列出全部
            summary = (f"变化幅度并列最大的维度是「{'』『'.join(tied)}」，"
                       f"均从基准分变为 {top['target']:.0f} 分（{direction} {abs(top['delta']):.0f} 分）；"
                       f"该结论按维度分差规则计算，不含模型推断。")
        else:
            summary = (f"变化最大的维度是「{top['name']}」，从 {top['base']:.0f} 分变为 "
                       f"{top['target']:.0f} 分（{direction} {abs(top['delta']):.0f} 分）。"
                       f"该结论按维度分差规则计算，不含模型推断。")
        return success(data=DashboardAttributionResp(
            base_id=base_id, target_id=target_id, top_dimension=top["name"],
            deltas=deltas, tied_dimensions=tied, summary=summary).model_dump())
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
