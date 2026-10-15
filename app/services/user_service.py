# -*- coding: utf-8 -*-
"""用户服务：注册、登录、令牌校验、默认账号初始化。"""
import logging
import uuid
from typing import Optional

from app.config import settings
from app.core import rules
from app.dao.user_dao import UserDAO
from app.exceptions.base_api_exception import (
    AuthenticationException,
    ConflictException,
    ValidationException,
)
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    ACCESS_EXPIRE_SECONDS,
    decode_token,
    encrypt_phone,
    hash_password,
    phone_hash,
    verify_password,
)

logger = logging.getLogger(__name__)

DEFAULT_USERNAME = "demo"
DEFAULT_PASSWORD = "123456"


class UserService:
    """用户账号服务"""

    @staticmethod
    def register(username: str, password: str, real_name: str = "",
                 role: str = "user", sex: str = "其他", age: int = None) -> dict:
        if not username or not password:
            raise ValidationException("账号和密码不能为空")
        # 密码规则收敛到 app/core/rules（8–64 位），避免与 schema 两套口径
        pwd_rule = rules.check_password(password)
        if not pwd_rule.ok:
            raise ValidationException(pwd_rule.message)
        if role != "user":
            raise ValidationException("公开注册只能创建普通用户，专业角色需经后台审核授予")

        if UserDAO.get_by_username(username):
            raise ConflictException("该账号已被注册")

        user_id = "U" + uuid.uuid4().hex[:12].upper()
        user = UserDAO.create({
            "user_id": user_id,
            "username": username,
            "password_hash": hash_password(password),
            "real_name": real_name or username,
            "sex": sex,
            "age": age,
            "role": role,
            "status": "active",
        })
        logger.info(f"用户注册成功：{username}")
        return user.to_dict()

    # ---------------- 手机号注册（短信验证码） ---------------- #
    @staticmethod
    def register_by_phone(phone: str, verify_code: str, password: str,
                          real_name: str = "", role: str = "user") -> dict:
        """手机号 + 短信验证码注册：验证码校验 → 手机号查重 → 建号（用户名自动生成）。

        登录后续走「手机号 + 密码」通道；username 仅作系统内部唯一标识。
        """
        from app.services.sms_service import sms_service

        ph = rules.check_phone(phone)
        if not ph.ok:
            raise ValidationException(ph.message)
        if not verify_code:
            raise ValidationException("请输入短信验证码")
        pwd_rule = rules.check_password(password)
        if not pwd_rule.ok:
            raise ValidationException(pwd_rule.message)
        if role != "user":
            raise ValidationException("公开注册只能创建普通用户，专业角色需经后台审核授予")

        phone = str(phone).strip()
        ph_hmac = phone_hash(phone)

        # 先查重（避免无谓消耗一次性验证码），再校验验证码
        if UserDAO.get_by_phone_hash(ph_hmac):
            raise ConflictException("该手机号已注册，请直接登录")
        sms_service.verify_code(phone, verify_code)

        # 用户名自动生成（系统内部唯一标识；用户用手机号+密码登录）
        username = "u" + uuid.uuid4().hex[:12]
        while UserDAO.get_by_username(username):
            username = "u" + uuid.uuid4().hex[:12]

        user_id = "U" + uuid.uuid4().hex[:12].upper()
        masked = phone[:3] + "****" + phone[-4:]
        user = UserDAO.create({
            "user_id": user_id,
            "username": username,
            "password_hash": hash_password(password),
            "real_name": real_name or masked,
            "role": role,
            "status": "active",
            "phone_cipher": encrypt_phone(phone),
            "phone_hash": ph_hmac,
        })
        logger.info(f"手机号注册成功：{masked} -> {username}")
        data = user.to_dict()
        data["phone_masked"] = masked
        return data

    @staticmethod
    def login(username: str = "", password: str = "", phone: str = "") -> dict:
        """登录：优先账号，其次手机号（双通道）。"""
        if not username and not phone:
            raise ValidationException("请输入登录账号或手机号")
        if username:
            user = UserDAO.get_by_username(username or "")
            if not user:
                raise AuthenticationException("账号不存在")
        else:
            user = UserDAO.get_by_phone_hash(phone_hash(str(phone).strip()))
            if not user:
                raise AuthenticationException("该手机号尚未注册")
        if user.status != "active":
            raise AuthenticationException("账号已被停用")
        if not verify_password(password or "", user.password_hash):
            raise AuthenticationException("密码错误")

        UserDAO.update_last_login(user.user_id)
        access_token = create_access_token(user.user_id, user.username, user.role)
        refresh_token = create_refresh_token(user.user_id, user.username, user.role)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_EXPIRE_SECONDS,
            "user": user.to_dict(),
        }

    @staticmethod
    def get_by_token(token: str) -> Optional[dict]:
        payload = decode_token(token)
        if not payload:
            return None
        user = UserDAO.get_by_user_id(payload.get("sub"))
        if not user or user.status != "active":
            return None
        return user.to_dict()

    @staticmethod
    def ensure_default_user() -> None:
        """首次启动时创建一个演示账号，方便本地登录调试。"""
        if not settings.INIT_DEMO_USER:
            return
        try:
            if UserDAO.get_by_username(DEFAULT_USERNAME):
                return
            UserDAO.create({
                "user_id": "UDEMO0000001",
                "username": DEFAULT_USERNAME,
                "password_hash": hash_password(DEFAULT_PASSWORD),
                "real_name": "李女士",
                "sex": "女",
                "age": 38,
                "role": "user",
                "status": "active",
            })
            logger.info(f"已初始化演示账号：{DEFAULT_USERNAME} / {DEFAULT_PASSWORD}")
        except Exception as e:
            logger.warning(f"初始化演示账号失败（可忽略）：{e}")
