"""F15 物料包 — build_pack 四件套生成与限制校验。

全部用 tmp_path 写临时文件，不触碰 ~/.musespiral 真实目录；无网络与 AI 调用
（limits 注入，不 import platforms）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from musespiral.services.pack_service import build_pack


def _write_article(tmp_path: Path, *, cover: str = "cover.png", cover_exists: bool = True) -> Path:
    """写一篇带 frontmatter 的测试文章，返回其路径。"""
    article = tmp_path / "demo.md"
    article.write_text(
        "---\n"
        "title: 一篇足够长的测试标题\n"
        "cover: " + cover + "\n"
        "tags: [AI, 写作]\n"
        "digest: 这是摘要。\n"
        "---\n"
        "\n"
        "这是文章正文第一段。\n"
        "\n"
        "这是文章正文第二段。\n",
        encoding="utf-8",
    )
    if cover and cover_exists:
        (tmp_path / cover).write_bytes(b"fake-cover-bytes")
    return article


# ── 四件套齐全 + README 字段表 ─────────────────────────────


def test_channels_pack_full_set(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    limits = {"title_max": 16, "content_max": 1000, "tags_max": 6}

    pack_dir = build_pack(article, "channels", limits=limits)

    assert pack_dir == tmp_path / "demo.channels.pack"
    assert (pack_dir / "README.md").exists()
    assert (pack_dir / "payload.md").exists()
    assert (pack_dir / "checks.txt").exists()
    copied = pack_dir / "cover" / "cover.png"
    assert copied.exists()
    assert copied.read_bytes() == b"fake-cover-bytes"

    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "ok 标题字数" in checks
    assert "ok 正文字数" in checks
    assert "ok 标签数" in checks
    assert "FAIL" not in checks


def test_readme_has_field_table_for_all_platforms(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    for platform in ("channels", "xhs", "zhihu", "douyin"):
        pack_dir = build_pack(article, platform)
        readme = (pack_dir / "README.md").read_text(encoding="utf-8")
        assert "字段对应表" in readme
        for field in ("标题", "描述", "话题"):
            assert field in readme
        assert "https://" in readme  # 入口 URL


# ── payload：variant 优先 / 回退原文 / 逐字段对照 ───────────


def test_variant_content_preferred(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    variant = tmp_path / "demo.xhs.md"
    variant.write_text(
        "---\n"
        "platform: xhs\n"
        "title: 变体短标题\n"
        "tags: [小红书, 笔记]\n"
        "---\n"
        "这是变体正文，口语化带 emoji。\n",
        encoding="utf-8",
    )

    pack_dir = build_pack(article, "xhs", variant=variant)
    payload = (pack_dir / "payload.md").read_text(encoding="utf-8")

    assert "这是变体正文，口语化带 emoji。" in payload
    assert "这是文章正文第一段。" not in payload
    assert "标题：变体短标题" in payload  # 标题优先取 variant frontmatter
    assert "话题：#小红书 #笔记" in payload
    assert "variant demo.xhs.md" in payload  # 内容来源可追溯


def test_variant_missing_rejected(tmp_path: Path) -> None:
    # 语义修正（第五期复核 medium#2）：显式传入的 --variant 不存在是输入错误，
    # 拒绝静默回退原文——拼错路径必须立刻暴露，而不是产出用原稿的物料包
    article = _write_article(tmp_path)

    with pytest.raises(FileNotFoundError, match="拒绝静默回退"):
        build_pack(article, "xhs", variant=tmp_path / "nope.xhs.md")


def test_limits_profiles_keys_normalized(tmp_path: Path) -> None:
    # 第五期复核集成断点：CLI 注入的是 platforms.profiles.get_limits 原键
    # （*_chars 后缀/desc_*），pack 侧自动归一后校验必须真实生效
    article = _write_article(tmp_path)

    pack_dir = build_pack(
        article,
        "channels",
        limits={"title_max_chars": 5, "title_min_chars": 3, "desc_max_chars": 10},
    )
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")

    assert "未接入" not in checks
    assert "FAIL 标题字数" in checks  # 标题 11 字 > 5，经 *_chars 归一后真实生效
    assert "FAIL 正文字数" in checks  # 正文约 20 字 > 10（desc_max_chars → content_max）
    assert "ok 标题最短字数" in checks  # 11 ≥ 3 下限通过



def test_payload_digest_falls_back_to_first_prose_line(tmp_path: Path) -> None:
    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 无摘要文章\ncover: c.png\n---\n\n第一段做描述。\n\n第二段。\n",
        encoding="utf-8",
    )
    (tmp_path / "c.png").write_bytes(b"x")

    pack_dir = build_pack(article, "zhihu")
    payload = (pack_dir / "payload.md").read_text(encoding="utf-8")
    assert "描述：第一段做描述。" in payload


# ── checks.txt：注入 limits 与未接入 ────────────────────────


def test_checks_overlimit_title_marks_fail(tmp_path: Path) -> None:
    article = _write_article(tmp_path)  # 标题 10 字

    pack_dir = build_pack(
        article, "channels", limits={"title_max": 5, "content_max": 1000}
    )
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8").splitlines()

    assert checks == ["FAIL 标题字数: 10/5（超限）", "ok 正文字数: 22/1000"]
    # limits 未含 tags_max：标签项跳过，不出现未接入提示


def test_checks_without_limits_reports_not_wired(tmp_path: Path) -> None:
    article = _write_article(tmp_path)

    pack_dir = build_pack(article, "douyin", limits=None)
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8").strip()

    assert checks == "未接入平台限制表"


def test_checks_empty_title_marks_required_fail(tmp_path: Path) -> None:
    """标题缺失不再是「ok 标题字数: 0/16」：必填 FAIL 且只报一次。"""
    article = tmp_path / "demo.md"
    article.write_text("---\ncover: cover.png\n---\n\n正文。\n", encoding="utf-8")
    (tmp_path / "cover.png").write_bytes(b"x")

    pack_dir = build_pack(article, "channels", limits={"title_max": 16, "title_min": 3})
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8").splitlines()

    assert checks == ["FAIL 标题字数: 0/16（未设置，frontmatter.title 必填）"]


def test_checks_title_below_min_marks_fail(tmp_path: Path) -> None:
    """标题低于平台下限（视频号 3-16 字）标 FAIL，不再只有上限校验。"""
    article = tmp_path / "demo.md"
    article.write_text("---\ntitle: 短题\ncover: cover.png\n---\n\n正文。\n", encoding="utf-8")
    (tmp_path / "cover.png").write_bytes(b"x")

    pack_dir = build_pack(article, "channels", limits={"title_max": 16, "title_min": 3})
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8").splitlines()

    assert checks == ["ok 标题字数: 2/16", "FAIL 标题最短字数: 2/3（低于下限）"]


# ── cover 跳过 / 拒绝覆盖 / 未知平台 ────────────────────────


def test_cover_missing_skipped_without_error(tmp_path: Path) -> None:
    article = _write_article(tmp_path, cover="missing.png", cover_exists=False)

    pack_dir = build_pack(article, "xhs")

    assert not (pack_dir / "cover").exists()
    assert (pack_dir / "README.md").exists()  # 其余三件不受影响


def test_rebuild_refuses_overwrite(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    first = build_pack(article, "channels")
    with pytest.raises(FileExistsError):
        build_pack(article, "channels")
    assert first.exists()  # 原 pack 未被破坏


def test_explicit_outdir_used_and_refused_on_rebuild(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    target = tmp_path / "sub" / "my.pack"
    pack_dir = build_pack(article, "zhihu", outdir=target)
    assert pack_dir == target
    assert (target / "payload.md").exists()
    with pytest.raises(FileExistsError):
        build_pack(article, "zhihu", outdir=target)


def test_unknown_platform_rejected(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    with pytest.raises(ValueError, match="不支持的平台"):
        build_pack(article, "myspace")


def test_variant_desc_line_preferred_for_payload_header(tmp_path: Path) -> None:
    """第五期遗留 low#9：variant 正文「描述：」行优先作为 payload 头部描述。"""
    article = _write_article(tmp_path)
    variant = tmp_path / "demo.channels.md"
    variant.write_text(
        "---\ntitle: 口播稿\nplatform: channels\n---\n\n"
        "【开场钩子】hi\n【收尾引导】bye\n\n"
        "封面标题：三步缓存\n\n"
        "描述：三步搞定缓存优化 #性能优化\n",
        encoding="utf-8",
    )

    pack_dir = build_pack(article, "channels", variant=variant)
    payload = (pack_dir / "payload.md").read_text(encoding="utf-8")
    assert "三步搞定缓存优化" in payload.splitlines()[0:6].__str__() or any(
        "三步搞定缓存优化" in line for line in payload.splitlines()[:8]
    )
    # 头部描述不再回退到公众号摘要「这是摘要。」
    header = "\n".join(payload.splitlines()[:10])
    assert "描述：三步搞定缓存优化" in header


# ── F22 图片卡集成：xhs 探测 .cards/ 整组带入（§十八）───────


def _write_article_with_cards(tmp_path: Path) -> Path:
    """测试文章 + 旁边预置 <stem>.cards/ 目录（2 张 PNG + 1 个非 PNG）。"""
    article = _write_article(tmp_path)
    cards_dir = tmp_path / "demo.cards"
    cards_dir.mkdir()
    (cards_dir / "demo-01.png").write_bytes(b"png-1")
    (cards_dir / "demo-02.png").write_bytes(b"png-2")
    (cards_dir / "note.txt").write_text("不是 PNG", encoding="utf-8")
    return article


def test_xhs_pack_includes_cards_group(tmp_path: Path) -> None:
    """xhs 物料包：.cards/ 目录存在即整组 PNG 复制入 images-cards/，非 PNG 不带。"""
    article = _write_article_with_cards(tmp_path)

    pack_dir = build_pack(article, "xhs")

    imgs = pack_dir / "images-cards"
    assert (imgs / "demo-01.png").read_bytes() == b"png-1"
    assert (imgs / "demo-02.png").read_bytes() == b"png-2"
    assert not (imgs / "note.txt").exists()  # 只带 PNG 整组
    # README 字段表补「图片卡」行
    readme = (pack_dir / "README.md").read_text(encoding="utf-8")
    assert "图片卡" in readme
    assert "images-cards/" in readme


def test_pack_without_cards_dir_skips_images_cards(tmp_path: Path) -> None:
    """无 .cards/ 目录：物料包不创建 images-cards/ 子目录。"""
    article = _write_article(tmp_path)

    pack_dir = build_pack(article, "xhs")

    assert (pack_dir / "README.md").exists()
    assert not (pack_dir / "images-cards").exists()


def test_non_xhs_platform_skips_cards_group(tmp_path: Path) -> None:
    """图片卡带入是 xhs 集成点：其余平台即使 .cards/ 存在也不带。"""
    article = _write_article_with_cards(tmp_path)

    pack_dir = build_pack(article, "channels")

    assert not (pack_dir / "images-cards").exists()
