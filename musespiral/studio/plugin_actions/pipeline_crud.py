"""plugin_actions · pipeline_crud 域——流水线定义写路径（保存）。

ARCH-2 ③（拆自 studio/plugin_actions.py 单文件 950 行，行为零变更）：

  - POST /api/pipelines（ROUTES 片段 1 条）：UI 形状 {id, plugin: manifest}
    归一为引擎形状 {id, plugin: <id>} 落盘；保存前 validate_pipeline_contract
    （后端真拒非法顺序——复核 high-2 的适配与校验双层）；
  - 流水线 id 白名单 ``_PIPE_ID_RE``（pgSlug 产物是小写字母数字连字符；
    拒绝空/穿越/过长）——run_adjudicate 域的运行提交同表复用；
  - 版本协商（ARCH-4 §四，2026-11-22）：每个节点落 plugin_version_hash
    （manifest 内容 sha256[:12]），加载时比当前插件 hash 给可读警告（不阻塞）。

存储：~/.musespiral/pipelines/<id>.json（设计 §六）。运行提交（POST
/api/pipelines/run）按域归 run_adjudicate（「运行/裁决/重生成」）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from ...core.plugins.manifest import validate_pipeline_contract
from ...core.plugins.version import HASH_KEY as VER_HASH_KEY
from ...core.plugins.version import manifest_hash
from ...jobs.store import now_iso
from ..security import _HttpError, _StudioContext
from .list_detail import _manifests_by_id, _pipelines_dir

# 流水线 id 白名单（pgSlug 产物是小写字母数字连字符；拒绝空/穿越/过长）
_PIPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


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
    version_hashes: dict[str, str] = {}
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
        # 版本协商：按节点记该 manifest 的内容 hash（§四）。单独落顶层键而非塞进
        # nodes——nodes 保持引擎形状的零回归（既有的精确形状断言/plan 直消费不变），
        # 版本元数据与执行元数据分离。前端传来的 raw dict 可能过时，以当前扫描为准。
        version_hashes[nid] = manifest_hash(manifest)
    if problems:
        raise _HttpError(400, "；".join(problems))
    errs = validate_pipeline_contract(contract_nodes)
    if errs:
        raise _HttpError(400, "契约校验未过: " + "；".join(errs))

    _pipelines_dir().mkdir(parents=True, exist_ok=True)
    payload = {
        "id": pid, "name": name, "nodes": engine_nodes, "saved": now_iso(),
        VER_HASH_KEY: version_hashes,  # {"node_id": manifest sha256[:12]}；老定义无此键
    }
    (_pipelines_dir() / f"{pid}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "id": pid, "nodes": len(engine_nodes)}


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/pipelines": _action_pipeline_save,  # §十二 保存（body id 形）
}
