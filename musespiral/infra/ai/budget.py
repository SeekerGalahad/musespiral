"""AI 日预算（Phase D F79.2/F79.4，L1）。

`~/.musespiral/ai_budget.json` 是本机日预算的**唯一事实源**（与 `ai.json` 的
档案+路由分离——配置与状态分离是设计 §1.2 迁移表的明确裁决）：

    {"daily_calls": 200, "daily_out_chars": 200000,
     "per_task": {"compose": {"daily_calls": 30}, "agent_synthesize": {...}}}

- 文件缺席/坏 JSON → 全用内置缺省（读侧宽容，零配置即可用）；
- `per_task.<task>.<key>` **覆盖**全局同名键（未给的键继续继承全局）；
- 写入 tmp+replace 原子（`--set per_task.compose.daily_calls=10` 点路径）。

**为什么预算是「读侧对账」而不是「写入前计数器」**：计数的事实源是
usage JSONL（append-only 台账），预算文件只存**上限**。两者分离让
`musespiral ai usage` 与超限判定永远同口径，也让「删掉 usage 就重置预算」
这样的越权操作不可能（预算不看自己记的计数）。

**`check()` 的口径**：返回 `(ok, reason)`；`reason` 是可直接展示给用户的
中文短语（含任务名/今日已用/上限），由 gateway 拼成 `BudgetExceeded` 消息。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import config
from ..storage.filesystem import atomic_write_bytes

BUDGET_FILE = "ai_budget.json"

# 缺省值（设计 §八-4 裁决：daily_calls=200 / daily_out_chars=200_000）
DEFAULT_DAILY_CALLS = 200
DEFAULT_DAILY_OUT_CHARS = 200_000

# 可设置的顶层键（点路径 set 的白名单）
GLOBAL_KEYS: tuple[str, ...] = ("daily_calls", "daily_out_chars")
# per_task 可覆盖的键（与全局同集，语义=覆盖）
PER_TASK_KEYS: tuple[str, ...] = ("daily_calls", "daily_out_chars")


def budget_path() -> Path:
    """预算文件路径 `~/.musespiral/ai_budget.json`（随 MUSESPIRAL_HOME 解析）。"""
    return config.home_dir() / BUDGET_FILE


def _positive_int(value: Any) -> int | None:
    """宽容取正整数；bool/非数/≤0 → None（坏值按「未设置」处理）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def load() -> dict[str, Any]:
    """读预算全口径：{daily_calls, daily_out_chars, per_task}（缺省已填好）。

    文件里坏的/负数/非整数项**逐键回落缺省**（不整份丢弃——用户手改出
    一个坏键不该让其他设置一起失效）。
    """
    data: dict[str, Any] = {
        "daily_calls": DEFAULT_DAILY_CALLS,
        "daily_out_chars": DEFAULT_DAILY_OUT_CHARS,
        "per_task": {},
    }
    try:
        raw = json.loads(budget_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in GLOBAL_KEYS:
        parsed = _positive_int(raw.get(key))
        if parsed is not None:
            data[key] = parsed
    per_task = raw.get("per_task")
    if isinstance(per_task, dict):
        clean: dict[str, dict[str, int]] = {}
        for task, overrides in per_task.items():
            if not isinstance(task, str) or not isinstance(overrides, dict):
                continue
            row: dict[str, int] = {}
            for key in PER_TASK_KEYS:
                parsed = _positive_int(overrides.get(key))
                if parsed is not None:
                    row[key] = parsed
            if row:
                clean[task] = row
        data["per_task"] = clean
    return data


def save(data: dict[str, Any]) -> Path:
    """原子写预算文件（tmp+replace）；形状先归一（非法项丢弃）。"""
    clean: dict[str, Any] = {}
    for key in GLOBAL_KEYS:
        parsed = _positive_int(data.get(key))
        clean[key] = parsed if parsed is not None else (
            DEFAULT_DAILY_CALLS if key == "daily_calls" else DEFAULT_DAILY_OUT_CHARS
        )
    per_task = data.get("per_task")
    clean["per_task"] = {}
    if isinstance(per_task, dict):
        for task, overrides in per_task.items():
            if not isinstance(task, str) or not isinstance(overrides, dict):
                continue
            row: dict[str, int] = {}
            for key in PER_TASK_KEYS:
                parsed = _positive_int(overrides.get(key))
                if parsed is not None:
                    row[key] = parsed
            if row:
                clean["per_task"][task] = row
    path = budget_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        path, (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return path


def limits_for(task: str, data: dict[str, Any] | None = None) -> dict[str, int]:
    """某任务的**生效上限**：`per_task[task]` 逐键覆盖全局（未给的键继承）。"""
    state = data if data is not None else load()
    limits = {key: state[key] for key in GLOBAL_KEYS}
    overrides = (state.get("per_task") or {}).get(task) or {}
    for key in PER_TASK_KEYS:
        if key in overrides:
            limits[key] = overrides[key]
    return limits


def check(
    task: str,
    today_usage: dict[str, Any],
    *,
    task_usage: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """超限判定：`(ok, reason)`；reason 为可读中文（含任务/已用/上限）。

    `today_usage` 形如 `{"calls": int, "out_chars": int}`——**只含未命中行**
    （`usage.today_summary` 的口径；缓存命中不消耗预算，故不进这两个数）。

    `task_usage`：同一任务当日的用量（`usage.task_summary` 口径）。**per_task
    覆盖的语义是「该任务用自己那一份计数对自己的那一份上限」**——否则
    「per_task.agent_synthesize.daily_calls=3」会拿全局计数去比 3，一有
    别的任务调用就误伤。因此：

      - 某键在 per_task 里有覆盖 → 用 `task_usage` 计数比该覆盖值；
      - 无覆盖 → 用 `today_usage` 计数比全局值。

    `task_usage` 省略时视为与 today_usage 同值（单任务场景的便利缺省）。

    边界语义：`已用 >= 上限` 即拒（已到上限的那次调用之后不再放行）。
    """
    limits = limits_for(task, data)
    overrides = ((data if data is not None else load()).get("per_task") or {}).get(task) or {}
    task_counts = task_usage if task_usage is not None else today_usage

    for key, counter in (
        ("daily_calls", "calls"),
        ("daily_out_chars", "out_chars"),
    ):
        limit = limits[key]
        used = int((task_counts if key in overrides else today_usage).get(counter) or 0)
        if used >= limit:
            unit = "次" if key == "daily_calls" else "字符"
            scope = "该任务今日" if key in overrides else "今日"
            return False, (
                f"任务 {task} {scope}已用 {used}/上限 {limit} {unit}，"
                f"请明日再试或 musespiral ai budget 调整"
            )
    return True, None


# ── 点路径 set（`musespiral ai budget --set per_task.compose.daily_calls=10`）──


def parse_set_expression(expr: str) -> tuple[str, int]:
    """解析 `key=value` 点路径表达式 → (点路径, 正整数)；非法 → ValueError。

    支持的形态：
      `daily_calls=150` / `daily_out_chars=300000`
      `per_task.<task>.<key>=<int>`（key ∈ {daily_calls, daily_out_chars}）
    """
    if "=" not in expr:
        raise ValueError(f"--set 需要 key=value 形态: {expr!r}")
    raw_key, raw_value = expr.split("=", 1)
    key = raw_key.strip()
    try:
        value = int(raw_value.strip())
    except ValueError:
        raise ValueError(f"--set 的值必须是整数: {expr!r}") from None
    if value <= 0:
        raise ValueError(f"--set 的值必须 >0: {expr!r}")

    if key in GLOBAL_KEYS:
        return key, value
    parts = key.split(".")
    if len(parts) == 3 and parts[0] == "per_task":
        task, sub = parts[1], parts[2]
        if not task:
            raise ValueError(f"--set 的任务名为空: {expr!r}")
        if sub not in PER_TASK_KEYS:
            raise ValueError(
                f"--set 的 per_task 键必须是 {'/'.join(PER_TASK_KEYS)}，实际: {sub!r}"
            )
        return key, value
    raise ValueError(
        f"--set 的键不合法: {key!r}"
        f"（合法: {'/'.join(GLOBAL_KEYS)} 或 per_task.<task>.{'|'.join(PER_TASK_KEYS)}）"
    )


def apply_set(expr: str) -> tuple[str, int, dict[str, Any]]:
    """执行一条 `--set` 表达式并落盘；返回 (点路径, 新值, 落盘后全口径)。"""
    key, value = parse_set_expression(expr)
    data = load()
    if key in GLOBAL_KEYS:
        data[key] = value
    else:
        _prefix, task, sub = key.split(".")
        per_task = dict(data.get("per_task") or {})
        row = dict(per_task.get(task) or {})
        row[sub] = value
        per_task[task] = row
        data["per_task"] = per_task
    save(data)
    return key, value, load()
