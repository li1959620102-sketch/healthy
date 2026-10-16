# -*- coding: utf-8 -*-
"""运维可观测性路由（监控）。

- GET  /ops/metrics       运行指标快照（管理员 / 运营可读）
- POST /ops/metrics/reset 清空指标计数（管理员；压测前对齐基线用）

安全：指标含调用量与错误分布，属运营数据，仅管理员/运营角色可读，不对外开放。
"""
from fastapi import APIRouter, Depends

from app.core.deps import require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.core.metrics import metrics
from app.core.responses import success

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
