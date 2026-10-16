# -*- coding: utf-8 -*-
"""意图识别评测：算混淆矩阵、准确率、误判率，并按门禁退出。

这是回答「意图识别误判率多少、怎么评测的」的**可执行答案**：
所有数字都由规则跑出来，没有任何模型参与，评测本身可复现、可回归。

用法：
    python scripts/eval_intent.py            # 人读报告
    python scripts/eval_intent.py --json     # 机器可读
    python scripts/eval_intent.py --fail-under 90   # 自定义准确率门禁

指标定义（不要混淆）：
  · **准确率 accuracy**   全部样本里「主意图或阻断判定完全符合标注」的比例。
  · **误判率 misroute**   非高危样本里，主意图与标注不一致的比例。
  · **高危召回 blocked_recall** 标注为 blocked 的样本里被真正拦下的比例。
    **这一项必须 100%** —— 路由层漏掉一个高危，后面每个子通道都得自己再拦一遍，
    漏一个就是一次合规事故。所以门禁里它是硬条件，不参与加权平均。
  · **兜底率 fallback_rate** 走兜底的比例。过高说明规则覆盖不足，
    但过低同样可疑——可能把不该判的硬判了。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import intent_service                      # noqa: E402
from scripts.intent_eval_dataset import (                    # noqa: E402
    DATASET, HOLDOUT, HOLDOUT2, normalize,
)


def _pred_primary(result: dict) -> str:
    """把分类器输出折算成可与标注对比的标签。

    blocked → 'blocked'；否则主意图就是 primary（兜底时 primary=fallback 意图，
    但**兜底不等于答对**——标注为 fallback 的样本，主意图是谁都算对）。
    """
    if result.get("blocked"):
        return "blocked"
    return result.get("primary") or ""


def _label_of(label) -> str:
    """标注 → 可与预测对比的单一标签。多意图取首位（首位即主意图）。"""
    mode, exp = normalize(label)
    if mode in ("blocked", "fallback"):
        return mode
    return exp[0] if exp else ""


def evaluate(dataset=None) -> dict:
    dataset = DATASET if dataset is None else dataset
    rows = []
    for text, label, note in dataset:
        mode, exp = normalize(label)
        r = intent_service.classify(text)
        pred = _pred_primary(r)
        gold = _label_of(label)
        if mode == "fallback":
            # 标注为「意图不明」：只要没误判进某个具体业务意图就算对
            hit = (not r.get("blocked")) and pred in ("fallback", "dietitian")
        elif mode == "multi":
            # 多意图本来就没有唯一主意图，判据是「主意图落在期望集合内」
            hit = pred in exp
        else:
            hit = (pred == gold)
        rows.append({
            "text": text, "gold": gold, "pred": pred, "hit": hit, "mode": mode,
            "blocked_pred": bool(r.get("blocked")),
            "fallback_pred": bool(r.get("fallback")),
            "confidence": r.get("confidence", 0.0),
            "margin": r.get("margin", 0.0),
            "ranked": [x["intent"] for x in r.get("ranked", [])],
            "note": note,
        })

    n = len(rows)
    blocked_rows = [x for x in rows if x["gold"] == "blocked"]
    normal_rows = [x for x in rows if x["gold"] != "blocked"]

    def rate(rs):
        return round(sum(1 for x in rs if x["hit"]) / len(rs), 4) if rs else 0.0

    labels = sorted({x["gold"] for x in rows} | {x["pred"] for x in rows})
    matrix = {g: {p: 0 for p in labels} for g in labels}
    for x in rows:
        matrix[x["gold"]][x["pred"]] = matrix[x["gold"]].get(x["pred"], 0) + 1

    per_label = {}
    for g in labels:
        tp = matrix[g][g]
        fp = sum(matrix[o].get(g, 0) for o in labels if o != g)
        fn = sum(matrix[g].get(p, 0) for p in labels if p != g)
        prec = round(tp / (tp + fp), 4) if (tp + fp) else None
        rec = round(tp / (tp + fn), 4) if (tp + fn) else None
        per_label[g] = {"support": tp + fn, "tp": tp, "fp": fp, "fn": fn,
                        "precision": prec, "recall": rec}

    return {
        "total": n,
        "accuracy": round(sum(1 for x in rows if x["hit"]) / n, 4) if n else 0.0,
        "misroute_rate": round(1 - rate(normal_rows), 4) if normal_rows else 0.0,
        "blocked_recall": rate(blocked_rows),
        "blocked_support": len(blocked_rows),
        "fallback_rate": round(sum(1 for x in rows if x["fallback_pred"]) / n, 4) if n else 0.0,
        "mean_confidence": round(sum(x["confidence"] for x in rows) / n, 4) if n else 0.0,
        "per_label": per_label,
        "confusion": matrix,
        "misses": [x for x in rows if not x["hit"]],
    }


def _report(r: dict, title: str = "主智能体 · 意图识别评测报告") -> str:
    out = []
    out.append("=" * 74)
    out.append(title)
    out.append("=" * 74)
    out.append(f"样本数           {r['total']}")
    out.append(f"准确率           {r['accuracy'] * 100:.1f}%")
    out.append(f"误判率（非高危） {r['misroute_rate'] * 100:.1f}%")
    out.append(f"高危召回         {r['blocked_recall'] * 100:.1f}%  "
               f"（{r['blocked_support']} 条样本，门禁要求 100%）")
    out.append(f"兜底率           {r['fallback_rate'] * 100:.1f}%")
    out.append(f"平均置信度       {r['mean_confidence']:.2f}")
    out.append("-" * 74)
    out.append("分类目���")
    out.append(f"{'label':<14}{'support':>8}{'precision':>12}{'recall':>10}")
    for g, v in r["per_label"].items():
        p = "-" if v["precision"] is None else f"{v['precision'] * 100:.1f}%"
        rc = "-" if v["recall"] is None else f"{v['recall'] * 100:.1f}%"
        out.append(f"{g:<14}{v['support']:>8}{p:>12}{rc:>10}")
    out.append("-" * 74)
    out.append("混淆矩阵（行=标注，列=预测）")
    cols = list(r["confusion"].keys())
    out.append(f"{'':<14}" + "".join(f"{c[:8]:>10}" for c in cols))
    for g in cols:
        out.append(f"{g:<14}" + "".join(f"{r['confusion'][g][c]:>10}" for c in cols))
    if r["misses"]:
        out.append("-" * 74)
        out.append(f"误判明细（{len(r['misses'])} 条）")
        for m in r["misses"]:
            out.append(f"  「{m['text'][:34]}」 标注={m['gold']} 预测={m['pred']} "
                       f"置信={m['confidence']:.2f}")
    else:
        out.append("无误判样本")
    out.append("=" * 74)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="意图识别评测")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--fail-under", type=float, default=90.0,
                    help="准确率门禁（%%），低于则退出码 1；默认 90")
    ap.add_argument("--no-gate", action="store_true", help="只报告不作为门禁")
    ap.add_argument("--holdout", metavar="1|2", choices=["1", "2"],
                    help="跑留出集：1=第一批（refusal 改动前，已污染），2=第二批（当前有效）")
    args = ap.parse_args()

    if args.holdout:
        ds = HOLDOUT if args.holdout == "1" else HOLDOUT2
        tag = ("第一批留出集（已污染：refusal.py 后续改动使其失效，仅存档）"
               if args.holdout == "1" else "第二批留出集（当前有效 · 检过拟合）")
        r = evaluate(ds)
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
              else _report(r, f"主智能体 · 意图识别评测报告（{tag}）"))
        if not args.no_gate and r["blocked_recall"] < 1.0:
            print(f"[门禁未过] 留出集高危召回 {r['blocked_recall'] * 100:.1f}% < 100%",
                  file=sys.stderr)
            return 1
        return 0

    r = evaluate()
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else _report(r))

    if args.no_gate:
        return 0
    # 高危召回是硬门禁：路由层漏一个高危就是一次合规事故
    if r["blocked_recall"] < 1.0:
        print(f"[门禁未过] 高危召回 {r['blocked_recall'] * 100:.1f}% < 100%", file=sys.stderr)
        return 1
    if r["accuracy"] * 100 < args.fail_under:
        print(f"[门禁未过] 准确率 {r['accuracy'] * 100:.1f}% < {args.fail_under}%",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
