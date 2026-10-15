# -*- coding: utf-8 -*-
"""健康风险测评记录服务：保存测评结果、查询历史。"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.dao.health_assessment_dao import HealthAssessmentDAO
from app.exceptions.base_api_exception import ValidationException

logger = logging.getLogger(__name__)


def judge_risk_level(total_score: int) -> str:
    """按 NRS2002 总分判定风险等级"""
    if total_score <= 0:
        return "无风险"
    if total_score <= 2:
        return "低风险"
    if total_score <= 4:
        return "中风险"
    return "高风险"


def build_recommendations(total_score: int, risk_level: str) -> str:
    if total_score < 3:
        return "当前营养风险较低，建议保持均衡饮食与规律作息，定期复查营养指标。"
    if risk_level == "中风险":
        return "建议制定营养支持计划：提高优质蛋白与能量摄入，少食多餐，2-4 周后复评。"
    return "建议尽快进行营养干预，考虑肠内营养支持，并由临床营养师/医师密切监测体重与营养指标。"


class HealthAssessmentService:
    """测评记录服务"""

    @staticmethod
    def save(user: Dict, payload: Dict) -> Dict:
        """保存一条测评记录，user 为当前登录用户信息"""
        if not user:
            raise ValidationException("请先登录后再保存测评结果")

        total_score = int(payload.get("total_score", 0))
        if not (0 <= total_score <= 7):
            raise ValidationException("总分必须在 0-7 之间")

        for field in ("nutritional_impairment_score", "disease_severity_score"):
            value = int(payload.get(field, 0))
            if not (0 <= value <= 3):
                raise ValidationException(f"{field} 必须在 0-3 之间")

        age_score = int(payload.get("age_score", 0))
        if age_score not in (0, 1):
            raise ValidationException("年龄分必须为 0 或 1")

        risk_level = payload.get("risk_level") or judge_risk_level(total_score)
        if risk_level not in ("无风险", "低风险", "中风险", "高风险"):
            raise ValidationException("风险等级取值不合法")

        sex = payload.get("sex") or user.get("sex") or "其他"
        if sex not in ("男", "女", "其他"):
            sex = "其他"

        age = int(payload.get("age") or user.get("age") or 0)
        user_id = user.get("user_id")
        assessment_count = HealthAssessmentDAO.count_by_user(user_id) + 1

        data = {
            "user_id": user_id,
            "user_name": payload.get("user_name") or user.get("real_name") or user.get("username"),
            "sex": sex,
            "age": age,
            "assessment_time": datetime.now(),
            "assessment_count": assessment_count,
            "assessment_type": payload.get("assessment_type") or "NRS2002",
            "total_score": total_score,
            "nutritional_impairment_score": int(payload.get("nutritional_impairment_score", 0)),
            "disease_severity_score": int(payload.get("disease_severity_score", 0)),
            "age_score": age_score,
            "assessment_basis": payload.get("assessment_basis") or "",
            "risk_level": risk_level,
            "recommendations": payload.get("recommendations") or build_recommendations(total_score, risk_level),
            "bmi": payload.get("bmi"),
            "weight_change": payload.get("weight_change"),
            "disease_condition": payload.get("disease_condition"),
            "dietary_intake": payload.get("dietary_intake"),
            "created_by": user.get("username") or "system",
        }

        record = HealthAssessmentDAO.create(data)
        logger.info(f"测评记录已保存：user_id={user_id} 总分={total_score} 等级={risk_level}")
        return record.to_dict()

    @staticmethod
    def list_history(user: Dict, limit: int = 50) -> List[Dict]:
        if not user:
            raise ValidationException("请先登录后再查询测评历史")
        records = HealthAssessmentDAO.list_by_user(user["user_id"], limit=limit)
        return [r.to_dict() for r in records]
