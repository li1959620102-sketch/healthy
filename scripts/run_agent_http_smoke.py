# -*- coding: utf-8 -*-
"""对运行中的 AI Health API 执行最小 HTTP 验收。"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return {"status_code": response.status, "body": json.loads(response.read().decode("utf-8"))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent HTTP readiness smoke")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    checks = []
    for name, path in (("health", "/api/health"), ("ready", "/api/v1/ready")):
        started = time.perf_counter()
        try:
            result = get_json(base + path)
            body = result["body"]
            ok = result["status_code"] == 200 and body.get("code", 0) == 0
            if name == "ready":
                ok = ok and body.get("data", {}).get("ready") is True
            checks.append({"name": name, "status": "passed" if ok else "failed",
                           "elapsed_ms": int((time.perf_counter() - started) * 1000),
                           "response": body})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "status": "failed", "error": str(exc)})
    report = {"suite": "agent-http-smoke-v1", "base_url": base,
              "generated_at": time.time(), "checks": checks,
              "passed": all(item["status"] == "passed" for item in checks)}
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
