# -*- coding: utf-8 -*-
"""手机号注册/登录（短信验证码）集成测试。

覆盖：发码限频、验证码错误/过期/超限、手机号注册查重、手机号登录、
      双通道回归（账号注册/登录不受影响）。
连真实 MySQL + Redis；验证码走真实 SmsService 流程（网关未配置时发送会失败，
因此本套件直接向 Redis 写入验证码，网关发送逻辑单测桩掉）。
"""
import secrets

import pytest
from sqlalchemy import text

from app.core.errors import ErrCode
from app.services.sms_service import sms_service
from app.utils.redis_client import redis_client
from app.utils.security import phone_hash as phone_hmac


def _plant_code(phone: str, code: str = "123456") -> None:
    """向 Redis 写入验证码（绕过网关下发，仅测流程）。"""
    h = phone_hmac(phone)
    assert redis_client.set("sms:code:" + h, code, ttl=300)
    assert redis_client.set("sms:try:" + h, "0", ttl=300)


def _clear_sms(phone: str) -> None:
    h = phone_hmac(phone)
    redis_client.delete("sms:code:" + h, "sms:try:" + h, "sms:limit:" + h)


# --------------------------------------------------------------------------- #
# 短信验证码
# --------------------------------------------------------------------------- #
def test_sms_send_demo_mode_when_explicitly_enabled(client, monkeypatch, redis_required):
    """仅开发环境显式开启演示模式时，才允许返回 demo_code。"""
    monkeypatch.setattr("app.services.sms_service.settings.ENV", "development")
    monkeypatch.setattr("app.services.sms_service.settings.SMS_DEMO_MODE", True)
    phone = "13800138000"
    h = phone_hmac(phone)
    try:
        r = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
        assert r["code"] == 0, r
        assert r["data"]["demo_mode"] is True, r
        code = r["data"]["demo_code"]
        assert len(str(code)) == 6 and str(code).isdigit(), r
        # 演示码可直接用于注册校验
        with pytest.raises(Exception):
            sms_service.verify_code(phone, "000000")     # 错误码 → 20003
        sms_service.verify_code(phone, str(code))        # 正确码 → 通过并作废
        with pytest.raises(Exception) as e:
            sms_service.verify_code(phone, str(code))    # 一次性：第二次应失效
        assert "失效" in str(e.value) or "重新获取" in str(e.value)
    finally:
        _clear_sms(phone)


def test_sms_send_never_returns_demo_code_in_production(client, monkeypatch, redis_required):
    """生产环境即使误开开关，也必须拒绝伪造短信成功。"""
    monkeypatch.setattr("app.services.sms_service.settings.ENV", "production")
    monkeypatch.setattr("app.services.sms_service.settings.SMS_DEMO_MODE", True)
    phone = "13800138001"
    try:
        result = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
        assert result["code"] == ErrCode.SYSTEM_ERROR, result
        assert result["data"] is None
    finally:
        _clear_sms(phone)


def test_sms_send_rejects_bad_phone(client):
    r = client.post("/api/v1/auth/sms/send", json={"phone": "12345"}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r


def test_sms_rate_limit_by_redis(client, redis_required):
    """60s 限频：缓存里已有限频标记 → 60003。"""
    phone = "13900000001"
    h = phone_hmac(phone)
    try:
        assert redis_client.set("sms:limit:" + h, "1", ttl=60)
        r = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()
        assert r["code"] == ErrCode.SMS_RATE_LIMITED, r
    finally:
        redis_client.delete("sms:limit:" + h)


def test_verify_code_wrong_and_expired(redis_required):
    """错误码 / 未获取过验证码 → 20003。"""
    phone = "13900000002"
    try:
        _plant_code(phone, "654321")
        with pytest.raises(Exception) as e:
            sms_service.verify_code(phone, "123456")
        assert "验证码不正确" in str(e.value)
        with pytest.raises(Exception) as e2:
            sms_service.verify_code("13900000003", "123456")   # 未发过码
        assert "失效" in str(e2.value) or "重新获取" in str(e2.value)
    finally:
        _clear_sms(phone)


def test_verify_code_max_attempts(redis_required):
    """连续错 5 次后作废，第 6 次提示重新获取。"""
    phone = "13900000004"
    try:
        _plant_code(phone, "654321")
        last = None
        for _ in range(5):
            with pytest.raises(Exception) as e:
                sms_service.verify_code(phone, "000000")
            last = e
        assert "还可尝试" in str(last.value) or "错误次数过多" in str(last.value)
        # 达上限后验证码已被作废
        with pytest.raises(Exception) as e:
            sms_service.verify_code(phone, "654321")
        assert "失效" in str(e.value) or "重新获取" in str(e.value)
    finally:
        _clear_sms(phone)


# --------------------------------------------------------------------------- #
# 手机号注册 / 登录
# --------------------------------------------------------------------------- #
def test_register_and_login_by_phone(client, run_id, cleanup, redis_required):
    phone = "13900000010"
    h = phone_hmac(phone)

    def _clean(s):
        s.execute(text("DELETE FROM sys_user WHERE phone_hash=:h"), {"h": h})

    cleanup.append(_clean)
    try:
        _plant_code(phone, "123456")

        # 手机号注册（不传 username，由后端自动生成）
        r = client.post("/api/v1/auth/register", json={
            "phone": phone, "verify_code": "123456",
            "password": "Abcd@2026", "real_name": "手机号用户",
        }).json()
        assert r["code"] == 0, r
        assert r["data"]["phone_masked"] == "139****0010", r
        assert r["data"]["username"].startswith("u"), r
        # 响应不泄露手机号明文/密文
        assert "phone" not in r["data"], r

        # 同手机号重复注册 → 20002，且不消耗第二次验证码之外的副作用
        _plant_code(phone, "123456")
        r2 = client.post("/api/v1/auth/register", json={
            "phone": phone, "verify_code": "123456", "password": "Abcd@2026",
        }).json()
        assert r2["code"] == ErrCode.USER_EXISTS, r2
        assert "已注册" in r2["message"], r2

        # 手机号 + 密码登录
        lg = client.post("/api/v1/auth/login", json={
            "phone": phone, "password": "Abcd@2026"}).json()
        assert lg["code"] == 0, lg
        assert lg["data"]["access_token"], lg
        assert lg["data"]["user"]["user_id"] == r["data"]["user_id"]

        # 错误密码 → 10006
        bad = client.post("/api/v1/auth/login", json={
            "phone": phone, "password": "Wrong@123"}).json()
        assert bad["code"] == ErrCode.AUTH_FAILED, bad

        # 未注册手机号登录 → 10006
        no = client.post("/api/v1/auth/login", json={
            "phone": "13900000011", "password": "Abcd@2026"}).json()
        assert no["code"] == ErrCode.AUTH_FAILED, no
    finally:
        _clear_sms(phone)


def test_register_phone_requires_code(client, run_id, cleanup):
    """手机号与验证码必须成对出现。"""
    phone = "13900000012"
    h = phone_hmac(phone)
    cleanup.append(lambda s: s.execute(
        text("DELETE FROM sys_user WHERE phone_hash=:h"), {"h": h}))
    r = client.post("/api/v1/auth/register", json={
        "phone": phone, "password": "Abcd@2026"}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    r2 = client.post("/api/v1/auth/register", json={
        "verify_code": "123456", "password": "Abcd@2026"}).json()
    assert r2["code"] == ErrCode.PARAM_INVALID, r2


def test_username_channel_regression(client, run_id, cleanup):
    """双通道回归：账号注册/登录不受手机号改动影响。"""
    username = f"{run_id}_rg"
    h = phone_hmac("13900000013")  # 占位，保持清理结构一致

    def _clean(s):
        s.execute(text("DELETE FROM sys_user WHERE username=:u"), {"u": username})

    cleanup.append(_clean)
    r = client.post("/api/v1/auth/register", json={
        "username": username, "password": "Abcd@2026", "real_name": "账号用户"}).json()
    assert r["code"] == 0, r
    lg = client.post("/api/v1/auth/login", json={
        "username": username, "password": "Abcd@2026"}).json()
    assert lg["code"] == 0, lg


def test_phone_encrypt_roundtrip_and_hash_stable():
    """手机号加密可逆、HMAC 稳定（同号同哈希）。"""
    from app.utils.security import encrypt_phone, decrypt_phone, phone_hash
    p = "13812345678"
    cipher = encrypt_phone(p)
    assert decrypt_phone(cipher) == p
    assert phone_hash(p) == phone_hash("13812345678")
    assert phone_hash(p) != phone_hash("13812345679")
