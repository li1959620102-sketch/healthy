# -*- coding: utf-8 -*-
"""AI 智参平台业务表模型（第一批：数据模型与建表）。

依据《数据库设计说明书 V1.1》。统一复用 knowledge_file 模块的 Base 与 DatabaseManager，
保证与 sys_user / knowledge_files 同一套元数据。

说明：
- sys_user、knowledge_files 为既有表，字段扩展在对应模型（user.py / knowledge_file.py）内完成；
  既有表新增列需执行 migrations/001_extend_legacy_tables.sql（create_all 不会改已有表）。
- 本文件定义其余 16 张新表，create_all 会在表不存在时自动创建。
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    DateTime,
    Enum,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    Date,
    DECIMAL,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.mysql import VARBINARY

from app.models.mysql.knowledge_file import Base


# --------------------------------------------------------------------------- #
# 3.2 用户健康档案
# --------------------------------------------------------------------------- #
class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = {"comment": "用户健康档案"}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="关联 sys_user.user_id")
    height_cm = Column(SmallInteger, comment="身高 cm")
    weight_kg = Column(DECIMAL(5, 2), comment="体重 kg")
    diseases_cipher = Column(VARBINARY(1024), comment="疾病史密文 AES-256（敏感个人信息）")
    diet_preference = Column(String(255), comment="饮食偏好，如 素食/少油/忌辛辣")
    allergies_json = Column(String(512), comment="过敏清单 JSON {labels,keywords}（测评档案题写入）")
    taboo_json = Column(String(512), comment="忌口清单 JSON {labels,keywords}（测评档案题写入）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    __table_args_2__ = (UniqueConstraint("user_id", name="uk_user"),)


# --------------------------------------------------------------------------- #
# 3.3 营养师专业信息
# --------------------------------------------------------------------------- #
class DietitianProfile(Base):
    __tablename__ = "dietitian_profile"
    __table_args__ = (
        Index("idx_audit", "audit_status", "audit_at"),
        {"comment": "营养师专业信息与资质审核"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="关联 sys_user.user_id")
    cert_name = Column(String(100), comment="资格证名称")
    cert_no = Column(String(100), comment="证书编号")
    cert_url = Column(String(500), comment="资格证扫描件（对象存储地址）")
    work_proof_url = Column(String(500), comment="工作证明（对象存储地址）")
    specialties = Column(JSON, comment='擅长领域 ["孕期营养","糖尿病饮食"]')
    cases = Column(Text, comment="服务案例（脱敏）")
    audit_status = Column(
        Enum("pending", "approved", "rejected", "expired", name="dietitian_audit_status"),
        nullable=False, default="pending", comment="审核状态",
    )
    audit_at = Column(DateTime, comment="审核时间")
    auditor_id = Column(String(64), comment="审核人")
    reject_reason = Column(String(500), comment="驳回原因")
    cert_expire_at = Column(Date, comment="证书有效期")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    __table_args_extra__ = (UniqueConstraint("user_id", name="uk_user"),)


# --------------------------------------------------------------------------- #
# 3.4 营养师-客户绑定（解 Blocker B2：生成列强制单绑定）
# --------------------------------------------------------------------------- #
class DietitianCustomer(Base):
    __tablename__ = "dietitian_customer"
    __table_args__ = (
        UniqueConstraint("dietitian_id", "customer_id", name="uk_pair"),
        UniqueConstraint("active_key", name="uk_active_customer"),
        Index("idx_dietitian", "dietitian_id", "status"),
        Index("idx_customer", "customer_id", "status"),
        {"comment": "营养师与客户绑定关系"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    dietitian_id = Column(String(64), nullable=False, comment="营养师 user_id")
    customer_id = Column(String(64), nullable=False, comment="客户 user_id")
    bind_source = Column(
        Enum("invite_code", "consultation", "admin", name="bind_source"),
        nullable=False, comment="绑定来源",
    )
    status = Column(
        Enum("active", "unbound", name="bind_status"),
        nullable=False, default="active", comment="绑定状态",
    )
    # 生成列：仅 active 时取 customer_id；解绑后置 NULL，唯一索引自动放行新绑定
    active_key = Column(
        String(64),
        Computed("IF(status='active', customer_id, NULL)", persisted=True),
        comment="当前生效客户键（用于唯一约束）",
    )
    bound_at = Column(DateTime, default=datetime.now, comment="绑定时间")
    unbound_at = Column(DateTime, comment="解绑时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.5 测评模板
# --------------------------------------------------------------------------- #
class AssessmentTemplate(Base):
    __tablename__ = "assessment_template"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uk_code_version"),
        Index("idx_status", "status", "published_at"),
        Index("idx_creator", "created_by", "created_at"),
        {"comment": "测评模板（版本不可变）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, comment="模板编码，跨版本不变")
    version = Column(Integer, nullable=False, default=1, comment="版本号，修改后递增")
    name = Column(String(200), nullable=False, comment="模板名称")
    type = Column(
        Enum("risk", "nutrition", "constitution", "tongue", name="template_type"),
        nullable=False, default="risk", comment="测评类型：风险/营养/体质/舌象",
    )
    description = Column(String(1000), comment="描述")
    dimension_config = Column(JSON, nullable=False, comment="维度与权重")
    question_config = Column(JSON, nullable=False, comment="题目配置")
    status = Column(
        Enum("draft", "pending", "published", "offline", name="template_status"),
        nullable=False, default="draft", comment="状态",
    )
    created_by = Column(String(64), nullable=False, comment="创建人")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    submitted_at = Column(DateTime, comment="提交审核时间")
    audit_at = Column(DateTime, comment="审核时间")
    auditor_id = Column(String(64), comment="审核人")
    audit_remark = Column(String(500), comment="审核备注")
    published_at = Column(DateTime, comment="发布时间")
    offline_at = Column(DateTime, comment="下线时间")


# --------------------------------------------------------------------------- #
# 3.6 测评记录（含草稿）
# --------------------------------------------------------------------------- #
class AssessmentRecord(Base):
    __tablename__ = "assessment_record"
    __table_args__ = (
        Index("idx_user_status", "user_id", "status"),
        Index("idx_guest", "guest_token"),
        Index("idx_expire", "expire_at", "status"),
        Index("idx_template", "template_id", "template_version"),
        UniqueConstraint("idempotency_key", name="uk_idempotency"),
        {"comment": "测评记录（含草稿状态）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), comment="用户标识，游客为空")
    guest_token = Column(String(64), comment="游客令牌哈希")
    template_id = Column(BigInteger, nullable=False, comment="模板 ID")
    template_version = Column(Integer, nullable=False, comment="发起时锁定的版本")
    profile_snapshot = Column(JSON, comment="发起时用户画像快照")
    status = Column(
        Enum("in_progress", "completed", "void", name="assessment_status"),
        nullable=False, default="in_progress", comment="状态",
    )
    answers = Column(JSON, comment="答案草稿（实时保存）")
    current_page = Column(SmallInteger, default=1, comment="当前页")
    progress = Column(String(16), comment="进度 8/24")
    idempotency_key = Column(String(64), comment="提交幂等键")
    started_at = Column(DateTime, default=datetime.now, comment="发起时间")
    last_saved_at = Column(DateTime, comment="草稿最后保存时间")
    expire_at = Column(DateTime, comment="草稿过期时间（30 天）")
    submitted_at = Column(DateTime, comment="提交时间")
    voided_at = Column(DateTime, comment="作废时间")


# --------------------------------------------------------------------------- #
# 3.7 测评报告（核心表：评分结构化）
# --------------------------------------------------------------------------- #
class AssessmentReport(Base):
    __tablename__ = "assessment_report"
    __table_args__ = (
        UniqueConstraint("record_id", name="uk_record"),
        Index("idx_user_time", "user_id", "generated_at"),
        Index("idx_level", "level", "generated_at"),
        Index("idx_state", "state"),
        Index("idx_template_version", "template_id", "template_version", "user_id"),
        {"comment": "测评报告（评分结构化存储）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    record_id = Column(BigInteger, nullable=False, comment="关联测评记录")
    user_id = Column(String(64), comment="冗余，便于趋势查询与筛选")
    template_id = Column(BigInteger, nullable=False, comment="模板 ID")
    template_version = Column(Integer, nullable=False, comment="模板版本")
    state = Column(
        Enum("generating", "basic", "complete", "failed", name="report_state"),
        nullable=False, default="generating", comment="生成状态",
    )
    score = Column(DECIMAL(5, 2), comment="总分")
    standardized_score = Column(DECIMAL(5, 2), comment="标准化百分制")
    level = Column(
        Enum("excellent", "good", "needs_improvement", name="report_level"),
        comment="优秀/良好/待改善",
    )
    dimension_scores = Column(JSON, comment="各维度得分")
    suggestions = Column(JSON, comment="建议")
    analysis_text = Column(Text, comment="智能体生成的分析文本")
    references = Column(JSON, comment="引用来源")
    disclaimer = Column(String(255), comment="免责声明")
    model_name = Column(String(64), comment="生成模型名")
    scoring_version = Column(String(32), comment="生成时的评分规则版本（评审 M6）")
    prompt_version = Column(String(32), comment="提示词版本")
    elapsed_ms = Column(Integer, comment="生成耗时")
    retry_count = Column(SmallInteger, default=0, comment="重试次数")
    generated_at = Column(DateTime, default=datetime.now, comment="生成时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


# --------------------------------------------------------------------------- #
# 3.8 营养师批注
# --------------------------------------------------------------------------- #
class ReportAnnotation(Base):
    __tablename__ = "report_annotation"
    __table_args__ = (
        Index("idx_report", "report_id", "created_at"),
        {"comment": "营养师对报告的批注（与智能体原文形成双版本）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False, comment="报告 ID")
    dietitian_id = Column(String(64), nullable=False, comment="营养师 user_id")
    content = Column(Text, nullable=False, comment="批注内容")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


# --------------------------------------------------------------------------- #
# 3.9 智能查询会话 / 消息
# --------------------------------------------------------------------------- #
class ChatSession(Base):
    __tablename__ = "chat_session"
    __table_args__ = (
        Index("idx_user", "user_id", "last_msg_at"),
        {"comment": "智能查询会话"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), comment="游客为空")
    guest_token = Column(String(64), comment="游客令牌哈希")
    title = Column(String(200), comment="会话标题（首问摘要）")
    message_count = Column(Integer, default=0, comment="消息数")
    context_summary = Column(Text, comment="长会话上下文压缩摘要")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    last_msg_at = Column(DateTime, comment="最后消息时间")


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        Index("idx_session", "session_id", "created_at"),
        Index("idx_refused", "refused", "created_at"),
        {"comment": "智能查询消息"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, nullable=False, comment="会话 ID")
    role = Column(Enum("user", "assistant", name="chat_role"), nullable=False, comment="角色")
    content = Column(Text, nullable=False, comment="内容")
    references = Column(JSON, comment="引用来源")
    refused = Column(SmallInteger, default=0, comment="是否命中拒答策略")
    refuse_reason = Column(String(64), comment="拒答原因")
    elapsed_ms = Column(Integer, comment="响应耗时")
    token_cost = Column(Integer, comment="token 消耗")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.10 人工咨询
# --------------------------------------------------------------------------- #
class Consultation(Base):
    __tablename__ = "consultation"
    __table_args__ = (
        Index("idx_dietitian", "dietitian_id", "status"),
        Index("idx_user", "user_id", "created_at"),
        Index("idx_pending", "status", "created_at"),
        {"comment": "1v1 咨询会话"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    dietitian_id = Column(String(64), comment="营养师 ID，待接入为空")
    type = Column(Enum("ai", "human", name="consultation_type"), nullable=False, default="human", comment="类型")
    status = Column(
        Enum("pending", "active", "closed", "timeout", name="consultation_status"),
        nullable=False, default="pending", comment="状态",
    )
    transferred_from_session_id = Column(BigInteger, comment="来源智能会话（转人工）")
    accepted_at = Column(DateTime, comment="接入时间")
    closed_at = Column(DateTime, comment="结束时间")
    close_reason = Column(String(100), comment="结束原因")
    rating = Column(SmallInteger, comment="满意度 1-5")
    rating_comment = Column(String(500), comment="评价内容")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


class ConsultationMessage(Base):
    __tablename__ = "consultation_message"
    __table_args__ = (
        Index("idx_consultation", "consultation_id", "created_at"),
        {"comment": "咨询消息（一期仅文本）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    consultation_id = Column(BigInteger, nullable=False, comment="咨询 ID")
    sender_type = Column(
        Enum("user", "dietitian", "system", name="sender_type"),
        nullable=False, comment="发送方",
    )
    content_type = Column(
        Enum("text", "image", name="msg_content_type"),
        nullable=False, default="text", comment="内容类型",
    )
    content = Column(Text, comment="内容")
    attachment_url = Column(String(500), comment="图片地址（二期）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.11 操作日志
# --------------------------------------------------------------------------- #
class OperationLog(Base):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("idx_operator", "operator_id", "created_at"),
        Index("idx_target", "target_type", "target_id"),
        Index("idx_created", "created_at"),
        {"comment": "管理员操作日志（追加写，保留≥1年）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    operator_id = Column(String(64), nullable=False, comment="操作人")
    operator_role = Column(String(20), nullable=False, comment="操作人角色")
    action = Column(String(64), nullable=False, comment="动作")
    target_type = Column(String(64), comment="目标类型")
    target_id = Column(String(64), comment="目标 ID")
    content = Column(JSON, comment="操作详情")
    ip_address = Column(String(64), comment="IP")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.12 系统配置
# --------------------------------------------------------------------------- #
class SystemConfig(Base):
    __tablename__ = "system_config"
    __table_args__ = {"comment": "系统参数（智能体参数、阈值、配置）"}

    config_key = Column(String(100), primary_key=True, comment="配置键")
    config_value = Column(Text, nullable=False, comment="配置值（JSON 或字符串）")
    description = Column(String(500), comment="说明")
    updated_by = Column(String(64), comment="更新人")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


# --------------------------------------------------------------------------- #
# 3.14 舌象图片（V1.1 原型新增）
# --------------------------------------------------------------------------- #
class TongueImage(Base):
    __tablename__ = "tongue_image"
    __table_args__ = (
        Index("idx_record", "record_id"),
        Index("idx_user", "user_id", "created_at"),
        {"comment": "舌象图片（体质测评附件，AI 辨识待 S4 验证）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), comment="用户标识，游客为空")
    record_id = Column(BigInteger, comment="关联测评记录")
    image_url = Column(String(500), nullable=False, comment="舌图对象存储地址")
    status = Column(
        Enum("uploaded", "recognizing", "recognized", "failed", name="tongue_status"),
        nullable=False, default="uploaded", comment="状态",
    )
    recognized_result = Column(JSON, comment="AI 辨识结果（S4 达标后写入）")
    model_name = Column(String(64), comment="辨识模型")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.13 注销数据清理任务（合规 D2：注销后 15 日内删除/匿名化）
# --------------------------------------------------------------------------- #
class DataDeletionTask(Base):
    __tablename__ = "data_deletion_task"
    __table_args__ = (
        Index("idx_state", "state", "execute_at"),
        {"comment": "账号注销后的数据清理任务（定时执行）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="目标用户")
    state = Column(
        Enum("pending", "running", "done", "failed", name="deletion_task_state"),
        nullable=False, default="pending", comment="状态",
    )
    scope = Column(String(64), comment="清理范围 mysql/vector/minio")
    execute_at = Column(DateTime, comment="计划执行时间（注销+15日）")
    finished_at = Column(DateTime, comment="完成时间")
    error = Column(String(500), comment="失败原因")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.15 / 3.16 食养计划 + 食谱明细（V1.1 原型新增）
# --------------------------------------------------------------------------- #
class DietPlan(Base):
    __tablename__ = "diet_plan"
    __table_args__ = (
        UniqueConstraint("report_id", name="uk_report"),
        Index("idx_user", "user_id", "generated_at"),
        Index("idx_state", "state"),
        {"comment": "食养食疗计划（依赖报告/体质）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    report_id = Column(BigInteger, comment="生成依据的报告")
    template_type = Column(String(32), comment="依据测评类型 risk/nutrition/constitution")
    period = Column(Enum("week", "month", name="diet_plan_period"),
                    nullable=False, default="week", comment="周期 week 周计划 / month 月计划")
    title = Column(String(200), nullable=False, comment="方案名称")
    constitution = Column(String(64), comment="体质结论")
    target_symptoms = Column(JSON, comment="针对的慢病/症状")
    avoid_foods = Column(JSON, comment="忌口清单")
    summary_text = Column(Text, comment="方案总述（AI 生成，流式）")
    state = Column(
        Enum("generating", "basic", "complete", "failed", name="diet_plan_state"),
        nullable=False, default="generating", comment="状态",
    )
    references = Column(JSON, comment="引用来源")
    disclaimer = Column(String(255), comment="免责声明")
    generated_at = Column(DateTime, default=datetime.now, comment="生成时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class DietPlanRecipe(Base):
    __tablename__ = "diet_plan_recipe"
    __table_args__ = (
        Index("idx_plan", "plan_id", "day_no", "meal_type"),
        {"comment": "食养计划食谱明细"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plan_id = Column(BigInteger, nullable=False, comment="方案 ID")
    meal_type = Column(
        Enum("breakfast", "lunch", "dinner", "snack", name="meal_type"),
        nullable=False, comment="早/午/晚/加餐",
    )
    day_no = Column(SmallInteger, nullable=False, default=1, comment="第几天")
    dish_name = Column(String(200), nullable=False, comment="菜名")
    ingredients = Column(JSON, comment="食材清单")
    efficacy = Column(String(500), comment="功效说明")
    caution = Column(String(500), comment="注意事项/替代")


# --------------------------------------------------------------------------- #
# 3.18 打卡记录（原型：首页「本周打卡」/ 食养页「今日打卡 · 执行率」）
# --------------------------------------------------------------------------- #
class CheckinRecord(Base):
    __tablename__ = "checkin_record"
    __table_args__ = (
        Index("idx_user_date", "user_id", "checkin_date"),
        UniqueConstraint("user_id", "checkin_date", "check_type", name="uk_checkin_user_date_type"),
        {"comment": "健康打卡记录（食养计划执行跟踪）"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    plan_id = Column(BigInteger, comment="关联食养计划，可为空")
    checkin_date = Column(Date, nullable=False, comment="打卡日期")
    check_type = Column(
        Enum("plan", "meal", "exercise", "weight", name="checkin_type"),
        nullable=False, default="plan", comment="打卡类型",
    )
    note = Column(String(500), comment="备注")
    created_at = Column(DateTime, default=datetime.now, comment="打卡时间")


# --------------------------------------------------------------------------- #
# 3.19 站内消息通知（原型：顶栏「🔔 消息中心」）
# --------------------------------------------------------------------------- #
class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index("idx_user_read", "user_id", "read_at"),
        {"comment": "站内消息通知"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="接收人")
    type = Column(
        Enum("risk", "checkin", "consultation", "system", name="notification_type"),
        nullable=False, default="system", comment="通知类型",
    )
    title = Column(String(200), nullable=False, comment="标题")
    content = Column(String(1000), comment="正文")
    link = Column(String(300), comment="跳转路径（前端路由）")
    read_at = Column(DateTime, comment="已读时间，空=未读")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


# --------------------------------------------------------------------------- #
# 3.20 ~ 3.25 原型 v2 新增业务表（第 5/6 批）
# --------------------------------------------------------------------------- #
class BanquetMenu(Base):
    """社交宴请助手：一次宴请的菜单草稿与宾客约束。"""
    __tablename__ = "banquet_menu"
    __table_args__ = (Index("idx_user", "user_id", "created_at"), {"comment": "宴请菜单草稿"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="创建人")
    title = Column(String(200), nullable=False, comment="宴请主题")
    party_date = Column(Date, comment="宴请日期")
    people_count = Column(SmallInteger, comment="人数")
    budget = Column(Integer, comment="预算（元）")
    constraints = Column(JSON, comment="约束：预算/忌口/口味等")
    dishes = Column(JSON, comment="推荐菜品（智能荐菜产出）")
    status = Column(Enum("draft", "confirmed", name="banquet_status"),
                    nullable=False, default="draft", comment="状态")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


class BanquetGuest(Base):
    """宴请宾客的慢病/过敏/体质（用于荐菜风险标注）。"""
    __tablename__ = "banquet_guest"
    __table_args__ = (Index("idx_menu", "menu_id"), {"comment": "宴请宾客健康约束"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    menu_id = Column(BigInteger, nullable=False, comment="菜单 ID")
    name = Column(String(64), nullable=False, comment="宾客称呼")
    chronic_tags = Column(JSON, comment="慢病标签，如 ['糖尿病','高血压']")
    allergies = Column(JSON, comment="过敏/忌口")
    constitution = Column(String(64), comment="体质")


class TravelPlan(Base):
    """旅行美食智荐：出行计划与健康饮食建议。"""
    __tablename__ = "travel_plan"
    __table_args__ = (Index("idx_user", "user_id", "created_at"), {"comment": "旅行饮食计划"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    destination = Column(String(200), nullable=False, comment="目的地")
    start_date = Column(Date, comment="出发日期")
    end_date = Column(Date, comment="返程日期")
    health_notes = Column(JSON, comment="健康注意：慢病/忌口/携带药物等")
    advice = Column(JSON, comment="饮食建议与目的地推荐（留桩，待接 POI/知识库）")
    status = Column(Enum("planned", "ongoing", "done", name="travel_status"),
                    nullable=False, default="planned", comment="状态")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


class OcrReport(Base):
    """OCR 体检/病历报告解析（引擎待接入，当前留桩+交互）。"""
    __tablename__ = "ocr_report"
    __table_args__ = (Index("idx_user", "user_id", "created_at"), {"comment": "OCR 报告解析"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    file_name = Column(String(200), comment="原始文件名")
    file_url = Column(String(500), comment="对象存储地址")
    status = Column(Enum("uploaded", "parsing", "done", "failed", name="ocr_status"),
                    nullable=False, default="uploaded", comment="状态")
    raw_text = Column(Text, comment="OCR 原文（引擎产出）")
    note = Column(String(300), comment="说明，如『OCR 引擎待接入』")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


class OcrIndicator(Base):
    """从报告中提取的指标项与异常标记。"""
    __tablename__ = "ocr_indicator"
    __table_args__ = (Index("idx_report", "report_id"), {"comment": "OCR 提取指标"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False, comment="报告 ID")
    name = Column(String(100), nullable=False, comment="指标名")
    value = Column(String(64), comment="实测值")
    unit = Column(String(32), comment="单位")
    ref_range = Column(String(100), comment="参考范围")
    abnormal = Column(SmallInteger, default=0, comment="是否异常 0/1")


class DashboardLayout(Base):
    """运营大屏方案（组件拖拽组装与设置）。"""
    __tablename__ = "dashboard_layout"
    __table_args__ = (Index("idx_owner", "owner_id"), {"comment": "可视化大屏方案"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    owner_id = Column(String(64), nullable=False, comment="创建人")
    name = Column(String(200), nullable=False, comment="大屏名称")
    is_default = Column(SmallInteger, default=0, comment="是否默认 0/1")
    refresh_seconds = Column(Integer, default=300, comment="刷新周期（秒）")
    theme = Column(String(32), default="dark", comment="dark 深色运营 / light 浅色简洁")
    highlight_abnormal = Column(SmallInteger, default=1, comment="异常高亮 0/1")
    layout = Column(JSON, comment="组件布局 JSON")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class Text2SqlQuery(Base):
    """Text2SQL 历史提问（含行级权限范围，便于审计）。"""
    __tablename__ = "text2sql_query"
    __table_args__ = (Index("idx_user", "user_id", "created_at"), {"comment": "Text2SQL 查询历史"})

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment="提问人")
    question = Column(String(500), nullable=False, comment="自然语言问题")
    sql_text = Column(Text, comment="最终执行的 SQL")
    row_scope = Column(String(200), comment="行级权限过滤说明")
    status = Column(Enum("ok", "unsupported", "failed", name="text2sql_status"),
                    nullable=False, default="ok", comment="状态")
    row_count = Column(Integer, comment="返回行数")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")


__all__ = [
    "UserProfile", "DietitianProfile", "DietitianCustomer",
    "AssessmentTemplate", "AssessmentRecord", "AssessmentReport", "ReportAnnotation",
    "ChatSession", "ChatMessage", "Consultation", "ConsultationMessage",
    "OperationLog", "SystemConfig", "TongueImage", "DietPlan", "DietPlanRecipe",
    "DataDeletionTask", "CheckinRecord", "Notification",
    "BanquetMenu", "BanquetGuest", "TravelPlan",
    "OcrReport", "OcrIndicator", "DashboardLayout", "Text2SqlQuery",
]
