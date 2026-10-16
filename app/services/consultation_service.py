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
from app.core.refusal import check_refusal, refusal_message
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
                    "ai_assisted": bool(m.ai_assisted),
                    "created_at": m.created_at,
                } for m in msgs],
                "total": len(msgs),
            }

    @staticmethod
    def send(user: dict, consultation_id: int, content: str,
             mtype: str = "text", ai_assisted: bool = False) -> Dict[str, Any]:
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
            # 来源标记只对营养师发出的消息有意义：客户侧不存在"AI 辅助建议"这个场景，
            # 若照抄客户端传入的 true，会把客户消息误标成 AI 生成，污染后续统计与回溯。
            assisted = bool(ai_assisted) and sender == "dietitian"
            msg = ConsultationDAO.add_message(consultation_id, sender, content.strip(),
                                              session=s, ai_assisted=assisted)
            return {"id": msg.id, "consultation_id": consultation_id,
                    "sender_type": msg.sender_type, "ai_assisted": assisted,
                    "created_at": msg.created_at}

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
        """该用户是否还有未结束的人工咨询（IT-C-13/14：转人工后入口提示、结束后恢复）。

        注意状态取值：`consultation_status` 的合法域是 pending/active/closed/timeout。
        这里原先写的是 `in_progress`——那是 `assessment_status` 的取值，**本表没有这个值**，
        于是"已接入（active）"的咨询被判为"没有未结束的咨询"，
        用户可以重复发起，与 IT-C-13 的预期相反。
        """
        if not user_id:
            return False
        with session_scope() as s:
            return (s.query(Consultation)
                    .filter(Consultation.user_id == str(user_id),
                            Consultation.status.in_(("pending", "active")))
                    .first()) is not None

    # ------------------------------------------------------------------ #
    # AI 咨询辅助建议（营养师故事 4）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _suggest_context(user: dict, consultation_id: int) -> Dict[str, Any]:
        """取生成建议所需的上下文（同步 DB 读取，调用方负责挪出事件循环）。

        返回 {consultation_id, customer_id, status, dialog, last_question, query, health}。
        权限口径与查看消息一致：仅咨询双方与管理员；未接入（pending）时营养师还没接管，
        此时无从"给这位客户的这次咨询"写建议，如实拒绝而不是凭空生成。
        """
        with session_scope() as s:
            _sweep_timeout(s)
            con = ConsultationDAO.get(consultation_id, session=s)
            _assert_party(con, user)
            if con.status in ("closed", "timeout"):
                raise BizError(ErrCode.PARAM_INVALID, "咨询已结束，无法生成辅助建议")
            if con.status != "active":
                raise BizError(ErrCode.PARAM_INVALID, "请先接入该咨询，再生成辅助建议")
            msgs = ConsultationDAO.list_messages(consultation_id, limit=200, session=s)
            dialog = [{"sender_type": m.sender_type, "content": m.content}
                      for m in msgs][-rules.SUGGEST_CONTEXT_MESSAGES:]
            customer_id, status = str(con.user_id), con.status

        # 客户提问：取最后一条用户消息（营养师的问题描述不能当作客户诉求）
        questions = [m["content"] for m in dialog if m["sender_type"] == "user"]
        if not questions:
            raise BizError(ErrCode.PARAM_INVALID, "该咨询还没有客户的提问，暂无可辅助的内容")
        last_question = questions[-1]

        # 健康档案（体质/重点风险/忌口/疾病史）—— 用**客户**的身份取，不是营养师自己的
        from app.services.dashboard_service import DashboardService

        health = DashboardService.health_context({"sub": customer_id, "role": "user"})

        # 检索词：客户本次提问 + 咨询里出现过的问题，避免只拿最后一句（可能只是"好的"）
        query = " ".join(dict.fromkeys(questions))[:200] or last_question
        return {"consultation_id": consultation_id, "customer_id": customer_id,
                "status": status, "dialog": dialog, "last_question": last_question,
                "query": query, "health": health}

    @staticmethod
    def _build_suggest_prompt(ctx: Dict[str, Any], docs: List[Dict], hint: str) -> tuple:
        """组装 (system_text, user_text)。"""
        health = ctx["health"]
        profile_lines = [
            f"- 体质：{health.get('constitution') or '未测评'}",
            f"- 重点风险：{'、'.join(health.get('key_risks') or []) or '暂无'}",
            f"- 过敏/忌口：{health.get('allergies') or '未填写'}",
            f"- 疾病史：{'、'.join(health.get('diseases') or []) or '未填写'}",
            f"- 身高体重：{health.get('height_cm') or '—'} cm / {health.get('weight_kg') or '—'} kg"
            f"（BMI {health.get('bmi') or '—'}）",
        ]
        dialog_lines = []
        for m in ctx["dialog"]:
            who = {"user": "客户", "dietitian": "营养师", "system": "系统"}.get(m["sender_type"], "其他")
            dialog_lines.append(f"{who}：{m['content']}")
        context = "\n".join(f"【{d['title']}】\n{d['fragment']}" for d in docs)

        system_text = (
            "你是「AI 智参」平台给**营养师**用的工作助手。你产出的是一份**建议草稿**，"
            "不是直接发给客户的答复——营养师会自行修改后再决定是否发送。\n"
            "写作规则：\n"
            "1. 只依据【客户健康档案】【咨询对话】【知识库片段】写；资料没有的不要编造。\n"
            f"2. 分点输出（每点一行，最多 5 点），先给结论；全文不超过 {rules.SUGGEST_MAX_CHARS} 字。\n"
            "3. 不给诊断结论、不给药物与剂量建议；若客户诉求涉及用药、诊断或报告解读，"
            "明确写出「建议转执业医师评估」，不要尝试自行回答。\n"
            "4. 语言口语化、可直接发给客户；结合客户的忌口与疾病史给出可操作的做法。\n"
            "5. 直接输出正文，不要出现「建议草稿：」「营养师：」等模板字样，不要复述问题。"
        )
        user_text = (
            f"【客户健康档案】\n" + "\n".join(profile_lines)
            + f"\n\n【咨询对话（最近 {len(dialog_lines)} 条）】\n" + "\n".join(dialog_lines)
            + f"\n\n【知识库片段】\n" + (context or "（未检索到相关内容）")
            + (f"\n\n【营养师补充要点】\n{hint.strip()}" if hint and hint.strip() else "")
            + "\n\n请给出这份建议草稿。"
        )
        return system_text, user_text

    @staticmethod
    async def ai_suggestion(user: dict, consultation_id: int, hint: str = "") -> Dict[str, Any]:
        """生成 AI 咨询辅助建议草稿（营养师故事 4）。

        边界（与需求一致，不是实现取舍）：
          · **只给营养师**：产出是草稿，必须由营养师修改后经正常发消息接口发送；
          · **拒答优先**：客户诉求命中用药/诊断/报告解读/孕婴等高风险类别时，
            直接返回就医指引，**不调用模型**（与问答链路同一套策略）；
          · **可溯源**：返回的知识库引用带 doc_id（Milvus 主键），营养师能看到依据；
          · **如实降级**：模型不可用就用检索片段兜底并标记 state=basic，
            绝不编一段"看起来像建议"的文字冒充模型输出。
        """
        import asyncio
        import time

        from app.services.chat_query_service import chat_query_service

        t0 = time.perf_counter()

        def _elapsed() -> int:
            return int((time.perf_counter() - t0) * 1000)

        ctx = await asyncio.to_thread(ConsultationService._suggest_context, user, consultation_id)

        # 1) 合规拒答：命中即不调模型（与 ChatQueryService.stream 同一策略与话术）
        refused, reason = check_refusal(ctx["last_question"])
        if refused:
            return {
                "consultation_id": consultation_id, "state": "refused", "refused": True,
                "reason": reason, "suggestion": refusal_message(reason),
                "references": [], "elapsed_ms": _elapsed(),
                "hint": "该诉求属高风险类别，请按话术引导客户就医，平台不生成用药/诊断建议。",
            }

        # 2) 检索知识库（带阈值；同步阻塞，挪出事件循环）
        docs = await asyncio.to_thread(chat_query_service.retrieve, ctx["query"])
        references = [{"doc_id": d.get("doc_id"), "title": d.get("title"),
                       "fragment": d.get("fragment"), "source": d.get("source") or ""}
                      for d in docs]

        # 3) 生成草稿
        system_text, user_text = ConsultationService._build_suggest_prompt(ctx, docs, hint)
        text = await chat_query_service.complete(system_text, user_text)

        if text:
            return {
                "consultation_id": consultation_id, "state": "ok", "refused": False,
                "reason": "", "suggestion": text, "references": references,
                "elapsed_ms": _elapsed(), "edited_required": True,
                "hint": "草稿仅供参考，请修改确认后再发送给客户。",
            }

        # 4) 降级：模型不可用 → 给检索片段，明确标注来源与状态（不伪造生成结果）
        if docs:
            basic = "（模型暂不可用，以下为知识库检索片段，请自行整理成建议）\n" + "\n".join(
                f"- {d['title']}：{d['fragment']}" for d in docs)
        else:
            basic = ""
        return {
            "consultation_id": consultation_id,
            "state": "basic", "refused": False, "reason": "",
            "suggestion": basic, "references": references, "elapsed_ms": _elapsed(),
            "edited_required": True,
            "hint": "模型服务暂不可用，已退回知识库片段；请勿把未经整理的内容直接发给客户。"
                    if docs else
                    "模型服务与知识库均暂不可用，请稍后重试或自行回复客户。",
        }

    @staticmethod
    def transfer_from_chat(user: dict, session_id: int, question: str = "") -> Dict[str, Any]:
        """从智能会话转人工（G 组 → H 组）。"""
        return ConsultationService.create(user, question=question or "用户请求转人工",
                                         ctype="human", session_id=session_id)


consultation_service = ConsultationService()
