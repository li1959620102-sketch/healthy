import os

from dotenv import load_dotenv

# 加载 .env：必须按**本文件所在项目根目录**定位，否则从别的 cwd 启动会静默读不到配置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, '.env'))


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
    SILICON_FLOW_RERANK: str = os.getenv("SILICON_FLOW_RERANK")  # 预留：rerank 阶段尚未接入，管线当前只用相似度阈值

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
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "ai-health-platform")
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

    MILVUS_HOST: str = os.getenv("MILVUS_HOST", "127.0.0.1")  # 2026-09-12：默认本机（原内网地址已失联）
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

    # 邮箱验证码（SMTP；缺任一必填项 = 未配置，发码端返回明确错误而不伪造成功）
    # FR-U-01：手机号/邮箱双通道注册，邮箱通道复用同一套「发码 / 限频 / 验码」流程。
    MAIL_PROVIDER: str = os.getenv("MAIL_PROVIDER", "smtp")
    MAIL_HOST: str = os.getenv("MAIL_HOST", "")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", "465"))
    MAIL_USER: str = os.getenv("MAIL_USER", "")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM: str = os.getenv("MAIL_FROM", "")          # 发件人；留空则回退为 MAIL_USER
    MAIL_USE_SSL: bool = os.getenv("MAIL_USE_SSL", "true").lower() == "true"   # 465 端口用 SSL
    MAIL_USE_TLS: bool = os.getenv("MAIL_USE_TLS", "false").lower() == "true"  # 587 端口用 STARTTLS
    MAIL_TIMEOUT: int = int(os.getenv("MAIL_TIMEOUT", "10"))
    EMAIL_CODE_TTL: int = int(os.getenv("EMAIL_CODE_TTL", "300"))              # 验证码有效期（秒）
    EMAIL_RESEND_INTERVAL: int = int(os.getenv("EMAIL_RESEND_INTERVAL", "60"))  # 重发限频（秒）
    EMAIL_CODE_MAX_ATTEMPTS: int = int(os.getenv("EMAIL_CODE_MAX_ATTEMPTS", "5"))  # 最大校验失败次数
    MAIL_DEMO_MODE: bool = os.getenv("MAIL_DEMO_MODE", "false").lower() == "true"
    KB_MIN_SIMILARITY: float = float(os.getenv("KB_MIN_SIMILARITY", "0.55"))  # RAG 检索阈值（COSINE，低于则视为无相关内容）

    # 文件上传（大小上限由 app/utils/file_handler.py 在读取前后各校验一次）
    FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50MB

    # 视觉模型（舌象辨识，O 组）：走本机 Ollama 的多模态模型
    VISION_MODEL: str = os.getenv("VISION_MODEL", "qwen2.5vl:3b")
    VISION_TIMEOUT: int = int(os.getenv("VISION_TIMEOUT", "180"))   # 单张图辨识超时（秒）
    VISION_ENABLE: bool = os.getenv("VISION_ENABLE", "true").lower() == "true"

    # 语音识别（ASR，S 组）：faster-whisper 本地推理
    # 模型名按 CTranslate2 命名（tiny/base/small/medium/large-v3），无 GPU 时用 int8 走 CPU。
    ASR_MODEL: str = os.getenv("ASR_MODEL", "small")
    ASR_DEVICE: str = os.getenv("ASR_DEVICE", "cpu")
    ASR_COMPUTE_TYPE: str = os.getenv("ASR_COMPUTE_TYPE", "int8")
    ASR_BEAM_SIZE: int = int(os.getenv("ASR_BEAM_SIZE", "1"))
    # 推理线程数。0 = 让 ctranslate2 自己决定。本机实测 8 线程最快：
    # 实时率 0.64；调到 16（逻辑核数）反而退化到 0.90——超订线程后
    # 每个线程分到的缓存变少，int8 矩阵乘的开销全花在切换上。
    ASR_CPU_THREADS: int = int(os.getenv("ASR_CPU_THREADS", "8"))
    ASR_MODEL_DIR: str = os.getenv("ASR_MODEL_DIR", os.path.join(BASE_DIR, "models", "whisper"))
    ASR_MAX_AUDIO_SECONDS: int = int(os.getenv("ASR_MAX_AUDIO_SECONDS", "120"))

    # 定时任务（合规 D2 注销清理 / FR-V-10 预警求值）
    # 进程内 APScheduler：不存在"crontab 改了但业务不知道"的两处配置问题，
    # 执行结果可在 GET /api/v1/ops/scheduler 查到。多副本靠 Redis 锁互斥。
    SCHEDULER_ENABLE: bool = os.getenv("SCHEDULER_ENABLE", "true").lower() == "true"
    SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "Asia/Shanghai")
    DELETION_PURGE_HOUR: int = int(os.getenv("DELETION_PURGE_HOUR", "3"))
    ALERT_EVAL_MINUTES: int = int(os.getenv("ALERT_EVAL_MINUTES", "60"))
    # 告警外发通道（企业微信/钉钉机器人）。留空表示只用站内通知——
    # 未配置不算失败，但运维必须知道"现在只有站内信这一条通道"。
    ALERT_WEBHOOK_URL: str = os.getenv("ALERT_WEBHOOK_URL", "")

    # 版本号（上线证据 3：发布记录可追溯）。由镜像/流水线注入，缺省为开发标记。
    APP_VERSION: str = os.getenv("APP_VERSION", "dev")
    APP_BUILD: str = os.getenv("APP_BUILD", "")


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
    if settings.MAIL_DEMO_MODE:
        errors.append("生产环境禁止开启 MAIL_DEMO_MODE")
    if "*" in settings.CORS_ORIGINS:
        errors.append("生产环境 CORS_ORIGINS 禁止使用通配符")
    if errors:
        raise RuntimeError("；".join(errors))

