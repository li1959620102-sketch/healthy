# -*- coding: utf-8 -*-
"""数据分析智能体服务（受控 Text2SQL 与运营分析）。

安全红线：**绝不让 LLM 直接生成/执行 SQL**。
流程：自然语言 → 匹配**受控查询模板**（参数化）→ 注入**角色级行权限** →
只执行安全 SELECT → 数据集 → 图表建议 → 解读 → 智能报告/导出。

这样既满足"用自然语言查数据"，又保证：
  - 用户永远只能看到自己（或自己管辖）的行；
  - 不存在注入、越权、全表扫描之外的 DDL/DML 风险；
  - 结果确定性可测（不依赖模型是否在线）。
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.core.errors import BizError, ErrCode
from app.models.mysql.platform import Text2SqlQuery
from app.utils.db import get_session

logger = logging.getLogger("AnalyticsService")

# role → 行级权限说明
SCOPE_DESC = {
    "user": "仅本人数据",
    "nutritionist": "仅本人管辖客户",
    "dietitian": "仅本人管辖客户",
    "admin": "全平台（管理员）",
}

# 受控查询模板（{scope} 会被角色级过滤条件替换）
TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "level_distribution",
        "name": "测评等级分布",
        "keywords": ["等级", "分布", "测评等级", "风险分布"],
        "scope_field": "user_id",
        "sql": ("SELECT level AS level, COUNT(*) AS cnt "
                "FROM assessment_report WHERE {scope} GROUP BY level"),
        "chart": "bar",
        "desc": "按测评等级统计人数",
    },
    {
        "key": "high_risk_customers",
        "name": "高风险/待改善客户",
        "keywords": ["高风险", "待改善", "重点跟进", "风险客户"],
        "scope_field": "user_id",
        "sql": ("SELECT user_id, score, level, generated_at "
                "FROM assessment_report "
                "WHERE {scope} AND level = 'needs_improvement' "
                "ORDER BY score ASC LIMIT 50"),
        "chart": "table",
        "desc": "得分最低、需重点跟进的客户",
    },
    {
        "key": "rating_distribution",
        "name": "咨询满意度分布",
        "keywords": ["满意度", "评分", "评价", "咨询满意度"],
        "scope_field": "dietitian_id",
        "sql": ("SELECT rating AS rating, COUNT(*) AS cnt "
                "FROM consultation WHERE {scope} AND rating IS NOT NULL "
                "GROUP BY rating ORDER BY rating"),
        "chart": "bar",
        "desc": "按 1-5 分统计咨询评价",
    },
    {
        "key": "template_usage",
        "name": "测评模板使用次数",
        "keywords": ["模板", "使用次数", "模板使用", "测评模板"],
        "scope_field": "user_id",
        "sql": ("SELECT template_id, COUNT(*) AS cnt "
                "FROM assessment_record WHERE {scope} GROUP BY template_id "
                "ORDER BY cnt DESC"),
        "chart": "bar",
        "desc": "各模板被发起的次数",
    },
    {
        "key": "overview",
        "name": "平台/个人概览",
        "keywords": ["概览", "总览", "多少人", "多少条", "统计", "数量"],
        "scope_field": "user_id",
        "sql": ("SELECT COUNT(DISTINCT user_id) AS user_cnt, COUNT(*) AS record_cnt "
                "FROM assessment_record WHERE {scope}"),
        "chart": "kpi",
        "desc": "测评人数与测评次数",
    },
]

FORBIDDEN = (";", "--", "/*", "*/", "drop ", "delete ", "update ", "insert ",
             "alter ", "create ", "truncate ", "grant ", "union ")


def _scope_clause(role: str, scope_field: str, uid: str) -> str:
    """按角色生成行级过滤条件（参数化，uid 通过绑定变量传入）。"""
    if role == "admin":
        return "1=1"
    if scope_field == "dietitian_id":
        if role in ("nutritionist", "dietitian"):
            return "dietitian_id = :uid"
        return "user_id = :uid"
    # scope_field == user_id
    if role in ("nutritionist", "dietitian"):
        return ("user_id IN (SELECT customer_id FROM dietitian_customer "
                "WHERE dietitian_id = :uid)")
    return "user_id = :uid"


def _assert_safe_sql(sql: str) -> None:
    low = f" {sql.lower()} "
    if not sql.strip().lower().startswith("select"):
        raise BizError(ErrCode.PARAM_INVALID, "只允许 SELECT 查询")
    for bad in FORBIDDEN:
        if bad in low:
            raise BizError(ErrCode.PARAM_INVALID, f"查询包含不允许的关键字：{bad.strip()}")


class AnalyticsService:
    @staticmethod
    def match_template(question: str) -> Optional[Dict[str, Any]]:
        """关键词打分选模板；无命中返回 None。"""
        q = (question or "").lower()
        best, best_score = None, 0
        for t in TEMPLATES:
            score = sum(1 for k in t["keywords"] if k in q)
            if score > best_score:
                best, best_score = t, score
        return best

    @staticmethod
    def catalog() -> Dict[str, Any]:
        return {"items": [{"key": t["key"], "name": t["name"], "desc": t["desc"],
                           "chart": t["chart"]} for t in TEMPLATES],
                "total": len(TEMPLATES)}

    @staticmethod
    def query(user: dict, question: str) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        question = (question or "").strip()
        if not question:
            raise BizError(ErrCode.PARAM_INVALID, "问题不能为空")

        uid = str(user.get("sub"))
        role = str(user.get("role") or "user")
        tpl = AnalyticsService.match_template(question)

        if tpl is None:
            AnalyticsService._save(uid, question, None, "unsupported", 0,
                                   SCOPE_DESC.get(role, "受限"))
            raise BizError(ErrCode.PARAM_INVALID,
                           "暂不支持该问法，请参考 /analytics/text2sql/catalog 中支持的问题")

        scope = _scope_clause(role, tpl["scope_field"], uid)
        sql = tpl["sql"].replace("{scope}", scope)
        _assert_safe_sql(sql)

        session = get_session()
        try:
            rows = [dict(r) for r in
                    session.execute(text(sql), {"uid": uid}).mappings().all()]
        except Exception as e:                       # noqa: BLE001
            logger.warning(f"Text2SQL 执行失败：{e}")
            AnalyticsService._save(uid, question, sql, "failed", 0,
                                   SCOPE_DESC.get(role, "受限"))
            raise BizError(ErrCode.SYSTEM_ERROR, f"查询执行失败：{e}")
        finally:
            session.close()

        AnalyticsService._save(uid, question, sql, "ok", len(rows),
                               SCOPE_DESC.get(role, "受限"))
        return {
            "question": question,
            "template_key": tpl["key"],
            "template_name": tpl["name"],
            "sql": sql,
            "row_scope": SCOPE_DESC.get(role, "受限"),
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows,
            "total": len(rows),
            "chart": tpl["chart"],
            "interpretation": AnalyticsService.interpret(tpl, rows),
            "note": "" if rows else "当前权限范围内暂无数据",
        }

    @staticmethod
    def interpret(tpl: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
        """规则化业务解读（确定性，不依赖模型在线）。"""
        if not rows:
            return "所选范围内暂无数据，无法给出解读。"
        if tpl["key"] == "level_distribution":
            total = sum(int(r.get("cnt") or 0) for r in rows)
            worst = max(rows, key=lambda r: int(r.get("cnt") or 0))
            return (f"共 {total} 份测评，其中「{worst.get('level')}」占比最高"
                    f"（{worst.get('cnt')} 份）。建议对占比最高的等级人群做定向跟进。")
        if tpl["key"] == "high_risk_customers":
            return f"识别出 {len(rows)} 位待改善客户，建议结合打卡率与近期咨询记录筛选优先跟进对象。"
        if tpl["key"] == "rating_distribution":
            vals = [int(r.get("rating") or 0) for r in rows if r.get("rating")]
            cnt = sum(int(r.get("cnt") or 0) for r in rows)
            avg = round(sum(v * int(r.get("cnt") or 0) for v, r in zip(vals, rows)) / cnt, 2) if cnt else 0
            return f"共 {cnt} 条评分，平均分 {avg} 分（满分 5）。建议关注低分评价对应的服务环节。"
        if tpl["key"] == "template_usage":
            top = rows[0]
            return f"共 {len(rows)} 个模板被使用，其中模板 {top.get('template_id')} 使用最多（{top.get('cnt')} 次）。"
        if tpl["key"] == "overview":
            r = rows[0]
            return f"测评人数 {r.get('user_cnt')}，测评次数 {r.get('record_cnt')}。"
        return "已返回查询结果。"

    @staticmethod
    def history(user: dict, limit: int = 20) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        uid = str(user.get("sub"))
        session = get_session()
        try:
            rows = (session.query(Text2SqlQuery).filter_by(user_id=uid)
                    .order_by(Text2SqlQuery.id.desc()).limit(limit).all())
            return {"items": [{"id": r.id, "question": r.question,
                               "sql": r.sql_text, "status": r.status,
                               "row_count": r.row_count, "created_at": r.created_at}
                              for r in rows], "total": len(rows)}
        finally:
            session.close()

    @staticmethod
    def _save(uid: str, question: str, sql: Optional[str], status: str,
              row_count: int, scope_desc: str) -> None:
        try:
            session = get_session()
            try:
                session.add(Text2SqlQuery(user_id=uid, question=question[:500],
                                          sql_text=sql, status=status,
                                          row_count=row_count, row_scope=scope_desc))
                session.commit()
            finally:
                session.close()
        except Exception as e:                       # noqa: BLE001
            logger.warning(f"Text2SQL 历史落库失败（不影响查询）：{e}")

    @staticmethod
    def export(user: dict, question: str, fmt: str = "csv") -> Dict[str, Any]:
        """智能报告导出：先查再导出为 CSV / XLSX，入 MinIO 返回签名 URL。"""
        import time

        from app.utils.exporter import store_bytes, to_csv_bytes, to_xlsx_bytes

        fmt = (fmt or "csv").lower()
        if fmt not in ("csv", "xlsx"):
            raise BizError(ErrCode.PARAM_INVALID, "导出格式需为 csv / xlsx")
        res = AnalyticsService.query(user, question)
        rows = res["rows"]
        headers = res["columns"] or (list(rows[0].keys()) if rows else [])
        payload = (to_csv_bytes(rows, headers) if fmt == "csv"
                   else to_xlsx_bytes(rows, headers, "智能报告"))
        name = f"{str(user.get('sub'))}_{int(time.time())}.{fmt}"
        url = store_bytes(payload, f"exports/text2sql_{name}",
                          "text/csv" if fmt == "csv" else
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return {"question": question, "format": fmt, "rows": len(rows),
                "url": url, "size": len(payload), "interpretation": res["interpretation"]}


analytics_service = AnalyticsService()
