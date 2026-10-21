# -*- coding: utf-8 -*-
from app.agent_runtime.readiness import readiness_report


def test_readiness_report_distinguishes_pending_infrastructure_drill():
    report = readiness_report("D:/AI_health")
    assert report["status"] == "pending"
    assert report["production_claim_allowed"] is False
    assert any(item["name"] == "sandbox_quota" for item in report["checks"])
    assert any(item["name"] == "infrastructure_drill" and item["status"] == "pending"
               for item in report["checks"])
