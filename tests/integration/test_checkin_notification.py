# -*- coding: utf-8 -*-
"""打卡与消息通知集成测试（原型：首页「本周打卡 5/7」/ 顶栏「🔔 消息中心」）。

连真实 MySQL；造数带 run_id 后缀并登记清理，跑完零残留。
"""
from datetime import date, timedelta

from sqlalchemy import text

from app.core.errors import ErrCode
from app.dao.notification_dao import NotificationDAO


# --------------------------------------------------------------------------- #
# 打卡
# --------------------------------------------------------------------------- #
def test_checkin_idempotent_and_stats(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_ck"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from checkin_record where user_id=:u"), {"u": u}))

    r1 = client.post("/api/v1/checkins", headers=headers, json={"check_type": "plan"}).json()
    assert r1["code"] == 0, r1
    assert r1["data"]["stats"]["checked_today"] is True
    assert r1["data"]["stats"]["week_total"] == 7          # 分母固定 7，与原型 5/7 一致

    # 同日同类型重复打卡 → 幂等更新，不新增记录
    r2 = client.post("/api/v1/checkins", headers=headers,
                     json={"check_type": "plan", "note": "补记"}).json()
    assert r2["code"] == 0, r2
    assert r2["data"]["id"] == r1["data"]["id"], r2

    lst = client.get("/api/v1/checkins?days=30", headers=headers).json()
    assert lst["code"] == 0, lst
    assert lst["data"]["total"] == 1, lst

    st = client.get("/api/v1/checkins/stats", headers=headers).json()
    assert st["code"] == 0, st
    assert st["data"]["week_checked"] == 1
    assert st["data"]["week_rate"] == round(1 / 7 * 100)   # 14%
    assert st["data"]["streak"] == 1


def test_checkin_rejects_future_date_and_bad_type(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_ck2"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from checkin_record where user_id=:u"), {"u": u}))

    future = (date.today() + timedelta(days=1)).isoformat()
    r = client.post("/api/v1/checkins", headers=headers,
                    json={"check_type": "plan", "checkin_date": future}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    assert "未来" in r["message"]

    r2 = client.post("/api/v1/checkins", headers=headers, json={"check_type": "nope"}).json()
    assert r2["code"] == ErrCode.PARAM_INVALID, r2


def test_checkin_requires_login(client):
    r = client.post("/api/v1/checkins", json={"check_type": "plan"}).json()
    assert r["code"] == ErrCode.UNAUTHENTICATED, r


# --------------------------------------------------------------------------- #
# 消息通知
# --------------------------------------------------------------------------- #
def test_notification_unread_count_and_mark_read(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_nt"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from notification where user_id=:u"), {"u": u}))

    ids = [NotificationDAO.create(uid, f"测试消息{i}", "内容", "system").id for i in range(3)]

    lst = client.get("/api/v1/notifications", headers=headers).json()
    assert lst["code"] == 0, lst
    assert lst["data"]["total"] == 3, lst
    assert lst["data"]["unread"] == 3, lst

    r = client.post(f"/api/v1/notifications/{ids[0]}/read", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["read"] is True
    assert r["data"]["unread"] == 2, r

    # 幂等：重复标记已读，未读数不变
    r2 = client.post(f"/api/v1/notifications/{ids[0]}/read", headers=headers).json()
    assert r2["data"]["unread"] == 2, r2

    only = client.get("/api/v1/notifications?unread_only=true", headers=headers).json()
    assert only["data"]["total"] == 2, only

    allr = client.post("/api/v1/notifications/read-all", headers=headers).json()
    assert allr["code"] == 0, allr
    assert allr["data"]["unread"] == 0, allr


def test_notification_mark_others_read_returns_404(client, auth_factory, run_id, cleanup):
    owner = f"{run_id}_ntA"
    other = f"{run_id}_ntB"
    cleanup.append(lambda s, u=owner: s.execute(
        text("delete from notification where user_id=:u"), {"u": u}))
    nid = NotificationDAO.create(owner, "私有消息", "", "system").id

    headers = auth_factory(other, "user")
    r = client.post(f"/api/v1/notifications/{nid}/read", headers=headers).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r   # 越权统一 404，防枚举
