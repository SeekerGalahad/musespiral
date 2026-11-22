"""pipeline 包：regenerate（节点级重生成 + 级联重放）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的重生成簇（AI-3 批
2026-11-21，正典=《mdpub-AI产物裁决机制设计-2026-11-21》§2.3 裁决三件套
·重生成）。「这条切片不对，再来一次」——从该节点重跑（**前序节点不重跑**：
local-file/transcribe 的产物是确定的，重跑纯费算力），旧版归档保留供对比，
新产物 status 重置 draft（用户要重新裁决，原语在 adjudicate 簇）。接线/
取数复用 plan_pipeline + _gather_inputs（不重新发明 wiring）；meta.json
是节点/插件清单的唯一重建依据（run_pipeline 终态写全的 nodes/plugins
两键）。

包门面与全量符号面见 ``musespiral.services.pipeline`` 的 ``__init__``。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from ...jobs.store import now_iso as _now_iso
from .adjudicate import _reset_draft_status
from .engine import (
    _RUN_ID_RE,
    _NodeFailure,
    _gather_inputs,
    _load_python_entry,
    pipeline_runs_root,
    OutputContractError,
)
from .plan import PipelinePlanError, PlannedNode, plan_pipeline
from .workspace import _PipelineContext, Workspace


class RegenerateError(ValueError):
    """节点重生成校验/执行失败（AI-3，2026-11-21；消息可读——studio 转 400）。

    与 PipelinePlanError 同族（构造期校验先行、零副作用的纪律），独立成类
    是因为重生成的失败面不同：run 不存在/仍在跑、节点不在 run 内、节点无
    旧产物可归档、插件被卸载致 plan 重建失败、上游产物缺失不可复活。
    """


# 旧版归档文件名（outputs.v{N}.json，N 从 1 递增——正则锚定既有最大 N）
_ARCHIVED_OUTPUTS_RE = re.compile(r"^outputs\.v(\d+)\.json$")

# 控制流节点 spec 键（ARCH-4）：meta.node_specs 里控制节点携带的完整原始
# 形状——重生成重建 plan 时透传给 plan_pipeline（经典节点无这些键，零回归）。
_CONTROL_SPEC_KEYS = ("type", "condition", "branch", "loop_over", "body", "max_iters")

# AI-3 复核 fix-3：md 归档名以 .v{N}.md 结尾（含 .v1.v2.md 等历史串味形态）。
# 归档扫 *.md 时必须排除这些，否则第 N 次重生成会把 N-1 份历史副本再各归档
# 一次——约 2^N 份指数膨胀（live_check2 实测 3 次→8 份）。
_ARCHIVED_MD_RE = re.compile(r"\.v\d+\.md$")


class _RegenLog:
    """run log 追加器（引擎 _log 同形状 {node, msg, ts}——与 adopt/discard
    审计行同款，裁决机制设计 §三 9「audit 复用」）；失败不吞主流程。"""

    def __init__(self, run_dir: Path) -> None:
        self._path = run_dir / "log.jsonl"

    def __call__(self, node_id: str, msg: str) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"node": node_id, "msg": msg, "ts": _now_iso()},
                    ensure_ascii=False,
                ) + "\n")
        except OSError:
            pass


def precheck_regenerate(
    run_id: str, node_id: str, *, run_root: Path | None = None
) -> Path:
    """重生成校验面（零副作用）：run 存在且非 running、节点在 run 内且
    有旧产物可归档、meta 的节点/插件清单可重建 plan。

    供 studio 提交 job 前调用（构造期校验先行、零 job 纪律——
    pipeline_service.PipelineServiceError 先例）；失败抛 RegenerateError。
    返回 run_dir（校验通过的副产品，调用方免重读）。
    """
    if not _RUN_ID_RE.match(run_id or ""):
        raise RegenerateError(f"run_id 形态非法: {run_id!r}")
    root = Path(run_root) if run_root is not None else pipeline_runs_root()
    run_dir = root / run_id
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        raise RegenerateError(f"运行不存在: {run_id}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegenerateError(f"运行记录读取失败: {run_id}（{exc}）") from None
    if not isinstance(meta, dict):
        raise RegenerateError(f"运行记录损坏: {run_id}（meta 顶层非对象）")
    if str(meta.get("status") or "") == "running":
        raise RegenerateError(f"运行仍在进行中，不可重生成（等终态或先取消）: {run_id}")
    nodes, plugins = meta.get("nodes"), meta.get("plugins")
    if (
        not isinstance(nodes, list) or not isinstance(plugins, list)
        or len(nodes) != len(plugins) or not nodes
    ):
        raise RegenerateError(
            f"运行记录缺节点/插件清单（老 run 无 nodes/plugins 键），无法重建接线: {run_id}"
        )
    node_ids = [str(n) for n in nodes]
    if node_id not in node_ids:
        raise RegenerateError(
            f"节点不在该运行内: {node_id!r}（run {run_id} 节点: {'/'.join(node_ids)}）"
        )
    if not (run_dir / "workspace" / node_id / "outputs.json").is_file():
        raise RegenerateError(
            f"节点 {node_id!r} 无旧产物可归档（未跑成/早停/已清理），无从重生成"
        )
    return run_dir


def regenerate_node(
    run_id: str,
    node_id: str,
    *,
    run_root: Path | None = None,
    feedback: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """从单个节点重跑（保留前序产物与旧版本；AI-3，studio regenerate 端点的
    执行体，跑在 kind=plugin_pipeline job 内）。

    步骤（接线全复用引擎件，不另造轮子）：
      1. ``precheck_regenerate`` 校验（run 终态/节点在册/旧产物在场）；
      2. 按 meta 的 nodes/plugins 重建 ``plan_pipeline``（config/wiring 沿
         运行期同款缺省——studio 运行面只存 {id, plugin}，本步与其等价）；
      3. ``Workspace.load_from_disk`` 回放**前序**节点 outputs.json（目标
         节点自身不读——防把旧值当输入），``_gather_inputs`` 照常取数；
      4. 归档旧版：outputs.json → ``outputs.v{N}.json``、节点目录 ``*.md``
         → ``{stem}.v{N}.md``（成稿正文不在 outputs.json 内，不归档即失）
         、``ranges.edited.json`` → ``ranges.edited.v{N}.json``（生效文档
         让位：不挪开则读面「编辑版优先」会继续吃旧编辑版，新产物被遮蔽）；
      5. 调插件入口（config 注入 ``feedback`` 键——key-segment/article-
         writer 消费进 AI prompt 尾部；不识别该键的插件自然忽略），
         产物 ``_reset_draft_status`` 重置 draft 后经 ``workspace.put`` 落
         outputs.json（**覆盖式，旧版已在第 4 步归档**）。

    失败原子性：插件执行失败 → 第 4 步的归档副本回滚（删除/挪回），节点
    目录回到调用前现场，异常向上抛（job failed 可读）。引用型产物（>1MiB
    json/bytes 旁车文件）旧版只归档 outputs.json 的引用清单，旁车文件会被
    新版同名覆盖——ks/aw 两步产物全内联，当前不受影响（诚实边界）。
    并发面：同一 run 并发两次重生成未加锁（单人 Studio 语义，同
    adopt/discard 端点）。

    返回 ``{ok, run_id, node, version, archived:[归档文件名], status:"draft",
    outputs:[run_dir 相对产物路径]}``；run log 追加重生成审计行。
    """
    run_dir = precheck_regenerate(run_id, node_id, run_root=run_root)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    # ARCH-4：控制流节点在 plan 重建**之前**即拒（meta specs 的 type 键可判，
    # 免一次无谓重建；老 run 无 specs 则不可能含控制节点，走原路径）。
    specs = meta.get("node_specs")
    if isinstance(specs, list):
        for s in specs:
            if (
                isinstance(s, dict) and str(s.get("id") or "") == node_id
                and s.get("type") in ("switch", "loop")
            ):
                raise RegenerateError(
                    f"节点 {node_id!r} 是控制流节点（{s.get('type')}）——"
                    "控制流不支持单节点重生成，请重跑整条流水线"
                )
    # AI-3 复核 fix-1：优先用 meta.node_specs（实际运行的 config/wiring）重建，
    # 而非仅 {id,plugin} 回落 manifest 缺省。老 run 无 node_specs → 旧形状回退
    # （零回归）。config/inputs 非 dict 视为空（宽容读侧）。
    if isinstance(specs, list) and len(specs) == len(meta["nodes"]):
        engine_nodes = [
            {
                "id": str(s.get("id") or n),
                "plugin": str(s.get("plugin") or p),
                "config": s.get("config") if isinstance(s.get("config"), dict) else {},
                "inputs": s.get("inputs") if isinstance(s.get("inputs"), dict) else {},
                # ARCH-4：控制节点的控制键原样透传（plan_pipeline 按 type 分派
                # 解析 branch/loop_over/body）；经典节点无这些键，形状零回归。
                **{k: s[k] for k in _CONTROL_SPEC_KEYS if isinstance(s, dict) and k in s},
            }
            for s, n, p in zip(specs, meta["nodes"], meta["plugins"])
        ]
    else:
        engine_nodes = [
            {"id": str(n), "plugin": str(p)}
            for n, p in zip(meta["nodes"], meta["plugins"])
        ]
    try:
        planned = plan_pipeline(engine_nodes)
    except PipelinePlanError as err:
        raise RegenerateError(
            f"流水线重建失败（插件被卸载/manifest 漂移？）: {err}"
        ) from None
    index = next(i for i, node in enumerate(planned) if node.id == node_id)  # precheck 保证在场
    node = planned[index]

    workspace = Workspace(run_dir)
    for earlier in planned[:index]:  # 前序回放（目标节点与后序不读）
        if (workspace.root / earlier.id / "outputs.json").is_file():
            try:
                workspace.load_from_disk(earlier.id)
            except ValueError as exc:
                raise RegenerateError(str(exc)) from None

    # AI-3 复核 fix-5：重生成作用域内旁路 AI 缓存——「再来一版」必须真调
    # LLM（ks 的 chat_policed(cache=True) 在 prompt 不变时会命中旧缓存→假重生成）。
    # 包住整段归档+执行；wiring 用 importlib 局部导入避免层依赖（services 可用
    # infra，反向不成立——gateway 不 import services，ContextVar 作用域隔离）。
    from ...infra.ai.gateway import bypass_cache as _bypass_cache
    with _bypass_cache():
        return _regenerate_node_inner(
            run_id, node_id, run_dir, meta, planned, index, workspace,
            feedback=feedback, cancelled=cancelled,
        )


def _regenerate_node_inner(
    run_id: str, node_id: str, run_dir: Path, meta: dict[str, Any],
    planned: list[PlannedNode], index: int, workspace: "Workspace",
    *, feedback: str | None, cancelled: Callable[[], bool] | None,
) -> dict[str, Any]:
    """regenerate_node 的执行体（bypass_cache 上下文内调用；见外层）。"""
    node = planned[index]
    node_dir = run_dir / "workspace" / node_id
    version = 1
    for p in node_dir.iterdir():
        m = _ARCHIVED_OUTPUTS_RE.match(p.name)
        if m:
            version = max(version, int(m.group(1)) + 1)

    archived: list[str] = []
    copied: list[Path] = []                # 失败回滚：新增的归档副本
    moved_back: list[tuple[Path, Path]] = []  # 失败回滚：(归档位, 原位)
    log = _RegenLog(run_dir)
    try:
        outputs_json = node_dir / "outputs.json"
        if outputs_json.is_file():
            target = node_dir / f"outputs.v{version}.json"
            shutil.copyfile(outputs_json, target)
            copied.append(target)
            archived.append(target.name)
        # 成稿正文（aw）随版归档。排除历史归档副本（*.vN.md 及 .v1.v2.md
        # 等串味形态）——否则每次重生成把旧副本再归档一遍，2^N 指数膨胀。
        # 只归档本版成稿原名（如 subtitle-srt.md → subtitle-srt.v1.md）。
        for p in sorted(node_dir.glob("*.md")):
            if _ARCHIVED_MD_RE.search(p.name):
                continue
            target = p.with_name(f"{p.stem}.v{version}.md")
            shutil.copyfile(p, target)
            copied.append(target)
            archived.append(target.name)
        edited = node_dir / "ranges.edited.json"
        if edited.is_file():  # 生效文档让位（读面编辑版优先，不挪开新产物被遮蔽）
            target = node_dir / f"ranges.edited.v{version}.json"
            edited.rename(target)
            moved_back.append((target, edited))
            archived.append(target.name)

        if node.plugin.runtime.type != "python":
            raise RegenerateError(
                f"runtime {node.plugin.runtime.type!r} 未实现（v1=python）"
            )
        fn = _load_python_entry(node.plugin, node.plugin_dir)
        try:
            inputs, upstream_map = _gather_inputs(planned, index, workspace)
        except KeyError as exc:  # 上游无 put 记录（未跑成）/mem 引用不可复活
            raise RegenerateError(f"上游产物缺失或不可复活: {exc}") from None
        ctx = _PipelineContext(
            node_id=node.id,
            run_dir=run_dir,
            workspace=workspace,
            cancelled=cancelled,
            ai_client=None,
            uses_ai=node.plugin.uses_ai,
            on_log=log,
            upstream=upstream_map,
            regen_version=version,  # fix-2：直接重生成 sink 也落版本化新素材
        )
        config = dict(node.config)
        if feedback is not None and str(feedback).strip():
            config["feedback"] = str(feedback).strip()
        outputs = fn(inputs, config, ctx)
        if not isinstance(outputs, dict):
            raise RegenerateError(
                f"入口返回 {type(outputs).__name__}，须为 dict（键=manifest.outputs）"
            )
        absent = set(node.plugin.outputs) - set(outputs)
        if absent:
            raise OutputContractError(
                f"缺输出键 {sorted(absent)}（manifest.outputs 声明 "
                f"{sorted(node.plugin.outputs)}）"
            )
        _reset_draft_status(outputs)
        put_paths = workspace.put(node.id, outputs)
    except BaseException:
        for target, origin in moved_back:  # 现场回滚：编辑版挪回原位
            try:
                target.rename(origin)
            except OSError:
                pass
        for target in copied:  # 归档副本删除（旧 outputs.json 本体未动）
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    note = f"重生成: 旧版归档 v{version}（{len(archived)} 文件），新产物 draft 待裁决"
    if "feedback" in config:
        note += "（含改进要求）"
    log(node_id, note)

    # AI-3 复核 fix-2：级联重放后序节点，让它们消费本次新产物（aw 重生成 →
    # material-sink 落一版新 preview 素材——新 draft 成稿有回灌素材库的通道；
    # 前序节点仍零重跑）。best-effort：单个后序失败只记日志+结果，不回滚已成功
    # 的目标节点重生成（避免「改成功了却报失败」的困惑）。
    cascaded = _cascade_replay_downstream(
        run_dir, planned, index, workspace, version, log, cancelled,
    )

    return {
        "ok": True,
        "run_id": run_id,
        "node": node_id,
        "version": version,
        "archived": archived,
        "status": "draft",
        "outputs": put_paths,
        "cascaded": cascaded,
    }


def _cascade_replay_downstream(
    run_dir: Path, planned: list[PlannedNode], index: int, workspace: "Workspace",
    version: int, log: "_RegenLog", cancelled: Callable[[], bool] | None,
) -> list[dict[str, Any]]:
    """重生成目标节点后按顺序重放其后所有节点（fix-2）。

    后序从 workspace 取数——目标节点新产物已被 put 进去，前序已在上游回放；
    每个后序重放完即 put，供更后面的节点消费（线性链自然传递）。给 ctx 注入
    ``regen_version``：material-sink 据此落**新一版** preview 素材（版本化
    uri），绕开 immutable 红线且旧素材原样保留供对比。前序（index 之前）绝不
    重跑（确定性节点，重跑纯费算力）。best-effort：后序失败只记录不抛。
    """
    replayed: list[dict[str, Any]] = []
    for di in range(index + 1, len(planned)):
        dn = planned[di]
        if dn.node_type:  # ARCH-4：控制流节点不级联重放（路由/迭代语义需整链重估）
            log(dn.id, f"级联重放跳过控制流节点（{dn.node_type}）——请重跑整条流水线")
            replayed.append({"node": dn.id, "status": "skipped"})
            continue
        try:
            if dn.plugin.runtime.type != "python":
                raise _NodeFailure(f"runtime {dn.plugin.runtime.type!r} 未实现")
            inputs, upstream_map = _gather_inputs(planned, di, workspace)
            fn = _load_python_entry(dn.plugin, dn.plugin_dir)
            ctx = _PipelineContext(
                node_id=dn.id,
                run_dir=run_dir,
                workspace=workspace,
                cancelled=cancelled,
                ai_client=None,
                uses_ai=dn.plugin.uses_ai,
                on_log=log,
                upstream=upstream_map,
                regen_version=version,
            )
            outputs = fn(inputs, dict(dn.config), ctx)
            if not isinstance(outputs, dict):
                raise _NodeFailure(f"入口返回 {type(outputs).__name__}，须为 dict")
            absent = set(dn.plugin.outputs) - set(outputs)
            if absent:
                raise OutputContractError(f"缺输出键 {sorted(absent)}")
            workspace.put(dn.id, outputs)
            replayed.append({"node": dn.id, "status": "ok"})
            log(dn.id, f"级联重放 v{version}：消费上游重生成产物")
        except Exception as exc:  # best-effort：后序失败不拖垮目标节点重生成
            log(dn.id, f"级联重放失败（旧产物保留）: {exc}")
            replayed.append({"node": dn.id, "status": "failed", "error": str(exc)})
    return replayed
