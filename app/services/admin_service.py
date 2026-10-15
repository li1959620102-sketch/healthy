# -*- coding: utf-8 -*-
"""J/P 组 · 管理与运营服务（用户管理 / 资质审核 / 系统配置 / 日志 / 大屏聚合）。

敏感操作（禁用账号、审核资质、改系统参数）统一写 operation_log，保留 ≥1 年（合规要求）。
system_config 中的 `scoring.*` 与 `agent.*` 是评分与智能体的关键参数，修改同样记日志。
"""
import logging
from typing import Any, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.admin_dao import AdminDAO
from app.dao.assessment_dao import session_scope
from app.dao.dietitian_dao import DietitianDAO
from app.models.mysql.platform import AssessmentTemplate

logger = logging.getLogger("AdminService")

SENSITIVE_PREFIX = ("scoring.", "agent.", "scene.")


class AdminService:
    # ---------------- 用户 ---------------- #
    @staticmethod
    def users(role: Optional[str] = None, status: Optional[str] = None,
              keyword: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        with session_scope() as s:
            rows = AdminDAO.list_users(role, status, keyword, limit, session=s)
            return {"items": [u.to_dict() for u in rows], "total": len(rows)}

    @staticmethod
    def set_user_status(operator: dict, user_id: str, status: str,
                        ip: Optional[str] = None) -> Dict[str, Any]:
        if status not in ("active", "disabled"):
            raise BizError(ErrCode.PARAM_INVALID, "状态只能为 active 或 disabled")
        with session_scope() as s:
            u = AdminDAO.set_user_status(user_id, status, session=s)
            if not u:
                raise BizError(ErrCode.USER_NOT_FOUND, "用户不存在")
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             "user_status_change", "user", user_id,
                             {"status": status}, ip, session=s)
            return u.to_dict()

    # ---------------- 资质审核 ---------------- #
    @staticmethod
    def nutritionist_audits() -> Dict[str, Any]:
        with session_scope() as s:
            rows = DietitianDAO.list_pending(session=s)
            return {"items": [{
                "user_id": r.user_id, "cert_name": r.cert_name, "cert_no": r.cert_no,
                "specialties": r.specialties, "cases": r.cases,
                "cert_url": r.cert_url, "work_proof_url": r.work_proof_url,
            } for r in rows], "total": len(rows)}

    @staticmethod
    def audit_nutritionist(operator: dict, user_id: str, approve: bool,
                           reason: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
        with session_scope() as s:
            prof = DietitianDAO.audit(user_id, operator.get("sub"), approve, reason, session=s)
            if not prof:
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "资质申请不存在")
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             "nutritionist_audit", "dietitian", user_id,
                             {"approve": approve, "reason": reason}, ip, session=s)
            return {"user_id": prof.user_id, "audit_status": prof.audit_status}

    # ---------------- 模板审核 ---------------- #
    @staticmethod
    def template_audits() -> Dict[str, Any]:
        with session_scope() as s:
            rows = (s.query(AssessmentTemplate)
                    .filter_by(status="pending")
                    .order_by(AssessmentTemplate.submitted_at.asc()).all())
            return {"items": [{
                "id": t.id, "code": t.code, "version": int(t.version),
                "name": t.name, "type": t.type, "created_by": t.created_by,
                "submitted_at": t.submitted_at,
            } for t in rows], "total": len(rows)}

    # ---------------- 系统配置 ---------------- #
    @staticmethod
    def list_configs() -> Dict[str, Any]:
        with session_scope() as s:
            rows = AdminDAO.list_configs(session=s)
            return {"items": [{
                "config_key": r.config_key, "config_value": r.config_value,
                "description": r.description, "updated_by": r.updated_by,
                "updated_at": r.updated_at,
            } for r in rows], "total": len(rows)}

    @staticmethod
    def set_config(operator: dict, key: str, value: str,
                   desc: Optional[str] = None, ip: Optional[str] = None) -> Dict[str, Any]:
        if not key:
            raise BizError(ErrCode.PARAM_INVALID, "缺少 config_key")
        with session_scope() as s:
            row = AdminDAO.set_config(key, value, desc, operator.get("sub"), session=s)
            # 评分/智能体/场景参数属敏感配置，变更必须留痕
            if key.startswith(SENSITIVE_PREFIX):
                AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                                 "config_change", "system_config", key,
                                 {"value": value[:500]}, ip, session=s)
            return {"config_key": row.config_key, "config_value": row.config_value,
                    "updated_by": row.updated_by, "updated_at": row.updated_at}

    # ---------------- 日志 ---------------- #
    @staticmethod
    def logs(action: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        with session_scope() as s:
            rows = AdminDAO.list_logs(action, limit, session=s)
            return {"items": [{
                "id": r.id, "operator_id": r.operator_id, "operator_role": r.operator_role,
                "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                "content": r.content, "created_at": r.created_at,
            } for r in rows], "total": len(rows)}

    # ---------------- 概览 / 大屏 ---------------- #
    @staticmethod
    def overview() -> Dict[str, Any]:
        with session_scope() as s:
            return AdminDAO.stats(session=s)

    @staticmethod
    def ops_dashboard() -> Dict[str, Any]:
        """运营可视化大屏聚合（P 组），非实时，允许缓存。"""
        with session_scope() as s:
            data = AdminDAO.stats(session=s)
            data["generated_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["note"] = "聚合数据非实时，供运营大屏展示"
            return data


admin_service = AdminService()
