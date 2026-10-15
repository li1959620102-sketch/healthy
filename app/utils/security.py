# -*- coding: utf-8 -*-
"""密码加密与登录令牌工具。

密码：pbkdf2_hmac(sha256) + 随机盐，标准库实现，无需额外依赖。
令牌：标准 HS256 JWT，用标准库 hmac 签发（不引入第三方 jwt 库，规避 jwt==1.4.0 冲突）。

令牌类型（payload.type）：
  - access  访问令牌，有效期 2h（ACCESS_EXPIRE_SECONDS）
  - refresh 刷新令牌，有效期 7d（REFRESH_EXPIRE_SECONDS）
  - guest   游客令牌，有效期 30min（GUEST_EXPIRE_SECONDS），权限受限
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from app.config import settings
from app.core.errors import BizError, ErrCode

ACCESS_EXPIRE_SECONDS = 2 * 3600
REFRESH_EXPIRE_SECONDS = 7 * 24 * 3600
GUEST_EXPIRE_SECONDS = 30 * 60
STREAM_EXPIRE_SECONDS = 60  # SSE 短票：解决 EventSource 无法带自定义请求头（设计评审 B1）
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """生成密码密文，格式：pbkdf2_sha256$盐$摘要"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
    )
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与密文是否匹配"""
    if not password or not stored:
        return False
    try:
        algo, salt, hex_digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
        )
        return hmac.compare_digest(digest.hex(), hex_digest)
    except Exception:
        return False


# ---------------------------- 手机号加密 / 哈希 ----------------------------
# 设计说明（数据库设计 V1.1 §3.1）：
#   phone_hash  = HMAC-SHA256(手机号, SECRET_KEY 派生密钥) —— 用于唯一约束与等值检索（不可逆）
#   phone_cipher= AES-256-GCM(手机号) —— 可逆密文，仅在确需展示/发短信时解密
# 两者均由 SECRET_KEY 派生密钥，标准库 + cryptography 实现，不引入额外对称密钥配置。

_PHONE_HMAC_KEY: Optional[bytes] = None
_PHONE_AES_KEY: Optional[bytes] = None


def _phone_keys() -> tuple:
    """由 SECRET_KEY 派生 HMAC 与 AES 密钥（进程内缓存）。"""
    global _PHONE_HMAC_KEY, _PHONE_AES_KEY
    if _PHONE_HMAC_KEY is None:
        material = settings.SECRET_KEY.encode("utf-8")
        _PHONE_HMAC_KEY = hmac.new(material, b"phone-hash-v1", hashlib.sha256).digest()
        _PHONE_AES_KEY = hmac.new(material, b"phone-aes-v1", hashlib.sha256).digest()
    return _PHONE_HMAC_KEY, _PHONE_AES_KEY


def phone_hash(phone: str) -> str:
    """手机号 HMAC-SHA256 十六进制（64 位），用于唯一索引与检索。"""
    key, _ = _phone_keys()
    return hmac.new(key, phone.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_phone(phone: str) -> bytes:
    """手机号 AES-256-GCM 加密，返回 nonce+密文+tag（VARBINARY 存储）。"""
    _, aes_key = _phone_keys()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(aes_key).encrypt(nonce, phone.strip().encode("utf-8"), None)


def decrypt_phone(cipher: bytes) -> Optional[str]:
    """解密手机号密文；密文不合法或密钥不匹配时返回 None（不抛异常）。"""
    if not cipher:
        return None
    try:
        _, aes_key = _phone_keys()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(aes_key).decrypt(cipher[:12], cipher[12:], None).decode("utf-8")
    except Exception:
        return None


def _b64url(raw: bytes) -> str:
    """URL 安全的 base64 编码（去掉填充）"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    """URL 安全的 base64 解码（补回填充）"""
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _sign(unsigned: str) -> str:
    """对 header.payload 做 HMAC-SHA256 签名"""
    return _b64url(
        hmac.new(settings.SECRET_KEY.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256).digest()
    )


def _make_token(user_id: str, username: str, role: str, expire: int, token_type: str) -> str:
    """签发指定类型的 JWT。"""
    header = {"alg": ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "type": token_type,
        "exp": int(time.time()) + expire,
    }
    seg1 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    seg2 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{seg1}.{seg2}.{_sign(seg1 + '.' + seg2)}"


def create_access_token(user_id: str, username: str, role: str) -> str:
    """签发访问令牌（2h）。"""
    return _make_token(user_id, username, role, ACCESS_EXPIRE_SECONDS, "access")


def create_refresh_token(user_id: str, username: str, role: str) -> str:
    """签发刷新令牌（7d）。"""
    return _make_token(user_id, username, role, REFRESH_EXPIRE_SECONDS, "refresh")


def create_guest_token() -> str:
    """签发游客令牌（30min），用于游客试测等匿名体验。"""
    return _make_token("guest", "guest", "guest", GUEST_EXPIRE_SECONDS, "guest")


def refresh_access_token(refresh_token: str) -> str:
    """用刷新令牌换取新的访问令牌；非法/非 refresh 类型抛 BizError。"""
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise BizError(ErrCode.TOKEN_INVALID, "刷新令牌无效或已过期")
    return create_access_token(
        payload.get("sub", "guest"),
        payload.get("username", "guest"),
        payload.get("role", "user"),
    )


def create_token(user_id: str, username: str, role: str) -> str:
    """兼容旧调用：等价于 create_access_token。"""
    return create_access_token(user_id, username, role)


def create_stream_ticket(user_id: str, username: str, role: str) -> str:
    """签发 SSE 短票（60s）：供 EventSource URL ?ticket= 使用，规避无法带 Authorization 头的问题。"""
    return _make_token(user_id, username, role, STREAM_EXPIRE_SECONDS, "stream")


def decode_token(token: str) -> Optional[dict]:
    """解析并校验令牌，签名错误或过期返回 None（不区分类型）。"""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        if not hmac.compare_digest(_sign(parts[0] + "." + parts[1]), parts[2]):
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
