"""字符预算口径与三级预算常量（Phase D F79 治理底盘，L2 纯函数零 I/O）。

**单一事实源**：三级字符预算的数值与对账数学只在此定义——`interp` 的
run 级对账与 `infra/ai/gateway` 的日预算口径共用同一套（设计 §三-D2/
F79.4）。改常量只改这里。

三级预算（设计 §三-D2，出自 D 档 §二 F75.2/F76.2）：

| 级 | 常量 | 缺省 | 语义 |
|---|---|---|---|
| ①槽位 | `SLOT_BUDGET` | 2000 | 单素材正文节选上限（沿 `composition/session.py:41` PER_SOURCE_BUDGET 语义） |
| ②步 | `STEP_BUDGET` | 8000 | 单步总输入上限（plan 可按步 `budget_in` 覆写） |
| ③run | `RUN_IN_CHARS` / `RUN_OUT_CHARS` / `RUN_STEPS` | 40000 / 20000 / 16 | 单次 run 全程输入/输出/步数上限（步数 8→16 见常量注） |

**口径诚实性（设计 §二 F79.4「token/字符口径」）**：不引入 tokenizer——
`est_tokens ≈ len(text)`（中文一字≈一 token 量级；ASCII 折半）。所有对
外返回的 meta 都带 `unit: "chars"`，文档化而不冒充精确 token 计数。
这是刻意的近似：本地单人环境下「同一把尺子量所有预算」比「更准的尺子
但不可复现」更有用，且零新增依赖是本期硬约束（§〇）。
"""
from __future__ import annotations

from typing import Any

# ── 三级预算常量（单一事实源）────────────────────────────────────────

# ① 槽位预算：单素材正文节选上限（字符）——沿 PER_SOURCE_BUDGET 语义
SLOT_BUDGET = 2000

# ② 步预算：单步总输入上限（字符）——沿 SESSION_BUDGET 语义
STEP_BUDGET = 8000

# ③ run 预算：单次编排全程上限（原 §八-4 裁决值 8；R3.5 书籍提炼批
# 2026-11-18 上调 8→16：`build_book_digest` 需要「N 章 digest + 1 综述」单计划
# 展平——设计正典示例 9 章书=10 步、R4 前端范围预设「最新 10 章」=11 步，
# 8 步上限会让主场景整本小书不可跑。上调只放宽**步数**上限；in/out 字符
# 上限不变，且每个构造器仍按 Σ步上界自报 run 预算（不虚增额度）。
RUN_IN_CHARS = 40_000
RUN_OUT_CHARS = 20_000
RUN_STEPS = 16

# 步间尾部携带缺省（字符）：后步只见前步输出尾段，永不全 transcript 累积
CARRY_TAIL_DEFAULT = 1200

# 超限截断固定文案（沿用 composition/session.py:38 同款纪律——
# 截断必须留可见标注，不静默丢内容）
TRUNCATED_MARK = "…（内容已节选）"

# 计量单位标注（对外 meta 恒带此键；诚实近似，见模块 docstring）
UNIT = "chars"

# run 级预算三键（Plan["budget"] 的键集；validate_plan 拒绝多余键）
BUDGET_KEYS: tuple[str, ...] = ("steps", "in_chars", "out_chars")


def run_budget(
    *,
    steps: int = RUN_STEPS,
    in_chars: int = RUN_IN_CHARS,
    out_chars: int = RUN_OUT_CHARS,
) -> dict[str, int]:
    """构造 run 级预算三键 dict（每次新 dict，防调用方污染常量）。"""
    return {"steps": steps, "in_chars": in_chars, "out_chars": out_chars}


# ── 口径换算与截断（纯函数）──────────────────────────────────────────


def est_tokens(text: str) -> int:
    """字符启发式 token 估算（**诚实近似**，不是精确计数）。

    规则：CJK 区间每字计 1；其余（ASCII/空白/标点）每 2 字符计 1（向上取整）。
    目的只是同口径比较，不用于计费。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff")
    rest = len(text) - cjk
    return cjk + (rest + 1) // 2


def meta(chars_in: int = 0, chars_out: int = 0) -> dict[str, Any]:
    """计量 meta（对外恒带 `unit:"chars"`——设计 §二 F79.4 标注义务）。"""
    return {"unit": UNIT, "chars_in": chars_in, "chars_out": chars_out}


def clip(text: str, limit: int, *, mark: str = TRUNCATED_MARK) -> str:
    """按字符上限截断并**在限内**前置固定文案 → 结果长度**恒 ≤ limit**。

    为什么标注要占限内预算（而不是溢出到 `limit+len(mark)`）：设计 §三-D2
    两处上界都是**字面承诺**（「单步总输入 ≤8000」「输出超限截断」）——若
    截断结果的长度是 `limit + 8`，那 `max_out` 就不是真正的上界，基于
    `Σ max_out` 推导的 run 级预算会在最后一步误判（构造器给足 N×2000+6000
    却仍 budget_stopped）。标注在限内 → `max_out` 是真上界 → 预算数学诚实。

    `limit <= 0` → 空串（预算已耗尽，不是错误）。`limit < len(mark)` 时
    连标注都放不下——只留纯内容前缀（标注让位于内容，仍守 ≤limit）。
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    room = limit - len(mark)
    if room <= 0:
        return text[:limit]
    return text[:room] + mark


def tail(text: str, spec: str) -> str:
    """`tail:N` 规格取文本尾 N 字符（步间 carry 的唯一求值器）。

    规格文法只认 `tail:<正整数>`——构造期由 `plan.validate_plan` 拒绝其他
    形态，运行期非法即 ValueError（不静默回退整段，防上下文炸预算）。
    截断处前置 TRUNCATED_MARK（与 `clip` 同款纪律）。
    """
    n = parse_tail_spec(spec)
    if len(text) <= n:
        return text
    return TRUNCATED_MARK + text[-n:] if n > 0 else TRUNCATED_MARK


def parse_tail_spec(spec: Any) -> int:
    """解析 `tail:N` → N；非法（非 str/前缀不符/非正整数）→ ValueError。"""
    if not isinstance(spec, str) or not spec.startswith("tail:"):
        raise ValueError(f"carry 规格必须是 'tail:<正整数>' 形态: {spec!r}")
    raw = spec[len("tail:"):].strip()
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"carry 尾部长度必须是整数: {spec!r}") from None
    if n < 1:
        raise ValueError(f"carry 尾部长度必须 ≥1: {spec!r}")
    return n


# ── run 级对账数学（interp 与 gateway 共用口径）──────────────────────


def over_any(budget: dict[str, int], used: dict[str, int]) -> str | None:
    """run 级三键任一触顶 → 返回触顶键名；未触顶 → None。

    **步前**对账（设计 §二 F75.2 ①）：`steps` 用「已用步数 / 上限」比，
    `in_chars`/`out_chars` 用同口径。触顶端决定 `budget_stopped` 的
    `reason` 措辞，因此返回键名而非布尔。
    """
    for key in ("steps", "in_chars", "out_chars"):
        limit = budget.get(key)
        if isinstance(limit, int) and used.get(key, 0) >= limit:
            return key
    return None


def remaining(budget: dict[str, int], used: dict[str, int]) -> dict[str, Any]:
    """余量三键（CLI 摘要块「预算余量」行；unit 标注同行给出）。"""
    out: dict[str, Any] = {}
    for key in ("steps", "in_chars", "out_chars"):
        limit = budget.get(key)
        out[key] = (limit - used.get(key, 0)) if isinstance(limit, int) else None
    out["unit"] = UNIT
    return out
