# -*- coding: utf-8 -*-
"""语音合成（TTS）：把智能体的回答念出来。

## 为什么用 edge-tts 而不是本地模型

数字人播报对音质的要求是"听得清、不像机器"，对**离线**的要求没有 ASR 那么硬——
用户说的话必须本地识别（涉及隐私，且不能有网络延迟），而播报内容是系统自己生成的回答，
过一遍在线合成不引入新的隐私面（发出去的是"本周打卡完成 2/7 天"这种非敏感文本）。

代价是**必须有外网**。所以这里做成"能力可探测"：`/config` 里 tts 为真才显示播报按钮，
合成失败时返回明确原因而不是静默给一段空音频——空音频在页面上表现为"点了没反应"，
用户只会以为坏了。

## 为什么限制长度

edge-tts 是流式的，长文本合成时间线性增长。回答超过 `TTS_MAX_CHARS` 时**截断并返回
`truncated=true`**，而不是排队合成三分钟——数字人播报的定位是"一句话把结论说出来"，
全文应该让用户看，不是听。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core import rules

logger = logging.getLogger("TTSService")

# 中文常用音色（edge-tts 的 voice name）
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class TTSUnavailable(RuntimeError):
    """依赖缺失或合成失败。"""


class TTSService:
    """无状态服务：每次合成都是一次独立请求，不缓存（回答内容几乎不重复）。"""

    @staticmethod
    def status() -> Dict[str, Any]:
        """只读探测，不发起网络请求。"""
        try:
            import edge_tts  # noqa: F401
            installed = True
            error = ""
        except Exception as e:                             # noqa: BLE001
            installed = False
            error = f"{type(e).__name__}: {e}"
        return {
            "installed": installed,
            "ready": installed,      # 在线合成，装包即可用；真正能否出声要一次实际调用
            "engine": "edge-tts",
            "voice": DEFAULT_VOICE,
            "max_chars": rules.TTS_MAX_CHARS,
            "error": error,
        }

    @staticmethod
    async def synthesize(text: str, voice: Optional[str] = None,
                         rate: str = "+0%") -> Dict[str, Any]:
        """合成语音，返回 {audio: bytes, truncated: bool, chars: int, voice: str}。

        失败抛 `TTSUnavailable`，由路由层转成统一错误响应。
        """
        raw = (text or "").strip()
        if not raw:
            raise TTSUnavailable("合成文本为空")
        truncated = len(raw) > rules.TTS_MAX_CHARS
        content = raw[: rules.TTS_MAX_CHARS] if truncated else raw

        try:
            import edge_tts
        except Exception as e:                             # noqa: BLE001
            raise TTSUnavailable(f"edge-tts 未安装：{e}") from e

        buf = bytearray()
        try:
            communicate = edge_tts.Communicate(content, voice or DEFAULT_VOICE, rate=rate)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf.extend(chunk["data"])
        except Exception as e:                             # noqa: BLE001
            # 网络不通是最常见的原因：说清楚，别让前端以为"点了没反应"
            raise TTSUnavailable(f"语音合成失败（需要外网访问 edge-tts）：{e}") from e

        if not buf:
            raise TTSUnavailable("语音合成返回空音频")
        return {
            "audio": bytes(buf), "truncated": truncated,
            "chars": len(content), "voice": voice or DEFAULT_VOICE,
            "content_type": "audio/mpeg",
        }


tts_service = TTSService()
