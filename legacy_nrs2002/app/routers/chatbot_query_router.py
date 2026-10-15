import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import status, Depends
from pydantic import BaseModel

from app.exceptions.base_api_exception import (
    NotFoundException,
    DatabaseException,
    ValidationException
)
from app.models.mysql.knowledge_file import KnowledgeFile
from app.routers.base_router import BaseRouter
from app.schemas.common import ApiResponse  # 通用响应模型
from app.services.chunk_service import ChunkService
from app.services.rag_query_service import RAGQueryService  # 确保该服务已修复Milvus问题
from app.config import settings
# 初始化日志
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# --------------------------
# 1. 配置常量（与业务对齐）
# --------------------------
RAG_CONFIG = {
    "milvus_uri": settings.MILVUS_URI,
    "milvus_token": None,
    "embedding_model": settings.EMBEDDING_MODEL,
    "llm_model": settings.LLM_MODEL,
    "ollama_base_url": settings.OLLAMA_BASE_URL,
    "vector_dim": 1024,  # bge-m3默认维度1024
    "default_top_k": 2  # 检索最相关的2个规则分片
}


# --------------------------
# 2. 输入输出模型（Pydantic严格校验）
# --------------------------
class UserQueryRequest(BaseModel):
    """用户查询请求模型：仅保留必要参数，补充默认值"""
    question: str  # 必须：用户问题（如“BMI19.2，2型糖尿病，近3月体重降4.6%，65岁”）
    file_id: str  # 必须：知识库文件ID（关联Milvus中的分片数据）
    collection_name: str  # 必须：Milvus集合名（如“yycp”）
    top_k: int = RAG_CONFIG["default_top_k"]  # 可选：检索分片数，默认2
    # 以下参数若暂未使用，可标记为Optional，避免强制传参
    provider_text_id: Optional[str] = None
    provider_embedding_id: Optional[str] = None
    max_distance: Optional[float] = None

    class Config:
        json_schema_extra = {
            "example": {
                "question": "患者女性，65岁，BMI 19.2，2型糖尿病稳定期，近3个月体重从65kg降至62kg（下降4.6%），无进食困难",
                "file_id": "ee481c40-6a37-4e78-b8e3-fe015e64ac81",
                "collection_name": "nrs2002",
                "top_k": 2
            }
        }

# --------------------------
# 3. RAG服务单例依赖（避免重复初始化）
# --------------------------
def get_rag_service() -> RAGQueryService:
    """依赖注入：全局单例RAG服务，避免每次请求重复初始化"""
    try:
        return RAGQueryService(
            # milvus_uri=RAG_CONFIG["milvus_uri"],
            milvus_host=settings.MILVUS_HOST,
            milvus_port=settings.MILVUS_PORT,
            milvus_token=RAG_CONFIG["milvus_token"],
            embedding_model=RAG_CONFIG["embedding_model"],
            llm_model=RAG_CONFIG["llm_model"],
            ollama_base_url=RAG_CONFIG["ollama_base_url"],
            dim=RAG_CONFIG["vector_dim"]
        )
    except Exception as e:
        logger.error(f"RAG服务初始化失败：{str(e)}")
        raise DatabaseException(f"向量查询服务不可用：{str(e)}")


# --------------------------
# 4. 路由实现（遵循FastAPI规范）
# --------------------------
class ChatBotQueryRouter(BaseRouter):
    """NRS2002评分查询路由：修复路由注册和协程逻辑"""

    def __init__(self):
        logger.info("Initializing ChatBotQueryRouter")
        super().__init__()
        self.chunk_service = ChunkService()  # 复用分片服务
        self.router = self._register_routes()  # 注册路由

    def _register_routes(self):
        """修复：路由注册时传递可调用对象（不加括号）"""
        # 注册NRS2002查询接口
        self.router.post(
            "/systemRagQuery",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="查询NRS2002营养风险评分",
            description="接收用户问题，通过Milvus检索NRS2002规则，调用Ollama生成评分（含依据）",
            tags=["营养风险筛查（NRS2002）"]
        )(self.system_rag_query)  # 关键：此处不加()，传递函数本身（可调用对象）

        return self.router


    async def system_rag_query(
            self,
            request: UserQueryRequest,
            rag_service: RAGQueryService = Depends(get_rag_service)  # 依赖注入RAG服务
    ) -> ApiResponse:
        """
        核心接口逻辑：
        1. 校验请求参数和文件状态
        2. 调用RAG服务查询评分
        3. 整理结果并返回
        """
        try:
            # 步骤1：基础校验
            if not request.question.strip():
                raise ValidationException("查询问题不能为空")

            # # 步骤2：校验文件状态（确保文件可查询）
            # self._validate_file_status(request.file_id)
            # logger.info(f"开始RAG查询：file_id={request.file_id}，question={request.question[:50]}...")

            # 步骤3：调用RAG服务查询（关键：使用请求参数，而非硬编码测试用例）
            rag_result = rag_service.query_score(
                user_question=request.question,
                file_id=request.file_id,
                collection_name=request.collection_name# 按file_id过滤Milvus中的分片
            )

            # 步骤4：整理响应数据（适配业务输出格式）
            response_data = {
                "score_info": rag_result["score_result"],  # 评分结果（含依据）
                "source_basis": rag_result["source_basis"],  # 检索到的原始规则片段（用于验证）
                "query_params": {
                    "file_id": request.file_id,
                    "collection_name": request.collection_name,
                    "top_k": request.top_k,
                    "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            }

            logger.info(f"RAG查询成功：file_id={request.file_id}，score={rag_result['score_result']['score']}")
            return ApiResponse(
                status=200,
                message="NRS2002评分查询成功",
                data=response_data
            )

        # 捕获业务异常（返回友好提示）
        except (NotFoundException, ValidationException) as e:
            logger.warning(f"查询业务异常：{str(e)}")
            return ApiResponse(status=e.code, message=str(e), data={})
        # 捕获数据库/服务异常
        except DatabaseException as e:
            logger.error(f"查询数据库异常：{str(e)}")
            return ApiResponse(status=500, message=f"服务异常：{str(e)}", data={})
        # 捕获其他未知异常
        except Exception as e:
            logger.error(f"查询未知异常：{str(e)}", exc_info=True)
            return ApiResponse(status=500, message="服务器内部错误", data={})