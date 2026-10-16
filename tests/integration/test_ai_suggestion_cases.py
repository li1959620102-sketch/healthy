# -*- coding: utf-8 -*-
"""营养师故事 4「使用 AI 生成咨询辅助建议并人工修改」用例。

分层验证：
  · 权限与状态闸门（未审核营养师 / 非接管人 / 未接入 / 已结束）；
  · 合规边界（高风险诉求拒答且**不调用模型**）；
  · 生成正确性（用假模型做确定性断言：prompt 组装、引用透传、草稿不落消息表）；
  · 如实降级（模型不可用 → state=basic，不编造）；
  · 来源标记（ai_assisted 只对营养师消息生效，客户消息不得被误标）。
另附一条 has_open 枚举取值的回归（原写 in_progress，实为 active）。
"""
import pytest
from sqlalchemy import text

from app.core.errors import ErrCode

API = "/api/v1"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
class _FakeLLM:
    """假模型：只实现 ainvoke，记录收到的消息，便于断言 prompt 组装。"""

    def __init__(self, text="1. 早餐加一份杂粮粥。\n2. 晚餐少盐少油。"):
        self.text = text
        self.calls = []

    async def ainvoke(self, msgs):
        self.calls.append(msgs)
        return type("_R", (), {"content": self.text})()


class _ExplodingLLM:
    """一旦被调用就报错的假模型：用来证明"拒答路径没有惊动模型"。"""

    async def ainvoke(self, msgs):                       # pragma: no cover
        raise AssertionError("高风险诉求不应调用模型")


def _open_consultation(api, make_dietitian, question: str):
    """造一条已接入（active）的咨询。

    返回 (consultation_id, 客户 headers, 已审核营养师 headers)。
    营养师必须**资质已审核通过**——本接口走 require_approved_dietitian，
    光有 dietitian 角色会被 30001 拦下（这正是审核闸门该有的行为）。
    """
    cust = api.make_customer("AS")
    con = api.c.post(f"{API}/consultations", headers=cust["headers"],
                     json={"question": question}).json()
    assert con["code"] == 0, con
    cid = con["data"]["consultation_id"]
    api.clean_consult(cid)
    hdr = make_dietitian(f"{api.run_id}_{api.uniq('asd')}")
    acc = api.c.post(f"{API}/consultations/{cid}/accept", headers=hdr).json()
    assert acc["code"] == 0, acc
    return cid, cust, hdr


# --------------------------------------------------------------------------- #
# 权限与状态闸门
# --------------------------------------------------------------------------- #
def test_suggestion_denied_for_plain_user(api, approved_dietitian):
    """普通用户无权调用：该能力属于营养师工作台。"""
    cid, cust, _ = _open_consultation(api, approved_dietitian, "高血压日常饮食怎么安排")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion",
                   headers=cust["headers"], json={}).json()
    assert r["code"] == ErrCode.PERMISSION_DENIED, r


def test_suggestion_denied_for_unapproved_dietitian(api, approved_dietitian):
    """已提交资质但**未审核通过**的营养师同样被拒（30001），不能靠 role 绕过审核闸门。"""
    cid, cust, _ = _open_consultation(api, approved_dietitian, "减脂期间主食怎么吃")
    hdr = api.synthetic("unapproved", "dietitian")    # 只有角色，没有资质档案
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == ErrCode.DIETITIAN_NOT_FOUND, r


def test_suggestion_denied_for_other_dietitian(api, approved_dietitian):
    """非本次咨询的接管营养师 → 90002（不泄露"咨询是否存在"）。"""
    cid, cust, _ = _open_consultation(api, approved_dietitian, "早餐吃什么比较好")
    other = approved_dietitian(f"{api.run_id}_other_d")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=other, json={}).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r


def test_suggestion_rejected_when_not_accepted(api, approved_dietitian):
    """尚未接入（pending）时，营养师**还不是当事人** → 统一 90002「咨询不存在」。

    这里刻意不返回"请先接入该咨询"：那等于告诉调用方"这个 ID 确实存在、只是没接入"，
    把咨询 ID 变成可枚举的。与评审 M3 / SEC-04 的防枚举口径保持一致
    （"未接入"分支由管理员视角覆盖，见下一条用例）。
    """
    cust = api.make_customer("PD")
    con = api.c.post(f"{API}/consultations", headers=cust["headers"],
                     json={"question": "晚餐怎么搭配"}).json()
    cid = con["data"]["consultation_id"]
    api.clean_consult(cid)
    hdr = approved_dietitian(f"{api.run_id}_{api.uniq('pd_d')}")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r
    assert "不存在" in r["message"], r


def test_suggestion_for_pending_seen_by_admin(api):
    """管理员是当事人，能走到"状态未接入"这一步 → 90001 并提示先接入。"""
    cust = api.make_customer("PA")
    con = api.c.post(f"{API}/consultations", headers=cust["headers"],
                     json={"question": "早餐怎么吃"}).json()
    cid = con["data"]["consultation_id"]
    api.clean_consult(cid)
    hdr = api.synthetic("pa_admin", "admin")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    assert "接入" in r["message"], r


def test_suggestion_rejected_when_closed(api, approved_dietitian):
    """已结束的咨询不再生成建议（避免营养师给已关闭的会话补建议发送失败）。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "午餐怎么吃")
    closed = api.c.post(f"{API}/consultations/{cid}/close",
                        headers=cust["headers"], json={"reason": "结束"}).json()
    assert closed["code"] == 0, closed
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == ErrCode.PARAM_INVALID, r
    assert "结束" in r["message"], r


# --------------------------------------------------------------------------- #
# 合规边界
# --------------------------------------------------------------------------- #
def test_suggestion_refuses_high_risk_without_calling_model(api, approved_dietitian, monkeypatch):
    """客户诉求是用药剂量 → 返回就医指引且**完全不调用模型**（边界划在模型之前）。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "我该吃多少降压药")
    from app.services import chat_query_service as cqs
    monkeypatch.setattr(cqs.chat_query_service, "_llm", _ExplodingLLM())
    monkeypatch.setattr(cqs.chat_query_service, "_ready", True)

    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["refused"] is True and d["state"] == "refused", d
    assert "医生" in d["suggestion"] or "就医" in d["suggestion"], d
    assert d["references"] == [], d


def test_suggestion_refuses_pregnancy_diet(api, approved_dietitian):
    """孕产妇特殊人群整类拒答（需求 V1.3 D4）。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "孕妇能吃山楂吗")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == 0, r
    assert r["data"]["refused"] is True, r
    assert r["data"]["state"] == "refused", r


# --------------------------------------------------------------------------- #
# 生成正确性（假模型）
# --------------------------------------------------------------------------- #
def test_suggestion_ok_and_prompt_assembly(api, approved_dietitian, monkeypatch):
    """正常路径：返回草稿、引用带 doc_id 可溯源、草稿**不落消息表**；prompt 含健康档案与补充要点。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "高血压日常饮食要注意什么")
    before = api.c.get(f"{API}/consultations/{cid}/messages", headers=hdr).json()
    before_n = before["data"]["total"]

    from app.services import chat_query_service as cqs
    fake = _FakeLLM("1. 每天食盐不超过 5 克。\n2. 增加深色蔬菜。")
    monkeypatch.setattr(cqs.chat_query_service, "_llm", fake)
    monkeypatch.setattr(cqs.chat_query_service, "_ready", True)
    monkeypatch.setattr(cqs.chat_query_service, "retrieve", lambda *a, **k: [
        {"doc_id": 101, "title": "高血压食养原则", "fragment": "限盐 5g/日",
         "source": "高血压食养指南.md", "score": 0.83},
        {"doc_id": 102, "title": "深色蔬菜", "fragment": "每日 300-500g",
         "source": "膳食指南.md", "score": 0.71},
    ])

    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr,
                   json={"hint": "客户在外卖上不好控制盐量"}).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["state"] == "ok" and d["refused"] is False, d
    assert d["suggestion"].startswith("1. 每天食盐"), d
    assert d["edited_required"] is True, d

    # 引用可溯源：必须带 doc_id（用例 A7-4 口径）
    assert [x["doc_id"] for x in d["references"]] == [101, 102], d
    assert d["references"][0]["title"] == "高血压食养原则", d

    # prompt 组装：客户健康档案 + 咨询对话 + 知识库片段 + 营养师补充要点
    assert fake.calls, "模型未被调用"
    user_text = fake.calls[0][1].content
    assert "【客户健康档案】" in user_text and "【咨询对话" in user_text, user_text[:200]
    assert "【知识库片段】" in user_text and "高血压食养原则" in user_text, user_text[:200]
    assert "【营养师补充要点】" in user_text and "外卖" in user_text, user_text[:200]
    assert "高血压日常饮食要注意什么" in user_text, "咨询对话应进入上下文"

    # 关键：草稿不落消息表（AI 绝不自动发给客户）
    after = api.c.get(f"{API}/consultations/{cid}/messages", headers=hdr).json()
    assert after["data"]["total"] == before_n, "生成草稿不应写入消息表"


def test_suggestion_degrades_honestly_without_model(api, approved_dietitian, monkeypatch):
    """模型不可用 → state=basic + 检索片段 + 明确提示，绝不编造"建议"。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "糖尿病主食怎么选")
    from app.services import chat_query_service as cqs
    monkeypatch.setattr(cqs.chat_query_service, "_llm", None)
    monkeypatch.setattr(cqs.chat_query_service, "_ready", True)
    monkeypatch.setattr(cqs.chat_query_service, "retrieve", lambda *a, **k: [
        {"doc_id": 201, "title": "主食选择", "fragment": "优先全谷物", "source": "指南.md",
         "score": 0.8},
    ])

    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["state"] == "basic", d
    assert "模型暂不可用" in d["suggestion"], d
    assert [x["doc_id"] for x in d["references"]] == [201], d
    assert "未经整理" in d["hint"] or "自行" in d["hint"], d


def test_suggestion_real_link_shape(api, approved_dietitian):
    """真实链路（不注入假模型）：只断言形状与状态取值，不假设模型一定可用。

    模型可用 → state=ok；模型/向量库不可用 → state=basic（如实降级）。
    两种都不允许出现"state=ok 但内容为空"这种假装成功。
    """
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "高血压人群的早餐怎么吃")
    r = api.c.post(f"{API}/consultations/{cid}/ai-suggestion", headers=hdr, json={}).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["state"] in ("ok", "basic", "refused"), d
    assert isinstance(d["references"], list), d
    assert isinstance(d["elapsed_ms"], int) and d["elapsed_ms"] >= 0, d
    if d["state"] == "ok":
        assert d["suggestion"].strip(), "state=ok 却内容为空：不能假装生成成功"


# --------------------------------------------------------------------------- #
# 来源标记
# --------------------------------------------------------------------------- #
def test_ai_assisted_flag_only_for_dietitian(api, approved_dietitian):
    """营养师带 ai_assisted=true → 落库 1；客户带 true → 仍为 0（防串标污染统计）。"""
    cid, cust, hdr = _open_consultation(api, approved_dietitian, "晚餐怎么控制油量")

    sent_d = api.c.post(f"{API}/consultations/{cid}/messages", headers=hdr,
                        json={"content": "1. 清蒸替代红烧。", "ai_assisted": True}).json()
    assert sent_d["code"] == 0, sent_d
    assert sent_d["data"]["ai_assisted"] is True, sent_d

    sent_u = api.c.post(f"{API}/consultations/{cid}/messages", headers=cust["headers"],
                        json={"content": "好的", "ai_assisted": True}).json()
    assert sent_u["code"] == 0, sent_u
    assert sent_u["data"]["ai_assisted"] is False, "客户消息不得被标记为 AI 辅助"

    rows = dict(api.db.execute(text(
        "SELECT content, ai_assisted FROM consultation_message WHERE consultation_id=:i"),
        {"i": cid}).fetchall())
    assert rows["1. 清蒸替代红烧。"] in (1, True), rows
    assert rows["好的"] in (0, False), rows

    # 消息列表如实回传来源标记
    msgs = api.c.get(f"{API}/consultations/{cid}/messages", headers=hdr).json()
    flags = {m["content"]: m["ai_assisted"] for m in msgs["data"]["items"]}
    assert flags["1. 清蒸替代红烧。"] is True, flags


# --------------------------------------------------------------------------- #
# has_open 枚举取值回归
# --------------------------------------------------------------------------- #
def test_has_open_counts_active_consultation(api):
    """`has_open` 必须把 active（已接入）也算作"未结束"。

    原实现写的是 `in_progress` —— 那是 assessment_status 的取值，
    consultation_status 的合法域是 pending/active/closed/timeout。
    于是"已接入"的咨询被判成"没有未结束的咨询"，用户可以重复发起。
    """
    from app.services.consultation_service import ConsultationService

    cust = api.make_customer("HO")
    uid = cust["user_id"]
    assert ConsultationService.has_open(uid) is False

    con = api.c.post(f"{API}/consultations", headers=cust["headers"],
                     json={"question": "第一个问题"}).json()
    cid = con["data"]["consultation_id"]
    api.clean_consult(cid)
    assert ConsultationService.has_open(uid) is True, "pending 应算未结束"

    hdr = api.synthetic("ho_d", "dietitian")
    api.c.post(f"{API}/consultations/{cid}/accept", headers=hdr)
    assert ConsultationService.has_open(uid) is True, "active（已接入）也必须算未结束"

    api.c.post(f"{API}/consultations/{cid}/close", headers=cust["headers"], json={})
    assert ConsultationService.has_open(uid) is False, "结束后应恢复可发起"
