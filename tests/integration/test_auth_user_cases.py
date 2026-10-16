# -*- coding: utf-8 -*-
"""认证与账号生命周期用例（IT-A-01~08/10、IT-U-02~09，共 17 条）。

分组理由：这批用例全部围绕"令牌与账号状态"，彼此共享短信验证码 / 令牌 /
账号状态机三类前置，放一起才能复用造数、避免短信 60s 限频互相干扰。

判定口径：以 app/core/errors.py 的已落地错误码为准（旧用例集文本中的
20004 / 30003 等编号在本实现中并不存在，见各条注释）。
"""
import threading
import time
from datetime import datetime

import pytest

from app.core.errors import ErrCode

pytestmark = pytest.mark.integration


def _expired_token(user_id: str, role: str = "user", offset: int = -1):
    """构造已过期令牌：直接给负有效期，避免用例真等 2 小时。"""
    from app.utils.security import _make_token
    return _make_token(user_id, "it", role, offset, "access")


# --------------------------------------------------------------------------- #
# IT-A-01 验证码限频：同号 60 秒内第二次发送被拒
# --------------------------------------------------------------------------- #
def test_it_a01_sms_rate_limited(api, redis_required):
    phone = api.phone()
    first = api.c.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
    assert first["code"] == 0, first

    second = api.c.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
    assert second["code"] == ErrCode.SMS_RATE_LIMITED, second
    assert "频繁" in second["message"] or "分钟" in second["message"], second


# --------------------------------------------------------------------------- #
# IT-A-02 验证码错误：填写错误码被拒；连错达上限后作废需重新获取
# --------------------------------------------------------------------------- #
def test_it_a02_wrong_verify_code_rejected(api, redis_required):
    phone = api.phone()
    code = api.sms_code(phone)
    wrong = "0" * 6 if code != "0" * 6 else "1" * 6

    r = api.c.post("/api/v1/auth/register", json={
        "phone": phone, "verify_code": wrong, "password": api.PASSWORD}).json()
    assert r["code"] == ErrCode.VERIFY_CODE_INVALID, r

    # 连错至上限后，正确验证码也应失效（必须重新获取）
    for _ in range(4):
        api.c.post("/api/v1/auth/register", json={
            "phone": phone, "verify_code": wrong, "password": api.PASSWORD})
    after = api.c.post("/api/v1/auth/register", json={
        "phone": phone, "verify_code": code, "password": api.PASSWORD}).json()
    assert after["code"] == ErrCode.VERIFY_CODE_INVALID, after
    assert "重新获取" in after["message"], after


# --------------------------------------------------------------------------- #
# IT-A-03 验证码过期：缓存失效后不可用
# --------------------------------------------------------------------------- #
def test_it_a03_expired_verify_code_rejected(api, redis_required):
    from app.utils.redis_client import redis_client
    from app.utils.security import phone_hash

    phone = api.phone()
    code = api.sms_code(phone)
    # 模拟 TTL 到期：直接清掉验证码缓存（等价于 5 分钟后再用）
    redis_client.delete("sms:code:" + phone_hash(phone))

    r = api.c.post("/api/v1/auth/register", json={
        "phone": phone, "verify_code": code, "password": api.PASSWORD}).json()
    assert r["code"] == ErrCode.VERIFY_CODE_INVALID, r
    assert "失效" in r["message"] or "重新获取" in r["message"], r


# --------------------------------------------------------------------------- #
# IT-A-04 手机号已注册：重复注册被拒
# --------------------------------------------------------------------------- #
def test_it_a04_phone_already_registered(api, redis_required):
    phone = api.phone()
    first = api.register(phone=phone)
    assert first["code"] == 0, first

    # 二次注册走同一手机号：查重在验证码之前，故无需重新取码
    again = api.c.post("/api/v1/auth/register", json={
        "phone": phone, "verify_code": "000000", "password": api.PASSWORD}).json()
    assert again["code"] == ErrCode.USER_EXISTS, again


# --------------------------------------------------------------------------- #
# IT-A-05 令牌刷新：refresh 换新 access；access 不能当 refresh 用
# --------------------------------------------------------------------------- #
def test_it_a05_refresh_token_flow(api):
    login = api.login_registered()
    refresh = login["data"]["refresh_token"]
    old_access = login["data"]["access_token"]

    refreshed = api.c.post("/api/v1/auth/token/refresh", json={"refresh_token": refresh}).json()
    assert refreshed["code"] == 0, refreshed
    new_access = refreshed["data"]["access_token"]
    assert new_access and refreshed["data"]["expires_in"] > 0

    # 新令牌可用
    me = api.c.get("/api/v1/users/me",
                   headers={"Authorization": f"Bearer {new_access}"}).json()
    assert me["code"] == 0, me

    # 类型隔离：access 令牌不能被当作 refresh 使用（否则等于无限续期）
    bad = api.c.post("/api/v1/auth/token/refresh", json={"refresh_token": old_access}).json()
    assert bad["code"] == ErrCode.TOKEN_INVALID, bad


# --------------------------------------------------------------------------- #
# IT-A-06 令牌过期：过期 access 一律拒绝
# --------------------------------------------------------------------------- #
def test_it_a06_expired_access_token(api):
    headers = {"Authorization": f"Bearer {_expired_token(f'{api.run_id}_exp', offset=-10)}"}
    r = api.c.get("/api/v1/users/me", headers=headers).json()
    assert r["code"] in (ErrCode.TOKEN_EXPIRED, ErrCode.TOKEN_INVALID), r


# --------------------------------------------------------------------------- #
# IT-A-07 长时间无操作：令牌到期后后端拒绝（前端据此登出）
# --------------------------------------------------------------------------- #
def test_it_a07_idle_timeout_rejected(api):
    """会话超时口径 = 令牌有效期（2h）。用 0 秒有效期令牌等价验证后端拦截行为。"""
    from app.utils.security import ACCESS_EXPIRE_SECONDS

    assert ACCESS_EXPIRE_SECONDS == 2 * 3600, "访问令牌有效期应为 2 小时（设计 §1.4）"
    headers = {"Authorization": f"Bearer {_expired_token(f'{api.run_id}_idle', offset=-1)}"}
    for path in ("/api/v1/users/me", "/api/v1/assessments", "/api/v1/dashboard/personal/overview"):
        r = api.c.get(path, headers=headers).json()
        assert r["code"] in (ErrCode.TOKEN_EXPIRED, ErrCode.TOKEN_INVALID), (path, r)


# --------------------------------------------------------------------------- #
# IT-A-08 登出：旧令牌立即失效
# --------------------------------------------------------------------------- #
def test_it_a08_logout_invalidates_token(api, redis_required):
    login = api.login_registered()
    headers = api.hdr(login)
    assert api.c.get("/api/v1/users/me", headers=headers).json()["code"] == 0

    out = api.c.post("/api/v1/auth/logout", headers=headers).json()
    assert out["code"] == 0, out

    after = api.c.get("/api/v1/users/me", headers=headers).json()
    assert after["code"] in (ErrCode.TOKEN_INVALID, ErrCode.UNAUTHENTICATED), after


# --------------------------------------------------------------------------- #
# IT-A-10 改绑手机号：缺二次验证码一律拒绝（不可静默放行）
# --------------------------------------------------------------------------- #
def test_it_a10_phone_change_requires_verify_code(api):
    login = api.login_registered()
    headers = api.hdr(login)
    new_phone = api.phone()

    no_code = api.c.post("/api/v1/users/me/phone/verify", headers=headers,
                         json={"phone": new_phone, "verify_code": ""}).json()
    assert no_code["code"] == ErrCode.PARAM_INVALID, no_code

    wrong = api.c.post("/api/v1/users/me/phone/verify", headers=headers,
                       json={"phone": new_phone, "verify_code": "000000"}).json()
    assert wrong["code"] == ErrCode.VERIFY_CODE_INVALID, wrong

    # 旧手机号（未绑定）的验证码不适用于新号 → 仍拒绝
    other = api.phone()
    code_for_other = api.sms_code(other)
    mismatch = api.c.post("/api/v1/users/me/phone/verify", headers=headers,
                          json={"phone": new_phone, "verify_code": code_for_other}).json()
    assert mismatch["code"] == ErrCode.VERIFY_CODE_INVALID, mismatch


# --------------------------------------------------------------------------- #
# IT-U-02 敏感信息单独同意（后端侧）
# --------------------------------------------------------------------------- #
def test_it_u02_sensitive_consent_required(api):
    login = api.login_registered()
    headers = api.hdr(login)

    denied = api.c.put("/api/v1/users/me/profile", headers=headers,
                       json={"diseases": ["高血压"]}).json()
    assert denied["code"] == ErrCode.PARAM_INVALID, denied

    allowed = api.c.put("/api/v1/users/me/profile", headers=headers,
                        json={"diseases": ["高血压"], "sensitive_consent": True}).json()
    assert allowed["code"] == 0, allowed


# --------------------------------------------------------------------------- #
# IT-U-03 改绑手机号：新号生效、旧号失效
# --------------------------------------------------------------------------- #
def test_it_u03_phone_change_takes_effect(api, redis_required):
    old_phone = api.phone()
    login = api.login_registered(phone=old_phone)
    headers = api.hdr(login)

    new_phone = api.phone()
    code = api.sms_code(new_phone)
    r = api.c.post("/api/v1/users/me/phone/verify", headers=headers,
                   json={"phone": new_phone, "verify_code": code}).json()
    assert r["code"] == 0, r
    assert r["data"]["phone_masked"] == new_phone[:3] + "****" + new_phone[-4:], r

    # 新号可登录
    assert api.login_phone(new_phone)["code"] == 0
    # 旧号不再对应任何账号
    assert api.login_phone(old_phone)["code"] != 0

    # 新号不可被他人重复注册
    dup = api.c.post("/api/v1/auth/register", json={
        "phone": new_phone, "verify_code": "000000", "password": api.PASSWORD}).json()
    assert dup["code"] == ErrCode.USER_EXISTS, dup


# --------------------------------------------------------------------------- #
# IT-U-04 改密码：旧密码失效、新密码可用
# --------------------------------------------------------------------------- #
def test_it_u04_password_change(api):
    login = api.login_registered()
    headers = api.hdr(login)
    username = login["data"]["user"]["username"]
    new_pwd = "NewPassw0rd!2026"

    bad = api.c.put("/api/v1/users/me/password", headers=headers,
                    json={"old_password": "WrongPass!2026", "new_password": new_pwd}).json()
    assert bad["code"] == ErrCode.AUTH_FAILED, bad

    ok = api.c.put("/api/v1/users/me/password", headers=headers,
                   json={"old_password": api.PASSWORD, "new_password": new_pwd}).json()
    assert ok["code"] == 0, ok

    assert api.login(username, api.PASSWORD)["code"] != 0, "旧密码必须失效"
    assert api.login(username, new_pwd)["code"] == 0, "新密码必须可用"


# --------------------------------------------------------------------------- #
# IT-U-05 数据导出：可导出范围与说明（文件生成能力） 
# --------------------------------------------------------------------------- #
def test_it_u05_data_export_scope(api):
    login = api.login_registered()
    headers = api.hdr(login)
    # 字段清单是独立的 GET 端点；/me/data-export 本身只有 POST，
    # 对它发 GET 拿到的是 405 Method Not Allowed，不是业务响应。
    r = api.c.get("/api/v1/users/me/data-export/fields", headers=headers).json()
    assert r["code"] == 0, r
    assert set(r["data"]["available"]) >= {"profile", "assessments", "reports", "consultations"}, r


# --------------------------------------------------------------------------- #
# IT-U-06 注销后登录：拒绝，且进入删除队列
# --------------------------------------------------------------------------- #
def test_it_u06_login_after_deletion(api, db):
    from app.models.mysql.platform import DataDeletionTask

    login = api.login_registered()
    headers = api.hdr(login)
    username = login["data"]["user"]["username"]
    user_id = login["data"]["user"]["user_id"]
    api.cleanup.append(lambda s, i=user_id: s.query(DataDeletionTask).filter_by(user_id=i).delete())

    assert api.c.delete("/api/v1/users/me", headers=headers).json()["code"] == 0
    assert api.login(username)["code"] != 0, "注销后不应还能登录"

    db.rollback()
    assert db.query(DataDeletionTask).filter_by(user_id=user_id).count() == 1


# --------------------------------------------------------------------------- #
# IT-U-07 注销数据范围：三处存储的清理登记与执行链
# --------------------------------------------------------------------------- #
def test_it_u07_deletion_covers_three_stores(api):
    import importlib.util
    import pathlib

    login = api.login_registered()
    user_id = login["data"]["user"]["user_id"]
    assert api.c.delete("/api/v1/users/me", headers=api.hdr(login)).json()["code"] == 0

    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "purge_accounts.py"
    spec = importlib.util.spec_from_file_location("purge_accounts", path)
    purger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(purger)

    conn = purger._conn()
    try:
        tasks = purger.due_tasks(conn, user_id=user_id)
        assert tasks, "注销必须登记清理任务"
        task_id, _ = tasks[0]
        cur = conn.cursor()
        cur.execute("SELECT scope FROM data_deletion_task WHERE id=%s", (task_id,))
        scope = cur.fetchone()[0]
        cur.close()
        assert set(scope.split(",")) == {"mysql", "vector", "minio"}, scope

        info = purger.run_task(conn, task_id, user_id, apply=True)
        assert info["vector"] and "平台级语料" in info["vector"], info
        assert info["sys_user_anonymized"] == 1, info
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# IT-U-08 请求追踪：每个响应带 request_id，失败响应同样可定位
# --------------------------------------------------------------------------- #
def test_it_u08_request_id_traceable(api):
    login = api.login_registered()
    headers = api.hdr(login)

    ok = api.c.get("/api/v1/users/me", headers=headers)
    assert ok.headers.get("X-Request-ID"), "响应头必须带 X-Request-ID"
    assert ok.json()["request_id"], "响应体必须带 request_id"

    # 主动传入的 request_id 应被原样透传（便于前端串日志）
    rid = f"trace-{api.run_id}"
    bad = api.c.get("/api/v1/reports/999999", headers={**headers, "X-Request-ID": rid})
    assert bad.headers.get("X-Request-ID") == rid
    assert bad.json()["code"] != 0 and bad.json()["request_id"] == rid, bad.json()


# --------------------------------------------------------------------------- #
# IT-U-09 并发改资料：无脏数据、无 500，最终值为其中一次写入
# --------------------------------------------------------------------------- #
def test_it_u09_concurrent_profile_update(api, db):
    from app.models.mysql.platform import UserProfile

    login = api.login_registered()
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]

    results, errors = [], []
    barrier = threading.Barrier(2)

    def _put(height):
        try:
            barrier.wait(timeout=20)
            r = api.c.put("/api/v1/users/me/profile", headers=headers,
                          json={"height_cm": height, "weight_kg": 60}).json()
            results.append(r)
        except Exception as e:                            # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=_put, args=(h,)) for h in (168, 172)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert all(r["code"] == 0 for r in results), results

    db.rollback()
    prof = db.query(UserProfile).filter_by(user_id=user_id).first()
    assert prof is not None and float(prof.height_cm) in (168.0, 172.0), \
        f"并发写入应落在其中一次的值上，实际 {prof and prof.height_cm}"



# --------------------------------------------------------------------------- #
# IT-U-05 数据导出（真实文件生成）
#
# 上面的 test_it_u05_data_export_scope 只验了"字段清单"，那是接口自述——
# **说自己能导出 ≠ 真导出了文件**。这里补齐真实链路：
# 造数（档案 + 一次完整测评含报告 + 一条咨询）→ 短信二次验证 → 调导出 →
# 真的把签名地址下载下来 → 校验文件里是**本人**的真实数据。
# --------------------------------------------------------------------------- #
def test_it_u05_data_export_real_file(api, db):
    import csv
    import io

    import requests

    phone = api.phone()
    reg = api.register(phone=phone)
    assert reg["code"] == 0, reg
    login = api.login_phone(phone)
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]

    # --- 造数：档案（写入本人真实值，导出时必须原样出现）---
    from app.models.mysql.platform import UserProfile
    db.rollback()
    prof = db.query(UserProfile).filter_by(user_id=user_id).first()
    if prof is None:
        prof = UserProfile(user_id=user_id)
        db.add(prof)
    prof.height_cm, prof.weight_kg = 171.0, 63.5
    db.commit()

    # --- 造数：走完一次测评，产出测评记录 + 报告 ---
    tid = api.template_id()
    record_id = api.record_id(headers, tid)
    api.answer(record_id, headers)
    sub = api.submit(record_id, headers)
    assert sub["code"] == 0, sub

    # --- 二次验证：错误验证码必须被拒（否则二次验证形同虚设）---
    # 只发一次码：短信按号 60s 限频，"先错后对"复用同一个码即可，
    # 重新 send 必然撞 60003，那是限频在起作用，跟本用例要验的东西无关。
    code = api.sms_code(phone)
    wrong = "0" * 6 if code != "0" * 6 else "1" * 6
    bad = api.c.post("/api/v1/users/me/data-export", headers=headers,
                     json={"verify_code": wrong, "fmt": "csv", "scope": "all"}).json()
    assert bad["code"] != 0, f"错误验证码不应放行：{bad}"

    # --- 同一个码再走正确路径 ---
    r = api.c.post("/api/v1/users/me/data-export", headers=headers,
                   json={"verify_code": code, "fmt": "csv", "scope": "all"}).json()
    assert r["code"] == 0, r
    data = r["data"]
    url = data.get("url")
    assert url, f"未返回下载地址：{data}"
    # 签名有效期必须与声明一致：不能"声明 10 分钟、实际签 7 天"
    assert data.get("expires_in_minutes") == 10, data
    assert "X-Amz-Expires=600" in url, f"签名有效期与声明不符：{url[:200]}"
    assert data.get("size", 0) > 0, data

    # --- 真把文件下下来校验内容 ---
    raw = requests.get(url, timeout=60).content
    assert len(raw) == data["size"], f"下载大小 {len(raw)} != 声明 {data['size']}"
    text = raw.decode("utf-8-sig")
    blocks = {ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")}
    assert {"profile", "assessments", "reports"} <= blocks, f"区块缺失：{blocks}"

    lines = [ln for ln in text.splitlines() if ln.strip()]
    prof_hdr = lines.index("# profile")
    prof_cols = lines[prof_hdr + 1].split(",")
    prof_row = lines[prof_hdr + 2].split(",")
    assert prof_cols[0] == "user_id", prof_cols[:4]
    assert prof_row[0] == user_id, f"导出的不是本人档案：{prof_row[:3]}"
    # 档案值必须是查出来的本人真实值，不是模板行
    assert prof_row[prof_cols.index("height_cm")] == "171", \
        f"身高未取到本人值：{prof_row}"
    assert prof_row[prof_cols.index("weight_kg")].startswith("63.5"), \
        f"体重未取到本人值：{prof_row}"

    as_hdr = lines.index("# assessments")
    assert lines[as_hdr + 2].split(",")[0] == str(record_id), \
        f"测评记录不匹配：{lines[as_hdr + 2][:60]}"

    rp_hdr = lines.index("# reports")
    assert lines[rp_hdr + 2].split(",")[0] == str(sub["data"]["report_id"]), \
        f"报告不匹配：{lines[rp_hdr + 2][:60]}"


def test_it_u05_data_export_xlsx(api):
    """xlsx 形态同样要真出文件：scope 限定时只出对应 sheet。"""
    import io

    import requests
    from openpyxl import load_workbook

    phone = api.phone()
    reg = api.register(phone=phone)
    assert reg["code"] == 0, reg
    login = api.login_phone(phone)
    headers = api.hdr(login)
    code = api.sms_code(phone)
    r = api.c.post("/api/v1/users/me/data-export", headers=headers,
                   json={"verify_code": code, "fmt": "xlsx", "scope": "profile"}).json()
    assert r["code"] == 0, r
    assert r["data"]["format"] == "xlsx", r["data"]
    assert r["data"]["scope"] == "profile", r["data"]

    raw = requests.get(r["data"]["url"], timeout=60).content
    wb = load_workbook(io.BytesIO(raw))
    assert wb.sheetnames == ["profile"], f"scope=profile 只应有 profile 一个 sheet：{wb.sheetnames}"
    ws = wb["profile"]
    assert ws.max_row >= 2, "profile sheet 只有表头，没有数据行"
    assert ws.cell(row=1, column=1).value == "user_id", ws.cell(row=1, column=1).value
