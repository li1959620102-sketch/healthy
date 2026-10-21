# -*- coding: utf-8 -*-
"""临时验证：用户端「人工咨询会话」面板能否打开并显示消息。
只读：登录 + 列表 + 拉消息，不发送任何消息，不改数据。
"""
import json
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8018"


def login(username, password):
    req = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode())
    assert d["code"] == 0, d
    return d["data"]


def main():
    data = login("1234567890", "Demo@2026")
    token = data["access_token"]
    user = data.get("user") or {"username": "1234567890"}

    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))

        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        # 走真实登录：账号通道 + 用户名/密码
        pg.click("#tabAccount")
        pg.fill("#lgUser", "1234567890")
        pg.fill("#lgPwdA", "Demo@2026")
        pg.click("#loginPage .hero-actions .btn.primary")
        pg.wait_for_function(
            "document.getElementById('loginPage').classList.contains('hidden')", timeout=20000)
        pg.wait_for_timeout(1200)
        pg.evaluate("go('nutrition')")
        pg.wait_for_timeout(2000)

        pg.wait_for_selector("#contactList .contact", timeout=15000)
        rows = pg.eval_on_selector_all(
            "#contactList .contact", "els => els.map(e => e.innerText.replace(/\\n/g,' | '))")
        print("咨询记录条目：")
        for r in rows:
            print("  -", r)

        pg.click("#contactList .contact")
        pg.wait_for_selector("#userConsultModal.open", timeout=10000)
        pg.wait_for_timeout(2500)

        sub = pg.inner_text("#ucSub")
        msgs = pg.inner_text("#ucMsgs")
        print("\n面板标题:", pg.inner_text("#ucTitle"))
        print("状态行:", sub.replace("\n", " "))
        print("消息区:\n", msgs[:600])
        print("\n发送按钮 disabled:", pg.get_attribute("#ucSendBtn", "disabled"))
        print("评价按钮 disabled:", pg.get_attribute("#ucRateBtn", "disabled"))

        pg.screenshot(path="_shots/user_consult_panel.png", full_page=False)

        # 第二条：进行中的咨询（有营养师），验证消息与按钮可用
        pg.click("#userConsultModal .close")
        pg.wait_for_timeout(500)
        pg.click('#contactList .contact:has-text("进行中")')
        pg.wait_for_timeout(2500)
        print("\n=== 进行中那条 ===")
        print("标题:", pg.inner_text("#ucTitle"))
        print("状态行:", pg.inner_text("#ucSub").replace("\n", " "))
        print("消息:\n", pg.inner_text("#ucMsgs")[:600])
        print("发送按钮 disabled:", pg.get_attribute("#ucSendBtn", "disabled"))
        print("待接入提示可见:", pg.is_visible("#ucPendingHint"))
        pg.screenshot(path="_shots/user_consult_active.png", full_page=False)
        print("\n截图: _shots/user_consult_panel.png / user_consult_active.png")
        print("控制台错误:", errors or "无")
        b.close()


if __name__ == "__main__":
    main()
