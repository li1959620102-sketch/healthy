# -*- coding: utf-8 -*-
"""集成测试 · 边界值 API + 主线贯通（连真实 MySQL）。

覆盖测试用例集：BV-01 手机号 / BV-08 疾病史 / BV-13 特殊字符 / BV-14 emoji /
BV-22 短票有效期 / BV-23 幂等窗口 / BV-29 并发提交 / BV-30 空列表，
以及一条「注册→建档→建模板→发起→草稿→提交→报告」的主线，证明 DB 链路真的通。

要点：
  - 所有造数带 run_id 后缀，结束时清理；
  - 错误码断言以**已落地实现**（app/core/errors.py）为准，不以旧版用例集编号为准。
"""
import threading
import time
from datetime import datetime, timedelta

import pytest

from app.core.errors import ErrCode

pytestmark = pytest.mark.integration

# 每个维度 2 题、每题满分 4（该维度满分 8）
OPTIONS = [{"value": "A", "score": 4}, {"value": "B", "score": 3},
           {"value": "C", "score": 2}, {"value": "D", "score": 0}]
DIMS = [{"key": "basic", "name": "营养基础", "weight": 0.5},
        {"key": "diet", "name": "膳食结构", "weight": 0.5}]
QUESTIONS = [{"question_id": f"{d['key']}_{i}", "dimension": d["key"], "type": "single",
              "max_score": 4, "required": True, "options": OPTIONS}
             for d in DIMS for i in range(2)]


# --------------------------------------------------------------------------- #
# 造数与清理工具
# --------------------------------------------------------------------------- #
PASSWORD = "Passw0rd!2026"


def _register(client, username, password=PASSWORD, phone=None, real_name=""):
    return client.post("/api/v1/auth/register", json={
        "username": username, "password": password,
        "real_name": real_name, "phone": phone,
    }).json()


def _login(client, username, password=PASSWORD):
    """注册接口只返回用户信息，令牌要走登录换取（见用例集 SM-03 差异说明）。"""
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": password}).json()


def _headers_of(body):
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


def _clean_user(session, username):
    from app.models.mysql.platform import UserProfile
    from app.models.mysql.user import SysUser
    u = session.query(SysUser).filter_by(username=username).first()
    if u:
        session.query(UserProfile).filter_by(user_id=u.user_id).delete()
        session.query(SysUser).filter_by(user_id=u.user_id).delete()


def _clean_user_ids(session, *user_ids):
    """清理「仅凭令牌合成、从未注册进 sys_user」的测试用户残留。

    ⚠️ 为什么需要它：用例用 `create_access_token(user_id, ...)` 直接签发令牌时，
    user_id 在 sys_user 里没有对应行；而 `POST /assessments` 会由
    `assessment_service` 自动建 `user_profile`（见 assessment_service.py:238）。
    `_clean_user` 是按 username 查 sys_user 的，对这类用户查不到 → 会漏删 user_profile，
    每轮回归静默泄漏 3 行（`_u` / `_idem` / `_conc`），并被 `user_id like 'IT%'` 之外的
    统计口径掩盖。此函数按 user_id 直删，堵住漏点。
    """
    from app.models.mysql.platform import UserProfile

    ids = [u for u in user_ids if u]
    if not ids:
        return
    session.query(UserProfile).filter(
        UserProfile.user_id.in_(ids)).delete(synchronize_session=False)


def _clean_template(session, code):
    from app.models.mysql.platform import AssessmentReport, AssessmentRecord, AssessmentTemplate
    tpl_ids = [t.id for t in session.query(AssessmentTemplate).filter_by(code=code).all()]
    if tpl_ids:
        rec_ids = [r.id for r in session.query(AssessmentRecord)
                   .filter(AssessmentRecord.template_id.in_(tpl_ids)).all()]
        if rec_ids:
            session.query(AssessmentReport).filter(
                AssessmentReport.record_id.in_(rec_ids)).delete(synchronize_session=False)
            session.query(AssessmentRecord).filter(
                AssessmentRecord.id.in_(rec_ids)).delete(synchronize_session=False)
        session.query(AssessmentTemplate).filter(
            AssessmentTemplate.id.in_(tpl_ids)).delete(synchronize_session=False)


@pytest.fixture
def db():
    from app.utils.db import get_session
    session = get_session()
    yield session
    session.close()


# --------------------------------------------------------------------------- #
# BV-01 手机号：非法号段返回参数错误，合法号段放行
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("phone", ["1380000000", "138000000012", "abcdefghijk",
                                   "23800000001", "12800000001"])
def test_bv01_invalid_phone_rejected(client, run_id, cleanup, phone):
    username = f"{run_id}_p_{abs(hash(phone)) % 9999}"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    body = _register(client, username, phone=phone)
    assert body["code"] == ErrCode.PARAM_INVALID, body


def test_bv01_valid_phone_accepted(client, run_id, cleanup, redis_required):
    """BV-01 合法号段：手机号+短信验证码注册成功，可用手机号+密码登录。"""
    from sqlalchemy import text as _text
    from app.utils.security import phone_hash as phone_hmac
    from app.utils.redis_client import redis_client

    phone = "13800000001"
    h = phone_hmac(phone)

    def _clean(s):
        s.execute(_text("DELETE FROM sys_user WHERE phone_hash=:h"), {"h": h})
        redis_client.delete("sms:code:" + h, "sms:try:" + h, "sms:limit:" + h)

    cleanup.append(_clean)
    # 直接向 Redis 写验证码（绕过网关下发，网关逻辑在 test_phone_auth.py 覆盖）
    assert redis_client.set("sms:code:" + h, "123456", ttl=300)
    assert redis_client.set("sms:try:" + h, "0", ttl=300)

    reg = client.post("/api/v1/auth/register", json={
        "phone": phone, "verify_code": "123456", "password": "Abcd@2026"}).json()
    assert reg["code"] == 0, reg

    login = client.post("/api/v1/auth/login", json={
        "phone": phone, "password": "Abcd@2026"}).json()
    assert login["code"] == 0, login
    assert login["data"]["access_token"] and login["data"]["refresh_token"]


# --------------------------------------------------------------------------- #
# BV-08 疾病史：0 项 / 1 项 / 全选 20 项均可保存
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("count", [0, 1, 20])
def test_bv08_diseases_saved(client, run_id, cleanup, db, count):
    from app.models.mysql.platform import UserProfile

    username = f"{run_id}_d_{count}"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    assert _register(client, username)["code"] == 0
    login = _login(client, username)
    assert login["code"] == 0, login
    headers = _headers_of(login)

    diseases = [f"疾病{i}" for i in range(count)]
    r = client.put("/api/v1/users/me/profile",
                   headers=headers, json={"diseases": diseases}).json()
    assert r["code"] == 0, r

    user_id = login["data"]["user"]["user_id"]
    prof = db.query(UserProfile).filter_by(user_id=user_id).first()
    assert prof is not None
    import json
    assert json.loads(prof.diseases_cipher.decode("utf-8")) == diseases


# --------------------------------------------------------------------------- #
# BV-13 特殊字符：脚本片段与 SQL 片段安全落库，转义后原样读回
# --------------------------------------------------------------------------- #
def test_bv13_special_chars_stored_safely(client, run_id, cleanup):
    username = f"{run_id}_xss"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    payload = "<script>alert(1)</script>"
    reg = _register(client, username, real_name=payload)
    assert reg["code"] == 0, reg
    assert reg["data"]["real_name"] == payload

    login = _login(client, username)
    me = client.get("/api/v1/users/me", headers=_headers_of(login)).json()
    assert me["data"]["real_name"] == payload


def test_bv13_sql_injection_attempt_is_not_executed(client, run_id, cleanup):
    """用户名里塞 SQL 片段：参数化绑定应把它当普通字符串，不产生注入。"""
    username = f"{run_id}' OR '1'='1"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    body = _register(client, username)
    assert body["code"] == 0, body
    # 库还在，且能按该用户名精确定位到唯一一行
    from app.models.mysql.user import SysUser
    from app.utils.db import get_session
    s = get_session()
    try:
        assert s.query(SysUser).filter_by(username=username).count() == 1
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# BV-14 emoji / 生僻字：UTF-8MB4 正常存取
# --------------------------------------------------------------------------- #
def test_bv14_emoji_and_rare_chars_roundtrip(client, run_id, cleanup):
    username = f"{run_id}_emoji"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    name = "张三🍎𠮷"
    reg = _register(client, username, real_name=name)
    assert reg["code"] == 0, reg
    login = _login(client, username)
    me = client.get("/api/v1/users/me", headers=_headers_of(login)).json()
    assert me["data"]["real_name"] == name


# --------------------------------------------------------------------------- #
# BV-22 短票有效期：有效票可用，过期票被拒
# --------------------------------------------------------------------------- #
def test_bv22_stream_ticket_issued(client, run_id, cleanup):
    username = f"{run_id}_ticket"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    assert _register(client, username)["code"] == 0
    headers = _headers_of(_login(client, username))
    r = client.post("/api/v1/auth/stream-ticket", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["ticket"] and r["data"]["expires_in"] == 60


def test_bv22_expired_ticket_rejected(client, run_id, cleanup, db):
    """用负有效期直接构造"已过期"的短票，避免用例真等 60 秒。"""
    from app.utils.security import _make_token

    username = f"{run_id}_expired"
    cleanup.append(lambda s, u=username: _clean_user(s, u))
    assert _register(client, username)["code"] == 0

    tpl_id, rec_id = _make_assessment(client, run_id, cleanup, db,
                                      _headers_of(_login(client, username)))
    expired = _make_token("u1", "it", "user", -1, "stream")   # exp 已是过去
    r = client.get(f"/api/v1/assessments/{rec_id}/report/stream?ticket={expired}")
    payload = r.json()
    assert payload["code"] in (ErrCode.TOKEN_INVALID, ErrCode.TOKEN_EXPIRED), payload


# --------------------------------------------------------------------------- #
# BV-23 幂等窗口：30s 内同键返回首次结果；超窗口按新提交处理
# --------------------------------------------------------------------------- #
def test_bv23_idempotency_window(client, run_id, cleanup, db):
    from app.models.mysql.platform import AssessmentRecord
    from app.utils.security import create_access_token

    user_id = f"{run_id}_idem"
    headers = {"Authorization": f"Bearer {create_access_token(user_id, 'it', 'user')}"}
    tpl_id, rec_id = _make_assessment(client, run_id, cleanup, db, headers, user_id=user_id)

    key = f"k-{run_id}"
    client.patch(f"/api/v1/assessments/{rec_id}/answers", headers=headers,
                 json={"answers": {q["question_id"]: "A" for q in QUESTIONS}})

    first = client.post(f"/api/v1/assessments/{rec_id}/submit",
                        headers={**headers, "Idempotency-Key": key}).json()
    assert first["code"] == 0, first
    assert first["data"]["idempotent"] is False

    # 窗口内（第 29 秒的量级）：幂等，返回同一份报告
    second = client.post(f"/api/v1/assessments/{rec_id}/submit",
                         headers={**headers, "Idempotency-Key": key}).json()
    assert second["code"] == 0
    assert second["data"]["idempotent"] is True
    assert second["data"]["report_id"] == first["data"]["report_id"]

    # 把 submitted_at 回拨 60 秒，模拟窗口外（第 31 秒）
    db.query(AssessmentRecord).filter_by(id=rec_id).update(
        {"submitted_at": datetime.now() - timedelta(seconds=60)})
    db.commit()

    third = client.post(f"/api/v1/assessments/{rec_id}/submit",
                        headers={**headers, "Idempotency-Key": key}).json()
    assert third["code"] == 0
    assert third["data"]["idempotent"] is False, "超过 30 秒窗口后不应再视为幂等"


# --------------------------------------------------------------------------- #
# BV-29 并发提交：10 并发同键只能落地一份报告
# --------------------------------------------------------------------------- #
def test_bv29_concurrent_submit_single_report(client, run_id, cleanup, db):
    from app.models.mysql.platform import AssessmentReport
    from app.utils.security import create_access_token

    user_id = f"{run_id}_conc"
    headers = {"Authorization": f"Bearer {create_access_token(user_id, 'it', 'user')}"}
    tpl_id, rec_id = _make_assessment(client, run_id, cleanup, db, headers, user_id=user_id)
    client.patch(f"/api/v1/assessments/{rec_id}/answers", headers=headers,
                 json={"answers": {q["question_id"]: "A" for q in QUESTIONS}})

    key = f"k-conc-{run_id}"
    results, errors = [], []
    barrier = threading.Barrier(10)

    def _submit():
        try:
            barrier.wait(timeout=20)
            r = client.post(f"/api/v1/assessments/{rec_id}/submit",
                            headers={**headers, "Idempotency-Key": key}).json()
            results.append(r)
        except Exception as e:      # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=_submit) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    ok = [r for r in results if r.get("code") == 0]
    assert ok, results
    report_ids = {r["data"]["report_id"] for r in ok}
    assert len(report_ids) == 1, f"并发提交产生了多份报告：{report_ids}"

    db.expire_all()
    assert db.query(AssessmentReport).filter_by(record_id=rec_id).count() == 1


# --------------------------------------------------------------------------- #
# BV-30 空列表：无数据时返回 items=[] + total=0
# --------------------------------------------------------------------------- #
def test_bv30_empty_lists(client, run_id, cleanup):
    from app.utils.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(f'{run_id}_empty', 'it', 'user')}"}
    for path in ("/api/v1/assessments", "/api/v1/diet-plans", "/api/v1/consultations"):
        body = client.get(path, headers=headers).json()
        assert body["code"] == 0, (path, body)
        assert body["data"]["items"] == [], (path, body["data"])
        assert body["data"]["total"] == 0, (path, body["data"])


# --------------------------------------------------------------------------- #
# 主线贯通：注册 → 建档 → 建模板 → 审核 → 发起 → 草稿 → 提交 → 报告
# --------------------------------------------------------------------------- #
def test_mainline_end_to_end(client, run_id, cleanup, db):
    from app.models.mysql.platform import AssessmentReport
    from app.utils.security import create_access_token

    admin = {"Authorization": f"Bearer {create_access_token(f'{run_id}_adm', 'it', 'admin')}"}
    code = f"TPL_{run_id}"
    cleanup.append(lambda s, c=code: _clean_template(s, c))

    # 1) 建模板（管理员）
    tpl = client.post("/api/v1/templates", headers=admin, json={
        "code": code, "name": f"集成测试模板 {run_id}", "type": "nutrition",
        "dimension_config": DIMS, "question_config": QUESTIONS,
    }).json()
    assert tpl["code"] == 0, tpl
    tpl_id = tpl["data"]["id"]

    # 2) 提交审核 → 审核通过
    assert client.post(f"/api/v1/templates/{tpl_id}/submit-audit", headers=admin).json()["code"] == 0
    audit = client.post(f"/api/v1/templates/{tpl_id}/audit",
                        headers=admin, json={"approve": True}).json()
    assert audit["code"] == 0, audit

    # 3) 已发布模板对普通用户可见
    listed = client.get("/api/v1/templates", headers=admin).json()
    assert any(t["id"] == tpl_id for t in listed["data"]["items"])

    # 4) 用户发起测评
    user_id = f"{run_id}_u"
    cleanup.append(lambda s, u=user_id: _clean_user_ids(s, u))
    headers = {"Authorization": f"Bearer {create_access_token(user_id, 'it', 'user')}"}
    started = client.post("/api/v1/assessments", headers=headers,
                          json={"template_id": tpl_id}).json()
    assert started["code"] == 0, started
    rec_id = started["data"]["assessment_id"]
    assert started["data"]["template_snapshot"] and started["data"]["profile_snapshot"] is not None

    # 5) 草稿
    saved = client.patch(f"/api/v1/assessments/{rec_id}/answers", headers=headers,
                         json={"answers": {"basic_0": "A"}}).json()
    assert saved["code"] == 0 and saved["data"]["progress"].startswith("1/")

    # 6) 提交算分
    submit = client.post(f"/api/v1/assessments/{rec_id}/submit",
                         headers={**headers, "Idempotency-Key": f"k-main-{run_id}"}).json()
    assert submit["code"] == 0, submit
    assert submit["data"]["report_id"]
    assert 0.0 <= submit["data"]["score"] <= 100.0
    assert submit["data"]["level"] in ("excellent", "good", "needs_improvement")

    # 7) 报告落库（分析文本由 SSE 通道生成，此处只校验评分骨架）
    rep_id = submit["data"]["report_id"]
    detail = client.get(f"/api/v1/reports/{rep_id}", headers=headers).json()
    assert detail["code"] == 0, detail
    assert detail["data"]["score"] == submit["data"]["score"]
    assert detail["data"]["dimension_scores"]

    db.expire_all()
    assert db.query(AssessmentReport).filter_by(id=rep_id).first() is not None


# --------------------------------------------------------------------------- #
# 内部工具：直接建模板 + 发起测评（给不需要走审核流程的用例用）
# --------------------------------------------------------------------------- #
def _make_assessment(client, run_id, cleanup, db, headers, user_id=None):
    from app.dao.assessment_dao import TemplateDAO, RecordDAO, session_scope
    from app.utils.security import create_access_token

    code = f"TPL_{run_id}"
    cleanup.append(lambda s, c=code: _clean_template(s, c))
    if user_id:
        # 合成用户不在 sys_user 中，必须单独登记 user_profile 清理（否则每轮泄漏）
        cleanup.append(lambda s, u=user_id: _clean_user_ids(s, u))
    admin = {"Authorization": f"Bearer {create_access_token(f'{run_id}_adm', 'it', 'admin')}"}
    tpl = client.post("/api/v1/templates", headers=admin, json={
        "code": code, "name": f"集成模板 {run_id}", "type": "nutrition",
        "dimension_config": DIMS, "question_config": QUESTIONS,
    }).json()
    assert tpl["code"] == 0, tpl
    tpl_id = tpl["data"]["id"]
    client.post(f"/api/v1/templates/{tpl_id}/submit-audit", headers=admin)
    client.post(f"/api/v1/templates/{tpl_id}/audit", headers=admin, json={"approve": True})

    started = client.post("/api/v1/assessments", headers=headers,
                          json={"template_id": tpl_id}).json()
    assert started["code"] == 0, started
    return tpl_id, started["data"]["assessment_id"]
