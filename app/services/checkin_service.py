# -*- coding: utf-8 -*-
"""打卡服务（原型：首页「本周打卡 5/7」/ 食养页「今日打卡 · 执行率」）。

规则：
- 一周以**周一**为起点，执行率分母固定为 **7**（与原型 5/7 = 71% 一致）。
- 同日同类型重复打卡 = 幂等更新，不重复计数（唯一键保证）。
- 不允许给未来日期打卡。
"""
import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

from app.core.errors import BizError, ErrCode
from app.dao.checkin_dao import CheckinDAO

logger = logging.getLogger("CheckinService")

CHECKIN_TYPES = ("plan", "meal", "exercise", "weight")
WEEK_DAYS = 7
MAX_LOOKBACK_DAYS = 365


def _week_start(d: date) -> date:
    """所在周的周一。"""
    return d - timedelta(days=d.weekday())


def _brief(r) -> Dict[str, Any]:
    return {
        "id": r.id,
        "checkin_date": r.checkin_date,
        "check_type": r.check_type,
        "plan_id": r.plan_id,
        "note": r.note,
        "created_at": r.created_at,
    }


class CheckinService:
    @staticmethod
    def checkin(user: Optional[dict], check_type: str = "plan",
                plan_id: Optional[int] = None, note: Optional[str] = None,
                checkin_date: Optional[date] = None) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        if check_type not in CHECKIN_TYPES:
            raise BizError(ErrCode.PARAM_INVALID, f"打卡类型需为 {'/'.join(CHECKIN_TYPES)}")
        day = checkin_date or date.today()
        if day > date.today():
            raise BizError(ErrCode.PARAM_INVALID, "不能为未来日期打卡")

        uid = str(user.get("sub"))
        row = CheckinDAO.upsert(uid, day, check_type, plan_id, note)
        data = _brief(row)
        data["stats"] = CheckinService.stats(user)
        return data

    @staticmethod
    def list_mine(user: Optional[dict], days: int = 30) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        if not isinstance(days, int) or days < 1 or days > MAX_LOOKBACK_DAYS:
            days = 30
        uid = str(user.get("sub"))
        today = date.today()
        rows = CheckinDAO.list_between(uid, today - timedelta(days=days - 1), today)
        return {"items": [_brief(r) for r in rows], "total": len(rows), "days": days}

    @staticmethod
    def stats(user: Optional[dict], today: Optional[date] = None) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        uid = str(user.get("sub"))
        t = today or date.today()
        monday = _week_start(t)
        sunday = monday + timedelta(days=WEEK_DAYS - 1)
        days = CheckinDAO.list_dates(uid, monday, sunday)
        return {
            "week_start": monday,
            "week_end": sunday,
            "week_checked": len(days),
            "week_total": WEEK_DAYS,
            "week_rate": round(len(days) / WEEK_DAYS * 100),
            "checked_today": t in days,
            "streak": CheckinService._streak(uid, t),
            "days": list(days),
        }

    @staticmethod
    def _streak(uid: str, today: date) -> int:
        """连续打卡天数（截至今天或昨天；断档则归零）。"""
        last = CheckinDAO.latest_date(uid)
        if not last or (today - last).days > 1:
            return 0
        start = max(last - timedelta(days=MAX_LOOKBACK_DAYS), date(1970, 1, 1))
        days = set(CheckinDAO.list_dates(uid, start, last))
        n, cur = 0, last
        while cur in days:
            n += 1
            cur -= timedelta(days=1)
        return n


checkin_service = CheckinService()
