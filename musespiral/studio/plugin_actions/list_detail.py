"""plugin_actions · list_detail 域——插件清单/详情/重扫/依赖检查 + 流水线定义读面。

ARCH-2 ③（正典=《musespiral-极限架构与功能扩展总规划-2026-11-21》§ARCH-2，
2026-11-22 拆自 studio/plugin_actions.py 单文件 950 行，沿批1 handlers/
按域拆分模式；行为零变更）：

  - GET /api/plugins、/api/plugins/<id>/check、/api/pipelines、
    /api/pipelines/<id>（routing._get_response 分派）；
  - POST /api/plugins/refresh（ROUTES 片段 1 条）；
  - 定义读取原语 ``_pipelines_dir``/``_load_pipeline``/``_manifests_by_id``/
    ``_rehydrate``——pipeline_crud/run_read 两域的公共读底座（本包内无环：
    依赖方向 pipeline_crud → 本模块 ← run_read）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ... import config
from ...core.plugins.version import HASH_KEY as VER_HASH_KEY
from ...core.plugins.version import version_warnings
from ...services import plugins_service
from ..security import _HttpError, _StudioContext


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
    """GET /api/plugins：清单（含 manifest）+ skipped（坏目录不炸整面）。

    W14b（2026-11-24）：加法扩展每条的 source/enabled（本地市场管理面），
    旧消费方只读 manifest 字段——零破坏。
    """
    out = plugins_service.list_plugins()
    from ...services import plugin_lifecycle

    state = {i["id"]: i for i in plugin_lifecycle.list_installed()}
    for item in out.get("plugins", []):
        mid = str((item.get("manifest") or {}).get("id") or "")
        info = state.get(mid) or {}
        item["source"] = info.get("source", "builtin")
        item["enabled"] = bool(info.get("enabled", True))
    return out


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
    """GET /api/pipelines/<id>：单流水线定义（节点 manifest 再水化）。

    version_warnings（ARCH-4 §四）：保存时节点 hash 与当前已装插件 hash 不一致
    即给可读警告（可读字段 ``warnings``，前端横幅展示）——不阻塞加载，用户自行
    判断「插件升级后这条流水线是否仍兼容」。老流水线无 hash 键 → 零警告。
    """
    data = _load_pipeline(pid)
    by_id = _manifests_by_id()
    return {
        "id": str(data.get("id") or pid),
        "name": str(data.get("name") or pid),
        "nodes": _rehydrate(data["nodes"], by_id),
        "warnings": version_warnings(data["nodes"], by_id, data.get(VER_HASH_KEY)),
    }


def _action_plugins_refresh(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/plugins/refresh：重扫（v1 refresh ≡ 全量重扫，无缓存）。"""
    return {"ok": True, **plugins_service.refresh()}


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/plugins/refresh": _action_plugins_refresh,  # §十二 重扫
}
