# -*- coding: utf-8 -*-
"""智能体中心与五大健康场景生产路由。

配置驱动：目录与场景均读 `system_config`（`agent.catalog` / `scene.config`），
缺省使用内置定义；管理员可通过 `PUT /admin/config` 维护，无需改代码。
"""
from dataclasses import asdict

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import DispatchReq, OrchestrateReq, RouteReq
from app.services.plan_service import plan_service
from app.services import intent_service, orchestrator_service
from app.agent_runtime.task_manager import task_manager
from app.agent_runtime.tool_registry import runtime_capabilities
from app.agent_runtime.approval import approval_manager
from app.agent_runtime.runner import resume_task
from app.agent_runtime.memory import memory_manager
from app.agent_runtime.telemetry import telemetry
from app.agent_runtime.evaluation import evaluate_contract, evaluate_fault_recovery
from app.agent_runtime.readiness import readiness_report

router = APIRouter(prefix="", tags=["智能体中心"])


def _assert_task_access(task, user: dict) -> None:
    """任务、trace、审批统一做所有者或运营角色校验。"""
    from app.core.errors import BizError, ErrCode
    owner = str(getattr(task, "user_id", None) or "")
    actor = str((user or {}).get("sub") or "")
    role = str((user or {}).get("role") or "user")
    if owner and owner != actor and role not in {"admin", "operator"}:
        raise BizError(ErrCode.PERMISSION_DENIED, "无权访问其他用户的 Agent 任务")


@router.get("/agents", summary="智能体目录", description="主智能体 + 六个子智能体列表（配置驱动）。")
async def agents(_: dict = Depends(get_current_user)):
    return success(data=plan_service.agents())


@router.post("/agents/dispatch", summary="主智能体调度", description="输入一句需求，由主智能体按规则规划：调用哪些子智能体、按什么顺序、为什么（规则引擎，不经大模型）。")
async def dispatch(body: DispatchReq, _: dict = Depends(get_current_user)):
    return success(data=plan_service.dispatch(body.text), message="调度方案已生成")


@router.post("/agents/route", summary="意图识别（只识别不执行）",
             description="把一句话判成意图并给出置信度与命中证据。**确定性规则分类器，不调用大模型**——"
                         "同一问句永远同一结果，误判率可配标注集量化。命中医疗干预/诊断/报告解读/"
                         "孕婴时直接标记 blocked，不纳入任何业务链路。")
async def route(body: RouteReq, _: dict = Depends(get_current_user)):
    result = intent_service.classify(body.text)
    return success(data={
        "request": body.text,
        "intent": {
            "primary": result.get("primary", ""),
            "blocked": result.get("blocked", False),
            "refuse_reason": result.get("refuse_reason", ""),
            "refuse_text": result.get("refuse_text", ""),
            "confidence": result.get("confidence", 0.0),
            "margin": result.get("margin", 0.0),
            "low_confidence": result.get("low_confidence", False),
            "fallback": result.get("fallback", False),
            "ranked": result.get("ranked", []),
            "classifier": "确定性规则分类器（关键词加权 + 阈值 + 顺序仲裁）",
        },
        "plan": intent_service.plan_agents(result, max_agents=body.max_agents),
        "explain": intent_service.explain(result),
    }, message="意图识别完成")


@router.post("/agents/orchestrate", summary="主智能体完整编排",
             description="识别意图 → 规划执行链路 → 逐个分派子智能体 → 结构化汇总。"
                         "**高危问题在识别阶段即拒答，一次子智能体都不调用**；"
                         "汇总只做结构化拼接，不经模型二次生成；任一子通道失败如实上报，不编造。")
async def orchestrate(body: OrchestrateReq, user: dict = Depends(get_current_user)):
    data = await orchestrator_service.orchestrate(
        body.text, user, max_agents=body.max_agents, execute=body.execute)
    return success(data=data, message="编排完成" if data.get("executed") else "已生成编排方案")


@router.get("/agents/{key}", summary="智能体详情", description="查看单个智能体的能力说明与启用状态。")
async def agent_detail(key: str, _: dict = Depends(get_current_user)):
    return success(data=plan_service.agent_detail(key))


@router.get("/agents/{key}/runtime", summary="查看 Agent Runtime 能力",
            description="返回该 Agent 的统一契约、注册工具和审批工具清单。")
async def agent_runtime_capabilities(key: str, _: dict = Depends(get_current_user)):
    return success(data=runtime_capabilities(key))


@router.get("/scenes", summary="五大健康场景", description="控糖/慢病/高血压/孕期/痰湿等场景入口配置。")
async def scenes(_: dict = Depends(get_current_user)):
    return success(data=plan_service.scenes())


@router.get("/agent-tasks/{task_id}", summary="查看 Agent 任务状态",
            description="查看任务状态、当前步骤、计划、结果和统一 Runtime 元数据。")
async def agent_task(task_id: str, user: dict = Depends(get_current_user)):
    task = task_manager.get(task_id)
    if task is None:
        from app.core.errors import BizError, ErrCode
        raise BizError(ErrCode.RESOURCE_NOT_FOUND, "Agent 任务不存在")
    _assert_task_access(task, user)
    return success(data=asdict(task))


@router.post("/agent-tasks/{task_id}/cancel", summary="取消 Agent 任务并记录补偿结果")
async def cancel_agent_task(task_id: str, body: dict = None,
                            user: dict = Depends(get_current_user)):
    from app.core.errors import BizError, ErrCode
    try:
        task = task_manager.get(task_id)
        if task is None:
            raise KeyError(task_id)
        _assert_task_access(task, user)
        result = await task_manager.cancel(
            task_id, (body or {}).get("reason") or "用户取消任务",
        )
    except KeyError:
        raise BizError(ErrCode.RESOURCE_NOT_FOUND, "Agent 任务不存在")
    return success(data=asdict(result), message="任务已取消，补偿结果已记录")


@router.get("/agent-tasks/{task_id}/trace", summary="查看 Agent 调用链",
            description="返回任务的状态事件时间线；后续接入数据库后支持跨进程追踪。")
async def agent_task_trace(task_id: str, user: dict = Depends(get_current_user)):
    try:
        task = task_manager.get(task_id)
        if task is None:
            raise KeyError(task_id)
        _assert_task_access(task, user)
        events = task_manager.trace(task_id)
    except KeyError:
        from app.core.errors import BizError, ErrCode
        raise BizError(ErrCode.RESOURCE_NOT_FOUND, "Agent 任务不存在")
    return success(data={"task_id": task_id, "events": events, "total": len(events)})


@router.get("/agent-tasks/{task_id}/approvals", summary="查看待审批项")
async def agent_task_approvals(task_id: str, user: dict = Depends(get_current_user)):
    task = task_manager.get(task_id)
    if task is None:
        from app.core.errors import BizError, ErrCode
        raise BizError(ErrCode.RESOURCE_NOT_FOUND, "Agent 任务不存在")
    _assert_task_access(task, user)
    return success(data={
        "task_id": task_id,
        "items": [asdict(item) for item in approval_manager.for_task(task_id)],
    })


@router.post("/agent-approvals/{approval_id}/decide", summary="处理 Agent 人工审批")
async def decide_agent_approval(approval_id: str, body: dict,
                                user: dict = Depends(get_current_user)):
    from app.core.errors import BizError, ErrCode
    approved = bool((body or {}).get("approved"))
    comment = (body or {}).get("comment") or ""
    try:
        item = approval_manager.decide(
            approval_id, approved, str(user.get("sub") or ""), comment,
        )
    except (KeyError, ValueError) as exc:
        raise BizError(ErrCode.PARAM_INVALID, str(exc))
    return success(data=asdict(item), message="审批已处理")


@router.post("/agent-approvals/{approval_id}/resume", summary="恢复已批准 Agent 任务")
async def resume_agent_approval(approval_id: str, _: dict = Depends(get_current_user)):
    from app.core.errors import BizError, ErrCode
    try:
        item = approval_manager.resume(approval_id)
        result = await resume_task(item.task_id)
    except (KeyError, ValueError) as exc:
        raise BizError(ErrCode.PARAM_INVALID, str(exc))
    return success(data={"approval": asdict(item), "result": result}, message="任务已恢复执行")


@router.get("/agent-memory", summary="查看当前用户 Agent 记忆")
async def agent_memory(agent: str = "", user: dict = Depends(get_current_user)):
    user_id = str(user.get("sub") or "")
    return success(data=memory_manager.context(
        user_id=user_id, agent_key=agent or None, limit=30,
    ))


@router.post("/agent-memory", summary="写入用户 Agent 偏好记忆")
async def remember_agent_memory(body: dict,
                                user: dict = Depends(get_current_user)):
    memory_type = (body or {}).get("memory_type") or "preference"
    if memory_type not in {"preference", "profile"}:
        from app.core.errors import BizError, ErrCode
        raise BizError(ErrCode.PARAM_INVALID, "只能由用户主动写入 preference 或 profile 记忆")
    item = memory_manager.remember(
        str((body or {}).get("agent_key") or "shared"), memory_type,
        (body or {}).get("content") or {},
        user_id=str(user.get("sub") or ""), source="user_explicit",
        confidence=1.0,
    )
    return success(data=asdict(item), message="用户记忆已保存")


@router.get("/agent-metrics", summary="Agent 运行指标与成本")
async def agent_metrics(_: dict = Depends(get_current_user)):
    return success(data=telemetry.snapshot())


@router.get("/agent-evaluations", summary="Agent 合同评估")
async def agent_evaluations(_: dict = Depends(get_current_user)):
    return success(data=evaluate_contract())


@router.post("/agent-evaluations/fault-recovery", summary="运行 Agent 故障恢复评估")
async def agent_fault_recovery(_: dict = Depends(get_current_user)):
    return success(data=await evaluate_fault_recovery())


@router.get("/agent-readiness", summary="Agent Runtime 上线前就绪检查")
async def agent_readiness(_: dict = Depends(get_current_user)):
    return success(data=readiness_report())
