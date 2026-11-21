"""三态合并（第十八期 §2.4 采纳合并与 habits 闭环）。

merged = 本轮 decisions 应用到本轮 blocks：
  keep          → AI 块原样入稿（origin=ai）
  adopt_edited  → 人工定稿文本替换入稿（origin=human——血统改写）
  discard       → 弃（不入稿）
  未裁决块      → 不入稿（红线：AI 产预览绝不直写 draft.md——没有人工
                  「采纳」动作的块进不了真稿）

幂等：纯函数 (blocks, decisions) → merged，重复应用同结果（§2.8 测试锚点）。
最后一条裁决胜出（同块改主意以末次为准）。

habits 闭环（§2.4 飞轮点火）：adopt_edited 且 ai_text ≠ human_text 的块
产出训练对 (draft=ai, final=human)——learn 只存差分不改行为（红线沿用）。
"""
from __future__ import annotations

from dataclasses import replace

from .blocks import Block, ORIGIN_HUMAN
from .session import ACTION_ADOPT_EDITED, ACTION_KEEP


def _latest_by_block(decisions: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for d in decisions:
        block_id = str(d.get("block") or "")
        if block_id:
            latest[block_id] = d  # 后写胜出
    return latest


def apply_decisions(blocks: list[Block], decisions: list[dict]) -> list[Block]:
    """三态合并本轮块（§2.4 adopt 的核心纯逻辑）。"""
    latest = _latest_by_block(decisions)
    out: list[Block] = []
    for b in blocks:
        d = latest.get(b.id)
        if d is None:
            continue  # 未裁决：不入稿（人工最后一步红线）
        action = d.get("action")
        if action == ACTION_KEEP:
            out.append(replace(b, origin="ai"))
        elif action == ACTION_ADOPT_EDITED:
            out.append(
                replace(b, text=str(d.get("text") or b.text), origin=ORIGIN_HUMAN)
            )
        # discard → 弃
    return out


def habits_pairs(blocks: list[Block], decisions: list[dict]) -> list[tuple[str, str]]:
    """adopt_edited 差分训练对 (ai_text, human_text)——§2.4：仅文本不同者。"""
    latest = _latest_by_block(decisions)
    pairs: list[tuple[str, str]] = []
    for b in blocks:
        d = latest.get(b.id)
        if d is None or d.get("action") != ACTION_ADOPT_EDITED:
            continue
        human = str(d.get("text") or "")
        if human.strip() and human != b.text:
            pairs.append((b.text, human))
    return pairs
