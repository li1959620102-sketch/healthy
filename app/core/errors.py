# -*- coding: utf-8 -*-
"""统一错误码与业务异常（设计基线 B0-2）。

错误码分段（业务码，非零即错误；HTTP 层统一返回 200 + 该 code，
由 main.py 的全局异常处理器完成封装，测试断言以 code 为准）：

  1xxxx 认证与授权
  2xxxx 用户
  3xxxx 营养师 / 客户
  4xxxx 测评模板
  5xxxx 测评 / 报告
  6xxxx 智能体 / 咨询
  7xxxx 看板 / 分析
  8xxxx 管理 / 知识库
  9xxxx 系统 / 参数

注意：报告生成失败「降级」不是错误，走 code=0 + data.state="basic"，
因此 ErrCode.REPORT_GEN_FAILED 仅用于真正的异常分支。
"""
from typing import Any, Optional


class ErrCode:
    # 1xxxx 认证与授权
    UNAUTHENTICATED = 10001
    TOKEN_EXPIRED = 10002
    TOKEN_INVALID = 10003
    GUEST_NOT_ALLOWED = 10004
    PERMISSION_DENIED = 10005
    AUTH_FAILED = 10006
    # 2xxxx 用户
    USER_NOT_FOUND = 20001
    USER_EXISTS = 20002
    VERIFY_CODE_INVALID = 20003
    # 3xxxx 营养师 / 客户
    DIETITIAN_NOT_FOUND = 30001
    CUSTOMER_BIND_CONFLICT = 30002
    # 4xxxx 测评模板
    TEMPLATE_NOT_FOUND = 40001
    TEMPLATE_VERSION_CONFLICT = 40002
    # 5xxxx 测评 / 报告
    ASSESSMENT_NOT_FOUND = 50001
    REPORT_GEN_FAILED = 50002   # 仅真正异常使用；降级走 code=0 + state=basic
    SCORE_ENGINE_ERROR = 50003
    ASSESSMENT_CONFLICT = 50004  # 草稿并发冲突（多端同时编辑）
    REPORT_NOT_FOUND = 50005
    # 6xxxx 智能体 / 咨询 / 短信
    AGENT_UNKNOWN = 60001
    QUERY_RATE_LIMITED = 60002
    SMS_RATE_LIMITED = 60003    # 短信验证码发送过频（60s 限频）
    SMS_SEND_FAILED = 60004     # 短信网关未配置 / 发送失败（绝不伪造“已发送”）
    EMAIL_RATE_LIMITED = 60005  # 邮箱验证码发送过频（与短信同一套限频口径）
    EMAIL_SEND_FAILED = 60006   # 邮件网关未配置 / 发送失败（绝不伪造“已发送”）
    ASR_UNAVAILABLE = 60007     # 语音识别不可用（依赖未装 / 权重缺失 / 音频超长）
    TTS_UNAVAILABLE = 60008     # 语音合成不可用（edge-tts 需要外网）
    # 7xxxx 看板 / 分析（预留）
    # 8xxxx 管理 / 知识库
    KB_DOC_NOT_FOUND = 80001
    # 9xxxx 系统 / 参数
    SYSTEM_ERROR = 90000
    PARAM_INVALID = 90001
    RESOURCE_NOT_FOUND = 90002
    DB_ERROR = 90003            # 数据库写入/事务失败（此前被 4 处引用但未定义 → AttributeError 500）


# HTTP 状态码 → 业务码 映射（全局异常处理器用）
_HTTP_TO_CODE = {
    401: ErrCode.UNAUTHENTICATED,
    403: ErrCode.PERMISSION_DENIED,
    404: ErrCode.RESOURCE_NOT_FOUND,
    400: ErrCode.PARAM_INVALID,
    409: ErrCode.USER_EXISTS,
    422: ErrCode.PARAM_INVALID,
    429: ErrCode.QUERY_RATE_LIMITED,
    500: ErrCode.SYSTEM_ERROR,
    502: ErrCode.SYSTEM_ERROR,
    504: ErrCode.SYSTEM_ERROR,
}


def http_to_code(status_code: int) -> int:
    return _HTTP_TO_CODE.get(status_code, ErrCode.SYSTEM_ERROR)


class BizError(Exception):
    """业务异常：携带业务码与可选 HTTP 状态码（默认 200，由处理器封装）。"""

    def __init__(self, code: int, message: str, data: Any = None, http_status: int = 200):
        self.code = code
        self.message = message
        self.data = data
        self.http_status = http_status
        super().__init__(message)
