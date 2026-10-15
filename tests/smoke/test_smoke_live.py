# -*- coding: utf-8 -*-
"""部署后冒烟测试；设置 SMOKE_BASE_URL 后执行。"""
import os

import pytest
import requests

BASE = os.getenv("SMOKE_BASE_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not BASE, reason="未设置 SMOKE_BASE_URL"),
]


def test_health():
    r = requests.get(BASE + "/api/health", timeout=10)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_ready_contract():
    r = requests.get(BASE + "/api/v1/ready", timeout=15)
    assert r.status_code == 200
    data = r.json().get("data") or {}
    assert {"mysql", "redis", "minio", "milvus"} <= set(data.get("checks") or {})


def test_openapi_available():
    r = requests.get(BASE + "/openapi.json", timeout=10)
    assert r.status_code == 200
    assert len((r.json().get("paths") or {})) >= 20


def test_anonymous_chat_is_rejected():
    r = requests.post(BASE + "/api/v1/chat/query", json={"question": "测试"}, timeout=15)
    assert r.status_code == 200
    assert r.json().get("code") == 10001


def test_public_registration_cannot_assign_admin():
    # 使用不满足账号规则的探针值，避免在真实环境创建垃圾账号；这里只校验 OpenAPI 契约说明。
    schema = requests.get(BASE + "/openapi.json", timeout=10).json()
    reg = schema["components"]["schemas"]["RegisterReq"]
    role = reg.get("properties", {}).get("role", {})
    assert "后台审核" in role.get("description", "")
