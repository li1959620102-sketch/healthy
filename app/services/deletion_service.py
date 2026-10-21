# -*- coding: utf-8 -*-
"""注销账号数据清理作业（合规 D2）。

用户注销时 `DELETE /users/me` 只做两件事：账号置 disabled、登记一条
`data_deletion_task`（scope=mysql,vector,minio，execute_at=注销+15 日）。
**真正把数据清掉的是本作业**。没有执行器，"15 日内完成清理"就只是任务表里的一行字。

本模块被两个入口共用：
  · 定时任务 `deletion_purge`（每日一次，扫到期任务）—— 生产主路径；
  · `scripts/purge_accounts.py`（人工预演 / 指定用户补救）—— 运维兜底。
两者走同一份清理口径，避免"脚本删的和定时任务删的不一样"。

清理口径（三处存储）
--------------------
  1. **MySQL**：删除个人内容表，并把 `sys_user`、`operation_log` 做**匿名化**而非删除。
     审计日志需保留 ≥1 年（合规 SEC-14），删掉会破坏审计链。
  2. **MinIO**：删除该用户报告导出的 PDF 对象。
  3. **向量库**：知识库向量为平台级语料，不含个人信息，故不涉及；
     如后续上线用户私有向量，在 `purge_vector()` 内补充。

幂等性：删除本身幂等；任务状态机 pending → running → done/failed。
进程在 running 阶段崩溃时，该任务会停在 running，由 `reclaim_stale()` 在下一轮
按超时时间回收（否则一个卡住的任务永远不再被扫到，数据就真的一直留着了）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pymysql

from app.config import settings

logger = logging.getLogger("DeletionService")

# (表, WHERE 条件模板, 说明) —— 叶子在前，顺序即安全删除顺序
DELETES: List[tuple] = [
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

# running 状态超过这个时长视为"进程崩了没回写"，允许回收重试
STALE_RUNNING_MINUTES = 120


def _conn():
    """从 MYSQL_URI 解析连接参数（.env 里通常只配 URI）。"""
    from sqlalchemy.engine import make_url

    url = make_url(settings.MYSQL_URI)
    return pymysql.connect(
        host=url.host, port=int(url.port or 3306), user=url.username,
        password=url.password, database=url.database,
        charset="utf8mb4", autocommit=False,
    )


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def due_tasks(conn, all_pending: bool = False,
              user_id: Optional[str] = None, limit: int = 50) -> List[tuple]:
    """取出待清理任务：(id, user_id)。"""
    cur = conn.cursor()
    try:
        if user_id:
            cur.execute("SELECT id, user_id FROM data_deletion_task WHERE user_id=%s", (user_id,))
        elif all_pending:
            cur.execute("SELECT id, user_id FROM data_deletion_task "
                        "WHERE state IN ('pending','failed') ORDER BY id LIMIT %s", (limit,))
        else:
            cur.execute("SELECT id, user_id FROM data_deletion_task "
                        "WHERE state='pending' AND execute_at <= NOW() ORDER BY id LIMIT %s", (limit,))
        return cur.fetchall()
    finally:
        cur.close()


def reclaim_stale(conn, apply: bool = True) -> int:
    """把卡在 running 且超时的任务退回 pending，返回回收条数。

    没有这一步，"进程在清理中途被杀"会让任务永久停在 running：
    既不重试也不再进台账的待办范围，数据就一直留着。
    判定依据是 `started_at`（不是 created_at —— 后者是注销登记时间，
    可能是 15 天前，拿它算会把正在跑的任务也一起回收）。

    `apply=False`（预演）时只统计不写库：预演要能反复跑，不能在库里留痕迹。
    """
    cutoff = datetime.now() - timedelta(minutes=STALE_RUNNING_MINUTES)
    cur = conn.cursor()
    try:
        if not apply:
            cur.execute("SELECT COUNT(*) FROM data_deletion_task "
                        "WHERE state='running' AND (started_at IS NULL OR started_at < %s)",
                        (cutoff,))
            return int(cur.fetchone()[0] or 0)
        n = cur.execute(
            "UPDATE data_deletion_task SET state='pending', started_at=NULL, "
            "error=CONCAT(COALESCE(error,''), ' [超时回收]') "
            "WHERE state='running' AND (started_at IS NULL OR started_at < %s)",
            (cutoff,),
        )
        conn.commit()
        if n:
            logger.warning(f"回收 {n} 个卡在 running 的注销清理任务（超 {STALE_RUNNING_MINUTES} 分钟）")
        return int(n or 0)
    finally:
        cur.close()


def report_ids_of(cur, user_id: str) -> List[int]:
    cur.execute("SELECT id FROM assessment_report WHERE user_id=%s", (user_id,))
    return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# 清理动作
# --------------------------------------------------------------------------- #
def purge_mysql(cur, user_id: str) -> Dict[str, Any]:
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
            # 表结构未落地（历史表被归档）时跳过，但不静默：记进结果
            counts[f"跳过:{table}"] = str(e)
    return counts


def purge_minio(report_ids: List[int]) -> List[str]:
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


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def run_task(conn, task_id: int, user_id: str, apply: bool) -> Dict[str, Any]:
    cur = conn.cursor()
    try:
        rids = report_ids_of(cur, user_id)
        result: Dict[str, Any] = {"task_id": task_id, "user_id": user_id, "report_ids": rids}
        if not apply:
            preview = {}
            for table, where, label in DELETES:
                params = [user_id] + ([user_id] if where.count("%s") == 2 else [])
                try:
                    cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE {where}", tuple(params))
                    n = cur.fetchone()[0]
                except pymysql.err.ProgrammingError:
                    continue
                if n:
                    preview[label] = n
            result["preview"] = preview
            return result

        # 先置 running 并提交：这样中途崩溃能被 reclaim_stale 认出来
        cur.execute("UPDATE data_deletion_task SET state='running', started_at=%s, error=NULL "
                    "WHERE id=%s", (datetime.now(), task_id))
        conn.commit()

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
    except Exception as e:
        conn.rollback()
        # 回滚会把 running 也一起回滚掉，所以失败要单独写一次状态
        try:
            cur.execute("UPDATE data_deletion_task SET state='failed', error=%s, finished_at=%s "
                        "WHERE id=%s", (f"{type(e).__name__}: {e}"[:480], datetime.now(), task_id))
            conn.commit()
        except Exception:                                  # noqa: BLE001
            pass
        raise
    finally:
        cur.close()


def run_due_deletions(limit: int = 50, apply: bool = True) -> Dict[str, Any]:
    """定时任务入口：回收卡死任务 → 扫到期任务 → 逐个执行。

    返回的是**可读的执行摘要**，会原样出现在 `GET /ops/scheduler` 的 last.result 里，
    运维不需要登机器翻日志就能知道这一轮干了什么。

    `apply=False` 是预演：只统计将要删多少行，不改任何状态。
    """
    conn = _conn()
    try:
        reclaimed = reclaim_stale(conn, apply=apply)
        tasks = due_tasks(conn, limit=limit)
        if not tasks:
            return {"scanned": 0, "done": 0, "failed": 0, "reclaimed": reclaimed,
                    "dry_run": not apply, "message": "没有到期任务"}
        done, failed = 0, 0
        detail: List[Dict[str, Any]] = []
        for task_id, user_id in tasks:
            try:
                info = run_task(conn, task_id, user_id, apply)
                done += 1
                detail.append({"task_id": task_id, "user_id": user_id,
                               "deleted": info.get("deleted"),
                               "minio": len(info.get("minio_removed") or []),
                               **(info.get("preview") or {})})
            except Exception as e:                         # noqa: BLE001
                failed += 1
                detail.append({"task_id": task_id, "user_id": user_id, "error": str(e)})
                logger.warning(f"注销清理任务 {task_id}（{user_id}）失败：{e}")
        return {"scanned": len(tasks), "done": done, "failed": failed,
                "reclaimed": reclaimed, "dry_run": not apply, "detail": detail[:20]}
    finally:
        conn.close()
