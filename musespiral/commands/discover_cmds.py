"""知识库选题引擎命令组：discover（scan / suggest / draft）——第十二期 F26-F29。

CLI 是薄壳：编排全部下沉 services/discover_service（判例 6），本模块只做
参数解析与展示；对服务的 import 一律写在命令函数体内（服务模块由并行
小组落地，导入期不耦合，--help 在服务缺席时仍可用）。
"""
from __future__ import annotations

import sys

import click


# ── discover（知识库选题引擎）──────────────────────────────


@click.group()
def discover() -> None:
    """知识库选题引擎：扫描候选、选题建议、生成文章骨架。"""


@discover.command("scan")
@click.option("--source", "source", default=None, type=click.Path(file_okay=False), help="扫描目录（KB 根内相对路径或绝对路径；默认源码分析目录）")
@click.option("--limit", default=20, show_default=True, help="最多显示条数")
@click.option("--min-score", "min_score", default=60, show_default=True, help="最低可发布性评分")
@click.option("--all", "include_drafted", is_flag=True, help="包含已生成过骨架的候选（默认过滤）")
@click.option("--json", "as_json", is_flag=True, help="按 F26 JSON 形状输出（含 score_breakdown/drafted）")
def discover_scan(source: str | None, limit: int, min_score: int, include_drafted: bool, as_json: bool) -> None:
    """扫描知识库，列出可发布性评分达标的候选选题（F26）。"""
    from ..services import discover_service

    try:
        items = discover_service.scan_candidates(
            source=source, limit=limit, min_score=min_score, include_drafted=include_drafted
        )
    except Exception as e:  # 目录不存在/KB 根未配置等可读错误
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    if as_json:
        import json

        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("未找到达标候选（可降低 --min-score 或检查知识库根配置）")
        return
    click.echo(f"共 {len(items)} 篇候选（按评分降序）\n")
    for i, c in enumerate(items, 1):
        drafted = " [已生成]" if c.get("drafted") else ""
        click.echo(f"{i:>2}. {c.get('score', 0):>3} 分  {c.get('title', c.get('path', ''))}{drafted}")
        click.echo(f"     {c.get('path', '')}")
        hint = c.get("skeleton_hint") or ""
        if hint:
            click.echo(f"     ❝ {hint}")


@discover.command("suggest")
@click.option("--source", "source", default=None, type=click.Path(file_okay=False), help="扫描目录（默认源码分析目录）")
@click.option("--limit", default=5, show_default=True, help="建议条数（取评分 Top-N）")
@click.option("--ai", "use_ai", is_flag=True, help="AI 增强标题与导语（需已配置 AI 档案；失败降级规则建议）")
def discover_suggest(source: str | None, limit: int, use_ai: bool) -> None:
    """从候选中挑 Top-N，输出选题角度建议（F28，--ai 可选增强）。"""
    from ..services import discover_service

    try:
        items = discover_service.scan_candidates(source=source, limit=limit, include_drafted=False)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    if not items:
        click.echo("未找到可推荐的候选")
        return
    if use_ai:
        try:
            from ..core.discovery import suggester
            from ..infra.ai import chat as _chat

            # F28 AI 增强统一走 domain 层 ai_enhance：提示词、两行解析与
            # ai_title/ai_intro 字段语义归 suggester 单点维护（本命令不自带
            # 第二套编排）；注入 task 绑定 wrapper 保持档案路由（discover_suggest）。
            n = suggester.ai_enhance(
                items, top_n=5, chat=lambda *a, **k: _chat(*a, task="discover_suggest", **k)
            )
            if n == 0:  # ai_enhance 按项吞 AIError（静默降级）→ 此处补可见警告
                click.echo("⚠️ AI 增强未生效（已降级为规则建议）", err=True)
        except Exception as e:  # 导入/未配置等整体失败 → 降级规则建议，不阻塞
            click.echo(f"⚠️ AI 增强不可用（降级为规则建议）：{e}", err=True)
    click.echo(f"Top {len(items)} 选题建议\n")
    for i, c in enumerate(items, 1):
        click.echo(f"{i}. {c.get('title', c.get('path', ''))}")
        click.echo(f"   系列：{c.get('series', '—')}" + (f" #{c.get('series_no')}" if c.get("series_no") else ""))
        click.echo(f"   ❝ {c.get('skeleton_hint', '')}")
        if c.get("reason"):
            click.echo(f"   理由：{c['reason']}")
        if c.get("ai_title"):
            click.echo(f"   AI 标题：{c['ai_title']}")
            if c.get("ai_intro"):
                click.echo(f"   AI 导语：{c['ai_intro']}")


@discover.command("draft")
@click.argument("path", type=click.Path())
@click.option("--series", default=None, help="系列名（默认由 suggester 规则推断）")
@click.option("-o", "--out", "output_dir", default=None, type=click.Path(file_okay=False), help="骨架输出目录（默认文章根）")
def discover_draft(path: str, series: str | None, output_dir: str | None) -> None:
    """从一篇知识库文档生成文章骨架（F29；同名已存在拒绝覆盖）。"""
    from ..services import discover_service

    try:
        output = discover_service.draft_from_kb(path, series=series, output_dir=output_dir)
    except FileExistsError as e:
        click.echo(f"❌ 骨架已存在（拒绝覆盖）: {e.filename or e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已生成文章骨架: {output}")
    click.echo("   下一步: 补充正文与封面 → musespiral check → musespiral preview（骨架注释标注了各章节来源）")
