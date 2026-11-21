"""凭证配置命令组：config（set / list）+ config kb（F46 KB 源设置）。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
第十八期 F46 新增 kb 子组（list/add/remove/set-root）——kb_sources.json
读写走 config 层，与 Studio POST /api/settings/kb 同一数据面与校验器。
"""
from __future__ import annotations

from pathlib import Path

import click

from .. import config


# ── config ────────────────────────────────────────────────
@click.group(name="config")
def config_cmd() -> None:
    """管理公众号凭证配置。"""


@config_cmd.command("set")
@click.argument("key", type=click.Choice(["appid", "secret"]))
@click.argument("value")
@click.option("--account", default="default", help="账号名")
def config_set(key: str, value: str, account: str) -> None:
    """设置 appid 或 secret。"""
    import json

    cred_path = config.home_dir() / config.CREDENTIALS_FILE
    data: dict = {"accounts": {}, "default": account}
    if cred_path.exists():
        data = json.loads(cred_path.read_text(encoding="utf-8"))
    data.setdefault("accounts", {}).setdefault(account, {})[key] = value
    data["default"] = account
    config.home_dir().mkdir(parents=True, exist_ok=True)
    cred_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"✅ 已设置 {account}.{key}")


@config_cmd.command("list")
def config_list() -> None:
    """列出已配置的公众号账号。"""
    accounts = config.list_accounts()
    if not accounts:
        click.echo("暂无配置。运行: musespiral config set appid <APPID>")
        return
    for a in accounts:
        click.echo(f"  - {a}")


# ── config kb（第十八期 F46：KB 源设置）──────────────────

_SOURCE_ORIGIN_NOTE = {
    config.KB_SOURCES_FILE: "kb_sources.json",
    config.KB_ROOT_FILE: "kb_root.txt 回退",
    "builtin": "内置默认源表",
}


@config_cmd.group("kb")
def config_kb() -> None:
    """KB 源设置（发现页筛选与选题扫描边界；存 ~/.musespiral/kb_sources.json）。"""


@config_kb.command("list")
def config_kb_list() -> None:
    """列出 KB 根与源清单（含存在性校验态）。"""
    st = config.load_kb_sources()
    root = st.get("root")
    if root:
        ok = "✅ " if Path(root).is_dir() else "⚠️ 不存在 "
        click.echo(f"根: {ok}{root}（来源: {_SOURCE_ORIGIN_NOTE.get(st['origin'], st['origin'])}）")
    else:
        click.echo("根: 未配置（musespiral config kb set-root <绝对路径>）")
    if not st["sources"]:
        click.echo("源: （空——发现页仅「全部」档）")
        return
    click.echo(f"源: {len(st['sources'])} 项")
    root_path = Path(root) if root and Path(root).is_dir() else None
    for s in st["sources"]:
        mark = ""
        if root_path is not None:
            mark = " ✅" if (root_path / s["relpath"]).is_dir() else " ⚠️ 目录缺失"
        click.echo(f"  - {s['key']}: {s['label']} → {s['relpath']}{mark}")


@config_kb.command("add")
@click.argument("key")
@click.argument("label")
@click.argument("relpath")
def config_kb_add(key: str, label: str, relpath: str) -> None:
    """添加一个源（key 唯一；relpath 为 KB 根内存在的相对目录）。"""
    st = config.load_kb_sources()
    if any(s["key"] == key.strip() for s in st["sources"]):
        click.echo(f"❌ key 已存在: {key}（先 remove 再加，或换 key）", err=True)
        raise SystemExit(1)
    root = st.get("root")
    if root:
        try:
            config.validate_kb_source(Path(root), relpath)
        except ValueError as err:
            click.echo(f"❌ {err}", err=True)
            raise SystemExit(1) from None
    else:
        click.echo("⚠️ KB 根未配置，跳过 relpath 存在性校验（set-root 后生效）", err=True)
    sources = st["sources"] + [{"key": key.strip(), "label": label.strip(), "relpath": relpath.strip()}]
    config.save_kb_sources(root, sources)
    click.echo(f"✅ 已添加源 {key} → {relpath}（{config.kb_sources_path().name}）")


@config_kb.command("remove")
@click.argument("key")
def config_kb_remove(key: str) -> None:
    """按键移除一个源。"""
    st = config.load_kb_sources()
    rest = [s for s in st["sources"] if s["key"] != key.strip()]
    if len(rest) == len(st["sources"]):
        click.echo(f"❌ key 不存在: {key}", err=True)
        raise SystemExit(1)
    config.save_kb_sources(st.get("root"), rest)
    click.echo(f"✅ 已移除源 {key}")


@config_kb.command("set-root")
@click.argument("path", type=click.Path())
def config_kb_set_root(path: str) -> None:
    """设置 KB 根（存在的绝对目录；存量源对新根越界/缺失者告警保留）。"""
    try:
        root = config.validate_kb_root(path)
    except ValueError as err:
        click.echo(f"❌ {err}", err=True)
        raise SystemExit(1) from None
    st = config.load_kb_sources()
    for s in st["sources"]:
        try:
            config.validate_kb_source(root, s["relpath"])
        except ValueError as err:
            click.echo(f"⚠️ 存量源 {s['key']} 在新根下不可用（{err}）——保留，可 remove 清理", err=True)
    config.save_kb_sources(str(root), st["sources"])
    click.echo(f"✅ KB 根 → {root}")
