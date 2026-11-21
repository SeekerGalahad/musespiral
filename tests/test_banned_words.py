"""banned_words（F33A 违禁词）+ check 扩（WARN 项 / --platform 预检）单元测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from musespiral.core.quality import banned_words as bw
from musespiral.core.quality.checks import has_failures, run_checks
from musespiral.core.content.parser import parse_article


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _write_article(tmp_path: Path, body: str, *, title: str = "违禁词测试", cover: str = "cover.png") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 383), "#336699").save(tmp_path / "cover.png")
    p = tmp_path / "article.md"
    p.write_text(
        f"---\ntitle: {title}\ncover: {cover}\ndigest: 摘要。\n---\n\n{body}", encoding="utf-8"
    )
    return p


# ── 词表加载 ──


def test_builtin_table_size() -> None:
    """内置基础表 ~30 词起步（绝对化用语 ∪ 广告法高危）。"""
    assert len(bw.BUILTIN_WORDS) >= 28


def test_user_file_merged_and_comments_skipped(tmp_path) -> None:
    """用户文件并入词表；# 注释行与空行跳过；文件缺失时只用内置表。"""
    user = tmp_path / "banned_words.txt"
    user.write_text("# 行业自定义\n自研黑话\n\n  # 缩进注释\n秒杀\n", encoding="utf-8")
    words = bw.load_words(user)
    assert "自研黑话" in words
    assert "秒杀" in words  # 与内置重复不炸
    assert all(not w.startswith("#") for w in words)
    # 词表按长度降序（长词优先，供扫描遮蔽）
    assert words == sorted(words, key=len, reverse=True)
    # 缺文件 → 只用内置表
    missing = bw.load_words(tmp_path / "nope.txt")
    assert set(missing) == set(bw.BUILTIN_WORDS)


def test_default_path_is_home_dir(tmp_path, monkeypatch) -> None:
    """默认读 ~/.musespiral/banned_words.txt（MUSESPIRAL_HOME 隔离）。"""
    home = tmp_path / "musespiral-home"
    home.mkdir()
    (home / "banned_words.txt").write_text("用户词\n", encoding="utf-8")
    assert "用户词" in bw.load_words()


# ── 逐行扫描 ──


def test_scan_hits_with_line_numbers() -> None:
    text = "正常一行。\n这行有秒杀。\n又正常。\n结尾来个根治。"
    assert bw.scan(text, words=["秒杀", "根治"]) == [("秒杀", 2), ("根治", 4)]


def test_scan_skips_code_blocks_not_headings() -> None:
    """标题行不豁免（绝对化用语最高发位）；``` 与 ~~~ 围栏内跳过（同符号开闭）。"""
    text = (
        "# 标题里有秒杀\n"
        "正文正常。\n"
        "```python\nprint('代码里的秒杀不算')\n```\n"
        "~~~python\nok = '波浪围栏里的秒杀也不算'\n~~~\n"
        "```python\nin_block = '``` 块内出现 ~~~ 也不闭合'\n```\n"
        "收尾。"
    )
    assert bw.scan(text, words=["秒杀"]) == [("秒杀", 1)]


def test_scan_long_word_masks_shorter_overlap() -> None:
    """「全网第一」命中后遮蔽，不再重复报「第一」（词表长词优先）。"""
    text = "我们的方案全网第一。"
    hits = bw.scan(text, words=["全网第一", "第一"])
    assert hits == [("全网第一", 1)]


def test_scan_same_word_same_line_reported_once() -> None:
    text = "秒杀价！再秒杀一次！"
    assert bw.scan(text, words=["秒杀"]) == [("秒杀", 1)]


# ── check 扩：违禁词恒 WARN 不 FAIL ──


def test_check_banned_words_warn_never_fail(tmp_path) -> None:
    p = _write_article(tmp_path, "我们的工具绝对是最好的。\n\n第二段正常。")
    results = run_checks(parse_article(p), p)
    warns = [msg for lvl, msg in results if lvl == "WARN" and "违禁词" in msg]
    assert len(warns) == 1
    assert "绝对×L1" in warns[0] and "最好×L1" in warns[0]
    assert not has_failures(results), "违禁词只标记不否决，check 仍 PASS"


def test_check_clean_article_has_no_banned_words_item(tmp_path) -> None:
    """无命中不输出违禁词条目（clean 文章报告输出零变化——F34 字节兼容前提）。"""
    p = _write_article(tmp_path, "性能优化从缓存开始。\n\n第二段。")
    results = run_checks(parse_article(p), p)
    assert not any("违禁词" in msg for _, msg in results)


def test_check_banned_words_line_numbers_are_body_relative(tmp_path) -> None:
    p = _write_article(tmp_path, "第一段正常。\n\n第二段出现秒杀。\n\n第三段。")
    results = run_checks(parse_article(p), p)
    warn = next(msg for lvl, msg in results if lvl == "WARN" and "违禁词" in msg)
    assert "秒杀×L3" in warn  # 正文第 3 行（空行计入，frontmatter 不计入）


# ── check 扩：--platform 预检（字限/图数/封面三项）──


def _has_cover_spec(code: str) -> bool:
    """F33 预检的封面维度消费 PLIMITS cover_w/cover_h（F31 规格表，Group A
    交付物）——表未落地时封面维度无法验证，相关用例显式 skip 而非假绿。"""
    from musespiral.platforms import profiles

    lim = profiles.get_limits(code)
    return "cover_w" in lim and "cover_h" in lim


_xhs_cover_ready = pytest.mark.skipif(
    not _has_cover_spec("xhs"), reason="PLIMITS 尚无 cover_w/cover_h（F31 规格表未落地）"
)


@_xhs_cover_ready
def test_platform_precheck_xhs_all_three(tmp_path) -> None:
    """xhs：标题超 20 字 FAIL；封面 900×383 ≠ 1242×1656 FAIL；1 图满足 images_min。"""
    p = _write_article(tmp_path, "正文。\n\n![图](img.png)", title="这是一个超过二十个字的超长标题肯定已经超限")
    Image.new("RGB", (900, 383), "#336699").save(tmp_path / "img.png")
    results = run_checks(parse_article(p), p, platform="xhs")
    fails = [msg for lvl, msg in results if lvl == "FAIL" and msg.startswith("xhs")]
    assert any("标题超限: 21 字 > 20 字" in m for m in fails)
    assert any("封面尺寸不符: 900×383 ≠ 1242×1656" in m for m in fails)
    assert not any("图片" in m for m in fails), "1 张图满足 images_min=1"


@_xhs_cover_ready
def test_platform_precheck_cover_dimension(tmp_path) -> None:
    """封面规格键（cover_w/cover_h）核对：尺寸不符 FAIL / 相符 PASS。"""
    p = _write_article(tmp_path, "正文一张图。\n\n![图](img.png)")
    Image.new("RGB", (10, 10), "#336699").save(tmp_path / "img.png")
    results = run_checks(parse_article(p), p, platform="xhs")
    assert any("封面尺寸不符: 900×383 ≠ 1242×1656" in m for _, m in results)

    ok = _write_article(tmp_path / "ok", "正文一张图。\n\n![图](img.png)")
    Image.new("RGB", (1242, 1656), "#336699").save(tmp_path / "ok" / "img.png")
    Image.new("RGB", (1242, 1656), "#336699").save(tmp_path / "ok" / "cover.png")
    ok_results = run_checks(parse_article(ok), ok, platform="xhs")
    assert any("xhs 封面规格合规: 1242×1656" in m for _, m in ok_results)


def test_platform_precheck_images_bounds(tmp_path, monkeypatch) -> None:
    """图数对齐 pack 口径（卡组+封面）：19 卡+封面=20 张超限 FAIL；zhihu 不设上限。"""
    from musespiral.platforms import profiles

    p = _write_article(tmp_path, "十九张卡。\n\n正文一张内联图 ![图](img.png)。")
    Image.new("RGB", (4, 4), "#336699").save(tmp_path / "img.png")
    cards = tmp_path / "article.cards"
    cards.mkdir()
    for i in range(19):
        Image.new("RGB", (4, 4), "#336699").save(cards / f"article-{i + 1:02d}.png")
    results = run_checks(parse_article(p), p, platform="xhs")
    # 19 卡 + 1 封面 = 20 张（内联图不计——平台上传的是卡组+封面，非 markdown 内联图）
    assert any("图片超限: 20 张 > 上限 18" in m for _, m in results)

    zhihu = run_checks(parse_article(p), p, platform="zhihu")
    assert not any("图片超限" in m for _, m in zhihu), "zhihu images_max=None 不设上限"


def test_platform_precheck_images_pack_parity_no_inline(tmp_path) -> None:
    """文本+卡组流（正文无内联图）：check --platform xhs 与 pack 口径一致不假阳性。

    3 卡 + 封面 = 4 张 → 合规（修复前数内联图得 0 张，恒 FAIL 与 pack 互相矛盾）。
    """
    p = _write_article(tmp_path, "正文无内联图的文本流。\n\n第二段。")
    cards = tmp_path / "article.cards"
    cards.mkdir()
    for i in range(3):
        Image.new("RGB", (1242, 1656), "#336699").save(cards / f"article-{i + 1:02d}.png")
    Image.new("RGB", (1242, 1656), "#336699").save(tmp_path / "cover.png")  # xhs 规格封面
    results = run_checks(parse_article(p), p, platform="xhs")
    assert any("xhs 图片数合规: 4 张" in m for _, m in results)
    assert any("xhs 封面规格合规: 1242×1656" in m for _, m in results)
    assert not any(m.startswith("xhs") and "图片不足" in m for _, m in results)


def test_platform_precheck_cover_prefers_platform_derived(tmp_path) -> None:
    """封面取材 F31 优先级：文章目录 cover.xhs.png > frontmatter.cover（mp 规格）。"""
    p = _write_article(tmp_path, "正文。")  # fixture 的 cover.png 是 900×383（mp 规格）
    Image.new("RGB", (1242, 1656), "#336699").save(tmp_path / "cover.xhs.png")
    results = run_checks(parse_article(p), p, platform="xhs")
    # 探测优先取平台派生封面 → xhs 规格合规；而不是 fm 指向的 900×383 报 FAIL
    assert any("xhs 封面规格合规: 1242×1656" in m for _, m in results)
    assert not any("xhs 封面尺寸不符" in m for _, m in results)


def test_platform_precheck_channels_title_min(tmp_path) -> None:
    """channels 标题低于 3 字下限 → FAIL。"""
    p = _write_article(tmp_path, "正文。", title="两字")
    results = run_checks(parse_article(p), p, platform="channels")
    assert any("标题低于下限: 2 字 < 3 字" in m for _, m in results)


def test_platform_precheck_mp_byte_limit(tmp_path) -> None:
    """mp 预检走字节口径（title_max_bytes=64）。"""
    long_title = "长" * 22  # 22 个汉字 = 66 字节 > 64
    p = _write_article(tmp_path, "正文。", title=long_title)
    results = run_checks(parse_article(p), p, platform="mp")
    assert any("mp 标题超限: 66 字节 > 64 字节" in m for _, m in results)


def test_platform_precheck_zhihu_no_cover_spec(tmp_path) -> None:
    """zhihu 无强制封面（无 cover_w/cover_h 键）→ 封面项静默跳过。"""
    p = _write_article(tmp_path, "正文。")
    results = run_checks(parse_article(p), p, platform="zhihu")
    assert any("zhihu 标题字限合规" in m for _, m in results)
    assert not any("封面" in m and m.startswith("zhihu") for _, m in results)


# ── F42：juejin/bilibili --platform 预检（PLIMITS 联动，§二-2.1 冻结表）──


def test_platform_precheck_juejin_title_and_cover(tmp_path) -> None:
    """juejin：标题 101 字 > 100 FAIL；封面 1600×900 派生图合规 PASS。"""
    p = _write_article(tmp_path, "正文。", title="超" * 101)
    Image.new("RGB", (1600, 900), "#336699").save(tmp_path / "cover.juejin.png")
    results = run_checks(parse_article(p), p, platform="juejin")
    assert any("juejin 标题超限: 101 字 > 100 字" in m for _, m in results)
    assert any("juejin 封面规格合规: 1600×900" in m for _, m in results)  # 探测优先平台派生图

    ok = _write_article(tmp_path / "ok", "正文。", title="合规标题")
    Image.new("RGB", (1600, 900), "#336699").save(tmp_path / "ok" / "cover.png")
    ok_results = run_checks(parse_article(ok), ok, platform="juejin")
    assert any("juejin 标题字限合规: 4/100" in m for _, m in ok_results)


def test_platform_precheck_bilibili_title_and_cover(tmp_path) -> None:
    """bilibili：标题 81 字 > 80 FAIL；mp 规格 900×383 封面 ≠ 1600×1000 FAIL。"""
    p = _write_article(tmp_path, "正文。", title="超" * 81)
    results = run_checks(parse_article(p), p, platform="bilibili")
    assert any("bilibili 标题超限: 81 字 > 80 字" in m for _, m in results)
    assert any("bilibili 封面尺寸不符: 900×383 ≠ 1600×1000" in m for _, m in results)

    ok = _write_article(tmp_path / "ok", "正文。", title="合规标题")
    Image.new("RGB", (1600, 1000), "#336699").save(tmp_path / "ok" / "cover.png")
    ok_results = run_checks(parse_article(ok), ok, platform="bilibili")
    assert any("bilibili 封面规格合规: 1600×1000" in m for _, m in ok_results)
