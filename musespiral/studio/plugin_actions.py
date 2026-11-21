"""插件系统 HTTP 接驳层（插件系统设计 §十二 as-built，PL-3 复核修复批 2026-11-20，
API 契约 §3.19）。

后端此前零 /api/plugins*、/api/pipelines* 路由（复核 high-1）：前端全按
「端点未接驳」占位降级，2668 全绿掩盖服务缺席。本模块落消费面全集——
前端 assets/views/{plugins,plugin-run}.js 实际消费的 7 个端点（设计 §十二
的 /{id} 形动态 POST 改为精确路径 + body id，server.py 分派零改动）：

  GET  /api/plugins                    → plugins_service.list_plugins（重扫即事实）
  GET  /api/plugins/<id>/check         → plugins_service.check_plugin
  POST /api/plugins/refresh            → plugins_service.refresh（confirm 闸门）
  GET  /api/pipelines                  → 已存流水线清单（节点 manifest 联表再水化）
  GET  /api/pipelines/<id>             → 单流水线定义（同上再水化）
  POST /api/pipelines                  → 保存：UI 形状 {id, plugin: manifest} 归一为
                                        引擎形状 {id, plugin: <id>} 落盘；保存前
                                        validate_pipeline_contract（后端真拒非法
                                        顺序——复核 high-2 的适配与校验双层）
  POST /api/pipelines/run              → 运行：plan_pipeline 校验先行（零 job 纪律）
                                        → _submit(kind=plugin_pipeline) → {job_id, run_id}
  GET  /api/pipelines/runs/<run_id>    → meta.json 读面（终态含 steps/artifacts）+
                                        各插件输出端口类型表（前端 segments 门控用）
  GET  /api/pipelines/runs/<run_id>/segments → 转写段落+切片 ranges（编辑版
                                        优先——E-4，PL-6 批接驳）
  POST /api/pipelines/runs/segments    → 编辑版落盘 segments.edited.json /
                                        ranges.edited.json（E-4；PL-3 先例：
                                        动态段 POST 改精确路径 + body run_id）
  GET  /api/pipelines/runs/<run_id>/audio?start=&end= → 裁段音频 wav 字节流
                                        （E-4；从源媒体现场裁——音轨 tmpdir
                                        运行后已清理）

存储：~/.musespiral/pipelines/<id>.json（设计 §六）；运行真相=~/.musespiral/pipeline_runs/
<run_id>/meta.json（引擎终态写全——steps/artifacts/failed_at）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..core.plugins.manifest import validate_pipeline_contract
from ..jobs.store import now_iso
from ..services import pipeline_service_plugins as psp
from ..services import plugins_service
from .security import _HttpError, _StudioContext

# 流水线 id 白名单（pgSlug 产物是小写字母数字连字符；拒绝空/穿越/过长）
_PIPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _pipelines_dir() -> Path:
    """流水线定义区：~/.musespiral/pipelines/（设计 §六，随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / "pipelines"


def _manifests_by_id() -> dict[str, dict[str, Any]]:
    valid, _skipped = plugins_service.scan_plugins()
    return {item["manifest"]["id"]: item["manifest"] for item in valid}


def _rehydrate(nodes: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """引擎形状节点 → 前端消费形状：plugin id 联表回完整 manifest（画布 in/out
    类型、hasSegs 门控都从 manifest 渲染——「manifest 即 UI」读面同纪律）。
    插件已卸载时保留 id 骨架（不 404 整条流水线——清单可诊断）。"""
    out: list[dict[str, Any]] = []
    for node in nodes:
        pid = str(node.get("plugin") or "")
        manifest = by_id.get(pid) or {"id": pid, "kind": "process",
                                      "inputs": {}, "outputs": {}}
        out.append({"id": str(node.get("id") or pid), "plugin": manifest})
    return out


def _load_pipeline(pid: str) -> dict[str, Any]:
    path = _pipelines_dir() / f"{pid}.json"
    if not path.is_file():
        raise _HttpError(404, f"流水线不存在: {pid}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise _HttpError(500, f"流水线定义读取失败: {pid}") from None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise _HttpError(500, f"流水线定义损坏: {pid}")
    return data


# ── GET 读面（routing._get_response 分派）──────────────────────


def _plugins_payload() -> dict[str, Any]:
    """GET /api/plugins：清单（含 manifest）+ skipped（坏目录不炸整面）。"""
    return plugins_service.list_plugins()


def _plugin_check_payload(ctx: _StudioContext, plugin_id: str) -> dict[str, Any]:
    """GET /api/plugins/<id>/check：依赖探测（shutil.which，正典 §十二）。"""
    if not plugin_id or "/" in plugin_id:
        raise _HttpError(404, f"插件不存在: {plugin_id!r}")
    out = plugins_service.check_plugin(plugin_id)
    if out is None:
        raise _HttpError(404, f"插件不存在: {plugin_id}")
    return out


def _pipelines_payload() -> dict[str, Any]:
    """GET /api/pipelines：已存流水线（id 排序稳定输出）。"""
    by_id = _manifests_by_id()
    items: list[dict[str, Any]] = []
    if _pipelines_dir().is_dir():
        for path in sorted(_pipelines_dir().glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # 单条坏定义不炸清单（skipped 语义在插件面）
            if isinstance(data, dict) and isinstance(data.get("nodes"), list):
                items.append({
                    "id": str(data.get("id") or path.stem),
                    "name": str(data.get("name") or path.stem),
                    "nodes": _rehydrate(data["nodes"], by_id),
                })
    return {"pipelines": items}


def _pipeline_detail_payload(ctx: _StudioContext, pid: str) -> dict[str, Any]:
    """GET /api/pipelines/<id>：单流水线定义（节点 manifest 再水化）。"""
    data = _load_pipeline(pid)
    return {
        "id": str(data.get("id") or pid),
        "name": str(data.get("name") or pid),
        "nodes": _rehydrate(data["nodes"], _manifests_by_id()),
    }


def _pipeline_run_payload(ctx: _StudioContext, run_id: str) -> dict[str, Any]:
    """GET /api/pipelines/runs/<run_id>：运行读面（meta.json 唯一真相）。

    - 终态 steps/artifacts 由引擎终态写入（psp._write_meta 终态写全）；
    - plugins=[{id, outputs}] 输出端口类型表：前端据「链上是否有 segments 型
      输出」门控转写编辑面板（复核 medium-4——不装在任何链上）；
    - failed 时附可读 error（失败节点+原因），前端 toast 消费。
    """
    if not psp._RUN_ID_RE.match(run_id or ""):
        raise _HttpError(404, f"运行不存在: {run_id!r}")
    meta_path = psp.pipeline_runs_root() / run_id / "meta.json"
    if not meta_path.is_file():
        raise _HttpError(404, f"运行不存在: {run_id}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise _HttpError(500, f"运行记录读取失败: {run_id}") from None
    by_id = _manifests_by_id()
    plugins_out: list[dict[str, Any]] = []
    for pid in meta.get("plugins") or []:
        manifest = by_id.get(str(pid))
        outputs = (
            {k: {"type": v.get("type")} for k, v in (manifest.get("outputs") or {}).items()}
            if manifest else {}
        )
        plugins_out.append({"id": str(pid), "outputs": outputs})
    steps = meta.get("steps") or []
    error = ""
    if meta.get("status") == "failed":
        failed_at = str(meta.get("failed_at") or "")
        detail = next((s.get("detail") or "" for s in steps if s.get("step") == failed_at), "")
        error = (failed_at + "：" + detail).strip("：")
    return {
        "run_id": run_id,
        "status": str(meta.get("status") or "running"),
        "steps": steps,
        "artifacts": meta.get("artifacts") or [],
        "plugins": plugins_out,
        **({"error": error} if error else {}),
    }


# ── E-4 转写编辑接驳（PL-6 批，台账 §四「PL-6 = E-3 + E-4」；契约 §3.19）──
# 前端转写编辑面板（plugin-run.js：低置信告警/逐段编辑/听原声/ranges 增删）
# 的三端点。编辑版文件 = key-segment/frame-grab 既有的 ``segments.edited.json``
# / ``ranges.edited.json`` 消费面（评审 G-2「编辑版优先」）——POST 落盘位置
# 就是这两个插件的读取位置，改完重跑即生效。


def _run_dir_checked(run_id: str) -> Path:
    """run_id 白名单 + meta.json 存在性（404 面；防路径穿越同 _RUN_ID_RE）。"""
    if not psp._RUN_ID_RE.match(run_id or ""):
        raise _HttpError(404, f"运行不存在: {run_id!r}")
    run_dir = psp.pipeline_runs_root() / run_id
    if not (run_dir / "meta.json").is_file():
        raise _HttpError(404, f"运行不存在: {run_id}")
    return run_dir


def _node_output_docs(run_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """workspace 各节点目录 → 完整 outputs.json 文档（inline+refs；坏文件跳过
    不炸面——单节点损坏不该拖垮整个编辑面板）。"""
    ws = run_dir / "workspace"
    if not ws.is_dir():
        return []
    out: list[tuple[Path, dict[str, Any]]] = []
    for node_dir in sorted(p for p in ws.iterdir() if p.is_dir()):
        f = node_dir / "outputs.json"
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((node_dir, data))
    return out


def _find_output_node(
    run_dir: Path, key: str
) -> tuple[Path, list[dict[str, Any]]] | None:
    """workspace 里 inline 含列表键 <key> 的第一个节点（目录名序稳定）——
    即该端口的产出节点（编辑版文件的落盘位置）。"""
    for node_dir, doc in _node_output_docs(run_dir):
        value = (doc.get("inline") or {}).get(key)
        if isinstance(value, list):
            return node_dir, value
    return None


def _effective_edited(
    node_dir: Path, filename: str, wrapper_key: str,
    original: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """编辑版优先（key-segment/frame-grab 同语义）：edited 文件存在且可读
    （宽容 {wrapper_key:[…]} 包装形）→ 编辑版；否则原始输出（编辑文件是
    增强不是依赖）。"""
    edited = node_dir / filename
    if not edited.is_file():
        return original
    try:
        data = json.loads(edited.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return original
    if isinstance(data, dict):
        data = data.get(wrapper_key)
    return data if isinstance(data, list) else original


def _pipeline_run_segments_payload(ctx: _StudioContext, run_id: str) -> dict[str, Any]:
    """GET /api/pipelines/runs/<run_id>/segments：转写段落 + 切片 ranges。

    编辑版优先——已编辑过的 run 返回编辑版（前端续编辑基线，与下游插件
    重跑时读到的同一份）；无转写产物（链无 transcribe / 失败早停）404。
    """
    run_dir = _run_dir_checked(run_id)
    found = _find_output_node(run_dir, "transcript")
    if found is None:
        raise _HttpError(404, f"该运行无转写产物（segments）: {run_id}")
    seg_node, segments = found
    segments = _effective_edited(seg_node, "segments.edited.json", "segments", segments)
    ranges: list[dict[str, Any]] = []
    found_r = _find_output_node(run_dir, "ranges")
    if found_r is not None:
        rng_node, ranges = found_r
        ranges = _effective_edited(rng_node, "ranges.edited.json", "ranges", ranges)
    return {"run_id": run_id, "segments": segments, "ranges": ranges}


def _validate_time_pair(item: Any, label: str) -> tuple[float, float]:
    if not isinstance(item, dict):
        raise _HttpError(400, f"{label} 须为对象")
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        raise _HttpError(400, f"{label} 缺有效 start/end") from None
    if not start < end:
        raise _HttpError(400, f"{label} start ≥ end（{start} ≥ {end}）")
    return start, end


def _validate_segments_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise _HttpError(400, "segments 须为非空段落列表（[{start, end, text}]）")
    for i, seg in enumerate(raw):
        _validate_time_pair(seg, f"segments[{i}]")
        if not isinstance(seg.get("text"), str):
            raise _HttpError(400, f"segments[{i}].text 须为字符串")
    return raw


def _validate_ranges_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise _HttpError(400, "ranges 须为区间列表（[{start, end, label?}]）")
    for i, rng in enumerate(raw):
        _validate_time_pair(rng, f"ranges[{i}]")
    return raw


def _action_pipeline_run_segments_save(
    ctx: _StudioContext, data: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/pipelines/runs/segments（body run_id）：编辑版落盘
    segments.edited.json / ranges.edited.json——写进**产出该端口**的节点
    目录（key-segment/frame-grab 的消费位置），原始 outputs.json 不覆盖。
    """
    run_id = str(data.get("run_id") or "").strip()
    run_dir = _run_dir_checked(run_id)
    found = _find_output_node(run_dir, "transcript")
    if found is None:
        raise _HttpError(400, f"该运行无转写产物节点，无处落 segments.edited: {run_id}")
    seg_node, _ = found
    segments = _validate_segments_payload(data.get("segments"))
    (seg_node / "segments.edited.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    saved = ["segments.edited.json"]
    raw_ranges = data.get("ranges")
    if raw_ranges is not None:
        found_r = _find_output_node(run_dir, "ranges")
        if found_r is None:
            raise _HttpError(
                400, "该运行无 ranges 产出节点，无处落 ranges.edited（链上需有切片节点）"
            )
        rng_node, _ = found_r
        ranges = _validate_ranges_payload(raw_ranges)
        (rng_node / "ranges.edited.json").write_text(
            json.dumps(ranges, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        saved.append("ranges.edited.json")
    return {
        "ok": True,
        "run_id": run_id,
        "saved": saved,
        "segments": len(segments),
        "ranges": len(raw_ranges or []),
    }


_AUDIO_MAX_CLIP_S = 120.0  # 裁段上限（听段落原声用，非整轨下载面）
_AUDIO_TIMEOUT_S = 120


def _find_media_path(run_dir: Path) -> Path | None:
    """源媒体定位：workspace 各节点 outputs.json 的 refs 里第一个
    kind=path 且文件存在的引用（local-file 的「原路径不复制」产出）。"""
    for node_dir, doc in _node_output_docs(run_dir):
        refs = doc.get("refs")
        if not isinstance(refs, dict):
            continue
        for ref in refs.values():
            if not (isinstance(ref, dict) and ref.get("kind") == "path"):
                continue
            cand = Path(str(ref.get("file") or ""))
            if cand.is_file():
                return cand
    return None


def _pipeline_run_audio_response(
    ctx: _StudioContext, run_id: str, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET /api/pipelines/runs/<run_id>/audio?start=&end=：裁段音频 wav。

    转写抽的音轨在 ctx.tmpdir（运行结束即清理）——不追 run 内缓存，从源
    媒体现场裁 16kHz mono wav（transcribe 抽轨同规格，听感一致）。源媒体
    是用户本机文件（local-file path 引用）：被移动/删除 → 404 可读。
    """
    run_dir = _run_dir_checked(run_id)
    media = _find_media_path(run_dir)
    if media is None:
        raise _HttpError(
            404, f"该运行无可裁音频的源媒体（local-file 引用缺失或文件已移动）: {run_id}"
        )
    try:
        start = float((query.get("start") or [""])[0])
        end = float((query.get("end") or [""])[0])
    except ValueError:
        raise _HttpError(400, "start/end 须为秒数（?start=1.5&end=6.2）") from None
    if start < 0 or end <= start:
        raise _HttpError(400, f"须 0 ≤ start < end: start={start}, end={end}")
    if end - start > _AUDIO_MAX_CLIP_S:
        raise _HttpError(400, f"裁段过长（上限 {_AUDIO_MAX_CLIP_S:g}s）: {end - start:g}s")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise _HttpError(500, "ffmpeg 不可用：请先安装（brew install ffmpeg / apt install ffmpeg）")
    fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    wav_path = Path(tmp_name)
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error",
             "-ss", f"{start:g}", "-t", f"{end - start:g}", "-i", str(media),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
            capture_output=True, timeout=_AUDIO_TIMEOUT_S, check=False,
        )
        if proc.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
            tail = proc.stderr.decode("utf-8", "replace")[-300:]
            raise _HttpError(500, f"裁段音频生成失败: {tail}")
        return 200, "audio/wav", wav_path.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise _HttpError(500, f"裁段音频生成超时（>{_AUDIO_TIMEOUT_S}s）") from exc
    finally:
        wav_path.unlink(missing_ok=True)


# ── POST 动作（actions._ACTION_ROUTES 合并面）──────────────────


def _action_plugins_refresh(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/plugins/refresh：重扫（v1 refresh ≡ 全量重扫，无缓存）。"""
    return {"ok": True, **plugins_service.refresh()}


def _action_pipeline_save(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/pipelines：保存（含静态契约校验——非法顺序 400，不落盘）。

    载荷归一（复核 high-2）：前端节点 {id, plugin: <manifest dict>} → 引擎
    {id, plugin: <id str>}；契约校验走 manifest 联表后的 UI 形状
    （validate_pipeline_contract 吃 manifest dict——四规则+权限并集在引擎
    plan_pipeline 复核，此处先拒可读错误）。
    """
    pid = str(data.get("id") or "").strip()
    if not _PIPE_ID_RE.match(pid):
        raise _HttpError(400, f"id 非法（字母数字连字符，≤64 字符）: {pid!r}")
    name = str(data.get("name") or pid).strip() or pid
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise _HttpError(400, "流水线至少需要一个节点")

    by_id = _manifests_by_id()
    engine_nodes: list[dict[str, Any]] = []
    contract_nodes: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, node in enumerate(raw_nodes):
        if not isinstance(node, dict):
            problems.append(f"节点[{i}] 须为对象")
            continue
        raw = node.get("plugin")
        plugin_id = str(raw.get("id") or "").strip() if isinstance(raw, dict) else str(raw or "").strip()
        manifest = by_id.get(plugin_id)
        if manifest is None:
            problems.append(
                f"节点[{i}] 插件 {plugin_id!r} 不可用（未安装/未过契约门/已卸载）"
            )
            continue
        nid = str(node.get("id") or "").strip() or manifest["id"]
        if nid in seen:
            problems.append(f"节点 id 重复: {nid}（workspace 按 node_id 存取）")
            continue
        seen.add(nid)
        engine_nodes.append({"id": nid, "plugin": manifest["id"]})
        contract_nodes.append({"id": nid, "plugin": manifest})
    if problems:
        raise _HttpError(400, "；".join(problems))
    errs = validate_pipeline_contract(contract_nodes)
    if errs:
        raise _HttpError(400, "契约校验未过: " + "；".join(errs))

    _pipelines_dir().mkdir(parents=True, exist_ok=True)
    payload = {"id": pid, "name": name, "nodes": engine_nodes, "saved": now_iso()}
    (_pipelines_dir() / f"{pid}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "id": pid, "nodes": len(engine_nodes)}


def _action_pipeline_run(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/pipelines/run：运行 → job（kind=plugin_pipeline）。

    校验先行（plan_pipeline，零 run 零 job 纪律）；run_id 预生成随响应返回
    （前端优先轮询 runs 读面，job 面兜底——psp.job_fn 语义不变）。
    """
    from .actions import _submit  # 惰性导入（tts_actions 同款，避开环导）

    pid = str(data.get("id") or "").strip()
    if not _PIPE_ID_RE.match(pid):
        raise _HttpError(400, f"id 非法: {pid!r}")
    saved = _load_pipeline(pid)
    engine_nodes = [
        {"id": str(n.get("id") or ""), "plugin": str(n.get("plugin") or "")}
        for n in saved["nodes"]
    ]
    try:
        psp.plan_pipeline(engine_nodes)  # 阶段一：manifest 可读+契约+权限并集
    except psp.PipelinePlanError as err:
        raise _HttpError(400, str(err)) from None
    run_id = psp._new_run_id(pid)

    def _body(_job_id: str) -> dict[str, Any]:
        return psp.run_pipeline(engine_nodes, run_id=run_id)

    out = _submit(ctx, "plugin_pipeline", {"pipeline": pid, "run_id": run_id}, _body)
    return {**out, "run_id": run_id}


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/plugins/refresh": _action_plugins_refresh,  # §十二 重扫
    "/api/pipelines": _action_pipeline_save,  # §十二 保存（body id 形）
    "/api/pipelines/run": _action_pipeline_run,  # §十二 运行（body id 形）
    # E-4 转写编辑保存（PL-6 批；body run_id——PL-3「动态段 POST 改精确
    # 路径」先例，server 动作分派零改动）
    "/api/pipelines/runs/segments": _action_pipeline_run_segments_save,
}
