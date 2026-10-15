# -*- coding: utf-8 -*-
"""健康风险测评记录数据访问层。"""
import logging
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.health_assessment import UserHealthRiskAssessment
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class HealthAssessmentDAO:
    @staticmethod
    def create(data: dict) -> UserHealthRiskAssessment:
        session = get_session()
        try:
            record = UserHealthRiskAssessment(**data)
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50) -> List[UserHealthRiskAssessment]:
        session = get_session()
        try:
            return (
                session.query(UserHealthRiskAssessment)
                .filter_by(user_id=user_id)
                .order_by(desc(UserHealthRiskAssessment.assessment_time))
                .limit(limit)
                .all()
            )
        finally:
            session.close()

    @staticmethod
    def count_by_user(user_id: str) -> int:
        session = get_session()
        try:
            return session.query(UserHealthRiskAssessment).filter_by(user_id=user_id).count()
        finally:
            session.close()

    @staticmethod
    def get_by_id(record_id: int, user_id: Optional[str] = None):
        session = get_session()
        try:
            query = session.query(UserHealthRiskAssessment).filter_by(id=record_id)
            if user_id:
                query = query.filter_by(user_id=user_id)
            return query.first()
        finally:
            session.close()
