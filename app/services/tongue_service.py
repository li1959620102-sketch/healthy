# -*- coding: utf-8 -*-
"""O 组 · 舌象辨识服务（视觉模型 → 可观察特征）。

## 边界（这是整个模块最重要的部分）

舌象辨识在中医里是**四诊之望诊**的一部分，专业门槛极高（需结合问、闻、切）。
本服务刻意只做**第一层**：把照片里能直接看到的形态与颜色描述出来。

不做的事，一条都不做：
  · 不给证型（"脾虚湿盛"）、不给体质结论、不给调理建议；
  · 不做疾病判断（"可能有肝病"）；
  · 图片里没有舌头时**不猜测**——直接返回 visible=false。

理由不是保守，是负责：把「舌尖偏红」说成「心火旺」，用户会当真。
模型没有四诊合参的能力，任何超出观察的推论都是编造。

## 抗幻觉的三道闸

1. **提示词闸**：明确要求"看不清就填无法判断"，并给出枚举范围；
2. **枚举闸**：模型输出落到白名单外 → `rules.normalize_tongue_features`
   统一归一为「无法判断」，并把原始值记进 `corrections` 如实上报；
3. **质量闸**：`visible=false` 或置信度低于 `TONGUE_MIN_CONFIDENCE`
   → `quality.sufficient=false`，前端不得据此生成任何方案。

## 性能

qwen2.5vl:3b 在纯 CPU 上单张约数十秒（首次含模型加载）。因此：
  · 图片先压到长边 768 再送模型；
  · `keep_alive` 让模型常驻，避免每次冷启动；
  · 对外一律走**后台辨识**（status: uploaded → recognizing → recognized），
    同步接口只在评测脚本里用。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.core.rules import (
    TONGUE_FEATURE_ENUMS,
    TONGUE_IMAGE_MAX_SIDE,
    TONGUE_MIN_CONFIDENCE,
    normalize_tongue_features,
    reconcile_tongue_features,
)

logger = logging.getLogger("TongueService")

DISCLAIMER = ("以上仅为照片可见特征的客观描述，不构成中医诊断或体质判定；"
              "如需辨证请由执业中医师四诊合参。")


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """你是一个舌象照片的**观察记录员**，不是医生。

你的唯一任务是：如实描述这张照片里舌头的可见特征。你**禁止**给出任何诊断、
证型（如"脾虚""湿热"）、体质结论或调理建议——这些需要中医师四诊合参，
你不能也不应该提供。

严格遵守以下规则：
1. 如果照片里没有伸出的人舌（风景、文字、物品、闭口的人脸、模糊到无法辨认），
   必须返回 visible=false，features 里所有字段填"无法判断"。不要猜测，不要编造。
2. 如果舌头只露出一部分、被牙齿或嘴唇遮挡、或光线严重不足，
   visible 仍为 true，但要在 quality.issues 里逐条写明具体问题。
3. 每个特征只能从给定选项中选一个，禁止自创新的词。
4. 看不清的字段一律填"无法判断"，不要用"看起来差不多"的颜色代替。
5. 只输出 JSON 对象本身，不要 markdown 代码块，不要任何解释文字。"""


USER_PROMPT = """请观察这张舌象照片，按下面的 JSON 结构输出：

{
  "visible": true 或 false,
  "quality": {
    "sufficient": true 或 false,
    "issues": ["光线过暗", "舌头只露出舌尖", "..."] 或 []
  },
  "features": {
    "tongue_color": "淡红" | "淡白" | "红" | "深红" | "紫暗" | "青紫" | "无法判断",
    "coating_color": "白" | "黄" | "灰黑" | "无苔" | "无法判断",
    "coating_thickness": "薄" | "厚" | "无苔" | "无法判断",
    "moisture": "润" | "偏干" | "水滑" | "无法判断",
    "tongue_shape": "正常" | "胖大" | "瘦薄" | "无法判断",
    "teeth_marks": "有" | "无" | "无法判断",
    "cracks": "有" | "无" | "无法判断",
    "spots": "有" | "无" | "无法判断"
  },
  "observations": ["舌尖偏红", "舌面中部苔稍厚"],
  "confidence": 0.0 到 1.0 之间的数字
}

说明：
- observations 只写你**直接看到**的，2 到 4 条，每条不超过 15 个字；
- confidence 表示你对整体判断的把握，看不清就给低分（0.3 以下），不要虚高。"""


# --------------------------------------------------------------------------- #
# 图像预处理
# --------------------------------------------------------------------------- #
def probe_image(data: bytes) -> Dict[str, Any]:
    """送模型前的**确定性闸门**：这张图里到底有没有可能拍到舌体。

    为什么需要它：实测把一张纯白图喂给 qwen2.5vl:3b，它返回了 visible=true
    并编出「舌尖偏红」。纯靠提示词约束不住 3B 模型，所以在调用模型**之前**
    先用像素统计拦一次——这是代码判断，不是模型判断，结论稳定可复现。

    判据只有两条，都刻意保守：
      · red_ratio：舌体在 HSV 里落红/粉区间的像素占比；
      · contrast ：灰度标准差，用来排除纯色图、纯白图、纯黑图。

    阈值取「宁可放过、不可错杀」：真实舌照即使光线差也能过 2%，
    而纯色/风景/文字图通常低于 0.5%。
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:                      # pragma: no cover
        return {"available": False, "likely_tongue": True, "reason": "图像统计依赖缺失，跳过预检"}

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB").resize((128, 128))
    except Exception as e:                   # noqa: BLE001
        return {"available": False, "likely_tongue": True, "reason": f"图像无法解码：{e}"}

    arr = np.asarray(img, dtype=np.int16)
    gray_std = float(arr.mean(axis=2).std())

    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    hue = hsv[..., 0] * 360.0 / 255.0
    sat = hsv[..., 1] / 255.0
    val = hsv[..., 2] / 255.0
    # 红/粉：色相在高位环或低位环，且有一定饱和度与亮度（排除纯黑与灰）
    reddish = ((hue <= 25.0) | (hue >= 335.0)) & (sat >= 0.18) & (val >= 0.12)
    red_ratio = float(reddish.mean())

    likely = red_ratio >= 0.02 and gray_std >= 8.0
    if gray_std < 8.0:
        reason = f"图像几乎为纯色（灰度标准差 {gray_std:.1f}），看不到任何主体"
    elif red_ratio < 0.02:
        reason = f"图像中红/粉区域占比仅 {red_ratio * 100:.1f}%，未检测到舌体特征"
    else:
        reason = ""
    return {
        "available": True,
        "likely_tongue": likely,
        "red_ratio": round(red_ratio, 4),
        "gray_std": round(gray_std, 2),
        "reason": reason,
    }


def prepare_image(data: bytes) -> bytes:
    """压缩到长边 768 并转 JPEG。

    原图直传是单张辨识慢到分钟级的主因；768 长边对特征判断已足够，
    再大只是在喂像素给模型而不增加信息。
    """
    try:
        from PIL import Image
    except ImportError:                      # pragma: no cover - Pillow 为必需依赖
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > TONGUE_IMAGE_MAX_SIDE:
            ratio = TONGUE_IMAGE_MAX_SIDE / float(longest)
            img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()
    except Exception as e:                   # noqa: BLE001
        # 压缩失败不阻断：退回原图，让模型自己处理（可能慢，但不至于不能用）
        logger.warning(f"舌图预处理失败，改用原图：{e}")
        return data


# --------------------------------------------------------------------------- #
# 模型调用
# --------------------------------------------------------------------------- #
def _parse_json(text: str) -> Dict[str, Any]:
    """解析模型输出。先试标准 JSON，失败再用 json_repair 兜一层。"""
    text = (text or "").strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        from json_repair import repair_json

        repaired = repair_json(text, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
    except Exception as e:                   # noqa: BLE001
        logger.warning(f"舌象结果 JSON 修复失败：{e}")
    raise ValueError("视觉模型未返回可解析的 JSON")


def recognize_bytes(data: bytes, *, model: Optional[str] = None) -> Dict[str, Any]:
    """对单张图片做辨识。**同步阻塞**——调用方负责放到线程里跑。

    返回结构（无论成功失败都带 model / elapsed_ms，便于排查与评测）：

        visible            是否看到舌头
        quality            质量判定（sufficient + issues）
        features           归一后的可观察特征
        observations       模型直接看到的描述
        confidence         模型自评置信度（已夹到 0~1）
        corrections        被枚举闸纠正的字段说明（模型乱给值的证据）
        disclaimer         固定话术：不是诊断
    """
    if not settings.VISION_ENABLE:
        raise RuntimeError("视觉辨识未启用（VISION_ENABLE=false）")
    if not settings.OLLAMA_BASE_URL:
        raise RuntimeError("OLLAMA_BASE_URL 未配置，无法调用视觉模型")

    model = model or settings.VISION_MODEL
    t0 = time.perf_counter()

    # —— 闸门 0：像素统计预检。不合格直接返回，不调模型 ——
    # 这一步同时省掉约 90 秒的无效推理，而且结论不依赖模型当时的"心情"。
    probe = probe_image(data)
    if probe.get("available") and not probe.get("likely_tongue"):
        return {
            "visible": False,
            "quality": {"sufficient": False, "issues": [probe.get("reason") or "未检测到舌体"]},
            "features": {k: "无法判断" for k in TONGUE_FEATURE_ENUMS},
            "observations": [],
            "confidence": 0.0,
            "corrections": [],
            "model": "image-probe（未调用视觉模型）",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "probe": probe,
            "raw_excerpt": "",
            "disclaimer": DISCLAIMER,
        }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT,
                "images": [base64.b64encode(prepare_image(data)).decode("ascii")],
            },
        ],
        "stream": False,
        "format": "json",          # 让 Ollama 约束输出为 JSON
        "keep_alive": "30m",       # 模型常驻：否则每次都要重新加载，单张能到分钟级
        "options": {
            "temperature": 0,      # 观察任务不需要创造性，越随机越糟
            "num_predict": 512,
        },
    }

    with httpx.Client(timeout=settings.VISION_TIMEOUT) as client:
        resp = client.post(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat", json=payload)
        resp.raise_for_status()
        body = resp.json()

    raw_text = ((body.get("message") or {}).get("content") or "").strip()
    parsed = _parse_json(raw_text)

    visible = bool(parsed.get("visible"))
    quality_raw = parsed.get("quality") if isinstance(parsed.get("quality"), dict) else {}
    features, corrections = normalize_tongue_features(parsed.get("features"))
    # 闸门 2：字段间自洽（如「无苔」又「苔厚」这种矛盾）
    features, reconciled = reconcile_tongue_features(features)
    corrections.extend(reconciled)

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    issues = [str(x).strip() for x in (quality_raw.get("issues") or []) if str(x).strip()]
    sufficient = bool(quality_raw.get("sufficient")) and visible and confidence >= TONGUE_MIN_CONFIDENCE
    if visible and confidence < TONGUE_MIN_CONFIDENCE:
        issues.append(f"模型自评置信度 {confidence:.2f} 低于门槛 {TONGUE_MIN_CONFIDENCE}")

    # visible=false 时特征一律作废，issues 也一并替换掉。
    # 实测发现：模型在「明明没有舌头」的图上仍会编出「舌头只露出舌尖」
    # 「舌头部分被遮挡」这类 issues——那是对不存在对象的描述，属于幻觉残留，
    # 留着会让前端显示"图片有问题"而不是"没拍到舌头"，是错的归因。
    if not visible:
        features = {k: "无法判断" for k in features}
        issues = ["未在照片中检测到可辨认的舌体"]
        sufficient = False

    observations = [str(x).strip() for x in (parsed.get("observations") or []) if str(x).strip()][:4]

    return {
        "visible": visible,
        "quality": {"sufficient": sufficient, "issues": issues},
        "features": features,
        "observations": observations if visible else [],
        "confidence": round(confidence, 3),
        "corrections": corrections,
        "model": model,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "probe": probe,
        "raw_excerpt": raw_text[:600],
        "disclaimer": DISCLAIMER,
    }
