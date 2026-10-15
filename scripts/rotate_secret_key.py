# -*- coding: utf-8 -*-
"""SECRET_KEY 轮换后的存量密文迁移脚本。

背景
----
手机号/邮箱密文是由 `SECRET_KEY` 派生的 AES-256-GCM 密钥加密、由同一 SECRET_KEY 派生的
HMAC 密钥计算检索哈希（见 app/utils/security.py::_phone_keys）。因此**直接替换 SECRET_KEY
会让存量用户的 phone_cipher / phone_hash 全部作废**——老用户按手机号登录会查不到人。

本脚本用「旧密钥解密 → 新密钥加密」把存量行迁移到新密钥下，保证轮换无感。

用法
----
    OLD_SECRET_KEY='旧值' python scripts/rotate_secret_key.py            # 预演（默认，只统计不写）
    OLD_SECRET_KEY='旧值' python scripts/rotate_secret_key.py --apply    # 真正写库

安全性
------
- 默认预演，需显式 `--apply` 才落库；执行前建议先 `mysqldump` 备份 sys_user。
- 解密失败的行会被跳过并单独列出（不覆盖、不清空），便于人工核对。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys

import pymysql

from app.config import settings

OLD_SECRET_KEY = os.environ.get("OLD_SECRET_KEY", "")
NEW_SECRET_KEY = settings.SECRET_KEY

TARGETS = (  # (密文字段, 哈希字段, 密钥派生标签)
    ("phone_cipher", "phone_hash", "phone"),
    ("email_cipher", "email_hash", "email"),
)


def _derive(material: str) -> tuple[bytes, bytes]:
    """与 app/utils/security.py 完全一致的密钥派生。"""
    raw = material.encode("utf-8")
    return (
        hmac.new(raw, b"phone-hash-v1", hashlib.sha256).digest(),
        hmac.new(raw, b"phone-aes-v1", hashlib.sha256).digest(),
    )


def _hmac_hex(plain: str, key: bytes) -> str:
    return hmac.new(key, plain.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _encrypt(plain: str, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plain.strip().encode("utf-8"), None)


def _decrypt(cipher: bytes, key: bytes) -> str | None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not cipher:
        return None
    try:
        return AESGCM(key).decrypt(bytes(cipher)[:12], bytes(cipher)[12:], None).decode("utf-8")
    except Exception:
        return None


def main() -> int:
    apply_changes = "--apply" in sys.argv

    if not OLD_SECRET_KEY:
        print("[X] 必须通过环境变量 OLD_SECRET_KEY 提供轮换前的旧密钥")
        return 2
    if OLD_SECRET_KEY == NEW_SECRET_KEY:
        print("[X] 新旧密钥相同，无需迁移")
        return 2

    old_hash_key, old_aes_key = _derive(OLD_SECRET_KEY)
    new_hash_key, new_aes_key = _derive(NEW_SECRET_KEY)

    conn = pymysql.connect(
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB, charset="utf8mb4", autocommit=False,
    )
    cur = conn.cursor()

    total = migrated = failed = 0
    failures: list[str] = []

    for cipher_col, hash_col, label in TARGETS:
        cur.execute(
            f"SELECT user_id, {cipher_col} FROM sys_user "
            f"WHERE {cipher_col} IS NOT NULL AND {hash_col} IS NOT NULL"
        )
        rows = cur.fetchall()
        print(f"[i] {label}: 待迁移 {len(rows)} 行")
        for user_id, cipher in rows:
            total += 1
            plain = _decrypt(cipher, old_aes_key)
            if plain is None:
                # 兼容上一轮已是新密钥的行（幂等重跑）
                if _decrypt(cipher, new_aes_key) is not None:
                    migrated += 1
                    continue
                failed += 1
                failures.append(f"{user_id} / {label}")
                continue
            new_cipher = _encrypt(plain, new_aes_key)
            new_hash = _hmac_hex(plain, new_hash_key)
            print(f"    - {user_id} {label} → 重加密 {'(写库)' if apply_changes else '(预演)'}")
            if apply_changes:
                cur.execute(
                    f"UPDATE sys_user SET {cipher_col}=%s, {hash_col}=%s WHERE user_id=%s",
                    (new_cipher, new_hash, user_id),
                )
            migrated += 1

    if apply_changes:
        conn.commit()
        print("[OK] 已提交")
    else:
        conn.rollback()
        print("[i] 预演模式，未写库（加 --apply 才落库）")

    print(f"[汇总] 共 {total} 行 / 迁移 {migrated} / 失败 {failed}")
    if failures:
        print("[!] 解密失败（未改动）：" + ", ".join(failures))
        print("    请确认 OLD_SECRET_KEY 是否为轮换前的真实值。")
    conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
