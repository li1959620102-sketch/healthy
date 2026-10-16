# -*- coding: utf-8 -*-
"""冒烟补测（用例集第二章未覆盖的 8 条：SM-02 / SM-06 / SM-10 / SM-16 /
SM-19 / SM-20 / SM-23 / SM-25）。

为什么单列一组：这 8 条是 P0，原回填时判为「未覆盖」。
它们共同点是**需要真实数据链**（真实短信缓存、真实绑定关系、真实报告落库），
用合成令牌 + 真实 MySQL 一条条跑通，作为「主线可用」的证据。

断言口径：以 app/core/errors.py 的已落地错误码为准。
"""
import pytest

from app.core.errors import ErrCode
from tests.integration.conftest import QUESTIONS

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# SM-02 发送验证码：返回 6 位码（开发态演示模式），并写入 Redis 供注册消费
# --------------------------------------------------------------------------- #
def test_sm02_sms_code_issued(api):
    phone = api.phone()
    r = api.c.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
    assert r["code"] == 0, r
    data = r["data"]
    assert data["expires_in"] == 300, data          # 5 分钟有效
    assert data["interval"] == 60, data             # 60s 重发限频
    code = data.get("demo_code")
    assert code and len(code) == 6 and code.isdigit(), data

    # 该码可真正完成注册（证明不是"只看日志"的假通过）
    reg = api.register(phone=phone, verify_code=code)
    assert reg["code"] == 0, reg


# --------------------------------------------------------------------------- #
# SM-06 未登录访问：受保护接口返回 10001
# --------------------------------------------------------------------------- #
def test_sm06_unauthenticated_blocked(client):
    r = client.get("/api/v1/users/me").json()
    assert r["code"] == ErrCode.UNAUTHENTICATED, r

    # 令牌格式非法同样拒绝（避免"随便写个 Bearer"就放行）
    bad = client.get("/api/v1/users/me",
                     headers={"Authorization": "Bearer not-a-token"}).json()
    assert bad["code"] == ErrCode.TOKEN_INVALID, bad


# --------------------------------------------------------------------------- #
# SM-10 获取题目：发起测评后拿到题目与空草稿，进度为 0/N
# --------------------------------------------------------------------------- #
def test_sm10_questions_and_empty_draft(api):
    headers = api.synthetic("sm10", "user")
    tpl_id = api.template_id()
    started = api.start(headers, tpl_id)
    assert started["code"] == 0, started
    rec_id = started["data"]["assessment_id"]

    detail = api.c.get(f"/api/v1/assessments/{rec_id}", headers=headers).json()
    assert detail["code"] == 0, detail
    data = detail["data"]
    assert data["status"] == "in_progress", data
    assert len(data["questions"]) == len(QUESTIONS), data["questions"]
    assert data["answers"] in ({}, None, []), data          # 空草稿
    assert data["progress"].endswith("/4"), data["progress"]


# --------------------------------------------------------------------------- #
# SM-16 拒答策略：用药类问题不进模型，直接拒答并给出就医引导
# --------------------------------------------------------------------------- #
def test_sm16_refuse_medical_advice(api):
    headers = api.synthetic("sm16", "user")
    events = api.post_sse("/api/v1/chat/query", headers,
                          {"question": "我该吃多少降压药", "agent": "dietitian"})
    done = api.event(events, "done")
    assert done is not None, events
    assert done["data"]["refused"] is True, done
    assert done["data"]["reason"] == "medical_advice", done

    deltas = "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")
    assert "医生" in deltas or "就医" in deltas, deltas      # 就医引导，不是冷拒


# --------------------------------------------------------------------------- #
# SM-19 营养师客户列表：只返回已绑定客户，未绑定客户不可见
# --------------------------------------------------------------------------- #
def test_sm19_dietitian_customer_list_only_bound(api):
    doc = api.make_dietitian()
    code = api.invite_code(doc["headers"])
    bound = api.make_customer("bound")
    stranger = api.make_customer("stranger")

    bind = api.bind_by_code(bound["headers"], code)
    assert bind["code"] == 0, bind
    api.clean_bind(doc["user_id"], bound["user_id"])

    r = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert r["code"] == 0, r
    ids = {it["customer_id"] for it in r["data"]["items"]}
    assert bound["user_id"] in ids, r["data"]
    assert stranger["user_id"] not in ids, "未绑定客户不应出现在客户列表"


# --------------------------------------------------------------------------- #
# SM-20 资质审核：审核通过前不可用营养师业务能力，通过后放行
# --------------------------------------------------------------------------- #
def test_sm20_dietitian_audit_gate(api):
    doc = api.make_dietitian(approve=False)

    # 待审核：看自己的审核进度可以（/me），但客户数据一律拦截
    me_before = api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()
    assert me_before["code"] == 0, me_before
    assert me_before["data"]["audit_status"] == "pending", me_before["data"]

    denied = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert denied["code"] == ErrCode.DIETITIAN_NOT_FOUND, denied      # 30001

    # 审核通过 → 业务接口放行
    audited = api.audit_dietitian(doc["user_id"])
    assert audited["code"] == 0, audited

    after = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert after["code"] == 0, after
    me_after = api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()
    assert me_after["data"]["audit_status"] == "approved", me_after["data"]


# --------------------------------------------------------------------------- #
# SM-23 趋势看板：同模板历史得分点，按时间升序
# --------------------------------------------------------------------------- #
def test_sm23_personal_trend_ordered(api):
    headers = api.synthetic("sm23", "user")
    tpl_id = api.template_id()

    scores = []
    for i in range(2):
        rec = api.record_id(headers, tpl_id)
        api.answer(rec, headers)
        sub = api.submit(rec, headers, key=api.uniq("k23"))
        assert sub["code"] == 0, sub
        scores.append(sub["data"]["score"])

    r = api.c.get(f"/api/v1/dashboard/personal/trend?template_id={tpl_id}",
                  headers=headers).json()
    assert r["code"] == 0, r
    points = r["data"]["points"]
    assert len(points) == 2, r["data"]
    assert [p["score"] for p in points] == scores, points
    dates = [p["date"] for p in points]
    assert dates == sorted(dates), f"趋势点应按时间升序：{dates}"


# --------------------------------------------------------------------------- #
# SM-25 注销账号：软删除 + 登记 15 日清理任务
# --------------------------------------------------------------------------- #
def test_sm25_account_deletion_task(api, db):
    from app.models.mysql.platform import DataDeletionTask
    from app.models.mysql.user import SysUser

    login = api.login_registered()
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]
    api.cleanup.append(lambda s, i=user_id: s.query(DataDeletionTask)
                       .filter_by(user_id=i).delete())

    r = api.c.delete("/api/v1/users/me", headers=headers).json()
    assert r["code"] == 0, r

    db.expire_all()
    u = db.query(SysUser).filter_by(user_id=user_id).first()
    assert u.status == "disabled" and u.deleted_at is not None, u

    task = db.query(DataDeletionTask).filter_by(user_id=user_id).first()
    assert task is not None, "注销必须登记数据清理任务（合规 D2）"
    assert set(task.scope.split(",")) == {"mysql", "vector", "minio"}, task.scope
    days = (task.execute_at - task.created_at).days if task.created_at else None
    assert days is not None and 14 <= days <= 15, f"清理任务应在 15 日内执行，实际 {days} 天"
