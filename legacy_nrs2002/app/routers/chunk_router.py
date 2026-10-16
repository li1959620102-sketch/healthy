import json
from typing import Dict, Any
from .base_router import BaseRouter
from ..config import settings
from ..designer.strategy.chunk.jieba_chunking_strategy import JiebaChunkingStrategy

from ..schemas.common import ApiResponse
from ..services.chunk_service import ChunkService
from app.exceptions.rag_exception import RAGException
import logging

logger = logging.getLogger("ChunkRouter")

# 配置
KB_BUCKET_NAME = settings.KB_BUCKET_NAME

# 支持的文件类型
class ChunkRouter(BaseRouter):
    def __init__(self):
        logger.info("Initializing ChunkRouter")
        super().__init__()
        self.router = self._register_routes()

    def _register_routes(self):
        """注册所有路由处理方法并添加Swagger文档注解"""

        # 解析文件
        self.router.post(
            "/parseFile",
            response_model=ApiResponse,
            summary="解析知识文件",
            description="对已上传的文件进行解析，提取文本内容并分割为可索引的片段",
            tags=["知识管理"]
        )(self.parse_file)
        return self.router


    async def parse_file(
            self,
            data: Dict[str, Any]
    ):
        """解析文件并生成 chunks"""
        try:
            # 初始化服务
            logger.info("=" * 50)
            logger.info("开始初始化ChunkService")

            chunk_service = ChunkService(
                # 使用Jieba分片策略
                chunking_strategy=JiebaChunkingStrategy(chunk_size=800, chunk_overlap=100)
            )

            logger.info("服务初始化成功")

            result = chunk_service.process_minio_document(data)
            logger.info(f"\n【MinIO文档处理结果】\n{result}")

            logger.info("\n" + "=" * 50)
            logger.info("MinIO文档处理完成")
            logger.info("=" * 50)
            return ApiResponse(
                status=200,
                message="text splitter successfully",
                data=result
            )
        except RAGException as e:
            logger.error(f"\n处理失败：[{e.code}] {e.message}", exc_info=True)
        except Exception as e:
            logger.error(f"\n未知错误：{str(e)}", exc_info=True)