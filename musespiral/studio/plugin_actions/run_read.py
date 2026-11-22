"""plugin_actions · run_read 域——运行读面 + run 产物定位原语 + 版本链。

ARCH-2 ③（拆自 studio/plugin_actions.py 单文件 950 行，行为零变更）：

  - GET /api/pipelines/runs/<run_id>（meta.json 读面 + AI 裁决态）与
    GET /api/pipelines/runs/<id>/versions?node=&a=&b=（AI-3 复核 fix-4 的
    run 域版本链清单/两版 diff）；
  - run 产物定位原语 ``_run_dir_checked``/``_node_output_docs``/
    ``_find_output_node``/``_effective_edited``——segments / run_adjudicate
    两域的公共底座（依赖方向 run_adjudicate → 本模块 ← segments，包内无环）；
  - ``_REGEN_STEPS``（step → 产物端口表）在本域与 run_adjudicate 的
    regenerate/versions 两处消费，host 于此保持单向依赖。

运行真相=~/.musespiral/pipeline_runs/<run_id>/meta.json（引擎终态写全
——steps/artifacts/failed_at）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...services import pipeline_service_plugins as psp
from ..security import _HttpError, _StudioContext
from .list_detail import _manifests_by_id


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


# step → (产物端口, 值型, 产物中文名)——与 run_adjudicate 的 step 枚举同表；
# host 于本域（run 读面）保持 run_adjudicate → run_read 单向依赖（versions
# 读面与 regenerate 提交两处消费）
_REGEN_STEPS: dict[str, tuple[str, type, str]] = {
    "ks": ("ranges", list, "切片 ranges"),
    "aw": ("article", dict, "成稿 article"),
}


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
    from ...core.version.diff import diff_result, render_diff_html

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
