"""pipeline 包：workspace（产物暂存区 + 引用/值分流 + 节点上下文）。

原 services/pipeline_service_plugins.py（1291 行单文件）按 ARCH-2（正典=
《musespiral-极限架构与功能扩展总规划-2026-11-21》§ARCH-2，沿 2026-11-19
R-1 compose 拆分与 2026-11-22 materials 拆分同模式）拆出的 workspace 簇：

- ``INLINE_MAX_BYTES``（1 MiB）——序列化阈值**全系统唯一权威**（评审 B-5）：
  产物序列化字节数 ≤ 此值内联进 outputs.json，超过（或不可 JSON 序列化）
  落盘存引用，勿散拷贝；
- ``ArtifactRef``——产物引用四态（json/bytes/path/mem，正典 §5.2）；
- ``Workspace``——节点间只通过它传数据；大产物存引用（路径/id）不存值，
  ``put`` 落盘分流 / ``get`` 消费点解析 / ``collect_output`` 阶段三收集
  （bytes/json 引用流式复制）；
- ``_PipelineContext``——每节点一个的 PluginContext 实现（接口在
  core/plugins/context；实现在本层）：log 进 run 级 jsonl、tmpdir 惰性、
  cancelled 读注入回调、upstream 接线面与 regen_version 注入。归本簇是
  因为它的 artifacts_dir/tmpdir 就是 workspace 目录布局的节点投影。

解析校验（plan）/拓扑执行（engine）/重生成（regenerate）/LRU 回收
（prune）/jobs 接驳（jobs）见各自子模块；包门面与全量符号面见
``musespiral.services.pipeline`` 的 ``__init__``，旧路径
``services/pipeline_service_plugins.py`` 保留为兼容层。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.plugins.context import PluginContext, PluginError

# 🟢 序列化阈值（评审 B-5）：产物序列化字节数 ≤ 此值内联进 outputs.json，
# 超过（或不可 JSON 序列化）落盘存引用。全系统唯一权威，勿散拷贝。
INLINE_MAX_BYTES = 1 << 20  # 1 MiB

# 节点产物文件名的端口名净化（port 是 manifest 自由键，防路径穿越/撞系统名）
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


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

    def put(
        self, node_id: str, outputs: dict[str, Any], *, file_tag: str = ""
    ) -> list[str]:
        """落盘并登记一个节点的全部输出（覆盖式——v1 无同节点重跑）。

        file_tag：迭代标签（loop body 节点传 ``iter<k>``）——bytes/大 JSON 的
        独立文件按标签命名（``<port>.iter<k>.bin``），同节点逐迭代不再互相
        覆盖，loop 合并列表里每个 Path 才各指其迭代内容（2026-11-22 复核
        发现 2 判例：无标签时全指向末轮文件，'A,B,C' 读回变 'C,C,C'）。
        outputs.json 与进程内缓存仍只记最新一轮——下游单值消费语义不变。

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
                fname = f"{name}.{file_tag}.bin" if file_tag else f"{name}.bin"
                (node_dir / fname).write_bytes(value)
                refs[port] = ArtifactRef(kind="bytes", file=fname)
                persisted.append(f"workspace/{node_id}/{fname}")
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
                        fname = f"{name}.{file_tag}.json" if file_tag else f"{name}.json"
                        (node_dir / fname).write_bytes(payload)
                        refs[port] = ArtifactRef(kind="json", file=fname)
                        persisted.append(f"workspace/{node_id}/{fname}")
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

    def load_from_disk(self, node_id: str) -> bool:
        """把既有 outputs.json 载入缓存（跨进程续读——AI-3 regenerate 消费面）。

        run_pipeline 的 Workspace 是进程内对象（put 才进缓存）；重生成在
        run 终态后另起调用，须先回放各上游节点的 outputs.json 才能经
        ``_gather_inputs`` 取数。返回是否载入（文件缺失 False=常态，损坏
        抛可读 ValueError）。**mem 引用按原样登记但对象表为空**——不可
        JSON 序列化的对象无法跨进程复活，get 时 KeyError（模块 docstring
        v1 诚实边界同款；ks/aw 的输入端口全为 inline/json/path 形态，
        不受影响）。
        """
        path = self._root / node_id / "outputs.json"
        if not path.is_file():
            return False
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"outputs.json 读取失败（节点 {node_id}）: {exc}") from None
        if not isinstance(doc, dict):
            raise ValueError(f"outputs.json 结构损坏（节点 {node_id}：顶层非对象）")
        inline = doc.get("inline")
        refs_raw = doc.get("refs")
        refs: dict[str, ArtifactRef] = {}
        if isinstance(refs_raw, dict):
            for port, raw in refs_raw.items():
                if isinstance(raw, dict) and raw.get("kind") in ("json", "bytes", "path", "mem"):
                    refs[str(port)] = ArtifactRef(
                        kind=str(raw["kind"]),
                        file=str(raw.get("file") or ""),
                        key=str(raw.get("key") or ""),
                    )
        self._cache[node_id] = (inline if isinstance(inline, dict) else {}, refs)
        return True

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
        regen_version: int | None = None,
    ) -> None:
        # AI-3 复核 fix-2：重生成级联重放下游时带版本号（素材落库据此给新
        # preview 素材的版本化 uri，绕开 immutable 冲突）。None=首轮运行。
        self._regen_version = regen_version
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
        """输入端口 → 供给节点 id（引擎按接线注入，core 默认空的覆写）。"""
        return self._upstream

    @property
    def regen_version(self) -> int | None:
        """重生成级联重放的版本号（AI-3 fix-2；首轮=None）。material-sink
        据此给新稿一个版本化 uri，追加新 preview 素材而非撞 immutable 红线。"""
        return self._regen_version

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
