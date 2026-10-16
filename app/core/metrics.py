# -*- coding: utf-8 -*-
"""运行指标采集（轻量、零外部依赖）。

设计意图
- 压测与运营需要"看得见的数字"：每个路由被调多少次、错误率多少、延迟分位多少。
- 不引入 Prometheus / OpenTelemetry 等重依赖，纯内存统计 + 一个只读端点即可。
- 只做"观测"，不做"限流 / 熔断"，避免与业务逻辑耦合。

关键取舍（踩坑记录）
- 路径归一化：`/api/v1/reports/123` 必须并成 `/api/v1/reports/{id}`，否则每次不同 id
  会撑爆维度、指标失去意义。优先取 Starlette 匹配后的 `scope["route"].path`（FastAPI 0.133
  在匹配成功时会写入 `route`）；未匹配（如 404）再退回正则归一化。
- 延迟样本用定长 `deque(maxlen=N)`：内存有上界，P95/P99 反映"近期"而非"历史全量"。
- **业务错误走 HTTP 200 + 非 0 错误码**，光看 HTTP 状态码统计不到。因此异常处理器里
  显式调用 `record_biz_error()`；又因为处理器在中间件 `finish()` 之前执行，这里用
  `_pending_biz` 暂存，等 `finish()` 时并回该路由，避免漏统计或重复计数。
- 多线程安全：uvicorn 单进程多线程执行同步路由，所有写操作走 `threading.Lock`。
"""
import os
import re
import threading
import time
from collections import Counter, deque
from typing import Any, Dict, List, Optional

_MAX_SAMPLES = 4096      # 全局延迟样本上限
_MAX_PER_ROUTE = 512     # 单路由延迟样本上限
_TOPN_ROUTES = 40        # 返回的路由条数上限

# 形如 123 / 16 位以上十六进制 / UUID 的路径段，一律并成 {id}
_ID_SEG = re.compile(
    r"^(?:\d+|[0-9a-fA-F]{16,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def normalize_path(raw: str, route_path: Optional[str] = None) -> str:
    """把具体路径归一成"路由模板"，便于按接口聚合。"""
    if route_path:
        return route_path
    parts = [p for p in raw.split("/") if p]
    return "/" + "/".join("{id}" if _ID_SEG.match(p) else p for p in parts)


def _percentile(sorted_vals: List[float], q: float) -> float:
    """线性插值分位数；空集返回 0。"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 2)


class MetricsRegistry:
    """进程内指标注册表（单例由模块底部导出）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.total = 0          # 累计请求数
        self.http_errors = 0    # HTTP 4xx/5xx
        self.biz_errors = 0     # 业务错误码（HTTP 200 但 code != 0）
        self.inflight = 0
        self.max_inflight = 0
        self._lat: deque = deque(maxlen=_MAX_SAMPLES)
        self._routes: Dict[str, Dict[str, Any]] = {}
        self._biz_codes: Counter = Counter()
        self._pending_biz: Counter = Counter()   # key -> 尚未并回的业务错误数

    # ---------- 采集侧 ----------
    def begin(self) -> None:
        with self._lock:
            self.inflight += 1
            if self.inflight > self.max_inflight:
                self.max_inflight = self.inflight

    def record_biz_error(self, method: str, path: str, code: int) -> None:
        """异常处理器调用：HTTP 200 响应体里的非 0 错误码也计为错误。"""
        key = f"{method} {path}"
        with self._lock:
            self._biz_codes[code] += 1
            self._pending_biz[key] += 1

    def finish(self, method: str, path: str, status: int, elapsed_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            self.inflight = max(0, self.inflight - 1)
            self.total += 1
            http_err = 1 if status >= 400 else 0
            pend = self._pending_biz.pop(key, 0)
            self.http_errors += http_err
            self.biz_errors += pend
            self._lat.append(elapsed_ms)

            r = self._routes.get(key)
            if r is None:
                r = {
                    "method": method, "path": path, "count": 0, "errors": 0,
                    "sum_ms": 0.0, "max_ms": 0.0, "lat": deque(maxlen=_MAX_PER_ROUTE),
                }
                self._routes[key] = r
            r["count"] += 1
            r["errors"] += http_err + pend
            r["sum_ms"] += elapsed_ms
            if elapsed_ms > r["max_ms"]:
                r["max_ms"] = elapsed_ms
            r["lat"].append(elapsed_ms)

    # ---------- 读取侧 ----------
    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            lat = sorted(self._lat)
            total, http_err, biz_err = self.total, self.http_errors, self.biz_errors
            errs = http_err + biz_err
            routes: List[Dict[str, Any]] = []
            for r in self._routes.values():
                sl = sorted(r["lat"])
                cnt = r["count"]
                routes.append({
                    "method": r["method"],
                    "path": r["path"],
                    "count": cnt,
                    "errors": r["errors"],
                    "error_rate": round(r["errors"] / cnt, 4) if cnt else 0.0,
                    "avg_ms": round(r["sum_ms"] / cnt, 2) if cnt else 0.0,
                    "p95_ms": _percentile(sl, 0.95),
                    "max_ms": round(r["max_ms"], 2),
                })
            routes.sort(key=lambda x: x["count"], reverse=True)
            biz_codes = dict(self._biz_codes.most_common(20))
            uptime = round(now - self.started_at, 2)
            snap = {
                "uptime_seconds": uptime,
                "process": _process_info(),
                "requests": {
                    "total": total,
                    "http_errors": http_err,
                    "biz_errors": biz_err,
                    "errors": errs,
                    "error_rate": round(errs / total, 4) if total else 0.0,
                    "rps_avg": round(total / uptime, 3) if uptime > 0 else 0.0,
                    "inflight": self.inflight,
                    "max_inflight": self.max_inflight,
                },
                "latency_ms": {
                    "p50": _percentile(lat, 0.50),
                    "p90": _percentile(lat, 0.90),
                    "p95": _percentile(lat, 0.95),
                    "p99": _percentile(lat, 0.99),
                    "max": round(lat[-1], 2) if lat else 0.0,
                    "avg": round(sum(lat) / len(lat), 2) if lat else 0.0,
                    "samples": len(lat),
                },
                "biz_error_codes": biz_codes,
                "routes_total": len(self._routes),
                "routes": routes[:_TOPN_ROUTES],
            }
        snap["db_pool"] = _db_pool_info()
        return snap

    def reset(self) -> None:
        """清空统计（压测前对齐基线用）。"""
        with self._lock:
            self.total = self.http_errors = self.biz_errors = 0
            self.inflight = self.max_inflight = 0
            self._lat.clear()
            self._routes.clear()
            self._biz_codes.clear()
            self._pending_biz.clear()
            self.started_at = time.time()


def _process_info() -> Dict[str, Any]:
    """进程内存/线程数；psutil 缺失时降级为空。"""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        with p.oneshot():
            return {
                "pid": p.pid,
                "rss_mb": round(p.memory_info().rss / 1048576, 1),
                "threads": p.num_threads(),
                "cpu_percent": p.cpu_percent(interval=None),
            }
    except Exception as e:  # pragma: no cover - 依赖缺失时不影响主流程
        return {"error": str(e)}


def _db_pool_info() -> Optional[Dict[str, Any]]:
    """SQLAlchemy 连接池占用情况；取不到返回 None。"""
    try:
        from app.utils.db import db_manager
        pool = getattr(getattr(db_manager, "engine", None), "pool", None)
        if pool is None:
            return None
        info: Dict[str, Any] = {}
        for name in ("size", "checkedout", "overflow"):
            fn = getattr(pool, name, None)
            if callable(fn):
                info[name] = fn()
        return info or None
    except Exception:
        return None


metrics = MetricsRegistry()


def install_metrics(app) -> None:
    """注册采集中间件（在 main.py 创建 app 后调用一次）。"""
    from fastapi import Request  # 局部导入，避免模块级耦合

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        started = time.perf_counter()
        metrics.begin()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            route = request.scope.get("route")
            path = normalize_path(request.url.path, getattr(route, "path", None))
            metrics.finish(request.method, path, status, elapsed_ms)
