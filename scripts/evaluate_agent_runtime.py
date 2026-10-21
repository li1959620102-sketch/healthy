# -*- coding: utf-8 -*-
"""生成 Agent Runtime 本地就绪报告。

用法：python scripts/evaluate_agent_runtime.py
报告只代表代码与本地运行时检查，不替代真实生产环境验收。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_runtime.readiness import readiness_report


if __name__ == "__main__":
    root = str(Path(__file__).resolve().parents[1])
    report = readiness_report(root)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
