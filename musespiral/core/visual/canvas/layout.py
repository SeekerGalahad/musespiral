"""F70 文本测量分行 — Pillow font.getlength 纯函数（cover.py:322-339 _wrap_text 提升）。

- 显式 ``\\n`` 恒为断行点；ASCII 词边界优先（cover 版逐字累积的提升点）；
  CJK 无空格逐字累积——黄金三态（F70-T3）；
- 单词宽于行宽时回退逐字硬拆（不溢出优先于词边界）；
- layout_document 把测量结果回填进 TextBlock.lines——行结构固化进文档，
  前端渲染不重排（F70 选型弱点对策）；
- relayout_vertical 在回填后补垂直重排（D-1）：多行 text 按实际占高
  下推后续同列元素，纠正模板「layout 前构造、按字号倍数推算 y」的假定。
"""
from __future__ import annotations

from PIL import ImageFont

from .model import CanvasDoc, Element

# CJK 字体候选（与 core/visual/cover.py:35-41 同一环境事实清单——L2 纯层不
# import cover，免拉入文章取材依赖链；字体清单非业务逻辑，双份有注释锚）
_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)

# 安全边距（cards/templates.SAFE_MARGIN=96 同源；元素无 w 时的兜底行宽）
_DEFAULT_MARGIN = 96


def load_font(px: int) -> ImageFont.ImageFont:
    """按像素取 CJK 字体：候选逐一尝试，全缺失退 Pillow 默认位图字体
    （cover._load_font 同语义；测量/重绘共用同一份，保证 B/C 一致性来源）。"""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _is_word_char(ch: str) -> bool:
    """ASCII 词字符（字母/数字/连字符类连接符）——词边界换行的成词单位。"""
    return ch.isascii() and (ch.isalnum() or ch in "_-'/&+")


def _tokenize(seg: str) -> list[str]:
    """段 → 词单元：ASCII 连续词符合成一个词，其余（CJK/标点/空格）各自成单元。"""
    units: list[str] = []
    buf = ""
    for ch in seg:
        if _is_word_char(ch):
            buf += ch
        else:
            if buf:
                units.append(buf)
                buf = ""
            units.append(ch)
    if buf:
        units.append(buf)
    return units


def _hard_split(unit: str, font, max_width: int) -> list[str]:
    """超宽单词逐字硬拆（不溢出优先于词边界）。"""
    out: list[str] = []
    cur = ""
    for ch in unit:
        if cur and font.getlength(cur + ch) > max_width:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out


def wrap_text(text: str, font, max_width: int) -> list[str]:
    """测量分行（纯函数）：显式 ``\\n`` 恒断 → 段内贪心装填（词边界/CJK 逐字）。

    保证：除「单字符本身宽于行宽」外，每行 ``font.getlength(line) <= max_width``。
    """
    if not text:
        return []
    lines: list[str] = []
    for seg in text.split("\n"):
        if not seg:
            lines.append("")  # 显式 \n 的空段保位（恒为断行点）
            continue
        cur = ""
        for unit in _tokenize(seg):
            pieces = (
                _hard_split(unit, font, max_width)
                if font.getlength(unit) > max_width
                else [unit]
            )
            for piece in pieces:
                if cur and font.getlength(cur + piece) > max_width:
                    lines.append(cur.rstrip())
                    cur = piece.lstrip()
                else:
                    cur += piece
        lines.append(cur.rstrip())
    return lines


def _iter_elements(elements: list[Element]):
    """深度遍历（含 g 子元素）。"""
    for e in elements:
        yield e
        if e.children:
            yield from _iter_elements(e.children)


def layout_document(doc: CanvasDoc, *, force: bool = False) -> CanvasDoc:
    """对全部 text 元素测量回填 block.lines（F70 调用流第 3 步；就地回填返回 doc）。

    - max_width 取值序：block.max_width > 元素 w > 画布宽 - 2×安全边距；
    - 幂等：已回填元素跳过（force=True 强制重测——spec 切换重渲用）。
    """
    default_w = max(doc.width - 2 * _DEFAULT_MARGIN, 1)
    for e in _iter_elements(doc.elements):
        if e.kind != "text" or e.text is None:
            continue
        block = e.text
        if block.lines is not None and not force:
            continue
        max_w = block.max_width or (round(e.w) if e.w > 0 else default_w)
        block.lines = wrap_text(block.text, load_font(block.font_size), max_w)
    return doc


def relayout_vertical(doc: CanvasDoc) -> CanvasDoc:
    """D-1 垂直重排：layout 回填 lines 后按实际占高下推**同列紧邻** text 元素。

    缺陷正典（验收报告 2026-11-17 §二 D-1）：模板在 layout **之前**构造 doc，
    副文 y 按字号倍数硬编码（如 ``title_y + round(fs*2.6)``，假定标题恒 1 行）；
    标题 5 行时副文落进标题内部（xhs-3x4 实测重叠 280px）。

    复核判例（同报告 2026-11-17 复核）——初版「x 区间有重叠即下推」过宽，误伤
    两类版式，故本版收紧为**同列盒（x 与 w 逐值相等）**才构成下推关系：

    - **points-three 标签列不误推**：标签元素 x 相同但 w=label_w 远窄于标题
      w=整行，属不同列盒——标签行须各自对齐自己的条形行（templates.py:185），
      不能被标题下推（初版致 wide16x9 三标签齐并 y、二跑继续漂移）；
    - **不推出画布**：下压目标（前元素占用底）若 > 画布高，则放弃本元素下推
      （保留原 y）——mp 封面 383px 高、5 行标题本身已越界，把副文再推到
      431px 会让原本可见的副文整块不可见（初版 Pillow 实测 muted 像素 613→0）。

    算法：
    - 列盒键 = ``(round(x), round(w))``（w≤0 时按一字宽兜底，视作独立列）；
    - 占用高度 = ``len(lines) × font_size × line_height``（y 为首行基线，
      与 svg.py tspan dy / pillow_render 逐行绘制同锚定语义）；
    - 对每个 text 元素，取**同列盒、文档序在前、其占用带覆盖本元素 y** 的最大
      占用底为下压目标 ``floor``；仅当 ``floor <= doc.height``（基线仍在画布内）
      才落盘，否则保留原 y（不制造新越界）；
    - 非文本元素（rect/line/path…）不参与：既不下推也不作依据（版式骨架不动）。

    回归锚：单行文本占用高度不越过模板原间距 → 1 行场景全部元素 y 不变；
    同稿重跑幂等（下推后本元素 y 即恰等于前元素占用底，续跑不再位移）。
    """
    placed: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for e in _iter_elements(doc.elements):
        if e.kind != "text" or e.text is None:
            continue  # 非文本元素不动也不作依据（保底约束）
        b = e.text
        n = len(b.lines) if b.lines is not None else 1
        key = (round(e.x), round(e.w) if e.w > 0 else round(float(b.font_size)))
        floor = e.y
        for p_y, p_bottom in placed.get(key, ()):
            if p_y <= e.y < p_bottom:  # 本元素落在前元素占用带内 → 下推候选
                floor = max(floor, p_bottom)
        if floor <= doc.height:  # 基线仍在画布内才落盘（不制造新越界）
            e.y = floor
        placed.setdefault(key, []).append((e.y, e.y + n * b.font_size * b.line_height))
    return doc


__all__ = ["layout_document", "load_font", "relayout_vertical", "wrap_text"]
