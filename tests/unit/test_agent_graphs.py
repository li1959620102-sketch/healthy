# -*- coding: utf-8 -*-
"""LangGraph 智能体图 · 单元测试。

覆盖四类**不依赖外部服务**的性质（不需要 MySQL / Milvus / Ollama）：

  · 图能编译：六个子图 + 主图，节点/边齐全；
  · 路由正确：高危不进链路、预览不执行、空方案不卡死；
  · 边界收敛：查询改写的填充词剥离；
  · 状态契约：并行分支的 step 只累加不覆盖（reducer）。

真正的端到端执行（要调模型/向量库）放在 integration 里，这里只保证
"图的结构和决策逻辑"是对的——这部分最容易被改坏，也最便宜测。
"""
import asyncio

import pytest

from app.agents import supervisor as sup
from app.agents.aggregate import aggregate_steps
from app.agents.state import merge_steps
from app.agents.subgraphs import get_subgraph, refine_query, subgraph_names
from app.services import orchestrator_service


# --------------------------------------------------------------------------- #
# 1. 图结构
# --------------------------------------------------------------------------- #
def test_all_subgraphs_compile():
    """六个子智能体都必须能编译成可执行图。"""
    names = subgraph_names()
    assert set(names) == {"assessment", "diet", "dish", "steward", "data", "dietitian"}
    for name in names:
        graph = get_subgraph(name)
        assert graph is not None, f"{name} 子图未编译"


def test_supervisor_has_expected_nodes():
    """主图必须同时挂上 6 个子图节点和 3 个终态节点。"""
    g = sup.get_supervisor_graph().get_graph()
    nodes = set(g.nodes.keys())
    for name in subgraph_names():
        assert name in nodes, f"主图缺少子智能体节点 {name}"
    for name in ("refuse", "preview", "aggregate"):
        assert name in nodes, f"主图缺少 {name} 节点"


def test_subgraph_is_singleton():
    """同一进程内重复取图应返回同一实例（避免每次请求都重新编译）。"""
    assert get_subgraph("diet") is get_subgraph("diet")


# --------------------------------------------------------------------------- #
# 2. 路由
# --------------------------------------------------------------------------- #
def test_blocked_question_never_executes_subagents():
    """高危问题：一次子智能体都不调用，steps 必须为空。"""
    out = asyncio.run(orchestrator_service.orchestrate(
        "帮我解读一下我的化验单，是不是肝癌", None
    ))
    assert out["intent"]["blocked"] is True
    assert out["steps"] == []
    assert out["executed"] is False
    assert out["result"]["ok_count"] == 0
    # 拒答文本必须真的给出来了，不能只是 blocked=true 却空着
    assert out["result"]["summary"]


def test_preview_mode_does_not_execute():
    """execute=False：只出方案，不跑任何子智能体。"""
    out = asyncio.run(orchestrator_service.orchestrate(
        "帮我看看这周打卡情况", None, execute=False
    ))
    assert out["executed"] is False
    assert out["steps"] == []
    assert out["result"]["citations_note"] == "execute=false，未执行"


def test_plan_is_ordered_and_bounded():
    """调度方案要有序、且不超过 max_agents。

    注意 `plan` 项里**没有** order 字段——`plan_agents()` 已经按链路顺序排好，
    order 只存在于分类器的 ranked 里。这里断言的是"输出已有序"这个结果，
    而不是去读一个不存在的字段。
    """
    out = asyncio.run(orchestrator_service.orchestrate(
        "帮我做健康评估然后给食谱再推荐菜品，最后看看打卡", None, execute=False
    ))
    plan = out["plan"]
    assert plan, "正常业务问题不应得到空方案"
    assert len(plan) <= 3

    agents = [p["agent"] for p in plan]
    assert len(agents) == len(set(agents)), f"方案里出现重复子智能体：{agents}"

    known = set(subgraph_names())
    assert set(agents) <= known, f"方案里有未注册的子智能体：{set(agents) - known}"

    # 有序性：plan 按**链路顺序**排列（评估→方案→菜品→跟踪→答疑→统计），
    # 不是按打分高低。这一点很容易被误改成"按置信度排"——那样用户问
    # "先评估再出食谱"，可能因为 dish 打分更高就先推菜、后给方案，逻辑就反了。
    from app.core.rules import INTENT_RULES

    order_of = {r["key"]: r["order"] for r in INTENT_RULES}
    seq = [order_of[p["intent"]] for p in plan if p["intent"] in order_of]
    assert seq == sorted(seq), f"方案未按链路顺序排列：{agents}"


def test_engine_is_langgraph():
    """对外结构里要能看出执行引擎，便于线上排查走的哪套实现。"""
    out = asyncio.run(orchestrator_service.orchestrate(
        "帮我看看这周打卡情况", None, execute=False
    ))
    assert out["engine"] == "langgraph-supervisor"


# --------------------------------------------------------------------------- #
# 3. 查询改写（self-RAG 的回边）
# --------------------------------------------------------------------------- #
def test_refine_query_strips_fillers():
    """口语填充词应被剥离，实体词保留。"""
    q = refine_query("高血压的人平时吃什么比较好")
    assert "高血压" in q
    assert "平时" not in q
    assert "比较" not in q


def test_refine_query_never_returns_empty():
    """整句都是填充词时退回原句，不能造出空查询。"""
    assert refine_query("怎么办呢") != ""
    assert refine_query("") == ""


def test_refine_query_keeps_unknown_domain():
    """改写不能把专业性强的词弄丢（否则重检只会越检越偏）。"""
    q = refine_query("糖尿病肾病的人能不能吃高钾的食物")
    assert "糖尿病" in q and "肾病" in q


# --------------------------------------------------------------------------- #
# 4. 状态契约
# --------------------------------------------------------------------------- #
def test_merge_steps_accumulates():
    """并行分支写 steps 必须累加，不能后到的覆盖先到的。"""
    a = [{"agent": "diet", "ok": True}]
    b = [{"agent": "dish", "ok": True}]
    merged = merge_steps(a, b)
    assert [s["agent"] for s in merged] == ["diet", "dish"]
    assert merge_steps(None, b) == b
    assert merge_steps(a, None) == a


def test_aggregate_dedups_references():
    """同一 doc_id 被多个通道命中时，引用只保留一份。"""
    steps = [
        {"agent": "dietitian", "ok": True, "data": {"references": [{"doc_id": "1"}]}},
        {"agent": "dish", "ok": True, "data": {"references": [{"doc_id": "1"}, {"doc_id": "2"}]}},
    ]
    agg = aggregate_steps(steps)
    assert len(agg["references"]) == 2
    assert agg["ok_count"] == 2


def test_aggregate_reports_failures_honestly():
    """有通道失败时摘要必须点出是谁失败，不能含糊成"已完成"。"""
    steps = [
        {"agent": "diet", "ok": True, "data": {}},
        {"agent": "data", "ok": False, "note": "执行失败：未登录"},
    ]
    agg = aggregate_steps(steps)
    assert agg["failed_count"] == 1
    assert "data" in agg["summary"]


def test_aggregate_handles_all_failed():
    """全失败也要给摘要，不能返回空字符串让前端无从展示。"""
    agg = aggregate_steps([{"agent": "data", "ok": False}])
    assert agg["ok_count"] == 0
    assert agg["summary"]


def test_aggregate_empty_steps():
    """空步骤（方案为空）也要有可读结论。"""
    assert aggregate_steps([])["summary"]


# --------------------------------------------------------------------------- #
# 5. 子图 step 契约
# --------------------------------------------------------------------------- #
def test_diet_subgraph_returns_step_contract():
    """子图对外只能交出一条 step，字段必须齐全（主图与前端都依赖它）。"""
    out = asyncio.run(get_subgraph("diet").ainvoke({"text": "给我一份食谱", "user": None}))
    step = out.get("step")
    assert step is not None
    for key in ("agent", "ok", "degraded", "summary", "data", "elapsed_ms", "note"):
        assert key in step, f"step 缺少字段 {key}"
    assert step["agent"] == "diet"
    assert step["ok"] is True
