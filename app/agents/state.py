# -*- coding: utf-8 -*-
"""智能体图（LangGraph）· 状态定义。

## 为什么主图和子图用**两套** state

如果父子共用一套 TypedDict，子图返回时会把父图传进来的 `steps` 一并带回，
配合 reducer 就会重复累加——调度 3 个子智能体会得到 6 条以上步骤记录。
这是把子图当节点嵌入时最常见的坑。

因此这里明确分层：

  · `AgentState`    主图状态：请求、意图、调度方案、各通道结果的集合；
  · `SubAgentState` 子图状态：单个子智能体内部的中间量（检索词、检索结果、
                    重试次数、答案…），**互不复用**。

父图节点负责把子图结果收敛成一条 step 记录，边界清晰。
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict


def merge_steps(left: Optional[List[Dict[str, Any]]],
                right: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """steps 的 reducer：按到达顺序拼接。

    主图并行调度多个子智能体时，多个分支会同时写 `steps`。没有 reducer 的话
    LangGraph 会报并发更新冲突（或互相覆盖，只留下最后一个）——两种结果都不能接受：
    前者让整次问答 500，后者让用户只看到 1 个子智能体的结果却以为是全部。
    """
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    """主图状态。total=False：节点只返回自己改动的字段。"""

    # —— 输入 ——
    text: str
    user: Optional[dict]
    execute: bool                # False = 只出调度方案不执行（预览/评测用）
    max_agents: int              # 单次最多调度几个子智能体
    started_at: float            # perf_counter，供 elapsed_ms 计算
    classify_ms: int

    # —— 意图与调度 ——
    intent: Dict[str, Any]       # 分类器原始结果（含 blocked / confidence / explain）
    plan: List[Dict[str, Any]]   # [{"agent": "assessment", "order": 1}, ...]

    # —— 汇总 ——
    steps: Annotated[List[Dict[str, Any]], merge_steps]
    result: Dict[str, Any]

    # —— 执行信息（可观测性）——
    trace: Annotated[List[Dict[str, Any]], merge_steps]


class SubAgentState(TypedDict, total=False):
    """子图状态：一个子智能体内部的全部中间量。

    `step` 是唯一的对外契约——不论子图内部绕了几个节点、重试过几次，
    最终只交出一条结构化的 step 记录（agent / ok / degraded / summary /
    data / elapsed_ms / note），主图与前端都只认这个结构。
    """

    text: str
    user: Optional[dict]
    runtime_task_id: str       # 统一 Runtime 任务 ID，用于工具审计与检查点
    memory_context: Dict[str, Any]  # 按 Agent 过滤后的分层记忆
    started_at: float            # 子图入口时间戳（perf_counter），供 step 报耗时

    # 检索与生成（dietitian 子图用）
    query: str
    attempts: int
    docs: List[Dict[str, Any]]
    top_score: float
    answer: str
    references: List[Dict[str, Any]]
    refused: bool
    refusal_text: str

    # 通用产出
    summary: str
    data: Any
    degraded: bool
    note: str

    # 对外契约
    step: Dict[str, Any]
