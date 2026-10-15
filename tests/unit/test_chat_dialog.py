# -*- coding: utf-8 -*-
"""多轮对话上下文（2026-09-11 新增能力）的纯函数单元测试。

覆盖：消息组装顺序（系统 → 历史 → 本轮）、历史裁剪长度、追问检索词增强。
不触达任何外部服务（LLM / Milvus / DB）。
"""
from app.services.chat_query_service import build_dialog_messages, retrieval_query


def test_dialog_messages_order_and_roles():
    history = [
        {"role": "user", "content": "高血压怎么吃"},
        {"role": "assistant", "content": "限盐、富钾。"},
    ]
    msgs = build_dialog_messages("系统提示", "【指南】限盐 5g", "那盐呢？", history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"], msgs
    assert msgs[0]["content"] == "系统提示"
    assert msgs[1]["content"] == "高血压怎么吃"
    assert msgs[2]["content"] == "限盐、富钾。"
    # 本轮消息必须带参考上下文 + 用户问题
    assert "【指南】限盐 5g" in msgs[-1]["content"] and "那盐呢？" in msgs[-1]["content"]


def test_dialog_messages_without_history():
    msgs = build_dialog_messages("sys", "ctx", "问题")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[-1]["content"].endswith("用户问题：问题")


def test_dialog_messages_skips_empty_and_truncates():
    history = [
        {"role": "user", "content": "   "},                 # 空内容跳过
        {"role": "assistant", "content": "x" * 5000},        # 超长裁剪
        {"role": "weird", "content": "按用户处理"},           # 未知角色 → user
    ]
    msgs = build_dialog_messages("sys", "ctx", "问题", history)
    assert [m["role"] for m in msgs] == ["system", "assistant", "user", "user"]
    assert len(msgs[1]["content"]) == 1500
    assert msgs[2]["content"] == "按用户处理"


def test_retrieval_query_short_followup_uses_previous_question():
    history = [{"role": "user", "content": "高血压怎么吃"}, 
               {"role": "assistant", "content": "限盐。"}]
    q = retrieval_query("那盐呢", history)          # 4 字，属追问
    assert "高血压怎么吃" in q and "那盐呢" in q


def test_retrieval_query_long_question_unchanged():
    history = [{"role": "user", "content": "高血压怎么吃"}]
    long_q = "我妈妈有高血压，平时做菜放盐有什么讲究吗？"
    assert retrieval_query(long_q, history) == long_q


def test_retrieval_query_no_history_unchanged():
    assert retrieval_query("那盐呢", None) == "那盐呢"
    assert retrieval_query("那盐呢", []) == "那盐呢"
