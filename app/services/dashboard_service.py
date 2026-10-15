# -*- coding: utf-8 -*-
"""数据看板聚合服务（原型：首页 KPI 与「今日风险提醒」/ 营养师对话右栏「当前健康上下文」）。

把散落在报告、打卡、食养计划、健康档案里的数据聚合成前端可直接渲染的结构，
避免前端为拼一个页面调 4~5 个接口。
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import desc

from app.dao.plan_dao import PlanDAO
from app.models.mysql.platform import AssessmentReport, UserProfile
from app.utils.db import get_session

logger = logging.getLogger("DashboardService")

RISK_HIGH = 60      # 维度得分 < 60 → 高关注
RISK_MID = 80       # 60 ~ 79 → 中关注；>= 80 → 良好


def _level_of(score: Optional[float]):
    if score is None:
        return "unknown", "待评估"
    if score < RISK_HIGH:
        return "high", "高关注"
    if score < RISK_MID:
        return "mid", "中关注"
    return "low", "良好"


def _bmi(height_cm, weight_kg):
    """BMI（保留 1 位）；缺身高或体重时返回 None，绝不编造。"""
    try:
        h = float(height_cm) / 100.0
        w = float(weight_kg)
        if h <= 0 or w <= 0:
            return None
        return round(w / (h * h), 1)
    except (TypeError, ValueError):
        return None


def _decode_diseases(raw):
    """疾病史解密（当前以 base64(JSON) 存储，加密待合规模块接入）→ list[str]。"""
    if not raw:
        return []
    try:
        import base64
        import json as _j
        text = raw
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", "ignore")
        if isinstance(text, str):
            try:
                text = base64.b64decode(text).decode("utf-8")
            except Exception:
                pass
        data = _j.loads(text)
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []



class DashboardService:
    @staticmethod
    def risks(user: dict) -> Dict[str, Any]:
        """结构化风险提醒（原型首页「今日风险提醒」：高/中/低关注）。"""
        uid = str(user.get("sub"))
        session = get_session()
        try:
            latest = (session.query(AssessmentReport).filter_by(user_id=uid)
                      .order_by(desc(AssessmentReport.generated_at)).first())
            if not latest:
                return {"items": [], "total": 0,
                        "note": "暂无测评报告，完成一次测评后生成风险提醒"}
            items = []
            for d in (latest.dimension_scores or []):
                lvl, label = _level_of(d.get("score"))
                if lvl in ("low", "unknown"):
                    continue
                items.append({
                    "dimension_key": d.get("key"),
                    "name": d.get("name") or d.get("key"),
                    "score": d.get("score"),
                    "level": lvl,
                    "level_name": label,
                    "desc": f"该维度得分 {d.get('score')}，建议优先改善",
                })
            items.sort(key=lambda x: x["score"] if x["score"] is not None else 0)
            return {"items": items, "total": len(items),
                    "report_id": latest.id, "generated_at": latest.generated_at}
        finally:
            session.close()

    @staticmethod
    def overview(user: dict) -> Dict[str, Any]:
        """首页 KPI 聚合：健康综合指数 / 本周打卡 / 待关注风险 / 食养计划周次。"""
        from app.services.checkin_service import checkin_service

        uid = str(user.get("sub"))
        stats = checkin_service.stats(user)
        risks = DashboardService.risks(user)

        session = get_session()
        try:
            latest = (session.query(AssessmentReport).filter_by(user_id=uid)
                      .order_by(desc(AssessmentReport.generated_at)).first())
            index = round(float(latest.score)) if latest and latest.score is not None else None
            level = latest.level if latest else None
            plans = PlanDAO.list_by_user(uid, limit=1, session=session)
            plan = plans[0] if plans else None
        finally:
            session.close()

        plan_info = None
        if plan:
            base = plan.generated_at or datetime.now()
            plan_info = {
                "plan_id": plan.id,
                "title": plan.title,
                "state": plan.state,
                "week_no": max(1, (datetime.now() - base).days // 7 + 1),
                "generated_at": plan.generated_at,
            }

        return {
            "health_index": index,
            "level": level,
            "risk_count": risks["total"],
            "top_risks": [r["name"] for r in risks["items"][:3]],
            "checkin": {
                "week_checked": stats["week_checked"],
                "week_total": stats["week_total"],
                "week_rate": stats["week_rate"],
                "checked_today": stats["checked_today"],
                "streak": stats["streak"],
            },
            "checkin_text": f"{stats['week_checked']} / {stats['week_total']}",
            "plan": plan_info,
        }

    @staticmethod
    def health_context(user: dict) -> Dict[str, Any]:
        """营养师对话右栏「当前健康上下文」：体质 / 重点风险 / 忌口 / AI 建议方向。"""
        uid = str(user.get("sub"))
        session = get_session()
        try:
            prof = session.query(UserProfile).filter_by(user_id=uid).first()
            plans = PlanDAO.list_by_user(uid, limit=1, session=session)
            plan = plans[0] if plans else None
        finally:
            session.close()

        risks = DashboardService.risks(user)
        key_risks = [r["name"] for r in risks["items"][:2]]
        suggestions = [f"重点改善：{n}" for n in key_risks]
        suggestions.append("增加蔬菜与全谷物摄入，外卖优先少酱少盐")
        if prof and prof.diet_preference:
            suggestions.append(f"按饮食偏好调整：{prof.diet_preference}")
        suggestions.append("如涉及诊断、用药问题，转执业医师")

        # 过敏/忌口：优先取测评档案题写入的结构化清单，其次退回自由文本偏好
        import json as _json

        def _labels(raw):
            if not raw:
                return None
            try:
                labels = (_json.loads(raw) or {}).get("labels") or []
                return "、".join(labels) or None
            except Exception:
                return None

        allergy_text = _labels(getattr(prof, "allergies_json", None)) if prof else None
        taboo_text = _labels(getattr(prof, "taboo_json", None)) if prof else None
        combined = "；".join(x for x in (
            f"过敏：{allergy_text}" if allergy_text else None,
            f"忌口：{taboo_text}" if taboo_text else None) if x)

        return {
            "constitution": plan.constitution if plan else None,
            "key_risks": key_risks,
            "allergies": combined or (prof.diet_preference if prof else None),
            "allergy_labels": allergy_text,
            "taboo_labels": taboo_text,
            "diet_preference": prof.diet_preference if prof else None,
            "height_cm": prof.height_cm if prof else None,
            "weight_kg": (float(prof.weight_kg) if prof and prof.weight_kg is not None else None),
            "bmi": _bmi(prof.height_cm if prof else None,
                        float(prof.weight_kg) if prof and prof.weight_kg is not None else None),
            "diseases": _decode_diseases(getattr(prof, "diseases_cipher", None)),
            "suggestions": suggestions,
            "disclaimer": "本内容由 AI 生成，不构成诊疗建议",
            "note": "体质结论取自最近一次食养方案；舌象 AI 辨识待 S4 验证后启用",
        }


dashboard_service = DashboardService()
