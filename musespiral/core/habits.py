"""改稿习惯学习（风格飞轮 v1）— diff → habits.json → rewrite 提示词注入。

Phase C 修复轮归位 core/habits.py（正典 §2.3）；habits_prompt 注入件已拆至
infra/ai/habits_inject.py（infra 不上探 domain，鸭子签名注入）。

从「AI 底稿 ↔ 用户终稿」的段落级 diff 中，用启发式规则提取稳定的改稿习惯，
追加存储到 ~/.musespiral/habits.json（JSON 数组，人工可编辑），rewrite 时由 ai.py
注入系统提示词。v1 六条规则（见设计文档 §十二 F5），不做 LLM 二次总结。
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config

HABITS_FILE = "habits.json"

# 客套话标记：被整段删除的段落含任一关键词 → 客套删除
_COURTESY_MARKERS = ("大家好", "废话不多说", "希望对", "感谢阅读", "感谢观看", "欢迎关注", "点赞", "在看")

# 书面词 → 口语词候选（命中即记「书面词口语化」）
_FORMAL_CASUAL = (
    ("综上所述", ("总的来说", "总之")),
    ("因此", ("所以",)),
    ("然而", ("但是", "不过")),
)

# emoji 大致按常用 Unicode 区间计数（符号表情 + 表情图表 + 旗帜）
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")

# 六条规则的固定输出顺序（与设计文档 F5 列表一致）
_RULE_ORDER = ("客套删除", "标题风格", "emoji 增删", "长段拆分", "代码示例补充", "书面词口语化")


@dataclass
class Habit:
    """一条改稿习惯：rule=规则名，example=可注入提示词的简述/前后对照例句。"""

    rule: str
    example: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "example": self.example}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Habit":
        return cls(rule=str(data.get("rule", "")), example=str(data.get("example", "")))


def _paragraphs(text: str) -> list[str]:
    """按空行切段（Markdown 段落），返回去首尾空白的非空段列表。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _squash(text: str) -> str:
    """去除全部空白，用于段落内容比对（忽略换行/空格差异）。"""
    return re.sub(r"\s+", "", text)


def _count_emoji(text: str) -> int:
    return len(_EMOJI_RE.findall(text))


def _count_code_blocks(text: str) -> int:
    """围栏代码块数 = 以 ``` 开头的行数 // 2。"""
    return len(re.findall(r"^```", text, flags=re.MULTILINE)) // 2


def extract_habits(draft_text: str, final_text: str) -> list[Habit]:
    """比较 AI 底稿与用户终稿，返回命中的改稿习惯（每条规则至多记一次）。

    用 difflib 做段落对齐，再跑六条启发式：客套删除/标题风格/emoji 增删/
    长段拆分/代码示例补充/书面词口语化。
    """
    draft_paras = _paragraphs(draft_text)
    final_paras = _paragraphs(final_text)
    found: dict[str, Habit] = {}

    sm = difflib.SequenceMatcher(a=draft_paras, b=final_paras, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old, new = draft_paras[i1:i2], final_paras[j1:j2]
        if tag == "delete" and "客套删除" not in found:
            for para in old:
                if not para.startswith("#") and any(m in para for m in _COURTESY_MARKERS):
                    snippet = para if len(para) <= 20 else para[:20] + "…"
                    found["客套删除"] = Habit(
                        "客套删除", f"不要开场客套，直接进正题（曾整段删除「{snippet}」）"
                    )
                    break
        if tag == "replace":
            joined_old = _squash("".join(old))
            joined_new = _squash("".join(new))
            # 长段拆分：一段对多段且内容基本保留
            if "长段拆分" not in found and len(old) == 1 and len(new) >= 2:
                ratio = difflib.SequenceMatcher(None, joined_old, joined_new).ratio()
                if ratio >= 0.8:
                    found["长段拆分"] = Habit(
                        "长段拆分", f"段落更短（{len(joined_old)} 字的长段拆成 {len(new)} 段）"
                    )
            # 书面词口语化：书面词消失、口语词出现
            if "书面词口语化" not in found:
                for formal, casuals in _FORMAL_CASUAL:
                    if formal in joined_old and formal not in joined_new and any(
                        c in joined_new for c in casuals
                    ):
                        hit = next(c for c in casuals if c in joined_new)
                        found["书面词口语化"] = Habit(
                            "书面词口语化", f"口语化表达（「{formal}」→「{hit}」）"
                        )
                        break

    # 标题风格：标题行被改 → 记录前后对照例句
    draft_heads = [p for p in draft_paras if p.startswith("#")]
    final_heads = [p for p in final_paras if p.startswith("#")]
    if "标题风格" not in found:
        hsm = difflib.SequenceMatcher(a=draft_heads, b=final_heads, autojunk=False)
        for tag, i1, i2, j1, j2 in hsm.get_opcodes():
            if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1:
                found["标题风格"] = Habit(
                    "标题风格", f"原「{draft_heads[i1]}」→ 改「{final_heads[j1]}」"
                )
                break

    # emoji 增删：差异计数定方向
    d_emoji, f_emoji = _count_emoji(draft_text), _count_emoji(final_text)
    if d_emoji != f_emoji:
        verb = "加" if f_emoji > d_emoji else "删"
        found["emoji 增删"] = Habit("emoji 增删", f"用户会{verb} emoji（{d_emoji} → {f_emoji}）")

    # 代码示例补充：终稿比底稿多了围栏代码块
    d_code, f_code = _count_code_blocks(draft_text), _count_code_blocks(final_text)
    if f_code > d_code:
        found["代码示例补充"] = Habit(
            "代码示例补充", f"倾向带可运行示例（补充了 {f_code - d_code} 个代码块）"
        )

    return [found[r] for r in _RULE_ORDER if r in found]


def _default_path() -> Path:
    return config.home_dir() / HABITS_FILE


def save_habits(habits: list[Habit], path: str | Path | None = None) -> Path:
    """追加式保存：保留文件中已有条目，把新习惯接在后面，返回文件路径。

    F11b 去重：rule+example 完全相同的条目跳过追加（重复 learn 同一改稿
    不使条目翻倍）；同 rule 不同 example 视为新观察，正常保留。
    """
    p = Path(path) if path is not None else _default_path()
    existing = load_habits(p)
    seen = {(h.rule, h.example) for h in existing}
    merged = existing + [h for h in habits if (h.rule, h.example) not in seen]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([h.to_dict() for h in merged], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def load_habits(path: str | Path | None = None) -> list[Habit]:
    """读取习惯列表；文件缺失/损坏/非数组时返回空列表（文件可人工编辑，宽容解析）。"""
    p = Path(path) if path is not None else _default_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [Habit.from_dict(item) for item in data if isinstance(item, dict)]

