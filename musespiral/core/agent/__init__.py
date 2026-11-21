"""编排智能体 Domain 层（Phase D F75-F77，L2 纯，2026-11-16）。

四个模块全部零 I/O、零时钟、零全局态（架构门 F75-7 机器校验）：

- `budgets.py`       字符口径与三级预算常量**单一事实源**（+对账数学）
- `plan.py`          计划 schema 与构造期全枚举校验（+槽位文法解析）
- `interp.py`        `run_plan(plan, ctx, chat_fn)` 顺序折叠解释器（D1 落点）
- `plans_builtin.py` 内置计划构造器（synthesis_v1 / tag_batch）

**依赖注入纪律**（设计 §三-D1）：`chat_fn` 由调用方注入——生产环境是
`services/agent_service` 组装的 `gateway.chat_policed` 闭包，测试是脚本化
假件。故本包**零 import infra/services**，也零 mock HTTP 即可全量单测。

**DSL 不图灵完备**（设计 §〇）：计划是声明式数据，控制流在代码。槽位文法
只有三条（`{{materials[i]}}` / `{{params.<k>}}` / `{{steps.<out>}}`），
无表达式、无嵌套、无自由分支；`plan.validate_plan` 拒绝任何未知 `{{…}}`。
"""
from __future__ import annotations

from . import budgets, interp, plan, plans_builtin

__all__ = ["budgets", "interp", "plan", "plans_builtin"]
