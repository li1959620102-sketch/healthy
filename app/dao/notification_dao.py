# -*- coding: utf-8 -*-
"""站内消息通知数据访问层（原型：顶栏「🔔 消息中心」）。"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import Notification
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class NotificationDAO:
    @staticmethod
    def create(user_id: str, title: str, content: str = "", ntype: str = "system",
               link: Optional[str] = None, session=None) -> Notification:
        own = session is None
        s = session or get_session()
        try:
            row = Notification(user_id=str(user_id), type=ntype, title=title,
                               content=content or None, link=link)
            s.add(row)
            if own:
                s.commit()
                s.refresh(row)
            else:
                s.flush()
            return row
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def list_mine(user_id: str, unread_only: bool = False, limit: int = 50,
                  session=None) -> List[Notification]:
        own = session is None
        s = session or get_session()
        try:
            q = s.query(Notification).filter_by(user_id=str(user_id))
            if unread_only:
                q = q.filter(Notification.read_at.is_(None))
            return q.order_by(Notification.id.desc()).limit(limit).all()
        finally:
            if own:
                s.close()

    @staticmethod
    def unread_count(user_id: str, session=None) -> int:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(Notification)
                    .filter(Notification.user_id == str(user_id),
                            Notification.read_at.is_(None)).count())
        finally:
            if own:
                s.close()

    @staticmethod
    def mark_read(user_id: str, notification_id: int, session=None) -> Optional[Notification]:
        """标记已读；非本人返回 None（调用方按 404 处理，防枚举）。"""
        own = session is None
        s = session or get_session()
        try:
            row = (s.query(Notification)
                   .filter_by(id=notification_id, user_id=str(user_id)).first())
            if not row:
                return None
            if row.read_at is None:
                row.read_at = datetime.now()
            if own:
                s.commit()
                s.refresh(row)
            return row
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def mark_all_read(user_id: str, session=None) -> int:
        own = session is None
        s = session or get_session()
        try:
            n = (s.query(Notification)
                 .filter(Notification.user_id == str(user_id),
                         Notification.read_at.is_(None))
                 .update({Notification.read_at: datetime.now()}, synchronize_session=False))
            if own:
                s.commit()
            return int(n or 0)
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()
