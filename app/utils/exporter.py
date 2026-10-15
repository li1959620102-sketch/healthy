# -*- coding: utf-8 -*-
"""导出工具：CSV / Excel / PDF 生成。

产物统一写入 MinIO 并返回预签名 URL（与其它附件一致，避免本地磁盘依赖）。
中文字体用 reportlab 内置的 CID 字体（STSong-Light），无需额外下载字体文件。
"""
import io
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Exporter")


def to_csv_bytes(rows: List[Dict[str, Any]], headers: Optional[List[str]] = None) -> bytes:
    """UTF-8 BOM 的 CSV，Excel 直接打开不乱码。"""
    import csv

    keys = headers or (list(rows[0].keys()) if rows else [])
    if not keys:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in keys})
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx_bytes(rows: List[Dict[str, Any]], headers: Optional[List[str]] = None,
                  sheet: str = "Sheet1") -> bytes:
    from openpyxl import Workbook

    keys = headers or (list(rows[0].keys()) if rows else [])
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    if keys:
        ws.append(keys)
    for r in rows:
        ws.append([r.get(k) for k in keys])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def to_pdf_bytes(title: str, meta: Dict[str, Any],
                 sections: List[Dict[str, Any]]) -> bytes:
    """通用报告 PDF：标题 + 信息表 + 若干段落/表格 + 免责声明。

    :param sections: [{"type": "paragraph", "text": "…"} 或
                      {"type": "table", "title": "…", "headers": [...], "rows": [[...]]}]
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    font = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    except Exception as e:                       # noqa: BLE001
        logger.warning(f"中文字体注册失败，回退 Helvetica：{e}")
        font = "Helvetica"

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontName=font, fontSize=18, leading=24)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=font, fontSize=13, leading=18)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=font,
                          fontSize=10, leading=15)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=42, rightMargin=42, topMargin=42, bottomMargin=42)
    story: List[Any] = [Paragraph(title, h1), Spacer(1, 10)]

    if meta:
        rows = [[str(k), str(v if v is not None else "-")] for k, v in meta.items()]
        t = Table(rows, colWidths=[110, 380])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3dd")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f6f9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [t, Spacer(1, 14)]

    for sec in sections or []:
        if sec.get("type") == "table":
            if sec.get("title"):
                story.append(Paragraph(str(sec["title"]), h2))
                story.append(Spacer(1, 6))
            data = [sec.get("headers") or []] + [list(r) for r in (sec.get("rows") or [])]
            if data and any(data):
                t = Table(data, hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3dd")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f2ee")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]))
                story += [t, Spacer(1, 12)]
        else:
            text = str(sec.get("text") or "").strip()
            if text:
                story += [Paragraph(text.replace("\n", "<br/>"), body), Spacer(1, 8)]

    doc.build(story)
    return buf.getvalue()


def store_bytes(data: bytes, object_name: str, content_type: str = "application/octet-stream") -> str:
    """写入 MinIO（默认桶）并返回 7 天有效的预签名 URL。"""
    from datetime import timedelta

    from app.utils.minio_client import get_minio

    mc = get_minio()
    mc.upload_file(io.BytesIO(data), object_name, minio_object_name=object_name)
    return mc.get_presigned_url(object_name, timedelta(days=7))
