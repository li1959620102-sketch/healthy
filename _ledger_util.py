# -*- coding: utf-8 -*-
"""把已真实执行完的用例结果回填进台账 CSV。

三条原则（勿破坏）：
1. **只回填有实测依据的行**：自动化用例给测试函数名与运行结论，
   浏览器 E2E 给脚本名与判定明细，性能用例给实测数字。
   拿不到依据的行一律保持"未执行"，不猜、不套模板。
2. 回填是**幂等**的：按用例编号定位，改写前先备份原文件。
3. 台账里的"实际结果"写人话（发生了什么、实测多少），
   "备注(依据)"写判定口径来源（错误码/端点/文件:行），便于复核。
"""
from __future__ import annotations

import csv
import shutil
from typing import Dict, List

SRC = "AI智参平台_测试用例清单_已回填.csv"
BAK = "AI智参平台_测试用例清单_已回填.bak.csv"


def load() -> List[Dict[str, str]]:
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def dump(rows: List[Dict[str, str]]) -> None:
    shutil.copyfile(SRC, BAK)
    cols = list(rows[0].keys())
    with open(SRC, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def fill(rows: List[Dict[str, str]], mapping: Dict[str, Dict[str, str]]) -> Dict[str, int]:
    """mapping: {用例编号: {执行状态, 实际结果, 备注(依据), 自动化覆盖, 自动化证据}}"""
    hit, miss = 0, []
    by_no = {r["用例编号"]: r for r in rows}
    for no, patch in mapping.items():
        r = by_no.get(no)
        if r is None:
            miss.append(no)
            continue
        r.update({k: v for k, v in patch.items() if k in r})
        hit += 1
    if miss:
        print(f"  ! 台账中找不到的编号：{miss}")
    return {"filled": hit, "missing": len(miss)}
