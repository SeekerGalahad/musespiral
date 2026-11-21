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
        for row in hunk.rows:
            old_no = "" if row.old_no is None else str(row.old_no)
            new_no = "" if row.new_no is None else str(row.new_no)
            text = html.escape(row.text) or "&nbsp;"
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
