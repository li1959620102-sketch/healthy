from typing import Any, Dict, Optional

from fastapi import HTTPException, status


class BaseAPIException(HTTPException):
    """基础 API 异常类"""
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Internal server error"
    headers: Optional[Dict[str, Any]] = None

    def __init__(self, detail: Optional[str] = None, headers: Optional[Dict[str, Any]] = None):
        super().__init__(
            status_code=self.status_code,
            detail=detail or self.detail,
            headers=headers or self.headers
        )

class NotFoundException(BaseAPIException):
    """资源未找到异常"""
    status_code = status.HTTP_404_NOT_FOUND
    detail = "Resource not found"

class ConflictException(BaseAPIException):
    """资源冲突异常（如唯一键冲突）"""
    status_code = status.HTTP_409_CONFLICT
    detail = "Resource conflict"

class UnauthorizedException(BaseAPIException):
    """未授权异常"""
    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Not authenticated"
    headers = {"WWW-Authenticate": "Bearer"}

class ForbiddenException(BaseAPIException):
    """禁止访问异常"""
    status_code = status.HTTP_403_FORBIDDEN
    detail = "Permission denied"

class BadRequestException(BaseAPIException):
    """错误请求异常"""
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Invalid request"

class ValidationException(BadRequestException):
    """数据验证异常"""
    detail = "Validation error"

class DatabaseException(BaseAPIException):
    """数据库操作异常"""
    detail = "Database operation failed"

class ExternalServiceException(BaseAPIException):
    """外部服务异常"""
    detail = "External service error"

class RateLimitExceededException(BaseAPIException):
    """速率限制异常"""
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    detail = "Rate limit exceeded"

class WorkflowExecutionException(BaseAPIException):
    """工作流执行异常"""
    detail = "Workflow execution error"

class WorkflowTimeoutException(WorkflowExecutionException):
    """工作流超时异常"""
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    detail = "Workflow execution timed out"

class WorkflowConfigurationException(BadRequestException):
    """工作流配置异常"""
    detail = "Invalid workflow configuration"

class LLMServiceException(ExternalServiceException):
    """大模型服务异常"""
    detail = "Large language model service error"

class AuthenticationException(UnauthorizedException):
    """认证异常"""
    detail = "Authentication failed"

class PermissionException(ForbiddenException):
    """权限异常"""
    detail = "Insufficient permissions"

# 用于 MongoDB 操作的异常
class MongoOperationException(DatabaseException):
    """MongoDB 操作异常"""
    detail = "MongoDB operation failed"

class MongoConnectionException(DatabaseException):
    """MongoDB 连接异常"""
    detail = "MongoDB connection failed"

# 用于文件操作的异常
class FileUploadException(BadRequestException):
    """文件上传异常"""
    detail = "File upload failed"

class FileSizeExceededException(FileUploadException):
    """文件大小超出限制异常"""
    detail = "File size exceeds allowed limit"

class InvalidFileTypeException(FileUploadException):
    """无效文件类型异常"""
    detail = "Invalid file type"

# 用于缓存操作的异常
class CacheException(BaseAPIException):
    """缓存操作异常"""
    detail = "Cache operation failed"

class CacheMissException(CacheException):
    """缓存未命中异常"""
    status_code = status.HTTP_404_NOT_FOUND
    detail = "Cache miss"
class UnsupportedFileTypeException(BaseAPIException):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    detail = "Unsupported file type"

class TimeoutException(BaseAPIException):
    detail = "Request timed out"