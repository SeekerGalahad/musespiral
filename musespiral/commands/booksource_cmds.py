"""书源命令组（R0 书源功能 + R0.5 F-X4 文件/过滤入口，2026-11-18 架构设计 §四/§六）。

  - musespiral booksource import [TEXT_OR_URL] [--file PATH|-] [--filter KW]
      [--group KW] [--status new|update|latest] [--apply]
      导入（缺省=三态预览零落库；--file 读文件/- 读 stdin——20MB 大包只能
      走文件；过滤参数避免大包刷屏，F-X4/弹窗设计 §3.4）
  - musespiral booksource list [--group] [--q]            索引列表（轻量投影）
  - musespiral booksource refresh [url]                   刷新订阅（缺省=全部；闸门旁路）
  - musespiral booksource subs add|del|list               订阅 CRUD

对齐 media/pipeline CLI 模式：薄壳只做参数校验与输出，编排全在
services/booksource_service（单一持有者）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click


@click.group()
def booksource() -> None:
    """书源库：导入/订阅刷新/索引（引擎缺席时全功能可用，搜索靠 bookengine）。"""


@booksource.command("import")
@click.argument("text_or_url", required=False)
@click.option("--file", "file_path", default=None,
              help="从文件读取书源包（--file - 读 stdin；20MB 级大包只能走文件）")
@click.option("--apply", "do_apply", is_flag=True,
              help="落库（缺省只预览三态清单，零落盘）")
@click.option("--keep-name", is_flag=True, help="保留本地源名称（importKeepName）")
@click.option("--keep-group", is_flag=True, help="保留本地分组（importKeepGroup）")
@click.option("--keep-enable", is_flag=True, help="保留本地启用位（importKeepEnable）")
@click.option("--filter", "filter_kw", default="", help="关键词过滤：名称/URL 包含（大包防刷屏）")
@click.option("--group", "group_kw", default="", help="按分组过滤（分组名包含）")
@click.option("--status", "status_kw",
              type=click.Choice(["new", "update", "latest"], case_sensitive=False),
              default=None, help="按三态过滤（new=新增 / update=更新 / latest=已最新）")
def booksource_import(
    text_or_url: str | None,
    file_path: str | None,
    do_apply: bool,
    keep_name: bool,
    keep_group: bool,
    keep_enable: bool,
    filter_kw: str,
    group_kw: str,
    status_kw: str | None,
) -> None:
    """导入书源（JSON 文本/订阅 URL；四分支识别）。"""
    from ..core.booksource.importer import BookSourceImportError
    from ..infra.booksource_fetch import BookSourceFetchError
    from ..services import booksource_service

    try:
        text = _resolve_input_text(text_or_url, file_path)
    except ValueError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)

    kwargs = dict(keep_name=keep_name, keep_group=keep_group, keep_enable=keep_enable)
    try:
        preview = booksource_service.import_preview(text, **kwargs)
    except (BookSourceImportError, BookSourceFetchError) as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)

    # F-X1：非书源包 → 类型化边界说明（不是冷漠拒绝，exit 0）
    if preview.get("kind") != "bookSource":
        click.echo(f"ℹ️ {preview.get('message') or '无法识别的配置包'}")
        return

    items = preview["items"]
    matched = _filter_items(items, filter_kw, group_kw, status_kw)
    filters_on = bool(filter_kw.strip() or group_kw.strip() or status_kw)
    skipped = preview.get("skipped") or []
    dup_urls = preview["counts"].get("dupUrls", 0)

    if not do_apply:
        _print_items(matched)
        if filters_on:
            total = preview["counts"]
            total_n = total["isNew"] + total["isUpdate"] + total["latest"]
            click.echo(f"（过滤后 {len(matched)} / 全部 {total_n} 条）")
        tail = f" / 重复 URL {dup_urls}" if dup_urls else ""
        if skipped:
            tail += f"（已跳过 {len(skipped)} 条脏源）"
        click.echo(
            f"预览：新增 {preview['counts']['isNew']} / 更新 {preview['counts']['isUpdate']}"
            f" / 已是最新 {preview['counts']['latest']}{tail}（未落库；--apply 落库）"
        )
        return

    selections = (
        [{"bookSourceUrl": i["source"]["bookSourceUrl"], "selected": True} for i in matched]
        if filters_on else None
    )
    try:
        result = booksource_service.import_apply(text, selections, **kwargs)
    except (BookSourceImportError, BookSourceFetchError) as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(
        f"✅ 已导入 {result['stored']} 个书源（跳过 {len(result['skipped'])}"
        f"；落库前 {result['deduped_from']} 条）"
    )
    for url in result["imported"]:
        click.echo(f"   + {url}")


def _resolve_input_text(text_or_url: str | None, file_path: str | None) -> str:
    """F-X4：位置参数（粘贴）与 --file（文件/stdin）二选一，优先 --file。"""
    if file_path:
        if file_path == "-":
            body = sys.stdin.read()
            if not body.strip():
                raise ValueError("--file - 读到空 stdin")
            return body
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ValueError(f"文件不存在或不可读: {file_path}")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030")  # 社区源包偶有 GBK
    if not text_or_url or not text_or_url.strip():
        raise ValueError("缺少导入内容：传位置参数（粘贴文本/URL）或 --file <PATH>（- 读 stdin）")
    return text_or_url


def _filter_items(
    items: list[dict[str, object]], filter_kw: str, group_kw: str, status_kw: str | None
) -> list[dict[str, object]]:
    """F-X4 过滤三轴：关键词（名称/URL 包含）+ 分组包含 + 三态。"""
    matched = items
    if filter_kw.strip():
        needle = filter_kw.strip().lower()
        matched = [
            i for i in matched
            if needle in str(i["source"].get("bookSourceName") or "").lower()
            or needle in str(i["source"].get("bookSourceUrl") or "").lower()
        ]
    if group_kw.strip():
        needle = group_kw.strip().lower()
        matched = [
            i for i in matched
            if needle in str(i["source"].get("bookSourceGroup") or "").lower()
        ]
    if status_kw:
        want = {"new": "isNew", "update": "isUpdate", "latest": "latest"}[status_kw.lower()]
        matched = [i for i in matched if i["status"] == want]
    return matched


def _print_items(items: list[dict[str, object]]) -> None:
    """三态清单打印（状态徽/名称/地址）。"""
    marks = {"isNew": "＋ 新增", "isUpdate": "↑ 更新", "latest": "= 最新"}
    for item in items:
        source = item["source"]  # type: ignore[index]
        status = str(item["status"])
        click.echo(
            f" {marks.get(status, status)}  {source.get('bookSourceName') or '(未命名)'}"
            f"  {source.get('bookSourceUrl')}"
        )


@booksource.command("list")
@click.option("--group", default="", help="按分组过滤（多值分组精确命中）")
@click.option("--q", default="", help="按名称/地址关键词过滤")
def booksource_list(group: str, q: str) -> None:
    """列出书源库索引（不载规则大字段）。"""
    from ..services import booksource_service

    rows = booksource_service.list_sources(group=group, q=q)
    if not rows:
        click.echo("书源库为空。运行: musespiral booksource import <文本或URL> --apply")
        return
    for row in rows:
        flag = "✅" if row["enabled"] else "⏸ "
        group_part = f" [{row['group']}]" if row["group"] else ""
        click.echo(f" {flag} {row['bookSourceName'] or '(未命名)'}{group_part}  {row['bookSourceUrl']}")
    click.echo(f"共 {len(rows)} 个书源")


@booksource.command("refresh")
@click.argument("url", required=False)
def booksource_refresh(url: str | None) -> None:
    """刷新订阅（省略 url=全部；手动刷新旁路间隔闸门）。"""
    from ..services import booksource_service

    results = (
        [booksource_service.refresh_subscription(url, force=True)]
        if url
        else booksource_service.refresh_all(force=True)
    )
    failed = 0
    for res in results:
        action = res.get("action")
        mark = {"updated": "✅", "pending": "⏳", "skipped": "⏭ ", "failed": "❌"}.get(str(action), "•")
        detail = ""
        if action == "updated":
            detail = f" 导入 {len(res.get('imported') or [])}"
        elif action == "pending":
            detail = f" 待确认 {len(res.get('items') or [])}"
        elif action in ("skipped", "failed"):
            detail = f"  {res.get('reason') or res.get('error') or ''}"
        click.echo(f"{mark} {res.get('url')}  {action}{detail}")
        if action == "failed":
            failed += 1
    if failed:
        sys.exit(1)


@booksource.group("subs")
def booksource_subs() -> None:
    """订阅 CRUD（书源包地址 + 间隔 + 静默开关）。"""


@booksource_subs.command("add")
@click.argument("url")
@click.option("--name", default="", help="订阅名称（缺省=URL）")
@click.option("--interval", "interval_h", default=0, type=int,
              help="刷新间隔（小时；0=不限）")
@click.option("--no-silent", is_flag=True, help="关闭静默更新（刷新产待确认清单）")
def subs_add(url: str, name: str, interval_h: int, no_silent: bool) -> None:
    """新增/更新一条订阅（https 强制）。"""
    from ..infra.booksource_fetch import BookSourceFetchError
    from ..services import booksource_service

    try:
        row = booksource_service.add_sub(
            url, name=name, update_interval_h=interval_h, silent_update=not no_silent
        )
    except (ValueError, BookSourceFetchError) as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 订阅「{row['name']}」已保存（间隔 {row['update_interval_h']}h"
              f"{'，静默' if row['silent_update'] else '，需确认'}）")


@booksource_subs.command("del")
@click.argument("url")
def subs_del(url: str) -> None:
    """删除一条订阅（已导入的书源不受影响）。"""
    from ..services import booksource_service

    try:
        booksource_service.del_sub(url)
    except ValueError as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(f"✅ 订阅已删除: {url}")


@booksource_subs.command("list")
def subs_list() -> None:
    """列出全部订阅。"""
    from ..services import booksource_service

    subs = booksource_service.list_subs()
    if not subs:
        click.echo("暂无订阅。运行: musespiral booksource subs add <https URL>")
        return
    for sub in subs:
        state = {"ok": "🟢", "failed": "🔴", "pending": "⚪"}.get(str(sub["fetch_state"]), "⚪")
        click.echo(
            f" {state} {sub['name']}  {sub['url']}"
            f"  （间隔 {sub['update_interval_h']}h{'，静默' if sub['silent_update'] else '，需确认'}）"
        )

# ── bookengine 管理（2026-11-23 用户反馈批：引擎缺席期缺管理入口）────────


@click.group()
def bookengine() -> None:
    """书籍引擎（bookengine）管理：状态/启停/日志。

    引擎=JVM 微服务（:8792，R1-R2 分期建设）。缺席时书源导入/订阅/管理
    全部可用，只有「搜索书籍→选章入素材库」不可用。
    """


@bookengine.command("status")
def engine_status_cmd() -> None:
    """引擎三态：未建设 / 建设中 / 可搜（含下一步指引）。"""
    from ..services import bookengine_admin

    st = bookengine_admin.status()
    click.echo(st["headline"])
    for line in st["detail"]:
        click.echo("  " + line)
    if st["next_steps"]:
        click.echo("下一步：")
        for line in st["next_steps"]:
            click.echo("  · " + line)


@bookengine.command("demo")
@click.argument("mode", type=click.Choice(["on", "off", "status"], case_sensitive=False))
def engine_demo(mode: str) -> None:
    """mock 体验模式开/关（引擎 mock 环境开关）。

    on=书籍搜索用确定性假数据跑全流程（搜索→选书→选章→入素材库），
    引擎 R1-R2 未建设期也能体验；off=恢复真实引擎探测。改后需重启
    Studio 生效（launchd: ctl.sh restart）。
    """
    from ..services import bookengine_admin

    click.echo(bookengine_admin.set_demo(mode))


@bookengine.command("check")
def engine_check() -> None:
    """全链自检：health/search/info/toc 四端点逐项探测。"""
    from ..services import bookengine_admin

    ok, lines = bookengine_admin.check_endpoints()
    for line in lines:
        click.echo(line)
    if not ok:
        sys.exit(2)
