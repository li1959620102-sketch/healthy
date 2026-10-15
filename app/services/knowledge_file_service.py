# [file name]: knowledge_file_service.py
import uuid
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime
from app.models.mysql.knowledge_file import KnowledgeFile, DatabaseManager
from app.dao.knowledge_file_dao import KnowledgeFileDAO
from app.config import settings
from app.utils.file_handler import UploadFile as FileHandler
import requests

logger = logging.getLogger(__name__)
db_manager = DatabaseManager(settings.MYSQL_URI)
file_dao = KnowledgeFileDAO(db_manager)
file_handler = FileHandler()

class KnowledgeFileService:
    """知识文件服务类，封装知识文件相关的数据库操作和向量化"""
    def __init__(self):
        self.ollama_base_url = settings.OLLAMA_BASE_URL
        self.embedding_model = settings.EMBEDDING_MODEL

    def save_knowledge_file(self, model_param: Dict[str, Any]) -> KnowledgeFile:
        """保存新的知识文件"""
        logger.info("保存新知识文件")
        try:
            id = uuid.uuid4()
            model_param['id'] = str(id)
            knowledge_file = file_dao.create_file(model_param)
            logger.info(f"知识文件保存成功! ID: {knowledge_file.id}")
            return knowledge_file
        except Exception as e:
            logger.error(f"保存知识文件失败: {str(e)}", exc_info=True)
            raise

    def get_file_by_name(self, file_name: str):
        """根据文件名获取文件记录"""
        logger.info(f"根据文件名获取文件: {file_name}")
        try:
            return file_dao.get_file_by_name(file_name)
        except Exception as e:
            logger.error(f"根据文件名获取文件失败: {str(e)}", exc_info=True)
            raise

    def update_file_status(self, file_id: str, status: str):
        """更新文件状态"""
        logger.info(f"更新文件状态: {file_id} -> {status}")
        try:
            return file_dao.update_file(file_id, {"status": status})
        except Exception as e:
            logger.error(f"更新文件状态失败: {str(e)}", exc_info=True)
            raise

    def list_knowledge_files(self, page: int = 1, per_page: int = 10) -> Dict:
        """分页查询知识文件"""
        logger.info(f"分页查询知识文件: page={page}, per_page={per_page}")
        try:
            result = file_dao.list_files(page=page, per_page=per_page)
            return {
                "items": [file.to_dict() for file in result.get('items', [])],
                "total": result.get('total', 0),
                "page": result.get('page', 1),
                "per_page": result.get('per_page', 10),
                "pages": result.get('pages', 0)
            }
        except Exception as e:
            logger.error(f"分页查询知识文件失败: {str(e)}", exc_info=True)
            raise

    # ========== 向量化操作 ==========

    def _generate_embedding(self, text: str) -> List[float]:
        """使用Ollama生成文本向量（1024维）"""
        try:
            response = requests.post(
                url=f"{self.ollama_base_url}/api/embed",
                json={
                    "model": self.embedding_model,
                    "input": text
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings", [])
            if embeddings:
                return embeddings[0]
            else:
                raise ValueError("Ollama返回的向量为空")
        except Exception as e:
            logger.error(f"生成向量失败: {str(e)}", exc_info=True)
            raise RuntimeError("嵌入服务不可用，已终止入库，禁止使用随机向量污染知识库") from e

    def _split_content(self, content: str, split_keyword: str) -> List[Dict[str, str]]:
        """通用内容分割方法：按关键词将内容分割为多个部分"""
        parts = []
        lines = content.split('\n')
        current_part = []
        current_title = "规则"
        for line in lines:
            if split_keyword in line:
                if current_part:
                    parts.append({
                        "category": current_title,
                        "content": '\n'.join(current_part).strip()
                    })
                    current_part = []
                current_title = "示例"
            else:
                current_part.append(line)
        if current_part:
            parts.append({
                "category": current_title,
                "content": '\n'.join(current_part).strip()
            })
        return parts

    def process_file_from_minio(self, file_name: str, minio_path: str, collection_name: str, file_id: str,
                                split_keyword: str = "#") -> List[Dict[str, Any]]:
        """从MinIO读取文件并处理"""
        content = self._read_from_minio(minio_path)
        parts = self._split_content(content, split_keyword)
        records = []
        for idx, part in enumerate(parts, 1):
            vector = self._generate_embedding(part["content"])
            record = {
                "text": part["content"],  # 改为 text
                "vector": vector,
                "file_id": file_id,
                "file_name": file_name,
                "minio_path": minio_path,
                "chunk_id": f"{file_id}_chunk_{idx:04d}",
                "created_at": datetime.now().isoformat(),
                "chunk_length": len(part["content"]),
                "business_scene": "NRS2002",
                "chunk_index": idx,
                "total_chunks": len(parts)
            }
            records.append(record)
        file_dao.insert_records(collection_name, records)
        logger.info(f"成功处理文件 {file_name}，生成 {len(records)} 条记录")
        return records

    def _read_from_minio(self, minio_path: str) -> str:
        """从MinIO读取文件内容（复用 file_handler.py）"""
        try:
            if 'http://' in minio_path or 'https://' in minio_path:
                from urllib.parse import urlparse
                parsed = urlparse(minio_path)
                object_name = parsed.path.lstrip('/')
                if object_name.startswith(f'{settings.KB_BUCKET_NAME}/'):
                    object_name = object_name[len(f'{settings.KB_BUCKET_NAME}/'):]
            else:
                object_name = minio_path

            content = file_handler.download_file(settings.KB_BUCKET_NAME, object_name)
            return content
        except Exception as e:
            logger.error(f"从MinIO读取文件失败: {str(e)}", exc_info=True)
            raise

    def query_by_category(self, collection_name: str, category: Optional[str] = None, file_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """通过标量检索查询记录"""
        return file_dao.query_by_category(collection_name, category, file_name)

    def semantic_search(self, collection_name: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """语义检索"""
        query_vector = self._generate_embedding(query)
        return file_dao.semantic_search(collection_name, query_vector, top_k)

    def add_single_record(self, collection_name: str, category: str, file_name: str, file_id: str, chunk_text: str) -> Dict[str, Any]:
        """动态新增单条向量记录"""
        record = {
            "id": str(uuid.uuid4()),
            "vector": self._generate_embedding(chunk_text),
            "file_id": file_id,
            "chunk_id": f"{file_id}_chunk_{uuid.uuid4().hex[:8]}",
            "category": category,
            "file_name": file_name,
            "desc": f"{file_name} - {category}",
            "chunk_text": chunk_text,
            "created_at": datetime.now().isoformat()
        }
        file_dao.insert_records(collection_name, [record])
        logger.info(f"成功新增向量数据到集合 {collection_name}，ID: {record['id']}")
        return record

    def query_knowledge(
            self,
            collection_name: str,
            query: Optional[str] = None,
            category: Optional[str] = None,
            file_name: Optional[str] = None,
            page: int = 1,
            per_page: int = 10,
            top_k: int = 5
    ) -> Dict[str, Any]:
        """
        统一查询：支持分页、标量搜索、语义检索和组合搜索
        """
        logger.info(f"统一查询: collection={collection_name}, query={query}, category={category}")

        try:
            # 1. 如果传入 query，执行语义检索
            if query:
                query_vector = self._generate_embedding(query)
                results = file_dao.semantic_search(collection_name, query_vector, top_k)
                return {
                    "total": len(results),
                    "page": page,
                    "per_page": per_page,
                    "pages": 1 if results else 0,
                    "items": results
                }

            # 2. 如果传入标量参数，执行标量搜索 + 分页
            else:
                total = file_dao.count_by_category(collection_name, category, file_name)
                results = file_dao.query_by_category(collection_name, category, file_name)
                return {
                    "total": total,
                    "page": page,
                    "per_page": per_page,
                    "pages": (total + per_page - 1) // per_page if per_page > 0 else 0,
                    "items": results
                }
        except Exception as e:
            logger.error(f"统一查询失败: {str(e)}", exc_info=True)
            raise
