# -*- coding: utf-8 -*-
"""清理测试 / 历史残留数据（P1-4 · 运维脚本，默认只预演，可重复执行）。

用法
----
    python scripts/purge_test_residue.py            # 预演：只统计不写库
    python scripts/purge_test_residue.py --apply    # 真正执行删除

判定口径（全部基于「引用不存在」这一客观事实，不靠命名猜测）
------------------------------------------------------------
  1. 悬空用户数据 — 任一「指向 sys_user 的外键列」的取值不在 sys_user 中。
     外键列不再只认 `user_id`：本平台业务库里还有 `operator_id`（操作日志）、
     `dietitian_id` / `customer_id`（绑定关系），旧版只扫 user_id 是漏清的根因。
  2. 测试残留账号 — sys_user.username 命中 IT% / e2e% / smoke%（测试脚本命名约定）。
  3. 孤儿子行     — 按 RELATIONS 全量反连接清扫（消息/方案/批注/日志/OCR/宴请…），
     迭代至收敛，可处理「删父表后子表变孤儿」的级联。
  4. 空会话       — 无任何消息的 chat_session，且**仅限种子导入基线（seed.active_run）
     之前的行**，避免误删本次导入或真实用户刚打开的会话。

删除顺序：叶子 → 根，最后删 sys_user。执行前请先整库备份：
    MYSQL_PWD=*** mysqldump -h127.0.0.1 -uroot --single-transaction --databases test_db > backups/xxx.sql
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql  # noqa: E402

from app.config import settings  # noqa: E402

# 测试脚本造账号的命名前缀（见 _e2e_*.py / tests）
TEST_NAME_LIKES = ("IT%", "e2e%", "smoke%")

# 遗留对象（NRS2002 历史表/视图）：不属平台数据，跳过
LEGACY_OBJECTS = {"high_risk_patients", "latest_assessments", "user_health_risk_assessment"}

# (子表, 子列, 父表, 父列, 说明, 子列可为 NULL) —— 叶子在前，顺序即安全删除顺序
RELATIONS = [
    ("chat_message", "session_id", "chat_session", "id", "问答消息 → 会话", False),
    ("consultation_message", "consultation_id", "consultation", "id", "咨询消息 → 咨询", False),
    ("diet_plan_recipe", "plan_id", "diet_plan", "id", "食谱 → 食养方案", False),
    ("banquet_guest", "menu_id", "banquet_menu", "id", "宴请宾客 → 菜单", False),
    ("ocr_indicator", "report_id", "ocr_report", "id", "OCR 指标 → OCR 报告", False),
    ("report_annotation", "report_id", "assessment_report", "id", "批注 → 报告", False),
    ("report_annotation", "dietitian_id", "sys_user", "user_id", "批注 → 营养师", False),
    ("operation_log", "operator_id", "sys_user", "user_id", "操作日志 → 操作人", False),
    ("notification", "user_id", "sys_user", "user_id", "通知 → 用户", False),
    ("checkin_record", "user_id", "sys_user", "user_id", "打卡 → 用户", False),
    ("user_profile", "user_id", "sys_user", "user_id", "健康档案 → 用户", False),
    ("dietitian_profile", "user_id", "sys_user", "user_id", "营养师资料 → 用户", False),
    ("diet_plan", "report_id", "assessment_report", "id", "食养方案 → 报告", False),
    ("diet_plan", "user_id", "sys_user", "user_id", "食养方案 → 用户", False),
    ("assessment_report", "record_id", "assessment_record", "id", "报告 → 测评记录", False),
    ("assessment_report", "user_id", "sys_user", "user_id", "报告 → 用户", True),
    ("consultation", "user_id", "sys_user", "user_id", "咨询 → 用户", False),
    ("dietitian_customer", "customer_id", "sys_user", "user_id", "绑定 → 客户", False),
    ("dietitian_customer", "dietitian_id", "sys_user", "user_id", "绑定 → 营养师", False),
    ("chat_session", "user_id", "sys_user", "user_id", "会话 → 用户", True),
    ("assessment_record", "template_id", "assessment_template", "id", "测评记录 → 模板", False),
    ("assessment_record", "user_id", "sys_user", "user_id", "测评记录 → 用户", True),
]


def connect():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB, charset="utf8mb4", autocommit=False,
    )


def baseline() -> dict:
    """读取种子导入基线（每表 MAX(id)），用于把「历史行」与「导入行」区分开。"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("SELECT config_value FROM system_config WHERE config_key='seed.active_run'")
            row = cur.fetchone()
        if not row:
            return {}
        return json.loads(row[0]).get("baseline", {}) or {}
    except Exception:
        return {}


def user_fk_columns(cur) -> list[tuple[str, str]]:
    """所有指向 sys_user 的外键列：动态扫描 user_id + RELATIONS 里的显式列。"""
    cur.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND column_name = 'user_id' "
        "AND table_name <> 'sys_user'")
    cols = {("user_id", r[0]) for r in cur.fetchall()}
    cols |= {(c, t) for t, c, p, _, _, _ in RELATIONS if p == "sys_user"}
    return sorted({(col, tbl) for col, tbl in cols if tbl not in LEGACY_OBJECTS})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认仅预演）")
    args = ap.parse_args()

    base = baseline()
    conn = connect()
    deleted: dict[str, int] = {}

    def count(sql: str, params=None) -> int:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def delete(label: str, sql: str, params=None) -> int:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        deleted[label] = deleted.get(label, 0) + n
        return n

    try:
        with conn.cursor() as cur:
            fk_cols = user_fk_columns(cur)

        # ---------------- 1. 悬空用户数据 ----------------
        print("=" * 74)
        print("【1】悬空用户数据（外键列取值不在 sys_user 中）")
        print("=" * 74)
        dangling_uids: set[str] = set()
        dangling_total = 0
        for col, tbl in fk_cols:
            cond = (f"x.`{col}` IS NOT NULL AND y.user_id IS NULL"
                    if col != "user_id" else "x.`user_id` IS NOT NULL AND y.user_id IS NULL")
            n = count(f"SELECT COUNT(*) FROM `{tbl}` x LEFT JOIN sys_user y ON y.user_id = x.`{col}` WHERE {cond}")
            if n:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT DISTINCT x.`{col}` FROM `{tbl}` x "
                                f"LEFT JOIN sys_user y ON y.user_id = x.`{col}` WHERE {cond}")
                    dangling_uids |= {r[0] for r in cur.fetchall()}
                dangling_total += n
                print(f"   {tbl + '.' + col:34s} {n:5d} 行")
        print(f"   {'小计':34s} {dangling_total:5d} 行 / {len(dangling_uids)} 个用户")

        # ---------------- 2. 测试残留账号 ----------------
        print("\n" + "=" * 74)
        print("【2】测试残留账号（username 命中测试命名前缀）")
        print("=" * 74)
        where = " OR ".join([f"username LIKE '{p}'" for p in TEST_NAME_LIKES])
        with conn.cursor() as cur:
            cur.execute(f"SELECT user_id, username, role FROM sys_user WHERE {where} ORDER BY created_at")
            accts = cur.fetchall()
        for uid, uname, role in accts:
            print(f"   {uid:16s} {uname:24s} {role}")
        acct_uids = {a[0] for a in accts}
        print(f"   共 {len(acct_uids)} 个账号")

        # ---------------- 3. 孤儿子行 ----------------
        print("\n" + "=" * 74)
        print("【3】孤儿子行（关联的父行已不存在）")
        print("=" * 74)
        orphan_plan: dict[str, int] = {}
        for child, ccol, parent, pcol, desc, _nullable in RELATIONS:
            # NULL 外键表示「没有引用」，不是「引用不存在」，必须排除（游客会话等）
            n = count(f"SELECT COUNT(*) FROM `{child}` x LEFT JOIN `{parent}` y ON x.`{ccol}`=y.`{pcol}` "
                      f"WHERE y.`{pcol}` IS NULL AND x.`{ccol}` IS NOT NULL")
            orphan_plan[desc] = n
            if n:
                print(f"   !! {desc:26s} {n:5d} 行")
        if not any(orphan_plan.values()):
            print("   （无）")

        # ---------------- 4. 空会话（仅历史行） ----------------
        cut = base.get("chat_session")
        empty_ids: list[int] = []
        if cut:
            with conn.cursor() as cur:
                cur.execute("SELECT s.id FROM chat_session s LEFT JOIN chat_message m ON m.session_id=s.id "
                            "WHERE m.id IS NULL AND s.id <= %s ORDER BY s.id", (cut,))
                empty_ids = [r[0] for r in cur.fetchall()]
        print("\n" + "=" * 74)
        print(f"【4】空会话（无消息且 id <= 种子基线 {cut}）")
        print("=" * 74)
        print(f"   {len(empty_ids)} 个：{empty_ids}")

        target_uids = sorted(dangling_uids | acct_uids)
        print("\n" + "=" * 74)
        print(f"合计：{len(target_uids)} 个待清用户 / {dangling_total} 行悬空数据 / "
              f"{sum(orphan_plan.values())} 行孤儿子行 / {len(empty_ids)} 个空会话")
        print("=" * 74)

        if not args.apply:
            print("\n[预演] 未写库。确认无误后加 --apply 执行删除。")
            return 0

        # ---------------- 执行删除 ----------------
        print("\n开始删除 ……")
        if target_uids:
            ph = ",".join(["%s"] * len(target_uids))
            # a) 叶子：直接属于待清用户的行（含 operator_id / dietitian_id 等非 user_id 外键）
            leaf_first = [("chat_message", "session_id", "chat_session", "id"),
                          ("consultation_message", "consultation_id", "consultation", "id"),
                          ("diet_plan_recipe", "plan_id", "diet_plan", "id"),
                          ("banquet_guest", "menu_id", "banquet_menu", "id"),
                          ("ocr_indicator", "report_id", "ocr_report", "id")]
            with conn.cursor() as cur:
                for child, ccol, parent, pcol in leaf_first:
                    cur.execute(f"DELETE FROM `{child}` WHERE `{ccol}` IN "
                                f"(SELECT `{pcol}` FROM `{parent}` WHERE user_id IN ({ph}))", target_uids)
                    deleted[child] = deleted.get(child, 0) + cur.rowcount
            # b) 所有指向 sys_user 的外键列
            for col, tbl in fk_cols:
                delete(tbl, f"DELETE FROM `{tbl}` WHERE `{col}` IN ({ph})", target_uids)

        # c) 孤儿清扫：迭代至收敛（处理「删父表 → 子表变孤儿」的级联）
        cascaded = True
        rounds = 0
        while cascaded and rounds < 6:
            cascaded = False
            rounds += 1
            for child, ccol, parent, pcol, desc, _nullable in RELATIONS:
                n = delete(desc, f"DELETE x FROM `{child}` x LEFT JOIN `{parent}` y "
                                 f"ON x.`{ccol}`=y.`{pcol}` WHERE y.`{pcol}` IS NULL "
                                 f"AND x.`{ccol}` IS NOT NULL")
                if n:
                    cascaded = True

        # d) 空会话
        if empty_ids:
            ph = ",".join(["%s"] * len(empty_ids))
            delete("chat_session(空)", f"DELETE FROM chat_session WHERE id IN ({ph})", empty_ids)

        # e) 最后删残留账号
        if acct_uids:
            ph = ",".join(["%s"] * len(acct_uids))
            delete("sys_user", f"DELETE FROM sys_user WHERE user_id IN ({ph})", sorted(acct_uids))

        conn.commit()

        print("\n删除明细：")
        for k, v in sorted(deleted.items(), key=lambda kv: -kv[1]):
            if v:
                print(f"   {k:26s} -{v}")
        print(f"   合计删除 {sum(deleted.values())} 行")

        # ---------------- 复查 ----------------
        print("\n" + "=" * 74)
        print("复查")
        print("=" * 74)
        left = 0
        for col, tbl in fk_cols:
            n = count(f"SELECT COUNT(*) FROM `{tbl}` x LEFT JOIN sys_user y ON y.user_id = x.`{col}` "
                      f"WHERE x.`{col}` IS NOT NULL AND y.user_id IS NULL")
            if n:
                print(f"   [!] {tbl}.{col} 仍有悬空 {n} 行")
                left += n
        orph = 0
        for child, ccol, parent, pcol, desc, _nullable in RELATIONS:
            n = count(f"SELECT COUNT(*) FROM `{child}` x LEFT JOIN `{parent}` y ON x.`{ccol}`=y.`{pcol}` "
                      f"WHERE y.`{pcol}` IS NULL AND x.`{ccol}` IS NOT NULL")
            if n:
                print(f"   [!] {desc} 仍有孤儿 {n} 行")
                orph += n
        empty_left = count("SELECT COUNT(*) FROM chat_session s LEFT JOIN chat_message m "
                           "ON m.session_id=s.id WHERE m.id IS NULL") if cut else 0
        acct_left = count(f"SELECT COUNT(*) FROM sys_user WHERE {where}")
        print(f"   悬空用户数据 {left} 行 / 孤儿子行 {orph} 行 / 空会话 {empty_left} 个 / 残留账号 {acct_left} 个")
        print("   " + ("全部清零 ✔" if left == orph == empty_left == acct_left == 0 else "仍有残留，请人工核对"))
        return 0 if left == orph == empty_left == acct_left == 0 else 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
