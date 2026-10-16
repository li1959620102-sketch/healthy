# -*- coding: utf-8 -*-
"""安全与合规用例（SEC-01 ~ SEC-15，共 12 条未覆盖项）。

覆盖：越权读报告 / 越权改资料 / 越权批注 / 水平越权枚举 / JWT 篡改 /
令牌泄漏 / 敏感信息脱敏 / 隐私单独同意 / 注销与删除 / 免责声明 /
审计日志 / 传输安全。

判定口径：
  - 越权一律 `RESOURCE_NOT_FOUND(90002)`（评审 M3：防资源枚举，不区分"不存在/无权限"）；
  - HTTP 层恒 200，断言以响应体 `code` 为准。
"""
import base64
import importlib.util
import json
import pathlib

import pytest

from app.core.errors import ErrCode

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_purger():
    """加载离线清理作业模块（合规 D2 的执行侧）。"""
    path = ROOT / "scripts" / "purge_accounts.py"
    spec = importlib.util.spec_from_file_location("purge_accounts", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_report(api, headers) -> int:
    """造一份属于该用户的报告，返回 report_id（越权用例的靶子）。"""
    tpl_id = api.template_id()
    rec = api.record_id(headers, tpl_id)
    api.answer(rec, headers)
    sub = api.submit(rec, headers, key=api.uniq("ksec"))
    assert sub["code"] == 0, sub
    return sub["data"]["report_id"]


# --------------------------------------------------------------------------- #
# SEC-01 越权读报告：非本人按 404（90002）处理，不泄露存在性
# --------------------------------------------------------------------------- #
def test_sec01_cross_user_report_read_denied(api):
    owner = api.synthetic("owner", "user")
    other = api.synthetic("other", "user")
    rid = _make_report(api, owner)

    mine = api.c.get(f"/api/v1/reports/{rid}", headers=owner).json()
    assert mine["code"] == 0, mine

    denied = api.c.get(f"/api/v1/reports/{rid}", headers=other).json()
    assert denied["code"] == ErrCode.RESOURCE_NOT_FOUND, denied   # 90002，不是 10005


# --------------------------------------------------------------------------- #
# SEC-02 越权改资料：不存在"改他人档案"的入口，且本人数据不受影响
# --------------------------------------------------------------------------- #
def test_sec02_cannot_modify_others_profile(api, db):
    from app.models.mysql.platform import UserProfile

    victim = api.synthetic_user("victim")
    attacker = api.synthetic_user("attacker")
    victim_id = victim["user_id"]
    api.c.put("/api/v1/users/me/profile", headers=victim["headers"],
              json={"height_cm": 170, "weight_kg": 65})

    # 攻击者尝试常见的 RESTful 越权路径：系统不提供"改他人档案"的入口
    resp = api.c.put(f"/api/v1/users/{victim_id}/profile",
                     headers=attacker["headers"], json={"height_cm": 200})
    assert resp.status_code in (404, 405), resp.text      # 无此路由（未匹配由框架直接 404）

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    prof = db.query(UserProfile).filter_by(user_id=victim_id).first()
    assert prof is not None and float(prof.height_cm) == 170.0, "他人档案不应被改写"


# --------------------------------------------------------------------------- #
# SEC-03 越权批注：非绑定营养师按 404 处理
# --------------------------------------------------------------------------- #
def test_sec03_non_bound_dietitian_cannot_annotate(api):
    owner = api.synthetic("s3owner", "user")
    rid = _make_report(api, owner)
    outsider = api.make_dietitian()          # 已审核，但与该用户无绑定关系

    r = api.c.put(f"/api/v1/reports/{rid}/annotation", headers=outsider["headers"],
                  json={"content": "越权批注尝试"}).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r


# --------------------------------------------------------------------------- #
# SEC-04 水平越权枚举：遍历 id 一律同一错误码，不暴露存在性
# --------------------------------------------------------------------------- #
def test_sec04_report_id_enumeration_uniform_404(api):
    owner = api.synthetic("s4owner", "user")
    attacker = api.synthetic("s4attacker", "user")
    rid = _make_report(api, owner)           # 攻击者已知的一个"真实存在"的报告

    codes = set()
    for probe in (rid, rid + 1, rid + 7, 1, 999999):
        body = api.c.get(f"/api/v1/reports/{probe}", headers=attacker).json()
        codes.add(body["code"])
        assert body.get("data") in (None, {}, []), f"越权响应泄露了数据：{body}"

    # 存在的报告与不存在的报告返回完全相同的错误码 → 无法据此枚举
    assert codes == {ErrCode.RESOURCE_NOT_FOUND}, codes


# --------------------------------------------------------------------------- #
# SEC-07 JWT 篡改：改 payload 提权 → 签名校验失败
# --------------------------------------------------------------------------- #
def test_sec07_tampered_jwt_rejected(api):
    login = api.login_registered()
    token = login["data"]["access_token"]
    head, payload, signature, *rest = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["role"] = "admin"
    forged = f"{head}.{_b64u(json.dumps(raw, separators=(',', ':')).encode())}.{signature}"

    r = api.c.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {forged}"}).json()
    assert r["code"] in (ErrCode.TOKEN_INVALID, ErrCode.UNAUTHENTICATED), r


# --------------------------------------------------------------------------- #
# SEC-08 令牌泄漏：SSE 不走 URL 传 JWT，access_token 不能当短票
# --------------------------------------------------------------------------- #
def test_sec08_sse_token_not_in_url(api):
    owner = api.synthetic("s8", "user")
    tpl_id = api.template_id()
    rec = api.record_id(owner, tpl_id)
    api.answer(rec, owner)
    assert api.submit(rec, owner, key=api.uniq("k8"))["code"] == 0      # 报告流需已提交

    # 用 access_token 冒充短票 → 拒绝（短票是独立类型）
    access = owner["Authorization"].split(" ")[1]
    r = api.c.get(f"/api/v1/assessments/{rec}/report/stream?ticket={access}").json()
    assert r["code"] == ErrCode.TOKEN_INVALID, r

    # 正确姿势：先换 60s 短票
    ticket = api.ticket(owner)
    assert ticket and ticket != access
    events = api.sse_events(f"/api/v1/assessments/{rec}/report/stream?ticket={ticket}")
    assert api.event(events, "start") is not None, events

    # 前端源码层面：SSE 只拼 ticket，不把 JWT 放进 URL
    js = (ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "ticket=" in js
    assert "access_token=" not in js, "前端不得把访问令牌拼进 URL（会进日志/Referer）"


# --------------------------------------------------------------------------- #
# SEC-10 敏感信息脱敏：列表接口不返回手机号明文与密文字段
# --------------------------------------------------------------------------- #
def test_sec10_no_plaintext_phone_in_lists(api):
    phone = api.phone()
    reg = api.register(phone=phone)
    assert reg["code"] == 0, reg

    admin = api.synthetic("s10adm", "admin")
    body = api.c.get("/api/v1/admin/users", headers=admin).json()
    assert body["code"] == 0, body
    dumped = json.dumps(body, ensure_ascii=False)
    assert phone not in dumped, "用户列表泄露了手机号明文"
    for field in ("phone_cipher", "phone_hash", "password_hash", "email_cipher"):
        assert field not in dumped, f"用户列表泄露了密文字段 {field}"

    # 管理员单点查询同样不含密文
    uid = api.user_id_of(reg["data"]["username"])
    one = api.c.get("/api/v1/admin/users", headers=admin, params={"keyword": uid}).json()
    assert phone not in json.dumps(one, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# SEC-11 隐私单独同意：未单独同意不得收集疾病史，同意后留痕
# --------------------------------------------------------------------------- #
def test_sec11_disease_history_requires_consent(api, db):
    from app.models.mysql.platform import OperationLog, UserProfile

    login = api.login_registered()
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]
    api.cleanup.append(lambda s, i=user_id: s.query(OperationLog)
                       .filter(OperationLog.operator_id == i).delete())
    api.cleanup.append(lambda s, i=user_id: s.query(UserProfile).filter_by(user_id=i).delete())

    # 1) 未勾选单独同意 → 拒绝落库
    denied = api.c.put("/api/v1/users/me/profile", headers=headers,
                       json={"diseases": ["高血压", "糖尿病"]}).json()
    assert denied["code"] == ErrCode.PARAM_INVALID, denied

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    prof = db.query(UserProfile).filter_by(user_id=user_id).first()
    assert prof is None or prof.diseases_cipher in (None, b"[]"), "未同意不应落库疾病史"

    # 2) 显式同意 → 落库且写入同意留痕（同意记录可查）
    ok = api.c.put("/api/v1/users/me/profile", headers=headers,
                   json={"diseases": ["高血压", "糖尿病"], "sensitive_consent": True}).json()
    assert ok["code"] == 0, ok

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    logs = db.query(OperationLog).filter_by(
        operator_id=user_id, action="consent.sensitive_info").all()
    assert logs, "同意记录必须留痕，否则无法证明同意过程"
    assert logs[0].content.get("scope") == "disease_history", logs[0].content


# --------------------------------------------------------------------------- #
# SEC-12 注销与删除：三处存储的清理链路可执行且可验证
# --------------------------------------------------------------------------- #
def test_sec12_deletion_pipeline_clears_user_data(api, db):
    from app.models.mysql.platform import AssessmentReport, DataDeletionTask, UserProfile
    from app.models.mysql.user import SysUser

    login = api.login_registered()
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]
    _make_report(api, headers)               # 造出一条真实报告，验证会被清理
    api.cleanup.append(lambda s, i=user_id: s.query(DataDeletionTask).filter_by(user_id=i).delete())
    api.cleanup.append(lambda s, i=user_id: s.query(SysUser).filter_by(user_id=i).delete())

    assert api.c.delete("/api/v1/users/me", headers=headers).json()["code"] == 0

    # 注销后不可再登录
    relogin = api.login(login["data"]["user"]["username"])
    assert relogin["code"] != 0, relogin

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    task = db.query(DataDeletionTask).filter_by(user_id=user_id).first()
    assert set(task.scope.split(",")) == {"mysql", "vector", "minio"}, task.scope

    # 执行离线清理作业（三处存储）
    purger = _load_purger()
    conn = purger._conn()
    try:
        info = purger.run_task(conn, task.id, user_id, apply=True)
    finally:
        conn.close()

    assert info["deleted"], f"应删除该用户的个人内容：{info}"
    assert "测评报告" in info["deleted"], info["deleted"]
    assert info["operation_log_anonymized"] >= 1, info
    assert info["sys_user_anonymized"] == 1, info
    assert "平台级语料" in info["vector"], info["vector"]

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    assert db.query(AssessmentReport).filter_by(user_id=user_id).count() == 0
    assert db.query(UserProfile).filter_by(user_id=user_id).count() == 0
    u = db.query(SysUser).filter_by(user_id=user_id).first()
    assert u is not None and u.status == "deleted", u        # 审计可追溯：匿名化而非物理删除
    assert u.username.startswith("deleted_") and u.phone_hash is None, u
    assert db.query(DataDeletionTask).filter_by(id=task.id).first().state == "done"


# --------------------------------------------------------------------------- #
# SEC-13 免责声明：智能体与报告输出均带标准免责语
# --------------------------------------------------------------------------- #
def test_sec13_disclaimer_present(api):
    owner = api.synthetic("s13", "user")
    rid = _make_report(api, owner)
    text = "本内容由 AI 生成，不构成诊疗建议"

    rep = api.c.get(f"/api/v1/reports/{rid}", headers=owner).json()
    assert text in (rep["data"]["disclaimer"] or ""), rep["data"].get("disclaimer")

    insight = api.c.get("/api/v1/dashboard/personal/insight", headers=owner).json()
    assert insight["code"] == 0, insight
    assert text in json.dumps(insight, ensure_ascii=False), insight

    # 智能查询：知识库外的问题走兜底话术，同样带免责声明
    events = api.post_sse("/api/v1/chat/query", owner,
                          {"question": "帮我把家里的路由器改成桥接模式", "agent": "dietitian"})
    deltas = "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")
    assert "不构成诊疗建议" in deltas, deltas


# --------------------------------------------------------------------------- #
# SEC-14 审计日志：敏感操作留痕，非管理员不可读、不可删
# --------------------------------------------------------------------------- #
def test_sec14_audit_log_immutable_to_users(api, db):
    from app.models.mysql.platform import OperationLog

    admin = api.synthetic("s14adm", "admin")
    victim = api.login_registered()
    victim_uid = victim["data"]["user"]["user_id"]
    api.cleanup.append(lambda s, i=victim_uid: s.query(OperationLog)
                       .filter(OperationLog.operator_id == i).delete())

    assert api.c.put(f"/api/v1/admin/users/{victim_uid}/status", headers=admin,
                     json={"status": "disabled"}).json()["code"] == 0

    # 结束当前只读事务再查：MySQL 默认 REPEATABLE READ，同一事务内会一直读旧快照，
    # 光 expire_all() 拿不到离线作业（另一连接）刚提交的结果。
    db.rollback()
    logs = db.query(OperationLog).filter_by(action="user.disable", target_id=victim_uid).all()
    assert logs, "禁用用户的动作必须留痕"
    assert logs[0].operator_id, "审计日志必须记录操作人"
    assert logs[0].content == {"status": "disabled"}, logs[0].content

    # 非管理员读不到审计日志；且不存在删除入口
    plain = api.synthetic("s14u", "user")
    assert api.c.get("/api/v1/admin/operation-logs", headers=plain).json()["code"] == \
        ErrCode.PERMISSION_DENIED
    dele = api.c.delete("/api/v1/admin/operation-logs", headers=admin)
    assert dele.status_code in (404, 405) or dele.json().get("code") != 0


# --------------------------------------------------------------------------- #
# SEC-15 传输安全：生产环境强制 HTTPS 跳转，反代已终止 HTTPS 时不重复跳
# --------------------------------------------------------------------------- #
def test_sec15_https_redirect_in_production(api, monkeypatch):
    from app.config import settings

    assert settings.ENV != "production", "本地回归不应运行在生产态"

    monkeypatch.setattr(settings, "ENV", "production")
    r = api.c.get("/api/health", follow_redirects=False)
    assert r.status_code == 308, r.status_code
    assert r.headers["location"].startswith("https://"), r.headers.get("location")

    # 反向代理已用 X-Forwarded-Proto 告知真实协议 → 不再跳转（否则死循环）
    r2 = api.c.get("/api/health", headers={"X-Forwarded-Proto": "https"},
                   follow_redirects=False)
    assert r2.status_code == 200, r2.status_code
