"""插件生命周期（W14a，2026-11-24）——本地市场的写面。

背景：系统原有扫描能力（`services/plugins_service.scan_plugins`）只读，
用户只能手工把目录放进 `~/.musespiral/plugins/`。本模块补五个写操作：

  list_installed / install_from_package / uninstall / set_enabled / export_package

**安全门（与既有契约纪律同构，缺一不可）**：
  1. 安装包必须是 zip，含**恰好一个** manifest.json（在根或其唯一子目录）
  2. manifest 六必需键齐全（core/plugins.manifest 校验）
  3. zip 内每条路径**不得含 `..`**、不得绝对路径、不得符号链接
  4. 解压后总体积 ≤10MB；文件数 ≤500（防 zip bomb）
  5. `builtin` 源一律拒绝卸载/禁用（随包升级，给可读原因）
  6. 禁用=移入 `.disabled/`（可恢复），不是删除
  7. 卸载仅作用于 user 源，且删前核对目标确在 plugins_dir() 内

本模块只做编排与文件落盘；manifest 校验复用 core/plugins.manifest，
路径复用 infra/plugins_loader——不重复实现规则（单一事实源纪律）。
"""
from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

from .. import config
from ..core.plugins.manifest import parse_manifest
from ..infra import plugins_loader


class PluginLifecycleError(ValueError):
    """插件生命周期可读错误（400 级，UI 直接显示）。"""


MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_FILES = 500
DISABLED_DIRNAME = ".disabled"


def _user_root() -> Path:
    return plugins_loader.plugins_dir()


def _builtin_root() -> Path:
    return plugins_loader.builtin_plugins_dir()


def list_installed() -> list[dict[str, Any]]:
    """已装插件清单：内置 + 用户，带 source/enabled 标记。

    与 `plugins_service.list_plugins` 的区别：后者只给 manifest 契约面，
    本函数给**管理面**（来源与状态），供市场视图消费。
    """
    out: list[dict[str, Any]] = []
    for source, root in (("builtin", _builtin_root()), ("user", _user_root())):
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            data, reason = plugins_loader.read_manifest(mf)
            if data is None:
                continue
            plugin, errors = parse_manifest(data)
            if plugin is None or errors:
                continue
            manifest = data  # 管理面回原始 dict（与 plugins_service 的 manifest 形状一致）
            out.append({
                "id": str(manifest.get("id") or d.name),
                "source": source,
                "enabled": True,
                "dir": str(d),
                "manifest": manifest,
            })
    # 禁用区
    dis = _user_root() / DISABLED_DIRNAME
    if dis.exists():
        for d in sorted(dis.iterdir()):
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            data, _ = plugins_loader.read_manifest(mf)
            if data is None:
                continue
            plugin, errors = parse_manifest(data)
            if plugin is None or errors:
                continue
            manifest = data
            out.append({
                "id": str(manifest.get("id") or d.name),
                "source": "user",
                "enabled": False,
                "dir": str(d),
                "manifest": manifest,
            })
    return out


def _safe_zip_entries(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """安全门：路径穿越 / 绝对路径 / 符号链接 / 体积与数量上限。"""
    infos = zf.infolist()
    if not infos:
        raise PluginLifecycleError("插件包为空")
    if len(infos) > MAX_FILES:
        raise PluginLifecycleError(f"插件包文件数 {len(infos)} 超过上限 {MAX_FILES}")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise PluginLifecycleError(f"插件包含非法路径: {name!r}")
        # 符号链接模式位（Unix 高 16 位）
        if (info.external_attr >> 16) & stat.S_IFLNK:
            raise PluginLifecycleError(f"插件包含符号链接（拒绝）: {name}")
        total += info.file_size
    if total > MAX_PACKAGE_BYTES:
        raise PluginLifecycleError(f"插件包解压体积 {total//1024//1024}MB 超过上限 10MB")
    return infos


def install_from_package(path: str | Path) -> dict[str, Any]:
    """从 .mspkg（zip）安装插件到 ~/.musespiral/plugins/<id>/。

    幂等：同 id 已装 → 可读拒绝（不覆盖，防误升级；卸载后可重装）。
    """
    p = Path(path)
    if not p.is_file():
        raise PluginLifecycleError(f"插件包不存在: {p}")
    if p.stat().st_size > MAX_PACKAGE_BYTES:
        raise PluginLifecycleError("插件包超过 10MB")
    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile:
        raise PluginLifecycleError("插件包不是合法 zip（.mspkg）") from None
    with zf:
        infos = _safe_zip_entries(zf)
        # 找 manifest：根或唯一顶层子目录
        roots = {i.filename.split("/")[0] for i in infos if i.filename and not i.is_dir()}
        mf_names = [i for i in infos if i.filename.count("/") <= 1
                    and i.filename.rsplit("/", 1)[-1] == "manifest.json"]
        if len(mf_names) != 1:
            raise PluginLifecycleError(f"插件包须含恰好一个 manifest.json（实际 {len(mf_names)}）")
        raw = zf.read(mf_names[0])
        try:
            data = __import__("json").loads(raw)
        except Exception:
            raise PluginLifecycleError("manifest.json 不是合法 JSON") from None
        plugin, errors = parse_manifest(data)
        if plugin is None:
            raise PluginLifecycleError(f"manifest 未过契约门: {'; '.join(errors[:3])}")
        manifest = data
        pid = str(manifest.get("id") or "").strip()
        if not pid or ".." in pid or "/" in pid:
            raise PluginLifecycleError(f"插件 id 非法: {pid!r}")
        dest = _user_root() / pid
        if dest.exists():
            raise PluginLifecycleError(f"插件 {pid} 已安装（先卸载再重装；不覆盖防误升级）")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _mf_name = mf_names[0].filename
        prefix = _mf_name.rsplit("/", 1)[0] + "/" if "/" in _mf_name else ""
        tmp = dest.with_name(dest.name + ".installing")
        try:
            tmp.mkdir(parents=True)
            for info in infos:
                if info.is_dir() or not info.filename.startswith(prefix):
                    continue
                rel = info.filename[len(prefix):]
                if not rel:
                    continue
                target = (tmp / rel).resolve()
                if not str(target).startswith(str(tmp.resolve())):
                    raise PluginLifecycleError(f"包内路径越界: {rel}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
            tmp.rename(dest)
        except Exception:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            raise
    return {"ok": True, "id": pid, "source": "user"}


def _locate_installed(plugin_id: str) -> Path:
    """定位已装用户插件目录（含禁用区）；拒绝内置与路径穿越。"""
    if not plugin_id or ".." in plugin_id or "/" in plugin_id:
        raise PluginLifecycleError(f"插件 id 非法: {plugin_id!r}")
    for root in (_user_root(), _user_root() / DISABLED_DIRNAME):
        d = root / plugin_id
        if d.is_dir() and (d / "manifest.json").is_file():
            return d
    # 命中内置 → 明确拒绝（不是「未找到」）
    b = _builtin_root() / plugin_id
    if b.is_dir() and (b / "manifest.json").is_file():
        raise PluginLifecycleError(f"插件 {plugin_id} 是内置插件，随包升级，不可卸载/禁用")
    raise PluginLifecycleError(f"插件未安装: {plugin_id}")


def uninstall(plugin_id: str) -> dict[str, Any]:
    """卸载用户插件（目录必须在 plugins_dir 内；内置拒绝）。"""
    d = _locate_installed(plugin_id)
    root = d.parent
    if root != _user_root():
        raise PluginLifecycleError("插件目录不在用户安装区，拒绝删除")
    shutil.rmtree(d)
    return {"ok": True, "id": plugin_id}


def set_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    """启停：禁用移入 `.disabled/`，启用移回（不删文件，可恢复）。"""
    if not isinstance(enabled, bool):
        raise PluginLifecycleError("enabled 必须是布尔值")
    d = _locate_installed(plugin_id)
    base = _user_root()
    if enabled:
        if d.parent == base:
            return {"ok": True, "id": plugin_id, "enabled": True}  # 幂等
        dest = base / plugin_id
    else:
        if d.parent.name == DISABLED_DIRNAME:
            return {"ok": True, "id": plugin_id, "enabled": False}  # 幂等
        dest = base / DISABLED_DIRNAME / plugin_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    d.rename(dest)
    return {"ok": True, "id": plugin_id, "enabled": enabled}


def export_package(plugin_id: str, out_path: str | Path) -> dict[str, Any]:
    """把已装插件打包为 .mspkg（manifest + 代码 + README）供分享。"""
    d = _locate_installed(plugin_id)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                zf.write(f, plugin_id + "/" + f.relative_to(d).as_posix())
    return {"ok": True, "id": plugin_id, "path": str(out)}
