import os
from typing import Optional

from dotenv import load_dotenv

# 加载.env文件
# 【关键修改】指定.env文件路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, '.env'))  # 指定完整路径
# load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
class Settings:
    ENV: str = os.getenv("ENV", "development").strip().lower()
    # Ollama配置
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen3:0.6b")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "bge-m3:latest")
    SILICON_FLOW_LLM_MODEL: str = os.getenv("SILICON_FLOW_LLM_MODEL")
    SILICON_FLOW_EMBEDDING_MODEL: str = os.getenv("SILICON_FLOW_EMBEDDING_MODEL")
    # 新增策略配置
    LLM_STRATEGY: str = os.getenv("LLM_STRATEGY")
    EMBEDDING_STRATEGY: str = os.getenv("EMBEDDING_STRATEGY")
    SILICON_FLOW_API_KEY: str = os.getenv("SILICON_FLOW_API_KEY")
    SILICON_FLOW_BASE_URL: str = os.getenv("SILICON_FLOW_BASE_URL")
    SILICON_FLOW_RERANK: str = os.getenv("SILICON_FLOW_RERANK")
    # API配置
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    API_KEY: Optional[str] = os.getenv("API_KEY")

    # MinIO配置
    # 登录令牌与演示账号
    SECRET_KEY: str = os.getenv("SECRET_KEY", "ai-health-dev-secret-please-change")
    CORS_ORIGINS: list[str] = [
        item.strip() for item in os.getenv(
            "CORS_ORIGINS", "http://127.0.0.1:8019,http://localhost:8019"
        ).split(",") if item.strip()
    ]
    # ⚠️ 默认关闭：演示账号 demo/123456 属弱口令（2026-09-10 后端体检 P0-2）。
    #    仅在本地调试需要时，用 INIT_DEMO_USER=true 显式开启。
    INIT_DEMO_USER: bool = os.getenv("INIT_DEMO_USER", "false").lower() == "true"

    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "vanna-training-data")
    MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "False").lower() == "true"

    KB_BUCKET_NAME: str = os.getenv("KB_BUCKET_NAME", "")

    # Redis（可选依赖：不可用时相关能力自动降级，见 app/utils/redis_client.py）
    # 注意：优先使用 REDIS_URL；未设置时才回退到 host/port/db。
    REDIS_URL: str = os.getenv("REDIS_URL")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_MAX_CONNECTIONS: int = int(os.getenv("REDIS_MAX_CONNECTIONS",10000))
    REDIS_SOCKET_TIMEOUT: int = int(os.getenv("REDIS_SOCKET_TIMEOUT",30))
    REDIS_SOCKET_CONNECT_TIMEOUT: int = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT",5))

    MYSQL_HOST: str = os.getenv("MYSQL_HOST")
    MYSQL_PORT: str = os.getenv("MYSQL_PORT")
    MYSQL_USER: str = os.getenv("MYSQL_USER")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD")
    MYSQL_DB: str = os.getenv("MYSQL_DB")
    MYSQL_URI = os.getenv("MYSQL_URI")

    MILVUS_URI: str = os.getenv("MILVUS_URI")
    MILVUS_HOST: str = os.getenv("MILVUS_HOST", "127.0.0.1")  # 2026-09-12：默认本机（原 192.168.128.210 已失联）
    MILVUS_PORT: str = os.getenv("MILVUS_PORT", "19530")
    MILVUS_DEFAULT_COLLECTION: str = os.getenv("MILVUS_DEFAULT_COLLECTION", "ai_health_kb_v1")
    MILVUS_DEFAULT_ALIAS: str = os.getenv("MILVUS_DEFAULT_ALIAS", "default")
    MILVUS_DEFAULT_DB: str = os.getenv("MILVUS_DEFAULT_DB", "default")

    # 短信验证码（腾讯云短信；缺任一必填项 = 未配置，发码端返回明确错误而不伪造成功）
    SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "tencent")
    SMS_SECRET_ID: str = os.getenv("SMS_SECRET_ID", "")
    SMS_SECRET_KEY: str = os.getenv("SMS_SECRET_KEY", "")
    SMS_SDK_APP_ID: str = os.getenv("SMS_SDK_APP_ID", "")
    SMS_SIGN_NAME: str = os.getenv("SMS_SIGN_NAME", "")
    SMS_TEMPLATE_ID: str = os.getenv("SMS_TEMPLATE_ID", "")
    SMS_CODE_TTL: int = int(os.getenv("SMS_CODE_TTL", "300"))            # 验证码有效期（秒）
    SMS_RESEND_INTERVAL: int = int(os.getenv("SMS_RESEND_INTERVAL", "60"))  # 重发限频（秒）
    SMS_CODE_MAX_ATTEMPTS: int = int(os.getenv("SMS_CODE_MAX_ATTEMPTS", "5"))  # 最大校验失败次数
    SMS_DEMO_MODE: bool = os.getenv("SMS_DEMO_MODE", "false").lower() == "true"
    KB_MIN_SIMILARITY: float = float(os.getenv("KB_MIN_SIMILARITY", "0.55"))  # RAG 检索阈值（COSINE，低于则视为无相关内容）

    # 文件上传
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50MB
    DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50MB


# 创建配置实例
settings = Settings()


def validate_runtime_settings() -> None:
    """校验生产环境安全基线；配置不安全时拒绝启动。"""
    if settings.ENV != "production":
        return
    errors = []
    if not settings.SECRET_KEY or settings.SECRET_KEY == "ai-health-dev-secret-please-change":
        errors.append("生产环境必须设置独立的高强度 SECRET_KEY")
    if len(settings.SECRET_KEY) < 32:
        errors.append("SECRET_KEY 长度不得少于 32 个字符")
    if settings.SMS_DEMO_MODE:
        errors.append("生产环境禁止开启 SMS_DEMO_MODE")
    if "*" in settings.CORS_ORIGINS:
        errors.append("生产环境 CORS_ORIGINS 禁止使用通配符")
    if errors:
        raise RuntimeError("；".join(errors))

