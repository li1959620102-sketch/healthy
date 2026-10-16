# -*- coding: utf-8 -*-
"""BV-10 单页题量（8 题 / 9 题）——浏览器端真实交互验证（Playwright + Chromium）。

为什么必须在浏览器里测：这条用例考核的是**前端分页行为**（单页 ≤8 题、
超出自动分页、翻页不丢答案），后端接口返回的永远是全量题目，
用 TestClient / requests 断言不到任何东西，只能自己判"过了"——那是假绿。

覆盖三组边界：
  1. 恰好 8 题  → 单页铺开，无分页控件
  2. 9 题       → 出现分页，第 1 页 8 题、第 2 页 1 题
  3. 翻页保答案 → 第 1 页作答 → 翻到第 2 页 → 翻回，答案仍在且进度计数正确

顺带校验：草稿保存的 answers 是**全量**合并结果，而不是只有当前页那 8 题。
"""
from __future__ import annotations

import json
import random
import string
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
API = "/api/v1"
PWD = "Test@2026"

RESULTS = []


def rec(item: str, verdict: str, evidence: str = ""):
    RESULTS.append({"item": item, "verdict": verdict, "evidence": evidence})
    print(f"  [{verdict}] {item}  {evidence[:110]}")


def rnd(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def api(path, body=None, token=None, method="post"):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    fn = requests.post if method == "post" else requests.get
    return fn(BASE + path, json=body, headers=h, timeout=30).json()


def make_admin():
    name = "bv10_ad_" + rnd()
    r = api("/api/v1/auth/register",
            {"username": name, "password": PWD, "real_name": "BV10管理员", "role": "user"})
    assert r.get("code") == 0, r
    uid = r["data"]["user_id"]
    from sqlalchemy import text
    from app.utils.db import get_session
    s = get_session()
    try:
        s.execute(text("UPDATE sys_user SET role='admin' WHERE user_id=:u"), {"u": uid})
        s.commit()
    finally:
        s.close()
    return name, uid


def make_user():
    name = "bv10_u_" + rnd()
    r = api("/api/v1/auth/register",
            {"username": name, "password": PWD, "real_name": "BV10用户", "role": "user"})
    assert r.get("code") == 0, r
    return name, r["data"]["user_id"]


def make_template(admin_token: str, code: str, n_questions: int) -> int:
    """建一个**已审核通过**、题目数为 n 的模板。

    路径口径与 tests/integration/api_helper.py 一致：
    POST /templates → /templates/{id}/submit-audit → /templates/{id}/audit
    """
    questions = [{
        "question_id": f"q{i}",
        "title": f"边界测试第 {i} 题",
        "dimension": "basic",
        "type": "single",
        "options": [{"value": "A", "label": "选项A"}, {"value": "B", "label": "选项B"}],
        "max_score": 10,
    } for i in range(1, n_questions + 1)]
    body = {
        "code": code, "name": f"分页边界{n_questions}题-{code}", "type": "nutrition",
        "description": "BV-10 单页题量边界验证",
        "dimension_config": [{"code": "basic", "name": "基础维度", "weight": 1.0}],
        "question_config": questions,
    }
    r = api("/api/v1/templates", body, admin_token)
    if r.get("code") != 0:
        print("  建模板失败：", r)
        return 0
    tid = r["data"]["id"]
    s = api(f"/api/v1/templates/{tid}/submit-audit", {}, admin_token)
    if s.get("code") != 0:
        print("  提交审核失败：", s)
        return 0
    a = api(f"/api/v1/templates/{tid}/audit", {"approve": True}, admin_token)
    if a.get("code") != 0:
        print("  审核失败：", a)
        return 0
    return tid


def teardown(ad_name, ad_uid, u_name, u_uid, codes):
    from sqlalchemy import text
    from app.utils.db import get_session
    s = get_session()
    try:
        for uid in (u_uid, ad_uid):
            rows = s.execute(text("SELECT id FROM assessment_record WHERE user_id=:u"),
                             {"u": uid}).fetchall()
            rids = [int(x[0]) for x in rows]
            if rids:
                rp = ",".join(map(str, rids))
                s.execute(text("DELETE FROM assessment_report WHERE record_id IN (%s)" % rp))
                s.execute(text("DELETE FROM assessment_record WHERE id IN (%s)" % rp))
            for t in ("user_profile", "chat_session"):
                try:
                    s.execute(text(f"DELETE FROM {t} WHERE user_id=:u"), {"u": uid})
                except Exception:
                    pass
        s.execute(text("DELETE FROM sys_user WHERE username IN (:a,:b)"),
                  {"a": ad_name, "b": u_name})
        # 模板必须连同引用它的测评记录一起删：assessment_record 有 template_id 外键，
        # 只删模板会被外键挡住（此前就是这么漏下 bv8_/bv9_/bv9diag 三条脏模板的）。
        for c in codes:
            row = s.execute(text("SELECT id FROM assessment_template WHERE code=:c"),
                            {"c": c}).fetchone()
            if not row:
                continue
            tid = int(row[0])
            rids = [int(x[0]) for x in s.execute(
                text("SELECT id FROM assessment_record WHERE template_id=:t"),
                {"t": tid}).fetchall()]
            if rids:
                rp = ",".join(map(str, rids))
                s.execute(text("DELETE FROM assessment_report WHERE record_id IN (%s)" % rp))
                s.execute(text("DELETE FROM assessment_record WHERE id IN (%s)" % rp))
            s.execute(text("DELETE FROM assessment_template WHERE id=:i"), {"i": tid})
        s.commit()
    finally:
        s.close()
    print("TEARDOWN ok")


def wait(pg, js, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pg.evaluate(js):
            return True
        pg.wait_for_timeout(500)
    return False


def page_items(pg):
    return pg.eval_on_selector_all("#questionList .q-item", "els => els.length")


def open_quiz(pg, token, tid):
    """从首页点进模板 → 作答页，直接走真实 UI 路径。

    两个必须做对的点：
    1) 令牌走前端自己的存取口径（sessionStorage['ai_health_token']）——
       用 localStorage 等于另造一套假状态，测的就不是真实前端行为了。
    2) 先 go('assessment') 激活「健康风险测评」屏。.screen 默认 display:none，
       不切屏的话作答区 HTML 照样渲染，但元素尺寸为 0，点不到分页按钮。
    """
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.evaluate("t => sessionStorage.setItem('ai_health_token', t)", token)
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_timeout(900)
    pg.evaluate("() => go('assessment')")
    pg.wait_for_timeout(400)
    pg.evaluate("t => startAssessment(%d)" % tid, token)
    ok = wait(pg, "document.getElementById('asStepQuiz') && "
                   "document.getElementById('asStepQuiz').style.display !== 'none' && "
                   "document.getElementById('asStepQuiz').getBoundingClientRect().height > 0")
    pg.wait_for_timeout(600)
    return ok


def main():
    codes = []
    ad_name, ad_uid = make_admin()
    u_name, u_uid = make_user()
    print(f"SETUP ok  admin={ad_name}  user={u_name}")

    ad_tok = api("/api/v1/auth/login", {"username": ad_name, "password": PWD})["data"]["access_token"]
    u_tok = api("/api/v1/auth/login", {"username": u_name, "password": PWD})["data"]["access_token"]

    t8, t9 = f"bv8_{rnd(4)}", f"bv9_{rnd(4)}"
    codes += [t8, t9]
    tid8 = make_template(ad_tok, t8, 8)
    tid9 = make_template(ad_tok, t9, 9)
    print(f"  模板 8题 id={tid8}   9题 id={tid9}")

    js_err = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        pg = b.new_page()
        pg.on("pageerror", lambda e: js_err.append(str(e)))

        # ---------- 1. 恰好 8 题：单页铺开，无分页控件 ----------
        if tid8:
            ok = open_quiz(pg, u_tok, tid8)
            n = page_items(pg)
            pager_visible = pg.evaluate(
                "!!(document.getElementById('quizPager') && "
                "document.getElementById('quizPager').style.display !== 'none')")
            rec("8题-单页铺开", "PASS" if (ok and n == 8 and not pager_visible) else "FAIL",
                f"渲染 {n} 题 / 分页控件可见={pager_visible}")
            # 8 题全部可答，草稿应收到 8 题
            pg.evaluate("""(function(){
              document.querySelectorAll('#questionList .q-item').forEach(function(it){
                var r=it.querySelector('input[type=radio]'); if(r){r.click();}
              });
            })()""")
            pg.wait_for_timeout(1600)
            rid = pg.evaluate("AS.recordId")
            d = api(f"/api/v1/assessments/{rid}", None, u_tok, "get")
            saved = (d.get("data") or {}).get("answers") or {}
            rec("8题-答案全部入库", "PASS" if len(saved) == 8 else "FAIL",
                f"草稿 answers={len(saved)} 题，progress={(d.get('data') or {}).get('progress')}")

        # ---------- 2. 9 题：分页出现，第 1 页 8 题 ----------
        if tid9:
            ok = open_quiz(pg, u_tok, tid9)
            n1 = page_items(pg)
            pager_visible = pg.evaluate(
                "!!(document.getElementById('quizPager') && "
                "document.getElementById('quizPager').style.display !== 'none')")
            rec("9题-自动分页", "PASS" if (ok and n1 == 8 and pager_visible) else "FAIL",
                f"第1页渲染 {n1} 题（应 8），分页控件可见={pager_visible}")

            # ---------- 3. 翻页 + 答案保留 ----------
            pg.evaluate("""(function(){
              document.querySelectorAll('#questionList .q-item').forEach(function(it){
                var r=it.querySelector('input[type=radio]'); if(r){r.click();}
              });
            })()""")
            pg.wait_for_timeout(300)
            pg.click("#quizPager button[data-pg='next']")
            pg.wait_for_timeout(700)
            n2 = page_items(pg)
            rec("第2页-剩余题量", "PASS" if n2 == 1 else "FAIL", f"第2页渲染 {n2} 题（应 1）")

            pg.evaluate("""(function(){
              var r=document.querySelector('#questionList input[type=radio]');
              if(r){r.click();}
            })()""")
            pg.wait_for_timeout(400)
            prog = pg.eval_on_selector("#quizProgress", "el => el.textContent")
            rec("跨页进度累计", "PASS" if "9 / 9" in prog.replace("作答进度 ", "") else "FAIL",
                f"进度文案：{prog.strip()}")

            # 翻回第 1 页：第 1 题答案仍在（不因翻页丢失）
            pg.click("#quizPager button[data-pg='1']")
            pg.wait_for_timeout(700)
            kept = pg.evaluate("""(function(){
              var el=document.querySelector('#questionList .q-item input[type=radio]:checked');
              return el ? el.value : '';
            })()""")
            rec("翻页不丢答案", "PASS" if kept else "FAIL",
                f"返回第1页首题选中值={kept or '（空，已丢失）'}")

            # 草稿应是 9 题全量合并，而不是只有当前页
            pg.wait_for_timeout(1600)
            rid = pg.evaluate("AS.recordId")
            d = api(f"/api/v1/assessments/{rid}", None, u_tok, "get")
            saved = (d.get("data") or {}).get("answers") or {}
            rec("草稿全量合并", "PASS" if len(saved) == 9 else "FAIL",
                f"草稿 answers={len(saved)} 题（应为 9，含未渲染页）")

        rec("零 JS 运行时错误", "PASS" if not js_err else "FAIL", str(js_err[:2]))
        b.close()

    fails = [x for x in RESULTS if x["verdict"] == "FAIL"]
    print(f"\n=== BV-10 汇总 PASS={len(RESULTS)-len(fails)} FAIL={len(fails)} ===")
    for f in fails:
        print("  FAIL:", f["item"], f["evidence"][:100])
    with open("_bv10_result.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)
    teardown(ad_name, ad_uid, u_name, u_uid, codes)
    return 1 if fails else 0


if __name__ == "__main__":
    ctx = {}
    try:
        code = main()
    finally:
        # 兜底：脚本中途异常退出时 main() 里的 teardown 不会执行，
        # 库里就会留下孤儿账号（实测攒了 4 个 bv10_* admin/user）。
        # 合成账号用户名统一前缀，按前缀兜底扫一遍即可，不碰种子数据。
        from sqlalchemy import text

        from app.utils.db import get_session
        s = get_session()
        try:
            n = s.execute(text(
                "DELETE FROM sys_user WHERE username LIKE 'bv10_%'")).rowcount
            s.commit()
            if n:
                print(f"[兜底] 已清理中断残留的合成账号 {n} 个")
        finally:
            s.close()
        if ctx:
            teardown(**ctx)
    sys.exit(code)
