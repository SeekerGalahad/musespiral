"""CompositionSession 聚合根与落盘形态逻辑（第十八期 §2.1 正典）。

纯逻辑零 I/O：本文件只定义「什么进 session.json / 决策长什么样 / 素材
节选怎么截 / 轮次摘要怎么算」，文件读写全部在 services/compose_service。

落盘形态（articles root 下 ``<slug>.compose/``，自包含）：
  session.json      元数据+sources+status+轮次块记录（本模块 dict 形态）
  draft.md          当前真稿唯一表达（blocks.py 锚协议）
  rounds/N.md       每轮 AI 原始产出（存证+habits 原料）
  N.decisions.json  逐块裁决记录（Decision 三态 dict）
  feedback.jsonl    裁决流追加（审计+下轮 prompt 素材）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .blocks import Block

STATUS_COMPOSING = "composing"
STATUS_ADOPTED = "adopted"

# Decision 三态（§2.1）
ACTION_KEEP = "keep"
ACTION_ADOPT_EDITED = "adopt_edited"
ACTION_DISCARD = "discard"
ACTIONS: tuple[str, ...] = (ACTION_KEEP, ACTION_ADOPT_EDITED, ACTION_DISCARD)

# token 治理默认值（§2.3：2000 字/素材、8000 字/会话；轮次窗口 K=2）
PER_SOURCE_BUDGET = 2000
SESSION_BUDGET = 8000
ROUND_CONTEXT_K = 2

# G-T4 滚动摘要（设计 §五）：正稿字数每跨 1500 字触发一次重算。
ROLLING_SUMMARY_STEP = 1500

# E-5 小说模式（评审 §3.6）：会话种类。article=普通公众号长文（缺省，
# 存量 session.json 无该键时按此归一——零迁移）；novel=小说创作（右栏
# 显示「📖 前情提要」，prompt 注入前情提要 + 人物卡）。
KIND_ARTICLE = "article"
KIND_NOVEL = "novel"
SESSION_KINDS: tuple[str, ...] = (KIND_ARTICLE, KIND_NOVEL)


def _kind_or_default(value: object) -> str:
    """kind 宽容归一（存量 session.json 无该键 / 脏值 → article；不崩读侧）。"""
    text = str(value or "").strip()
    return text if text in SESSION_KINDS else KIND_ARTICLE

# 章节标题行（二级；与 discovery/scanner 的 sections 口径一致——`## `）
_SECTION_RE = re.compile(r"^(#{1,2})[ \t]+(.+?)[ \t]*$")

# 超限截断标注（§2.3 固定文案）
TRUNCATED_MARK = "…（素材已节选）"


@dataclass
class SourceRef:
    """一个素材引用：path + 勾选章节（空 = 全文）。

    material_id（N-2「引用自己」2026-11-21）：素材库直引形态——path 以
    虚拟定位符 ``material://<id>`` 承载（prompt 侧经素材库取 payload.text，
    不落文件），本键存素材 id；文件素材（path 指真实文件）恒空串。存量
    session.json 无该键 → ""（零迁移零回归）。
    """

    path: str
    sections: list[str] = field(default_factory=list)
    material_id: str = ""

    def to_dict(self) -> dict:
        out: dict = {"path": self.path, "sections": list(self.sections)}
        if self.material_id:
            out["material_id"] = self.material_id  # 仅素材直引落键（存量形状不变）
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "SourceRef":
        sections = data.get("sections") or []
        return cls(
            path=str(data.get("path") or ""),
            sections=[str(s) for s in sections],
            material_id=str(data.get("material_id") or ""),
        )


@dataclass
class Round:
    """一轮：AI 产出块 + 人工裁决（decisions 由服务层按落盘记录装配）。"""

    n: int
    blocks: list[Block] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)

    def to_record(self) -> dict:
        """轮次块记录（进 session.json——rounds/N.md 只是原始产出存证，
        解析后的块序列与 origin 血统以本记录为权威）。"""
        return {"n": self.n, "blocks": [b.to_dict() for b in self.blocks]}


@dataclass
class CompositionSession:
    """会话聚合根（§2.1）。draft 为当前真稿块视图（从 draft.md 装配）。"""

    slug: str
    title: str
    sources: list[SourceRef] = field(default_factory=list)
    draft: list[Block] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    status: str = STATUS_COMPOSING
    # G-T4 滚动摘要（§五）：AI 对「到目前为止写了什么」的 <200 字脉络；
    # 会话内唯一真值在 session.json rolling_summary（用户可在右栏改写）。
    rolling_summary: str = ""
    # 已跨过的 1500 字阈值档位（0/1/2…）：防同档重复触发，回退到低档后
    # 再跨档才会再次触发（§五「每 1500 字」的实现口径）。
    rolling_summary_level: int = 0
    # E-5 小说模式（评审 §3.6）：会话种类（article 缺省 / novel）。
    kind: str = KIND_ARTICLE
    # E-5 前情提要（§3.6「AI 摘要前 N-1 章 → 存 session.json novel_summary」）：
    # 与 rolling_summary 并存且互不覆盖——rolling 记「写到哪」的近期脉络，
    # novel 记「此前各章」的情节账本；两者都进 prompt（各自独立插槽）。
    novel_summary: str = ""
    # 已摘要到的「正文章数」——换章才建议重算（同章内不再打 AI；用户手改
    # 不推进该计数，与 rolling_summary_level 同语义）。
    novel_summary_chapters: int = 0
    # E-5 人物卡挂载（§3.6「创作工坊会话可挂多张人物卡」）：存素材 id 列表
    # （真值在素材库 sub_type=character 行——本会话只记「挂了哪几张」）。
    characters: list[str] = field(default_factory=list)

    def to_meta(self) -> dict:
        """session.json 形态（draft 不入——draft.md 是真稿唯一表达）。"""
        return {
            "slug": self.slug,
            "title": self.title,
            "sources": [s.to_dict() for s in self.sources],
            "status": self.status,
            "rounds": [r.to_record() for r in self.rounds],
            "rolling_summary": self.rolling_summary,
            "rolling_summary_level": self.rolling_summary_level,
            "kind": self.kind,
            "novel_summary": self.novel_summary,
            "novel_summary_chapters": self.novel_summary_chapters,
            "characters": list(self.characters),
        }

    @classmethod
    def from_meta(cls, meta: dict, draft: list[Block] | None = None) -> "CompositionSession":
        rounds = [
            Round(
                n=int(r.get("n") or 0),
                blocks=[Block.from_dict(b) for b in (r.get("blocks") or [])],
            )
            for r in (meta.get("rounds") or [])
        ]
        return cls(
            slug=str(meta.get("slug") or ""),
            title=str(meta.get("title") or ""),
            sources=[SourceRef.from_dict(s) for s in (meta.get("sources") or [])],
            draft=list(draft or []),
            rounds=rounds,
            status=str(meta.get("status") or STATUS_COMPOSING),
            rolling_summary=str(meta.get("rolling_summary") or ""),
            rolling_summary_level=int(meta.get("rolling_summary_level") or 0),
            kind=_kind_or_default(meta.get("kind")),
            novel_summary=str(meta.get("novel_summary") or ""),
            novel_summary_chapters=int(meta.get("novel_summary_chapters") or 0),
            characters=[str(x) for x in (meta.get("characters") or []) if isinstance(x, str)],
        )

    def taken_ids(self) -> set[str]:
        """全会话已用 id 集（draft + 全部轮次块）——match_round 的 taken。"""
        return {b.id for b in self.draft} | {b.id for r in self.rounds for b in r.blocks}


# ---- Decision 三态校验（§2.1：keep | adopt_edited(text) | discard(direction)）

def validate_decision(action: str, text: str | None, direction: str | None) -> dict:
    """校验并归一一条裁决载荷；非法抛 ValueError（可读消息）。

    - keep：原样保留，无附加字段；
    - adopt_edited：必须带非空 text（人工定稿文本）；
    - discard：必须带非空 direction（丢弃原因/方向，喂下轮 prompt）。
    """
    if action not in ACTIONS:
        raise ValueError(f"未知裁决动作: {action!r}（可选: {'/'.join(ACTIONS)}）")
    if action == ACTION_ADOPT_EDITED and not (text or "").strip():
        raise ValueError("adopt_edited 必须携带人工定稿文本 text")
    if action == ACTION_DISCARD and not (direction or "").strip():
        raise ValueError("discard 必须携带方向备注 direction（原因/改写方向）")
    decision: dict = {"action": action}
    if action == ACTION_ADOPT_EDITED:
        decision["text"] = text.strip()
    if action == ACTION_DISCARD:
        decision["direction"] = direction.strip()
    return decision


# ---- 素材节选（§2.3 token 治理：章节勾选 + 双层预算）--------------------


def select_sections(body: str, sections: list[str]) -> str:
    """按勾选的二级章节提取素材文本（空选择 = 全文）。

    章节切到下一个一/二级标题为止（§2 内的 ### 小节随章走）；输出按文档
    顺序拼接（勾选乱序不重排原文）；未命中的章节名静默跳过（校验归
    服务层 add_source，此处宽容）。
    """
    if not sections:
        return body.strip()
    chosen = {s.strip() for s in sections}
    lines = body.split("\n")
    out: list[str] = []
    capturing = False
    for line in lines:
        m = _SECTION_RE.match(line)
        if m and len(m.group(1)) <= 2:
            capturing = m.group(2).strip() in chosen
            if capturing:
                out.append("")
                out.append(line)
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def excerpt_sources(
    sources: list[dict],
    per_limit: int = PER_SOURCE_BUDGET,
    total_limit: int = SESSION_BUDGET,
) -> tuple[str, bool]:
    """素材节选组装（§2.3）：逐素材预算 + 会话总预算，超限截断+标注。

    sources 项：{"path", "title"?, "content"}（content 已过 select_sections）。
    返回 (节选文本, 是否发生截断)。总预算耗尽后的素材以占位行标注
    （不静默丢弃——模型与人都看得到「还有素材没进来」）。
    """
    blocks: list[str] = []
    remaining = total_limit
    truncated = False
    for i, s in enumerate(sources, 1):
        label = s.get("title") or s.get("path") or f"素材{i}"
        content = str(s.get("content") or "").strip()
        if remaining <= 0:
            blocks.append(f"【素材 {i}｜{label}】\n（因会话总预算已节选略）")
            truncated = True
            continue
        room = min(per_limit, remaining)
        if len(content) > room:
            content = content[:room] + TRUNCATED_MARK
            truncated = True
        remaining -= len(content)
        blocks.append(f"【素材 {i}｜{label}】\n{content}")
    return "\n\n".join(blocks), truncated


# ---- 轮次上下文（§2.3：只带最近 K=2 轮的块级 diff 摘要，不整稿重放）----


def _brief(text: str, limit: int = 24) -> str:
    s = text.strip().replace("\n", " ")
    return s[:limit] + "…" if len(s) > limit else s


def round_diff_summary(rounds: list[Round], draft: list[Block], k: int = ROUND_CONTEXT_K) -> str:
    """最近 K 轮的块级 diff 摘要（相对各自基线：上一轮 / 首轮为当前 draft）。

    每轮一行：新增/继承/消失计数 + 消失块的一瞥（人工最关心丢了什么）。
    """
    lines: list[str] = []
    start = max(len(rounds) - k, 0) if k > 0 else len(rounds)
    for pos in range(start, len(rounds)):
        r = rounds[pos]
        baseline = rounds[pos - 1].blocks if pos > 0 else draft
        cur_ids = {b.id for b in r.blocks}
        base_ids = {b.id for b in baseline}
        added = cur_ids - base_ids
        dropped = base_ids - cur_ids
        dropped_brief = (
            "；".join(
                f"{bid}（{_brief(next((b.text for b in baseline if b.id == bid), ''))}）"
                for bid in sorted(dropped)
            )
            if dropped
            else "无"
        )
        lines.append(
            f"- 第 {r.n} 轮：新增 {len(added)} 块、继承 {len(cur_ids & base_ids)} 块、消失 {len(dropped)} 块"
            f"（消失块：{dropped_brief}）"
        )
    return "\n".join(lines)
