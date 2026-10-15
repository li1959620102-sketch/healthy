# -*- coding: utf-8 -*-
"""原型对齐集成测试（原型 index_v2.html）。

覆盖本次按原型补齐的聚合接口与配置口径：
  - 智能体中心 = **1 个主智能体（调度中枢）+ 6 个子智能体**，由主智能体调度其余六个
  - 五大健康场景 = 原型口径（健康饮食管家/慢病管理专家/社交宴请助手/妇幼健康守护/旅行美食智荐）
  - 首页 KPI 聚合（健康综合指数 / 本周打卡 / 待关注风险 / 食养计划周次）
  - 结构化风险提醒、健康上下文、营养师工作台 KPI
"""
from sqlalchemy import text


def test_agents_catalog_is_one_primary_plus_six_subs(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_ag", "user")
    r = client.get("/api/v1/agents", headers=headers).json()
    assert r["code"] == 0, r
    d = r["data"]
    items = d["items"]
    assert len(items) == 7, [a.get("name") for a in items]

    primary = [a for a in items if a.get("role") == "orchestrator"]
    subs = [a for a in items if a.get("role") == "sub"]
    assert len(primary) == 1 and len(subs) == 6, (primary, subs)
    assert d["primary_key"] == primary[0]["key"], d
    assert {a["name"] for a in subs} == {
        "健康测评智能体", "智能荐菜智能体", "食养食疗智能体", "健康管家智能体",
        "营养师智能体", "数据分析智能体"}, [a["name"] for a in subs]
    assert all(a["parent"] == primary[0]["key"] for a in subs), subs
    assert set(primary[0]["children"]) == {a["key"] for a in subs}, primary[0]
    assert all(a["enabled"] is True for a in items)


def test_primary_agent_dispatch_plan(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_dp", "user")

    r = client.post("/api/v1/agents/dispatch", headers=headers,
                    json={"text": "帮我做一周控糖食谱，顺便看下体检报告"}).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert [p["key"] for p in d["plan"]] == ["assessment", "diet"], d
    assert d["fallback"] is False, d
    assert d["primary"]["key"] == "orchestrator", d

    # 意图不明时不硬猜，落到通用营养问答兜底
    r2 = client.post("/api/v1/agents/dispatch", headers=headers, json={"text": "你好"}).json()
    assert r2["code"] == 0, r2
    assert r2["data"]["fallback"] is True, r2
    assert [p["key"] for p in r2["data"]["plan"]] == ["dietitian"], r2


def test_scenes_match_prototype(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_sc", "user")
    r = client.get("/api/v1/scenes", headers=headers).json()
    assert r["code"] == 0, r
    names = [s["name"] for s in r["data"]["items"]]
    assert names == ["健康饮食管家", "慢病管理专家", "社交宴请助手", "妇幼健康守护", "旅行美食智荐"], names


def test_home_overview_aggregate(client, auth_factory, run_id, cleanup):
    uid = f"{run_id}_ov"
    headers = auth_factory(uid, "user")
    cleanup.append(lambda s, u=uid: s.execute(
        text("delete from checkin_record where user_id=:u"), {"u": u}))

    client.post("/api/v1/checkins", headers=headers, json={"check_type": "plan"})

    r = client.get("/api/v1/dashboard/personal/overview", headers=headers).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["checkin"]["week_total"] == 7, d
    assert d["checkin"]["week_checked"] >= 1, d
    assert d["checkin"]["checked_today"] is True, d
    assert d["checkin_text"].endswith("/ 7"), d
    assert "risk_count" in d and "top_risks" in d
    assert "health_index" in d and "plan" in d


def test_personal_risks_empty_without_report(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_rk", "user")
    r = client.get("/api/v1/dashboard/personal/risks", headers=headers).json()
    assert r["code"] == 0, r
    assert r["data"]["items"] == [], r
    assert r["data"]["total"] == 0
    assert r["data"]["note"], r          # 无报告时给明确说明，不假装有数据


def test_health_context_structure(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_hc", "user")
    r = client.get("/api/v1/users/me/health-context", headers=headers).json()
    assert r["code"] == 0, r
    d = r["data"]
    for k in ("constitution", "key_risks", "allergies", "diet_preference",
              "suggestions", "disclaimer"):
        assert k in d, (k, d)
    assert isinstance(d["suggestions"], list) and d["suggestions"]
    assert "不构成诊疗建议" in d["disclaimer"]


def test_dietitian_overview_kpi(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_dt", "dietitian")
    r = client.get("/api/v1/nutritionists/me/overview", headers=headers).json()
    assert r["code"] == 0, r
    d = r["data"]
    assert d["customer_count"] == 0, d
    assert d["high_risk_count"] == 0, d
    assert d["today_consult_count"] == 0, d
    assert d["satisfaction"] is None, d


def test_dietitian_overview_requires_role(client, auth_factory, run_id):
    headers = auth_factory(f"{run_id}_u", "user")
    r = client.get("/api/v1/nutritionists/me/overview", headers=headers).json()
    assert r["code"] != 0, r
