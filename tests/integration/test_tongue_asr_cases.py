# -*- coding: utf-8 -*-
"""舌象辨识 + 语音识别 · 确定性边界测试。

这两个功能都依赖外部模型，端到端跑一次要几十秒（舌象）到几秒（ASR），
不适合每次都跑。但它们的**错误处理与输入校验**恰恰是最容易出事、
又快得可以每次都跑的部分，所以拆出来单独覆盖：

  舌象：像素预检（抗幻觉第一道闸）、枚举归一、字段自洽、格式与大小校验；
  ASR ：能力探测、格式白名单、空音频、依赖缺失时的明确报错。

不在这里测"识别得准不准"——那需要标注集和人工评判，属于评测脚本
（scripts/eval_tongue.py）的职责，不能靠单测假装。
"""
import io

import pytest
from PIL import Image

from app.core.rules import (
    TONGUE_UNKNOWN,
    check_tongue_image,
    normalize_tongue_features,
    reconcile_tongue_features,
)
from app.services import tongue_service
from app.services.asr_service import ASRService, ASRUnavailable


def _img_bytes(color=(255, 255, 255), size=(600, 400), fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 舌象 · 输入校验
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,ok", [
    ("tongue.jpg", True), ("tongue.JPEG", True), ("a.png", True),
    ("a.webp", True), ("a.bmp", True),
    ("a.gif", False), ("a.pdf", False), ("", False), ("a", False),
])
def test_tongue_format_whitelist(name, ok):
    """只放行常见图片格式；PDF/GIF 之类直接拒，别把非图像喂给视觉模型。"""
    assert check_tongue_image(name, 1024).ok is ok


def test_tongue_size_limits():
    assert check_tongue_image("a.jpg", 0).ok is False
    assert check_tongue_image("a.jpg", 8 * 1024 * 1024).ok is True
    assert check_tongue_image("a.jpg", 8 * 1024 * 1024 + 1).ok is False


# --------------------------------------------------------------------------- #
# 舌象 · 抗幻觉第一道闸（像素预检）
# --------------------------------------------------------------------------- #
def test_probe_rejects_blank_image():
    """纯白图必须被预检拦下——实测模型会在这张图上编出"舌尖偏红"。"""
    p = tongue_service.probe_image(_img_bytes((255, 255, 255)))
    assert p["available"] is True
    assert p["likely_tongue"] is False
    assert p["reason"]


def test_probe_rejects_cold_scene():
    """冷色风景没有红/粉区域，应被拦下。"""
    p = tongue_service.probe_image(_img_bytes((95, 135, 195)))
    assert p["likely_tongue"] is False


def test_probe_passes_reddish_image():
    """有主体、且含红/粉区域的画面应放行，交给模型判断。

    注意不能直接用纯色红图：那是"一块纯色"，没有主体，会被灰度标准差
    这条判据正确拦下（拦得对——纯色块里不可能有舌头）。所以这里画一个
    肤色底 + 粉红椭圆的构图，才是"像舌象"的最小样本。
    """
    from PIL import ImageDraw

    img = Image.new("RGB", (800, 600), (226, 190, 170))
    ImageDraw.Draw(img).ellipse([250, 180, 550, 470], fill=(205, 120, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)

    p = tongue_service.probe_image(buf.getvalue())
    assert p["likely_tongue"] is True, p
    assert p["red_ratio"] >= 0.02


def test_recognize_on_blank_skips_model_called(monkeypatch):
    """预检拦下时必须**不调用模型**，并返回结构完整的负结果。"""
    def _boom(*a, **k):
        raise AssertionError("不该调用视觉模型")

    monkeypatch.setattr(tongue_service.httpx, "Client", _boom)
    r = tongue_service.recognize_bytes(_img_bytes((255, 255, 255)))

    assert r["visible"] is False
    assert r["quality"]["sufficient"] is False
    assert r["confidence"] == 0.0
    assert "image-probe" in r["model"]
    assert r["disclaimer"]
    # 特征必须全部为「无法判断」，不能残留任何编造值
    assert set(r["features"].values()) == {TONGUE_UNKNOWN}


# --------------------------------------------------------------------------- #
# 舌象 · 输出收敛
# --------------------------------------------------------------------------- #
def test_normalize_rejects_out_of_enum():
    """模型自造的颜色词必须被归一，并留下纠正记录。"""
    feat, corrected = normalize_tongue_features({
        "tongue_color": "黄绿色",           # 不在枚举里
        "coating_color": "白",              # 合法
    })
    assert feat["tongue_color"] == TONGUE_UNKNOWN
    assert feat["coating_color"] == "白"
    assert corrected and "黄绿色" in corrected[0]


def test_normalize_fills_missing_fields():
    """模型漏字段时全部补成「无法判断」，不能出现 KeyError 或 None。"""
    feat, _ = normalize_tongue_features({})
    assert len(feat) == 8
    assert set(feat.values()) == {TONGUE_UNKNOWN}


def test_reconcile_no_coating_with_thickness():
    """「无苔」却给了厚薄 → 厚薄必须被置为无法判断（保留"无苔"这个观察）。"""
    feat, fixed = reconcile_tongue_features({
        "coating_color": "无苔", "coating_thickness": "薄",
    })
    assert feat["coating_color"] == "无苔"
    assert feat["coating_thickness"] == TONGUE_UNKNOWN
    assert fixed


def test_reconcile_thickness_no_coating():
    """厚薄为「无苔」时，颜色也必须是「无苔」（没有苔就没有苔色）。"""
    feat, fixed = reconcile_tongue_features({
        "coating_color": "黄", "coating_thickness": "无苔",
    })
    assert feat["coating_color"] == "无苔"
    assert fixed


def test_reconcile_leaves_normal_combo_alone():
    """正常的「白 + 薄」不该被改动。"""
    feat, fixed = reconcile_tongue_features({
        "coating_color": "白", "coating_thickness": "薄",
    })
    assert feat == {"coating_color": "白", "coating_thickness": "薄"}
    assert fixed == []


# --------------------------------------------------------------------------- #
# ASR
# --------------------------------------------------------------------------- #
def test_asr_status_shape():
    """能力探测必须能如实报出：装没装、模型权重在不在、有没有加载。"""
    st = ASRService().status()
    for key in ("installed", "loaded", "model", "device", "compute_type",
                "model_dir", "downloaded", "ready"):
        assert key in st
    assert isinstance(st["downloaded"], list)
    # 新实例不该是已加载状态
    assert st["loaded"] is False


def test_asr_rejects_bad_extension():
    svc = ASRService()
    with pytest.raises(ValueError) as e:
        svc.transcribe_bytes(b"xxxx", "voice.exe")
    assert "不支持" in str(e.value)


def test_asr_rejects_empty_audio():
    svc = ASRService()
    with pytest.raises(ValueError) as e:
        svc.transcribe_bytes(b"", "voice.webm")
    assert "空" in str(e.value)


def test_asr_rejects_oversize():
    svc = ASRService()
    with pytest.raises(ValueError) as e:
        svc.transcribe_bytes(b"x" * (20 * 1024 * 1024 + 1), "voice.webm")
    assert "MB" in str(e.value)


def test_asr_raises_clear_error_when_dependency_missing(monkeypatch):
    """依赖没装时必须抛 ASRUnavailable（带安装提示），而不是空指针或静默失败。"""
    svc = ASRService()

    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(ASRUnavailable) as e:
        svc.ensure_model()
    assert "faster-whisper" in str(e.value)
