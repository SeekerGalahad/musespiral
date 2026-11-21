"""编排解释器（Phase D F75.2，L2 纯函数零 I/O —— 架构纲 D1 的落点）。

`run_plan(plan, ctx, chat_fn) -> RunResult` 是**顺序折叠（fold）**：同一份
plan + 同一 ctx + 同一 chat_fn 响应序列 ⇒ 逐字节相同的 RunResult。无 I/O、
无时钟、无全局态、无并发——「推理」全部留在每步的 prompt 与任务路由里，
控制流是确定性代码（设计 §〇「不做自主智能体循环」）。

**依赖注入（关键）**：`chat_fn(prompt, task=…, cache=…) -> {text, cache_hit,
chars_in, chars_out}` 是唯一 AI 原语。生产环境由 `services/agent_service`
注入 `gateway.chat_policed` 的闭包；测试注入脚本化假件。因此 core/agent
**零 import infra**（架构门 F75-7 机器校验），也天然零 mock HTTP。

**为什么 interp 不 import `AIError` / `BudgetExceeded`**：这两个类定义在
`infra/ai`（L1），而 core/agent（L2）不得向上依赖。步级失败因此走**鸭子
类型协议**（下方 `_is_budget_exceeded`）：任何异常只要带 `budget_exceeded=True`
类/实例属性（或类名恰为 `BudgetExceeded`）即视为限额态——不重试、立即终态。
`infra/ai/budget.BudgetExceeded` 按该协议定义，两层的解耦点就是这一个布尔。

**执行序**（设计 §二 F75.2 伪代码逐条对应）：

1. `validate_plan`（构造期全枚举拒；运行期再校一次，防手工构造绕过）
2. 逐步：① run 级预算**步前对账** → ② 槽位装配 → ③ carry 尾部携带 →
   ④ 单步输入截断 → ⑤ chat_fn（含 retries）→ ⑥ BudgetExceeded 特判 →
   ⑦ 输出截断 → ⑧ cache_hit 免计 run 预算 → ⑨ 进槽位池 + steps_meta
3. `_finish` 产 RunResult（五键：status/steps/slots/budget/error）

**步前对账是「前瞻可负担性」而非「已超支」**：某步最大可能花费上界可知
（输入 ≤ `budget_in`、输出 ≤ `max_out`），因此在启动前判 `已用 + 本步上界
> 上限` 即止——不会出现「跑完这一步才发现钱不够」。这同时让 F75-5 用例
（`out_chars=2500`、两步 `max_out=2000` → 第二步前触停）的期望成立：
`2000 + 2000 > 2500`。steps 键同口径（`已用步数 + 1 > 上限`），故 8 步
计划配 8 步预算可跑满。
"""
from __future__ import annotations

from typing import Any, Callable

from . import budgets
from .plan import slot_kind, validate_plan

# RunResult.status 三态（与 §1.1 run.json status 同枚举）
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_BUDGET_STOPPED = "budget_stopped"

STATUSES: tuple[str, ...] = (STATUS_DONE, STATUS_FAILED, STATUS_BUDGET_STOPPED)

# steps_meta 单项状态（run.json steps[].status；budget_stopped 不产 meta 项）
STEP_DONE = "done"
STEP_FAILED = "failed"

ChatFn = Callable[..., dict[str, Any]]


# ── 限额态协议（跨层解耦点）──────────────────────────────────────────


def _is_budget_exceeded(exc: BaseException) -> bool:
    """限额态判定（鸭子类型协议，见模块 docstring）。

    ① 显式协议属性 `budget_exceeded is True`（infra 侧显式声明，优先）；
    ② 类名恰为 `BudgetExceeded`（测试假件/未来实现的兜底）。
    """
    if getattr(exc, "budget_exceeded", False) is True:
        return True
    return type(exc).__name__ == "BudgetExceeded"


# ── 槽位装配（纯函数）────────────────────────────────────────────────


def render_value(value: Any, ctx: dict[str, Any], slots: dict[str, str]) -> str:
    """递归求值单个输入值（str 走槽位文法；容器递归；其他 str() 化）。

    槽位文法三条（与 `plan.slot_kind` 同源）：
      `{{materials[i]}}` → ctx["materials"][i]
      `{{params.<k>}}`   → ctx["params"][k]
      `{{steps.<out>}}`  → slots[<out>]（已完成步输出全文）
    含 `{{…}}` 但不匹配合法文法 → ValueError（构造期已拦，此处兜底）；
    materials 下标越界 / params 键缺失 / steps 槽缺失 → ValueError（可读）。
    """
    if isinstance(value, str):
        kind = slot_kind(value)
        if kind is None:
            return value  # 纯字面量（含 {{…}} 的非法形态已由 validate_plan 拒）
        src, key = kind
        if src == "materials":
            materials = ctx.get("materials") or []
            idx = key if isinstance(key, int) else 0
            if idx >= len(materials):
                raise ValueError(
                    f"槽位引用越界: {value!r}（ctx.materials 只有 {len(materials)} 项）"
                )
            return str(materials[idx])
        if src == "params":
            params = ctx.get("params") or {}
            if key not in params:
                raise ValueError(
                    f"槽位引用缺失: {value!r}（ctx.params 无键 {key!r}）"
                )
            return str(params[key])
        # steps
        if key not in slots:
            raise ValueError(
                f"槽位引用缺失: {value!r}（已完成步未产出槽 {key!r}）"
            )
        return slots[key]  # type: ignore[index]
    if isinstance(value, dict):
        return "\n\n".join(
            part for part in (render_value(v, ctx, slots) for v in value.values()) if part
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(
            part for part in (render_value(v, ctx, slots) for v in value) if part
        )
    if value is None:
        return ""
    return str(value)


def assemble(inputs: dict[str, Any], ctx: dict[str, Any], slots: dict[str, str]) -> str:
    """步输入装配：按插入序取各值的求值结果，非空项以空行连接。

    入参（键名）不渲染进 prompt——键是结构性标签，prompt 语义由各值自身的
    指令文本承载（计划作者控制，零隐式措辞）。插入序保持（不 sort），
    故结果确定。
    """
    parts = [render_value(v, ctx, slots) for v in inputs.values()]
    return "\n\n".join(part for part in parts if part)


# ── run 级预算对账（前瞻可负担性）────────────────────────────────────


def _effective_budget(plan: dict[str, Any]) -> dict[str, int]:
    """取 run 级预算：plan 给定键优先，缺键回落到 budgets 常量。"""
    given = plan.get("budget") or {}
    return {
        "steps": given.get("steps", budgets.RUN_STEPS),
        "in_chars": given.get("in_chars", budgets.RUN_IN_CHARS),
        "out_chars": given.get("out_chars", budgets.RUN_OUT_CHARS),
    }


def _step_upper_bound(step: dict[str, Any]) -> dict[str, int]:
    """本步可预知的花费上界：输入 ≤ budget_in，输出 ≤ max_out。"""
    return {
        "in_chars": step.get("budget_in", budgets.STEP_BUDGET),
        "out_chars": step["max_out"],
    }


def _budget_stop_key(
    budget: dict[str, int], used: dict[str, int], step: dict[str, Any]
) -> str | None:
    """步前前瞻对账：任一键「已用 + 本步上界 > 上限」→ 返回该键名。"""
    if used["steps"] + 1 > budget["steps"]:
        return "steps"
    bound = _step_upper_bound(step)
    if used["in_chars"] + bound["in_chars"] > budget["in_chars"]:
        return "in_chars"
    if used["out_chars"] + bound["out_chars"] > budget["out_chars"]:
        return "out_chars"
    return None


# ── 终态构造 ─────────────────────────────────────────────────────────


def _finish(
    status: str,
    steps_meta: list[dict[str, Any]],
    slots: dict[str, str],
    used: dict[str, int],
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """RunResult（L2 纯数据）：agent_service 据此落 run.json + steps/<n>.json。

    `slots` 为中间结果全量（浅拷贝——调用方拿到的结果不与解释器内部共享
    可变引用）；`error` 仅 failed 态非 None（budget_stopped 是守门非错误）。
    """
    return {
        "status": status,
        "steps": list(steps_meta),
        "slots": dict(slots),
        "budget": dict(used),
        "error": error,
    }


# ── 解释器主体 ───────────────────────────────────────────────────────


def run_plan(
    plan: Any,
    ctx: dict[str, Any],
    chat_fn: ChatFn,
    *,
    tasks: tuple[str, ...],
) -> dict[str, Any]:
    """解释执行一份声明式计划（纯函数）→ RunResult。

    参数：
      plan    Plan dict（见 `plan.py` schema）
      ctx     {"materials": [...], "params": {...}} —— run 输入（服务层装配）
      chat_fn 唯一 AI 原语（gateway 闭包或脚本化假件；见模块 docstring）
      tasks   TASKS 白名单（**必填**——策略由调用方注入，见 `plan.py` docstring）

    返回 RunResult：{"status", "steps", "slots", "budget", "error"}。
    run_id 由服务层落盘时回填（解释器不产 id——它无时钟/无随机源）。
    """
    validate_plan(plan, tasks=tasks)

    budget = _effective_budget(plan)
    slots: dict[str, str] = {}
    used: dict[str, int] = {"steps": 0, "in_chars": 0, "out_chars": 0}
    steps_meta: list[dict[str, Any]] = []

    for step in plan["steps"]:
        # ① 步前前瞻对账（三键任一不可负担即止）
        stopped_by = _budget_stop_key(budget, used, step)
        if stopped_by is not None:
            return _finish(STATUS_BUDGET_STOPPED, steps_meta, slots, used)
        used["steps"] += 1

        # ② 槽位装配
        prompt = assemble(step.get("inputs") or {}, ctx, slots)

        # ③ 声明式尾部携带（后步只见前步输出尾段，永不全 transcript 累积）
        for out_name, spec in (step.get("carry") or {}).items():
            if out_name not in slots:  # validate_plan 已保证；手工构造绕过时兜底
                raise ValueError(
                    f"步骤 {step['id']!r}: carry 引用的槽位 {out_name!r} 尚未产出"
                )
            prompt += "\n" + budgets.tail(slots[out_name], spec)

        # ④ 单步输入预算（超限截断 + 固定文案）
        prompt = budgets.clip(prompt, step.get("budget_in", budgets.STEP_BUDGET))

        # ⑤ 调用（retries 次重试；每 attempt 一行 usage 归 gateway）
        text: str | None = None
        res: dict[str, Any] | None = None
        last_err: BaseException | None = None
        attempts = 0
        for _attempt in range(int(step.get("retries", 0)) + 1):
            attempts = _attempt + 1
            try:
                res = chat_fn(prompt, task=step["task"], cache=bool(step.get("cache", False)))
                candidate = res.get("text") if isinstance(res, dict) else None
                if isinstance(candidate, str) and candidate:
                    text = candidate
                    break
                # 空响应：按可重试失败处理（与 chat() 的「AI 响应为空」同语义）
                last_err = ValueError("空响应")
            except BaseException as exc:  # noqa: BLE001 - 步级全吞，run 级才终态
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # ⑥ 限额态特判：不因重试改变，立即终态且不计后续 attempt
                if _is_budget_exceeded(exc):
                    return _finish(
                        STATUS_FAILED,
                        steps_meta,
                        slots,
                        used,
                        error=f"预算触顶: {exc}",
                    )
                last_err = exc

        if text is None:
            return _finish(
                STATUS_FAILED,
                steps_meta,
                slots,
                used,
                error=f"步骤 {step['id']} 重试后仍失败: {last_err}",
            )

        # ⑦ 输出截断（超 max_out 处前置固定文案）
        text = budgets.clip(text, step["max_out"])

        # ⑧ 缓存命中免计 run 预算（F79「命中不出账」的 run 侧对偶）
        cache_hit = bool(res.get("cache_hit")) if isinstance(res, dict) else False
        if not cache_hit:
            used["in_chars"] += int(res.get("chars_in") or 0) if isinstance(res, dict) else 0
            used["out_chars"] += len(text)

        # ⑨ 中间结果进槽位池 + 步级存证
        slots[step["out"]] = text
        steps_meta.append(
            {
                "id": step["id"],
                "task": step["task"],
                "status": STEP_DONE,
                "chars_in": int(res.get("chars_in") or 0) if isinstance(res, dict) else 0,
                "chars_out": len(text),
                "cache_hit": cache_hit,
                "attempts": attempts,
            }
        )

    return _finish(STATUS_DONE, steps_meta, slots, used)
