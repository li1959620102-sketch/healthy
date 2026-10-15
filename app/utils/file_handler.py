import logging
import os
import re
import uuid
from datetime import timedelta

from fastapi import UploadFile

from app.schemas.common import ApiResponse
from app.utils.minio_client import get_minio
from app.config import settings

logger = logging.getLogger(__name__)


def _get_minio_client():
    """按需返回 MinIO 单例。"""
    return get_minio()


class UploadFile:
    def __init__(self):
        logger.info("Uploading file")

    def upload_file(self,request,bucket_name):
        """处理Apifox上传的文件并保存到MinIO"""
        # 验证请求方法和文件
        if 'file' not in request.FILES:
            return ApiResponse(
                status=400,
                message= '未提供文件'
            )

        uploaded_file = request.FILES['file']

        try:
            # 1. 净化文件名（防止路径注入和特殊字符）
            safe_filename = re.sub(r'[^\w\-.]', '_', uploaded_file.name)

            # 2. 生成唯一文件名（避免冲突）
            file_base, file_ext = os.path.splitext(safe_filename)
            unique_suffix = uuid.uuid4().hex[:8]
            minio_filename = f"{file_base}_{unique_suffix}{file_ext}"
            logger.info(f"file_base,file_ext,minio_filename,{file_base,file_ext,minio_filename},unique_suffix:{unique_suffix}")
            # 3. 上传到MinIO
            stored_name = _get_minio_client().upload_file_for_bucket(
                file_object=uploaded_file,
                original_name=safe_filename,
                bucket_name=bucket_name,
                minio_object_name=minio_filename
            )

            # 4. 生成访问URL（有效期7天）
            file_url = _get_minio_client().get_presigned_url(
                object_name=stored_name,
                expires_in=timedelta(7)
                # ,response_headers = {
                #     'response-content-disposition': 'inline'
                #     }
            )

            # 5. 返回结果
            return {
                'original_filename': uploaded_file.name,
                'stored_filename': stored_name,
                'file_url': file_url,
                'file_size': uploaded_file.size,
                'content_type': uploaded_file.content_type or 'application/octet-stream'
            }

        except Exception as e:
            logger.error(f"文件上传失败: {str(e)}", exc_info=True)
            return None

        # 关键修改：将函数改为为async

    async def upload_file_form(self, uploaded_file: UploadFile, bucket_name: str):
        """
        异步处理FastAPI UploadFile，上传到MinIO
        :param uploaded_file: FastAPI UploadFile对象（前端上传的文件）
        :param bucket_name: MinIO目标桶名
        :return: 上传传结果（含存储名、URL等）或None
        """
        if not uploaded_file or not uploaded_file.filename:
            logger.error("未提供供有效文件")
            return ApiResponse(code=400, message="未提供有效文件")
        declared_size = getattr(uploaded_file, "size", None)
        if declared_size is not None and declared_size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
            logger.warning("拒绝超限文件：%s bytes", declared_size)
            return None

        try:
            # 1. 净化文件名（保留中文、数字、下划线、短横线和点，避免路径注入）
            safe_filename = re.sub(r'[^\w\-.\u4e00-\u9fa5]', '_', uploaded_file.filename)
            file_base, file_ext = os.path.splitext(safe_filename)
            # 生成唯一一文件名（避免MinIO中文件重名）
            unique_suffix = uuid.uuid4().hex[:8]
            minio_filename = f"{file_base}_{unique_suffix}{file_ext}"
            logger.info(f"生成MinIO唯一文件名：{minio_filename}")

            # 2. 关键步骤：异步读取文件内容（必须await，否则返回coroutine）
            # 读取UploadFile的字节流（而非直接传递UploadFile对象给MinIO）
            file_content = await uploaded_file.read()  # 关键：await获取实际字节流
            if not file_content:
                logger.error(f"文件内容为空：{uploaded_file.filename}")
                return None
            if len(file_content) > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
                logger.warning("拒绝超限文件：%s bytes", len(file_content))
                return None

            # 3. 调用MinIO客户端的同步方法（传入字节流，而非UploadFile对象）
            # 注意：此时传给upload_file_for_bucket的是file_content（字节流），不是uploaded_file
            stored_name = _get_minio_client().upload_file_for_bucket(
                file_object=file_content,  # 传入递字节流，而非UploadFile
                original_name=safe_filename,
                bucket_name=bucket_name,
                minio_object_name=minio_filename
            )

            # 4. 生成MinIO预签名URL（如果需要）
            # 注意：你的MinIOClient的get_presigned_url是同步的，直接调用即可
            file_url = _get_minio_client().get_presigned_url_for_bucket(
                object_name=stored_name,
                expires_in=timedelta(days=7),
                bucket_name=bucket_name
            )

            # 5. 返回上传结果
            return {
                'original_filename': uploaded_file.filename,
                'stored_filename': stored_name,
                'file_url': file_url,
                'file_size': len(file_content),  # 字节流可正常计算长度
                'content_type': uploaded_file.content_type or 'application/octet-stream'
            }

        except Exception as e:
            logger.error(f"文件上传失败: {str(e)}", exc_info=True)
            return None

    def download_file(self,bucket_name,object_name):
        try:
            # 从MinIO获取文件流
            response = _get_minio_client().get_object_bucket(bucket_name,object_name)
            content_bytes = response.read()
            response.close()
            response.release_conn()
            # 将字节流解码为字符串
            content = content_bytes.decode('utf-8')
            return content
        except Exception as e:
            logger.error(f"下载文件失败: {str(e)}", exc_info=True)
            return None
