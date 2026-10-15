# -*- coding: utf-8 -*-
"""营养师专业端·批次2 专项联调（Playwright 真实浏览器）

覆盖链路：
  资质申请（含必填校验）→ 未通过审核时不可生成邀请码 → 管理员审核通过
  → 生成邀请码 → 客户凭邀请码绑定 → 营养师查看客户报告 + 人工批注
  → 解除绑定 → 创建测评模板（草稿）→ 提交审核 → 管理员审核通过（用户端可见）

说明：管理员审核动作走 API（管理员审核界面属批次3），其余全部走真实界面点击。
"""
import json
import random
import string
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8019"
PWD = "Test@2026"
R = []


def rec(no, item, verdict, ev=""):
    R.append((no, item, verdict, ev))
    print(f"  [{verdict}] {no} {item}  {str(ev)[:100]}")


def rnd(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def api(path, body=None, token=None, method="post"):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    fn = requests.post if method == "post" else requests.get
    return fn(BASE + path, json=body, headers=h, timeout=60).json()


def login(username):
    d = api("/api/v1/auth/login", {"username": username, "password": PWD})
    return (d.get("data") or {}).get("access_token")


def ui_login(pg, username):
    pg.evaluate("openLogin()")
    pg.evaluate("switchLoginTab('account')")
    pg.wait_for_timeout(250)
    pg.fill("#lgUser", username)
    pg.fill("#lgPwdA", PWD)
    pg.click("#loginPage .hero-actions button.btn.primary")
    for _ in range(30):
        pg.wait_for_timeout(300)
        if pg.evaluate("!!(typeof CURRENT_USER!=='undefined'&&CURRENT_USER)"):
            pg.wait_for_timeout(700)
            return True
    return False


def main():
    dt_user = "dt2_" + rnd()
    cu_user = "cu2_" + rnd()
    ad_user = "ad2_" + rnd()
    tpl_code = "E2E_" + rnd(5).upper()

    r = api("/api/v1/auth/register", {"username": dt_user, "password": PWD, "real_name": "联调营养师", "role": "user"})
    assert r.get("code") == 0, r
    dt_uid = r["data"]["user_id"]
    r = api("/api/v1/auth/register", {"username": cu_user, "password": PWD, "real_name": "联调客户", "role": "user"})
    assert r.get("code") == 0, r
    cu_uid = r["data"]["user_id"]
    r = api("/api/v1/auth/register", {"username": ad_user, "password": PWD, "real_name": "联调审核员", "role": "user"})
    assert r.get("code") == 0, r
    ad_uid = r["data"]["user_id"]
    # 特权角色由后台授予，禁止利用公开注册接口创建。
    from sqlalchemy import text
    from app.utils.db import get_session
    session = get_session()
    try:
        session.execute(text("UPDATE sys_user SET role='dietitian' WHERE user_id=:u"), {"u": dt_uid})
        session.execute(text("UPDATE sys_user SET role='admin' WHERE user_id=:u"), {"u": ad_uid})
        session.commit()
    finally:
        session.close()
    ad_tk = login(ad_user)
    print(f"SETUP ok 营养师={dt_user}({dt_uid}) 客户={cu_user}({cu_uid}) 管理员={ad_user} 模板={tpl_code}")

    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        ctx = b.new_context(viewport={"width": 1440, "height": 980})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(800)

        # ---------- 1. 营养师登录 → 资质卡应为「未提交资质」 ----------
        assert ui_login(pg, dt_user), "营养师登录失败"
        pg.evaluate("go('dietitian')")
        pg.wait_for_timeout(2200)
        body = pg.text_content("#dtLicenseBody") or ""
        rec("N1", "资质卡显示未提交状态", "PASS" if "未提交资质" in body else "FAIL", body.strip()[:50])

        # ---------- 2. 资质表单必填校验 ----------
        pg.evaluate("openLicenseForm()")
        pg.wait_for_timeout(600)
        pg.click("#licenseModal .hero-actions button.btn.primary")
        pg.wait_for_timeout(500)
        rec("N2", "资质表单必填校验", "PASS" if "请填写" in (pg.text_content("#licErr") or "") else "FAIL",
            (pg.text_content("#licErr") or "").strip()[:40])

        # ---------- 3. 正确提交资质 ----------
        pg.fill("#licName", "注册营养师")
        pg.fill("#licNo", "RD-" + rnd(6).upper())
        pg.fill("#licExpire", "2028-12-31")
        pg.click('#licSpecs .pf-chip[data-name="糖尿病饮食"]')
        pg.click('#licSpecs .pf-chip[data-name="体重管理"]')
        pg.fill("#licCases", "累计服务 2 型糖尿病患者 30 例（脱敏）")
        pg.click("#licenseModal .hero-actions button.btn.primary")
        pg.wait_for_timeout(2000)
        body = pg.text_content("#dtLicenseBody") or ""
        rec("N3", "提交资质后进入待审核", "PASS" if "待审核" in body else "FAIL", body.strip()[:60])

        # ---------- 4. 未通过审核不能生成邀请码 ----------
        pg.evaluate("genInviteCode()")
        pg.wait_for_timeout(1200)
        tst = pg.evaluate("(document.getElementById('toast')||{}).textContent||''")
        rec("N4", "待审核时不可生成邀请码（如实提示）",
            "PASS" if ("审核" in tst or "资质" in tst) else "FAIL", tst[:40])

        # ---------- 5. 管理员审核通过（API；管理端界面属批次3） ----------
        ap = api(f"/api/v1/admin/nutritionists/{dt_uid}/audit", {"approve": True, "remark": "材料齐全"}, token=ad_tk)
        rec("N5", "管理员审核通过（API）", "PASS" if ap.get("code") == 0 else "FAIL", ap.get("message", "")[:40])
        pg.evaluate("loadLicense()")
        pg.wait_for_timeout(1500)
        body = pg.text_content("#dtLicenseBody") or ""
        rec("N6", "资质卡刷新为已通过", "PASS" if "已通过" in body else "FAIL", body.strip()[:60])

        # ---------- 6. 生成邀请码 ----------
        pg.evaluate("genInviteCode()")
        pg.wait_for_timeout(1500)
        body = pg.text_content("#dtLicenseBody") or ""
        invite = ""
        import re as _re
        m = _re.search(r"邀请码：\s*([A-Z0-9]{4,12})", body)
        if m:
            invite = m.group(1)
        rec("N7", "生成邀请码并展示", "PASS" if invite else "FAIL", f"code={invite}")

        # ---------- 7. 客户在用户端绑定营养师 ----------
        cu_ctx = b.new_context(viewport={"width": 1440, "height": 980})
        cu = cu_ctx.new_page()
        cu.on("pageerror", lambda e: errs.append("CU:" + str(e)))
        cu.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        cu.wait_for_timeout(700)
        assert ui_login(cu, cu_user), "客户登录失败"
        cu.evaluate("go('nutrition')")
        cu.wait_for_timeout(1200)
        cu.evaluate("openBindDtn()")
        cu.wait_for_timeout(400)
        cu.fill("#bindCode", invite)
        cu.click("#bindDtnModal .hero-actions button.btn.primary")
        cu.wait_for_timeout(1500)
        bind_err = (cu.text_content("#bindErr") or "").strip()
        bind_toast = cu.evaluate("(document.getElementById('toast')||{}).textContent||''")
        rec("N8", "客户凭邀请码绑定营养师", "PASS" if (not bind_err) and ("绑定成功" in bind_toast) else "FAIL",
            f"{bind_toast[:30]} / err={bind_err[:30]}")
        cu_ctx.close()

        # ---------- 8. 营养师看到客户 + 查看报告 + 批注 ----------
        pg.evaluate("loadClients('')")
        pg.wait_for_timeout(1800)
        rows = pg.evaluate("document.querySelectorAll('#clientBody tr').length")
        rec("N9", "客户列表出现已绑定客户", "PASS" if rows >= 1 else "FAIL", f"行数={rows}")

        pg.evaluate(f"openCustomerReports('{cu_uid}')")
        pg.wait_for_timeout(1800)
        cr = pg.text_content("#crBody") or ""
        rec("N10", "查看客户测评报告", "PASS" if ("报告" in cr or "暂无" in cr) else "FAIL", cr.strip()[:50])

        # 造一份真实报告（客户还没测评）→ 用 API 走完整测评链路
        cu_tk = login(cu_user)
        _tp = api("/api/v1/templates", token=cu_tk, method="get").get("data")
        tpls = (_tp.get("items") if isinstance(_tp, dict) else _tp) or []
        if tpls:
            t0 = tpls[0]
            rec0 = api("/api/v1/assessments", {"template_id": t0["id"], "template_version": t0.get("version") or 1}, token=cu_tk)
            rid_rec = ((rec0.get("data") or {}).get("assessment_id"))
            if rid_rec:
                detail = api(f"/api/v1/assessments/{rid_rec}", token=cu_tk, method="get").get("data") or {}
                ans = {}
                for q in detail.get("questions") or []:
                    opts = [o["value"] for o in (q.get("options") or [])]
                    ans[q["question_id"]] = opts if q.get("type") == "multi" else (opts[0] if opts else "")
                requests.patch(BASE + f"/api/v1/assessments/{rid_rec}/answers", json={"answers": ans},
                               headers={"Authorization": "Bearer " + cu_tk, "Content-Type": "application/json"}, timeout=60)
                sub = requests.post(BASE + f"/api/v1/assessments/{rid_rec}/submit",
                                    json={}, headers={"Authorization": "Bearer " + cu_tk,
                                                      "Idempotency-Key": "dt2-" + cu_user,
                                                      "Content-Type": "application/json"}, timeout=180).json()
                rec("N11", "客户完成测评（造真实报告）", "PASS" if sub.get("code") == 0 else "FAIL", sub.get("message", "")[:40])
                report_id = ((sub.get("data") or {}).get("report_id"))
            else:
                report_id = None
        else:
            report_id = None
        pg.evaluate(f"openCustomerReports('{cu_uid}')")
        pg.wait_for_timeout(1800)
        cr = pg.text_content("#crBody") or ""
        has_list = "查看详情" in cr
        rec("N12", "报告列表出现该客户报告", "PASS" if has_list or report_id is None else "FAIL", cr.strip()[:50])
        if has_list:
            pg.evaluate("(function(){var b=[...document.querySelectorAll('#crBody button')].find(x=>/查看详情/.test(x.textContent)); if(b) b.click();})()")
            pg.wait_for_timeout(2200)
            det = pg.text_content("#crBody") or ""
            rec("N13", "报告详情渲染（总分/维度/正文）",
                "PASS" if ("总分" in det and "报告正文" in det) else "FAIL", det.strip()[:60])
            pg.fill("#crAnno", "已复核：建议增加全谷物比例，2 周后复评。")
            pg.evaluate("(function(){var b=[...document.querySelectorAll('#crBody button')].find(x=>/保存批注/.test(x.textContent)); if(b) b.click();})()")
            pg.wait_for_timeout(1500)
            tst = pg.evaluate("(document.getElementById('toast')||{}).textContent||''")
            rec("N14", "营养师人工批注保存", "PASS" if "批注已保存" in tst else "FAIL", tst[:40])
        pg.evaluate("closeCustomerReports()")
        pg.wait_for_timeout(400)

        # ---------- 9. 模板：新建草稿 → 提交审核 → 管理员通过 → 用户端可见 ----------
        pg.evaluate("openTemplateForm()")
        pg.wait_for_timeout(800)
        pg.fill("#tplCode", tpl_code)
        pg.fill("#tplName", "E2E 联调测评模板")
        pg.fill("#tplDesc", "批次2 联调自动创建")
        # 维度（默认 1 行）
        pg.fill("#tplDims .tpl-dim-key", "basic")
        pg.fill("#tplDims .tpl-dim-name", "基础营养")
        pg.fill("#tplDims .tpl-dim-weight", "1")
        # 题目（默认 1 行）→ 填值
        pg.fill("#tplQs .tpl-q-title", "近一月体重变化")
        pg.fill("#tplQs .tpl-q-dim", "basic")
        pg.fill("#tplQs .tpl-q-max", "100")
        pg.fill("#tplQs .tpl-q-opts", "无明显变化|100\n略有下降|70\n明显下降|40")
        pg.click("#tplModal .hero-actions button.btn.primary")
        pg.wait_for_timeout(2000)
        err = (pg.text_content("#tplErr") or "").strip()
        rec("N15", "新建模板保存为草稿", "PASS" if not err else "FAIL", err[:60] or "已保存")
        pg.evaluate("loadMyTemplates()")
        pg.wait_for_timeout(1500)
        tb = pg.text_content("#tplMineBody") or ""
        rec("N16", "模板列表显示草稿", "PASS" if (tpl_code in tb and "草稿" in tb) else "FAIL", tb.strip()[:60])

        # 提交审核（点该行的「提交审核」）
        pg.evaluate("(function(){var b=[...document.querySelectorAll('#tplMineBody button')].find(x=>/提交审核/.test(x.textContent)); if(b) b.click();})()")
        pg.wait_for_timeout(1800)
        tb = pg.text_content("#tplMineBody") or ""
        rec("N17", "提交审核后状态待审核", "PASS" if "待审核" in tb else "FAIL", tb.strip()[:60])

        # 管理员审核通过（API）
        mine = api("/api/v1/templates/mine", token=login(dt_user), method="get").get("data") or {}
        tid = None
        for t in (mine.get("items") or []):
            if t.get("code") == tpl_code:
                tid = t.get("id")
        ap = api(f"/api/v1/templates/{tid}/audit", {"approve": True, "remark": "结构完整"}, token=ad_tk)
        rec("N18", "管理员审核通过模板（API）", "PASS" if ap.get("code") == 0 else "FAIL", ap.get("message", "")[:40])
        pg.evaluate("loadMyTemplates()")
        pg.wait_for_timeout(1500)
        tb = pg.text_content("#tplMineBody") or ""
        rec("N19", "模板状态变已发布", "PASS" if "已发布" in tb else "FAIL", tb.strip()[:60])

        # 版本列表
        pg.evaluate(f"openTemplateVersions({tid})")
        pg.wait_for_timeout(1500)
        vb = pg.text_content("#tplVerBody") or ""
        rec("N20", "模板版本列表", "PASS" if "v1" in vb else "FAIL", vb.strip()[:50])
        pg.evaluate("closeTemplateVersions()")

        # 用户端可见（新建用户看已发布列表）
        _pp = api("/api/v1/templates", token=cu_tk, method="get").get("data")
        pub = (_pp.get("items") if isinstance(_pp, dict) else _pp) or []
        rec("N21", "审核通过后用户端模板可见",
            "PASS" if any(t.get("code") == tpl_code for t in (pub or [])) else "FAIL",
            f"用户端模板数={len(pub or [])}")

        # ---------- 10. 解除绑定 ----------
        pg.evaluate("unbindCustomer('%s')" % cu_uid)
        pg.wait_for_timeout(2200)
        rows = pg.evaluate("document.querySelectorAll('#clientBody tr').length")
        body_txt = pg.text_content("#clientBody") or ""
        rec("N22", "解除绑定后客户不再出现",
            "PASS" if ("暂无管辖客户" in body_txt or "该筛选" in body_txt) else "FAIL", f"行数={rows}")

        rec("N23", "浏览器运行时错误", "PASS" if not errs else "FAIL", f"{len(errs)} 条 {errs[:2]}")
        b.close()

    # ---------- teardown ----------
    from sqlalchemy import text
    from app.utils.db import get_session
    s = get_session()
    try:
        s.execute(text("DELETE FROM dietitian_customer WHERE dietitian_id=:d OR customer_id=:c"), {"d": dt_uid, "c": cu_uid})
        s.execute(text("DELETE FROM assessment_report WHERE user_id=:c"), {"c": cu_uid})
        s.execute(text("DELETE FROM assessment_record WHERE user_id=:c"), {"c": cu_uid})
        s.execute(text("DELETE FROM assessment_template WHERE code=:t"), {"t": tpl_code})
        s.execute(text("DELETE FROM dietitian_profile WHERE user_id=:d"), {"d": dt_uid})
        s.execute(text("DELETE FROM user_profile WHERE user_id IN (:d,:c)"), {"d": dt_uid, "c": cu_uid})
        s.execute(text("DELETE FROM sys_user WHERE user_id IN (:d,:c,:a)"), {"d": dt_uid, "c": cu_uid, "a": (api("/api/v1/auth/login", {"username": ad_user, "password": PWD}).get("data") or {}).get("user", {}).get("user_id")})
        s.commit()
        print("TEARDOWN ok 测试数据已清理")
    except Exception as e:
        s.rollback()
        print("TEARDOWN 失败：", e)
    finally:
        s.close()

    ok = sum(1 for x in R if x[2] == "PASS")
    bad = [x for x in R if x[2] == "FAIL"]
    print(f"\n=== 汇总 ===\nPASS={ok}  FAIL={len(bad)}  总={len(R)}")
    for x in bad:
        print("  失败：", x[0], x[1], x[3])
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
