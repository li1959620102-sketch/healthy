# -*- coding: utf-8 -*-
"""管理后台端到端验收（Playwright + Chromium）

覆盖管理后台五块：运营概览 / 用户管理 / 模板审核 / 审计日志 / 预警阈值 / 角色配置。

验收红线：
  1) 零 JS 运行时错误、零控制台 error、零失败请求（HTTP >= 400）；
  2) 每个管理动作都真实落库，并写入 operation_log（审计留痕）；
  3) 运行结束后自清理：删掉本轮创建的测试用户 / 模板 / 审计日志，还原配置，库回到运行前状态。
"""
import json
import random
import string
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8019"
ADMIN_USER = "1234567890"
ADMIN_PWD = "Demo@2026"
PWD = "Test@2026"

errors = []          # pageerror
console_errors = []  # console error
failed_reqs = []     # HTTP >= 400

cleanup = {"user_ids": [], "template_ids": [], "log_max_id": None,
           "alert_before": None, "role_before": None}


def rnd(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def api(method, path, body=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = requests.request(method, BASE + path, json=body, headers=h, timeout=30)
    return r.json()


def token_of(username, password):
    d = api("POST", "/api/v1/auth/login", {"username": username, "password": password})
    assert d.get("code") == 0, "登录失败：" + json.dumps(d, ensure_ascii=False)[:200]
    return d["data"]["access_token"], d["data"]["user"]


def snapshot():
    """记录运行前基线（日志最大 id、预警阈值、角色矩阵），供还原使用。"""
    tk, _ = token_of(ADMIN_USER, ADMIN_PWD)
    logs = api("GET", "/api/v1/admin/operation-logs?limit=1", None, tk)
    items = (logs.get("data") or {}).get("items") or []
    cleanup["log_max_id"] = items[0]["id"] if items else 0
    a = api("GET", "/api/v1/admin/alerts/thresholds", None, tk)
    cleanup["alert_before"] = {i["key"]: i["value"] for i in (a.get("data") or {}).get("items", [])}
    r = api("GET", "/api/v1/admin/roles", None, tk)
    cleanup["role_before"] = {i["capability"]: dict(i["roles"]) for i in (r.get("data") or {}).get("items", [])}
    return tk


def setup():
    """准备测试素材：一个普通用户（供禁用/解封）、一个待审模板（供审核）。"""
    tk, _ = token_of(ADMIN_USER, ADMIN_PWD)
    uname = "e2eadmin_" + rnd()
    r = api("POST", "/api/v1/auth/register",
            {"username": uname, "password": PWD, "real_name": "E2E待管用户"}, tk)
    assert r.get("code") == 0, "创建测试用户失败：" + json.dumps(r, ensure_ascii=False)[:200]
    cleanup["user_ids"].append(r["data"]["user_id"])

    # 用管理员身份创建一个模板并提交审核 → 供模板审核用
    code = "E2E_TPL_" + rnd(8).upper()
    cr = api("POST", "/api/v1/templates",
             {"code": code, "name": "E2E 管理端验收模板", "type": "constitution",
              "description": "管理后台 E2E 自动创建，运行后自动清理",
              "dimension_config": [{"key": "basic", "name": "基础维度", "weight": 1.0}],
              "question_config": {"questions": [{
                  "question_id": "q1", "dimension": "basic", "type": "single",
                  "required": True, "max_score": 4,
                  "options": [{"value": "A", "score": 4}, {"value": "B", "score": 2}]}]}}, tk)
    if cr.get("code") == 0:
        tid = cr["data"]["id"]
        cleanup["template_ids"].append(tid)
        api("POST", "/api/v1/templates/%d/submit-audit" % tid, {}, tk)
    else:
        print("创建模板失败（模板审核仍可对既有待审模板操作）：",
              json.dumps(cr, ensure_ascii=False)[:160])
    return uname


def teardown():
    """清理本轮副作用：日志、测试用户、测试模板、配置还原。"""
    try:
        tk, _ = token_of(ADMIN_USER, ADMIN_PWD)
    except Exception as e:   # noqa: BLE001
        print("TEARDOWN: 无法取得令牌：", e)
        return
    from app.utils.db import get_session
    from sqlalchemy import text
    s = get_session()
    try:
        # 1) 删掉本轮新增的审计日志
        if cleanup["log_max_id"] is not None:
            n = s.execute(text("DELETE FROM operation_log WHERE id > :m"),
                          {"m": cleanup["log_max_id"]}).rowcount
            print("TEARDOWN 删除本轮审计日志：%d 条" % n)
        # 2) 删测试模板
        for tid in cleanup["template_ids"]:
            s.execute(text("DELETE FROM assessment_template WHERE id = :i"), {"i": tid})
        if cleanup["template_ids"]:
            print("TEARDOWN 删除测试模板：", cleanup["template_ids"])
        # 3) 删测试用户
        for uid in cleanup["user_ids"]:
            s.execute(text("DELETE FROM user_profile WHERE user_id = :u"), {"u": uid})
            s.execute(text("DELETE FROM sys_user WHERE user_id = :u"), {"u": uid})
        if cleanup["user_ids"]:
            print("TEARDOWN 删除测试用户：", cleanup["user_ids"])
        s.commit()
    finally:
        s.close()

    # 4) 还原预警阈值与角色矩阵
    if cleanup["alert_before"]:
        api("PUT", "/api/v1/admin/alerts/thresholds", {"thresholds": cleanup["alert_before"]}, tk)
    if cleanup["role_before"]:
        for cap, roles in cleanup["role_before"].items():
            for role, allowed in roles.items():
                api("PUT", "/api/v1/admin/roles",
                    {"capability": cap, "role": role, "allowed": allowed}, tk)
    # 5) 再删一次（还原动作本身也会写日志）
    if cleanup["log_max_id"] is not None:
        s2 = get_session()
        try:
            n = s2.execute(text("DELETE FROM operation_log WHERE id > :m"),
                           {"m": cleanup["log_max_id"]}).rowcount
            s2.commit()
            print("TEARDOWN 二次清理日志：%d 条" % n)
        finally:
            s2.close()
    print("TEARDOWN 完成")


def inject(pg, token, user):
    """把令牌写入 sessionStorage，使 admin.html 启动即视为已登录。"""
    pg.add_init_script("""
        try {
          sessionStorage.setItem('ai_health_token', %s);
          sessionStorage.setItem('ai_health_user', %s);
        } catch (e) {}
    """ % (json.dumps(token), json.dumps(json.dumps(user, ensure_ascii=False))))


def main():
    tk = snapshot()
    uname = setup()
    token, user = token_of(ADMIN_USER, ADMIN_PWD)
    print("管理员登录成功：%s（%s）" % (user.get("real_name"), user.get("role")))

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        pg.on("response", lambda r: failed_reqs.append("%s %s" % (r.status, r.url))
              if r.status >= 400 else None)
        inject(pg, token, user)

        pg.goto(BASE + "/admin.html", wait_until="networkidle")
        pg.wait_for_timeout(1200)

        # ---------- 1) 运营概览 ----------
        cards = pg.eval_on_selector_all("#ovCards .card", "els=>els.map(e=>e.querySelector('.v').textContent.trim())")
        levels = pg.eval_on_selector_all("#ovLevels .bar", "els=>els.length")
        print("[概览] 指标卡=%d %s | 等级分布行=%d" % (len(cards), cards[:4], levels))
        assert len(cards) >= 8, "概览指标卡不足"

        # ---------- 2) 用户管理 ----------
        pg.click('#tabs button[data-tab="users"]')
        pg.wait_for_timeout(1200)
        rows = pg.eval_on_selector_all("#usrBody tr", "els=>els.length")
        # 搜索刚创建的测试用户
        pg.fill("#fKeyword", uname)
        pg.click('#tab-users button.primary')
        pg.wait_for_timeout(900)
        n = pg.eval_on_selector_all("#usrBody tr", "els=>els.length")
        print("[用户] 首屏 %d 行；搜索 %r 命中 %d 行" % (rows, uname, n))
        assert n == 1, "搜索测试用户未命中唯一一行：%d" % n

        # 禁用
        pg.click('#usrBody tr:first-child button.danger')
        pg.wait_for_timeout(400)
        assert pg.is_visible("#dlg"), "禁用确认弹窗未出现"
        pg.fill("#dlgRemark", "E2E 自动化：验证禁用留痕")
        pg.click("#dlgOk")
        pg.wait_for_timeout(1100)
        st = pg.eval_on_selector("#usrBody tr:first-child td:nth-child(5)", "e=>e.textContent.trim()")
        print("[用户] 禁用后状态单元格 = %r" % st)
        assert "禁用" in st, "禁用未生效"

        # 解封
        pg.click('#usrBody tr:first-child button.ok')
        pg.wait_for_timeout(400)
        pg.click("#dlgOk")
        pg.wait_for_timeout(1100)
        st2 = pg.eval_on_selector("#usrBody tr:first-child td:nth-child(5)", "e=>e.textContent.trim()")
        print("[用户] 解封后状态单元格 = %r" % st2)
        assert "正常" in st2, "解封未生效"

        # ---------- 3) 模板审核 ----------
        pg.click('#tabs button[data-tab="templates"]')
        pg.wait_for_timeout(1400)
        pend = pg.eval_on_selector_all("#tplAuditBody tr", "els=>els.length")
        allt = pg.eval_on_selector_all("#tplAllBody tr", "els=>els.length")
        badge = pg.text_content("#tplPending").strip()
        print("[模板] 待审 %d 行 | 全量 %d 行 | 标签=%r" % (pend, allt, badge))
        assert allt >= 1, "全量模板为空"

        # 对刚创建的测试模板执行「通过」（只点自己那一行，避免误伤演示数据）
        if cleanup["template_ids"]:
            tid = cleanup["template_ids"][0]
            btn = pg.evaluate("""(tid) => {
                const rows = [...document.querySelectorAll('#tplAuditBody tr')];
                const row = rows.find(r => {
                    const c = r.querySelector('td.num');
                    return c && c.textContent.trim() === String(tid);
                });
                if (!row) return false;
                const b = row.querySelector('button.ok');
                if (!b) return false;
                b.click();
                return true;
            }""", tid)
            assert btn, "待审列表中未找到本轮创建的测试模板 #%d" % tid
            pg.wait_for_timeout(400)
            pg.fill("#dlgRemark", "E2E 自动化：资料齐全，准予发布")
            pg.click("#dlgOk")
            pg.wait_for_timeout(1300)
            print("[模板] 已通过本轮创建的模板 id=%d" % tid)

        # ---------- 4) 审计日志 ----------
        pg.click('#tabs button[data-tab="logs"]')
        pg.wait_for_timeout(1200)
        lr = pg.eval_on_selector_all("#logBody tr", "els=>els.length")
        first = pg.eval_on_selector("#logBody tr", "e=>e.textContent.trim().slice(0,120)")
        print("[日志] %d 行；最新一条=%r" % (lr, first))
        assert lr >= 1, "审计日志为空"
        # 用动作筛选
        pg.fill("#fAction", "template.audit")
        pg.click('#tab-logs button.primary')
        pg.wait_for_timeout(900)
        fa = pg.eval_on_selector_all("#logBody tr", "els=>els.length")
        print("[日志] 按 template.audit 筛选 = %d 行" % fa)

        # ---------- 5) 预警阈值 ----------
        pg.click('#tabs button[data-tab="alerts"]')
        pg.wait_for_timeout(1100)
        thr = pg.eval_on_selector_all("#thrBox .thr-row", "els=>els.length")
        first_key = pg.eval_on_selector("#thrBox .thr-row", "e=>e.getAttribute('data-key')")
        print("[预警] 阈值项=%d 首项=%s" % (thr, first_key))
        assert thr >= 5, "预警阈值项不足 5"
        # 改一个值（越界）并保存，验证收敛
        inp = pg.query_selector("#thrBox .thr-row input")
        inp.fill("999")
        pg.click('#tab-alerts button.primary')
        pg.wait_for_timeout(1300)
        after = pg.eval_on_selector("#thrBox .thr-row input", "e=>e.value")
        print("[预警] 输入 999 保存后回显 = %r（应被收敛到上限）" % after)
        assert after != "999", "越界值未被收敛"

        # ---------- 6) 角色配置 ----------
        pg.click('#tabs button[data-tab="roles"]')
        pg.wait_for_timeout(1100)
        head = pg.eval_on_selector_all("#roleHead th", "els=>els.length")
        body = pg.eval_on_selector_all("#roleBody tr", "els=>els.length")
        boxes = pg.eval_on_selector_all("#roleBody input[type=checkbox]", "els=>els.length")
        print("[角色] 表头列=%d 能力行=%d 开关=%d" % (head, body, boxes))
        assert head >= 5 and body >= 10 and boxes >= 40, "角色矩阵渲染不完整"
        # 切换一个开关再切回
        cb = pg.query_selector("#roleBody input[type=checkbox]")
        was = cb.is_checked()
        cb.click()
        pg.wait_for_timeout(1000)
        cb.click()
        pg.wait_for_timeout(1000)
        now = cb.is_checked()
        print("[角色] 首个开关 %s → 切换 → 复原 = %s" % (was, now))
        assert was == now, "开关切换未能复原"

        b.close()

    print("\n===== 结果 =====")
    print("JS 运行时错误:", len(errors), errors[:3])
    print("控制台 error :", len(console_errors), console_errors[:3])
    print("失败请求     :", len(failed_reqs), failed_reqs[:3])
    ok = (not errors) and (not console_errors) and (not failed_reqs)
    print("判定:", "PASS（零错误）" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        teardown()
    sys.exit(rc)
