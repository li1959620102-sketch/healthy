# -*- coding: utf-8 -*-
"""进程内定时任务调度（APScheduler）。

## 为什么是进程内，而不是宿主机 cron 调脚本

宿主机 cron 的优点是"进程崩了也照跑"，但它有两件事做不到：

  1. **看不到执行结果**。任务失败了只留在 crontab 的邮件或系统日志里，
     运维得登机器翻；而定时清理和预警求值都是**合规相关**的，
     "上周有没有跑过、跑成功没有"必须能在系统里查到。
  2. **配置分两处**。执行时间在 crontab、阈值在数据库，改一次要动两边。

代价是"服务停了任务就停"。这个代价可以接受，因为任务本身依赖的
MySQL / Redis / MinIO 都在这套服务旁边，服务停了它也跑不了；
而且启动时会补跑（`misfire_grace_time` + `coalesce`），停机期间错过的
一次会在启动后立刻补一次，不会因为停机一天就漏掉一天。

## 多副本怎么防止重复执行

用 Redis 的 `SET NX` 做**按任务维度的互斥锁**：抢到锁的实例执行，其余跳过。
Redis 不可用时 `set_nx` 返回 `None`，此时**放行**——单实例部署是常态，
为了让"Redis 抖一下"不至于把定时任务全部静默停掉。多副本 + Redis 挂
这个组合下可能重复执行一次，对"清理"（幂等删除）和"预警"（按天去重）
都是可接受的，比漏跑安全。

## 线程模型

BackgroundScheduler 起自己的线程池，任务函数是**同步**的（DB 驱动是同步的）。
异步端点里调 `run_now()` 时必须用 `asyncio.to_thread` 包一层，
否则会阻塞事件循环——这是本项目踩过的老坑。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("Scheduler")

# 任务注册表：name -> 元信息。执行体按名字懒加载，
# 避免 import 期就把 services 全拖进来（也避免循环依赖）。
JOBS: Dict[str, Dict[str, Any]] = {
    "deletion_purge": {
        "name": "注销数据清理",
        "desc": "扫描到期的 data_deletion_task 并执行 MySQL/MinIO 清理",
        "target": "app.services.deletion_service:run_due_deletions",
        "lock_ttl": 3600,
    },
    "alert_eval": {
        "name": "预警阈值求值",
        "desc": "读取 alert.thresholds 求值并落预警记录 + 站内通知",
        "target": "app.services.alert_service:evaluate_alerts",
        "lock_ttl": 900,
    },
}


class Scheduler:
    """APScheduler 的薄封装：启停、状态、手动触发。"""

    def __init__(self) -> None:
        self._sched = None
        self._started = False
        self._error = ""
        self._lock = threading.Lock()
        self._last: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """启动调度器。返回是否真正启动（未启用/已启动/启动失败都返回 False）。"""
        from app.config import settings

        if not settings.SCHEDULER_ENABLE:
            logger.info("定时任务未启用（SCHEDULER_ENABLE=false）")
            return False
        with self._lock:
            if self._started:
                return False
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
                from apscheduler.triggers.cron import CronTrigger
                from apscheduler.triggers.interval import IntervalTrigger
            except ImportError as e:                       # pragma: no cover
                self._error = f"APScheduler 未安装：{e}"
                logger.error(self._error)
                return False

            try:
                sched = BackgroundScheduler(
                    timezone=settings.SCHEDULER_TIMEZONE,
                    job_defaults={
                        # 停机期间错过的任务，启动后补跑一次（不补跑历史全部）
                        "coalesce": True,
                        # 允许迟到 1 小时内补跑
                        "misfire_grace_time": 3600,
                        # 同一任务不并发（上一轮没跑完就跳过本轮）
                        "max_instances": 1,
                    },
                )
                sched.add_job(
                    self._wrap("deletion_purge"),
                    CronTrigger(hour=settings.DELETION_PURGE_HOUR, minute=10),
                    id="deletion_purge", replace_existing=True,
                    name=JOBS["deletion_purge"]["name"],
                )
                sched.add_job(
                    self._wrap("alert_eval"),
                    IntervalTrigger(minutes=settings.ALERT_EVAL_MINUTES),
                    id="alert_eval", replace_existing=True,
                    name=JOBS["alert_eval"]["name"],
                )
                sched.start()
                self._sched = sched
                self._started = True
                self._error = ""
                logger.info(
                    "定时任务已启动：deletion_purge 每日 %02d:10，alert_eval 每 %d 分钟（时区 %s）",
                    settings.DELETION_PURGE_HOUR, settings.ALERT_EVAL_MINUTES,
                    settings.SCHEDULER_TIMEZONE,
                )
                return True
            except Exception as e:                         # noqa: BLE001
                self._error = f"{type(e).__name__}: {e}"
                logger.error(f"定时任务启动失败：{self._error}")
                return False

    def shutdown(self) -> None:
        with self._lock:
            if not self._started or self._sched is None:
                return
            try:
                # wait=False：容器停机的宽限期内不等长任务跑完，
                # 未完成的任务靠下一轮重新扫描（两个任务都是幂等的）
                self._sched.shutdown(wait=False)
            except Exception as e:                         # noqa: BLE001
                logger.warning(f"定时任务停止异常（忽略）：{e}")
            finally:
                self._started = False
                self._sched = None

    # ------------------------------------------------------------------ #
    # 执行包装
    # ------------------------------------------------------------------ #
    def _resolve(self, name: str) -> Optional[Callable[[], Any]]:
        spec = JOBS.get(name)
        if not spec:
            return None
        module_path, func_name = spec["target"].split(":")
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, func_name)

    def _wrap(self, name: str) -> Callable[[], None]:
        """包一层：抢锁 → 执行 → 记录结果。异常必须自己吞掉并落日志，
        否则 APScheduler 会把它当未捕获异常打一屏 traceback，
        但状态查询里什么都看不到。"""

        def runner() -> None:
            spec = JOBS.get(name) or {}
            started = datetime.now()
            # 不抢锁直接跑：单实例部署时 Redis 可能没配
            import uuid

            from app.utils.redis_client import redis_client

            key = f"scheduler:lock:{name}"
            token = uuid.uuid4().hex
            got = redis_client.set_nx(key, token, int(spec.get("lock_ttl") or 300))
            if got is False:
                logger.info(f"定时任务 {name} 已被其他实例执行，本次跳过")
                self._last[name] = {
                    "at": started.strftime("%Y-%m-%d %H:%M:%S"),
                    "ok": True, "skipped": True, "elapsed_ms": 0,
                    "result": "另一实例持有锁，本次跳过", "error": "",
                }
                return
            try:
                fn = self._resolve(name)
                if fn is None:
                    raise RuntimeError(f"任务 {name} 未注册执行体")
                result = fn()
                self._last[name] = {
                    "at": started.strftime("%Y-%m-%d %H:%M:%S"),
                    "ok": True, "skipped": False,
                    "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
                    "result": result, "error": "",
                }
                logger.info(f"定时任务 {name} 完成：{result}")
            except Exception as e:                         # noqa: BLE001
                self._last[name] = {
                    "at": started.strftime("%Y-%m-%d %H:%M:%S"),
                    "ok": False, "skipped": False,
                    "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
                    "result": None, "error": f"{type(e).__name__}: {e}",
                }
                logger.exception(f"定时任务 {name} 失败")
            finally:
                # 「比对后释放」：只有锁里还是自己那个令牌才删。
                # 直接删会误删别人的锁（自己的 ttl 已过期、另一实例刚抢到时）；
                # 不删则下一轮会被自己的锁挡到 ttl 过期为止——每 15 分钟一次的
                # 任务配 900 秒 ttl，等于实际变成半小时一次，这是真会踩的坑。
                try:
                    if redis_client.get(key) == token:
                        redis_client.delete(key)
                except Exception as e:                     # noqa: BLE001
                    logger.debug(f"释放调度锁失败（交 ttl 兜底）：{e}")

        runner.__name__ = f"job_{name}"
        return runner

    # ------------------------------------------------------------------ #
    # 对外查询 / 手动触发
    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        from app.config import settings

        jobs: List[Dict[str, Any]] = []
        for name, spec in JOBS.items():
            item: Dict[str, Any] = {
                "id": name, "name": spec["name"], "desc": spec["desc"],
                "last": self._last.get(name),
            }
            if self._sched is not None:
                job = self._sched.get_job(name)
                if job is not None:
                    nxt = getattr(job, "next_run_time", None)
                    item["next_run"] = nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else None
            jobs.append(item)
        return {
            "enabled": bool(settings.SCHEDULER_ENABLE),
            "running": self._started,
            "timezone": settings.SCHEDULER_TIMEZONE,
            "error": self._error,
            "jobs": jobs,
        }

    def run_now(self, name: str) -> Dict[str, Any]:
        """立即执行一次（管理员补偿用）。同步阻塞，异步端点里必须 to_thread。"""
        if name not in JOBS:
            raise KeyError(name)
        self._wrap(name)()
        return self._last.get(name) or {}


scheduler = Scheduler()
