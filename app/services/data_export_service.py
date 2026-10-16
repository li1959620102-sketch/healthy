# -*- coding: utf-8 -*-
"""个人数据导出服务（IT-U-05 / 合规 D3「可携带权」）。

为什么必须二次验证：导出的是**全部个人健康数据**（病史、测评维度分、
咨询原文）。只有登录态是不够的——令牌可能泄露、被 XSS 拿到。
所以要求短信验证码，与改绑手机号同一道门。

三条硬约束：
1. 只导**本人**数据：所有查询都带 user_id 过滤，不接受前端传别人的 ID。
2. 不导密文/哈希：手机号等敏感列要么解密后按需给，要么直接不给。
3. 文件不落业务库：走 MinIO 临时签名地址，签名 10 分钟即失效。
"""
from __future__ import annotations

import io
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core.errors import BizError, ErrCode

# 导出文件的签名有效期（分钟）。与响应里的 expires_in_minutes 取同一个值，
# 避免"声明 10 分钟、实际 7 天"这种合规口径与实现不一致。
_URL_TTL_MINUTES = 10


# 导出字段清单（前端据此展示"你的数据包含什么"）
# 口径以真实 schema 为准：基础资料在 sys_user、健康档案在 user_profile、
# 咨询正文在 consultation_message（consultation 单里没有 question 列）。
_FIELDS: Dict[str, List[str]] = {
    "profile": ["user_id", "real_name", "sex", "age", "height_cm", "weight_kg",
                "diet_preference", "diseases", "allergies", "taboo"],
    "assessments": ["assessment_id", "template_id", "status", "started_at",
                    "last_saved_at", "submitted_at"],
    "reports": ["report_id", "assessment_id", "score", "standardized_score", "level",
                "dimension_scores", "analysis_text", "generated_at"],
    "consultations": ["consultation_id", "type", "status", "created_at", "question"],
}


def _rows(session, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    res = session.execute(text(sql), params).mappings().all()
    out = []
    for r in res:
        d = {}
        for k, v in dict(r).items():
            d[k] = (v.isoformat(sep=" ") if hasattr(v, "isoformat")
                    else (v if isinstance(v, (int, float, str, type(None)))
                          else _flatten(v)))
        out.append(d)
    return out


def _flatten(v: Any) -> Any:
    """JSON 列转成可读文本：直接塞 dict 进 CSV 会写出 Python 字面量。"""
    import json
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


class DataExportService:
    @staticmethod
    def fields() -> Dict[str, List[str]]:
        return dict(_FIELDS)

    @staticmethod
    def export(user: dict, body) -> Dict[str, Any]:
        from app.services.sms_service import sms_service
        from app.utils.db import get_session

        uid = str(user.get("sub"))
        fmt = (getattr(body, "fmt", "csv") or "csv").lower()
        if fmt not in ("csv", "xlsx"):
            raise BizError(ErrCode.PARAM_INVALID, "导出格式需为 csv / xlsx")
        scope = (getattr(body, "scope", "all") or "all").lower()
        if scope != "all" and scope not in _FIELDS:
            raise BizError(ErrCode.PARAM_INVALID, "导出范围需为 all/profile/reports/consultations")

        # 1) 二次验证：先校验，再动数据
        phone = _phone_of(uid)
        if not phone:
            raise BizError(ErrCode.PARAM_INVALID, "账号未绑定手机号，请先绑定后再导出")
        sms_service.verify_code(phone, (getattr(body, "verify_code", "") or "").strip())

        # 2) 按本人过滤聚合（每个查询都带 user_id，不接受前端传 ID）
        wanted = [scope] if scope != "all" else list(_FIELDS.keys())
        blocks: List[Dict[str, Any]] = []
        total = 0
        session = get_session()
        try:
            for name in wanted:
                rows = _collect(session, name, uid)
                total += len(rows)
                blocks.append({"section": name, "columns": _FIELDS[name], "rows": rows})
        finally:
            session.close()

        payload_bytes, sheet = _render(blocks, fmt)
        name = f"my_data_{uid}_{int(time.time())}.{fmt}"
        ctype = ("text/csv" if fmt == "csv" else
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        # 签名有效期必须**与返回给前端的 expires_in_minutes 一致**。
        # store_bytes 的通用默认是 7 天，用在这里等于让"10 分钟失效"变成一句空话，
        # 而导出的是全部健康数据——签名期就是数据暴露窗口，不能虚标。
        from datetime import timedelta
        from app.utils.minio_client import get_minio
        object_name = f"exports/user_{name}"
        mc = get_minio()
        mc.upload_file(io.BytesIO(payload_bytes), object_name,
                       minio_object_name=object_name)
        url = mc.get_presigned_url(object_name, timedelta(minutes=_URL_TTL_MINUTES))
        return {
            "scope": scope, "format": fmt, "rows": total,
            "sections": [{"section": b["section"], "rows": len(b["rows"])} for b in blocks],
            "url": url, "size": len(payload_bytes), "sheet": sheet,
            "expires_in_minutes": _URL_TTL_MINUTES,
        }


def _collect(session, name: str, uid: str) -> List[Dict[str, Any]]:
    """按区块聚合本人数据。列名与表结构一一对应，不做前端传参拼 SQL。"""
    if name == "profile":
        base = _rows(session,
                     "SELECT u.user_id, u.real_name, u.sex, u.age, "
                     "p.height_cm, p.weight_kg, p.diet_preference, "
                     "p.diseases_cipher, p.allergies_json, p.taboo_json "
                     "FROM sys_user u LEFT JOIN user_profile p ON p.user_id = u.user_id "
                     "WHERE u.user_id = :u AND u.deleted_at IS NULL",
                     {"u": uid})
        for r in base:
            # 疾病史是密文列：导出的是**本人明文**，这正是"可携带权"的含义。
            # 解不开就留空并标注，不能把密文/base64 甩给用户（那不是数据）。
            r["diseases"] = _decrypt_or_blank(r.pop("diseases_cipher", None))
            for key in ("allergies_json", "taboo_json"):
                r[key.replace("_json", "")] = r.pop(key, None)
        return base
    if name == "assessments":
        return _rows(session,
                     "SELECT id AS assessment_id, template_id, status, started_at, "
                     "last_saved_at, submitted_at FROM assessment_record "
                     "WHERE user_id=:u ORDER BY id",
                     {"u": uid})
    if name == "reports":
        return _rows(session,
                     "SELECT id AS report_id, record_id AS assessment_id, score, "
                     "standardized_score, level, dimension_scores, analysis_text, generated_at "
                     "FROM assessment_report WHERE user_id=:u ORDER BY id",
                     {"u": uid})
    if name == "consultations":
        # 咨询首问在消息表里（customer 发的那条），取最早一条作为 question
        return _rows(session,
                     "SELECT c.id AS consultation_id, c.type, c.status, c.created_at, "
                     "(SELECT m.content FROM consultation_message m "
                     "  WHERE m.consultation_id = c.id AND m.sender_type = 'customer' "
                     "  ORDER BY m.id LIMIT 1) AS question "
                     "FROM consultation c WHERE c.user_id=:u ORDER BY c.id",
                     {"u": uid})
    return []


def _decrypt_or_blank(blob: Any) -> str:
    """解密本人疾病史；失败返回空串而不是密文。

    返回 base64 密文对用户毫无意义，还会让人以为"我的病史丢了"。

    注意存储口径：`user_router` 写入的是 `json.dumps(diseases).encode()`，
    与 `dashboard_service._decode_diseases` 是同一套（base64(JSON) 过渡格式，
    真加密待合规模块接入）。这里必须沿用同一口径，否则导出的病史
    要么是一串 base64、要么直接空着——两个都不是"我的数据"。
    """
    if not blob:
        return ""
    try:
        import base64
        import json as _j
        text = bytes(blob) if isinstance(blob, (bytes, bytearray)) else str(blob)
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", "ignore")
        try:
            text = base64.b64decode(text).decode("utf-8")
        except Exception:
            pass
        data = _j.loads(text)
        if isinstance(data, list):
            return "、".join(str(x) for x in data)
        return str(data)
    except Exception:                                    # noqa: BLE001
        return ""


def _render(blocks: List[Dict[str, Any]], fmt: str):
    """多区块导出：CSV 竖向拼接并带区块标题；XLSX 每区块一个 sheet。"""
    import io
    from app.utils.exporter import to_xlsx_bytes
    if fmt == "csv":
        parts = []
        for b in blocks:
            parts.append(f"# {b['section']}")
            parts.append(",".join(b["columns"]))
            for r in b["rows"]:
                parts.append(",".join(_csv_cell(r.get(c)) for c in b["columns"]))
            parts.append("")
        return ("\n".join(parts).encode("utf-8-sig")), "my_data"
    # xlsx：逐区块建 sheet
    sheets = []
    for b in blocks:
        buf = to_xlsx_bytes(b["rows"], b["columns"], b["section"][:31])
        sheets.append((b["section"][:31], buf))
    return _merge_xlsx(sheets), "多工作表"


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    if any(ch in s for ch in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _merge_xlsx(sheets) -> bytes:
    """把多个单表 xlsx 合成一个多 sheet 工作簿。"""
    from openpyxl import Workbook, load_workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, blob in sheets:
        src = load_workbook(io.BytesIO(blob))
        src_ws = src.active
        ws = wb.create_sheet(title)
        for row in src_ws.iter_rows(values_only=True):
            ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _phone_of(uid: str) -> Optional[str]:
    """取绑定手机号明文：唯一来源是 sys_user.phone_cipher 解密。

    没有这一步就无法做短信二次验证；解不开就返回 None 让用户先绑定，
    不能"跳过验证"——那等于把二次验证做成装饰。
    """
    from app.utils.db import get_session
    session = get_session()
    try:
        # 手机号只落在 sys_user.phone_cipher（AES-256-GCM）。
        # 两处曾经的错：① 查 user_profile.phone —— 该列根本不存在，一执行就 1054；
        # ② from app.utils.crypto import decrypt_value —— 没有这个模块，
        #    ImportError 被 except 吞掉后返回 None，于是**所有人**都收到
        #    "账号未绑定手机号"。真实加解密在 app.utils.security。
        from app.models.mysql.user import SysUser
        from app.utils.security import decrypt_phone
        u = session.query(SysUser).filter_by(user_id=uid).first()
        if not u:
            return None
        return decrypt_phone(u.phone_cipher)
    finally:
        session.close()


data_export_service = DataExportService()