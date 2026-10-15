# -*- coding: utf-8 -*-
"""对象存储（MinIO）与 Redis 真连集成测试（2026-09-10 起不再跳过）。

覆盖：
  - `/ready` 同时把 redis / minio 报为 ok（两者均为必备依赖）
  - Redis 封装层真实读写（SET / GET / INCR / JSON / DELETE）
  - MinIO 客户端 上传 → 预签名 URL → 下载 → 删除 往返
  - 舌象上传端点真实落对象存储（存储侧链路打通）
  - 知识库上传的扩展名守卫（BV-28 格式拒绝分支）

依赖真实服务：`ai-health-redis`(6380) / `ai-health-minio`(9000)。
"""
import io
import time
from datetime import timedelta

from sqlalchemy import text

from app.utils.minio_client import get_minio


# --------------------------------------------------------------------------- #
# 就绪探针
# --------------------------------------------------------------------------- #
def test_ready_reports_redis_and_minio_and_aggregates_all_dependencies(client):
    r = client.get("/api/v1/ready").json()
    assert r["code"] == 0, r
    checks = r["data"]["checks"]
    assert checks.get("redis") == "ok", checks
    assert checks.get("minio") == "ok", checks
    assert r["data"]["ready"] is all(value == "ok" for value in checks.values()), checks


# --------------------------------------------------------------------------- #
# Redis 封装层
# --------------------------------------------------------------------------- #
def test_redis_wrapper_roundtrip(redis_required):
    from app.utils.redis_client import redis_client

    ns = f"ai_health:it:{int(time.time() * 1000)}"
    try:
        assert redis_client.ping() is True
        assert redis_client.set(f"{ns}:s", "v1", ttl=30) is True
        assert redis_client.get(f"{ns}:s") == "v1"
        assert redis_client.incr_with_ttl(f"{ns}:n", 30) == 1
        assert redis_client.incr_with_ttl(f"{ns}:n", 30) == 2
        assert redis_client.set_json(f"{ns}:j", {"a": 1, "b": [2, 3]}, ttl=30) is True
        assert redis_client.get_json(f"{ns}:j") == {"a": 1, "b": [2, 3]}
    finally:
        redis_client.delete(f"{ns}:s", f"{ns}:n", f"{ns}:j")


# --------------------------------------------------------------------------- #
# MinIO 客户端往返
# --------------------------------------------------------------------------- #
def test_minio_client_roundtrip(minio_required):
    mc = get_minio()
    obj = f"it/{int(time.time() * 1000)}.txt"
    payload = "药食同源 · MinIO 往返自检".encode("utf-8")
    try:
        assert mc.health() is True
        assert mc.upload_file(io.BytesIO(payload), obj) == obj

        # 预签名 URL 可用于直接下载
        url = mc.get_presigned_url(obj, timedelta(minutes=5))
        assert "X-Amz-Signature" in url and obj in url

        resp = mc.get_object(obj)
        try:
            assert resp.read() == payload
        finally:
            resp.close()
            resp.release_conn()
    finally:
        mc.client.remove_object(mc.bucket_name, obj)


# --------------------------------------------------------------------------- #
# 端到端：舌象上传真实落对象存储
# --------------------------------------------------------------------------- #
def test_tongue_upload_stores_object(client, auth_factory, run_id, cleanup, minio_required):
    headers = auth_factory(f"{run_id}_tongue", "user")
    payload = b"\xff\xd8\xff\xe0" + b"fake-jpeg-bytes" * 10

    r = client.post(
        "/api/v1/tongue/upload",
        headers=headers,
        files={"file": ("tongue.jpg", payload, "image/jpeg")},
    ).json()
    assert r["code"] == 0, r
    assert r["data"]["status"] == "uploaded", r
    image_id = r["data"]["image_id"]
    object_name = r["data"]["image_url"]

    mc = get_minio()
    cleanup.append(lambda s, i=image_id: s.execute(
        text("delete from tongue_image where id=:i"), {"i": i}))
    cleanup.append(lambda s, o=object_name: mc.client.remove_object(mc.bucket_name, o))

    # 对象确实在桶里，且内容一致
    resp = mc.get_object(object_name)
    try:
        assert resp.read() == payload
    finally:
        resp.close()
        resp.release_conn()


# --------------------------------------------------------------------------- #
# BV-28 知识库上传：扩展名守卫
# --------------------------------------------------------------------------- #
def test_bv28_knowledge_reject_unsupported_extension(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_kb", "admin")
    r = client.post(
        "/api/v1/knowledge/docs",
        headers=headers,
        files={"file": ("hack.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"collection_name": "it_test", "category": "guideline"},
    ).json()
    assert r["code"] != 0, r
    assert "不支持" in r["message"], r
