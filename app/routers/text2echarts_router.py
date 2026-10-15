# routers/text2echarts_router.py
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import status
from pydantic import BaseModel
from app.routers.base_router import BaseRouter
from app.utils.vanna_engine import vn
from app.utils.string_utils import format_json
from app.adapter.workflow_client import execute_workflow
from app.utils.string_utils import extract_sql_after_escape_mark
import re
logger = logging.getLogger(__name__)


# 数据模型
class QueryRequest(BaseModel):
    question: str
    echarts_type: str
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


class Text2EchartsRouter(BaseRouter):
    def __init__(self):
        logger.info("Initializing Text2EchartsRouter")
        super().__init__()
        self.router = self._register_routes()
        # 移除异步初始化代码
        self._initialized = False



    def _register_routes(self):
        self.router.post(
            "/generate",
            response_model=QueryResponse,
            status_code=status.HTTP_200_OK,
            summary="查询问题",
            description="查询健康",
            tags=["营养风险测评记录"]
        )(self.run_workflow_proxy)

        return self.router

    async def run_workflow_proxy(self, request: QueryRequest):
        """自然语言查询接口"""
        start_time = datetime.now()

        try:
            logger.info(f"收到查询请求: {request.question}")
            # 生成SQL
            generated_sql = vn.generate_sql(request.question)
            logger.info(f"generated_sql: {generated_sql}")
            formatted_sql = extract_sql_after_escape_mark(generated_sql)
            logger.info(f"生成的SQL: {formatted_sql}")

            format_result = {}

            if request.auto_execute and formatted_sql:
                # 执行SQL查询
                result = vn.execute_sql(formatted_sql)
                logger.info(f"查询结果: {result},type:{type(result)}")
                format_result = format_json(result)
                logger.info(f"查询结果转换后: {format_result}")

            execution_time = (datetime.now() - start_time).total_seconds()
            new_params = f"{json.dumps(format_result, ensure_ascii=False, indent=2)} {request.echarts_type}"
            logger.info(f"生成结果：{new_params}")
            echarts_result = execute_workflow(new_params)
            format_output = [echarts_result]
            logger.info(f"生成echarts结果：{format_output}")
            return QueryResponse(
                success=True,
                question=request.question,
                generated_sql=generated_sql,
                result=format_output,
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