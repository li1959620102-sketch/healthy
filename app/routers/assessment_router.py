# -*- coding: utf-8 -*-
"""E 组 · 测评路由（依据设计_01 §二 E / §3.1-3.3 / §3.6）。

关键约定：
- 发起时锁定模板版本与画像快照（C2 / C6）。
- 提交走 `Idempotency-Key` 头做幂等；规则引擎同步算分，分析文本由 SSE 通道渐进返回（A1）。
- 报告流式通道 `GET /assessments/{id}/report/stream` 补齐设计评审 B3 缺口，
  鉴权沿用短票 `?ticket=`（评审 B1 方案）。
- 游客试测：Authorization 携带游客令牌即可，游客令牌哈希入库用于注册后认领（M3）。
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user, get_optional_user, resolve_sse_user
from app.core.errors import BizError, ErrCode
from app.core.responses import success
from app.schemas.platform import ClaimGuestReq, SaveAnswersReq, StartAssessmentReq
from app.services.assessment_service import AssessmentService, guest_hash_of
from app.services.report_service import report_service

router = APIRouter(prefix="/assessments", tags=["测评"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _guest_hash(request: Request, user: Optional[dict]) -> Optional[str]:
    """游客身份哈希：仅当请求方为游客时，从 Authorization 原串取哈希做归属。"""
    if user and user.get("role") not in (None, "guest"):
        return None
    auth = request.headers.get("Authorization") or ""
    token = auth.split(" ")[-1].strip()
    return guest_hash_of(token) if token else None


@router.post("", summary="发起测评", description="支持游客试测；返回锁定的模板快照与画像快照。")
async def start_assessment(body: StartAssessmentReq, request: Request,
                           user: Optional[dict] = Depends(get_optional_user)):
    data = AssessmentService.start_assessment(
        user, body.template_id, body.template_version,
        guest_hash=_guest_hash(request, user),
    )
    return success(data=data)


@router.get("", summary="我的测评列表", description="按状态筛选，附带报告评分摘要。")
async def list_mine(status: Optional[str] = Query(None, description="in_progress/completed/void"),
                    user: dict = Depends(get_current_user)):
    return success(data=AssessmentService.list_mine(user, status=status))


@router.post("/guest/claim", summary="游客数据认领", description="注册后认领 7 天内的游客测评数据（评审 M3）。")
async def claim_guest(body: ClaimGuestReq, user: dict = Depends(get_current_user)):
    return success(data=AssessmentService.claim_guest(user, body.guest_token))


@router.get("/{record_id}", summary="测评详情", description="返回题目、已答草稿与进度。")
async def get_assessment(record_id: int, request: Request,
                         user: Optional[dict] = Depends(get_optional_user)):
    return success(data=AssessmentService.get_detail(
        record_id, user, guest_hash=_guest_hash(request, user)))


@router.patch("/{record_id}/answers", summary="保存草稿", description="每答完一页调用一次，实时保存；异常不丢答案（A8）。")
async def save_answers(record_id: int, body: SaveAnswersReq, request: Request,
                       user: Optional[dict] = Depends(get_optional_user)):
    return success(data=AssessmentService.save_draft(
        record_id, user, _guest_hash(request, user),
        body.answers, body.current_page))


@router.post("/{record_id}/submit", summary="提交测评", description="请求头 Idempotency-Key 必填；规则引擎同步算分并返回评分，分析文本经 SSE 返回。")
async def submit_assessment(record_id: int, request: Request,
                            idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                            user: Optional[dict] = Depends(get_optional_user)):
    return success(data=AssessmentService.submit(
        record_id, user, _guest_hash(request, user), idempotency_key))


@router.post("/{record_id}/cancel", summary="作废测评", description="进行中的测评可作废；已提交的不可作废。")
async def cancel_assessment(record_id: int, request: Request,
                            user: Optional[dict] = Depends(get_optional_user)):
    return success(data=AssessmentService.cancel(
        record_id, user, guest_hash=_guest_hash(request, user)))


@router.post("/{record_id}/report/regenerate", summary="重新生成报告", description="基础版报告可重新生成；评分由规则引擎确定性计算，不受影响。")
async def regenerate_report(record_id: int, request: Request,
                            user: Optional[dict] = Depends(get_optional_user)):
    return success(data=AssessmentService.regenerate_report(
        record_id, user, guest_hash=_guest_hash(request, user)))


@router.get("/{record_id}/report/stream", summary="报告生成流式通道（SSE）",
            description="EventSource 用 ?ticket= 短票鉴权；事件 start/reference/delta/done。"
                        "生成失败时 done 事件带 state=basic + degraded=true（评审 B2：不用错误码）。")
async def report_stream(record_id: int, request: Request):
    user = resolve_sse_user(request)
    if not user:
        raise BizError(ErrCode.UNAUTHENTICATED, "报告流需要有效的短票")
    # 流式生成前先做记录归属校验，禁止通过枚举 record_id 读取他人健康报告。
    AssessmentService.get_detail(record_id, user, guest_hash=None)

    async def event_gen():
        try:
            async for ev in report_service.stream(record_id):
                yield _sse(ev["event"], ev["data"])
        except BizError as e:
            yield _sse("error", {"code": e.code, "message": e.message})
        except Exception as e:  # 兜底：不让流式通道抛出未捕获异常
            yield _sse("error", {"code": ErrCode.SYSTEM_ERROR, "message": f"报告生成异常：{e}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
