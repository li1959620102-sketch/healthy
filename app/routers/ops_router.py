# -*- coding: utf-8 -*-
"""运维可观测性路由（监控 + 定时任务 + 预警）。

- GET  /ops/metrics            运行指标快照（管理员 / 运营可读）
- POST /ops/metrics/reset      清空指标计数（管理员；压测前对齐基线用）
- GET  /ops/scheduler          定时任务状态（最近一次执行结果 / 下次执行时间）
- POST /ops/scheduler/{job}/run 立即执行一次（管理员补偿用）
- GET  /ops/alerts             预警记录
- POST /ops/alerts/{id}/ack    确认预警

安全：指标、任务状态与预警都属运营数据，仅管理员/运营角色可读，不对外开放。

为什么任务状态要开成接口而不是"看日志"：
定时清理和预警求值是**合规与运营承诺**，必须能回答"上周三那次到底跑没跑、
成功没有、删了多少"。日志会被轮转、会被容器回收，接口不会。
"""
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from app.core.deps import require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.core.errors import BizError, ErrCode
from app.core.metrics import metrics
from app.core.responses import success
from app.core.scheduler import JOBS, scheduler as app_scheduler

router = APIRouter(prefix="/ops", tags=["运维监控"])


@router.get("/metrics", summary="运行指标快照（调用量 / 错误率 / 延迟分位 / 依赖）", tags=["运维监控"])
def get_metrics(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    """返回进程内指标快照，供压测观测与运营大屏展示。"""
    return success(data=metrics.snapshot())


@router.post("/metrics/reset", summary="清空运行指标计数", tags=["运维监控"])
def reset_metrics(_: dict = Depends(require_role(ROLE_ADMIN))):
    """清空计数并重置计时起点，便于压测前后对比。"""
    metrics.reset()
    return success(data={"reset": True})


@router.get("/scheduler", summary="定时任务状态与最近执行结果", tags=["运维监控"])
def scheduler_status(_: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    """返回调度器是否在跑、每个任务的最近一次结果与下次执行时间。

    `last.result` 是任务自己返回的可读摘要（扫到几个、成功几个、删了多少行），
    运维不需要登机器翻日志就能判断"这一轮有没有真的干活"。
    """
    return success(data=app_scheduler.status())


@router.post("/scheduler/{job}/run", summary="立即执行一次定时任务", tags=["运维监控"])
async def run_scheduler_job(job: str = Path(..., description="任务 ID，见 GET /ops/scheduler"),
                            _: dict = Depends(require_role(ROLE_ADMIN))):
    """补偿入口：漏跑、或者想立刻验证阈值配置是否生效时用。

    任务体是同步的（DB 驱动同步），必须 `to_thread` 包一层——
    直接在事件循环里跑会把整个服务的请求全部堵住。
    """
    import asyncio

    if job not in JOBS:
        raise BizError(ErrCode.PARAM_INVALID,
                       f"未知任务：{job}；可选 {', '.join(JOBS)}")
    result = await asyncio.to_thread(app_scheduler.run_now, job)
    return success(data={"job": job, "result": result})


@router.get("/alerts", summary="预警记录列表", tags=["运维监控"])
def list_alerts(state: str = "", limit: int = 50,
                _: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    """按状态查预警（open / ack / closed），按时间倒序。"""
    from app.services.alert_service import list_alerts as _list

    return success(data=_list(state=state or None, limit=limit))


class AckReq(BaseModel):
    note: str = ""


@router.post("/alerts/{alert_id}/ack", summary="确认预警", tags=["运维监控"])
def ack_alert(alert_id: int, body: AckReq = AckReq(),
              operator: dict = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))):
    """确认一条预警。只改状态、不删记录：留痕比列表干净重要。"""
    from app.services.alert_service import ack_alert as _ack

    return success(data=_ack(alert_id, operator))
