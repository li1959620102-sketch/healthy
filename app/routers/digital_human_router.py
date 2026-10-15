# -*- coding: utf-8 -*-
"""数字人助手「小智」路由（原型：首页数字人卡片 + 语音对话模态框）。

一期为「留桩 + 交互」：**文本对话复用 `/api/v1/chat/query`**（已可用），
语音识别（ASR）、语音合成（TTS）、形象驱动返回 `enabled=false` + 明确说明，
待 **S5 Spike** 达标后打开开关即可，前端契约不变。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.core.responses import success

router = APIRouter(prefix="/digital-human", tags=["数字人助手"])

AVATAR_NAME = "小智"

# 能力开关：语音与形象驱动待 S5 达标
CAPABILITIES = {
    "text_chat": True,
    "asr": False,
    "tts": False,
    "avatar": False,
}

PENDING_NOTE = "语音识别 / 语音合成 / 形象驱动待 S5 Spike 达标后开启；当前可用文本对话"


class VoiceCommandReq(BaseModel):
    text: Optional[str] = Field(None, description="若已在前端完成识别的文本（留桩期可传）")
    audio_ref: Optional[str] = Field(None, description="音频对象存储引用（S5 接入后启用）")


@router.get("/config", summary="数字人能力配置", description="返回可用能力与文本对话入口，供前端决定显示哪些交互（原型数字人卡片）。")
async def config(user: dict = Depends(get_current_user)):
    return success(data={
        "name": AVATAR_NAME,
        "online": True,
        "capabilities": CAPABILITIES,
        "chat_endpoint": "/api/v1/chat/query",
        "greeting": "您好，我是小智。我可以陪您完成健康测评、解读报告，也可以把问题转给营养师智能体。",
        "note": PENDING_NOTE,
    })


@router.post("/session", summary="开启数字人会话", description="创建一次数字人会话上下文；语音能力未开启时以降级提示返回。")
async def open_session(user: dict = Depends(get_current_user)):
    return success(data={
        "session": {
            "name": AVATAR_NAME,
            "voice_enabled": False,
            "capabilities": CAPABILITIES,
            "greeting": "您好，我是小智。您可以直接说：帮我做一个健康风险测评。",
            "suggestions": ["帮我做一个健康风险测评", "解读我的最近一次报告", "今天该吃点什么"],
        },
        "note": PENDING_NOTE,
    })


@router.post("/voice-command", summary="语音指令（留桩）", description="ASR 未接入：若前端已自行完成识别可传 text；否则明确返回未启用。")
async def voice_command(body: VoiceCommandReq, user: dict = Depends(get_current_user)):
    text = (body.text or "").strip()
    if not text:
        return success(data={"enabled": False, "recognized_text": None,
                             "note": "语音识别待 S5 接入；当前请使用文本对话（/api/v1/chat/query）"})
    # 前端已识别出文本：透传给对话入口，保持交互闭环
    return success(data={"enabled": True, "recognized_text": text,
                         "forward_to": "/api/v1/chat/query",
                         "note": "已识别文本，请调用对话接口获取回答"})
