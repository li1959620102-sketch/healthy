# -*- coding: utf-8 -*-
"""子智能体结果的**结构化汇总**。

刻意不做的事：不把多个通道的答案交给模型"总结成一段"。
那等于让模型在多个来源之间做二次演绎，一处失真就会被当成结论传播出去。
这里只按顺序把各通道的原始结果、引用、耗时、降级原因摆出来。

摘要文案只说**事实**（几个成功、几个失败、几个降级），不做任何评价性归纳。
"""
from __future__ import annotations

from typing import Any, Dict, List


def aggregate_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [s for s in steps if s.get("ok")]
    failed = [s for s in steps if not s.get("ok")]
    degraded = [s for s in steps if s.get("ok") and s.get("degraded")]

    refs: List[Dict[str, Any]] = []
    for s in ok:
        data = s.get("data") or {}
        if isinstance(data, dict):
            refs.extend(data.get("references") or [])

    # 引用去重（同一 doc 可能被多个通道命中）
    seen, uniq = set(), []
    for r in refs:
        key = str(r.get("doc_id") or r.get("source") or id(r))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    if not steps:
        summary = "没有匹配到可执行的子智能体，已如实返回空结果，不编造。"
    elif not ok:
        summary = "所有子通道均未成功，已如实返回失败原因，不编造结果。"
    elif failed:
        summary = (f"{len(ok)} 个子智能体返回结果，{len(failed)} 个失败"
                   f"（{', '.join(s['agent'] for s in failed)}）；失败部分见各步骤 note。")
    elif degraded:
        summary = f"{len(ok)} 个子智能体已返回，其中 {len(degraded)} 个如实降级（结果为空）。"
    else:
        summary = f"{len(ok)} 个子智能体全部正常返回。"

    return {
        "summary": summary,
        "ok_count": len(ok),
        "failed_count": len(failed),
        "degraded_count": len(degraded),
        "references": uniq,
        "citations_note": "汇总仅做结构化拼接，不经模型二次生成；引用均来自各子通道自身的检索结果",
    }


def blocked_result(refuse_text: str) -> Dict[str, Any]:
    """高危问题的结果体：一次子智能体都没调用。"""
    return {
        "summary": refuse_text,
        "ok_count": 0,
        "failed_count": 0,
        "degraded_count": 0,
        "references": [],
        "citations_note": "高危问题未进入业务链路，未检索、未调用模型",
    }
