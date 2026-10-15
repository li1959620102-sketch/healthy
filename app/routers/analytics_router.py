# -*- coding: utf-8 -*-
"""数据分析智能体路由与智能体运行监控。

Text2SQL：自然语言 → 受控模板 → 角色级行权限 → 安全 SELECT → 数据集/图表/解读/导出。
运行监控：按智能体目录汇总启用与待接入状态（规则计算，不依赖外部探针）。
"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.responses import success
from app.services.analytics_service import analytics_service
from app.services.plan_service import DEFAULT_AGENTS, PlanService

router = APIRouter(prefix="/analytics", tags=["数据分析智能体"])


@router.get("/text2sql/catalog", summary="支持的问法清单", description="列出受控查询模板；不支持的问法会明确拒绝，不擅自拼 SQL。")
async def catalog(user: dict = Depends(get_current_user)):
    return success(data=analytics_service.catalog())


@router.post("/text2sql/query", summary="执行自然语言查询", description="匹配模板 → 注入行级权限 → 只跑安全 SELECT；返回 SQL、数据集、图表建议与解读。")
async def run_query(question: str = Query(..., description="自然语言问题"),
                    user: dict = Depends(get_current_user)):
    return success(data=analytics_service.query(user, question))


@router.get("/text2sql/history", summary="历史提问", description="本人最近的自然语言查询记录（含最终 SQL 与权限范围，便于审计）。")
async def history(limit: int = Query(20, description="返回条数"),
                  user: dict = Depends(get_current_user)):
    return success(data=analytics_service.history(user, limit))


@router.get("/text2sql/export", summary="导出智能报告", description="先按权限查询，再导出 CSV / XLSX 入 MinIO 并返回签名 URL。")
async def export(question: str = Query(..., description="自然语言问题"),
                 fmt: str = Query("csv", description="csv / xlsx"),
                 user: dict = Depends(get_current_user)):
    return success(data=analytics_service.export(user, question, fmt), message="智能报告已生成")


@router.get("/agents/monitor", summary="智能体运行监控", description="按智能体目录汇总：运行中 / 待接入第三方 / 总数（原型 agents 页「智能体运行监控」）。")
async def monitor(user: dict = Depends(get_current_user)):
    agents = PlanService.agents().get("items") or DEFAULT_AGENTS
    running = [a for a in agents if a.get("enabled")]
    pending = [a for a in agents if not a.get("enabled")]
    return success(data={
        "items": [{"key": a.get("key"), "name": a.get("name"),
                   "status": "running" if a.get("enabled") else "pending",
                   "note": a.get("note") or ("运行中" if a.get("enabled")
                                             else "能力待接入")}
                  for a in agents],
        "total": len(agents),
        "running": len(running),
        "pending": len(pending),
        "checked_at": None,
    })
