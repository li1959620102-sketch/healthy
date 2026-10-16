# -*- coding: utf-8 -*-
"""PERF-02 实测脚本：50 条**不重复**问题的真实链路时延。

为什么不用重复问题：答案缓存命中时约 20~40ms，测的是缓存不是链路。
压测必须打未命中路径，否则数字没有意义。

用法（后台跑，结果写 perf02_result.json）：
    python _perf02_probe.py 50
"""
from __future__ import annotations

import json
import sys
import time
from urllib.parse import urljoin

import requests

BASE = "http://127.0.0.1:8000"
API = "/api/v1"

SUBJECTS = [
    "高血压", "糖尿病", "高血脂", "痛风", "肥胖", "失眠", "便秘", "腹泻",
    "贫血", "免疫力低下", "慢性胃炎", "脂肪肝", "骨质疏松", "乳腺增生",
    "前列腺增生", "慢性咽炎", "鼻窦炎", "皮肤干燥", "手脚冰凉", "湿气重",
    "熬夜后", "吃海鲜", "喝酒后", "吃辣", "喝咖啡", "喝牛奶", "吃鸡蛋",
    "吃粗粮", "吃水果", "吃牛羊肉",
]


def build_questions(n: int):
    qs = [f"{s}人群日常饮食要注意什么" for s in SUBJECTS]
    qs += [f"{s}的食疗搭配有哪些" for s in SUBJECTS]
    qs += [f"{s}人群适合喝什么汤" for s in SUBJECTS]
    qs += [f"{s}可以吃哪些药食同源材料" for s in SUBJECTS]
    return qs[:n]


def p95(values):
    o = sorted(values)
    return o[min(len(o) - 1, int(len(o) * 0.95))]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    r = requests.post(urljoin(BASE, f"{API}/auth/login"),
                      json={"username": "1234567890", "password": "Demo@2026"},
                      timeout=20).json()
    assert r.get("code") == 0, r
    hdr = {"Authorization": "Bearer " + r["data"]["access_token"]}

    ttfb, total, rows, failed = [], [], [], 0
    for q in build_questions(n):
        t0 = time.perf_counter()
        try:
            with requests.post(urljoin(BASE, f"{API}/chat/query"), headers=hdr,
                               json={"question": q}, stream=True, timeout=120) as rs:
                first = None
                state = ""
                for line in rs.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if first is None:
                        first = time.perf_counter() - t0
                    if line.startswith("event:"):
                        state = line[6:].strip()
                    if state == "done":
                        break
            if first:
                ttfb.append(first)
                total.append(time.perf_counter() - t0)
                rows.append({"q": q, "ttfb": round(first, 3),
                             "total": round(time.perf_counter() - t0, 3)})
        except Exception as e:                        # noqa: BLE001
            failed += 1
            rows.append({"q": q, "error": f"{type(e).__name__}: {e}"[:120]})

    out = {
        "n": len(ttfb), "failed": failed,
        "ttfb_p50": round(sorted(ttfb)[len(ttfb) // 2], 3) if ttfb else None,
        "ttfb_p95": round(p95(ttfb), 3) if ttfb else None,
        "ttfb_max": round(max(ttfb), 3) if ttfb else None,
        "total_p50": round(sorted(total)[len(total) // 2], 3) if total else None,
        "total_p95": round(p95(total), 3) if total else None,
        "total_max": round(max(total), 3) if total else None,
        "rows": rows,
    }
    with open("perf02_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
