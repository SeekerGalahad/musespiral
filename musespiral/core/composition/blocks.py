"""创作台最小裁决单元 Block（第十八期 §2.1/§2.2 正典）。

块 = Markdown 结构片段 + 会话稳定 id + 血统 origin：
  - id 由锚协议保证跨轮稳定（协议解析在 protocol.py，锚语义在本文件）；
  - kind ∈ heading|para|code|quote|image（AI 输出协议与 draft.md 同一分类集）；
  - origin ∈ source_ref|ai|human——溯源与归因用（source_ref 预留给素材直引块）。

draft.md 双向映射（§2.2）：块以 HTML 注释锚 ``<!--b:id-->`` 持久化——
渲染不可见、编辑器容忍、round-trip 稳定。人工直接改文件后重进会话：
锚未破坏的块保留 id（及其裁决历史），锚破坏的块按新块处理（解析时
拿不到锚 → fresh id）。

L2 Domain 纯逻辑零 I/O：只依赖 re/dataclasses（架构门 test_composition
锁定：不 import pathlib/os/io 等，不出现 open()）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

# 块类型全集（AI 输出协议 kind 与 draft.md 解析共用；顺序即展示序）
KINDS: tuple[str, ...] = ("heading", "para", "code", "quote", "image")

# 块血统（§2.1 origin）
ORIGIN_SOURCE = "source_ref"
ORIGIN_AI = "ai"
ORIGIN_HUMAN = "human"

# 锚行：<!--b:id-->（独立成行，容许行尾空白；id 字符集 [A-Za-z0-9_-]+）
ANCHOR_PREFIX = "<!--b:"
ANCHOR_SUFFIX = "-->"
ANCHOR_RE = re.compile(r"^<!--b:([A-Za-z0-9_-]+)-->[ \t]*$")

# 标题行（# ~ ######）——与 discovery/scanner._HEADING_RE 同口径
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")

# 自动 id 形态 b<n>（n 连续递增，避开会话内已占用 id）
_AUTO_ID_RE = re.compile(r"^b(\d+)$")


@dataclass
class Block:
    """最小裁决单元：id 全会话稳定，text 为该块的 Markdown 原文。"""

    id: str
    kind: str
    text: str
    origin: str = ORIGIN_AI

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "text": self.text, "origin": self.origin}

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        kind = str(data.get("kind") or "para")
        return cls(
            id=str(data.get("id") or ""),
            kind=kind if kind in KINDS else "para",
            text=str(data.get("text") or ""),
            origin=str(data.get("origin") or ORIGIN_AI),
        )


def fresh_id(taken: set[str]) -> str:
    """生成会话内未占用的自动 id（b<n>，n = 既有 b 系最大序 + 1）。"""
    n = 0
    for existing in taken:
        m = _AUTO_ID_RE.match(existing)
        if m:
            n = max(n, int(m.group(1)))
    candidate = f"b{n + 1}"
    while candidate in taken:
        n += 1
        candidate = f"b{n + 1}"
    return candidate


def retag(blocks: list[Block], taken: set[str] | None = None) -> list[Block]:
    """给缺 id（或 id 冲突）的块补自动 id——锚破坏按新块的落点。

    taken 之外的 id 视为合法保留（人工/历史 id 不重排）；id 为空或与
    taken+前序冲突者发新 id。
    """
    used = set(taken or set())
    out: list[Block] = []
    for b in blocks:
        if b.id and b.id not in used:
            out.append(b)
            used.add(b.id)
        else:
            out.append(replace(b, id=fresh_id(used)))
            used.add(out[-1].id)
    return out


# ---- Markdown ↔ Blocks（draft.md 双向映射，§2.2）------------------------


def blocks_from_markdown(
    text: str, default_origin: str = ORIGIN_AI, avoid: set[str] | None = None
) -> list[Block]:
    """把 draft.md 正文解析为块序列（无 frontmatter 概念——服务层负责剥离）。

    - 锚行 ``<!--b:id-->`` 绑定其后的首个块（连续多锚取最靠近者）；
    - 无锚的块 = 锚破坏/人工新写 → fresh id（「按新块处理」）；avoid 传入
      会话既有 id 集（draft+轮次），防锚破坏后的裸文本碰巧撞回旧 id——
      撞回即错并裁决历史，正是协议要防的；
    - origin 统一取 default_origin——draft.md 只持久化 id（真稿唯一表达
      不夹带元数据），血统由服务层用会话轮次记录按 id 回填；
    - 分类按片段首行：围栏代码块（```/~~~ 至闭合行，未闭合吃到文末）、
      标题行、> 引用段（连续 > 行）、![ 图片行、其余为 para（至空行或
      下一结构起点）。
    """
    lines = text.split("\n")
    blocks: list[Block] = []
    seen_ids: set[str] = set()
    fresh_pool: set[str] = set(avoid or set())  # fresh id 避让池（会话既有 id）
    pending_id: str | None = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        m = ANCHOR_RE.match(stripped)
        if m:
            pending_id = m.group(1)  # 连续多锚：最靠近块者生效
            i += 1
            continue
        frag: list[str]
        lead = lines[i].lstrip()
        if lead.startswith("```") or lead.startswith("~~~"):
            kind = "code"
            fence = lead[:3]
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith(fence):
                j += 1
            frag = lines[i : j + 1] if j < len(lines) else lines[i:]
            i = j + 1 if j < len(lines) else len(lines)
        elif _HEADING_RE.match(lines[i]):
            kind = "heading"
            frag = [lines[i]]
            i += 1
        elif lead.startswith(">"):
            kind = "quote"
            j = i
            while j < len(lines) and lines[j].strip() and lines[j].lstrip().startswith(">"):
                j += 1
            frag = lines[i:j]
            i = j
        elif lead.startswith("!["):
            kind = "image"
            frag = [lines[i]]
            i += 1
        else:
            kind = "para"
            j = i
            while j < len(lines):
                s = lines[j].strip()
                if not s or ANCHOR_RE.match(s) or _HEADING_RE.match(lines[j]):
                    break
                lj = lines[j].lstrip()
                if lj.startswith((">", "![", "```", "~~~")):
                    break
                j += 1
            frag = lines[i:j]
            i = j
        block_text = "\n".join(frag).rstrip()
        if not block_text:
            continue
        if pending_id and pending_id not in seen_ids:
            blocks.append(Block(pending_id, kind, block_text, default_origin))
            seen_ids.add(pending_id)
        else:
            new_id = fresh_id(fresh_pool | seen_ids)
            blocks.append(Block(new_id, kind, block_text, default_origin))
            seen_ids.add(new_id)
        pending_id = None
    return blocks


def markdown_from_blocks(blocks: list[Block]) -> str:
    """块序列 → draft.md 正文（每块前置锚行，块间空行分隔）。

    round-trip 契约：blocks_from_markdown(markdown_from_blocks(bs)) 的
    (id, kind, text) 与 bs 逐块相等（test_composition 锚定）。
    """
    parts = [f"{ANCHOR_PREFIX}{b.id}{ANCHOR_SUFFIX}\n{b.text}" for b in blocks if b.text]
    return "\n\n".join(parts)
