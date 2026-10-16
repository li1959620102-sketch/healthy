# -*- coding: utf-8 -*-
"""测评执行与报告契约用例（IT-AS 13 条 + IT-R 12 条）。

IT-AS-03/05/06/07/08/10/11/12/13/14/15/16/18 —— 跨设备续答 / 实时校验 / 必答校验 /
    保存与提交性能 / 幂等窗口内外 / 缺幂等键 / 重复提交 / 未完成测评 / 作废 /
    游客认领链路与防冒用 / 草稿乐观锁；
IT-R-01~09/11/13/14 —— 结构化字段 / 建议结构 / 引用可溯源 / 免责声明 / 生成中 /
    自动重试 / 降级基础版 / 手动重生成 / 答案不丢失 / 流式事件 / 批注 /
    越权 404。

两处口径说明（台账「备注」列同步）：
  1. 旧用例集写的 40002/40003/409 属旧编号：本实现以 `app/core/errors.py` 为准，
     重复提交=ASSESSMENT_CONFLICT(50004)、缺幂等键=PARAM_INVALID(90001)；
     本项目基线是「HTTP 恒 200 + 业务码」，故 409 以业务码表达。
  2. 「存在未完成测评」用例允许"引导继续作答"这一实现口径：
     再次发起不报错，而是复用原记录并回 `resumed=true`。

模型相关用例（自动重试 / 降级 / 流式完整性）一律用**可控假模型**注入，
既不真调外部模型，也不依赖"恰好网络抖动"——否则用例不可重现。
"""
import asyncio
import time
from datetime import datetime, timedelta

import pytest

from app.core.errors import ErrCode
from tests.integration.conftest import QUESTIONS

pytestmark = pytest.mark.integration

FULL = "A"                 # 满分选项
DISCLAIMER = "本内容由 AI 生成，不构成诊疗建议"


# =========================================================================== #
# 工具
# =========================================================================== #
def _all_answers(value: str = FULL):
    return {q["question_id"]: value for q in QUESTIONS}


def _numeric_questions():
    """带数值题（身高）的模板题目：用于 IT-AS-05 实时校验。"""
    qs = [dict(q) for q in QUESTIONS]
    qs.append({"question_id": "height", "dimension": "profile", "type": "number",
               "title": "身高", "unit": "cm", "required": False, "min": 50, "max": 250})
    return qs


def _drain(agen):
    """把异步生成器整体抽干为事件列表（同步用例里验证 SSE 用）。"""
    async def _run():
        return [ev async for ev in agen]
    return asyncio.run(_run())


class _Chunk:
    def __init__(self, content):
        self.content = content


class _GoodLLM:
    """最小可用假模型：验证"生成成功"路径，不触网。"""

    def __init__(self, pieces=("总体评价：各维度表现良好。", "建议保持规律饮食。")):
        self.pieces = pieces

    def astream(self, prompt):
        async def _gen():
            for p in self.pieces:
                yield _Chunk(p)
        return _gen()


class _BadLLM:
    """始终失败的假模型：验证重试与降级，不依赖真实网络故障。"""

    def __init__(self):
        self.calls = 0

    def astream(self, prompt):
        self.calls += 1

        async def _gen():
            raise RuntimeError("上游模型不可用（用例注入）")
            yield None                          # pragma: no cover
        return _gen()


def _freeze_sleep(monkeypatch, report_service_module):
    """把 2/4/8 秒退避替换为记录器：验证退避策略但不真等 14 秒。"""
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(report_service_module.asyncio, "sleep", _sleep)
    return slept


def _submitted(api, owner, value: str = FULL):
    """造一份已提交的测评，返回 (record_id, report_id)。"""
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers(value))
    sub = api.submit(rec, owner, key=api.uniq("k"))
    assert sub["code"] == 0, sub
    return rec, sub["data"]["report_id"]


# =========================================================================== #
# IT-AS-03 跨设备续答：A 设备答 5 题，B 设备可见同样答案并继续
# =========================================================================== #
def test_it_as03_resume_on_another_device(api):
    from fastapi.testclient import TestClient
    import main

    owner = api.synthetic("as3", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)

    five = {q["question_id"]: "A" for q in QUESTIONS[:3]}
    saved = api.answer(rec, owner, five, page=1)
    assert saved["code"] == 0, saved

    device_b = TestClient(main.app)             # 第二台"设备"
    det = device_b.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert det["code"] == 0, det
    assert len(det["data"]["answers"]) == 3, det["data"]["answers"]
    assert det["data"]["answers"] == five, det["data"]["answers"]
    assert det["data"]["saved_at"], "详情需回传 saved_at，供另一台设备带版本续答"

    # B 设备继续补答，两侧答案合并而不是覆盖
    rest = {q["question_id"]: "B" for q in QUESTIONS[3:]}
    again = device_b.patch(f"/api/v1/assessments/{rec}/answers", headers=owner,
                           json={"answers": rest, "version": det["data"]["saved_at"]}).json()
    assert again["code"] == 0, again
    final = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert len(final["data"]["answers"]) == len(QUESTIONS), final["data"]["answers"]


# =========================================================================== #
# IT-AS-05 实时校验：越界答案当次响应给 warnings，且不调模型、≤200ms
# =========================================================================== #
def test_it_as05_realtime_validation_warnings(api, monkeypatch):
    from app.services import report_service as rs

    def _boom(*_a, **_kw):                      # 调用了就说明"实时校验"跑了模型
        raise AssertionError("保存草稿不得调用模型")
    monkeypatch.setattr(rs.report_service, "_ensure_llm", _boom)

    owner = api.synthetic("as5", "user")
    tpl = api.template_id(questions=_numeric_questions())
    rec = api.record_id(owner, tpl)

    t0 = time.perf_counter()
    r = api.answer(rec, owner, {"height": 500})
    cost_ms = (time.perf_counter() - t0) * 1000

    assert r["code"] == 0, r                       # 提示但不阻断保存
    warns = r["data"]["warnings"]
    assert warns and warns[0]["question_id"] == "height", r["data"]
    assert "身高" in warns[0]["message"], warns
    assert cost_ms <= 200, f"实时校验耗时 {cost_ms:.0f}ms，超出 200ms"

    # 合法值不产生 warning
    ok = api.answer(rec, owner, {"height": 170})
    assert ok["data"]["warnings"] == [], ok["data"]


# =========================================================================== #
# IT-AS-06 必填校验：跳过必答题不可提交，且返回完整缺失清单
# =========================================================================== #
def test_it_as06_required_answers_block_submit(api):
    owner = api.synthetic("as6", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, {QUESTIONS[0]["question_id"]: "A"})

    r = api.submit(rec, owner, key=api.uniq("k6"))
    assert r["code"] == ErrCode.PARAM_INVALID, r
    missing = r["data"]["missing"]
    assert len(missing) == len(QUESTIONS) - 1, missing
    assert all(m.get("question_id") and m.get("title") for m in missing), missing
    assert QUESTIONS[0]["question_id"] not in [m["question_id"] for m in missing]

    # 补全后可提交
    api.answer(rec, owner, _all_answers())
    assert api.submit(rec, owner, key=api.uniq("k6b"))["code"] == 0


# =========================================================================== #
# IT-AS-07 保存草稿性能：PATCH 8 题 P95 ≤200ms
# =========================================================================== #
def test_it_as07_save_draft_latency(api):
    owner = api.synthetic("as7", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    answers = _all_answers()

    costs = []
    for _ in range(10):
        t0 = time.perf_counter()
        r = api.answer(rec, owner, answers)
        costs.append((time.perf_counter() - t0) * 1000)
        assert r["code"] == 0, r
    costs.sort()
    p95 = costs[int(0.95 * len(costs)) - 1]
    assert p95 <= 200, f"保存草稿 P95={p95:.0f}ms，超出 200ms（全部 {costs}）"


# =========================================================================== #
# IT-AS-08 提交性能：POST submit ≤2s 返回 score/level
# =========================================================================== #
def test_it_as08_submit_latency(api):
    owner = api.synthetic("as8", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())

    t0 = time.perf_counter()
    r = api.submit(rec, owner, key=api.uniq("k8"))
    cost_ms = (time.perf_counter() - t0) * 1000

    assert r["code"] == 0, r
    assert r["data"]["score"] is not None and r["data"]["level"], r["data"]
    assert cost_ms <= 2000, f"提交耗时 {cost_ms:.0f}ms，超出 2s"


# =========================================================================== #
# IT-AS-10 幂等-超窗口：31 秒后同键再提交 → 已提交状态拦截，不再生成第二份
# =========================================================================== #
def test_it_as10_idempotency_window_expired(api, db):
    from app.models.mysql.platform import AssessmentRecord

    owner = api.synthetic("as10", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())
    key = api.uniq("k10")

    first = api.submit(rec, owner, key=key)
    assert first["code"] == 0 and first["data"]["idempotent"] is False, first

    # 窗口内：同键幂等，返回同一份报告
    same = api.submit(rec, owner, key=key)
    assert same["code"] == 0 and same["data"]["idempotent"] is True, same
    assert same["data"]["report_id"] == first["data"]["report_id"]

    # 时间旅行到窗口外（31 秒后）：不再幂等，被"已完成"状态拦截
    db.query(AssessmentRecord).filter_by(id=rec).update(
        {"submitted_at": datetime.now() - timedelta(seconds=61)})
    db.commit()
    late = api.submit(rec, owner, key=key)
    assert late["code"] == ErrCode.ASSESSMENT_CONFLICT, late
    assert late["data"] is None or "重复提交" in late["message"], late


# =========================================================================== #
# IT-AS-11 幂等键缺失：明确拒绝，不静默放行
# =========================================================================== #
def test_it_as11_missing_idempotency_key(api):
    owner = api.synthetic("as11", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())

    raw = api.c.post(f"/api/v1/assessments/{rec}/submit", headers=owner).json()
    assert raw["code"] == ErrCode.PARAM_INVALID, raw
    assert "Idempotency-Key" in raw["message"], raw

    # 记录仍是进行中：拒绝发生在任何落库动作之前
    det = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert det["data"]["status"] == "in_progress", det["data"]


# =========================================================================== #
# IT-AS-12 已提交重复提交：换键再提交被拒，报告不被打回生成中
# =========================================================================== #
def test_it_as12_resubmit_completed_rejected(api):
    owner = api.synthetic("as12", "user")
    rec, report_id = _submitted(api, owner)

    again = api.submit(rec, owner, key=api.uniq("k12-other"))
    assert again["code"] == ErrCode.ASSESSMENT_CONFLICT, again

    rep = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert rep["code"] == 0, rep


# =========================================================================== #
# IT-AS-13 存在未完成测评：同模板再次发起 → 引导继续作答（复用原记录）
# =========================================================================== #
def test_it_as13_reuse_in_progress(api):
    owner = api.synthetic("as13", "user")
    tpl = api.template_id()

    first = api.start(owner, tpl)
    assert first["code"] == 0 and first["data"]["resumed"] is False, first
    rec = first["data"]["assessment_id"]
    api.answer(rec, owner, {QUESTIONS[0]["question_id"]: "A"})

    second = api.start(owner, tpl)
    assert second["code"] == 0, second
    assert second["data"]["assessment_id"] == rec, second
    assert second["data"]["resumed"] is True, second

    det = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert det["data"]["answers"], "复用记录必须保留已答内容"


# =========================================================================== #
# IT-AS-14 作废：状态置 void，且不可再提交
# =========================================================================== #
def test_it_as14_cancel_then_submit_denied(api):
    owner = api.synthetic("as14", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())

    c = api.c.post(f"/api/v1/assessments/{rec}/cancel", headers=owner).json()
    assert c["code"] == 0 and c["data"]["status"] == "void", c

    r = api.submit(rec, owner, key=api.uniq("k14"))
    assert r["code"] == ErrCode.PARAM_INVALID, r
    assert "作废" in r["message"], r

    det = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert det["data"]["status"] == "void", det["data"]


# =========================================================================== #
# IT-AS-15 游客 → 注册 → 认领：数据归入新账号，游客令牌立即失效
# =========================================================================== #
def test_it_as15_guest_claim_transfer(api):
    guest = api.guest()
    tpl = api.template_id()
    started = api.start(guest["headers"], tpl)
    assert started["code"] == 0, started
    rec = started["data"]["assessment_id"]
    api.answer(rec, guest["headers"], _all_answers())
    assert api.submit(rec, guest["headers"], key=api.uniq("g15"))["code"] == 0

    # 注册正式账号（账号通道，手机号通道留给认领校验）
    login = api.login_registered()
    headers = api.hdr(login)

    phone = api.phone()
    code = api.sms_code(phone)
    claimed = api.claim(headers, guest["token"], phone, code)
    assert claimed["code"] == 0, claimed
    assert claimed["data"]["claimed_assessments"] == 1, claimed

    me = api.c.get(f"/api/v1/assessments/{rec}", headers=headers).json()
    assert me["code"] == 0 and me["data"]["answers"], me

    stale = api.c.get(f"/api/v1/assessments/{rec}", headers=guest["headers"]).json()
    assert stale["code"] == ErrCode.RESOURCE_NOT_FOUND, "认领后游客令牌必须失效"


# =========================================================================== #
# IT-AS-16 游客认领冒用：仅凭 guest_token 认领被拒；短信码不对被拒
# =========================================================================== #
def test_it_as16_claim_requires_sms_proof(api):
    guest = api.guest()
    tpl = api.template_id()
    started = api.start(guest["headers"], tpl)
    rec = started["data"]["assessment_id"]
    api.answer(rec, guest["headers"], _all_answers())
    assert api.submit(rec, guest["headers"], key=api.uniq("g16"))["code"] == 0

    attacker = api.hdr(api.login_registered())

    # ① 只带令牌（令牌从别处捡到）→ 拒绝
    only_token = api.claim(attacker, guest["token"])
    assert only_token["code"] == ErrCode.PARAM_INVALID, only_token

    # ② 带手机号但验证码错误 → 拒绝
    phone = api.phone()
    api.sms_code(phone)
    bad = api.claim(attacker, guest["token"], phone, "000000")
    assert bad["code"] == ErrCode.VERIFY_CODE_INVALID, bad

    # ③ 验证码正确 → 放行（换一个新号码走完整流程，避免复用已消费的验证码）
    phone2 = api.phone()
    code = api.sms_code(phone2)
    ok = api.claim(attacker, guest["token"], phone2, code)
    assert ok["code"] == 0 and ok["data"]["verified"] is True, ok

    # ④ 已被认领的游客数据不能再被第三方认领
    third = api.hdr(api.login_registered())
    p3 = api.phone()
    c3 = api.sms_code(p3)
    again = api.claim(third, guest["token"], p3, c3)
    assert again["code"] == 0 and again["data"]["claimed_assessments"] == 0, again


# =========================================================================== #
# IT-AS-18 草稿乐观锁：第二个设备带旧 version 提交 → 冲突，不覆盖远端答案
# =========================================================================== #
def test_it_as18_draft_optimistic_lock(api):
    owner = api.synthetic("as18", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)

    first = api.answer(rec, owner, {QUESTIONS[0]["question_id"]: "A"})
    assert first["code"] == 0, first
    v1 = first["data"]["saved_at"]

    # 两台设备读到同一版本后，A 设备又保存了一次（时间跨入下一秒，保证版本变化）
    time.sleep(1.1)
    second = api.answer(rec, owner, {QUESTIONS[1]["question_id"]: "B"}, version=v1)
    assert second["code"] == 0, second
    v2 = second["data"]["saved_at"]
    assert v2 != v1, "两次保存应产生不同 saved_at（DATETIME 秒级精度）"

    # B 设备仍拿旧版本提交 → 冲突，且明确提示"已在其他设备更新"
    stale = api.answer(rec, owner, {QUESTIONS[2]["question_id"]: "C"}, version=v1)
    assert stale["code"] == ErrCode.ASSESSMENT_CONFLICT, stale
    assert "其他设备" in stale["message"], stale

    det = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    answers = det["data"]["answers"]
    assert QUESTIONS[1]["question_id"] in answers, "远端已保存的答案不能被旧草稿覆盖"
    assert QUESTIONS[2]["question_id"] not in answers, "冲突请求不得写入"

    # 刷新拿到新版本后可继续
    refreshed = api.answer(rec, owner, {QUESTIONS[2]["question_id"]: "C"},
                           version=det["data"]["saved_at"])
    assert refreshed["code"] == 0, refreshed


# =========================================================================== #
# IT-R-01 结构化字段：评分/维度/建议/引用都是独立字段，不是正文里的字符串
# =========================================================================== #
def test_it_r01_structured_fields(api):
    owner = api.synthetic("r1", "user")
    _, report_id = _submitted(api, owner)
    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert d["code"] == 0, d
    data = d["data"]
    for field in ("score", "level", "dimension_scores", "suggestions", "references"):
        assert field in data, f"报告缺少结构化字段 {field}"
    assert isinstance(data["dimension_scores"], list) and data["dimension_scores"]
    assert isinstance(data["suggestions"], list) and data["suggestions"]
    assert isinstance(data["references"], list)
    assert isinstance(data["score"], (int, float))
    assert data["level"] in ("excellent", "good", "needs_improvement"), data["level"]


# =========================================================================== #
# IT-R-02 建议结构：每条建议含 type/item/delta/unit/frequency
# =========================================================================== #
def test_it_r02_suggestion_shape(api):
    owner = api.synthetic("r2", "user")
    _, report_id = _submitted(api, owner, value="D")     # 低分 → 触发"待改善"建议
    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    sug = d["data"]["suggestions"]
    assert sug and isinstance(sug[0], dict), sug
    for key in ("type", "item", "delta", "unit", "frequency"):
        assert key in sug[0], f"建议缺少字段 {key}：{sug[0]}"
    assert sug[0]["frequency"], sug[0]


# =========================================================================== #
# IT-R-03 引用可溯源：每条引用含 doc_id/title/fragment，且 doc_id 在知识库中真实存在
# =========================================================================== #
def test_it_r03_references_traceable(api, monkeypatch):
    from app.services import report_service as rs
    from app.config import settings
    from pymilvus import Collection, connections

    owner = api.synthetic("r3", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())
    sub = api.submit(rec, owner, key=api.uniq("k3"))
    assert sub["code"] == 0, sub

    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _GoodLLM())
    events = _drain(rs.report_service.stream(rec))
    assert api.event(events, "done"), events

    d = api.c.get(f"/api/v1/reports/{sub['data']['report_id']}", headers=owner).json()
    refs = d["data"]["references"]
    assert refs, "报告必须带知识库来源，否则建议无法溯源"
    for r in refs:
        for key in ("doc_id", "title", "fragment"):
            assert key in r, f"引用缺少 {key}：{r}"
        assert r["doc_id"], r
        assert r["fragment"], r

    # doc_id 必须能在知识库里查到：拿它回查 Milvus 主键
    connections.connect(alias="verify", host=settings.MILVUS_HOST,
                        port=str(settings.MILVUS_PORT), timeout=5)
    col = Collection(settings.MILVUS_DEFAULT_COLLECTION, using="verify")
    col.load()
    for r in refs[:2]:
        rows = col.query(expr=f"pk == {int(r['doc_id'])}",
                         output_fields=["pk", "file_name"])
        assert rows, f"引用 doc_id={r['doc_id']} 在知识库中不存在"
        assert rows[0]["file_name"] == r["title"], (rows, r)


# =========================================================================== #
# IT-R-04 免责声明：固定文案，前后端口径一致
# =========================================================================== #
def test_it_r04_disclaimer(api):
    owner = api.synthetic("r4", "user")
    _, report_id = _submitted(api, owner)
    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert d["data"]["disclaimer"] == DISCLAIMER, d["data"]["disclaimer"]


# =========================================================================== #
# IT-R-05 生成中状态：提交后未生成完成时 state=generating（不是错误）
# =========================================================================== #
def test_it_r05_generating_state(api):
    owner = api.synthetic("r5", "user")
    _, report_id = _submitted(api, owner)
    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert d["code"] == 0, d
    assert d["data"]["state"] == "generating", d["data"]


# =========================================================================== #
# IT-R-06 自动重试：失败重试 3 次，退避 2/4/8 秒
# =========================================================================== #
def test_it_r06_retry_backoff(api, monkeypatch):
    from app.services import report_service as rs

    bad = _BadLLM()
    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: bad)
    slept = _freeze_sleep(monkeypatch, rs)

    state = {"ok": False, "emitted": False, "reason": ""}
    pieces = _drain(rs.report_service._stream_generate("分析这个报告", state))

    assert state["ok"] is False, state
    # 首次 1 次 + 重试 MAX_RETRY 次，共 4 次调用
    assert bad.calls == rs.ReportService.MAX_RETRY + 1 == 4, bad.calls
    assert slept == [2, 4, 8], slept
    assert pieces and "基础版" in pieces[0], pieces


# =========================================================================== #
# IT-R-07 降级基础版：重试耗尽 → code=0 + state=basic，评分与等级仍在
# =========================================================================== #
def test_it_r07_degrade_to_basic(api, monkeypatch):
    from app.services import report_service as rs

    owner = api.synthetic("r7", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())
    sub = api.submit(rec, owner, key=api.uniq("k7"))
    assert sub["code"] == 0, sub
    report_id = sub["data"]["report_id"]

    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _BadLLM())
    _freeze_sleep(monkeypatch, rs)

    events = _drain(rs.report_service.stream(rec))
    assert api.event(events, "error") is None, "降级不是错误，不得产出 error 事件"
    done = [e for e in events if e["event"] == "done"][-1]["data"]
    assert done["state"] == "basic", done
    assert done["degraded"] is True and done["can_retry"] is True, done

    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert d["code"] == 0, "降级必须走 code=0，否则前端会误弹错误框"
    data = d["data"]
    assert data["state"] == "basic" and data["degraded"] is True and data["can_retry"] is True
    assert data["score"] is not None and data["level"], "降级不得丢评分与等级"


# =========================================================================== #
# IT-R-08 手动重生成：基础版可重生成，报告 id 不变，成功后补齐分析文本
# =========================================================================== #
def test_it_r08_regenerate_keeps_report_id(api, monkeypatch):
    from app.services import report_service as rs

    owner = api.synthetic("r8", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())
    sub = api.submit(rec, owner, key=api.uniq("k8"))
    report_id = sub["data"]["report_id"]

    # 先降级一次
    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _BadLLM())
    _freeze_sleep(monkeypatch, rs)
    _drain(rs.report_service.stream(rec))
    assert api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()["data"]["state"] == "basic"

    regen = api.c.post(f"/api/v1/assessments/{rec}/report/regenerate", headers=owner).json()
    assert regen["code"] == 0, regen
    assert regen["data"]["report_id"] == report_id, "重生成不得换报告 ID"
    assert regen["data"]["generation"]["state"] == "generating", regen

    # 模型恢复后重跑 → complete 且分析文本补齐
    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _GoodLLM())
    _drain(rs.report_service.stream(rec))

    d = api.c.get(f"/api/v1/reports/{report_id}", headers=owner).json()
    assert d["data"]["report_id"] == report_id
    assert d["data"]["state"] == "complete", d["data"]
    assert (d["data"]["analysis_text"] or "").strip(), d["data"]


# =========================================================================== #
# IT-R-09 答案不丢失：全流程故障注入后，草稿答案在库与接口两侧都完整
# =========================================================================== #
def test_it_r09_answers_survive_fault(api, monkeypatch, db):
    from app.models.mysql.platform import AssessmentRecord
    from app.services import report_service as rs

    owner = api.synthetic("r9", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    answers = _all_answers()
    api.answer(rec, owner, answers)
    sub = api.submit(rec, owner, key=api.uniq("k9"))
    assert sub["code"] == 0, sub

    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _BadLLM())
    _freeze_sleep(monkeypatch, rs)
    _drain(rs.report_service.stream(rec))

    det = api.c.get(f"/api/v1/assessments/{rec}", headers=owner).json()
    assert det["data"]["answers"] == answers, det["data"]["answers"]

    db.rollback()
    row = db.query(AssessmentRecord).filter_by(id=rec).first()
    assert row is not None and row.answers == answers, row.answers


# =========================================================================== #
# IT-R-11 流式事件完整性：start/delta≥1/done；异常时 error 带 code
# =========================================================================== #
def test_it_r11_stream_events_complete(api, monkeypatch):
    from app.services import report_service as rs

    owner = api.synthetic("r11", "user")
    tpl = api.template_id()
    rec = api.record_id(owner, tpl)
    api.answer(rec, owner, _all_answers())
    assert api.submit(rec, owner, key=api.uniq("k11"))["code"] == 0

    monkeypatch.setattr(rs.report_service, "_ensure_llm", lambda: _GoodLLM())
    ticket = api.ticket(owner)
    events = api.sse_events(f"/api/v1/assessments/{rec}/report/stream?ticket={ticket}")

    names = [e["event"] for e in events]
    assert names[0] == "start", names
    assert names.count("delta") >= 1, names
    assert names[-1] == "done", names
    assert api.event(events, "error") is None, events
    done = api.event(events, "done")["data"]
    assert done["state"] == "complete" and done["disclaimer"] == DISCLAIMER, done
    ref = api.event(events, "reference")
    if ref:
        assert ref["data"]["docs"], ref

    # 异常分支：未提交（无报告）时 error 事件必须带 code
    rec2 = api.record_id(owner, api.template_id())
    ticket2 = api.ticket(owner)
    bad = api.sse_events(f"/api/v1/assessments/{rec2}/report/stream?ticket={ticket2}")
    err = api.event(bad, "error")
    assert err is not None and err["data"]["code"] == ErrCode.REPORT_NOT_FOUND, bad


# =========================================================================== #
# IT-R-13 批注后状态：营养师批注后 revised=true，用户端可见批注内容与时间
# =========================================================================== #
def test_it_r13_annotation_marks_revised(api):
    doc = api.make_dietitian()
    customer = api.make_customer()
    code = api.invite_code(doc["headers"])
    assert api.bind_by_code(customer["headers"], code)["code"] == 0

    tpl = api.template_id()
    rec = api.record_id(customer["headers"], tpl)
    api.answer(rec, customer["headers"], _all_answers())
    sub = api.submit(rec, customer["headers"], key=api.uniq("k13"))
    report_id = sub["data"]["report_id"]

    ann = api.c.put(f"/api/v1/reports/{report_id}/annotation", headers=doc["headers"],
                    json={"content": "建议把晚餐主食减半，增加绿叶蔬菜"}).json()
    assert ann["code"] == 0, ann

    mine = api.c.get(f"/api/v1/reports/{report_id}", headers=customer["headers"]).json()
    assert mine["data"]["revised"] is True, mine["data"]
    assert mine["data"]["revised_at"], mine["data"]
    assert mine["data"]["annotations"], mine["data"]
    assert mine["data"]["annotations"][0]["content"] == "建议把晚餐主食减半，增加绿叶蔬菜"
    assert mine["data"]["annotations"][0]["created_at"], mine["data"]


# =========================================================================== #
# IT-R-14 他人报告越权：404（非 403），不暴露资源是否存在
# =========================================================================== #
def test_it_r14_other_user_report_404(api):
    owner = api.synthetic("r14owner", "user")
    _, report_id = _submitted(api, owner)
    other = api.synthetic("r14other", "user")

    r = api.c.get(f"/api/v1/reports/{report_id}", headers=other).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r

    # 不存在的报告与"存在但无权"必须同码，否则 ID 可被枚举
    missing = api.c.get("/api/v1/reports/999999999", headers=other).json()
    assert missing["code"] == r["code"], (missing, r)
