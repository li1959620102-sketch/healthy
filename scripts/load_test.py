# -*- coding: utf-8 -*-
"""并发压测（QPS / 延迟分位 / 错误率）。

设计原则
- **读为主**：主场景只打只读接口，不污染业务库；唯一写路径是 LLM 问答（chat），
  它必然建会话，脚本在结束时按测试用户归属精确清理（chat_message → chat_session）。
- 测试用户自助建/清：固定 user_id `ULOADTEST00001`，幂等创建，压测后连行一起删除。
- 统计口径与运行指标一致：p50/p90/p95/p99 用线性插值，错误含 HTTP 4xx/5xx 与业务码非 0。
- 用法：
    python scripts/load_test.py --scenario probe
    python scripts/load_test.py --scenario ready  --concurrency 30 --duration 10
    python scripts/load_test.py --scenario read   --concurrency 20 --duration 15
    python scripts/load_test.py --scenario chat   --concurrency 4  --total 12
    python scripts/load_test.py --scenario mixed  --concurrency 20 --duration 20
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import pymysql  # noqa: E402

from app.config import settings  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

TEST_UID = "ULOADTEST00001"
TEST_USER = "loadtest"
TEST_PASS = "Demo@2026"

# (method, path, auth, weight, label) —— 轻负载：纯本地/DB 读取，不含模型推理
READ_SET: List[Tuple[str, str, bool, int, str]] = [
    ("GET", "/api/v1/templates", True, 3, "已发布模板列表"),
    ("GET", "/api/v1/agents", True, 2, "智能体目录"),
    ("GET", "/api/v1/scenes", True, 2, "五大健康场景"),
    ("GET", "/api/v1/notifications", True, 3, "消息列表"),
    ("GET", "/api/v1/dashboard/personal/overview", True, 3, "首页 KPI 聚合"),
    ("GET", "/api/v1/users/me", True, 2, "当前用户信息"),
    ("GET", "/api/v1/chat/kb-info", True, 2, "知识库信息"),
    ("GET", "/api/v1/assessments", True, 3, "我的测评列表"),
    ("GET", "/api/v1/checkins/stats", True, 2, "打卡统计"),
]

# 重负载：触发嵌入（Ollama bge-m3）的检索类接口，需与轻负载分开测，避免互相干扰
HEAVY_SET: List[Tuple[str, str, bool, int, str]] = [
    ("GET", "/api/v1/recommend/ingredients?keyword=%E5%B1%B1%E8%8D%AF&top_k=8", True, 1, "食材库检索（嵌入）"),
]

CHAT_BODY = {"question": "山药有什么功效？适合哪些人群食用？"}


def _db():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB, charset="utf8mb4", autocommit=True,
    )


def ensure_test_user() -> None:
    """幂等创建测试用户（角色 user）。"""
    c = _db()
    cur = c.cursor()
    cur.execute("SELECT user_id FROM sys_user WHERE user_id=%s OR username=%s", (TEST_UID, TEST_USER))
    if cur.fetchone():
        cur.execute("UPDATE sys_user SET password_hash=%s, status='active', deleted_at=NULL WHERE user_id=%s",
                    (hash_password(TEST_PASS), TEST_UID))
    else:
        cur.execute(
            "INSERT INTO sys_user (user_id,username,password_hash,real_name,sex,age,role,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (TEST_UID, TEST_USER, hash_password(TEST_PASS), "压测账号", "unknown", 30, "user", "active"))
    c.close()
    print(f"[setup] 测试用户就绪：{TEST_USER} / {TEST_UID}")


def cleanup_test_user() -> Dict[str, int]:
    """删除测试用户及其衍生行（子表优先），返回各表删除行数。"""
    c = _db()
    cur = c.cursor()
    deleted: Dict[str, int] = {}
    try:
        cur.execute("SELECT id FROM chat_session WHERE user_id=%s", (TEST_UID,))
        sids = [r[0] for r in cur.fetchall()]
        if sids:
            ph = ",".join(["%s"] * len(sids))
            cur.execute(f"DELETE FROM chat_message WHERE session_id IN ({ph})", sids)
            deleted["chat_message"] = cur.rowcount
        cur.execute("DELETE FROM chat_session WHERE user_id=%s", (TEST_UID,))
        deleted["chat_session"] = cur.rowcount
        for tbl, col in [("checkin_record", "user_id"), ("notification", "user_id"),
                         ("assessment_record", "user_id"), ("assessment_report", "user_id"),
                         ("diet_plan", "user_id"), ("operation_log", "operator_id"),
                         ("user_profile", "user_id")]:
            try:
                cur.execute(f"DELETE FROM `{tbl}` WHERE `{col}`=%s", (TEST_UID,))
                if cur.rowcount:
                    deleted[tbl] = cur.rowcount
            except Exception:
                pass
        cur.execute("DELETE FROM sys_user WHERE user_id=%s", (TEST_UID,))
        deleted["sys_user"] = cur.rowcount
    finally:
        c.close()
    return deleted


async def login(client: httpx.AsyncClient, base: str) -> str:
    r = await client.post(f"{base}/api/v1/auth/login",
                          json={"username": TEST_USER, "password": TEST_PASS})
    body = r.json()
    if body.get("code") != 0:
        raise SystemExit(f"登录失败：{body}")
    return body["data"]["access_token"]


def _pct(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    k = (len(v) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


class Collector:
    """按 endpoint 收集延迟与错误。"""

    def __init__(self) -> None:
        self.lat: Dict[str, List[float]] = {}
        self.err: Dict[str, int] = {}
        self.cnt: Dict[str, int] = {}
        self.ttfb: List[float] = []
        self._lock = asyncio.Lock()

    async def add(self, key: str, ms: float, ok: bool) -> None:
        async with self._lock:
            self.cnt[key] = self.cnt.get(key, 0) + 1
            self.lat.setdefault(key, []).append(ms)
            if not ok:
                self.err[key] = self.err.get(key, 0) + 1

    async def add_ttfb(self, ms: float) -> None:
        async with self._lock:
            self.ttfb.append(ms)

    def report(self, wall: float) -> Dict[str, Any]:
        total = sum(self.cnt.values())
        errors = sum(self.err.values())
        endpoints = []
        for k in sorted(self.cnt, key=lambda x: -self.cnt[x]):
            v = self.lat[k]
            endpoints.append({
                "endpoint": k, "count": self.cnt[k], "errors": self.err.get(k, 0),
                "qps": round(self.cnt[k] / wall, 2),
                "avg_ms": round(statistics.mean(v), 2) if v else 0,
                "p50_ms": _pct(v, 0.50), "p90_ms": _pct(v, 0.90),
                "p95_ms": _pct(v, 0.95), "p99_ms": _pct(v, 0.99),
                "max_ms": round(max(v), 2) if v else 0,
            })
        allv = [x for v in self.lat.values() for x in v]
        return {
            "wall_seconds": round(wall, 2),
            "total_requests": total,
            "total_errors": errors,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "qps": round(total / wall, 2) if wall > 0 else 0.0,
            "latency_ms": {
                "avg": round(statistics.mean(allv), 2) if allv else 0,
                "p50": _pct(allv, 0.50), "p90": _pct(allv, 0.90),
                "p95": _pct(allv, 0.95), "p99": _pct(allv, 0.99),
                "max": round(max(allv), 2) if allv else 0,
            },
            "chat_ttfb_ms": {
                "avg": round(statistics.mean(self.ttfb), 2) if self.ttfb else 0,
                "p50": _pct(self.ttfb, 0.50), "p95": _pct(self.ttfb, 0.95),
                "n": len(self.ttfb),
            },
            "endpoints": endpoints,
        }


async def _one_read(client, base, token, sem, col: Collector, spec):
    method, path, auth, _, label = spec
    key = f"{method} {path} | {label}"
    async with sem:
        t0 = time.perf_counter()
        ok = True
        try:
            r = await client.get(base + path, headers={"Authorization": f"Bearer {token}"} if auth else {})
            ok = r.status_code == 200 and r.json().get("code") == 0
        except Exception:
            ok = False
        await col.add(key, (time.perf_counter() - t0) * 1000, ok)


async def _one_chat(client, base, token, col: Collector):
    key = "POST /api/v1/chat/query (SSE) | 智能查询"
    t0 = time.perf_counter()
    ok = True
    ttfb = None
    try:
        async with client.stream("POST", f"{base}/api/v1/chat/query", json=CHAT_BODY,
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=httpx.Timeout(120.0)) as r:
            ok = r.status_code == 200
            async for line in r.aiter_lines():
                if ttfb is None and line.startswith("data:"):
                    ttfb = (time.perf_counter() - t0) * 1000
    except Exception:
        ok = False
    if ttfb is not None:
        await col.add_ttfb(ttfb)
    await col.add(key, (time.perf_counter() - t0) * 1000, ok)


async def run_scenario(scn: str, base: str, conc: int, duration: float, total: Optional[int]) -> Dict[str, Any]:
    col = Collector()
    sem = asyncio.Semaphore(conc)
    limits = httpx.Limits(max_connections=conc * 2, max_keepalive_connections=conc)
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0)) as client:
        token = await login(client, base)
        print(f"[run] 场景={scn} 并发={conc} " +
              (f"时长={duration}s" if total is None else f"总请求={total}"))

        if scn == "ready":
            async def ready_worker():
                while True:
                    async with sem:
                        t0 = time.perf_counter()
                        ok = True
                        try:
                            r = await client.get(f"{base}/api/v1/ready")
                            ok = r.status_code == 200 and r.json().get("code") == 0
                        except Exception:
                            ok = False
                        await col.add("GET /api/v1/ready | 依赖就绪探针",
                                      (time.perf_counter() - t0) * 1000, ok)
            wall0 = time.perf_counter()
            tasks = [asyncio.create_task(ready_worker()) for _ in range(conc)]
            await asyncio.sleep(duration if total is None else 0)
            if total is not None:
                await asyncio.sleep(0.01)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return col.report(time.perf_counter() - wall0)

        if scn == "chat":
            n = total or 8
            wall0 = time.perf_counter()
            await asyncio.gather(*[_one_chat(client, base, token, col) for _ in range(n)])
            return col.report(time.perf_counter() - wall0)

        active = HEAVY_SET if scn == "heavy" else READ_SET
        weights = [w for *_, w, _ in active]
        async def read_worker():
            while True:
                try:
                    await _one_read(client, base, token, sem, col,
                                    random.choices(active, weights=weights, k=1)[0])
                except asyncio.CancelledError:
                    raise

        wall0 = time.perf_counter()
        if total is not None:
            async def bounded_read():
                async with sem:
                    await _one_read(client, base, token, sem, col,
                                    random.choices(active, weights=weights, k=1)[0])
            await asyncio.gather(*[bounded_read() for _ in range(total)])
        else:
            tasks = [asyncio.create_task(read_worker()) for _ in range(conc)]
            # 混合负载：读为背景流量，叠加少量问答（LLM 路径），约每 4 秒一次
            chat_task = None
            if scn == "mixed":
                async def chat_loop():
                    while True:
                        await _one_chat(client, base, token, col)
                        await asyncio.sleep(4.0)
                chat_task = asyncio.create_task(chat_loop())
                tasks.append(chat_task)
            await asyncio.sleep(duration)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return col.report(time.perf_counter() - wall0)


async def probe(base: str) -> Dict[str, Any]:
    """逐接口探活一次，确认压测目标集可用。"""
    out = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        token = await login(client, base)
        for spec in READ_SET + HEAVY_SET:
            method, path, auth, _, label = spec
            try:
                r = await client.get(base + path, headers={"Authorization": f"Bearer {token}"} if auth else {})
                body = r.json()
                out.append({"path": path, "http": r.status_code, "code": body.get("code"), "label": label})
            except Exception as e:
                out.append({"path": path, "error": str(e), "label": label})
    return {"probe": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8019")
    ap.add_argument("--scenario", default="read",
                    choices=["probe", "ready", "read", "heavy", "chat", "mixed"])
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--total", type=int, default=None)
    ap.add_argument("--out", default="_loadtest_result.json")
    ap.add_argument("--keep-user", action="store_true", help="保留测试用户（默认压测后删除）")
    args = ap.parse_args()

    ensure_test_user()
    try:
        if args.scenario == "probe":
            res = asyncio.run(probe(args.base_url))
        else:
            res = asyncio.run(run_scenario(args.scenario, args.base_url,
                                           args.concurrency, args.duration, args.total))
        res["scenario"] = args.scenario
        res["concurrency"] = args.concurrency
        print(json.dumps(res, ensure_ascii=False, indent=2))
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[saved] {args.out}")
    finally:
        if not args.keep_user:
            d = cleanup_test_user()
            print(f"[cleanup] 清理测试用户及其衍生行：{d}")


if __name__ == "__main__":
    main()
