# -*- coding: utf-8 -*-
"""数字人助手「小智」路由（首页数字人卡片 + 语音对话模态框）。

能力现状：
  · 文本对话 —— `POST /api/v1/chat/query`（SSE 流式）；
  · 语音识别（ASR）—— `POST /api/v1/digital-human/asr`，本机 faster-whisper 推理；
  · 语音合成（TTS）—— `POST /api/v1/digital-human/tts`，edge-tts 在线合成，直接返回音频字节；
  · 形象驱动 —— 前端由 `assets/avatar.js` 做 Canvas 渲染与口型同步，不需要后端模型。

`/asr` 支持 `with_answer=true`：一次请求完成「音频 → 文本 → 主智能体编排 → 答案」，
这就是语音对话的完整闭环。前端拿到 `text` 后也可以自己再走 SSE，两者都支持。

**能力开关是动态的**：`/config` 里 `asr` 为真表示"依赖装好且模型已在本地"，
`tts` 为真表示"edge-tts 可用"，都不是硬编码常量。依赖没装时返回 false 并给出原因，
前端据此隐藏麦克风/播报按钮——比让用户点了没反应要好。
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.core.errors import BizError, ErrCode
from app.core.responses import success
from app.services.asr_service import ASRUnavailable, asr_service
from app.services.tts_service import TTSUnavailable, tts_service

logger = logging.getLogger("DigitalHumanRouter")

router = APIRouter(prefix="/digital-human", tags=["数字人助手"])

AVATAR_NAME = "小智"


def _capabilities() -> dict:
    """动态能力快照。ASR 取决于依赖与本地权重，TTS 取决于 edge-tts 是否可用。"""
    st = asr_service.status()
    tts = tts_service.status()
    return {
        "text_chat": True,
        "asr": bool(st.get("ready")),
        "asr_detail": st,
        "tts": bool(tts.get("ready")),
        "tts_detail": tts,
        "avatar": True,          # 前端 Canvas 渲染，无需后端推理
    }


class VoiceCommandReq(BaseModel):
    text: Optional[str] = Field(None, description="前端已完成识别的文本（如用了浏览器自带识别）")


class TTSReq(BaseModel):
    text: str = Field(..., description="要朗读的文本（超过 500 字会截断）")
    voice: Optional[str] = Field(None, description="音色，留空用默认 zh-CN-XiaoxiaoNeural")
    rate: str = Field("+0%", description="语速，如 +10% / -10%")


@router.get("/config", summary="数字人能力配置",
            description="返回可用能力、文本对话入口与 ASR/TTS 状态，供前端决定显示哪些交互。")
async def config(user: dict = Depends(get_current_user)):
    caps = _capabilities()
    return success(data={
        "name": AVATAR_NAME,
        "online": True,
        "capabilities": caps,
        "chat_endpoint": "/api/v1/chat/query",
        "asr_endpoint": "/api/v1/digital-human/asr",
        "tts_endpoint": "/api/v1/digital-human/tts",
        "avatar_asset": "/assets/avatar.js",
        "greeting": "您好，我是小智。我可以陪您完成健康测评、解读报告，也可以把问题转给营养师智能体。",
        "note": ("语音识别与播报均已就绪" if (caps["asr"] and caps["tts"])
                 else "；".join(x for x in (
                     None if caps["asr"] else "语音识别不可用：请安装 faster-whisper 并下载模型权重",
                     None if caps["tts"] else "语音播报不可用：需要安装 edge-tts 并可访问外网") if x)),
    })


@router.post("/session", summary="开启数字人会话", description="创建一次数字人会话上下文。")
async def open_session(user: dict = Depends(get_current_user)):
    caps = _capabilities()
    return success(data={
        "session": {
            "name": AVATAR_NAME,
            "voice_enabled": bool(caps["asr"]),
            "tts_enabled": bool(caps["tts"]),
            "capabilities": caps,
            "greeting": "您好，我是小智。您可以直接说：帮我做一个健康风险测评。",
            "suggestions": ["帮我做一个健康风险测评", "解读我的最近一次报告", "今天该吃点什么"],
        },
        "note": "" if caps["asr"] else "语音识别未就绪，当前请使用文本对话",
    })


@router.post("/asr", summary="语音识别与语音问答",
             description="上传录音（wav/mp3/m4a/webm/ogg，≤20MB）→ 本地 faster-whisper 转写；"
                         "with_answer=true 时直接走主智能体编排并返回答案，完成语音对话闭环。")
async def asr(file: UploadFile = File(..., description="录音文件"),
              language: Optional[str] = Form(None, description="语言代码，留空自动检测"),
              with_answer: bool = Form(False, description="是否直接把识别文本交给主智能体作答"),
              user: dict = Depends(get_current_user)):
    data = await file.read()
    try:
        result = await asyncio.to_thread(
            asr_service.transcribe_bytes, data, file.filename or "audio.webm", language
        )
    except ASRUnavailable as e:
        # 明确告诉前端"能力不可用"，而不是落到 90000「服务暂时不可用」——
        # 后者会让用户一直重试，而重试永远不会成功。
        raise BizError(ErrCode.ASR_UNAVAILABLE, str(e))

    if not result.get("text"):
        # 空结果也是结果：如实告诉用户"没听清"，不要假装识别成功
        return success(data={**result, "answer": None},
                       message="没有识别到语音内容，请靠近麦克风再说一次")

    if with_answer:
        from app.services import orchestrator_service

        try:
            orch = await orchestrator_service.orchestrate(result["text"], user)
            steps = orch.get("steps") or []
            ok_steps = [s for s in steps if s.get("ok")]
            agg_summary = (orch.get("result") or {}).get("summary", "")

            # 语音场景下单说"1 个子智能体全部正常返回"毫无意义——那是给运维看的，
            # 用户要的是内容。所以优先把**子智能体自己的 summary** 拼给用户，
            # 只有全都拿不出内容时才退回汇总话术。
            parts = [s.get("summary", "").strip() for s in ok_steps if s.get("summary")]
            answer_text = "\n\n".join(parts) if parts else agg_summary

            result["answer"] = {
                "summary": answer_text,
                "aggregate_summary": agg_summary,
                "steps": steps,
                "intent": (orch.get("intent") or {}).get("primary"),
                "references": (orch.get("result") or {}).get("references") or [],
                "elapsed_ms": orch.get("elapsed_ms"),
            }
        except Exception as e:                                  # noqa: BLE001
            # 转写成功但编排失败：文本照常返回，答案部分如实标失败
            logger.warning(f"语音问答编排失败：{e!r}")
            result["answer"] = {"summary": "", "error": f"{type(e).__name__}: {e}"}

    return success(data=result, message="识别完成")


@router.post("/tts", summary="语音合成（把回答念出来）",
             description="文本 → mp3 音频字节流。超过 500 字自动截断并返回 X-TTS-Truncated 头；"
                         "合成失败返回 JSON 错误（code=60008）而不是空音频。")
async def tts(body: TTSReq, user: dict = Depends(get_current_user)):
    """返回 `audio/mpeg` 二进制。

    这里**不用统一 JSON 信封**：音频没有地方塞信封。所以成功时直接回二进制，
    失败时才回 JSON 错误——前端按 `Content-Type` 分支处理。
    元信息放响应头（截断标记、字符数），避免为一个纯音频接口再造一套包体格式。
    """
    try:
        result = await tts_service.synthesize(body.text, body.voice, body.rate)
    except TTSUnavailable as e:
        raise BizError(ErrCode.TTS_UNAVAILABLE, str(e))

    return Response(
        content=result["audio"],
        media_type=result["content_type"],
        headers={
            "X-TTS-Voice": result["voice"],
            "X-TTS-Chars": str(result["chars"]),
            "X-TTS-Truncated": "true" if result["truncated"] else "false",
            "Cache-Control": "no-store",   # 回答内容不进任何缓存
        },
    )


@router.post("/voice-command", summary="语音指令（文本通道）",
             description="若前端已自行完成语音识别，可把文本传到这里测试调度；"
                         "真实录音请走 POST /digital-human/asr。")
async def voice_command(body: VoiceCommandReq, user: dict = Depends(get_current_user)):
    text = (body.text or "").strip()
    if not text:
        return success(data={"enabled": False, "recognized_text": None,
                             "note": "请上传录音到 POST /api/v1/digital-human/asr"})
    return success(data={"enabled": True, "recognized_text": text,
                         "forward_to": "/api/v1/chat/query",
                         "note": "已识别文本，请调用对话接口获取回答"})
