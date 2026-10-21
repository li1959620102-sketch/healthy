# -*- coding: utf-8 -*-
"""把当前业务 Agent 图使用的兼容 handler 显式绑定到 Runtime。

业务图仍可传入 handler 以保持兼容，但启动后的能力盘点必须知道这些实现确实存在。
"""
from __future__ import annotations

from typing import Any, Optional

from app.agent_runtime.tool_executor import register_handler
from app.agent_runtime.compensation import register_compensation

_BOUND = False


def _diet_plan(scene: str = "通用", days: int = 3, avoid: Optional[list] = None):
    from app.services.plan_service import plan_service
    return plan_service.build_recipes(scene, days=days, avoid=avoid)


def _dish_search(query: str = "", avoid: Optional[list] = None):
    from app.services.plan_service import plan_service
    return plan_service.recommend_dishes(query, avoid=avoid)


def _menu_export(user: dict, plan_id: int):
    from app.services.plan_service import plan_service
    return plan_service.shopping_list(user, plan_id)


async def _publish_diet_plan(user: dict, body: dict):
    from app.services.plan_service import plan_service
    return await plan_service.create_plan(user, body)


async def _delegate(agent_key: str, goal: str, user: Optional[dict] = None,
                    context: Optional[dict] = None, graph_input: Optional[dict] = None):
    from app.agent_runtime.runner import run_graph_task
    from app.agents.subgraphs import get_subgraph
    graph = get_subgraph(agent_key)
    if graph is None:
        raise LookupError(f"未知子 Agent：{agent_key}")
    return await run_graph_task(agent_key, goal, graph, user=user,
                                context=context, graph_input=graph_input)


def _aggregate(steps: list):
    from app.agents.aggregate import aggregate_steps
    return aggregate_steps(steps or [])


def _graph_executor(agent_key: str):
    async def execute(goal: str, user: Optional[dict] = None,
                      context: Optional[dict] = None, graph_input: Optional[dict] = None):
        return await _delegate(agent_key, goal, user=user, context=context,
                               graph_input=graph_input)
    return execute


def _export_analysis(user: dict, question: str, fmt: str = "csv"):
    from app.services.analytics_service import analytics_service
    return analytics_service.export(user, question, fmt)


def _publish_report(record_id: int, user: dict, guest_hash: Optional[str] = None,
                    idempotency_key: Optional[str] = None):
    from app.services.assessment_service import AssessmentService
    return AssessmentService.submit(record_id, user, guest_hash, idempotency_key)


def _delete_plan(plan_id: int, user: dict, **_kwargs):
    from app.dao.plan_dao import PlanDAO
    return {"deleted": PlanDAO.delete(plan_id, str((user or {}).get("sub") or ""))}


def _cancel_report(record_id: int, user: dict, guest_hash: Optional[str] = None, **_kwargs):
    from app.services.assessment_service import AssessmentService
    return AssessmentService.cancel(record_id, user, guest_hash)


def _delete_export(object_name: str, **_kwargs):
    from app.utils.minio_client import get_minio
    client = get_minio()
    client.client.remove_object(client.bucket_name, object_name)
    return {"deleted": object_name}


def _send_notification(user_id: str, title: str, content: str = "",
                       ntype: str = "system", link: Optional[str] = None):
    from app.dao.notification_dao import NotificationDAO
    row = NotificationDAO.create(user_id, title, content, ntype, link)
    return {"notification_id": row.id, "user_id": user_id}


def _delete_notification(notification_id: int, user_id: str, **_kwargs):
    from app.dao.notification_dao import NotificationDAO
    return {"deleted": NotificationDAO.delete_owned(user_id, notification_id)}


def _assessment_records(user: dict, status: Optional[str] = None, limit: int = 20):
    from app.services.assessment_service import AssessmentService
    return AssessmentService.list_mine(user, status=status, limit=limit)


def _risk_calculate(user: dict):
    from app.services.dashboard_service import dashboard_service
    return dashboard_service.risks(user)


def _sql_validate(sql: str):
    from app.services.analytics_service import _assert_safe_sql
    _assert_safe_sql(sql)
    return {"valid": True}


def _permission_check(user: dict):
    return {"allowed": bool(user and user.get("sub")), "role": (user or {}).get("role", "user")}


def _risk_threshold_read():
    from app.core import rules
    return {"thresholds": rules.ALERT_THRESHOLDS}


def _conversation_read(user: dict, consultation_id: int, limit: int = 200):
    from app.services.consultation_service import consultation_service
    return consultation_service.messages(user, consultation_id, limit)


def _answer_safety_check(answer: str = ""):
    from app.core.refusal import check_refusal
    refused, reason = check_refusal(answer or "")
    return {"safe": not refused, "reason": reason if refused else ""}


def bind_default_handlers() -> None:
    global _BOUND
    if _BOUND:
        return
    from app.services.dashboard_service import dashboard_service
    from app.services.checkin_service import checkin_service
    from app.services.analytics_service import analytics_service
    from app.services.chat_query_service import chat_query_service
    from app.services import intent_service
    from app.services.consultation_service import consultation_service
    from app.services.notification_service import notification_service

    register_handler("health_profile.read", dashboard_service.health_context)
    register_handler("assessment_record.read", _assessment_records)
    register_handler("health_risk.calculate", _risk_calculate)
    register_handler("health_context.read", dashboard_service.health_context)
    register_handler("user_restrictions.read", dashboard_service.health_context)
    register_handler("diet_plan.generate", _diet_plan)
    register_handler("diet_plan.publish", _publish_diet_plan)
    register_handler("recipe.search", _dish_search)
    register_handler("recipe_library.search", _dish_search)
    register_handler("menu.export", _menu_export)
    register_handler("food_knowledge.search", chat_query_service.retrieve)
    register_handler("allergy.check", dashboard_service.health_context)
    register_handler("checkin.read", checkin_service.stats)
    register_handler("plan_progress.read", checkin_service.stats)
    register_handler("risk_threshold.read", _risk_threshold_read)
    register_handler("sql.execute_readonly", analytics_service.query)
    register_handler("knowledge.search", chat_query_service.retrieve)
    register_handler("llm.generate", chat_query_service.complete)
    register_handler("conversation.read", _conversation_read)
    register_handler("answer.safety_check", _answer_safety_check)
    register_handler("intent.classify", intent_service.classify)
    register_handler("agent.delegate", _delegate)
    register_handler("agent.aggregate", _aggregate)
    register_handler("human_consultation.create", consultation_service.create)
    register_handler("notification.send", _send_notification)
    register_handler("data.export", _export_analysis)
    register_handler("sql.validate", _sql_validate)
    register_handler("data_permission.check", _permission_check)
    register_handler("metric_catalog.read", analytics_service.catalog)
    register_handler("health_report.publish", _publish_report)
    register_compensation("health_report.publish", _cancel_report)
    register_compensation("diet_plan.publish", _delete_plan)
    register_compensation("data.export", _delete_export)
    register_compensation("notification.send", _delete_notification)
    for agent_key in ("assessment", "diet", "dish", "steward", "dietitian", "data"):
        register_handler(f"{agent_key}.execute", _graph_executor(agent_key))
    _BOUND = True
