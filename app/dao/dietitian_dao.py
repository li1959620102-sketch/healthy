# -*- coding: utf-8 -*-
"""C 组数据访问层：营养师资质、营养师-客户绑定、邀请码。

绑定机制（需求 B2 评审结论）：
- 同期仅 1 名营养师：由 `dietitian_customer.active_key` 生成列 + 唯一索引在 DB 层强制，
  应用层不依赖"先查后写"，避免并发下绕过。
- 解绑：status 置 unbound（生成列随之变 NULL，唯一约束自动放行新绑定）。
- 邀请码：存 system_config（`invite.<code>` → dietitian_id），无新增表；绑定后即失效。
"""
import logging
import random
import string
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import (
    DietitianCustomer,
    DietitianProfile,
    SystemConfig,
    UserProfile,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class DietitianDAO:
    # ---------------- 资质 ---------------- #
    @staticmethod
    def get_by_user(user_id: str, session=None) -> Optional[DietitianProfile]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(DietitianProfile).filter_by(user_id=str(user_id)).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def apply(user_id: str, data: Dict[str, Any], session=None) -> DietitianProfile:
        """提交/更新资质申请。已存在则更新内容并回到 pending。"""
        own = session is None
        s = session or get_session()
        try:
            prof = s.query(DietitianProfile).filter_by(user_id=str(user_id)).first()
            if prof:
                for k in ("cert_name", "cert_no", "cert_url", "work_proof_url",
                          "specialties", "cases", "cert_expire_at"):
                    if k in data and data[k] is not None:
                        setattr(prof, k, data[k])
                prof.audit_status = "pending"
                prof.reject_reason = None
            else:
                prof = DietitianProfile(user_id=str(user_id), audit_status="pending", **{
                    k: v for k, v in data.items()
                    if k in ("cert_name", "cert_no", "cert_url", "work_proof_url",
                             "specialties", "cases", "cert_expire_at")
                })
                s.add(prof)
            if own:
                s.commit()
                s.refresh(prof)
            else:
                s.flush()
            return prof
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def update_mine(user_id: str, data: Dict[str, Any], session=None) -> Optional[DietitianProfile]:
        """维护擅长领域与服务案例（不改审核状态）。"""
        own = session is None
        s = session or get_session()
        try:
            prof = s.query(DietitianProfile).filter_by(user_id=str(user_id)).first()
            if not prof:
                return None
            for k in ("specialties", "cases", "cert_expire_at"):
                if k in data and data[k] is not None:
                    setattr(prof, k, data[k])
            if own:
                s.commit()
                s.refresh(prof)
            return prof
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def search(specialty: Optional[str] = None, limit: int = 20,
               session=None) -> List[DietitianProfile]:
        """按擅长领域搜索已通过审核的营养师。"""
        own = session is None
        s = session or get_session()
        try:
            q = s.query(DietitianProfile).filter_by(audit_status="approved")
            rows = q.order_by(DietitianProfile.audit_at.desc()).limit(200).all()
            if specialty:
                rows = [r for r in rows
                        if r.specialties and specialty in (r.specialties or [])]
            return rows[:limit]
        finally:
            if own:
                s.close()

    @staticmethod
    def list_pending(session=None) -> List[DietitianProfile]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DietitianProfile).filter_by(audit_status="pending")
                    .order_by(DietitianProfile.id.asc()).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def audit(user_id: str, auditor_id: str, approve: bool,
              reason: str = "", session=None) -> Optional[DietitianProfile]:
        own = session is None
        s = session or get_session()
        try:
            prof = s.query(DietitianProfile).filter_by(user_id=str(user_id)).first()
            if not prof:
                return None
            prof.audit_status = "approved" if approve else "rejected"
            prof.audit_at = datetime.now()
            prof.auditor_id = str(auditor_id)
            prof.reject_reason = None if approve else (reason or "资质审核未通过")
            if own:
                s.commit()
                s.refresh(prof)
            return prof
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    # ---------------- 绑定关系 ---------------- #
    @staticmethod
    def current_dietitian(customer_id: str, session=None) -> Optional[DietitianCustomer]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DietitianCustomer)
                    .filter_by(customer_id=str(customer_id), status="active").first())
        finally:
            if own:
                s.close()

    @staticmethod
    def bind(dietitian_id: str, customer_id: str, source: str = "invite_code",
             session=None) -> DietitianCustomer:
        """绑定客户：先解绑该客户已有的生效关系，再建新关系（保证同期仅 1 名）。"""
        own = session is None
        s = session or get_session()
        try:
            exist = (s.query(DietitianCustomer)
                     .filter_by(customer_id=str(customer_id), status="active").first())
            if exist:
                if str(exist.dietitian_id) == str(dietitian_id):
                    return exist
                exist.status = "unbound"
                exist.unbound_at = datetime.now()
                s.flush()
            rel = DietitianCustomer(dietitian_id=str(dietitian_id), customer_id=str(customer_id),
                                    bind_source=source, status="active", bound_at=datetime.now())
            s.add(rel)
            if own:
                s.commit()
                s.refresh(rel)
            else:
                s.flush()
            return rel
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def unbind(dietitian_id: str, customer_id: str, session=None) -> bool:
        own = session is None
        s = session or get_session()
        try:
            rel = (s.query(DietitianCustomer)
                   .filter_by(dietitian_id=str(dietitian_id), customer_id=str(customer_id),
                              status="active").first())
            if not rel:
                return False
            rel.status = "unbound"
            rel.unbound_at = datetime.now()
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

    @staticmethod
    def list_customers(dietitian_id: str, session=None) -> List[DietitianCustomer]:
        own = session is None
        s = session or get_session()
        try:
            return (s.query(DietitianCustomer)
                    .filter_by(dietitian_id=str(dietitian_id), status="active")
                    .order_by(DietitianCustomer.bound_at.desc()).all())
        finally:
            if own:
                s.close()

    @staticmethod
    def count_customers(dietitian_id: str, session=None) -> int:
        return len(DietitianDAO.list_customers(dietitian_id, session=session))

    # ---------------- 邀请码 ---------------- #
    @staticmethod
    def new_invite_code(dietitian_id: str, session=None) -> str:
        """生成 6 位邀请码，存 system_config（`invite.<code>` → dietitian_id）。"""
        own = session is None
        s = session or get_session()
        try:
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
            s.merge(SystemConfig(config_key=f"invite.{code}",
                                 config_value=str(dietitian_id),
                                 description="营养师绑定邀请码"))
            if own:
                s.commit()
            return code
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def resolve_invite_code(code: str, session=None) -> Optional[str]:
        """解析邀请码 → 营养师 user_id；未找到返回 None。"""
        if not code:
            return None
        own = session is None
        s = session or get_session()
        try:
            row = s.query(SystemConfig).filter_by(config_key=f"invite.{code.strip().upper()}").first()
            return row.config_value if row else None
        finally:
            if own:
                s.close()

    @staticmethod
    def consume_invite_code(code: str, session=None) -> None:
        """绑定成功后作废旧码，避免重复使用。"""
        own = session is None
        s = session or get_session()
        try:
            row = s.query(SystemConfig).filter_by(config_key=f"invite.{code.strip().upper()}").first()
            if row:
                s.delete(row)
                if own:
                    s.commit()
        finally:
            if own:
                s.close()

    @staticmethod
    def customer_profile(customer_id: str, session=None) -> Optional[UserProfile]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(UserProfile).filter_by(user_id=str(customer_id)).first()
        finally:
            if own:
                s.close()
