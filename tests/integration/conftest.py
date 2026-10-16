# -*- coding: utf-8 -*-
"""集成测试公共夹具（连真实 MySQL；Redis 可用时连带验证）。

约定：
  - 所有造数一律带时间戳后缀，互相隔离，不污染既有数据；
  - 用例结束在 fixture teardown 里按后缀清理（先删子表再删主表，避免外键冲突）；
  - 需要 Redis 的用例统一用 `redis_required` 标记，不可用时自动 SKIP 而非 FAIL。
"""
import time
from datetime import datetime

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
    """按 (user_id, role) 直接签发令牌——鉴权本身的用例在其它套件覆盖。

    注意：2026-10-16 起鉴权会校验账号状态（禁用/注销后旧令牌立即失效，
    见 app/core/deps._assert_user_active）。因此这里同步**确保账号存在且启用**，
    否则"只签令牌不建账号"会让用例全部 401。清理由 residue_watchdog 的
    `user_id LIKE 'IT%'` 兜底完成。
    """
    from app.utils.security import create_access_token

    def _make(user_id: str, role: str = "user", username: str = "it") -> dict:
        _ensure_sys_user(user_id, role)
        return {"Authorization": f"Bearer {create_access_token(user_id, username, role)}"}

    return _make


def _ensure_sys_user(user_id: str, role: str = "user") -> None:
    """确保合成令牌对应的账号存在于 sys_user 且为启用态（幂等）。"""
    from app.models.mysql.user import SysUser
    from app.utils.db import get_session

    session = get_session()
    try:
        if session.query(SysUser).filter_by(user_id=user_id).first():
            return
        session.add(SysUser(user_id=user_id, username=user_id[:64],
                            password_hash="!synthetic-no-login",
                            real_name="合成用例账号", role=role, status="active"))
        session.commit()
    finally:
        session.close()


def synthetic_headers(user_id: str, role: str = "user", username: str = "it") -> dict:
    """签发合成令牌并确保账号存在（供集成用例直接调用）。

    为什么需要它而不是裸 `create_access_token`：
    鉴权会校验账号存在且为 active（`deps._assert_user_active`），
    只签令牌不建账号会被判为"账号不存在"。
    """
    from app.utils.security import create_access_token

    _ensure_sys_user(user_id, role)
    return {"Authorization": f"Bearer {create_access_token(user_id, username, role)}"}


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
    """会话级兜底：把本轮回归留下的残留清干净，并把漏网项**打印出来**。

    两条防线：

    1) **审计日志用基线快照**（与 scripts/seed_business_data.py 同一套做法）：
       会话开始记录 operation_log 的 MAX(id)，结束删掉基线之上的所有行。
       因为审计日志的操作人列叫 `operator_id` 而非 `user_id`，按前缀扫描既挑人也漏项；
       基线快照不挑操作人，凡是本轮新写的日志一律回收。
    2) **IT* 测试用户按前缀兜底**：清掉未登记到 cleanup 的合成用户，
       并把表名与行数打出来——让"零残留"成为可验证的结论而不是口头声明。

    设计意图是「兜底但绝不静默」。
    （2026-10-15 发现：合成用户 user_id 不进 sys_user，而发起测评会自动建 user_profile，
    每轮回归静默泄漏 3 行；2026-10-16 发现：原实现把删除放进一个事务，遇到不可删的
    join view 就整体回滚，兜底长期形同虚设——现改为逐项提交。）
    """
    from sqlalchemy import text

    from app.utils.db import get_session

    baseline_session = get_session()
    try:
        row = baseline_session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM operation_log")).fetchone()
        log_baseline = int(row[0]) if row else 0
        # 知识库元数据的回收基线（id 是 UUID，只能按时间切分）
        kb_baseline = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    finally:
        baseline_session.close()

    yield

    session = get_session()
    leaked = {}
    reclaimed = {}

    def _del(sql: str, key: str, bucket: dict):
        """单项删除 + 立即提交：任何一项失败都只回滚它自己，不牵连已成功的清理。"""
        try:
            n = session.execute(text(sql)).rowcount
            session.commit()
            if n:
                bucket[key] = n
        except Exception as e:                       # noqa: BLE001
            session.rollback()
            print(f"[residue-watchdog] 跳过 {key}：{e!r}")

    try:
        # 防线 1：审计日志按基线回收（不挑操作人，本轮新增一律清）—— 属常规兜底
        _del(f"DELETE FROM operation_log WHERE id > {log_baseline}",
             "operation_log", reclaimed)

        # 防线 2：IT* 合成用户按前缀兜底 —— 命中即说明有用例漏登记 cleanup
        tables = [r[0] for r in session.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND column_name = 'user_id' "
            "AND table_name <> 'sys_user' "
            "AND table_name IN (SELECT table_name FROM information_schema.tables "
            "                   WHERE table_schema = DATABASE() "
            "                   AND table_type = 'BASE TABLE')"
        )).fetchall()]
        for t in tables:
            _del(f"DELETE FROM `{t}` WHERE user_id LIKE 'IT%'", t, leaked)

        # 防线 2b：合成用例账号本身（auth_factory / api.synthetic 造，密码为哨兵值）。
        # 不能用 `user_id LIKE 'IT%'` 一刀切——种子数据里也可能有该前缀的账号，
        # 用哨兵密码精确定位，避免误删演示数据。
        _del("DELETE FROM sys_user WHERE user_id LIKE 'IT%' "
             "AND password_hash = '!synthetic-no-login'", "sys_user", reclaimed)

        # 防线 3：知识库元数据按 created_time 基线回收。
        # knowledge_files 没有 user_id 列，前缀兜底扫不到；而用例若在断言处失败，
        # cleanup 链可能不执行完，就会留下"显示已向量化、实际是测试垃圾"的记录
        # （2026-10-16 实测：IT-K-04 崩在 k05 时留下 1 条）。
        # 注意 id 是 UUID 字符串，不能用 `id > N` 比较，只能按入库时间。
        _del(f"DELETE FROM knowledge_files WHERE created_time > '{kb_baseline}'",
             "knowledge_files", reclaimed)
    except Exception as e:                       # noqa: BLE001
        session.rollback()
        print(f"[residue-watchdog] 兜底清理失败：{e!r}")
    finally:
        session.close()
    if reclaimed:
        print(f"[residue-watchdog] 已按基线回收本轮审计日志：{reclaimed}")
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


# =========================================================================== #
# 用例执行辅助层（2026-10-16 新增，供 153 条补测用例复用）
#
# 为什么集中到一处：补测用例数量大（SM/IT/BV/SEC/EX 共 143 条），
# 若每个文件各写一份「注册 / 建模板 / 发起测评」的造数逻辑，
# 口径必然漂移——A 文件造出来的模板状态与 B 文件不同，台账证据就不可信。
# =========================================================================== #
DIMS = [{"key": "basic", "name": "营养基础", "weight": 0.5},
        {"key": "diet", "name": "膳食结构", "weight": 0.5}]
OPTIONS = [{"value": "A", "score": 4}, {"value": "B", "score": 3},
           {"value": "C", "score": 2}, {"value": "D", "score": 0}]
QUESTIONS = [{"question_id": f"{d['key']}_{i}", "dimension": d["key"], "type": "single",
              "title": f"{d['name']} 第 {i + 1} 题", "max_score": 4, "required": True,
              "options": OPTIONS}
             for d in DIMS for i in range(2)]


@pytest.fixture
def db():
    """真实会话；用例内直接查库做断言或改状态（时间旅行）。"""
    from app.utils.db import get_session
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def api(client, run_id, cleanup, db):
    """API 操作封装。所有造数自动登记清理，用户名/编码一律带 run_id 后缀。"""
    from tests.integration.api_helper import ApiHelper
    return ApiHelper(client, run_id, cleanup, db)


@pytest.fixture
def approved_dietitian(cleanup):
    """构造「资质已通过审核」的营养师令牌（合成用户，不落 sys_user）。

    为什么需要：2026-10-16 起营养师业务接口（客户列表 / 工作台概览 / 导出报表 /
    客户报告）要求 `dietitian_profile.audit_status='approved'`——这是补测
    SM-20 时发现的越权面（原先只校验令牌里的 role，未审核账号即可拉客户数据）。
    因此凡需要营养师业务能力的用例，必须先备好资质档案，不能再靠"光有 role"。
    """
    from datetime import datetime

    def _make(user_id: str, role: str = "dietitian") -> dict:
        from app.models.mysql.platform import DietitianProfile
        from app.utils.db import get_session

        _ensure_sys_user(user_id, role)
        s = get_session()
        try:
            row = s.query(DietitianProfile).filter_by(user_id=user_id).first()
            if not row:
                s.add(DietitianProfile(user_id=user_id, audit_status="approved",
                                       audit_at=datetime.now(), auditor_id="conftest",
                                       specialties=["慢病饮食"]))
                s.commit()
        finally:
            s.close()
        cleanup.append(lambda ss, u=user_id: ss.query(DietitianProfile)
                       .filter_by(user_id=u).delete())
        return synthetic_headers(user_id, role, username=user_id)

    return _make
