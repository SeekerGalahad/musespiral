"""pipeline 包：engine（阶段二拓扑执行 + 阶段三收集 + 阶段四清理）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的 engine 簇：

- ``run_pipeline``——逐节点 ``gather inputs → check_input → execute →
  workspace.put``；末节点产物收集到 ``<run_dir>/output/``（仅成功态）；
  finally 清理临时目录（ctx.tmpdir 受管区）。**失败不清理 workspace**
  （用户要看现场）；成功态 workspace 也不删——保留策略=LRU 最近
  PRUNE_KEEP 次（见 prune 簇），引擎只写 meta.json 终态。v1 无引擎级
  子进程要清（python runtime 进程内；exec runtime 是 PL-7）。
- 控制流执行（ARCH-4，2026-11-22）——**刻意最小语义**：
  switch（单条件 → 多路由）：条件形式由 ``evaluate_condition`` 对上游
  产物求值（true/false 即路由名），插件形式读 control 插件的 ``route``
  输出；按 route 选 ``branch[route]`` 执行且只执行选中分支（其余分支
  不产生 step，log 留痕），选中分支跑完后顶层照常续跑（分支自然汇聚
  ——workspace 按 node_id 取数，未执行分支的节点被引用时走可读失败）。
  无对应路由的 route → switch 节点可读失败。condition 形式也把
  ``{"route": …}`` put 进 workspace（下游可消费路由值）。
  loop（对列表逐项）：``loop_over`` 引用上游列表产物逐项执行 body 子链
  （body 节点用 ``{"$item": true}`` 接线注入当前项）；``max_iters``
  （默认 ``plan.MAX_LOOP_ITERS``=100）超出判该节点失败（防死循环门，
  可读）；输出端口=body 末节点输出端口的镜像，值=逐项合并列表（顺序
  保持）。单项失败即停（失败即停纪律）；取消透传 PluginCancelled。
- 失败语义（沿用 pipeline_service 纪律）：失败即停；已完成步与失败步
  （含 reason）全部保留在 result.steps；节点返回值缺 manifest.outputs
  声明的键 → OutputContractError 判失败（reason 可读），额外键忽略不报错。
- 取消接 runner 协作式取消（R3.5 §2.2）：ctx.cancelled 读
  ``runner.is_cancelled(job_id)``；插件抛 PluginCancelled → run 级
  status="cancelled"（非 failed；job 侧按 batch_adapt 部分成功先例以
  done 收束、result 携带步级记录）。
- 节点执行件（_load_python_entry/_wire_input/_gather_inputs/_exec_node_step）
  与 run 目录族（_new_run_id/pipeline_runs_root）也归本簇——regenerate
  复用同一套件，不另造轮子。

exec 插件（ARCH-4 §三，2026-11-22）：runtime.type=exec 走 exec_runtime 子进程
——JSON→stdin/stdout、权限门（须声明 exec:subprocess）、超时强杀进程组、stderr
透传日志；python runtime 的 timeout_s 仍未强制（进程内无法安全打断线程）。

诚实边界（v1，记入正典 §十三）：runtime.timeout_s 未强制（进程内
python 无法安全打断线程，需线程级看门狗）；「不可 JSON 序列化」的非
bytes/PathLike 值以进程内对象引用（mem ref）保真——跨进程续读不支持
（单次 run 生命周期内取数正确）；loop 合并列表若含 bytes/Path 元素，
整个端口落 mem ref（进程内取数正确——body 文件产物按迭代号落盘
``<port>.iter<k>.bin``，逐迭代不互相覆盖；跨进程不复活）。

解析校验（plan）/workspace（workspace）/重生成（regenerate）/LRU 回收
（prune）/jobs 接驳（jobs）见各自子模块；包门面与全量符号面见
``musespiral.services.pipeline`` 的 ``__init__``。
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ... import config
from ...core.plugins.context import PluginCancelled
from ...core.plugins.manifest import Plugin, Port, expected_dirname
from ...core.plugins.types import types_compatible
from ...jobs.store import now_iso as _now_iso
from . import exec_runtime as _exec_runtime
from .plan import (
    PipelinePlanError,
    PlannedNode,
    _ConditionError,
    evaluate_condition,
    plan_pipeline,
)
from .workspace import _UNSAFE_FILENAME_RE, _PipelineContext, Workspace

# run 目录根：~/.musespiral/pipeline_runs/（随 MUSESPIRAL_HOME，测试传 run_root 隔离）
RUNS_DIRNAME = "pipeline_runs"

# run_id 形态：pl-<YYYYMMDD-HHMMSS>-<rand4>（agent_service run_id 同款纪律：
# 会话级唯一，rand4 防同秒并发撞名；run_id 直接拼路径，白名单回验防穿越）
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


class OutputContractError(RuntimeError):
    """节点返回值缺 manifest.outputs 声明的键——判该节点失败（reason 可读）。"""


class _NodeFailure(RuntimeError):
    """引擎内节点失败：消息已可读，直接进 step.detail。"""


# ── 阶段二：拓扑执行（线性）────────────────────────────────────────


def _load_python_entry(plugin: Plugin, plugin_dir: Path) -> Callable[..., Any]:
    """python runtime 入口加载：``file.py:func`` → 可调用（正典 §4.3/本批 req 11）。

    阶段一已核验入口文件存在（scan_plugins → loader.entry_file_ok）；
    importlib 按文件位置加载（模块名带插件 id 防多插件同文件名相互
    污染），函数缺失/不可调用 → 可读 _NodeFailure。
    """
    file_part, func_name = plugin.runtime.entry.split(":", 1)
    path = plugin_dir / file_part
    modname = (
        f"musespiral_plugin_{expected_dirname(plugin.id)}_"
        f"{_UNSAFE_FILENAME_RE.sub('_', file_part)}"
    )
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib 形态防御
        raise _NodeFailure(f"入口文件无法加载: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise _NodeFailure(
            f"入口 {plugin.runtime.entry!r} 的函数 {func_name!r} 不存在或不可调用"
        )
    return fn


def _wire_input(
    planned: Sequence[PlannedNode], index: int, port_name: str, port: Port,
    wiring: dict[str, Any],
) -> tuple[str, str] | None:
    """输入端口接线解析：显式 wiring > 同名就近 > 兼容类型就近。

    显式形态 ``{"node": id, "port": 名}``；缺省自动接线沿
    validate_pipeline_contract 的供给规则（任意更早节点），偏好同端口名，
    其次兼容类型（types_compatible 唯一权威），从最近的更早节点往回找
    ——线性链里「最近的同名输出」即用户直觉中的供给者。
    """
    explicit = wiring.get(port_name)
    if isinstance(explicit, dict) and explicit.get("node") and explicit.get("port"):
        return str(explicit["node"]), str(explicit["port"])
    for earlier in reversed(planned[:index]):
        out = earlier.plugin.outputs.get(port_name)
        if out is not None and types_compatible(out.type, port.type):
            return earlier.id, port_name
    for earlier in reversed(planned[:index]):
        for out_name, out in earlier.plugin.outputs.items():
            if types_compatible(out.type, port.type):
                return earlier.id, out_name
    return None


def _gather_inputs(
    planned: Sequence[PlannedNode], index: int, workspace: Workspace,
    loop_item: tuple[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """gather + check_input：拼齐节点 inputs（缺必需端口 → 可读 _NodeFailure）。

    供给优先级：``{"$item": true}`` 标记（loop body 专用——注入当前迭代项，
    loop_item=(loop 节点 id, 项值)；计划期已放宽该端口 required，标记
    出现在 body 之外会先被 plan 拒）> workspace 上游输出 > wiring 字面值
    （source 节点运行级参数）> 可选端口 default。必需端口三者皆无 →
    列名报错。
    第二返回值是 **接线面**（端口 → 供给节点 id）——随 ctx.upstream 注入，
    插件据此把「兄弟目录里的编辑版文件」限定到真实供给它的上游节点
    （mtime 全局扫描会跨节点串味，2026-11-20 复核判例）；字面值直供的
    端口不在面内（无节点目录可指）。
    """
    node = planned[index]
    wiring = node.wiring
    inputs: dict[str, Any] = {}
    upstream: dict[str, str] = {}
    missing: list[str] = []
    for port_name, port in node.plugin.inputs.items():
        wv = wiring.get(port_name)
        if isinstance(wv, dict) and wv.get("$item"):
            if loop_item is None:  # plan 期已拒；防御性兜底仍可读
                missing.append(f"{port_name!r}(type={port.type})[$item 不在 loop 迭代内]")
                continue
            inputs[port_name] = loop_item[1]
            upstream[port_name] = loop_item[0]
            continue
        source = None if index == 0 else _wire_input(planned, index, port_name, port, wiring)
        if source is not None:
            inputs[port_name] = workspace.get(source[0], source[1])
            upstream[port_name] = source[0]
        elif port_name in wiring:
            inputs[port_name] = wiring[port_name]  # 字面值直供（source 运行级参数）
        elif port.required:
            missing.append(f"{port_name!r}(type={port.type})")
        else:
            inputs[port_name] = port.default
    if missing:
        raise _NodeFailure(
            f"必需输入端口无供给: {', '.join(missing)}"
            "（请核对上游输出或用 inputs 显式接线/直供）"
        )
    return inputs, upstream


def _condition_context(
    prefix: Sequence[PlannedNode], workspace: Workspace,
) -> dict[str, dict[str, Any]]:
    """switch 条件求值上下文：前缀各节点的已产出端口 ``{节点 id: {端口: 值}}``。

    未产出节点（未执行/上游失败/非选中分支）缺席——evaluate_condition
    对缺席引用给可读 _ConditionError，不抛裸 KeyError。
    """
    context: dict[str, dict[str, Any]] = {}
    for node in prefix:
        produced: dict[str, Any] = {}
        for port in node.plugin.outputs:
            try:
                produced[port] = workspace.get(node.id, port)
            except KeyError:
                continue
        if produced:
            context[node.id] = produced
    return context


def _node_spec(node: PlannedNode) -> dict[str, Any]:
    """meta.node_specs 条目：经典节点保持既有四键形状（零回归）；控制节点
    携带完整原始 spec（type/condition/branch/loop_over/body/max_iters）
    ——regenerate 据此透传 plan_pipeline 重建控制流树。"""
    if not node.node_type:
        return {"id": node.id, "plugin": node.plugin.id, "config": node.config, "inputs": node.wiring}
    spec = dict(node.spec)
    spec["id"] = node.id
    spec.setdefault("type", node.node_type)
    return spec


def _new_run_id(slug: str = "pl") -> str:
    """run_id 生成：``<slug>-<YYYYMMDD-HHMMSS>-<rand4>``（agent_service 同款）。"""
    safe_slug = _SLUG_RE.sub("-", str(slug)).strip("-") or "pl"
    stamp = _now_iso().replace("-", "").replace(":", "").replace("T", "-")
    run_id = f"{safe_slug}-{stamp}-{uuid.uuid4().hex[:4]}"
    if not _RUN_ID_RE.match(run_id):  # pragma: no cover - 生成器输出恒白名单形态
        raise ValueError(f"run_id 形态非法: {run_id!r}")
    return run_id


def pipeline_runs_root() -> Path:
    """run 目录根：~/.musespiral/pipeline_runs/（每次现读，随 MUSESPIRAL_HOME 漂移）。"""
    return config.home_dir() / RUNS_DIRNAME


def run_pipeline(
    nodes: Iterable[dict[str, Any] | str],
    *,
    plugins_root: Path | None = None,
    run_root: Path | None = None,
    run_id: str | None = None,
    ai_client: Any | None = None,
    cancelled: Callable[[], bool] | None = None,
    slug: str = "pl",
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """执行一条插件流水线 → 步级记录（失败即停，不抛步内异常）。

    阶段一失败（PipelinePlanError）**向上抛**且零 run 目录；阶段二起的
    一切失败收敛进 result（步级记账，pipeline_service 纪律同款）。
    控制流节点（switch/loop，ARCH-4）由 _exec_switch/_exec_loop 驱动其
    嵌套子链——result.steps 含全部**实际执行**的节点（未选中分支不产生
    step，log.jsonl 留痕）。返回结构（job result 同形，「计数无正文」
    ——正文在 run 目录）::

        {"run_id", "run_dir", "status": done|failed|cancelled",
         "steps_done", "steps", "artifacts", "failed_at"?, "cancelled_at"?}
        steps 每项 {"step", "status": ok|failed|cancelled, "detail",
                   "artifacts": [run_dir 相对路径]}
        （status 基础三态沿用金路径纪律 + cancelled 为取消专属终态标记
        ——req 23「判 cancelled 而非 failed」的落点；v1 无 skipped 产线，
        枚举位保留供后续 --skip 语义。）
    """
    planned = plan_pipeline(nodes, plugins_root=plugins_root)  # 阶段一（可抛，零 run）

    rid = run_id if run_id is not None else _new_run_id(slug)
    if not _RUN_ID_RE.match(rid):
        raise PipelinePlanError(f"run_id 形态非法: {rid!r}")
    root = Path(run_root) if run_root is not None else pipeline_runs_root()
    run_dir = root / rid
    run_dir.mkdir(parents=True, exist_ok=False)  # 阶段一过才建 run（req 4/22）
    workspace = Workspace(run_dir)

    steps: list[dict[str, Any]] = []
    artifacts: list[str] = []
    failed_at: str | None = None
    cancelled_at: str | None = None
    terminal: str | None = None  # 最后一个产出产物的节点（阶段三收集基准）
    log_path = run_dir / "log.jsonl"
    started = _now_iso()

    def _log(node_id: str, msg: str) -> None:
        entry = {"node": node_id, "msg": msg, "ts": _now_iso()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_meta(**extra: Any) -> None:
        meta: dict[str, Any] = {
            "run_id": rid,
            # nodes/plugins=顶层清单（控制节点占一位；嵌套节点在 steps/log 可见）；
            # loop/条件 switch 的 plugin id 是合成契约标记（builtin/loop|switch）。
            "nodes": [n.id for n in planned],
            "plugins": [n.plugin.id for n in planned],
            # AI-3 复核 fix-1（2026-11-21）：把**实际运行**的每节点 config（已合并
            # manifest 缺省）与 inputs（接线/字面值）落 meta——重生成按 meta 重建
            # 时沿用原运行参数，而非回落 manifest 缺省（mode/thematic→chronological
            # 漂移判例）。控制节点（ARCH-4）落完整原始 spec（含 branch/loop_over/
            # body），regenerate 透传重建控制流树。老 run 无此键 → 重生成侧回退
            # 旧 nodes/plugins 重建（零回归）。
            "node_specs": [_node_spec(n) for n in planned],
            "status": "running",
            "started": started,
        }
        meta.update(extra)
        (run_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    _write_meta()

    def _finish_step(node_id: str, status: str, detail: str, produced: list[str]) -> bool:
        """记录一步；返回 True=应停止（失败/取消）。成功步更新收集基准。"""
        nonlocal failed_at, cancelled_at, terminal
        steps.append(
            {"step": node_id, "status": status, "detail": detail, "artifacts": produced}
        )
        artifacts.extend(produced)
        if status == "cancelled":
            cancelled_at = node_id
            return True
        if status == "failed":
            failed_at = node_id
            return True
        terminal = node_id
        return False

    def _exec_node_step(
        node: PlannedNode,
        prefix: Sequence[PlannedNode],
        *,
        loop_item: tuple[str, Any] | None = None,
        iter_index: int | None = None,
    ) -> tuple[str, str, list[str], dict[str, Any] | None]:
        """单节点执行（gather → run → 输出契约校验 → workspace.put）。

        prefix=执行序供给前缀（经典顶层=planned[:index]；分支=switch 前+
        switch；body=loop 前+同轮早前 body 节点）。iter_index=loop body 内
        的迭代序号（文件产物按 ``iter<k>`` 命名，防逐迭代互相覆盖）。返回
        (status, detail, produced, outputs|None)——outputs 供 loop 合并。"""
        status, detail, produced = "ok", "", []
        outputs: dict[str, Any] | None = None
        try:
            inputs, upstream_map = _gather_inputs(
                [*prefix, node], len(prefix), workspace, loop_item=loop_item
            )
            rtype = node.plugin.runtime.type
            if rtype == "exec":
                # 外部进程插件（ARCH-4 §三）：JSON → stdin/stdout；权限门/超时强杀/
                # stderr 透传在 exec_runtime 内收口。stderr 与 plugin log 同走 _log。
                outputs = _exec_runtime.run_exec_plugin(
                    node.plugin, node.plugin_dir, inputs,
                    on_log=lambda msg: _log(node.id, msg),
                )
                upstream_map = {}  # exec 无 ctx 注入，接线面不参与（仅 python 消费）
            elif rtype == "python":
                fn = _load_python_entry(node.plugin, node.plugin_dir)
                ctx = _PipelineContext(
                    node_id=node.id,
                    run_dir=run_dir,
                    workspace=workspace,
                    cancelled=cancelled,
                    ai_client=ai_client,
                    uses_ai=node.plugin.uses_ai,
                    on_log=_log,
                    upstream=upstream_map,  # 接线面（端口→供给节点 id）
                )
                outputs = fn(inputs, node.config, ctx)
            else:
                raise _NodeFailure(
                    f"runtime {rtype!r} 未实现（v1=python/exec；http 留接口不实现）"
                )
            if not isinstance(outputs, dict):
                raise _NodeFailure(
                    f"入口返回 {type(outputs).__name__}，须为 dict（键=manifest.outputs）"
                )
            declared = set(node.plugin.outputs)
            absent = declared - set(outputs)
            if absent:
                raise OutputContractError(
                    f"缺输出键 {sorted(absent)}（manifest.outputs 声明 {sorted(declared)}）"
                )
            produced = workspace.put(
                node.id,
                outputs,
                # loop body 内按迭代号命名文件产物——bytes/大 JSON 逐迭代不
                # 互相覆盖，loop 合并列表每个 Path 各指其轮（复核发现 2）
                file_tag=(f"iter{iter_index}" if iter_index is not None else ""),
            )  # 额外键自然忽略（req 13）
            detail = f"{len(declared)} 输出端口已入 workspace"
        except PluginCancelled as exc:  # 先于一切 Exception——req 23 语义分叉点
            status, outputs = "cancelled", None
            detail = str(exc).strip() or "插件在取消检查点主动退出"
        except Exception as exc:  # PluginError/OutputContractError/未知异常一律可读失败
            status, outputs = "failed", None
            detail = str(exc).strip() or exc.__class__.__name__
        return status, detail, produced, outputs

    def _exec_switch(
        node: PlannedNode, prefix: Sequence[PlannedNode],
        *, loop_item: tuple[str, Any] | None = None,
        iter_index: int | None = None,
    ) -> bool:
        """switch 节点：得路由值 → 只执行 branch[route]（ARCH-4 硬要求 1-3）。

        返回是否应停止（失败/取消）——与经典节点同套失败即停纪律。
        loop_item 透传：嵌套在 loop body 内时，switch 自身与分支子链的
        $item 端口仍注入外层迭代项。iter_index 同步透传（分支子链的
        文件产物同样按迭代号命名）。"""
        branch = node.branch or {}
        if node.condition:  # 条件形式：引擎求值（true/false 即路由名）
            try:
                route = (
                    "true"
                    if evaluate_condition(node.condition, _condition_context(prefix, workspace))
                    else "false"
                )
            except _ConditionError as exc:
                return _finish_step(node.id, "failed", str(exc), [])
            produced = workspace.put(node.id, {"route": route})  # 下游可消费路由值
            route_src = f"condition {node.condition!r}"
        else:  # 插件形式：control 插件的 route 输出即路由名
            status, detail, produced, outputs = _exec_node_step(
                node, prefix, loop_item=loop_item, iter_index=iter_index
            )
            if status != "ok":
                return _finish_step(node.id, status, detail, produced)
            route = outputs.get("route") if isinstance(outputs, dict) else None
            if not isinstance(route, str) or not route.strip():
                return _finish_step(
                    node.id, "failed",
                    f"switch 插件输出 route 非法（须为非空 string，实为 {route!r}）——无法选分支",
                    produced,
                )
            route_src = "插件输出"
        chain = branch.get(route)
        if chain is None:
            return _finish_step(
                node.id, "failed",
                f"{route_src} → route={route!r} 无对应分支"
                f"（branch 可用路由: {sorted(branch)}）",
                produced,
            )
        skipped = sorted(r for r in branch if r != route)
        _finish_step(
            node.id, "ok",
            f"{route_src} → route={route}；执行分支 {route!r}（{len(chain)} 节点），"
            f"未执行: {skipped}",
            produced,
        )
        _log(node.id, f"switch → route={route}；未执行分支: {skipped}")
        chain_prefix = [*prefix, node]
        for sub in chain:
            if _dispatch_node(sub, chain_prefix, loop_item=loop_item, iter_index=iter_index):
                return True
            chain_prefix.append(sub)
        return False

    def _exec_loop(
        node: PlannedNode, prefix: Sequence[PlannedNode],
        *, loop_item: tuple[str, Any] | None = None,
    ) -> bool:
        """loop 节点：逐项执行 body → 合并回列表（ARCH-4 硬要求 4-6）。

        返回是否应停止（失败/取消）。body 内可嵌套控制节点（loop 套 loop
        /switch）——递归走 _dispatch_node。合并源=body 尾节点落 workspace
        的产物（控制尾=嵌套 loop 的合并输出，经典尾=put 值；bytes 元素以
        Path 形态合并——引用化省一次全量驻留）。"""
        ref_node, ref_port = node.loop_over or ("", "")
        try:
            items = workspace.get(ref_node, ref_port)
        except KeyError as exc:
            return _finish_step(node.id, "failed", f"loop_over 上游产物缺失: {exc}", [])
        if not isinstance(items, list):
            return _finish_step(
                node.id, "failed",
                f"loop_over {ref_node}.{ref_port} 须为列表，实为 {type(items).__name__}",
                [],
            )
        if len(items) > node.max_iters:  # 防死循环门（默认 100）
            return _finish_step(
                node.id, "failed",
                f"循环项数 {len(items)} 超过最大循环数 {node.max_iters}——"
                "请调高该节点 max_iters 或分批处理",
                [],
            )
        body = node.body or ()
        tail = body[-1] if body else None
        merged: dict[str, list[Any]] = {p: [] for p in tail.plugin.outputs} if tail else {}
        for k, item in enumerate(items):
            if on_phase is not None:
                on_phase(f"{node.id} {k + 1}/{len(items)}")
            chain_prefix = list(prefix)  # 每轮重置：供给域=loop 前+本轮早前 body 节点
            for sub in body:
                if _dispatch_node(sub, chain_prefix, loop_item=(node.id, item), iter_index=k):
                    _log(node.id, f"第 {k + 1}/{len(items)} 项在 {sub.id} 处中止")
                    return True
                chain_prefix.append(sub)
            if tail is not None:  # 尾节点产物（workspace 取——控制/经典统一）
                tail_outputs: dict[str, Any] = {}
                for p in tail.plugin.outputs:
                    try:
                        tail_outputs[p] = workspace.get(tail.id, p)
                    except KeyError:
                        pass
                for p, v in tail_outputs.items():
                    if p in merged:
                        merged[p].append(v)
            _log(node.id, f"第 {k + 1}/{len(items)} 项完成")
        put_paths = workspace.put(node.id, merged)
        return _finish_step(
            node.id, "ok",
            f"{len(items)} 项 × body {len(body)} 节点；合并输出端口 {sorted(merged)}",
            put_paths,
        )

    def _dispatch_node(
        node: PlannedNode, prefix: Sequence[PlannedNode],
        *, loop_item: tuple[str, Any] | None = None,
        iter_index: int | None = None,
    ) -> bool:
        """单节点派发（经典/switch/loop）——主循环、分支链、body 链共用，
        嵌套控制流由此自然递归。返回是否应停止。

        iter_index 只对经典/switch 节点生效（文件产物按迭代号命名）；嵌套
        loop 不透传外层迭代号——内层自建迭代命名空间。"""
        if node.node_type == "switch":
            return _exec_switch(node, prefix, loop_item=loop_item, iter_index=iter_index)
        if node.node_type == "loop":
            return _exec_loop(node, prefix, loop_item=loop_item)
        status, detail, produced, _outs = _exec_node_step(
            node, prefix, loop_item=loop_item, iter_index=iter_index
        )
        return _finish_step(node.id, status, detail, produced)

    try:
        total = len(planned)
        for index, node in enumerate(planned):
            if on_phase is not None:
                on_phase(f"{index + 1}/{total} {node.id}")
            if _dispatch_node(node, planned[:index]):
                break  # 失败即停 / 取消即停——后序节点不执行

        # 阶段三：末节点产物收集（仅成功态；失败/取消保留现场不收集）。
        # 控制流下「末节点」=最后一个产出产物的节点（分支尾/loop 自身）。
        if failed_at is None and cancelled_at is None and planned:
            artifacts.extend(
                workspace.collect_output(terminal or planned[-1].id, run_dir / "output")
            )

        status = "cancelled" if cancelled_at else ("failed" if failed_at else "done")
        result: dict[str, Any] = {
            "run_id": rid,
            "run_dir": str(run_dir),
            "status": status,
            "steps_done": sum(1 for s in steps if s["status"] == "ok"),
            "steps": steps,
            "artifacts": artifacts,
        }
        if failed_at:
            result["failed_at"] = failed_at
        if cancelled_at:
            result["cancelled_at"] = cancelled_at
        # 终态写全（正典 §六「meta.json=状态、时间、节点状态」）：steps/artifacts
        # 随终态落盘——studio runs 读面（GET /api/pipelines/runs/<id>）以 meta 为
        # 唯一真相，不依赖 job result 存活（job 面只保计数，正文在 run 目录）。
        _write_meta(
            status=status,
            finished=_now_iso(),
            steps=steps,
            artifacts=artifacts,
            failed_at=failed_at,
            cancelled_at=cancelled_at,
        )
        return result
    finally:
        # 阶段四：清理临时目录（ctx.tmpdir 受管区）；workspace 无论成败保留
        # （失败看现场——req 10；成功态保留策略归 `musespiral plrun prune`，
        # prune_runs：LRU 最近 PRUNE_KEEP 次）
        tmp_root = run_dir / "tmp"
        if tmp_root.is_dir():
            shutil.rmtree(tmp_root, ignore_errors=True)
