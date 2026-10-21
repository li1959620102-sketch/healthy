# -*- coding: utf-8 -*-
"""Agent Runtime 可识别错误。"""


class AgentRuntimeError(Exception):
    """默认不可恢复错误。"""

    code = "AGENT_RUNTIME_ERROR"
    retryable = False


class RetryableAgentError(AgentRuntimeError):
    """工具暂时不可用，可按策略重试。"""

    code = "AGENT_RETRYABLE_ERROR"
    retryable = True


class AgentApprovalRequired(AgentRuntimeError):
    """当前步骤必须等待人工审批。"""

    code = "AGENT_APPROVAL_REQUIRED"
    retryable = False
