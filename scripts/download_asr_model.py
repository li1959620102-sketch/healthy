# -*- coding: utf-8 -*-
"""下载 faster-whisper 的 CTranslate2 权重到本地。

为什么自己写下载脚本，而不是让 `WhisperModel("small")` 自动拉：

  1. **自动拉走 huggingface.co**，本机网络直连不通（实测 20 秒超时），
     必须换镜像；而 faster-whisper 只认 `HF_ENDPOINT` 环境变量，配置容易漏；
  2. **没有进度、不能续传**。483MB 在当前网络下断一次就得从头再来；
  3. **离线部署要能复现**：把权重落在项目里的固定目录，容器/新机器直接拷过去即可。

用法：
    PYTHONPATH=D:/AI_health python scripts/download_asr_model.py
    PYTHONPATH=D:/AI_health python scripts/download_asr_model.py --source modelscope --dest D:/AI_health/models/whisper/small

下载完把 `ASR_MODEL` 指向该目录即可（faster-whisper 支持直接传目录路径）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, List, Tuple

import requests

# 两个源的文件清单（(文件名, 期望字节数)）。大小用于校验，不是装饰：
# 网络中断时 requests 可能不报错却只落了半个文件，只有比对大小才能发现。
FILES: List[Tuple[str, int]] = [
    ("config.json", 2370),
    ("tokenizer.json", 2203239),
    ("vocabulary.txt", 459861),
    ("model.bin", 483546902),      # 放最后：大文件，前面小文件先验证通道可用
]

SOURCES: Dict[str, str] = {
    # ModelScope（国内 CDN，实测约 1.1 MB/s）
    "modelscope": "https://modelscope.cn/models/pengzhendong/faster-whisper-small/resolve/master/{file}",
    # hf-mirror（实测约 40 KB/s，仅作备用）
    "hf-mirror": "https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/{file}",
}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download_once(url: str, dest: str, expected: int, read_timeout: int = 30) -> None:
    """流式下载 + 断点续传 + 大小校验。失败会抛出，由外层重试。"""
    part = dest + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    if done > expected:
        os.remove(part)
        done = 0

    headers = {"Range": f"bytes={done}-"} if done else {}
    t0 = time.perf_counter()
    base_done = done
    # (连接超时, 读超时)：读超时不能太大，网络卡住时要尽快失败以便续传重试
    with requests.get(url, headers=headers, stream=True, timeout=(20, read_timeout)) as r:
        if done and r.status_code != 206:
            # 服务端不支持续传：从头来，避免把完整响应追加到半个文件后面
            done = 0
            base_done = 0
        r.raise_for_status()
        mode = "ab" if done else "wb"
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                speed = (done - base_done) / max(0.001, time.perf_counter() - t0)
                pct = done * 100.0 / expected if expected else 0
                sys.stdout.write(
                    f"\r  {os.path.basename(dest)}  {pct:5.1f}%  "
                    f"{_human(done)}/{_human(expected)}  {_human(speed)}/s   "
                )
                sys.stdout.flush()
    sys.stdout.write("\n")

    actual = os.path.getsize(part)
    if expected and actual != expected:
        raise RuntimeError(f"{dest} 大小不符：期望 {expected} 实际 {actual}（可续传）")
    os.replace(part, dest)


def download(url: str, dest: str, expected: int, attempts: int = 6) -> None:
    """带重试的下载。

    本机到 ModelScope 的连接会间歇性卡住（实测 60 秒无数据被读超时打断），
    所以**必须**重试：每次重试都从 `.part` 已有字节处续传，不会白下。
    没有这一层，483MB 的权重几乎不可能一次拉完。
    """
    last = None
    for i in range(1, attempts + 1):
        try:
            download_once(url, dest, expected)
            return
        except Exception as e:                      # noqa: BLE001
            last = e
            have = os.path.getsize(dest + ".part") if os.path.exists(dest + ".part") else 0
            print(f"  第 {i}/{attempts} 次中断（已落 {_human(have)}）：{type(e).__name__}: {e}")
            if i < attempts:
                time.sleep(2)
    raise RuntimeError(f"重试 {attempts} 次仍失败：{last}")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 faster-whisper 权重")
    parser.add_argument("--dest", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "whisper", "small"))
    parser.add_argument("--source", default="modelscope", choices=list(SOURCES))
    args = parser.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    tpl = SOURCES[args.source]
    print(f"目标目录：{args.dest}")
    print(f"下载源  ：{args.source}")

    for name, size in FILES:
        dest = os.path.join(args.dest, name)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            print(f"  已存在且大小正确，跳过：{name}")
            continue
        url = tpl.format(file=name)
        print(f"  下载 {name}（{_human(size)}）← {url}")
        download(url, dest, size)

    print("\n全部完成。可用文件：")
    for name, _ in FILES:
        print(f"  {os.path.join(args.dest, name)}")
    print(f"\n把 ASR_MODEL 设为：{args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
