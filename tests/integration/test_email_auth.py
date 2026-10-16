# -*- coding: utf-8 -*-
"""邮箱注册/登录（邮箱验证码）集成测试 —— FR-U-01「手机号或邮箱注册」。

覆盖：邮箱格式与归一化、验证码错误/过期/超限/一次性、60s 限频、
      注册查重（含大小写归一）、邮箱登录、通道互斥、
      未配网关时不伪造发送、加密可逆、脱敏、不串字段。
连真实 MySQL + Redis；验证码走真实 EmailCodeService 流程（开发态演示模式
响应回传 demo_code，与短信通道同构）。
"""
import pytest
from sqlalchemy import text

from app.core.errors import ErrCode
from app.utils.redis_client import redis_client
from app.utils.security import email_hash


def _plant_code(email: str, code: str = "123456") -> None:
    """向 Redis 写入邮箱验证码（绕过网关下发，仅测流程）。"""
    h = email_hash(email)
    assert redis_client.set("email:code:" + h, code, ttl=300)
    assert redis_client.set("email:try:" + h, "0", ttl=300)


def _clear(email: str) -> None:
    h = email_hash(email)
    redis_client.delete("email:code:" + h, "email:try:" + h, "email:limit:" + h)


# --------------------------------------------------------------------------- #
# 规则与加密（不依赖外部服务）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["", "   ", "bad@", "@example.com", "a@b",
                                 "a b@c.com", "a@b..com", "a@.com", "a@b.c"])
def test_email_format_rejected(client, bad):
    """非法邮箱在建号之前就被拒（90001），不产生任何账号。"""
    r = client.post("/api/v1/auth/register", json={
        "email": bad, "email_code": "123456", "password": "Abcd@2026"}).json()
    assert r["code"] != 0, r


def test_email_encrypt_hash_and_mask():
    """邮箱加密可逆、HMAC 归一化后稳定、脱敏不泄露完整地址。"""
    from app.utils.security import (decrypt_email, email_hash as eh,
                                    encrypt_email, mask_email)

    cipher = encrypt_email("Alice@Example.com")
    assert decrypt_email(cipher) == "alice@example.com", "密文应为归一化后的小写值"
    assert eh("a@b.com") == eh(" A@B.COM "), "大小写/空格不应产生不同哈希"
    assert eh("a@b.com") != eh("a@c.com")
    assert mask_email("alice@example.com") == "al***@example.com"
    assert mask_email("bad") == "***"


# --------------------------------------------------------------------------- #
# 发码
# --------------------------------------------------------------------------- #
def test_email_send_demo_mode(api, redis_required):
    """开发态演示模式：返回 6 位演示码，且与缓存里的码一致。"""
    email = api.email()
    try:
        r = api.c.post("/api/v1/auth/email/send", json={"email": email}).json()
        assert r["code"] == 0, r
        assert r["data"]["demo_mode"] is True, r
        code = str(r["data"]["demo_code"])
        assert len(code) == 6 and code.isdigit(), r
        assert api.peek_email_code(email) == code, "缓存里的码应与回传的演示码一致"
    finally:
        _clear(email)


def test_email_send_rejects_bad_address(client):
    r = client.post("/api/v1/auth/email/send", json={"email": "not-an-email"}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r


def test_email_rate_limit(api, redis_required):
    """60s 限频：缓存里已有标记 → 60005。"""
    email = api.email()
    h = email_hash(email)
    try:
        assert redis_client.set("email:limit:" + h, "1", ttl=60)
        r = api.c.post("/api/v1/auth/email/send", json={"email": email}).json()
        assert r["code"] == ErrCode.EMAIL_RATE_LIMITED, r
    finally:
        _clear(email)


def test_email_send_never_fakes_success_in_production(client, monkeypatch, redis_required):
    """生产环境即使误开演示开关，也必须拒绝伪造发送成功（90000 + data 为空）。"""
    monkeypatch.setattr("app.services.verify_code_service.settings.ENV", "production")
    monkeypatch.setattr("app.services.verify_code_service.settings.MAIL_DEMO_MODE", True)
    email = "prod_refuse@example.invalid"
    try:
        result = client.post("/api/v1/auth/email/send", json={"email": email}).json()
        assert result["code"] == ErrCode.SYSTEM_ERROR, result
        assert result["data"] is None, result
    finally:
        _clear(email)


# --------------------------------------------------------------------------- #
# 验证码校验（服务层，与短信同构）
# --------------------------------------------------------------------------- #
def test_email_code_service_wrong_attempts_and_once_only(redis_required):
    """错误码 → 20003；错满上限作废；正确码通过后立即失效（一次性）。"""
    from app.services.email_code_service import email_code_service as svc

    email = "svc_case@example.invalid"
    try:
        _plant_code(email, "654321")
        with pytest.raises(Exception) as e:
            svc.verify_code(email, "123456")
        assert "验证码不正确" in str(e.value)

        svc.verify_code(email, "654321")                 # 正确码 → 通过并作废
        with pytest.raises(Exception) as e2:
            svc.verify_code(email, "654321")             # 一次性：第二次应失效
        assert "失效" in str(e2.value) or "重新获取" in str(e2.value)

        # 未发过码
        with pytest.raises(Exception) as e3:
            svc.verify_code("never_sent@example.invalid", "123456")
        assert "失效" in str(e3.value) or "重新获取" in str(e3.value)

        # 连续错 5 次后作废，第 6 次提示重新获取
        _plant_code(email, "777777")
        last = None
        for _ in range(5):
            with pytest.raises(Exception) as e4:
                svc.verify_code(email, "000000")
            last = e4
        assert "还可尝试" in str(last.value) or "错误次数过多" in str(last.value)
        with pytest.raises(Exception) as e5:
            svc.verify_code(email, "777777")
        assert "失效" in str(e5.value) or "重新获取" in str(e5.value)
    finally:
        _clear(email)


def test_email_code_expired_or_never_sent(api, redis_required):
    """没发过码就注册 → 20003，且提示重新获取（不是 500）。"""
    email = api.email()
    try:
        r = api.c.post("/api/v1/auth/register", json={
            "email": email, "email_code": "123456", "password": "Abcd@2026"}).json()
        assert r["code"] == ErrCode.VERIFY_CODE_INVALID, r
        assert "失效" in r["message"] or "重新获取" in r["message"], r
    finally:
        _clear(email)


# --------------------------------------------------------------------------- #
# 注册 / 登录
# --------------------------------------------------------------------------- #
def test_register_and_login_by_email(api, redis_required):
    """邮箱注册 → 返回掩码邮箱 → 邮箱登录 → 错误密码被拒。"""
    email = api.email()
    try:
        reg = api.register_by_email(email=email, real_name="邮箱用户")
        assert reg["code"] == 0, reg
        assert reg["data"]["email_masked"] == email[:2] + "***" + email[email.index("@"):], reg
        assert reg["data"]["username"].startswith("u"), reg
        # 响应不得泄露邮箱明文/密文与哈希
        assert email not in str(reg["data"]), reg
        assert "email_cipher" not in reg["data"] and "email_hash" not in reg["data"], reg

        lg = api.login_email(email)
        assert lg["code"] == 0, lg
        assert lg["data"]["access_token"], lg
        assert lg["data"]["user"]["user_id"] == reg["data"]["user_id"], lg

        bad = api.login_email(email, "Wrong@123")
        assert bad["code"] == ErrCode.AUTH_FAILED, bad

        no = api.login_email("nobody_" + email)
        assert no["code"] == ErrCode.AUTH_FAILED, no
    finally:
        _clear(email)


def test_register_email_duplicate_and_case_insensitive(api, redis_required):
    """同一邮箱重复注册 → 20002；换大小写仍是同一邮箱（归一化必须生效）。"""
    email = api.email()
    upper = email.upper()
    try:
        first = api.register_by_email(email=email)
        assert first["code"] == 0, first

        # 大小写不同的同一邮箱：必须判定为已注册，否则可绕过查重开多号
        _clear(upper)
        again = api.c.post("/api/v1/auth/register", json={
            "email": upper, "email_code": api.email_code(upper),
            "password": "Abcd@2026"}).json()
        assert again["code"] == ErrCode.USER_EXISTS, again
        assert "已注册" in again["message"], again

        # 而且换大小写也能登录（归一化后等值检索）
        lg = api.login_email(upper)
        assert lg["code"] == 0, lg
        assert lg["data"]["user"]["user_id"] == first["data"]["user_id"], lg
    finally:
        _clear(email)
        _clear(upper)


def test_register_email_requires_code_pair(client):
    """邮箱与邮箱验证码必须成对出现（缺任一 → 90001）。"""
    r1 = client.post("/api/v1/auth/register", json={
        "email": "pair1@example.invalid", "password": "Abcd@2026"}).json()
    assert r1["code"] == ErrCode.PARAM_INVALID, r1
    r2 = client.post("/api/v1/auth/register", json={
        "email_code": "123456", "password": "Abcd@2026"}).json()
    assert r2["code"] == ErrCode.PARAM_INVALID, r2


def test_register_channels_are_mutually_exclusive(client, run_id, cleanup):
    """手机号 / 邮箱 / 账号三种身份不能混填——否则无法判断用哪个身份来验证。"""
    username = f"{run_id}_mix"
    cleanup.append(lambda s, u=username: s.execute(
        text("DELETE FROM sys_user WHERE username=:u"), {"u": u}))
    r = client.post("/api/v1/auth/register", json={
        "username": username, "email": "mix@example.invalid", "email_code": "123456",
        "phone": "13800138999", "verify_code": "123456",
        "password": "Abcd@2026"}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    hit = client.post("/api/v1/auth/login", json={
        "username": username, "password": "Abcd@2026"}).json()
    assert hit["code"] != 0, "被拒的注册不该留下账号"


def test_email_account_does_not_have_phone(api, redis_required):
    """邮箱注册的账号不应凭空多出手机号（字段串用是真实踩过的坑）。"""
    email = api.email()
    try:
        reg = api.register_by_email(email=email)
        assert reg["code"] == 0, reg
        assert "phone_masked" not in reg["data"], reg
        row = api.db.execute(text(
            "SELECT phone_hash, phone_cipher FROM sys_user WHERE user_id=:i"),
            {"i": reg["data"]["user_id"]}).first()
        assert row is not None, "账号未落库"
        assert row[0] is None and row[1] is None, f"邮箱账号不该有手机号字段：{row}"
    finally:
        _clear(email)
