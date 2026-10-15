# -*- coding: utf-8 -*-
"""集成测试 · 基础设施连通性（连真实 MySQL / Redis）。

这是"集成测试能跑起来"的前置证明：先证明依赖真的连得上，再看业务行为。
Redis 不可用时对应用例 SKIP（平台核心链路不依赖 Redis）。
"""
import pytest

from tests.integration.env_probe import (
    MYSQL_OK, MYSQL_REASON, REDIS_OK, REDIS_REASON, SKIP_MYSQL, SKIP_REDIS,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# MySQL
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not MYSQL_OK, reason=SKIP_MYSQL)
def test_mysql_reachable_and_tables_ready():
    from sqlalchemy import create_engine, text

    from app.config import settings
    engine = create_engine(settings.MYSQL_URI, pool_pre_ping=True)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT VERSION()")).scalar()
        tables = [r[0] for r in conn.execute(text("SHOW TABLES")).fetchall()]

    assert version
    # 平台 17 张业务表 + 继承表，低于 20 说明建表没跑全
    assert len(tables) >= 20, f"表数量异常：{len(tables)}"
    for expected in ("sys_user", "assessment_record", "assessment_report",
                     "assessment_template", "consultation", "diet_plan"):
        assert expected in tables, f"缺少表 {expected}"


@pytest.mark.skipif(not MYSQL_OK, reason=SKIP_MYSQL)
def test_mysql_session_roundtrip():
    """写入 → 读回 → 删除，证明 ORM 会话可用且事务能提交。"""
    from app.utils.db import get_session
    from app.models.mysql.platform import SystemConfig

    key = "test.integration.probe"
    session = get_session()
    try:
        session.query(SystemConfig).filter_by(config_key=key).delete()
        session.add(SystemConfig(config_key=key, config_value="1", description="集成测试"))
        session.commit()

        row = session.query(SystemConfig).filter_by(config_key=key).first()
        assert row is not None and row.config_value == "1"

        session.query(SystemConfig).filter_by(config_key=key).delete()
        session.commit()
        assert session.query(SystemConfig).filter_by(config_key=key).first() is None
    finally:
        session.close()


def test_ready_probe_reports_each_dependency(client):
    """探针逐项报告依赖，并且 ready 必须等于“所有依赖均正常”。"""
    body = client.get("/api/v1/ready").json()
    assert body["code"] == 0
    checks = body["data"]["checks"]
    assert {"mysql", "milvus", "redis", "minio"} <= set(checks)
    assert "optional" not in body["data"]           # 不再是「可选依赖」，故无该字段
    if MYSQL_OK:
        assert checks["mysql"] == "ok"
    if REDIS_OK:
        assert checks["redis"] == "ok"
    if checks["minio"] == "ok":
        assert checks["minio"] == "ok"
    assert body["data"]["ready"] is all(value == "ok" for value in checks.values())


# --------------------------------------------------------------------------- #
# Redis（必备依赖：真连真测，连不上即 FAIL 而非 SKIP）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not REDIS_OK, reason=SKIP_REDIS)
def test_redis_reachable_and_set_get(redis_required):
    import redis

    from app.config import settings
    client = redis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT,
        socket_connect_timeout=3, decode_responses=True,
    )
    assert client.ping() is True
    client.set("ai_health:it:probe", "ok", ex=15)
    assert client.get("ai_health:it:probe") == "ok"
    client.delete("ai_health:it:probe")


@pytest.mark.skipif(not REDIS_OK, reason=SKIP_REDIS)
def test_redis_client_wrapper_works(redis_required):
    from app.utils.redis_client import redis_client

    assert redis_client.ping() is True
    assert redis_client.set("ai_health:it:wrapper", "v", ttl=15) is True
    assert redis_client.get("ai_health:it:wrapper") == "v"
    assert redis_client.set_json("ai_health:it:json", {"a": 1}, ttl=15) is True
    assert redis_client.get_json("ai_health:it:json") == {"a": 1}
    assert redis_client.delete("ai_health:it:wrapper", "ai_health:it:json") == 2


def test_redis_client_degrades_silently_when_unavailable(monkeypatch):
    """无论是哪种失败（连接拒绝 / 服务端握手即断开），封装层都必须返回安全默认值。

    这条用例在 Redis 可用与不可用的机器上都要能跑，因此用打桩强制制造故障。
    """
    from app.utils import redis_client as rc_module

    client = rc_module.RedisClient()

    class Boom:
        def __getattr__(self, name):
            def _raise(*a, **kw):
                raise ConnectionError("Connection closed by server")
            return _raise

    monkeypatch.setattr(client, "_build", lambda: Boom())

    assert client.ping() is False
    assert client.get("k") is None
    assert client.set("k", "v") is False
    assert client.incr_with_ttl("k", 60) is None
    assert client.delete("k") == 0
    assert client.get_json("k") is None
    assert client.health().startswith("unavailable")


def test_env_probe_reports_reasons():
    """探测结果必须带可读原因，方便在 CI 日志里定位环境问题。"""
    assert isinstance(MYSQL_REASON, str) and MYSQL_REASON
    assert isinstance(REDIS_REASON, str) and REDIS_REASON
