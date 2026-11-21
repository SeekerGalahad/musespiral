"""集成接线测试 — 六个功能模块接入 CLI 与渲染链路（设计 §十二 实施与测试约定）。

覆盖：stats / theme new+list / check --render / publish 渲染级门 / ai learn /
notes --db / 渲染器经 themes.load_theme 加载用户主题 / adapt→pack --variant
多平台两步链路（第五期 §十四）。fixture 基于 __file__ 解析，命令输出全部走
tmp_path + MUSESPIRAL_HOME 隔离，不触碰 ~/.musespiral。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

import pytest

from musespiral.cli import cli
from musespiral.infra.ai import client as ai_mod
from musespiral.core.content.renderer import Renderer

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _copy_fixture(tmp_path: Path) -> Path:
    """把样例文章与封面/正文配图复制到 tmp_path（含 assets 子目录），返回文章路径。"""
    article = tmp_path / "sample_article.md"
    article.write_text((FIXTURES / "sample_article.md").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "cover.png").write_bytes((FIXTURES / "cover.png").read_bytes())
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "fig1.png").write_bytes((FIXTURES / "assets" / "fig1.png").read_bytes())
    return article


def _copy_source_note(tmp_path: Path) -> Path:
    """把对照审阅用的原笔记 fixture 复制到 tmp_path，返回路径。"""
    note = tmp_path / "source_note.md"
    note.write_text((FIXTURES / "source_note.md").read_text(encoding="utf-8"), encoding="utf-8")
    return note


# ---- 冒烟总门：generate → check --render（设计 §十二 实施与测试约定）----


def test_fixture_generate_then_check_render(tmp_path: Path) -> None:
    """样例文章 generate 成功产出 HTML，随后 check --render 退出码 0。"""
    runner = CliRunner()
    article = _copy_fixture(tmp_path)

    gen = runner.invoke(cli, ["generate", str(article)])
    assert gen.exit_code == 0, gen.output
    rendered = tmp_path / "sample_article.musespiral" / "rendered.html"
    assert rendered.is_file()

    chk = runner.invoke(cli, ["check", str(article), "--render"])
    assert chk.exit_code == 0, chk.output
    assert "无外链残留" in chk.output  # 外链已转脚注，参考资料段豁免
    assert "图片来源合规" in chk.output  # fixture 本地图片经真实 parser 链路命中图片检查
    assert "[FAIL]" not in chk.output
    assert "无 <script>/<iframe>/<link> 标签" in chk.output


def test_check_render_foreign_link_with_linkfoot_off_warns(tmp_path: Path) -> None:
    """linkfoot: false 时外链原样保留 → WARN 不 FAIL（§十三 F11a 用户裁决：显式选择，机器不否决）。"""
    runner = CliRunner()
    article = tmp_path / "raw.md"
    article.write_text(
        "---\ntitle: 外链直出\ncover: cover.png\ndigest: d\nlinkfoot: false\n---\n\n"
        "[外部](https://example.com/a) 链接保留。\n",
        encoding="utf-8",
    )
    (tmp_path / "cover.png").write_bytes((FIXTURES / "cover.png").read_bytes())

    result = runner.invoke(cli, ["check", str(article), "--render"])
    assert result.exit_code == 0  # WARN 不阻断
    assert "外链残留" in result.output
    assert "linkfoot:false" in result.output
    assert "[FAIL]" not in result.output


# ---- stats（F1 接线）----


def test_stats_no_log_friendly_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    # stats 读侧为 root∪home 合并（第十四期集成）——root 也隔离，测试不随真机内容漂移
    monkeypatch.setattr(
        "musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", tmp_path / "no-root"
    )
    result = CliRunner().invoke(cli, ["stats"])
    assert result.exit_code == 0
    assert "暂无发布记录" in result.output


def test_stats_prints_counts_and_recent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", tmp_path / "no-root"
    )
    log = tmp_path / "publish_log.jsonl"
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    log.write_text(
        json.dumps({"ts": now, "action": "draft", "article": "a.md", "title": "甲"}, ensure_ascii=False)
        + "\n"
        + json.dumps(
            {"ts": now, "action": "publish", "article": "a.md", "title": "甲", "article_url": "https://mp.weixin.qq.com/s/x"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["stats"])
    assert result.exit_code == 0
    assert "累计: 草稿 1 篇 / 发表 1 篇" in result.output
    assert "本月: 草稿 1 篇 / 发表 1 篇" in result.output
    assert "甲" in result.output


def test_stats_merges_dual_logs_and_excludes_demo(tmp_path: Path, monkeypatch) -> None:
    """第十四期 §1.2 契约统计侧：root∪home 合并计数、demo_ fixture 行判除。

    root 放 1 条 demo 行 + 1 条真实 draft，home 放 1 条真实 publish——
    stats 输出只见真实行；演示标题不出现在任何输出面。
    """
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    root = tmp_path / "arts-root"
    monkeypatch.setattr("musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", root)
    (root).mkdir(parents=True)
    root_log = root / "publish_log.jsonl"
    root_log.write_text(
        json.dumps(
            {
                "ts": "2026-11-11T10:30:00", "action": "draft", "article": "03_photo_essay.md",
                "title": "Studio 演示行", "media_id": "demo_draft_media_id_001",
                "platform": "mp",
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"ts": "2026-11-12T09:00:00", "action": "draft", "article": "a.md",
             "title": "root 真实草稿", "platform": "mp"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    from musespiral.infra.storage.jsonl import default_log_path

    default_log_path().parent.mkdir(parents=True, exist_ok=True)
    default_log_path().write_text(
        json.dumps(
            {"ts": "2026-11-13T09:00:00", "action": "publish", "article": "a.md",
             "title": "home 真实发表", "platform": "xhs"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["stats"])
    assert result.exit_code == 0, result.output
    assert "累计: 草稿 1 篇 / 发表 1 篇" in result.output  # demo 行不入计数
    assert "root 真实草稿" in result.output and "home 真实发表" in result.output  # 双日志合并可见
    assert "Studio 演示行" not in result.output  # demo 排除


# ---- theme new / list（F2 接线）----


def test_theme_new_then_list_marks_origin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    runner = CliRunner()

    created = runner.invoke(cli, ["theme", "new", "mytheme", "--base", "ember"])
    assert created.exit_code == 0, created.output
    target = tmp_path / "themes" / "mytheme.yaml"
    assert target.is_file()
    assert "extends: ember" in target.read_text(encoding="utf-8")

    listed = runner.invoke(cli, ["theme", "list"])
    assert listed.exit_code == 0
    assert "mytheme [用户]" in listed.output
    assert "ember [内置]" in listed.output


def test_theme_new_duplicate_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    runner = CliRunner()
    assert runner.invoke(cli, ["theme", "new", "dup"]).exit_code == 0
    again = runner.invoke(cli, ["theme", "new", "dup"])
    assert again.exit_code == 1


# ---- 渲染器走 themes.load_theme（engine 接线）----


def test_renderer_resolves_user_theme_with_extends(tmp_path: Path, monkeypatch) -> None:
    """Renderer 经 themes.load_theme 加载用户目录主题并深合并 extends 基座。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "custom.yaml").write_text(
        "name: custom\nextends: lapis\ncolors:\n  primary: '#ff0000'\n",
        encoding="utf-8",
    )
    r = Renderer(theme_name="custom")
    assert r.theme["colors"]["primary"] == "#ff0000"  # 用户覆盖生效
    assert r.theme["colors"].get("background")  # 基座其余键经深合并保留


# ---- ai learn（F5 接线）----


def test_ai_learn_saves_and_prints_habits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    draft = tmp_path / "draft.md"
    final = tmp_path / "final.md"
    draft.write_text(
        "# 原标题\n\n第一段保持不变。\n\n大家好，废话不多说，直接开始。\n\n"
        "第二段也保持不变。\n\n因此这一段是正文内容。\n\n结尾段保持不变。\n",
        encoding="utf-8",
    )
    final.write_text(
        "# 新标题\n\n第一段保持不变。\n\n第二段也保持不变。\n\n"
        "所以这一段是正文内容。\n\n结尾段保持不变。\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["ai", "learn", "--draft", str(draft), "--final", str(final)])
    assert result.exit_code == 0, result.output
    assert "客套删除" in result.output
    assert "书面词口语化" in result.output

    saved = json.loads((tmp_path / "habits.json").read_text(encoding="utf-8"))
    assert any(h["rule"] == "客套删除" for h in saved)


# ---- notes --db / --from-db（F6 接线）----


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "vault.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (title TEXT, content TEXT, path TEXT, mtime REAL)")
    note_file = tmp_path / "note1.md"
    note_file.write_text("# 我的笔记\n\n" + "正文内容。" * 60, encoding="utf-8")
    con.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?)",
        ("SQLite 笔记", "字" * 250, str(note_file), 1700000000.0),
    )
    con.commit()
    con.close()
    return db


def test_notes_list_from_db(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = CliRunner().invoke(cli, ["notes", "list", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "SQLite 笔记" in result.output


def test_notes_draft_from_db_scaffolds_article(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = tmp_path / "article.md"
    result = CliRunner().invoke(
        cli,
        ["notes", "draft", "--from-db", "1", "--db", str(db), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "title:" in text and "我的笔记" in text
    assert "cover:" in text  # scaffold 补齐了 musespiral 所需 frontmatter


def test_notes_list_requires_root_or_db() -> None:
    result = CliRunner().invoke(cli, ["notes", "list"])
    assert result.exit_code == 1


# ---- 复核修复回归 ----


# 问题 1：mistune 3 image(text, url, title) 签名 —— 含 ![](...) 的文章此前直接
# TypeError 崩溃，F3 图片检查在真实链路不可达。


def test_parse_inline_image_reaches_render_checks(tmp_path: Path) -> None:
    """含图片文章经真实 parse→render 链路命中 F3 图片检查（外域 WARN/本地 PASS）。"""
    from musespiral.core.content.parser import parse_article
    from musespiral.core.quality.render_checks import render_checks
    from musespiral.core.content.renderer import Renderer

    article = tmp_path / "img.md"
    article.write_text(
        "---\ntitle: 带图文章\n---\n\n"
        "![外域图](https://cdn.example.com/pic.png)\n\n![本地图](local.png)\n",
        encoding="utf-8",
    )
    parsed = parse_article(article)  # 修复前此处 TypeError
    html = Renderer().render(parsed)
    assert html.count("<img ") == 2
    warns = [msg for lvl, msg in render_checks(html, parsed, article) if lvl == "WARN"]
    assert any("外域" in m and "cdn.example.com" in m for m in warns)


def test_check_render_foreign_image_warns_via_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    article = tmp_path / "raw.md"
    article.write_text(
        "---\ntitle: 外域图\ncover: cover.png\ndigest: d\n---\n\n"
        "![外域图](https://cdn.example.com/pic.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "cover.png").write_bytes((FIXTURES / "cover.png").read_bytes())
    result = runner.invoke(cli, ["check", str(article), "--render"])
    assert result.exit_code == 0  # WARN 不阻断
    assert "外域" in result.output


# 问题 3：check --render 对无效主题应输出 FAIL 报告，而非未捕获堆栈。


def test_check_render_bad_theme_reports_fail_not_traceback(tmp_path: Path) -> None:
    runner = CliRunner()
    article = tmp_path / "badtheme.md"
    article.write_text(
        "---\ntitle: 坏主题\ncover: cover.png\ndigest: d\ntheme: nope\n---\n\n正文。\n",
        encoding="utf-8",
    )
    (tmp_path / "cover.png").write_bytes((FIXTURES / "cover.png").read_bytes())

    rendered = runner.invoke(cli, ["check", str(article), "--render"])
    assert rendered.exit_code == 1
    assert "渲染失败" in rendered.output
    assert "不是已安装主题" in rendered.output  # WARN 与 FAIL 同报
    assert "Traceback" not in rendered.output

    plain = runner.invoke(cli, ["check", str(article)])
    assert plain.exit_code == 0  # 不带 --render 保持原 WARN 行为


# 问题 2：publish/submit 真实 CLI 调用点补齐日志字段（article/title/article_url）。


class _FakeResp:
    """替身 requests.Response，只实现 publisher 用到的 .json()。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _read_log(home: Path) -> list[dict]:
    log = home / "publish_log.jsonl"
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_publish_command_writes_log_with_article_and_title(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    # 编排统一进 platforms.wechat.publish_flow 后，patch 目标同步到其引用处（low#9 修复）
    monkeypatch.setattr("musespiral.platforms.wechat.get_access_token", lambda account=None: "TOK")

    class _FakeUploader:
        def __init__(self, token, path) -> None:
            pass

        def upload_cover(self, src: str) -> str:
            return "THUMB1"

    monkeypatch.setattr("musespiral.platforms.wechat.ImageUploader", _FakeUploader)
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"media_id": "MEDIA9"}),
    )

    article = _copy_fixture(tmp_path)
    result = CliRunner().invoke(cli, ["publish", str(article)])
    assert result.exit_code == 0, result.output

    records = _read_log(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "draft"
    assert r["article"] == str(article)  # 真实调用点带上 article，不再恒空
    assert r["title"] == "示例文章：渲染级验收冒烟"
    assert r["media_id"] == "MEDIA9"
    assert r["account"] == "default"


def test_publish_command_marks_reviewed_without_warn(tmp_path: Path, monkeypatch) -> None:
    """C1（第十四期）：publish 建草稿成功 draft→reviewed，不再出状态置位 WARN。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("musespiral.platforms.wechat.get_access_token", lambda account=None: "TOK")

    class _FakeUploader:
        def __init__(self, token, path) -> None:
            pass

        def upload_cover(self, src: str) -> str:
            return "THUMB1"

    monkeypatch.setattr("musespiral.platforms.wechat.ImageUploader", _FakeUploader)
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"media_id": "MEDIA10"}),
    )

    article = _copy_fixture(tmp_path)  # 样例无 status → draft
    result = CliRunner().invoke(cli, ["publish", str(article)])
    assert result.exit_code == 0, result.output
    assert "草稿创建成功" in result.output
    assert "状态置位失败" not in result.output  # 原 mark_published 恒 WARN 的回归锚

    from musespiral.core.article import read_status

    assert read_status(article) == "reviewed"  # publish 语义=建草稿：draft→reviewed


def test_submit_command_writes_log_with_article_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("musespiral.infra.wechat.token.get_access_token", lambda *a, **k: "TOK")

    def fake_post(url, **kwargs):
        if "freepublish/submit" in url:
            return _FakeResp({"errcode": 0, "publish_id": "PUB1"})
        if "freepublish/get" in url:
            return _FakeResp(
                {
                    "publish_status": 0,
                    "article_detail": {"item": [{"article_url": "https://mp.weixin.qq.com/s/pub1"}]},
                }
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("musespiral.infra.wechat.publisher.requests.post", fake_post)

    article = _copy_fixture(tmp_path)
    result = CliRunner().invoke(cli, ["submit", str(article), "--draft-id", "MEDIA9"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "https://mp.weixin.qq.com/s/pub1" in result.output

    records = _read_log(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "publish"
    assert r["article"] == str(article)
    assert r["title"] == "示例文章：渲染级验收冒烟"
    assert r["publish_id"] == "PUB1"
    assert r["article_url"] == "https://mp.weixin.qq.com/s/pub1"  # 轮询取得后回填


def test_set_publish_url_updates_only_matching_record(tmp_path: Path) -> None:
    from musespiral.infra.storage.jsonl import append_log, set_publish_url

    log = tmp_path / "publish_log.jsonl"
    append_log(log, {"ts": "t1", "action": "draft", "article": "a.md", "media_id": "M"})
    append_log(log, {"ts": "t2", "action": "publish", "article": "a.md", "publish_id": "PUB1", "article_url": ""})

    assert set_publish_url(log, "PUB1", "https://mp.weixin.qq.com/s/x") is True
    records = _read_log(tmp_path)
    assert records[0]["action"] == "draft"  # 其他行不动
    assert records[1]["article_url"] == "https://mp.weixin.qq.com/s/x"

    assert set_publish_url(log, "NOPE", "https://x") is False
    assert set_publish_url(tmp_path / "missing.jsonl", "PUB1", "https://x") is False


# 问题 4：cols 列名写错必须给可读错误，而非被 SQLite 字符串字面量兼容吞掉。


def test_notes_wrong_column_map_raises_readable_error(tmp_path: Path) -> None:
    from musespiral.infra.sources.sqlite import list_from_db

    db = _make_db(tmp_path)
    with pytest.raises(ValueError, match="缺少列.*conent"):
        list_from_db(
            db,
            cols={"title": "title", "content": "conent", "path": "path", "mtime": "mtime"},
        )


# ---- 第四期接线：review / ai check-facts / digest（§十三 F8/F9/F10）----


def test_review_command_writes_default_and_custom_output(tmp_path: Path) -> None:
    """review 生成默认 <article>.review.html 并打印路径；-o 自定义输出。"""
    runner = CliRunner()
    article = _copy_fixture(tmp_path)
    note = _copy_source_note(tmp_path)

    result = runner.invoke(cli, ["review", str(article), "--note", str(note)])
    assert result.exit_code == 0, result.output
    default_out = tmp_path / "sample_article.review.html"
    assert default_out.is_file()
    assert str(default_out) in result.output  # 打印产出路径
    page = default_out.read_text(encoding="utf-8")
    assert "段落统计：保留 4 · 修改 1 · 新增 4 · 删除 1" in page  # fixture 构造的四类段落
    assert "大家好，这一段客套话只在原笔记里，改写后已删除。" in page  # 删除段留在左列

    custom = tmp_path / "rev.html"
    again = runner.invoke(cli, ["review", str(article), "--note", str(note), "-o", str(custom)])
    assert again.exit_code == 0, again.output
    assert custom.is_file()


def test_review_requires_note_option(tmp_path: Path) -> None:
    """--note 必填：缺省时 click 直接拒绝（退出码 2）。"""
    article = _copy_fixture(tmp_path)
    result = CliRunner().invoke(cli, ["review", str(article)])
    assert result.exit_code == 2


def _fake_ai(tmp_path: Path, monkeypatch, content: str | None = None) -> None:
    """配置假 AI（ai.json）；content 非 None 时一并 monkeypatch factcheck 的 post。

    content == "__raise__" 表示让 post 抛网络异常（走 FactcheckUnavailable 分支）。
    """
    import json as _json

    import requests

    from musespiral.core.quality import factcheck

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        _json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}), encoding="utf-8"
    )
    if content is None:
        return

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {"choices": [{"message": {"content": content}}]}

    def fake_post(*a, **k):
        if content == "__raise__":
            raise requests.RequestException("network down")
        return _Resp()

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(fake_post, u, h, b, to))


def _fact_pair(tmp_path: Path) -> tuple[Path, Path]:
    note = tmp_path / "n.md"
    note.write_text("今天的笔记：缓存命中率 92%。", encoding="utf-8")
    article = tmp_path / "a.md"
    article.write_text(
        "---\ntitle: 缓存实战\ncover: cover.png\ndigest: d\n---\n\n缓存是性能的第一道防线。\n\n据说命中率高达 99%。",
        encoding="utf-8",
    )
    return note, article


def test_ai_check_facts_not_configured_exits_1(tmp_path: Path, monkeypatch) -> None:
    """AI 未配置 → 明确提示配置命令并以退出码 1 结束（不静默跳过）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json
    note, article = _fact_pair(tmp_path)

    result = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert result.exit_code == 1
    assert "musespiral ai config" in result.output


def test_ai_check_facts_unavailable_degrades_to_exit_0(tmp_path: Path, monkeypatch) -> None:
    """网络失败等不可用情形 → 降级提示「自检不可用」，退出码 0 不崩溃。"""
    _fake_ai(tmp_path, monkeypatch, content="__raise__")
    note, article = _fact_pair(tmp_path)

    result = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert result.exit_code == 0
    assert "自检不可用" in result.output


def test_ai_check_facts_prints_incremental_claims(tmp_path: Path, monkeypatch) -> None:
    """AI 返回清单 → 逐条打印 claim 与段落定位；空清单给出通过提示。"""
    _fake_ai(tmp_path, monkeypatch, content='[{"claim": "命中率高达 99%", "where": "第二段"}]')
    note, article = _fact_pair(tmp_path)

    result = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert result.exit_code == 0, result.output
    assert "1 条增量事实" in result.output
    assert "命中率高达 99%" in result.output
    assert "第二段" in result.output


def test_digest_command_lists_heuristic_candidates_and_applies(tmp_path: Path, monkeypatch) -> None:
    """AI 未配置 → 启发式两条编号列出；--apply 1 只改 digest 键，正文与其余键不动。"""
    import re as _re

    import frontmatter as _fm

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json → 启发式
    article = _copy_fixture(tmp_path)
    before = _fm.load(article)

    listed = CliRunner().invoke(cli, ["digest", str(article)])
    assert listed.exit_code == 0, listed.output
    numbered = [_re.match(r"^\s*(\d+)\. (.+)$", l) for l in listed.output.splitlines()]
    numbered = [m for m in numbered if m]
    assert len(numbered) == 2  # 启发式固定两条
    assert all(len(m.group(2)) <= 54 for m in numbered)  # 各条 ≤54 字

    picked = CliRunner().invoke(cli, ["digest", str(article), "--apply", "1"])
    assert picked.exit_code == 0, picked.output
    after = _fm.load(article)
    assert after.metadata["digest"] == numbered[0].group(2)  # 第 1 条写回 digest
    assert after.content == before.content  # 正文不动
    assert after.metadata["title"] == before.metadata["title"]  # 其余键不动
    assert after.metadata["cover"] == before.metadata["cover"]


def test_digest_apply_out_of_range_exits_1(tmp_path: Path, monkeypatch) -> None:
    """启发式只有 2 条时 --apply 3 超出范围 → 退出码 1。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    article = _copy_fixture(tmp_path)

    result = CliRunner().invoke(cli, ["digest", str(article), "--apply", "3"])
    assert result.exit_code == 1
    assert "超出范围" in result.output


# ---- notes draft --from-db 源文件缺失兜底（§十三 F11e 接线）----


def test_notes_draft_from_db_falls_back_to_library_content(tmp_path: Path) -> None:
    """--from-db 源文件缺失 → 用库内 content 正文 scaffold，提示「已用库内正文」。"""
    db = tmp_path / "vault.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (title, content TEXT, path TEXT, mtime REAL)".replace("notes (title,", "notes (title TEXT,"))
    con.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?)",
        ("缺失源", "# 缺失源的笔记\n\n" + "库内正文内容。" * 40, str(tmp_path / "gone.md"), 1700000000.0),
    )
    con.commit()
    con.close()

    out = tmp_path / "article.md"
    result = CliRunner().invoke(cli, ["notes", "draft", "--from-db", "1", "--db", str(db), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "已用库内正文" in result.output
    text = out.read_text(encoding="utf-8")
    assert "缺失源的笔记" in text  # 标题取库内正文一级标题
    assert "库内正文内容。" in text  # 正文来自 content 列


# ---- 第五期接线：adapt → pack --variant 两步链路（§十四 F14/F15）----


def test_adapt_then_pack_variant_chain(tmp_path: Path, monkeypatch) -> None:
    """adapt（monkeypatch AI，不真实联网）→ pack --variant 两步链路产出四件套。

    adapt 产物作 --variant 传入 pack：payload 取其内容，checks 用 profiles
    注入的 xhs 限制（标题 ≤20 字 / 正文 ≤1000 字 / 话题 ≤10 个）。
    """
    import json as _json

    from musespiral.core import adaptation as _adapt

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        _json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": "先压测再优化 ✅\n\n你有不同做法吗？\n\n话题：#缓存 #性能"}}
                ]
            }

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(_Resp(), u, h, b, to))

    article = _copy_fixture(tmp_path)
    # F33B：xhs 物料封面按平台规格核对——fixture cover.png 是 mp 规格
    # （900×383），本链路要「全项 ok」，先把封面换成 xhs 规格（1242×1656）
    from PIL import Image as _PILImage

    _PILImage.new("RGB", (1242, 1656), "#eeeeee").save(tmp_path / "cover.png")
    runner = CliRunner()

    adapted = runner.invoke(cli, ["adapt", str(article), "--platform", "xhs"])
    assert adapted.exit_code == 0, adapted.output
    variant = tmp_path / "sample_article.xhs.md"
    assert variant.is_file()
    assert "人审后才可进入 pack / publish" in adapted.output  # 人审闸门提醒

    packed = runner.invoke(
        cli, ["pack", str(article), "--platform", "xhs", "--variant", str(variant)]
    )
    assert packed.exit_code == 0, packed.output
    pack_dir = tmp_path / "sample_article.xhs.pack"
    for name in ("README.md", "payload.md", "checks.txt"):
        assert (pack_dir / name).is_file()
    assert (pack_dir / "cover" / "cover.png").is_file()  # fixture 封面复制进物料包

    payload = (pack_dir / "payload.md").read_text(encoding="utf-8")
    assert "variant sample_article.xhs.md" in payload  # 内容来源标记 variant
    assert "先压测再优化" in payload  # 正文取 adapt 产物而非原文
    assert "#缓存" in payload  # xhs 话题行解析进 tags 后回显
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "ok 标题字数" in checks and "ok 正文字数" in checks and "ok 标签数" in checks
    assert "FAIL" not in checks  # profiles 注入限制后逐项可校验且全过


def test_pack_rejects_missing_variant_and_enforces_title_min(tmp_path: Path) -> None:
    """--variant 路径不存在 → 快败 exit 1；channels 标题下限经 profiles→CLI 注入 checks。

    （复核修复升级：拼错的 variant 不产出「以为是口播稿实为原文」的误导性物料包，
    与 check 门 FAIL 即退出的哲学一致；标题 2 字低于视频号 3 字下限标 FAIL。）
    """
    article = tmp_path / "shorty.md"
    article.write_text(
        "---\ntitle: 短题\ncover: cover.png\n---\n\n正文内容。\n",
        encoding="utf-8",
    )
    (tmp_path / "cover.png").write_bytes((FIXTURES / "cover.png").read_bytes())

    # 快败路径：拼错的 --variant 直接 exit 1，不产出物料包
    result = CliRunner().invoke(
        cli,
        ["pack", str(article), "--platform", "channels", "--variant", str(tmp_path / "typo.md")],
    )
    assert result.exit_code == 1, result.output
    assert "拒绝静默回退" in result.output
    assert not (tmp_path / "shorty.channels.pack").exists()

    # 不带 variant 正常打包：下限校验经 profiles→CLI 注入生效
    ok_result = CliRunner().invoke(
        cli,
        ["pack", str(article), "--platform", "channels", "-o", str(tmp_path / "ok.pack")],
    )
    assert ok_result.exit_code == 0, ok_result.output
    checks = (tmp_path / "ok.pack" / "checks.txt").read_text(encoding="utf-8")
    assert "ok 标题字数: 2/16" in checks  # channels title_max_chars=16 注入
    assert "FAIL 标题最短字数: 2/3（低于下限）" in checks  # title_min_chars=3 注入生效


# ---- 遗留批修复（第四/五期 low）的 CLI 行为锚点 ----


def test_platform_list_outputs_capability_matrix() -> None:
    """musespiral platform list：能力矩阵表 exit 0 且含各平台行与通道档（low#10 补测试）。"""
    result = CliRunner().invoke(cli, ["platform", "list"])
    assert result.exit_code == 0, result.output
    assert "视频号" in result.output and "channels" in result.output
    assert "物料包" in result.output and "官方API" in result.output  # 通道档显示名


def test_platform_list_f42_six_manual_pack_platforms() -> None:
    """F42：platform list 覆盖六个 manual_pack 平台（xhs/channels/douyin/zhihu
    + juejin/bilibili，§五 验收「platform list 六平台」——六指 manual_pack 集，
    mp 走 official_api 另计一行）。"""
    from musespiral.platforms import registry

    result = CliRunner().invoke(cli, ["platform", "list"])
    assert result.exit_code == 0, result.output
    for name in ("掘金", "juejin", "B站专栏", "bilibili"):
        assert name in result.output, f"platform list 缺 {name}"
    # 通道与限制摘要列：两新平台均为物料包通道、标题字限摘要在场
    rows = result.output.splitlines()
    for code, limit in (("juejin", "标题≤100字"), ("bilibili", "标题≤80字")):
        row = next(ln for ln in rows if code in ln and "物料包" in ln)
        assert limit in row
    manual_pack = {a.name for a in registry.list_platforms() if a.route == "manual_pack"}
    assert manual_pack == {"xhs", "channels", "douyin", "zhihu", "juejin", "bilibili"}


def test_adapt_channels_without_ai_exits_1(tmp_path: Path, monkeypatch) -> None:
    """无 AI 配置时 channels 口播稿无启发式降级 → exit 1 且提示配置（low#10）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json
    article = _copy_fixture(tmp_path)
    result = CliRunner().invoke(
        cli, ["adapt", str(article), "--platform", "channels", "-o", str(tmp_path / "o.md")]
    )
    assert result.exit_code == 1, result.output
    assert "需先配置 AI" in result.output


def test_stats_grouped_recent_per_platform(tmp_path: Path, monkeypatch) -> None:
    """stats 按平台分组含每平台最近条目展示（low#8 修复）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", tmp_path / "no-root"
    )
    from musespiral.infra.storage.jsonl import append_log, default_log_path

    log = default_log_path()
    append_log(log, {"ts": "2026-11-12T10:00:00", "action": "draft", "title": "公众号篇", "platform": "mp"})
    append_log(log, {"ts": "2026-11-12T11:00:00", "action": "publish", "title": "视频号篇", "platform": "channels"})

    result = CliRunner().invoke(cli, ["stats"])
    assert result.exit_code == 0, result.output
    assert "按平台" in result.output
    assert "微信公众号（mp）" in result.output and "视频号（channels）" in result.output
    assert "视频号篇" in result.output  # 分组 recent 条目已展示


def test_publish_bad_theme_plus_failing_checks_outputs_report_not_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    """坏主题 + 检查不过叠加：先给检查报告 + 干净渲染错误退出，而非堆栈（low#2）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    article = tmp_path / "bad.md"
    article.write_text(
        "---\ntitle: 坏主题缺封面\ntheme: nosuch\n---\n\n正文。\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["publish", str(article)])
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "封面" in result.output  # frontmatter 级检查报告先出
    assert "渲染失败" in result.output


# ---- 第七期接线（§十六 F16/F17/F18：upload 组 / stats --sync / theme learn）----


# 预生成物料包 fixture（build_pack 真实产物；dry-run 与冒烟共用）
DEMO_CHANNELS_PACK = FIXTURES / "studio_demo" / "03_photo_essay.channels.pack"
WX_SAMPLE_HTML = FIXTURES / "wx_article_sample.html"


def test_upload_publish_dry_run_prints_step_list(tmp_path: Path, monkeypatch) -> None:
    """upload publish --dry-run：exit 0，打印步骤清单（入口/字段/选择器/人工发布项）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    result = CliRunner().invoke(
        cli, ["upload", "publish", "--platform", "channels", "--pack", str(DEMO_CHANNELS_PACK), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "视频号（channels）dry-run 步骤清单" in result.output
    assert "https://channels.weixin.qq.com/platform/post/create" in result.output  # 入口
    assert "Studio 演示：海边纯图文笔记" in result.output  # payload 标题填写值
    assert "cover.png" in result.output  # cover/ 封面
    assert "【人工】" in result.output and "永不点击" in result.output  # 红线①文案
    assert "选择器校验通过" in result.output


def test_upload_publish_real_mode_without_cookie_prompts_login(
    tmp_path: Path, monkeypatch
) -> None:
    """真实模式缺 cookie：确认后在起浏览器前快败，可读提示先 login（exit 1）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 cookies/channels.json
    result = CliRunner().invoke(
        cli,
        ["upload", "publish", "--platform", "channels", "--pack", str(DEMO_CHANNELS_PACK)],
        input="y\n",
    )
    assert result.exit_code == 1, result.output
    assert "musespiral upload login --platform channels" in result.output


def test_upload_login_without_playwright_prints_install_hint(
    tmp_path: Path, monkeypatch
) -> None:
    """upload login 缺 playwright：BrowserNotInstalled 可读提示（pip extra + 镜像）exit 1。

    sys.modules 注入 None 确定性复现缺失（与 test_browser 同法，
    装了 playwright 的机器结论相同）。
    """
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    result = CliRunner().invoke(cli, ["upload", "login", "--platform", "channels"])
    assert result.exit_code == 1, result.output
    assert 'pip install "musespiral[browser]"' in result.output
    assert "playwright install chromium" in result.output


def test_upload_unknown_platform_readable_error(tmp_path: Path, monkeypatch) -> None:
    """未知平台：选择器文件缺失转可读错误（列出可用平台）exit 1。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    result = CliRunner().invoke(
        cli, ["upload", "publish", "--platform", "nosuch", "--pack", str(DEMO_CHANNELS_PACK), "--dry-run"]
    )
    assert result.exit_code == 1, result.output
    assert "不支持的平台" in result.output or "平台选择器文件不存在" in result.output


def test_theme_learn_html_fixture_generates_theme(tmp_path: Path, monkeypatch) -> None:
    """theme learn --html：从 fixture 学习生成主题（已知频次断言）+ list 可见。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    out = tmp_path / "themes"
    result = CliRunner().invoke(
        cli,
        ["theme", "learn", "--html", str(WX_SAMPLE_HTML), "--name", "smoke_theme", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    target = out / "smoke_theme.yaml"
    assert target.is_file()
    import yaml

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["extends"] == "default"
    assert data["colors"] == {"text": "#333333", "primary": "#c2410c"}  # fixture 已知频次
    assert data["typography"] == {"font_size": "15px", "line_height": "1.75"}
    assert "smoke_theme" in CliRunner().invoke(cli, ["theme", "list"]).output


def test_theme_learn_without_source_readable_error(tmp_path: Path, monkeypatch) -> None:
    """既无 url 也无 --html：可读提示 exit 1（不静默猜测来源）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    result = CliRunner().invoke(cli, ["theme", "learn"])
    assert result.exit_code == 1, result.output
    assert "--html" in result.output


def test_theme_learn_url_fetch_failure_readable_error(tmp_path: Path, monkeypatch) -> None:
    """url 拉取失败（网络异常）：可读错误 exit 1，不落主题文件。"""
    import requests as _requests

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))

    def _boom(*a, **k):
        raise _requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.get", _boom)
    result = CliRunner().invoke(
        cli, ["theme", "learn", "https://mp.weixin.qq.com/s/xxx", "-o", str(tmp_path / "themes")]
    )
    assert result.exit_code == 1, result.output
    assert "拉取文章 HTML 失败" in result.output
    assert not (tmp_path / "themes").exists()


def test_theme_learn_url_success_generates_theme(tmp_path: Path, monkeypatch) -> None:
    """url 成功路径（monkeypatch requests.get，不联网）：正常生成主题。"""

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        text = (
            '<section style="color: #333333"><p style="color: #333333">a</p>'
            '<p style="color: #c2410c; font-size: 16px; line-height: 1.6">b</p></section>'
        )

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
    result = CliRunner().invoke(
        cli, ["theme", "learn", "https://mp.weixin.qq.com/s/xxx", "-o", str(tmp_path / "themes")]
    )
    assert result.exit_code == 0, result.output
    assert "来源: https://mp.weixin.qq.com/s/xxx" in result.output
    assert (tmp_path / "themes" / "learned.yaml").is_file()  # --name 缺省 learned


def test_stats_sync_without_credentials_exits_1(tmp_path: Path, monkeypatch) -> None:
    """stats --sync 有发表记录但无凭证：可读凭证提示 exit 1（不静默跳过）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 credentials.json
    monkeypatch.setattr(
        "musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", tmp_path / "no-root"
    )
    log = tmp_path / "publish_log.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-11-10T09:00:00",
                "action": "publish",
                "article": "a.md",
                "title": "甲",
                "article_url": "https://mp.weixin.qq.com/s/x",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["stats", "--sync"])
    assert result.exit_code == 1, result.output
    assert "未配置公众号凭证" in result.output
    assert "musespiral config set" in result.output


def test_stats_sync_backfills_then_prints_reads(tmp_path: Path, monkeypatch) -> None:
    """stats --sync mock 拉取成功：回填行内字段后打印阅读/分享汇总（exit 0）。"""

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {
                "list": [
                    {
                        "ref_date": "2026-11-10",
                        "url": "https://mp.weixin.qq.com/s/x",
                        "details": [
                            {"stat_date": "2026-11-10", "int_page_read_count": 42,
                             "share_count": 4, "like_count": 1},
                        ],
                    }
                ]
            }

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "musespiral.services.discover_service.DEFAULT_ARTICLES_ROOT", tmp_path / "no-root"
    )
    monkeypatch.setattr("musespiral.infra.wechat.analytics.get_access_token", lambda account=None: "TOK")
    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", lambda *a, **k: _Resp())
    log = tmp_path / "publish_log.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-11-10T09:00:00",
                "action": "publish",
                "article": "a.md",
                "title": "甲",
                "article_url": "https://mp.weixin.qq.com/s/x",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["stats", "--sync"])
    assert result.exit_code == 0, result.output
    assert "数据回填: 候选 1 条" in result.output
    assert "阅读 42" in result.output and "分享 4" in result.output  # 汇总含阅读/分享
    # 日志行内已回填 reads/shares/likes（其余字段不动）
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["reads"] == 42 and row["shares"] == 4 and row["likes"] == 1
    assert row["title"] == "甲"
