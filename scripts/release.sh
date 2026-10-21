#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${1:-}"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]+$ ]] || { echo "用法：$0 VERSION（例如 2026.10.17-rc1）" >&2; exit 2; }
export APP_VERSION="$VERSION"
export APP_BUILD="${APP_BUILD:-$(git rev-parse --short HEAD)}"

# 发布前先构建并校验配置；数据库迁移和备份由发布人显式执行。
docker compose -f docker-compose.production.yml build api
docker compose -f docker-compose.production.yml config >/dev/null
docker compose -f docker-compose.production.yml up -d api
docker compose -f docker-compose.production.yml up -d --wait api

BASE="http://127.0.0.1:${API_PORT:-8000}"
python3 - "$BASE" "$VERSION" <<'PY'
import json, sys, urllib.request
base, expected = sys.argv[1:]
with urllib.request.urlopen(base + "/api/v1/version", timeout=15) as r:
    data = json.load(r)["data"]
if data.get("version") != expected:
    raise SystemExit(f"版本校验失败：期望 {expected}，实际 {data.get('version')}")
print(f"发布成功：{data.get('version')} build={data.get('build')}")
PY
