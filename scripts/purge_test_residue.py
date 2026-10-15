# -*- coding: utf-8 -*-
"""清理测试残留数据（P1-4 · 一次性运维脚本，可重复执行）。

用法：
    python scripts/purge_test_residue.py            # 预演：只统计不写库（默认）
    python scripts/purge_test_residue.py --apply    # 真正执行删除

判定口径全部基于「引用不存在」这一客观事实，不靠命名猜测：
  1. 悬空用户数据 — 任何含 user_id 列的表里，user_id 不在 sys_user 中；
  2. 测试残留账号 — sys_user 中 username 以 IT / e2e / smoke 开头（测试脚本命名约定）；
  3. 孤儿报告     — assessment_report.record_id 在 assessment_record 中不存在；
  4. 孤儿批注     — report_annotation.report_id 在 assessment_report 中不存在。

删除顺序：子表 → 父表，最后删 sys_user。
执行前请先整库备份：
    MYSQL_PWD=*** mysqldump -h127.0.0.1 -uroot --single-transaction --databases test_db > backups/xxx.sql
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

from sqlalchemy import bindparam, text            # noqa: E402

from app.utils.db import get_session              # noqa: E402

# 测试脚本造账号的命名前缀（见 _e2e_*.py / tests）
TEST_NAME_LIKES = ("IT%", "e2e%", "smoke%")

# P1-3 遗留对象（NRS2002 历史表/视图）：排序规则与主库不一致且不属平台数据，跳过
LEGACY_OBJECTS = {"high_risk_patients", "latest_assessments", "user_health_risk_assessment"}

# 无 user_id 列的子表：按父表 id 清理  (子表, 子表外键列, 父表)
CHILD_OF_PARENT = [
    ("chat_message", "session_id", "chat_session"),
    ("consultation_message", "consultation_id", "consultation"),
    ("diet_plan_recipe", "plan_id", "diet_plan"),
    ("purchase_item", "list_id", "purchase_list"),
    ("banquet_guest", "menu_id", "banquet_menu"),
    ("ocr_indicator", "report_id", "ocr_report"),
]


def _user_id_tables(s):
    names = [r[0] for r in s.execute(text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND column_name = 'user_id' "
        "AND table_name <> 'sys_user' ORDER BY table_name")).fetchall()]
    skipped = sorted(set(names) & LEGACY_OBJECTS)
    if skipped:
        print(f"[跳过] 遗留对象（不属平台数据）：{skipped}\n")
    return [n for n in names if n not in LEGACY_OBJECTS]


def _ids(s, sql, **params):
    return [r[0] for r in s.execute(text(sql), params).fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认仅预演）")
    args = ap.parse_args()

    s = get_session()
    try:
        tables = _user_id_tables(s)

        # ---------------- 1. 悬空用户数据 ----------------
        print("=" * 70)
        print("【1】悬空用户数据（user_id 不在 sys_user 中）")
        print("=" * 70)
        dangling_uids = set()
        total_dangling = 0
        for t in tables:
            rows = _ids(s,
                        f"SELECT DISTINCT a.user_id FROM `{t}` a "
                        "LEFT JOIN sys_user b ON b.user_id = a.user_id "
                        "WHERE a.user_id IS NOT NULL AND b.user_id IS NULL")
            if rows:
                n = s.execute(text(
                    f"SELECT COUNT(*) FROM `{t}` a "
                    "LEFT JOIN sys_user b ON b.user_id = a.user_id "
                    "WHERE a.user_id IS NOT NULL AND b.user_id IS NULL")).scalar()
                dangling_uids.update(rows)
                total_dangling += n
                print(f"   {t:26s} {n:4d} 行 / {len(rows)} 个 user_id")
        print(f"   {'小计':26s} {total_dangling:4d} 行")

        # ---------------- 2. 测试残留账号 ----------------
        print("\n" + "=" * 70)
        print("【2】测试残留账号（username 命中测试命名前缀）")
        print("=" * 70)
        where = " OR ".join([f"username LIKE '{p}'" for p in TEST_NAME_LIKES])
        accts = s.execute(text(
            f"SELECT user_id, username, role FROM sys_user WHERE {where} ORDER BY created_at")).fetchall()
        for uid, uname, role in accts:
            print(f"   {uid:16s} {uname:22s} {role}")
        acct_uids = {a[0] for a in accts}
        print(f"   共 {len(acct_uids)} 个账号")

        # ---------------- 3/4. 孤儿报告与孤儿批注 ----------------
        orphan_reports = _ids(s,
                              "SELECT rp.id FROM assessment_report rp "
                              "LEFT JOIN assessment_record r ON r.id = rp.record_id "
                              "WHERE r.id IS NULL")
        all_report_ids = _ids(s, "SELECT id FROM assessment_report")
        orphan_ann = _ids(s,
                          "SELECT a.id FROM report_annotation a "
                          "LEFT JOIN assessment_report rp ON rp.id = a.report_id "
                          "WHERE rp.id IS NULL")
        print("\n" + "=" * 70)
        print("【3】孤儿报告（关联的测评记录已不存在）")
        print("=" * 70)
        print(f"   {len(orphan_reports)} 条：{orphan_reports}")
        print("\n" + "=" * 70)
        print("【4】孤儿批注（关联的报告已不存在）")
        print("=" * 70)
        print(f"   {len(orphan_ann)} 条：{orphan_ann}")

        # ---------------- 待删 id 集合 ----------------
        res_uids = sorted(dangling_uids | acct_uids)
        report_ids = orphan_reports + _ids(
            s, "SELECT id FROM assessment_report WHERE user_id IN :u",
            u=res_uids) if res_uids else orphan_reports

        print("\n" + "=" * 70)
        print(f"合计待处理：{len(res_uids)} 个 user_id / {total_dangling} 行悬空数据 / "
              f"{len(acct_uids)} 个残留账号 / {len(orphan_reports)} 条孤儿报告 / "
              f"{len(orphan_ann)} 条孤儿批注")
        print("=" * 70)

        if not args.apply:
            print("\n[预演] 未写库。确认无误后加 --apply 执行删除。")
            return

        if not res_uids and not orphan_reports and not orphan_ann:
            print("\n[跳过] 没有需要清理的数据。")
            return

        # ---------------- 执行删除（子 → 父）----------------
        print("\n开始删除 ……")
        deleted = {}

        def run(label, sql, params=None, expanding=False):
            stmt = text(sql)
            if expanding:
                stmt = stmt.bindparams(bindparam("u", expanding=True))
            n = s.execute(stmt, params or {}).rowcount
            deleted[label] = deleted.get(label, 0) + n
            return n

        if res_uids:
            # a) 批注：随将被删除的报告一起删
            if report_ids:
                run("report_annotation",
                    "DELETE FROM report_annotation WHERE report_id IN :u",
                    {"u": report_ids}, expanding=True)
            # b) 无 user_id 列的子表，按父表 id 删
            for child, fk, parent in CHILD_OF_PARENT:
                run(child,
                    f"DELETE FROM `{child}` WHERE {fk} IN "
                    f"(SELECT id FROM `{parent}` WHERE user_id IN :u)",
                    {"u": res_uids}, expanding=True)
            # c) 绑定关系（无 user_id 列，两列都指向用户）
            run("dietitian_customer",
                "DELETE FROM dietitian_customer "
                "WHERE dietitian_id IN :u OR customer_id IN :u",
                {"u": res_uids}, expanding=True)
            # d) 所有带 user_id 列的表
            for t in tables:
                run(t, f"DELETE FROM `{t}` WHERE user_id IN :u", {"u": res_uids}, expanding=True)

        # e) 孤儿报告（user_id 为 NULL、记录已不存在的游客残留）
        if orphan_reports:
            run("assessment_report(孤儿)",
                "DELETE FROM assessment_report WHERE id IN :u",
                {"u": orphan_reports}, expanding=True)

        # f) 孤儿批注
        if orphan_ann:
            run("report_annotation(孤儿)",
                "DELETE FROM report_annotation WHERE id IN :u",
                {"u": orphan_ann}, expanding=True)

        # g) 最后删残留账号
        if acct_uids:
            run("sys_user", "DELETE FROM sys_user WHERE user_id IN :u",
                {"u": sorted(acct_uids)}, expanding=True)

        s.commit()

        print("\n删除明细：")
        for k, v in deleted.items():
            if v:
                print(f"   {k:26s} -{v}")
        print(f"   合计删除 {sum(deleted.values())} 行")

        # ---------------- 复查 ----------------
        print("\n" + "=" * 70)
        print("复查")
        print("=" * 70)
        left = 0
        for t in tables:
            n = s.execute(text(
                f"SELECT COUNT(*) FROM `{t}` a "
                "LEFT JOIN sys_user b ON b.user_id = a.user_id "
                "WHERE a.user_id IS NOT NULL AND b.user_id IS NULL")).scalar()
            if n:
                print(f"   [!] {t} 仍有悬空 {n} 行")
                left += n
        orph = s.execute(text(
            "SELECT COUNT(*) FROM assessment_report rp "
            "LEFT JOIN assessment_record r ON r.id = rp.record_id WHERE r.id IS NULL")).scalar()
        orph_a = s.execute(text(
            "SELECT COUNT(*) FROM report_annotation a "
            "LEFT JOIN assessment_report rp ON rp.id = a.report_id WHERE rp.id IS NULL")).scalar()
        acct_left = s.execute(text(
            f"SELECT COUNT(*) FROM sys_user WHERE {where}")).scalar()
        print(f"   悬空用户数据：{left} 行")
        print(f"   孤儿报告：{orph} 条")
        print(f"   孤儿批注：{orph_a} 条")
        print(f"   测试残留账号：{acct_left} 个")
        print("   " + ("全部清零 ✔" if left == orph == orph_a == acct_left == 0
                       else "仍有残留，请人工核对"))
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


if __name__ == "__main__":
    main()
