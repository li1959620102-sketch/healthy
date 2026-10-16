# -*- coding: utf-8 -*-
"""智能体中心与五大健康场景生产路由。

配置驱动：目录与场景均读 `system_config`（`agent.catalog` / `scene.config`），
缺省使用内置定义；管理员可通过 `PUT /admin/config` 维护，无需改代码。
"""
from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.responses import success
from app.schemas.platform import DispatchReq, OrchestrateReq, RouteReq
from app.services.plan_service import plan_service
from app.services import intent_service, orchestrator_service

router = APIRouter(prefix="", tags=["智能体中心"])


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


@router.get("/scenes", summary="五大健康场景", description="控糖/慢病/高血压/孕期/痰湿等场景入口配置。")
async def scenes(_: dict = Depends(get_current_user)):
    return success(data=plan_service.scenes())
