# -*- coding: utf-8 -*-
"""对 Agent Runtime 所依赖的基础设施执行真实连通性演练。

脚本只做只读健康检查，不写业务数据。输出可作为发布证据保存。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check(name, fn):
    started = time.perf_counter()
    try:
        detail = fn()
        return {"name": name, "status": "passed", "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "status": "failed", "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "detail": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 基础设施真实连通性演练")
    parser.add_argument("--output", type=Path, help="保存 JSON 证据")
    args = parser.parse_args()

    def mysql():
        from sqlalchemy import text
        from app.utils.db import get_session
        session = get_session()
        try:
            value = session.execute(text("SELECT 1")).scalar()
            return {"select_1": value}
        finally:
            session.close()

    def redis():
        from app.utils.redis_client import redis_client
        if not redis_client.ping():
            raise RuntimeError(redis_client.health())
        return {"health": redis_client.health()}

    def minio():
        from app.utils.minio_client import get_minio
        client = get_minio()
        if not client.health():
            raise RuntimeError("MinIO bucket 不可达")
        return {"bucket": client.bucket_name}

    def milvus():
        from pymilvus import Collection, connections, utility
        from app.config import settings
        alias = "agent_drill"
        connections.connect(alias=alias, host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
        exists = utility.has_collection(settings.MILVUS_DEFAULT_COLLECTION, using=alias)
        if exists:
            Collection(settings.MILVUS_DEFAULT_COLLECTION, using=alias).load()
        connections.disconnect(alias)
        return {"collection": settings.MILVUS_DEFAULT_COLLECTION, "exists": bool(exists)}

    checks = [check("mysql", mysql), check("redis", redis), check("minio", minio), check("milvus", milvus)]
    report = {"suite": "agent-infrastructure-drill-v1", "generated_at": time.time(), "checks": checks,
              "passed": all(item["status"] == "passed" for item in checks)}
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
