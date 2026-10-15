# -*- coding: utf-8 -*-
"""原型 v2 第 5/6 批集成测试。

第 5 批（P1 余）：食养周/月计划、采购清单、导出客户报表、Text2SQL 改造、报告 PDF。
第 6 批（P2，留桩+交互）：宴请助手、旅行智荐、OCR、大屏方案、智能体监控、数字人。

约定：造数带 run_id 后缀并登记清理；涉及 LLM 的「方案总述」在用例内打桩以保证快且确定。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.errors import ErrCode
from app.dao.assessment_dao import ReportDAO, RecordDAO, session_scope
from app.services.plan_service import PlanService


async def _fake_summary(ctx, reason=""):
    return "【测试】食养方案总述", True


def _rm_minio(name_contains: str) -> int:
    """删除对象存储中名称含指定片段的测试产物（导出/OCR/PDF）。"""
    from app.utils.minio_client import get_minio

    mc = get_minio()
    n = 0
    for o in mc.client.list_objects(mc.bucket_name, recursive=True):
        if name_contains in o.object_name:
            mc.client.remove_object(mc.bucket_name, o.object_name)
            n += 1
    return n


def _seed_plan(session, uid, period="week", days=7, skills=None):
    from app.dao.plan_dao import PlanDAO
    plan = PlanDAO.create({
        "user_id": uid, "template_type": "控糖", "period": period,
        "title": f"控糖食养{'周' if period == 'week' else '月'}方案（{days}天）",
        "constitution": "痰湿", "target_symptoms": ["血糖偏高"],
        "avoid_foods": ["海鲜"], "state": "complete", "summary_text": "测试总述",
    }, session=session)
    PlanDAO.add_recipes(plan.id, [
        {"meal_type": "breakfast", "day_no": 1, "dish_name": "燕麦鸡蛋",
         "ingredients": [{"name": "燕麦"}, {"name": "鸡蛋"}, {"name": "海鲜"}], "efficacy": "-"},
        {"meal_type": "lunch", "day_no": 1, "dish_name": "杂粮饭",
         "ingredients": [{"name": "燕麦"}, {"name": "糙米"}], "efficacy": "-"},
    ], session=session)
    return plan.id


def _seed_report(session, uid):
    rec = RecordDAO.create({
        "user_id": uid, "template_id": 1, "template_version": 1,
        "status": "completed", "answers": {}, "started_at": datetime.now(),
    }, session=session)
    rep = ReportDAO.create({
        "record_id": rec.id, "user_id": uid, "template_id": 1, "template_version": 1,
        "state": "complete", "score": 72, "standardized_score": 72,
        "level": "needs_improvement",
        "dimension_scores": [{"key": "basic", "name": "营养均衡", "score": 55,
                              "level": "needs_improvement", "level_name": "待改善"}],
        "suggestions": [{"item": "营养均衡", "note": "建议优先改善"}],
        "analysis_text": "测试分析文本", "disclaimer": "本内容由 AI 生成，不构成诊疗建议",
    }, session=session)
    return rec.id, rep.id


# ------------------------------------------------------------------ #
# 第 5 批
# ------------------------------------------------------------------ #
def test_plan_period_week_and_month(client, auth_factory, run_id, cleanup, monkeypatch):
    monkeypatch.setattr(PlanService, "generate_summary", _fake_summary)
    uid = f"{run_id}_pd"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: (
        s.execute(text("delete from diet_plan_recipe where plan_id in "
                       "(select id from diet_plan where user_id=:u)"), {"u": u}),
        s.execute(text("delete from diet_plan where user_id=:u"), {"u": u})))

    w = client.post("/api/v1/diet-plans", headers=headers,
                    json={"period": "week", "constitution": "痰湿"}).json()
    assert w["code"] == 0, w
    assert w["data"]["period"] == "week", w
    assert w["data"]["days"] and len(w["data"]["days"]) <= 7, w["data"]

    m = client.post("/api/v1/diet-plans", headers=headers,
                    json={"period": "month", "constitution": "痰湿"}).json()
    assert m["code"] == 0, m
    assert m["data"]["period"] == "month", m

    bad = client.post("/api/v1/diet-plans", headers=headers,
                      json={"period": "quarter"}).json()
    assert bad["code"] == ErrCode.PARAM_INVALID, bad


def test_plan_shopping_list_aggregates_and_excludes_avoid(client, auth_factory,
                                                          run_id, cleanup):
    uid = f"{run_id}_sl"
    headers = auth_factory(uid, "user")
    plan_id = None
    with session_scope() as s:
        plan_id = _seed_plan(s, uid, period="week", days=7)
    cleanup.append(lambda s, p=plan_id: (
        s.execute(text("delete from diet_plan_recipe where plan_id=:p"), {"p": p}),
        s.execute(text("delete from diet_plan where id=:p"), {"p": p})))

    r = client.get(f"/api/v1/diet-plans/{plan_id}/shopping-list", headers=headers).json()
    assert r["code"] == 0, r
    names = [i["name"] for i in r["data"]["items"]]
    assert "燕麦" in names and names.count("燕麦") == 1, names   # 去重
    assert "海鲜" not in names, names                            # 忌口剔除
    assert r["data"]["total"] == len(names)


def test_dietitian_export_customers(client, auth_factory, run_id, cleanup, minio_required):
    uid = f"{run_id}_ex"
    headers = auth_factory(uid, "dietitian")
    cleanup.append(lambda s, u=uid: _rm_minio(u))          # 清理 MinIO 导出产物
    r = client.get("/api/v1/nutritionists/me/customers/export?fmt=csv", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["format"] == "csv"
    assert r["data"]["url"] and r["data"]["size"] >= 0, r
    bad = client.get("/api/v1/nutritionists/me/customers/export?fmt=pdf", headers=headers).json()
    assert bad["code"] == ErrCode.PARAM_INVALID, bad


def test_text2sql_row_level_permission_and_reject(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_t2s"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from text2sql_query where user_id=:u"), {"u": u}))

    r = client.post("/api/v1/analytics/text2sql/query",
                    params={"question": "统计测评等级分布"}, headers=headers).json()
    assert r["code"] == 0, r
    assert "user_id = :uid" in r["data"]["sql"], r["data"]["sql"]   # 普通用户只能看自己
    assert r["data"]["row_scope"] == "仅本人数据", r
    assert r["data"]["chart"] == "bar"
    assert r["data"]["interpretation"], r

    # 不支持的问法必须明确拒绝，而不是硬拼 SQL
    bad = client.post("/api/v1/analytics/text2sql/query",
                      params={"question": "帮我删掉所有用户"}, headers=headers).json()
    assert bad["code"] == ErrCode.PARAM_INVALID, bad

    hist = client.get("/api/v1/analytics/text2sql/history", headers=headers).json()
    assert hist["code"] == 0, hist
    assert hist["data"]["total"] >= 1, hist


def test_text2sql_admin_scope_is_platform_wide(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_adm2"
    headers = auth_factory(uid, "admin")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from text2sql_query where user_id=:u"), {"u": u}))
    r = client.post("/api/v1/analytics/text2sql/query",
                    params={"question": "测评模板使用次数"}, headers=headers).json()
    assert r["code"] == 0, r
    assert "1=1" in r["data"]["sql"], r["data"]["sql"]
    assert r["data"]["row_scope"] == "全平台（管理员）", r


def test_report_pdf_generates_real_file(client, auth_factory, run_id, cleanup):
    pytest.importorskip("reportlab", reason="本机未安装已声明的 PDF 运行时依赖")
    uid = f"{run_id}_pdf"
    headers = auth_factory(uid, "user")
    with session_scope() as s:
        rec_id, rep_id = _seed_report(s, uid)
    cleanup.append(lambda s, r=rec_id: (
        s.execute(text("delete from assessment_report where record_id=:r"), {"r": r}),
        s.execute(text("delete from assessment_record where id=:r"), {"r": r})))
    cleanup.append(lambda s, r=rep_id: _rm_minio(f"reports/{r}_"))   # 清理 PDF 产物

    r = client.get(f"/api/v1/reports/{rep_id}/pdf", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["size"] > 800, r                    # 真 PDF，不是占位
    assert r["data"]["url"] and "X-Amz-Signature" in r["data"]["url"], r


# ------------------------------------------------------------------ #
# 第 6 批（留桩 + 交互）
# ------------------------------------------------------------------ #
def test_banquet_create_and_risk_flagging(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_bq"
    headers = auth_factory(uid, "user")
    r = client.post("/api/v1/banquets", headers=headers, json={
        "title": "家庭聚餐", "people_count": 4, "budget": 300,
        "guests": [{"name": "张先生", "chronic_tags": ["糖尿病"], "allergies": ["海鲜"]}],
    }).json()
    assert r["code"] == 0, r
    menu_id = r["data"]["menu_id"]
    cleanup.append(lambda s, m=menu_id: (
        s.execute(text("delete from banquet_guest where menu_id=:m"), {"m": m}),
        s.execute(text("delete from banquet_menu where id=:m"), {"m": m})))

    assert r["data"]["status"] == "draft"
    assert r["data"]["guests"][0]["name"] == "张先生"
    assert len(r["data"]["dishes"]) >= 1, r

    # 忌口（海鲜）不应出现在菜单食材里
    all_ings = [i for d in r["data"]["dishes"] for i in d["ingredients"]]
    assert "海鲜" not in all_ings, all_ings

    detail = client.get(f"/api/v1/banquets/{menu_id}", headers=headers).json()
    assert detail["code"] == 0 and detail["data"]["menu_id"] == menu_id


def test_travel_plan_is_stubbed_but_interactive(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_tv"
    headers = auth_factory(uid, "user")
    r = client.post("/api/v1/travel-plans", headers=headers, json={
        "destination": "成都", "health_notes": {"avoid_foods": ["辛辣"]}}).json()
    assert r["code"] == 0, r
    plan_id = r["data"]["plan_id"]
    cleanup.append(lambda s, p=plan_id: s.execute(
        text("delete from travel_plan where id=:p"), {"p": p}))

    advice = r["data"]["advice"]
    assert advice["enabled"] is False, advice            # 明确未启用，不伪造推荐
    assert advice["general_tips"], advice
    assert r["data"]["destination"] == "成都"


def test_ocr_upload_is_stubbed_with_status(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_ocr"
    headers = auth_factory(uid, "user")
    r = client.post("/api/v1/ocr-reports", headers=headers,
                    files={"file": ("tijian.jpg", b"\xff\xd8\xff\xe0fake", "image/jpeg")}).json()
    assert r["code"] == 0, r
    rep_id = r["data"]["report_id"]
    cleanup.append(lambda s, i=rep_id: s.execute(
        text("delete from ocr_report where id=:i"), {"i": i}))
    cleanup.append(lambda s: _rm_minio("ocr/"))          # 清理 OCR 上传产物

    assert r["data"]["status"] == "uploaded"
    assert r["data"]["recognizable"] is False
    assert r["data"]["indicators"] == []
    assert "待接入" in r["data"]["note"]

    detail = client.get(f"/api/v1/ocr-reports/{rep_id}", headers=headers).json()
    assert detail["code"] == 0 and detail["data"]["recognizable"] is False


def test_ops_dashboard_config_save_and_snapshot(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_dash"
    headers = auth_factory(uid, "admin")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from dashboard_layout where owner_id=:u"), {"u": u}))

    r = client.get("/api/v1/admin/dashboard/ops/config", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["source"] == "default"
    assert len(r["data"]["layout"]) >= 4, r

    saved = client.put("/api/v1/admin/dashboard/ops/config", headers=headers, json={
        "name": "测试大屏", "refresh_seconds": 600, "theme": "light",
        "highlight_abnormal": False,
    }).json()
    assert saved["code"] == 0, saved
    assert saved["data"]["name"] == "测试大屏"
    assert saved["data"]["refresh_seconds"] == 600

    snap = client.post("/api/v1/admin/dashboard/ops/snapshot", headers=headers).json()
    assert snap["code"] == 0, snap
    assert snap["data"]["enabled"] is False, snap        # 截图渲染待接入，不伪造图片
    assert "待接入" in snap["data"]["note"]


def test_agent_monitor_and_digital_human_stub(client, auth_factory, run_id):
    uid = f"{run_id}_dh"
    headers = auth_factory(uid, "user")

    mon = client.get("/api/v1/analytics/agents/monitor", headers=headers).json()
    assert mon["code"] == 0, mon
    assert mon["data"]["total"] == 7, mon      # 1 主智能体 + 6 子智能体
    assert mon["data"]["running"] == 7, mon
    assert mon["data"]["pending"] == 0, mon

    cfg = client.get("/api/v1/digital-human/config", headers=headers).json()
    assert cfg["code"] == 0, cfg
    assert cfg["data"]["capabilities"]["text_chat"] is True
    assert cfg["data"]["capabilities"]["asr"] is False   # 语音待 S5
    assert cfg["data"]["chat_endpoint"] == "/api/v1/chat/query"

    voice = client.post("/api/v1/digital-human/voice-command", headers=headers, json={}).json()
    assert voice["code"] == 0, voice
    assert voice["data"]["enabled"] is False, voice      # 未识别不装成功
    assert voice["data"]["recognized_text"] is None
