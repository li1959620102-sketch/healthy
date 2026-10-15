# -*- coding: utf-8 -*-
"""C 组 · 营养师服务（资质 / 搜索 / 邀请码绑定 / 客户列表 / 客户报告）。

绑定规则（需求 B2 + 设计评审）：
- 同期仅 1 名营养师（DB 层由 `active_key` 生成列 + 唯一索引强制）
- 绑定来源：邀请码 / 咨询完成后自动绑定 / 管理员指派
- 用户可主动解绑；营养师也可解除客户关系
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.assessment_dao import ReportDAO, session_scope
from app.dao.consultation_dao import ConsultationDAO
from app.dao.dietitian_dao import DietitianDAO
from app.dao.plan_dao import PlanDAO
from app.services.checkin_service import CheckinService

logger = logging.getLogger("DietitianService")


def _brief_profile(p) -> Dict[str, Any]:
    return {
        "user_id": p.user_id,
        "specialties": p.specialties or [],
        "cases": p.cases,
        "audit_status": p.audit_status,
        "cert_name": p.cert_name,
        "cert_expire_at": p.cert_expire_at,
        "audit_at": p.audit_at,
    }


class DietitianService:
    # ---------------- 资质 ---------------- #
    @staticmethod
    def apply(user: dict, data: Dict[str, Any]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            prof = DietitianDAO.apply(str(user.get("sub")), data, session=s)
            return _brief_profile(prof)

    @staticmethod
    def get_me(user: dict) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            prof = DietitianDAO.get_by_user(str(user.get("sub")), session=s)
            if not prof:
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "尚未提交资质申请")
            data = _brief_profile(prof)
            data["cert_no"] = prof.cert_no
            data["reject_reason"] = prof.reject_reason
            data["customer_count"] = DietitianDAO.count_customers(str(user.get("sub")), session=s)
            return data

    @staticmethod
    def update_me(user: dict, data: Dict[str, Any]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            prof = DietitianDAO.update_mine(str(user.get("sub")), data, session=s)
            if not prof:
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "尚未提交资质申请")
            return _brief_profile(prof)

    @staticmethod
    def public_info(dietitian_id: str) -> Dict[str, Any]:
        with session_scope() as s:
            prof = DietitianDAO.get_by_user(dietitian_id, session=s)
            if not prof or prof.audit_status != "approved":
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "营养师不存在或未通过审核")
            data = _brief_profile(prof)
            data["customer_count"] = DietitianDAO.count_customers(dietitian_id, session=s)
            return data

    @staticmethod
    def search(specialty: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        with session_scope() as s:
            rows = DietitianDAO.search(specialty, limit=limit, session=s)
            return {"items": [_brief_profile(r) for r in rows], "total": len(rows)}

    # ---------------- 邀请码与绑定 ---------------- #
    @staticmethod
    def new_invite_code(user: dict) -> Dict[str, Any]:
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可生成邀请码")
        with session_scope() as s:
            prof = DietitianDAO.get_by_user(str(user.get("sub")), session=s)
            if not prof or prof.audit_status != "approved":
                raise BizError(ErrCode.PERMISSION_DENIED, "资质审核通过后才可生成邀请码")
            code = DietitianDAO.new_invite_code(str(user.get("sub")), session=s)
            return {"invite_code": code, "dietitian_id": str(user.get("sub"))}

    @staticmethod
    def bind_by_code(user: dict, code: str) -> Dict[str, Any]:
        """用户凭邀请码绑定营养师。"""
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        if not code:
            raise BizError(ErrCode.PARAM_INVALID, "缺少邀请码")
        with session_scope() as s:
            dietitian_id = DietitianDAO.resolve_invite_code(code, session=s)
            if not dietitian_id:
                raise BizError(ErrCode.PARAM_INVALID, "邀请码无效或已失效")
            if str(dietitian_id) == str(user.get("sub")):
                raise BizError(ErrCode.PARAM_INVALID, "不能绑定自己")
            prof = DietitianDAO.get_by_user(dietitian_id, session=s)
            if not prof or prof.audit_status != "approved":
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "该营养师资质未通过审核")
            rel = DietitianDAO.bind(dietitian_id, str(user.get("sub")),
                                    source="invite_code", session=s)
            DietitianDAO.consume_invite_code(code, session=s)
            return {"dietitian_id": rel.dietitian_id, "customer_id": rel.customer_id,
                    "status": rel.status, "bound_at": rel.bound_at}

    @staticmethod
    def unbind(user: dict, customer_uid: Optional[str] = None) -> Dict[str, Any]:
        """解绑：用户传空解绑自己的营养师；营养师传 customer_uid 解绑指定客户。"""
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            if customer_uid:
                # 营养师（或管理员）解除与某客户的关系
                if user.get("role") not in ("nutritionist", "dietitian", "admin"):
                    raise BizError(ErrCode.PERMISSION_DENIED, "无权限解除该绑定")
                ok = DietitianDAO.unbind(str(user.get("sub")), customer_uid, session=s)
            else:
                rel = DietitianDAO.current_dietitian(str(user.get("sub")), session=s)
                if not rel:
                    raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "当前没有绑定的营养师")
                ok = DietitianDAO.unbind(rel.dietitian_id, str(user.get("sub")), session=s)
            if not ok:
                raise BizError(ErrCode.DIETITIAN_NOT_FOUND, "绑定关系不存在")
            return {"unbound": True}

    @staticmethod
    def auto_bind_after_consultation(dietitian_id: str, customer_id: str, session=None) -> bool:
        """咨询结束后自动绑定（需求：咨询完成可自动建立关系）。"""
        try:
            DietitianDAO.bind(dietitian_id, customer_id, source="consultation", session=session)
            return True
        except Exception as e:
            logger.warning(f"咨询后自动绑定失败（不影响结束流程）：{e}")
            return False

    # ---------------- 客户 ---------------- #
    @staticmethod
    def customers(user: dict, level: Optional[str] = None) -> Dict[str, Any]:
        """客户列表：可按最近一份报告等级筛选（如只筛"待改善"）。"""
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可查看客户列表")
        with session_scope() as s:
            rels = DietitianDAO.list_customers(str(user.get("sub")), session=s)
            items: List[Dict[str, Any]] = []
            for r in rels:
                reports = ReportDAO.list_by_user(r.customer_id, limit=1, session=s)
                latest = reports[0] if reports else None
                latest_level = latest.level if latest else None
                if level and latest_level != level:
                    continue
                prof = DietitianDAO.customer_profile(r.customer_id, session=s)
                plans = PlanDAO.list_by_user(r.customer_id, limit=1, session=s)
                plan = plans[0] if plans else None
                tags: List[str] = []
                if plan and plan.constitution:
                    tags.append(str(plan.constitution))
                if plan and isinstance(plan.target_symptoms, list):
                    tags.extend(str(x) for x in plan.target_symptoms[:2])
                if not tags and prof and prof.diet_preference:
                    tags.append(str(prof.diet_preference))

                consults = ConsultationDAO.list_for_user(r.customer_id, limit=1, session=s)
                last_consult = consults[0].created_at if consults else None
                try:
                    execution_rate = CheckinService.stats({"sub": r.customer_id})["week_rate"]
                except Exception:                       # noqa: BLE001
                    execution_rate = None

                items.append({
                    "customer_id": r.customer_id,
                    "tags": tags,
                    "bound_at": r.bound_at,
                    "bind_source": r.bind_source,
                    "latest_score": float(latest.score) if latest and latest.score is not None else None,
                    "latest_level": latest_level,
                    "latest_report_at": latest.generated_at if latest else None,
                    "execution_rate": execution_rate,
                    "last_consult_at": last_consult,
                    "height_cm": prof.height_cm if prof else None,
                    "weight_kg": float(prof.weight_kg) if prof and prof.weight_kg else None,
                    "diet_preference": prof.diet_preference if prof else None,
                })
            return {"items": items, "total": len(items)}

    @staticmethod
    def overview(user: dict) -> Dict[str, Any]:
        """营养师工作台 KPI（原型：管辖客户 / 高风险待跟进 / 今日咨询 / 满意度）。"""
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可查看工作台概览")
        uid = str(user.get("sub"))
        with session_scope() as s:
            rels = DietitianDAO.list_customers(uid, session=s)
            high_risk = 0
            for r in rels:
                rep = ReportDAO.list_by_user(r.customer_id, limit=1, session=s)
                if rep and rep[0].level in ("needs_improvement",):
                    high_risk += 1
            consults = ConsultationDAO.list_for_dietitian(uid, limit=200, session=s)
            today = datetime.now().date()
            today_count = sum(1 for c in consults
                              if c.created_at and c.created_at.date() == today)
            rated = [c.rating for c in consults if c.rating]
        return {
            "customer_count": len(rels),
            "high_risk_count": high_risk,
            "today_consult_count": today_count,
            "satisfaction": (round(sum(rated) / len(rated) / 5 * 100) if rated else None),
            "rating_count": len(rated),
        }

    @staticmethod
    def export_customers(user: dict, fmt: str = "csv") -> Dict[str, Any]:
        """导出客户报表（原型：营养师工作台「导出客户报表」）；产物入 MinIO 并返回签名 URL。"""
        import time

        from app.utils.exporter import store_bytes, to_csv_bytes, to_xlsx_bytes

        fmt = (fmt or "csv").lower()
        if fmt not in ("csv", "xlsx"):
            raise BizError(ErrCode.PARAM_INVALID, "导出格式需为 csv / xlsx")
        data = DietitianService.customers(user)
        headers = ["客户ID", "标签", "最新评分", "等级", "执行率(%)", "最近咨询",
                   "绑定来源", "绑定时间"]
        rows = [{
            "客户ID": i.get("customer_id"),
            "标签": "、".join(i.get("tags") or []),
            "最新评分": i.get("latest_score"),
            "等级": i.get("latest_level"),
            "执行率(%)": i.get("execution_rate"),
            "最近咨询": i.get("last_consult_at"),
            "绑定来源": i.get("bind_source"),
            "绑定时间": i.get("bound_at"),
        } for i in data["items"]]
        payload = (to_csv_bytes(rows, headers) if fmt == "csv"
                   else to_xlsx_bytes(rows, headers, "客户报表"))
        name = f"{str(user.get('sub'))}_{int(time.time())}.{fmt}"
        url = store_bytes(
            payload, f"exports/customers_{name}",
            "text/csv" if fmt == "csv" else
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return {"total": len(rows), "format": fmt, "url": url, "size": len(payload)}

    @staticmethod
    def customer_reports(user: dict, customer_uid: str, limit: int = 50) -> Dict[str, Any]:
        """查看已绑定客户的测评报告（未绑定则拒绝，防越权）。"""
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可查看客户报告")
        with session_scope() as s:
            from app.models.mysql.platform import DietitianCustomer
            # 校验：该客户当前绑定的营养师必须是本人（管理员可看全部）
            bind = (s.query(DietitianCustomer)
                    .filter_by(customer_id=str(customer_uid), status="active").first())
            if not bind or (str(bind.dietitian_id) != str(user.get("sub"))
                            and user.get("role") != "admin"):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "客户不存在或未绑定给你")
            reports = ReportDAO.list_by_user(customer_uid, limit=limit, session=s)
            return {
                "customer_id": customer_uid,
                "items": [{
                    "report_id": r.id,
                    "assessment_id": r.record_id,
                    "score": float(r.score) if r.score is not None else None,
                    "level": r.level,
                    "state": r.state,
                    "generated_at": r.generated_at,
                } for r in reports],
                "total": len(reports),
            }


dietitian_service = DietitianService()
