# -*- coding: utf-8 -*-
"""F 组 · 报告路由（依据设计_01 §二 F / §3.4 / §3.7）。

降级语义（设计评审 B2）：详细分析生成失败时返回 **code=0 + state=basic**，
评分与等级照常返回，前端展示"重新生成"按钮；不返回 50002，避免误弹错误提示。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_DIETITIAN
from app.core.errors import BizError, ErrCode
from app.core.responses import success
from app.schemas.platform import AnnotationReq
from app.services.assessment_service import guest_hash_of
from app.services.report_service import report_service

router = APIRouter(prefix="/reports", tags=["报告"])


@router.get("/{report_id}", summary="报告详情", description="结构化评分 + 分析文本 + 引用 + 营养师批注；本人/绑定营养师/管理员可见。")
async def get_report(report_id: int, request: Request,
                     user: Optional[dict] = Depends(get_current_user)):
    auth = request.headers.get("Authorization") or ""
    token = auth.split(" ")[-1].strip()
    data = report_service.detail(report_id, user, guest_hash=guest_hash_of(token))
    return success(data=data)


@router.put("/{report_id}/annotation", summary="营养师批注", description="仅可批注已绑定客户的报告；与 AI 原文并存形成双版本。")
async def annotate(report_id: int, body: AnnotationReq,
                   user: dict = Depends(require_role(ROLE_DIETITIAN, ROLE_ADMIN))):
    return success(data=report_service.annotate(report_id, str(user.get("sub")), body.content))


@router.get("/{report_id}/pdf", summary="导出 PDF", description="渲染报告为 PDF（评分 + 维度 + 建议 + 免责声明），入 MinIO 并返回 7 天有效的签名 URL。")
async def export_pdf(report_id: int, user: dict = Depends(get_current_user)):
    import time

    from app.utils.exporter import store_bytes, to_pdf_bytes

    data = report_service.detail(report_id, user)
    sections: list = []
    if data.get("analysis_text"):
        sections.append({"type": "paragraph", "text": data["analysis_text"]})

    dims = data.get("dimension_scores") or []
    if dims:
        sections.append({"type": "table", "title": "维度得分",
                         "headers": ["维度", "得分", "等级"],
                         "rows": [[d.get("name") or d.get("key"), d.get("score"),
                                   d.get("level_name")] for d in dims]})

    sugg = data.get("suggestions") or []
    if sugg:
        sections.append({"type": "table", "title": "改善建议",
                         "headers": ["项目", "说明"],
                         "rows": [[s.get("item"), str(s.get("note") or "")[:120]]
                                  for s in sugg]})
    sections.append({"type": "paragraph", "text": data.get("disclaimer")
                     or "本内容由 AI 生成，不构成诊疗建议"})

    meta = {"报告编号": report_id, "综合得分": data.get("score"),
            "等级": data.get("level_name") or data.get("level"),
            "生成时间": data.get("generated_at"),
            "评分版本": data.get("scoring_version")}
    try:
        pdf = to_pdf_bytes(f"AI 智参 · 健康测评报告 #{report_id}", meta, sections)
        url = store_bytes(pdf, f"reports/{report_id}_{int(time.time())}.pdf",
                          "application/pdf")
    except Exception as e:                       # noqa: BLE001
        raise BizError(ErrCode.SYSTEM_ERROR, f"PDF 生成失败：{e}")
    return success(data={"report_id": report_id, "url": url, "size": len(pdf)},
                   message="PDF 已生成")
