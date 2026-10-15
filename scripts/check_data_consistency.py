# -*- coding: utf-8 -*-
"""数据一致性巡检（可重复执行，只读）。

用途
----
本平台的引用完整性只在应用层维护（数据库 0 外键），因此需要常态化巡检：
  1. 悬空引用：子表指向的父行已不存在（反连接判定，不用 user_id 前缀这类脆弱口径）
  2. 结构性异常：报告缺维度分/缺引用/文本过短、方案无食谱、会话无消息
  3. 分布合理性：等级分布、分数区间、BMI 与慢病占比、打卡率

用法
----
    python scripts/check_data_consistency.py            # 全量巡检
    python scripts/check_data_consistency.py --json     # 输出 JSON（供自动化消费）
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

# (子表, 子列, 父表, 父列, 说明)
RELATIONS = [
    ("assessment_report", "record_id", "assessment_record", "id", "报告 → 测评记录"),
    ("assessment_report", "user_id", "sys_user", "user_id", "报告 → 用户"),
    ("assessment_record", "template_id", "assessment_template", "id", "测评记录 → 模板"),
    ("assessment_record", "user_id", "sys_user", "user_id", "测评记录 → 用户"),
    ("diet_plan", "report_id", "assessment_report", "id", "食养方案 → 报告"),
    ("diet_plan", "user_id", "sys_user", "user_id", "食养方案 → 用户"),
    ("diet_plan_recipe", "plan_id", "diet_plan", "id", "食谱 → 食养方案"),
    ("report_annotation", "report_id", "assessment_report", "id", "批注 → 报告"),
    ("chat_message", "session_id", "chat_session", "id", "问答消息 → 会话"),
    ("consultation", "user_id", "sys_user", "user_id", "咨询 → 用户"),
    ("consultation_message", "consultation_id", "consultation", "id", "咨询消息 → 咨询"),
    ("dietitian_customer", "customer_id", "sys_user", "user_id", "绑定 → 客户"),
    ("dietitian_customer", "dietitian_id", "sys_user", "user_id", "绑定 → 营养师"),
    ("notification", "user_id", "sys_user", "user_id", "通知 → 用户"),
    ("checkin_record", "user_id", "sys_user", "user_id", "打卡 → 用户"),
    ("banquet_guest", "menu_id", "banquet_menu", "id", "宴请宾客 → 菜单"),
    ("ocr_indicator", "report_id", "ocr_report", "id", "OCR 指标 → OCR 报告"),
    ("report_annotation", "dietitian_id", "sys_user", "user_id", "批注 → 营养师"),
    ("operation_log", "operator_id", "sys_user", "user_id", "操作日志 → 操作人"),
    ("chat_session", "user_id", "sys_user", "user_id", "会话 → 用户"),
]

# 常量 NULL 属正常（游客报告、游客会话）：NULL = 没有引用 ≠ 引用不存在，一律排除
NULLABLE = {("assessment_report", "user_id"), ("assessment_record", "user_id"), ("chat_session", "user_id")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    conn = pymysql.connect(
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB, charset="utf8mb4",
    )
    cur = conn.cursor()
    report: dict = {"dangling": [], "structure": {}, "distribution": {}}

    for child, ccol, parent, pcol, desc in RELATIONS:
        # NULL 外键 = 没有引用（游客数据），不是「引用不存在」→ 一律排除
        cur.execute(
            f"SELECT COUNT(*) FROM {child} x LEFT JOIN {parent} y ON x.{ccol}=y.{pcol} "
            f"WHERE y.{pcol} IS NULL AND x.{ccol} IS NOT NULL"
        )
        n = cur.fetchone()[0]
        report["dangling"].append({"relation": desc, "count": n})
        if not args.json:
            print(f"  {'OK ' if n == 0 else '!! '} {desc:24s} 悬空 {n}")

    struct_sql = {
        "报告_缺维度分": "SELECT COUNT(*) FROM assessment_report WHERE dimension_scores IS NULL OR JSON_LENGTH(dimension_scores)=0",
        "报告_无引用": "SELECT COUNT(*) FROM assessment_report WHERE `references` IS NULL OR JSON_LENGTH(`references`)=0",
        "报告_文本过短": "SELECT COUNT(*) FROM assessment_report WHERE analysis_text IS NULL OR CHAR_LENGTH(analysis_text)<200",
        "方案_无食谱": "SELECT COUNT(*) FROM diet_plan p LEFT JOIN diet_plan_recipe r ON r.plan_id=p.id WHERE r.id IS NULL",
        "会话_无消息": "SELECT COUNT(*) FROM chat_session s LEFT JOIN chat_message m ON m.session_id=s.id WHERE m.id IS NULL",
        "档案_无用户": "SELECT COUNT(*) FROM user_profile p LEFT JOIN sys_user u ON u.user_id=p.user_id WHERE u.user_id IS NULL",
        "报告_等级与分数不符": (
            "SELECT COUNT(*) FROM assessment_report WHERE "
            "(level='excellent' AND score<80) OR (level='good' AND (score<60 OR score>=80)) "
            "OR (level='needs_improvement' AND score>=60)"
        ),
    }
    if not args.json:
        print()
    for name, sql in struct_sql.items():
        cur.execute(sql)
        n = cur.fetchone()[0]
        report["structure"][name] = n
        if not args.json:
            print(f"  {'OK ' if n == 0 else '!! '} {name:24s} {n}")

    dist_sql = {
        "报告_等级分布": "SELECT level,COUNT(*) FROM assessment_report GROUP BY level",
        "报告_分数区间": "SELECT ROUND(AVG(score),2),MIN(score),MAX(score) FROM assessment_report",
        "用户_角色分布": "SELECT role,COUNT(*) FROM sys_user GROUP BY role",
        "档案_平均BMI外的身高体重": "SELECT ROUND(AVG(height_cm)),ROUND(AVG(weight_kg)) FROM user_profile",
        "慢病人数_高血压": "SELECT COUNT(*) FROM user_profile WHERE diseases_cipher LIKE '%高血压%'",
        "慢病人数_糖尿病": "SELECT COUNT(*) FROM user_profile WHERE diseases_cipher LIKE '%糖尿病%'",
        "慢病人数_高血脂": "SELECT COUNT(*) FROM user_profile WHERE diseases_cipher LIKE '%高血脂%'",
        "打卡_独立用户": "SELECT COUNT(DISTINCT user_id) FROM checkin_record",
        "打卡_去重后行数": "SELECT COUNT(*) FROM (SELECT DISTINCT user_id,checkin_date,check_type FROM checkin_record) t",
        "咨询_满意度": "SELECT ROUND(AVG(rating),2),COUNT(rating) FROM consultation WHERE rating IS NOT NULL",
    }
    if not args.json:
        print()
    for name, sql in dist_sql.items():
        cur.execute(sql)
        rows = cur.fetchall()
        report["distribution"][name] = rows
        if not args.json:
            print(f"  {name:26s} {rows}")

    cur.execute("SELECT table_name,table_rows FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_rows DESC",
                (settings.MYSQL_DB,))
    totals = cur.fetchall()
    total_rows = 0
    cur2 = conn.cursor()
    for t, _ in totals:
        cur2.execute(f"SELECT COUNT(*) FROM {t}")
        total_rows += cur2.fetchone()[0]
    report["totals"] = {"tables": len(totals), "rows": total_rows}
    if not args.json:
        print(f"\n  数据表 {len(totals)} 张，精确总行数 {total_rows:,}")
    cur2.close()

    cur.execute("SELECT run_id,source_type,status,row_counts,period_start,period_end "
                "FROM dataset_provenance ORDER BY id DESC LIMIT 3")
    if not args.json:
        print("\n  最近数据来源登记：")
        for r in cur.fetchall():
            print(f"    {r[0]} | {r[1]} | {r[2]} | {r[4]}~{r[5]}")
    conn.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    bad = sum(d["count"] for d in report["dangling"]) + sum(report["structure"].values())
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
