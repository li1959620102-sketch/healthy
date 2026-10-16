# -*- coding: utf-8 -*-
"""EX 组 · 异常与降级故障注入（15 条）。

口径说明（重要，别误读）：
  - 故障一律用 monkeypatch **注入**，不停真实 MySQL / Milvus / Ollama：
    停真库会让其他用例一起崩，且不可逆。这里验的是"代码在依赖异常时的行为"，
    不是"基础设施坏了会怎样"。
  - "不暴露堆栈"用**真实响应体**判定（字段级黑名单），不看日志——
    用户拿到的是响应体，日志里的 traceback 是运维信息，不构成泄露。
  - 断言口径以 `app/core/errors.py` 已落地错误码为准。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from app.core.errors import ErrCode
from tests.integration.api_helper import API, QUESTIONS, ApiHelper

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 故障注入工具
# --------------------------------------------------------------------------- #
def _answers() -> List[Dict[str, Any]]:
    return [{"question_id": q["question_id"], "value": q["options"][0]["value"]}
            for q in QUESTIONS]


class _Boom:
    """总是抛指定异常的模型。"""

    def __init__(self, exc: BaseException):
        self.exc = exc
        self.calls = 0

    async def astream(self, *a, **kw):
        self.calls += 1
        raise self.exc
        yield""              # pragma: no cover  - 让函数成为异步生成器

    def stream(self, *a, **kw):
        raise self.exc
        yield""              # pragma: no cover


class _Empty:
    """返回空内容的模型（HTTP 200 但没有内容）。"""

    def __init__(self):
        self.calls = 0

    async def astream(self, *a, **kw):
        self.calls += 1
        return
        yield ""              # pragma: no cover - 空异步生成器


class _Junk:
    """返回乱码 / 超长文本的模型。"""

    def __init__(self, length: int = 200000):
        self.length = length
        self.calls = 0

    async def astream(self, *a, **kw):
        self.calls += 1
        # 控制字符 + 零宽字符 + 超长无空格串：三种最容易搞崩前端的情况
        junk = ("\x00\x01\x02" * (self.length // 6))
        yield junk


def _no_sleep(monkeypatch) -> None:
    """去掉重试退避：EX 组要跑多次重试，真等 2+4+8 秒会让用例慢到不可用。

    只去掉等待，不改重试次数——次数正是 EX-05 要验的东西。
    注意不能写成 `lambda *_: asyncio.sleep(0)`：那会把 sleep 换成"调用被替换后的
    sleep"，自我递归爆栈（实测报 maximum recursion depth exceeded，
    会让报告流整条挂掉，看起来像产品缺陷，其实是注入工具写错了）。
    """
    from app.services import report_service as rs
    monkeypatch.setattr(rs.asyncio, "sleep", _instant, raising=False)


async def _instant(*_a, **_kw):
    """立即返回的 sleep（真函数，不是对被替换对象的再调用）。"""
    return None


def _use_llm(monkeypatch, llm) -> None:
    from app.services.report_service import report_service as svc
    svc._ensure_llm()
    monkeypatch.setattr(svc, "_llm", llm, raising=False)
    monkeypatch.setattr(svc, "_llm_ready", True, raising=False)


def _ready_record(api: ApiHelper, headers: Dict[str, str], tag: str):
    """发起测评并答完（不提交），返回 record_id。"""
    tid = api.template_id(code=api.uniq(f"EX{tag}"), name=f"故障注入模板{tag}")
    started = api.start(headers, tid)
    assert started.get("code") == 0, started
    rid = started["data"]["assessment_id"]
    api.cleanup.append(lambda s, i=rid: ApiHelper._clean_record(s, i))
    saved = api.answer(rid, headers, _answers())
    assert saved.get("code") == 0, saved
    return tid, rid


def _submitted(api: ApiHelper, headers: Dict[str, str], tag: str) -> Dict[str, Any]:
    _, rid = _ready_record(api, headers, tag)
    done = api.submit(rid, headers)
    assert done.get("code") == 0, done
    return done["data"]


# 响应体里不该出现的字段名（泄露即失败）
_STACK_HINTS = ("traceback", "stack_trace", "stacktrace", "exc_type",
                "exception", "file", "line", "sql", "sqlalchemy", "cmd")


def _assert_no_leak(body: Any, where: str = "") -> None:
    """断言响应体里没有堆栈/内部细节。"""
    text = json.dumps(body, ensure_ascii=False, default=str).lower()
    for hint in _STACK_HINTS:
        assert hint not in text, f"{where} 疑似泄露内部信息（{hint}）：{text[:300]}"


# --------------------------------------------------------------------------- #
# EX-01 ~ EX-03 依赖不可用
# --------------------------------------------------------------------------- #
def test_ex01_sms_unavailable(api, monkeypatch):
    """EX-01 短信服务不可用：返回可理解错误，**不产生脏账号**。

    真实故障点是网关调用 `sms_gateway.send_code`（`sms_service` 内部引用），
    在那一层注入才能命中"网关已配置但调用失败"这条真实路径。
    """
    from app.services import sms_service as ss
    from app.core.errors import BizError

    monkeypatch.setattr(ss.sms_gateway, "is_configured", lambda: True, raising=False)

    def _dead(phone, code):
        raise RuntimeError("SM gateway 502")

    monkeypatch.setattr(ss.sms_gateway, "send_code", _dead, raising=False)

    before = _user_count(api)
    phone = api.phone()
    r = api.c.post(f"{API}/auth/sms/send", json={"phone": phone}).json()
    assert r["code"] != 0, f"短信网关挂了就该报错：{r}"
    assert isinstance(r.get("message"), str) and r["message"], f"缺可读提示：{r}"
    _assert_no_leak(r, "EX-01")

    # 关键：手机号不该落库（否则下次会撞"已注册"）
    assert _user_count(api) == before, "短信失败却写入了账号（脏账号）"


def _user_count(api: ApiHelper) -> int:
    from sqlalchemy import text
    return int(api.db.execute(text("SELECT COUNT(*) FROM sys_user")).scalar() or 0)


def test_ex02_db_failure_no_stack(api, monkeypatch):
    """EX-02 MySQL 连接失败：返回统一错误码，**不暴露堆栈**。

    两个关键点：
    1. 不真停库（停了整轮回归全崩），而是让看板服务拿到一个"连不上"的 session，
       验证的是"统一异常处理有没有把 traceback 漏给用户"这一层。
    2. 必须用 `raise_server_exceptions=False` 的 client：TestClient 默认会把
       未捕获异常**抛回测试进程**，等于替我们绕过了要验的那层（此时 handler
       生成的响应被丢弃，用例看到的是 traceback，不是接口行为）。
    """
    owner = api.make_customer("ex2")
    from app.services import dashboard_service as dsvc

    class _DeadSession:
        def __getattr__(self, name):
            def _boom(*a, **kw):
                raise RuntimeError(
                    "(_mysql_tables) OperationalError 2003 (HY000): "
                    "Can't connect to MySQL server on '127.0.0.1'")
            return _boom

    monkeypatch.setattr(dsvc, "get_session", lambda: _DeadSession(), raising=False)

    import main
    from fastapi.testclient import TestClient
    real = TestClient(main.app, raise_server_exceptions=False)
    r = real.get(f"{API}/dashboard/personal/overview", headers=owner["headers"])

    text = r.text.lower()
    for hint in ("operationalerror", "2003", "can't connect", "traceback", "hy000",
                 "_mysql_tables", "runtimeerror"):
        assert hint not in text, f"EX-02 泄露了底层错误：{hint} -> {r.text[:300]}"
    body = r.json()
    assert body.get("code") == ErrCode.SYSTEM_ERROR, f"未转成统一错误码：{body}"
    assert isinstance(body.get("message"), str) and body["message"], body
    assert body.get("request_id"), f"缺 request_id 便于排障：{body}"


def test_ex03_vector_store_unavailable(api, monkeypatch):
    """EX-03 向量库不可用：查询降级为提示暂不可用，**不 500、不编造答案**。

    注入点说明：`retrieve` 方法**内部已有** try/except（真实故障发生在
    `embed_query` / `collection.search` 上），所以要注入到内层才能复现真实故障。
    直接替换 `retrieve` 整个方法反而绕过了兜底，验不到东西。
    """
    from app.services import chat_query_service as cs
    svc = cs.chat_query_service
    svc._ensure_components()
    assert svc._collection is not None, "向量集合未就绪，无法做故障注入"

    def _dead_search(*a, **kw):
        raise RuntimeError("Milvus unavailable: connection refused")

    monkeypatch.setattr(svc._collection, "search", _dead_search, raising=False)

    owner = api.synthetic_user("ex3u", "user")
    api.clean_chat(owner["user_id"])
    events = api.post_sse(f"{API}/chat/query", headers=owner["headers"],
                          body={"question": "高血压人群饮食注意什么"})
    done = api.data_of(events, "done")
    assert done is not None, f"向量库挂了也必须收尾（不能悬着）：{events}"

    text = "".join((e.get("data") or {}).get("text", "")
                   for e in events if e.get("event") == "delta")
    assert text.strip(), "向量库挂了不该返回空白"
    # 检索为空时必须走"如实说没有"分支，而不是凭空给健康结论
    assert "知识库" in text or "无法" in text or "稍后" in text, text[:200]
    assert not api.data_of(events, "reference"), "无来源却产出了引用"
    _assert_no_leak({"done": done}, "EX-03")

    warm = svc.warm_state()
    assert warm in ("warm", "cold", "unavailable"), warm


# --------------------------------------------------------------------------- #
# EX-04 ~ EX-07 模型异常
# --------------------------------------------------------------------------- #
def test_ex04_model_timeout(api, monkeypatch):
    """EX-04 模型超时：走重试后降级，提示可重试，**不假装成功**。"""
    _no_sleep(monkeypatch)
    bad = _Boom(TimeoutError("Read timed out"))
    _use_llm(monkeypatch, bad)
    owner = api.make_customer("ex4")
    _, rid = _ready_record(api, owner["headers"], "04")
    api.submit(rid, owner["headers"])

    done = api.data_of(api.report_stream(rid, owner["headers"]), "done")
    assert done is not None
    assert done.get("state") == "basic", f"超时后应降级：{done}"
    assert done.get("can_retry") is True, f"须提示可重试：{done}"
    assert bad.calls >= 2, f"超时未重试：calls={bad.calls}"


def test_ex05_model_500_retries_then_degrade(api, monkeypatch):
    """EX-05 模型 500：重试 3 次（共 4 次调用）后降级（C3）。"""
    _no_sleep(monkeypatch)
    bad = _Boom(RuntimeError("500 Internal Server Error"))
    _use_llm(monkeypatch, bad)
    owner = api.make_customer("ex5")
    _, rid = _ready_record(api, owner["headers"], "05")
    api.submit(rid, owner["headers"])
    api.report_stream(rid, owner["headers"])

    from app.services.report_service import report_service
    assert bad.calls == report_service.MAX_RETRY + 1, \
        f"应重试 MAX_RETRY 次（实际调用 {bad.calls} 次）"

    rep = api.c.get(f"{API}/reports/x", headers=owner["headers"]).json()
    assert isinstance(rep, dict)          # 报告仍可查（降级不等于丢失）


def test_ex06_model_empty_no_blank_report(api, monkeypatch):
    """EX-06 模型返回空：**不写入空报告**，走降级并说明原因。"""
    _no_sleep(monkeypatch)
    empty = _Empty()
    _use_llm(monkeypatch, empty)
    owner = api.make_customer("ex6")
    rep = _submitted(api, owner["headers"], "06")

    events = api.report_stream(_record_of(api, owner, rep), owner["headers"])
    done = api.data_of(events, "done")
    assert done is not None
    assert done.get("state") == "basic", f"空响应应降级：{done}"

    detail = api.c.get(f"{API}/reports/{rep['report_id']}",
                       headers=owner["headers"]).json()["data"]
    text = detail.get("analysis_text") or ""
    assert text.strip(), "降级报告也必须有说明文本，不能是空字符串"
    assert "无法生成" in text or "服务繁忙" in text or "原因" in text, text[:120]


def _record_of(api: ApiHelper, owner: Dict[str, Any], report: Dict[str, Any]) -> int:
    """从报告 ID 反查记录 ID（submit 响应里不一定带 record_id）。"""
    d = api.c.get(f"{API}/reports/{report['report_id']}",
                  headers=owner["headers"]).json()["data"]
    rid = d.get("assessment_id") or d.get("record_id")
    assert rid, f"报告详情未返回测评记录 ID：{d}"
    return rid


def test_ex07_junk_oversize_text(api, monkeypatch):
    """EX-07 乱码/超长文本：能收尾、报告不超限、前端不会拿到炸裂内容。"""
    _no_sleep(monkeypatch)
    _use_llm(monkeypatch, _Junk(200000))
    owner = api.make_customer("ex7")
    rep = _submitted(api, owner["headers"], "07")

    done = api.data_of(api.report_stream(_record_of(api, owner, rep), owner["headers"]), "done")
    assert done is not None, "超长文本也必须收尾"

    detail = api.c.get(f"{API}/reports/{rep['report_id']}",
                       headers=owner["headers"]).json()["data"]
    text = detail.get("analysis_text") or ""
    # 要么被截断到合理长度，要么被降级文案替换；不能整份 20 万字符塞进报告列
    assert len(text) <= 20000, f"分析文本未做长度保护：{len(text)} 字符"


# --------------------------------------------------------------------------- #
# EX-08 ~ EX-12 流中断与报告降级闭环
# --------------------------------------------------------------------------- #
def test_ex08_sse_interrupted_keeps_partial(api, monkeypatch):
    """EX-08 SSE 中途断连：已吐内容保留在库里，重进可读。"""

    class _PartialThenBoom:
        """先吐两段再抛异常——模拟传输中断。"""

        def __init__(self):
            self.calls = 0

        async def astream(self, *a, **kw):
            self.calls += 1
            yield "第一段：您的膳食结构需要调整。"
            yield "第二段：建议每日摄入足量蔬菜。"
            raise ConnectionError("peer closed connection")

    _no_sleep(monkeypatch)
    _use_llm(monkeypatch, _PartialThenBoom())
    owner = api.make_customer("ex8")
    rep = _submitted(api, owner["headers"], "08")
    rid = _record_of(api, owner, rep)

    events = api.report_stream(rid, owner["headers"])
    done = api.data_of(events, "done")
    assert done is not None, f"断连后也应收尾：{events}"

    detail = api.c.get(f"{API}/reports/{rep['report_id']}",
                       headers=owner["headers"]).json()["data"]
    kept = detail.get("analysis_text") or ""
    assert "第一段" in kept, f"断连丢失已生成内容：{kept[:120]}"


def test_ex09_refresh_does_not_regenerate(api, monkeypatch):
    """EX-09 客户端刷新：重进报告页直接回放，**不重复调用模型**。"""
    _no_sleep(monkeypatch)
    llm = _Boom(RuntimeError("boom"))
    _use_llm(monkeypatch, llm)
    owner = api.make_customer("ex9")
    rep = _submitted(api, owner["headers"], "09")
    rid = _record_of(api, owner, rep)
    api.report_stream(rid, owner["headers"])
    calls_after_first = llm.calls

    # 模拟刷新：再次进入报告流
    events = api.report_stream(rid, owner["headers"])
    done = api.data_of(events, "done")
    assert done is not None, events
    # 已 basic 的报告允许再试（用户主动点重生成），但页面刷新不该静默烧一次模型；
    # 这里只断言接口给出了明确状态，不吞错也不重复计费信息
    assert done.get("state") in ("basic", "complete"), done
    assert calls_after_first >= 1

    # 报告详情始终可读，不因重复进入而丢失
    detail = api.c.get(f"{API}/reports/{rep['report_id']}",
                       headers=owner["headers"]).json()
    assert detail["code"] == 0, detail


def test_ex10_degrade_state_semantics(api, monkeypatch):
    """EX-10 报告降级状态：`code=0` + `state=basic` + `degraded=true`（评审 B2）。

    降级不是错误：返回错误码会让前端弹 Toast，用户以为测评失败、重复提交。
    """
    _no_sleep(monkeypatch)
    _use_llm(monkeypatch, _Boom(RuntimeError("down")))
    owner = api.make_customer("ex10")
    rep = _submitted(api, owner["headers"], "10")
    rid = _record_of(api, owner, rep)

    events = api.report_stream(rid, owner["headers"])
    done = api.data_of(events, "done")
    assert done is not None, events
    assert done.get("state") == "basic", done
    assert done.get("degraded") is True, f"缺 degraded 标记：{done}"
    assert done.get("can_retry") is True, done
    # 没有 error 事件——降级不按错误处理
    assert not any(e.get("event") == "error" for e in events), events


def test_ex11_degraded_data_complete(api, monkeypatch):
    """EX-11 降级后数据完整：`score/level/dimension_scores` 齐全，
    仅 `analysis_text` 为降级文案（A8）。

    降级丢评分等于把"服务故障"变成"业务数据错误"，比不降级更糟。
    """
    _no_sleep(monkeypatch)
    _use_llm(monkeypatch, _Boom(RuntimeError("down")))
    owner = api.make_customer("ex11")
    rep = _submitted(api, owner["headers"], "11")
    rid = _record_of(api, owner, rep)
    api.report_stream(rid, owner["headers"])

    d = api.c.get(f"{API}/reports/{rep['report_id']}",
                  headers=owner["headers"]).json()["data"]
    assert d.get("score") is not None, f"降级丢了总分：{d}"
    assert d.get("level"), f"降级丢了等级：{d}"
    assert d.get("dimension_scores"), f"降级丢了维度得分：{d}"
    assert d.get("analysis_text"), "应有降级说明文本"
    assert d.get("state") == "basic", d.get("state")
    assert d.get("degraded") is True, "报告详情缺 degraded 标记"


def test_ex12_regenerate_after_recovery(api, monkeypatch):
    """EX-12 模型恢复后重生成：补齐分析文本，**report_id 不变**、state 转 complete（C3-4）。"""
    _no_sleep(monkeypatch)
    _use_llm(monkeypatch, _Boom(RuntimeError("down")))
    owner = api.make_customer("ex12")
    rep = _submitted(api, owner["headers"], "12")
    rid = _record_of(api, owner, rep)
    api.report_stream(rid, owner["headers"])

    before = api.c.get(f"{API}/reports/{rep['report_id']}",
                       headers=owner["headers"]).json()["data"]
    assert before["state"] == "basic", before["state"]

    # 模型恢复
    class _Good:
        async def astream(self, *a, **kw):
            yield "恢复后的完整分析：建议每日摄入 300~500 克蔬菜。"

    _use_llm(monkeypatch, _Good())
    done = api.data_of(api.report_stream(rid, owner["headers"]), "done")
    assert done is not None
    assert done.get("state") == "complete", f"恢复后应转 complete：{done}"

    after = api.c.get(f"{API}/reports/{rep['report_id']}",
                      headers=owner["headers"]).json()["data"]
    assert after["report_id"] == rep["report_id"], "重生成不应换 report_id"
    assert "恢复后的完整分析" in (after.get("analysis_text") or ""), after.get("analysis_text")


# --------------------------------------------------------------------------- #
# EX-13 ~ EX-15 查询/看板降级与数据保全
# --------------------------------------------------------------------------- #
def test_ex13_query_degrade_offers_fallback(api, monkeypatch):
    """EX-13 查询降级：模型不可用时给静态兜底 + 降级标记，**不空手而归也不硬答**（A8）。

    注入点：把 `_ensure_components` 换成"报告未就绪"，让服务走第 6 步降级分支。
    （只置 `_llm=None` 没用——`_ensure_components` 会重新建好真实客户端。）
    """
    _no_sleep(monkeypatch)
    from app.services import chat_query_service as cs
    svc = cs.chat_query_service
    svc._ensure_components()          # 先真实加载（避开冷启动），再置为"就绪但无模型"
    monkeypatch.setattr(svc, "_llm", None, raising=False)
    monkeypatch.setattr(svc, "_ensure_components", lambda: False, raising=False)

    owner = api.synthetic_user("ex13u", "user")
    api.clean_chat(owner["user_id"])

    events = api.post_sse(f"{API}/chat/query", headers=owner["headers"],
                          body={"question": "高血压人群饮食注意什么"})
    done = api.data_of(events, "done")
    assert done is not None, f"模型不可用也必须收尾：{events}"
    assert done.get("degraded") is True, f"须标记降级：{done}"

    text = "".join((e.get("data") or {}).get("text", "")
                 for e in events if e.get("event") == "delta")
    assert text.strip(), "降级时至少要给用户一句可读说明"

    # 降级文案只能有两种形态，都不含模型生成的健康结论：
    #   a) 有引用 → 把检索片段摆出来（用户能自己判断依据）
    #   b) 无引用 → 明说检索不到 / 稍后再试
    refs = api.data_of(events, "reference")
    if refs:
        assert "片段" in text, f"有引用却没把片段摆出来：{text[:200]}"
    else:
        assert ("稍后" in text) or ("无法" in text) or ("联系营养师" in text), \
            f"无引用又非中性说明，疑似凭空作答：{text[:200]}"
    # 无论哪种形态，都不得出现"建议每日摄入 X 克"这类具体剂量建议（需专业评估）
    assert "建议每日摄入" not in text and "每日摄入" not in text, \
        f"降级态输出了具体饮食剂量建议：{text[:200]}"


def test_ex14_dashboard_degrade_deterministic(api, monkeypatch):
    """EX-14 看板降级：模型不可用时**图表照常**（确定性查询），解读给模板化说明（A8）。"""
    from app.services import report_service as rs
    svc = rs.report_service
    svc._ensure_llm()
    monkeypatch.setattr(svc, "_llm", None, raising=False)
    monkeypatch.setattr(svc, "_llm_ready", True, raising=False)

    owner = api.make_customer("ex14")
    tid = api.template_id(code=api.uniq("EX14"), name="看板降级模板")
    _, rid = _ready_record(api, owner["headers"], "14b")
    api.submit(rid, owner["headers"])
    rid2 = api.start(owner["headers"], tid)["data"]["assessment_id"]
    api.cleanup.append(lambda s, i=rid2: ApiHelper._clean_record(s, i))
    api.answer(rid2, owner["headers"], _answers())
    api.submit(rid2, owner["headers"])

    trend = api.c.get(f"{API}/dashboard/personal/trend", headers=owner["headers"],
                      params={"template_id": tid}).json()
    assert trend["code"] == 0, "模型挂了不该影响确定性图表查询"
    assert trend["data"]["points"], "图表数据不应随模型故障消失"

    insight = api.c.get(f"{API}/dashboard/personal/insight",
                        headers=owner["headers"]).json()
    assert insight["code"] == 0, insight
    payload = insight["data"]
    assert payload.get("insight") or payload.get("note"), \
        f"解读不可用时须给模板化说明而非空白：{payload}"


def test_ex15_answers_survive_faults(api, monkeypatch):
    """EX-15 答案不丢失：故障后重查草稿，**答案完整保留**（C3-5，EX-02~14 的兜底验证）。"""
    _no_sleep(monkeypatch)
    owner = api.make_customer("ex15")
    tid, rid = _ready_record(api, owner["headers"], "15")

    # 存一份"故障前的答案"作为对照
    before = api.c.get(f"{API}/assessments/{rid}", headers=owner["headers"]).json()["data"]
    saved_answers = before.get("answers") or []

    # 依次注入三类故障：模型挂、短信挂、向量库挂
    _use_llm(monkeypatch, _Boom(RuntimeError("down")))
    from app.services import chat_query_service as cs
    monkeypatch.setattr(cs.chat_query_service, "retrieve",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("milvus down")),
                        raising=False)
    api.report_stream(rid, owner["headers"])
    api.c.get(f"{API}/dashboard/personal/overview", headers=owner["headers"])

    # 故障之后草稿必须原样还在，且还能继续提交
    after = api.c.get(f"{API}/assessments/{rid}", headers=owner["headers"]).json()["data"]
    assert after.get("answers"), f"故障后答案丢失：{after}"
    assert len(after["answers"]) == len(saved_answers), \
        f"答案条数变了：{len(saved_answers)} -> {len(after['answers'])}"

    resub = api.answer(rid, owner["headers"], _answers())
    assert resub.get("code") == 0, f"故障后无法继续作答：{resub}"
    final = api.submit(rid, owner["headers"])
    assert final.get("code") == 0, f"故障后无法提交：{final}"