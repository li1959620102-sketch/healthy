# -*- coding: utf-8 -*-
"""P2 技术债的**回归守卫**（纯静态扫描，不连任何外部服务）。

为什么要把"清理"写成用例：
这些是"删掉一次就完事"的债，但代码库会持续被人和模型改写，
不设闸门就会回流——比如再有人从骨架里拷一个 `query_knowledge` 进来、
或者在路由里塞一句调试 `print()`。把口径固化成断言，比写进文档可靠。

覆盖三项（对应体检报告 P2-1 / P2-2 / P2-5）：
  1. 交付代码（`app/` + `main.py`）不得出现裸 `except:` 与 `print()`；
  2. `app/` 下不得出现空目录（骨架残留）；
  3. `app/config.py` 声明的配置键必须有消费方，`.env` 与 `.env.example` 键必须一致。

注意 1 的边界：`legacy_nrs2002/` **刻意豁免**——那是按项目红线隔离保留的旧系统，
不删也不修；`scripts/` 与根目录 `_e2e_*.py` 是命令行工具，`print` 是它们的输出方式，同样豁免。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit

# 预留但尚未接入的能力键：允许"声明了暂时没人读"。
# 每加一条都必须写清理由，否则这个豁免名单会变成垃圾桶。
RESERVED_CONFIG_KEYS = {
    # RAG 管线目前只用相似度阈值（KB_MIN_SIMILARITY）过滤，rerank 阶段未接入
    "SILICON_FLOW_RERANK",
}

# 由声明式基类生成、不出现在业务代码里的键（SQLAlchemy/工具约定）
CONFIG_KEY_EXEMPT = {
    "ENV",
}


def _py_files(*roots):
    files = []
    for r in roots:
        p = ROOT / r
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


# --------------------------------------------------------------------------- #
# P2-1：裸 except / print
# --------------------------------------------------------------------------- #
def test_shipping_code_has_no_bare_except():
    """裸 `except:` 会连 KeyboardInterrupt / SystemExit 一起吞掉，交付代码里必须为零。

    历史上 5 处裸 except 全在 `legacy_nrs2002/`（已按红线隔离保留），
    本条守的是"别再把它们带回交付目录"。
    """
    offenders = []
    for f in _py_files("app", "main.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"except\s*:", code):
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert not offenders, f"交付代码出现裸 except:：{offenders}"


def test_shipping_code_has_no_print():
    """`app/` 是服务端代码，日志一律走 logging；`print` 会绕过日志级别与采集。

    命令行工具（`scripts/`、根目录 `_e2e_*.py`）不在检查范围——它们的输出就是 print。
    """
    offenders = []
    for f in _py_files("app"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"(?<![\w.])print\s*\(", code):
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert not offenders, f"服务端代码出现 print()：{offenders}"


# --------------------------------------------------------------------------- #
# P2-2：空目录
# --------------------------------------------------------------------------- #
def test_no_empty_package_dirs():
    """`app/` 下不应有空目录：那是骨架拷贝留下的"看起来有模块、其实什么都没有"。"""
    empties = [str(d.relative_to(ROOT)) for d in (ROOT / "app").rglob("*")
               if d.is_dir() and not any(d.iterdir()) and "__pycache__" not in d.parts]
    assert not empties, f"存在空目录：{empties}"


# --------------------------------------------------------------------------- #
# P2-5：死配置与 .env 键一致
# --------------------------------------------------------------------------- #
def _declared_config_keys():
    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"^\s{4}([A-Z][A-Z0-9_]*)\s*[:=]", cfg, re.M)))


def _consumption_text():
    """除 config.py 外，全部代码/前端/脚本/compose 的文本（配置要么在这里被读，要么就是死的）。"""
    texts = []
    for pat in ("app/**/*.py", "scripts/**/*.py", "tests/**/*.py",
                "frontend/**/*.js", "frontend/**/*.html", "migrations/*.sql",
                "*.yml", "*.yaml", "*.py"):
        for p in ROOT.glob(pat):
            if p == ROOT / "app" / "config.py":
                continue
            if "legacy_nrs2002" in p.parts or "backups" in p.parts:
                continue
            texts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(texts)


def test_every_config_key_has_a_consumer():
    """`app/config.py` 里声明的键必须有人读——否则它就是一条骗人的配置。

    体检报告的 P2-5 就是这样来的：`MILVUS_URI`（以及一批 Django 残留
    `MEDIA_URL`/`MEDIA_ROOT`/`DATA_UPLOAD_MAX_MEMORY_SIZE`/`API_HOST`/`API_PORT`/`API_KEY`）
    声明了、`.env` 里没有、代码里也没人用，恒为 None。
    """
    text = _consumption_text()
    dead = [k for k in _declared_config_keys()
            if k not in RESERVED_CONFIG_KEYS and k not in CONFIG_KEY_EXEMPT
            and not re.search(r"\b" + k + r"\b", text)]
    assert not dead, (
        f"以下配置键在 config.py 声明但全仓无人消费：{dead}。"
        "要么接上消费方，要么删掉；确有预留需求请加入 RESERVED_CONFIG_KEYS 并写明理由。")


def test_reserved_config_keys_are_documented():
    """预留键必须写明理由——防止豁免名单被当成`让用例变绿`的后门。"""
    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    for k in RESERVED_CONFIG_KEYS:
        line = next((ln for ln in cfg.splitlines() if re.match(r"\s*" + k + r"\s*[:=]", ln)), None)
        assert line is not None, f"预留键 {k} 已不在 config.py 中，请从豁免名单移除"
        assert "#" in line and len(line.split("#", 1)[1].strip()) >= 6, \
            f"预留键 {k} 缺少说明注释：{line.strip()}"


def test_env_and_example_have_same_keys():
    """`.env` 与 `.env.example` 键必须一一对应。

    口径不同即出事：`.env` 多出来的键是"只有本地能跑"，
    `.env.example` 多出来的键是"新人照抄也起不来"（曾真实发生：
    `.env` 缺 `SECRET_KEY` / `CORS_ORIGINS` / `SMS_DEMO_MODE`）。
    """
    def keys(path):
        if not path.exists():
            pytest.skip(f"{path.name} 不存在（可能未初始化本地环境）")
        return {m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=",
                                                path.read_text(encoding="utf-8"), re.M)}

    env = keys(ROOT / ".env")
    example = keys(ROOT / ".env.example")
    assert env == example, (
        f"仅 .env 有：{sorted(env - example)}；仅 .env.example 有：{sorted(example - env)}")
