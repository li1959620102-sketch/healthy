# -*- coding: utf-8 -*-
"""打卡数据访问层（原型：首页「本周打卡」/ 食养页「今日打卡 · 执行率」）。

约定与其它 DAO 一致：所有方法接受可选 `session`，传入则由调用方管事务。
"""
import logging
from datetime import date
from typing import List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import CheckinRecord
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class CheckinDAO:
    @staticmethod
    def upsert(user_id: str, checkin_date: date, check_type: str = "plan",
               plan_id: Optional[int] = None, note: Optional[str] = None,
               session=None) -> CheckinRecord:
        """按唯一键 (user_id, 日期, 类型) upsert —— 同日重复打卡视为更新，天然幂等。"""
        own = session is None
        s = session or get_session()
        try:
            row = (s.query(CheckinRecord)
                   .filter_by(user_id=str(user_id), checkin_date=checkin_date,
                              check_type=check_type)
                   .first())
            if row:
                if note is not None:
                    row.note = note
                if plan_id is not None:
                    row.plan_id = plan_id
            else:
                row = CheckinRecord(user_id=str(user_id), checkin_date=checkin_date,
                                    check_type=check_type, plan_id=plan_id, note=note)
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
    def list_between(user_id: str, start: date, end: date, session=None) -> List[CheckinRecord]:
        """区间内的打卡记录（含首尾）。"""
        own = session is None
        s = session or get_session()
        try:
            return (s.query(CheckinRecord)
                    .filter(CheckinRecord.user_id == str(user_id),
                            CheckinRecord.checkin_date >= start,
                            CheckinRecord.checkin_date <= end)
                    .order_by(CheckinRecord.checkin_date.asc()).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def list_dates(user_id: str, start: date, end: date, session=None) -> List[date]:
        """区间内去重后的打卡日期（执行率只需"哪天打了"）。"""
        rows = CheckinDAO.list_between(user_id, start, end, session=session)
        return sorted({r.checkin_date for r in rows})

    @staticmethod
    def latest_date(user_id: str, session=None) -> Optional[date]:
        own = session is None
        s = session or get_session()
        try:
            row = (s.query(CheckinRecord)
                   .filter_by(user_id=str(user_id))
                   .order_by(CheckinRecord.checkin_date.desc()).first())
            return row.checkin_date if row else None
        finally:
            if own:
                s.close()
