# -*- coding: utf-8 -*-
"""输出可用于评审的数据库规模与来源清单（只读）。"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.db import db_manager


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def main() -> int:
    if db_manager is None:
        raise SystemExit("MYSQL_URI 未配置")
    report = {"database": {}, "tables": {}, "provenance": []}
    with db_manager.engine.connect() as conn:
        report["database"]["name"] = conn.execute(text("SELECT DATABASE()" )).scalar()
        tables = conn.execute(text(
            "SELECT table_name,table_type,data_length,index_length "
            "FROM information_schema.tables WHERE table_schema=DATABASE() ORDER BY table_name"
        )).fetchall()
        total_rows = total_bytes = 0
        for name, table_type, data_length, index_length in tables:
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                continue
            rows = int(conn.execute(text("SELECT COUNT(*) FROM " + name)).scalar() or 0)
            size = int(data_length or 0) + int(index_length or 0)
            report["tables"][name] = {"type": table_type, "rows": rows, "bytes": size}
            if table_type == "BASE TABLE":
                total_rows += rows
                total_bytes += size
        report["database"].update({
            "base_table_rows": total_rows,
            "allocated_bytes": total_bytes,
        })
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='dataset_provenance'"
        )).scalar()
        if exists:
            rows = conn.execute(text(
                "SELECT run_id,source_type,generator_version,period_start,period_end,status,row_counts,"
                "checksum_sha256,created_at,completed_at FROM dataset_provenance ORDER BY id"
            )).mappings().all()
            report["provenance"] = [{k: encode(v) for k, v in dict(row).items()} for row in rows]
    print(json.dumps(report, ensure_ascii=False, indent=2, default=encode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
