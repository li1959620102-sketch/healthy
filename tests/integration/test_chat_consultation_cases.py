# -*- coding: utf-8 -*-
"""智能查询与人工咨询用例（IT-C 16 条 + IT-CS 7 条）。

IT-C-01~16 —— 首字节/完整时延 / 缓存命中 / 短输入 / 引用来源 / 无来源兜底 /
    四类合规拒答 / 多轮追问 / 会话列表与历史 / 转人工与恢复 / 并发排队反馈 / 模型异常；
IT-CS-01~04/06~08 —— 发起咨询 / 状态机 / 消息收发 / 非相关方 404 / 满意度 /
    结束咨询 / 图片消息一期拒绝。

口径说明（台账「备注」列同步）：
  · 时延类用例用**可控假模型 + 真实检索**测量流水线自身预算——模型侧 TTFB 取决于
    第三方服务商、波动不可控；真实模型端到端时延在 PERF-02 单独实测并记录。
  · "拒答/兜底不编造"用**模型调用次数 = 0** 来证明，比只看文案更硬。
  · 旧用例集列出的 `/chat/sessions`、`/chat/sessions/{id}/messages`、
    `/chat/sessions/{id}/transfer-human` 本次一并补齐（原设计_01 §G 标 ⏸）。
"""
import asyncio
import time

import pytest
from sqlalchemy import text

from app.core.errors import ErrCode

pytestmark = pytest.mark.integration

DISCLAIMER = "本内容由 AI 生成，不构成诊疗建议"


# =========================================================================== #
# 工具
# =========================================================================== #
class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """可控假模型：记录收到的 messages，按片返回固定文本，delay 模拟逐字吐字。"""

    def __init__(self, pieces=None, delay=0.0):
        self.pieces = pieces or ["高血压人群建议低盐饮食，每日食盐不超过 5 克。", DISCLAIMER]
        self.delay = delay
        self.calls = 0
        self.seen_messages = []

    def astream(self, messages):
        self.calls += 1
        self.seen_messages.append(messages)

        async def _gen():
            for i, p in enumerate(self.pieces):
                if self.delay and i:
                    await asyncio.sleep(self.delay)
                yield _Chunk(p)
        return _gen()


class _BrokenLLM:
    """始终抛错的假模型（对应"模型超时/上游异常"）。"""

    def __init__(self):
        self.calls = 0

    def astream(self, messages):
        self.calls += 1

        async def _gen():
            raise RuntimeError("上游模型超时（用例注入）")
            yield None                          # pragma: no cover
        return _gen()


@pytest.fixture(autouse=True)
def _isolated_answer_cache():
    """每个用例前后清空问答缓存：缓存挂在进程级单例上，否则用例之间会互相命中。"""
    from app.services.chat_query_service import chat_query_service as svc
    svc.cache_clear()
    yield
    svc.cache_clear()


def _use_llm(monkeypatch, llm):
    """替换智能体的模型。

    顺序很关键：`_ensure_components()` 只在 `_ready=False` 时构建组件并写入 `_llm`；
    若先替换模型再触发首次加载，真实模型对象会把假模型覆盖掉。
    """
    from app.services.chat_query_service import chat_query_service as svc
    svc._ensure_components()
    monkeypatch.setattr(svc, "_llm", llm)
    return svc


def _owner(api, tag: str):
    """造一名问答用户并登记会话清理（返回 {user_id, headers}）。"""
    u = api.synthetic_user(tag, "user")
    api.clean_chat(u["user_id"])
    return u


def _h(x):
    """兼容传入 headers 或 {user_id, headers}。"""
    return x["headers"] if isinstance(x, dict) and "headers" in x else x


def _chat(api, who, question, session_id=None, agent="dietitian"):
    body = {"question": question, "agent": agent}
    if session_id:
        body["session_id"] = session_id
    return api.post_sse("/api/v1/chat/query", _h(who), body)


def _text_of(events):
    return "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")


def _answer(api, who, question, session_id=None, **kw):
    """发一次问答，返回 (events, 答案文本, start 事件 data)。"""
    events = _chat(api, who, question, session_id=session_id, **kw)
    start = next((e["data"] for e in events if e["event"] == "start"), {}) or {}
    return events, _text_of(events), start


def _clean_consult(api, cid: int):
    api.cleanup.append(lambda s, i=cid: s.execute(
        text("DELETE FROM consultation_message WHERE consultation_id=:i"), {"i": i}))
    api.cleanup.append(lambda s, i=cid: s.execute(
        text("DELETE FROM consultation WHERE id=:i"), {"i": i}))


# =========================================================================== #
# IT-C-01/02 首字节与完整回答时延（流水线预算：真实检索 + 可控模型）
# =========================================================================== #
def test_it_c01_c02_query_latency(api, monkeypatch):
    """IT-C-01/02：首字节 P95 ≤1.5s、完整回答 P95 ≤8s。

    测量口径（台账「备注」列同此）：

    1) **必须先预热**。冷启动（Ollama 载入 bge-m3 + Milvus 加载 segment）实测约 2.4s，
       而同一次检索稳态只要 0.055s——差 40 倍。生产侧 `lifespan` 起线程调
       `chat_query_service.prewarm()`，所以真实用户不会吃到冷启动；
       用例不预热就测的是"重启后第一秒"，与被测行为不是同一件事。
       这里调**同一个** `prewarm()`，保证口径不分叉。
    2) **测的是保守上界**。TestClient 对 stream 响应会一次性取回全文（`r.text`），
       所以这里量到的"首字节"其实包含了整个响应。因此
       「完整响应 ≤1.5s」**必然蕴含**「真实首字节 ≤1.5s」——是充分条件，
       断言方向依旧成立，只是比真实 TTFB 更严。
    3) **模型侧用可控假模型**：真实模型 TTFB 取决于第三方服务商、波动不可控，
       拿它卡 P0 门槛会把"上游抖动"误判成本项目缺陷。真实模型端到端时延在 PERF-02 单独实测。
    """
    from app.services.chat_query_service import chat_query_service as svc

    svc.prewarm()
    assert svc.warm_state() == "warm", "预热后组件应处于就绪态"
    _use_llm(monkeypatch, _FakeLLM(
        pieces=["第 1 段。", "第 2 段。", "第 3 段。", DISCLAIMER], delay=0.05))
    owner = _owner(api, "c1")

    first_deltas, totals = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        events = _chat(api, owner, "高血压人群饮食注意什么")
        first = next((time.perf_counter() - t0 for e in events
                      if e["event"] == "delta"), None)
        first_deltas.append(first)
        totals.append(time.perf_counter() - t0)
        assert events and events[-1]["event"] == "done", events

    first_deltas.sort()
    totals.sort()
    assert all(x is not None for x in first_deltas), first_deltas
    assert first_deltas[-1] <= 1.5, f"首字节最慢 {first_deltas[-1]:.2f}s，超出 1.5s"
    assert totals[-1] <= 8.0, f"完整回答最慢 {totals[-1]:.2f}s，超出 8s"
    print(f"[IT-C-01/02] 预热后 首字节(上界) max={first_deltas[-1]:.3f}s "
          f"完整 max={totals[-1]:.3f}s")


# =========================================================================== #
# 就绪探针：冷启动窗口对运维可见（本轮新增字段，配套上方口径说明）
# =========================================================================== #
def test_it_c01b_ready_reports_agent_warmth(api):
    """/ready 必须如实报告智能体预热状态，且探针本身不能被预热拖慢。

    为什么这条要单独立：`ready` 只回答"依赖是否就绪"，冷启动窗口就没人看得见——
    运维只能靠"用户投诉首问慢"才发现。而探针里**绝不能**调 `_ensure_components()`，
    否则探针自己阻塞 2~3 秒，可用性探针就成了压测目标。
    """
    from app.services.chat_query_service import chat_query_service as svc

    t0 = time.perf_counter()
    body = api.c.get("/api/v1/ready").json()
    cost = time.perf_counter() - t0
    assert body["code"] == 0, body

    state = body["data"]["agent"]["state"]
    assert state in ("warm", "cold", "unavailable"), body["data"]["agent"]
    # 探针必须是只读的、不能说句话就跑去加载模型
    assert cost <= 1.0, f"探针耗时 {cost:.2f}s，疑似在探针里做了组件加载"
    # `ready` 语义不被智能体预热状态影响（仍只表达基础设施依赖）
    assert body["data"]["ready"] is all(
        v == "ok" for v in body["data"]["checks"].values()), body["data"]

    svc.prewarm()
    assert api.c.get("/api/v1/ready").json()["data"]["agent"]["state"] == "warm"


# =========================================================================== #
# IT-C-03 缓存命中：重复问高频问题不再走检索 + 生成，且答案一致
# =========================================================================== #
def test_it_c03_answer_cache(api, monkeypatch):
    llm = _FakeLLM()
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c3")

    q = "高血压人群饮食注意什么"
    _, first_text, _ = _answer(api, owner, q)
    assert llm.calls == 1, "首次应答应调用模型"

    t0 = time.perf_counter()
    events2, second_text, start2 = _answer(api, owner, q)
    cost = time.perf_counter() - t0

    assert llm.calls == 1, "缓存命中不得再调模型"
    assert start2.get("cached") is True, start2
    assert second_text == first_text, "缓存答案必须与首次一致"
    assert cost <= 1.0, f"缓存命中耗时 {cost:.2f}s，超出 1s"
    assert events2[-1]["event"] == "done", events2


# =========================================================================== #
# IT-C-04 短输入：2 字问题也能给出有效回答，不报"输入过短"
# =========================================================================== #
def test_it_c04_short_input(api, monkeypatch):
    _use_llm(monkeypatch, _FakeLLM())
    owner = _owner(api, "c4")

    events, answer, _ = _answer(api, owner, "鸡蛋")
    assert events and events[-1]["event"] == "done", events
    assert answer.strip(), "短输入也必须给出有效回答"
    assert "过短" not in answer and "太短" not in answer, answer
    assert not any(e["event"] == "error" for e in events), events

    # 空输入才应被参数校验拦下
    empty = api.post_sse("/api/v1/chat/query", _h(owner), {"question": "  "})
    assert empty[0]["event"] == "_json" and empty[0]["data"]["code"] == ErrCode.PARAM_INVALID, empty


# =========================================================================== #
# IT-C-05 引用来源：reference 事件非空、内容相关且带可溯源 doc_id
# =========================================================================== #
def test_it_c05_reference_present(api, monkeypatch):
    _use_llm(monkeypatch, _FakeLLM())
    owner = _owner(api, "c5")

    events, _, _ = _answer(api, owner, "高血压人群饮食注意什么")
    ref = next((e for e in events if e["event"] == "reference"), None)
    assert ref is not None, events
    docs = ref["data"]["docs"]
    assert docs, ref
    for d in docs:
        assert d.get("doc_id"), d
        assert d.get("title"), d
        assert d.get("fragment"), d
    assert any("高血压" in d["title"] for d in docs), docs


# =========================================================================== #
# IT-C-06 无来源兜底：知识库无相关内容时如实说明，绝不编造数值
# =========================================================================== #
def test_it_c06_no_source_fallback(api, monkeypatch):
    llm = _BrokenLLM()          # 一旦进入生成就会抛错，以此证明"没进模型"
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c6")

    events, answer, _ = _answer(api, owner, "怎么给汽车换机油")
    assert events[-1]["event"] == "done", events
    assert "知识库" in answer and "超出" in answer, answer
    assert not any(e["event"] == "error" for e in events), events
    assert llm.calls == 0, "无来源时应如实说明，不得把问题送进模型"
    for bad in ("克", "毫克", "mg", "毫升", "每日"):
        assert bad not in answer, f"兜底话术出现疑似编造数值：{answer}"


# =========================================================================== #
# IT-C-07~10 合规拒答四类：命中即拒，且绝不进入模型
# =========================================================================== #
@pytest.mark.parametrize("question,reason", [
    ("我该吃多少降压药", "medical_advice"),             # IT-C-07 用药 / 剂量
    ("我是不是糖尿病", "diagnosis"),                      # IT-C-08 疾病诊断
    ("帮我解读一下这个化验单", "report_interpretation"),    # IT-C-09 报告解读
    ("孕妇能吃这个吗", "pregnancy"),                      # IT-C-10 孕婴特殊人群
])
def test_it_c07_10_refusal(api, monkeypatch, question, reason):
    llm = _BrokenLLM()
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c7ref")

    events, answer, _ = _answer(api, owner, question)
    done = next(e["data"] for e in events if e["event"] == "done")
    assert done.get("refused") is True, events
    assert done.get("reason") == reason, done
    assert "医生" in answer or "医师" in answer, f"拒答必须引导就医：{answer}"
    assert not any(e["event"] == "reference" for e in events), "拒答不该附引用"
    assert llm.calls == 0, "拒答问题绝不能送进模型"


def test_it_c07_drug_question_never_reaches_model(api, monkeypatch):
    """拒答的关键证据不是文案，而是模型调用次数为 0（用可调用模型反证）。"""
    llm = _FakeLLM()
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c7b")

    _answer(api, owner, "我该吃多少降压药")
    assert llm.calls == 0, "拒答问题绝不能送进模型"


def test_it_c08_disease_diet_still_answered(api, monkeypatch):
    """反向保护：裸病名的食养问题必须正常回答，不能被拒答误伤。"""
    llm = _FakeLLM()
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c8")

    events, answer, _ = _answer(api, owner, "糖尿病主食怎么选")
    done = next(e["data"] for e in events if e["event"] == "done")
    assert not done.get("refused"), (done, answer)
    assert llm.calls == 1, "慢病食养问题应正常进入模型"


# =========================================================================== #
# IT-C-11 多轮追问：同会话连续 3 问，上下文被带进模型
# =========================================================================== #
def test_it_c11_multi_turn_context(api, monkeypatch):
    llm = _FakeLLM(pieces=["好的。", DISCLAIMER])
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c11")

    _, _, start1 = _answer(api, owner, "高血压人群饮食注意什么")
    assert start1.get("has_history") is False, start1
    sid = start1.get("session_id")
    assert sid, start1

    q2 = "那每天吃多少盐合适"
    _, _, start2 = _answer(api, owner, q2, session_id=sid)
    assert start2.get("has_history") is True, start2
    assert start2.get("session_id") == sid, start2

    q3 = "它和什么食材搭配更好"
    _, _, start3 = _answer(api, owner, q3, session_id=sid)
    assert start3.get("has_history") is True, start3

    # 第 3 轮送进模型的 messages 里必须含前两轮的问答（指代"它"的前提）
    assert llm.calls == 3, llm.calls
    joined = " ".join(str(getattr(m, "content", "")) for m in llm.seen_messages[-1])
    assert "高血压人群饮食注意什么" in joined, joined
    assert q2 in joined, joined
    assert q3 in joined, joined


# =========================================================================== #
# IT-C-12 会话列表与历史：顺序正确，助手内容与流式拼接结果一致
# =========================================================================== #
def test_it_c12_sessions_and_history(api, monkeypatch):
    _use_llm(monkeypatch, _FakeLLM(pieces=["第一段回答。", "第二段回答。", DISCLAIMER]))
    owner = _owner(api, "c12")

    _, text1, start1 = _answer(api, owner, "高血压人群饮食注意什么")
    sid = start1["session_id"]
    _, text2, _ = _answer(api, owner, "高尿酸人群饮食注意什么", session_id=sid)

    listed = api.c.get("/api/v1/chat/sessions", headers=_h(owner)).json()
    assert listed["code"] == 0, listed
    assert sid in [x["session_id"] for x in listed["data"]["items"]], listed["data"]

    hist = api.c.get(f"/api/v1/chat/sessions/{sid}/messages", headers=_h(owner)).json()
    assert hist["code"] == 0, hist
    items = hist["data"]["items"]
    assert [m["role"] for m in items] == ["user", "assistant", "user", "assistant"], items
    assert items[0]["content"] == "高血压人群饮食注意什么"
    assert items[2]["content"] == "高尿酸人群饮食注意什么"
    # 落库的助手内容必须等于流式 delta 的拼接结果
    assert items[1]["content"] == text1, (items[1]["content"], text1)
    assert items[3]["content"] == text2, (items[3]["content"], text2)
    assert all(items[i]["id"] < items[i + 1]["id"] for i in range(len(items) - 1)), items

    # 他人的会话不可读（与报告 / 咨询一致的 404 口径）
    other = api.synthetic("c12other", "user")
    denied = api.c.get(f"/api/v1/chat/sessions/{sid}/messages", headers=other).json()
    assert denied["code"] == ErrCode.RESOURCE_NOT_FOUND, denied


# =========================================================================== #
# IT-C-13 转人工：生成咨询单并携带上下文；智能入口转为"人工处理中"
# =========================================================================== #
def test_it_c13_transfer_human(api, monkeypatch):
    from app.models.mysql.platform import Consultation

    _use_llm(monkeypatch, _FakeLLM())
    owner = _owner(api, "c13")

    _, _, start = _answer(api, owner, "我最近血压有点高，想请营养师帮我看看")
    sid = start["session_id"]
    assert start.get("human_pending") is False, start

    transferred = api.c.post(f"/api/v1/chat/sessions/{sid}/transfer-human",
                             headers=_h(owner), json={}).json()
    assert transferred["code"] == 0, transferred
    cid = transferred["data"]["consultation_id"]
    _clean_consult(api, cid)
    assert transferred["data"]["status"] == "pending", transferred

    row = api.db.query(Consultation).filter_by(id=cid).first()
    assert row is not None, "咨询单未落库"
    assert row.user_id == owner["user_id"], (row.user_id, owner["user_id"])
    assert row.transferred_from_session_id == sid, \
        "咨询单必须携带来源会话，营养师才能看到上下文"

    msgs = api.c.get(f"/api/v1/consultations/{cid}/messages", headers=_h(owner)).json()
    assert msgs["code"] == 0, msgs
    assert any("血压有点高" in m["content"] for m in msgs["data"]["items"]), msgs["data"]

    # 人工处理中：智能入口给出提示
    _, _, start2 = _answer(api, owner, "高血压人群饮食注意什么", session_id=sid)
    assert start2.get("human_pending") is True, start2


# =========================================================================== #
# IT-C-14 转人工后恢复：人工会话结束后智能入口恢复
# =========================================================================== #
def test_it_c14_resume_after_human_closed(api, monkeypatch):
    _use_llm(monkeypatch, _FakeLLM())
    owner = _owner(api, "c14")

    _, _, start = _answer(api, owner, "想请营养师看看我的饮食")
    sid = start["session_id"]

    cid = api.c.post(f"/api/v1/chat/sessions/{sid}/transfer-human",
                     headers=_h(owner), json={}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)
    _, _, busy = _answer(api, owner, "高血压人群饮食注意什么", session_id=sid)
    assert busy.get("human_pending") is True, busy

    closed = api.c.post(f"/api/v1/consultations/{cid}/close", headers=_h(owner),
                        json={"reason": "已回复"}).json()
    assert closed["code"] == 0, closed

    _, answer, resumed = _answer(api, owner, "高尿酸人群饮食注意什么", session_id=sid)
    assert resumed.get("human_pending") is False, resumed
    assert answer.strip()


# =========================================================================== #
# IT-C-15 服务繁忙：超容量给排队反馈，但绝不报错、不丢弃
# =========================================================================== #
def test_it_c15_busy_notice_without_dropping(api, monkeypatch):
    from app.services.chat_query_service import chat_query_service as svc

    _use_llm(monkeypatch, _FakeLLM())
    owner = _owner(api, "c15")

    held = []
    try:
        for _ in range(svc.capacity() + 2):       # 预占额度，制造"已满"的并发快照
            held.append(svc.enter())
        assert held[-1]["busy"] is True and held[-1]["notice"], held[-1]
        assert held[-1]["capacity"] == svc.capacity(), held[-1]

        events, answer, start = _answer(api, owner, "高血压人群饮食注意问题")
        load = start.get("load") or {}
        assert load.get("busy") is True, start
        assert "稍慢" in (load.get("notice") or ""), load
        # 关键：给提示的同时请求照常被服务 —— 不报错、不丢弃
        assert not any(e["event"] == "error" for e in events), events
        assert events[-1]["event"] == "done", events
        assert answer.strip(), events
    finally:
        for _ in held:
            svc.exit()
    assert svc._inflight == 0, "并发计数必须归还"


# =========================================================================== #
# IT-C-16 模型异常：SSE 发 error 事件且带错误码
# =========================================================================== #
def test_it_c16_model_error_event(api, monkeypatch):
    from app.services.chat_query_service import chat_query_service as svc
    llm = _BrokenLLM()
    _use_llm(monkeypatch, llm)
    owner = _owner(api, "c16")

    events, _, _ = _answer(api, owner, "高血压人群饮食注意什么")
    assert llm.calls == 1, llm.calls
    err = next((e for e in events if e["event"] == "error"), None)
    assert err is not None, events
    assert err["data"]["code"] == ErrCode.AGENT_UNKNOWN == 60001, err
    assert err["data"]["message"], err
    assert not any(e["event"] == "done" for e in events), "异常时不应再发 done"
    assert svc._inflight == 0, "异常路径同样必须归还并发额度"


# =========================================================================== #
# IT-CS-01 发起咨询：状态=待接入
# =========================================================================== #
def test_it_cs01_create_consultation(api):
    customer = api.make_customer("cs1")
    created = api.c.post("/api/v1/consultations", headers=customer["headers"],
                         json={"question": "想请营养师看看饮食结构", "type": "human"}).json()
    assert created["code"] == 0, created
    assert created["data"]["status"] == "pending", created
    assert created["data"]["type"] == "human", created
    _clean_consult(api, created["data"]["consultation_id"])

    # 营养师侧能看到这条咨询。
    # 待接入队列是 FIFO 且 limit=50，前面若压着 50 条更早的咨询，
    # 本条新咨询会被挤出窗口——那是队列的正常行为，不是缺陷。
    # 所以这里按 id 精确取它的消息列表（相关方可见），不拿"是否出现在
    # 队列第一页"当判据。GET /consultations/{id} 详情端点本项目未提供，
    # 对它发请求拿到的是框架级 404，不是业务响应。
    admin = api.synthetic("cs1adm", "admin")
    detail = api.c.get(
        f"/api/v1/consultations/{created['data']['consultation_id']}/messages",
        headers=admin).json()
    assert detail["code"] == 0, detail
    assert detail["data"], f"新建咨询应至少有一条首问消息：{detail}"

    queue = api.c.get("/api/v1/consultations/pending", headers=admin).json()
    assert queue["code"] == 0, queue
    assert queue["data"]["total"] >= 1, queue["data"]


# =========================================================================== #
# IT-CS-02 状态机：待接入 → 进行中 → 已结束，且记录操作人与时间
# =========================================================================== #
def test_it_cs02_status_machine(api):
    """待接入(pending) → 进行中(active) → 已结束(closed)。

    取值一律以模型/DAO 为准（红线：状态机取值必须读模型，不得凭印象写文字）。
    这里踩过一次：写用例时把"进行中"顺手写成 `in_progress`——那是**测评**
    的状态枚举（`assessment_status`），咨询的是 `active`
    （`consultation_status = Enum("pending","active","closed","timeout")`，
    `ConsultationDAO.accept` 写入 `"active"`）。两个枚举撞了同一个中文词，
    对照 `platform.py:313` 才不会错。
    """
    doc = api.make_dietitian()
    customer = api.make_customer("cs2")
    created = api.c.post("/api/v1/consultations", headers=customer["headers"],
                         json={"question": "求助饮食建议"}).json()
    cid = created["data"]["consultation_id"]
    assert created["data"]["status"] == "pending", created
    _clean_consult(api, cid)

    accepted = api.c.post(f"/api/v1/consultations/{cid}/accept", headers=doc["headers"],
                          json={}).json()
    assert accepted["code"] == 0, accepted
    assert accepted["data"]["status"] == "active", accepted
    assert accepted["data"]["dietitian_id"] == doc["user_id"], accepted
    assert accepted["data"]["accepted_at"], accepted

    closed = api.c.post(f"/api/v1/consultations/{cid}/close", headers=doc["headers"],
                        json={"reason": "已给出建议"}).json()
    assert closed["code"] == 0, closed
    assert closed["data"]["status"] == "closed", closed
    assert closed["data"]["closed_at"], closed
    assert closed["data"]["dietitian_id"] == doc["user_id"], closed


# =========================================================================== #
# IT-CS-03 消息收发：双方互发，顺序一致，无丢失
# =========================================================================== #
def test_it_cs03_message_exchange(api):
    doc = api.make_dietitian()
    customer = api.make_customer("cs3")
    cid = api.c.post("/api/v1/consultations", headers=customer["headers"],
                     json={"question": "我一日三餐怎么安排"}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)
    api.c.post(f"/api/v1/consultations/{cid}/accept", headers=doc["headers"], json={})

    for content, who in [("早餐一般是包子豆浆", customer), ("建议换成杂粮粥 + 鸡蛋", doc),
                         ("好的，谢谢", customer)]:
        sent = api.c.post(f"/api/v1/consultations/{cid}/messages",
                          headers=who["headers"], json={"content": content}).json()
        assert sent["code"] == 0, sent

    for who in (customer, doc):
        got = api.c.get(f"/api/v1/consultations/{cid}/messages", headers=who["headers"]).json()
        assert got["code"] == 0, got
        contents = [m["content"] for m in got["data"]["items"] if m["sender_type"] != "system"]
        assert contents == ["我一日三餐怎么安排", "早餐一般是包子豆浆",
                            "建议换成杂粮粥 + 鸡蛋", "好的，谢谢"], contents
        assert all(m["created_at"] for m in got["data"]["items"]), got["data"]


# =========================================================================== #
# IT-CS-04 非相关方访问：404
# =========================================================================== #
def test_it_cs04_non_party_404(api):
    customer = api.make_customer("cs4")
    cid = api.c.post("/api/v1/consultations", headers=customer["headers"],
                     json={"question": "咨询一下"}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)

    outsider = api.make_customer("cs4out")
    r = api.c.get(f"/api/v1/consultations/{cid}/messages", headers=outsider["headers"]).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r

    s = api.c.post(f"/api/v1/consultations/{cid}/messages", headers=outsider["headers"],
                   json={"content": "混进来看看"}).json()
    assert s["code"] == ErrCode.RESOURCE_NOT_FOUND, s

    # 不存在的咨询与无权限同码，防枚举。
    # 注意探测目标是**已注册**路径 `/{id}/messages`：
    # 业务层对"不存在"和"无权访问"统一抛 RESOURCE_NOT_FOUND(90002)，
    # 攻击者无法通过状态码差异区分"这条咨询存在但不是我"与"这条咨询根本不存在"。
    missing = api.c.get("/api/v1/consultations/999999999/messages",
                        headers=outsider["headers"]).json()
    assert missing["code"] == ErrCode.RESOURCE_NOT_FOUND, missing

    # 完全未注册的 URL 走标准 HTTP 404（不是本项目缺陷）：FastAPI 对未匹配路由
    # 抛 StarletteHTTPException，不经业务层、也没有统一响应体；
    # 前端 api.js 对 `!res.ok` 已单独兜底（抛 ApiError(404)），不会因拿不到 code 而崩。
    raw = api.c.get("/api/v1/consultations/999999999", headers=outsider["headers"])
    assert raw.status_code == 404, raw.status_code


# =========================================================================== #
# IT-CS-06 满意度评价：写入成功；重复评价给出提示
# =========================================================================== #
def test_it_cs06_rating_once(api):
    doc = api.make_dietitian()
    customer = api.make_customer("cs6")
    cid = api.c.post("/api/v1/consultations", headers=customer["headers"],
                     json={"question": "评价测试"}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)
    api.c.post(f"/api/v1/consultations/{cid}/accept", headers=doc["headers"], json={})
    api.c.post(f"/api/v1/consultations/{cid}/close", headers=doc["headers"], json={})

    first = api.c.post(f"/api/v1/consultations/{cid}/rating", headers=customer["headers"],
                       json={"rating": 5, "comment": "很有帮助"}).json()
    assert first["code"] == 0 and first["data"]["rating"] == 5, first

    again = api.c.post(f"/api/v1/consultations/{cid}/rating", headers=customer["headers"],
                       json={"rating": 1, "comment": "改一下"}).json()
    assert again["code"] == ErrCode.PARAM_INVALID, again
    assert "重复" in again["message"], again

    # 越界评分被拦
    bad = api.c.post(f"/api/v1/consultations/{cid}/rating", headers=customer["headers"],
                     json={"rating": 9}).json()
    assert bad["code"] == ErrCode.PARAM_INVALID, bad


# =========================================================================== #
# IT-CS-07 结束咨询：双方不可再发送；历史消息保留
# =========================================================================== #
def test_it_cs07_close_blocks_send(api):
    doc = api.make_dietitian()
    customer = api.make_customer("cs7")
    cid = api.c.post("/api/v1/consultations", headers=customer["headers"],
                     json={"question": "结束测试"}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)
    api.c.post(f"/api/v1/consultations/{cid}/accept", headers=doc["headers"], json={})
    api.c.post(f"/api/v1/consultations/{cid}/messages", headers=customer["headers"],
               json={"content": "结束前最后一句"})

    closed = api.c.post(f"/api/v1/consultations/{cid}/close", headers=customer["headers"],
                        json={"reason": "问题已解决"}).json()
    assert closed["code"] == 0 and closed["data"]["status"] == "closed", closed

    for who in (customer, doc):
        r = api.c.post(f"/api/v1/consultations/{cid}/messages", headers=who["headers"],
                       json={"content": "结束后还能发吗"}).json()
        assert r["code"] == ErrCode.PARAM_INVALID, r
        assert "结束" in r["message"], r

    got = api.c.get(f"/api/v1/consultations/{cid}/messages", headers=customer["headers"]).json()
    assert any(m["content"] == "结束前最后一句" for m in got["data"]["items"]), got["data"]

    twice = api.c.post(f"/api/v1/consultations/{cid}/close", headers=customer["headers"],
                       json={}).json()
    assert twice["code"] == ErrCode.PARAM_INVALID, twice


# =========================================================================== #
# IT-CS-08 图片消息：一期明确拒绝（不静默按文本存下来）
# =========================================================================== #
def test_it_cs08_image_message_rejected(api):
    customer = api.make_customer("cs8")
    cid = api.c.post("/api/v1/consultations", headers=customer["headers"],
                     json={"question": "图片消息测试"}).json()["data"]["consultation_id"]
    _clean_consult(api, cid)

    r = api.c.post(f"/api/v1/consultations/{cid}/messages", headers=customer["headers"],
                   json={"content": "IMG_BASE64_PLACEHOLDER", "type": "image"}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    assert "一期" in r["message"] or "文字" in r["message"], r

    got = api.c.get(f"/api/v1/consultations/{cid}/messages", headers=customer["headers"]).json()
    assert not any("IMG_BASE64" in m["content"] for m in got["data"]["items"]), got["data"]
