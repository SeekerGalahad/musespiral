"""F38 用户文章模板 new 命令 — 单元/集成测试（正典 §五「new」行）。

覆盖：占位符替换（{{title}}/{{date}}/{{series}}）；无 templates 目录自动
生成 default.md（骨架保留占位符原样）；slug 已存在 O_EXCL 拒绝；frontmatter
补齐（scaffold 纪律：digest 自动/cover 默认/status=draft）；非缺省模板缺失
可读报错；新文可被 parse_article 消费（入队列前置）。

本期只定义不注册（挂 cli 归集成）——测试直接 invoke 命令对象。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.new_cmds import build_article, new, new_from_template, render_template
from musespiral.core.content.parser import parse_article


def test_render_template_placeholders() -> None:
    out = render_template(
        "---\ntitle: {{title}}\ndate: {{date}}\nseries: {{series}}\n---\n\n# {{title}}",
        title="缓存三讲",
        series="性能优化",
    )
    assert out == (
        f"---\ntitle: 缓存三讲\ndate: {date.today().isoformat()}\n"
        "series: 性能优化\n---\n\n# 缓存三讲"
    )


def test_render_template_unknown_placeholder_kept_verbatim() -> None:
    """零模板引擎：未约定占位符原样保留（不猜语义）。"""
    assert render_template("{{unknown}} {{title}}", title="T", series="") == "{{unknown}} T"


def test_new_generates_default_template_when_missing(tmp_path: Path) -> None:
    """无 templates 目录 → 自动生成 default.md（骨架保留占位符）并产出文章。"""
    dst = new_from_template("my-post", "default", root=tmp_path)
    template = tmp_path / "templates" / "default.md"
    assert template.is_file()
    assert "{{title}}" in template.read_text(encoding="utf-8")  # 模板保留占位符原样

    assert dst == tmp_path / "my-post.md"
    article = parse_article(dst)
    assert article.metadata["title"] == "my-post"  # {{title}}←slug
    assert str(article.metadata["date"]) == date.today().isoformat()
    assert article.metadata["series"] == ""
    assert article.metadata["status"] == "draft"
    assert article.metadata["cover"] == "./assets/cover.png"  # scaffold 补齐默认
    assert article.metadata["digest"]  # 自动摘要非空


def test_new_custom_template_placeholder_and_series(tmp_path: Path) -> None:
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "weekly.md").write_text(
        "---\ntitle: {{title}}\nseries: {{series}}\ndate: {{date}}\n"
        "tags: [周报]\ntheme: phycat\n---\n\n本周要点：{{title}}。\n",
        encoding="utf-8",
    )
    dst = new_from_template("w47", "weekly", series="效率周刊", root=tmp_path)
    article = parse_article(dst)
    assert article.metadata["title"] == "w47"
    assert article.metadata["series"] == "效率周刊"
    assert article.metadata["tags"] == ["周报"]  # 模板自带键保留
    assert article.metadata["theme"] == "phycat"  # 模板值优先于补齐默认
    assert "本周要点：w47。" in article.raw_body


def test_new_refuses_existing_slug(tmp_path: Path) -> None:
    new_from_template("dup", "default", root=tmp_path)
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        new_from_template("dup", "default", root=tmp_path)


def test_new_missing_template_readable_error(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "only.md").write_text("---\ntitle: {{title}}\n---\n\nx\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="模板不存在.*可用: only"):
        new_from_template("slug", "nosuch", root=tmp_path)


def test_build_article_fills_digest_and_keeps_template_keys() -> None:
    post = build_article(
        "---\ntitle: 我的标题\nseries: S1\nauthor: 阿明\n---\n\n这是首段，足够长可以产生自动摘要。",
        "slug",
    )
    md = post.metadata
    assert md["title"] == "我的标题"
    assert md["author"] == "阿明"
    assert md["series"] == "S1"  # 非补齐键原样保留
    assert md["digest"]  # _auto_digest（与 scaffold 同一实现）
    assert md["status"] == "draft"


def test_cli_new_invocation(tmp_path: Path) -> None:
    """命令壳：成功提示 + 已存在退出码非零（注册归集成，此处直 invoke）。"""
    r = CliRunner().invoke(new, ["my-post", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "已新建文章" in r.output and "musespiral check" in r.output
    r2 = CliRunner().invoke(new, ["my-post", "--root", str(tmp_path)])
    assert r2.exit_code == 1
    assert "拒绝覆盖" in r2.output


def test_new_series_option_flows_through_cli(tmp_path: Path) -> None:
    r = CliRunner().invoke(new, ["post2", "--series", "系列甲", "--root", str(tmp_path)])
    assert r.exit_code == 0
    meta = parse_article(tmp_path / "post2.md").metadata
    assert meta["series"] == "系列甲"


def test_default_template_frontmatter_is_yaml_clean(tmp_path: Path) -> None:
    """自动生成的 default.md 本身是合法 frontmatter 文档（可直接手改复用）——
    占位符在引号内，未替换的模板也能被 YAML 安全加载。"""
    new_from_template("x", "default", root=tmp_path)
    import frontmatter as fm

    post = fm.load(tmp_path / "templates" / "default.md")
    assert post.metadata["title"] == "{{title}}"  # 模板层占位符未替换
    assert post.metadata["date"] == "{{date}}"
    assert post.metadata["series"] == "{{series}}"
