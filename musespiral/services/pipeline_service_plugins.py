"""插件流水线执行引擎（插件系统设计 §五，PL-2）——L3 编排。

与 services/pipeline_service **并存不替代**（正典 §一：那是「发布前的
内置金路径」五步硬编码，本模块是「数据处理管道平台」用户可编排）；
两者共享的只有步级记账纪律 ``{step, status, detail, artifacts}`` 与
jobs 接驳形态（job_fn/submit_to_runner 先例）。

四阶段模型（正典 §5.1）：
  阶段一 解析校验（plan_pipeline）——manifest 可读 + 相邻契约接得上
         （core.plugins.manifest.validate_pipeline_contract）+ 权限并集
         检查；**此阶段失败不产生 run 目录**（PipelinePlanError 构造期
         抛，零 run 零 job——agent_service/pipeline_service 同款纪律）。
  阶段二 拓扑执行（run_pipeline 线性逐节点）——gather inputs →
         check_input → execute → workspace.put。
  阶段三 产物收集——末节点（sink）产物收集到 ``<run_dir>/output/``。
  阶段四 清理（finally）——临时目录（ctx.tmpdir 受管区）收尾。**失败
         不清理 workspace**（用户要看现场）；成功态 workspace 也不删
         ——保留策略=LRU 最近 PRUNE_KEEP（默认 5）次，由
         ``musespiral plrun prune`` 接管，引擎
         只写 meta.json 终态。v1 无引擎级子进程要清（python runtime
         进程内；exec runtime 是 PL-7）。

失败语义（沿用 pipeline_service 纪律）：失败即停；已完成步与失败步
（含 reason）全部保留在 result.steps；节点返回值缺 manifest.outputs
声明的键 → OutputContractError 判失败（reason 可读），额外键忽略不报错。

序列化阈值（评审 B-5）：``INLINE_MAX_BYTES``（1 MiB）唯一权威——超过
或不可 JSON 序列化的产物落盘存引用，小于则内联（Workspace.put）。

job 集成（正典 §5.3 裁决）：整条流水线只占 1 个 kind ``plugin_pipeline``
（KINDS 18→19），内部节点不是独立 job——节点生命周期归流水线管理。
取消接 runner 协作式取消（R3.5 §2.2）：ctx.cancelled 读
``runner.is_cancelled(job_id)``；插件抛 PluginCancelled → run 级
status="cancelled"（非 failed；job 侧按 batch_adapt 部分成功先例以
done 收束、result 携带步级记录）。

诚实边界（v1，记入正典 §十三）：runtime.timeout_s 未强制（进程内
python 无法安全打断线程，需线程级看门狗）；config_schema 只填 default
不做深校验（JSON-Schema 子集评估留给后续期）；「不可 JSON 序列化」的
非 bytes/PathLike 值以进程内对象引用（mem ref）保真——跨进程续读
不支持（单次 run 生命周期内取数正确）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import config
from ..core.plugins.context import PluginCancelled, PluginContext, PluginError
from ..core.plugins.manifest import (
    PERMISSIONS,
    Plugin,
    Port,
    expected_dirname,
    parse_manifest,
    validate_pipeline_contract,
)
from ..core.plugins.types import types_compatible
from ..jobs.runner import JobFailure
from ..jobs.store import now_iso as _now_iso
from . import plugins_service

# 🟢 序列化阈值（评审 B-5）：产物序列化字节数 ≤ 此值内联进 outputs.json，
# 超过（或不可 JSON 序列化）落盘存引用。全系统唯一权威，勿散拷贝。
INLINE_MAX_BYTES = 1 << 20  # 1 MiB

# run 目录根：~/.musespiral/pipeline_runs/（随 MUSESPIRAL_HOME，测试传 run_root 隔离）
RUNS_DIRNAME = "pipeline_runs"

# run_id 形态：pl-<YYYYMMDD-HHMMSS>-<rand4>（agent_service run_id 同款纪律：
# 会话级唯一，rand4 防同秒并发撞名；run_id 直接拼路径，白名单回验防穿越）
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")

# 节点产物文件名的端口名净化（port 是 manifest 自由键，防路径穿越/撞系统名）
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class PipelinePlanError(ValueError):
    """阶段一校验失败（构造期即抛——零 run 目录、零 job）。"""


class OutputContractError(RuntimeError):
    """节点返回值缺 manifest.outputs 声明的键——判该节点失败（reason 可读）。"""


class _NodeFailure(RuntimeError):
    """引擎内节点失败：消息已可读，直接进 step.detail。"""


@dataclass(frozen=True)
class ArtifactRef:
    """产物引用（正典 §5.2「引用（路径或 id）」）：四态。

    json  结构化大产物（>INLINE_MAX_BYTES）——file 为节点目录内相对名；
    bytes 二进制产物——file 同上，resolve 返回 Path（不读内容）；
    path  插件自持路径（如原视频）——file 为原路径字符串，不复制；
    mem   不可 JSON 序列化的进程内对象——key 查 Workspace 对象表。
    """

    kind: str  # "json" | "bytes" | "path" | "mem"
    file: str = ""
    key: str = ""


# ── Workspace（正典 §5.2：一次运行的产物暂存区）──────────────────


class Workspace:
    """节点间只通过它传数据；大产物存引用（路径/id）不存值。

    目录布局（正典 §六）：``<run_dir>/workspace/<node_id>/``，每节点一份
    ``outputs.json``（``{"inline": {port: 值}, "refs": {port: 引用}}``
    ——内联值与引用分列，杜绝「值本身长得像引用」的标记碰撞）。

    序列化判定（INLINE_MAX_BYTES 唯一权威）：
      bytes/bytearray      → ``<port>.bin`` 落盘，bytes 引用；
      PathLike             → path 引用（原路径不复制——媒体文件可能 GB 级）；
      可序列化 且 ≤ 阈值   → 内联（outputs.json 直接携带）；
      可序列化 且 > 阈值   → ``<port>.json`` 落盘，json 引用；
      不可 JSON 序列化     → mem 引用（进程内对象表，见模块 docstring 边界）。

    get 是**消费点解析**：bytes/path 引用返回 Path（不读内容——大文件
    不塞内存），json 引用惰性加载全值，mem 引用查对象表。
    """

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        self._root = self._run_dir / "workspace"
        # node_id → (inline dict, refs dict)；只缓存小值与引用形状
        self._cache: dict[str, tuple[dict[str, Any], dict[str, ArtifactRef]]] = {}
        # mem 引用的进程内对象表（key → 对象；单 run 生命周期）
        self._objects: dict[str, Any] = {}

    @property
    def root(self) -> Path:
        return self._root

    def node_dir(self, node_id: str) -> Path:
        """该节点的产物目录（= ctx.artifacts_dir；惰性创建）。"""
        d = self._root / node_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _safe_name(port: str, used: set[str]) -> str:
        """端口名 → 节点目录内安全文件名（净化 + 撞名加序号）。"""
        base = _UNSAFE_FILENAME_RE.sub("_", port) or "_"
        name = base
        i = 1
        while name in used:
            i += 1
            name = f"{base}~{i}"
        used.add(name)
        return name

    def put(self, node_id: str, outputs: dict[str, Any]) -> list[str]:
        """落盘并登记一个节点的全部输出（覆盖式——v1 无同节点重跑）。

        返回实际持久化路径清单（run_dir 相对）：outputs.json 恒在册 +
        bytes/json 引用的独立文件；path/mem 引用无独立文件（登记在
        outputs.json 内）——step.artifacts 用真路径，不虚构端口名。
        """
        node_dir = self.node_dir(node_id)
        inline: dict[str, Any] = {}
        refs: dict[str, ArtifactRef] = {}
        persisted: list[str] = []
        used: set[str] = set()
        for port, value in outputs.items():
            if isinstance(value, (bytes, bytearray)):
                name = self._safe_name(port, used)
                (node_dir / f"{name}.bin").write_bytes(value)
                refs[port] = ArtifactRef(kind="bytes", file=f"{name}.bin")
                persisted.append(f"workspace/{node_id}/{name}.bin")
            elif isinstance(value, os.PathLike):
                refs[port] = ArtifactRef(kind="path", file=str(value))
            else:
                try:
                    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
                except TypeError:
                    key = f"{node_id}:{port}"
                    self._objects[key] = value
                    refs[port] = ArtifactRef(kind="mem", key=key)
                else:
                    if len(payload) > INLINE_MAX_BYTES:
                        name = self._safe_name(port, used)
                        (node_dir / f"{name}.json").write_bytes(payload)
                        refs[port] = ArtifactRef(kind="json", file=f"{name}.json")
                        persisted.append(f"workspace/{node_id}/{name}.json")
                    else:
                        inline[port] = value
        (node_dir / "outputs.json").write_text(
            json.dumps(
                {
                    "inline": inline,
                    "refs": {
                        port: {"kind": r.kind, "file": r.file, "key": r.key}
                        for port, r in refs.items()
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._cache[node_id] = (inline, refs)
        return [f"workspace/{node_id}/outputs.json", *persisted]

    def storage_kind(self, node_id: str, port: str) -> str:
        """查询端口存储形态（"inline" | ArtifactRef.kind）——测试与巡检面。"""
        inline, refs = self._entries(node_id)
        if port in inline:
            return "inline"
        if port in refs:
            return refs[port].kind
        raise KeyError(f"节点 {node_id!r} 无输出端口 {port!r}")

    def get(self, node_id: str, port: str) -> Any:
        """取一个端口的值：内联→原值；引用→消费点解析（见类 docstring）。"""
        inline, refs = self._entries(node_id)
        if port in inline:
            return inline[port]
        if port in refs:
            return self.resolve(refs[port], node_id=node_id)
        raise KeyError(f"节点 {node_id!r} 无输出端口 {port!r}")

    def resolve(self, ref: ArtifactRef, *, node_id: str = "") -> Any:
        """引用 → 值/Path（正典 §5.2 resolve；node_id 供 json/bytes 定位）。"""
        if ref.kind in ("json", "bytes"):
            if not node_id:
                raise ValueError("json/bytes 引用解析需 node_id 定位节点目录")
            path = self._root / node_id / ref.file
            if ref.kind == "bytes":
                return path  # 不读内容——大文件走路径
            return json.loads(path.read_text(encoding="utf-8"))
        if ref.kind == "path":
            return Path(ref.file)
        if ref.kind == "mem":
            return self._objects[ref.key]
        raise ValueError(f"未知引用形态: {ref.kind!r}")

    def _entries(self, node_id: str) -> tuple[dict[str, Any], dict[str, ArtifactRef]]:
        cached = self._cache.get(node_id)
        if cached is None:
            raise KeyError(
                f"节点 {node_id!r} 尚未产出（无 put 记录；跨进程续读非 v1 能力）"
            )
        return cached

    def collect_output(self, node_id: str, out_dir: Path) -> list[str]:
        """阶段三：末节点产物收集到 ``<run_dir>/output/``。

        内联值写 ``<port>.json``；bytes/json 引用**流式复制**
        （shutil.copyfile——大产物不整段进内存：多条产物叠加时峰值内存
        不随文件大小走；2026-11-20 复核修复，哨兵回归锁=
        test_pipeline_plugins.py::test_workspace_collect_output_streams_big_refs）；
        path 引用是插件自持原路径（可能 GB 级原视频）——**不复制**，以原样
        路径记入返回清单（消费方按引用取）。返回收集清单（run_dir 相对
        优先，外部路径原样）。
        """
        cached = self._cache.get(node_id)
        if cached is None:
            return []
        inline, refs = cached
        out_dir.mkdir(parents=True, exist_ok=True)
        collected: list[str] = []
        used: set[str] = set()

        def _display(target: Path) -> str:
            try:
                return str(target.resolve().relative_to(self._run_dir.resolve()))
            except ValueError:
                return str(target)

        for port, value in sorted(inline.items()):
            name = self._safe_name(port, used)
            target = out_dir / f"{name}.json"
            target.write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            collected.append(_display(target))
        for port, ref in sorted(refs.items()):
            if ref.kind in ("bytes", "json"):
                target = out_dir / ref.file
                # 流式复制（复核修复）：read_bytes+write_bytes 会把整段产物
                # 读进内存——GB 级 bytes/json 产物在成功态收集时被整段加载，
                # 多产物叠加峰值=各文件之和。copyfile 走内核缓冲块拷贝。
                shutil.copyfile(self._root / node_id / ref.file, target)
                collected.append(_display(target))
            elif ref.kind == "path":
                collected.append(ref.file)
            # mem 引用不可落盘——跳过（进程内对象，无磁盘形态）
        return collected


# ── PluginContext 实现（接口在 core/plugins/context；实现在本层）──


class _PipelineContext(PluginContext):
    """每节点一个：log 进 run 级 jsonl、tmpdir 惰性、cancelled 读注入回调。"""

    def __init__(
        self,
        *,
        node_id: str,
        run_dir: Path,
        workspace: Workspace,
        cancelled: Callable[[], bool] | None,
        ai_client: Any | None,
        uses_ai: bool,
        on_log: Callable[[str, str], None] | None = None,
        upstream: dict[str, str] | None = None,
    ) -> None:
        self._node_id = node_id
        self._run_dir = Path(run_dir)
        self._workspace = workspace
        self._cancelled = cancelled
        self._ai = ai_client
        self._uses_ai = uses_ai
        self._tmpdir: Path | None = None
        self._on_log = on_log
        # 输入端口 → 供给节点 id（_gather_inputs 按实际接线回填；None=无接线面）
        self._upstream = dict(upstream or {})

    def log(self, msg: str) -> None:
        if self._on_log is not None:
            self._on_log(self._node_id, msg)

    def progress(self, done: int, total: int) -> None:
        if self._on_log is not None:
            self._on_log(self._node_id, f"progress {done}/{total}")

    def tmpdir(self) -> Path:
        if self._tmpdir is None:
            self._tmpdir = self._run_dir / "tmp" / self._node_id
            self._tmpdir.mkdir(parents=True, exist_ok=True)
        return self._tmpdir

    @property
    def run_id(self) -> str:
        """run_id = run 目录名（core 默认空串的覆写；material-sink 血统链消费方）。"""
        return self._run_dir.name

    @property
    def cancelled(self) -> bool:
        return bool(self._cancelled is not None and self._cancelled())

    @property
    def artifacts_dir(self) -> Path:
        return self._workspace.node_dir(self._node_id)

    @property
    def upstream(self) -> dict[str, str]:
        """输入端口 → 供给节点 id（引擎按实际接线注入，core 默认空的覆写）。"""
        return self._upstream

    @property
    def ai(self) -> Any:
        if not self._uses_ai:
            raise PluginError(
                "ctx.ai 不可用：插件未声明 uses_ai: true（正典 §7.3——要调 AI "
                "必须声明，由平台注入受管客户端）"
            )
        if self._ai is None:
            raise PluginError("ctx.ai 不可用：平台未注入受管 AI 客户端（未配置/未接线）")
        return self._ai


# ── 阶段一：解析校验（零 run 目录）─────────────────────────────────


@dataclass(frozen=True)
class PlannedNode:
    """阶段一产物：节点 id + Plugin 契约 + 插件目录 + 合并后 config + 接线。

    wiring（原始节点 dict 的 ``inputs`` 键）：``{输入端口: {"node": id,
    "port": 名}}`` 显式接线，或 ``{输入端口: 字面值}`` 直供（首节点
    source 的运行级参数走此路）。缺省键由 _wire_input 自动接线。
    """

    id: str
    plugin: Plugin
    plugin_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    wiring: dict[str, Any] = field(default_factory=dict)


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


def plan_pipeline(
    nodes: Iterable[dict[str, Any] | str], *, plugins_root: Path | None = None
) -> list[PlannedNode]:
    """阶段一：解析校验 → PlannedNode 列表；任一不过抛 PipelinePlanError。

    **此函数不创建任何 run 目录**（阶段一失败零副作用的实现基点）。
    三道校验（正典 §5.1）：
      1. 每个插件 manifest 可读（从插件区按 id 解析——scan_plugins 已过
         parse_manifest/目录名核对/入口存在性三道门，这里按 id 索引）；
      2. 相邻契约接得上（validate_pipeline_contract：kind 位置/必需端口
         供给/重复 id——core 层唯一权威）；
      3. 权限并集在允许范围内（PERMISSIONS 表——parse_manifest 已逐插件
         校验，这里按正典 §5.1 要求显式复核全流水线并集，防未来绕过）。
    """
    node_list = _normalize_nodes(nodes)
    if not node_list:
        raise PipelinePlanError("流水线至少需要一个节点")

    valid, _skipped = plugins_service.scan_plugins(plugins_root)
    by_id: dict[str, dict[str, Any]] = {item["manifest"]["id"]: item for item in valid}

    planned: list[PlannedNode] = []
    contract_nodes: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, node in enumerate(node_list):
        node_id = str(node.get("id") or f"node-{i + 1}")
        plugin_id = node.get("plugin")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            problems.append(f"节点[{i}] {node_id}: 缺少 plugin（插件 id）")
            continue
        item = by_id.get(plugin_id)
        if item is None:
            problems.append(
                f"节点[{i}] {node_id}: 插件 {plugin_id!r} 不可用（未安装/manifest 红/"
                "目录名不符/入口缺失——详参 plugins list 的 skipped 清单）"
            )
            continue
        plugin, errs = parse_manifest(item["manifest"])
        if plugin is None:  # scan 已过校验，防御性兜底仍收敛为可读错
            problems.append(f"节点[{i}] {node_id}: manifest 复核失败: {'；'.join(errs)}")
            continue
        planned.append(
            PlannedNode(
                id=node_id,
                plugin=plugin,
                plugin_dir=Path(item["dir"]),
                config=_merge_config(node, plugin),
                wiring=dict(node.get("inputs") or {}),
            )
        )
        contract_nodes.append({"id": node_id, "plugin": item["manifest"]})

    if problems:
        raise PipelinePlanError("；".join(problems))

    contract_errors = validate_pipeline_contract(contract_nodes)
    if contract_errors:
        raise PipelinePlanError("契约校验未过: " + "；".join(contract_errors))

    union_perms = {p for node in planned for p in node.plugin.permissions}
    unknown_perms = union_perms - set(PERMISSIONS)
    if unknown_perms:
        raise PipelinePlanError(
            f"权限并集含未知权限 {sorted(unknown_perms)}（允许表: {list(PERMISSIONS)}）"
        )
    return planned


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
) -> tuple[dict[str, Any], dict[str, str]]:
    """gather + check_input：拼齐节点 inputs（缺必需端口 → 可读 _NodeFailure）。

    供给优先级：workspace 上游输出 > wiring 字面值（source 节点运行级
    参数）> 可选端口 default。必需端口三者皆无 → 列名报错。
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


# run 目录保留策略（引擎 docstring「保留策略」的落地处）：LRU 保最近 N 次
# （agent_service.prune 判例同款——「7 天」在目录级上不可靠：一次长跑的
# run 一样该保，按个数才是稳定上限）。默认 5，`musespiral plrun prune` 可调。
PRUNE_KEEP = 5


def prune_runs(keep: int = PRUNE_KEEP) -> dict[str, Any]:
    """LRU 清理 pipeline run 目录（保留最近 keep 个）→ {"removed", "kept"}。

    排序键：meta.json 的 finished/started（终态写全纪律保证在场）；坏
    目录（meta 读不出）按目录 mtime 参与（防永久留垃圾）。run 目录不进
    materials，prune=显式删非墓碑（agent_service 判例同款）。CLI 面=
    ``musespiral plrun prune --keep N``。
    """
    root = pipeline_runs_root()
    if not root.is_dir():
        return {"removed": 0, "kept": 0}

    def _mtime_iso(directory: Path) -> str:
        try:
            stamp = directory.stat().st_mtime
        except OSError:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp))

    entries: list[tuple[str, Path]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if not isinstance(meta, dict):
            entries.append((_mtime_iso(directory), directory))
            continue
        entries.append((str(meta.get("finished") or meta.get("started") or ""), directory))
    entries.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
    limit = keep if keep and keep > 0 else PRUNE_KEEP
    removed = 0
    for _stamp, directory in entries[limit:]:
        try:
            shutil.rmtree(directory)
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "kept": min(len(entries), limit)}


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
    返回结构（job result 同形，「计数无正文」——正文在 run 目录）::

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
    log_path = run_dir / "log.jsonl"
    started = _now_iso()

    def _log(node_id: str, msg: str) -> None:
        entry = {"node": node_id, "msg": msg, "ts": _now_iso()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_meta(**extra: Any) -> None:
        meta: dict[str, Any] = {
            "run_id": rid,
            "nodes": [n.id for n in planned],
            "plugins": [n.plugin.id for n in planned],
            "status": "running",
            "started": started,
        }
        meta.update(extra)
        (run_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    _write_meta()

    try:
        total = len(planned)
        for index, node in enumerate(planned):
            if on_phase is not None:
                on_phase(f"{index + 1}/{total} {node.id}")
            step_status, detail, produced = "ok", "", []
            try:
                if node.plugin.runtime.type != "python":
                    raise _NodeFailure(
                        f"runtime {node.plugin.runtime.type!r} 未实现（v1=python；exec 是 PL-7）"
                    )
                fn = _load_python_entry(node.plugin, node.plugin_dir)
                inputs, upstream_map = _gather_inputs(planned, index, workspace)
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
                put_paths = workspace.put(node.id, outputs)  # 额外键自然忽略（req 13：不报错）
                produced = put_paths
                detail = f"{len(declared)} 输出端口已入 workspace"
            except PluginCancelled as exc:  # 先于一切 Exception——req 23 语义分叉点
                step_status = "cancelled"
                detail = str(exc).strip() or "插件在取消检查点主动退出"
                cancelled_at = node.id
            except Exception as exc:  # PluginError/OutputContractError/未知异常一律可读失败
                step_status = "failed"
                detail = str(exc).strip() or exc.__class__.__name__
                failed_at = node.id
            steps.append(
                {"step": node.id, "status": step_status, "detail": detail, "artifacts": produced}
            )
            artifacts.extend(produced)
            if step_status != "ok":
                break  # 失败即停 / 取消即停——后序节点不执行

        # 阶段三：末节点产物收集（仅成功态；失败/取消保留现场不收集）
        if failed_at is None and cancelled_at is None and planned:
            artifacts.extend(
                workspace.collect_output(planned[-1].id, run_dir / "output")
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


# ── jobs 接驳（kind=plugin_pipeline；整条流水线 1 个 kind，§5.3）───


def job_fn(
    nodes: Iterable[dict[str, Any] | str],
    *,
    plugins_root: Path | None = None,
    run_root: Path | None = None,
    ai_client: Any = None,
    runner: Any = None,
) -> Callable[[str], dict[str, Any]]:
    """把一条插件流水线包成 jobs 闭包（runner 调 ``fn(job_id)``）。

    失败 → JobFailure（消息「插件流水线在 <node> 节点失败：<原因>」，
    result 携带步级记录）；取消 → 按 batch_adapt 部分成功先例以 done
    收束（result.status="cancelled" 供 UI 区分，步级记录完整保留）。
    """
    node_list = _normalize_nodes(nodes)  # 生成器安全：闭包内只消费这一份

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(text: str) -> None:
            if runner is not None:
                runner.set_phase(job_id, text)

        result = run_pipeline(
            node_list,
            plugins_root=plugins_root,
            run_root=run_root,
            ai_client=ai_client,
            cancelled=(lambda: runner.is_cancelled(job_id)) if runner is not None else None,
            on_phase=_phase,
        )
        if result["status"] == "failed":
            failed = str(result.get("failed_at") or "")
            detail = next(
                (s["detail"] for s in result["steps"] if s["step"] == failed), ""
            )
            raise JobFailure(
                f"插件流水线在 {failed} 节点失败：{detail}", result=result
            )
        return result

    return _run


def submit_to_runner(
    runner: Any,
    nodes: Iterable[dict[str, Any] | str],
    *,
    plugins_root: Path | None = None,
    run_root: Path | None = None,
    ai_client: Any = None,
) -> str:
    """提交一条插件流水线 job（kind=plugin_pipeline），返回 job_id。

    阶段一校验在 ``runner.submit`` **之前**（构造期，零 job——
    pipeline_service.PipelineServiceError 先例同款纪律）。
    """
    node_list = _normalize_nodes(nodes)
    plan_pipeline(node_list, plugins_root=plugins_root)  # 校验先行；可抛 PipelinePlanError
    fn = job_fn(
        node_list,
        plugins_root=plugins_root,
        run_root=run_root,
        ai_client=ai_client,
        runner=runner,
    )
    return runner.submit("plugin_pipeline", {"nodes": node_list}, fn)


__all__ = [
    "INLINE_MAX_BYTES",
    "ArtifactRef",
    "OutputContractError",
    "PipelinePlanError",
    "PlannedNode",
    "PRUNE_KEEP",
    "Workspace",
    "job_fn",
    "pipeline_runs_root",
    "plan_pipeline",
    "prune_runs",
    "run_pipeline",
    "submit_to_runner",
]
