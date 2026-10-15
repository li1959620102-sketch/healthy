# -*- coding: utf-8 -*-
"""智能查询服务（G 组）：RAG 检索 + 拒答 + 流式生成。

设计要点（落地设计评审 B1 / 需求合规 D4）：
- 拒答优先：命中拒答策略绝不调用模型。
- 闲聊分流：问候/感谢等非知识类问题不进 RAG，友好回应即可（不产生假引用）。
- 检索阈值：COSINE 相似度低于 KB_MIN_SIMILARITY 的片段不作为依据，
  此时如实回答"知识库暂无相关内容"，绝不编造（合规红线）。
- 懒加载：组件在首次请求时构建，导入阶段不连任何外部服务。
- 优雅降级：检索/模型不可用时不抛 500，流式返回降级提示。

知识库：Milvus 集合 settings.MILVUS_DEFAULT_COLLECTION（ai_health_kb_v1，
内容为 knowledge_base/*.md 药食同源常识，由 scripts/build_knowledge_base.py 构建）。
"""
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.config import settings
from app.core import rules
from app.core.refusal import check_refusal

logger = logging.getLogger("ChatQueryService")

# 纯问候/寒暄/感谢/告别：不走 RAG，直接友好回应
_SMALLTALK_RE = re.compile(
    r"^\s*(你好|您好|您好呀|你好呀|hi|hello|嗨|哈喽|在吗|在么|谢谢|多谢|感谢|"
    r"辛苦了|再见|拜拜|晚安|早安|下午好|晚上好|早上好)[!！。，~～\s]*$",
    re.IGNORECASE,
)

_DISCLAIMER = "\n\n—— 本内容由 AI 生成，不构成诊疗建议；涉及疾病请咨询执业医师。"

_SMALLTALK_REPLY = {
    "greet": ("您好！我是 AI 智参的营养师智能体 🌿\n"
              "可以问我：食材营养与搭配、药食同源食疗、慢病饮食原则等问题；"
              "也可以先做一次「健康风险测评」，我再结合结果给您更有针对性的食养参考。"),
    "thanks": ("不客气！祝您和家人吃得健康、吃得安心。"
               "有食材搭配或食疗方面的问题随时问我。"),
    "bye": ("再见！祝您健康，需要时随时回来找我 🌿"),
}


def _smalltalk_kind(question: str) -> Optional[str]:
    """识别纯寒暄类消息；命中返回类别，否则 None。"""
    q = (question or "").strip()
    if not q or len(q) > 24:
        return None
    if _SMALLTALK_RE.match(q):
        if re.search(r"谢谢|多谢|感谢", q):
            return "thanks"
        if re.search(r"再见|拜拜|晚安", q):
            return "bye"
        return "greet"
    return None


def build_dialog_messages(system_text: str, context: str, question: str,
                          history: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
    """把系统提示 + 历史对话 + 本轮（参考上下文 + 问题）组装成消息列表。

    history: [{"role": "user"|"assistant", "content": str}, ...]（由旧到新）
    返回: [{"role": ..., "content": ...}, ...]（纯数据，便于单元测试）
    """
    msgs: List[Dict[str, str]] = [{"role": "system", "content": system_text}]
    for h in (history or []):
        role = "assistant" if h.get("role") == "assistant" else "user"
        content = (h.get("content") or "").strip()
        if content:
            msgs.append({"role": role, "content": content[:1500]})
    msgs.append({"role": "user", "content": f"参考上下文：\n{context}\n\n用户问题：{question}"})
    return msgs


def retrieval_query(question: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
    """追问场景（很短的问句 + 有历史）拼上上一轮用户问题，提升指代类追问的检索召回。"""
    q = (question or "").strip()
    if not history or len(q) > int(getattr(rules, "CHAT_FOLLOWUP_MAX_CHARS", 12)):
        return q
    prev = ""
    for h in reversed(history):
        if h.get("role") != "assistant" and (h.get("content") or "").strip():
            prev = h["content"].strip()
            break
    return (prev[:80] + " " + q).strip() if prev else q


class ChatQueryService:
    def __init__(self):
        self._ready = False
        self._embeddings = None
        self._collection = None      # pymilvus Collection（原生检索，语义可控）
        self._llm = None

    # ------------------------------------------------------------------ #
    def _ensure_components(self) -> bool:
        """懒加载嵌入/向量集合/LLM。失败仅记日志并返回 False，不抛异常。"""
        if self._ready:
            return True
        try:
            from langchain_ollama import OllamaEmbeddings
            from langchain_openai import ChatOpenAI, OpenAIEmbeddings
            from langchain_ollama import ChatOllama
            import pymilvus

            # 嵌入
            if settings.EMBEDDING_STRATEGY == "silicon_flow":
                emb = OpenAIEmbeddings(
                    model=settings.SILICON_FLOW_EMBEDDING_MODEL,
                    openai_api_key=settings.SILICON_FLOW_API_KEY,
                    openai_api_base=settings.SILICON_FLOW_BASE_URL,
                )
            else:
                emb = OllamaEmbeddings(model=settings.EMBEDDING_MODEL,
                                       base_url=settings.OLLAMA_BASE_URL)

            # 向量集合（原生 pymilvus：COSINE 相似度，score 越大越相关）
            pymilvus.connections.connect(
                alias=settings.MILVUS_DEFAULT_ALIAS,
                host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
                db_name=settings.MILVUS_DEFAULT_DB,
            )
            from pymilvus import Collection
            col = Collection(settings.MILVUS_DEFAULT_COLLECTION,
                             using=settings.MILVUS_DEFAULT_ALIAS)
            col.load()

            # LLM（chat 接口；qwen3 的指令标签只对 chat 生效）
            if settings.LLM_STRATEGY == "silicon_flow":
                llm = ChatOpenAI(model=settings.SILICON_FLOW_LLM_MODEL,
                                 openai_api_key=settings.SILICON_FLOW_API_KEY,
                                 openai_api_base=settings.SILICON_FLOW_BASE_URL,
                                 temperature=0.1, streaming=True, max_tokens=350)
            else:
                llm = ChatOllama(model=settings.LLM_MODEL, base_url=settings.OLLAMA_BASE_URL,
                                 temperature=0.1, streaming=True, num_predict=350)

            self._embeddings = emb
            self._collection = col
            self._llm = llm
            self._ready = True
            logger.info("ChatQueryService 组件就绪（集合=%s）", settings.MILVUS_DEFAULT_COLLECTION)
            return True
        except Exception as e:
            logger.warning(f"ChatQueryService 组件初始化失败（降级运行）：{e}")
            self._ready = False
            return False

    # ------------------------------------------------------------------ #
    def retrieve(self, question: str, top_k: int = 4) -> List[Dict]:
        """相似检索 + 阈值过滤，返回 [{title, fragment, source, score}]。

        COSINE 相似度低于 settings.KB_MIN_SIMILARITY 的片段不返回——
        与其引用不相关内容误导模型，不如如实说"暂无相关内容"。
        """
        if not self._ensure_components():
            return []
        threshold = float(getattr(settings, "KB_MIN_SIMILARITY", 0.55))
        try:
            vec = self._embeddings.embed_query(question)
            res = self._collection.search(
                [vec], anns_field="vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k, output_fields=["text", "file_name", "source"],
            )
            out = []
            for hits in res:
                for h in hits:
                    if h.score < threshold:
                        continue
                    ent = h.entity
                    out.append({
                        "title": ent.get("file_name") or "未知来源",
                        "fragment": (ent.get("text") or "")[:300],
                        "source": ent.get("source") or "",
                        "score": round(float(h.score), 3),
                    })
            return out
        except Exception as e:
            logger.warning(f"检索失败：{e}")
            return []

    # ------------------------------------------------------------------ #
    async def stream(self, question: str, agent: str = "dietitian",
                   history: Optional[List[Dict[str, Any]]] = None) -> AsyncGenerator[Dict, None]:
        """生成 SSE 事件字典流。事件类型：start / delta / reference / done / error"""
        # 1) 闲聊分流：不检索、不附引用，直接友好回应
        kind = _smalltalk_kind(question)
        if kind:
            yield {"event": "start", "data": {"message_id": None}}
            yield {"event": "delta", "data": {"text": _SMALLTALK_REPLY[kind]}}
            yield {"event": "done", "data": {"elapsed_ms": 0, "smalltalk": True}}
            return

        # 2) 合规拒答：命中策略绝不调用模型
        refused, reason = check_refusal(question)
        if refused:
            yield {"event": "start", "data": {"message_id": None}}
            yield {"event": "delta", "data": {
                "text": "该问题涉及健康干预建议，我无法回答，建议咨询医生或前往正规医疗机构就诊。"}}
            yield {"event": "done", "data": {"elapsed_ms": 0, "refused": True, "reason": reason}}
            return

        # 3) 检索（带阈值）：追问场景用"上一轮问题 + 本轮"作为检索词
        docs = self.retrieve(retrieval_query(question, history))
        yield {"event": "start", "data": {"message_id": None}}
        references = [{"title": d["title"], "fragment": d["fragment"]} for d in docs]
        if references:
            yield {"event": "reference", "data": {"docs": references}}

        # 4) 无 LLM → 降级返回检索片段
        if not self._ensure_components() or self._llm is None:
            fallback = ("（模型服务未配置，以下为知识库检索片段）\n" + "\n".join(
                f"- {d['title']}：{d['fragment']}" for d in docs)
                if docs else "暂时无法检索到相关内容，请稍后再试或联系营养师。")
            yield {"event": "delta", "data": {"text": fallback}}
            yield {"event": "done", "data": {"elapsed_ms": 0, "degraded": True}}
            return

        # 5) 知识库无相关内容 → 如实回答，不编造（合规红线）
        if not docs:
            yield {"event": "delta", "data": {"text":
                "这个问题超出了我当前知识库（药食同源食材、慢病饮食原则、特殊人群饮食）的范围，"
                "我先不随意回答，以免误导您。\n"
                "您可以换个问法（如：某种食材的功效、高血压/糖尿病怎么吃），"
                "或到「健康风险测评」完成测评后获得个性化食养参考。"
                + _DISCLAIMER}}
            yield {"event": "done", "data": {"elapsed_ms": 0, "no_reference": True}}
            return

        # 6) 正常流式生成（系统/用户双消息，避免模型复读提示词模板）
        try:
            from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
            context = "\n".join(f"【{d['title']}】\n{d['fragment']}" for d in docs)
            system_text = (
                "你是「AI 智参」平台的营养师智能体，风格亲切、专业、简洁。\n"
                "回答规则：\n"
                "1. 只依据用户消息中给出的参考上下文回答；上下文没提到的不要编造。\n"
                "2. 用中文分点回答（每点一行），先给结论；全文不超过 150 字。\n"
                "3. 不给诊断结论、不给药物与剂量建议；需要就医的情形要提示就医。\n"
                "4. 回答末尾另起一行写：本内容由 AI 生成，不构成诊疗建议。\n"
                "5. 直接输出回答正文，不要复述问题、不要出现「用户：」「回答：」等模板字样。"
            )
            dialog = build_dialog_messages(system_text, context, question, history)
            msgs = []
            for m in dialog:
                if m["role"] == "system":
                    msgs.append(SystemMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    msgs.append(AIMessage(content=m["content"]))
                else:
                    msgs.append(HumanMessage(content=m["content"]))
            async for chunk in self._llm.astream(msgs):
                text = chunk.content if hasattr(chunk, "content") else str(chunk)
                if text:
                    yield {"event": "delta", "data": {"text": text}}
            yield {"event": "done", "data": {"elapsed_ms": 0}}
        except Exception as e:
            logger.error(f"生成失败：{e}")
            yield {"event": "error", "data": {"code": 60001, "message": "服务繁忙，请稍后重试"}}


chat_query_service = ChatQueryService()
