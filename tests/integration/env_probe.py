# -*- coding: utf-8 -*-
"""集成测试环境探测。

在收集阶段就判断 MySQL / Redis / MinIO 是否真的可用，供 `skipif` 使用。

注意：**端口能连 ≠ 服务可用**。Docker 的端口转发器（vpnkit / com.docker.backend）
会在容器停止时空接 TCP —— 连接能建立，但一发数据就被关闭。因此：
  - Redis 必须用 redis-py 真实 `PING`；
  - MinIO 必须真实访问其桶（`bucket_exists`）。
"""
import os

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def _probe_mysql():
    try:
        from sqlalchemy import create_engine, text

        from app.config import settings
        if not settings.MYSQL_URI:
            return False, "未配置 MYSQL_URI"
        engine = create_engine(settings.MYSQL_URI, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as e:                                  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def _probe_redis():
    try:
        from app.utils.redis_client import redis_client
        if redis_client.ping():
            return True, "ok"
        return False, redis_client._last_error or "PING 无响应"
    except Exception as e:                                  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def _probe_minio():
    try:
        from app.config import settings
        from minio import Minio

        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        if client.bucket_exists(settings.MINIO_BUCKET):
            return True, "ok"
        return False, f"存储桶不存在：{settings.MINIO_BUCKET}"
    except Exception as e:                                  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


MYSQL_OK, MYSQL_REASON = _probe_mysql()
REDIS_OK, REDIS_REASON = _probe_redis()
MINIO_OK, MINIO_REASON = _probe_minio()

SKIP_MYSQL = f"MySQL 不可用：{MYSQL_REASON}"
SKIP_REDIS = f"Redis 不可用：{REDIS_REASON}"
SKIP_MINIO = f"MinIO 不可用：{MINIO_REASON}"
