# -*- coding: utf-8 -*-
"""营养师绑定与模板状态机用例（IT-N-01~13、IT-T-01~06/08~10，共 22 条）。

两条主线：
  · 营养师侧 —— 资质审核 / 邀请码绑定 / 同期唯一绑定 / 解绑后权限回收 / 批注；
  · 模板侧   —— 草稿→审核→发布→下线状态机 / 版本递增与锁定 / 权限。

判定口径以已落地实现为准；与旧用例集文本不一致处，在注释里写明**实现口径**，
并会在台账「备注」列同步，避免台账与代码两套说法。
"""
import pytest
from datetime import datetime

from app.core.errors import ErrCode

pytestmark = pytest.mark.integration

FULL = "A"      # 满分选项
ZERO = "D"      # 零分选项


def _answers(value: str, n_basic: int = 4, n_diet: int = 4):
    return {f"basic_{i}": value for i in range(n_basic)} | \
           {f"diet_{i}": value for i in range(n_diet)}


def _report_for(api, headers, value: str = FULL) -> int:
    """给该用户造一份报告（value 决定等级），返回 report_id。"""
    tpl_id = api.template_id()
    rec = api.record_id(headers, tpl_id)
    api.answer(rec, headers, _answers(value))
    sub = api.submit(rec, headers, key=api.uniq("kn"))
    assert sub["code"] == 0, sub
    return sub["data"]["report_id"]


# =========================================================================== #
# IT-N-01 资质提交：未审核不可访问业务能力，通过后放行
# =========================================================================== #
def test_it_n01_apply_then_gate(api):
    doc = api.make_dietitian(approve=False)

    me = api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()
    assert me["code"] == 0 and me["data"]["audit_status"] == "pending", me

    denied = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert denied["code"] == ErrCode.DIETITIAN_NOT_FOUND, denied

    assert api.audit_dietitian(doc["user_id"])["code"] == 0
    assert api.c.get("/api/v1/nutritionists/me/customers",
                     headers=doc["headers"]).json()["code"] == 0


# =========================================================================== #
# IT-N-02 审核驳回：可见驳回原因，可重新提交
# =========================================================================== #
def test_it_n02_audit_rejected_resubmit(api):
    doc = api.make_dietitian(approve=False)
    rejected = api.audit_dietitian(doc["user_id"], approve=False, reason="证书照片不清晰")
    assert rejected["code"] == 0, rejected
    assert rejected["data"]["audit_status"] == "rejected", rejected

    me = api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()
    assert me["data"]["audit_status"] == "rejected", me
    assert me["data"]["reject_reason"] == "证书照片不清晰", me["data"]

    # 重新提交 → 回到待审核
    again = api.dietitian_apply(doc["headers"])
    assert again["code"] == 0, again
    assert again["data"]["audit_status"] == "pending", again


# =========================================================================== #
# IT-N-03 邀请码绑定：绑定后出现在客户列表
# =========================================================================== #
def test_it_n03_invite_code_binding(api):
    doc = api.make_dietitian()
    customer = api.make_customer("C")
    code = api.invite_code(doc["headers"])

    bound = api.bind_by_code(customer["headers"], code)
    assert bound["code"] == 0, bound
    assert bound["data"]["dietitian_id"] == doc["user_id"], bound

    listed = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert customer["user_id"] in {i["customer_id"] for i in listed["data"]["items"]}, listed["data"]

    # 邀请码一次性：绑定成功即失效
    reuse = api.bind_by_code(api.make_customer("C2")["headers"], code)
    assert reuse["code"] != 0, reuse


# =========================================================================== #
# IT-N-04 同期只绑 1 名营养师
# =========================================================================== #
def test_it_n04_only_one_dietitian_at_a_time(api, db):
    """实现口径：改绑会**自动解除**旧关系（同期仅 1 名由 DB 唯一约束保证），
    而非回"已有绑定营养师请先解绑"的错误——两种都满足"同期唯一"，
    本实现选择对用户更顺滑的一条路。"""
    from app.models.mysql.platform import DietitianCustomer

    n1 = api.make_dietitian()
    n2 = api.make_dietitian()
    customer = api.make_customer("C")

    assert api.bind_by_code(customer["headers"], api.invite_code(n1["headers"]))["code"] == 0
    second = api.bind_by_code(customer["headers"], api.invite_code(n2["headers"]))
    assert second["code"] == 0, second

    db.rollback()
    actives = db.query(DietitianCustomer).filter_by(
        customer_id=customer["user_id"], status="active").all()
    assert len(actives) == 1, f"同期只能有一条生效绑定，实际 {len(actives)}"
    assert actives[0].dietitian_id == n2["user_id"], actives[0].dietitian_id

    # 旧营养师不再能看到该客户
    listed = api.c.get("/api/v1/nutritionists/me/customers", headers=n1["headers"]).json()
    assert customer["user_id"] not in {i["customer_id"] for i in listed["data"]["items"]}


# =========================================================================== #
# IT-N-05 咨询完成后自动绑定
# =========================================================================== #
def test_it_n05_auto_bind_after_consultation(api, db):
    from app.models.mysql.platform import DietitianCustomer

    doc = api.make_dietitian()
    customer = api.make_customer("C")

    created = api.c.post("/api/v1/consultations", headers=customer["headers"],
                         json={"question": "想请营养师帮我看看饮食结构", "type": "human"}).json()
    assert created["code"] == 0, created
    # 咨询对象主键字段名以实现为准：consultation_id（同 test_temporal_rules.py）
    cid = created["data"]["consultation_id"]

    accepted = api.c.post(f"/api/v1/consultations/{cid}/accept", headers=doc["headers"],
                          json={"dietitian_id": doc["user_id"]}).json()
    assert accepted["code"] == 0, accepted

    closed = api.c.post(f"/api/v1/consultations/{cid}/close", headers=doc["headers"],
                        json={"reason": "已给出建议"}).json()
    assert closed["code"] == 0, closed

    db.rollback()
    rel = db.query(DietitianCustomer).filter_by(
        customer_id=customer["user_id"], status="active").first()
    assert rel is not None and rel.dietitian_id == doc["user_id"], \
        "咨询结束后应自动建立绑定关系（绑定来源之一）"


# =========================================================================== #
# IT-N-06 / IT-N-08 解绑后与未绑定：客户报告一律 404
# =========================================================================== #
def test_it_n06_after_unbind_no_access(api, db):
    from app.models.mysql.platform import DietitianCustomer

    doc = api.make_dietitian()
    customer = api.make_customer("C")
    assert api.bind_by_code(customer["headers"], api.invite_code(doc["headers"]))["code"] == 0
    rid = _report_for(api, customer["headers"])

    # 绑定期间可看
    assert api.c.get(f"/api/v1/nutritionists/me/customers/{customer['user_id']}/reports",
                     headers=doc["headers"]).json()["code"] == 0

    # 客户主动解绑
    assert api.c.post("/api/v1/nutritionists/me/unbind",
                      headers=customer["headers"]).json()["code"] == 0

    denied = api.c.get(f"/api/v1/nutritionists/me/customers/{customer['user_id']}/reports",
                       headers=doc["headers"]).json()
    assert denied["code"] == ErrCode.RESOURCE_NOT_FOUND, denied       # 404 口径，非 403

    db.rollback()
    assert db.query(DietitianCustomer).filter_by(
        customer_id=customer["user_id"], status="active").count() == 0
    # 客户本人仍可查看自己的报告（IT-N-07 历史数据保留）
    assert api.c.get(f"/api/v1/reports/{rid}", headers=customer["headers"]).json()["code"] == 0


def test_it_n08_unbound_dietitian_cannot_see(api):
    owner = api.make_customer("owner")
    outsider = api.make_dietitian()
    rid = _report_for(api, owner["headers"])

    denied = api.c.get(f"/api/v1/nutritionists/me/customers/{owner['user_id']}/reports",
                       headers=outsider["headers"]).json()
    assert denied["code"] == ErrCode.RESOURCE_NOT_FOUND, denied
    assert api.c.get(f"/api/v1/reports/{rid}", headers=outsider["headers"]).json()["code"] == \
        ErrCode.RESOURCE_NOT_FOUND


# =========================================================================== #
# IT-N-07 解绑后历史数据保留（客户侧可见性不受影响）
# =========================================================================== #
def test_it_n07_customer_keeps_history_after_unbind(api):
    doc = api.make_dietitian()
    customer = api.make_customer("C")
    assert api.bind_by_code(customer["headers"], api.invite_code(doc["headers"]))["code"] == 0
    rid = _report_for(api, customer["headers"])

    assert api.c.post("/api/v1/nutritionists/me/unbind",
                      headers=customer["headers"]).json()["code"] == 0

    assert api.c.get(f"/api/v1/reports/{rid}", headers=customer["headers"]).json()["code"] == 0
    listed = api.c.get("/api/v1/assessments", headers=customer["headers"]).json()
    assert listed["code"] == 0 and listed["data"]["total"] >= 1, listed


# =========================================================================== #
# IT-N-09 客户筛选：按最近报告等级筛选
# =========================================================================== #
def test_it_n09_customer_filter_by_level(api):
    doc = api.make_dietitian()
    poor = api.make_customer("poor")
    good = api.make_customer("good")
    assert api.bind_by_code(poor["headers"], api.invite_code(doc["headers"]))["code"] == 0
    assert api.bind_by_code(good["headers"], api.invite_code(doc["headers"]))["code"] == 0

    _report_for(api, poor["headers"], ZERO)        # 全选最低分 → 待改善
    _report_for(api, good["headers"], FULL)        # 全选最高分 → 优秀

    allc = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert allc["data"]["total"] == 2, allc["data"]
    levels = {i["customer_id"]: i["latest_level"] for i in allc["data"]["items"]}
    assert levels[poor["user_id"]] == "needs_improvement", levels
    assert levels[good["user_id"]] == "excellent", levels

    filtered = api.c.get("/api/v1/nutritionists/me/customers?level=needs_improvement",
                         headers=doc["headers"]).json()
    ids = {i["customer_id"] for i in filtered["data"]["items"]}
    assert ids == {poor["user_id"]}, filtered["data"]


# =========================================================================== #
# IT-N-10 营养师被禁用：立即失去客户数据访问
# =========================================================================== #
def test_it_n10_disabled_dietitian_loses_access(api, db):
    from app.models.mysql.user import SysUser

    doc = api.make_dietitian()
    admin = api.synthetic("n10adm", "admin")
    assert api.c.get("/api/v1/nutritionists/me/customers",
                     headers=doc["headers"]).json()["code"] == 0

    assert api.c.put(f"/api/v1/admin/users/{doc['user_id']}/status", headers=admin,
                     json={"status": "disabled"}).json()["code"] == 0

    after = api.c.get("/api/v1/nutritionists/me/customers", headers=doc["headers"]).json()
    assert after["code"] in (ErrCode.PERMISSION_DENIED, ErrCode.UNAUTHENTICATED), after
    assert api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()["code"] not in (0,)

    # 解封后恢复
    assert api.c.put(f"/api/v1/admin/users/{doc['user_id']}/status", headers=admin,
                     json={"status": "active"}).json()["code"] == 0
    assert api.c.get("/api/v1/nutritionists/me", headers=doc["headers"]).json()["code"] == 0


# =========================================================================== #
# IT-N-11 禁用后客户处置：客户侧历史不受影响
# =========================================================================== #
def test_it_n11_customer_side_unaffected(api):
    doc = api.make_dietitian()
    customer = api.make_customer("C")
    assert api.bind_by_code(customer["headers"], api.invite_code(doc["headers"]))["code"] == 0
    rid = _report_for(api, customer["headers"])

    admin = api.synthetic("n11adm", "admin")
    assert api.c.put(f"/api/v1/admin/users/{doc['user_id']}/status", headers=admin,
                     json={"status": "disabled"}).json()["code"] == 0

    # 客户自身的报告与趋势不受营养师状态影响
    assert api.c.get(f"/api/v1/reports/{rid}", headers=customer["headers"]).json()["code"] == 0
    trend = api.c.get("/api/v1/dashboard/personal/trend", headers=customer["headers"]).json()
    assert trend["code"] == 0 and trend["data"]["points"], trend
    # 解绑状态可查（客户可另行选择营养师）
    assert api.c.get("/api/v1/nutritionists/search",
                     headers=customer["headers"]).json()["code"] == 0


# =========================================================================== #
# IT-N-12 批注报告：批注落库、用户端可见并标记已修订
# =========================================================================== #
def test_it_n12_annotate_report(api):
    doc = api.make_dietitian()
    customer = api.make_customer("C")
    assert api.bind_by_code(customer["headers"], api.invite_code(doc["headers"]))["code"] == 0
    rid = _report_for(api, customer["headers"])

    before = api.c.get(f"/api/v1/reports/{rid}", headers=customer["headers"]).json()
    assert before["data"]["revised"] is False, before["data"].get("revised")

    ann = api.c.put(f"/api/v1/reports/{rid}/annotation", headers=doc["headers"],
                    json={"content": "蛋白质摄入偏少，建议增加豆制品与鱼类的比例。"}).json()
    assert ann["code"] == 0, ann

    after = api.c.get(f"/api/v1/reports/{rid}", headers=customer["headers"]).json()
    assert after["data"]["revised"] is True, "批注后报告应标记为已修订"
    assert after["data"]["revised_at"], after["data"]
    contents = [a["content"] for a in after["data"]["annotations"]]
    assert any("蛋白质" in c for c in contents), contents
    assert after["data"]["score"] == before["data"]["score"], "批注不应改动评分"


# =========================================================================== #
# IT-N-13 非绑定批注（与 SEC-03 同口径，独立编号复核）
# =========================================================================== #
def test_it_n13_annotation_requires_binding(api):
    owner = api.make_customer("owner")
    outsider = api.make_dietitian()
    rid = _report_for(api, owner["headers"])

    r = api.c.put(f"/api/v1/reports/{rid}/annotation", headers=outsider["headers"],
                  json={"content": "非绑定营养师的批注"}).json()
    assert r["code"] == ErrCode.RESOURCE_NOT_FOUND, r


# =========================================================================== #
# IT-N-14 导出授权：导出行为写审计日志
# =========================================================================== #
def test_it_n14_export_writes_audit_log(api, db, minio_required):
    from app.models.mysql.platform import OperationLog

    doc = api.make_dietitian()
    customer = api.make_customer("C")
    assert api.bind_by_code(customer["headers"], api.invite_code(doc["headers"]))["code"] == 0
    _report_for(api, customer["headers"])

    r = api.c.get("/api/v1/nutritionists/me/customers/export?fmt=csv",
                  headers=doc["headers"]).json()
    assert r["code"] == 0, r
    assert r["data"]["format"] == "csv" and r["data"]["url"], r

    db.rollback()
    logs = db.query(OperationLog).filter_by(
        operator_id=doc["user_id"], action="customer.export").all()
    assert logs, "导出客户数据属敏感动作，必须写审计日志"
    assert logs[0].content.get("customer_count") == 1, logs[0].content


# =========================================================================== #
# 模板状态机与版本（IT-T）
# =========================================================================== #
def _new_template(api, admin, **kw):
    """建模板（草稿态），返回 (tpl_id, code)。"""
    r = api.make_template(publish=False, admin=admin, **kw)
    return r["data"]["id"], r["data"]["code"]


def test_it_t01_template_state_machine(api):
    admin = api.synthetic("t01adm", "admin")
    tpl_id, _ = _new_template(api, admin)

    draft = api.c.get(f"/api/v1/templates/{tpl_id}", headers=admin).json()
    assert draft["data"]["status"] == "draft", draft["data"]

    pending = api.c.post(f"/api/v1/templates/{tpl_id}/submit-audit", headers=admin).json()
    assert pending["code"] == 0 and pending["data"]["status"] == "pending", pending

    published = api.c.post(f"/api/v1/templates/{tpl_id}/audit", headers=admin,
                           json={"approve": True}).json()
    assert published["data"]["status"] == "published", published

    offline = api.c.post(f"/api/v1/templates/{tpl_id}/offline", headers=admin).json()
    assert offline["data"]["status"] == "offline", offline

    # 状态流转留痕：审核动作入审计日志
    logs = api.c.get("/api/v1/admin/operation-logs?action=template.offline",
                     headers=admin).json()
    assert logs["code"] == 0 and logs["data"]["total"] >= 1, logs["data"]


def test_it_t02_draft_pending_offline_invisible(api):
    admin = api.synthetic("t02adm", "admin")
    draft_id, _ = _new_template(api, admin)
    pending_id, _ = _new_template(api, admin)
    assert api.c.post(f"/api/v1/templates/{pending_id}/submit-audit",
                      headers=admin).json()["code"] == 0
    offline_id = api.template_id()
    assert api.c.post(f"/api/v1/templates/{offline_id}/offline", headers=admin).json()["code"] == 0

    user = api.synthetic("t02u", "user")
    visible = {t["id"] for t in api.c.get("/api/v1/templates", headers=user).json()["data"]["items"]}
    assert draft_id not in visible, "草稿不应出现在用户可见列表"
    assert pending_id not in visible, "待审核不应出现在用户可见列表"
    assert offline_id not in visible, "已下线不应出现在用户可见列表"


def test_it_t03_edit_creates_new_version(api):
    admin = api.synthetic("t03adm", "admin")
    tpl_id, code = _new_template(api, admin)
    assert api.c.post(f"/api/v1/templates/{tpl_id}/submit-audit", headers=admin).json()["code"] == 0
    assert api.c.post(f"/api/v1/templates/{tpl_id}/audit", headers=admin,
                      json={"approve": True}).json()["data"]["status"] == "published"

    edited = api.c.put(f"/api/v1/templates/{tpl_id}", headers=admin,
                       json={"name": f"改名-{api.run_id}"}).json()
    assert edited["code"] == 0, edited
    assert edited["data"]["version"] == 2, edited["data"]
    assert edited["data"]["status"] == "draft", "新版本需重新审核"
    assert edited["data"]["id"] != tpl_id, "新版本应是与旧版本并存的新行"

    # 原版本保持不变
    old = api.c.get(f"/api/v1/templates/{tpl_id}", headers=admin).json()
    assert old["data"]["version"] == 1 and old["data"]["status"] == "published", old["data"]


def test_it_t04_version_locking(api, db):
    from app.models.mysql.platform import AssessmentRecord

    admin = api.synthetic("t04adm", "admin")
    v1_id = api.template_id()
    user = api.synthetic("t04u", "user")
    rec_id = api.record_id(user, v1_id)
    api.answer(rec_id, user, {"basic_0": FULL})

    # 管理员发布 v2
    v2 = api.c.put(f"/api/v1/templates/{v1_id}", headers=admin,
                   json={"name": f"v2-{api.run_id}"}).json()
    assert v2["code"] == 0, v2
    assert api.c.post(f"/api/v1/templates/{v2['data']['id']}/submit-audit",
                      headers=admin).json()["code"] == 0
    assert api.c.post(f"/api/v1/templates/{v2['data']['id']}/audit", headers=admin,
                      json={"approve": True}).json()["code"] == 0

    # 已发起的测评仍锁定 v1
    db.rollback()
    rec = db.query(AssessmentRecord).filter_by(id=rec_id).first()
    assert rec.template_version == 1, f"测评应锁定发起时的版本，实际 {rec.template_version}"
    assert rec.template_id == v1_id, rec.template_id
    detail = api.c.get(f"/api/v1/assessments/{rec_id}", headers=user).json()
    assert detail["data"]["questions"], detail["data"]


def test_it_t05_offline_keeps_history_blocks_new(api):
    admin = api.synthetic("t05adm", "admin")
    tpl_id = api.template_id()
    user = api.synthetic("t05u", "user")
    rec_id = api.record_id(user, tpl_id)
    api.answer(rec_id, user)
    sub = api.submit(rec_id, user, key=api.uniq("k05"))
    assert sub["code"] == 0, sub
    rid = sub["data"]["report_id"]

    assert api.c.post(f"/api/v1/templates/{tpl_id}/offline", headers=admin).json()["code"] == 0

    # 历史报告仍可查看
    assert api.c.get(f"/api/v1/reports/{rid}", headers=user).json()["code"] == 0
    # 不能再基于它发起新测评
    again = api.start(user, tpl_id)
    assert again["code"] == ErrCode.TEMPLATE_NOT_FOUND, again


def test_it_t06_offline_allows_in_progress(api):
    admin = api.synthetic("t06adm", "admin")
    tpl_id = api.template_id()
    user = api.synthetic("t06u", "user")
    rec_id = api.record_id(user, tpl_id)

    # 下线时该测评进行中
    assert api.c.post(f"/api/v1/templates/{tpl_id}/offline", headers=admin).json()["code"] == 0

    saved = api.answer(rec_id, user)
    assert saved["code"] == 0, saved
    sub = api.submit(rec_id, user, key=api.uniq("k06"))
    assert sub["code"] == 0, "进行中的测评应允许完成后提交"


def test_it_t08_version_list(api):
    admin = api.synthetic("t08adm", "admin")
    tpl_id = api.template_id()
    edited = api.c.put(f"/api/v1/templates/{tpl_id}", headers=admin,
                       json={"name": f"v2-{api.run_id}"}).json()
    assert edited["code"] == 0, edited

    r = api.c.get(f"/api/v1/templates/{tpl_id}/versions", headers=admin).json()
    assert r["code"] == 0, r
    versions = [i["version"] for i in r["data"]["items"]]
    assert versions == [2, 1], versions                 # 倒序返回全部版本
    statuses = {i["version"]: i["status"] for i in r["data"]["items"]}
    assert statuses[1] == "published" and statuses[2] == "draft", statuses


def test_it_t09_non_creator_cannot_edit(api):
    owner = api.make_dietitian()
    other = api.make_dietitian()
    tpl_id, _ = _new_template(api, owner["headers"], creator=owner["headers"])

    r = api.c.put(f"/api/v1/templates/{tpl_id}", headers=other["headers"],
                  json={"name": "他人改名"}).json()
    assert r["code"] == ErrCode.PERMISSION_DENIED, r

    submit = api.c.post(f"/api/v1/templates/{tpl_id}/submit-audit",
                        headers=other["headers"]).json()
    assert submit["code"] == ErrCode.PERMISSION_DENIED, submit


def test_it_t10_report_records_scoring_version(api):
    """评审 M6：报告记录生成时的评分规则版本；改权重后旧报告版本不变。"""
    admin = api.synthetic("t10adm", "admin")
    user = api.synthetic("t10u", "user")

    rid_old = _report_for(api, user)
    old = api.c.get(f"/api/v1/reports/{rid_old}", headers=user).json()
    old_version = old["data"]["scoring_version"]

    new_version = f"v-test-{api.run_id}"
    api.cleanup.append(lambda s: s.execute(
        __import__("sqlalchemy").text(
            "DELETE FROM system_config WHERE config_key='scoring.version'")))
    cfg = api.c.put("/api/v1/admin/config", headers=admin,
                    json={"config_key": "scoring.version", "config_value": new_version}).json()
    assert cfg["code"] == 0, cfg

    rid_new = _report_for(api, user)
    fresh = api.c.get(f"/api/v1/reports/{rid_new}", headers=user).json()
    assert fresh["data"]["scoring_version"] == new_version, fresh["data"]

    # 旧报告的版本号不随配置变更而改变
    again = api.c.get(f"/api/v1/reports/{rid_old}", headers=user).json()
    assert again["data"]["scoring_version"] == old_version, again["data"]
