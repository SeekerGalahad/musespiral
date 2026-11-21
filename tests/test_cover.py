"""F31 封面生成 + F33B 物料级校验 — 四路线/写回/pack 边界（§五 测试策略）。

全部 tmp_path 隔离，无网络、无 AI。工作流 venv 不装 playwright（cards 同款
约定）：title-card 走 Pillow 兜底直绘路径测——monkeypatch sys.modules 注入
None 复现「无浏览器环境」（装没装 playwright 都确定性走兜底）；光栅路径的
浏览器门（BrowserNotInstalled）由 cards/test_cards.py 结构测试覆盖，这里
只锚定「缺浏览器 → 兜底直绘不报错、尺寸精确」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from musespiral.cli import cli
from musespiral.commands.cover_cmds import cover as cover_cmd
from musespiral.core.visual import cover as cover_mod
from musespiral.platforms.profiles import PLIMITS
from musespiral.services import cover_service


def _write_article(tmp_path: Path, name: str = "demo.md") -> Path:
    article = tmp_path / name
    article.write_text(
        "---\n"
        "title: 缓存优化三步走\n"
        "digest: 这是摘要。\n"
        "tags: [AI, 写作]\n"
        "---\n"
        "\n"
        "首句钩子在这里，一篇文章的开始。后续句子。\n"
        "\n"
        "## 一\n"
        "\n"
        "内容一。\n",
        encoding="utf-8",
    )
    return article


def _png(path: Path, size: tuple[int, int], color: str = "white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.modules 注入 None → import 确定性抛 ImportError（cards 测试同款）。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)


def _size(png: Path) -> tuple[int, int]:
    with Image.open(png) as im:
        return im.size


# ── 规格表：PLIMITS 唯一事实源（§一 表冻结值）───────────────


def test_plimits_cover_specs_frozen_table() -> None:
    """§一 封面规格表逐值断言（F31 派生与 F33 预检的唯一事实源）。"""
    assert (PLIMITS["mp"]["cover_w"], PLIMITS["mp"]["cover_h"]) == (900, 383)
    assert (PLIMITS["xhs"]["cover_w"], PLIMITS["xhs"]["cover_h"]) == (1242, 1656)
    assert (PLIMITS["douyin"]["cover_w"], PLIMITS["douyin"]["cover_h"]) == (1080, 1440)
    assert (PLIMITS["channels"]["cover_w"], PLIMITS["channels"]["cover_h"]) == (1080, 1080)
    assert "cover_w" not in PLIMITS["zhihu"] and "cover_h" not in PLIMITS["zhihu"]
    # F42 §二-2.1 冻结表：掘金 16:9 / B站专栏 16:10
    assert (PLIMITS["juejin"]["cover_w"], PLIMITS["juejin"]["cover_h"]) == (1600, 900)
    assert (PLIMITS["bilibili"]["cover_w"], PLIMITS["bilibili"]["cover_h"]) == (1600, 1000)
    # cover_size 消费同表：None→mp；zhihu 明确拒绝（无强制封面不派生）
    assert cover_mod.cover_size(None) == (900, 383)
    assert cover_mod.cover_size("mp") == (900, 383)
    assert cover_mod.cover_size("juejin") == (1600, 900)
    assert cover_mod.cover_size("bilibili") == (1600, 1000)
    with pytest.raises(ValueError, match="无强制封面"):
        cover_mod.cover_size("zhihu")
    with pytest.raises(ValueError, match="未知平台"):
        cover_mod.cover_size("myspace")


# ── title-card：Pillow 兜底直绘 + HTML 模板复用 cards 管线 ──


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        (None, (900, 383)),  # 缺省 = mp 规格
        ("mp", (900, 383)),
        ("xhs", (1242, 1656)),
        ("douyin", (1080, 1440)),
        ("channels", (1080, 1080)),
        ("juejin", (1600, 900)),  # F42 §二-2.1
        ("bilibili", (1600, 1000)),  # F42 §二-2.1
    ],
)
def test_title_card_pillow_fallback_exact_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str | None, expected: tuple[int, int],
) -> None:
    """无浏览器环境（monkeypatch 复现）：title-card 兜底直绘产出精确规格 PNG。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)

    out = cover_service.generate_cover(article, route="title-card", platform=platform)

    assert out == tmp_path / ("cover.png" if platform in (None, "mp") else f"cover.{platform}.png")
    assert _size(out) == expected


def test_title_card_fallback_draws_title_and_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底直绘内容可见性：标题与首句钩子渲染为非底色像素（CJK 字体在场）。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)

    img = cover_mod.draw_title_card(article, size=(900, 383))
    assert img.size == (900, 383)

    # 版式元素非空：强调条与文本区存在非纯白像素（有字有条，不是空白卡）
    assert any(lo < 255 for lo, _ in img.getextrema())


def test_title_card_html_reuses_cards_pipeline(tmp_path: Path) -> None:
    """HTML 模板路径：复用 cards cover_card 容器（.musespiral-card + data-w/h）。"""
    article = _write_article(tmp_path)
    html = cover_mod.build_title_card_html(article, size=(900, 383))

    assert 'class="musespiral-card musespiral-card--cover"' in html
    assert 'data-w="900" data-h="383"' in html
    assert "缓存优化三步走" in html  # frontmatter.title
    assert "首句钩子在这里，一篇文章的开始。" in html  # 首段首句作钩子
    assert "<style" not in html  # cards 同款纯内联 CSS


def test_title_card_browser_raster_preferred_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有浏览器时走 cards 光栅管线：mock shoot 落一张标记 PNG，断言被消费。"""
    article = _write_article(tmp_path)

    def _fake_shoot(html_path, out_dir):
        out = Path(out_dir) / "shot-01.png"
        _png(out, (900, 383), color="#010203")  # 标记色：只有 mock 会产出
        return [out]

    monkeypatch.setattr(cover_mod.cards_raster, "shoot", _fake_shoot)
    img = cover_mod.render_title_card(article, size=(900, 383))
    assert img.size == (900, 383)
    assert img.getpixel((10, 10)) == (1, 2, 3)  # 光栅产物（非 Pillow 兜底）


def test_title_card_bad_browser_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """光栅中途异常（含缺 playwright）同样兜底直绘，封面必有产出。"""
    article = _write_article(tmp_path)

    def _boom(html_path, out_dir):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(cover_mod.cards_raster, "shoot", _boom)
    img = cover_mod.render_title_card(article, size=(900, 383))
    assert img.size == (900, 383)


# ── 中心裁 / 头区裁：比例正确性 ─────────────────────────────


def test_crop_box_center_math() -> None:
    """中心裁纯函数：源比目标更宽（w/h>ratio）左右裁，否则上下裁，居中取最大整比矩形。"""
    # 1000×1000（1.0 < 2.35）→ 上下裁 h=round(1000/2.3499)=426，y0=(1000-426)//2=287
    assert cover_mod.crop_box_center(1000, 1000, 900 / 383) == (0, 287, 1000, 713)
    # 2000×1000（2.0 < 2.35）→ 上下裁 h=round(2000/2.3499)=851，y0=(1000-851)//2=74
    assert cover_mod.crop_box_center(2000, 1000, 900 / 383) == (0, 74, 2000, 925)
    # 3000×1000（3.0 > 2.35）→ 左右裁 w=round(1000*2.3499)=2350，x0=(3000-2350)//2=325
    assert cover_mod.crop_box_center(3000, 1000, 900 / 383) == (325, 0, 2675, 1000)


def test_from_image_center_crop_keeps_center_region(
    tmp_path: Path,
) -> None:
    """from-image：中心裁保留源图中央带（上下缘被裁掉），输出精确规格。"""
    article = _write_article(tmp_path)
    src = tmp_path / "src.png"
    base = Image.new("RGB", (900, 900), "red")
    draw = ImageDraw.Draw(base)
    draw.rectangle([0, 300, 900, 600], fill="orange")  # 中央水平带
    base.save(src)

    out = cover_service.generate_cover(
        article, route="from-image", platform="mp", image=src, write_back=False
    )
    with Image.open(out) as img:
        assert img.size == (900, 383)
        # 裁剪框 (0,258,900,641)：中央带（y 300-600）映到输出 y 约 42-342
        assert img.getpixel((450, 191)) == (255, 165, 0)  # 中部=源中央带（橙）
        assert img.getpixel((450, 380)) == (255, 0, 0)  # 底部=框内源底（红）


def test_from_image_rgba_flattened_to_white(tmp_path: Path) -> None:
    """RGBA→RGB 白底：透明源图输出为不透明 RGB PNG。"""
    article = _write_article(tmp_path)
    src = tmp_path / "rgba.png"
    Image.new("RGBA", (500, 500), (255, 0, 0, 0)).save(src)  # 全透明

    out = cover_service.generate_cover(
        article, route="from-image", platform="mp", image=src, write_back=False
    )
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert im.size == (900, 383)


def test_from_image_missing_rejected(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    with pytest.raises(FileNotFoundError, match="源图不存在"):
        cover_service.generate_cover(
            article, route="from-image", image=tmp_path / "nope.png"
        )
    with pytest.raises(ValueError, match="--from-image"):
        cover_service.generate_cover(article, route="from-image", image=None)
    with pytest.raises(ValueError, match="未知封面路线"):
        cover_service.generate_cover(article, route="magic")


# ── from-cards / from-poster：取材定位与头区 ────────────────


def test_from_cards_uses_first_sorted_card(tmp_path: Path) -> None:
    """from-cards：取 .cards/ 排序首卡（cards 光栅 <stem>-01.png 命名惯例）。"""
    article = _write_article(tmp_path)
    cards = tmp_path / "demo.cards"
    _png(cards / "demo-01.png", (1242, 1656), color="#1000aa")
    _png(cards / "demo-02.png", (1242, 1656), color="#2000bb")

    out = cover_service.generate_cover(
        article, route="from-cards", platform="mp", write_back=False
    )
    with Image.open(out) as im:
        assert im.size == (900, 383)
        assert im.getpixel((450, 190)) == (16, 0, 170)  # 首卡颜色，非次卡


def test_from_cards_missing_dir_rejected(tmp_path: Path) -> None:
    article = _write_article(tmp_path)
    with pytest.raises(FileNotFoundError, match="卡组目录不存在"):
        cover_service.generate_cover(article, route="from-cards")
    (tmp_path / "demo.cards").mkdir()
    with pytest.raises(FileNotFoundError, match="无 PNG"):
        cover_service.generate_cover(article, route="from-cards")


def test_from_poster_takes_header_region(tmp_path: Path) -> None:
    """from-poster：头区裁自顶部（标题区在上，顶部色带必须进入封面）。"""
    article = _write_article(tmp_path)
    poster = Image.new("RGB", (1080, 3000), "navy")
    draw = ImageDraw.Draw(poster)
    draw.rectangle([0, 0, 1080, 300], fill="orange")  # 头区标题带（< 裁剪高 460）
    poster.save(tmp_path / "demo.poster.png")

    out = cover_service.generate_cover(
        article, route="from-poster", platform="mp", write_back=False
    )
    with Image.open(out) as im:
        assert im.size == (900, 383)
        # 裁剪区 (0,0,1080,460)：源 y<300（橙）映到输出 y<250
        assert im.getpixel((5, 5)) == (255, 165, 0)  # 顶部头区颜色在场
        assert im.getpixel((450, 380)) == (0, 0, 128)  # 底部已是头区以下（navy）


def test_from_poster_short_falls_back_to_center(tmp_path: Path) -> None:
    """长图高度不足整比时退中心裁：输出仍精确规格、不拉伸。"""
    article = _write_article(tmp_path)
    _png(tmp_path / "demo.poster.png", (1080, 300))  # 3.6:1 比 2.35:1 更宽
    out = cover_service.generate_cover(
        article, route="from-poster", platform="mp", write_back=False
    )
    assert _size(out) == (900, 383)
    with pytest.raises(FileNotFoundError, match="长图不存在"):
        cover_service.generate_cover(
            _write_article(tmp_path, "other.md"), route="from-poster"
        )


# ── cover_service：落盘命名 / 写回 / 版本池 / --no-write-back ─


def test_service_default_writes_cover_png_and_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认路线：cover.png 落盘 + frontmatter cover 写回 + 当前版入版本池。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    original = article.read_bytes()

    out = cover_service.generate_cover(article)

    assert out == tmp_path / "cover.png"
    assert out.exists()
    # 版本池（F43）：<name>.md.bak.<时间戳> = 写回前整文件字节（Studio 保存同语义）
    pool = sorted(tmp_path.glob("demo.md.bak.*"))
    assert len(pool) == 1 and pool[0].read_bytes() == original
    import frontmatter

    post = frontmatter.load(article)
    assert post.metadata["cover"] == "cover.png"
    assert post.metadata["title"] == "缓存优化三步走"  # 其余 frontmatter 不动
    assert "首句钩子在这里" in post.content  # 正文不动


def test_service_platform_derived_name_no_write_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--platform xhs：cover.xhs.png 落盘但不写回 frontmatter（§二 F31——写回
    只挂默认行；平台封面由 pack/check --platform 探测优先级发现，不顶替 mp
    指针）；重跑覆盖幂等，全程不动文章、不入版本池。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    original = article.read_bytes()

    out1 = cover_service.generate_cover(article, platform="xhs")
    assert out1 == tmp_path / "cover.xhs.png"
    import frontmatter

    assert "cover" not in frontmatter.load(article).metadata  # frontmatter 不动
    assert article.read_bytes() == original  # 文章整字节不动
    assert not list(tmp_path.glob("demo.md.bak*"))  # 不写回即不备份

    out2 = cover_service.generate_cover(article, platform="xhs")
    assert out2 == out1 and _size(out2) == (1242, 1656)
    assert article.read_bytes() == original


def test_service_no_write_back_only_drops_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-write-back：只落图——frontmatter 不动、不入版本池。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    original = article.read_bytes()

    out = cover_service.generate_cover(article, platform="mp", write_back=False)

    assert out.exists()
    assert article.read_bytes() == original
    assert not list(tmp_path.glob("demo.md.bak*"))


# ── CLI ─────────────────────────────────────────────────────


def test_cli_cover_from_image_smoke(tmp_path: Path) -> None:
    """CLI 冒烟：cover --from-image src.png --platform xhs → 1242×1656（验收 §七 句）；
    非 mp 只落图不写回 frontmatter。"""
    article = _write_article(tmp_path)
    original = article.read_bytes()
    src = _png(tmp_path / "photo.png", (1600, 1200))

    result = CliRunner().invoke(
        cover_cmd, [str(article), "--from-image", str(src), "--platform", "xhs"]
    )

    assert result.exit_code == 0, result.output
    out = tmp_path / "cover.xhs.png"
    assert out.exists() and _size(out) == (1242, 1656)
    assert "封面已生成" in result.output
    assert "frontmatter cover 不动" in result.output
    assert article.read_bytes() == original  # 非 mp 不写回：文章整字节不动


def test_cli_cover_title_card_default_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 缺省路线：无路线旗标 → title-card（Pillow 兜底）+ 写回。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)

    result = CliRunner().invoke(cover_cmd, [str(article)])

    assert result.exit_code == 0, result.output
    assert _size(tmp_path / "cover.png") == (900, 383)
    assert list(tmp_path.glob("demo.md.bak.*"))  # 写回 → 时间戳版本池


def test_cli_cover_no_write_back_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    original = article.read_bytes()

    result = CliRunner().invoke(cover_cmd, [str(article), "--no-write-back"])

    assert result.exit_code == 0, result.output
    assert "--no-write-back" in result.output
    assert article.read_bytes() == original


def test_cli_cover_missing_source_readable_error(tmp_path: Path) -> None:
    """取材缺失给可读错误 exit 1，不落半成品。"""
    article = _write_article(tmp_path)
    result = CliRunner().invoke(cover_cmd, [str(article), "--from-poster"])
    assert result.exit_code == 1
    assert "长图不存在" in result.output
    assert not (tmp_path / "cover.png").exists()


def test_cli_cover_registered_in_root_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cli.py 注册生效（经根入口可调，cover 命令在本组注册行）。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    result = CliRunner().invoke(cli, ["cover", str(article)], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cover.png").exists()


def test_cli_cover_routes_mutually_exclusive(tmp_path: Path) -> None:
    """路线互斥：同时给两条 → 可读错误 exit 1，不落半成品。"""
    article = _write_article(tmp_path)
    src = _png(tmp_path / "photo.png", (900, 900))
    result = CliRunner().invoke(
        cover_cmd, [str(article), "--from-cards", "--from-image", str(src)]
    )
    assert result.exit_code == 1
    assert "路线互斥" in result.output
    assert not (tmp_path / "cover.png").exists()


# ── F33B：pack_checks 物料级边界（xhs 18/19 张、封面尺寸对错）─


def _xhs_pack(tmp_path: Path, *, n_cards: int, cover_size_px: tuple[int, int] | None):
    """搭 xhs 物料场景：文章 + cover.png（可指尺寸）+ .cards/ N 张，走 build_pack。"""
    from musespiral.services.pack_service import build_pack

    tmp_path.mkdir(parents=True, exist_ok=True)
    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ntags: [AI]\n---\n\n正文内容若干。\n",
        encoding="utf-8",
    )
    if cover_size_px is not None:
        _png(tmp_path / "cover.png", cover_size_px)
        text = article.read_text(encoding="utf-8").replace(
            "tags:", "cover: cover.png\ntags:"
        )
        article.write_text(text, encoding="utf-8")
    if n_cards:
        cards = tmp_path / "demo.cards"
        for i in range(1, n_cards + 1):
            _png(cards / f"demo-{i:02d}.png", (1242, 1656))
    pack_dir = build_pack(article, "xhs", limits=dict(PLIMITS["xhs"]))
    return pack_dir, (pack_dir / "checks.txt").read_text(encoding="utf-8")


def test_pack_xhs_eighteen_cards_pass(tmp_path: Path) -> None:
    """xhs 18 张图（18 卡不设封面）：图数边界上沿 PASS，无 FAIL。"""
    _, checks = _xhs_pack(tmp_path, n_cards=18, cover_size_px=None)
    assert "ok 图片数: 18/1-18" in checks
    assert "FAIL 图片数" not in checks
    assert "封面尺寸" not in checks  # 无封面 → 尺寸项跳过


def test_pack_xhs_nineteen_cards_fail(tmp_path: Path) -> None:
    """xhs 19 张图：越界 FAIL（§七 验收句：xhs pack 19 图 FAIL）。"""
    _, checks = _xhs_pack(tmp_path, n_cards=19, cover_size_px=None)
    assert "FAIL 图片数: 19/1-18（超限）" in checks


def test_pack_xhs_zero_images_below_min(tmp_path: Path) -> None:
    """无卡无封面：0 张低于下限 1 → FAIL（承接缺口 1-8 的机器核对）。"""
    _, checks = _xhs_pack(tmp_path, n_cards=0, cover_size_px=None)
    assert "FAIL 图片数: 0/1-18（低于下限）" in checks


def test_pack_xhs_cover_size_match_and_mismatch(tmp_path: Path) -> None:
    """封面尺寸对错：1242×1656 → ok；900×383（mp 规格）→ FAIL。"""
    _, checks_ok = _xhs_pack(tmp_path, n_cards=1, cover_size_px=(1242, 1656))
    assert "ok 封面尺寸: 1242×1656" in checks_ok
    # 1 卡 + 1 封面 = 2 张，图数项也在场且过
    assert "ok 图片数: 2/1-18" in checks_ok
    assert "FAIL" not in checks_ok

    _, checks_bad = _xhs_pack(tmp_path / "sub2", n_cards=1, cover_size_px=(900, 383))
    assert "FAIL 封面尺寸: 900×383 ≠ 1242×1656" in checks_bad


def test_pack_platform_cover_priority_and_skip_unreadable(tmp_path: Path) -> None:
    """探测优先级 cover.<platform>.png > cover.png；读不出尺寸跳过不 FAIL。"""
    from musespiral.core.quality.pack_checks import _find_pack_cover, _material_checks_lines
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ncover: cover.xhs.png\ntags: [AI]\n---\n\n正文。\n",
        encoding="utf-8",
    )
    _png(tmp_path / "cover.xhs.png", (1242, 1656))
    (tmp_path / "cover.png").write_bytes(b"not-a-png")  # 占位同名基础封面

    pack_dir = build_pack(article, "xhs", limits=dict(PLIMITS["xhs"]))
    # cover/ 内两文件都在（文章目录 cover.xhs.png 探测优先被复制；再手动放一个）
    (pack_dir / "cover" / "cover.png").write_bytes(b"not-a-png")

    assert _find_pack_cover(pack_dir, "xhs") == pack_dir / "cover" / "cover.xhs.png"
    lines = _material_checks_lines(pack_dir, "xhs", PLIMITS["xhs"])
    assert "ok 封面尺寸: 1242×1656" in lines  # 优先命中的 xhs 封面可读 → 核对通过

    # 全部读不出尺寸（假字节）：尺寸项跳过，不产出 FAIL
    (pack_dir / "cover" / "cover.xhs.png").write_bytes(b"not-a-png-either")
    lines2 = _material_checks_lines(pack_dir, "xhs", PLIMITS["xhs"])
    assert not any("封面尺寸" in ln for ln in lines2)


def test_pack_article_dir_platform_cover_wins_over_frontmatter(tmp_path: Path) -> None:
    """build_pack 文章目录探测（§二 F31 修复）：文章目录有合规 cover.<platform>.png
    时优先复制之，不再被 frontmatter 指向的 mp 规格图顶替（修复②：旧实现复制
    cover.png 反报 900×383≠1242×1656 FAIL）。"""
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ncover: cover.png\ntags: [AI]\n---\n\n正文。\n",
        encoding="utf-8",
    )
    _png(tmp_path / "cover.png", (900, 383))  # mp 规格基础封面（fm 指向）
    _png(tmp_path / "cover.xhs.png", (1242, 1656))  # F31 平台封面（探测优先）

    pack_dir = build_pack(article, "xhs", limits=dict(PLIMITS["xhs"]))
    copied = list((pack_dir / "cover").iterdir())
    assert [p.name for p in copied] == ["cover.xhs.png"]  # 只复制平台封面
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "ok 封面尺寸: 1242×1656" in checks
    assert "FAIL 封面尺寸: 900×383" not in checks


def test_pack_foreign_cover_name_still_size_checked(tmp_path: Path) -> None:
    """frontmatter cover 自由文件名（banner.jpg）：pack 复制后照样进尺寸核对
    （单文件回退）——错规格不再零核对静默放行（问题2）。"""
    from musespiral.core.quality.pack_checks import _find_pack_cover
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ncover: banner.jpg\ntags: [AI]\n---\n\n正文。\n",
        encoding="utf-8",
    )
    Image.new("RGB", (900, 383), "white").save(tmp_path / "banner.jpg")  # mp 规格冒充

    pack_dir = build_pack(article, "channels", limits=dict(PLIMITS["channels"]))
    assert _find_pack_cover(pack_dir, "channels") == pack_dir / "cover" / "banner.jpg"
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "FAIL 封面尺寸: 900×383 ≠ 1080×1080" in checks  # 非标准名也 FAIL


def test_pack_cross_platform_cover_mismatch_now_caught(tmp_path: Path) -> None:
    """fm 指向他平台封面（cover.xhs.png）+ pack channels：错规格图被复制进包
    时（无 cover.channels.png 可探测），尺寸核对经单文件回退照样 FAIL（修复③：
    旧实现整项静默跳过）。"""
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ncover: cover.xhs.png\ntags: [AI]\n---\n\n正文。\n",
        encoding="utf-8",
    )
    _png(tmp_path / "cover.xhs.png", (1242, 1656))  # xhs 规格图冒充 channels 封面

    pack_dir = build_pack(article, "channels", limits=dict(PLIMITS["channels"]))
    assert (pack_dir / "cover" / "cover.xhs.png").is_file()
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "FAIL 封面尺寸: 1242×1656 ≠ 1080×1080" in checks


def test_pack_checks_legacy_limits_unchanged(tmp_path: Path) -> None:
    """旧四键注入（无 images/cover 键）：checks.txt 与旧版逐行一致（向后兼容）。"""
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ntags: [AI, 写作]\n---\n\n这是文章正文第一段。\n",
        encoding="utf-8",
    )
    pack_dir = build_pack(
        article, "channels", limits={"title_max": 16, "content_max": 1000, "tags_max": 6}
    )
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8").splitlines()
    assert checks == [
        "ok 标题字数: 7/16",
        "ok 正文字数: 10/1000",
        "ok 标签数: 2/6",
    ]


def test_pack_zhihu_no_cover_spec_no_material_lines(tmp_path: Path) -> None:
    """zhihu 无封面规格键：封面尺寸项天然跳过（无强制封面，§一 表）。"""
    from musespiral.services.pack_service import build_pack

    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 缓存优化三步走\ntags: [AI]\n---\n\n正文。\n", encoding="utf-8"
    )
    pack_dir = build_pack(article, "zhihu", limits=dict(PLIMITS["zhihu"]))
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "封面尺寸" not in checks
    assert "图片数" not in checks  # zhihu images_max=None 不触发图数项


# ── F42：juejin/bilibili pack 规格核对（§二-2.1 冻结表 + bilibili 5MB WARN）─


def _f42_pack(
    tmp_path: Path,
    platform: str,
    *,
    cover_img=None,
    title: str = "缓存优化三步走",
):
    """搭 F42 平台物料场景：文章 + 可选封面（可指定生成器），走 build_pack。"""
    from musespiral.services.pack_service import build_pack

    tmp_path.mkdir(parents=True, exist_ok=True)
    article = tmp_path / "demo.md"
    article.write_text(
        f"---\ntitle: {title}\ntags: [AI]\n---\n\n正文内容若干。\n", encoding="utf-8"
    )
    if cover_img is not None:
        cover_img(tmp_path / "cover.png")
        text = article.read_text(encoding="utf-8").replace("tags:", "cover: cover.png\ntags:")
        article.write_text(text, encoding="utf-8")
    pack_dir = build_pack(article, platform, limits=dict(PLIMITS[platform]))
    return pack_dir, (pack_dir / "checks.txt").read_text(encoding="utf-8")


def _noise_png(path: Path, size: tuple[int, int]) -> Path:
    """不可压缩噪声 PNG（RGBA）——bilibili 封面体积 >5MB 用例的体积放大器。"""
    import random

    w, h = size
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGBA", (w, h), random.Random(42).randbytes(w * h * 4)).save(path)
    return path


def test_pack_f42_title_limits_from_plimits(tmp_path: Path) -> None:
    """juejin/bilibili 标题字限经 PLIMITS 注入 checks.txt（100/80，§二-2.1）。"""
    _, jj = _f42_pack(tmp_path / "jj", "juejin")
    assert "ok 标题字数: 7/100" in jj
    _, bl = _f42_pack(tmp_path / "bl", "bilibili")
    assert "ok 标题字数: 7/80" in bl
    _, over = _f42_pack(tmp_path / "over", "bilibili", title="超" * 81)
    assert "FAIL 标题字数: 81/80（超限）" in over


def test_pack_f42_cover_dimensions_match_and_mismatch(tmp_path: Path) -> None:
    """封面尺寸对错：1600×900/1600×1000 → ok；mp 规格 900×383 冒充 → 各自 FAIL。"""
    _, jj_ok = _f42_pack(
        tmp_path / "jj-ok", "juejin", cover_img=lambda p: _png(p, (1600, 900))
    )
    assert "ok 封面尺寸: 1600×900" in jj_ok
    assert "封面大小" not in jj_ok  # juejin 不设 cover_max_bytes，大小项跳过

    _, bl_ok = _f42_pack(
        tmp_path / "bl-ok", "bilibili", cover_img=lambda p: _png(p, (1600, 1000))
    )
    assert "ok 封面尺寸: 1600×1000" in bl_ok

    _, jj_bad = _f42_pack(
        tmp_path / "jj-bad", "juejin", cover_img=lambda p: _png(p, (900, 383))
    )
    assert "FAIL 封面尺寸: 900×383 ≠ 1600×900" in jj_bad
    _, bl_bad = _f42_pack(
        tmp_path / "bl-bad", "bilibili", cover_img=lambda p: _png(p, (900, 383))
    )
    assert "FAIL 封面尺寸: 900×383 ≠ 1600×1000" in bl_bad


def test_pack_bilibili_cover_over_5mb_warns(tmp_path: Path) -> None:
    """bilibili 封面 1600×1000 尺寸合规但体积 >5MB → ok 尺寸 + WARN 大小
    （F42 §二-2.1：≤5MB 由 pack_checks 补 WARN，不 FAIL）。"""
    _, checks = _f42_pack(
        tmp_path, "bilibili", cover_img=lambda p: _noise_png(p, (1600, 1000))
    )
    assert "ok 封面尺寸: 1600×1000" in checks  # 尺寸照常核对通过
    warn = next(l for l in checks.splitlines() if l.startswith("WARN 封面大小"))
    assert "> 5MB（bilibili 上传上限" in warn
    assert "FAIL" not in checks  # 体积超限只告警不否决


def test_pack_bilibili_small_cover_size_ok(tmp_path: Path) -> None:
    """bilibili 小体积封面：大小项产出 ok 行、无 WARN（纯色 PNG 远小于上限）。"""
    _, checks = _f42_pack(
        tmp_path, "bilibili", cover_img=lambda p: _png(p, (1600, 1000))
    )
    assert "ok 封面大小:" in checks and "≤ 5MB" in checks
    assert "WARN 封面大小" not in checks


# ── C6（第十四期顺手批）：cover --platform 选项引 platform_choices 单一事实源 ──


def test_cover_platform_choices_no_literal_copy() -> None:
    """C6：cover_cmds 不再持有字面平台副本——选项 choices 与
    cover_service.platform_choices()（PLIMITS 派生）同源；帮助文本不带字面
    尺寸（规格数字只活在 profiles 表与 platform list）。"""
    import click

    from musespiral.commands import cover_cmds as cover_cmds_mod

    src = Path(cover_cmds_mod.__file__).read_text(encoding="utf-8")
    assert "_PLATFORM_CHOICES" not in src  # 字面副本已消
    assert "click.Choice(platform_choices())" in src  # 选项引单一事实源

    opt = next(p for p in cover_cmd.params if p.name == "platform")
    assert tuple(opt.type.choices) == cover_service.platform_choices()
    assert "zhihu" not in opt.type.choices  # 无封面规格平台不进选项（PLIMITS 派生口径）

    help_text = cover_cmd.get_help(click.Context(cover_cmd))
    assert "900×383" not in help_text  # 帮助文本去字面尺寸
    assert "规格以 platform list 为准" in help_text
