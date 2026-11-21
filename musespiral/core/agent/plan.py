"""编排计划 schema 与构造期校验（Phase D F75.2，L2 纯函数零 I/O）。

计划是**声明式数据**，控制流在代码（设计 §〇「不做自主智能体循环」）：
本模块只回答「这份 dict 是不是一份可以安全解释执行的计划」，不执行任何
步骤、不触任何 I/O、不读时钟。解释执行在 `interp.run_plan`。

**全枚举拒（设计 §二 F75.2）**——`validate_plan` 对下列每种违规都抛
`ValueError`，且消息里带**违规项名**（步 id / 槽位名 / 任务名 / 键名），
便于 CLI 与 API 把可读错误直接透给用户：

| 违规 | 消息含 |
|---|---|
| 计划不是 dict / steps 不是非空 list | `plan` / `steps` |
| 步不是 dict | `steps[i]` |
| 步 id 缺失/非 str/为空 | 该位置下标 |
| 步 id 重复 | 重复的 id |
| 步数 > RUN_STEPS（16） | 实际步数与上限 |
| `task` 不在 `TASKS` 白名单 | 任务名 |
| `out` 缺失/非 str/为空 | 步 id |
| `out` 槽名重复（后步会覆盖前步） | 槽名 |
| `max_out` 缺失/非正整数 | 步 id |
| `budget_in` 给定但非正整数 | 步 id |
| `retries` 给定但非非负整数 | 步 id |
| `cache` 给定但非 bool | 步 id |
| `inputs` 不是 dict | 步 id |
| `carry` 不是 dict / 规格非 `tail:N` | 槽名 |
| `carry` 引用的槽名尚未产出（悬空或**指向后方步**） | 槽名 |
| `{{steps.x}}` 引用不存在或指向后方步（线性无环的等价校验） | 槽名 |
| `{{materials[i]}}` 下标非十进制整数 | 原文 |
| 未知 `{{…}}` 文法（`{{params}}`、`{{foo}}`、嵌套等） | 原文 |
| `budget` 不是 dict / 键集不是三键 / 值非非负整数 | 键名 |

**槽位引用文法**（刻意无表达式、无嵌套——DSL 不图灵完备）：

- `{{materials[i]}}` → `ctx["materials"][i]`（run 输入；越界在**运行期**由
  interp 抛 ValueError，构造期无法知道 ctx 形状）
- `{{params.<k>}}` → `ctx["params"][k]`（CLI `--param k=v` 直通）
- `{{steps.<out>}}` → 已完成步输出全文（只许向前引用）

`plan.py` 不做 TASKS 的 import：白名单是**策略输入**，由调用方经 `tasks`
参数注入（生产 = `services/agent_service` 传 `profiles.TASKS`；测试传固定
元组）。理由有二——① 架构门 F75-7 硬性要求 `core/agent` 零 infra/services
import（懒加载也算：AST 扫描穿函数体），纯函数不该向上取策略；② TASKS 的
单一事实源仍是 `infra/ai/profiles.py`，此处只消费不定义。
"""
from __future__ import annotations

import re
from typing import Any

from . import budgets

# 步数硬上限（与 budgets.RUN_STEPS 同值——plan 缺省 budget 也取该值；
# 8→16 的上调依据见 budgets.RUN_STEPS 注）
MAX_STEPS = budgets.RUN_STEPS

# 槽位引用文法（三条，全串匹配；不做部分替换、不做嵌套）
# 文法原文：`{{materials[0]}}` / `{{params.k}}` / `{{steps.out}}`
_MATERIALS_RE = re.compile(r"^\{\{materials\[(\d+)\]\}\}$")
_PARAMS_RE = re.compile(r"^\{\{params\.([A-Za-z_][A-Za-z0-9_]*)\}\}$")
_STEPS_RE = re.compile(r"^\{\{steps\.([A-Za-z_][A-Za-z0-9_]*)\}\}$")

# 任何 {{…}} 形态的出现（含嵌套/空白/非法字符）——用于「未知文法」判定
_ANY_BRACE_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Step 的已知键集（多余键拒绝——防止拼写错的键静默失效）
_STEP_KEYS: tuple[str, ...] = (
    "id", "task", "inputs", "carry", "out", "budget_in", "max_out", "cache", "retries",
)
_PLAN_KEYS: tuple[str, ...] = ("plan", "steps", "budget")


def slot_kind(ref: str) -> tuple[str, str | int] | None:
    """槽位引用归类：`("materials", i)` / `("params", k)` / `("steps", out)`。

    不匹配任何合法文法 → None（调用方据此区分「未知文法」与「悬空引用」）。
    """
    m = _MATERIALS_RE.match(ref)
    if m:
        return ("materials", int(m.group(1)))
    m = _PARAMS_RE.match(ref)
    if m:
        return ("params", m.group(1))
    m = _STEPS_RE.match(ref)
    if m:
        return ("steps", m.group(1))
    return None


def _require_int(step_id: str, key: str, value: Any, *, minimum: int) -> None:
    """正整数校验（bool 是 int 子类，须显式排除——`True` 不是合法预算）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"步骤 {step_id!r}: {key} 必须是 ≥{minimum} 的整数，实际: {value!r}"
        )


def _validate_inputs(step_id: str, inputs: Any) -> list[tuple[str, str]]:
    """校验 inputs 的每条字面量/引用，返回 [(键, 引用原文)] 供跨步检查。"""
    if not isinstance(inputs, dict):
        raise ValueError(f"步骤 {step_id!r}: inputs 必须是 dict，实际: {type(inputs).__name__}")
    refs: list[tuple[str, str]] = []
    for key, value in inputs.items():
        if not isinstance(value, str):
            # 非 str 值（数字/列表）不参与槽位求值——作为字面量保留给模型，
            # 但 {{…}} 文法只对 str 生效，因此这里不做进一步检查
            continue
        kind = slot_kind(value)
        if kind is None:
            # 出现 {{…}} 但不是三条合法文法之一 → 未知文法（构造期错误）
            if _ANY_BRACE_RE.search(value):
                raise ValueError(
                    f"步骤 {step_id!r}: inputs[{key!r}] 含未知槽位引用文法: {value!r}"
                    f"（合法: {{{{materials[i]}}}}/{{{{params.<k>}}}}/{{{{steps.<out>}}}}）"
                )
            continue  # 纯字面量
        refs.append((key, value))
    return refs


def _validate_carry(step_id: str, carry: Any, produced: set[str]) -> None:
    """校验 carry：必须是 {槽名: "tail:N"}，且槽名须**已由前步产出**。

    「指向后方步」在此自然被拒——`produced` 只含本步之前的槽名。
    """
    if carry is None:
        return
    if not isinstance(carry, dict):
        raise ValueError(f"步骤 {step_id!r}: carry 必须是 dict，实际: {type(carry).__name__}")
    for slot_name, spec in carry.items():
        if slot_name not in produced:
            raise ValueError(
                f"步骤 {step_id!r}: carry 引用的槽位 {slot_name!r} 尚未产出"
                f"（只许向前引用；已产出: {sorted(produced) or '（无）'}）"
            )
        try:
            budgets.parse_tail_spec(spec)
        except ValueError as err:
            raise ValueError(f"步骤 {step_id!r}: carry[{slot_name!r}] {err}") from None


def _validate_step_refs(step_id: str, refs: list[tuple[str, str]], produced: set[str]) -> None:
    """校验 `{{steps.x}}` 的悬空/后方引用（materials/params 归运行期）。"""
    for key, ref in refs:
        kind = slot_kind(ref)
        assert kind is not None  # 调用方已过滤
        if kind[0] != "steps":
            continue
        target = kind[1]
        if target not in produced:
            raise ValueError(
                f"步骤 {step_id!r}: inputs[{key!r}] 引用 {ref} 的槽位不存在或指向后方步"
                f"（只许向前引用；已产出: {sorted(produced) or '（无）'}）"
            )


def validate_plan(plan: Any, *, tasks: tuple[str, ...]) -> None:
    """全枚举校验一份计划；任何违规 → ValueError（消息含违规项名）。

    校验通过返回 None（不返回归一化副本——计划是 immutable 输入，解释器
    不做改写，避免「校验过的」与「执行的」是两个对象）。

    `tasks`：TASKS 白名单（**必填**——策略由调用方注入，见模块 docstring；
    生产传 `profiles.TASKS`，测试传固定元组）。
    """
    allowed_tasks = tuple(tasks)

    if not isinstance(plan, dict):
        raise ValueError(f"plan 必须是 dict，实际: {type(plan).__name__}")

    for key in plan:
        if key not in _PLAN_KEYS:
            raise ValueError(f"plan 含未知键 {key!r}（合法键: {_PLAN_KEYS}）")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"plan['steps'] 必须是非空 list，实际: {type(steps).__name__}")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"步数上限 {MAX_STEPS}，实际 {len(steps)} 步")

    # budget（可选；缺省由 interp/plans_builtin 补——此处只校验给定的形状）
    budget = plan.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            raise ValueError(
                f"plan['budget'] 必须是 dict，实际: {type(budget).__name__}"
            )
        for key, value in budget.items():
            if key not in budgets.BUDGET_KEYS:
                raise ValueError(
                    f"plan['budget'] 含未知键 {key!r}（合法键: {budgets.BUDGET_KEYS}）"
                )
            _require_int("<budget>", key, value, minimum=0)

    seen_ids: set[str] = set()
    produced: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] 必须是 dict，实际: {type(step).__name__}")
        for key in step:
            if key not in _STEP_KEYS:
                raise ValueError(
                    f"steps[{i}] 含未知键 {key!r}（合法键: {_STEP_KEYS}）"
                )

        # ① 步 id：非空 str + 全 plan 唯一（run.json steps[].id 同名，重复即歧义）
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError(f"steps[{i}].id 必须是非空字符串，实际: {step_id!r}")
        if step_id in seen_ids:
            raise ValueError(f"步骤 id 重复: {step_id!r}（每步 id 须在 plan 内唯一）")
        seen_ids.add(step_id)

        # ② task 必须在 TASKS 白名单（路由/风格/覆盖/方言继承的唯一入口键）
        task = step.get("task")
        if not isinstance(task, str) or not task:
            raise ValueError(f"步骤 {step_id!r}: task 必须是非空字符串，实际: {task!r}")
        if task not in allowed_tasks:
            raise ValueError(
                f"步骤 {step_id!r}: 任务 {task!r} 不在 TASKS 白名单"
                f"（可用: {'/'.join(allowed_tasks)}）"
            )

        # ③ out：非空 str + 全局唯一（后步覆盖前步会让存证与引用双双失真）
        out = step.get("out")
        if not isinstance(out, str) or not out.strip():
            raise ValueError(f"步骤 {step_id!r}: out 必须是非空字符串，实际: {out!r}")
        if out in produced:
            raise ValueError(f"步骤 {step_id!r}: out 槽名重复: {out!r}（槽位全局唯一）")

        # ④ max_out 必填正整数；budget_in/retries/cache 可选但类型钉死
        if "max_out" not in step:
            raise ValueError(f"步骤 {step_id!r}: 缺少必填键 max_out")
        _require_int(step_id, "max_out", step.get("max_out"), minimum=1)
        if "budget_in" in step:
            _require_int(step_id, "budget_in", step.get("budget_in"), minimum=1)
        if "retries" in step:
            retries = step.get("retries")
            if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
                raise ValueError(
                    f"步骤 {step_id!r}: retries 必须是 ≥0 的整数，实际: {retries!r}"
                )
        if "cache" in step and not isinstance(step.get("cache"), bool):
            raise ValueError(
                f"步骤 {step_id!r}: cache 必须是布尔值，实际: {step.get('cache')!r}"
            )

        # ⑤ inputs 文法（悬空/后方 {{steps.x}} 在此拒）
        refs = _validate_inputs(step_id, step.get("inputs", {}))

        # ⑥ carry：tail:N + 只许引用**已产出**槽位
        _validate_carry(step_id, step.get("carry"), produced)

        # ⑦ {{steps.x}} 引用解析（只许向前——线性无环的等价校验）
        _validate_step_refs(step_id, refs, produced)

        produced.add(out)
