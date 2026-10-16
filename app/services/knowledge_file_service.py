# [file name]: knowledge_file_service.py
import uuid
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime
from app.models.mysql.knowledge_file import KnowledgeFile, DatabaseManager
from app.dao.knowledge_file_dao import KnowledgeFileDAO
from app.config import settings
from app.core import rules
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

    def get_file_by_id(self, file_id: str):
        """按 ID 取文件记录（供接口在写状态后返回**最新**快照）。

        为什么不能直接用上传时那个对象返回：`create_file` 里已经 `expunge`，
        后续 `update_embed_status` 在另一个 session 里改了库，内存对象不会同步——
        直接返回它会让调用方看到 `embed_status=pending`，与库里实际状态不一致。
        """
        return file_dao.get_file_by_id(file_id)

    def delete_vectors(self, collection_name: str, file_name: str,
                       file_id: Optional[str] = None) -> int:
        """清理文档在向量库中的分块（IT-K-03 删除联动）。"""
        return file_dao.delete_by_file(collection_name, file_name, file_id=file_id)

    def update_file_status(self, file_id: str, status: str):
        """更新文件的**业务状态**（active/inactive/archived），不用于向量化状态。

        ⚠️ 别再用它表达"向量化完成"（2026-10-16 修）：
        原上传链路写的是 `update_file_status(doc.id, "processed")`，而 `processed`
        既不是 `status` 的合法语义（active/inactive/archived），也不在 `embed_status`
        的取值域里（pending/processing/done/failed）——
        结果就是**向量化状态永远停在 pending**：管理员看不出文档到底有没有进向量库，
        IT-K-02 要求的 pending→processing→embedded 迁移在库里根本无从观察。
        向量化状态一律走 `update_embed_status`。
        """
        logger.info(f"更新文件状态: {file_id} -> {status}")
        try:
            return file_dao.update_file(file_id, {"status": status})
        except Exception as e:
            logger.error(f"更新文件状态失败: {str(e)}", exc_info=True)
            raise

    def update_embed_status(self, file_id: str, embed_status: str,
                            chunk_count: Optional[int] = None,
                            fail_reason: Optional[str] = None) -> Any:
        """回写**向量化状态**：pending / processing / done / failed。

        `chunk_count` 与 `embedded_at` 一并落库，管理员据此判断"这条文档进库了几片"；
        失败时把原因写进 `description`（复用既有列，不新增表结构），
        满足 IT-K-02「失败可见原因」——否则列表上只有一个孤零零的 failed。
        """
        patch: Dict[str, Any] = {"embed_status": embed_status}
        if chunk_count is not None:
            patch["chunk_count"] = chunk_count
        if embed_status == "done":
            patch["embedded_at"] = datetime.now()
        if fail_reason:
            patch["description"] = f"向量化失败：{fail_reason[:200]}"
        logger.info(f"更新向量化状态: {file_id} -> {embed_status}（chunks={chunk_count}）")
        try:
            return file_dao.update_file(file_id, patch)
        except Exception as e:
            logger.error(f"更新向量化状态失败: {str(e)}", exc_info=True)
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
                "per_page": result.get('per_page', per_page),
                "pages": result.get('pages', 0)
            }
        except Exception as e:
            logger.error(f"分页查询知识文件失败: {str(e)}", exc_info=True)
            raise

    # ========== 原文件访问 ==========

    def presign_original(self, file_name: str, expires_minutes: int = 10) -> str:
        """按需签发原文件临时访问地址（下载/预览时调用，不落库）。

        为什么库里不存 URL：预签名地址带签名且会过期。上传时把签名 URL 写进
        `minio_path`，管理员在后台看到的是一个 7 天后就点不开的死链，而且重试
        向量化时还得先从死链里解析对象名。库里只存对象名，签名每次现签——
        有效期按分钟计足够短，也不用考虑什么时候失效。
        """
        if not file_name:
            raise ValueError("缺少对象名，无法签发访问地址")
        from datetime import timedelta
        from app.utils.minio_client import get_minio
        return get_minio().get_presigned_url_for_bucket(
            object_name=file_name,
            expires_in=timedelta(minutes=expires_minutes),
            bucket_name=settings.KB_BUCKET_NAME,
        )

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
        """通用内容分割方法：按关键词将内容分割为多个部分。

        ⚠️ 必须丢弃空白片段（2026-10-16 修）。
        按 `#` 切分 Markdown 时，标题行本身会**关闭上一段并开启新的一段**，
        于是每个标题后面都会多出一个内容为空的片段。空串送进 Ollama `/api/embed`
        会返回 `{"embeddings": []}`（200，不是报错），随后 `_generate_embedding`
        判定"向量为空"抛错——表现为**任何带多个 `#` 标题的文档都向量化失败**，
        文档被整篇标记 failed，知识库一条都进不去。

        这个 bug 能长期存活，是因为它只在"上传成功"这条路上暴露，
        而既有用例只覆盖了"扩展名不支持"的提前返回分支。
        """
        parts = []
        lines = content.split('\n')
        current_part = []
        current_title = "规则"

        def _flush():
            text = '\n'.join(current_part).strip()
            if text:                       # ← 空片段直接丢弃，不进知识库
                parts.append({"category": current_title, "content": text})

        for line in lines:
            if split_keyword in line:
                _flush()
                current_part = []
                current_title = "示例"
            else:
                current_part.append(line)
        _flush()
        return parts

    def process_file_from_minio(self, file_name: str, minio_path: str, collection_name: str, file_id: str,
                                split_keyword: str = "#", category: str = "",
                                source: str = "") -> List[Dict[str, Any]]:
        """从MinIO读取文件并处理。

        `category` / `source` 会随分块一起写进向量库：检索侧
        （`ChatQueryService.retrieve` 读 `category`/`source`）靠它们回答
        "这条依据出自哪份材料、属于哪一类"，缺了就只剩一个文件名。
        目标集合没有这两个字段时由 DAO 按字段名自动跳过（不会报错）。
        """
        content = self._read_from_minio(minio_path)
        parts = self._split_content(content, split_keyword)
        if not parts:
            # 明确报错而不是"插入 0 条然后标记成功"：后者会让管理员看到一个
            # 显示"已完成"、实际知识库里什么都没有的文档——比报错更难排查。
            raise ValueError(f"文件无可向量化内容（按 {split_keyword!r} 切分后为空）：{file_name}")
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
                "business_scene": rules.KB_BUSINESS_SCENE,
                "chunk_index": idx,
                "total_chunks": len(parts),
                "category": category,
                "source": source,
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
