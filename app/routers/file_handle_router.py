# [file name]: file_handle_router.py
from typing import Optional

from fastapi import status, UploadFile, File, Form, Body, HTTPException, Query

from .base_router import BaseRouter
from ..services.knowledge_file_service import KnowledgeFileService
from ..schemas.common import ApiResponse
from ..utils.file_handler import UploadFile as FileUploader
from ..config import settings
from ..exceptions.base_api_exception import (
    DatabaseException,
    ValidationException,
    UnsupportedFileTypeException
)
import logging

logger = logging.getLogger(__name__)
knowledge_file_service = KnowledgeFileService()

KB_BUCKET_NAME = settings.KB_BUCKET_NAME
SUPPORTED_EXTENSIONS = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain',
    'sql': 'text/sql',
    'json': 'application/json',
    'md': 'text/markdown'
}

class FileHandleRouter(BaseRouter):
    def __init__(self):
        logger.info("Initializing FileHandleRouter")
        super().__init__()
        self.router = self._register_routes()

    def _register_routes(self):
        """注册所有路由处理方法并添加Swagger文档注解"""
        # 1. 上传文件 + 自动向量化
        self.router.post(
            "/uploadFile",
            response_model=ApiResponse,
            status_code=status.HTTP_201_CREATED,
            summary="上传知识文件并自动向量化",
            description="上传文档文件，自动解析、拆分、生成向量并存入Milvus",
            tags=["知识管理"]
        )(self.upload_knowledge_file)

        # 2. 统一查询（分页 + 标量 + 语义）
        self.router.post(
            "/search",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="统一查询接口",
            description="支持分页查询、标量搜索和语义检索的组合查询",
            tags=["知识管理"]
        )(self.search_knowledge)

        # 3. 新增向量数据
        self.router.post(
            "/addVectorData",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="新增向量数据",
            description="动态新增一条向量数据到Milvus集合中，并生成向量值",
            tags=["知识管理"]
        )(self.add_vector_data)

        return self.router

    async def upload_knowledge_file(
            self,
            file: UploadFile = File(..., description="待上传的知识文件（支持 pdf/docx/txt/xlsx）"),
            collection_name: str = Form(..., description="知识文件所属的集合名称（必填）"),
            split_keyword: str = Form("#", description="分割关键词，用于解析文件内容（可选）")
    ):
        """上传知识文件并自动完成向量化"""
        try:
            if not file:
                raise ValidationException("No file provided")

            file_extension = file.filename.split('.')[-1].lower() if file.filename else ''
            if file_extension not in SUPPORTED_EXTENSIONS:
                raise UnsupportedFileTypeException(f"Unsupported file type: {file_extension}")

            uploaded_file = FileUploader()
            logger.info(f"KB_BUCKET_NAME:{KB_BUCKET_NAME}")
            uploaded_object = await uploaded_file.upload_file_form(file, KB_BUCKET_NAME)

            if not uploaded_object:
                raise DatabaseException("Failed to upload file")

            knowledge_file_param = {
                "collection_name": collection_name,
                "file_name": uploaded_object.get('stored_filename'),
                "file_type": file_extension,
                "minio_path": uploaded_object.get('file_url'),
                "chunk_strategy": 'adaptive',
                "status": 'uploaded'
            }

            knowledge_file = knowledge_file_service.save_knowledge_file(knowledge_file_param)
            logger.info(f"Uploaded knowledge file: {knowledge_file.id}")

            vectorization_result = None
            try:
                minio_path = knowledge_file.minio_path
                vectorization_result = knowledge_file_service.process_file_from_minio(
                    file_name=knowledge_file.file_name,
                    minio_path=minio_path,
                    collection_name=collection_name,
                    file_id=knowledge_file.id,  # 新增：传递文件ID
                    split_keyword=split_keyword
                )
                knowledge_file_service.update_file_status(knowledge_file.id, 'processed')
            except Exception as vector_error:
                logger.error(f"向量化失败: {str(vector_error)}", exc_info=True)
                knowledge_file_service.update_file_status(knowledge_file.id, 'failed')

            return ApiResponse(
                status=200,
                message="File uploaded and vectorized successfully",
                data={
                    "file": knowledge_file.to_dict(),
                    "vectorization": vectorization_result
                }
            )
        except ValidationException as e:
            raise
        except UnsupportedFileTypeException as e:
            raise
        except Exception as e:
            logger.error(f"Error uploading knowledge file: {str(e)}", exc_info=True)
            raise DatabaseException("Failed to upload knowledge file") from e

    async def search_knowledge(
            self,
            collection_name: str = Query("nrs2002", description="集合名称"),
            query: Optional[str] = Query(None, description="语义检索查询（可选）"),
            category: Optional[str] = Query(None, description="标量搜索分类（可选）"),
            file_name: Optional[str] = Query(None, description="标量搜索文件名（可选）"),
            page: int = Query(1, description="分页页码"),
            per_page: int = Query(10, description="每页数量"),
            top_k: int = Query(5, description="语义检索返回数量")
    ):
        """统一查询接口：分页 + 标量搜索 + 语义检索"""
        try:
            results = knowledge_file_service.query_knowledge(
                collection_name=collection_name,
                query=query,
                category=category,
                file_name=file_name,
                page=page,
                per_page=per_page,
                top_k=top_k
            )
            return ApiResponse(
                status=200,
                message="查询成功",
                data=results
            )
        except Exception as e:
            logger.error(f"查询失败: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    async def add_vector_data(
            self,
            collection_name: str = Body(..., description="集合名称"),
            category: str = Body(..., description="分类（如：规则/示例）"),
            chunk_text: str = Body(..., description="新增的内容文本"),
            file_name: Optional[str] = Body(None, description="文件名（可选，默认从集合中获取）")
    ):
        """动态新增向量数据到Milvus集合"""
        try:
            if not file_name:
                existing_records = knowledge_file_service.query_by_category(collection_name)
                file_name = existing_records[0].get("file_name", collection_name) if existing_records else collection_name

            records = knowledge_file_service.add_single_record(
                collection_name=collection_name,
                category=category,
                file_name=file_name,
                chunk_text=chunk_text
            )
            return ApiResponse(
                status=200,
                message="向量数据新增成功",
                data=records
            )
        except Exception as e:
            logger.error(f"新增向量数据失败: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))