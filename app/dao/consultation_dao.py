# -*- coding: utf-8 -*-
"""H 组数据访问层：1v1 人工咨询（含转人工来源）。

状态机：pending（待接入）→ active（进行中）→ closed（已结束）/ timeout（超时未接入）
一期仅文本消息；附件（图片）字段已预留，二期启用。
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import Consultation, ConsultationMessage
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class ConsultationDAO:
    @staticmethod
    def create(data: Dict[str, Any], session=None) -> Consultation:
        own = session is None
        s = session or get_session()
        try:
            con = Consultation(**data)
            s.add(con)
            if own:
                s.commit()
                s.refresh(con)
            else:
                s.flush()
            return con
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def get(consultation_id: int, session=None) -> Optional[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(Consultation).filter_by(id=consultation_id).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def list_for_user(user_id: str, limit: int = 50, session=None) -> List[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(Consultation).filter_by(user_id=str(user_id))
                    .order_by(Consultation.id.desc()).limit(limit).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def list_for_dietitian(dietitian_id: str, limit: int = 50,
                           session=None) -> List[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(Consultation).filter_by(dietitian_id=str(dietitian_id))
                    .order_by(Consultation.id.desc()).limit(limit).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def list_pending(limit: int = 50, session=None) -> List[Consultation]:
        """待接入队列（营养师工作台/管理员派单用）。"""
        own = session is None
        s = session or get_session()
        try:
            return (s.query(Consultation).filter_by(status="pending")
                    .order_by(Consultation.created_at.asc()).limit(limit).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def accept(consultation_id: int, dietitian_id: str, session=None) -> Optional[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            con = s.query(Consultation).filter_by(id=consultation_id).first()
            if not con or con.status != "pending":
                return None
            con.dietitian_id = str(dietitian_id)
            con.status = "active"
            con.accepted_at = datetime.now()
            if own:
                s.commit()
                s.refresh(con)
            return con
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def close(consultation_id: int, reason: str = "用户结束",
              session=None) -> Optional[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            con = s.query(Consultation).filter_by(id=consultation_id).first()
            if not con or con.status in ("closed", "timeout"):
                return None
            con.status = "closed"
            con.closed_at = datetime.now()
            con.close_reason = reason
            if own:
                s.commit()
                s.refresh(con)
            return con
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def rate(consultation_id: int, rating: int, comment: str = "",
             session=None) -> Optional[Consultation]:
        own = session is None
        s = session or get_session()
        try:
            con = s.query(Consultation).filter_by(id=consultation_id).first()
            if not con:
                return None
            con.rating = rating
            con.rating_comment = comment or None
            if own:
                s.commit()
                s.refresh(con)
            return con
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def expire_stale(minutes: int = 30, session=None) -> int:
        """把超过 N 分钟仍未接入的 pending 咨询标记为 timeout（BV-25 懒清理）。

        返回本次置为超时的条数。仅在读取路径调用，无需定时任务。
        """
        own = session is None
        s = session or get_session()
        try:
            deadline = datetime.now() - timedelta(minutes=minutes)
            rows = (s.query(Consultation)
                    .filter(Consultation.status == "pending",
                            Consultation.created_at < deadline)
                    .all())
            count = 0
            for con in rows:
                con.status = "timeout"
                con.closed_at = datetime.now()
                con.close_reason = "超时未接入"
                count += 1
            if own:
                s.commit()
            return count
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    # ---------------- 消息 ---------------- #
    @staticmethod
    def add_message(consultation_id: int, sender_type: str, content: str,
                    session=None) -> ConsultationMessage:
        own = session is None
        s = session or get_session()
        try:
            msg = ConsultationMessage(consultation_id=consultation_id, sender_type=sender_type,
                                      content_type="text", content=content)
            s.add(msg)
            if own:
                s.commit()
                s.refresh(msg)
            else:
                s.flush()
            return msg
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def list_messages(consultation_id: int, limit: int = 200,
                      session=None) -> List[ConsultationMessage]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(ConsultationMessage)
                    .filter_by(consultation_id=consultation_id)
                    .order_by(ConsultationMessage.id.asc()).limit(limit).all())
        finally:
            if own:
                s.close()
