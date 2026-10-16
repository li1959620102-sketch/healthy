# -*- coding: utf-8 -*-
"""注销账号数据清理作业（合规 D2 · 离线定时任务，默认只预演）。

背景
----
用户注销时 `DELETE /users/me` 只做两件事：账号置为 disabled、登记一条
`data_deletion_task`（scope=mysql,vector,minio，execute_at=注销+15 日）。
**真正把数据清掉的是本作业**。没有它，"15 日内完成清理"就只是任务表里的一行字。

用法
----
    python scripts/purge_accounts.py                 # 预演：列出待清理对象与影响行数
    python scripts/purge_accounts.py --apply         # 到期任务真正执行
    python scripts/purge_accounts.py --apply --user UXXXX    # 立即清理指定用户（运维补救）
    python scripts/purge_accounts.py --apply --all   # 忽略到期时间，清理全部 pending

清理口径（三处存储）
------------------
  1. **MySQL**：删除个人内容表（档案/测评/报告/问答/咨询/打卡/通知/计划/舌象/OCR/行程…），
     并把 `sys_user`、`operation_log` 做**匿名化**而非删除：
     审计日志需保留 ≥1 年（合规 SEC-14），删掉会破坏审计链；注销用户的身份标识
     以不可逆占位符替代，既满足"个人信息删除"又不毁审计。
  2. **MinIO**：删除该用户报告导出的 PDF 对象（`reports/{report_id}_*.pdf`）。
  3. **向量库**：知识库向量为**平台级**（药食同源语料），不含用户个人信息，
     故用户注销不涉及向量删除；如后续上线用户私有向量，在 `purge_vector()` 内补充。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql  # noqa: E402

from app.config import settings  # noqa: E402

# (表, WHERE 条件模板, 说明) —— 叶子在前，顺序即安全删除顺序
DELETES = [
    ("chat_message", "session_id IN (SELECT id FROM chat_session WHERE user_id=%s)", "问答消息"),
    ("consultation_message", "consultation_id IN (SELECT id FROM consultation WHERE user_id=%s)",
     "咨询消息"),
    ("diet_plan_recipe", "plan_id IN (SELECT id FROM diet_plan WHERE user_id=%s)", "食谱明细"),
    ("banquet_guest", "menu_id IN (SELECT id FROM banquet_menu WHERE user_id=%s)", "宴请宾客"),
    ("ocr_indicator", "report_id IN (SELECT id FROM ocr_report WHERE user_id=%s)", "OCR 指标"),
    ("report_annotation",
     "report_id IN (SELECT id FROM assessment_report WHERE user_id=%s)", "报告批注"),
    ("notification", "user_id=%s", "站内通知"),
    ("checkin_record", "user_id=%s", "打卡记录"),
    ("user_profile", "user_id=%s", "健康档案"),
    ("dietitian_profile", "user_id=%s", "营养师资质"),
    ("dietitian_customer", "customer_id=%s OR dietitian_id=%s", "绑定关系"),
    ("diet_plan", "user_id=%s", "食养方案"),
    ("assessment_report", "user_id=%s", "测评报告"),
    ("assessment_record", "user_id=%s", "测评记录"),
    ("chat_session", "user_id=%s", "问答会话"),
    ("consultation", "user_id=%s", "人工咨询"),
    ("tongue_image", "user_id=%s", "舌象图片"),
    ("ocr_report", "user_id=%s", "OCR 报告"),
    ("travel_plan", "user_id=%s", "旅行方案"),
    ("banquet_menu", "user_id=%s", "宴请菜单"),
    ("text2sql_query", "user_id=%s", "智能问数记录"),
    ("user_health_risk_assessment", "user_id=%s", "历史健康风险评估"),
]

ANON_USER = ("UPDATE sys_user SET username=CONCAT('deleted_', user_id), real_name='已注销用户', "
             "phone_cipher=NULL, phone_hash=NULL, email_cipher=NULL, email_hash=NULL, "
             "wechat_openid=NULL, alipay_user_id=NULL, status='deleted', deleted_at=%s "
             "WHERE user_id=%s")
ANON_LOG = "UPDATE operation_log SET operator_id=%s WHERE operator_id=%s"


def _conn():
    """从 MYSQL_URI 解析连接参数（.env 里通常只配 URI，host/port 等未必单列）。"""
    from sqlalchemy.engine import make_url

    url = make_url(settings.MYSQL_URI)
    return pymysql.connect(
        host=url.host, port=int(url.port or 3306), user=url.username,
        password=url.password, database=url.database,
        charset="utf8mb4", autocommit=False,
    )


def due_tasks(conn, all_pending: bool = False, user_id: str | None = None):
    """取出待清理任务：(id, user_id)。"""
    cur = conn.cursor()
    try:
        if user_id:
            cur.execute("SELECT id, user_id FROM data_deletion_task WHERE user_id=%s", (user_id,))
        elif all_pending:
            cur.execute("SELECT id, user_id FROM data_deletion_task WHERE state='pending'")
        else:
            cur.execute("SELECT id, user_id FROM data_deletion_task "
                        "WHERE state='pending' AND execute_at <= NOW()")
        return cur.fetchall()
    finally:
        cur.close()


def report_ids_of(cur, user_id: str):
    cur.execute("SELECT id FROM assessment_report WHERE user_id=%s", (user_id,))
    return [r[0] for r in cur.fetchall()]


def purge_mysql(cur, user_id: str) -> dict:
    """按表清理；返回 {表: 行数}。"""
    counts = {}
    for table, where, label in DELETES:
        params = [user_id]
        if where.count("%s") == 2:
            params.append(user_id)
        try:
            n = cur.execute(f"DELETE FROM `{table}` WHERE {where}", tuple(params))
            if n:
                counts[label] = n
        except pymysql.err.ProgrammingError as e:
            # 表结构未落地（如历史表被归档）时跳过，但不静默：打出来
            counts[f"跳过:{table}"] = f"{e}"
    return counts


def purge_minio(report_ids) -> list:
    """删除用户报告导出的 PDF 对象。"""
    if not report_ids:
        return []
    try:
        from minio import Minio
    except Exception:                                     # noqa: BLE001
        return ["MinIO SDK 不可用，已跳过对象清理"]
    client = Minio(settings.MINIO_ENDPOINT, access_key=settings.MINIO_ACCESS_KEY,
                   secret_key=settings.MINIO_SECRET_KEY, secure=settings.MINIO_SECURE)
    removed = []
    for rid in report_ids:
        try:
            for obj in client.list_objects(settings.MINIO_BUCKET,
                                           prefix=f"reports/{rid}_", recursive=True):
                client.remove_object(settings.MINIO_BUCKET, obj.object_name)
                removed.append(obj.object_name)
        except Exception as e:                            # noqa: BLE001
            removed.append(f"reports/{rid}_* 清理失败：{e}")
    return removed


def purge_vector() -> str:
    """向量库侧：知识库向量为平台级语料，不含个人信息，无需按用户删除。"""
    return "知识库向量为平台级语料，无用户个人信息，无需清理"


def run_task(conn, task_id: int, user_id: str, apply: bool) -> dict:
    cur = conn.cursor()
    try:
        rids = report_ids_of(cur, user_id)
        result = {"task_id": task_id, "user_id": user_id, "report_ids": rids}
        if not apply:
            preview = {}
            for table, where, label in DELETES:
                params = [user_id] + ([user_id] if where.count("%s") == 2 else [])
                cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where}", tuple(params))
                n = cur.fetchone()[0]
                if n:
                    preview[label] = n
            result["preview"] = preview
            return result

        result["deleted"] = purge_mysql(cur, user_id)
        cur.execute(ANON_LOG, (f"deleted_{user_id}", user_id))
        result["operation_log_anonymized"] = cur.rowcount
        cur.execute(ANON_USER, (datetime.now(), user_id))
        result["sys_user_anonymized"] = cur.rowcount
        cur.execute("UPDATE data_deletion_task SET state='done', finished_at=%s WHERE id=%s",
                    (datetime.now(), task_id))
        result["minio_removed"] = purge_minio(rids)
        result["vector"] = purge_vector()
        conn.commit()
        return result
    finally:
        cur.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="注销账号数据清理作业")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认仅预演）")
    ap.add_argument("--all", action="store_true", help="忽略到期时间，处理全部 pending")
    ap.add_argument("--user", help="只处理指定 user_id")
    args = ap.parse_args()

    conn = _conn()
    try:
        tasks = due_tasks(conn, all_pending=args.all, user_id=args.user)
        if not tasks:
            print("[purge_accounts] 没有到期任务，无需清理")
            return 0
        print(f"[purge_accounts] {'执行' if args.apply else '预演'}：{len(tasks)} 个任务")
        for task_id, user_id in tasks:
            try:
                info = run_task(conn, task_id, user_id, args.apply)
            except Exception as e:                        # noqa: BLE001
                conn.rollback()
                print(f"  ✗ 任务 {task_id}（{user_id}）失败：{e}")
                continue
            if args.apply:
                print(f"  ✓ {user_id}：删除 {info['deleted']}；MinIO {info['minio_removed']}；"
                      f"审计匿名化 {info['operation_log_anonymized']} 行；"
                      f"账号匿名化 {info['sys_user_anonymized']} 行")
            else:
                print(f"  · {user_id}：将删除 {info['preview']}；"
                      f"报告 PDF 对象 {len(info['report_ids'])} 组")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
