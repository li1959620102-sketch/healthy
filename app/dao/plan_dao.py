# -*- coding: utf-8 -*-
"""L/M/O 组数据访问层：食养计划、食谱明细、舌象图片。"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import DietPlan, DietPlanRecipe, TongueImage
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class PlanDAO:
    @staticmethod
    def create(data: Dict[str, Any], session=None) -> DietPlan:
        own = session is None
        s = session or get_session()
        try:
            plan = DietPlan(**data)
            s.add(plan)
            if own:
                s.commit()
                s.refresh(plan)
            else:
                s.flush()
            return plan
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def get(plan_id: int, session=None) -> Optional[DietPlan]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(DietPlan).filter_by(id=plan_id).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def list_by_user(user_id: str, limit: int = 20, session=None) -> List[DietPlan]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DietPlan).filter_by(user_id=str(user_id))
                    .order_by(DietPlan.generated_at.desc()).limit(limit).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def update(plan_id: int, data: Dict[str, Any], session=None) -> Optional[DietPlan]:
        own = session is None
        s = session or get_session()
        try:
            plan = s.query(DietPlan).filter_by(id=plan_id).first()
            if not plan:
                return None
            for k in ("title", "constitution", "target_symptoms", "avoid_foods",
                      "summary_text", "state", "references"):
                if k in data and data[k] is not None:
                    setattr(plan, k, data[k])
            if own:
                s.commit()
                s.refresh(plan)
            return plan
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def add_recipes(plan_id: int, recipes: List[Dict[str, Any]], session=None) -> int:
        own = session is None
        s = session or get_session()
        try:
            for r in recipes:
                s.add(DietPlanRecipe(plan_id=plan_id, **r))
            if own:
                s.commit()
            return len(recipes)
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def list_recipes(plan_id: int, session=None) -> List[DietPlanRecipe]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DietPlanRecipe).filter_by(plan_id=plan_id)
                    .order_by(DietPlanRecipe.day_no.asc(), DietPlanRecipe.id.asc()).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def clear_recipes(plan_id: int, session=None) -> None:
        own = session is None
        s = session or get_session()
        try:
            s.query(DietPlanRecipe).filter_by(plan_id=plan_id).delete(synchronize_session=False)
            if own:
                s.commit()
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def delete(plan_id: int, user_id: str, session=None) -> bool:
        """按用户归属删除计划及食谱明细，供 Agent 补偿事务使用。"""
        own = session is None
        s = session or get_session()
        try:
            plan = (s.query(DietPlan)
                    .filter(DietPlan.id == plan_id, DietPlan.user_id == str(user_id)).first())
            if not plan:
                return False
            s.query(DietPlanRecipe).filter_by(plan_id=plan_id).delete(synchronize_session=False)
            s.delete(plan)
            if own:
                s.commit()
            return True
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()


class TongueDAO:
    @staticmethod
    def create(data: Dict[str, Any], session=None) -> TongueImage:
        own = session is None
        s = session or get_session()
        try:
            img = TongueImage(**data)
            s.add(img)
            if own:
                s.commit()
                s.refresh(img)
            else:
                s.flush()
            return img
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def get(image_id: int, session=None) -> Optional[TongueImage]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(TongueImage).filter_by(id=image_id).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def update(image_id: int, data: Dict[str, Any], session=None) -> Optional[TongueImage]:
        own = session is None
        s = session or get_session()
        try:
            img = s.query(TongueImage).filter_by(id=image_id).first()
            if not img:
                return None
            for k in ("status", "recognized_result", "model_name", "record_id"):
                if k in data and data[k] is not None:
                    setattr(img, k, data[k])
            if own:
                s.commit()
                s.refresh(img)
            return img
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()
