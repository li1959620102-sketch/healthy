# -*- coding: utf-8 -*-
"""运行 Agent 治理门禁并输出可保存的 JSON 证据。

用法：
  python scripts/verify_agent_governance.py --allow-pending-infra
  python scripts/verify_agent_governance.py --output agent-governance.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 治理能力验收")
    parser.add_argument("--allow-pending-infra", action="store_true",
                        help="只允许基础设施演练处于 pending；工具绑定、预算等治理项仍必须通过")
    parser.add_argument("--output", type=Path, help="将 JSON 证据写入文件")
    parser.add_argument("--infra-evidence", type=Path,
                        help="run_agent_infrastructure_drill.py 生成的 JSON 证据")
    parser.add_argument("--http-evidence", type=Path,
                        help="run_agent_http_smoke.py 生成的 JSON 证据")
    args = parser.parse_args()

    if args.infra_evidence:
        os.environ["AGENT_INFRA_DRILL_EVIDENCE"] = str(args.infra_evidence)
    if args.http_evidence:
        os.environ["AGENT_HTTP_SMOKE_EVIDENCE"] = str(args.http_evidence)

    from app.agent_runtime.evaluation import evaluate_contract, evaluate_fault_recovery
    from app.agent_runtime.readiness import readiness_report

    report = readiness_report()
    report["contract_evaluation"] = evaluate_contract()
    report["fault_recovery"] = asyncio.run(evaluate_fault_recovery())
    blocking = [item for item in report["checks"] if item["status"] == "failed"]
    pending = [item for item in report["checks"] if item["status"] == "pending"]
    allowed_pending = {"infrastructure_drill", "http_smoke"} if args.allow_pending_infra else set()
    blocking_pending = [item for item in pending if item["name"] not in allowed_pending]
    report["governance_gate"] = {
        "passed": not blocking and not blocking_pending,
        "blocking_checks": [item["name"] for item in blocking],
        "blocking_pending_checks": [item["name"] for item in blocking_pending],
        "pending_checks": [item["name"] for item in pending],
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["governance_gate"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
