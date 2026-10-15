# -*- coding: utf-8 -*-
"""生成可追溯的生产规模合成数据。

默认只打印计划；必须显式传入 ``--apply`` 才会写数据库。生成数据统一使用
``SYN``/``syn_`` 标识，并写入 dataset_provenance，禁止作为真实用户数据对外宣称。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.db import db_manager
from app.utils.security import hash_password

VERSION = "1.0.0"
DISCLAIMER = "合成压测数据，不对应任何真实个人"


def batches(rows: Iterable[dict], size: int):
    bucket = []
    for row in rows:
        bucket.append(row)
        if len(bucket) >= size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket


def random_dt(rng: random.Random, start: date, end: date) -> datetime:
    seconds = int((datetime.combine(end, time.max) - datetime.combine(start, time.min)).total_seconds())
    return datetime.combine(start, time.min) + timedelta(seconds=rng.randint(0, max(0, seconds)))


def execute_batches(conn, sql: str, rows: Iterable[dict], size: int) -> int:
    total = 0
    stmt = text(sql)
    for chunk in batches(rows, size):
        conn.execute(stmt, chunk)
        conn.commit()
        total += len(chunk)
        print(f"  已写入 {total:,} 行", flush=True)
    return total


def parse_args():
    p = argparse.ArgumentParser(description="生成带来源审计的生产规模合成数据")
    p.add_argument("--users", type=int, default=10_000)
    p.add_argument("--assessments", type=int, default=50_000)
    p.add_argument("--sessions", type=int, default=50_000)
    p.add_argument("--messages", type=int, default=200_000)
    p.add_argument("--checkins", type=int, default=100_000)
    p.add_argument("--plans", type=int, default=15_000)
    p.add_argument("--start-date", type=date.fromisoformat, default=date(2024, 10, 1))
    p.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 4, 30))
    p.add_argument("--seed", type=int, default=202410)
    p.add_argument("--batch-size", type=int, default=1_000)
    p.add_argument("--apply", action="store_true", help="确认写入数据库")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("end-date 不能早于 start-date")
    if args.users <= 0:
        raise SystemExit("users 必须大于 0")
    if args.batch_size <= 0:
        raise SystemExit("batch-size 必须大于 0")
    for name in ("assessments", "sessions", "messages", "checkins", "plans"):
        if getattr(args, name) < 0:
            raise SystemExit(f"{name} 不能小于 0")
    if args.messages and not args.sessions:
        raise SystemExit("messages 大于 0 时 sessions 必须大于 0")
    period_days = (args.end_date - args.start_date).days + 1
    checkin_capacity = args.users * period_days * 4
    if args.checkins > checkin_capacity:
        raise SystemExit(
            f"checkins 超过唯一约束容量 {checkin_capacity:,}，请增加用户数或时间范围"
        )
    counts = {
        "users": args.users,
        "assessments": args.assessments,
        "reports": args.assessments,
        "chat_sessions": args.sessions,
        "chat_messages": args.messages,
        "checkins": args.checkins,
        "plans": args.plans,
        "recipes": args.plans * 3,
    }
    print(json.dumps({
        "source_type": "synthetic",
        "period": [args.start_date.isoformat(), args.end_date.isoformat()],
        "counts": counts,
        "apply": args.apply,
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        print("当前为预览模式；确认后追加 --apply 才会写库。")
        return 0
    if db_manager is None:
        raise SystemExit("MYSQL_URI 未配置")

    rng = random.Random(args.seed)
    run_id = "syn-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    prefix = run_id.replace("-", "")[-18:]
    params = vars(args).copy()
    params["start_date"] = args.start_date.isoformat()
    params["end_date"] = args.end_date.isoformat()
    params.pop("apply", None)
    checksum = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    password_hash = hash_password("SyntheticOnly@2025")

    with db_manager.engine.connect() as conn:
        migration = (PROJECT_ROOT / "migrations" / "002_dataset_provenance.sql").read_text(encoding="utf-8")
        for statement in migration.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.commit()
        conn.execute(text(
            "INSERT INTO dataset_provenance "
            "(run_id,source_type,generator_version,period_start,period_end,status,parameters_json,checksum_sha256) "
            "VALUES (:r,'synthetic',:v,:s,:e,'running',:p,:c)"
        ), {"r": run_id, "v": VERSION, "s": args.start_date, "e": args.end_date,
            "p": json.dumps(params, ensure_ascii=False), "c": checksum})
        conn.commit()

        templates = conn.execute(text(
            "SELECT id, version FROM assessment_template WHERE status='published' ORDER BY id"
        )).fetchall()
        if not templates:
            conn.execute(text(
                "UPDATE dataset_provenance SET status='failed',completed_at=NOW() WHERE run_id=:r"
            ), {"r": run_id})
            conn.commit()
            raise SystemExit("没有已发布测评模板，无法生成关联数据")

        user_ids = [f"SYN{prefix.upper()}{i:07d}" for i in range(args.users)]
        print("写入合成用户")
        period_end_dt = datetime.combine(args.end_date, time.max)
        execute_batches(conn,
            "INSERT INTO sys_user "
            "(user_id,username,password_hash,real_name,sex,age,role,status,created_at,last_login_at,last_active_at) "
            "VALUES (:uid,:un,:ph,:rn,:sex,:age,'user','active',:created,:last_login,:last_active)",
            ({"uid": uid, "un": f"syn_{prefix}_{i:07d}", "ph": password_hash,
              "rn": f"合成用户{i:07d}", "sex": "男" if i % 2 else "女",
              "age": rng.randint(18, 78), "created": (dt := random_dt(rng, args.start_date, args.end_date)),
              "last_login": min(period_end_dt, dt + timedelta(days=rng.randint(0, 14))),
              "last_active": min(period_end_dt, dt + timedelta(days=rng.randint(0, 30)))}
             for i, uid in enumerate(user_ids)), args.batch_size)

        print("写入合成用户画像")
        execute_batches(conn,
            "INSERT INTO user_profile "
            "(user_id,height_cm,weight_kg,diet_preference,created_at,updated_at,allergies_json,taboo_json) "
            "VALUES (:uid,:h,:w,:diet,:created,:updated,:allergy,:taboo)",
            ({"uid": uid, "h": rng.randint(150, 190), "w": round(rng.uniform(45, 100), 2),
              "diet": rng.choice(["清淡", "低盐", "控糖", "均衡"]),
              "created": (dt := random_dt(rng, args.start_date, args.end_date)), "updated": dt,
              "allergy": json.dumps({"labels": [], "keywords": []}, ensure_ascii=False),
              "taboo": json.dumps({"labels": [], "keywords": []}, ensure_ascii=False)}
             for uid in user_ids), args.batch_size)

        print("写入合成测评")
        assessment_rows = []
        for i in range(args.assessments):
            uid = user_ids[i % len(user_ids)]
            tpl_id, tpl_ver = templates[i % len(templates)]
            dt = random_dt(rng, args.start_date, args.end_date)
            assessment_rows.append({"uid": uid, "tpl": tpl_id, "ver": tpl_ver,
                "answers": json.dumps({"synthetic": True, "seed": args.seed}),
                "idem": f"{run_id}-a-{i}", "dt": dt})
        execute_batches(conn,
            "INSERT INTO assessment_record "
            "(user_id,template_id,template_version,status,answers,current_page,progress,idempotency_key,started_at,last_saved_at,submitted_at) "
            "VALUES (:uid,:tpl,:ver,'completed',:answers,1,'1/1',:idem,:dt,:dt,:dt)",
            assessment_rows, args.batch_size)
        record_rows = conn.execute(text(
            "SELECT id,user_id,template_id,template_version,submitted_at FROM assessment_record "
            "WHERE idempotency_key LIKE :p ORDER BY id"
        ), {"p": run_id + "-a-%"}).fetchall()

        print("写入合成报告")
        execute_batches(conn,
            "INSERT INTO assessment_report "
            "(record_id,user_id,template_id,template_version,state,score,standardized_score,level,"
            "dimension_scores,suggestions,analysis_text,disclaimer,model_name,prompt_version,elapsed_ms,retry_count,generated_at,updated_at) "
            "VALUES (:rid,:uid,:tpl,:ver,'complete',:score,:score,:level,:dims,:sugs,:analysis,:disclaimer,"
            "'synthetic-benchmark','syn-v1',:elapsed,0,:dt,:dt)",
            ({"rid": r.id, "uid": r.user_id, "tpl": r.template_id, "ver": r.template_version,
              "score": (score := round(rng.uniform(45, 96), 2)),
              "level": "excellent" if score >= 85 else ("good" if score >= 65 else "needs_improvement"),
              "dims": json.dumps([{"key": "synthetic", "name": "合成维度", "score": score}], ensure_ascii=False),
              "sugs": json.dumps([{"item": DISCLAIMER, "note": "仅用于容量与性能验证"}], ensure_ascii=False),
              "analysis": DISCLAIMER, "disclaimer": DISCLAIMER,
              "elapsed": rng.randint(300, 5000), "dt": r.submitted_at}
             for r in record_rows), args.batch_size)

        print("写入合成会话")
        execute_batches(conn,
            "INSERT INTO chat_session (user_id,title,message_count,created_at,last_msg_at) "
            "VALUES (:uid,:title,:count,:dt,:dt)",
            ({"uid": user_ids[i % len(user_ids)], "title": f"SYN-{run_id}-{i}",
              "count": max(1, args.messages // max(1, args.sessions)),
              "dt": random_dt(rng, args.start_date, args.end_date)}
             for i in range(args.sessions)), args.batch_size)
        sessions = conn.execute(text(
            "SELECT id,created_at FROM chat_session WHERE title LIKE :p ORDER BY id"
        ), {"p": "SYN-" + run_id + "-%"}).fetchall()

        print("写入合成消息")
        execute_batches(conn,
            "INSERT INTO chat_message (session_id,role,content,refused,elapsed_ms,token_cost,created_at) "
            "VALUES (:sid,:role,:content,0,:elapsed,:tokens,:dt)",
            ({"sid": sessions[i % len(sessions)].id, "role": "user" if i % 2 == 0 else "assistant",
              "content": DISCLAIMER, "elapsed": rng.randint(50, 4000), "tokens": rng.randint(10, 600),
              "dt": sessions[i % len(sessions)].created_at + timedelta(seconds=i % 1800)}
             for i in range(args.messages)), args.batch_size)

        print("写入合成打卡")
        execute_batches(conn,
            "INSERT INTO checkin_record (user_id,checkin_date,check_type,note,created_at) "
            "VALUES (:uid,:d,:type,:note,:dt)",
            ({"uid": user_ids[i % len(user_ids)],
              "d": (dt := datetime.combine(
                  args.start_date + timedelta(days=(i // len(user_ids)) % period_days),
                  time(hour=(i // len(user_ids)) % 24),
              )).date(),
              "type": ("plan", "meal", "exercise", "weight")[(i // len(user_ids) // period_days) % 4],
              "note": DISCLAIMER, "dt": dt}
             for i in range(args.checkins)), args.batch_size)

        print("写入合成食养计划")
        execute_batches(conn,
            "INSERT INTO diet_plan "
            "(user_id,period,title,constitution,target_symptoms,avoid_foods,summary_text,state,disclaimer,generated_at,updated_at) "
            "VALUES (:uid,:period,:title,:constitution,:symptoms,:avoid,:summary,'complete',:disclaimer,:dt,:dt)",
            ({"uid": user_ids[i % len(user_ids)], "period": "week" if i % 3 else "month",
              "title": f"SYN-{run_id}-PLAN-{i}", "constitution": rng.choice(["平和", "气虚", "痰湿"]),
              "symptoms": json.dumps([], ensure_ascii=False), "avoid": json.dumps([], ensure_ascii=False),
              "summary": DISCLAIMER, "disclaimer": DISCLAIMER,
              "dt": random_dt(rng, args.start_date, args.end_date)}
             for i in range(args.plans)), args.batch_size)
        plans = conn.execute(text(
            "SELECT id FROM diet_plan WHERE title LIKE :p ORDER BY id"
        ), {"p": "SYN-" + run_id + "-PLAN-%"}).fetchall()

        print("写入合成食谱")
        execute_batches(conn,
            "INSERT INTO diet_plan_recipe "
            "(plan_id,meal_type,day_no,dish_name,ingredients,efficacy,caution) "
            "VALUES (:pid,:meal,1,:dish,:ingredients,:efficacy,:caution)",
            ({"pid": plan.id, "meal": ("breakfast", "lunch", "dinner")[j],
              "dish": f"合成菜品{j + 1}",
              "ingredients": json.dumps([{"name": "合成食材", "amount": "100g"}], ensure_ascii=False),
              "efficacy": DISCLAIMER, "caution": DISCLAIMER}
             for plan in plans for j in range(3)), args.batch_size)

        conn.execute(text(
            "UPDATE dataset_provenance SET status='complete',row_counts=:counts,completed_at=NOW() WHERE run_id=:r"
        ), {"counts": json.dumps(counts, ensure_ascii=False), "r": run_id})
        conn.commit()
    print(f"生成完成：{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
