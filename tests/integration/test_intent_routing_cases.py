# -*- coding: utf-8 -*-
"""主智能体调度中枢用例：意图识别 → 规划 → 分派 → 汇总。

**这个文件锁的是"确定性"与"合规闸门"，不是"准确率"。**
理由见 `tests/integration/test_intent_routing_cases.py` 顶部与评测脚本的实际数字：
调参集 100%、留出集 57~68%。把 100% 写成断言就是自己骗自己。

真正值得回归的是三件不会随词表调整而变的事：
  1. **确定性**：同一问句永远同一个路由（不依赖模型、不依赖时钟、不依赖随机数）；
  2. **高危优先于一切**：命中高危时 plan 为空、steps 为空，一次子智能体都不调用；
  3. **不伪造**：子通道失败如实上报，汇总不做模型二次生成。
"""
from __future__ import annotations

import pytest

from app.core import rules
from app.services import intent_service, orchestrator_service

API = "/api/v1"
pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 1. 确定性
# --------------------------------------------------------------------------- #
def test_classification_is_deterministic():
    """同一问句重复识别 N 次结果必须逐字段一致。

    这是选规则分类而不是 LLM 分类的**首要理由**：路由层最怕"同一句话这次走 A 那个走 B"，
    用户会觉得这系统一会儿明白一会儿不懂。
    """
    q = "我想控糖，给我排一周食谱，再提醒我每天打卡"
    first = intent_service.classify(q)
    for _ in range(20):
        r = intent_service.classify(q)
        assert r["primary"] == first["primary"]
        assert r["blocked"] == first["blocked"]
        assert r["confidence"] == first["confidence"]
        assert [x["intent"] for x in r["ranked"]] == [x["intent"] for x in first["ranked"]]


def test_classification_does_not_touch_model(monkeypatch):
    """意图识别不得调用大模型——一旦有人图省事改成 LLM 分类，这条用例立刻炸。"""
    from app.services import chat_query_service as cqs

    async def _boom(*a, **k):                        # pragma: no cover
        raise AssertionError("意图识别不应调用大模型")

    monkeypatch.setattr(cqs.chat_query_service, "_llm", object())
    monkeypatch.setattr(cqs.chat_query_service, "complete", _boom)
    r = intent_service.classify("高血压患者一周食谱怎么安排")
    assert r["primary"] == "diet"


# --------------------------------------------------------------------------- #
# 2. 高危优先于一切（合规闸门）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "我该吃多少降压药",
    "这个药能不能停",
    "我是不是得了糖尿病",
    "帮我看看这个化验单",
    "孕妇能吃山楂吗",
    "孩子反复发烧要去查什么科",
])
def test_high_risk_is_blocked_before_routing(text):
    """四类高危 + 就医引导，必须在**分类阶段**就拦下。

    注意：这些断言只覆盖"已被词表覆盖到的说法"。留出集显示真实用户表述的覆盖率远没到 100%
    （见 test_eval_gate_guards_blocked_recall 的说明），所以生产上不能只靠这一层。
    """
    r = intent_service.classify(text)
    assert r["blocked"] is True, f"{text!r} 未被拦截，reason={r}"
    assert r["refuse_reason"], r
    assert r["refuse_text"], "拦截后必须给用户明确话术，不能静默返回"
    assert r["ranked"] == [], "高危问题不应产生任何意图排名"


@pytest.mark.parametrize("text", [
    "我该吃多少降压药",
    "孕妇能吃山楂吗",
])
def test_blocked_query_produces_empty_plan(text):
    """高危 → 规划为空，一次子智能体都不该被调用。"""
    r = intent_service.classify(text)
    assert intent_service.plan_agents(r) == []


def test_blocked_query_does_not_call_any_subagent(api):
    """端到端验证：高危输入下 steps 必须为空，且提示没进检索与模型。"""
    tok = api.synthetic("orch_blk", "user")
    body = api.c.post(f"{API}/agents/orchestrate", headers=tok,
                      json={"text": "我该吃多少降压药"}).json()
    assert body.get("code") == 0, body
    d = body["data"]
    assert d["intent"]["blocked"] is True
    assert d["plan"] == []
    assert d["steps"] == [], "高危问题竟然调用了子智能体"
    assert d["executed"] is False
    assert d["result"]["references"] == []
    assert "咨询" in d["result"]["summary"] or "就医" in d["result"]["summary"], d


# --------------------------------------------------------------------------- #
# 3. 规划：按业务链路顺序，不按分数堆顺序
# --------------------------------------------------------------------------- #
def test_plan_follows_business_chain_order():
    """多意图时执行顺序必须是业务链路顺序（先评估 → 再方案 → 再提醒…），不是按分数排。

    分数只能决定"谁做主"，决定不了"谁先做"。
    """
    r = intent_service.classify("高血压患者一周食谱怎么安排")
    plan = intent_service.plan_agents(r)
    orders = [rules.INTENT_TO_AGENT[a["agent"]] for a in plan]
    by_rule = {x["key"]: x["order"] for x in rules.INTENT_RULES}
    seq = [by_rule[a] for a in orders if a in by_rule]
    assert seq == sorted(seq), f"执行顺序 {seq} 与链路顺序不符"


def test_plan_respects_max_agents():
    r = intent_service.classify("做个食谱，顺便推荐两道下饭菜，顺便提醒打卡，再统计占比")
    plan = intent_service.plan_agents(r, max_agents=2)
    assert len(plan) <= 2, plan


def test_low_confidence_falls_back_instead_of_guessing():
    """置信度不足时必须走兜底并**如实标注**，不能硬猜一个意图。"""
    r = intent_service.classify("这个菜多少热量")   # 只命中弱词「多少」
    assert r["low_confidence"] is True
    assert r["fallback"] is True
    assert r["primary"] == rules.INTENT_FALLBACK
    assert "置信度不足" in intent_service.explain(r)


def test_empty_input_is_safe():
    """空输入不能崩，也不能把「意图不明」和「识别失败」混成同一个状态。"""
    r = intent_service.classify("")
    assert r["primary"] == rules.INTENT_FALLBACK
    assert r["fallback"] is True
    assert r["blocked"] is False


# --------------------------------------------------------------------------- #
# 4. 不伪造
# --------------------------------------------------------------------------- #
def test_aggregate_reports_failure_instead_of_fabricating(api):
    """任一子通道失败，汇总里必须能看到失败，不允许"看起来都成功了"。"""
    steps = [
        {"agent": "dish", "ok": True, "degraded": False, "summary": "3 道菜",
         "data": {"references": [{"doc_id": 1, "source": "a.md"}]},
         "elapsed_ms": 10, "note": ""},
        {"agent": "data", "ok": False, "degraded": True, "summary": "",
         "data": None, "elapsed_ms": 5, "note": "业务拒绝 40001：暂不支持该问法"},
    ]
    agg = orchestrator_service._aggregate(steps)
    assert agg["ok_count"] == 1
    assert agg["failed_count"] == 1
    assert "失败" in agg["summary"]
    assert len(agg["references"]) == 1


def test_aggregate_dedupes_references(api):
    """多个通道命中同一知识条目时引用去重，不重复展示。"""
    ref = {"doc_id": 7, "source": "慢病食养.md"}
    steps = [
        {"agent": "dietitian", "ok": True, "degraded": False, "summary": "a",
         "data": {"references": [dict(ref)]}, "elapsed_ms": 1, "note": ""},
        {"agent": "dish", "ok": True, "degraded": False, "summary": "b",
         "data": {"references": [dict(ref)]}, "elapsed_ms": 1, "note": ""},
    ]
    assert len(orchestrator_service._aggregate(steps)["references"]) == 1


def test_summary_is_not_model_generated(api):
    """汇总必须声明自己不是模型二次生成的——这条约束要能被看到，不能只写在注释里。"""
    agg = orchestrator_service._aggregate([])
    assert "结构化拼接" in agg["citations_note"]


def test_subagent_failure_is_caught_not_swallowed():
    """子通道抛异常时不能把异常吞掉后返回一个"看起来正常"的结果。"""
    import asyncio

    async def _boom(text, user):
        raise RuntimeError("模拟子通道故障")

    orig = orchestrator_service.EXECUTORS["dish"]
    orchestrator_service.EXECUTORS["dish"] = _boom
    try:
        # 用 asyncio.run 而不是 get_event_loop().run_until_complete()：
        # 全量回归里前面的用例可能已把当前线程的事件循环关掉，
        # get_event_loop() 在 Python 3.11 会直接抛 RuntimeError（单跑能过、全跑挂）。
        step = asyncio.run(orchestrator_service._run_step("dish", "推荐下饭菜", None))
    finally:
        orchestrator_service.EXECUTORS["dish"] = orig
    assert step["ok"] is False
    assert "执行失败" in step["note"]


# --------------------------------------------------------------------------- #
# 5. 评测门禁本身要被回归
# --------------------------------------------------------------------------- #
def test_eval_gate_fails_when_blocked_recall_below_full():
    """门禁逻辑本身要有用例——否则改了阈值没人知道。"""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_eval_intent", Path(rules.__file__).resolve().parents[2] / "scripts" / "eval_intent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    r = mod.evaluate()
    # 调参集上高危必须 100%：这是硬门禁，不参与加权
    assert r["blocked_recall"] == 1.0, f"调参集高危召回掉到 {r['blocked_recall']}"
    # 留出集的真实水平记在这里，供文档与对外口径引用
    h2 = mod.evaluate(mod.HOLDOUT2)
    assert h2["total"] > 0
    print(f"\n[评测] 调参集 准确率 {r['accuracy']:.1%} / 留出集2 准确率 {h2['accuracy']:.1%}"
          f" / 留出集2 高危召回 {h2['blocked_recall']:.1%}")
