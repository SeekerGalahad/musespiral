"""稳定块标识协议（第十八期 §2.2 正典——核心技术难点）。

问题：AI 再生成会整篇重写，朴素 diff 丢失「同一块」的跨轮映射，裁决
无法附着。

双层协议：
1. AI 输出结构化定界：compose 任务按 ``<<<block id?:… kind:…>>>`` 定界
   分块输出；改写上轮块时携带上轮 id 作锚，新内容省略 id。解析器容错：
   定界缺失时整段降级为单 para 块（不报错）。
2. 无锚回退匹配：无锚块 → 与上轮块做 content-hash 精确匹配 → 命中即
   继承 id；未命中 → difflib 相似度 ≥0.6 的最高分且未占用者继承；
   否则视为新块。

安全决策（铁律）：**宁可当新块，不可错并**——匹配歧义（多候选同分）
一律新块。裁决附错块比多裁决一次代价高。

L2 Domain 纯逻辑零 I/O（hashlib/difflib/re 均为非 I/O stdlib）。
"""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

from .blocks import Block, fresh_id

# 相似度回退的命中下限（正典 §2.2：≥0.6）
SIMILARITY_THRESHOLD = 0.6

# 浮点并列判定容差（ratio 精确并列常见：相同文本改一两字）
_SCORE_EPS = 1e-9

# 定界符行：<<<block id:b3 kind:para>>>（id 可省略；kind 容错缺省 para；
# >>> 之后同行残留文字容错收作该块首行——模型不总把定界符独立成行）
_DELIM_RE = re.compile(r"^<<<\s*block\b(?P<attrs>[^>]*?)>>>(?P<rest>.*)$")
_ID_ATTR_RE = re.compile(r"\bid[ \t]*:[ \t]*([A-Za-z0-9_-]+)")
_KIND_ATTR_RE = re.compile(r"\bkind[ \t]*:[ \t]*([A-Za-z]+)")


@dataclass
class ParsedBlock:
    """AI 输出的一段：anchor=AI 携带的上轮 id 锚（未携带为 None）。"""

    anchor: str | None
    kind: str
    text: str


def parse_ai_output(text: str) -> list[ParsedBlock]:
    """解析 AI 结构化定界输出（容错）。

    - 全文无任何定界符 → 整段降级为单 para 块（§2.2 定界缺失降级）；
    - kind 非法/缺失 → para；空内容段跳过；
    - 定界符之外的零散文（首个定界符之前的前言）忽略——协议指令要求
      模型不输出协议说明，残留前言视为噪声。
    """
    lines = text.split("\n")
    segments: list[tuple[str | None, str, list[str]]] = []
    current: tuple[str | None, str, list[str]] | None = None
    saw_delim = False
    for line in lines:
        m = _DELIM_RE.match(line.strip())
        if m:
            saw_delim = True
            if current is not None:
                segments.append(current)
            attrs = m.group("attrs")
            id_m = _ID_ATTR_RE.search(attrs)
            kind_m = _KIND_ATTR_RE.search(attrs)
            kind = kind_m.group(1).lower() if kind_m else "para"
            if kind not in ("heading", "para", "code", "quote", "image"):
                kind = "para"
            rest = m.group("rest").strip()
            current = (id_m.group(1) if id_m else None, kind, [rest] if rest else [])
        elif current is not None:
            current[2].append(line)
    if current is not None:
        segments.append(current)
    if not saw_delim:
        body = text.strip()
        return [ParsedBlock(None, "para", body)] if body else []
    out: list[ParsedBlock] = []
    for anchor, kind, frag in segments:
        body = "\n".join(frag).strip()
        if not body:
            continue  # 空段（连续定界符等）不产块
        out.append(ParsedBlock(anchor, kind, body))
    return out


def content_hash(text: str) -> str:
    """无锚精确匹配的 content-hash：去首尾空白后 sha1 前 12 位。"""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def match_round(
    parsed: list[ParsedBlock], previous: list[Block], taken: set[str]
) -> list[Block]:
    """把一轮 AI 输出解析结果匹配为稳定块序列（§2.2 双层协议）。

    匹配链（逐块）：
      1. 锚继承——AI 携带的 id 在上轮块集中存在且本轮未被占用 → 继承；
      2. content-hash 精确匹配——文本一致的上轮块（首个未占用者）→ 继承；
      3. difflib 相似度 ≥0.6 的最高分且未占用 → 继承；**最高分并列
         （≥2 个候选）→ 歧义，恒新块**；
      4. 否则新块（fresh id，避开 taken 与本轮已占用 id）。

    taken 为全会话已用 id 集（draft + 既有轮），保证 id 全会话稳定不撞。
    """
    used: set[str] = set()  # 本轮占用集（继承即占用；与 taken 分离——taken 只管 fresh id 避让）
    fresh_pool = set(taken)
    prev_ids = {b.id for b in previous}
    out: list[Block] = []
    for p in parsed:
        block_id: str | None = None
        if p.anchor and p.anchor in prev_ids and p.anchor not in used:
            block_id = p.anchor
        else:
            hit = next(
                (b for b in previous if b.id not in used and content_hash(b.text) == content_hash(p.text)),
                None,
            )
            if hit is not None:
                block_id = hit.id
            else:
                best_id: str | None = None
                best_score = 0.0
                tie = False
                for b in previous:
                    if b.id in used:
                        continue
                    score = difflib.SequenceMatcher(
                        None, p.text.strip(), b.text.strip()
                    ).ratio()
                    if score < SIMILARITY_THRESHOLD:
                        continue
                    if score > best_score + _SCORE_EPS:
                        best_id, best_score, tie = b.id, score, False
                    elif best_id is not None and abs(score - best_score) <= _SCORE_EPS:
                        tie = True  # 并列最高分：歧义恒新块
                if best_id is not None and not tie:
                    block_id = best_id
        if block_id is None:
            block_id = fresh_id(fresh_pool | used)
        used.add(block_id)
        fresh_pool.add(block_id)
        out.append(Block(id=block_id, kind=p.kind, text=p.text, origin="ai"))
    return out
