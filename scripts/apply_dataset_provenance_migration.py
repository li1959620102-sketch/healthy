# -*- coding: utf-8 -*-
"""幂等创建数据来源审计表；仅变更表结构，不写业务数据。"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.db import db_manager


def main() -> int:
    if db_manager is None:
        raise SystemExit("MYSQL_URI 未配置")
    sql_path = PROJECT_ROOT / "migrations" / "002_dataset_provenance.sql"
    statements = [item.strip() for item in sql_path.read_text(encoding="utf-8").split(";") if item.strip()]
    with db_manager.engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='dataset_provenance'"
        )).scalar()
    if exists != 1:
        raise SystemExit("迁移校验失败：dataset_provenance 未创建")
    print("迁移完成：dataset_provenance 已就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
