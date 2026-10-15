# -*- coding: utf-8 -*-
"""单元测试 · 评分引擎边界值（对应测试用例集 BV-15/16/17/18）。

技术红线验证：分数完全由规则计算，不经过任何模型调用。

为了保证可重复（不受 system_config 里 C1 权重数值影响），
本文件把 `scoring_engine._read_config` 打桩为返回 None，
强制走「模板自带权重 → 等权」与「内置默认阈值」两条回退链。
"""
import pytest

from app.core import rules
from app.services import scoring_engine as se_module
from app.services.scoring_engine import DEFAULT_THRESHOLDS, ScoringEngine

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 造数工具
# --------------------------------------------------------------------------- #
class FakeTemplate:
    """最小模板替身：只需 dimension_config / question_config / code 三个属性。"""

    def __init__(self, dims, questions, code="TPL_TEST"):
        self.dimension_config = dims
        self.question_config = questions
        self.code = code


# 选项分档：A=4（最高）… E=0（最低）
OPTIONS = [
    {"value": "A", "score": 4},
    {"value": "B", "score": 3},
    {"value": "C", "score": 2},
    {"value": "D", "score": 1},
    {"value": "E", "score": 0},
]


def build_template(dims, per_dim=2):
    """每个维度生成 per_dim 道单选，每题满分 4（该维度满分 = per_dim × 4）。"""
    questions = []
    for d in dims:
        for i in range(per_dim):
            questions.append({
                "question_id": f"{d['key']}_{i}",
                "dimension": d["key"],
                "type": "single",
                "max_score": 4,
                "options": OPTIONS,
            })
    return FakeTemplate(dims, questions)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    """隔离 system_config：避免 C1 数值上线后单元测试结果漂移。"""
    monkeypatch.setattr(se_module, "_read_config", lambda key: None)
    yield


# --------------------------------------------------------------------------- #
# BV-15 总分边界：全选最低 = 0，全选最高 = 100，不得越界
# --------------------------------------------------------------------------- #
def test_bv15_all_lowest_is_zero():
    dims = [{"key": "basic", "name": "基础"}, {"key": "diet", "name": "饮食"}]
    tpl = build_template(dims)
    answers = {q["question_id"]: "E" for q in tpl.question_config}
    result = ScoringEngine.score(tpl, answers)
    assert result["score"] == 0.0
    assert result["level"] == "needs_improvement"


def test_bv15_all_highest_is_hundred():
    dims = [{"key": "basic", "name": "基础"}, {"key": "diet", "name": "饮食"}]
    tpl = build_template(dims)
    answers = {q["question_id"]: "A" for q in tpl.question_config}
    result = ScoringEngine.score(tpl, answers)
    assert result["score"] == 100.0
    assert result["level"] == "excellent"


@pytest.mark.parametrize("weight_a, weight_b", [
    (1.0, 0.0), (0.0, 1.0), (0.5, 0.5),
], ids=["权A", "权B", "等权"])
def test_bv15_score_never_out_of_range(weight_a, weight_b):
    """任意权重组合下总分都被钳制在 0–100。"""
    dims = [{"key": "a", "name": "A", "weight": weight_a},
            {"key": "b", "name": "B", "weight": weight_b}]
    tpl = build_template(dims)
    for pick in ("A", "E"):
        answers = {q["question_id"]: pick for q in tpl.question_config}
        assert 0.0 <= ScoringEngine.score(tpl, answers)["score"] <= 100.0


# --------------------------------------------------------------------------- #
# BV-16 等级阈值：恰好等于分界线归属明确，且只匹配一档
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("score, expect_level", [
    (100.0, "excellent"),
    (80.0, "excellent"),     # 恰好分界：属「优秀」（≥）
    (79.99, "good"),
    (60.0, "good"),          # 恰好分界：属「良好」（≥）
    (59.99, "needs_improvement"),
    (0.0, "needs_improvement"),
], ids=["100", "80分界", "79.99", "60分界", "59.99", "0"])
def test_bv16_level_boundary_is_single_valued(score, expect_level):
    lvl = ScoringEngine.level_of(score, DEFAULT_THRESHOLDS)
    assert lvl["level"] == expect_level


def test_bv16_thresholds_are_ordered_and_non_overlapping():
    """阈值表必须降序且档位唯一，否则同一分数会命中多档。"""
    mins = [t["min"] for t in DEFAULT_THRESHOLDS]
    assert mins == sorted(mins, reverse=True)
    levels = [t["level"] for t in DEFAULT_THRESHOLDS]
    assert len(levels) == len(set(levels))


def test_bv16_level_is_deterministic():
    assert ScoringEngine.level_of(60.0, DEFAULT_THRESHOLDS) == \
           ScoringEngine.level_of(60.0, DEFAULT_THRESHOLDS)


# --------------------------------------------------------------------------- #
# BV-17 加权和：各维度得分加权求和，误差 ≤ 0.01
# --------------------------------------------------------------------------- #
def test_bv17_weighted_sum_matches_manual_calc():
    dims = [{"key": "basic", "name": "基础", "weight": 0.4},
            {"key": "diet", "name": "饮食", "weight": 0.6}]
    tpl = build_template(dims)
    # basic：一题满分 4 分得 A(4)、另一题得 E(0) → 4/8 = 50 分
    # diet ：两题全选 A → 8/8 = 100 分
    answers = {
        "basic_0": "A", "basic_1": "E",
        "diet_0": "A", "diet_1": "A",
    }
    result = ScoringEngine.score(tpl, answers)
    dim_map = {d["key"]: d["score"] for d in result["dimension_scores"]}
    assert dim_map == {"basic": 50.0, "diet": 100.0}

    manual = 50.0 * 0.4 + 100.0 * 0.6
    assert abs(result["score"] - manual) <= 0.01
    assert result["dimension_scores"][0]["weight"] == 0.4
    assert result["dimension_scores"][1]["weight"] == 0.6


def test_bv17_weights_are_normalized_to_one():
    dims = [{"key": "a", "name": "A", "weight": 2},
            {"key": "b", "name": "B", "weight": 6}]
    tpl = build_template(dims)
    result = ScoringEngine.score(tpl, {})
    total_weight = sum(d["weight"] for d in result["dimension_scores"])
    assert abs(total_weight - 1.0) <= 0.01


# --------------------------------------------------------------------------- #
# BV-18 权重合计：模板声明不合规时给出告警（不阻断评分）
# --------------------------------------------------------------------------- #
def test_bv18_bad_weight_sum_produces_warning():
    dims = [{"key": "a", "name": "A", "weight": 0.4},
            {"key": "b", "name": "B", "weight": 0.5}]   # 合计 0.9
    tpl = build_template(dims)
    result = ScoringEngine.score(tpl, {})
    assert result["config_warnings"]
    assert "1" in result["config_warnings"][0]


def test_bv18_good_weight_sum_has_no_warning():
    dims = [{"key": "a", "name": "A", "weight": 0.4},
            {"key": "b", "name": "B", "weight": 0.6}]
    tpl = build_template(dims)
    assert ScoringEngine.score(tpl, {})["config_warnings"] == []


def test_bv18_zero_weight_dimension_warns():
    dims = [{"key": "a", "name": "A", "weight": 1.0},
            {"key": "b", "name": "B", "weight": 0.0}]
    tpl = build_template(dims)
    warnings = ScoringEngine.score(tpl, {})["config_warnings"]
    assert warnings and "0" in warnings[0]


# --------------------------------------------------------------------------- #
# 反作弊：维度声明与题目归属不一致时不得污染总分
# --------------------------------------------------------------------------- #
def test_unknown_dimension_question_is_ignored():
    dims = [{"key": "a", "name": "A"}]
    tpl = build_template(dims)
    tpl.question_config.append({
        "question_id": "ghost", "dimension": "not_exists", "type": "single",
        "max_score": 100, "options": OPTIONS,
    })
    result = ScoringEngine.score(tpl, {"a_0": "A", "a_1": "A", "ghost": "A"})
    assert result["score"] == 100.0


def test_missing_dimension_config_raises():
    from app.core.errors import BizError
    tpl = FakeTemplate([], [])
    with pytest.raises(BizError):
        ScoringEngine.score(tpl, {})


def test_rules_and_engine_share_weight_tolerance():
    """权重容差只应有一处定义（rules），引擎通过 rules 判断。"""
    assert rules.WEIGHT_SUM_TOLERANCE == 1e-6
