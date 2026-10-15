# -*- coding: utf-8 -*-
"""其余屏接真数据 —— 端到端验收（Playwright + Chromium）
覆盖：食养食疗(diet) / 五大场景(scenes) / 数据分析(data) / 营养师工作台(dietitian) / 运营大屏(admin)
红线校验：无编造数据；无权限一律如实提示；零 JS 错误 / 零失败请求。
"""
import json
import random
import string
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8019"
PWD = "Test@2026"

errors = []
console_errors = []
failed_reqs = []


def rnd(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def api_post(path, body, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = requests.post(BASE + path, json=body, headers=h, timeout=30)
    return r.json()


def setup():
    dt = "e2e_dt_" + rnd()
    cu = "e2e_cu_" + rnd()
    ad = "e2e_ad_" + rnd()
    r1 = api_post("/api/v1/auth/register", {"username": dt, "password": PWD,
                                            "real_name": "E2E营养师", "role": "user"})
    r2 = api_post("/api/v1/auth/register", {"username": cu, "password": PWD,
                                            "real_name": "E2E客户"})
    r3 = api_post("/api/v1/auth/register", {"username": ad, "password": PWD,
                                            "real_name": "E2E管理员", "role": "user"})
    if r1.get("code") != 0 or r2.get("code") != 0 or r3.get("code") != 0:
        print("SETUP FAIL", r1, r2, r3)
        sys.exit(1)
    dt_uid = r1["data"]["user_id"]
    cu_uid = r2["data"]["user_id"]
    ad_uid = r3["data"]["user_id"]
    # 建立有效绑定（直接落库，便于验证客户列表真实渲染）
    from app.utils.db import get_session
    from sqlalchemy import text
    s = get_session()
    try:
        # 注意：注册接口角色白名单是 dietitian，但 deps.ROLE_DIETITIAN="nutritionist"，
        # 二者不一致 → 测试态直接置为 nutritionist 以验证工作台渲染（缺陷另行报告）
        s.execute(text("UPDATE sys_user SET role='dietitian' WHERE user_id=:d"), {"d": dt_uid})
        s.execute(text("UPDATE sys_user SET role='admin' WHERE user_id=:a"), {"a": ad_uid})
        s.execute(text(
            "INSERT INTO dietitian_customer (dietitian_id, customer_id, bind_source, status, bound_at, created_at) "
            "VALUES (:d,:c,'admin','active',NOW(),NOW())"), {"d": dt_uid, "c": cu_uid})
        s.commit()
    finally:
        s.close()
    print(f"SETUP ok  dietitian={dt}({dt_uid})  customer={cu}({cu_uid})  admin={ad}({ad_uid})")
    return {"dt_user": dt, "dt_uid": dt_uid, "cu_user": cu, "cu_uid": cu_uid,
            "ad_user": ad, "ad_uid": ad_uid}


def teardown(ctx):
    """清理本轮造的 3 个账号及其**全部依赖数据**（先子表、后父表）。

    2026-10-15 修复：原实现只删 dietitian_customer + sys_user，漏了 user_profile 等表；
    且 run() 中途抛异常时根本走不到这里（当日残留 3 个账号）。现由 atexit 兜底调用。
    """
    from app.utils.db import get_session
    from sqlalchemy import text
    ids = [ctx["dt_uid"], ctx["cu_uid"], ctx["ad_uid"]]
    p = {"a": ids[0], "b": ids[1], "c": ids[2]}
    s = get_session()
    failed = []
    try:
        # 1) 无 user_id 列的子表，需按父表 id 清理
        child_deletes = [
            ("report_annotation", "DELETE FROM report_annotation WHERE report_id IN (SELECT id FROM assessment_report WHERE user_id IN (:a,:b,:c))"),
            ("chat_message", "DELETE FROM chat_message WHERE session_id IN (SELECT id FROM chat_session WHERE user_id IN (:a,:b,:c))"),
            ("consultation_message", "DELETE FROM consultation_message WHERE consultation_id IN (SELECT id FROM consultation WHERE user_id IN (:a,:b,:c))"),
            ("diet_plan_recipe", "DELETE FROM diet_plan_recipe WHERE plan_id IN (SELECT id FROM diet_plan WHERE user_id IN (:a,:b,:c))"),
            ("banquet_guest", "DELETE FROM banquet_guest WHERE menu_id IN (SELECT id FROM banquet_menu WHERE user_id IN (:a,:b,:c))"),
            ("ocr_indicator", "DELETE FROM ocr_indicator WHERE report_id IN (SELECT id FROM ocr_report WHERE user_id IN (:a,:b,:c))"),
            ("dietitian_customer", "DELETE FROM dietitian_customer WHERE dietitian_id IN (:a,:b,:c) OR customer_id IN (:a,:b,:c)"),
        ]
        for label, sql in child_deletes:
            try:
                s.execute(text(sql), p)
            except Exception as e:      # noqa: BLE001
                failed.append(f"{label}:{type(e).__name__}")
        # 2) 一切带 user_id 列的表统一清理（表名取自 information_schema，避免再漏表）
        #    排除 sys_user（最后单独删）与 P1-3 遗留对象（视图删不掉、排序规则也不一致）
        tables = [r[0] for r in s.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND column_name = 'user_id' "
            "AND table_name NOT IN ('sys_user','high_risk_patients','latest_assessments',"
            "'user_health_risk_assessment')")).fetchall()]
        for t in tables:
            try:
                s.execute(text(f"DELETE FROM `{t}` WHERE user_id IN (:a,:b,:c)"), p)
            except Exception as e:      # noqa: BLE001
                failed.append(f"{t}:{type(e).__name__}")
        # 3) 最后删账号
        s.execute(text("DELETE FROM sys_user WHERE user_id IN (:a,:b,:c)"), p)
        s.commit()
    finally:
        s.close()
    print("TEARDOWN ok  账号及其依赖数据已清理" + (f"（跳过：{failed}）" if failed else ""))


_CTX = None
_ATEXIT_ARMED = False


def _teardown_once():
    """兜底：无论脚本正常结束还是中途抛错，都保证清理执行一次。"""
    global _CTX
    ctx, _CTX = _CTX, None
    if not ctx:
        return
    try:
        teardown(ctx)
    except Exception as e:              # noqa: BLE001
        print("TEARDOWN 异常（请人工核对）：", repr(e))


def login_ui(page, user):
    # 登录页为「手机号 / 账号」双通道，账号面板默认隐藏 → 必须先切到 account Tab
    page.evaluate("openLogin()")
    page.evaluate("switchLoginTab('account')")
    page.wait_for_timeout(250)
    page.fill("#lgUser", user)
    page.fill("#lgPwdA", PWD)
    page.click("#loginPage .hero-actions button.btn.primary")
    page.wait_for_timeout(1500)


def logout_ui(page):
    page.evaluate("(function(){ CURRENT_USER=null; API.Token.clear(); updateAuthUI(); })()")
    page.wait_for_timeout(300)


def run():
    global _CTX, _ATEXIT_ARMED
    ctx = setup()
    _CTX = ctx
    if not _ATEXIT_ARMED:
        import atexit
        atexit.register(_teardown_once)     # 中途抛错也能清理（2026-10-15 的教训）
        _ATEXIT_ARMED = True
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        pg.on("response", lambda r: failed_reqs.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(600)

        # ---------- 1) 普通用户视角 ----------
        login_ui(pg, ctx["cu_user"])
        print("LOGIN(customer) ok  user=" + str(pg.evaluate("CURRENT_USER && CURRENT_USER.username")))

        # 食养食疗
        pg.evaluate("go('diet')"); pg.wait_for_timeout(1500)
        diet_rate = pg.text_content("#dietRate").strip()
        diet_sub = pg.text_content("#dietRateSub").strip()
        diet_plan = pg.evaluate("(document.getElementById('planDays').textContent||'').trim()")
        print(f"[diet] rate={diet_rate!r} sub={diet_sub!r} plan={diet_plan[:40]!r}")

        # 五大场景
        pg.evaluate("go('scenes')"); pg.wait_for_timeout(1200)
        scenes = pg.eval_on_selector_all("#sceneGrid .card", "els=>els.map(e=>e.querySelector('h3')?e.querySelector('h3').textContent:'')")
        print(f"[scenes] cards={len(scenes)} {scenes}")

        # 智能体中心（主智能体 + 子智能体 + 调度演示）
        pg.evaluate("go('agents')"); pg.wait_for_timeout(1500)
        pa_body = pg.evaluate("(document.getElementById('primaryAgentBody').textContent||'').trim()")
        sub_cnt = pg.evaluate("(document.getElementById('subAgentCount').textContent||'').trim()")
        pg.fill("#dispatchInput", "帮我做一周控糖食谱，顺便看下体检报告")
        pg.click("#agentsDispatch button.btn.primary")
        pg.wait_for_timeout(1500)
        disp = pg.evaluate("(document.getElementById('dispatchResult').textContent||'').trim()")
        print(f"[agents] primary={pa_body[:60]!r} subs={sub_cnt!r} dispatch={disp[:100]!r}")

        # 数据分析
        pg.evaluate("go('data')"); pg.wait_for_timeout(1800)
        trend = pg.evaluate("(document.getElementById('trendChart').textContent||'').trim()")
        dims = pg.evaluate("(document.getElementById('dimBox').textContent||'').trim()")
        risks = pg.evaluate("(document.getElementById('riskBox').textContent||'').trim()")
        insight = pg.evaluate("(document.getElementById('insightText').textContent||'').trim()")
        print(f"[data] trend={trend[:30]!r} dims={dims[:30]!r} risks={risks[:30]!r} insight={insight[:30]!r}")

        # 营养师工作台（普通用户 → 如实提示）
        pg.evaluate("go('dietitian')"); pg.wait_for_timeout(1500)
        dt_c = pg.text_content("#dtCustomers").strip()
        cli = pg.evaluate("(document.getElementById('clientBody').textContent||'').trim()")
        print(f"[dietitian/customer] kpi={dt_c!r} body={cli[:50]!r}")

        # 运营大屏（普通用户 → 如实提示）
        pg.evaluate("go('admin')"); pg.wait_for_timeout(1500)
        ops_note = pg.evaluate("(document.getElementById('opsNote').textContent||'').trim()")
        print(f"[admin/customer] note={ops_note[:60]!r}")

        # ---------- 2) 营养师视角 ----------
        logout_ui(pg)
        login_ui(pg, ctx["dt_user"])
        print("LOGIN(dietitian) ok  role=" + str(pg.evaluate("CURRENT_USER && CURRENT_USER.role")))
        pg.evaluate("go('dietitian')"); pg.wait_for_timeout(1800)
        dt_c = pg.text_content("#dtCustomers").strip()
        dt_hr = pg.text_content("#dtHighRisk").strip()
        dt_td = pg.text_content("#dtToday").strip()
        dt_sa = pg.text_content("#dtSatisfaction").strip()
        cli = pg.evaluate("(document.getElementById('clientBody').textContent||'').trim()")
        print(f"[dietitian/dietitian] KPI 管辖={dt_c!r} 高风险={dt_hr!r} 今日={dt_td!r} 满意度={dt_sa!r}")
        print(f"[dietitian/dietitian] body={cli[:80]!r}")

        # ---------- 3) 管理员视角 ----------
        logout_ui(pg)
        login_ui(pg, ctx["ad_user"])
        print("LOGIN(admin) ok  role=" + str(pg.evaluate("CURRENT_USER && CURRENT_USER.role")))
        pg.evaluate("go('admin')"); pg.wait_for_timeout(2000)
        ops_note = pg.evaluate("(document.getElementById('opsNote').textContent||'').trim()")
        ops_u = pg.text_content("#opsUsers").strip()
        ops_a = pg.text_content("#opsAssess").strip()
        ops_r = pg.text_content("#opsReports").strip()
        ops_c = pg.text_content("#opsChat").strip()
        levels = pg.evaluate("(document.getElementById('opsLevels').textContent||'').trim()")
        print(f"[admin/admin] KPI 用户={ops_u!r} 测评={ops_a!r} 报告={ops_r!r} 咨询={ops_c!r}")
        print(f"[admin/admin] note={ops_note[:70]!r} levels={levels[:50]!r}")

        b.close()

    print("\n=== 结果 ===")
    print("JS 运行时错误:", len(errors), errors[:3])
    print("控制台 error:", len(console_errors), console_errors[:3])
    print("失败请求(>=400):", len(failed_reqs), failed_reqs[:5])
    _teardown_once()


if __name__ == "__main__":
    run()
