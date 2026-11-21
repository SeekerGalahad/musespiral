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
  POST /api/pipelines/runs/<run_id>/adopt|discard → 三态裁决（AI-1 批
                                        2026-11-21，契约 §3.19 修订 3；body
                                        {step:'ks'|'aw', indices?}——动态段
                                        POST 本批为 URL 形（run_id 在段内，
                                        server._handle 裁决分流先于 GET 面
                                        判定），把 run 的 draft 产物转
                                        adopted/discarded）
  POST /api/pipelines/runs/<run_id>/regenerate → 节点重生成（AI-3 批
                                        2026-11-21，正典=裁决机制设计 §2.3
                                        三件套·重生成；body {step:'ks'|'aw',
                                        feedback?}）——从该节点重跑（前序不
                                        重跑），旧产物归档 outputs.v{N}.json
                                        、新产物 status 重置 draft；长动作
                                        （ks/aw 是 AI 节点）走 job
                                        kind=plugin_pipeline，前端轮询 jobs 面

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
    - ai_verdicts={node_id: {port: status}}：AI 端口裁决态（AI-1 批）——前端
      产物区徽章唯一数据源（draft→「AI 建议」琥珀 / adopted→无 / discarded→
      「已弃用」灰）；老 run 无 status 字段时该端口键缺席（前端不渲染）；
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
        # AI 端口裁决态读面（AI-1 批 2026-11-21）：{节点id: {端口: 态}}——前端
        # 徽章唯一数据源。只放标量态零正文；老 run（无 status 字段）该端口
        # 键不存在 → 前端不渲染徽章（零回归）。
        "ai_verdicts": _ai_verdicts(psp.pipeline_runs_root() / run_id),
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
    run_dir: Path, key: str, *, expect: type = list
) -> tuple[Path, Any] | None:
    """workspace 里 inline 含 <key>（值型如 expect，默认列表）的第一个节点
    （目录名序稳定）——即该端口的产出节点（编辑版文件的落盘位置）。
    expect=dict 供 article-writer 的单产物 article 端口（裁决端点用）。"""
    for node_dir, doc in _node_output_docs(run_dir):
        value = (doc.get("inline") or {}).get(key)
        if isinstance(value, expect):
            return node_dir, value
    return None


# AI 端口合法裁决态（与 key-segment/article-writer/material-sink 同枚举；
# material-sink 侧另有自己的副本 _AI_STATUSES——插件是独立可安装单元）
_AI_STATUS_VALUES = ("draft", "adopted", "discarded")


def _group_ranges_status(items: list[Any]) -> str | None:
    """ranges 整组裁决态：全部同态 → 该态；存在异态/缺态/非对象项 → "mixed"；
    一项态都没有（旧稿）→ None（调用方不进映射）。"""
    if not items:
        return None
    seen: set[str] = set()
    for item in items:
        st = item.get("status") if isinstance(item, dict) else None
        seen.add(st if isinstance(st, str) else "")  # 缺态统一记空串（≠ 任何合法态）
    if seen == {""}:
        return None
    return seen.pop() if len(seen) == 1 else "mixed"


def _ai_verdicts(run_dir: Path) -> dict[str, dict[str, str]]:
    """{node_id: {port: status}}——AI 端口裁决态读面（AI-1 批 2026-11-21）。

    形状 `{节点id: {端口名: 状态字符串}}`，**只放标量态**（draft/adopted/
    discarded；ranges 另有整组态 "mixed"）——零正文，与「计数无正文」纪律
    一致（正典=AI 产物裁决机制设计 §二）。端口口径：
      - ``article``：``inline.article.status``（article-writer 单产物）；
      - ``ranges``：整组态（全同→该态 / 异态或缺态→"mixed"），**编辑版优先**
        （ranges.edited.json 在场读编辑版——与 GET segments 读面同源，裁决
        端点写的就是这份生效文档；只读 outputs.json 会把已裁决的 run 读回
        draft）。
    缺节点目录/缺 outputs.json/缺 status 字段（本批前的老 run）→ 该端口
    不进映射（不是填 null——前端据「键不存在」判「无状态可显示」，零回归）。
    """
    out: dict[str, dict[str, str]] = {}
    for node_dir, doc in _node_output_docs(run_dir):
        inline = doc.get("inline") or {}
        ports: dict[str, str] = {}
        article = inline.get("article")
        if isinstance(article, dict):
            status = article.get("status")
            if status in _AI_STATUS_VALUES:
                ports["article"] = status
        ranges = inline.get("ranges")
        if isinstance(ranges, list):
            ranges = _effective_edited(node_dir, "ranges.edited.json", "ranges", ranges)
            group = _group_ranges_status(ranges)
            if group is not None:
                ports["ranges"] = group
        if ports:
            out[node_dir.name] = ports
    return out


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


# ── 三态裁决端点（AI-1 批 2026-11-21，契约 §3.19 修订 3；正典=AI 产物裁决
# 机制设计 §二/§三）────────────────────────────────────────────────
# draft=AI 建议（可用但 UI 标「AI 建议」徽章）/ adopted=人工确认 /
# discarded=人工否定。AI-1 落 adopt/discard；AI-3 批（2026-11-21）补
# regenerate——从该节点重跑（前序产物不动，旧版归档 outputs.v{N}.json，
# 新产物 draft 待重新裁决；feedback 拼进插件 AI prompt 尾部）。落点=
# **生效文档**：ranges 有编辑版（ranges.edited.json，E-4）改编辑版、无
# 编辑版改原始 outputs.json inline——与 GET segments「编辑版优先」读面
# 同源，前端徽章读到的就是裁决后的态；article 无编辑版面，直改
# outputs.json inline（article.status 与顶层 article_status 直判面同步）。
# 已终态（adopted→adopt 等）幂等零改。

_ADJUDICATE_STATUSES = {"adopt": "adopted", "discard": "discarded"}
_ADJUDICATE_PATH_RE = re.compile(
    r"^/api/pipelines/runs/([^/]+)/(adopt|discard|regenerate)$"
)

# 动态 POST 面（占位形——契约锁 POST_EXACT 与 API 契约文档对齐用；
# <run_id> 为运行期段，与 GET_DYNAMIC_PREFIXES 承载动态读面同一思路）
POST_DYNAMIC_SURFACE = frozenset({
    "/api/pipelines/runs/<run_id>/adopt",
    "/api/pipelines/runs/<run_id>/discard",
    "/api/pipelines/runs/<run_id>/regenerate",
})


def resolve_adjudicate_post(path: str) -> tuple[str, str] | None:
    """路径 → (裁决动作名, run_id)；非裁决动作路径返回 None（server 分派用）。"""
    m = _ADJUDICATE_PATH_RE.match(path)
    return None if m is None else (m.group(2), m.group(1))


def _validate_indices(raw: Any, total: int) -> list[int] | None:
    """indices 校验：省略 → None（全集）；空数组/非数组/非整数/越界 → 400
    可读拒绝（裁决点 18：非法参数不静默丢弃）。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise _HttpError(400, f"indices 须为下标数组或省略（省略=全集）: {raw!r}")
    if not raw:
        raise _HttpError(400, "indices 为空数组——至少选一个下标或省略（省略=全集）")
    for i in raw:
        if isinstance(i, bool) or not isinstance(i, int):  # bool 是 int 子类，显式排除
            raise _HttpError(400, f"indices 须为非负整数下标: {i!r}")
        if i < 0 or i >= total:
            raise _HttpError(
                400, f"indices 越界: {i}（产物共 {total} 项，合法下标 0-{total - 1}）"
            )
    return sorted(dict.fromkeys(raw))


def _append_run_log(run_dir: Path, node_id: str, msg: str) -> None:
    """裁决动作追加一行 run log（引擎 _log 同形状）——审计面，失败不吞裁决。"""
    try:
        with (run_dir / "log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"node": node_id, "msg": msg, "ts": now_iso()}, ensure_ascii=False
            ) + "\n")
    except OSError:
        pass  # 状态已落 outputs.json，日志追加失败不影响裁决结果


def _rewrite_inline_output(node_dir: Path, key: str, value: Any) -> None:
    """把改后的 inline 值写回 outputs.json（refs 与其余端口原样保留）。"""
    path = node_dir / "outputs.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _HttpError(500, f"outputs.json 读取失败: {exc}") from None
    if not isinstance(doc, dict):
        raise _HttpError(500, "outputs.json 结构损坏（顶层非对象）")
    inline = doc.get("inline")
    if not isinstance(inline, dict):
        inline = doc["inline"] = {}
    inline[key] = value
    try:
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise _HttpError(500, f"outputs.json 写回失败: {exc}") from None


def _adjudicate_ranges(run_dir: Path, indices: Any, target: str) -> dict[str, Any]:
    """ks 切片裁决：生效 ranges（编辑版优先）按 indices（缺省全集）盖 status。"""
    found = _find_output_node(run_dir, "ranges")
    if found is None:
        raise _HttpError(400, f"该运行无 ranges 产物节点（链上需有切片节点）: {run_dir.name}")
    node_dir, original = found
    edited_path = node_dir / "ranges.edited.json"
    dest_list, dest_file = original, ""
    if edited_path.is_file():
        try:
            data = json.loads(edited_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):  # 宽容包装形（_effective_edited 同判据）
            data = data.get("ranges")
        if isinstance(data, list):  # 编辑版优先
            dest_list, dest_file = data, "ranges.edited.json"
    chosen = _validate_indices(indices, len(dest_list))
    targets = dest_list if chosen is None else [dest_list[i] for i in chosen]
    for i, item in enumerate(targets):
        if not isinstance(item, dict):  # 先验全量再落笔——半途失败不留混合态
            raise _HttpError(400, f"ranges 产物含非对象项（下标 {i}）: {item!r}")
    changed = sum(1 for item in targets if item.get("status") != target)
    for item in targets:
        item["status"] = target
    if dest_file:
        try:
            edited_path.write_text(
                json.dumps(dest_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise _HttpError(500, f"ranges.edited.json 写回失败: {exc}") from None
    else:
        _rewrite_inline_output(node_dir, "ranges", original)
    return {"changed": changed, "total": len(dest_list), "edited": bool(dest_file)}


def _adjudicate_article(run_dir: Path, target: str) -> dict[str, Any]:
    """aw 成稿裁决：单产物（article）整稿盖 status，顶层 article_status 同步。"""
    found = _find_output_node(run_dir, "article", expect=dict)
    if found is None:
        raise _HttpError(400, f"该运行无 article 产物节点（链上需有成稿节点）: {run_dir.name}")
    node_dir, _ = found
    path = node_dir / "outputs.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _HttpError(500, f"outputs.json 读取失败: {exc}") from None
    inline = doc.get("inline") if isinstance(doc, dict) else None
    article = inline.get("article") if isinstance(inline, dict) else None
    if not isinstance(article, dict):
        raise _HttpError(500, "outputs.json 结构损坏（inline.article 非对象）")
    changed = 1 if article.get("status") != target else 0
    article["status"] = target
    if isinstance(inline.get("article_status"), str):  # 顶层直判面同步（下游免解包判态）
        inline["article_status"] = target
    try:
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise _HttpError(500, f"outputs.json 写回失败: {exc}") from None
    return {"changed": changed, "total": 1}


def run_adjudicate(
    ctx: _StudioContext, data: dict[str, Any], run_id: str, action: str
) -> dict[str, Any]:
    """POST /api/pipelines/runs/<run_id>/adopt|discard：把指定 run 的 draft
    产物转 adopted/discarded（body: {step: 'ks'|'aw', indices?}；confirm 闸门
    走 server 通用层）。动态段 POST（server 分派见 _handle 裁决分流），
    run_id 在 URL 段内——白名单回验在 _run_dir_checked（404 面，防穿越）。"""
    target = _ADJUDICATE_STATUSES[action]
    run_dir = _run_dir_checked(run_id)
    step = data.get("step")
    if step not in ("ks", "aw"):
        raise _HttpError(
            400, f"step 须为 'ks'（key-segment 切片 ranges）或 'aw'（article-writer 成稿）: {step!r}"
        )
    if step == "ks":
        outcome = _adjudicate_ranges(run_dir, data.get("indices"), target)
        node = "ranges"
    else:
        if data.get("indices") is not None:
            raise _HttpError(400, "step=aw 为单产物（article），不接受 indices（省略即整稿裁决）")
        outcome = _adjudicate_article(run_dir, target)
        node = "article"
    _append_run_log(
        run_dir, node,
        f"裁决: {node} → {target}（changed={outcome['changed']}/{outcome['total']}）",
    )
    return {
        "ok": True,
        "run_id": run_dir.name,
        "step": step,
        "status": target,
        "changed": outcome["changed"],
        "total": outcome["total"],
    }


# ── 节点重生成（AI-3 批 2026-11-21；正典=裁决机制设计 §2.3/§三 3.1/3.2）──
# POST /api/pipelines/runs/<run_id>/regenerate {step:'ks'|'aw', feedback?}：
# 「这条切片不对，再来一次」——从该节点重跑（前序不重跑），旧版归档
# outputs.v{N}.json 供对比，新产物 status 重置 draft（要重新裁决）。执行
# 体=psp.regenerate_node（长动作：ks/aw 是 AI 节点，走 job 面轮询）。


# step → (产物端口, 值型, 产物中文名)——与 run_adjudicate 的 step 枚举同表
_REGEN_STEPS: dict[str, tuple[str, type, str]] = {
    "ks": ("ranges", list, "切片 ranges"),
    "aw": ("article", dict, "成稿 article"),
}


def run_regenerate(ctx: _StudioContext, data: dict[str, Any], run_id: str) -> dict[str, Any]:
    """POST /api/pipelines/runs/<run_id>/regenerate（动态段 POST，confirm 闸门
    走 server 通用层）：提交一次节点重生成 job（kind=plugin_pipeline）。

    - step 校验（'ks'|'aw'，枚举外 400 可读）→ 按产物端口定位节点目录
      （_find_output_node——与裁决端点同款定位面，无产物节点 400）；
    - feedback 可选字符串（自然语言改进要求，如「更简洁」），空串视同
      省略；非字符串 400；
    - 提交期校验先行（psp.precheck_regenerate，零 job 纪律）：run 终态、
      节点在册、旧产物在场、plan 可重建——不过即 400 可读，失败零副作用；
    - job 体=psp.regenerate_node（归档/重跑/draft 重置/log 审计行全在引擎
      侧）；响应 {ok, job_id, run_id, step, node}，前端轮询 /api/jobs/<id>。
    """
    from .actions import _submit  # 惰性导入（环导规避，_action_pipeline_run 同款）

    run_dir = _run_dir_checked(run_id)
    step = data.get("step")
    if step not in _REGEN_STEPS:
        raise _HttpError(
            400, f"step 须为 'ks'（key-segment 切片 ranges）或 'aw'（article-writer 成稿）: {step!r}"
        )
    port, expect, label = _REGEN_STEPS[step]
    found = _find_output_node(run_dir, port, expect=expect)
    if found is None:
        raise _HttpError(
            400, f"该运行无 {label} 产物节点（链上需有该步骤）: {run_id}"
        )
    node_id = found[0].name
    feedback = data.get("feedback")
    if feedback is not None:
        if not isinstance(feedback, str):
            raise _HttpError(400, f"feedback 须为字符串（自然语言改进要求，可省略）: {feedback!r}")
        feedback = feedback.strip() or None
    try:
        psp.precheck_regenerate(run_id, node_id)
    except psp.RegenerateError as err:
        raise _HttpError(400, str(err)) from None

    def _body(_job_id: str) -> dict[str, Any]:
        return psp.regenerate_node(run_id, node_id, feedback=feedback)

    out = _submit(
        ctx, "plugin_pipeline",
        {"regenerate": run_id, "step": step, "node": node_id}, _body,
    )
    return {**out, "run_id": run_id, "step": step, "node": node_id}


# ── run 域版本链读面（AI-3 复核 fix-4，2026-11-21）──────────────────
# 归档 outputs.v{N}.json 此前只写不读——UI 文案承诺「旧版可对照」却无任何
# 端点/前端读取。这里补两读面：版本清单 + 任意两版 diff（复用 core/version/
# diff，与素材域 list_iterations/diff_iterations 同款产物面）。

_ARCHIVED_OUTPUTS_RE = re.compile(r"^outputs\.v(\d+)\.json$")


def _run_versions(run_dir: Path, node_id: str) -> list[dict[str, Any]]:
    """某节点的归档版本清单（当前 outputs.json=最新版 + outputs.vN.json 历版），
    按版本号升序。每项 {version, name, chars}——chars=正文字数（article.path 指向
    的 md 或 outputs inline 文本），零正文进清单（「计数无正文」纪律）。"""
    node_dir = run_dir / "workspace" / node_id
    out: list[dict[str, Any]] = []
    for p in node_dir.glob("outputs*.json"):
        m = _ARCHIVED_OUTPUTS_RE.match(p.name)
        version = int(m.group(1)) if m else (0 if p.name == "outputs.json" else -1)
        if version < 0:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        inline = data.get("inline") if isinstance(data, dict) else None
        inline = inline if isinstance(inline, dict) else {}
        chars = 0
        article = inline.get("article")
        if isinstance(article, dict) and article.get("path"):
            try:
                chars = len(Path(str(article["path"])).read_text(encoding="utf-8"))
            except OSError:
                chars = 0
        elif isinstance(article, dict) and isinstance(article.get("text"), str):
            chars = len(article["text"])
        else:
            ranges = inline.get("ranges")
            if isinstance(ranges, list):
                chars = sum(len(str(r.get("text") or r.get("label") or ""))
                            for r in ranges if isinstance(r, dict))
        out.append({"version": version, "name": p.name, "chars": chars})
    out.sort(key=lambda v: v["version"])
    return out


def _version_text(run_dir: Path, node_id: str, name: str) -> str:
    """归档版本的对比正文：article.path 指向的 md（当前稿）随版归档的
    {stem}.v{N}.md 优先；否则回退 outputs.json 的 inline 文本（article.text /
    ranges 逐行 label+text）。"""
    node_dir = run_dir / "workspace" / node_id
    try:
        data = json.loads((node_dir / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _HttpError(400, f"版本产物读取失败: {name}（{exc}）") from None
    inline = data.get("inline") if isinstance(data, dict) else None
    inline = inline if isinstance(inline, dict) else {}
    m = _ARCHIVED_OUTPUTS_RE.match(name)
    version = int(m.group(1)) if m else 0  # 0=当前稿
    article = inline.get("article")
    if isinstance(article, dict):
        # 当前稿 md 原文；归档版 {stem}.v{N}.md（stem 由 path 推）
        apath = Path(str(article.get("path") or ""))
        if version == 0 and apath.is_file():
            return apath.read_text(encoding="utf-8")
        if version >= 1:
            archived = apath.with_name(f"{apath.stem}.v{version}.md")
            if archived.is_file():
                return archived.read_text(encoding="utf-8")
        if isinstance(article.get("text"), str):
            return article["text"]
    ranges = inline.get("ranges")
    if isinstance(ranges, list):
        return "\n".join(
            f"{r.get('label') or ''} {r.get('text') or ''}".strip()
            for r in ranges if isinstance(r, dict)
        )
    return json.dumps(inline, ensure_ascii=False, indent=2)


def _pipeline_run_versions_payload(
    ctx: _StudioContext, run_id: str, query: dict[str, list[str]]
) -> dict[str, Any]:
    """GET /api/pipelines/runs/<id>/versions?node=<id>[&a=&b=]：run 域版本链。

    无 a/b → {"ok", "node", "versions":[{version,name,chars}]}；有 a/b（归档
    文件名，来自清单 name）→ 附 {"diff": {...}}（复用 core/version/diff，
    红删绿增 html——与素材域 diff_iterations 同款产物面）。
    """
    from ..core.version.diff import diff_result, render_diff_html

    run_dir = _run_dir_checked(run_id)
    node_id = (query.get("node") or [""])[0]
    step = (query.get("step") or [""])[0]
    if step:
        # 前端只知道 regen step（ks/aw），按产物端口定位节点——与 regenerate
        # 端点同款 _find_output_node 口径，免前端记节点 id。
        port, expect, _ = _REGEN_STEPS.get(step, (None, None, ""))
        if port is None:
            raise _HttpError(400, f"step 须为 ks/aw: {step!r}")
        found = _find_output_node(run_dir, port, expect=expect)
        if found is None:
            raise _HttpError(400, f"该运行无 {port} 产物节点")
        node_id = found[0].name
    if not node_id or "/" in node_id or ".." in node_id:
        raise _HttpError(400, "缺少或非法的 node/step 参数（版本所属节点）")
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    if node_id not in [str(n) for n in (meta.get("nodes") or [])]:
        raise _HttpError(400, f"节点不在该运行内: {node_id!r}")
    versions = _run_versions(run_dir, node_id)
    result: dict[str, Any] = {"ok": True, "node": node_id, "versions": versions}
    q = query.get("a") or []
    b = query.get("b") or []
    if q and b:
        a_name, b_name = q[0], b[0]
        valid = {v["name"] for v in versions}
        if a_name not in valid or b_name not in valid:
            raise _HttpError(400, "a/b 须为版本清单中的 name（outputs.json / outputs.vN.json）")
        d = diff_result(
            _version_text(run_dir, node_id, a_name),
            _version_text(run_dir, node_id, b_name),
            from_label=a_name, to_label=b_name,
        )
        result["diff"] = {
            **d.to_dict(),
            "html": render_diff_html(d, from_label=a_name, to_label=b_name),
        }
    return result


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
