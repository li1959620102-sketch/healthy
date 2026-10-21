# -*- coding: utf-8 -*-
"""临时验证：用户端人工咨询改为「页内视图切换」后的行为。

断言重点（不是功能，是形式）：
  1. 页面里不存在人工咨询弹窗，也没有任何 .modal.open 遮罩
  2. 点左栏咨询记录，AI 视图隐藏、人工视图出现，两者占同一块区域（同 x/y/宽高）
  3. 点「AI营养师」切回 AI 视图，不丢消息
  4. 两条会话之间来回切换，标题与消息内容真的跟着换
  5. 刷新后仍停在上次那条会话（普通聊天窗口的行为）

用一次性账号 smoke_u1021；结束时删掉本次产生的咨询与消息。
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


def engine():
    from sqlalchemy import create_engine
    return create_engine(os.environ["MYSQL_URI"])


def cleanup(uid):
    from sqlalchemy import text
    with engine().begin() as c:
        ids = [r[0] for r in c.execute(
            text("select id from consultation where user_id=:u"), {"u": uid})]
        if ids:
            lst = ",".join(str(i) for i in ids)
            c.execute(text("delete from consultation_message where consultation_id in (%s)" % lst))
            c.execute(text("delete from consultation where id in (%s)" % lst))
    return ids


def box(pg, sel):
    return pg.eval_on_selector(
        sel, "e => { const r = e.getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}; }")


def main():
    reg = api("/api/v1/auth/register", {"username": USERNAME, "password": PASSWORD, "real_name": "自检用户"})
    if reg.get("code") != 0:
        reg = api("/api/v1/auth/login", {"username": USERNAME, "password": PASSWORD})
    assert reg.get("code") == 0, reg
    utoken = reg["data"]["access_token"]
    uid = (reg["data"].get("user") or {}).get("user_id")
    if not uid:
        from sqlalchemy import text
        with engine().connect() as c:
            uid = c.execute(text("select user_id from sys_user where username=:u"), {"u": USERNAME}).scalar()
    print("测试账号:", USERNAME, "user_id=", uid)

    diet = api("/api/v1/auth/login", {"username": "123456789", "password": "Demo@2026"})["data"]["access_token"]

    ok, errors = {}, []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

            pg.goto(BASE + "/", wait_until="domcontentloaded")
            pg.wait_for_timeout(1000)
            pg.click("#tabAccount")
            pg.fill("#lgUser", USERNAME)
            pg.fill("#lgPwdA", PASSWORD)
            pg.click("#loginPage .hero-actions .btn.primary")
            pg.wait_for_function("document.getElementById('loginPage').classList.contains('hidden')", timeout=20000)
            pg.evaluate("go('nutrition')")
            pg.wait_for_timeout(1500)

            # ---- 0) 默认态：AI 视图在，人工视图不在，且没有弹窗
            ok["no_modal_dom"] = pg.evaluate("document.getElementById('userConsultModal')===null && document.querySelectorAll('.modal.open').length===0")
            ok["ai_view_default"] = pg.is_visible("#aiChatView") and not pg.is_visible("#humanChatView")
            ai_box = box(pg, "#aiChatView")
            print("① 进入营养屏：AI 视图可见 =", ok["ai_view_default"], "| 无弹窗 =", ok["no_modal_dom"],
                  "| 区域 =", ai_box)

            # ---- 1) 点「切换人工咨询」→ 同一区域换成人工会话
            pg.click('button:has-text("切换人工咨询")')
            pg.wait_for_selector("#humanChatView:not([hidden])", timeout=15000)
            pg.wait_for_timeout(1200)
            hu_box = box(pg, "#humanChatView")
            ok["switch_in_place"] = (not pg.is_visible("#aiChatView")) and pg.is_visible("#humanChatView")
            ok["same_area"] = (abs(ai_box["x"] - hu_box["x"]) <= 2 and abs(ai_box["y"] - hu_box["y"]) <= 2
                               and abs(ai_box["w"] - hu_box["w"]) <= 2 and abs(ai_box["h"] - hu_box["h"]) <= 2)
            title = pg.inner_text("#ucTitle")
            cid1 = int(title.split("#")[-1].strip())
            print("② 点「切换人工咨询」→ 原地换成", title, "| 区域 =", hu_box,
                  "| AI 视图已隐藏 =", not pg.is_visible("#aiChatView"),
                  "| 区域一致 =", ok["same_area"])
            print("   顶部模式徽标 =", pg.inner_text("#chatMode"), "| 待接入提示 =", pg.is_visible("#ucPendingHint"))

            # ---- 2) 营养师接入 + 回复，轮询自动出现在同一块区域
            acc = api("/api/v1/consultations/%d/accept" % cid1, {}, token=diet)
            marker1 = "营养师自检A-%d" % int(time.time())
            api("/api/v1/consultations/%d/messages" % cid1, {"content": marker1}, token=diet)
            print("   营养师接入:", acc.get("code"))
            pg.wait_for_function("t => document.getElementById('ucMsgs').innerText.includes(t)", arg=marker1, timeout=15000)
            ok["poll_in_place"] = True
            print("③ 营养师回复在页内自动出现（未刷新）= 通过 | 状态行 =",
                  pg.inner_text("#ucSub").replace("\n", " "))

            # ---- 3) 点左栏「AI营养师」切回 AI
            pg.click("#aiContactItem")
            pg.wait_for_timeout(600)
            ok["back_to_ai"] = pg.is_visible("#aiChatView") and not pg.is_visible("#humanChatView")
            pg.screenshot(path="_shots/user_consult_ai_view.png")
            print("④ 点左栏「AI营养师」→ 切回 AI 视图 =", ok["back_to_ai"], "| 徽标 =", pg.inner_text("#chatMode"))

            # ---- 4) 再点左栏那条会话切回人工，消息还在
            pg.click("#contactList .contact")
            pg.wait_for_selector("#humanChatView:not([hidden])", timeout=10000)
            pg.wait_for_timeout(1200)
            msgs_txt = pg.inner_text("#ucMsgs")
            ok["reopen_keeps_msgs"] = ("#%d" % cid1) in pg.inner_text("#ucTitle") and marker1 in msgs_txt
            print("⑤ 再点左栏会话切回 =", ok["reopen_keeps_msgs"])

            # ---- 5) 第二条会话：验证真的「切换」而不是只有一条
            c2 = api("/api/v1/consultations", {"question": "第二条自检咨询", "type": "manual"}, token=utoken)
            cid2 = (c2.get("data") or {}).get("consultation_id")
            api("/api/v1/consultations/%d/accept" % cid2, {}, token=diet)
            marker2 = "营养师自检B-%d" % int(time.time())
            api("/api/v1/consultations/%d/messages" % cid2, {"content": marker2}, token=diet)
            pg.evaluate("loadContacts()")
            pg.wait_for_timeout(1200)
            rows = pg.eval_on_selector_all("#contactList .contact", "els => els.map(e => e.innerText.replace(/\\n/g,' '))")
            print("   左栏列表:", rows)
            pg.click('#contactList .contact:has-text("#%d")' % cid2)
            pg.wait_for_timeout(1800)
            t2, m2 = pg.inner_text("#ucTitle"), pg.inner_text("#ucMsgs")
            ok["switch_between"] = ("#%d" % cid2) in t2 and marker2 in m2 and marker1 not in m2
            print("⑥ 切到第二条会话 =", ok["switch_between"], "|", t2.replace("\n", " "))

            # ---- 6) 刷新后停在上次那条（视图 + 会话都不回退）
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            pg.wait_for_selector("#humanChatView:not([hidden])", timeout=15000)
            t3 = pg.inner_text("#ucTitle")
            ok["persist_on_reload"] = ("#%d" % cid2) in t3
            print("⑦ 刷新后仍停在上次会话 =", ok["persist_on_reload"], "|", t3.replace("\n", " "))
            pg.screenshot(path="_shots/user_consult_inline.png")

            # ---- 7) Enter 发送（Shift+Enter 换行）
            msg = "用户自检Enter-%d" % int(time.time())
            pg.click("#ucInput")
            pg.type("#ucInput", msg)
            pg.keyboard.press("Enter")
            pg.wait_for_function("t => document.getElementById('ucMsgs').innerText.includes(t)", arg=msg, timeout=15000)
            left = pg.input_value("#ucInput")
            ok["enter_send"] = (left == "")
            print("⑧ Enter 发送 =", ok["enter_send"], "| 输入框已清空 =", left == "")
            b.close()
    finally:
        if uid:
            print("清理咨询记录:", cleanup(uid))

    print("异常:", errors or "无")
    print("结果:", ok)
    assert all(ok.values()), "未通过项: %s" % [k for k, v in ok.items() if not v]


if __name__ == "__main__":
    main()
