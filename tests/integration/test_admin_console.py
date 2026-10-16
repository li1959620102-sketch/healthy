# -*- coding: utf-8 -*-
"""J 组 · 管理后台接口集成测试（连真实 MySQL）。

覆盖本轮补强的五块能力，重点验证两件事：
  1) 权限边界：非管理员一律 10005，游客 10001；
  2) **审计留痕**：禁用/解封、模板审核、预警阈值、角色权限的每次变更，
     都必须写入 operation_log（合规要求，保留 ≥ 1 年）。

清理策略：所有本次产生的审计日志以「操作人 ID = {run_id}_ADM」标记，
teardown 按该标记精确删除；配置类改动先读原值、结束再写回。
"""
import pytest  # noqa: F401  （夹具由 conftest 提供）
from sqlalchemy import text

from app.core import rules


def _admin(factory, run_id):
    """管理员的直签令牌 + 其操作人 ID（用于精确清理审计日志）。"""
    oid = f"{run_id}_ADM"
    return factory(oid, "admin", "it_admin"), oid


def _actions_of(oid):
    """读取该操作人写下的审计日志动作（用完即关 session，避免连接泄漏）。"""
    from app.utils.db import get_session
    s = get_session()
    try:
        rows = s.execute(
            text("SELECT action FROM operation_log WHERE operator_id = :o ORDER BY id"),
            {"o": oid}).fetchall()
        return [x[0] for x in rows]
    finally:
        s.close()


def _cleanup_logs(oid):
    def _do(s):
        s.execute(text("DELETE FROM operation_log WHERE operator_id = :o"), {"o": oid})
    return _do


# --------------------------------------------------------------------------- #
# 权限边界
# --------------------------------------------------------------------------- #
def test_admin_endpoints_reject_non_admin(client, auth_factory):
    """非管理员访问管理端接口必须 10005，且请求体合法时也不放行。"""
    normal = auth_factory("IT_NOT_ADMIN", "user", "it_u")
    gets = ["/api/v1/admin/users", "/api/v1/admin/alerts/thresholds",
            "/api/v1/admin/roles", "/api/v1/admin/operation-logs",
            "/api/v1/admin/templates", "/api/v1/admin/overview"]
    for path in gets:
        r = client.get(path, headers=normal).json()
        assert r["code"] == 10005, f"GET {path} 未拦截：{r}"

    puts = [
        ("/api/v1/admin/alerts/thresholds", {"thresholds": {"dau_drop_pct": 10}}),
        ("/api/v1/admin/roles",
         {"capability": "ops_dashboard", "role": "user", "allowed": True}),
        ("/api/v1/admin/users/U_NOT_EXIST/status", {"status": "disabled"}),
    ]
    for path, body in puts:
        r = client.put(path, headers=normal, json=body).json()
        assert r["code"] == 10005, f"PUT {path} 未拦截：{r}"


# --------------------------------------------------------------------------- #
# 预警阈值（FR-V-10）
# --------------------------------------------------------------------------- #
def test_alert_thresholds_shape_and_clamp(client, auth_factory, cleanup, run_id):
    """阈值清单结构完整；越界值收敛到合法区间；变更写审计日志。"""
    adm, oid = _admin(auth_factory, run_id)
    cleanup.append(_cleanup_logs(oid))

    r = client.get("/api/v1/admin/alerts/thresholds", headers=adm).json()
    assert r["code"] == 0, r
    items = r["data"]["items"]
    keys = {i["key"] for i in items}
    assert keys == set(rules.ALERT_THRESHOLDS), "阈值项与 rules.py 不一致"
    for i in items:
        assert i["min"] <= i["value"] <= i["max"], f"{i['key']} 默认值越界"
        assert i["name"] and i["unit"] and i["desc"]

    before = {i["key"]: i["value"] for i in items}
    cleanup.append(lambda s: _restore_alerts(client, adm, before))

    spec = rules.ALERT_THRESHOLDS["dau_drop_pct"]
    r = client.put("/api/v1/admin/alerts/thresholds", headers=adm,
                   json={"thresholds": {"dau_drop_pct": spec["max"] + 5000}}).json()
    assert r["code"] == 0, r
    assert r["data"]["values"]["dau_drop_pct"] == spec["max"], "越界值未被收敛到上限"

    assert "alert.update" in _actions_of(oid), "阈值变更未留痕"


def test_alert_thresholds_reject_unknown_key(client, auth_factory, run_id):
    """未知阈值项必须报参数错误，不得静默接受。"""
    adm, _ = _admin(auth_factory, run_id)
    r = client.put("/api/v1/admin/alerts/thresholds", headers=adm,
                   json={"thresholds": {"not_a_real_key": 1}}).json()
    assert r["code"] == 90001, r


# --------------------------------------------------------------------------- #
# 角色 - 权限矩阵（需求 §3.1）
# --------------------------------------------------------------------------- #
def test_role_matrix_and_toggle(client, auth_factory, cleanup, run_id):
    """矩阵结构 = 4 角色 × N 能力；切换单个开关生效并写审计日志。"""
    adm, oid = _admin(auth_factory, run_id)
    cleanup.append(_cleanup_logs(oid))

    r = client.get("/api/v1/admin/roles", headers=adm).json()
    assert r["code"] == 0, r
    data = r["data"]
    assert [x["key"] for x in data["roles"]] == rules.ROLES
    assert len(data["items"]) == len(rules.ROLE_CAPABILITIES)
    for it in data["items"]:
        assert set(it["roles"].keys()) == set(rules.ROLES), it

    before = {it["capability"]: dict(it["roles"]) for it in data["items"]}
    cleanup.append(lambda s: _restore_roles(client, adm, before))

    # 选一个当前为 False 的格子翻成 True，验证真正落库
    target = None
    for it in data["items"]:
        for role in rules.ROLES:
            if not it["roles"][role]:
                target = (it["capability"], role)
                break
        if target:
            break
    assert target, "矩阵中不存在可翻转的 False 格子"

    cap, role = target
    r = client.put("/api/v1/admin/roles", headers=adm,
                   json={"capability": cap, "role": role, "allowed": True}).json()
    assert r["code"] == 0, r
    assert r["data"]["roles"][role] is True

    again = client.get("/api/v1/admin/roles", headers=adm).json()["data"]
    got = next(x for x in again["items"] if x["capability"] == cap)
    assert got["roles"][role] is True, "切换后重新读取未生效（未落库）"

    assert "role.update" in _actions_of(oid), "权限变更未留痕"


def test_role_matrix_rejects_unknown(client, auth_factory, run_id):
    """未知能力键 / 未知角色必须报参数错误。"""
    adm, _ = _admin(auth_factory, run_id)
    r = client.put("/api/v1/admin/roles", headers=adm,
                   json={"capability": "nope", "role": "admin", "allowed": True}).json()
    assert r["code"] == 90001, r
    r = client.put("/api/v1/admin/roles", headers=adm,
                   json={"capability": "ops_dashboard", "role": "superman", "allowed": True}).json()
    assert r["code"] == 90001, r


# --------------------------------------------------------------------------- #
# 用户禁用 / 解封
# --------------------------------------------------------------------------- #
def test_user_disable_enable_and_audit(client, auth_factory, cleanup, run_id):
    """禁用→解封闭环，两次动作各写一条审计日志。"""
    adm, oid = _admin(auth_factory, run_id)
    cleanup.append(_cleanup_logs(oid))

    uname = f"it_disable_{run_id}"
    reg = client.post("/api/v1/auth/register",
                      json={"username": uname, "password": "Passw0rd!2026",
                            "real_name": "IT禁用用例"}).json()
    assert reg["code"] == 0, reg
    uid = reg["data"]["user_id"]
    cleanup.append(lambda s, u=uid: s.execute(
        text("DELETE FROM user_profile WHERE user_id = :u"), {"u": u}))
    cleanup.append(lambda s, u=uid: s.execute(
        text("DELETE FROM sys_user WHERE user_id = :u"), {"u": u}))

    r = client.put(f"/api/v1/admin/users/{uid}/status", headers=adm,
                   json={"status": "disabled"}).json()
    assert r["code"] == 0 and r["data"]["status"] == "disabled", r

    # 被禁用的账号不能登录
    login = client.post("/api/v1/auth/login",
                        json={"username": uname, "password": "Passw0rd!2026"}).json()
    assert login["code"] != 0, f"禁用后仍可登录：{login}"

    r = client.put(f"/api/v1/admin/users/{uid}/status", headers=adm,
                   json={"status": "active"}).json()
    assert r["code"] == 0 and r["data"]["status"] == "active", r

    actions = _actions_of(oid)
    assert "user.disable" in actions and "user.enable" in actions, f"留痕不全：{actions}"


def test_user_status_invalid_value(client, auth_factory, run_id):
    """非法状态值必须报参数错误。"""
    adm, _ = _admin(auth_factory, run_id)
    r = client.put("/api/v1/admin/users/U_NOT_EXIST/status", headers=adm,
                   json={"status": "frozen"}).json()
    assert r["code"] == 90001, r


# --------------------------------------------------------------------------- #
# 模板审核留痕
# --------------------------------------------------------------------------- #
def _mk_template(client, adm, run_id, suffix=""):
    code = f"IT_TPL_{run_id}{suffix}"
    r = client.post("/api/v1/templates", headers=adm, json={
        "code": code, "name": "IT 管理端测试模板", "type": "nutrition",
        "dimension_config": [{"key": "basic", "name": "基础营养", "weight": 1.0}],
        "question_config": {"questions": [{
            "question_id": "q1", "dimension": "basic", "type": "single", "required": True,
            "max_score": 4, "options": [{"value": "A", "score": 4}, {"value": "B", "score": 2}]}]},
    }).json()
    assert r["code"] == 0, r
    return r["data"]["id"]


def test_template_audit_and_offline_write_logs(client, auth_factory, cleanup, run_id):
    """提交审核 → 审核通过 → 下线，三个动作都必须在审计日志中可查。"""
    adm, oid = _admin(auth_factory, run_id)
    cleanup.append(_cleanup_logs(oid))

    tid = _mk_template(client, adm, run_id)
    cleanup.append(lambda s, i=tid: s.execute(
        text("DELETE FROM assessment_template WHERE id = :i"), {"i": i}))

    assert client.post(f"/api/v1/templates/{tid}/submit-audit", headers=adm).json()["code"] == 0

    # 待审列表应能看到它
    audits = client.get("/api/v1/admin/templates/audits", headers=adm).json()["data"]["items"]
    assert any(x["id"] == tid for x in audits), "提交后未出现在待审列表"

    r = client.post(f"/api/v1/templates/{tid}/audit", headers=adm,
                    json={"approve": True, "remark": "IT 用例：准予发布"}).json()
    assert r["code"] == 0 and r["data"]["status"] == "published", r

    r = client.post(f"/api/v1/templates/{tid}/offline", headers=adm).json()
    assert r["code"] == 0 and r["data"]["status"] == "offline", r

    actions = _actions_of(oid)
    assert "template.audit" in actions, f"模板审核未留痕：{actions}"
    assert "template.offline" in actions, f"模板下线未留痕：{actions}"


def test_admin_template_list_filterable(client, auth_factory, cleanup, run_id):
    """管理员全量模板列表可按状态筛选。"""
    adm, oid = _admin(auth_factory, run_id)
    cleanup.append(_cleanup_logs(oid))
    tid = _mk_template(client, adm, run_id, "_f")
    cleanup.append(lambda s, i=tid: s.execute(
        text("DELETE FROM assessment_template WHERE id = :i"), {"i": i}))

    r = client.get("/api/v1/admin/templates", params={"status": "draft"}, headers=adm).json()
    assert r["code"] == 0, r
    items = r["data"]["items"]
    assert all(x["status"] == "draft" for x in items), "状态筛选失效"
    assert any(x["id"] == tid for x in items), "新建草稿未出现在 draft 列表"


# --------------------------------------------------------------------------- #
# 审计日志查询
# --------------------------------------------------------------------------- #
def test_operation_logs_filter(client, auth_factory, run_id):
    """日志可按动作筛选，且返回字段齐全（合规审计需要操作人与时间）。"""
    adm, _ = _admin(auth_factory, run_id)
    r = client.get("/api/v1/admin/operation-logs",
                   params={"action": "user.enable", "limit": 5}, headers=adm).json()
    assert r["code"] == 0, r
    for x in r["data"]["items"]:
        assert x["action"] == "user.enable"
        for f in ("id", "operator_id", "operator_role", "action", "created_at"):
            assert f in x, f"日志缺少字段 {f}"


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _restore_alerts(client, adm, before):
    client.put("/api/v1/admin/alerts/thresholds", headers=adm,
               json={"thresholds": before})


def _restore_roles(client, adm, before):
    """整表还原：先一次性写回原值，逐格回写会产生大量无谓审计日志。"""
    for cap, roles in before.items():
        for role, allowed in roles.items():
            client.put("/api/v1/admin/roles", headers=adm,
                       json={"capability": cap, "role": role, "allowed": allowed})
