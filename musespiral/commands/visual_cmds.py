"""F70-F74 design 命令组 —— `musespiral design render/save/export/infographic/preset/list-templates`。

设计 §三 L4：CLI 与 Studio 双入口殊途同归（同一 services/visual_service 单一持有者）。
快照纪律（§5.4）：`musespiral --help` 与既有命令 --help 基线只增不删——新增 design
组只追加一行与自身快照，不改既有输出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..core.visual.canvas.templates import REGISTRY
from ..core.visual.canvas.model import SPECS
from ..services.visual_service import KINDS as _VISUAL_KINDS

# 设计稿类型枚举（services 单一事实源；模块级取用供 click.Choice）
_VKINDS: tuple[str, ...] = tuple(_VISUAL_KINDS)


@click.group()
def design() -> None:
    """视觉设计稿：渲染/存稿/导出/信息图/预设/模板清单（纯本地产出）。"""


def _fail(message: str) -> None:
    click.echo(f"❌ {message}", err=True)
    sys.exit(1)


@design.command("list-templates")
def list_templates_cmd() -> None:
    """列出内置模板（id/kind/适用规格）——F74 注册表数据面（CLI 版）。"""
    click.echo(f"{'id':<16} {'kind':<12} specs")
    for tid, meta in sorted(REGISTRY.items()):
        click.echo(f"{tid:<16} {meta.kind:<12} {'/'.join(meta.specs)}")


@design.command("render")
@click.option("--article", "article", default=None, type=click.Path(exists=True, dir_okay=False),
              help="文章路径（给出时补 title/hook 缺省取材）")
@click.option("--template", required=True, help="模板 id（见 design list-templates）")
@click.option("--spec", required=True, type=click.Choice(tuple(SPECS)), help="规格键")
@click.option("--params", default=None, help="params JSON 字符串（如 '{\"title\":\"T\",\"align\":\"center\"}'）")
@click.option("--theme", default="lapis", show_default=True, help="主题（色槽来源）")
@click.option("-o", "--out", default=None, type=click.Path(dir_okay=False),
              help="输出 SVG 路径（缺省打印到 stdout）")
def render_cmd(article, template, spec, params, theme, out) -> None:
    """渲染设计稿为 SVG（校验+测量分行+白名单序列化）。"""
    from ..services import visual_service

    parsed = _parse_params(params)
    try:
        svg, merged = visual_service.render(
            template=template, params=parsed, spec=spec, theme=theme,
            article_path=article,
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    if out:
        Path(out).write_text(svg, encoding="utf-8")
        click.echo(f"✅ SVG 已写出: {out}（{merged.get('title','')!s}）")
    else:
        click.echo(svg)


@design.command("save")
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", required=True, type=click.Choice(_VKINDS), help="设计稿类型")
@click.option("--template", required=True, help="模板 id")
@click.option("--spec", default=None, type=click.Choice(tuple(SPECS)), help="规格键（缺省模板首个适用）")
@click.option("--params", default=None, help="params JSON 字符串")
@click.option("--overrides", default=None, help="params_overrides JSON 字符串（手工编辑回写位）")
@click.option("--preset", "preset_id", default=None, help="预设 id（来自预设目录时回指）")
@click.option("--theme", default="lapis", show_default=True)
def save_cmd(article, kind, template, spec, params, overrides, preset_id, theme) -> None:
    """存稿：<stem>.design/design.json + 可再生 SVG（版本池先行+原子写）。"""
    from ..services import visual_service

    try:
        result = visual_service.save_design(
            article, kind=kind, template=template, spec=spec,
            params=_parse_params(params), params_overrides=_parse_params(overrides),
            preset_id=preset_id, theme=theme,
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    click.echo(f"✅ 设计稿已存: {result['dir']}（{'/'.join(result['files'])}；{result['updated_at']}）")


@design.command("export")
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", required=True, type=click.Choice(_VKINDS), help="产物类型")
@click.option("--route", default=None, type=click.Choice(("browser", "pillow")),
              help="光栅路线（缺省 B 主 C 兜底；pillow 强制 C 路）")
@click.option("--platform", default=None,
              help="封面平台（mp/xhs/channels/douyin；仅 kind=cover 生效）")
@click.option("--theme", default="lapis", show_default=True)
def export_cmd(article, kind, route, platform, theme) -> None:
    """导出 PNG（B 主 C 兜底；尺寸恒精确；kind=cover 时 mp 写回 frontmatter）。"""
    from ..services import visual_service

    try:
        result = visual_service.export_design(
            article, kind=kind, route=route, platform=platform, theme=theme
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    w, h = result["size"]
    click.echo(f"✅ 导出完成: {result['png']}（{w}×{h}，route={result['route']}）")
    if result["route"] == "C":
        click.echo("   注记: 无浏览器环境已降 C 路（Pillow 重绘，尺寸精确）")
    if kind == "cover" and platform in (None, "mp"):
        click.echo("   frontmatter cover 已写回（旧版入 .md.bak.<时间戳> 滚动版本池）")


@design.command("infographic")
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--source", "source_kind", required=True, type=click.Choice(("points", "radar", "sources")),
              help="数据源（抽数在服务层：points=文章要点 / radar=雷达信号 / sources=每源计数）")
def infographic_cmd(article, source_kind) -> None:
    """信息图抽数预览（F73；打印有效/跳过行，产物经 design export --kind infographic）。"""
    from ..services import visual_service

    try:
        result = visual_service.build_infographic(article, source_ref={"kind": source_kind})
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    points = result["params"]["points"]
    n_ok, n_skip = len(points), len(result["warnings"])
    click.echo(f"✅ 抽数完成: {n_ok} 行有效/{n_skip} 行跳过")
    for p in points:
        click.echo(f"   {p['label']}: {p['value']:g}")
    for w in result["warnings"]:
        click.echo(f"   ⚠ {w}")
    click.echo(f"   预览 SVG {len(result['svg'])} 字节（导出: musespiral design export --kind infographic）")


@design.group("preset")
def preset() -> None:
    """我的模板预设（~/.musespiral/visual_presets.d/*.json——纯 JSON，零代码）。"""


@preset.command("save")
@click.option("--id", "preset_id", required=True, help="预设 id（kebab 形态，如 my-cover-1）")
@click.option("--template", required=True, help="模板 id")
@click.option("--params", default=None, help="params JSON 字符串")
@click.option("--label", default=None, help="展示名（缺省=id）")
@click.option("--kind", default=None, type=click.Choice(_VKINDS), help="类型（缺省取模板 kind）")
def preset_save_cmd(preset_id, template, params, label, kind) -> None:
    """保存预设（同 id 幂等覆盖；纯 JSON 落盘）。"""
    from ..services import visual_service

    try:
        result = visual_service.save_preset(
            preset_id=preset_id, template=template, params=_parse_params(params),
            label=label, kind=kind,
        )
    except ValueError as exc:
        _fail(str(exc))
        return
    click.echo(f"✅ 预设已存: {result['path']}（{result['template']}/{result['kind']}）")


@preset.command("list")
def preset_list_cmd() -> None:
    """列出全部预设（CLI 与 Studio 同一列表）。"""
    from ..services import visual_service

    items = visual_service.list_presets()
    if not items:
        click.echo("（无预设：musespiral design preset save --id <kebab> --template <id> --params '{…}'）")
        return
    click.echo(f"{'id':<20} {'template':<16} {'kind':<12} label")
    for row in items:
        click.echo(f"{row['id']:<20} {str(row['template']):<16} {str(row['kind']):<12} {row['label']}")


@preset.command("show")
@click.argument("preset_id")
def preset_show_cmd(preset_id) -> None:
    """打印单条预设的 JSON（纯数据自检：json.load 可解析、无代码痕迹）。"""
    from ..services import visual_service

    path = visual_service._preset_path(preset_id)  # noqa: SLF001 - CLI 内部一致
    if not path.exists():
        _fail(f"预设不存在: {preset_id}")
        return
    click.echo(path.read_text(encoding="utf-8"))


def visual_kinds() -> tuple[str, ...]:
    """设计稿类型枚举（services 单一事实源）。"""
    from ..services.visual_service import KINDS

    return KINDS


def _parse_params(raw: str | None) -> dict:
    """params JSON 字符串 → dict（缺省/空串/坏 JSON → 可读报错）。"""
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"params 不是合法 JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        _fail("params 应为 JSON 对象")
        return {}
    return value


__all__ = ["design"]
