"""知识库命令组：notes（list / draft）。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
"""
from __future__ import annotations

import sys

import click


# ── notes（本地知识库）────────────────────────────────────
@click.group()
def notes() -> None:
    """本地 Markdown 知识库：扫描笔记、生成待审核文章。"""


@notes.command("list")
@click.argument("root", type=click.Path(exists=True, file_okay=False), required=False)
@click.option("--db", "db_path", default=None, type=click.Path(), help="SQLite 笔记库路径（与目录扫描二选一）")
@click.option("--table", default="notes", help="笔记表名（配合 --db）")
@click.option("--min-chars", default=200, help="最低字数过滤")
@click.option("--limit", default=20, help="最多显示条数")
def notes_list(root: str | None, db_path: str | None, table: str, min_chars: int, limit: int) -> None:
    """列出知识库中可发布的候选笔记（目录扫描，或 --db 直连 SQLite 笔记库）。"""
    if db_path:
        from ..infra.sources.sqlite import list_from_db

        try:
            items = list_from_db(db_path, table=table, min_chars=min_chars, limit=limit)
        except (ValueError, FileNotFoundError) as e:
            click.echo(f"❌ {e}", err=True)
            sys.exit(1)
    elif root:
        from ..infra.sources.markdown_dir import scan

        items = scan(root, min_chars=min_chars)
    else:
        click.echo("❌ 需要指定知识库目录 ROOT，或用 --db <笔记库路径> 直连 SQLite 库", err=True)
        sys.exit(1)

    if not items:
        click.echo("未找到候选笔记（可能字数都不足或没有可读条目）")
        return
    click.echo(f"共 {len(items)} 篇候选（按时间倒序，显示前 {min(limit, len(items))} 篇）\n")
    for i, n in enumerate(items[:limit], 1):
        tags = f"  #{','.join(n.tags)}" if n.tags else ""
        click.echo(f"{i:>2}. {n.title}  [{n.date}] {n.chars} 字{tags}")
        click.echo(f"    {n.path}")


@notes.command("draft")
@click.argument("note", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option("--out", "-o", required=True, type=click.Path(), help="输出的文章路径")
@click.option("--from-db", "from_db", default=None, type=int, help="按 rowid 从笔记库取笔记（配合 --db）")
@click.option("--db", "db_path", default=None, type=click.Path(), help="SQLite 笔记库路径（配合 --from-db）")
@click.option("--cover", default="./assets/cover.png", help="封面图路径")
@click.option("--theme", default="lapis", help="排版主题")
def notes_draft(
    note: str | None, out: str, from_db: int | None, db_path: str | None, cover: str, theme: str
) -> None:
    """把一篇笔记转成待审核文章（保留正文，补齐 frontmatter）。"""
    from ..infra.sources.markdown_dir import scaffold_article

    lib_content: str | None = None
    if from_db is not None:
        if not db_path:
            click.echo("❌ --from-db 需要同时指定 --db <笔记库路径>", err=True)
            sys.exit(1)
        from ..infra.sources.sqlite import get_by_id

        try:
            n = get_by_id(db_path, from_db)
        except ValueError as e:
            click.echo(f"❌ {e}", err=True)
            sys.exit(1)
        if n is None:
            click.echo(f"❌ 笔记不存在: rowid {from_db}", err=True)
            sys.exit(1)
        note = str(n.path)
        if not n.path.is_file():
            # F11e：源文件缺失时回退库内 content 列正文，而非直接失败
            if not (n.content or "").strip():
                click.echo(f"❌ 该笔记的源文件不存在且库内正文为空（path 列: {n.path!s}）", err=True)
                sys.exit(1)
            lib_content = n.content
    elif not note:
        click.echo("❌ 需要指定笔记 NOTE 路径，或用 --from-db <rowid> --db <笔记库路径>", err=True)
        sys.exit(1)

    try:
        dst = scaffold_article(note, out, cover=cover, theme=theme, content=lib_content)
    except FileExistsError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已生成待审核文章: {dst}")
    if lib_content is not None:
        click.echo("   ⚠️ 源文件缺失，已用库内正文生成")
    click.echo(f"   下一步: 编辑补充内容 → musespiral check {dst} → musespiral preview {dst}")
