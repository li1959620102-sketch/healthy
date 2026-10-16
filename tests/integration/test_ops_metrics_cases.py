# -*- coding: utf-8 -*-
"""批次 4-3 指标用例（故事 4）：DAU / MAU / 本月测评 / 智能咨询占比 / 模板使用率。

口径说明（本文件是这些口径的**可执行定义**，改实现请同步改这里）：

  · **活跃（DAU/MAU）**：窗口内出现过 登录 ∪ 提交测评 ∪ 发起咨询 ∪ 发起对话 的
    去重用户。刻意不只认 `sys_user.last_login_at` —— access token 2 小时且前端
    自动续期，用户"登录一次用一下午"不会再写登录记录，只认登录会系统性少算。
    因此这里专门造一个**从不登录、只提测评**的用户，断言它被算进 DAU。
  · **本月测评**：自然月（1 号零点起）累计，与"近 7 日滑动窗口"是两套口径。
  · **智能咨询占比**：分母只取 `sender_type='dietitian'` 的咨询消息，
    客户消息不得进入分母（否则比例被压扁，指标失去意义）。
  · **模板使用率**：分母只统计**归属到某个模板**的测评记录；把无归属的记录
    混进分母，所有模板占比之和就凑不到 1（下有用例专门守这条）。
"""
import re
from datetime import datetime

import pytest

from app.core.errors import ErrCode
from app.utils.db import get_session
from tests.integration.conftest import QUESTIONS

API = "/api/v1"
pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _ops(api, headers):
    """取运营大屏数据（管理员）。"""
    r = api.c.get(f"{API}/admin/dashboard/ops", headers=headers).json()
    assert r["code"] == 0, r
    return r["data"]


def _admin(api):
    return api.synthetic("ops_adm", "admin")


def _note_pair(note):
    """从「…（ai/dt）」里取出 (ai, dt) 两个计数。"""
    m = re.search(r"（(\d+)/(\d+)）", note or "")
    assert m, f"占比说明里没有计数：{note!r}"
    return int(m.group(1)), int(m.group(2))


def _answers():
    return [{"question_id": q["question_id"], "value": q["options"][0]["value"]}
            for q in QUESTIONS]


def _submitted(api, headers, template_id):
    """发起 → 答全 → 提交（走真实链路，产生 submitted_at）。清理已由 start 登记。"""
    started = api.start(headers, template_id)
    assert started.get("code") == 0, started
    rid = started["data"]["assessment_id"]
    saved = api.answer(rid, headers, _answers())
    assert saved.get("code") == 0, saved
    done = api.submit(rid, headers)
    assert done.get("code") == 0, done
    return done["data"]


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #
def test_ops_dashboard_requires_admin(api):
    """运营大屏只对管理员/运营开放；普通用户 10005，未登录 10001。"""
    plain = api.synthetic("ops_u", "user")
    r = api.c.get(f"{API}/admin/dashboard/ops", headers=plain).json()
    assert r["code"] == ErrCode.PERMISSION_DENIED, r

    r = api.c.get(f"{API}/admin/dashboard/ops").json()
    assert r["code"] in (ErrCode.UNAUTHENTICATED, ErrCode.PERMISSION_DENIED), r


# --------------------------------------------------------------------------- #
# 指标形状与自洽
# --------------------------------------------------------------------------- #
def test_ops_metrics_shape_and_consistency(api):
    """五个新指标齐备、类型正确，且内部自洽。"""
    d = _ops(api, _admin(api))

    for key in ("dau", "mau", "month_assessment"):
        assert key in d, f"缺少指标 {key}：{sorted(d)}"
        assert isinstance(d[key], int), (key, d[key])
    assert isinstance(d["ai_ratio"], float), d["ai_ratio"]
    assert d["active_note"] and "活跃口径" in d["active_note"], d.get("active_note")
    assert d["ai_ratio_note"] and "AI 辅助" in d["ai_ratio_note"], d.get("ai_ratio_note")

    # DAU ⊆ MAU ⊆ 注册用户：倒挂说明窗口算错了
    assert d["dau"] <= d["mau"] <= d["users"], d
    assert d["month_assessment"] <= d["assessments"], d
    assert 0.0 <= d["ai_ratio"] <= 1.0, d

    tu = d["template_usage"]
    assert isinstance(tu.get("items"), list) and tu["items"], tu
    assert tu["used_template_count"] <= tu["template_count"], tu
    assert tu["published_template_count"] <= tu["template_count"], tu
    assert tu["total_records"] == sum(i["records"] for i in tu["items"]), tu
    assert abs(sum(i["share"] for i in tu["items"]) - 1.0) < 0.01, tu
    for it in tu["items"]:
        assert set(it) >= {"template_id", "code", "name", "status", "records", "share"}, it


# --------------------------------------------------------------------------- #
# DAU 口径：不登录但用了产品，也算活跃
# --------------------------------------------------------------------------- #
def test_dau_counts_user_who_never_logged_in(api):
    """造一个**从不登录**、只提交一次测评的用户，DAU 必须 +1。

    这正是这套口径存在的理由：`last_login_at` 只反映"最后一次登录"，
    登录后保持会话继续使用的用户不会再写登录记录，只认登录会漏掉他们。
    """
    hdr = _admin(api)
    before = _ops(api, hdr)["dau"]

    u = api.synthetic_user("dau_nl")
    _submitted(api, u["headers"], api.template_id(code=api.uniq("D"),
                                                  name="DAU 口径验证模板"))

    after = _ops(api, hdr)["dau"]
    assert after == before + 1, (
        f"只提测评不登录的用户没被算进 DAU（{before} → {after}）："
        "活跃口径退化成只认 last_login_at 了")


def test_dau_not_double_counted_across_sources(api):
    """同一用户既提测评又发起咨询，仍然只算 1 个活跃用户（UNION 去重）。"""
    hdr = _admin(api)
    before = _ops(api, hdr)["dau"]

    cust = api.make_customer("dau_dup")
    con = api.c.post(f"{API}/consultations", headers=cust["headers"],
                     json={"question": "早餐怎么吃"}).json()
    assert con["code"] == 0, con
    api.clean_consult(con["data"]["consultation_id"])
    _submitted(api, cust["headers"], api.template_id(code=api.uniq("D"),
                                                     name="DAU 去重验证模板"))

    after = _ops(api, hdr)["dau"]
    assert after == before + 1, f"多来源活跃被重复计数（{before} → {after}）"


# --------------------------------------------------------------------------- #
# 智能咨询占比：分母只认营养师消息
# --------------------------------------------------------------------------- #
def test_ai_ratio_counts_only_dietitian_messages(api, approved_dietitian):
    """营养师发 1 条 AI 辅助 + 1 条手写，客户再发 2 条 → 分母只 +2。

    若把客户消息也塞进分母，占比会被稀释，指标就失去
    "AI 在营养师产出里占多大比重"的含义。
    """
    from tests.integration.test_ai_suggestion_cases import _open_consultation

    hdr = _admin(api)
    ai0, dt0 = _note_pair(_ops(api, hdr)["ai_ratio_note"])

    cid, cust, d_hdr = _open_consultation(api, approved_dietitian, "晚餐怎么控油")
    for body, h in (({"content": "少油少盐", "ai_assisted": True}, d_hdr),
                    ({"content": "另外多喝水", "ai_assisted": False}, d_hdr),
                    ({"content": "好的", "ai_assisted": True}, cust["headers"]),
                    ({"content": "谢谢", "ai_assisted": True}, cust["headers"])):
        r = api.c.post(f"{API}/consultations/{cid}/messages", headers=h, json=body).json()
        assert r["code"] == 0, r

    ai1, dt1 = _note_pair(_ops(api, hdr)["ai_ratio_note"])
    assert (ai1 - ai0, dt1 - dt0) == (1, 2), (
        f"分母应只含营养师消息：期望 (+1/+2)，实得 ({ai1 - ai0}/{dt1 - dt0})")


# --------------------------------------------------------------------------- #
# 模板使用率：归属到模板
# --------------------------------------------------------------------------- #
def test_template_usage_attributes_records(api):
    """新建模板提交 1 次后，该模板出现在清单里、计数为 1、占比 > 0，且不搬到别人头上。"""
    hdr = _admin(api)
    before = {i["template_id"]: i["records"]
              for i in _ops(api, hdr)["template_usage"]["items"]}

    tid = api.template_id(code=api.uniq("U"), name="使用率验证模板")
    u = api.synthetic_user("tplu")
    _submitted(api, u["headers"], tid)

    tu = _ops(api, hdr)["template_usage"]
    items = {i["template_id"]: i for i in tu["items"]}
    assert tid in items, f"新建并使用的模板未进入使用率清单：{sorted(items)}"
    assert items[tid]["records"] == 1, items[tid]
    assert items[tid]["share"] > 0, items[tid]
    assert items[tid]["status"] == "published", items[tid]

    # 增量正确：既有模板计数不受影响（没把本次记录算到别人头上）
    for k, v in before.items():
        if k in items:
            assert items[k]["records"] == v, (k, v, items[k])
    assert tu["total_records"] == sum(i["records"] for i in tu["items"]), tu


def test_template_usage_denominator_excludes_unattributed(api):
    """分母只认"归属到模板"的记录：造一条不属于任何模板的记录，占比之和仍为 1。

    这条守的是实现细节：分母若图省事写成 `COUNT(*) FROM assessment_record`，
    无归属的记录会把所有模板的占比一起拉低、占比之和凑不到 1，
    指标就从"某模板占多少"退化成一个没人能解释的数。
    这里把 template_id 指向一个不存在的模板来制造"无归属"。
    """
    from app.models.mysql.platform import AssessmentRecord
    from tests.integration.conftest import _ensure_sys_user

    orphan_uid = f"{api.run_id}_orphan"
    _ensure_sys_user(orphan_uid, "user")
    s = get_session()
    try:
        s.add(AssessmentRecord(user_id=orphan_uid, template_id=999999999,
                               template_version=1, status="completed",
                               submitted_at=datetime.now()))
        s.commit()
    finally:
        s.close()
    api.cleanup.append(lambda ss, u=orphan_uid: ss.query(AssessmentRecord)
                       .filter_by(user_id=u).delete())

    tu = _ops(api, _admin(api))["template_usage"]
    assert abs(sum(i["share"] for i in tu["items"]) - 1.0) < 0.01, (
        "无模板归属的记录被算进了分母，占比之和不再为 1")
    assert tu["total_records"] == sum(i["records"] for i in tu["items"]), tu
