# -*- coding: utf-8 -*-
"""舌象辨识评测脚本（O 组）。

用法：
    PYTHONPATH=D:/AI_health python scripts/eval_tongue.py --dir <图片目录>
    PYTHONPATH=D:/AI_health python scripts/eval_tongue.py --dir ... --json out.json

设计意图：舌象辨识没有标注集就不能声称"准确率达标"。本脚本只做两件
**可客观判定**的事，不做无根据的准确率宣称：

  1. **抗幻觉**：对目录下 `neg_*.{jpg,png}`（明确不含舌体的图）统计
     `visible=false` 的比例——这个指标有唯一正确答案，可以硬判；
  2. **可用性**：对 `pos_*.{jpg,png}`（含舌体的图）统计
     `visible=true` 且 `quality.sufficient=true` 的比例，并打印抽出的特征，
     供人工比对是否"说得对"。

字段对不对最终仍需执业中医师判定，脚本只把结果摊出来，不代替人下结论。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.tongue_service import recognize_bytes  # noqa: E402


def _collect(directory: str):
    pos, neg = [], []
    for pattern in ("pos_*", "neg_*"):
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                (pos if pattern.startswith("pos") else neg).append(path)
    return pos, neg


def main() -> int:
    parser = argparse.ArgumentParser(description="舌象辨识评测")
    parser.add_argument("--dir", default="D:/AI_health/_testdata/tongue", help="图片目录（pos_*/neg_* 前缀）")
    parser.add_argument("--json", default="", help="结果另存为 JSON")
    parser.add_argument("--limit", type=int, default=0, help="每类最多评测张数（0=不限）")
    args = parser.parse_args()

    pos, neg = _collect(args.dir)
    if not pos and not neg:
        print(f"目录中没有 pos_* / neg_* 命名的图片：{args.dir}")
        return 2
    if args.limit:
        pos, neg = pos[: args.limit], neg[: args.limit]

    rows = []

    def _run(path: str, expect_visible: bool):
        data = open(path, "rb").read()
        t0 = time.perf_counter()
        try:
            r = recognize_bytes(data)
            err = ""
        except Exception as e:                      # noqa: BLE001
            r, err = {}, f"{e}"
        cost = int((time.perf_counter() - t0) * 1000)
        row = {
            "file": os.path.basename(path),
            "expect_visible": expect_visible,
            "visible": r.get("visible"),
            "sufficient": (r.get("quality") or {}).get("sufficient"),
            "confidence": r.get("confidence"),
            "features": r.get("features"),
            "observations": r.get("observations"),
            "issues": (r.get("quality") or {}).get("issues"),
            "elapsed_ms": cost,
            "error": err,
        }
        rows.append(row)
        flag = "OK " if (r.get("visible") == expect_visible and not err) else "BAD"
        print(f"[{flag}] {row['file']:42s} visible={row['visible']!s:5s} "
              f"sufficient={row['sufficient']!s:5s} conf={row['confidence']} {cost}ms {err}")
        if row.get("features"):
            print(f"        {json.dumps(row['features'], ensure_ascii=False)}")
        if row.get("observations"):
            print(f"        obs={json.dumps(row['observations'], ensure_ascii=False)}")
        return row

    print(f"== 正例 {len(pos)} 张 / 反例 {len(neg)} 张 ==")
    for p in pos:
        _run(p, True)
    for n in neg:
        _run(n, False)

    # ---- 指标 ----
    neg_rows = [r for r in rows if not r["expect_visible"]]
    pos_rows = [r for r in rows if r["expect_visible"]]
    halluc = [r for r in neg_rows if r["visible"] is True]
    usable = [r for r in pos_rows if r["visible"] is True and r["sufficient"] is True]

    summary = {
        "positive_total": len(pos_rows),
        "negative_total": len(neg_rows),
        "antihallucination_rate": round(1 - len(halluc) / len(neg_rows), 3) if neg_rows else None,
        "hallucinated_files": [r["file"] for r in halluc],
        "usable_rate": round(len(usable) / len(pos_rows), 3) if pos_rows else None,
        "avg_elapsed_ms": round(sum(r["elapsed_ms"] for r in rows) / len(rows)) if rows else 0,
        "rows": rows,
    }
    print("\n== 汇总 ==")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
