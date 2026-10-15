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
    created_time = Column(DateTime, comment='创建时间')

    # 更新时间
    updated_time = Column(DateTime, comment='更新时间')
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

    def __init__(self, id=None, version_no=None, collection_name=None,
                 file_name=None, file_type=None, minio_path=None,
                 chunk_strategy=None, description=None, status=None,
                 created_time=None, updated_time=None, created_by=None,
                 updated_by=None):
        """
        初始化知识文件对象
        Args:
            id: 主键ID，使用UUID格式
            version_no: 版本号
            collection_name: 知识库集合名称
            file_name: 文件名称
            file_type: 文件类型
            minio_path: MinIO存储路径
            chunk_strategy: 文档分块策略
            description: 文件描述
            status: 文件状态
            created_time: 创建时间
            updated_time: 更新时间
            created_by: 创建者ID
            updated_by: 更新者ID
        """
        self.id = id
        self.version_no = version_no
        self.collection_name = collection_name
        self.file_name = file_name
        self.file_type = file_type
        self.minio_path = minio_path
        self.chunk_strategy = chunk_strategy
        self.description = description
        self.status = status
        self.created_time = created_time or datetime.now()
        self.updated_time = updated_time or datetime.now()
        self.created_by = created_by
        self.updated_by = updated_by

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