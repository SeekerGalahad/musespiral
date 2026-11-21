"""F38 用户文章模板命令：musespiral new <slug> --template <name>。

templates/<name>.md → 占位符 {{title}}/{{date}}/{{series}} 纯文本替换（零
模板引擎）→ <root>/<slug>.md，进入既有 check→adapt→pack 流水线。

落盘纪律（正典 §二 F38 / §三-3.1）：
  - 落盘路径与 notes draft 的 scaffold 同一约定；frontmatter 补齐复用
    markdown_dir 的 _auto_digest / _first_line_title（scaffold 同一实现，
    不另写第二份启发式）；
  - O_EXCL 拒绝覆盖：slug 已存在 → FileExistsError（与 scaffold/adapt 同
    纪律，不透传 errno）；
  - 模板资产自包含于 articles root（<root>/templates/，随 musespiral backup
    覆盖，零新配置）；无 templates 目录（或缺 default.md）首次使用自动
    生成缺省骨架；
  - 本期**只定义不注册**——挂入 cli 主组与快照基线扩展归集成阶段（两组
    并行禁碰 cli.py）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import click
import frontmatter

from ..infra.sources.markdown_dir import _auto_digest, _first_line_title
from ..services.discover_service import DEFAULT_ARTICLES_ROOT

# 缺省模板骨架（首次使用自动落盘 templates/default.md，保留占位符原样）。
# 占位符一律写在引号内——未替换的裸 {{title}} 不是合法 YAML 标量（flow 映射
# 误解析），引号版模板本身可被 frontmatter 直接加载手改复用。
_DEFAULT_TEMPLATE = """\
---
title: "{{title}}"
date: "{{date}}"
series: "{{series}}"
tags: []
status: draft
---

# {{title}}

（正文从这里开始。）

## 小结

- 
"""


def render_template(text: str, *, title: str, series: str) -> str:
    """占位符纯文本替换（零模板引擎；未知占位符原样保留）。"""
    return (
        text.replace("{{title}}", title)
        .replace("{{date}}", date.today().isoformat())
        .replace("{{series}}", series)
    )


def _ensure_template(root: Path, name: str) -> Path:
    """定位模板文件；缺省名 default 缺失时自动生成骨架（含整个 templates 目录）。

    非缺省模板缺失 → 可读 FileNotFoundError（点名可用模板，与
    load_selectors 的报错风格一致）。生成动作走 O_EXCL 语义（已存在不覆盖）。
    """
    templates_dir = root / "templates"
    path = templates_dir / f"{name}.md"
    if path.is_file():
        return path
    if name == "default":
        templates_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")
        return path
    known = "/".join(sorted(p.stem for p in templates_dir.glob("*.md"))) or "（无）"
    raise FileNotFoundError(f"模板不存在: {path}（可用: {known}）")


def build_article(rendered: str, slug: str) -> frontmatter.Post:
    """渲染产物 → musespiral 文章：模板自带 frontmatter 键全保留，缺的按 scaffold
    纪律补齐（title 兜底 slug、digest 自动、cover/theme 默认、status=draft）。
    """
    post = frontmatter.loads(rendered)
    md = post.metadata
    body = post.content.strip()
    meta = {
        # title 兜底链与 scaffold_article 同款：模板 title > 正文 H1 > slug
        "title": str(md.get("title") or _first_line_title(body) or slug),
        "author": str(md.get("author", "")),
        "digest": str(md.get("digest") or _auto_digest(body)),
        "cover": str(md.get("cover") or "./assets/cover.png"),
        "theme": str(md.get("theme") or "lapis"),
        "code_theme": str(md.get("code_theme") or "monokai"),
        "tags": md.get("tags", []),
        "status": str(md.get("status") or "draft"),
    }
    extras = {k: v for k, v in md.items() if k not in meta}  # date/series 等模板键保留
    return frontmatter.Post(body, **{**extras, **meta})


def new_from_template(
    slug: str,
    template_name: str,
    *,
    series: str = "",
    root: str | Path | None = None,
) -> Path:
    """new 编排：定位/生成模板 → 替换 → 补齐 → O_EXCL 落盘，返回文章路径。"""
    arts_root = Path(root).expanduser() if root is not None else DEFAULT_ARTICLES_ROOT
    template = _ensure_template(arts_root, template_name)
    rendered = render_template(
        template.read_text(encoding="utf-8"), title=slug, series=series
    )
    post = build_article(rendered, slug)
    dst = arts_root / f"{slug}.md"
    if dst.exists():
        raise FileExistsError(f"目标文章已存在，拒绝覆盖: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        frontmatter.dumps(post, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return dst


@click.command()
@click.argument("slug")
@click.option("--template", "template_name", default="default", help="模板名（<root>/templates/<name>.md；缺省 default，缺失自动生成）")
@click.option("--series", default=None, help="系列名（替换 {{series}} 占位；缺省空串）")
@click.option("--root", "root_dir", default=None, type=click.Path(file_okay=False), help="文章库根（默认 ~/musespiral-articles，与 backup/discover 同源）")
def new(slug: str, template_name: str, series: str | None, root_dir: str | None) -> None:
    """从模板新建文章（F38）：占位符替换 + frontmatter 补齐，进入 check 流水线。"""
    try:
        dst = new_from_template(
            slug, template_name, series=series or "", root=root_dir
        )
    except (FileExistsError, FileNotFoundError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已新建文章: {dst}")
    click.echo(f"   模板: {template_name} | 下一步: 编辑正文 → musespiral check {dst}")
