# -*- coding: utf-8 -*-
"""用户表数据访问层。"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.user import SysUser
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class UserDAO:
    @staticmethod
    def get_by_username(username: str) -> Optional[SysUser]:
        session = get_session()
        try:
            return session.query(SysUser).filter_by(username=username).first()
        finally:
            session.close()

    @staticmethod
    def get_by_user_id(user_id: str) -> Optional[SysUser]:
        session = get_session()
        try:
            return session.query(SysUser).filter_by(user_id=user_id).first()
        finally:
            session.close()

    @staticmethod
    def get_by_phone_hash(phone_hash: str) -> Optional[SysUser]:
        """按手机号 HMAC 检索（手机号登录 / 注册查重）。"""
        if not phone_hash:
            return None
        session = get_session()
        try:
            return session.query(SysUser).filter_by(phone_hash=phone_hash, deleted_at=None).first()
        finally:
            session.close()

    @staticmethod
    def create(user_data: dict) -> SysUser:
        session = get_session()
        try:
            user = SysUser(**user_data)
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def update_last_login(user_id: str) -> None:
        session = get_session()
        try:
            user = session.query(SysUser).filter_by(user_id=user_id).first()
            if user:
                user.last_login_at = datetime.now()
                session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            logger.warning(f"更新登录时间失败：{e}")
        finally:
            session.close()

    @staticmethod
    def count() -> int:
        session = get_session()
        try:
            return session.query(SysUser).count()
        finally:
            session.close()
