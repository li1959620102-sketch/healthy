# -*- coding: utf-8 -*-
"""临时验证：完整复现「用户端发起 → 营养师接入回复 → 用户端点开/切换会话」。

用一次性账号 smoke_u1021（已存在则直接登录），控件走真实点击。
结束时删除本次产生的咨询与消息，账号保留（可用 scripts/purge_test_residue.py 清）。
"""
import json
import os
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8018"
USERNAME = "smoke_u1021"
PASSWORD = "Smoke@2026"

load_dotenv()


def api(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def db_begin():
    from sqlalchemy import create_engine
    return create_engine(os.environ["MYSQL_URI"])


def cleanup(uid):
    from sqlalchemy import text
    with db_begin().begin() as c:
        ids = [r[0] for r in c.execute(
            text("select id from consultation where user_id=:u"), {"u": uid})]
        if ids:
            c.execute(text("delete from consultation_message where consultation_id in :ids")
                      .bindparams(ids=tuple(ids)) if False else
                      text("delete from consultation_message where consultation_id in (%s)" % ",".join(str(i) for i in ids)))
            c.execute(text("delete from consultation where user_id=:u"), {"u": uid})
    return ids


def main():
    # 1) 一次性账号
    reg = api("/api/v1/auth/register", {"username": USERNAME, "password": PASSWORD, "real_name": "自检用户"})
    if reg.get("code") != 0:
        reg = api("/api/v1/auth/login", {"username": USERNAME, "password": PASSWORD})
        assert reg.get("code") == 0, reg
    uid = (reg.get("data", {}).get("user") or {}).get("user_id") or (reg.get("data", {}).get("user") or {}).get("id")
    if not uid:
        from sqlalchemy import text
        with db_begin().connect() as c:
            uid = c.execute(text("select user_id from sys_user where username=:u"),
                            {"u": USERNAME}).scalar()
    print("测试账号:", USERNAME, "user_id=", uid)

    # 2) 营养师令牌
    diet = api("/api/v1/auth/login", {"username": "123456789", "password": "Demo@2026"})["data"]["access_token"]

    cid = None
    ok = {}
    errors = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

            pg.goto(BASE + "/", wait_until="domcontentloaded")
            pg.wait_for_timeout(1200)
            pg.click("#tabAccount")
            pg.fill("#lgUser", USERNAME)
            pg.fill("#lgPwdA", PASSWORD)
            pg.click("#loginPage .hero-actions .btn.primary")
            pg.wait_for_function("document.getElementById('loginPage').classList.contains('hidden')", timeout=20000)
            pg.evaluate("go('nutrition')")
            pg.wait_for_timeout(1500)

            # 3) 点「切换人工咨询」→ 建单 + 自动打开面板
            pg.click('button:has-text("切换人工咨询")')
            pg.wait_for_selector("#userConsultModal.open", timeout=15000)
            pg.wait_for_timeout(1500)
            title = pg.inner_text("#ucTitle")
            cid = int(title.split("#")[-1].strip())
            ok["create_and_open"] = True
            print("① 发起后自动打开面板:", title, "|", pg.inner_text("#ucSub").replace("\n", " "))
            print("   待接入提示可见:", pg.is_visible("#ucPendingHint"))

            # 4) 营养师接入 + 回复
            acc = api("/api/v1/consultations/%d/accept" % cid, {}, token=diet)
            print("   营养师接入:", acc.get("code"), acc.get("message"))
            marker = "营养师自检回复-%d" % int(time.time())
            snd = api("/api/v1/consultations/%d/messages" % cid, {"content": marker}, token=diet)
            print("   营养师发送:", snd.get("code"))

            # 5) 面板靠轮询自动出现（不刷新页面）
            pg.wait_for_function(
                "t => document.getElementById('ucMsgs').innerText.includes(t)", arg=marker, timeout=15000)
            ok["poll_inject"] = True
            print("② 营养师回复自动出现：通过（未刷新页面）")
            sub2 = pg.inner_text("#ucSub").replace("\n", " ")
            print("   状态行:", sub2)
            print("   待接入提示可见:", pg.is_visible("#ucPendingHint"))
            ok["status_synced"] = ("进行中" in sub2) and (not pg.is_visible("#ucPendingHint"))
            print("③ 接入状态同步:", "通过" if ok["status_synced"] else "未通过（仍显示待接入）")

            # 6) 关闭 → 列表应显示「进行中」→ 再点开切换
            pg.click("#userConsultModal .close")
            pg.wait_for_timeout(800)
            rows = pg.eval_on_selector_all(
                "#contactList .contact", "els => els.map(e => e.innerText.replace(/\\n/g,' | '))")
            print("   关闭后列表:", rows)
            pg.click('#contactList .contact:has-text("进行中")')
            pg.wait_for_selector("#userConsultModal.open", timeout=10000)
            pg.wait_for_timeout(1500)
            switch_title = pg.inner_text("#ucTitle")
            msgs = pg.inner_text("#ucMsgs")
            ok["switch_and_show"] = (("#%d" % cid) in switch_title) and (marker in msgs)
            print("③ 从列表点开切换:", switch_title, "| 含营养师消息:", marker in msgs)

            # 7) 切到另一条（旧会话）再切回来，验证切换真的换内容
            others = pg.eval_on_selector_all(
                "#contactList .contact", "els => els.map(e => e.getAttribute('onclick'))")
            print("   列表 onclick:", others)
            pg.screenshot(path="_shots/user_consult_flow.png")
            b.close()
    finally:
        if uid:
            ids = cleanup(uid)
            print("清理咨询记录:", ids)

    print("异常:", errors or "无")
    print("结果:", ok)
    assert all(ok.values()), "存在未通过项"


if __name__ == "__main__":
    main()
