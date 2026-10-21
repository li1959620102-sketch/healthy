#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="${1:-}"
[[ "$VERSION" =~ ^[0-9A-Za-z._-]+$ ]] || { echo "用法：$0 VERSION（指定已发布镜像版本）" >&2; exit 2; }
export APP_VERSION="$VERSION"
export APP_BUILD="rollback-${VERSION}"

IMAGE="ai-health-api:${VERSION}"
docker image inspect "$IMAGE" >/dev/null
docker compose -f docker-compose.production.yml config >/dev/null
docker compose -f docker-compose.production.yml up -d --no-build --wait api

BASE="http://127.0.0.1:${API_PORT:-8000}"
python3 - "$BASE" "$VERSION" <<'PY'
import json, sys, urllib.request
base, expected = sys.argv[1:]
with urllib.request.urlopen(base + "/api/v1/version", timeout=15) as r:
    data = json.load(r)["data"]
if data.get("version") != expected:
    raise SystemExit(f"回滚版本校验失败：期望 {expected}，实际 {data.get('version')}")
print(f"回滚完成：{data.get('version')} build={data.get('build')}")
PY
