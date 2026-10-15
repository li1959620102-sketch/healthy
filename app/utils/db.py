# -*- coding: utf-8 -*-
"""数据库连接与建表工具。

统一复用 knowledge_file 模块中定义的 Base 与 DatabaseManager，
保证新表与已有表使用同一套元数据与连接方式。
"""
import logging

from app.config import settings
from app.models.mysql.knowledge_file import Base, DatabaseManager

logger = logging.getLogger(__name__)


def _build_manager():
    """创建数据库管理器，失败时返回 None 而不阻断服务启动。"""
    if not settings.MYSQL_URI:
        logger.error("未配置 MYSQL_URI，数据库相关接口将不可用")
        return None
    try:
        return DatabaseManager(settings.MYSQL_URI)
    except Exception as e:
        logger.error(f"MySQL 连接初始化失败：{e}")
        return None


db_manager = _build_manager()


def init_tables():
    """创建尚未存在的表（已存在的表不会被修改）。

    注意：旧 NRS2002 的 health_assessment 模型已迁移至 legacy_nrs2002/，
    此处仅保留平台用户模型；后续新表模型在对应模块落地后按需追加导入。
    """
    # 延迟导入：确保模型类已注册到 Base.metadata
    from app.models.mysql import user as _user  # noqa: F401
    from app.models.mysql import platform as _platform  # noqa: F401  (16 张新表)
    from app.models.mysql import knowledge_file as _kf  # noqa: F401

    if db_manager is None:
        logger.warning("跳过建表：数据库未连接")
        return False
    try:
        Base.metadata.create_all(db_manager.engine)
        logger.info("数据库表检查/创建完成")
        return True
    except Exception as e:
        logger.error(f"建表失败：{e}")
        return False


def get_session():
    """获取数据库会话，未连接时抛出异常。"""
    if db_manager is None:
        raise RuntimeError("数据库未连接，请检查 .env 中的 MYSQL_URI 配置")
    return db_manager.get_session()
