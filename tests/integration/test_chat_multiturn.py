# -*- coding: utf-8 -*-
"""多轮对话：会话续接 + 历史回传 + 会话归属隔离（2026-09-11 新增）。

做法：把 `chat_query_service.stream` 打桩（避免真实调用付费大模型），
只验证路由层的会话/历史管线是否正确。落库与查询走真实 MySQL。
"""
import json

import pytest


def _sid_from_sse(text: str):
    """从 SSE 文本里取出 start 事件的 session_id。"""
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("event:"):
            cur = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and cur == "start":
            data = json.loads(line[5:].strip())
            return data.get("session_id")
    return None


def _delete_messages(session, sid):
    from sqlalchemy import text
    session.execute(text("DELETE FROM chat_message WHERE session_id=:s"), {"s": sid})


def _delete_session(session, sid):
    from sqlalchemy import text
    session.execute(text("DELETE FROM chat_session WHERE id=:s"), {"s": sid})


@pytest.fixture
def stub_stream(monkeypatch):
    """把流式问答打桩，记录每次调用收到的 history。"""
    from app.services.chat_query_service import chat_query_service

    captured = []

    async def fake_stream(question, agent="dietitian", history=None, memory_context=None):
        captured.append({"question": question, "history": list(history or [])})
        yield {"event": "start", "data": {"message_id": None}}
        yield {"event": "delta", "data": {"text": "好的，我来说说。"}}
        yield {"event": "done", "data": {"elapsed_ms": 0}}

    monkeypatch.setattr(chat_query_service, "stream", fake_stream)
    return captured


def test_multiturn_second_question_carries_history(client, auth_factory, run_id,
                                                   cleanup, stub_stream):
    uid = f"{run_id}_mt1"
    headers = auth_factory(uid, "user")

    # 第一问：不带 session_id → 新建会话，历史为空
    r1 = client.post("/api/v1/chat/query", headers=headers, json={"question": "高血压怎么吃"})
    assert r1.status_code == 200
    sid = _sid_from_sse(r1.text)
    assert sid, r1.text[:300]
    cleanup.append(lambda s, _sid=sid: _delete_session(s, _sid))
    cleanup.append(lambda s, _sid=sid: _delete_messages(s, _sid))

    # 第二问：带 session_id → 续接同一会话，并把上一轮问答作为历史回传
    r2 = client.post("/api/v1/chat/query", headers=headers,
                     json={"question": "那盐呢", "session_id": sid})
    assert r2.status_code == 200
    assert _sid_from_sse(r2.text) == sid, "应续接同一个会话"

    assert stub_stream[0]["history"] == [], "首问不应带历史"
    hist = stub_stream[1]["history"]
    assert [h["role"] for h in hist] == ["user", "assistant"], hist
    assert "高血压" in hist[0]["content"]
    assert "好的" in hist[1]["content"]

    # 落库：同一会话累计 4 条消息（2 轮问答）
    from app.utils.db import get_session
    from sqlalchemy import text
    s = get_session()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM chat_message WHERE session_id=:s"),
                      {"s": sid}).scalar()
        cnt = s.execute(text("SELECT message_count FROM chat_session WHERE id=:s"),
                        {"s": sid}).scalar()
    finally:
        s.close()
    assert n == 4, f"同一会话应有 4 条消息，实际 {n}"
    assert cnt == 4, f"message_count 应为 4，实际 {cnt}"


def test_foreign_session_id_isolated(client, auth_factory, run_id, cleanup, stub_stream):
    """传别人的 session_id 不能读别人的历史：应新建会话（不越权）。"""
    a = auth_factory(f"{run_id}_mta", "user")
    b = auth_factory(f"{run_id}_mtb", "user")

    ra = client.post("/api/v1/chat/query", headers=a, json={"question": "糖尿病主食怎么选"})
    sid_a = _sid_from_sse(ra.text)
    assert sid_a
    cleanup.append(lambda s, _sid=sid_a: _delete_session(s, _sid))
    cleanup.append(lambda s, _sid=sid_a: _delete_messages(s, _sid))

    rb = client.post("/api/v1/chat/query", headers=b,
                     json={"question": "痛风能吃豆腐吗", "session_id": sid_a})
    sid_b = _sid_from_sse(rb.text)
    assert sid_b and sid_b != sid_a, "跨用户续接必须被拒（应新建自己的会话）"
    cleanup.append(lambda s, _sid=sid_b: _delete_messages(s, _sid))
    cleanup.append(lambda s, _sid=sid_b: _delete_session(s, _sid))
    # B 的这次调用不应读到 A 的历史
    assert stub_stream[-1]["history"] == [], stub_stream[-1]["history"]


def test_latest_session_restores_messages(client, auth_factory, run_id, cleanup, stub_stream):
    uid = f"{run_id}_mtc"
    headers = auth_factory(uid, "user")
    r = client.post("/api/v1/chat/query", headers=headers, json={"question": "减重期间主食怎么吃"})
    sid = _sid_from_sse(r.text)
    assert sid
    cleanup.append(lambda s, _sid=sid: _delete_messages(s, _sid))
    cleanup.append(lambda s, _sid=sid: _delete_session(s, _sid))

    d = client.get("/api/v1/chat/sessions/latest", headers=headers).json()
    assert d["code"] == 0, d
    assert d["data"]["session_id"] == sid
    roles = [m["role"] for m in d["data"]["messages"]]
    assert roles == ["user", "assistant"], d["data"]
    assert "减重" in d["data"]["messages"][0]["content"]


def test_followup_word_passed_to_retrieval(client, auth_factory, run_id, cleanup, stub_stream):
    """追问场景：路由只负责传历史；检索词增强由服务内 retrieval_query 完成（另有单元测试）。"""
    uid = f"{run_id}_mtd"
    headers = auth_factory(uid, "user")
    r1 = client.post("/api/v1/chat/query", headers=headers, json={"question": "高血压怎么吃"})
    sid = _sid_from_sse(r1.text)
    cleanup.append(lambda s, _sid=sid: _delete_session(s, _sid))
    cleanup.append(lambda s, _sid=sid: _delete_messages(s, _sid))
    client.post("/api/v1/chat/query", headers=headers,
                json={"question": "那盐呢", "session_id": sid})
    assert len(stub_stream) == 2
    assert stub_stream[1]["question"] == "那盐呢"
