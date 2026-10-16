# -*- coding: utf-8 -*-
"""边界值补测（用例集第四章未覆盖的 4 条：BV-09 / BV-10 / BV-20 / BV-26）。

BV-09 测评题目数：1 题 / 8 题 / 200 题
BV-10 单页题量：8 题 / 9 题（**前端分页行为**，在 _e2e_* 交互批次验证）
BV-20 时间范围：起>止 / 起=止 / 跨时区
BV-26 注销期限：第 14 日未完成不告警 / 第 16 日未完成触发告警
"""
import time
from datetime import datetime, timedelta

import pytest

from app.core.errors import ErrCode
from tests.integration.conftest import DIMS, OPTIONS

pytestmark = pytest.mark.integration

ONE_DIM = [{"key": "basic", "name": "营养基础", "weight": 1.0}]


def _questions(n_basic: int, n_diet: int = 0):
    """按维度生成题目；题量是本批用例唯一变量。"""
    qs = []
    for dim, n in (("basic", n_basic), ("diet", n_diet)):
        for i in range(n):
            qs.append({"question_id": f"{dim}_{i}", "dimension": dim, "type": "single",
                       "title": f"{dim} 第 {i + 1} 题", "max_score": 4, "required": True,
                       "options": OPTIONS})
    return qs


# --------------------------------------------------------------------------- #
# BV-09 测评题目数：1 / 8 / 200
# --------------------------------------------------------------------------- #
def test_bv09_single_question_template_can_submit(api):
    """1 题模板：能发起、能作答、能提交（题量不构成阻塞）。"""
    headers = api.synthetic("bv09a", "user")
    tpl_id = api.template_id(dims=ONE_DIM, questions=_questions(1))
    rec = api.record_id(headers, tpl_id)
    assert api.answer(rec, headers, {"basic_0": "A"})["code"] == 0
    sub = api.submit(rec, headers, key=api.uniq("k09a"))
    assert sub["code"] == 0, sub
    assert sub["data"]["score"] is not None, sub


def test_bv09_eight_question_template(api):
    """8 题模板：进度分母正确、可提交。"""
    headers = api.synthetic("bv09b", "user")
    tpl_id = api.template_id(questions=_questions(4, 4))
    rec = api.record_id(headers, tpl_id)

    detail = api.c.get(f"/api/v1/assessments/{rec}", headers=headers).json()
    assert len(detail["data"]["questions"]) == 8, detail["data"]["questions"]
    assert detail["data"]["progress"].endswith("/8"), detail["data"]["progress"]

    saved = api.answer(rec, headers, {q["question_id"]: "A" for q in _questions(4, 4)})
    assert saved["data"]["progress"] == "8/8", saved
    assert api.submit(rec, headers, key=api.uniq("k09b"))["code"] == 0


def test_bv09_two_hundred_questions_not_degraded(api):
    """200 题模板：下发不超时（分页由前端按 8 题/页渲染，后端一次性下发快照）。"""
    headers = api.synthetic("bv09c", "user")
    tpl_id = api.template_id(questions=_questions(100, 100))
    rec = api.record_id(headers, tpl_id)

    t0 = time.time()
    detail = api.c.get(f"/api/v1/assessments/{rec}", headers=headers).json()
    cost = time.time() - t0

    assert detail["code"] == 0, detail
    assert len(detail["data"]["questions"]) == 200, len(detail["data"]["questions"])
    assert cost < 2.0, f"200 题下发耗时 {cost:.2f}s，超出 2s 预期"


# --------------------------------------------------------------------------- #
# BV-20 时间范围：起>止 明确报错；起=止 含当日；非法格式报错
# --------------------------------------------------------------------------- #
def test_bv20_trend_date_range_rejects_inverted(api):
    headers = api.synthetic("bv20", "user")
    r = api.c.get("/api/v1/dashboard/personal/trend?start_date=2026-05-01&end_date=2026-01-01",
                  headers=headers).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r        # 起>止：明确提示，不是 500


def test_bv20_trend_bad_format_rejected(api):
    headers = api.synthetic("bv20f", "user")
    r = api.c.get("/api/v1/dashboard/personal/trend?start_date=2026/05/01", headers=headers).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r


def test_bv20_trend_same_day_inclusive(api):
    """起=止：区间收敛为当天，且"含当日"——当天生成的报告必须被查出来。"""
    headers = api.synthetic("bv20d", "user")
    tpl_id = api.template_id()
    rec = api.record_id(headers, tpl_id)
    api.answer(rec, headers)
    assert api.submit(rec, headers, key=api.uniq("k20"))["code"] == 0

    today = datetime.now().strftime("%Y-%m-%d")
    r = api.c.get(f"/api/v1/dashboard/personal/trend?template_id={tpl_id}"
                  f"&start_date={today}&end_date={today}", headers=headers).json()
    assert r["code"] == 0, r
    assert len(r["data"]["points"]) == 1, f"起=止应含当日数据：{r['data']}"


# --------------------------------------------------------------------------- #
# BV-26 注销期限：未到期不告警，超期触发告警口径
# --------------------------------------------------------------------------- #
def test_bv26_deletion_task_overdue_flag(api, db):
    from app.models.mysql.platform import DataDeletionTask

    login = api.login_registered()
    headers = api.hdr(login)
    user_id = login["data"]["user"]["user_id"]
    admin = api.synthetic("bv26adm", "admin")
    api.cleanup.append(lambda s, i=user_id: s.query(DataDeletionTask)
                       .filter_by(user_id=i).delete())

    assert api.c.delete("/api/v1/users/me", headers=headers).json()["code"] == 0

    # 第 14 日：注销登记 14 天前、执行期限还剩 1 天 → 不告警
    db.query(DataDeletionTask).filter_by(user_id=user_id).update(
        {"created_at": datetime.now() - timedelta(days=14),
         "execute_at": datetime.now() + timedelta(days=1)})
    db.commit()

    r = api.c.get("/api/v1/admin/deletion-tasks", headers=admin).json()
    assert r["code"] == 0, r
    mine = [i for i in r["data"]["items"] if i["user_id"] == user_id]
    assert mine and mine[0]["overdue"] is False, mine
    assert mine[0]["state"] == "pending" and set(mine[0]["scope"].split(",")) == {
        "mysql", "vector", "minio"}, mine
    # 天数按整天向下取整：登记时间带毫秒，第 14 天当刻会显示 13，故用区间断言
    assert 13 <= mine[0]["elapsed_days"] <= 14, mine
    assert r["data"]["summary"]["alert_days"] == 15, r["data"]["summary"]

    # 第 16 日：注销已 16 天、执行期限已过 → 必须被标为超期（触发告警口径）
    db.query(DataDeletionTask).filter_by(user_id=user_id).update(
        {"created_at": datetime.now() - timedelta(days=16),
         "execute_at": datetime.now() - timedelta(days=1)})
    db.commit()

    r2 = api.c.get("/api/v1/admin/deletion-tasks", headers=admin).json()
    mine2 = [i for i in r2["data"]["items"] if i["user_id"] == user_id]
    assert mine2 and mine2[0]["overdue"] is True, mine2
    assert 15 <= mine2[0]["elapsed_days"] <= 16, mine2
    assert r2["data"]["summary"]["overdue"] >= 1, r2["data"]["summary"]


def test_bv26_deletion_tasks_requires_admin(api):
    """清理任务台账含用户标识，属敏感运营数据，普通用户不可读取。"""
    user = api.synthetic("bv26u", "user")
    r = api.c.get("/api/v1/admin/deletion-tasks", headers=user).json()
    assert r["code"] == ErrCode.PERMISSION_DENIED, r


# --------------------------------------------------------------------------- #
# BV-10 单页题量（8 / 9）：行为在前端 —— 见 _e2e_quiz_paging.py
# --------------------------------------------------------------------------- #
@pytest.mark.skip(reason="BV-10 属前端交互行为（单页 ≤8 题、超出自动分页），"
                         "在浏览器 E2E 中验证：见 _e2e_quiz_paging.py")
def test_bv10_questions_per_page():
    pass
