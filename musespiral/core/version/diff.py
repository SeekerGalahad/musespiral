"""版本 diff（E-6，L2 纯）：difflib.unified_diff → 结构化 hunks + HTML 红删绿增。

设计正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.7「I4 版本 diff 视图」：
版本池（F43，`<article>.md.bak.<YYYYMMDD-HHMMSS>`）里任意两版对比，旧版删除行
绯红删除线、新版新增行竹绿。

**本模块零 I/O、零网络、零三方依赖**（架构门规则 3 的域层纪律）——它只吃两段
文本，吐结构化结果与 HTML 字符串。哪两段文本（当前版 / 池里第 N 版）由
`services/version_service` 决定，落盘与恢复也在那里。

三层产物（同一份 DiffResult 的三种投影，避免前端各自解析 unified diff）：
  1. `unified_diff_lines`——原生 unified diff 文本行（`---` / `+++` / `@@` /
     ` `/`-`/`+` 前缀），给人读与测试对拍；
  2. `DiffResult`——`hunks`（含**行号**的 ctx/del/ins 行），供 API JSON；
  3. `render_diff_html`——服务端渲染的 HTML（`<del class="dl-del">` 绯红删除线 /
     `<ins class="dl-add">` 竹绿），内容一律 `html.escape`（文章正文是不可信输入：
     正文里写 `<script>` 只会以文本形态出现在 diff 里）。

口径说明：按行切分（`str.splitlines()`），**行尾换行差异不呈现**——文章是散文，
末尾是否多一个换行不是「改了哪一段」；"\\ No newline at end of file" 标记因此
不会出现。行号从 1 起算；ctx 行两侧行号并列，del 只占旧侧、ins 只占新侧。

**行内字符级精化（2026-11-21 批4 复核④修复）**：对比仍按行（hunks/行号/
added/removed 计数不变），但渲染替换块时对配对的 del/ins 行做字符级
SequenceMatcher——「改一个字只高亮那个字」，未变字符以普通文本呈现（GitHub
word-diff 的字符版）。判例与阈值和前端编辑器 edDiffRows（editor.js，单行
≤1500 字符才逐字）同语义；两端各自实现（Python/JS 不能互调），阈值改值两处
同步。全部 diff 消费面（E-6 版本对比 / REFINE-ITER 打磨史 / 插件 run 版本
对照 / N-5 物料包并排预览）共用本引擎，一次升级全体生效。
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

# 上下文行数缺省（与 unified diff 惯例一致：改动前后各三行）
DEFAULT_CONTEXT = 3

# 首行摘要展示宽度（列表里每版一行，超出截断）
SUMMARY_WIDTH = 60

# 行内字符级精化的单行长度上限（与 editor.js edDiffRows 的 1500 同值——长行
# 逐字会碎成噪声且 O(n·m) 放大；超限退回整行红删绿增）
INLINE_REFINE_MAX_CHARS = 1500

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# 行四态（ctx=上下文；del=仅旧版有；ins=仅新版有）
CTX = "ctx"
DEL = "del"
INS = "ins"


@dataclass(frozen=True)
class DiffRow:
    """diff 一行：kind ∈ ctx|del|ins；old_no/new_no 为该侧行号（缺失侧为 None）。"""

    kind: str
    text: str
    old_no: int | None
    new_no: int | None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text,
                "old_no": self.old_no, "new_no": self.new_no}


@dataclass(frozen=True)
class DiffHunk:
    """一个 `@@` 段：header 原文 + 行列表。"""

    header: str
    rows: tuple[DiffRow, ...]

    def to_dict(self) -> dict:
        return {"header": self.header, "rows": [r.to_dict() for r in self.rows]}


@dataclass(frozen=True)
class DiffResult:
    """两版对比结果：hunks + 统计（added/removed）+ 是否完全相同。

    added/removed 按**行**计（与 unified diff 的 +/- 行数同口径，不折算「改动
    一处 = 一删一增」）。两版相同 → hunks 为空元组、identical=True。
    """

    hunks: tuple[DiffHunk, ...]
    added: int
    removed: int
    identical: bool

    def to_dict(self) -> dict:
        return {
            "hunks": [h.to_dict() for h in self.hunks],
            "added": self.added,
            "removed": self.removed,
            "identical": self.identical,
        }


def summarize(text: str, width: int = SUMMARY_WIDTH) -> str:
    """首行摘要：首个非空且非 frontmatter 界定符的行（超宽截断加省略号）。

    只用于列表展示（「这一版开头是什么」），不参与对比口径。
    """
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "---":
            continue
        return s[:width] + "…" if len(s) > width else s
    return "（空文件）"


def unified_diff_lines(
    old_text: str,
    new_text: str,
    from_label: str = "旧版",
    to_label: str = "新版",
    context: int = DEFAULT_CONTEXT,
) -> list[str]:
    """两段文本 → unified diff 文本行（`lineterm=""`，无尾随换行差异噪声）。"""
    return list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
            n=context,
        )
    )


def diff_result(
    old_text: str,
    new_text: str,
    from_label: str = "旧版",
    to_label: str = "新版",
    context: int = DEFAULT_CONTEXT,
) -> DiffResult:
    """两段文本 → 结构化 DiffResult（hunks 带行号 + 增删行数统计）。

    `@@ -a,b +c,d @@` 头给出两侧起始行号，之后逐行累加：ctx 两侧同增，del 只增
    旧侧，ins 只增新侧（与 unified diff 的行号语义逐字一致）。
    """
    lines = unified_diff_lines(old_text, new_text, from_label, to_label, context)
    hunks: list[DiffHunk] = []
    rows: list[DiffRow] = []
    header = ""
    old_no = new_no = 0
    added = removed = 0

    def _flush() -> None:
        nonlocal rows
        if header:
            hunks.append(DiffHunk(header=header, rows=tuple(rows)))
        rows = []

    for line in lines:
        if line.startswith("---") or line.startswith("+++"):
            continue  # 文件标签行（hunk 之外；标签由调用方展示）
        match = _HUNK_HEADER_RE.match(line)
        if match is not None:
            _flush()
            header = line
            old_no, new_no = int(match.group(1)), int(match.group(2))
            continue
        if not header:  # pragma: no cover - unified_diff 不会在此之前吐行
            continue
        prefix, text = (line[:1], line[1:]) if line else (" ", "")
        if prefix == "-":
            rows.append(DiffRow(DEL, text, old_no, None))
            old_no += 1
            removed += 1
        elif prefix == "+":
            rows.append(DiffRow(INS, text, None, new_no))
            new_no += 1
            added += 1
        else:  # " " 上下文行（含空行：unified_diff 给单个空格）
            rows.append(DiffRow(CTX, text, old_no, new_no))
            old_no += 1
            new_no += 1
    _flush()

    return DiffResult(
        hunks=tuple(hunks),
        added=added,
        removed=removed,
        identical=not hunks,
    )


def _partner_map(rows: tuple[DiffRow, ...]) -> dict[int, int]:
    """替换块内配对 del/ins 行：返回 行下标 → 配对行下标。

    unified diff 的替换块形状恒为「连续 del 行 + 紧随连续 ins 行」（difflib
    不会交错输出）；按出现顺序两两配对（第 k 条 del ↔ 第 k 条 ins），两侧
    多余的行不配对（纯删/纯增语义，整行包裹才是对的形状）。
    """
    partners: dict[int, int] = {}
    n = len(rows)
    i = 0
    while i < n:
        if rows[i].kind != DEL:
            i += 1
            continue
        j = i
        while j < n and rows[j].kind == DEL:
            j += 1
        k = j
        while k < n and rows[k].kind == INS:
            k += 1
        for p in range(min(j - i, k - j)):
            partners[i + p] = j + p
            partners[j + p] = i + p
        i = k
    return partners


def _inline_body(old_line: str, new_line: str, kind: str) -> str | None:
    """配对替换行的行内字符级精化（批4 复核④：「改一个字只高亮那个字」）。

    对 del/ins 行各返回**包裹在单个 `.ver-tx` 容器里的混合片段**——本侧未变
    字符为普通文本、本侧实改字符包 `<del class="dl-del">` / `<ins class="dl-add">`
    （容器作 flex 子元素，内层片段自然行内排版）。返回 None = 不精化（调用方
    走整行包裹的既有形态）：
      - 任一侧为空 / 两行相同 / 任一侧超 `INLINE_REFINE_MAX_CHARS`；
      - 本侧没有任何实改片段（改动全在对侧——整行包裹才是可读形状）；
      - 整行无共同字符（精化结果与整行包裹逐字节同形，退回外层路径）。
    相邻同向片段合并（SequenceMatcher 的 replace+delete 连发只出一个标签）。
    """
    if not old_line or not new_line or old_line == new_line:
        return None
    if len(old_line) > INLINE_REFINE_MAX_CHARS or len(new_line) > INLINE_REFINE_MAX_CHARS:
        return None
    matcher = difflib.SequenceMatcher(None, old_line, new_line, autojunk=False)
    tag, cls = ("del", "dl-del") if kind == DEL else ("ins", "dl-add")
    parts: list[str] = []
    plain = ""
    changed = False
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            plain += old_line[i1:i2]
            continue
        seg = old_line[i1:i2] if kind == DEL else new_line[j1:j2]
        if not seg:
            continue  # 该操作在本侧无对应片段（纯增对 del 行 / 纯删对 ins 行）
        changed = True
        if plain:
            parts.append(html.escape(plain))
            plain = ""
        head, tail = f'<{tag} class="{cls}">', f"</{tag}>"
        if parts and parts[-1].startswith(head):
            # 相邻同向片段合并：剥外层标签拼接（减少碎标签噪声）
            parts[-1] = f"{head}{parts[-1][len(head):-len(tail)]}{html.escape(seg)}{tail}"
        else:
            parts.append(f"{head}{html.escape(seg)}{tail}")
    if plain:
        parts.append(html.escape(plain))
    if not changed:
        return None
    if len(parts) == 1 and parts[0].startswith(f"<{tag} "):
        return None  # 整行实改且无共同字符 → 整行包裹同形，走外层路径
    return f'<span class="ver-tx">{"".join(parts)}</span>'


def render_diff_html(
    result: DiffResult,
    from_label: str = "旧版",
    to_label: str = "新版",
    empty_text: str = "两版内容相同——没有可展示的差异。",
) -> str:
    """DiffResult → 可直插页面的 HTML（全部文本 `html.escape`，零内联脚本）。

    结构：顶部一行图例（`− 旧版标签` / `+ 新版标签` / `+N 行 −M 行`，对应设计
    §3.7 示意图的「── v3（旧）── / ++ v5（新）──」段头），其后每段一个
    `.ver-hunk`（`@@` 头 + 行），每行 `.ver-row.ctx|del|ins` 含两侧行号槽与文本槽；
    删除行包 `<del class="dl-del">`（绯红 + 删除线）、新增行包
    `<ins class="dl-add">`（竹绿），着色与装饰在 ui.css。

    替换块（del 行紧跟 ins 行）在两侧都可精化时按对升级为**行内字符级**
    红删绿增（`_inline_body`）——共同字符以普通文本呈现、实改字符才着色；
    纯删/纯增/长行/无共同字符的行保持整行包裹（同批两侧要么都精化要么都
    整行，不出现半精化的错位对）。
    """
    if result.identical:
        return f'<p class="action-hint ver-same">{html.escape(empty_text)}</p>'

    out: list[str] = [
        '<div class="ver-diff">',
        '<div class="ver-legend">'
        f'<span class="dl-del">− {html.escape(from_label)}</span>'
        f'<span class="dl-add">+ {html.escape(to_label)}</span>'
        f'<span class="ver-stat">+{result.added} 行 / −{result.removed} 行</span>'
        "</div>",
    ]
    for hunk in result.hunks:
        out.append('<div class="ver-hunk">')
        out.append(f'<div class="ver-hh">{html.escape(hunk.header)}</div>')
        partners = _partner_map(hunk.rows)
        bodies: dict[int, str | None] = {}  # 行内精化体缓存（del 行先算，ins 行复用同对判定）
        for idx, row in enumerate(hunk.rows):
            old_no = "" if row.old_no is None else str(row.old_no)
            new_no = "" if row.new_no is None else str(row.new_no)
            text = html.escape(row.text) or "&nbsp;"
            body: str | None = None
            pidx = partners.get(idx)
            if row.kind in (DEL, INS) and pidx is not None:
                if idx in bodies:
                    body = bodies[idx]
                else:
                    old_line = row.text if row.kind == DEL else hunk.rows[pidx].text
                    new_line = hunk.rows[pidx].text if row.kind == DEL else row.text
                    ref_del = _inline_body(old_line, new_line, DEL)
                    ref_ins = _inline_body(old_line, new_line, INS)
                    # 两侧同生同死：一侧不可精化（如共同字符为零/超长）则整对整行包裹
                    body = ref_del if row.kind == DEL else ref_ins
                    bodies[pidx] = (ref_ins if row.kind == DEL else ref_del) if body else None
            if body is None:
                if row.kind == DEL:
                    body = f'<del class="dl-del">{text}</del>'
                elif row.kind == INS:
                    body = f'<ins class="dl-add">{text}</ins>'
                else:
                    body = f'<span class="ver-tx">{text}</span>'
            out.append(
                f'<div class="ver-row {row.kind}">'
                f'<span class="ver-no">{old_no}</span>'
                f'<span class="ver-no">{new_no}</span>'
                f"{body}</div>"
            )
        out.append("</div>")
    out.append("</div>")
    return "".join(out)
