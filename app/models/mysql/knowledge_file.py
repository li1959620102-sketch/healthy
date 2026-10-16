from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.mysql import VARCHAR

# 创建基类
Base = declarative_base()


class KnowledgeFile(Base):
    """
    知识文件存储模型类
    用于管理知识库中的文件元数据信息

    V1.1 扩展：category/source/doc_version/embed_status/embedded_at/chunk_count，
    支撑 K 组知识库接口。既有表新增列需执行 migrations/001_extend_legacy_tables.sql。
    """
    __tablename__ = 'knowledge_files'
    __table_args__ = (
        Index("idx_category", "category", "embed_status"),
        Index("idx_collection", "collection_name"),
        {'comment': '知识文件存储表，记录上传文件的元数据信息'}
    )
    # 主键ID，使用UUID格式
    id = Column(String(36), primary_key=True, comment='主键ID，使用UUID格式')
    # 版本号，用于乐观锁控制或版本管理
    version_no = Column(Integer, comment='版本号')
    # 知识库集合名称
    collection_name = Column(String(256), comment='知识库集合名称')
    # 文件名称
    file_name = Column(String(255), comment='文件名称')
    # 文件类型
    file_type = Column(String(20), comment='文件类型')
    # MinIO存储路径
    minio_path = Column(String(1000), comment='MinIO存储路径')
    # 文档分块策略
    chunk_strategy = Column(String(20), comment='文档分块策略')
    # 文件描述
    description = Column(String(255), comment='文件描述')
    # 文件状态：active(激活)/inactive(未激活)/archived(归档)等
    status = Column(String(20), comment='文件状态(active/inactive/archived等)')
    # 创建时间
    created_time = Column(DateTime, default=datetime.now, comment='创建时间')

    # 更新时间
    updated_time = Column(DateTime, default=datetime.now, onupdate=datetime.now,
                          comment='更新时间')
    # 创建者ID
    created_by = Column(Integer, comment='创建者ID')
    # 更新者ID
    updated_by = Column(Integer, comment='更新者ID')

    # ---- V1.1 扩展字段（知识库 K 组） ----
    category = Column(String(32), comment='food 食材 / disease 疾病 / guideline 指南')
    source = Column(String(200), comment='来源，如 中国居民膳食指南2022')
    doc_version = Column(String(32), comment='知识版本')
    embed_status = Column(String(20), default='pending', comment='向量化状态 pending/processing/done/failed')
    embedded_at = Column(DateTime, comment='向量化完成时间')
    chunk_count = Column(Integer, default=0, comment='分块数量')

    # ⛔ 不要再给这个模型手写 __init__（2026-10-16 移除）。
    #
    # 原先这里有一个逐参数手写的 __init__（id/version_no/.../updated_by 共 13 个参数），
    # V1.1 加列时**只加了 Column 定义、没同步 __init__ 的参数表**，于是
    # `KnowledgeFile(**file_data)` 里带了 category/source 就抛
    # `TypeError: __init__() got an unexpected keyword argument 'category'`——
    # 上传知识文档 `POST /knowledge/docs` 的**成功路径 100% 500**。
    # 之所以长期没被发现：既有用例 BV-28 只覆盖"扩展名不支持"（在校验处就提前返回了），
    # 谁也没真正上传成功过一次。
    #
    # 交由 SQLAlchemy 声明式基类自动生成 __init__（接受所有映射列）后，
    # 未来再加列不需要动这个文件；created_time/updated_time 的默认值改由
    # Column(default=...) 承担（语义也更准确：默认值属于列，不属于构造函数）。
    #
    # 若某天必须自定义构造行为，请用 `@reconstructor` 或静态工厂方法，
    # 不要重新引入白名单式 __init__——那是这类"加列即炸"的复发点。

    def __str__(self):
        """
        返回对象的字符串表示
        Returns:
            格式化的字符串，包含主要字段信息
        """
        return (
            "id:{}, file_name:{}, file_type:{}, chunk_strategy:{}, "
            "description:{}, minio_path:{}, status:{}"
        ).format(
            str(self.id), self.file_name, self.file_type, self.chunk_strategy,
            self.description, self.minio_path, self.status
        )

    def to_dict(self):
        """
        将对象转换为字典格式
        Returns:
            包含所有字段的字典
        """
        return {
            'id': str(self.id),
            'collection_name': self.collection_name,
            'file_name': self.file_name,
            'file_type': self.file_type,
            "minio_path": self.minio_path,
            "chunk_strategy": self.chunk_strategy,
            "description": self.description,
            "status": self.status,
            "category": self.category,
            "source": self.source,
            "doc_version": self.doc_version,
            "embed_status": self.embed_status,
            "embedded_at": str(self.embedded_at) if self.embedded_at else '',
            "chunk_count": self.chunk_count if self.chunk_count is not None else 0,
            "created_time": str(self.created_time) if self.created_time else '',
            "updated_time": str(self.updated_time) if self.updated_time else '',
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "version_no": self.version_no if self.version_no is not None else 0,
        }


# 数据库连接和会话管理
class DatabaseManager:
    """
    数据库管理类
    负责数据库连接、会话管理和表创建
    """

    def __init__(self, connection_string):
        """
        初始化数据库管理器

        Args:
            connection_string: 数据库连接字符串
        """
        self.engine = create_engine(connection_string)
        self.Session = sessionmaker(bind=self.engine)

    def create_tables(self):
        """
        创建所有表
        执行SQLAlchemy的元数据创建所有定义的表
        """
        Base.metadata.create_all(self.engine)

    def get_session(self):
        """
        获取数据库会话

        Returns:
            SQLAlchemy会话对象
        """
        return self.Session()