# -*- coding: utf-8 -*-
"""用户健康风险测评记录表（映射已存在的 user_health_risk_assessment）。"""
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.mysql import BIGINT

from app.models.mysql.knowledge_file import Base


class UserHealthRiskAssessment(Base):
    """用户健康风险测评记录

    与 user_health_risk_assessment.sql 中的表结构保持一致，
    该表已存在时 SQLAlchemy 只做映射，不会重建。
    """

    __tablename__ = "user_health_risk_assessment"

    id = Column(BIGINT, primary_key=True, autoincrement=True, comment="记录ID")
    user_id = Column(String(64), nullable=False, comment="用户唯一标识")
    user_name = Column(String(100), nullable=False, comment="用户姓名")
    sex = Column(Enum("男", "女", "其他"), nullable=False, comment="性别")
    age = Column(Integer, nullable=False, comment="年龄")
    assessment_time = Column(DateTime, nullable=False, comment="测评时间")
    assessment_count = Column(Integer, default=1, comment="第几次测评")
    assessment_type = Column(String(50), default="NRS2002", comment="测评类型")
    total_score = Column(Integer, nullable=False, comment="总分 0-7")
    nutritional_impairment_score = Column(Integer, nullable=False, comment="营养受损分 0-3")
    disease_severity_score = Column(Integer, nullable=False, comment="疾病严重度分 0-3")
    age_score = Column(Integer, nullable=False, comment="年龄分 0-1")
    assessment_basis = Column(Text, comment="评分依据说明")
    risk_level = Column(
        Enum("无风险", "低风险", "中风险", "高风险"), nullable=False, comment="风险等级"
    )
    recommendations = Column(Text, comment="健康建议")
    bmi = Column(Numeric(4, 2), comment="BMI指数")
    weight_change = Column(String(100), comment="体重变化情况")
    disease_condition = Column(Text, comment="疾病状况描述")
    dietary_intake = Column(String(200), comment="进食情况")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
    created_by = Column(String(100), default="system", comment="创建人")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "sex": self.sex,
            "age": self.age,
            "assessment_time": str(self.assessment_time) if self.assessment_time else "",
            "assessment_count": self.assessment_count,
            "assessment_type": self.assessment_type,
            "total_score": self.total_score,
            "nutritional_impairment_score": self.nutritional_impairment_score,
            "disease_severity_score": self.disease_severity_score,
            "age_score": self.age_score,
            "assessment_basis": self.assessment_basis,
            "risk_level": self.risk_level,
            "recommendations": self.recommendations,
            "bmi": float(self.bmi) if self.bmi is not None else None,
            "weight_change": self.weight_change,
            "disease_condition": self.disease_condition,
            "dietary_intake": self.dietary_intake,
        }
