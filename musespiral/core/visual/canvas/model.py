"""F70 画布文档模型 — CanvasDoc/Element/TextBlock + 元素白名单执法（L2 纯层）。

- 元素白名单（text/rect/circle/line/path/image/g）在构造期与渲染期双重执法
  （构造 post_init 拒非法 kind；validate_doc 防构造后篡改——F70-T1）；
- 颜色恒 hex（#rgb/#rrggbb/#rrggbbaa）、路径数据恒白名单字符集、href 恒
  dataURI/本地相对引用（http(s) 一律拒——「光栅不触网」红线在文档层前置）；
- SPECS 规格表沿用既有常量不复制数值（§1.3 唯一事实源）：mp 取
  platforms.profiles.PLIMITS，xhs-3x4/xhs-4x5 取 cards.templates.RATIOS，
  square/wide16x9 为本期新增常量（无既有出处）。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ....platforms.profiles import PLIMITS
from ..cards.templates import RATIOS

# 元素白名单（F70 选型论点 5：受限词汇可机器校验——AI 输出=参数 JSON 的前提）
ELEMENT_WHITELIST: tuple[str, ...] = (
    "text", "rect", "circle", "line", "path", "image", "g",
)

# 语义色槽（F72-T2 色槽限定执法面；hex 解析在服务层经 themes 注入——
# themes 是全 visual 域唯一色板事实源，本层只认槽名不认任意色值）。
# F-B 色板升级（2026-11-18）：5→10——以 core.content.themes.load_theme
# 实际键集为准（5 内置主题 colors 恒为 9 键，实证 2026-11-18）补
# border/code_bg/code_text/quote_bg/quote_border 五语义槽；accent 仍为
# 派生槽（quote_border→primary 回退，visual_service.theme_colors 执法）。
# 本表是槽集唯一事实源：schema enum（templates.validate_params）/构建器
# 色表（templates._SLOTS）/payload（visual_payload.color_slots、
# visual_service.theme_colors）四处经 import 同源扩散，改集只动此处。
SEMANTIC_COLOR_SLOTS: tuple[str, ...] = (
    "primary", "text", "muted", "background", "accent",
    "border", "code_bg", "code_text", "quote_bg", "quote_border",
)

# 新增规格常量（§1.3：社媒方图/通用横图，无既有出处，本期钉死）
SQUARE = (1080, 1080)
WIDE16X9 = (1242, 700)

SPECS: dict[str, tuple[int, int]] = {
    "mp": (int(PLIMITS["mp"]["cover_w"]), int(PLIMITS["mp"]["cover_h"])),
    "xhs-3x4": RATIOS["3:4"],
    "xhs-4x5": RATIOS["4:5"],
    "square": SQUARE,
    "wide16x9": WIDE16X9,
}

_ALIGN = ("left", "center", "right")
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_PATH_DATA = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9\s,.+-]*$")
_DATA_URI = re.compile(r"^data:image/[\w.+-]+;base64,[A-Za-z0-9+/=\s]*$")
_LOCAL_REF = re.compile(r"^[\w][\w ./-]*$")


def _is_xml_char(ch: str) -> bool:
    """XML 1.0 合法字符（Char 产生式）：#x9/#xA/#xD、#x20-#xD7FF、
    #xE000-#xFFFD、#x10000-#x10FFFF。其余（C0 控制符、代理区、0xFFFE/0xFFFF）
    写进 SVG 会让 ET.fromstring 报 not well-formed（验收缺陷 D-2 回归面）。"""
    cp = ord(ch)
    return (
        cp in (0x9, 0xA, 0xD)
        or 0x20 <= cp <= 0xD7FF
        or 0xE000 <= cp <= 0xFFFD
        or 0x10000 <= cp <= 0x10FFFF
    )


def decode_text_entities(s: str) -> str:
    """文本取值边界：解码来源实体**一次**，再剔除 XML 非法字符（D-2 真根因）。

    上游 title/hook 取自 summary/frontmatter 时可能已含 ``&quot;`` 等预转义
    实体；若留到序列化器才 unescape，则出现两处不一致（验收报告 2026-11-17
    复核判例）：
    - layout_document 按**实体串**测量分行（``&quot;`` 6 字符），实渲却是解码后的
      ``\"``（1 字符）→ 行宽/断行错位；
    - Pillow 兜底路（route C）直接 ``draw.text(block.lines)`` 未解码 → 显示
      ``&quot;`` 字面量——同一设计稿可见文本随有无浏览器而不同。
    故在**文档构造边界**（TextBlock）一次性解码，layout/两路渲染器此后都看
    解码后文本。另：``html.unescape('&#12;')`` 产 U+000C（XML 非法），若放行
    会令 load_design/export_design 的 verify_svg 解析失败——此处剔除，保证
    序列化面恒 well-formed。解码一次即止（不做二次，避免 ``&amp;`` 逐层剥落）。
    """
    return "".join(ch for ch in html.unescape(s) if _is_xml_char(ch))


def spec_size(spec: str) -> tuple[int, int]:
    """规格键 → (宽, 高)；未知规格抛可读 ValueError（列出全部合法键）。"""
    size = SPECS.get(spec)
    if size is None:
        raise ValueError(f"未知规格: {spec!r}（可选: {'/'.join(SPECS)}）")
    return size


def _check_color(value: object, where: str) -> None:
    if not isinstance(value, str) or not _HEX_COLOR.match(value):
        raise ValueError(
            f"{where}: 非法颜色 {value!r}（只接受 #rgb/#rrggbb/#rrggbbaa 形态 hex）"
        )


def _check_href(value: str, where: str) -> None:
    """图片引用执法：dataURI 或本地相对路径；任何网络地址一律拒收
    （poster.py offline_route「光栅不触网」同红线，在文档层前置）。"""
    low = value.lower()
    if low.startswith(("http://", "https://", "//")) or "://" in low:
        raise ValueError(
            f"{where}: href 禁止网络地址 {value!r}（光栅不触网——"
            "只允许 data:image/*;base64 或本地相对路径）"
        )
    if not value.startswith("data:") and not _LOCAL_REF.match(value):
        raise ValueError(
            f"{where}: 非法引用 {value!r}（dataURI 或本地相对路径）"
        )
    if value.startswith("data:") and not _DATA_URI.match(value):
        raise ValueError(f"{where}: 非法 dataURI {value!r}")


@dataclass
class TextBlock:
    """text 元素负载：原文 + 排版参数 + 行结构（layout 回填后固化进文档）。

    lines=None 表示未测量；layout_document 回填后前端渲染不重排（F70
    选型弱点对策：行结构固化在文档里）。
    """

    text: str = ""
    font_size: int = 48
    color: str = "#1f2937"
    line_height: float = 1.4
    align: str = "left"
    max_width: int | None = None  # None → 取元素 w；元素 w 也为 0 → 画布宽-2×边距
    lines: list[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError(f"text.text: 应为字符串，得到 {type(self.text).__name__}")
        # D-2 真根因：在构造边界一次性解码来源实体并剔除 XML 非法字符，
        # layout 测量与两路渲染器（SVG/Pillow）此后都看解码后文本。
        self.text = decode_text_entities(self.text)
        if isinstance(self.font_size, bool) or not isinstance(self.font_size, int) or self.font_size <= 0:
            raise ValueError(f"text.font_size: 应为正整数，得到 {self.font_size!r}")
        _check_color(self.color, "text.color")
        if not isinstance(self.line_height, (int, float)) or self.line_height <= 0:
            raise ValueError(f"text.line_height: 应为正数，得到 {self.line_height!r}")
        if self.align not in _ALIGN:
            raise ValueError(
                f"text.align: 非法对齐 {self.align!r}（可选: {'/'.join(_ALIGN)}）"
            )
        if self.max_width is not None and (
            isinstance(self.max_width, bool) or not isinstance(self.max_width, int) or self.max_width <= 0
        ):
            raise ValueError(f"text.max_width: 应为正整数，得到 {self.max_width!r}")
        if self.lines is not None:
            if not isinstance(self.lines, list) or any(not isinstance(ln, str) for ln in self.lines):
                raise ValueError("text.lines: 应为字符串列表（layout 回填产物）")
            self.lines = [decode_text_entities(ln) for ln in self.lines]


@dataclass
class Element:
    """画布图元：kind + 几何/样式属性（白名单内的受限词汇，非自由 SVG）。"""

    kind: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    r: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    rx: float = 0.0
    d: str = ""
    fill: str = ""
    stroke: str = ""
    stroke_width: float = 0.0
    opacity: float = 1.0
    dasharray: str = ""
    dashoffset: float = 0.0
    rotate: float | None = None  # transform rotate 中心恒取 (cx, cy)
    href: str = ""
    text: TextBlock | None = None
    children: list["Element"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in ELEMENT_WHITELIST:
            raise ValueError(
                f"非法元素 kind={self.kind!r}（元素白名单: {'/'.join(ELEMENT_WHITELIST)}）"
            )
        if self.fill:
            _check_color(self.fill, f"{self.kind}.fill")
        if self.stroke:
            _check_color(self.stroke, f"{self.kind}.stroke")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError(f"{self.kind}.opacity: 应在 0..1，得到 {self.opacity!r}")
        if self.d and not _PATH_DATA.match(self.d):
            raise ValueError(
                f"{self.kind}.d: 非法路径数据（只允许 SVG 路径指令字符与数值，"
                "拒绝引号/尖括等注入形态）"
            )
        if self.dasharray:
            try:
                [float(p) for p in self.dasharray.replace(",", " ").split()]
            except ValueError:
                raise ValueError(f"{self.kind}.dasharray: 应为数值串，得到 {self.dasharray!r}") from None
        if self.href:
            _check_href(self.href, f"{self.kind}.href")
        if self.children and self.kind != "g":
            raise ValueError(f"{self.kind}: children 仅 g 组合元素可持有（元素白名单语义）")
        if self.text is not None and self.kind != "text":
            raise ValueError(f"{self.kind}: TextBlock 仅 text 元素可持有（元素白名单语义）")
        if self.kind == "text" and self.text is None:
            raise ValueError("text: 元素必须携带 TextBlock（text=None 非法）")
        for child in self.children:
            if not isinstance(child, Element):
                raise ValueError(f"g.children: 应为 Element 列表，得到 {child!r}")


@dataclass
class CanvasDoc:
    """SVG 画布文档：尺寸 + 背景色 + 白名单元素列表（F70 文档模型）。"""

    width: int
    height: int
    background: str = "#ffffff"
    elements: list[Element] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ValueError(f"doc.{name}: 应为正整数，得到 {v!r}")
        if self.background:
            _check_color(self.background, "doc.background")
        for e in self.elements:
            if not isinstance(e, Element):
                raise ValueError(f"doc.elements: 应为 Element 列表，得到 {e!r}")


def validate_doc(doc: CanvasDoc) -> CanvasDoc:
    """渲染前复验（第二道门：构造后篡改的**全部进入序列化的字段**仍被拦）。

    覆盖范围与构造期 `__post_init__` 对齐——kind/颜色/d/href/dasharray/
    opacity/几何标量/TextBlock 全套（font_size/line_height/align/max_width/
    lines）。设计稿载入路径不经 TextBlock 重建（只过 verify_svg 与 params
    schema），本门是该路径的唯一防线：漏检的标量会以未捕获 TypeError/KeyError
    崩在序列化层（500 面），而非设计承诺的可读 ValueError。
    """
    if not isinstance(doc.width, int) or doc.width <= 0 or not isinstance(doc.height, int) or doc.height <= 0:
        raise ValueError(f"doc 尺寸非法: {doc.width}×{doc.height}")
    if doc.background:
        _check_color(doc.background, "doc.background")
    for e in doc.elements:
        _validate_element(e)
    return doc


def _check_number(value: object, where: str) -> None:
    """数值标量执法：只接受有限 int/float（bool 拒绝——bool 是 int 子类但非数值语义）。

    防的是「构造后被换成字符串」这一类篡改：字符串一旦进入 f-string 或
    算术运算，要么抛未捕获异常，要么成为属性逃逸的载体（`1" onload="x`）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: 应为数值，得到 {value!r}")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"{where}: 应为有限数值，得到 {value!r}")


def _check_textblock(block: TextBlock, where: str) -> None:
    """TextBlock 全字段复验（构造期 post_init 的镜像——构造后篡改同样拦）。"""
    if not isinstance(block.text, str):
        raise ValueError(f"{where}.text: 应为字符串，得到 {type(block.text).__name__}")
    if isinstance(block.font_size, bool) or not isinstance(block.font_size, int) or block.font_size <= 0:
        raise ValueError(f"{where}.font_size: 应为正整数，得到 {block.font_size!r}")
    _check_color(block.color, f"{where}.color")
    if isinstance(block.line_height, bool) or not isinstance(block.line_height, (int, float)) or block.line_height <= 0:
        raise ValueError(f"{where}.line_height: 应为正数，得到 {block.line_height!r}")
    if block.align not in _ALIGN:
        raise ValueError(
            f"{where}.align: 非法对齐 {block.align!r}（可选: {'/'.join(_ALIGN)}）"
        )
    if block.max_width is not None and (
        isinstance(block.max_width, bool) or not isinstance(block.max_width, int) or block.max_width <= 0
    ):
        raise ValueError(f"{where}.max_width: 应为正整数，得到 {block.max_width!r}")
    if block.lines is not None and (
        not isinstance(block.lines, list) or any(not isinstance(ln, str) for ln in block.lines)
    ):
        raise ValueError(f"{where}.lines: 应为字符串列表（layout 回填产物）")


def _validate_element(e: object) -> None:
    if not isinstance(e, Element):
        raise ValueError(f"非法元素 {e!r}（应为 Element）")
    if e.kind not in ELEMENT_WHITELIST:
        raise ValueError(
            f"非法元素 kind={e.kind!r}（元素白名单: {'/'.join(ELEMENT_WHITELIST)}）"
        )
    if e.fill:
        _check_color(e.fill, f"{e.kind}.fill")
    if e.stroke:
        _check_color(e.stroke, f"{e.kind}.stroke")
    if e.d and not _PATH_DATA.match(e.d):
        raise ValueError(f"{e.kind}.d: 非法路径数据")
    if e.href:
        _check_href(e.href, f"{e.kind}.href")
    # 几何/样式标量：全部进入序列化（f-string 拼接或算术），必须为有限数值
    for name in ("x", "y", "w", "h", "cx", "cy", "r", "x1", "y1", "x2", "y2",
                 "rx", "stroke_width", "opacity", "dashoffset"):
        _check_number(getattr(e, name), f"{e.kind}.{name}")
    if not 0.0 <= e.opacity <= 1.0:
        raise ValueError(f"{e.kind}.opacity: 应在 0..1，得到 {e.opacity!r}")
    if e.rotate is not None:
        _check_number(e.rotate, f"{e.kind}.rotate")
    if e.dasharray:
        try:
            [float(p) for p in e.dasharray.replace(",", " ").split()]
        except ValueError:
            raise ValueError(f"{e.kind}.dasharray: 应为数值串，得到 {e.dasharray!r}") from None
    if e.text is not None:
        if e.kind != "text":
            raise ValueError(f"{e.kind}: TextBlock 仅 text 元素可持有（元素白名单语义）")
        _check_textblock(e.text, f"{e.kind}.text")
    if e.kind == "text" and e.text is None:
        raise ValueError("text: 元素必须携带 TextBlock（text=None 非法）")
    if e.children and e.kind != "g":
        raise ValueError(f"{e.kind}: children 仅 g 组合元素可持有（元素白名单语义）")
    for child in e.children:
        _validate_element(child)


__all__ = [
    "ELEMENT_WHITELIST",
    "SEMANTIC_COLOR_SLOTS",
    "SPECS",
    "SQUARE",
    "WIDE16X9",
    "CanvasDoc",
    "Element",
    "TextBlock",
    "spec_size",
    "validate_doc",
]
