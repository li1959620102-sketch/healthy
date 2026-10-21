# -*- coding: utf-8 -*-
"""G 组 · 智能查询路由（SSE 流式，依据设计_01 §二 G / §3.5）。

解决设计评审 B1：浏览器 EventSource 无法带 Authorization 头 → 提供 GET /chat/query/stream
配合 POST /auth/stream-ticket 换取的 60 秒短票（?ticket=）。

事件格式（落地设计 §1.5）：
  event: start | delta | reference | done | error
鉴权：GET 走短票；POST 走 Bearer（游客可匿名，权限受限）。
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Query
from fastapi.responses import StreamingResponse

from app.services.chat_query_service import chat_query_service
from app.core import rules
from app.core.errors import ErrCode, BizError
from app.core.deps import get_optional_user, get_current_user
from app.core.responses import success
from app.utils.security import decode_token
from app.utils.db import get_session
from app.models.mysql.platform import ChatSession, ChatMessage
from app.agent_runtime.memory import memory_manager

logger = logging.getLogger("chat_router")
router = APIRouter(prefix="/chat", tags=["智能查询"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resolve_sse_user(request: Request) -> Optional[dict]:
    """优先短票（?ticket=），回退 Bearer；游客返回 None。"""
    ticket = request.query_params.get("ticket")
    if ticket:
        p = decode_token(ticket)
        if p and p.get("type") == "stream":
            return p
        raise BizError(ErrCode.TOKEN_INVALID, "短票无效或已过期")
    auth = request.headers.get("Authorization")
    if auth:
        return get_optional_user(auth)
    return None


def _guest_hash(authorization: Optional[str]) -> Optional[str]:
    """游客令牌 → 归属哈希；非游客（登录用户/匿名）返回 None。"""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    payload = decode_token(token)
    if not payload or payload.get("role") != "guest":
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:64]


def _guest_quota_used(guest_hash: str) -> int:
    session = get_session()
    try:
        return session.query(ChatSession).filter_by(guest_token=guest_hash).count()
    finally:
        session.close()


def _prepare_session(user: Optional[dict], guest_hash: Optional[str], question: str,
                     session_id: Optional[int] = None):
    """取（或建）会话并返回历史消息，供多轮上下文使用。

    返回 (session_id, history)：history 由旧到新，最多 rules.CHAT_HISTORY_TURNS*2 条。
    归属校验：只能续接"自己的"会话（登录用户按 user_id，游客按 guest_token）。
    任何异常都不阻断问答，退化为"无历史"。
    """
    try:
        s = get_session()
        user_id = user.get("sub") if user else None
        sess = None
        if session_id:
            try:
                sess = s.query(ChatSession).filter(ChatSession.id == int(session_id)).first()
            except (TypeError, ValueError):
                sess = None
            if sess is not None:
                same_owner = ((user_id and sess.user_id == user_id) or
                              (not user_id and guest_hash and sess.guest_token == guest_hash))
                if not same_owner:
                    sess = None          # 不是自己的会话 → 视为新会话
        if sess is None:
            sess = ChatSession(user_id=user_id, guest_token=guest_hash, title=(question or "")[:50])
            s.add(sess)
            s.flush()
        limit = max(1, int(getattr(rules, "CHAT_HISTORY_TURNS", 3))) * 2
        rows = (s.query(ChatMessage).filter(ChatMessage.session_id == sess.id)
                .order_by(ChatMessage.id.desc()).limit(limit).all())
        history = [{"role": m.role, "content": m.content} for m in reversed(rows)]
        sid = sess.id
        s.commit()
        s.close()
        return sid, history
    except Exception as e:
        logger.warning(f"读取会话历史失败（继续无历史问答）：{e}")
        return None, []


def _persist(session_id: Optional[int], user: Optional[dict], guest_hash: Optional[str],
             question: str, answer_chunks: list, refused: bool, reason: str):
    """最佳努力落库：续接既有会话则追加消息，否则新建会话；失败仅记日志，不阻断流式。"""
    try:
        session = get_session()
        sess = None
        if session_id:
            try:
                sess = session.query(ChatSession).filter(ChatSession.id == int(session_id)).first()
            except (TypeError, ValueError):
                sess = None
        if sess is None:
            user_id = user.get("sub") if user else None
            sess = ChatSession(user_id=user_id, guest_token=guest_hash, title=(question or "")[:50])
            session.add(sess)
            session.flush()
        session.add(ChatMessage(session_id=sess.id, role="user", content=question))
        full = "".join(answer_chunks)
        session.add(ChatMessage(
            session_id=sess.id, role="assistant", content=full,
            refused=1 if refused else 0, refuse_reason=reason or None,
        ))
        sess.message_count = int(sess.message_count or 0) + 2
        sess.last_msg_at = datetime.now()
        session.commit()
        session.close()
    except Exception as e:
        logger.warning(f"会话落库失败（不影响流式返回）：{e}")


@router.get("/kb-info", summary="知识库信息",
            description="营养师智能体右栏展示：检索集合名、向量条数、相似度阈值。")
async def kb_info(_: dict = Depends(get_current_user)):
    from app.config import settings
    entities = None

    def _read_entities():
        """同步读取向量库统计（pymilvus 是阻塞调用）。"""
        chat_query_service._ensure_components()
        col = chat_query_service._collection
        return col.num_entities if col is not None else None

    try:
        # 挪到线程池执行：向量库不可达时最多拖住一个线程，不会冻结整个事件循环
        entities = await asyncio.to_thread(_read_entities)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取知识库统计失败：{e}")
    return success(data={
        "collection": settings.MILVUS_DEFAULT_COLLECTION,
        "entities": entities,
        "min_similarity": float(getattr(settings, "KB_MIN_SIMILARITY", 0.55)),
    })


@router.post("/query", summary="智能查询（SSE）", description="营养师智能体问答，流式返回。Bearer 鉴权，游客可匿名（有免费次数上限）。")
async def chat_query(body: dict, user: Optional[dict] = Depends(get_optional_user),
                     authorization: Optional[str] = Header(None, alias="Authorization")):
    question = (body or {}).get("question", "")
    agent = (body or {}).get("agent", "dietitian")
    sid = (body or {}).get("session_id")
    return _stream_response(question, agent, user, _guest_hash(authorization), sid)


@router.get("/query/stream", summary="智能查询流式通道（EventSource）", description="供前端 EventSource 使用：?ticket=短票&question=问题&agent=。")
async def chat_query_stream(
    request: Request,
    question: str = Query(..., description="用户问题"),
    agent: str = Query("dietitian", description="智能体 dietitian/assessment/data/dish"),
    session_id: Optional[int] = Query(None, description="续接的会话 ID（不传则新建会话）"),
):
    user = _resolve_sse_user(request)
    return _stream_response(question, agent, user,
                            _guest_hash(request.headers.get("Authorization")), session_id)


def _stream_response(question: str, agent: str, user: Optional[dict],
                     guest_hash: Optional[str] = None,
                     session_id: Optional[int] = None):
    if not question or not question.strip():
        raise BizError(ErrCode.PARAM_INVALID, "问题不能为空")
    if not user and not guest_hash:
        raise BizError(ErrCode.UNAUTHENTICATED, "请登录或先获取游客令牌")

    # BV-21 游客配额：游客免费查询有次数上限，达上限引导注册
    if guest_hash and _guest_quota_used(guest_hash) >= rules.GUEST_QUERY_QUOTA:
        raise BizError(ErrCode.QUERY_RATE_LIMITED,
                       "游客免费查询次数已用完，注册后可继续咨询")

    # 会话续接：取（或建）会话 + 历史上下文（异常时退化为单轮问答）
    sid, history = _prepare_session(user, guest_hash, question, session_id)

    # IT-C-13/14：用户是否正处于人工咨询中。转人工后智能入口给出"人工处理中"提示，
    # 咨询结束（closed/timeout）后自动恢复为 False——前端据此暂停/恢复智能入口。
    human_pending = False
    if user:
        try:
            from app.services.consultation_service import consultation_service
            human_pending = consultation_service.has_open(str(user.get("sub")))
        except Exception as e:                      # noqa: BLE001
            logger.warning(f"查询人工咨询状态失败（按无人工处理）：{e}")

    async def event_gen():
        chunks = []
        refused = False
        reason = ""
        memory_context = memory_manager.context_text(
            user_id=str((user or {}).get("sub")) if user else None,
            agent_key=agent,
            limit=8,
        )
        async for ev in chat_query_service.stream(
                question, agent, history=history, memory_context=memory_context):
            if ev["event"] == "start":
                ev["data"] = dict(ev["data"] or {}, session_id=sid,
                                  has_history=bool(history), human_pending=human_pending)
            if ev["event"] == "done":
                refused = ev["data"].get("refused", False)
                reason = ev["data"].get("reason", "")
            if ev["event"] in ("delta",):
                chunks.append(ev["data"].get("text", ""))
            yield _sse(ev["event"], ev["data"])
        _persist(sid, user, guest_hash, question, chunks, refused, reason)
        if user and chunks and not refused:
            memory_manager.remember(
                agent, "conversation",
                {"session_id": sid, "question": question[:500],
                 "answer_preview": "".join(chunks)[:1000]},
                user_id=str(user.get("sub")), task_id=None,
                source="chat_session", ttl_seconds=30 * 24 * 3600,
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get("/sessions", summary="智能会话列表",
            description="登录用户的智能问答会话列表（按最近活动倒序），供历史入口展示。")
async def list_sessions(limit: int = Query(20, ge=1, le=100),
                        user: dict = Depends(get_current_user)):
    s = get_session()
    try:
        rows = (s.query(ChatSession).filter(ChatSession.user_id == str(user.get("sub")))
                .order_by(ChatSession.last_msg_at.desc(), ChatSession.id.desc())
                .limit(limit).all())
        return success(data={"items": [{
            "session_id": r.id,
            "title": r.title,
            "message_count": r.message_count,
            "last_msg_at": r.last_msg_at,
        } for r in rows], "total": len(rows)})
    finally:
        s.close()


@router.get("/sessions/latest", summary="最新会话与历史消息",
            description="进入智能体页面时恢复多轮上下文：返回最近一次会话 ID 与最近的消息列表。")
async def latest_session(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    try:
        s = get_session()
        sess = (s.query(ChatSession).filter(ChatSession.user_id == user_id)
                .order_by(ChatSession.last_msg_at.desc(), ChatSession.id.desc()).first())
        if not sess:
            s.close()
            return success(data={"session_id": None, "messages": []})
        limit = max(1, int(getattr(rules, "CHAT_HISTORY_TURNS", 3))) * 2
        rows = (s.query(ChatMessage).filter(ChatMessage.session_id == sess.id)
                .order_by(ChatMessage.id.desc()).limit(limit).all())
        msgs = [{"role": m.role, "content": m.content,
                 "created_at": m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else None}
                for m in reversed(rows)]
        data = {"session_id": sess.id, "title": sess.title, "messages": msgs}
        s.close()
        return success(data=data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取最新会话失败：{e}")
        return success(data={"session_id": None, "messages": []})


@router.get("/sessions/{session_id}/messages", summary="会话历史消息",
            description="按时间正序返回该会话的完整消息（用户/助手交替）；仅会话本人可见，非本人按 404 处理。")
async def session_messages(session_id: int, limit: int = Query(200, ge=1, le=1000),
                           user: dict = Depends(get_current_user)):
    s = get_session()
    try:
        sess = s.query(ChatSession).filter(ChatSession.id == session_id).first()
        # 与报告/咨询一致：不存在与无权限共用 404，避免通过响应差异枚举他人会话
        if sess is None or (sess.user_id and str(sess.user_id) != str(user.get("sub"))):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "会话不存在")
        rows = (s.query(ChatMessage).filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.asc()).limit(limit).all())
        return success(data={
            "session_id": session_id,
            "title": sess.title,
            "items": [{"id": m.id, "role": m.role, "content": m.content,
                       "refused": bool(m.refused), "created_at": m.created_at} for m in rows],
            "total": len(rows),
        })
    finally:
        s.close()


@router.post("/sessions/{session_id}/transfer-human", summary="转人工",
             description="把当前智能会话转成人工咨询单，携带来源会话 ID 以便营养师看到上下文（设计_01 §G）。")
async def transfer_human(session_id: int, body: Optional[dict] = None,
                         user: dict = Depends(get_current_user)):
    s = get_session()
    try:
        sess = s.query(ChatSession).filter(ChatSession.id == session_id).first()
        if sess is None or (sess.user_id and str(sess.user_id) != str(user.get("sub"))):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "会话不存在")
        # 用会话最后一条用户提问作为咨询单的首条消息，人工侧无需用户重复描述
        last_user = (s.query(ChatMessage)
                     .filter(ChatMessage.session_id == session_id, ChatMessage.role == "user")
                     .order_by(ChatMessage.id.desc()).first())
        question = (body or {}).get("question") or (last_user.content if last_user else "")
    finally:
        s.close()

    from app.services.consultation_service import consultation_service
    data = consultation_service.transfer_from_chat(user, session_id, question)
    return success(data=data, message="已为你转接人工营养师，请稍候")
