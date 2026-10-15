# -*- coding: utf-8 -*-
"""集成测试公共夹具（连真实 MySQL；Redis 可用时连带验证）。

约定：
  - 所有造数一律带时间戳后缀，互相隔离，不污染既有数据；
  - 用例结束在 fixture teardown 里按后缀清理（先删子表再删主表，避免外键冲突）；
  - 需要 Redis 的用例统一用 `redis_required` 标记，不可用时自动 SKIP 而非 FAIL。
"""
import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.env_probe import MINIO_OK, REDIS_OK, SKIP_MINIO, SKIP_REDIS


@pytest.fixture(scope="session")
def client():
    import main
    return TestClient(main.app)


@pytest.fixture(scope="session")
def run_id():
    """本轮运行唯一后缀，用于隔离测试数据。"""
    return f"IT{int(time.time())}"


@pytest.fixture(scope="session")
def auth_factory():
    """按 (user_id, role) 直接签发令牌——鉴权本身的用例在其它套件覆盖。"""
    from app.utils.security import create_access_token

    def _make(user_id: str, role: str = "user", username: str = "it") -> dict:
        return {"Authorization": f"Bearer {create_access_token(user_id, username, role)}"}

    return _make


@pytest.fixture
def cleanup():
    """收集待清理的清理函数，用例结束后逆序执行。

    每个清理函数**各自提交**：任何一个失败都只回滚它自己，
    不会把同一 session 里已经成功的删除一起回滚掉（否则会静默留脏数据）。
    """
    tasks = []
    yield tasks
    from app.utils.db import get_session
    session = get_session()
    try:
        for fn in reversed(tasks):
            try:
                fn(session)
                session.commit()
            except Exception as e:               # noqa: BLE001
                session.rollback()
                print(f"[cleanup] 清理失败（已跳过，请人工核对）：{e!r}")
    finally:
        session.close()


@pytest.fixture(scope="session", autouse=True)
def residue_watchdog():
    """会话级兜底：清掉本轮未登记到的 `IT*` 测试用户残留，并把漏网项**打印出来**。

    设计意图是「兜底但绝不静默」：一旦某张表出现漏网，就说明有用例忘了登记 cleanup，
    这里删掉的同时把表名与行数打出来，让"零残留"成为可验证的结论而不是口头声明。
    （2026-10-15 发现：合成用户 user_id 不进 sys_user，而发起测评会自动建 user_profile，
    每轮回归静默泄漏 3 行；根因已在 test_boundary_api.py 登记清理，本夹具只作最后一道网。）
    """
    yield
    from sqlalchemy import text

    from app.utils.db import get_session

    session = get_session()
    leaked = {}
    try:
        tables = [r[0] for r in session.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND column_name = 'user_id' "
            "AND table_name <> 'sys_user'"
        )).fetchall()]
        for t in tables:
            n = session.execute(
                text(f"DELETE FROM `{t}` WHERE user_id LIKE 'IT%'")).rowcount
            if n:
                leaked[t] = n
        session.commit()
    except Exception as e:                       # noqa: BLE001
        session.rollback()
        print(f"[residue-watchdog] 兜底清理失败：{e!r}")
    finally:
        session.close()
    if leaked:
        print(f"[residue-watchdog] 注意：兜底清理了未登记的残留，请补登记 cleanup -> {leaked}")


@pytest.fixture
def redis_required():
    """Redis 相关用例的统一前置守卫。"""
    if not REDIS_OK:
        pytest.skip(SKIP_REDIS)


@pytest.fixture
def minio_required():
    """MinIO 相关用例的统一前置守卫。"""
    if not MINIO_OK:
        pytest.skip(SKIP_MINIO)
