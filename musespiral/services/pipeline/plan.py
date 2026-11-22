"""pipeline 包：plan（阶段一解析校验——零 run 目录、零 job）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的 plan 簇：
``plan_pipeline`` 三道校验（正典 §5.1）——manifest 可读（按 id 索引
scan_plugins 已过三道门的插件区）+ 相邻契约接得上
（core.plugins.manifest.validate_pipeline_contract 唯一权威）+ 权限并集
复核。**此阶段失败不产生 run 目录**（PipelinePlanError 构造期抛，零 run
零 job——agent_service/pipeline_service 同款纪律）。

控制流扩展（ARCH-4 §一/§二，2026-11-22）——**刻意不做图灵完备 DSL**：
switch 只支持「单条件 → 多路由」，loop 只支持「对列表逐项」，两条原语
其余一概不做（无并行/无子程序/无表达式语言）。节点类型可显式
``"type": "switch"/"loop"``，也可由控制键推断（branch/condition →
switch；loop_over/body → loop；两族并存无法推断即红）：

- switch 节点 ``{"id", "type": "switch", "condition"|"plugin", "branch":
  {路由名: [子节点…]}}``。二选一：``condition`` 是引擎求值的**单条比较**
  表达式（``<节点>.<端口>[.键…] <op> <JSON 字面值>``，evaluate_condition；
  结果 true/false 即路由名），或 ``plugin`` 指向 kind=control 插件（其
  ``route`` 输出即路由名）。运行期引擎按 route 值执行且只执行选中分支。
- loop 节点 ``{"id", "type": "loop", "loop_over": "<节点>[.<端口>]",
  "body": [子节点…], "max_iters": 100}``——引擎原生（无 plugin 键，写了
  即红）：对上游列表产物逐项执行 body 子链，body 节点用 ``{"$item": true}``
  接线声明「注入当前项」，loop 的输出端口=body 末节点输出端口（同名同型，
  值=逐项合并列表）。``max_iters`` 防死循环门，默认
  ``MAX_LOOP_ITERS``（100），超出判该节点失败（可读）。

静态校验按「链 + 执行序前缀」递归（_validate_tree）：source 只能链首/
sink 只能链尾规则对每条链（顶层链/每条分支/body）独立成立；供给检查
的前缀=执行序更早节点。loop 的 loop_over 引用解析对**计划期供给序**
（ordered：前置节点 < body 子树 < loop 自身）——body 不能消费 loop
合并产物（迭代期尚不存在），后置节点可以。

拓扑执行（engine）/重生成（regenerate）/LRU 回收（prune）/jobs 接驳
（jobs）见各自子模块；包门面与全量符号面见 ``musespiral.services.pipeline``
的 ``__init__``。
"""
from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ...core.plugins.manifest import (
    PERMISSIONS,
    Plugin,
    Port,
    Runtime,
    parse_manifest,
    validate_pipeline_contract,
)
from .. import plugins_service


class PipelinePlanError(ValueError):
    """阶段一校验失败（构造期即抛——零 run 目录、零 job）。"""


# ── 控制流常量与条件表达式（ARCH-4；刻意最小：单条比较，无表达式语言）──

# loop 防死循环门的默认上限（节点可用 max_iters 覆盖；ARCH-4 硬要求 6）
MAX_LOOP_ITERS = 100

_COND_RE = re.compile(r"^\s*(\S+?)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")
_COND_OPS = {
    "==": operator.eq, "!=": operator.ne,
    ">=": operator.ge, "<=": operator.le,
    ">": operator.gt, "<": operator.lt,
}


class _ConditionError(ValueError):
    """switch 条件表达式语法/求值失败——消息可读，plan 期与运行期共用。"""


def _parse_condition(expr: str) -> tuple[list[str], str, Any]:
    """解析一条比较表达式 → (路径段, 操作符, JSON 字面值)；不识别即 _ConditionError。

    语法=``<节点>.<端口>[.<键>…] <op> <字面值>``，op ∈ {==, !=, >=, <=,
    >, <}，字面值必须是 JSON（字符串带引号）。无函数调用/算术/逻辑组合
    ——那是另一个量级的语言设计，本期刻意不做（ARCH-4 架构师裁决）。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise _ConditionError("condition 须为非空比较表达式 <节点>.<端口>[.键…] <op> <JSON 字面值>")
    m = _COND_RE.match(expr)
    if m is None:
        raise _ConditionError(
            f"condition 语法不识别: {expr!r}"
            "（支持单条比较 <路径> <==|!=|>=|<=|>|<> <JSON 字面值>；字符串字面值须带引号）"
        )
    path, op, literal = m.group(1), m.group(2), m.group(3)
    try:
        value = json.loads(literal)
    except json.JSONDecodeError:
        raise _ConditionError(
            f"condition 字面值须为 JSON: {literal!r}（字符串须带引号，如 \"long\"）"
        ) from None
    return path.split("."), op, value


def check_condition(expr: str) -> str | None:
    """条件表达式语法静态检查（plan 期红）→ 错误消息；合法返回 None。"""
    try:
        _parse_condition(expr)
    except _ConditionError as exc:
        return str(exc)
    return None


def evaluate_condition(expr: str, context: dict[str, dict[str, Any]]) -> bool:
    """求值 switch 条件表达式（引擎运行期调用）→ 布尔（true/false 即路由名）。

    context 形状 ``{节点 id: {端口: 值}}``（engine._condition_context 从
    workspace 收集）。路径解析：首段=节点 id、次段=端口名、其余=逐层
    dict 键下钻（列表下标不支持——最小语义）。任何一步解析不到都是
    _ConditionError 可读消息，不抛裸 KeyError。
    """
    parts, op, literal = _parse_condition(expr)
    if len(parts) < 2:
        raise _ConditionError(f"condition 路径须至少到端口级 <节点>.<端口>: {expr!r}")
    node_id, port = parts[0], parts[1]
    produced = context.get(node_id)
    if produced is None:
        raise _ConditionError(
            f"condition 引用的节点 {node_id!r} 无产出（不存在/未执行/无输出端口）: {expr!r}"
        )
    if port not in produced:
        raise _ConditionError(
            f"condition 引用的节点 {node_id!r} 无输出端口 {port!r}（有: {sorted(produced)}）: {expr!r}"
        )
    value = produced[port]
    for key in parts[2:]:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise _ConditionError(
                f"condition 路径在键 {key!r} 处解析失败（仅支持 dict 键下钻）: {expr!r}"
            )
    try:
        return bool(_COND_OPS[op](value, literal))
    except TypeError:
        raise _ConditionError(
            f"condition 比较双方类型不可比: {value!r} {op} {literal!r}"
        ) from None


# ── 阶段一：解析校验（零 run 目录）─────────────────────────────────


@dataclass(frozen=True)
class PlannedNode:
    """阶段一产物：节点 id + Plugin 契约 + 插件目录 + 合并后 config + 接线。

    wiring（原始节点 dict 的 ``inputs`` 键）：``{输入端口: {"node": id,
    "port": 名}}`` 显式接线，或 ``{输入端口: 字面值}`` 直供（首节点
    source 的运行级参数走此路）。缺省键由 _wire_input 自动接线。

    控制流扩展（ARCH-4）：``node_type`` 为 "switch"/"loop" 时节点由引擎
    按控制语义执行——switch 可携带真实 control 插件（plugin 形式）或
    合成契约插件（condition 形式，``builtin/switch``）；loop 一律合成
    ``builtin/loop`` 契约插件（inputs=loop_over 引用端口，outputs=body
    末节点输出端口的镜像）。``spec`` 保留原始归一节点 dict——meta.json
    node_specs 据此保真，重生成可重建控制流树。
    """

    id: str
    plugin: Plugin
    plugin_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    wiring: dict[str, Any] = field(default_factory=dict)
    node_type: str = ""  # ""=经典插件节点 | "switch" | "loop"
    condition: str = ""  # switch 条件形式的表达式（插件形式为空串）
    branch: dict[str, tuple["PlannedNode", ...]] | None = None
    body: tuple["PlannedNode", ...] | None = None
    loop_over: tuple[str, str] | None = None  # (上游节点 id, 端口名)
    max_iters: int = MAX_LOOP_ITERS
    spec: dict[str, Any] = field(default_factory=dict)


def _synthetic_control(
    plugin_id: str, *, inputs: dict[str, Port], outputs: dict[str, Port]
) -> Plugin:
    """引擎原生控制节点的合成契约插件（不落插件区、入口永不执行）。

    用途：PlannedNode.plugin 非空契约、meta.json 节点/插件清单可读、
    validate_pipeline_contract 供给校验（loop 的 loop_over 输入与合并
    输出面）。entry 是标记值——引擎对控制节点走专用执行路径。
    """
    return Plugin(
        id=plugin_id,
        version="0.0.0",
        kind="control",
        inputs=inputs,
        outputs=outputs,
        runtime=Runtime(type="python", entry="builtin:control"),
    )


def _merge_config(node: dict[str, Any], plugin: Plugin) -> dict[str, Any]:
    """config 合并：节点 config 覆盖 manifest.config_schema 声明的 default。

    v1 只填 default 不做深校验（JSON-Schema 子集评估留给后续期，见模块
    docstring 诚实边界）——缺键即 default，显式键原样透传。
    """
    merged: dict[str, Any] = {}
    for key, spec in (plugin.config_schema or {}).items():
        if isinstance(spec, dict) and "default" in spec:
            merged[key] = spec["default"]
    merged.update(node.get("config") or {})
    return merged


def _normalize_nodes(nodes: Iterable[dict[str, Any] | str]) -> list[dict[str, Any]]:
    """节点序列归一：str 视为裸插件 id（无 config/接线）；dict 原样。"""
    out: list[dict[str, Any]] = []
    for n in nodes:
        out.append({"plugin": n} if isinstance(n, str) else dict(n))
    return out


def _claim_id(
    node: dict[str, Any], i: int, label: str,
    seen_ids: set[str], problems: list[str],
) -> str | None:
    """节点 id 认领：缺省合成 + 全树唯一（跨分支/body 也查重——workspace
    按 node_id 存取，重名互相覆盖）。重复返回 None（该节点跳过）。"""
    node_id = str(node.get("id") or f"node-{i + 1}")
    if node_id in seen_ids:
        problems.append(
            f"{label} 节点 id 重复: {node_id!r}（workspace 按 node_id 存取，重复会互相覆盖）"
        )
        return None
    seen_ids.add(node_id)
    return node_id


def _plan_classic(
    node: dict[str, Any], i: int, label: str, by_id: dict[str, dict[str, Any]],
    problems: list[str], seen_ids: set[str], *, allow_item: bool,
) -> PlannedNode | None:
    """经典插件节点规划（ARCH-2 原逻辑抽取；控制流键误用即红）。"""
    node_id = _claim_id(node, i, label, seen_ids, problems)
    if node_id is None:
        return None
    for key in ("branch", "body", "loop_over", "condition", "max_iters"):
        if key in node:
            problems.append(
                f"{label} {node_id}: 经典插件节点不接受控制流键 {key!r}"
                "（控制节点需显式 \"type\": \"switch\"/\"loop\"）"
            )
    wiring = dict(node.get("inputs") or {})
    for pname, wv in wiring.items():
        if isinstance(wv, dict) and wv.get("$item") and not allow_item:
            problems.append(
                f"{label} {node_id}: $item 注入只在 loop 的 body 子链内可用（端口 {pname!r}）"
            )
    plugin_id = node.get("plugin")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        problems.append(f"{label} {node_id}: 缺少 plugin（插件 id）")
        return None
    item = by_id.get(plugin_id)
    if item is None:
        problems.append(
            f"{label} {node_id}: 插件 {plugin_id!r} 不可用（未安装/manifest 红/"
            "目录名不符/入口缺失——详参 plugins list 的 skipped 清单）"
        )
        return None
    plugin, errs = parse_manifest(item["manifest"])
    if plugin is None:  # scan 已过校验，防御性兜底仍收敛为可读错
        problems.append(f"{label} {node_id}: manifest 复核失败: {'；'.join(errs)}")
        return None
    return PlannedNode(
        id=node_id,
        plugin=plugin,
        plugin_dir=Path(item["dir"]),
        config=_merge_config(node, plugin),
        wiring=wiring,
        spec=dict(node),
    )


def _plan_switch(
    node: dict[str, Any], i: int, label: str, by_id: dict[str, dict[str, Any]],
    problems: list[str], seen_ids: set[str], ordered: list["PlannedNode"],
) -> "PlannedNode | None":
    """switch 节点规划：condition/plugin 二选一 + branch 逐链递归。

    ordered 供给序：switch 先入（分支子链可消费其 route 输出），分支
    子树随后（dict 声明序）；未选中分支在运行期不执行，但供给校验按
    计划序放行（与「任意更早节点供给」的既有宽容一致，运行期未产出
    走可读 KeyError）。
    """
    for stray in ("loop_over", "body"):
        if stray in node:
            problems.append(
                f"{label}: switch 节点不接受 loop 键 {stray!r}（loop_over/body 属 loop）"
            )
    branch_raw = node.get("branch")
    if not isinstance(branch_raw, dict) or not branch_raw:
        problems.append(f"{label}: switch 节点需要非空 branch 映射 {{路由名: [子节点…]}}")
        branch_raw = {}
    for route, sub in branch_raw.items():
        if not isinstance(route, str) or not route.strip():
            problems.append(f"{label}: branch 路由名须为非空字符串: {route!r}")
        if not isinstance(sub, list) or not sub or not all(isinstance(n, dict) for n in sub):
            problems.append(f"{label}: branch[{route!r}] 须为非空子节点 dict 列表")
    cond = node.get("condition")
    pid = node.get("plugin")
    if cond is not None and pid is not None:
        problems.append(
            f"{label}: switch 的 condition 与 plugin 二选一"
            "（条件形式由引擎求值，插件形式由 control 插件输出 route）"
        )
        return None
    if cond is None and pid is None:
        problems.append(
            f"{label}: switch 节点缺 condition（比较表达式）或 plugin"
            "（control 插件 id）——二选一"
        )
        return None
    node_id = _claim_id(node, i, label, seen_ids, problems)
    if node_id is None:
        return None
    if cond is not None:
        err = check_condition(str(cond))
        if err:
            problems.append(f"{label} {node_id}: {err}")
            return None
        switch_plugin = _synthetic_control(
            "builtin/switch", inputs={}, outputs={"route": Port(type="string")}
        )
        switch_dir = Path(".")
    else:
        item = by_id.get(pid) if isinstance(pid, str) else None
        if item is None:
            problems.append(
                f"{label} {node_id}: 插件 {pid!r} 不可用（未安装/manifest 红/"
                "目录名不符/入口缺失——详参 plugins list 的 skipped 清单）"
            )
            return None
        plugin, errs = parse_manifest(item["manifest"])
        if plugin is None:
            problems.append(f"{label} {node_id}: manifest 复核失败: {'；'.join(errs)}")
            return None
        if plugin.kind != "control":
            problems.append(
                f"{label} {node_id}: switch 插件 {plugin.id!r} kind={plugin.kind!r}，"
                "须为 control（声明 route 输出供引擎选分支）"
            )
            return None
        switch_plugin = plugin
        switch_dir = Path(item["dir"])

    base = PlannedNode(
        id=node_id,
        plugin=switch_plugin,
        plugin_dir=switch_dir,
        config=_merge_config(node, switch_plugin),
        wiring=dict(node.get("inputs") or {}),
        node_type="switch",
        condition=str(cond) if cond is not None else "",
        spec=dict(node),
    )
    mark = len(ordered)  # 分支子树先规划（嵌套控制流自己维护 ordered），随后重排到 switch 之后
    branch_planned: dict[str, tuple[PlannedNode, ...]] = {}
    for route, sub in branch_raw.items():
        if not (isinstance(route, str) and route.strip()):
            continue
        if not (isinstance(sub, list) and sub and all(isinstance(n, dict) for n in sub)):
            continue
        subs = _plan_chain(
            sub, by_id, problems, seen_ids, ordered,
            allow_item=False, scope=f"{label}.branch[{route}]",
        )
        if subs:
            branch_planned[str(route)] = tuple(subs)
    subtree = ordered[mark:]
    del ordered[mark:]
    planned = replace(base, branch=branch_planned or None)
    ordered.append(planned)
    ordered.extend(subtree)
    return planned


def _resolve_loop_over(
    ref: str, ordered: Sequence[PlannedNode], label: str, problems: list[str]
) -> tuple[str, str, str] | None:
    """解析 loop_over（'<节点>' 或 '<节点>.<端口>'）→ (节点 id, 端口, 端口类型)。

    只解析 ordered（计划期供给序）中的更早节点；省端口要求该节点恰好
    一个输出端口（多个即列名让用户显式写）。"""
    ref = ref.strip()
    by_node: dict[str, PlannedNode] = {n.id: n for n in ordered}
    target: PlannedNode | None = None
    port: str | None = None
    if ref in by_node:
        target = by_node[ref]
    else:
        head, _, tail = ref.rpartition(".")
        if head:
            candidate = by_node.get(head)
            if candidate is not None:
                target = candidate
                port = tail
    if target is None:
        problems.append(
            f"{label}: loop_over {ref!r} 解析不到更早节点"
            "（写 <节点id> 或 <节点id>.<端口>；被引用节点须出现在 loop 之前）"
        )
        return None
    outputs = target.plugin.outputs
    if port is None:
        if len(outputs) == 1:
            port = next(iter(outputs))
        else:
            problems.append(
                f"{label}: loop_over {ref!r} 的节点有多个输出端口 {sorted(outputs)}"
                "——请显式写 <节点>.<端口>"
            )
            return None
    out = outputs.get(port)
    if out is None:
        problems.append(
            f"{label}: loop_over {ref!r} 端口 {port!r} 不存在（节点输出: {sorted(outputs)}）"
        )
        return None
    return target.id, port, out.type


def _plan_loop(
    node: dict[str, Any], i: int, label: str, by_id: dict[str, dict[str, Any]],
    problems: list[str], seen_ids: set[str], ordered: list["PlannedNode"],
) -> "PlannedNode | None":
    """loop 节点规划：loop_over 解析 + body 递归（$item 放行）+ max_iters 门。"""
    for stray in ("branch", "condition"):
        if stray in node:
            problems.append(
                f"{label}: loop 节点不接受 switch 键 {stray!r}（branch/condition 属 switch）"
            )
    if node.get("plugin") is not None:
        problems.append(f"{label}: loop 节点由引擎原生实现，不接受 plugin 键")
    lo = node.get("loop_over")
    if not isinstance(lo, str) or not lo.strip():
        problems.append(f"{label}: loop 节点需要 loop_over（'<节点>' 或 '<节点>.<端口>'）")
        lo = None
    body = node.get("body")
    if not isinstance(body, list) or not body or not all(isinstance(n, dict) for n in body):
        problems.append(f"{label}: loop 节点需要非空 body 子节点 dict 列表")
        body = None
    max_iters = node.get("max_iters", MAX_LOOP_ITERS)
    if isinstance(max_iters, bool) or not isinstance(max_iters, int) or max_iters < 1:
        problems.append(
            f"{label}: max_iters 须为 ≥1 整数（默认 {MAX_LOOP_ITERS}），实为 {max_iters!r}"
        )
        max_iters = None
    node_id = _claim_id(node, i, label, seen_ids, problems)
    if node_id is None or lo is None or body is None or max_iters is None:
        return None

    # body 子树先规划（供给序：前置节点 < body 子树 < loop 自身——body
    # 迭代期消费不到 loop 合并产物，故 body 不允许引用 loop）。
    mark = len(ordered)
    body_planned = _plan_chain(
        body, by_id, problems, seen_ids, ordered,
        allow_item=True, scope=f"{label}.body",
    )
    subtree = ordered[mark:]
    del ordered[mark:]
    if not body_planned:
        return None  # body 内部问题已记入 problems
    ref = _resolve_loop_over(lo, ordered, label, problems)
    if ref is None:
        return None
    ref_node, ref_port, ref_type = ref
    tail = body_planned[-1]
    loop_plugin = _synthetic_control(
        "builtin/loop",
        inputs={ref_port: Port(type=ref_type, required=True)},
        outputs=dict(tail.plugin.outputs),  # 合并输出端口=body 末节点输出（同名同型）
    )
    spec = dict(node)
    spec.setdefault("max_iters", max_iters)  # 显式缺省落 spec（重生成保真）
    planned = PlannedNode(
        id=node_id,
        plugin=loop_plugin,
        plugin_dir=Path("."),
        node_type="loop",
        body=tuple(body_planned),
        loop_over=(ref_node, ref_port),
        max_iters=max_iters,
        spec=spec,
    )
    ordered.extend(subtree)
    ordered.append(planned)
    return planned


def _plan_chain(
    chain: list[dict[str, Any]], by_id: dict[str, dict[str, Any]],
    problems: list[str], seen_ids: set[str], ordered: list["PlannedNode"],
    *, allow_item: bool, scope: str,
) -> list["PlannedNode"]:
    """一条链（顶层/分支/body）的递归规划；控制流节点自带子树入 ordered。"""
    out: list[PlannedNode] = []
    for i, node in enumerate(chain):
        if not isinstance(node, dict):
            problems.append(f"{scope}[{i}] 节点须为对象")
            continue
        node_type = node.get("type")
        label = f"{scope}[{i}]"
        if node_type in (None, ""):
            # 类型推断（宽容读）：branch/condition → switch，loop_over/body →
            # loop——正典 DSL 形 {id, plugin, branch} 不写 type 也能跑；两族
            # 键并存无法推断 → 可读红，要求显式 type。
            has_switch_keys = "branch" in node or "condition" in node
            has_loop_keys = "loop_over" in node or "body" in node
            if has_switch_keys and has_loop_keys:
                problems.append(
                    f"{label}: 节点同时携带 switch 键（branch/condition）与 loop 键"
                    "（loop_over/body），无法推断类型——请显式写 \"type\""
                )
                continue
            if has_switch_keys:
                node_type = "switch"
            elif has_loop_keys:
                node_type = "loop"
        if node_type in (None, ""):
            planned = _plan_classic(
                node, i, label, by_id, problems, seen_ids, allow_item=allow_item
            )
            if planned is not None:
                out.append(planned)
                ordered.append(planned)
        elif node_type == "switch":
            planned = _plan_switch(node, i, label, by_id, problems, seen_ids, ordered)
            if planned is not None:
                out.append(planned)
        elif node_type == "loop":
            planned = _plan_loop(node, i, label, by_id, problems, seen_ids, ordered)
            if planned is not None:
                out.append(planned)
        else:
            problems.append(
                f"{label}: 未知节点类型 {node_type!r}"
                "（支持 \"switch\" / \"loop\"，或省略 type 走经典插件节点）"
            )
    return out


def _iter_tree(planned: Iterable[PlannedNode]) -> Iterator[PlannedNode]:
    """深度遍历全部计划节点（顶层 + 分支子树 + body 子树）——权限并集用。"""
    for node in planned:
        yield node
        for sub in (node.branch or {}).values():
            yield from _iter_tree(sub)
        if node.body:
            yield from _iter_tree(node.body)


def _contract_plugin(node: PlannedNode) -> Plugin:
    """校验投影：$item 注入端口放宽 required（引擎迭代期注入，非流水线供给）。

    返回原插件或深拷贝（只动投影，不碰真 manifest）——core 层
    validate_pipeline_contract 的供给规则因此对 body 首节点不误红。
    """
    item_ports = [
        p for p, wv in node.wiring.items()
        if isinstance(wv, dict) and wv.get("$item")
    ]
    if not item_ports:
        return node.plugin
    clone = node.plugin.model_copy(deep=True)
    for p in item_ports:
        port = clone.inputs.get(p)
        if port is not None:
            port.required = False
    return clone


def _validate_chain(
    prefix: Sequence[PlannedNode], chain: Sequence[PlannedNode]
) -> list[str]:
    """单链契约校验：前缀（执行序供给上下文）+ 链节点拼成一条展平链，
    交给 core 的 validate_pipeline_contract（kind 位置/供给/重复 id 唯一
    权威）。source 只能链首/sink 只能链尾因此对每条链独立成立。"""
    nodes = [{"id": n.id, "plugin": _contract_plugin(n)} for n in [*prefix, *chain]]
    return validate_pipeline_contract(nodes)


def _validate_tree(
    prefix: list[PlannedNode], chain: list[PlannedNode]
) -> list[str]:
    """控制流树逐链校验：分支链前缀=switch 之前 + switch 自身（兄弟分支
    是备选不是前缀）；body 链前缀=loop 之前（loop 合并产物迭代期不存在）。"""
    errors = _validate_chain(prefix, chain)
    flat = list(prefix)
    for node in chain:
        if node.node_type == "switch" and node.branch:
            for sub in node.branch.values():
                errors += _validate_tree([*flat, node], list(sub))
        elif node.node_type == "loop" and node.body:
            errors += _validate_tree(list(flat), list(node.body))
        flat.append(node)
    return errors


def plan_pipeline(
    nodes: Iterable[dict[str, Any] | str], *, plugins_root: Path | None = None
) -> list[PlannedNode]:
    """阶段一：解析校验 → PlannedNode 列表；任一不过抛 PipelinePlanError。

    **此函数不创建任何 run 目录**（阶段一失败零副作用的实现基点）。
    校验（正典 §5.1 + ARCH-4 控制流）：
      1. 每个插件 manifest 可读（从插件区按 id 解析——scan_plugins 已过
         parse_manifest/目录名核对/入口存在性三道门，这里按 id 索引）；
         控制流节点静态校验（switch 二选一/branch 形状/loop_over 解析/
         max_iters/$item 作用域/全树 id 唯一/条件表达式语法）；
      2. 相邻契约接得上（validate_pipeline_contract：kind 位置/必需端口
         供给/重复 id——core 层唯一权威；控制流按 _validate_tree 逐链）；
      3. 权限并集在允许范围内（PERMISSIONS 表——按全树收集复核）。

    返回**顶层** PlannedNode 列表（控制节点携带嵌套 branch/body）——
    引擎按 node_type 分派执行，嵌套子链随控制节点执行。
    """
    node_list = _normalize_nodes(nodes)
    if not node_list:
        raise PipelinePlanError("流水线至少需要一个节点")

    valid, _skipped = plugins_service.scan_plugins(plugins_root)
    by_id: dict[str, dict[str, Any]] = {item["manifest"]["id"]: item for item in valid}

    problems: list[str] = []
    seen_ids: set[str] = set()
    ordered: list[PlannedNode] = []  # 计划期供给序（loop_over 解析基准）
    planned = _plan_chain(
        node_list, by_id, problems, seen_ids, ordered,
        allow_item=False, scope="流水线",
    )
    if problems:
        raise PipelinePlanError("；".join(problems))

    contract_errors = _validate_tree([], planned)
    if contract_errors:
        raise PipelinePlanError("契约校验未过: " + "；".join(contract_errors))

    union_perms = {p for node in _iter_tree(planned) for p in node.plugin.permissions}
    unknown_perms = union_perms - set(PERMISSIONS)
    if unknown_perms:
        raise PipelinePlanError(
            f"权限并集含未知权限 {sorted(unknown_perms)}（允许表: {list(PERMISSIONS)}）"
        )
    # exec runtime 权限门（ARCH-4 §三）：exec 插件必须显式声明 exec:subprocess，
    # 否则构造期即拒（零 run 目录）；与 run 期 exec_runtime.require_exec_permission
    # 同判定（二道门，run 期防御兜底）。
    for node in _iter_tree(planned):
        if node.plugin.runtime.type == "exec" and "exec:subprocess" not in node.plugin.permissions:
            raise PipelinePlanError(
                f"节点 {node.id!r} 的 exec 插件 {node.plugin.id!r} 未声明权限 "
                "'exec:subprocess'——拒绝执行（请在 manifest.permissions 显式声明）"
            )
    return planned
