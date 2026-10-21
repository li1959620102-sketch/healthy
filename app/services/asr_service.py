# -*- coding: utf-8 -*-
"""S 组 · 语音识别服务（faster-whisper 本地推理）。

## 为什么用 faster-whisper 而不是调云端 ASR

健康咨询的语音里会出现"血糖""肌酐""孕周"这类信息。走云端意味着音频要出网，
这个项目从第一天起就把"数据不出内网"当基线，所以语音只能在本地做。

faster-whisper 是 Whisper 的 CTranslate2 重实现，同样的权重在 CPU 上比
openai-whisper 快数倍，且支持 `int8` 量化——本机没有 GPU，`int8` 是唯一
能把 `small` 模型跑到可交互速度的配置。

## 三个工程要点

1. **模型只加载一次，且在启动时就加载**。加载 small 权重实测 2.2 秒，
   且吃 500MB 内存；每个请求都 new 一个 WhisperModel 会在并发时直接把内存打满。
   这里用双检锁单例，并在 lifespan 里起后台线程调 `warmup()` 预热——
   单例只解决"重复加载"，不解决"第一次由用户承担加载"，两者要一起做。

2. **必须放到线程里跑**。`model.transcribe()` 是纯同步阻塞调用，
   在 async 端点里直接调会冻住整个事件循环（这个坑本项目踩过一次，
   表现是全站假死而不是单个接口变慢）。调用方一律 `asyncio.to_thread`。

3. **失败要能区分**。模型没装、权重没下、音频解不了，是三种完全不同的
   故障，给用户的提示也不同。不能统一报"语音识别失败"——那样运维无从下手。

## VAD

开 `vad_filter=True`：用户按麦之后往往有 0.5~1 秒静音，不裁掉会被 Whisper
转成"嗯""谢谢观看"这类幻觉文本（Whisper 在纯静音上极爱编造，
这是它的已知缺陷，不是我们的 bug）。
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger("ASRService")

# 音频容器白名单：前端 MediaRecorder 默认 webm/opus，iOS Safari 给 mp4/m4a
ALLOWED_EXT = (".wav", ".mp3", ".m4a", ".webm", ".ogg", ".oga", ".mp4", ".aac", ".flac")
MAX_BYTES = 20 * 1024 * 1024        # 20MB，约 10 分钟 webm/opus


class ASRUnavailable(RuntimeError):
    """ASR 依赖缺失或模型缺失。与「识别失败」区分开，便于给出准确提示。"""


def probe_duration(path: str) -> Optional[float]:
    """读音频总时长（秒）；读不出来返回 None。

    为什么要在转写**之前**探时长：转写是按音频长度线性耗时的，
    一条 10 分钟的录音在 CPU 上要跑一两分钟，把请求挂在那里很不友好。
    先用容器头拿到时长，超限立刻拒绝，用户马上得到"请分段"的明确提示。

    容器头里没有 duration 的情况（部分流式 webm）返回 None，
    此时放行——宁可让它转写，也不要因为探测失败把正常请求挡掉。
    """
    try:
        import av
    except ImportError:                          # pragma: no cover
        return None
    try:
        with av.open(path) as container:
            if container.duration is None:
                return None
            return float(container.duration) / float(av.time_base)
    except Exception as e:                       # noqa: BLE001
        logger.debug(f"音频时长探测失败（放行）：{e}")
        return None


class ASRService:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 模型加载
    # ------------------------------------------------------------------ #
    @property
    def model_dir(self) -> str:
        return settings.ASR_MODEL_DIR

    def resolve_model_ref(self) -> str:
        """决定最终传给 WhisperModel 的是什么。

        `ASR_MODEL="small"` 这种写法会让 faster-whisper 去 HuggingFace 拉权重，
        而本机到 huggingface.co 直连不通。所以先看本地有没有下载好的目录：
        `{ASR_MODEL_DIR}/{ASR_MODEL}/model.bin` 存在就直接用绝对路径。

        这一步不能省——否则会出现"明明已经把 483MB 权重下到本地，
        一加载还是去联网并超时"这种最难排查的情况。
        """
        local = os.path.join(self.model_dir, settings.ASR_MODEL)
        if os.path.isdir(local) and os.path.exists(os.path.join(local, "model.bin")):
            return local
        return settings.ASR_MODEL

    def ensure_model(self):
        """双检锁懒加载。加载失败会把原因记在 `_load_error` 上并抛 ASRUnavailable。"""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:                      # pragma: no cover
                raise ASRUnavailable(
                    "faster-whisper 未安装，请先执行 pip install faster-whisper"
                ) from e

            os.makedirs(self.model_dir, exist_ok=True)
            t0 = time.perf_counter()
            ref = self.resolve_model_ref()
            try:
                self._model = WhisperModel(
                    ref,
                    device=settings.ASR_DEVICE,
                    compute_type=settings.ASR_COMPUTE_TYPE,
                    download_root=self.model_dir,
                    cpu_threads=settings.ASR_CPU_THREADS,
                )
            except Exception as e:                        # noqa: BLE001
                self._load_error = str(e)
                raise ASRUnavailable(f"语音模型加载失败：{e}") from e
            logger.info(
                f"ASR 模型已加载：{ref} "
                f"({settings.ASR_DEVICE}/{settings.ASR_COMPUTE_TYPE}) "
                f"{int((time.perf_counter() - t0) * 1000)}ms"
            )
            return self._model

    def warmup(self) -> bool:
        """启动预热：把权重提前读进内存，让第一个用户不吃加载开销。

        本机实测 small/int8 冷加载 2.2 秒，而一段 4 秒语音本身只要 3 秒左右——
        冷启动的首请求体感是稳态的两倍，用户会以为"卡住了"。
        预热后首请求与后续请求耗时一致。

        失败**不抛异常**：语音只是辅助入口，权重缺失不该拖垮整个服务启动。
        原因记在 `_load_error` 上，`/ready` 的 asr 段会如实报出来。
        """
        try:
            t0 = time.perf_counter()
            model = self.ensure_model()
            # 光加载权重还不够——实测首条真实音频仍比稳态多约 0.6 秒：
            # VAD 的 onnxruntime session 和 CTranslate2 的算子内核都是"首次调用"才初始化，
            # 不跑这一下，这笔开销照样落在第一个真实用户身上。
            # 用 1 秒静音做哑推理：走完整解码→VAD→编码器路径，但没有任何可识别内容，
            # 结果直接丢弃（静音段在 VAD 里会被裁掉，不会产生幻觉文本）。
            import numpy as np

            list(model.transcribe(
                np.zeros(16000, dtype=np.float32),
                language="zh",
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
            )[0])
            logger.info(f"ASR 预热完成：{int((time.perf_counter() - t0) * 1000)}ms")
            return True
        except Exception as e:                        # noqa: BLE001
            logger.warning(f"ASR 预热失败（不影响文本对话，语音入口不可用）：{e!r}")
            return False

    def status(self) -> Dict[str, Any]:
        """能力探针：给 /ready 与前端能力协商用，不触发加载。"""
        try:
            import faster_whisper  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        weights = []
        if os.path.isdir(self.model_dir):
            try:
                # 只认"目录里真有 model.bin"的，避免把半成品目录当成可用权重
                weights = [d for d in os.listdir(self.model_dir)
                           if os.path.exists(os.path.join(self.model_dir, d, "model.bin"))]
            except OSError:
                weights = []
        return {
            "installed": installed,
            "loaded": self._model is not None,
            "model": settings.ASR_MODEL,
            "resolved": self.resolve_model_ref(),
            "device": settings.ASR_DEVICE,
            "compute_type": settings.ASR_COMPUTE_TYPE,
            "model_dir": self.model_dir,
            "downloaded": weights,
            "load_error": self._load_error,
            "ready": installed and (self._model is not None or bool(weights)),
        }

    # ------------------------------------------------------------------ #
    # 转写
    # ------------------------------------------------------------------ #
    def transcribe_bytes(self, data: bytes, filename: str = "audio.webm",
                         language: Optional[str] = None) -> Dict[str, Any]:
        """同步转写。**调用方负责放进线程**（见模块 docstring 第 2 点）。"""
        name = (filename or "audio.webm").lower()
        ext = os.path.splitext(name)[1]
        if ext not in ALLOWED_EXT:
            raise ValueError(f"不支持的音频格式 {ext or '(无扩展名)'}，"
                             f"请上传 {'/'.join(x.lstrip('.') for x in ALLOWED_EXT)}")
        if not data:
            raise ValueError("音频内容为空")
        if len(data) > MAX_BYTES:
            raise ValueError(f"音频不能超过 {MAX_BYTES // (1024 * 1024)}MB")

        model = self.ensure_model()

        # faster-whisper 通过 PyAV 读文件，给它一个真实后缀的临时文件最省事，
        # 也避免把 webm 内存流直接塞进解码器时的容器探测问题。
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            # 时长预检：超限立刻拒绝，不把用户的请求挂在一条长录音上
            seconds = probe_duration(tmp_path)
            limit = settings.ASR_MAX_AUDIO_SECONDS
            if seconds is not None and seconds > limit:
                raise ValueError(
                    f"录音时长 {seconds:.0f} 秒，超过上限 {limit} 秒；请分段后逐段上传"
                )

            t0 = time.perf_counter()
            # 中文必须给 initial_prompt：Whisper 的中文训练数据繁体占比高，
            # 不给提示时实测会直接吐繁体（"高血壓的人平時吃什麼比較好"），
            # 内容对但对下游是灾难——拒答词表、意图关键词全是简体，匹配不上。
            initial_prompt = None
            if (language or "").lower().startswith("zh"):
                initial_prompt = "以下是普通话的句子，请用简体中文输出。"

            segments, info = model.transcribe(
                tmp_path,
                language=language,              # None = 自动检测；中文场景可传 "zh"
                beam_size=settings.ASR_BEAM_SIZE,
                vad_filter=True,                # 见 docstring「VAD」
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,   # 关掉可显著减少长静音后的复读
                initial_prompt=initial_prompt,
            )
            seg_list = []
            texts = []
            for seg in segments:               # 生成器，必须遍历才真正开始推理
                piece = (seg.text or "").strip()
                if not piece:
                    continue
                texts.append(piece)
                seg_list.append({
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": piece,
                })
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        text = "".join(texts).strip()
        duration = round(float(getattr(info, "duration", 0.0) or 0.0), 2)
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
            "duration": duration,
            "segments": seg_list,
            "model": settings.ASR_MODEL,
            "elapsed_ms": elapsed_ms,
            # 语速：中文正常对话约 3~5 字/秒。异常低往往意味着只识别到片段，
            # 交给调用方决定要不要提示"没听清，请再说一遍"。
            "chars_per_second": round(len(text) / duration, 2) if duration > 0 else None,
            "empty": not text,
        }


asr_service = ASRService()
