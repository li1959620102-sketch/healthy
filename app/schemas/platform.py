# -*- coding: utf-8 -*-
"""AI 智参平台 · 第一批 Pydantic 请求/响应模型（A/B/K/G/I）。

统一响应外层用 app.schemas.common.ApiResponse 包裹，这里只定义业务载荷。
"""
from datetime import date
from typing import Optional, Any, Dict, List, Literal
from pydantic import BaseModel, Field, field_validator

from app.core import rules


# --------------------------------------------------------------------------- #
# A. 认证
# --------------------------------------------------------------------------- #
class RegisterReq(BaseModel):
    username: Optional[str] = Field(None, description="登录账号（手机号注册时可留空，由后端自动生成）",
                                    examples=["alice"])
    password: str = Field(..., min_length=rules.PASSWORD_MIN_LEN,
                          max_length=rules.PASSWORD_MAX_LEN,
                          description=f"密码（{rules.PASSWORD_MIN_LEN}–{rules.PASSWORD_MAX_LEN} 位）",
                          examples=["alice@2026"])
    real_name: str = Field("", description="姓名")
    phone: Optional[str] = Field(None, description="手机号（密文存储；手机号注册必填）")
    verify_code: Optional[str] = Field(None, description="短信验证码（手机号注册必填，与 phone 成对）")
    role: Literal["user"] = Field(
        "user", description="公开注册固定为普通用户；专业角色由后台审核授予"
    )

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        """BV-01：填了手机号就必须是 11 位合法号段。"""
        if v is None or str(v).strip() == "":
            return None
        r = rules.check_phone(v)
        if not r.ok:
            raise ValueError(r.message)
        return r.value



class LoginReq(BaseModel):
    """登录请求：双通道 —— 账号登录传 username，手机号登录传 phone（二选一，都传以 username 为准）。"""
    username: Optional[str] = Field(None, description="登录账号（与 phone 二选一）", examples=["demo"])
    phone: Optional[str] = Field(None, description="手机号（与 username 二选一）")
    password: str = Field(..., description="密码", examples=["123456"])

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        if v is None or str(v).strip() == "":
            return None
        r = rules.check_phone(v)
        if not r.ok:
            raise ValueError(r.message)
        return r.value


class RefreshReq(BaseModel):
    refresh_token: str = Field(..., description="刷新令牌")


class SmsSendReq(BaseModel):
    phone: str = Field(..., description="手机号")


class TokenResp(BaseModel):
    access_token: str = Field(..., description="访问令牌")
    refresh_token: str = Field(..., description="刷新令牌")
    token_type: str = Field("Bearer", description="令牌类型")
    expires_in: int = Field(..., description="访问令牌有效期（秒）")
    user: Optional[dict] = Field(None, description="用户信息")


class GuestTokenResp(BaseModel):
    guest_token: str = Field(..., description="游客令牌（7 天有效）")
    expires_in: int = Field(..., description="有效期（秒）")


# --------------------------------------------------------------------------- #
# B. 用户
# --------------------------------------------------------------------------- #
class ProfileUpdateReq(BaseModel):
    height_cm: Optional[int] = Field(None, ge=rules.HEIGHT_MIN_CM, le=rules.HEIGHT_MAX_CM,
                                     description=f"身高 cm（{rules.HEIGHT_MIN_CM}–{rules.HEIGHT_MAX_CM}）")
    weight_kg: Optional[float] = Field(None, ge=rules.WEIGHT_MIN_KG, le=rules.WEIGHT_MAX_KG,
                                       description=f"体重 kg（{rules.WEIGHT_MIN_KG:g}–{rules.WEIGHT_MAX_KG:g}）")
    diseases: Optional[List[str]] = Field(None, description="疾病史（密文存储）")
    diet_preference: Optional[str] = Field(None, description="饮食偏好，如 素食/少油/忌辛辣")


class PasswordUpdateReq(BaseModel):
    old_password: str = Field(..., description="原密码")
    new_password: str = Field(..., min_length=rules.PASSWORD_MIN_LEN,
                              max_length=rules.PASSWORD_MAX_LEN,
                              description=f"新密码（{rules.PASSWORD_MIN_LEN}–{rules.PASSWORD_MAX_LEN} 位）")


# --------------------------------------------------------------------------- #
# K. 知识库
# --------------------------------------------------------------------------- #
class DocItem(BaseModel):
    id: str
    collection_name: Optional[str] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    embed_status: Optional[str] = None
    chunk_count: int = 0


class DocListResp(BaseModel):
    items: List[DocItem]
    total: int
    page: int
    page_size: int
    has_more: bool


# --------------------------------------------------------------------------- #
# G. 智能查询
# --------------------------------------------------------------------------- #
class ChatQueryReq(BaseModel):
    question: str = Field(..., description="用户问题", examples=["鸡蛋的营养成分"])
    session_id: Optional[int] = Field(None, description="会话 ID（多轮上下文，可空）")
    agent: str = Field("dietitian", description="智能体：dietitian/assessment/data/dish")


# --------------------------------------------------------------------------- #
# I. 看板（骨架：结构占位，数据待测评/报告模块）
# --------------------------------------------------------------------------- #
class TrendPoint(BaseModel):
    date: str
    score: Optional[float] = None
    level: Optional[str] = None


class DashboardTrendResp(BaseModel):
    template_id: Optional[int] = None
    points: List[TrendPoint]
    note: str = "测评数据来源于 E/F 组（第二批），当前为空"


class DashboardDimensionsResp(BaseModel):
    dimensions: List[dict] = []
    note: str = "维度得分来源于 assessment_report（第二批）"


class DashboardInsightResp(BaseModel):
    insight: Optional[str] = None
    disclaimer: str = "本内容由 AI 生成，不构成诊疗建议"
    note: str = "智能解读依赖报告生成（第二批）"


# --------------------------------------------------------------------------- #
# D. 测评模板
# --------------------------------------------------------------------------- #
class TemplateCreateReq(BaseModel):
    code: str = Field(..., description="模板编码，跨版本不变", examples=["NUTR_BASE"])
    name: str = Field(..., description="模板名称", examples=["成人基础营养测评"])
    type: str = Field("risk", description="类型 risk/nutrition/constitution/tongue")
    description: Optional[str] = Field(None, description="描述")
    dimension_config: List[dict] = Field(
        ..., description="维度与权重，如 [{'key':'basic','name':'基础营养','weight':0.4}]")
    question_config: Any = Field(
        ..., description="题目配置：list，或 {'questions':[...]}；"
                         "每题含 question_id/dimension/type(single|multi|number)/options/max_score/reverse")


class TemplateUpdateReq(BaseModel):
    name: Optional[str] = Field(None, description="模板名称")
    type: Optional[str] = Field(None, description="类型")
    description: Optional[str] = Field(None, description="描述")
    dimension_config: Optional[List[dict]] = Field(None, description="维度与权重")
    question_config: Optional[Any] = Field(None, description="题目配置")


class TemplateAuditReq(BaseModel):
    approve: bool = Field(True, description="true 通过 / false 驳回")
    remark: Optional[str] = Field(None, description="审核备注")


class TemplateItem(BaseModel):
    id: int
    code: Optional[str] = None
    version: Optional[int] = None
    name: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


# --------------------------------------------------------------------------- #
# E. 测评
# --------------------------------------------------------------------------- #
class StartAssessmentReq(BaseModel):
    template_id: int = Field(..., description="模板 ID")
    template_version: Optional[int] = Field(None, description="模板版本，不传取当前发布版本")


class SaveAnswersReq(BaseModel):
    answers: Any = Field(..., description="答案：{'q1':3} 或 [{'question_id':'q1','value':3}]")
    current_page: Optional[int] = Field(None, description="当前页")


class ClaimGuestReq(BaseModel):
    guest_token: str = Field(..., description="游客令牌")


# --------------------------------------------------------------------------- #
# F. 报告
# --------------------------------------------------------------------------- #
class AnnotationReq(BaseModel):
    content: str = Field(..., description="批注内容")


# --------------------------------------------------------------------------- #
# C. 营养师
# --------------------------------------------------------------------------- #
class DietitianApplyReq(BaseModel):
    cert_name: Optional[str] = Field(None, description="资格证名称")
    cert_no: Optional[str] = Field(None, description="证书编号")
    cert_url: Optional[str] = Field(None, description="资格证扫描件地址")
    work_proof_url: Optional[str] = Field(None, description="工作证明地址")
    specialties: Optional[List[str]] = Field(None, description="擅长领域，如 ['孕期营养','糖尿病饮食']")
    cases: Optional[str] = Field(None, description="服务案例（脱敏）")
    cert_expire_at: Optional[str] = Field(None, description="证书有效期 YYYY-MM-DD")


class DietitianUpdateReq(BaseModel):
    specialties: Optional[List[str]] = Field(None, description="擅长领域")
    cases: Optional[str] = Field(None, description="服务案例")
    cert_expire_at: Optional[str] = Field(None, description="证书有效期")


class BindByCodeReq(BaseModel):
    invite_code: str = Field(..., description="营养师提供的邀请码", examples=["A3F9K2"])


# --------------------------------------------------------------------------- #
# H. 咨询
# --------------------------------------------------------------------------- #
class ConsultCreateReq(BaseModel):
    question: Optional[str] = Field(None, description="咨询问题（作为首条消息）")
    type: str = Field("human", description="human 人工 / ai 智能")


class SendMsgReq(BaseModel):
    content: str = Field(..., description="消息内容（一期仅文本）")


class CloseConsultReq(BaseModel):
    reason: Optional[str] = Field(None, description="结束原因")


class RateConsultReq(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="满意度 1-5")
    comment: Optional[str] = Field(None, description="评价内容")


class AcceptConsultReq(BaseModel):
    """接入咨询（无额外参数，保留以便扩展）。"""
    note: Optional[str] = Field(None, description="接入备注")


# --------------------------------------------------------------------------- #
# J. 管理 / P. 运营大屏
# --------------------------------------------------------------------------- #
class UserStatusReq(BaseModel):
    status: str = Field(..., description="active 解封 / disabled 禁用", examples=["disabled"])


class AuditReq(BaseModel):
    approve: bool = Field(True, description="true 通过 / false 驳回")
    reason: Optional[str] = Field(None, description="驳回原因")


class AdminConfigReq(BaseModel):
    config_key: str = Field(..., description="配置键", examples=["scoring.weights"])
    config_value: str = Field(..., description="配置值（JSON 字符串或文本）")
    description: Optional[str] = Field(None, description="说明")


# --------------------------------------------------------------------------- #
# L. 食养计划 / M. 智能荐菜（原型新增）
# --------------------------------------------------------------------------- #
class PlanCreateReq(BaseModel):
    report_id: Optional[int] = Field(None, description="依据的测评报告（可选，不传自动取最近一次报告）")
    question: Optional[str] = Field(None, max_length=200,
                                    description="用户本次咨询的问题（可选）：用于场景匹配与方案总述，如“我有肾结石怎么食疗”")
    constitution: Optional[str] = Field(None, description="体质，如 痰湿/气虚")
    target_symptoms: Optional[List[str]] = Field(None, description="针对的慢病或症状")
    avoid_foods: Optional[List[str]] = Field(None, description="忌口清单")
    period: Optional[str] = Field("week", description="周期：week 周计划（默认，7 天）/ month 月计划（30 天）",
                                  examples=["week"])
    days: Optional[int] = Field(None, ge=1, le=31, description="计划天数；默认随 period（周 7 / 月 30）")


class PlanUpdateReq(BaseModel):
    title: Optional[str] = Field(None, description="方案名称")
    constitution: Optional[str] = Field(None, description="体质")
    target_symptoms: Optional[List[str]] = Field(None, description="针对问题")
    avoid_foods: Optional[List[str]] = Field(None, description="忌口")
    summary_text: Optional[str] = Field(None, description="方案总述")


class RecommendReq(BaseModel):
    query: Optional[str] = Field(None, description="想吃的食材/菜式，如 早餐 高蛋白")
    constitution: Optional[str] = Field(None, description="体质")
    avoid_foods: Optional[List[str]] = Field(None, description="忌口食材")


# --------------------------------------------------------------------------- #
# 打卡（原型：首页「本周打卡」/ 食养页「今日打卡 · 执行率」）
# --------------------------------------------------------------------------- #
class CheckinReq(BaseModel):
    check_type: str = Field("plan", description="plan 计划 / meal 用餐 / exercise 运动 / weight 体重",
                            examples=["plan"])
    plan_id: Optional[int] = Field(None, description="关联食养计划 ID")
    note: Optional[str] = Field(None, max_length=500, description="备注")
    checkin_date: Optional[date] = Field(None, description="打卡日期，缺省今天（YYYY-MM-DD）")


# --------------------------------------------------------------------------- #
# 智能体中心（主智能体调度）
# --------------------------------------------------------------------------- #
class DispatchReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=500,
                      description="自然语言需求，如「帮我做一周控糖食谱，顺便看下体检报告」",
                      examples=["帮我做一周控糖食谱，顺便看下体检报告"])


# --------------------------------------------------------------------------- #
# 原型 v2 场景类（宴请 / 旅行 / 大屏方案）
# --------------------------------------------------------------------------- #
class BanquetGuestItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="宾客称呼")
    chronic_tags: Optional[List[str]] = Field(None, description="慢病标签，如 ['糖尿病']")
    allergies: Optional[List[str]] = Field(None, description="过敏/忌口")
    constitution: Optional[str] = Field(None, description="体质")


class BanquetCreateReq(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="宴请主题")
    party_date: Optional[date] = Field(None, description="宴请日期")
    people_count: Optional[int] = Field(None, ge=1, le=200, description="人数")
    budget: Optional[int] = Field(None, ge=0, description="预算（元）")
    constraints: Optional[Dict[str, Any]] = Field(None, description="其他约束")
    guests: Optional[List[BanquetGuestItem]] = Field(None, description="宾客健康约束（最多 20 位）")


class BanquetUpdateReq(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    party_date: Optional[date] = None
    people_count: Optional[int] = Field(None, ge=1, le=200)
    budget: Optional[int] = Field(None, ge=0)
    status: Optional[str] = Field(None, description="draft / confirmed")
    guests: Optional[List[BanquetGuestItem]] = None


class TravelCreateReq(BaseModel):
    destination: str = Field(..., min_length=1, max_length=200, description="目的地")
    start_date: Optional[date] = Field(None, description="出发日期")
    end_date: Optional[date] = Field(None, description="返程日期")
    health_notes: Optional[Dict[str, Any]] = Field(None, description="慢病/忌口/随身药物等")


class DashboardLayoutReq(BaseModel):
    name: Optional[str] = Field(None, max_length=200, description="大屏名称")
    refresh_seconds: Optional[int] = Field(None, ge=30, le=3600, description="刷新周期（秒）")
    theme: Optional[str] = Field(None, description="dark 深色运营 / light 浅色简洁")
    highlight_abnormal: Optional[bool] = Field(None, description="异常高亮")
    layout: Optional[List[Dict[str, Any]]] = Field(None, description="组件布局")
