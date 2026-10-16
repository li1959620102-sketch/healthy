# -*- coding: utf-8 -*-
"""J/P 组 · 管理与运营服务（用户管理 / 资质审核 / 系统配置 / 日志 / 大屏聚合）。

敏感操作（禁用账号、审核资质、改系统参数）统一写 operation_log，保留 ≥1 年（合规要求）。
system_config 中的 `scoring.*` 与 `agent.*` 是评分与智能体的关键参数，修改同样记日志。
"""
import json
import logging
from typing import Any, Dict, List, Optional

from app.core import rules
from app.core.errors import BizError, ErrCode
from app.dao.admin_dao import AdminDAO
from app.dao.assessment_dao import session_scope
from app.dao.dietitian_dao import DietitianDAO
from app.models.mysql.platform import AssessmentTemplate

logger = logging.getLogger("AdminService")

# 需要留痕的配置前缀。`chat.` 是智能体参数（检索阈值直接决定"答不出时是否拒答"），
# 与 `agent.` 同属 AI 行为开关；此前漏掉导致改拒答阈值不留审计（IT-AD-04 实测暴露）。
SENSITIVE_PREFIX = ("scoring.", "agent.", "chat.", "scene.", "alert.", "role.")


class AdminService:
    # ---------------- 用户 ---------------- #
    @staticmethod
    def users(role: Optional[str] = None, status: Optional[str] = None,
              keyword: Optional[str] = None, limit: int = 50,
              page: Optional[int] = None, page_size: Optional[int] = None) -> Dict[str, Any]:
        """用户列表。

    分页两种口径并存：传 `page` 走标准分页（越界返回空 items + 真实 total，
    见 IT-AD-07），只传 `limit` 时保持原来的"取前 N 条"行为，兼容既有调用方。
    """
        with session_scope() as s:
            total = AdminDAO.count_users(role, status, keyword, session=s)
            if page is None:
                rows = AdminDAO.list_users(role, status, keyword, limit, session=s)
                return {"items": [u.to_dict() for u in rows], "total": total,
                        "page": 1, "page_size": limit,
                        "has_more": len(rows) < limit or limit < total}
            p = max(1, int(page))
            size = max(1, int(page_size or limit))
            rows = AdminDAO.list_users(role, status, keyword, size,
                                       offset=(p - 1) * size, session=s)
            return {"items": [u.to_dict() for u in rows], "total": total,
                    "page": p, "page_size": size,
                    "has_more": p * size < total}

    @staticmethod
    def set_user_status(operator: dict, user_id: str, status: str,
                        ip: Optional[str] = None) -> Dict[str, Any]:
        if status not in ("active", "disabled"):
            raise BizError(ErrCode.PARAM_INVALID, "状态只能为 active 或 disabled")
        with session_scope() as s:
            u = AdminDAO.set_user_status(user_id, status, session=s)
            if not u:
                raise BizError(ErrCode.USER_NOT_FOUND, "用户不存在")
            # 动作名与审计台账口径一致（点分命名）：user.enable / user.disable
            AdminDAO.add_log(
                operator.get("sub"), operator.get("role", ""),
                "user.enable" if status == "active" else "user.disable",
                "user", user_id, {"status": status}, ip, session=s)
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
                             "dietitian.audit", "dietitian", user_id,
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

    @staticmethod
    def templates(status: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """全量模板（管理员视角）：可按状态筛选，供管理后台审核 / 下线操作。"""
        with session_scope() as s:
            q = s.query(AssessmentTemplate)
            if status:
                q = q.filter_by(status=status)
            rows = q.order_by(AssessmentTemplate.id.desc()).limit(limit).all()
            return {"items": [{
                "id": t.id, "code": t.code, "version": int(t.version) if t.version else None,
                "name": t.name, "type": t.type, "status": t.status,
                "created_by": t.created_by,
                "submitted_at": t.submitted_at, "published_at": t.published_at,
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
            # 评分/智能体/场景/预警/角色参数属敏感配置，变更必须留痕
            if key.startswith(SENSITIVE_PREFIX):
                AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                                 "config.update", "system_config", key,
                                 {"value": value[:500]}, ip, session=s)
            return {"config_key": row.config_key, "config_value": row.config_value,
                    "updated_by": row.updated_by, "updated_at": row.updated_at}

    # ---------------- 模板审核（补审计留痕） ---------------- #
    @staticmethod
    def log_template_audit(operator: dict, template_id: Any, action: str,
                           detail: Optional[dict] = None, ip: Optional[str] = None) -> None:
        """模板审核 / 下线动作写审计日志（action：template.audit / template.offline）。"""
        with session_scope() as s:
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             action, "template", str(template_id), detail, ip, session=s)

    @staticmethod
    def log_dashboard_update(operator: dict, detail: Optional[dict] = None,
                             ip: Optional[str] = None) -> None:
        """大屏方案保存写审计日志（action：dashboard.update）。"""
        with session_scope() as s:
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             "dashboard.update", "system_config", "dashboard.layout",
                             detail, ip, session=s)

    # ---------------- 预警阈值（FR-V-10） ---------------- #
    @staticmethod
    def alert_thresholds() -> Dict[str, Any]:
        """返回预警阈值：默认值 + 当前生效值 + 每项的中文名/单位/区间/说明。"""
        with session_scope() as s:
            row = AdminDAO.get_config(rules.ALERT_CONFIG_KEY, session=s)
            saved: Dict[str, Any] = {}
            if row and row.config_value:
                try:
                    saved = json.loads(row.config_value)
                except (ValueError, TypeError):
                    saved = {}
            items = []
            for key, spec in rules.ALERT_THRESHOLDS.items():
                items.append({
                    "key": key, "name": spec["name"], "unit": spec["unit"],
                    "min": spec["min"], "max": spec["max"], "desc": spec["desc"],
                    "default": spec["default"],
                    "value": rules.clamp_alert(key, saved.get(key, spec["default"])),
                })
            return {
                "items": items,
                "updated_by": row.updated_by if row else None,
                "updated_at": row.updated_at if row else None,
                "notify_channel": "短信 / 邮件通知待网关接入，当前仅落配置并留痕",
            }

    @staticmethod
    def set_alert_thresholds(operator: dict, thresholds: Dict[str, Any],
                             ip: Optional[str] = None) -> Dict[str, Any]:
        """保存预警阈值：逐项收敛到合法区间，写入 alert.thresholds 并留痕。"""
        if not isinstance(thresholds, dict) or not thresholds:
            raise BizError(ErrCode.PARAM_INVALID, "缺少 thresholds")
        unknown = [k for k in thresholds if k not in rules.ALERT_THRESHOLDS]
        if unknown:
            raise BizError(ErrCode.PARAM_INVALID, f"未知阈值项：{', '.join(unknown)}")
        clean = {k: rules.clamp_alert(k, v) for k, v in thresholds.items()}
        with session_scope() as s:
            row = AdminDAO.set_config(rules.ALERT_CONFIG_KEY, json.dumps(clean, ensure_ascii=False),
                                      "运营预警阈值（FR-V-10，改配置不改代码）",
                                      operator.get("sub"), session=s)
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             "alert.update", "system_config", rules.ALERT_CONFIG_KEY,
                             clean, ip, session=s)
            return {"config_key": row.config_key, "values": clean,
                    "updated_by": row.updated_by, "updated_at": row.updated_at}

    # ---------------- 角色配置（需求 §3.1 RBAC） ---------------- #
    @staticmethod
    def roles() -> Dict[str, Any]:
        """返回角色-权限矩阵：角色清单 + 能力清单 + 当前开关（未保存则由默认矩阵兜底）。"""
        with session_scope() as s:
            row = AdminDAO.get_config(rules.ROLE_CONFIG_KEY, session=s)
            matrix: Dict[str, Any] = {}
            if row and row.config_value:
                try:
                    matrix = json.loads(row.config_value)
                except (ValueError, TypeError):
                    matrix = {}
            items = []
            for cap, label in rules.ROLE_CAPABILITIES.items():
                saved = matrix.get(cap) or {}
                items.append({
                    "capability": cap, "name": label,
                    "roles": {r: bool(saved.get(r, rules.ROLE_PERMISSIONS[cap][r]))
                              for r in rules.ROLES},
                })
            return {
                "roles": [{"key": r, "label": rules.ROLE_LABELS[r]} for r in rules.ROLES],
                "items": items,
                "updated_by": row.updated_by if row else None,
                "updated_at": row.updated_at if row else None,
            }

    @staticmethod
    def set_role_permission(operator: dict, cap: str, role: str, allowed: bool,
                            ip: Optional[str] = None) -> Dict[str, Any]:
        """调整单个「角色 × 能力」开关，整表回写 role.permissions 并留痕。"""
        if cap not in rules.ROLE_CAPABILITIES:
            raise BizError(ErrCode.PARAM_INVALID, f"未知能力键：{cap}")
        if role not in rules.ROLES:
            raise BizError(ErrCode.PARAM_INVALID, f"未知角色：{role}")
        with session_scope() as s:
            row = AdminDAO.get_config(rules.ROLE_CONFIG_KEY, session=s)
            matrix: Dict[str, Any] = {}
            if row and row.config_value:
                try:
                    matrix = json.loads(row.config_value)
                except (ValueError, TypeError):
                    matrix = {}
            current = dict(matrix.get(cap) or rules.ROLE_PERMISSIONS[cap])
            current[role] = bool(allowed)
            matrix[cap] = current
            AdminDAO.set_config(rules.ROLE_CONFIG_KEY, json.dumps(matrix, ensure_ascii=False),
                                "角色-权限矩阵（需求 §3.1 RBAC，改配置不改代码）",
                                operator.get("sub"), session=s)
            AdminDAO.add_log(operator.get("sub"), operator.get("role", ""),
                             "role.update", "system_config", cap,
                             {"role": role, "allowed": bool(allowed)}, ip, session=s)
            return {"capability": cap, "roles": {r: bool(current.get(r, False)) for r in rules.ROLES}}


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

    # ---------------- 注销数据清理任务（合规 D2 / BV-26） ---------------- #
    @staticmethod
    def deletion_tasks(state: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """注销后的数据清理任务台账，标出**已超期**项。

        合规 D2 要求注销后 15 日内完成 MySQL / 向量库 / MinIO 三处清理。
        执行侧由离线清理作业负责，但必须有可查询的出口，否则"15 日内清理"
        只是写入任务表的一句话、无法核实是否兑现（这正是 BV-26 要验的）。
        超期判定：execute_at 已过且 state 未到 done，即触发告警口径（DELETION_ALERT_DAYS）。
        """
        from datetime import datetime

        from app.models.mysql.platform import DataDeletionTask

        now = datetime.now()
        with session_scope() as s:
            q = s.query(DataDeletionTask)
            if state:
                q = q.filter(DataDeletionTask.state == state)
            rows = q.order_by(DataDeletionTask.execute_at.asc()).limit(max(1, min(limit, 500))).all()
            items = []
            for r in rows:
                created = r.created_at or r.execute_at
                # 距注销登记已过天数；用 max(0, …) 避免"刚登记"因毫秒级时间差显示 -1 天
                elapsed_days = max(0, (now - created).days) if created else None
                items.append({
                    "id": r.id, "user_id": r.user_id, "state": r.state,
                    "scope": r.scope, "execute_at": r.execute_at,
                    "created_at": r.created_at,
                    "elapsed_days": elapsed_days,
                    "overdue": bool(r.execute_at and r.execute_at < now and r.state != "done"),
                })
            overdue = [i for i in items if i["overdue"]]
            return {
                "items": items,
                "total": len(items),
                "summary": {
                    "pending": sum(1 for i in items if i["state"] == "pending"),
                    "done": sum(1 for i in items if i["state"] == "done"),
                    "overdue": len(overdue),
                    "alert_days": rules.DELETION_ALERT_DAYS,
                },
            }

    @staticmethod
    def ops_dashboard() -> Dict[str, Any]:
        """运营可视化大屏聚合（P 组），非实时，允许缓存。"""
        with session_scope() as s:
            data = AdminDAO.stats(session=s)
            data["generated_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["note"] = "聚合数据非实时，供运营大屏展示"
            return data


admin_service = AdminService()
