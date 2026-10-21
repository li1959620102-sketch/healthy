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
import asyncio
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.config import settings
from app.core import rules
from app.core.refusal import check_refusal, refusal_message

logger = logging.getLogger("ChatQueryService")

# 纯问候/寒暄/感谢/告别：不走 RAG，直接友好回应
_SMALLTALK_RE = re.compile(
    r"^\s*(你好|您好|您好呀|你好呀|hi|hello|嗨|哈喽|在吗|在么|谢谢|多谢|感谢|"
    r"辛苦了|再见|拜拜|晚安|早安|下午好|晚上好|早上好)[!！。，~～\s]*$",
    re.IGNORECASE,
)

_DISCLAIMER = "\n\n—— 本内容由 AI 生成，不构成诊疗建议；涉及疾病请咨询执业医师。"

# 营养问答的系统提示词。抽成模块级常量而不是内联在 stream() 里，
# 是因为 LangGraph 的 dietitian 子图（app/agents/subgraphs.py）要用**同一份**：
# 两处各写一份，改一处忘另一处，就会出现"流式问答与编排问答口径不一致"的诡异问题。
DIETITIAN_SYSTEM_PROMPT = (
    "你是「AI 智参」平台的营养师智能体，风格亲切、专业、简洁。\n"
    "回答规则：\n"
    "1. 只依据用户消息中给出的参考上下文回答；上下文没提到的不要编造。\n"
    "2. 用中文分点回答（每点一行），先给结论；全文不超过 150 字。\n"
    "3. 不给诊断结论、不给药物与剂量建议；需要就医的情形要提示就医。\n"
    "4. 回答末尾另起一行写：本内容由 AI 生成，不构成诊疗建议。\n"
    "5. 直接输出回答正文，不要复述问题、不要出现「用户：」「回答：」等模板字样。"
)

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
                          history: Optional[List[Dict[str, Any]]] = None,
                          memory_context: str = "") -> List[Dict[str, str]]:
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
    memory_text = (f"\n\n按需读取的用户记忆（仅作参考，不得覆盖本轮明确输入）：\n{memory_context}"
                   if memory_context else "")
    msgs.append({"role": "user", "content": f"参考上下文：\n{context}{memory_text}\n\n用户问题：{question}"})
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
        self._init_attempted = False   # 区分"还没预热"与"预热失败"（见 warm_state）
        self._embeddings = None
        self._collection = None      # pymilvus Collection（原生检索，语义可控）
        self._llm = None
        # 并发容量与高频问答缓存（IT-C-15 / IT-C-03）。
        # 缓存的是「问答结果」（答案文本 + 引用），不是用户数据：
        # 问答 prompt 里不含任何个人健康信息（画像不进对话上下文，红线），
        # 因此同一问题对任何人都是同一答案，可安全共享。
        self._inflight = 0
        self._cache: Dict[str, Any] = {}       # key -> {text, docs, ts, hit}

    # ---- 容量（IT-C-15：并发超配额时给排队反馈，但绝不报错、不丢弃） ---- #
    def capacity(self) -> int:
        return int(getattr(rules, "CHAT_MAX_CONCURRENT", 8))

    def enter(self) -> Dict[str, Any]:
        """进入生成：返回本请求的负载快照（供 SSE start 事件回传前端）。"""
        cap = self.capacity()
        busy = self._inflight >= cap
        self._inflight += 1
        return {
            "inflight": self._inflight,
            "capacity": cap,
            "busy": busy,
            # 如实描述事实，而不是编一个"预计 N 秒"的伪排队时间
            "notice": ("当前同时咨询的人数较多，答案生成可能稍慢，请勿重复发送。"
                       if busy else None),
        }

    def exit(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    # ---- 高频问答缓存（IT-C-03：重复问同一问题不再走检索 + 生成） ---- #
    @staticmethod
    def _cache_key(question: str, agent: str) -> str:
        import hashlib
        return hashlib.sha256(f"{agent}|{question.strip()}".encode("utf-8")).hexdigest()[:32]

    def cache_get(self, question: str, agent: str) -> Optional[Dict[str, Any]]:
        import time as _time
        item = self._cache.get(self._cache_key(question, agent))
        if not item:
            return None
        if _time.time() - item["ts"] > int(getattr(rules, "CHAT_ANSWER_CACHE_TTL", 600)):
            self._cache.pop(self._cache_key(question, agent), None)
            return None
        item["hit"] = int(item.get("hit", 0)) + 1
        return item

    def cache_put(self, question: str, agent: str, text: str, docs: List[Dict]) -> None:
        import time as _time
        if not text or not docs:
            return                    # 只缓存"知识库有依据"的完整答案，不缓存兜底/拒答话术
        max_items = int(getattr(rules, "CHAT_ANSWER_CACHE_MAX", 200))
        if len(self._cache) >= max_items:
            oldest = min(self._cache, key=lambda k: self._cache[k]["ts"])
            self._cache.pop(oldest, None)
        self._cache[self._cache_key(question, agent)] = {
            "text": text, "docs": docs, "ts": _time.time(),
        }

    def cache_clear(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ #
    def _ensure_components(self) -> bool:
        """懒加载嵌入/向量集合/LLM。失败仅记日志并返回 False，不抛异常。"""
        if self._ready:
            return True
        self._init_attempted = True
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
            # timeout：向量库不可达时快速失败，避免长重试把调用方拖住
            pymilvus.connections.connect(
                alias=settings.MILVUS_DEFAULT_ALIAS,
                host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
                db_name=settings.MILVUS_DEFAULT_DB, timeout=5,
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
    def prewarm(self) -> bool:
        """预热智能体组件：建连 + 加载嵌入模型 + 跑一次空转嵌入。

        为什么要单独抽出来（原逻辑内联在 main.py 的启动钩子里）：

        `_ensure_components()` 只建客户端对象，**第一次 `embed_query` 才是真正的开销所在**——
        Ollama 要把 bge-m3 载入显存、Milvus 要完成 segment 加载，实测首问冷启动约 2.4s，
        而稳态同一次检索只要 0.055s（约 40 倍差距，用例 IT-C-01/02 实测）。
        所以"预热"必须包含一次真实嵌入，否则等于没预热。

        抽成服务方法后，生产启动钩子与用例走的是**同一条**预热路径，
        不会出现"生产热了、用例测的是冷的"这种口径分叉。
        """
        try:
            if not self._ensure_components():
                return False
            if self._embeddings is not None:
                self._embeddings.embed_query("预热")
            logger.info("智能体组件预热完成（集合=%s）", settings.MILVUS_DEFAULT_COLLECTION)
            return True
        except Exception as e:                      # noqa: BLE001
            logger.warning(f"智能体组件预热失败（不影响服务，首问将承担冷启动）：{e}")
            return False

    def warm_state(self) -> str:
        """供就绪探针如实报告预热状态；**只读标志位，不触发任何加载**。

        三种取值，区别"还没热"与"热不起来"——运维看这两种要采取的动作完全不同：
          · `warm`        组件就绪且已跑过真实嵌入，首问即稳态；
          · `cold`        尚未预热（进程刚起、预热线程还在跑），首问要承担 2~3s 冷启动；
          · `unavailable` 预热尝试过并失败（Milvus/Ollama 不可达），问答会走降级路径。

        如果在探针里调用 `_ensure_components()`，`/ready` 自己就会阻塞 2~3 秒，
        把"探针"变成"压测目标"——那才是真正把可用性探针做坏了。
        """
        if self._ready:
            return "warm"
        return "unavailable" if self._init_attempted else "cold"

    # ------------------------------------------------------------------ #
    async def complete(self, system_text: str, user_text: str) -> Optional[str]:
        """一次性生成（非流式）：给"需要拿到完整文本再加工"的调用方用。

        目前唯一调用方是营养师的「AI 咨询辅助建议」——它要的是**一段可编辑的草稿**，
        而不是逐字上屏的流式输出。

        返回 `None` 表示**不可用**（组件未就绪 / 模型调用失败）。
        刻意不返回空串：调用方必须据此走如实降级（"模型暂不可用"），
        否则营养师会看到一段空白，还以为模型就是这么写的。
        """
        if not await asyncio.to_thread(self._ensure_components) or self._llm is None:
            return None
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            resp = await self._llm.ainvoke([SystemMessage(content=system_text),
                                            HumanMessage(content=user_text)])
            text = resp.content if hasattr(resp, "content") else str(resp)
            text = (text or "").strip()
            return text or None
        except Exception as e:                            # noqa: BLE001
            logger.error("一次性生成失败：%s", e)
            return None

    def retrieve(self, question: str, top_k: int = 4) -> List[Dict]:
        """相似检索 + 阈值过滤，返回 [{doc_id, title, file_name, fragment, source, score}]。

        COSINE 相似度低于 settings.KB_MIN_SIMILARITY 的片段不返回——
        与其引用不相关内容误导模型，不如如实说"暂无相关内容"。

        `doc_id` 取 Milvus 记录主键 `pk`：没有它，前端只能展示一段无出处的文字，
        用户与审核方都无法回溯"这句话出自哪一条知识"（用例 IT-R-03 / A7-4 要求可溯源）。
        """
        if not self._ensure_components():
            return []
        threshold = float(getattr(settings, "KB_MIN_SIMILARITY", 0.55))
        try:
            vec = self._embeddings.embed_query(question)
            res = self._collection.search(
                [vec], anns_field="vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k, output_fields=["pk", "text", "file_name", "source", "category"],
            )
            out = []
            for hits in res:
                for h in hits:
                    if h.score < threshold:
                        continue
                    ent = h.entity
                    pk = ent.get("pk")
                    out.append({
                        "doc_id": str(pk) if pk is not None else None,
                        "title": ent.get("file_name") or "未知来源",
                        "file_name": ent.get("file_name") or "",
                        "category": ent.get("category") or "",
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
                   history: Optional[List[Dict[str, Any]]] = None,
                   memory_context: str = "") -> AsyncGenerator[Dict, None]:
        """生成 SSE 事件字典流。事件类型：start / delta / reference / done / error"""
        # 计时起点放在最前面：闲聊/拒答/缓存/降级/无引用五条短路分支
        # 也必须如实报耗时，否则监控只会看到生成路径的数字。
        t_enter = time.perf_counter()

        def _done_ms() -> int:
            return int((time.perf_counter() - t_enter) * 1000)

        # 1) 闲聊分流：不检索、不附引用，直接友好回应
        kind = _smalltalk_kind(question)
        if kind:
            yield {"event": "start", "data": {"message_id": None}}
            yield {"event": "delta", "data": {"text": _SMALLTALK_REPLY[kind]}}
            yield {"event": "done", "data": {"elapsed_ms": _done_ms(), "smalltalk": True}}
            return

        # 2) 合规拒答：命中策略绝不调用模型
        refused, reason = check_refusal(question)
        if refused:
            yield {"event": "start", "data": {"message_id": None}}
            yield {"event": "delta", "data": {"text": refusal_message(reason)}}
            yield {"event": "done", "data": {"elapsed_ms": _done_ms(), "refused": True,
                                             "reason": reason}}
            return

        # 3) 高频问题缓存命中：不检索、不调模型，直接回放（IT-C-03）
        cached = self.cache_get(question, agent)
        if cached:
            yield {"event": "start", "data": {"message_id": None, "cached": True}}
            yield {"event": "reference", "data": {"docs": cached["docs"]}}
            yield {"event": "delta", "data": {"text": cached["text"]}}
            yield {"event": "done", "data": {"elapsed_ms": _done_ms(), "cached": True,
                                             "disclaimer": _DISCLAIMER}}
            return

        # 4) 检索（带阈值）：追问场景用"上一轮问题 + 本轮"作为检索词
        #    检索是同步阻塞调用（嵌入 + 向量库 RPC），必须挪出事件循环——
        #    否则向量库一慢，整个进程所有请求（含健康检查）都会被拖死。
        _t0 = time.perf_counter()
        docs = await asyncio.to_thread(self.retrieve, retrieval_query(question, history))
        retrieve_ms = int((time.perf_counter() - _t0) * 1000)
        # 从进入 stream 起算，供首字节口径使用（不含路由/票据等更外层开销）
        t_start = time.perf_counter()

        # 5) 并发准入（IT-C-15）：把当前负载如实回传前端。
        #    超容量**不拒绝、不丢弃**——照常执行，只是提前告诉用户"人多、稍慢"，
        #    免得用户以为卡死而反复重发（重发才是真正的雪崩来源）。
        load = self.enter()
        try:
            yield {"event": "start", "data": {"message_id": None, "load": load}}
            # 引用一并带上 doc_id / source：前端可据此展示"出自哪份资料的第几段"，
            # 也让用例 A7-4「来源可溯源」在问答链路上同样成立。
            references = [{"doc_id": d.get("doc_id"), "title": d["title"],
                           "fragment": d["fragment"], "source": d.get("source") or ""}
                          for d in docs]
            if references:
                yield {"event": "reference", "data": {"docs": references}}

            # 6) 无 LLM → 降级返回检索片段
            if not await asyncio.to_thread(self._ensure_components) or self._llm is None:
                fallback = ("（模型服务未配置，以下为知识库检索片段）\n" + "\n".join(
                    f"- {d['title']}：{d['fragment']}" for d in docs)
                    if docs else "暂时无法检索到相关内容，请稍后再试或联系营养师。")
                yield {"event": "delta", "data": {"text": fallback}}
                yield {"event": "done", "data": {"elapsed_ms": _done_ms(), "degraded": True}}
                return

            # 7) 知识库无相关内容 → 如实回答，不编造（合规红线）
            if not docs:
                yield {"event": "delta", "data": {"text":
                    "这个问题超出了我当前知识库（药食同源食材、慢病饮食原则、特殊人群饮食）的范围，"
                    "我先不随意回答，以免误导您。\n"
                    "您可以换个问法（如：某种食材的功效、高血压/糖尿病怎么吃），"
                    "或到「健康风险测评」完成测评后获得个性化食养参考。"
                    + _DISCLAIMER}}
                yield {"event": "done", "data": {"elapsed_ms": _done_ms(), "no_reference": True}}
                return

            # 8) 正常流式生成（系统/用户双消息，避免模型复读提示词模板）
            try:
                from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
                context = "\n".join(f"【{d['title']}】\n{d['fragment']}" for d in docs)
                system_text = DIETITIAN_SYSTEM_PROMPT
                dialog = build_dialog_messages(
                    system_text, context, question, history,
                    memory_context=memory_context,
                )
                msgs = []
                for m in dialog:
                    if m["role"] == "system":
                        msgs.append(SystemMessage(content=m["content"]))
                    elif m["role"] == "assistant":
                        msgs.append(AIMessage(content=m["content"]))
                    else:
                        msgs.append(HumanMessage(content=m["content"]))
                answer: List[str] = []
                first_ms: Optional[int] = None
                async for chunk in self._llm.astream(msgs):
                    text = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if text:
                        if first_ms is None:
                            first_ms = int((time.perf_counter() - t_start) * 1000)
                        answer.append(text)
                        yield {"event": "delta", "data": {"text": text}}
                # 仅"知识库有依据且生成完整"的答案进缓存；拒答/兜底话术不入缓存
                self.cache_put(question, agent, "".join(answer).strip(), references)
                # elapsed_ms / breakdown 如实回传：检索、首 token、总耗时各是多少。
                # 之前这里恒为 0，前端与运维看到的是"0 毫秒完成"——那不是测量，是占位。
                yield {"event": "done", "data": {
                    "elapsed_ms": int((time.perf_counter() - t_start) * 1000),
                    "breakdown": {"retrieve_ms": retrieve_ms, "first_token_ms": first_ms,
                                  "total_ms": int((time.perf_counter() - t_start) * 1000)},
                }}
            except Exception as e:
                logger.error(f"生成失败：{e}")
                yield {"event": "error", "data": {"code": 60001, "message": "服务繁忙，请稍后重试"}}
        finally:
            # 无论正常结束、异常还是客户端断开，都要归还并发额度
            self.exit()


chat_query_service = ChatQueryService()
