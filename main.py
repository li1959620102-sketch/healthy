from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
# ⛔ 已下线（2026-09-10 后端体检 P0-1 / P0-3）：
#   以下 4 个遗留 router（NRS2002 时期）内部无任何鉴权依赖，且 text2sql/text2echarts
#   会让 LLM 生成 SQL 后直接执行（违反「绝不让 LLM 直连数据库」红线）；同时它们的
#   __init__ 已注册过一次路由、此处又调一次，造成端点重复注册。
#   同类能力已由 /api/v1/analytics/**（受控查询模板 + 角色行级权限）承接。
#   2026-10-16 起源码已归档至 legacy_nrs2002/app/**（连同其下游 adapter/designer/chunk_service/
#   vanna_*/rag_query_service 等 25 个模块），活跃代码树不再保留，故此处的 import 一并删去。
#   如需恢复：从 legacy_nrs2002/app/routers/ 取回并把父包同路径也一并取回。
# 第一批 · 函数式路由（A/B/K/G/I）
from app.routers.auth_router import router as auth_router
from app.routers.user_router import router as user_router
from app.routers.knowledge_router import router as knowledge_router
from app.routers.chat_router import router as chat_router
from app.routers.dashboard_router import router as dashboard_router
# 第二批 · 函数式路由（D 测评模板 / E 测评 / F 报告）
from app.routers.template_router import router as template_router
from app.routers.assessment_router import router as assessment_router
from app.routers.report_router import router as report_router
# 原型新增模块（第二批续）：C 营养师 / H 咨询 / J 管理 / L 食养 / M 荐菜 / N 智能体中心 / O 舌象
from app.routers.nutritionist_router import router as nutritionist_router
from app.routers.consultation_router import router as consultation_router
from app.routers.admin_router import router as admin_router
from app.routers.plan_router import router as plan_router
from app.routers.recommend_router import router as recommend_router
from app.routers.agent_router import router as agent_router
from app.routers.tongue_router import router as tongue_router
from app.routers.checkin_router import router as checkin_router
from app.routers.notification_router import router as notification_router
from app.routers.analytics_router import router as analytics_router
from app.routers.scene_router import banquet_router, ocr_router, travel_router
from app.routers.digital_human_router import router as digital_human_router
from app.routers.ops_router import router as ops_router
from app.utils.db import init_tables, db_manager
from app.core.metrics import install_metrics, metrics, normalize_path
from app.services.user_service import UserService
from app.core.errors import BizError, ErrCode, http_to_code
from app.core.responses import success, error, new_request_id
from app.exceptions.base_api_exception import BaseAPIException
from app.config import settings, validate_runtime_settings
from contextlib import asynccontextmanager
import os
from fastapi.staticfiles import StaticFiles
import logging.config
import uvicorn


# 配置logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 移除所有现有的handler
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# 创建新的格式化器和handler
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler('ai_health.log', encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
# ------------------------------
# 核心修改：使用 lifespan 替代 on_event
# ------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动初始化：检查数据库表并准备演示账号"""
    validate_runtime_settings()
    logger.info("启动初始化：检查数据库表与演示账号")
    # 安全基线自检提示：非 production 环境也打印，避免"默认值悄悄上线"
    if settings.SECRET_KEY == "ai-health-dev-secret-please-change":
        logger.warning("⚠️ SECRET_KEY 仍是内置默认值，仅可用于本地开发；上线前必须在 .env 覆盖（见 scripts/rotate_secret_key.py）")
    if settings.SMS_DEMO_MODE:
        logger.warning("⚠️ 短信处于演示模式（验证码回传前端），仅限本地联调；ENV=production 下会拒绝启动")
    try:
        init_tables()
        UserService.ensure_default_user()
    except Exception as e:
        logger.error(f"启动初始化失败（不阻断服务）：{e}")

    # 预热营养师智能体组件（嵌入模型/向量集合/LLM 客户端）：
    # 否则首次提问要现场建连+加载模型，首字延迟高达 9 秒。后台线程执行，不阻断启动。
    import threading

    def _prewarm():
        try:
            from app.services.chat_query_service import chat_query_service
            ok = chat_query_service._ensure_components()
            if ok and chat_query_service._embeddings is not None:
                chat_query_service._embeddings.embed_query("预热")
            logger.info("智能体组件预热完成（ready=%s）", ok)
        except Exception as e:
            logger.warning(f"智能体组件预热失败（不影响服务）：{e}")

    threading.Thread(target=_prewarm, daemon=True).start()
    yield


# 创建 FastAPI 实例并绑定生命周期
app = FastAPI(lifespan=lifespan)

# 运行指标采集（监控）：统计调用量 / 错误率 / 延迟分位，纯内存、零外部依赖
install_metrics(app)



# 挂载路由（确保每个路由模块中定义了 router 实例）
# ⛔ 遗留 4 个 router 已下线并归档（2026-09-10 体检 P0-1/P0-3：零鉴权 + LLM 直连库 + 端点重复注册）
#    2026-10-16：源码连同下游 25 个模块一并移入 legacy_nrs2002/app/**，挂载处整体移除。
#    恢复：从 legacy_nrs2002/app/routers/ 取回对应 router 与其下游，再重新注册。

# 第一批 · 函数式路由挂载（A 认证 / B 用户 / K 知识库 / G 智能查询 / I 看板）
app.include_router(auth_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1")
app.include_router(knowledge_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")
# 第二批（D 模板 / E 测评 / F 报告）
app.include_router(template_router, prefix="/api/v1")
app.include_router(assessment_router, prefix="/api/v1")
app.include_router(report_router, prefix="/api/v1")
# 原型新增模块
app.include_router(nutritionist_router, prefix="/api/v1")
app.include_router(consultation_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(plan_router, prefix="/api/v1")
app.include_router(recommend_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(tongue_router, prefix="/api/v1")
app.include_router(checkin_router, prefix="/api/v1")
app.include_router(notification_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(banquet_router, prefix="/api/v1")
app.include_router(travel_router, prefix="/api/v1")
app.include_router(ocr_router, prefix="/api/v1")
app.include_router(digital_human_router, prefix="/api/v1")
# 运维监控（P 运维指标 / 压测观测）
app.include_router(ops_router, prefix="/api/v1")
# 健康检查端点

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/api/health", summary="服务健康检查", tags=["系统"])
async def api_health():
    return {"status": "ok", "service": "AI智参（药食同源）健康平台"}


# ------------------------------
# 设计基线 B0：请求追踪 / 全局异常统一响应 / 依赖探针
# ------------------------------
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """为每个请求分配/透传 request_id，便于链路排查。"""
    rid = request.headers.get("X-Request-ID") or new_request_id()
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


def _metric_path(request: Request) -> str:
    """归一化请求路径，供指标按接口聚合（/reports/123 → /reports/{id}）。"""
    return normalize_path(request.url.path, getattr(request.scope.get("route"), "path", None))


@app.exception_handler(BizError)
async def _biz_handler(request: Request, exc: BizError):
    metrics.record_biz_error(request.method, _metric_path(request), exc.code)
    return JSONResponse(
        status_code=exc.http_status,
        content=error(exc.code, exc.message, exc.data, _rid(request)).model_dump(),
    )


@app.exception_handler(BaseAPIException)
async def _base_handler(request: Request, exc: BaseAPIException):
    code = http_to_code(exc.status_code)
    metrics.record_biz_error(request.method, _metric_path(request), code)
    return JSONResponse(
        status_code=200,
        content=error(code, exc.detail, request_id=_rid(request)).model_dump(),
    )


def _sanitize_validation_errors(errors) -> list:
    """把 RequestValidationError.errors() 转成可 JSON 序列化的结构。

    坑：`field_validator` 里 `raise ValueError(...)` 时，错误项的 `ctx` 会带上**原始异常对象**，
    直接塞进响应体会导致 `TypeError: Object of type ValueError is not JSON serializable`，
    参数校验失败被放大成 500。这里统一把异常对象降级成字符串。
    """
    def _clean(value):
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_clean(v) for v in value]
        if isinstance(value, BaseException):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    return [_clean(dict(e)) for e in (errors or [])]


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    metrics.record_biz_error(request.method, _metric_path(request), ErrCode.PARAM_INVALID)
    return JSONResponse(
        status_code=200,
        content=error(ErrCode.PARAM_INVALID, "参数校验失败",
                      _sanitize_validation_errors(exc.errors()), _rid(request)).model_dump(),
    )


@app.exception_handler(HTTPException)
async def _http_handler(request: Request, exc: HTTPException):
    code = http_to_code(exc.status_code)
    metrics.record_biz_error(request.method, _metric_path(request), code)
    return JSONResponse(
        status_code=200,
        content=error(code, exc.detail or "error", request_id=_rid(request)).model_dump(),
    )


@app.get("/api/v1/ready", summary="依赖就绪检查（探针）", tags=["系统"])
async def ready():
    """供容器/K8s 探针与运营大屏调用；任一依赖失败仅标记，不影响其他检查。"""
    checks: dict = {}
    try:
        if db_manager is None:
            checks["mysql"] = "unconfigured"
        else:
            with db_manager.engine.connect() as c:
                c.execute(text("SELECT 1"))
            checks["mysql"] = "ok"
    except Exception as e:
        checks["mysql"] = f"fail: {e}"
    try:
        from pymilvus import MilvusClient
        if settings.MILVUS_HOST:
            mc = MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
            mc.list_collections()
            checks["milvus"] = "ok"
        else:
            checks["milvus"] = "unconfigured"
    except Exception as e:
        checks["milvus"] = f"fail: {e}"
    # Redis / MinIO 均为必备依赖（2026-09-10 起不再降级跳过）：任一失败即不就绪
    try:
        from app.utils.redis_client import redis_client
        checks["redis"] = redis_client.health()
    except Exception as e:
        checks["redis"] = f"fail: {e}"
    try:
        from app.utils.minio_client import get_minio
        checks["minio"] = "ok" if get_minio().health() else "fail: 存储桶不可达"
    except Exception as e:
        checks["minio"] = f"fail: {e}"
    all_ok = all(v == "ok" for v in checks.values())
    return success(data={"ready": all_ok, "checks": checks})

# ------------------------------
# 托管前端静态页面（同源访问，无需跨域）
# 注意：必须放在所有 API 路由注册之后
# ------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    logger.info(f"已托管前端静态目录：{FRONTEND_DIR}")
else:
    logger.warning(f"未找到前端目录，跳过静态托管：{FRONTEND_DIR}")



if __name__ == "__main__":
    import os

    # 默认关闭 reload：避免 ai_health.log、chroma 向量库等运行时文件变动
    # 被当成"代码变化"触发 uvicorn 无限重启（该进程残留还会占住 8018 端口）。
    # 开发期需要热重载时，用环境变量开启：APP_RELOAD=true python main.py
    reload_flag = os.getenv("APP_RELOAD", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=8018,
                workers=1, timeout_keep_alive=600, reload=reload_flag,
                reload_excludes=["*.log", "*.sqlite3", "*.bin", "*.pyc",
                                 "__pycache__/*"])
