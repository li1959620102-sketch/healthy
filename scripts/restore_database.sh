#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "用法：ENV_FILE=/path/to/restore.env $0 /path/to/backup.sql.gz TARGET_DATABASE" >&2
  echo "恢复前需设置 RESTORE_CONFIRM=I_UNDERSTAND_THIS_OVERWRITES_TARGET" >&2
}
[[ $# -eq 2 ]] || { usage; exit 2; }
BACKUP_FILE="$1"
TARGET_DATABASE="$2"
[[ -f "$BACKUP_FILE" ]] || { echo "备份文件不存在：$BACKUP_FILE" >&2; exit 2; }
[[ "${RESTORE_CONFIRM:-}" == "I_UNDERSTAND_THIS_OVERWRITES_TARGET" ]] || {
  echo "拒绝恢复：必须设置 RESTORE_CONFIRM=I_UNDERSTAND_THIS_OVERWRITES_TARGET" >&2; exit 2;
}
[[ "$TARGET_DATABASE" =~ ^[A-Za-z0-9_]+$ ]] || { echo "目标数据库名格式不合法" >&2; exit 2; }
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$BACKUP_FILE")/.." && pwd)/.env}"
[[ -f "$ENV_FILE" ]] || { echo "找不到环境文件：$ENV_FILE" >&2; exit 2; }

ENV_FILE="$ENV_FILE" BACKUP_FILE="$BACKUP_FILE" TARGET_DATABASE="$TARGET_DATABASE" python3 - <<'PY'
import gzip
import os
import re
import subprocess
from pathlib import Path
from sqlalchemy.engine import make_url
from dotenv import dotenv_values

config = dotenv_values(os.environ["ENV_FILE"])
uri = config.get("MYSQL_URI")
if not uri:
    raise SystemExit("必须在环境文件中设置 MYSQL_URI")
url = make_url(uri)
if url.get_backend_name() != "mysql":
    raise SystemExit("仅支持 MySQL 数据库恢复")
target = os.environ["TARGET_DATABASE"]
if not re.fullmatch(r"[A-Za-z0-9_]+", target):
    raise SystemExit("目标数据库名格式不合法")
args = ["mysql", "--host", url.host or "127.0.0.1", "--port", str(url.port or 3306),
        "--user", url.username or "", target]
env = os.environ.copy()
if url.password:
    env["MYSQL_PWD"] = url.password
with gzip.open(Path(os.environ["BACKUP_FILE"]), "rb") as source:
    proc = subprocess.run(args, stdin=source, env=env, check=False)
if proc.returncode:
    raise SystemExit(f"mysql 恢复失败，退出码 {proc.returncode}")
print(f"恢复完成：{os.environ['BACKUP_FILE']} -> {target}")
PY
