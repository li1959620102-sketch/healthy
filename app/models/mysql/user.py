# -*- coding: utf-8 -*-
"""平台用户表（登录用）。

依据《数据库设计说明书 V1.1》§3.1 扩展：手机号/邮箱密文与 HMAC、三方 openid、
最近活跃时间、软删除时间。注意：既有表新增列需执行 migrations/001_extend_legacy_tables.sql，
SQLAlchemy create_all 不会修改已存在的表。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import VARBINARY

from app.models.mysql.knowledge_file import Base


class SysUser(Base):
    """用户账号表

    角色 role：user（普通用户）/ dietitian（营养师）/ admin（管理员）
    接口层将 dietitian 映射为业务术语 nutritionist。
    """

    __tablename__ = "sys_user"
    __table_args__ = (
        Index("idx_role_status", "role", "status"),
        Index("idx_deleted", "deleted_at"),
        UniqueConstraint("phone_hash", name="uk_phone_hash"),
        UniqueConstraint("email_hash", name="uk_email_hash"),
        UniqueConstraint("wechat_openid", name="uk_wechat"),
        {"comment": "平台用户账号表"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(String(64), unique=True, nullable=False, comment="用户唯一标识")
    username = Column(String(64), unique=True, nullable=False, comment="登录账号")
    password_hash = Column(String(255), nullable=False, comment="密码密文")
    real_name = Column(String(100), comment="姓名")
    sex = Column(String(10), default="其他", comment="性别")
    age = Column(Integer, comment="年龄")
    role = Column(String(20), default="user", comment="角色 user/dietitian/admin")
    status = Column(String(20), default="active", comment="状态 active/disabled")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    last_login_at = Column(DateTime, comment="最近登录时间")

    # ---- V1.1 扩展字段（敏感信息加密存储） ----
    phone_cipher = Column(VARBINARY(512), comment="手机号密文 AES-256")
    phone_hash = Column(String(64), comment="手机号 HMAC-SHA256，用于唯一性与检索")
    email_cipher = Column(VARBINARY(512), comment="邮箱密文")
    email_hash = Column(String(64), comment="邮箱 HMAC")
    wechat_openid = Column(String(64), comment="微信 openid")
    alipay_user_id = Column(String(64), comment="支付宝 user_id")
    last_active_at = Column(DateTime, comment="最近活跃时间（30 分钟自动登出判断）")
    deleted_at = Column(DateTime, comment="软删除时间（注销后 15 日内完成删除/匿名化）")

    def to_dict(self):
        # 注意：不返回密文/哈希等敏感字段
        return {
            "user_id": self.user_id,
            "username": self.username,
            "real_name": self.real_name,
            "sex": self.sex,
            "age": self.age,
            "role": self.role,
            "status": self.status,
            "created_at": str(self.created_at) if self.created_at else "",
            "last_login_at": str(self.last_login_at) if self.last_login_at else "",
        }
