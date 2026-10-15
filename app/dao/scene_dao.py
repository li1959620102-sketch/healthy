# -*- coding: utf-8 -*-
"""原型 v2 场景类数据访问层（宴请 / 旅行 / OCR / 大屏方案）。

用统一基类收敛重复的 create/get/list/update，各实体只声明 model 与过滤字段。
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import (
    BanquetGuest,
    BanquetMenu,
    DashboardLayout,
    OcrIndicator,
    OcrReport,
    TravelPlan,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class _BaseDAO:
    model = None
    owner_field = "user_id"

    @classmethod
    def create(cls, data: Dict[str, Any], session=None):
        own = session is None
        s = session or get_session()
        try:
            row = cls.model(**data)
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

    @classmethod
    def get(cls, row_id: int, session=None):
        own = session is None
        s = session or get_session()
        try:
            return s.query(cls.model).filter_by(id=row_id).first()
        finally:
            if own:
                s.close()

    @classmethod
    def list_by_owner(cls, owner: str, limit: int = 50, session=None) -> List[Any]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(cls.model)
                    .filter(getattr(cls.model, cls.owner_field) == str(owner))
                    .order_by(cls.model.id.desc()).limit(limit).all())
        finally:
            if own:
                s.close()

    @classmethod
    def update(cls, row_id: int, data: Dict[str, Any], session=None):
        own = session is None
        s = session or get_session()
        try:
            row = s.query(cls.model).filter_by(id=row_id).first()
            if not row:
                return None
            for k, v in (data or {}).items():
                setattr(row, k, v)
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


class BanquetMenuDAO(_BaseDAO):
    model = BanquetMenu


class TravelPlanDAO(_BaseDAO):
    model = TravelPlan


class OcrReportDAO(_BaseDAO):
    model = OcrReport


class DashboardLayoutDAO(_BaseDAO):
    model = DashboardLayout
    owner_field = "owner_id"

    @classmethod
    def get_default(cls, owner: str, session=None) -> Optional[DashboardLayout]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DashboardLayout)
                    .filter(DashboardLayout.owner_id == str(owner))
                    .order_by(DashboardLayout.is_default.desc(), DashboardLayout.id.desc())
                    .first())
        finally:
            if own:
                s.close()


class BanquetGuestDAO:
    """宾客按 menu_id 归组，单独实现（非 owner 维度）。"""

    @staticmethod
    def replace(menu_id: int, guests: List[Dict[str, Any]], session=None) -> int:
        own = session is None
        s = session or get_session()
        try:
            s.query(BanquetGuest).filter_by(menu_id=menu_id).delete()
            for g in guests or []:
                s.add(BanquetGuest(menu_id=menu_id, name=str(g.get("name") or "宾客"),
                                   chronic_tags=g.get("chronic_tags") or [],
                                   allergies=g.get("allergies") or [],
                                   constitution=g.get("constitution")))
            if own:
                s.commit()
            return len(guests or [])
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def list_by_menu(menu_id: int, session=None) -> List[BanquetGuest]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(BanquetGuest).filter_by(menu_id=menu_id).all()
        finally:
            if own:
                s.close()


class OcrIndicatorDAO:
    @staticmethod
    def list_by_report(report_id: int, session=None) -> List[OcrIndicator]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(OcrIndicator).filter_by(report_id=report_id).all()
        finally:
            if own:
                s.close()

    @staticmethod
    def replace(report_id: int, indicators: List[Dict[str, Any]], session=None) -> int:
        own = session is None
        s = session or get_session()
        try:
            s.query(OcrIndicator).filter_by(report_id=report_id).delete()
            for it in indicators or []:
                s.add(OcrIndicator(report_id=report_id, name=str(it.get("name") or "-"),
                                   value=it.get("value"), unit=it.get("unit"),
                                   ref_range=it.get("ref_range"),
                                   abnormal=1 if it.get("abnormal") else 0))
            if own:
                s.commit()
            return len(indicators or [])
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()
