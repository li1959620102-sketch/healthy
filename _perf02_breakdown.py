# -*- coding: utf-8 -*-
"""拆解 SSE 首字节构成：start / reference / 首个 delta 各自的到达时刻。

用途：判断 PERF-02 的首字节耗时到底花在**检索**、**上游模型**，
还是**本地缓冲**（事件攒着不发）——三者的修法完全不同，不拆开就分不清。
"""
from __future__ import annotations

import json
import time
from urllib.parse import urljoin

import requests

BASE = "http://127.0.0.1:8000"
API = "/api/v1"

# 这几个问题此前已被缓存命中过，换一批新的确保走真实生成路径
QUESTIONS = [
    "银耳和黑豆一起煮有什么好处",
    "玉米须泡水能代替降压药吗",
    "芋头和山药哪个更适合糖尿病人",
    "红枣和枸杞泡水会上火吗",
    "苦瓜泡水喝能降血糖吗",
    "黑芝麻糊怎么吃更养发",
]


def main():
    r = requests.post(urljoin(BASE, f"{API}/auth/login"),
                      json={"username": "1234567890", "password": "Demo@2026"},
                      timeout=20).json()
    assert r.get("code") == 0, r
    hdr = {"Authorization": "Bearer " + r["data"]["access_token"]}

    rows = []
    for q in QUESTIONS:
        t0 = time.perf_counter()
        marks, deltas = {}, 0
        with requests.post(urljoin(BASE, f"{API}/chat/query"), headers=hdr,
                           json={"question": q}, stream=True, timeout=120) as rs:
            first_line = None
            for raw in rs.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if first_line is None:
                    first_line = time.perf_counter() - t0
                    marks["首行"] = first_line
                if raw.startswith("event:"):
                    marks.setdefault(raw[6:].strip(), time.perf_counter() - t0)
                elif raw.startswith("data:"):
                    deltas += 1
                    marks.setdefault("首个data", time.perf_counter() - t0)
                if raw.startswith("event: done"):
                    break
        total = time.perf_counter() - t0
        rows.append({
            "q": q,
            "首行": round(marks.get("首行", -1), 3),
            "start": round(marks.get("start", -1), 3),
            "reference": round(marks.get("reference", -1), 3),
            "首个data": round(marks.get("首个data", -1), 3),
            "总": round(total, 3),
            "delta行数": deltas,
        })
        print(json.dumps(rows[-1], ensure_ascii=False))

    with open("perf02_breakdown.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
