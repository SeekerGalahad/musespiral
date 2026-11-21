"""对照审阅（F8）— 原笔记 ↔ 文章并排 diff，生成单文件 HTML 供人工审核。

段落对齐引擎与 aihabits.extract_habits 同源（空行分段 + difflib.SequenceMatcher），
此前只在发布后 ai learn 用，本期前置到人审之前：审核者直接看四类着色段落，
而不是肉眼重读全文。纯本地生成，不调 AI、不调任何 API；CLI 接线见集成阶段。
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

# 段落四类（与设计文档 §十三 F8 统计口径一致）
KEEP = "keep"      # 保留：两侧未变
MODIFY = "modify"  # 修改：两侧黄色描边 + 段内词级 <del>/<ins> 高亮
ADD = "add"        # 新增：仅文章有 → 右侧绿底
DELETE = "delete"  # 删除：仅原笔记有 → 左侧红底

# 词级 diff 的词元切分：CJK 逐字一词、其余非空白连排一词、空白单独成词
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[^\s\u4e00-\u9fff]+|\s+")


@dataclass
class _Row:
    """一行对照：kind=四类之一，left=原笔记段，right=文章段（缺失侧为空串）。"""

    kind: str
    left: str = ""
    right: str = ""


def _paragraphs(text: str) -> list[str]:
    """按空行切段（Markdown 段落），返回去首尾空白的非空段列表。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _tokens(text: str) -> list[str]:
    """切词元：中文逐字、拉丁/数字/符号连排成词、空白单独成词。"""
    return _TOKEN_RE.findall(text)


def _token_diff_html(old: str, new: str) -> tuple[str, str]:
    """段内词级差异：返回（左列 HTML，右列 HTML）。

    左列把被删词包进 <del>，右列把新增词包进 <ins>，共同词两侧原样；文本先转义。
    """
    a, b = _tokens(old), _tokens(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    left_parts: list[str] = []
    right_parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            seg = html.escape("".join(a[i1:i2]))
            left_parts.append(seg)
            right_parts.append(seg)
            continue
        if tag in ("delete", "replace"):
            left_parts.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
        if tag in ("insert", "replace"):
            right_parts.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
    return "".join(left_parts), "".join(right_parts)


def _align(note_paras: list[str], article_paras: list[str]) -> list[_Row]:
    """段落对齐：SequenceMatcher 逐 opcode 映射为四类行。

    replace 块内按出现顺序逐段配对为「修改」，多出的旧段记删除、新段记新增。
    """
    rows: list[_Row] = []
    sm = difflib.SequenceMatcher(a=note_paras, b=article_paras, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        olds = note_paras[i1:i2]
        news = article_paras[j1:j2]
        if tag == "equal":
            rows.extend(_Row(KEEP, o, n) for o, n in zip(olds, news))
        elif tag == "delete":
            rows.extend(_Row(DELETE, left=o) for o in olds)
        elif tag == "insert":
            rows.extend(_Row(ADD, right=n) for n in news)
        else:  # replace
            rows.extend(_Row(MODIFY, o, n) for o, n in zip(olds, news))
            rows.extend(_Row(DELETE, left=o) for o in olds[len(news):])
            rows.extend(_Row(ADD, right=n) for n in news[len(olds):])
    return rows


def _stats(rows: list[_Row], note_paras: list[str]) -> dict[str, object]:
    """统计条数据：段落四类计数 + 字符保留率（未变段字符和 / 原文总字符）。

    原文为空（无段落）时保留率记 None，页面显示「—」而不是除零。
    """
    counts = {
        "keep": sum(1 for r in rows if r.kind == KEEP),
        "modify": sum(1 for r in rows if r.kind == MODIFY),
        "add": sum(1 for r in rows if r.kind == ADD),
        "delete": sum(1 for r in rows if r.kind == DELETE),
    }
    total = sum(len(p) for p in note_paras)
    retained = sum(len(r.left) for r in rows if r.kind == KEEP)
    retention: float | None = retained / total * 100 if total else None
    return {**counts, "retention": retention}


_STYLE = """\
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; }
.stats { position: sticky; top: 0; z-index: 1; background: #1f2937; color: #fff;
         padding: 10px 16px; font-size: 14px; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th { background: #f3f4f6; padding: 6px 12px; border: 1px solid #e5e7eb;
     font-size: 13px; color: #6b7280; }
td { vertical-align: top; width: 50%; padding: 8px 12px; border: 1px solid #e5e7eb;
     font-size: 14px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
td.delete { background: #fdecec; }
td.add { background: #e8f7ec; }
td.modify { outline: 2px solid #f5b940; outline-offset: -2px; }
del { background: #ffd7d7; color: #b91c1c; }
ins { background: #c9f0d3; color: #15803d; text-decoration: none; }
"""


def _row_tr(row: _Row) -> str:
    """把一行对照渲染为 <tr>：类名与四类一致（keep/modify/add/delete）。"""
    if row.kind == KEEP:
        left, right = html.escape(row.left), html.escape(row.right)
        return f'<tr><td class="keep">{left}</td><td class="keep">{right}</td></tr>'
    if row.kind == MODIFY:
        left, right = _token_diff_html(row.left, row.right)
        return f'<tr><td class="modify">{left}</td><td class="modify">{right}</td></tr>'
    if row.kind == DELETE:
        return f'<tr><td class="delete">{html.escape(row.left)}</td><td></td></tr>'
    return f'<tr><td></td><td class="add">{html.escape(row.right)}</td></tr>'  # ADD


def build_review_html(note_path: str | Path, article_path: str | Path) -> str:
    """生成原笔记 ↔ 文章的单文件对照 HTML（无外部依赖），返回完整 HTML 字符串。

    左列原笔记、右列文章：删除段左侧红底、新增段右侧绿底、修改段两侧黄色描边
    且段内词级 <del>/<ins> 高亮；顶部统计条给出四类段落计数与字符保留率。
    """
    note_body = frontmatter.load(Path(note_path)).content.strip()
    article_body = frontmatter.load(Path(article_path)).content.strip()
    note_paras = _paragraphs(note_body)
    article_paras = _paragraphs(article_body)
    rows = _align(note_paras, article_paras)
    s = _stats(rows, note_paras)
    retention = "—" if s["retention"] is None else f"{s['retention']:.1f}%"

    trs = [
        "<tr><th>原笔记</th><th>文章</th></tr>",
    ]
    trs.extend(_row_tr(row) for row in rows)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>对照审阅 · {html.escape(Path(article_path).stem)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f'<div class="stats">段落统计：保留 {s["keep"]} · 修改 {s["modify"]} · '
        f'新增 {s["add"]} · 删除 {s["delete"]} · 字符保留率 {retention}</div>\n'
        f"<table>\n{''.join(trs)}\n</table>\n</body>\n</html>\n"
    )
