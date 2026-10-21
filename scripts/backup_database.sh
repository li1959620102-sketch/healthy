#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
[[ -f "$ENV_FILE" ]] || { echo "找不到环境文件：$ENV_FILE" >&2; exit 2; }

ENV_FILE="$ENV_FILE" BACKUP_DIR="$BACKUP_DIR" python3 - <<'PY'
import gzip
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url
from dotenv import dotenv_values

config = dotenv_values(os.environ["ENV_FILE"])
uri = config.get("MYSQL_URI")
if not uri:
    raise SystemExit("必须在环境文件中设置 MYSQL_URI")
url = make_url(uri)
if url.get_backend_name() != "mysql":
    raise SystemExit("仅支持 MySQL 数据库备份")
if not url.database:
    raise SystemExit("MYSQL_URI 未指定数据库名")
out_dir = Path(os.environ["BACKUP_DIR"])
out_dir.mkdir(parents=True, exist_ok=True)
name = f"{url.database}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sql.gz"
output = out_dir / name
args = ["mysqldump", "--single-transaction", "--routines", "--triggers", "--events",
        "--hex-blob", "--set-gtid-purged=OFF", "--host", url.host or "127.0.0.1",
        "--port", str(url.port or 3306), "--user", url.username or "", url.database]
env = os.environ.copy()
if url.password:
    env["MYSQL_PWD"] = url.password
with output.open("wb") as raw:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            while chunk := proc.stdout.read(1024 * 1024):
                compressed.write(chunk)
    finally:
        stderr = proc.stderr.read().decode("utf-8", "replace")
        code = proc.wait()
if code:
    output.unlink(missing_ok=True)
    raise SystemExit(f"mysqldump 失败（{code}）：{stderr.strip()}")
if output.stat().st_size < 100:
    output.unlink(missing_ok=True)
    raise SystemExit("备份文件异常过小，已删除")
print(f"备份完成：{output} ({output.stat().st_size} bytes)")
PY
