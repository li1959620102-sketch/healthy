# [file name]: knowledge_file_dao.py
from datetime import datetime
from typing import Optional, Dict, Any, List
from app.models.mysql.knowledge_file import KnowledgeFile
import logging
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility

logger = logging.getLogger(__name__)


class KnowledgeFileDAO:
    def __init__(self, db_manager, milvus_host: str = None, milvus_port: str = None):
        self.db_manager = db_manager
        # 2026-09-12：不再硬编码地址——统一取 settings（.env 的 MILVUS_HOST/PORT），
        # 否则换机器/换实例时这里会静默连旧地址并让整个应用启动失败。
        from app.config import settings
        self.milvus_host = milvus_host or settings.MILVUS_HOST
        self.milvus_port = str(milvus_port or settings.MILVUS_PORT)
        self._connected = False
        self._init_milvus_connection()

    def _init_milvus_connection(self):
        """初始化 Milvus 连接；失败只告警不阻断启动（真正用到时再重连）。"""
        try:
            connections.connect(host=self.milvus_host, port=self.milvus_port)
            self._connected = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Milvus 连接失败（{self.milvus_host}:{self.milvus_port}）：{e}；使用时会重试")

    def _ensure_connection(self):
        """确保连接可用（惰性重连），供各 Milvus 操作前调用。"""
        if self._connected:
            return
        try:
            connections.connect(host=self.milvus_host, port=self.milvus_port)
            self._connected = True
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Milvus 不可用（{self.milvus_host}:{self.milvus_port}）：{e}") from e

    def create_file(self, file_data):
        """创建新文件记录"""
        session = self.db_manager.get_session()
        try:
            new_file = KnowledgeFile(**file_data)
            session.add(new_file)
            session.commit()
            session.refresh(new_file)
            session.expunge(new_file)
            return new_file
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def get_file_by_id(self, file_id):
        """根据ID获取文件"""
        session = self.db_manager.get_session()
        try:
            return session.query(KnowledgeFile).filter_by(id=file_id).first()
        finally:
            session.close()

    def get_file_by_name(self, file_name: str):
        """根据文件名获取文件记录"""
        session = self.db_manager.get_session()
        try:
            return session.query(KnowledgeFile).filter_by(file_name=file_name).first()
        finally:
            session.close()

    def update_file(self, file_id, update_data):
        """更新文件信息"""
        session = self.db_manager.get_session()
        try:
            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if file:
                for key, value in update_data.items():
                    if hasattr(file, key):
                        setattr(file, key, value)
                file.updated_time = datetime.now()
                session.commit()
                return file
            return None
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def delete_file(self, file_id):
        """删除文件记录"""
        session = self.db_manager.get_session()
        try:
            file = session.query(KnowledgeFile).filter_by(id=file_id).first()
            if file:
                session.delete(file)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def list_files(self, filters=None, page=1, per_page=20):
        """列出文件，支持过滤和分页"""
        session = self.db_manager.get_session()
        try:
            query = session.query(KnowledgeFile)

            if filters:
                for key, value in filters.items():
                    if hasattr(KnowledgeFile, key):
                        query = query.filter(getattr(KnowledgeFile, key) == value)

            total = query.count()
            items = query.order_by(
                KnowledgeFile.created_time.desc()
            ).offset((page - 1) * per_page).limit(per_page).all()

            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page
            }
        finally:
            session.close()

    # ========== Milvus 操作 ==========

    def _get_collection(self, collection_name: str) -> Collection:
        """确保集合存在并返回集合对象"""
        if utility.has_collection(collection_name):
            return Collection(collection_name)
        else:
            # 创建集合（字段与 chunk_service.py 完全一致）
            fields = [
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="pk", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
                FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="minio_path", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="chunk_length", dtype=DataType.INT64),
                FieldSchema(name="business_scene", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                FieldSchema(name="total_chunks", dtype=DataType.INT64),
            ]
            schema = CollectionSchema(fields, description="知识库集合")
            collection = Collection(collection_name, schema)

            index_params = {
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            collection.create_index("vector", index_params)
            collection.load()
            return collection

    def insert_records(self, collection_name: str, records: List[Dict[str, Any]]):
        """批量插入记录到Milvus。

        ⚠️ 按**字段名**对齐，不再按位置硬塞（2026-10-16 修）。

        原实现写死 11 个位置列表，等于假设"集合结构就是我这张表"。
        但 `ai_health_kb_v1`（`scripts/build_knowledge_base.py` 用 LangChain 建的）
        只有 text/vector/file_name/source/category 5 个非自增字段，于是插入直接报
        `The data doesn't match with schema fields, expect 5 list, got 11`。

        比"报错"更该防的是**错位**：若某天字段数凑巧相等而顺序不同，
        位置式写入会把 `source` 写成 `minio_path`——库里有数据、不报错，
        但引用溯源全错，属于最难发现的一类脏数据。按名取字段可以从根上排除。
        """
        self._ensure_connection()

        if not records:
            return

        collection = self._get_collection(collection_name)
        fields = [f for f in collection.schema.fields if not getattr(f, "auto_id", False)]

        payload = []
        for f in fields:
            payload.append([self._coerce(f, r) for r in records])
        collection.insert(payload)
        collection.flush()

    @staticmethod
    def _coerce(field, record: Dict[str, Any]):
        """按目标字段类型把记录值转成 Milvus 可接受的形式。"""
        from pymilvus import DataType

        raw = record.get(field.name)
        if field.dtype == DataType.FLOAT_VECTOR:
            return raw or []
        if field.dtype in (DataType.INT8, DataType.INT16, DataType.INT32, DataType.INT64):
            try:
                return int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                return 0
        if field.dtype in (DataType.FLOAT, DataType.DOUBLE):
            try:
                return float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                return 0.0
        if field.dtype == DataType.BOOL:
            return bool(raw)
        # 字符串类：目标集合缺这个字段时留空，不报错、不猜值
        return str(raw) if raw is not None else ""

    def query_by_category(self, collection_name: str, category: Optional[str] = None,
                          file_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """通过标量检索查询记录"""
        self._ensure_connection()
        if not utility.has_collection(collection_name):
            return []

        collection = Collection(collection_name)
        expr_parts = []
        if category:
            expr_parts.append(f'category == "{category}"')
        if file_name:
            expr_parts.append(f'file_name == "{file_name}"')
        expr = " and ".join(expr_parts) if expr_parts else "id != ''"

        results = collection.query(
            expr=expr,
            output_fields=["pk", "file_id", "file_name", "chunk_text", "chunk_id", "created_at"]
        )
        return results

    def semantic_search(self, collection_name: str, query_vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """语义检索"""
        self._ensure_connection()
        if not utility.has_collection(collection_name):
            return []

        collection = Collection(collection_name)
        collection.load()

        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 10}
        }

        results = collection.search(
            data=[query_vector],
            anns_field="vector",
            param=search_params,
            limit=top_k,
            output_fields=["pk", "file_id", "file_name", "chunk_text", "chunk_id", "created_at"]
        )

        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "pk": hit.id,
                    "score": hit.distance,
                    "file_id": hit.entity.get("file_id", ""),
                    "file_name": hit.entity.get("file_name", ""),
                    "chunk_text": hit.entity.get("chunk_text", ""),
                    "chunk_id": hit.entity.get("chunk_id", ""),
                    "created_at": hit.entity.get("created_at", "")
                })
        return formatted_results

    def delete_by_file(self, collection_name: str, file_name: str,
                       file_id: Optional[str] = None) -> int:
        """删除某文档在向量库中的**全部**分块，返回删除前命中的分块数。

        为什么必须存在（IT-K-03 / 评审 M5）：
        向量库里留着已删除文档的分块，会同时破坏两条红线——
        ① 管理员撤下一份过时/错误的材料后，AI 仍会引用它（IT-K-04）；
        ② 引用里带的 `doc_id` 指向 MySQL 中已不存在的记录，用户与审核方
           再也回溯不到出处（A7-4「来源必须可溯源」）。
        所以"删文档"必须是「向量 + 元数据」一起删，不能只删一半。

        字段取舍：`ai_health_kb_v1` 这类由 LangChain 建的集合**没有 file_id**，
        只有 file_name；而 DAO 自建的集合两者都有。
        因此优先用 file_id（精确），退化用 file_name（可用），
        并且只引用集合里**确实存在**的字段——否则 Milvus 会直接报字段不存在。
        """
        self._ensure_connection()
        if not utility.has_collection(collection_name):
            return 0

        collection = Collection(collection_name)
        names = {f.name for f in collection.schema.fields}
        expr = None
        if file_id and "file_id" in names:
            expr = f'file_id == "{file_id}"'
        elif "file_name" in names:
            expr = f'file_name == "{file_name}"'
        if not expr:
            logger.warning(f"集合 {collection_name} 无 file_id/file_name 字段，无法按文档定位分块")
            return 0

        hit = collection.query(expr=expr, output_fields=["count(*)"])
        n = int(hit[0]["count(*)"]) if hit else 0
        if n:
            collection.delete(expr)
            collection.flush()
        return n

    def count_by_category(self, collection_name: str, category: Optional[str] = None,
                          file_name: Optional[str] = None) -> int:
        """统计符合条件的记录数量"""
        self._ensure_connection()

        if not utility.has_collection(collection_name):
            return 0

        collection = Collection(collection_name)
        expr_parts = []
        if category:
            expr_parts.append(f'category == "{category}"')
        if file_name:
            expr_parts.append(f'file_name == "{file_name}"')
        expr = " and ".join(expr_parts) if expr_parts else "id != ''"

        result = collection.query(
            expr=expr,
            output_fields=["count(*)"]
        )
        return result[0]["count(*)"]