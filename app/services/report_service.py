# -*- coding: utf-8 -*-
"""F 组 · 报告服务（结构化评分 + LLM 分析文本流式生成 + 降级）。

分工（技术红线）：
    **评分由规则引擎算**（scoring_engine），本服务只让 LLM 写"分析文本"，
    且生成失败时评分与等级依然保留 —— 降级为基础版报告，用户无需重做测评（需求 A8）。

降级语义（设计评审 B2）：
    降级**不是错误**，走 code=0 + data.state="basic"，禁止返回 50002，
    否则会触发前端统一错误拦截器误弹窗。

重试：最多 3 次，退避 2/4/8 秒；全部失败后 state=basic，前端可再次请求重新生成。
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.assessment_dao import AnnotationDAO, RecordDAO, ReportDAO, TemplateDAO, session_scope
from app.models.mysql.platform import DietitianCustomer
from app.utils.db import get_session

logger = logging.getLogger("ReportService")

DISCLAIMER = "本内容由 AI 生成，不构成诊疗建议"


def _questions_of(template: Any) -> list:
    qc = getattr(template, "question_config", None)
    if isinstance(qc, dict):
        return qc.get("questions") or []
    return qc or []


class ReportService:
    MAX_RETRY = 3
    BACKOFF_SECONDS = [2, 4, 8]

    # ------------------------------------------------------------------ #
    # LLM（懒加载：导入阶段不连任何外部服务）
    # ------------------------------------------------------------------ #
    def __init__(self):
        self._llm = None
        self._llm_ready = False

    def _ensure_llm(self):
        if self._llm_ready:
            return self._llm
        try:
            from app.config import settings
            if settings.LLM_STRATEGY == "silicon_flow":
                from langchain_openai import ChatOpenAI
                self._llm = ChatOpenAI(model=settings.SILICON_FLOW_LLM_MODEL,
                                       openai_api_key=settings.SILICON_FLOW_API_KEY,
                                       openai_api_base=settings.SILICON_FLOW_BASE_URL,
                                       temperature=0.3, streaming=True)
            else:
                # 用 chat 接口（非 completion）：qwen3 的 <arg_key:6124c78e> 需要 chat 模板才生效，
                # 可跳过 thinking token 输出，显著降低生成时延（S1 实测结论）。
                from langchain_ollama import ChatOllama
                self._llm = ChatOllama(model=settings.LLM_MODEL,
                                       base_url=settings.OLLAMA_BASE_URL,
                                       temperature=0.3)
            self._llm_ready = True
        except Exception as e:
            logger.warning(f"报告生成 LLM 初始化失败（将走降级）：{e}")
            self._llm = None
            self._llm_ready = False
        return self._llm

    # ------------------------------------------------------------------ #
    # 提示词
    # ------------------------------------------------------------------ #
    @staticmethod
    def build_prompt(dimensions: List[Dict], suggestions: List[Dict],
                     score: float, level_name: str, references: List[Dict]) -> str:
        dim_text = "\n".join(
            f"- {d.get('name')}：{d.get('score')} 分（{d.get('level_name') or d.get('level')}），权重 {d.get('weight')}"
            for d in dimensions
        )
        sug_text = "\n".join(f"- {s.get('item')}：{s.get('note')}" for s in suggestions)
        ref_text = "\n".join(f"[{r.get('title')}] {r.get('fragment')}" for r in references) or "（无参考片段）"
        return (
            "你是 AI 智参（药食同源）平台的营养测评分析助手。\n"
            "严格依据下面已经计算完成的评分结果撰写分析文本，**不得修改任何分数**，"
            "不得给出用药剂量、诊断结论，不得编造检查指标。\n\n"
            f"总分：{score}（{level_name}）\n各维度得分：\n{dim_text}\n\n"
            f"建议要点：\n{sug_text}\n\n"
            f"可参考的知识库片段：\n{ref_text}\n\n"
            "请输出 3 段：①总体评价 ②薄弱维度解析 ③可执行的生活与饮食建议（具体、可量化）。"
            "语言通俗，避免专业术语堆砌。总字数控制在 400 字以内。<arg_key:6124c78e>"
        )

    # ------------------------------------------------------------------ #
    # 流式生成
    # ------------------------------------------------------------------ #
    async def stream(self, record_id: int) -> AsyncGenerator[Dict[str, Any], None]:
        """产出 SSE 事件：start / reference / delta / done。"""
        started = time.time()
        with session_scope() as s:
            rec = RecordDAO.get(record_id, session=s)
            if not rec:
                yield {"event": "error", "data": {"code": ErrCode.ASSESSMENT_NOT_FOUND, "message": "测评记录不存在"}}
                return
            rep = ReportDAO.get_by_record(rec.id, session=s)
            if not rep:
                yield {"event": "error", "data": {"code": ErrCode.REPORT_NOT_FOUND, "message": "报告不存在，请先提交测评"}}
                return

            tpl = TemplateDAO.get(rec.template_id, session=s)
            report_id = rep.id
            dims = rep.dimension_scores or []
            sugg = rep.suggestions or []
            score = float(rep.score) if rep.score is not None else 0.0
            level_name = {"excellent": "优秀", "good": "良好",
                          "needs_improvement": "待改善"}.get(rep.level, rep.level or "")

            yield {"event": "start", "data": {
                "report_id": report_id,
                "assessment_id": rec.id,
                "score": score,
                "level": rep.level,
                "state": rep.state,
            }}

            # 已完成：直接回放，不重复调用模型
            if rep.state == "complete" and rep.analysis_text:
                yield {"event": "delta", "data": {"text": rep.analysis_text}}
                yield {"event": "done", "data": {
                    "state": "complete", "elapsed_ms": int((time.time() - started) * 1000),
                    "disclaimer": DISCLAIMER, "replayed": True,
                }}
                return

            # 检索参考片段（复用 G 组检索能力，失败不影响生成）
            references: List[Dict] = []
            try:
                from app.services.chat_query_service import chat_query_service
                query = " ".join([f"{d.get('name')}得分{d.get('score')}" for d in dims]) or "营养测评报告"
                # 走线程池：检索含嵌入计算，同步阻塞会卡住事件循环影响并发
                references = await asyncio.to_thread(chat_query_service.retrieve, query, 3) or []
                # 截断片段：S1 实测 prompt 越长首字节越慢，引用只保留要点
                for d in references:
                    if d.get("fragment"):
                        d["fragment"] = d["fragment"][:120]
            except Exception as e:
                logger.warning(f"报告引用检索失败（忽略）：{e}")
            if references:
                yield {"event": "reference", "data": {"docs": references}}

            prompt = self.build_prompt(dims, sugg, score, level_name, references)
            state = {"ok": False, "emitted": False, "reason": ""}
            buf: List[str] = []
            async for piece in self._stream_generate(prompt, state):
                buf.append(piece)
                yield {"event": "delta", "data": {"text": piece}}
            text = "".join(buf).strip()
            elapsed = int((time.time() - started) * 1000)

            if state["ok"]:
                ReportDAO.mark_complete(report_id, text, references, elapsed,
                                        model_name=self._model_name(), session=s)
                yield {"event": "done", "data": {
                    "state": "complete", "elapsed_ms": elapsed, "disclaimer": DISCLAIMER,
                }}
            else:
                # 部分成功时也保留已生成内容，避免用户白等（需求 A8）
                ReportDAO.mark_basic(report_id, retry_count=self.MAX_RETRY,
                                     partial_text=text or None, session=s)
                yield {"event": "done", "data": {
                    "state": "basic", "elapsed_ms": elapsed, "disclaimer": DISCLAIMER,
                    "degraded": True, "can_retry": True,
                    "message": "详细分析生成未完成，已提供基础版报告（评分与等级不受影响）",
                }}

    async def _stream_generate(self, prompt: str, state: dict):
        """真流式生成：边收边产出（TTFB = 模型首 token 时间）。

        重试策略（关键）：一旦已向客户端吐出内容就不再重试，
        否则用户会看到重复文本；此时按"部分成功"处理，状态降级为 basic 但保留已生成内容。
        """
        llm = self._ensure_llm()
        if llm is None:
            state["ok"] = False
            state["reason"] = "模型服务未配置"
            yield self._fallback_text(state["reason"])
            return

        last_err: Optional[Exception] = None
        for attempt in range(self.MAX_RETRY):
            try:
                got = False
                async for chunk in llm.astream(prompt):
                    piece = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if piece:
                        got = True
                        state["emitted"] = True
                        yield piece
                if got:
                    state["ok"] = True
                    return
                last_err = RuntimeError("模型返回空内容")
            except Exception as e:
                last_err = e
                logger.warning(f"报告生成第 {attempt + 1} 次失败：{e}")

            if state["emitted"]:
                # 已吐出内容：不再重试，按部分成功处理
                state["ok"] = False
                state["reason"] = f"生成中断：{last_err}"
                return
            if attempt < self.MAX_RETRY - 1:
                await asyncio.sleep(self.BACKOFF_SECONDS[attempt])

        state["ok"] = False
        state["reason"] = str(last_err) if last_err else "未知原因"
        logger.error(f"报告生成重试 {self.MAX_RETRY} 次仍失败，降级：{state['reason']}")
        yield self._fallback_text(state["reason"])

    @staticmethod
    def _fallback_text(reason: str = "") -> str:
        return (
            "详细分析暂时无法生成，以下为基础版报告。\n"
            "您的评分与各维度得分已计算完成，详见报告页结构化数据；"
            "可参考建议要点调整饮食与作息，或联系营养师获取人工解读。\n"
            f"（原因：{reason or '服务繁忙'}，可稍后点击重新生成）"
        )

    @staticmethod
    def _chunks(text: str, size: int = 60):
        for i in range(0, len(text), size):
            yield text[i:i + size]

    def _model_name(self) -> Optional[str]:
        try:
            from app.config import settings
            return (settings.SILICON_FLOW_LLM_MODEL if settings.LLM_STRATEGY == "silicon_flow"
                    else settings.LLM_MODEL)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # 详情 / 批注
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_bound_dietitian(dietitian_id: str, customer_id: Optional[str]) -> bool:
        if not customer_id:
            return False
        session = get_session()
        try:
            row = (session.query(DietitianCustomer)
                   .filter_by(dietitian_id=str(dietitian_id), customer_id=str(customer_id),
                              status="active").first())
            return row is not None
        except Exception as e:
            logger.warning(f"绑定关系查询失败：{e}")
            return False
        finally:
            session.close()

    @classmethod
    def detail(cls, report_id: int, user: Optional[dict],
               guest_hash: Optional[str] = None) -> Dict[str, Any]:
        """报告详情：本人 / 绑定营养师 / 管理员可见；其余按 404 处理（防枚举）。"""
        with session_scope() as s:
            rep = ReportDAO.get(report_id, session=s)
            if not rep:
                raise BizError(ErrCode.REPORT_NOT_FOUND, "报告不存在")
            rec = RecordDAO.get(rep.record_id, session=s)

            owner_id = rec.user_id if rec else rep.user_id
            allowed = False
            if user:
                role = user.get("role")
                if role == "admin" or (owner_id and str(owner_id) == str(user.get("sub"))):
                    allowed = True
                elif role in ("nutritionist", "dietitian") and cls._is_bound_dietitian(
                        user.get("sub"), owner_id):
                    allowed = True
            if not allowed and rec and guest_hash and rec.guest_token == guest_hash:
                allowed = True
            if not allowed:
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "报告不存在")

            anns = AnnotationDAO.list_by_report(report_id, session=s)
            return {
                "report_id": rep.id,
                "assessment_id": rep.record_id,
                "state": rep.state,
                "score": float(rep.score) if rep.score is not None else None,
                "standardized_score": float(rep.standardized_score) if rep.standardized_score is not None else None,
                "level": rep.level,
                "dimension_scores": rep.dimension_scores or [],
                "suggestions": rep.suggestions or [],
                "analysis_text": rep.analysis_text,
                "references": rep.references or [],
                "annotations": [
                    {"dietitian_id": a.dietitian_id, "content": a.content, "created_at": a.created_at}
                    for a in anns
                ],
                "disclaimer": rep.disclaimer or DISCLAIMER,
                "model_name": rep.model_name,
                "generated_at": rep.generated_at,
                "elapsed_ms": rep.elapsed_ms,
                "degraded": rep.state == "basic",
                "can_retry": rep.state == "basic",
            }

    @staticmethod
    def annotate(report_id: int, dietitian_id: str, content: str) -> Dict[str, Any]:
        """营养师批注（与 AI 原文并存，形成双版本）。"""
        if not content or not content.strip():
            raise BizError(ErrCode.PARAM_INVALID, "批注内容不能为空")
        with session_scope() as s:
            rep = ReportDAO.get(report_id, session=s)
            if not rep:
                raise BizError(ErrCode.REPORT_NOT_FOUND, "报告不存在")
            if not ReportService._is_bound_dietitian(dietitian_id, rep.user_id):
                raise BizError(ErrCode.PERMISSION_DENIED, "只能批注自己客户的报告")
            ann = AnnotationDAO.upsert(report_id, str(dietitian_id), content.strip(), session=s)
            return {"report_id": report_id, "dietitian_id": ann.dietitian_id,
                    "content": ann.content, "updated_at": ann.updated_at}


report_service = ReportService()
