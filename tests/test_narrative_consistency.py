"""文案一致性门：对外叙事面的漂移防线（2026-11-23 建制）。

背景判例（三起真实翻车，均在发布后才发现）：
  1. CLI 定位文案停在「Markdown 驱动的公众号发布工具」，README/pyproject 已是
     流水线叙事——cli.py:46 改完才发，0.5.0 已烘焙进 sdist；
  2. README「内置 9 个插件」在 PLUGIN-DEMO 落 12 个后漂移两批；
  3. README 演示图用仓库相对路径 docs/demo.gif——GitHub 正常、PyPI 项目页断链。

本门锁四个对齐面：CLI 定位 / README 状态词 ↔ classifier / README 插件计数 ↔
plugins_builtin 实数 / README 演示图引用形态。纯文本断言，零网络零解析器。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from musespiral.cli import cli

MS = Path(__file__).resolve().parent.parent
README = MS / "README.md"
PYPROJECT = MS / "pyproject.toml"
CHANGELOG = MS / "CHANGELOG.md"
PLUGINS_DIR = MS / "musespiral" / "plugins_builtin"

# 旧定位叙事词——任何对外面出现即红（改名 musespiral 后的遗留词）
_LEGACY_WORDS = ["公众号发布工具", "mdpub —", "Markdown 驱动的公众号"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_cli_positioning_no_legacy_words() -> None:
    """CLI --help 首行定位不残留旧叙事（判例 1 的机器化）。"""
    doc = cli.__doc__ or ""
    for w in _LEGACY_WORDS:
        assert w not in doc, f"CLI 定位文案残留旧叙事词「{w}」——发布前漏改"


def test_package_docstring_no_legacy_words() -> None:
    """包 docstring 同样不残留旧叙事（W1-B3 发现的盲区：门原本只查 cli.__doc__）。"""
    import musespiral

    doc = (musespiral.__doc__ or "")
    for w in ("WeChat Official Account publisher", "公众号发布工具"):
        assert w not in doc, f"包 docstring 残留旧叙事词「{w}」"


def test_readme_status_matches_classifier() -> None:
    """README 状态词（**Beta**/Alpha）与 pyproject classifier 同档（判例 2 同类）。"""
    text = _read(README)
    m = re.search(r"\*\*(Alpha|Beta|Production/Stable)\*\*", text)
    assert m, "README 状态段未找到 **Alpha/Beta/Production/Stable** 加粗状态词"
    status = m.group(1)
    pyproject = _read(PYPROJECT)
    if status == "Beta":
        assert "Development Status :: 4 - Beta" in pyproject
    elif status == "Alpha":
        assert "Development Status :: 3 - Alpha" in pyproject
    else:
        assert "Development Status :: 5" in pyproject


def test_readme_plugin_count_matches_reality() -> None:
    """README「内置 N 个插件」== plugins_builtin 有 manifest 的目录实数。"""
    actual = len([d for d in PLUGINS_DIR.iterdir() if (d / "manifest.json").is_file()])
    text = _read(README)
    m = re.search(r"内置\s*(\d+)\s*个插件", text)
    assert m, "README 未找到「内置 N 个插件」计数声明"
    claimed = int(m.group(1))
    assert claimed == actual, (
        f"README 声明 {claimed} 个插件，实际 {actual} 个——新增/删除插件后须同步 README 表"
    )


def test_readme_demo_gif_uses_absolute_url() -> None:
    """演示图引用必须绝对 URL——PyPI 项目页不随 sdist 带 docs/，相对路径断链。"""
    text = _read(README)
    gifs = re.findall(r"!\[[^\]]*\]\(([^)]+\.gif)\)", text)
    assert gifs, "README 未找到 GIF 引用"
    for g in gifs:
        assert g.startswith("https://"), (
            f"GIF 引用须绝对 URL（PyPI 页断链判例）：{g}"
        )


def test_version_pyproject_matches_changelog_head() -> None:
    """pyproject version 与 CHANGELOG 顶部版本条目一致（发布前口径锁）。"""
    pyproject = _read(PYPROJECT)
    vm = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert vm, "pyproject 未找到 version"
    version = vm.group(1)
    head = _read(CHANGELOG)[:2000]
    assert version in head, (
        f"pyproject version {version} 未出现在 CHANGELOG 顶部——发版须同步两处"
    )


def test_tracked_pyproject_urls_resolve_to_real_repo() -> None:
    """project.urls 指向真实仓库（SeekerGalahad/musespiral）非占位符。"""
    pyproject = _read(PYPROJECT)
    assert "SeekerGalahad/musespiral" in pyproject
    assert "example.com" not in pyproject


def test_pypi_release_doc_mentions_pycache_guard() -> None:
    """PYPI_RELEASE.md 必须保留 __pycache__ 清理步骤（0.5.0 真缺陷的防回退锚）。"""
    doc = _read(MS / "docs" / "PYPI_RELEASE.md")
    assert "__pycache__" in doc, "PYPI_RELEASE.md 丢失 pycache 清理步骤（0.5.0 缺陷防回退）"
