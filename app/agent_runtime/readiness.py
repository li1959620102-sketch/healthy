# -*- coding: utf-8 -*-
"""Agent Runtime 上线前就绪检查。"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List

from app.agent_runtime.evaluation import evaluate_contract
from app.agent_runtime.telemetry import telemetry
from app.agent_runtime.tool_registry import runtime_capabilities


def readiness_report(root: str | None = None) -> Dict[str, Any]:
    # 不把开发机绝对路径写死到生产探针；默认从 app/agent_runtime 向上定位项目根目录。
    root = root or str(Path(__file__).resolve().parents[2])
    checks: List[dict] = []
    contract = evaluate_contract()
    checks.append({
        "name": "agent_contract",
        "status": "passed" if contract["failed"] == 0 else "failed",
        "detail": {"passed": contract["passed"], "total": contract["total"]},
    })

    migration_files = [
        os.path.join(root, "migrations", "009_agent_runtime.sql"),
        os.path.join(root, "migrations", "010_agent_tool_cost.sql"),
    ]
    missing = [path for path in migration_files if not os.path.exists(path)]
    checks.append({
        "name": "runtime_migrations",
        "status": "passed" if not missing else "failed",
        "detail": {"missing": missing},
    })

    agents = ["orchestrator", "assessment", "diet", "dish", "steward", "dietitian", "data"]
    capability_missing = [agent for agent in agents if not runtime_capabilities(agent)["tools"]]
    unbound = {
        agent: [item["name"] for item in runtime_capabilities(agent)["tools"]
                if not item.get("handler_registered")]
        for agent in agents
    }
    unbound = {agent: names for agent, names in unbound.items() if names}
    checks.append({
        "name": "tool_registry",
        "status": "passed" if not capability_missing else "failed",
        "detail": {"missing_agents": capability_missing, "unbound_tools": unbound},
    })
    checks.append({
        "name": "tool_handler_binding",
        "status": "pending" if unbound else "passed",
        "detail": {"unbound_tools": unbound},
    })

    checks.append({
        "name": "telemetry",
        "status": "passed",
        "detail": telemetry.snapshot(),
    })

    budget = os.getenv("AGENT_COST_BUDGET_USD", "").strip()
    checks.append({
        "name": "cost_budget",
        "status": "passed" if budget else "pending",
        "detail": "已配置单任务成本预算" if budget else "未配置 AGENT_COST_BUDGET_USD，成本只统计不拦截",
    })

    sandbox_calls = os.getenv("AGENT_SANDBOX_MAX_TOOL_CALLS", "100").strip()
    sandbox_runtime = os.getenv("AGENT_SANDBOX_MAX_RUNTIME_SECONDS", "900").strip()
    try:
        sandbox_ok = int(sandbox_calls) > 0 and int(sandbox_runtime) > 0
    except ValueError:
        sandbox_ok = False
    checks.append({
        "name": "sandbox_quota",
        "status": "passed" if sandbox_ok else "failed",
        "detail": {
            "max_tool_calls": sandbox_calls,
            "max_runtime_seconds": sandbox_runtime,
        },
    })

    from app.agent_runtime.default_handlers import bind_default_handlers
    from app.agent_runtime.compensation import registered_compensations
    bind_default_handlers()
    compensation_file = os.path.join(root, "app", "agent_runtime", "compensation.py")
    compensation_names = registered_compensations()
    checks.append({
        "name": "compensation_registry",
        "status": "passed" if os.path.exists(compensation_file) and compensation_names else "failed",
        "detail": {"path": compensation_file, "registered": compensation_names},
    })

    evidence_path = os.getenv("AGENT_INFRA_DRILL_EVIDENCE", "").strip()
    drill_status = "pending"
    drill_detail: Any = "需要目标环境执行数据库、向量库和模型服务故障演练"
    if evidence_path and os.path.exists(evidence_path):
        try:
            with open(evidence_path, "r", encoding="utf-8") as handle:
                evidence = json.load(handle)
            if evidence.get("passed") is True:
                drill_status = "passed"
                drill_detail = evidence
            else:
                drill_status = "failed"
                drill_detail = evidence
        except (OSError, ValueError) as exc:
            drill_status = "failed"
            drill_detail = str(exc)
    # 没有证据时不能把真实 MySQL/Milvus/MinIO 演练伪造成通过。
    checks.append({
        "name": "infrastructure_drill",
        "status": drill_status,
        "detail": drill_detail,
    })
    http_evidence_path = os.getenv("AGENT_HTTP_SMOKE_EVIDENCE", "").strip()
    http_status = "pending"
    http_detail: Any = "需要运行中的 API 执行 /api/health 与 /api/v1/ready HTTP 验收"
    if http_evidence_path and os.path.exists(http_evidence_path):
        try:
            with open(http_evidence_path, "r", encoding="utf-8") as handle:
                http_evidence = json.load(handle)
            http_status = "passed" if http_evidence.get("passed") is True else "failed"
            http_detail = http_evidence
        except (OSError, ValueError) as exc:
            http_status = "failed"
            http_detail = str(exc)
    checks.append({"name": "http_smoke", "status": http_status, "detail": http_detail})
    passed = sum(1 for item in checks if item["status"] == "passed")
    return {
        "suite": "agent-runtime-readiness-v1",
        "status": "pending" if any(item["status"] == "pending" for item in checks)
                  else ("passed" if all(item["status"] == "passed" for item in checks) else "failed"),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "production_claim_allowed": False,
        "claim_note": "本报告证明代码、依赖和当前验收环境；真实生产声明仍需目标域名 HTTPS、备份恢复和发布审批证据。",
    }
