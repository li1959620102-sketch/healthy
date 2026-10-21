# -*- coding: utf-8 -*-
"""智能体图（LangGraph）。

  · `state.py`     主图 / 子图两套状态定义（分层原因见模块注释）
  · `subgraphs.py` 六个子智能体各自的图；dietitian 内含 self-RAG 循环
  · `supervisor.py` 主图：分类 → 规划 → Send 扇出 → 汇总
  · `aggregate.py`  结构化汇总（不做二次生成）
"""
