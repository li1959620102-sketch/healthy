# -*- coding: utf-8 -*-
"""H 组 · 人工咨询服务（1v1 咨询 / 转人工 / 满意度）。

状态机：pending（待接入）→ active（进行中）→ closed / timeout
兜底（评审 M4）：转人工时若无营养师在线，仍创建 pending 咨询进入待接入队列，
并写入系统提示消息告知用户，不直接失败。
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import rules
from app.core.errors import BizError, ErrCode
from app.dao.consultation_dao import ConsultationDAO
from app.dao.dietitian_dao import DietitianDAO
from app.dao.assessment_dao import session_scope
from app.models.mysql.platform import Consultation

logger = logging.getLogger("ConsultationService")


def _brief(c: Consultation) -> Dict[str, Any]:
    return {
        "consultation_id": c.id,
        "user_id": c.user_id,
        "dietitian_id": c.dietitian_id,
        "type": c.type,
        "status": c.status,
        "created_at": c.created_at,
        "accepted_at": c.accepted_at,
        "closed_at": c.closed_at,
        "rating": c.rating,
    }


def _assert_party(c, user: dict) -> None:
    if c is None:
        raise BizError(ErrCode.RESOURCE_NOT_FOUND, "咨询不存在")
    uid = str(user.get("sub"))
    if str(c.user_id) == uid or (c.dietitian_id and str(c.dietitian_id) == uid):
        return
    if user.get("role") == "admin":
        return
    raise BizError(ErrCode.RESOURCE_NOT_FOUND, "咨询不存在")


def _sweep_timeout(s) -> None:
    """BV-25：读取前把超过 30 分钟仍未接入的咨询懒置为 timeout。"""
    ConsultationDAO.expire_stale(minutes=rules.CONSULTATION_TIMEOUT_MINUTES, session=s)


class ConsultationService:
    @staticmethod
    def create(user: dict, question: str = "", ctype: str = "human",
               session_id: Optional[int] = None) -> Dict[str, Any]:
        """发起咨询；首条消息即用户的问题。"""
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            con = ConsultationDAO.create({
                "user_id": str(user.get("sub")),
                "type": ctype if ctype in ("ai", "human") else "human",
                "status": "pending",
                "transferred_from_session_id": session_id,
            }, session=s)
            if question:
                ConsultationDAO.add_message(con.id, "user", question, session=s)
            # 有已绑定营养师则直接指派，否则进入待接入队列（M4 兜底）
            rel = DietitianDAO.current_dietitian(str(user.get("sub")), session=s)
            if rel:
                ConsultationDAO.accept(con.id, rel.dietitian_id, session=s)
                ConsultationDAO.add_message(
                    con.id, "system", "已为你分配专属营养师，请稍候。", session=s)
            else:
                ConsultationDAO.add_message(
                    con.id, "system", "已进入待接入队列，营养师接入后将第一时间回复。", session=s)
            return _brief(con)

    @staticmethod
    def list_mine(user: dict, limit: int = 50) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            _sweep_timeout(s)
            if user.get("role") in ("nutritionist", "dietitian"):
                rows = ConsultationDAO.list_for_dietitian(str(user.get("sub")), limit=limit, session=s)
            else:
                rows = ConsultationDAO.list_for_user(str(user.get("sub")), limit=limit, session=s)
            return {"items": [_brief(r) for r in rows], "total": len(rows)}

    @staticmethod
    def pending_queue(user: dict, limit: int = 50) -> Dict[str, Any]:
        """待接入队列（营养师/管理员查看）。"""
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可查看待接入队列")
        with session_scope() as s:
            _sweep_timeout(s)
            rows = ConsultationDAO.list_pending(limit=limit, session=s)
            return {"items": [_brief(r) for r in rows], "total": len(rows)}

    @staticmethod
    def accept(user: dict, consultation_id: int) -> Dict[str, Any]:
        if not user or user.get("role") not in ("nutritionist", "dietitian", "admin"):
            raise BizError(ErrCode.PERMISSION_DENIED, "仅营养师可接入咨询")
        with session_scope() as s:
            con = ConsultationDAO.accept(consultation_id, str(user.get("sub")), session=s)
            if not con:
                raise BizError(ErrCode.PARAM_INVALID, "咨询不存在或已被接入")
            ConsultationDAO.add_message(consultation_id, "system", "营养师已接入。", session=s)
            return _brief(con)

    @staticmethod
    def messages(user: dict, consultation_id: int, limit: int = 200) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            _sweep_timeout(s)
            con = ConsultationDAO.get(consultation_id, session=s)
            _assert_party(con, user)
            msgs = ConsultationDAO.list_messages(consultation_id, limit=limit, session=s)
            return {
                "consultation_id": consultation_id,
                "items": [{
                    "id": m.id,
                    "sender_type": m.sender_type,
                    "content": m.content,
                    "created_at": m.created_at,
                } for m in msgs],
                "total": len(msgs),
            }

    @staticmethod
    def send(user: dict, consultation_id: int, content: str,
             mtype: str = "text") -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        # IT-CS-08（一期范围）：图片/语音/视频消息一期不支持，明确拒绝并给替代方案。
        # 若静默按文本存下来，前端会以为发成功，营养师却只看到一串空内容。
        if mtype and mtype != "text":
            raise BizError(ErrCode.PARAM_INVALID,
                           "一期暂不支持图片/语音消息，请用文字描述（如食材名、症状与饮食情况）")
        if not content or not content.strip():
            raise BizError(ErrCode.PARAM_INVALID, "消息内容不能为空")
        with session_scope() as s:
            _sweep_timeout(s)
            con = ConsultationDAO.get(consultation_id, session=s)
            _assert_party(con, user)
            if con.status in ("closed", "timeout"):
                raise BizError(ErrCode.PARAM_INVALID, "咨询已结束，无法发送消息")
            sender = "dietitian" if (con.dietitian_id
                                     and str(con.dietitian_id) == str(user.get("sub"))) else "user"
            msg = ConsultationDAO.add_message(consultation_id, sender, content.strip(), session=s)
            return {"id": msg.id, "consultation_id": consultation_id,
                    "sender_type": msg.sender_type, "created_at": msg.created_at}

    @staticmethod
    def close(user: dict, consultation_id: int, reason: str = "会话结束") -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            con = ConsultationDAO.get(consultation_id, session=s)
            _assert_party(con, user)
            updated = ConsultationDAO.close(consultation_id, reason, session=s)
            if not updated:
                raise BizError(ErrCode.PARAM_INVALID, "咨询已结束")
            # 咨询完成后自动绑定营养师（需求：绑定来源之一）
            if updated.dietitian_id:
                from app.services.dietitian_service import DietitianService
                DietitianService.auto_bind_after_consultation(
                    updated.dietitian_id, updated.user_id, session=s)
            return _brief(updated)

    @staticmethod
    def rate(user: dict, consultation_id: int, rating: int, comment: str = "") -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        if not isinstance(rating, int) or not (1 <= rating <= 5):
            raise BizError(ErrCode.PARAM_INVALID, "评分需为 1-5 的整数")
        with session_scope() as s:
            con = ConsultationDAO.get(consultation_id, session=s)
            if con is None or str(con.user_id) != str(user.get("sub")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "咨询不存在")
            # IT-CS-06：满意度只能提交一次。原先的实现是直接覆盖，
            # 用户误点或重复提交会把已提交的评价静默改掉，运营侧也无法判断哪次是真实反馈。
            if con.rating is not None:
                raise BizError(ErrCode.PARAM_INVALID,
                               f"本次咨询已评价过（{con.rating} 星），不能重复评价")
            updated = ConsultationDAO.rate(consultation_id, rating, comment, session=s)
            return {"consultation_id": consultation_id, "rating": updated.rating}

    @staticmethod
    def has_open(user_id: str) -> bool:
        """该用户是否还有未结束的人工咨询（IT-C-13/14：转人工后入口提示、结束后恢复）。"""
        if not user_id:
            return False
        with session_scope() as s:
            return (s.query(Consultation)
                    .filter(Consultation.user_id == str(user_id),
                            Consultation.status.in_(("pending", "in_progress")))
                    .first()) is not None

    @staticmethod
    def transfer_from_chat(user: dict, session_id: int, question: str = "") -> Dict[str, Any]:
        """从智能会话转人工（G 组 → H 组）。"""
        return ConsultationService.create(user, question=question or "用户请求转人工",
                                         ctype="human", session_id=session_id)


consultation_service = ConsultationService()
