# -*- coding: utf-8 -*-
"""临时验证：用户端会话面板的「轮询自动刷新」与「用户发送」。
用 admin 名下进行中的咨询 #5989 做载体：
  1) 面板打开后，用营养师账号发一条消息 → 等 6 秒 → 面板应自动出现该消息；
  2) 在面板里发一条 → 应落库且立即显示。
本次新增的消息 id 会在结束时删除（只删本脚本自己写的那两条）。
"""
import json
import os
import time
import urllib.request

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8018"
CID = 5989

load_dotenv()


def api(path, body=None, token=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def db():
    from sqlalchemy import create_engine
    return create_engine(os.environ["MYSQL_URI"])


def max_msg_id():
    from sqlalchemy import text
    with db().connect() as c:
        return c.execute(text("select coalesce(max(id),0) from consultation_message")).scalar()


def cleanup(after_id):
    from sqlalchemy import text
    with db().begin() as c:
        rows = list(c.execute(text(
            "select id, sender_type, content from consultation_message where id > :i"), {"i": after_id}))
        c.execute(text("delete from consultation_message where id > :i"), {"i": after_id})
    return rows


def main():
    diet = api("/api/v1/auth/login", {"username": "123456789", "password": "Demo@2026"})["data"]["access_token"]
    before = max_msg_id()
    errors = []
    ok = {"poll": False, "send": False}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(BASE + "/", wait_until="domcontentloaded")
            pg.wait_for_timeout(1200)
            pg.click("#tabAccount")
            pg.fill("#lgUser", "1234567890")
            pg.fill("#lgPwdA", "Demo@2026")
            pg.click("#loginPage .hero-actions .btn.primary")
            pg.wait_for_function("document.getElementById('loginPage').classList.contains('hidden')", timeout=20000)
            pg.evaluate("go('nutrition')")
            pg.wait_for_selector('#contactList .contact:has-text("进行中")', timeout=15000)
            pg.click('#contactList .contact:has-text("进行中")')
            pg.wait_for_selector("#userConsultModal.open", timeout=10000)
            pg.wait_for_timeout(1500)
            print("面板已打开:", pg.inner_text("#ucTitle"))

            # ① 营养师发消息 → 靠 4s 轮询自动出现
            marker = "轮询自检-%d" % int(time.time())
            api("/api/v1/consultations/%d/messages" % CID, {"content": marker}, token=diet)
            pg.wait_for_function(
                "t => document.getElementById('ucMsgs').innerText.includes(t)", arg=marker, timeout=12000)
            ok["poll"] = True
            print("① 轮询自动刷新：通过（面板出现了营养师新消息）")

            # ② 面板内发送
            mine = "用户端自检-%d" % int(time.time())
            pg.fill("#ucInput", mine)
            pg.click("#ucSendBtn")
            pg.wait_for_function(
                "t => document.getElementById('ucMsgs').innerText.includes(t)", arg=mine, timeout=12000)
            ok["send"] = True
            print("② 面板内发送：通过（消息已回显）")
            pg.screenshot(path="_shots/user_consult_poll.png")
            b.close()
    finally:
        removed = cleanup(before)
        print("清理本次新增消息:", removed)

    print("控制台异常:", errors or "无")
    print("结果:", ok)
    assert ok["poll"] and ok["send"], "存在未通过项"


if __name__ == "__main__":
    main()
