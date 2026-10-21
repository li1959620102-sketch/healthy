# -*- coding: utf-8 -*-
"""ASR 端到端自测：合成中文语音 → 本地转写 → 与原文比对。

为什么要"自己造音频"而不是等用户录音：
  转写的正确性必须有一个**已知正确答案**的输入才能判断。edge-tts 合成的语音
  自带文本标签，逐字比对即可算出字准率（CER 的粗略版），比"听起来差不多"强得多。

注意 edge-tts 走微软在线接口，只是**生成测试素材**用的；真实用户音频的识别
全程在本地 faster-whisper 完成，不出网。
"""
import asyncio
import difflib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLES = [
    "我想做一个健康风险测评，最近血压有点高。",
    "高血压的人平时吃什么比较好，能不能吃咸菜？",
    "帮我看看这周打卡完成得怎么样。",
]


async def synth(text: str, path: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
    await communicate.save(path)


def cer(ref: str, hyp: str) -> float:
    """字准率（1 - 编辑距离/参考长度）。粗略但够判断"能不能用"。"""
    sm = difflib.SequenceMatcher(None, ref, hyp)
    dist = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes()
               if tag != "equal")
    return max(0.0, 1 - dist / max(1, len(ref)))


def main() -> int:
    from app.services.asr_service import asr_service

    print("ASR 状态：", asr_service.status())
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_testdata", "asr")
    os.makedirs(tmpdir, exist_ok=True)

    total_cer = []
    for i, text in enumerate(SAMPLES, 1):
        path = os.path.join(tmpdir, f"sample{i}.mp3")
        if not os.path.exists(path):
            try:
                asyncio.run(synth(text, path))
            except Exception as e:                   # noqa: BLE001
                print(f"[{i}] 合成失败（edge-tts 需外网）：{e}")
                continue
        data = open(path, "rb").read()

        t0 = time.perf_counter()
        r = asr_service.transcribe_bytes(data, "sample.mp3", "zh")
        cost = time.perf_counter() - t0

        score = cer(text, r["text"])
        total_cer.append(score)
        print(f"\n[{i}] 原文：{text}")
        print(f"    转写：{r['text']}")
        print(f"    字准率 {score*100:.1f}% · 音频 {r['duration']}s · 推理 {cost:.1f}s "
              f"（{r['duration']/max(0.01,cost):.1f}x 实时）· 语速 {r['chars_per_second']} 字/秒")
        if r["segments"]:
            print(f"    分段 {len(r['segments'])} 条，首段 {r['segments'][0]['start']}s-{r['segments'][0]['end']}s")

    if total_cer:
        print(f"\n平均字准率：{sum(total_cer)/len(total_cer)*100:.1f}%（{len(total_cer)} 条样本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
