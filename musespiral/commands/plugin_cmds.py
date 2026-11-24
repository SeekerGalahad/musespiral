"""插件生命周期命令组（W14b，2026-11-24）：本地市场的 CLI 面。

  musespiral plugin list                          已装插件（来源/状态）
  musespiral plugin install <pkg>                 安装 .mspkg
  musespiral plugin uninstall <id>                卸载（内置拒绝）
  musespiral plugin enable|disable <id>           启停
  musespiral plugin export <id> [--out PATH]      导出 .mspkg 供分享

薄壳：编排全在 services/plugin_lifecycle（与 API 端点同源，单一事实源）。
"""
from __future__ import annotations

import sys

import click


@click.group()
def plugin() -> None:
    """插件生命周期：安装/卸载/启停/导出（本地市场，数据不出本机）。"""


@plugin.command("list")
def plugin_list() -> None:
    """已装插件清单（来源 + 启用状态）。"""
    from ..services import plugin_lifecycle

    items = plugin_lifecycle.list_installed()
    if not items:
        click.echo("暂无插件。安装：musespiral plugin install <pkg.mspkg>")
        return
    builtin = [i for i in items if i["source"] == "builtin"]
    user = [i for i in items if i["source"] == "user"]
    click.echo(f"共 {len(items)} 个（内置 {len(builtin)} / 用户 {len(user)}）")
    for i in items:
        src = "内置" if i["source"] == "builtin" else "用户"
        state = "启用" if i["enabled"] else "禁用"
        mid = (i["manifest"].get("name") or {})
        label = mid.get("zh") if isinstance(mid, dict) else str(mid)
        click.echo(f"  [{src}/{state}] {i['id']}  {label or ''}")


@plugin.command("install")
@click.argument("pkg")
@click.option("--inspect", "inspect_only", is_flag=True, help="只展示透明清单不安装（MCP SEP-1024 预装知情）")
@click.option("--yes", "-y", is_flag=True, help="跳过确认直接安装（已看过透明清单时用）")
def plugin_install(pkg: str, inspect_only: bool, yes: bool) -> None:
    """安装插件包（先展示透明清单再确认——MCP SEP-1024 要求）。"""
    from ..services import plugin_lifecycle

    # W15-A1：先展示透明清单（安装内容完整未截断——权威实践）
    try:
        info = plugin_lifecycle.inspect_package(pkg)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"插件：{info['id']} v{info['version']}  [{info['kind']}]")
    click.echo(f"入口：{info['entry']}")
    click.echo(f"权限：{', '.join(info['permissions']) or '（无声明）'}"
               + ("  [uses_ai]" if info["uses_ai"] else ""))
    click.echo(f"文件（{len(info['files'])} 个）：")
    for f in info["files"]:
        click.echo(f"  {f['size']:>7d}  {f['path']}")
    if info["warnings"]:
        click.echo("⚠ 警告：")
        for w in info["warnings"]:
            click.echo(f"  · {w}")
        click.echo("  （权限声明不是沙箱——插件与宿主同等权限）")
    if inspect_only:
        return
    if not yes:
        click.echo()
        if not click.confirm("确认安装以上内容？", default=False):
            click.echo("已取消（未安装任何文件）")
            return
    try:
        r = plugin_lifecycle.install_from_package(pkg)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已安装: {r['id']}（来源 user）")


@plugin.command("uninstall")
@click.argument("plugin_id")
def plugin_uninstall(plugin_id: str) -> None:
    """卸载用户插件（内置插件随包升级，不可卸载）。"""
    from ..services import plugin_lifecycle

    try:
        plugin_lifecycle.uninstall(plugin_id)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已卸载: {plugin_id}")


@plugin.command("enable")
@click.argument("plugin_id")
def plugin_enable(plugin_id: str) -> None:
    """启用插件（从 .disabled/ 移回）。"""
    from ..services import plugin_lifecycle

    try:
        plugin_lifecycle.set_enabled(plugin_id, True)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已启用: {plugin_id}")


@plugin.command("disable")
@click.argument("plugin_id")
def plugin_disable(plugin_id: str) -> None:
    """禁用插件（移入 .disabled/，可随时恢复）。"""
    from ..services import plugin_lifecycle

    try:
        plugin_lifecycle.set_enabled(plugin_id, False)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已禁用: {plugin_id}（移入 .disabled/，enable 可恢复）")


@plugin.command("export")
@click.argument("plugin_id")
@click.option("--out", "out_path", default=None, help="输出路径（缺省 ./<id>.mspkg）")
def plugin_export(plugin_id: str, out_path: str | None) -> None:
    """导出插件为 .mspkg（manifest + 代码 + README）供分享。"""
    from ..services import plugin_lifecycle

    out = out_path or f"{plugin_id}.mspkg"
    try:
        r = plugin_lifecycle.export_package(plugin_id, out)
    except plugin_lifecycle.PluginLifecycleError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已导出: {r['path']}")


@plugin.command("block")
@click.argument("plugin_id")
@click.option("--remove", is_flag=True, help="从拒绝名单移除")
def plugin_block(plugin_id: str, remove: bool) -> None:
    """加入/移出拒绝名单（.blocked.json）。"""
    from ..services import plugin_lifecycle

    r = plugin_lifecycle.block(plugin_id, unblock=remove)
    verb = "已从拒绝名单移除" if remove else "已加入拒绝名单"
    click.echo(f"✅ {verb}: {plugin_id}（当前 {r['total']} 个）")
