# -*- coding: utf-8 -*-
"""单元测试 · 业务规则边界值（对应测试用例集 BV-01/03/04/05/06/07/11/12/18/19）。

这些是纯函数，不连数据库、不起服务，毫秒级跑完。
断言口径来自《AI智参平台_测试_01_测试用例集.md》第四章「边界值用例」的预期结果列。
"""
import pytest

from app.core import rules
from app.core.rules import (
    HEIGHT_MAX_CM, HEIGHT_MIN_CM, PASSWORD_MAX_LEN, PASSWORD_MIN_LEN,
    WEIGHT_MAX_KG, WEIGHT_MIN_KG,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# BV-01 手机号：仅 11 位合法号段通过，其余拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("phone, expected", [
    ("13800000001", True),    # 11 位正常
    ("1380000000", False),    # 10 位
    ("138000000012", False),  # 12 位
    ("", False),              # 空
    ("abcdefghijk", False),   # 字母
    ("23800000001", False),   # 非 1 开头（11 位）
    ("12800000001", False),   # 1 开头但第二位为 2，非法号段
    (None, False),            # 缺省
], ids=["11位正常", "10位", "12位", "空", "字母", "非1开头", "非法号段", "None"])
def test_bv01_phone(phone, expected):
    assert rules.check_phone(phone).ok is expected


# --------------------------------------------------------------------------- #
# BV-02 验证码：仅 6 位数字通过
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code, expected", [
    ("123456", True),
    ("12345", False),      # 5 位
    ("1234567", False),    # 7 位
    ("      ", False),     # 全空格
    ("12 456", False),     # 含空格
    ("abc123", False),     # 含字母
    ("", False),
    (None, False),
], ids=["6位", "5位", "7位", "全空格", "含空格", "含字母", "空", "None"])
def test_bv02_verify_code(code, expected):
    assert rules.check_verify_code(code).ok is expected


def test_bv02_code_is_trimmed_before_check():
    assert rules.check_verify_code(" 123456 ").ok is True


# --------------------------------------------------------------------------- #
# BV-03 密码：8–64 位通过，边界外拒绝并提示长度规则
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pwd, expected", [
    ("a" * 8, True),
    ("a" * 7, False),
    ("a" * PASSWORD_MIN_LEN, True),
    ("a" * PASSWORD_MAX_LEN, True),
    ("a" * (PASSWORD_MAX_LEN + 1), False),   # 65 位
    ("", False),
    (None, False),
], ids=["8位", "7位", "下界", "64位", "65位", "空", "None"])
def test_bv03_password(pwd, expected):
    assert rules.check_password(pwd).ok is expected


def test_bv03_password_error_message_mentions_length():
    """边界外必须给出可读提示，而不是静默失败。"""
    assert "8" in rules.check_password("a" * 7).message
    assert "64" in rules.check_password("a" * 65).message


# --------------------------------------------------------------------------- #
# BV-04 身高：50–250 通过，超出（含 0 / 负数）拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("height, expected", [
    (50, True), (100, True), (250, True),
    (251, False), (0, False), (-1, False),
    (None, True),   # 未填写：不是错误，只是不参与计算
], ids=["50", "100", "250", "251", "0", "-1", "None"])
def test_bv04_height(height, expected):
    assert rules.check_height(height).ok is expected


# --------------------------------------------------------------------------- #
# BV-05 体重：2–300 通过，超出（含 0）拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("weight, expected", [
    (2, True), (20, True), (300, True),
    (301, False), (0, False),
    (None, True),
], ids=["2", "20", "300", "301", "0", "None"])
def test_bv05_weight(weight, expected):
    assert rules.check_weight(weight).ok is expected


# --------------------------------------------------------------------------- #
# BV-06 BMI：身高 0 不得返回 Infinity / NaN
# --------------------------------------------------------------------------- #
def test_bv06_bmi_no_infinity():
    assert rules.calc_bmi(0, 60) is None
    assert rules.calc_bmi(-10, 60) is None
    assert rules.calc_bmi(None, 60) is None
    assert rules.calc_bmi(170, None) is None
    assert rules.calc_bmi("abc", 60) is None


def test_bv06_bmi_normal():
    assert rules.calc_bmi(170, 65) == 22.49
    assert rules.calc_bmi(150, 45) == 20.0


# --------------------------------------------------------------------------- #
# BV-07 年龄：0–120 通过；<18 标注未成年；超范围拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("age, ok, minor", [
    (0, True, True), (1, True, True), (17, True, True),
    (18, True, False), (120, True, False),
    (121, False, False),
], ids=["0", "1", "17", "18", "120", "121"])
def test_bv07_age(age, ok, minor):
    r = rules.check_age(age)
    assert r.ok is ok
    if ok:
        assert rules.is_minor(age) is minor


def test_bv07_minor_flagged_as_warning():
    """未成年要提示特殊人群，但仍可放行（warn 而非 error）。"""
    r = rules.check_age(17)
    assert r.ok and r.level == "warn" and "未成年" in r.message


# --------------------------------------------------------------------------- #
# BV-11 必答项：未选必答题不可提交
# --------------------------------------------------------------------------- #
def test_bv11_required_answer_missing():
    qs = [{"question_id": "q1", "title": "年龄", "required": True}]
    assert rules.check_required_answers(qs, {}).ok is False
    assert rules.check_required_answers(qs, {"q1": None}).ok is False
    assert rules.check_required_answers(qs, {"q1": ""}).ok is False
    assert rules.check_required_answers(qs, {"q1": []}).ok is False
    assert rules.check_required_answers(qs, {"q1": 3}).ok is True


def test_bv11_optional_answer_may_be_empty():
    qs = [{"question_id": "q1", "required": False}]
    assert rules.check_required_answers(qs, {}).ok is True


# --------------------------------------------------------------------------- #
# BV-12 答案文本：0 字可提交；超长拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text, expected", [
    ("", True),            # 0 字（非必填）
    ("好", True),          # 1 字
    ("字" * 500, True),     # 上限
    ("字" * 501, False),    # 超限
    (None, True),
], ids=["0字", "1字", "500字", "501字", "None"])
def test_bv12_answer_text(text, expected):
    assert rules.check_answer_text(text).ok is expected


# --------------------------------------------------------------------------- #
# BV-18 维度权重：合计必须为 1；单项 0 允许但提示
# --------------------------------------------------------------------------- #
def test_bv18_weight_sum_must_be_one():
    assert rules.check_weights([0.5, 0.5]).ok is True
    assert rules.check_weights([1.0]).ok is True
    assert rules.check_weights([0.5, 0.501]).ok is False      # 合计 1.001
    assert rules.check_weights([0.5, 0.499]).ok is False      # 合计 0.999


def test_bv18_zero_weight_allowed_with_warning():
    r = rules.check_weights([1.0, 0.0])
    assert r.ok is True and r.level == "warn"


def test_bv18_negative_weight_rejected():
    assert rules.check_weights([1.2, -0.2]).ok is False
    assert rules.check_weights([]).ok is False


# --------------------------------------------------------------------------- #
# BV-19 分页：page<1 归 1；size 上限 100；超范围不报错
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("page, size, expect", [
    (0, 10, (1, 10)),        # page=0 归为 1
    (1, 10, (1, 10)),
    (-5, 10, (1, 10)),       # 负数同样归 1
    (1, 0, (1, 1)),          # size=0 归 1
    (1, 1, (1, 1)),
    (1, 100, (1, 100)),      # 上限内保留
    (1, 101, (1, 100)),      # 超上限截断
    (9999, 101, (9999, 100)),  # 超范围页码不报错，交给 DAO 返回空列表
    (None, None, (1, 20)),   # 缺省
], ids=["page0", "page1", "page负数", "size0", "size1", "size100", "size101", "超范围页", "缺省"])
def test_bv19_paging(page, size, expect):
    assert rules.normalize_paging(page, size) == expect


def test_bv19_offset():
    assert rules.offset_of(3, 10) == (3, 10, 20)


# --------------------------------------------------------------------------- #
# 契约常量快照：防止后续有人把阈值改回去
# --------------------------------------------------------------------------- #
def test_contract_constants_snapshot():
    assert (PASSWORD_MIN_LEN, PASSWORD_MAX_LEN) == (8, 64)
    assert (HEIGHT_MIN_CM, HEIGHT_MAX_CM) == (50, 250)
    assert (WEIGHT_MIN_KG, WEIGHT_MAX_KG) == (2.0, 300.0)
    assert rules.VERIFY_CODE_LEN == 6
    assert rules.PAGE_SIZE_MAX == 100
    assert rules.IDEMPOTENCY_WINDOW_SECONDS == 30
    assert rules.DRAFT_TTL_DAYS == 30
    assert rules.CONSULTATION_TIMEOUT_MINUTES == 30
    assert rules.CLAIM_WINDOW_DAYS == 7
