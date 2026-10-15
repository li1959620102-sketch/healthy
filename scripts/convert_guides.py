# -*- coding: utf-8 -*-
"""卫健委食养指南 PDF → knowledge_base/*.md 转换脚本（可复用）。

用法：
    D:/conda/envs/ai_py311/python.exe scripts/convert_guides.py

清洗规则：
- 去掉目录点线行（....）、页码行（— 5 — / 12）、页眉页脚空行；
- 合并连续空行；保留章节标题与表格文字（表格行转普通文本）。
输出：knowledge_base/10_食养指南_<名称>.md，文件头注明来源与合规声明。
"""
import os
import re
import sys

import pdfplumber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "downloads", "food_guides")
DST = os.path.join(ROOT, "knowledge_base")


def clean(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        # 目录点线 / 纯点线
        if re.match(r"^[.·…\s]{6,}$", s):
            continue
        if re.match(r"^[一二三四五六七八九十][、.].*?[.·…]{3,}", s):
            s = re.sub(r"[.·…]{3,}.*$", "", s).strip()
        # 尾部点线 + 页码（目录行"标题 ...... 2"）
        s = re.sub(r"[.·…]{3,}\s*\d*\s*$", "", s).strip()
        if not s:
            continue
        # 页码行
        if re.match(r"^[—–\-]?\s*\d{1,3}\s*[—–\-]?$", s):
            continue
        out.append(s)
    # 合并连续空行
    merged, blank = [], 0
    for l in out:
        if l == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        merged.append(l)
    return "\n".join(merged).strip()


def convert(name: str) -> None:
    path = os.path.join(SRC, name)
    title = re.sub(r"\.pdf$", "", name)
    out_path = os.path.join(DST, f"10_食养指南_{title}.md")
    if os.path.exists(out_path):
        print("已存在，跳过:", title)
        return
    parts = [f"# {title}", "",
             f"> 来源：国家卫生健康委员会办公厅发布《{title}》；本文件为官方公开文件文字提取版，仅供平台知识检索使用，不构成诊疗建议。", ""]
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            t = clean(t)
            if t:
                parts.append(t)
                parts.append("")
    body = "\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"{title}: {len(body)} 字 → {os.path.basename(out_path)}")


def main():
    names = sorted(n for n in os.listdir(SRC) if n.lower().endswith(".pdf"))
    if not names:
        print("downloads/food_guides/ 下没有 PDF")
        return
    for n in names:
        try:
            convert(n)
        except Exception as e:  # noqa: BLE001
            print(f"{n} 转换失败：{str(e)[:100]}")


if __name__ == "__main__":
    sys.exit(main())
