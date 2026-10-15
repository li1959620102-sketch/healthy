import json
# Jieba中文分词
import logging
import os
import tempfile
from datetime import datetime
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse

from langchain_community.document_loaders import UnstructuredMarkdownLoader
# Milvus向量数据库
# from langchain_community.vectorstores import Milvus
from langchain_milvus import Milvus
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from pymilvus import connections, utility
from app.designer.strategy.chunk.chunking_strategy import ChunkingStrategy
from app.designer.strategy.chunk.jieba_chunking_strategy import JiebaChunkingStrategy
from app.designer.strategy.chunk.minio_metadata_strategy import MinIOMetadataStrategy
from app.designer.strategy.chunk.metadata_strategy import MetadataStrategy
from app.exceptions.rag_exception import RAGException
from app.utils.minio_client import MinioClient
from app.config import settings


# 初始化日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ChunkService")


# 主服务类
class ChunkService:
    def __init__(
        self,
        # Ollama配置
        ollama_base_url: str = settings.OLLAMA_BASE_URL,
        embedding_model: str = settings.EMBEDDING_MODEL,
        # Milvus配置
        milvus_host: str = settings.MILVUS_HOST,
        milvus_port: str = settings.MILVUS_PORT,
        milvus_collection: str = settings.MILVUS_DEFAULT_COLLECTION,
        # MinIO配置
        minio_bucket: str = settings.KB_BUCKET_NAME,

        # 策略配置
        chunking_strategy: Optional[ChunkingStrategy] = None,
        metadata_strategy: Optional[MetadataStrategy] = None
    ):
        # 初始化组件
        self.embeddings = self._init_embeddings(ollama_base_url, embedding_model)
        self.minio_client = MinioClient(minio_bucket)

        # Milvus连接参数
        milvus_connection = {
            "host": milvus_host,
            "port": milvus_port
        }
        self.vector_store = self._init_vector_store(milvus_connection, milvus_collection)

        # 设置策略（使用默认策略如果没有提供）
        self.chunking_strategy = chunking_strategy or JiebaChunkingStrategy()
        self.metadata_strategy = metadata_strategy or MinIOMetadataStrategy()

        logger.info("MinIO服务初始化完成")

    def _init_embeddings(self, base_url: str, model: str) -> OllamaEmbeddings:
        """初始化嵌入模型"""
        try:
            os.environ["OLLAMA_HOST"] = base_url
            embeddings = OllamaEmbeddings(model=model, base_url=base_url)
            # 验证嵌入模型
            test_emb = embeddings.embed_query("测试")
            if isinstance(test_emb, list) and len(test_emb) > 0 and isinstance(test_emb[0], float):
                logger.info(f"嵌入模型就绪：{model}（维度：{len(test_emb)}）")
                return embeddings
            raise RAGException(503, "嵌入模型返回无效结果")
        except Exception as e:
            raise RAGException(500, f"嵌入模型初始化失败：{str(e)}")

    def _init_vector_store(self, connection_args: Dict, collection_name: str) -> Milvus:
        """初始化Milvus向量存储"""
        try:
            # 连接Milvus
            connections.connect(**connection_args)

            # 【关键修改】检查集合是否存在，但不要删除
            if utility.has_collection(collection_name):
                logger.info(f"集合 '{collection_name}' 已存在，直接复用")
            else:
                logger.info(f"集合 '{collection_name}' 不存在，将创建新集合")

            # 创建新的集合
            vector_store = Milvus(
                embedding_function=self.embeddings,
                collection_name=collection_name,
                connection_args=connection_args,
                auto_id=True,
                drop_old=False  # 删除旧集合
            )

            logger.info(f"Milvus集合创建成功：{collection_name}")
            return vector_store
        except Exception as e:
            raise RAGException(500, f"Milvus初始化失败：{str(e)}")

    def _extract_object_name(self, minio_path: str, minio_metadata: Dict) -> str:
        """从MinIO元数据中提取对象名称（使用file_name）"""
        try:
            # 直接从元数据中获取文件名作为对象名称
            file_name = minio_metadata.get("file_name")
            if not file_name:
                # 如果元数据中没有文件名，则从URL中提取
                parsed_url = urlparse(minio_path)
                path = parsed_url.path.lstrip('/')

                # 分割路径部分
                path_parts = path.split('/')

                # 如果路径以bucket名称开头，则移除bucket名称部分
                if path_parts and path_parts[0] == self.minio_client.bucket_name:
                    path_parts = path_parts[1:]

                # 获取文件名部分
                file_name = path_parts[-1] if path_parts else "unknown_file"

            logger.info(f"使用对象名称: {file_name}")
            return file_name
        except Exception as e:
            raise RAGException(500, f"提取对象名称失败：{str(e)}")

    def _download_from_minio(self, minio_path: str, minio_metadata: Dict) -> str:
        """从MinIO下载文件"""
        try:
            # 从MinIO元数据中提取对象名称
            object_name = self._extract_object_name(minio_path, minio_metadata)

            # 创建临时文件
            temp_dir = tempfile.mkdtemp()
            local_path = os.path.join(temp_dir, object_name)

            # 下载文件
            self.minio_client.download_file(object_name, local_path)

            logger.info(f"文件下载成功：{local_path}")
            return local_path
        except Exception as e:
            raise RAGException(500, f"从MinIO下载文件失败：{str(e)}")

    def _load_document(self, doc_path: str) -> List[Document]:
        """加载文档"""
        if not os.path.exists(doc_path):
            raise RAGException(404, f"文档不存在：{doc_path}")

        try:
            # 直接读取文本文件（不依赖unstructured和nltk）
            with open(doc_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 创建Document对象
            docs = [Document(page_content=content)]
            logger.info(f"文档加载成功：{doc_path}（字符数：{len(content)}）")
            return docs
        except UnicodeDecodeError:
            # 尝试GBK编码
            with open(doc_path, 'r', encoding='gbk') as f:
                content = f.read()
            docs = [Document(page_content=content)]
            logger.warning(f"用GBK编码加载文档：{doc_path}")
            return docs
        except Exception as e:
            raise RAGException(500, f"文档加载失败：{str(e)}")

    def process_minio_document(self, minio_metadata: Dict) -> Dict[str, Any]:
        """
        处理MinIO文档的完整流程

        Args:
            minio_metadata: 包含MinIO文档信息的字典，必须包含minio_path和file_id

        Returns:
            处理结果信息
        """
        try:
            # 验证必要参数
            if "minio_path" not in minio_metadata:
                raise RAGException(400, "minio_metadata中必须包含minio_path")
            if "file_id" not in minio_metadata:
                raise RAGException(400, "minio_metadata中必须包含file_id")

            minio_path = minio_metadata["minio_path"]
            file_id = minio_metadata["file_id"]

            logger.info(f"开始处理MinIO文档：{minio_path}")

            # 1. 从MinIO下载文档
            local_path = self._download_from_minio(minio_path, minio_metadata)

            # 2. 加载文档
            docs = self._load_document(local_path)

            # 3. 使用策略模式进行分片
            chunks = self.chunking_strategy.split_document(docs, local_path, minio_metadata)

            # 3.1 提取文件名
            # file_name = self._extract_object_name(minio_path, minio_metadata)
            # minio_metadata["file_name"] = file_name
            minio_metadata["file_name"] = self._extract_object_name(minio_path, minio_metadata)

            # 4. 使用策略模式处理元数据
            processed_chunks = self.metadata_strategy.process_metadata(chunks, minio_metadata)

            # 5. 添加到向量数据库
            chunk_ids = self.vector_store.add_documents(processed_chunks)

            # 6. 清理临时文件
            os.remove(local_path)
            os.rmdir(os.path.dirname(local_path))

            return {
                "file_id": file_id,
                "file_name": minio_metadata.get("file_name", "unknown"),
                "minio_path": minio_path,
                "chunks_processed": len(processed_chunks),
                "chunk_ids": chunk_ids,
                "process_time": datetime.now().isoformat()
            }

        except Exception as e:
            # 确保临时文件被清理
            if 'local_path' in locals() and os.path.exists(local_path):
                os.remove(local_path)
                if os.path.exists(os.path.dirname(local_path)):
                    os.rmdir(os.path.dirname(local_path))
            raise RAGException(500, f"MinIO文档处理失败：{str(e)}")