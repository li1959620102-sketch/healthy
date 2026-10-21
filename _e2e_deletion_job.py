# -*- coding: utf-8 -*-
"""注销清理作业端到端验收（自建一次性账号，不碰任何真实用户）。

为什么不用现成的到期任务验证：库里那批 pending 是历史测试留下的真实账号，
拿它们验证等于真删用户数据。本脚本自建 `U_DELTEST*` 账号 → 造三张表的个人
数据 → 登记一条已到期任务 → 走完整 apply 路径 → 断言数据被清、账号被匿名化
→ 最后把自建账号与任务行删干净，不留残留。

跑法：
    NO_PROXY=127.0.0.1,localhost HTTP_PROXY= HTTPS_PROXY= \
    PYTHONPATH=D:/AI_health D:/conda/envs/ai_py311/python.exe _e2e_deletion_job.py
"""
import sys
from datetime import datetime, timedelta

from app.services.deletion_service import _conn, run_task

UID = "U_DELTEST0001"
UNAME = "deltest0001"


def cleanup(conn):
    cur = conn.cursor()
    for sql in (
        "DELETE FROM notification WHERE user_id=%s",
        "DELETE FROM checkin_record WHERE user_id=%s",
        "DELETE FROM user_profile WHERE user_id=%s",
        "DELETE FROM data_deletion_task WHERE user_id=%s",
        "DELETE FROM sys_user WHERE user_id=%s",
    ):
        try:
            cur.execute(sql, (UID,))
        except Exception:                              # noqa: BLE001  表不存在即跳过
            pass
    conn.commit()
    cur.close()


def seed(conn):
    cleanup(conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sys_user (user_id, username, password_hash, real_name, status, created_at) "
        "VALUES (%s,%s,'x','删除作业验证账号','disabled',%s)",
        (UID, UNAME, datetime.now()),
    )
    cur.execute("INSERT INTO user_profile (user_id, height_cm, weight_kg, created_at) "
                "VALUES (%s,175,70.5,%s)", (UID, datetime.now()))
    cur.execute("INSERT INTO notification (user_id, type, title, content, created_at) "
                "VALUES (%s,'system','验证通知','注销清理验证用',%s)", (UID, datetime.now()))
    cur.execute("INSERT INTO checkin_record (user_id, checkin_date, check_type, created_at) "
                "VALUES (%s,'2026-10-17','meal',%s)", (UID, datetime.now()))
    cur.execute(
        "INSERT INTO data_deletion_task (user_id, state, scope, execute_at, created_at) "
        "VALUES (%s,'pending','mysql',%s,%s)",
        (UID, datetime.now() - timedelta(days=1), datetime.now() - timedelta(days=16)),
    )
    task_id = cur.lastrowid
    conn.commit()
    cur.close()
    return task_id


def counts(conn):
    cur = conn.cursor()
    out = {}
    for table in ("user_profile", "notification", "checkin_record"):
        cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE user_id=%s", (UID,))
        out[table] = cur.fetchone()[0]
    cur.execute("SELECT status FROM sys_user WHERE user_id=%s", (UID,))
    row = cur.fetchone()
    out["sys_user_status"] = row[0] if row else None
    cur.close()
    return out


def main():
    conn = _conn()
    failures = []

    def check(name, cond, got):
        print(("PASS " if cond else "FAIL ") + f"{name}  got={got}")
        if not cond:
            failures.append(name)

    try:
        task_id = seed(conn)
        print(f"seeded: user={UID} task_id={task_id}")

        before = counts(conn)
        check("造数据成功（档案 1 条）", before["user_profile"] == 1, before)
        check("造数据成功（通知 1 条）", before["notification"] == 1, before["notification"])

        # 1) 预演：只统计，不改状态
        preview = run_task(conn, task_id, UID, apply=False)
        check("预演给出待删行数", bool(preview.get("preview")), preview.get("preview"))
        check("预演不动数据", counts(conn)["user_profile"] == 1, counts(conn)["user_profile"])

        # 2) 真执行
        result = run_task(conn, task_id, UID, apply=True)
        after = counts(conn)
        check("档案已删除", after["user_profile"] == 0, after["user_profile"])
        check("通知已删除", after["notification"] == 0, after["notification"])
        check("打卡已删除", after["checkin_record"] == 0, after["checkin_record"])
        check("账号改为 deleted", after["sys_user_status"] == "deleted", after["sys_user_status"])
        check("任务状态回写 done", _task_state(conn, task_id) == "done", _task_state(conn, task_id))
        check("返回体含删除统计", isinstance(result.get("deleted"), dict), result.get("deleted"))

        # 3) 匿名化口径：用户名改写、手机号密文清空
        cur = conn.cursor()
        cur.execute("SELECT username, real_name, phone_hash FROM sys_user WHERE user_id=%s", (UID,))
        row = cur.fetchone()
        cur.close()
        check("用户名被改写", row and row[0] == f"deleted_{UID}", row[0] if row else None)
        check("手机号哈希已清空", row and row[2] is None, row[2] if row else None)
    finally:
        cleanup(conn)
        conn.close()
        print("cleaned: 自建账号与任务行已删除，未留残留")

    print("\n结果：" + ("全部通过" if not failures else f"{len(failures)} 项失败 -> {failures}"))
    return 1 if failures else 0


def _task_state(conn, task_id):
    cur = conn.cursor()
    cur.execute("SELECT state FROM data_deletion_task WHERE id=%s", (task_id,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


if __name__ == "__main__":
    sys.exit(main())
