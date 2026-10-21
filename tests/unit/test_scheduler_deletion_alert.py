# -*- coding: utf-8 -*-
"""定时任务、注销清理和预警核心边界测试。"""
from datetime import datetime

import pytest

from app.core.scheduler import Scheduler
from app.services import alert_service, deletion_service

pytestmark = pytest.mark.unit


class FakeCursor:
    def __init__(self, count=0):
        self.count = count
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.count

    def fetchone(self):
        return (self.count,)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, count=0):
        self.cursor_instance = FakeCursor(count)
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def test_deletion_dry_run_counts_stale_tasks_without_update():
    conn = FakeConnection(count=3)
    assert deletion_service.reclaim_stale(conn, apply=False) == 3
    assert len(conn.cursor_instance.calls) == 1
    assert conn.cursor_instance.calls[0][0].lstrip().startswith("SELECT COUNT(*)")
    assert conn.commits == 0


def test_deletion_apply_reclaims_stale_tasks_and_commits():
    conn = FakeConnection(count=2)
    assert deletion_service.reclaim_stale(conn, apply=True) == 2
    sql, _ = conn.cursor_instance.calls[0]
    assert sql.lstrip().startswith("UPDATE data_deletion_task")
    assert conn.commits == 1


class FakeRedis:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.value = None
        self.deleted = []

    def set_nx(self, key, token, ttl):
        self.key = key
        self.value = token if self.acquired else None
        return self.acquired

    def get(self, key):
        return self.value

    def delete(self, key):
        self.deleted.append(key)
        self.value = None


def test_scheduler_releases_its_lock_after_success(monkeypatch):
    from app.utils.redis_client import redis_client

    fake = FakeRedis()
    monkeypatch.setattr(redis_client, "set_nx", fake.set_nx)
    monkeypatch.setattr(redis_client, "get", fake.get)
    monkeypatch.setattr(redis_client, "delete", fake.delete)
    scheduler = Scheduler()
    scheduler._resolve = lambda name: (lambda: {"done": 1})

    scheduler._wrap("alert_eval")()

    assert scheduler._last["alert_eval"]["ok"] is True
    assert scheduler._last["alert_eval"]["result"] == {"done": 1}
    assert fake.deleted == ["scheduler:lock:alert_eval"]


def test_scheduler_skips_when_another_instance_holds_lock(monkeypatch):
    from app.utils.redis_client import redis_client

    fake = FakeRedis(acquired=False)
    monkeypatch.setattr(redis_client, "set_nx", fake.set_nx)
    scheduler = Scheduler()
    called = []
    scheduler._resolve = lambda name: (lambda: called.append(name))

    scheduler._wrap("alert_eval")()

    assert called == []
    assert scheduler._last["alert_eval"]["skipped"] is True
    assert fake.deleted == []


class FakeAlertQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class FakeAlertSession:
    def __init__(self, row=None):
        self.row = row
        self.added = []

    def query(self, model):
        return FakeAlertQuery(self.row)

    def add(self, item):
        self.added.append(item)


def test_alert_upsert_adds_first_daily_occurrence():
    session = FakeAlertSession()
    item = {"alert_key": "score_low_ratio", "level": "warn", "title": "比例超阈值",
            "detail": {"ratio": 20}, "value": "20%", "threshold": "10%"}
    assert alert_service._upsert(session, item, "2026-10-17") == "new"
    assert len(session.added) == 1
    assert session.added[0].dedup_key == "2026-10-17"


def test_alert_upsert_only_escalates_when_level_increases():
    existing = type("OpsAlertStub", (), {
        "level": "warn", "value": "11%", "threshold": "10%", "detail": {}, "title": "old"
    })()
    session = FakeAlertSession(existing)
    item = {"alert_key": "score_low_ratio", "level": "critical", "title": "比例严重超阈值",
            "detail": {"ratio": 30}, "value": "30%", "threshold": "10%"}

    assert alert_service._upsert(session, item, "2026-10-17") == "escalated"
    assert existing.level == "critical"
    assert existing.value == "30%"
    assert session.added == []


def test_alert_same_level_refresh_does_not_escalate():
    existing = type("OpsAlertStub", (), {
        "level": "warn", "value": "11%", "threshold": "10%", "detail": {}, "title": "old"
    })()
    session = FakeAlertSession(existing)
    item = {"alert_key": "score_low_ratio", "level": "warn", "title": "比例超阈值",
            "detail": {"ratio": 15}, "value": "15%", "threshold": "10%"}

    assert alert_service._upsert(session, item, "2026-10-17") == "same"
    assert existing.value == "15%"
    assert alert_service._level_rank("critical") > alert_service._level_rank("warn")
