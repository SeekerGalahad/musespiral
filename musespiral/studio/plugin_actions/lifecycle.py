"""插件生命周期端点（W14b，2026-11-24）——本地市场的写面。

四端点（均 confirm 闸门，沿既有动作接口纪律）：
  POST /api/plugins/install    {path}          安装 .mspkg
  POST /api/plugins/uninstall  {id}            卸载（内置拒绝）
  POST /api/plugins/enabled    {id, enabled}   启停（禁用=移入 .disabled/）
  POST /api/plugins/export     {id, out?}      导出 .mspkg 供分享

GET /api/plugins 的清单也扩展了 source/enabled 字段（向后兼容的加法）。
薄壳纪律：参数校验 + 服务层异常转可读 400，编排全在
`services/plugin_lifecycle`。
"""
from __future__ import annotations

import tempfile
from typing import Any, Callable

from ..security import _HttpError, _StudioContext, _require_str


def _lifecycle():
    from ...services import plugin_lifecycle

    return plugin_lifecycle


def _action_plugins_install(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """安装插件包（.mspkg）。path 必须是本机文件（不接受 URL——零外链红线）。"""
    path = _require_str(data, "path")
    try:
        return _lifecycle().install_from_package(path)
    except _lifecycle().PluginLifecycleError as exc:
        raise _HttpError(400, str(exc)) from None


def _action_plugins_uninstall(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    pid = _require_str(data, "id")
    try:
        return _lifecycle().uninstall(pid)
    except _lifecycle().PluginLifecycleError as exc:
        raise _HttpError(400, str(exc)) from None


def _action_plugins_enabled(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    pid = _require_str(data, "id")
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise _HttpError(400, "enabled 必须是布尔值")
    try:
        return _lifecycle().set_enabled(pid, enabled)
    except _lifecycle().PluginLifecycleError as exc:
        raise _HttpError(400, str(exc)) from None


def _action_plugins_export(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """导出 .mspkg。out 缺省写到临时目录（返回路径供前端下载/提示）。"""
    pid = _require_str(data, "id")
    out = data.get("out")
    if out is None:
        out = str(tempfile.mkdtemp(prefix="musespiral-plugin-")) + f"/{pid}.mspkg"
    elif not isinstance(out, str):
        raise _HttpError(400, "out 必须是字符串或 null")
    try:
        return _lifecycle().export_package(pid, out)
    except _lifecycle().PluginLifecycleError as exc:
        raise _HttpError(400, str(exc)) from None


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/plugins/install": _action_plugins_install,
    "/api/plugins/uninstall": _action_plugins_uninstall,
    "/api/plugins/enabled": _action_plugins_enabled,
    "/api/plugins/export": _action_plugins_export,
}
