import logging
import os
import threading
from datetime import timedelta
from io import BytesIO
from typing import Optional

from minio import Minio
from minio.error import S3Error, InvalidResponseError

from app.config import settings

logger = logging.getLogger("MinioClient")


class MinioClient:
    def __init__(self,bucket_name: str = None):
        self.client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        if bucket_name is not None:
            self.bucket_name = bucket_name
        else:
            self.bucket_name = settings.MINIO_BUCKET
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
       """确保存储桶存在，不存在则创建"""
       return self._ensure_custom_bucket(self.bucket_name)

    def _ensure_custom_bucket(self,bucket_name):
        """确保存储桶存在，不存在则创建"""
        if not self.client.bucket_exists(bucket_name):
            try:
                self.client.make_bucket(bucket_name)
                logger.info(f"创建MinIO存储桶: {bucket_name}")
                return True
            except S3Error as e:
                logger.error(f"创建MinIO存储桶失败: {e}")
                raise

    def health(self) -> bool:
        """桶可达即视为健康（供启动校验与 /ready 探针调用）。"""
        try:
            return bool(self.client.bucket_exists(self.bucket_name))
        except Exception as e:
            logger.warning(f"MinIO 健康检查失败：{e}")
            return False

    def _get_content_type(self, file_name: str) -> str:
        """根据文件名获取MIME类型"""
        ext = os.path.splitext(file_name)[1].lower()
        content_types = {
            '.md': 'text/plain; charset=utf-8',  # 改成 text/plain
            '.txt': 'text/plain; charset=utf-8',
            '.csv': 'text/csv; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.zip': 'application/zip',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            }
        return content_types.get(ext, 'application/octet-stream')

    def upload_file(self, file_object, original_name: str, minio_object_name: Optional[str] = None) -> Optional[str]:
        """
        上传文件到MinIO
        :param file_object: 文件对象
        :param original_name: 原始文件名
        :param minio_object_name: MinIO中的存储名称
        :return: 存储的对象名称
        """
        try:
            # 处理文件内容
            if hasattr(file_object, 'read'):
                file_content = file_object.read()
                file_size = len(file_content)
                file_object.seek(0)  # 重置文件指针
            else:
                file_content = file_object
                file_size = len(file_content)


            # 2. 将字节流转换为可读取的类文件对象（关键修复）
            data_stream = BytesIO(file_content)

            # 3. 确定MinIO中的存储名称
            object_name = minio_object_name if minio_object_name else original_name

            # 上传到MinIO
            self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                data=data_stream,
                length=file_size,
                content_type=self._get_content_type(original_name)
            )

            logger.info(f"文件上传成功: {self.bucket_name}/{object_name}")
            return object_name
        except (S3Error, InvalidResponseError) as e:
            logger.error(f"MinIO上传失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"文件处理错误: {str(e)}")
            raise

    def upload_file_for_bucket(self, file_object, original_name: str, bucket_name:str,minio_object_name: Optional[str] = None) -> Optional[str]:
        """
        上传文件到MinIO
        :param file_object: 文件对象
        :param original_name: 原始文件名
        :param minio_object_name: MinIO中的存储名称
        :return: 存储的对象名称
        """
        try:
            # 处理文件内容
            if hasattr(file_object, 'read'):
                file_content = file_object.read()
                file_size = len(file_content)
                file_object.seek(0)  # 重置文件指针
            else:
                file_content = file_object
                file_size = len(file_content)


            # 2. 将字节流转换为可读取的类文件对象（关键修复）
            data_stream = BytesIO(file_content)

            # 3. 确定MinIO中的存储名称
            object_name = minio_object_name if minio_object_name else original_name
            self._ensure_custom_bucket(bucket_name)
            # 上传到MinIO
            self.client.put_object(
                bucket_name=bucket_name,
                object_name=object_name,
                data=data_stream,
                length=file_size,
                content_type=self._get_content_type(original_name)
            )

            logger.info(f"文件上传成功: {bucket_name}/{object_name}")
            return object_name
        except (S3Error, InvalidResponseError) as e:
            logger.error(f"MinIO上传失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"文件处理错误: {str(e)}")
            raise
    def get_presigned_url(self, object_name: str, expires_in: timedelta) -> str:
        """获取带签名的访问URL"""
        try:
            return self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=expires_in
            )
        except S3Error as e:
            logger.error(f"生成预签名URL失败: {str(e)}")
            raise e

    def get_presigned_url_for_bucket(self, object_name: str, expires_in: timedelta,bucket_name:str) -> str:
        """获取带签名的访问URL"""
        try:
            return self.client.presigned_get_object(
                bucket_name=bucket_name,
                object_name=object_name,
                expires=expires_in
            )
        except S3Error as e:
            logger.error(f"生成预签名URL失败: {str(e)}")
            raise e

    def get_object(self, object_name: str):
        """获取带签名的访问URL"""
        return self.get_object_bucket(self.bucket_name,object_name)

    def get_object_bucket(self, bucket_name,object_name: str):
        """获取带签名的访问URL"""
        try:
            return self.client.get_object(
                bucket_name=bucket_name,
                object_name=object_name
            )
        except S3Error as e:
            logger.error(f"生成预签名URL失败: {str(e)}")
            raise e

    def download_file(self, object_name: str, file_path: str) -> str:
        """从MinIO下载文件"""
        try:
            self.client.fget_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                file_path=file_path
            )
            return file_path
        except S3Error as e:
            logger.error(f"MinIO下载失败: {str(e)}")
            raise e


# --------------------------------------------------------------------------- #
# 启动即初始化（明确不做懒加载）
#
# MinIO 是平台的**必备外部依赖**（知识库文档 / 舌象图片 / 食养计划附件都落在它上面），
# 因此在模块导入阶段就建立连接并确保桶存在：连接失败直接抛错，让问题在
# **服务启动期**暴露（快速失败），而不是拖到用户第一次上传时才 500。
# --------------------------------------------------------------------------- #
def build_minio_client(bucket_name: Optional[str] = None) -> "MinioClient":
    try:
        client = MinioClient(bucket_name)
        logger.info(f"MinIO 已连接：{settings.MINIO_ENDPOINT} / bucket={client.bucket_name}")
        return client
    except Exception as e:                       # noqa: BLE001
        logger.error(f"MinIO 初始化失败（endpoint={settings.MINIO_ENDPOINT}）：{e}")
        raise


def get_minio() -> "MinioClient":
    """按需创建并返回 MinIO 单例，避免模块导入时产生网络副作用。"""
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                _minio_client = build_minio_client()
    return _minio_client


_minio_client: Optional[MinioClient] = None
_minio_lock = threading.Lock()
