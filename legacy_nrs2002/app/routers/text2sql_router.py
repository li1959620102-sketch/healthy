from fastapi import FastAPI
import logging
from datetime import datetime
from typing import Optional, List, Dict
import asyncio

import json

from fastapi import status, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from app.routers.base_router import BaseRouter
from app.schemas.common import ApiResponse
from app.config import settings
from app.utils.vanna_instance import vn

logger = logging.getLogger(__name__)


# 数据模型
class QueryRequest(BaseModel):
    question: str
    user_id: Optional[str] = None
    limit: Optional[int] = 10
    auto_execute: Optional[bool] = True


class QueryResponse(BaseModel):
    success: bool
    question: str
    generated_sql: Optional[str] = None
    result: Optional[List[Dict]] = None
    error: Optional[str] = None
    execution_time: float


class HealthResponse(BaseModel):
    status: str
    version: str
    database_connected: bool
    model_loaded: bool


class Text2SqlRouter(BaseRouter):
    def __init__(self):
        logger.info("Initializing Text2SqlRouter")
        super().__init__()
        self.router = self._register_routes()
        # 移除异步初始化代码
        self._initialized = False

    async def _async_startup(self):
        """异步初始化方法"""
        # 原来的异步初始化代码
        print("Initializing Text2SqlRouter")
        # 你的异步初始化逻辑...
        self._initialized = True

    def register_startup_event(self, app: FastAPI):
        """注册启动事件"""

        @app.on_event("startup")
        async def startup_event():
            await self._async_startup()

    def _register_routes(self):
        self.router.post(
            "/query",
            response_model=QueryResponse,
            status_code=status.HTTP_200_OK,
            summary="查询问题",
            description="查询健康",
            tags=["营养风险测评记录"]
        )(self.natural_language_query)

        self.router.post(
            "/train",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="训练数据",
            description="训练数据",
            tags=["营养风险测评记录"]
        )(self.train_model)

        self.router.post(
            "/train_upload",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="上传训练数据",
            description="上传 JSON 文件进行模型训练",
            tags=["营养风险测评记录"]
        )(self.train_upload)

        self.router.get(
            "/health",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="查询健康",
            description="查询健康",
            tags=["营养风险测评记录"]
        )(self.health_check)

        self.router.get(
            "/ask",
            response_model=QueryResponse,
            status_code=status.HTTP_200_OK,
            summary="问题",
            description="查询健康",
            tags=["营养风险测评记录"]
        )(self.ask_question)

        self.router.get(
            "/tables",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="查询健康",
            description="查询健康",
            tags=["营养风险测评记录"]
        )(self.get_table_info)

        return self.router

    async def health_check(self):
        """健康检查端点"""
        try:
            db_connected = vn.connect_to_mysql()
            return ApiResponse(
                status=200,
                message="服务运行正常",
                data={
                    "status": "healthy",
                    "version": "1.0.0",
                    "database_connected": db_connected,
                    "model_loaded": True
                }
            )
        except Exception as e:
            return ApiResponse(
                status=500,
                message=f"健康检查失败: {str(e)}",
                data={}
            )

    async def natural_language_query(self, request: QueryRequest):
        """自然语言查询接口"""
        start_time = datetime.now()

        try:
            logger.info(f"收到查询请求: {request.question}")

            # 生成SQL
            generated_sql = vn.generate_sql(request.question)
            logger.info(f"生成的SQL: {generated_sql}")

            result = None
            if request.auto_execute and generated_sql:
                # 执行SQL查询
                result = vn.execute_sql(generated_sql)
                logger.info(f"查询结果记录数: {len(result) if result else 0}")

            execution_time = (datetime.now() - start_time).total_seconds()

            return QueryResponse(
                success=True,
                question=request.question,
                generated_sql=generated_sql,
                result=result,
                execution_time=execution_time
            )

        except Exception as e:
            logger.error(f"查询处理失败: {str(e)}")
            execution_time = (datetime.now() - start_time).total_seconds()

            return QueryResponse(
                success=False,
                question=request.question,
                error=str(e),
                execution_time=execution_time
            )

    async def ask_question(self,
                           question: str = Query(..., description="自然语言问题"),
                           limit: int = Query(10, description="结果限制条数"),
                           user_id: Optional[str] = Query(None, description="用户ID过滤")
                           ):
        """GET方式的简化查询接口"""
        request = QueryRequest(
            question=question,
            user_id=user_id,
            limit=limit,
            auto_execute=True
        )
        return await self.natural_language_query(request)

    async def train_model(self):
        """手动触发模型训练"""
        try:
            # 在后台线程中执行训练
            await asyncio.get_event_loop().run_in_executor(
                None, vn.train_from_config
            )
            return ApiResponse(code=0, message="模型训练完成", data={})
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"训练失败: {str(e)}")

    async def train_upload(self, file: UploadFile = File(..., description="训练数据 JSON 文件")):
        """上传 JSON 文件进行模型训练"""
        # 校验文件类型
        if not file.filename.endswith('.json'):
            raise HTTPException(
                status_code=400,
                detail="仅支持 .json 格式的文件"
            )

        try:
            # 读取并解析 JSON
            content = await file.read()
            data = json.loads(content.decode('utf-8'))

            # 在后台线程中执行训练
            result = await asyncio.get_event_loop().run_in_executor(
                None, vn.train_from_data, data
            )

            return ApiResponse(
                status=200,
                message=result.get("message", "训练完成"),
                data=result
            )
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"JSON 解析失败: {str(e)}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"训练失败: {str(e)}"
            )

    async def get_table_info(self):
        """获取表结构信息"""
        try:
            if not vn.connect_to_mysql():
                raise HTTPException(status_code=500, detail="数据库连接失败")

            sql = """
            SELECT 
                TABLE_NAME,
                COLUMN_NAME,
                DATA_TYPE,
                COLUMN_COMMENT
            FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'user_health_risk_assessment'
            ORDER BY ORDINAL_POSITION
            """

            result = vn.execute_sql(sql)
            return ApiResponse(code=0, data=result, message="success")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取表结构失败: {str(e)}")