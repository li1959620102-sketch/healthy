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
# 邮箱（FR-U-01）：本地部分允许常见字符，域名至少一个点且顶级域≥2 位字母。
# 不追 RFC 5322 全量文法——过度宽松的正则会把明显非法的值放进来，
# 真正的可用性由"发一封验证码邮件、用户收得到"来证明。
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
EMAIL_MAX_LEN = 254                            # RFC 5321 对邮件地址长度的上限
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

# 智能查询并发与缓存（IT-C-03 缓存命中 / IT-C-15 服务繁忙排队反馈）
# 说明：项目按单进程部署（uvicorn 单实例 + 前置 Nginx），故在进程内做准入与缓存；
# 多实例部署时这两项应迁移到 Redis（键相同，实现可平替）。
CHAT_MAX_CONCURRENT = 8              # 同时生成答案的容量；超出即给用户排队提示（不丢请求）
CHAT_ANSWER_CACHE_TTL = 600          # 高频问题答案缓存 10 分钟
CHAT_ANSWER_CACHE_MAX = 200          # 缓存条目上限（有界，防内存膨胀）

# 知识库向量化写入的业务场景标记（落 Milvus business_scene 字段，用于按场景筛片段）
# 原为硬编码 "NRS2002"（旧项目名），会让新写入的片段带着上一代项目名进检索结果。
KB_BUSINESS_SCENE = "food_health_kb"

# AI 咨询辅助建议（营养师故事 4：「使用 AI 生成咨询辅助建议并人工修改」）
# AI 产出的是**草稿**，由营养师修改后主动发送；条数与字数上限用于控制上下文与生成成本。
SUGGEST_CONTEXT_MESSAGES = 10   # 组装给模型的最近对话条数（用户 + 营养师 + 系统提示）
SUGGEST_MAX_CHARS = 300         # 建议草稿字数上限（提示模型遵守，超长不截断、不判错）

# 运营活跃口径（需求 FR-V-08：管理员查看 DAU / MAU）
# 定义为「窗口内有过登录 或 提交测评 或 发起咨询 或 发起对话」的**去重用户**。
# 只看 sys_user.last_login_at 会系统性低估：用户登录后长时间保持登录态继续使用，
# 期间不会再产生登录记录，于是"天天在用"的人在指标上等于"没来过"。
ACTIVE_MAU_DAYS = 30            # MAU 统计窗口（天）

# --------------------------------------------------------------------------- #
# 主智能体 · 意图识别规则表
#
# 为什么用规则而不是大模型（这是本项目最重要的一次取舍）：
#   ① **可复现**：同一个问句永远得到同一个路由。LLM 分类做不到，
#      同一句话两次走不同分支，用户看到的体验是"这系统一会儿明白一会儿不懂"。
#   ② **可评测**：规则分类能配固定标注集算混淆矩阵；LLM 分类每次跑都可能不一样，
#      误判率根本没法量化——而"误判率多少"恰恰是路由层最该被问的问题。
#   ③ **成本与时延**：路由发生在每次问答之前，LLM 分类等于每次问答多一次模型调用。
#   ④ **可解释**：命中了哪几个词、为什么给这个分，都能摊开给用户和运营看。
#
# 代价是规则要维护。所以规则表放这里（业务常量的唯一事实源），
# 并由 tests/integration/test_intent_routing_cases.py + 评测集持续回归。
#
# 权重设计：
#   `strong` —— 领域专有词，命中即可判定意图（"采购清单"只可能是食养食疗）；
#   `weak`   —— 通用词，需要多个共同出现才够（"多少"单独出现不足以判意图）。
# 评分 = strong 命中数 × strong 权重 + weak 命中数 × weak 权重，
# 同分按 `order` 仲裁（order 与业务链路顺序一致：先评估 → 再方案 → 再菜品 → 再提醒 → 再答疑 → 最后统计）。
# --------------------------------------------------------------------------- #
INTENT_STRONG_WEIGHT = 3.0       # 领域专有词权重
INTENT_WEAK_WEIGHT = 1.0         # 通用词权重
INTENT_CRITICAL_WEIGHT = 9.0     # 显式指令词权重（"问数""Text2SQL"这类：用户主动点名了能力）
INTENT_MIN_SCORE = 2.0           # 低于此分视为「意图不明」，不硬猜
INTENT_LOW_CONFIDENCE = 0.34     # 归一化置信度低于此值 → 走兜底并在响应里如实标注

# 兜底意图：识别不出就走营养问答，因为它有拒答兜底、不会给出有害输出
INTENT_FALLBACK = "dietitian"

# 每个意图对应的子智能体（key 与 GET /agents 返回的目录 key 一致）
INTENT_TO_AGENT: Dict[str, str] = {
    "assessment": "assessment",
    "diet": "diet",
    "dish": "dish",
    "steward": "steward",
    "dietitian": "dietitian",
    "data": "data",
}

INTENT_NAMES: Dict[str, str] = {
    "assessment": "健康评估",
    "diet": "食养方案",
    "dish": "菜品推荐",
    "steward": "跟踪提醒",
    "dietitian": "营养问答",
    "data": "数据分析",
    "unknown": "意图不明",
}

# --------------------------------------------------------------------------- #
# 「基线信号」：指标名不是意图，是基线
#
# 评测集抓出来的设计缺陷：用户说「高血压人群的早餐怎么吃」，真正想问的是营养知识，
# 但因为句子里有「高血压」就被判成健康评估。问题出在——**指标名描述的是用户是谁，
# 不是用户要什么**。它应该让健康评估成为链路的第一步（先了解基线），
# 但不该抢走主意图。
#
# 所以把指标词从 strong 挪到这里：命中会计入 `baseline_hit` 并让 assessment
# 进入执行链路，但**不参与主意图打分**。只有当没有任何其他意图过阈值时，
# assessment 才作为兜底主意图——那时"我就是来量个血压"确实是真实意图。
# --------------------------------------------------------------------------- #
INTENT_BASELINE_TERMS: List[str] = [
    "血压", "血糖", "血脂", "尿酸", "BMI", "体脂", "静息心率",
    "高血压", "糖尿病", "高血脂", "痛风", "脂肪肝", "贫血",
]

# 规则表：key=意图，strong/weak/critical=词表，order=链路顺序，desc=为什么归到这一类
INTENT_RULES: List[Dict[str, Any]] = [
    {
        "key": "assessment", "order": 1,
        "strong": ["测评", "评测", "问卷", "体质辨识", "健康档案", "风险评估",
                   # 「判定动作」属于评估意图：用户要的是一个结论，不是一段建议。
                   # 不加这几个词，「空腹血糖 6.8 算高吗」会掉进营养问答兜底——
                   # 而问答链路只会复述知识，说不出「算不算高」这个结论。
                   "算多少", "算高吗", "正常吗", "偏高吗", "超标", "是多少"],
        "weak": ["体检", "报告", "指标", "风险", "评估", "身体情况", "合格", "分数", "等级"],
        "desc": "涉及身体状况测量与风险判定，需先建立基线",
    },
    {
        "key": "diet", "order": 2,
        "strong": ["食养计划", "食疗", "药膳", "采购清单", "一周食谱", "月度食谱",
                   "周期", "菜谱", "食养", "配餐", "三餐", "食谱"],
        "weak": ["计划", "方案", "搭配", "调理", "养生", "一周", "采购", "菜单设计"],
        "desc": "需要成体系的周期饮食安排与采购清单",
    },
    {
        "key": "dish", "order": 3,
        # strong 全部是「点到了具体吃法/菜品」的表述。区分 dietitian 的关键就在这里：
        # "这个能吃吗"（承接上文、泛指）走 dietitian；"糖尿病人能吃什么菜"（要菜）走 dish。
        "strong": ["荐菜", "推荐菜", "宴请菜单", "推荐菜品", "适合吃", "不能吃",
                   "忌口", "过敏", "外食", "外卖怎么选", "下饭菜", "硬菜", "汤品",
                   "能吃什么菜", "能吃哪些", "做什么菜", "吃哪些", "能一起吃",
                   "能一起炖", "一起吃", "同食", "怎么做菜", "推荐点", "吃点",
                   "的菜", "做菜", "怎么搭配", "怎么配"],
        "weak": ["吃什么", "吃啥", "推荐", "菜品", "菜单", "饮品", "预算", "人数", "聚餐"],
        "desc": "具体到某一道菜/食材能不能吃、怎么搭配",
    },
    {
        "key": "steward", "order": 4,
        "strong": ["打卡", "提醒我", "监督", "随访", "复诊提醒", "监测提醒",
                   "设置提醒", "用餐提醒", "喝水提醒", "吃维生素",
                   "坚持不下来", "坚持不住"],
        "weak": ["提醒", "跟踪", "通知", "坚持", "督促", "计划提醒", "怎么坚持"],
        "desc": "方案要落到位，需要持续跟踪与提醒",
    },
    {
        "key": "dietitian", "order": 5,
        "strong": ["药食同源", "食疗功效", "营养师", "咨询营养师", "怎么吃才",
                   "为什么不能吃", "禁忌", "功效是什么"],
        "weak": ["咨询", "问答", "怎么吃", "营养", "能不能吃", "可以吃吗", "为什么", "科普"],
        "desc": "开放式营养知识问答，需检索知识库并带来源作答",
    },
    {
        "key": "data", "order": 5,
        # critical：用户主动点名了这项能力，权重必须压过任何领域专有词，
        # 否则「问数：我这个月测评几次」会被句子里那两个字抢成健康评估。
        "critical": ["问数", "Text2SQL", "生成图表", "数据看板"],
        "strong": ["统计", "报表", "趋势图", "趋势", "折线图", "占比", "同比", "环比"],
        "weak": ["数据", "分析", "对比", "多少", "排名", "汇总"],
        "desc": "跨表统计与可视化，需走受控 Text2SQL",
    },
]

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
# FR-U-01 邮箱
# --------------------------------------------------------------------------- #
def check_email(email: Optional[str]) -> CheckResult:
    """邮箱：格式合法且长度 ≤254 通过；大小写与首尾空格统一归一为小写去空格。

    归一化很关键：`Alice@Example.com` 与 `alice@example.com` 是同一个邮箱，
    若不做归一，用户换一种大小写就能绕过「已注册」判断，注册出多个账号。
    """
    if email is None or str(email).strip() == "":
        return _err("邮箱不能为空")
    value = str(email).strip().lower()
    if len(value) > EMAIL_MAX_LEN:
        return _err(f"邮箱长度不能超过 {EMAIL_MAX_LEN} 位")
    if "@" not in value:
        return _err("邮箱格式不正确")
    if not EMAIL_PATTERN.match(value):
        return _err("邮箱格式不正确（示例：name@example.com）")
    return _ok(value)


def normalize_email(email: Optional[str]) -> str:
    """邮箱归一化（小写去空格）；不做合法性校验，供已有合法值二次处理使用。"""
    return str(email or "").strip().lower()


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
def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "") or v == []


def missing_required_questions(questions: Iterable[Dict[str, Any]],
                               answers: Dict[str, Any]) -> List[Dict[str, str]]:
    """必答题未作答**清单**（IT-AS-06）。

    为什么返回清单而不是第一条提示：前端提交被拒后要一次性把缺失项高亮出来，
    只回第一条会让用户"改一题再被拒一次"，十道必答题就要来回十次。
    """
    ans = answers if isinstance(answers, dict) else {}
    missing: List[Dict[str, str]] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        if not (q.get("required") or q.get("is_required")):
            continue
        qid = str(q.get("question_id") or q.get("id") or "")
        if not qid:
            continue
        if _is_blank(ans.get(qid)):
            missing.append({"question_id": qid, "title": str(q.get("title") or qid)})
    return missing


def check_required_answers(questions: Iterable[Dict[str, Any]],
                           answers: Dict[str, Any]) -> CheckResult:
    """必答题未作答时不可提交，返回第一条缺失项提示。"""
    missing = missing_required_questions(questions, answers)
    if missing:
        return _err(f"必答题「{missing[0]['title']}」尚未作答")
    return _ok()


# --------------------------------------------------------------------------- #
# BV-13 答题实时校验（纯本地规则，不调模型）
# --------------------------------------------------------------------------- #
def _numeric_checker(q: Dict[str, Any]):
    """按题干关键字把数值题映射到已有规则函数（身高/体重/年龄）。"""
    text = " ".join(str(q.get(k) or "") for k in
                    ("question_id", "key", "title", "unit", "name")).lower()
    if "身高" in text or "height" in text:
        return check_height
    if "体重" in text or "weight" in text:
        return check_weight
    if "年龄" in text or "age" in text:
        return check_age
    return None


def _range_check(value: Any, lo: Any, hi: Any, q: Dict[str, Any]) -> CheckResult:
    label = str(q.get("title") or q.get("question_id") or "该项")
    unit = str(q.get("unit") or "")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _err(f"{label}必须为数字")
    if lo is not None and v < float(lo):
        return _err(f"{label}需不低于 {lo}{unit}")
    if hi is not None and v > float(hi):
        return _err(f"{label}需不高于 {hi}{unit}")
    return _ok(v)


def answer_warnings(questions: Iterable[Dict[str, Any]],
                    answers: Dict[str, Any]) -> List[Dict[str, str]]:
    """逐题本地校验，产出 warnings（不阻断保存，也不调用模型）。

    用例 IT-AS-05 / A6 的口径是「实时提示、不调模型、≤200ms」：
    提交答案越界（如身高 500cm）时必须**当次响应**就告诉用户，
    但又不能因此拒绝保存——否则用户答到一半的草稿会丢。

    校验优先级：
      1) 题目自带 min/max（模板声明范围，最精确）；
      2) 题干/单位能识别出身高/体重/年龄时，套用 rules 里的既有区间。
    数字以外的题型（单选/多选/文本）一律不产生 warning，避免误报。
    """
    ans = answers if isinstance(answers, dict) else {}
    out: List[Dict[str, str]] = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("question_id") or q.get("id") or "")
        if not qid or qid not in ans:
            continue
        val = ans.get(qid)
        if isinstance(val, (list, dict)) or _is_blank(val):
            continue
        lo, hi = q.get("min"), q.get("max")
        if lo is not None or hi is not None:
            chk: Optional[CheckResult] = _range_check(val, lo, hi, q)
        else:
            fn = _numeric_checker(q)
            chk = fn(val) if fn is not None else None
        if chk is None or chk.ok:
            continue
        out.append({"question_id": qid, "level": "warn", "message": chk.message})
    return out


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


# --------------------------------------------------------------------------- #
# 舌象辨识（O 组）· 枚举白名单与置信度门槛
#
# 视觉模型只负责「看到什么」，不负责「说明什么问题」。因此这里定的是
# **可观察特征的枚举**，不是证型/体质结论。模型输出落到枚举外的值一律
# 归一成「无法判断」——抗幻觉靠代码兜底，不靠模型自觉。
# --------------------------------------------------------------------------- #
TONGUE_UNKNOWN = "无法判断"

TONGUE_FEATURE_ENUMS: Dict[str, List[str]] = {
    # 舌质颜色
    "tongue_color": ["淡红", "淡白", "红", "深红", "紫暗", "青紫", TONGUE_UNKNOWN],
    # 舌苔颜色
    "coating_color": ["白", "黄", "灰黑", "无苔", TONGUE_UNKNOWN],
    # 舌苔厚薄
    "coating_thickness": ["薄", "厚", "无苔", TONGUE_UNKNOWN],
    # 润燥
    "moisture": ["润", "偏干", "水滑", TONGUE_UNKNOWN],
    # 舌体形态
    "tongue_shape": ["正常", "胖大", "瘦薄", TONGUE_UNKNOWN],
    # 齿痕 / 裂纹 / 瘀点：有 / 无 / 无法判断
    "teeth_marks": ["有", "无", TONGUE_UNKNOWN],
    "cracks": ["有", "无", TONGUE_UNKNOWN],
    "spots": ["有", "无", TONGUE_UNKNOWN],
}

# 模型自评置信度低于此值 → 视为「看得不够清楚」，质量判定为不充分
TONGUE_MIN_CONFIDENCE = 0.55
# 舌图长边压到多少像素再送模型：原图直传会把单张耗时推到分钟级
TONGUE_IMAGE_MAX_SIDE = 768
# 单张图字节上限（压缩前，超出直接拒绝，避免把大文件读进内存）
TONGUE_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def check_tongue_image(filename: Optional[str], size: int) -> CheckResult:
    """上传前校验：格式与大小。"""
    name = (filename or "").lower()
    if not name:
        return _err("请选择舌象图片")
    if not name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
        return _err("仅支持 JPG / PNG / WEBP / BMP 格式的图片")
    if size <= 0:
        return _err("图片内容为空")
    if size > TONGUE_IMAGE_MAX_BYTES:
        return _err("图片不能超过 8MB，请压缩后重试")
    return _ok(size)


def normalize_tongue_features(raw: Any) -> Tuple[Dict[str, str], List[str]]:
    """把模型吐出的特征值收敛到白名单。

    返回 (归一后的特征, 被纠正的字段说明)。第二项用于如实上报
    「模型给了什么值、我们改成了什么」，而不是悄悄替换掉。
    """
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, str] = {}
    corrected: List[str] = []
    for key, allowed in TONGUE_FEATURE_ENUMS.items():
        val = raw.get(key)
        val = str(val).strip() if val is not None else ""
        if val in allowed:
            out[key] = val
            continue
        out[key] = TONGUE_UNKNOWN
        if val:
            corrected.append(f"{key}: 模型输出「{val}」不在可枚举范围内，已归一为「{TONGUE_UNKNOWN}」")
    return out, corrected


def reconcile_tongue_features(features: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """舌象特征自洽校验：消掉字段之间的逻辑矛盾。

    实测 qwen2.5vl 会同时给出「舌苔颜色=无苔」和「舌苔厚薄=薄」——
    无苔就谈不上厚薄，这在语义上不成立。留着会让前端显示出自相矛盾的报告，
    比给「无法判断」更糟（用户会以为系统坏了）。

    规则只有两条，都朝"更保守"的方向收敛：
      1. 厚薄为「无苔」→ 颜色必须是「无苔」（没有苔就没有苔色）；
      2. 颜色为「无苔」而厚薄给了薄/厚 → 厚薄改「无法判断」（保留"看到无苔"这个观察）。

    返回 (修正后特征, 修正说明)，说明用于如实上报改了什么。
    """
    out = dict(features or {})
    fixed: List[str] = []
    color = out.get("coating_color")
    thick = out.get("coating_thickness")

    if thick == "无苔" and color != "无苔":
        out["coating_color"] = "无苔"
        fixed.append(f"舌苔厚薄=无苔，颜色「{color}」与之矛盾，已统一为「无苔」")
    elif color == "无苔" and thick in ("薄", "厚"):
        out["coating_thickness"] = TONGUE_UNKNOWN
        fixed.append(f"舌苔颜色=无苔，厚薄却为「{thick}」，已将该字段置为「{TONGUE_UNKNOWN}」")
    return out, fixed


# --------------------------------------------------------------------------- #
# 子智能体 self-RAG 检索质量门槛（LangGraph 子图用）
#
# 营养问答的答案质量取决于检索到的依据。检索不到就生成，等于让模型凭记忆编。
# 所以子图里放了一个循环：检索 → 评估质量 → 不达标就改写查询重检 → 仍不达标就拒答。
# 这两个常量就是循环的边界。
# --------------------------------------------------------------------------- #
SELFRAG_MAX_ATTEMPTS = 2       # 最多检索 2 次（首检 + 1 次改写重检）
SELFRAG_MIN_DOCS = 1           # 至少要命中 1 条过阈值的片段
SELFRAG_MIN_TOP_SCORE = 0.55   # 最高分不得低于此值（与 KB_MIN_SIMILARITY 同口径）

# 查询改写时要剥掉的口语填充词：用户问"高血压的人平时吃啥比较好"，
# 检索词里"的人""平时""比较好"只会稀释向量，去掉后召回更准。
QUERY_STOPWORDS = {
    "的人", "平时", "比较", "好点", "怎么样", "怎么办", "可以吗", "能不能",
    "应该", "什么", "哪些", "怎么", "如何", "为什么", "是不是", "有没有",
    "请问", "我想", "知道", "一下", "这个", "那个", "我们", "你们", "他们",
    "一些", "有点", "稍微", "还是", "然后", "就是", "这样", "那样",
}

# --------------------------------------------------------------------------- #
# 数字人语音播报（TTS）
#
# 播报的定位是"把结论念一遍"，不是"把全文读出来"。超过这个长度就截断并标记
# `truncated=true`：合成时间是线性的，读三分钟没人会听，还会把前端按钮一直占着。
# --------------------------------------------------------------------------- #
TTS_MAX_CHARS = 500
