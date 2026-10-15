# -*- coding: utf-8 -*-
"""时效类业务规则集成测试（BV-21 / BV-24 / BV-25 / BV-27）。

连真实 MySQL（不依赖 Redis）。验证四条「时效」规则在边界上的真实行为：
  - BV-21 游客配额：游客第 2 次测评、第 6 次查询被拒并引导注册
  - BV-24 草稿有效期：超过 30 天未提交的草稿自动作废
  - BV-25 咨询超时：29 分钟仍在队列，31 分钟自动置为 timeout
  - BV-27 认领期限：7 天内的游客测评可认领，超期拒绝

造数一律带 run_id 后缀并登记清理，跑完零残留。
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.errors import ErrCode
from app.services.assessment_service import guest_hash_of
from app.utils.db import get_session
from app.utils.security import create_guest_token


# --------------------------------------------------------------------------- #
# 造数工具
# --------------------------------------------------------------------------- #
def _create_published_template(client, auth_factory, run_id, cleanup, suffix=""):
    """建一个已发布模板（含 1 个单题维度），返回 template_id。"""
    admin = auth_factory(f"{run_id}_adm", "admin")
    code = f"TPL_{run_id}{suffix}"
    r = client.post("/api/v1/templates", headers=admin, json={
        "code": code,
        "name": "时效用例模板",
        "type": "nutrition",
        "dimension_config": [{"key": "basic", "name": "基础营养", "weight": 1.0}],
        "question_config": {"questions": [{
            "question_id": "q1", "dimension": "basic", "type": "single", "required": True,
            "options": [{"value": "A", "score": 4}, {"value": "B", "score": 2}],
        }]},
    }).json()
    assert r["code"] == 0, r
    tpl_id = r["data"]["id"]
    ar = client.post(f"/api/v1/templates/{tpl_id}/audit", headers=admin,
                     json={"approve": True}).json()
    assert ar["code"] == 0, ar
    cleanup.append(lambda s, i=tpl_id: s.execute(
        text("delete from assessment_template where id=:i"), {"i": i}))
    return tpl_id


def _guest():
    """签发游客令牌，返回 (原始令牌, 请求头, 归属哈希)。"""
    token = create_guest_token()
    return token, {"Authorization": f"Bearer {token}"}, guest_hash_of(token)


def _exec(sql, params=None, commit=True):
    session = get_session()
    try:
        res = session.execute(text(sql), params or {})
        if commit:
            session.commit()
        return res
    finally:
        session.close()


def _scalar(sql, params=None):
    session = get_session()
    try:
        return session.execute(text(sql), params or {}).scalar()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# BV-21 游客测评配额
# --------------------------------------------------------------------------- #
def test_bv21_guest_assessment_quota(client, run_id, auth_factory, cleanup):
    tpl_id = _create_published_template(client, auth_factory, run_id, cleanup, "_q")
    _, gheaders, gh = _guest()
    # ⚠️ conftest 的 cleanup 是「逆序执行」：先登记的会后跑。
    # 报告清理依赖父记录仍存在（子查询按 guest_token 找 record_id），
    # 因此必须「先登记删记录、后登记删报告」，真正执行时才是 报告 → 记录 的顺序。
    # （曾因顺序反了导致每跑一轮泄漏 1 条 user_id=NULL 的孤儿报告）
    cleanup.append(lambda s, g=gh: s.execute(
        text("delete from assessment_record where guest_token=:g"), {"g": g}))
    cleanup.append(lambda s, g=gh: s.execute(
        text("delete from assessment_report where record_id in "
             "(select id from assessment_record where guest_token=:g)"), {"g": g}))

    # 第 1 次：发起 → 作答 → 提交，成为「已完成」记录
    r1 = client.post("/api/v1/assessments", headers=gheaders,
                     json={"template_id": tpl_id}).json()
    assert r1["code"] == 0, r1
    rec_id = r1["data"]["assessment_id"]

    rs = client.patch(f"/api/v1/assessments/{rec_id}/answers", headers=gheaders,
                      json={"answers": {"q1": "A"}}).json()
    assert rs["code"] == 0, rs

    rsub = client.post(f"/api/v1/assessments/{rec_id}/submit",
                       headers={**gheaders, "Idempotency-Key": f"g-{run_id}"}).json()
    assert rsub["code"] == 0, rsub
    assert rsub["data"]["status"] == "completed"

    # 第 2 次：达到游客配额上限，被拦截并引导注册
    r2 = client.post("/api/v1/assessments", headers=gheaders,
                     json={"template_id": tpl_id}).json()
    assert r2["code"] == ErrCode.GUEST_NOT_ALLOWED, r2
    assert "注册" in r2["message"]


# --------------------------------------------------------------------------- #
# BV-21 游客查询配额
# --------------------------------------------------------------------------- #
def test_bv21_guest_query_quota(client, run_id, cleanup):
    _, gheaders, gh = _guest()
    # 预置 5 条游客会话，等价于已用满 5 次免费查询
    _exec("insert into chat_session (user_id, guest_token, title, message_count, created_at) "
          "values (null, :g, '配额用例', 2, now()), (null, :g, '配额用例', 2, now()), "
          "(null, :g, '配额用例', 2, now()), (null, :g, '配额用例', 2, now()), "
          "(null, :g, '配额用例', 2, now())", {"g": gh})
    cleanup.append(lambda s, g=gh: s.execute(
        text("delete from chat_message where session_id in "
             "(select id from chat_session where guest_token=:g)"), {"g": g}))
    cleanup.append(lambda s, g=gh: s.execute(
        text("delete from chat_session where guest_token=:g"), {"g": g}))

    # 第 6 次查询应被限流（配额 5），且不触发 LLM 调用（快速失败）
    r = client.post("/api/v1/chat/query", headers=gheaders,
                    json={"question": "高血压人群日常饮食要注意什么"}).json()
    assert r["code"] == ErrCode.QUERY_RATE_LIMITED, r
    assert "注册" in r["message"]


# --------------------------------------------------------------------------- #
# BV-24 草稿有效期
# --------------------------------------------------------------------------- #
def test_bv24_draft_expiry(client, run_id, auth_factory, cleanup):
    tpl_id = _create_published_template(client, auth_factory, run_id, cleanup, "_e")
    headers = auth_factory(f"{run_id}_draft", "user")

    r1 = client.post("/api/v1/assessments", headers=headers,
                     json={"template_id": tpl_id}).json()
    assert r1["code"] == 0, r1
    rec_id = r1["data"]["assessment_id"]
    cleanup.append(lambda s, i=rec_id: s.execute(
        text("delete from assessment_record where id=:i"), {"i": i}))

    # 把过期时间改到过去，模拟「第 31 天」
    _exec("update assessment_record set expire_at=:t where id=:i",
          {"t": datetime.now() - timedelta(days=1), "i": rec_id})

    r2 = client.patch(f"/api/v1/assessments/{rec_id}/answers", headers=headers,
                      json={"answers": {"q1": "A"}}).json()
    assert r2["code"] == ErrCode.PARAM_INVALID, r2
    assert "过期" in r2["message"]

    # 过期草稿应已被置为作废
    assert _scalar("select status from assessment_record where id=:i",
                   {"i": rec_id}) == "void"


# --------------------------------------------------------------------------- #
# BV-25 咨询超时
# --------------------------------------------------------------------------- #
def test_bv25_consultation_timeout(client, run_id, auth_factory, cleanup):
    user_id = f"{run_id}_cust"
    headers = auth_factory(user_id, "user")

    now = datetime.now()
    ids = []
    for minutes in (29, 31):
        res = _exec("insert into consultation (user_id, type, status, created_at) "
                    "values (:u, 'human', 'pending', :c)",
                    {"u": user_id, "c": now - timedelta(minutes=minutes)})
        ids.append(res.lastrowid)

    cleanup.append(lambda s, I=list(ids): s.execute(
        text("delete from consultation_message where consultation_id in "
             f"({','.join(str(i) for i in I)})")))
    cleanup.append(lambda s, I=list(ids): s.execute(
        text(f"delete from consultation where id in ({','.join(str(i) for i in I)})")))

    r = client.get("/api/v1/consultations", headers=headers).json()
    assert r["code"] == 0, r
    status = {c["consultation_id"]: c["status"] for c in r["data"]["items"]}

    assert status[ids[0]] == "pending", status   # 第 29 分钟：仍在待接入队列
    assert status[ids[1]] == "timeout", status   # 第 31 分钟：已超时关闭


# --------------------------------------------------------------------------- #
# BV-27 认领期限
# --------------------------------------------------------------------------- #
def test_bv27_claim_deadline(client, run_id, auth_factory, cleanup):
    token, _, gh = _guest()
    headers = auth_factory(f"{run_id}_claim", "user")

    now = datetime.now()
    # 6 天前（可认领）与 8 天前（超期）各一条已完成游客记录
    _exec("insert into assessment_record "
          "(user_id, guest_token, template_id, template_version, status, answers, started_at) "
          "values (null, :g, 1, 1, 'completed', '{}', :t1), "
          "       (null, :g, 1, 1, 'completed', '{}', :t2)",
          {"g": gh, "t1": now - timedelta(days=6), "t2": now - timedelta(days=8)})
    cleanup.append(lambda s, g=gh: s.execute(
        text("delete from assessment_record where guest_token=:g"), {"g": g}))

    r = client.post("/api/v1/assessments/guest/claim", headers=headers,
                    json={"guest_token": token}).json()
    assert r["code"] == 0, r
    # 仅 6 天内那条被认领，8 天前的超期记录被拒绝
    assert r["data"]["claimed_assessments"] == 1, r
