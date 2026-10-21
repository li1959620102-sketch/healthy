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
    email_hash,
    encrypt_email,
    encrypt_phone,
    hash_password,
    mask_email,
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

    # ---------------- 邮箱注册（邮箱验证码，FR-U-01） ---------------- #
    @staticmethod
    def register_by_email(email: str, verify_code: str, password: str,
                          real_name: str = "", role: str = "user") -> dict:
        """邮箱 + 邮箱验证码注册：格式归一化 → 验证码校验 → 邮箱查重 → 建号。

        与手机号通道同构（同一套发码/验码流程），差异只在被验证对象与落库字段。
        登录后续走「邮箱 + 密码」通道；username 仅作系统内部唯一标识。
        """
        from app.services.email_code_service import email_code_service

        em = rules.check_email(email)
        if not em.ok:
            raise ValidationException(em.message)
        if not verify_code:
            raise ValidationException("请输入邮箱验证码")
        pwd_rule = rules.check_password(password)
        if not pwd_rule.ok:
            raise ValidationException(pwd_rule.message)
        if role != "user":
            raise ValidationException("公开注册只能创建普通用户，专业角色需经后台审核授予")

        normalized = em.value                      # check_email 已做小写去空格归一化
        em_hmac = email_hash(normalized)

        # 先查重（避免无谓消耗一次性验证码），再校验验证码
        if UserDAO.get_by_email_hash(em_hmac):
            raise ConflictException("该邮箱已注册，请直接登录")
        email_code_service.verify_code(normalized, verify_code)

        username = "u" + uuid.uuid4().hex[:12]
        while UserDAO.get_by_username(username):
            username = "u" + uuid.uuid4().hex[:12]

        user_id = "U" + uuid.uuid4().hex[:12].upper()
        masked = mask_email(normalized)
        user = UserDAO.create({
            "user_id": user_id,
            "username": username,
            "password_hash": hash_password(password),
            "real_name": real_name or masked,
            "role": role,
            "status": "active",
            "email_cipher": encrypt_email(normalized),
            "email_hash": em_hmac,
        })
        logger.info(f"邮箱注册成功：{masked} -> {username}")
        data = user.to_dict()
        data["email_masked"] = masked
        return data

    @staticmethod
    def reset_password(identifier: str, verify_code: str, new_password: str) -> dict:
        """通过已验证的手机号或邮箱验证码重置密码。"""
        ident = (identifier or '').strip()
        rule = rules.check_password(new_password)
        if not rule.ok:
            raise ValidationException(rule.message)
        if not verify_code:
            raise ValidationException("请输入验证码")
        if '@' in ident:
            norm = rules.normalize_email(ident)
            if not rules.check_email(norm).ok:
                raise ValidationException("邮箱格式不正确")
            user = UserDAO.get_by_email_hash(email_hash(norm))
            if not user:
                raise AuthenticationException("该邮箱尚未注册")
            from app.services.email_code_service import email_code_service
            email_code_service.verify_code(norm, verify_code)
        else:
            phone_rule = rules.check_phone(ident)
            if not phone_rule.ok:
                raise ValidationException(phone_rule.message)
            user = UserDAO.get_by_phone_hash(phone_hash(ident))
            if not user:
                raise AuthenticationException("该手机号尚未注册")
            from app.services.sms_service import sms_service
            sms_service.verify_code(ident, verify_code)
        UserDAO.update_password(user.user_id, hash_password(new_password))
        logger.info("用户密码已通过验证渠道重置：%s", user.user_id)
        return {"user_id": user.user_id, "reset": True}

    @staticmethod
    def login(username: str = "", password: str = "", phone: str = "", email: str = "") -> dict:
        """登录：三通道 —— 账号 / 手机号 / 邮箱（按此优先级取第一个非空值）。

        邮箱同样按「归一化 → HMAC → 等值检索」，与手机号一致，不存明文。
        """
        if not username and not phone and not email:
            raise ValidationException("请输入登录账号、手机号或邮箱")
        if username:
            user = UserDAO.get_by_username(username or "")
            if not user:
                raise AuthenticationException("账号不存在")
        elif phone:
            user = UserDAO.get_by_phone_hash(phone_hash(str(phone).strip()))
            if not user:
                raise AuthenticationException("该手机号尚未注册")
        else:
            norm = rules.normalize_email(email)
            if not rules.check_email(norm).ok:
                raise ValidationException("邮箱格式不正确")
            user = UserDAO.get_by_email_hash(email_hash(norm))
            if not user:
                raise AuthenticationException("该邮箱尚未注册")
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
