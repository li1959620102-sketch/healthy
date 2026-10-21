# -*- coding: utf-8 -*-
import asyncio

from app.agent_runtime.evaluation import evaluate_contract, evaluate_fault_recovery


def test_agent_contract_evaluation_covers_all_agents():
    result = evaluate_contract()
    assert result["total"] == 7
    assert result["failed"] == 0
    assert result["pass_rate"] == 1.0


def test_fault_recovery_evaluation_passes():
    result = asyncio.run(evaluate_fault_recovery())
    assert result["passed"] is True
    assert result["retry"]["attempts"] == 2
