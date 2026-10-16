# -*- coding: utf-8 -*-
"""业务规则单一事实源（第一步 · 后端编码）。

把「手机号 / 密码 / 身高 / 体重 / 年龄 / BMI / 答案文本 / 分页 / 维度权重」等**可判定规则**
集中到一处纯函数，不依赖数据库与 Web 框架，因此单元测试可直接覆盖。

对应测试用例集边界值：
    BV-01 手机号 · BV-03 密码 · BV-04 身高 · BV-05 体重 · BV-06 BMI 除零
    BV-07 年龄 · BV-11 必答项 · BV-12 答案文本 · BV-18 维度权重 · BV-19 分页

统一返回 `CheckResult`：
    ok      是否通过
    level   "ok" | "warn" | "error"（warn 表示可放行但要提示）
    message 面向用户的中文提示，可直接回传前端
    value   归一化后的值（分页归一化、BMI 等使用）

改规则只改本文件的常量，不要再把数字散落到路由 / schema / service 里。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 常量（规则的唯一出处）
# --------------------------------------------------------------------------- #
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")   # 11 位，1 开头，第二位 3-9
PASSWORD_MIN_LEN, PASSWORD_MAX_LEN = 8, 64
HEIGHT_MIN_CM, HEIGHT_MAX_CM = 50, 250
WEIGHT_MIN_KG, WEIGHT_MAX_KG = 2.0, 300.0
AGE_MIN, AGE_MAX, ADULT_AGE = 0, 120, 18
VERIFY_CODE_LEN = 6
ANSWER_TEXT_MAX = 500
PAGE_SIZE_MAX = 100
WEIGHT_SUM_TOLERANCE = 1e-6

# 业务时效（秒/天/分钟），供各 service 引用，避免散落魔法数字
IDEMPOTENCY_WINDOW_SECONDS = 30      # BV-23 幂等窗口：30s 内同键幂等
DRAFT_TTL_DAYS = 30                  # BV-24 草稿有效期
CONSULTATION_TIMEOUT_MINUTES = 30    # BV-25 咨询超时
DELETION_ALERT_DAYS = 15             # BV-26 注销清理期限（第 16 日未完成告警）
CLAIM_WINDOW_DAYS = 7                # BV-27 游客认领期限
GUEST_ASSESSMENT_QUOTA = 1           # BV-21 游客测评次数上限
GUEST_QUERY_QUOTA = 5                # BV-21 游客查询次数上限

# --------------------------------------------------------------------------- #
# 运营预警阈值（需求 FR-V-10 / 数据字典 system_config）
# 落库键 `alert.thresholds`（JSON），管理员可在管理后台调整；短信/邮件通知待网关接入。
# 每项：key -> {name 中文名, default 默认值, unit 单位, min/max 合法区间, desc 说明}
# --------------------------------------------------------------------------- #
ALERT_CONFIG_KEY = "alert.thresholds"

ALERT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "dau_drop_pct": {
        "name": "日活环比下降", "default": 10, "unit": "%", "min": 1, "max": 100,
        "desc": "日活（DAU）连续 3 日环比下降超过该比例时触发预警",
    },
    "score_low_ratio": {
        "name": "低分报告占比", "default": 30, "unit": "%", "min": 1, "max": 100,
        "desc": "「待改善」等级报告占比超过该值时触发预警",
    },
    "consult_timeout_min": {
        "name": "人工咨询超时", "default": CONSULTATION_TIMEOUT_MINUTES, "unit": "分钟",
        "min": 5, "max": 240,
        "desc": "咨询进入待接入队列后超过该时长未被接手即预警（与咨询超时口径联动）",
    },
    "kb_hit_rate": {
        "name": "知识库命中率", "default": 60, "unit": "%", "min": 1, "max": 100,
        "desc": "RAG 检索命中率低于该值时预警（需结合 chat.min_similarity 观察）",
    },
    "report_fail_rate": {
        "name": "报告生成失败率", "default": 5, "unit": "%", "min": 1, "max": 100,
        "desc": "报告生成失败占比超过该值时预警（对应错误码 50002）",
    },
}

# --------------------------------------------------------------------------- #
# 角色-权限矩阵（需求 §3.1 RBAC）
# 落库键 `role.permissions`（JSON），管理后台可查看并调整。
# operator 为项目新增的运营角色：只读运营数据，不参与用户与配置管理。
# --------------------------------------------------------------------------- #
ROLE_CONFIG_KEY = "role.permissions"

ROLES: List[str] = ["user", "dietitian", "operator", "admin"]
ROLE_LABELS: Dict[str, str] = {
    "user": "普通用户", "dietitian": "营养师", "operator": "运营", "admin": "管理员",
}

ROLE_CAPABILITIES: Dict[str, str] = {
    "profile_edit": "完善个人基础信息",
    "assessment_run": "发起 / 续答测评，查看本人报告",
    "template_write": "创建 / 编辑测评模板",
    "template_audit": "模板审核发布",
    "smart_query": "智能查询 / 智能咨询",
    "consult_serve": "1v1 人工咨询（接待）",
    "customer_manage": "客户管理与数据导出",
    "system_config": "系统 / 智能体参数配置",
    "user_manage": "用户审核、禁用解封",
    "ops_dashboard": "平台运营数据看板",
}

ROLE_PERMISSIONS: Dict[str, Dict[str, bool]] = {
    "profile_edit":    {"user": True,  "dietitian": True,  "operator": False, "admin": False},
    "assessment_run":  {"user": True,  "dietitian": True,  "operator": False, "admin": True},
    "template_write":  {"user": False, "dietitian": True,  "operator": False, "admin": True},
    "template_audit":  {"user": False, "dietitian": False, "operator": False, "admin": True},
    "smart_query":     {"user": True,  "dietitian": True,  "operator": True,  "admin": True},
    "consult_serve":   {"user": False, "dietitian": True,  "operator": False, "admin": False},
    "customer_manage": {"user": False, "dietitian": True,  "operator": False, "admin": True},
    "system_config":   {"user": False, "dietitian": False, "operator": False, "admin": True},
    "user_manage":     {"user": False, "dietitian": False, "operator": False, "admin": True},
    "ops_dashboard":   {"user": False, "dietitian": False, "operator": True,  "admin": True},
}


def clamp_alert(key: str, value: Any) -> Any:
    """把预警阈值收敛到合法区间；非法值回退默认。"""
    spec = ALERT_THRESHOLDS.get(key)
    if not spec:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return spec["default"]
    return max(spec["min"], min(spec["max"], v))


@dataclass
class CheckResult:
    """规则校验结果。"""
    ok: bool
    message: str = ""
    level: str = "ok"          # ok / warn / error
    value: Any = None

    def __bool__(self) -> bool:  # 便于 `if check_phone(x):`
        return self.ok


def _err(msg: str) -> CheckResult:
    return CheckResult(ok=False, message=msg, level="error")


def _ok(value: Any = None) -> CheckResult:
    return CheckResult(ok=True, value=value)


def _warn(msg: str, value: Any = None) -> CheckResult:
    return CheckResult(ok=True, message=msg, level="warn", value=value)


# --------------------------------------------------------------------------- #
# BV-01 手机号
# --------------------------------------------------------------------------- #
def check_phone(phone: Optional[str]) -> CheckResult:
    """手机号：仅 11 位、1 开头、第二位 3-9 的号码通过。"""
    if phone is None or str(phone).strip() == "":
        return _err("手机号不能为空")
    value = str(phone).strip()
    if not value.isdigit():
        return _err("手机号只能包含数字")
    if len(value) != 11:
        return _err("手机号必须为 11 位")
    if not PHONE_PATTERN.match(value):
        return _err("手机号格式不正确（需 1 开头，第二位 3-9）")
    return _ok(value)


# --------------------------------------------------------------------------- #
# BV-03 密码
# --------------------------------------------------------------------------- #
def check_password(password: Optional[str]) -> CheckResult:
    """密码：8–64 位，边界外拒绝并提示长度规则。"""
    if password is None or password == "":
        return _err("密码不能为空")
    n = len(password)
    if n < PASSWORD_MIN_LEN:
        return _err(f"密码至少 {PASSWORD_MIN_LEN} 位")
    if n > PASSWORD_MAX_LEN:
        return _err(f"密码最多 {PASSWORD_MAX_LEN} 位")
    return _ok(password)


def mask_password_error(password: Optional[str]) -> Optional[str]:
    """返回密码长度不合规的提示；合规返回 None（供 schema 复用）。"""
    r = check_password(password)
    return None if r.ok else r.message


# --------------------------------------------------------------------------- #
# 智能体多轮对话：回传给模型的最近对话轮数（1 轮 = 问 + 答）
CHAT_HISTORY_TURNS = 3
# 追问判定：问题短于该长度且有历史时，检索词拼上上一轮问题（提升指代类追问的召回）
CHAT_FOLLOWUP_MAX_CHARS = 12

# 档案题关键词映射（测评档案题 → 忌口过滤）：标签 → 食材匹配关键词。
# 关键词必须是菜谱 ingredients 里的子串；宁保守不遗漏（如"鱼"覆盖所有鱼类菜品）。
ALLERGY_KEYWORDS = {
    "海鲜鱼类": ["虾", "蟹", "鱼", "海带", "紫菜"],
    "蛋类": ["鸡蛋", "蛋"],
    "牛奶及乳制品": ["牛奶", "酸奶", "奶"],
    "花生坚果": ["坚果", "花生"],
    "芒果": ["芒果"],
    "麸质（小麦）": ["麦", "馒头", "面条", "面包"],
}
TABOO_KEYWORDS = {
    "辛辣": ["辣"],
    "海鲜发物": ["虾", "蟹", "鱼"],
    "动物内脏": ["肝", "内脏"],
    "高糖甜品": ["甜"],
    "生冷": ["生冷", "冰"],
}

# BV-02 短信验证码
# --------------------------------------------------------------------------- #
def check_verify_code(code: Optional[str]) -> CheckResult:
    """短信验证码：必须为 6 位数字（全空格 / 前后空格 / 位数不符均拒绝）。

    注意：短信网关尚未接入（`/auth/sms/send` 为占位），本规则供接入后直接复用。
    """
    if code is None:
        return _err("验证码不能为空")
    value = str(code).strip()
    if value == "":
        return _err("验证码不能为空")
    if not value.isdigit():
        return _err("验证码只能为数字")
    if len(value) != VERIFY_CODE_LEN:
        return _err(f"验证码必须为 {VERIFY_CODE_LEN} 位")
    return _ok(value)


# --------------------------------------------------------------------------- #
# BV-04 身高 / BV-05 体重 / BV-06 BMI
# --------------------------------------------------------------------------- #
def check_height(height_cm: Any) -> CheckResult:
    """身高：50–250 cm 通过；0 / 负数 / 超上限拒绝。"""
    if height_cm is None:
        return _ok(None)
    try:
        v = float(height_cm)
    except (TypeError, ValueError):
        return _err("身高必须为数字")
    if v < HEIGHT_MIN_CM or v > HEIGHT_MAX_CM:
        return _err(f"身高需在 {HEIGHT_MIN_CM}–{HEIGHT_MAX_CM} cm 之间")
    return _ok(v)


def check_weight(weight_kg: Any) -> CheckResult:
    """体重：2–300 kg 通过；0 / 超上限拒绝。"""
    if weight_kg is None:
        return _ok(None)
    try:
        v = float(weight_kg)
    except (TypeError, ValueError):
        return _err("体重必须为数字")
    if v < WEIGHT_MIN_KG or v > WEIGHT_MAX_KG:
        return _err(f"体重需在 {WEIGHT_MIN_KG:g}–{WEIGHT_MAX_KG:g} kg 之间")
    return _ok(v)


def calc_bmi(height_cm: Any, weight_kg: Any) -> Optional[float]:
    """BMI = 体重(kg) / 身高(m)²。

    身高为 0 / 缺失 / 非法时**返回 None**，绝不返回 Infinity 或 NaN（BV-06）。
    """
    if height_cm in (None, "") or weight_kg in (None, ""):
        return None
    try:
        h = float(height_cm)
        w = float(weight_kg)
    except (TypeError, ValueError):
        return None
    if h <= 0 or w <= 0:
        return None
    return round(w / ((h / 100.0) ** 2), 2)


# --------------------------------------------------------------------------- #
# BV-07 年龄
# --------------------------------------------------------------------------- #
def check_age(age: Any) -> CheckResult:
    """年龄：0–120 通过；超范围拒绝；<18 标注未成年（特殊人群）。"""
    if age is None:
        return _ok(None)
    try:
        v = int(age)
    except (TypeError, ValueError):
        return _err("年龄必须为整数")
    if v < AGE_MIN or v > AGE_MAX:
        return _err(f"年龄需在 {AGE_MIN}–{AGE_MAX} 之间")
    if v < ADULT_AGE:
        return _warn("未成年属于特殊人群，建议在监护人陪同下使用", v)
    return _ok(v)


def is_minor(age: Any) -> bool:
    r = check_age(age)
    return r.ok and r.value is not None and int(r.value) < ADULT_AGE


# --------------------------------------------------------------------------- #
# BV-12 答案文本
# --------------------------------------------------------------------------- #
def check_answer_text(text: Optional[str], max_len: int = ANSWER_TEXT_MAX) -> CheckResult:
    """开放题文本：0 字（非必填）允许；超过上限拒绝并提示。"""
    if text is None:
        return _ok("")
    if len(text) > max_len:
        return _err(f"内容最多 {max_len} 字，请精简后重试")
    return _ok(text)


# --------------------------------------------------------------------------- #
# BV-11 必答项
# --------------------------------------------------------------------------- #
def check_required_answers(questions: Iterable[Dict[str, Any]],
                           answers: Dict[str, Any]) -> CheckResult:
    """必答题未作答时不可提交，返回第一条缺失项提示。"""
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        if not (q.get("required") or q.get("is_required")):
            continue
        qid = str(q.get("question_id") or q.get("id") or "")
        if not qid:
            continue
        v = (answers or {}).get(qid)
        if v is None or (isinstance(v, str) and v.strip() == "") or v == []:
            return _err(f"必答题「{q.get('title') or qid}」尚未作答")
    return _ok()


# --------------------------------------------------------------------------- #
# BV-18 维度权重
# --------------------------------------------------------------------------- #
def check_weights(weights: Iterable[Any]) -> CheckResult:
    """模板声明的维度权重：合计必须为 1（容差 1e-6）；单项为 0 允许但要提示。"""
    vals: List[float] = []
    for w in weights or []:
        try:
            vals.append(float(w))
        except (TypeError, ValueError):
            return _err("维度权重必须为数字")
    if not vals:
        return _err("维度权重不能为空")
    if any(v < 0 for v in vals):
        return _err("维度权重不能为负")
    total = sum(vals)
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        return _err(f"维度权重合计必须为 1（当前 {round(total, 6)}）")
    if any(abs(v) <= WEIGHT_SUM_TOLERANCE for v in vals):
        return _warn("存在权重为 0 的维度，该维度将不参与总分计算", total)
    return _ok(total)


# --------------------------------------------------------------------------- #
# BV-19 分页
# --------------------------------------------------------------------------- #
def normalize_paging(page: Any = 1, page_size: Any = 20,
                     max_size: int = PAGE_SIZE_MAX) -> Tuple[int, int]:
    """分页归一化：page < 1 归为 1；size 限制在 1–max_size（默认 100）。

    超范围页码不报错，由 DAO 自然返回空列表（配合 total 让前端展示空状态）。
    """
    try:
        p = int(page)
    except (TypeError, ValueError):
        p = 1
    try:
        s = int(page_size)
    except (TypeError, ValueError):
        s = 20
    if p < 1:
        p = 1
    if s < 1:
        s = 1
    if s > max_size:
        s = max_size
    return p, s


def offset_of(page: Any, page_size: Any, max_size: int = PAGE_SIZE_MAX) -> Tuple[int, int, int]:
    """返回 (page, size, offset)，供 DAO 直接使用。"""
    p, s = normalize_paging(page, page_size, max_size)
    return p, s, (p - 1) * s
