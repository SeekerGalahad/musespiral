"""F18 theme learn — 从公众号文章 HTML 学习生成用户主题。

learn_from_html(html_text, name, out_dir=None) -> Path：
用标准库 html.parser 解析文章 HTML，收集 <section>/<p>/<span> 内联 style 中的
color / font-size / line-height / text-align 值，按出现频次归一映射到主题键：

  - 出现最多的正文色               → colors.text
  - 次高频强调色（区别于 text 的
    第一颜色；无第二色则不写）      → colors.primary
  - 最常见字号                     → typography.font_size
  - 最常见行高                     → typography.line_height

所有样式值先经白名单正则过滤（#hex 三/六位、rgb() 纯数字、数字+px、无单位
小数；text-align 仅限 left/center/right/justify 关键字），不匹配即丢弃——
expression()/url() 之类一律进不了产物，防注入。同一 style 属性内同名属性
后声明覆盖前声明（CSS 语义）；频次并列时取先出现者。

生成含 extends: default 的主题 YAML（结构对齐 themes.load_theme 的加载
约定：未采集键如 colors.code_bg 经 extends 继承基座），默认写
config.home_dir()/themes/<name>.yaml，out_dir 可覆盖供测试/冒烟；同名文件
直接覆盖（theme learn 是再生成动作）。CLI 接线在集成阶段完成。
"""
from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import yaml

from ... import config
from .themes import _validate_name

# 只收集正文这三类标签的内联 style（公众号正文的标准骨架）
_STYLE_TAGS = frozenset({"section", "p", "span"})
_TRACKED_PROPS = ("color", "font-size", "line-height", "text-align")

# ── 样式值白名单（防注入：不匹配一律丢弃）─────────────────
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")  # 三/六位 hex
_RGB_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.IGNORECASE)
_PX_RE = re.compile(r"^(\d+(?:\.\d+)?)px$", re.IGNORECASE)  # 纯数字+px
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")  # 无单位小数（含整数形态）
_ALIGN_KEYWORDS = frozenset({"left", "center", "right", "justify"})


def _clean_color(value: str) -> str | None:
    """颜色白名单：#hex 三/六位（归一小写）或 rgb(数字,数字,数字)（归一去空格）。"""
    v = value.strip()
    if _HEX_RE.match(v):
        return v.lower()
    m = _RGB_RE.match(v)
    if m:
        return "rgb({},{},{})".format(*m.groups())
    return None


def _clean_px(value: str) -> str | None:
    """字号白名单：纯数字+px（归一小写单位），如 15px / 16.5px。"""
    m = _PX_RE.match(value.strip())
    return f"{m.group(1)}px" if m else None


def _clean_line_height(value: str) -> str | None:
    """行高白名单：无单位小数（1.75）或数字+px（28px）。"""
    v = value.strip()
    m = _PX_RE.match(v)
    if m:
        return f"{m.group(1)}px"
    if _NUMBER_RE.match(v):
        return v
    return None


def _clean_text_align(value: str) -> str | None:
    """对齐白名单：仅四个标准关键字，其余丢弃。"""
    v = value.strip().lower()
    return v if v in _ALIGN_KEYWORDS else None


_CLEANERS: dict[str, Callable[[str], str | None]] = {
    "color": _clean_color,
    "font-size": _clean_px,
    "line-height": _clean_line_height,
    "text-align": _clean_text_align,
}


class _StyleCollector(HTMLParser):
    """收集 _STYLE_TAGS 内联 style 中受关注属性的（白名单过滤后）值频次。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: dict[str, Counter[str]] = {p: Counter() for p in _TRACKED_PROPS}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _STYLE_TAGS:
            return
        style = dict(attrs).get("style")
        if not style:
            return
        decls: dict[str, str] = {}
        for part in style.split(";"):
            if ":" not in part:
                continue
            prop, _, val = part.partition(":")
            prop = prop.strip().lower()
            if prop in _CLEANERS:
                decls[prop] = val  # 同一 style 内后声明覆盖前声明
        for prop, val in decls.items():
            cleaned = _CLEANERS[prop](val)
            if cleaned is not None:
                self.counts[prop][cleaned] += 1


def _analyze(html_text: str) -> dict[str, Counter[str]]:
    parser = _StyleCollector()
    parser.feed(html_text)
    parser.close()
    return parser.counts


def _build_theme_data(counts: dict[str, Counter[str]], name: str) -> dict[str, Any]:
    """频次统计 → 主题数据（只写有据可依的键，其余经 extends: default 继承）。"""
    data: dict[str, Any] = {
        "name": name,
        "description": "theme learn 从公众号文章 HTML 学习生成",
        "extends": "default",
    }
    colors: dict[str, str] = {}
    color_counts = counts["color"]
    if color_counts:
        text_color = color_counts.most_common(1)[0][0]
        colors["text"] = text_color
        rest = [(v, c) for v, c in color_counts.most_common() if v != text_color]
        if rest:
            colors["primary"] = rest[0][0]
    if colors:
        data["colors"] = colors
    typo: dict[str, str] = {}
    if counts["font-size"]:
        typo["font_size"] = counts["font-size"].most_common(1)[0][0]
    if counts["line-height"]:
        typo["line_height"] = counts["line-height"].most_common(1)[0][0]
    if typo:
        data["typography"] = typo
    return data


def learn_from_html(html_text: str, name: str, out_dir: str | Path | None = None) -> Path:
    """解析公众号文章 HTML，频次归一生成 extends: default 的用户主题 YAML。

    返回写出的 YAML 路径；out_dir 缺省写 config.home_dir()/themes/，
    同名文件直接覆盖。CLI（url 拉取/--html 文件）在集成阶段接线。
    """
    _validate_name(name)
    data = _build_theme_data(_analyze(html_text), name)
    target_dir = Path(out_dir) if out_dir is not None else config.home_dir() / "themes"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.yaml"
    header = (
        "# musespiral theme learn 生成 — extends 继承基座 default\n"
        "# 值取自文章正文内联样式（section/p/span）频次统计；重跑 learn 会覆盖本文件。\n"
    )
    target.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return target
