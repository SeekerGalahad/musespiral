"""adapt（一稿多态 F14）单元测试——AI 路径全程 monkeypatch requests.post，不真实联网。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import frontmatter
import pytest

from musespiral.core import adaptation as adapt
from musespiral.infra.ai import client as ai_mod
from musespiral.core.article import body_sha1


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _write_article(tmp_path: Path) -> Path:
    p = tmp_path / "article.md"
    p.write_text(
        "---\ntitle: 缓存优化实录\ntags: [性能, 缓存]\n---\n\n"
        "# 缓存优化实录\n\n"
        "缓存是提升系统性能的第一道防线，实测收益明显。\n\n"
        "## 先压测再优化\n\n"
        "不做压测就上缓存，很容易优化了没人用的路径。\n\n"
        "## 过期时间是关键\n\n"
        "过期策略选错，命中率会断崖式下跌。实测 TTL 从 5 分钟调到 30 分钟后命中率翻倍。\n",
        encoding="utf-8",
    )
    return p


def _no_ai(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json → 走降级路径


def _setup_ai(tmp_path: Path, monkeypatch, ai_content: str) -> dict:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(ai_content)

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(fake_post, u, h, b, to))
    return captured


def _read(out: Path) -> frontmatter.Post:
    return frontmatter.load(out)


def _line_value(body: str, prefix: str) -> str:
    return next(l.split("：", 1)[1].strip() for l in body.splitlines() if l.startswith(prefix))


def test_ai_xhs_variant_shape(tmp_path, monkeypatch) -> None:
    captured = _setup_ai(
        tmp_path,
        monkeypatch,
        "缓存优化真的别乱来 😤\n\n"
        "✅ 先压测再优化\n"
        "💡 过期时间是命中率的生命线\n\n"
        "你有不同做法吗？评论区聊聊 👇\n\n"
        "话题：#缓存 #性能优化",
    )
    src = _write_article(tmp_path)
    out = adapt.generate(src, "xhs")
    assert out == tmp_path / "article.xhs.md"
    post = _read(out)
    assert post.metadata["platform"] == "xhs"
    assert post.metadata["tags"] == ["缓存", "性能优化"]
    assert len(post.content) <= 1000
    assert "话题：#缓存 #性能优化" in post.content  # 话题行留在正文便于粘贴
    assert captured["url"] == "http://fake.local/v1/chat/completions"
    assert "小红书" in captured["json"]["messages"][-1]["content"]


def test_ai_channels_variant_shape(tmp_path, monkeypatch) -> None:
    _setup_ai(
        tmp_path,
        monkeypatch,
        "【开场钩子】你的接口为什么慢？可能缓存全配错了。\n"
        "【要点1】先压测，找到真热点再动手。\n"
        "【要点2】过期时间是命中率的生命线。\n"
        "【收尾引导】关注我，下期拆多级缓存。\n"
        "封面标题：缓存别乱配\n"
        "描述：三步搞定缓存优化 #性能优化",
    )
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "channels"))
    assert post.metadata["platform"] == "channels"
    body = post.content
    for marker in ("【开场钩子】", "【要点1】", "【要点2】", "【收尾引导】"):
        assert marker in body
    assert len(_line_value(body, "封面标题：")) <= 14
    assert _line_value(body, "描述：").startswith("三步搞定缓存优化")


def test_ai_douyin_variant_shape(tmp_path, monkeypatch) -> None:
    _setup_ai(
        tmp_path,
        monkeypatch,
        "你的缓存可能全白做了？三个数字告诉你哪里错了。\n\n"
        "字幕要点：\n"
        "- 先压测再优化\n"
        "- TTL 决定命中率\n"
        "- 多级缓存要分层\n\n"
        "标题：缓存三步法 #性能",
    )
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "douyin"))
    assert post.metadata["platform"] == "douyin"
    body = post.content
    assert "字幕要点：" in body
    title = _line_value(body, "标题：")
    assert len(title) <= 20
    assert "#" in title  # 带话题词


def test_ai_zhihu_variant_shape(tmp_path, monkeypatch) -> None:
    _setup_ai(
        tmp_path,
        monkeypatch,
        "先说结论：缓存优化的收益上限由压测数据决定。\n\n"
        "## 为什么先压测\n\n"
        "不做压测就上缓存，等于优化了没人走的路径。实测 70% 的收益来自 30% 的热点键。\n\n"
        "## 过期策略的隐藏成本\n\n"
        "TTL 选错的代价是命中率断崖式下跌；从 5 分钟调到 30 分钟后，命中率翻倍。\n\n"
        "讨论：你的系统里 TTL 是怎么定的，踩过命中率下跌的坑吗？",
    )
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "zhihu"))
    assert post.metadata["platform"] == "zhihu"
    body = post.content.strip()
    assert "讨论：" in body
    assert body.endswith("？")  # 结尾留讨论问题
    assert "点击关注" not in body  # 去公众号话术


def test_ai_juejin_variant_shape(tmp_path, monkeypatch) -> None:
    """F42 掘金：技术长文轻改——导语点题 + 代码块/术语原样保留 + 无公众号话术。"""
    _setup_ai(
        tmp_path,
        monkeypatch,
        "导语：缓存优化别乱来，先压测再动手。\n\n"
        "## 先压测再优化\n\n"
        "不做压测就上缓存，等于优化了没人走的路径。\n\n"
        "```python\n"
        "cache = TTLCache(maxsize=128, ttl=1800)\n"
        "```\n\n"
        "## 过期时间是关键\n\n"
        "TTL 从 5 分钟调到 30 分钟后，命中率翻倍。",
    )
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "juejin"))
    assert post.metadata["platform"] == "juejin"
    body = post.content.strip()
    assert body.startswith("导语：")
    assert "## 先压测再优化" in body  # 小节结构保留
    assert "```python" in body and "TTLCache(maxsize=128, ttl=1800)" in body  # 代码块逐字保留
    assert "TTL" in body  # 术语不改写
    assert "点击关注" not in body  # 去公众号话术


def test_ai_bilibili_variant_shape(tmp_path, monkeypatch) -> None:
    """F42 B站专栏：亲和化——口语导语 + 分段小标题 + emoji 点缀 + 互动收尾。"""
    _setup_ai(
        tmp_path,
        monkeypatch,
        "聊聊缓存优化～ 你的缓存可能一直白做了 😊\n\n"
        "## 先压测再优化 ✨\n\n"
        "不做压测就上缓存，很容易忙半天没效果。\n\n"
        "## 过期时间是关键 💡\n\n"
        "TTL 调对，命中率直接翻倍，实测有效。\n\n"
        "你有类似的经历吗？评论区聊聊～",
    )
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "bilibili"))
    assert post.metadata["platform"] == "bilibili"
    body = post.content.strip()
    assert body.startswith("聊聊")  # 口语导语
    assert "## 先压测再优化" in body and "## 过期时间是关键" in body  # 分段小标题
    assert any(ch in body for ch in "😊✨💡")  # emoji 有点缀
    assert sum(ch in "😊✨💡🍵🎯🔥✅📌⭐️" for ch in body) <= 4  # 节制：不满篇
    assert "评论区" in body  # 互动引导


def test_xhs_heuristic_without_ai(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    out = adapt.generate(src, "xhs")
    post = _read(out)
    assert post.metadata["platform"] == "xhs"
    assert post.metadata["tags"] == ["性能", "缓存"]  # 复用原 frontmatter tags
    body = post.content
    assert len(body) <= 1000
    assert "话题：#性能 #缓存" in body
    assert "先压测再优化：" in body and "过期时间是关键：" in body  # 小标题组要点
    assert "评论区" in body  # 互动引导


def test_zhihu_heuristic_without_ai(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "zhihu"))
    assert post.metadata["platform"] == "zhihu"
    body = post.content.strip()
    assert "先说结论：" in body
    assert "## 先压测再优化" in body  # 小标题组拼装
    assert "讨论：" in body and body.endswith("？")


def test_juejin_heuristic_without_ai_keeps_code_blocks(tmp_path, monkeypatch) -> None:
    """F42 掘金降级：代码块逐行原样保留（围栏内话术字样不误伤）、小节保留、
    H1 不重复、围栏外公众号话术行剔除。"""
    _no_ai(tmp_path, monkeypatch)
    p = tmp_path / "tech.md"
    p.write_text(
        "---\ntitle: 缓存优化实录\ntags: [性能]\n---\n\n"
        "# 缓存优化实录\n\n"
        "缓存是提升系统性能的第一道防线，实测收益明显。\n\n"
        "点击关注不迷路。\n\n"
        "## 环境准备\n\n"
        "先装依赖。\n\n"
        "```bash\n"
        "pip install musespiral  # 注释里的「点击关注」在围栏内，必须原样保留\n"
        "```\n\n"
        "## 小结\n\n"
        "过期策略选错，命中率会断崖式下跌。",
        encoding="utf-8",
    )
    post = _read(adapt.generate(p, "juejin"))
    assert post.metadata["platform"] == "juejin"
    body = post.content
    assert body.startswith("导语：缓存是提升系统性能的第一道防线")
    assert "```bash" in body
    assert "pip install musespiral  # 注释里的「点击关注」在围栏内，必须原样保留" in body
    assert "## 环境准备" in body and "## 小结" in body  # 小节结构保留
    lines = [l for l in body.splitlines() if "点击关注" in l]
    assert lines == ["pip install musespiral  # 注释里的「点击关注」在围栏内，必须原样保留"]
    assert not any(l.strip().startswith("# ") for l in body.splitlines())  # H1 不重复


def test_bilibili_heuristic_without_ai(tmp_path, monkeypatch) -> None:
    """F42 B站降级：口语导语 + 小标题组要点 + emoji 隔段节制 + 互动收尾。"""
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)  # 两节：先压测再优化 / 过期时间是关键
    post = _read(adapt.generate(src, "bilibili"))
    assert post.metadata["platform"] == "bilibili"
    body = post.content.strip()
    assert body.startswith("聊聊缓存优化实录～")  # 口语导语（title 入导语）
    assert "## 先压测再优化" in body and "## 过期时间是关键" in body
    assert body.endswith("你有类似的经历吗？评论区聊聊～")
    emoji_n = sum(ch in "😊✨💡🍵🎯" for ch in body)
    assert 1 <= emoji_n <= 3  # 适度：导语 1 + 隔段小标题，不满篇


def test_channels_without_ai_raises(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    with pytest.raises(adapt.AdaptNeedsAI):
        adapt.generate(src, "channels")


def test_douyin_without_ai_raises(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    with pytest.raises(adapt.AdaptNeedsAI):
        adapt.generate(src, "douyin")


def test_habits_injected_into_messages(tmp_path, monkeypatch) -> None:
    captured = _setup_ai(tmp_path, monkeypatch, "任意笔记内容 😊\n\n话题：#测试")
    (tmp_path / "habits.json").write_text(
        json.dumps([{"rule": "客套删除", "example": "不要开场客套，直接进正题"}], ensure_ascii=False),
        encoding="utf-8",
    )
    src = _write_article(tmp_path)
    adapt.generate(src, "xhs")
    messages = captured["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "用户改稿习惯" in messages[0]["content"]
    assert "客套删除" in messages[0]["content"]
    assert messages[-1]["role"] == "user"


def test_refuse_overwrite_existing_output(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    out = adapt.generate(src, "xhs")
    with pytest.raises(FileExistsError):
        adapt.generate(src, "xhs")
    assert out.exists()


def test_explicit_out_path(tmp_path, monkeypatch) -> None:
    _setup_ai(tmp_path, monkeypatch, "短视频口播\n\n【开场钩子】钩子\n【要点1】一\n【要点2】二\n【收尾引导】关注\n封面标题：口播稿\n描述：描述 #话题")
    src = _write_article(tmp_path)
    dst = tmp_path / "sub" / "note.md"
    out = adapt.generate(src, "channels", out_path=dst)
    assert out == dst
    assert dst.exists()


def test_unknown_platform_rejected(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    with pytest.raises(ValueError):
        adapt.generate(src, "twitter")


def test_heuristic_xhs_char_budget_holds_with_long_tags(tmp_path, monkeypatch) -> None:
    """长文 + 长标签复现预算击穿：要点按实际拼接长度截停、标签装不下即弃，全文 ≤1000。

    修复前话题行只截条数（≤6）不截长度且预算固定 40 字，本用例形态实测超到 1063 字。
    """
    _no_ai(tmp_path, monkeypatch)
    sections = "\n\n".join(f"## 第{i}节\n\n{'字' * 79}。" for i in range(15))
    long_tags = [f"超长话题标签编号{i}" for i in range(6)]  # 每个 9 字
    p = tmp_path / "long.md"
    p.write_text(
        "---\ntitle: 长文\ntags: [" + ", ".join(long_tags) + "]\n---\n\n"
        "开头导语一句。\n\n" + sections,
        encoding="utf-8",
    )

    body = _read(adapt.generate(p, "xhs")).content

    assert len(body) <= 1000  # 修复前 1063
    assert "评论区" in body  # 互动句必留
    assert "第0节：" in body  # 要点自上而下保留
    topic = next((l for l in body.splitlines() if l.startswith("话题：")), "")
    assert topic.count("#") <= 6  # 话题行只保留装得下的标签（可为 0 个则整行不出现）


def test_heuristic_xhs_keeps_all_tags_when_budget_allows(tmp_path, monkeypatch) -> None:
    """短文预算充足时 6 个长标签全部保留——按长度截断只在必要时发生。"""
    _no_ai(tmp_path, monkeypatch)
    long_tags = [f"超长话题标签编号{i}" for i in range(6)]
    p = tmp_path / "short.md"
    p.write_text(
        "---\ntitle: 短文\ntags: [" + ", ".join(long_tags) + "]\n---\n\n导语句。\n\n## 唯一一节\n\n要点句。",
        encoding="utf-8",
    )

    body = _read(adapt.generate(p, "xhs")).content

    assert len(body) <= 1000
    assert body.splitlines()[-1] == "话题：" + " ".join(f"#{t}" for t in long_tags)


def test_generate_channels_local_validation_warnings(tmp_path, monkeypatch) -> None:
    """第五期遗留 low#6：AI 产物超出口播字数区间 → adapt_warnings 写入 frontmatter。"""
    long_script = (
        "【开场钩子】" + "字" * 300 + "\n【要点1】x\n【收尾引导】y\n封面标题：好标题\n描述：一段描述 #话题"
    )
    _setup_ai(tmp_path, monkeypatch, long_script)
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "channels"))
    warns = post.metadata.get("adapt_warnings") or []
    assert any("口播稿" in w and "超出" in w for w in warns)


# ---- R3 产物回链（source / source_sha1）----


def test_generate_writes_source_backlink(tmp_path, monkeypatch) -> None:
    """R3 回链：产物 frontmatter 记 source（源文件名）与 source_sha1（源正文哈希前 12 位）。

    AI 路径（启发式路径共用同一构造点，见下一条用例覆盖）。
    """
    _setup_ai(tmp_path, monkeypatch, "先说结论：回链写入。\n\n## 一\n\n要点。\n\n讨论：够吗？")
    src = _write_article(tmp_path)
    post = _read(adapt.generate(src, "zhihu"))
    assert post.metadata["source"] == "article.md"
    assert post.metadata["source_sha1"] == body_sha1(frontmatter.load(src).content)
    assert re.fullmatch(r"[0-9a-f]{12}", post.metadata["source_sha1"])


def test_backlink_hit_after_source_rename_heuristic(tmp_path, monkeypatch) -> None:
    """源改名后回链仍命中：产物 source_sha1 与改名后的源正文哈希保持一致
    （探测端按产物 source 字段回溯源文件，stem 约定降级为兜底——正典 §四-R3）。"""
    _no_ai(tmp_path, monkeypatch)
    src = _write_article(tmp_path)
    out = adapt.generate(src, "xhs")
    renamed = src.with_name("renamed.md")
    src.rename(renamed)
    post = _read(out)
    assert post.metadata["source"] == "article.md"  # 记的是生成时的源文件名
    assert post.metadata["source_sha1"] == body_sha1(frontmatter.load(renamed).content)
