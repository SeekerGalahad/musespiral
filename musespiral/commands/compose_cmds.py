"""创作台命令组：compose（new / add / generate / decide / adopt / status）
——第十八期 F48-F52（M2 后端；命令注册进 cli.py 归集成轮，本文件只写不注册）。

CLI 是薄壳：编排全部下沉 services/compose_service（判例 6），本模块只做
参数解析与展示；对服务的 import 一律写在命令函数体内（服务模块由并行
小组落地，导入期不耦合，--help 在服务缺席时仍可用——discover_cmds 同款
纪律）。CLI 保持同步执行（正典 §三-3：任务层只服务 Studio 长动作）。
"""
from __future__ import annotations

import sys

import click


@click.group()
def compose() -> None:
    """创作台：多素材分块 AI 起草 + 逐块三态裁决 + 采纳入稿。"""


@compose.command("new")
@click.argument("slug")
@click.option("--title", default=None, help="会话标题（缺省 = slug；正稿 title 同源）")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_new(slug: str, title: str | None, root: str | None) -> None:
    """新建创作会话（<slug>.compose/ 自包含：session.json + 空 draft.md）。"""
    from ..services import compose_service

    try:
        d = compose_service.new_session(slug, title or slug, root=root)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已建会话: {d}")
    click.echo("   下一步: musespiral compose add <slug> <素材.md> [--section …] → generate → decide → adopt")


@compose.command("add")
@click.argument("slug")
@click.argument("path", type=click.Path())
@click.option("--section", "sections", multiple=True, help="勾选的二级章节标题（可重复；缺省=全文入预算）")
@click.option("--list-sections", "list_sections", is_flag=True, help="只列出该素材的二级章节清单（不添加）")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_add(slug: str, path: str, sections: tuple[str, ...], list_sections: bool, root: str | None) -> None:
    """素材入池（章节勾选校验复用发现页 scanner；重复添加=更新勾选）。"""
    from ..services import compose_service

    try:
        if list_sections:
            known = compose_service.list_source_sections(path)
            click.echo(f"{path} 二级章节（{len(known)}）：" if known else f"{path} 无二级标题（全文即一章）")
            for s in known:
                click.echo(f"  - {s}")
            return
        info = compose_service.add_source(slug, path, list(sections) or None, root=root)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    picked = info["sections"]
    click.echo(f"✅ 已入池: {info['path']}")
    click.echo(f"   勾选章节: {'、'.join(picked) if picked else '（全文，生成时走预算节选）'}")


@compose.command("generate")
@click.argument("slug")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_generate(slug: str, root: str | None) -> None:
    """AI 按反馈再生成一轮（prompt 管线全插槽化；产出落 rounds/N.md 预览）。"""
    from ..services import compose_service

    try:
        result = compose_service.generate(slug, root=root)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 第 {result['round']} 轮已生成（{len(result['blocks'])} 块；原始产出: {result['raw']}）")
    for b in result["blocks"]:
        preview = b["text"].strip().replace("\n", " ")[:40]
        click.echo(f"  [{b['id']}] {b['kind']:<7} {preview}…")


@compose.command("decide")
@click.argument("slug")
@click.argument("round_n", type=int)
@click.argument("block_id")
@click.argument("action", type=click.Choice(["keep", "adopt_edited", "discard"]))
@click.option("--text", default=None, help="adopt_edited 的人工定稿文本")
@click.option("--direction", default=None, help="discard 的方向备注（原因/改写方向，喂下轮 prompt）")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_decide(slug: str, round_n: int, block_id: str, action: str, text: str | None, direction: str | None, root: str | None) -> None:
    """逐块三态裁决（keep 保留 / adopt_edited 改后采纳 / discard 丢弃+方向）。"""
    from ..services import compose_service

    try:
        record = compose_service.decide(
            slug, round_n, block_id, action, text=text, direction=direction, root=root
        )
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    extra = record.get("text") or record.get("direction") or ""
    click.echo(f"✅ 第 {round_n} 轮块 {block_id}: {action}" + (f"（{extra[:60]}）" if extra else ""))


@compose.command("adopt")
@click.argument("slug")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_adopt(slug: str, root: str | None) -> None:
    """采纳入稿：三态合并 → draft.md（旧稿入版本池）→ 正稿入队列 + habits 学习。"""
    from ..services import compose_service

    try:
        result = compose_service.adopt(slug, root=root)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已采纳入稿: {result['article']}")
    click.echo(
        f"   第 {result['round']} 轮合并 {result['merged_blocks']} 块；"
        f"habits 训练对 {result['habits_learned']} 条（adopt_edited 差分）"
    )
    click.echo("   下一步: musespiral check → adapt → pack → publish（进既有流水线）")


@compose.command("status")
@click.argument("slug", required=False)
@click.option("--root", "root", default=None, type=click.Path(file_okay=False), help="文章根目录（缺省 ~/musespiral-articles）")
def compose_status(slug: str | None, root: str | None) -> None:
    """会话清单（无 slug）或单会话详情（素材/轮次/裁决进度）。"""
    from ..services import compose_service

    try:
        info = compose_service.status(slug, root=root)
    except Exception as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    if "sessions" in info:
        sessions = info["sessions"]
        if not sessions:
            click.echo("暂无创作会话（musespiral compose new <slug> 起一篇）")
            return
        click.echo(f"共 {len(sessions)} 个会话\n")
        for s in sessions:
            click.echo(
                f"  {s['slug']:<24} [{s['status']}] 轮次 {s['rounds']} · 素材 {s['sources']} · {s['title']}"
            )
        return
    click.echo(f"会话: {info['slug']}（{info['status']}）— {info['title']}")
    for s in info["sources"]:
        click.echo(f"  素材: {s['path']}" + (f"（勾选: {'、'.join(s['sections'])}）" if s["sections"] else "（全文）"))
    click.echo(f"  当前稿: {len(info['draft'])} 块")
    for r in info["rounds"]:
        decided = len({d.get("block") for d in r["decisions"]})
        click.echo(f"  第 {r['n']} 轮: {len(r['blocks'])} 块 · 已裁决 {decided}")
    if info.get("final_article"):
        click.echo(f"  正稿: {info['final_article']}")
