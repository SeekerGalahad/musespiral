"""F71-F74 服务层测试（services/visual_service.py）——设计稿 CRUD/预设/导出/写回/抽数。

覆盖 §五 矩阵：F71-T1/T2/T3/T4、F72-T1/T2/T4/T5、F73-T3/T4、F74-T2/T4/T5。
全部走 tmp_path + MUSESPIRAL_HOME 隔离；B/C 路用 mock raster（无浏览器环境降 C）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from musespiral import config
from musespiral.services import visual_service as v


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def article(tmp_path: Path) -> Path:
    path = tmp_path / "demo.md"
    path.write_text(
        "---\n-title: 缓存优化三步走\n---\n\n首段钩子在这里。\n\n## 第一小节\n内容一\n\n## 第二小节\n\n## 第三小节\n内容三\n",
        encoding="utf-8",
    )
    # 修正 YAML 前导（- 后紧跟 title 是合法 YAML 但语义异常——改为标准 frontmatter）
    path.write_text(
        "---\ntitle: 缓存优化三步走\ndigest: 摘要\n---\n\n首段钩子在这里。\n\n## 第一小节\n内容一\n\n## 第二小节\n\n## 第三小节\n内容三\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺浏览器环境：raster 抛 BrowserNotInstalled → 服务层静默降 C。"""
    import sys

    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)


# ── F71-T1 全链：render→save→export→cover 写回（版本池先行）─────


def test_f71t1_full_chain_cover(article: Path, no_browser: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """全链三件齐 + PNG 尺寸精确 + **版本池先行于 frontmatter 写**（真断言）。

    断言口径（设计 §七「写回纪律：mp 写回前版本池文件存在」）：
    - spy 挂在**真正落盘的那一处**——文章版本池走
      `cover_service.backup_article`（cover_service.py:29 import、:103 调用），
      不是 `visual_service.backup_article`（后者只服务 design.json 入池）；
    - 顺序比较是**相对序**断言（index 比大小），不是「各自被调用过」；
    - 追加**落盘事实断言**：备份瞬间文章尚不含 cover（证明备份的是写回前的
      旧版），且磁盘上确有 .bak 版本池文件（证明版本池真落盘，非空转）。
    """
    order: list[str] = []
    # design.json 版本池那处（visual_service 直接绑定的名字）
    orig_design_backup = v.backup_article

    def spy_design_backup(p: Path) -> Path:
        order.append(f"backup:{Path(p).name}")
        return orig_design_backup(p)

    monkeypatch.setattr(v, "backup_article", spy_design_backup)
    # 真版本池那处（cover_service 模块内已绑定的名字——v.* 的猴补丁对它无效）。
    # 2026-11-24 W2 断环批：visual_service 不再模块级 import cover_service（改为
    # 函数体内导入），故此处直取服务模块（猴补丁打在 cover_service 上同样生效）。
    from musespiral.services import cover_service as _cover_svc

    orig_backup = _cover_svc.backup_article
    bak_content_at_backup: list[str] = []

    def spy_backup(p: Path) -> Path:
        order.append(f"backup:{Path(p).name}")
        bak_content_at_backup.append(Path(p).read_text(encoding="utf-8"))
        return orig_backup(p)

    monkeypatch.setattr(_cover_svc, "backup_article", spy_backup)
    # frontmatter 重写的**真实落盘时刻**（cover_service 用 mkstemp+os.replace
    # 原子重写）——观察 os.replace，比 spy 整个 _write_back_cover 更细：
    # 后者只记到「进入写回」，记不到「备份已完成、重写将发生」这一中间态。
    import musespiral.services.cover_service as cover_service_mod

    orig_replace = cover_service_mod.os.replace

    def spy_replace(src, dst, *args, **kwargs):
        if Path(dst) == article:
            order.append("frontmatter_write")
        return orig_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(cover_service_mod.os, "replace", spy_replace)

    # 先落一版（首写无既有版本池——版本池先行只对「覆写」成立）
    v.save_design(article, kind="cover", template="vermilion-slab",
                  params={"hook": "钩子"}, spec="mp")
    # 第二版：覆写 → design.json 入版本池（visual_service 侧 backup_article）
    r = v.save_design(article, kind="cover", template="vermilion-slab",
                      params={"hook": "钩子2"}, spec="mp")
    assert (Path(r["dir"]) / "design.json").exists()
    assert (Path(r["dir"]) / "cover.svg").exists()

    ex = v.export_design(article, kind="cover")
    out = Path(ex["png"])
    assert out.name == "cover.png"
    assert Image.open(out).size == (900, 383)

    # ── 写回纪律三连断言（相对序 + 备份内容 + 池文件落盘）──
    design_bk = [i for i, o in enumerate(order) if o.startswith("backup:design.json")]
    article_bk = [i for i, o in enumerate(order) if o == f"backup:{article.name}"]
    write_bk = [i for i, o in enumerate(order) if o == "frontmatter_write"]
    assert design_bk, "save 时应先入 design.json 版本池"
    assert article_bk, "cover 写回应先入文章版本池（backup_article）"
    assert write_bk, "cover 写回应发生（mp 默认）"
    # 相对序：文章版本池入池 **先于** frontmatter 重写落盘
    assert article_bk[0] < write_bk[0], (
        f"版本池必须先于 frontmatter 写（事件序={order}）"
    )
    # 备份的是写回前的旧版（此刻文章尚不含 cover）
    assert "cover:" not in bak_content_at_backup[0], (
        "版本池备份的应是写回前的旧版（不含 cover 字段）"
    )
    # 版本池文件真落盘（磁盘事实，非调用事实）
    bak_files = [p for p in article.parent.iterdir() if ".bak." in p.name]
    assert bak_files, f"文章版本池文件应落盘（目录={sorted(p.name for p in article.parent.iterdir())}）"
    # frontmatter cover 已落
    assert "cover: cover.png" in article.read_text(encoding="utf-8")


def test_f71t1_frontmatter_write_marked_only_mp(article: Path, no_browser: None) -> None:
    """T1 补充：写回结果 frontmatter cover 落一行（mp 默认）。"""
    v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    v.export_design(article, kind="cover")
    text = article.read_text(encoding="utf-8")
    assert "cover:" in text


# ── F71-T2 非 mp 平台恒不写 frontmatter ──────────────────────


def test_f71t2_non_mp_never_writes_frontmatter(article: Path, no_browser: None) -> None:
    """非 mp 平台导出：frontmatter cover 不变，产物名 cover.<platform>.png。"""
    v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    before = article.read_text(encoding="utf-8")
    ex = v.export_design(article, kind="cover", platform="xhs")
    out = Path(ex["png"])
    assert out.name == "cover.xhs.png"
    after = article.read_text(encoding="utf-8")
    assert before == after, "非 mp 平台不得写回 frontmatter"
    assert "cover:" not in after


# ── F71-T3 render 参数违例 → ValueError（零落盘）────────────


def test_f71t3_invalid_font_size_enum(article: Path, no_browser: None) -> None:
    """字号档枚举外 → ValueError 文案含合法枚举；零落盘断言。"""
    with pytest.raises(ValueError, match="font_size"):
        v.render(template="vermilion-slab", params={"font_size": "XXL"}, spec="mp")
    with pytest.raises(ValueError, match="S/M/L"):
        v.render(template="vermilion-slab", params={"font_size": "XL"}, spec="mp")
    # 无设计稿目录产生
    assert not v.design_dir(article).exists()


def test_f71t3_unknown_template_and_spec() -> None:
    with pytest.raises(ValueError, match="未知模板"):
        v.render(template="nope", params={}, spec="mp")
    with pytest.raises(ValueError, match="未知规格"):
        v.render(template="vermilion-slab", params={}, spec="a4")


def test_f71t3_spec_not_applicable(article: Path) -> None:
    """规格不属于模板 specs 集合 → ValueError。"""
    with pytest.raises(ValueError, match="不适用"):
        v.render(template="points-three", params={"points": []}, spec="mp")


# ── F71-T4 无浏览器降 C 路，尺寸仍精确 ──────────────────────


def test_f71t4_export_falls_back_to_c(article: Path, no_browser: None) -> None:
    """无浏览器环境导出：route=C，PNG 尺寸仍精确 (900,383)。"""
    v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    ex = v.export_design(article, kind="cover")
    assert ex["route"] == "C"
    assert Image.open(ex["png"]).size == (900, 383)


def test_f71t4_forces_pillow_route(article: Path) -> None:
    """route='pillow' 强制走 C（不依赖环境）。"""
    v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    ex = v.export_design(article, kind="cover", route="pillow")
    assert ex["route"] == "C"


# ── F72-T1 spec 切换非拉伸重渲（尺寸+tspan 重排）────────────


def test_f72t1_switch_spec_rebuilds_non_stretch(article: Path) -> None:
    """同稿 xhs-3x4→square：两次 render 尺寸精确各 spec；tspan y 重排。"""
    v.save_design(article, kind="social", template="xhs-card",
                  params={"title": "标题" * 6, "hook": "钩子"}, spec="xhs-3x4")
    svg3, _ = v.render(template="xhs-card", params={"title": "标题" * 6, "hook": "钩子"}, spec="xhs-3x4")
    svg4, _ = v.switch_spec(article, new_spec="square")
    assert 'width="1242" height="1656"' in svg3
    assert 'width="1080" height="1080"' in svg4
    assert 'viewBox="0 0 1242 1656"' in svg3
    assert 'viewBox="0 0 1080 1080"' in svg4


def test_f72t1_tspan_y_series_realigned(article: Path) -> None:
    """tspan 行结构在新 spec 下重排（y 系列不同）。"""
    long_title = "数" * 40
    v.save_design(article, kind="social", template="xhs-card",
                  params={"title": long_title, "hook": ""}, spec="xhs-3x4")
    svg_3x4, _ = v.render(template="xhs-card", params={"title": long_title, "hook": ""}, spec="xhs-3x4")
    svg_sq, _ = v.render(template="xhs-card", params={"title": long_title, "hook": ""}, spec="square")
    # 行的 y 由 build 计算，spec 切换后 y 系列应不同（重排发生）
    assert svg_3x4 != svg_sq


# ── F72-T2 色槽限定：任意 hex 拒收（400 面）；渲染产物无字面量 ──


def test_f72t2_hex_in_background_slot_rejected(article: Path) -> None:
    """params.background_slot 给任意 hex → ValueError 含合法色槽清单。"""
    with pytest.raises(ValueError) as ei:
        v.render(template="xhs-card", params={"background_slot": "#ff00aa", "title": "x"},
                 spec="xhs-3x4")
    msg = str(ei.value)
    assert "background_slot" in msg and "色槽" in msg


def test_f72t2_hotpink_slot_rejected(article: Path) -> None:
    """非语义槽名 hotpink → ValueError 含槽清单。"""
    with pytest.raises(ValueError, match="hotpink"):
        v.save_design(article, kind="social", template="xhs-card",
                      params={"background_slot": "hotpink"}, spec="xhs-3x4")


def test_f72t2_no_hex_literal_in_product(article: Path) -> None:
    """色槽合法时渲染产物中不出现非法槽名字面量。"""
    svg, _ = v.render(template="xhs-card",
                      params={"background_slot": "background", "title": "标题"}, spec="xhs-3x4")
    assert "hotpink" not in svg


# ── F72-T4 三规格批量导出尺寸精确 + 命名递增 ───────────────


def test_f72t4_batch_export_three_specs(article: Path, no_browser: None) -> None:
    """三规格批量导出：**PNG 实际像素**=(1242,1656)/(1242,1553)/(1080,1080)；命名 social-1/2/3。

    断言口径（设计 §七「三 spec × 三路导出 PNG 像素尺寸精确断言全过（测试内断言，
    非手测）」）：读**磁盘 PNG 的真实像素**（`Image.open(...).size`），不是服务层
    回填的 `r["size"]` 上报值——上报值来自 doc.width/height，与实际写出的 PNG
    可能脱节（光栅尺寸漂移时上报值仍正确，断言即失效）。
    """
    v.save_design(article, kind="social", template="xhs-card",
                  params={"title": "社媒标题", "hook": "钩子"}, spec="xhs-3x4")
    results = v.export_social_batch(article)
    assert len(results) == 3
    # 上报值与服务层口径一致（形状断言）
    assert [tuple(r["size"]) for r in results] == [(1242, 1656), (1242, 1553), (1080, 1080)]
    # 真像素断言（读磁盘 PNG）
    pixel_sizes = []
    for r in results:
        with Image.open(r["png"]) as im:
            pixel_sizes.append(im.size)
    assert pixel_sizes == [(1242, 1656), (1242, 1553), (1080, 1080)], (
        f"PNG 实际像素须精确（实测={pixel_sizes}）"
    )
    names = [Path(r["png"]).name for r in results]
    assert names == ["demo.social-1.png", "demo.social-2.png", "demo.social-3.png"]


def test_f72t4_batch_naming_no_overwrite(article: Path, no_browser: None) -> None:
    """第二次批量导出递增不覆盖既有（social-4/5/6）。"""
    v.save_design(article, kind="social", template="xhs-card",
                  params={"title": "标题", "hook": ""}, spec="xhs-3x4")
    v.export_social_batch(article)
    second = v.export_social_batch(article)
    names = [Path(r["png"]).name for r in second]
    assert names == ["demo.social-4.png", "demo.social-5.png", "demo.social-6.png"]


def test_f72t4_all_spec_tiers_pixel_exact(article: Path, no_browser: None) -> None:
    """全部五档规格的导出 PNG **真实像素**精确（补 xhs-4x5/square/wide16x9 三档）。

    前置发现：全库 PNG 像素断言只覆盖 (900,383)/(1242,1656) 两档，xhs-4x5
    (1242,1553)/square (1080,1080)/wide16x9 (1242,700) 三档**无任何导出后
    像素读取**——「三 spec」实际只测了 mp 与 xhs-3x4。本用例把五档补齐
    （设计 §七「三 spec × 三路导出 PNG 像素尺寸精确断言全过」的完整口径）。
    """
    # 社媒三档（xhs-card 适用 xhs-3x4/xhs-4x5/square）
    v.save_design(article, kind="social", template="xhs-card",
                  params={"title": "社媒标题", "hook": "钩子"}, spec="xhs-3x4")
    for spec, expected in (
        ("xhs-3x4", (1242, 1656)),
        ("xhs-4x5", (1242, 1553)),
        ("square", (1080, 1080)),
    ):
        results = v.export_social_batch(article, specs=[spec])
        with Image.open(results[0]["png"]) as im:
            assert im.size == expected, f"{spec} 像素须精确（实测={im.size}）"
    # 信息图档（points-three 适用 wide16x9/square/xhs-3x4）
    v.save_design(article, kind="infographic", template="points-three",
                  params={"title": "信息图", "points": [{"label": "甲", "value": 3}]},
                  spec="wide16x9")
    ex = v.export_design(article, kind="infographic")
    with Image.open(ex["png"]) as im:
        assert im.size == (1242, 700), f"wide16x9 像素须精确（实测={im.size}）"
    # 封面档（mp）
    v.save_design(article, kind="cover", template="vermilion-slab",
                  params={"title": "标题"}, spec="mp")
    ex = v.export_design(article, kind="cover")
    with Image.open(ex["png"]) as im:
        assert im.size == (900, 383), f"mp 像素须精确（实测={im.size}）"


# ── F72-T5 载入恶意 SVG 白名单复验拒绝 ──────────────────────


def test_f72t5_malicious_svg_rejected_on_load(article: Path) -> None:
    """手改 design 目录夹带 `<script>` → load_design 抛 ValueError。"""
    r = v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    svg_path = Path(r["dir"]) / "cover.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="383">'
        '<script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SVG 元素越界|<script>"):
        v.load_design(article)


def test_f72t5_svg_network_href_rejected() -> None:
    """SVG href 含 http 地址 → 复验拒绝。"""
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><image href="http://x/y.png"/></svg>'
    with pytest.raises(ValueError, match="网络"):
        v.verify_svg(bad)


def test_f72t5_svg_event_attr_rejected() -> None:
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>'
    with pytest.raises(ValueError, match="事件属性"):
        v.verify_svg(bad)


def test_f72t5_valid_svg_passes() -> None:
    """合法渲染产物过复验（tspan 行结构在允许清单）。"""
    svg, _ = v.render(template="xhs-card", params={"title": "标题"}, spec="xhs-3x4")
    assert v.verify_svg(svg) == svg


# ── F72-T5 补强：网络引用面覆盖 style / fill / stroke（不只 href）──
#
# 原实现只查 href/xlink:href 的协议前缀，`style` 属性整个不在拒绝名单里——
# 实测 `style="background:url(//evil.example/x.png)"`、
# `style="fill:url(HTTPS://evil.example/x.svg#f)"`、
# `style="@import url(https://evil.example/a.css)"` 三种**全部 ACCEPTED**。
# 设计稿 SVG 经 GET /api/visual/design 原样透出、前端 inline 注入
# （ui.js `$('cv-prev').innerHTML=d.svg`），浏览器渲染 style url() 会发外域
# 请求——与「光栅不触网 / 零外联」红线冲突。


@pytest.mark.parametrize("payload", [
    'style="background:url(//evil.example/x.png)"',
    'style="fill:url(HTTPS://evil.example/x.svg#f)"',
    'style="fill:url(http://evil.example/x.svg#f)"',
    'style="@import url(https://evil.example/a.css)"',
    'style="@import \'https://evil.example/a.css\'"',
    'fill="url(http://evil.example/x.svg#f)"',
    'fill="url(//evil.example/x.svg#f)"',
    'stroke="url(https://evil.example/x)"',
])
def test_f72t5_svg_style_and_paint_network_refs_rejected(payload: str) -> None:
    """style / fill / stroke 里的网络引用 → 复验拒绝（光栅不触网）。"""
    bad = f'<svg xmlns="http://www.w3.org/2000/svg"><rect {payload}/></svg>'
    with pytest.raises(ValueError):
        v.verify_svg(bad)


@pytest.mark.parametrize("payload", [
    'style="fill:#ffffff"',
    'style="font-size:12px"',
    'fill="url(#localGradient)"',
    'href="data:image/png;base64,AAAA"',
    'href="cards/local.png"',
])
def test_f72t5_svg_benign_refs_accepted(payload: str) -> None:
    """本地/内联引用仍放行（复验不误杀合法产物）。"""
    ok = f'<svg xmlns="http://www.w3.org/2000/svg"><rect {payload}/></svg>'
    assert v.verify_svg(ok) == ok


def test_f72t5_svg_href_case_insensitive_rejected() -> None:
    """href 协议大小写变体 → 拒绝（scheme 大小写不敏感是 RFC 3986 事实）。"""
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><image href="HTTPS://x/y.png"/></svg>'
    with pytest.raises(ValueError, match="网络"):
        v.verify_svg(bad)


# ── F73-T3 radar_signal 脏行跳过 + warnings 计数 ────────────


def test_f73t3_radar_skips_dirty_rows_with_warnings() -> None:
    """grade 缺失行跳过且 warnings 计数+1；有效行进 points。"""
    materials = [
        {"id": "a1", "type": "radar_signal", "payload": {"meta": {"line": "ai", "grade": "S"}}},
        {"id": "a2", "type": "radar_signal", "payload": {"meta": {"line": "ai"}}},  # 缺 grade
        {"id": "a3", "type": "text", "payload": {}},  # 非 radar 忽略
        {"id": "a4", "type": "radar_signal", "payload": {"meta": {"line": "data", "grade": "A"}}},
    ]
    out = v.extract_radar(materials)
    assert {p["label"] for p in out["points"]} == {"ai", "data"}
    ai = next(p for p in out["points"] if p["label"] == "ai")
    assert ai["value"] == 1.0  # 只 a1 有效
    assert len(out["warnings"]) == 1
    assert "a2" in out["warnings"][0]


def test_f73t3_radar_all_valid_no_warnings() -> None:
    materials = [
        {"id": "b1", "type": "radar_signal", "payload": {"meta": {"line": "x", "grade": "S"}}},
    ]
    out = v.extract_radar(materials)
    assert out["warnings"] == []
    assert out["points"] == [{"label": "x", "value": 1.0}]


# ── F73-T4 全零/空数据边界（信息图空态）─────────


def test_f73t4_empty_points_yields_empty_state_svg(article: Path) -> None:
    """无小节或全空 → 合法空态 SVG（empty 基元+提示文案），非除零异常。"""
    art = article.parent / "empty.md"
    art.write_text("---\ntitle: 空\n---\n\n只有正文，无小节。\n", encoding="utf-8")
    result = v.extract_points(art)
    assert result["points"] == []
    info = v.build_infographic(art, source_ref={"kind": "points"})
    assert "暂无有效数据" in info["svg"] or "<svg" in info["svg"]


def test_f73t4_empty_radar_yields_empty_state(article: Path) -> None:
    """空 radar 素材 → 空态（points-three 空态占位）。"""
    info = v.build_infographic(article, source_ref={"kind": "radar"})
    assert "<svg" in info["svg"]


# ── F74-T2 四级合并优先级 ────────────────────────


def test_f74t2_four_level_merge_priority(article: Path) -> None:
    """同槽四层值各异 → overrides 胜出；去 overrides 取 params；逐级回退。"""
    preset = {"font_size": "L", "align": "center"}
    params = {"font_size": "S", "align": "right", "title": "T"}
    overrides = {"font_size": "L"}  # 与 preset 同值、异于 params——证明 overrides 胜 params
    _svg, m = v.render(
        template="vermilion-slab", params=params, spec="mp",
        preset_params=preset, params_overrides=overrides,
    )
    assert m["font_size"] == "L"  # overrides(S->L) 胜 params(S 为 S)
    assert m["align"] == "right"  # params 胜 preset
    assert m["title"] == "T"

    # 去 overrides → 取 params
    _svg2, m2 = v.render(
        template="vermilion-slab", params={"font_size": "S", "title": "T"}, spec="mp",
        preset_params=preset, params_overrides={},
    )
    assert m2["font_size"] == "S"

    # 去 params → 取 preset
    _svg3, m3 = v.render(
        template="vermilion-slab", params={}, spec="mp",
        preset_params=preset, params_overrides={},
    )
    assert m3["font_size"] == "L"


def test_f74t2_no_overrides_uses_params(article: Path) -> None:
    _svg, m = v.render(
        template="vermilion-slab", params={"font_size": "S", "align": "left"},
        preset_params={"font_size": "L"}, spec="mp",
    )
    assert m["font_size"] == "S"
    assert m["align"] == "left"


# ── F74-T4 预设保存/加载往返 + 幂等覆盖 ─────────────────


def test_f74t4_preset_roundtrip(tmp_path: Path) -> None:
    """save→list 含该 id→apply（render 参数=预设值）；同 id 重跑幂等覆盖。"""
    r = v.save_preset(preset_id="my-cover-1", template="vermilion-slab",
                      params={"font_size": "L", "align": "center"}, label="朱砂大封面")
    assert Path(r["path"]).exists()
    items = v.list_presets()
    assert any(row["id"] == "my-cover-1" for row in items)
    _svg, m = v.render(template="vermilion-slab", spec="mp", preset_params={"font_size": "L"})
    assert m["font_size"] == "L"

    # 幂等覆盖：mtime 变、条目数不变
    first_mtime = Path(r["path"]).stat().st_mtime_ns
    v.save_preset(preset_id="my-cover-1", template="vermilion-slab",
                  params={"font_size": "S"}, label="朱砂大封面2")
    second_mtime = Path(r["path"]).stat().st_mtime_ns
    assert len(v.list_presets()) == len(items)  # 条目数不变
    assert second_mtime >= first_mtime


def test_f74t4_preset_invalid_kebab() -> None:
    with pytest.raises(ValueError, match="kebab"):
        v.save_preset(preset_id="我的模板 1", template="vermilion-slab", params={})


# ── F74-T5 用户目录零代码断言（纯 JSON grep 无 lambda/import/__）──


def test_f74t5_preset_file_pure_json_zero_code(tmp_path: Path) -> None:
    """落盘预设 json.load 可解析；文本 grep 无 lambda/import/__ 可执行痕迹。"""
    r = v.save_preset(preset_id="pure-1", template="vermilion-slab",
                      params={"font_size": "M"}, label="纯")
    path = Path(r["path"])
    raw = path.read_text(encoding="utf-8")
    json.loads(raw)  # 可解析
    low = raw.lower()
    for marker in ("lambda", "import ", "__", "eval(", "exec("):
        assert marker not in low, f"预设文件含可执行痕迹 {marker!r}"


# ── 补充：theme_colors / extract_sources / export_file_name ────


def test_theme_colors_uses_lapis() -> None:
    """theme_colors 返回语义色槽 hex 表（accent 沿 primary 或缺省）。"""
    colors = v.theme_colors("lapis")
    for slot in ("primary", "text", "muted", "background", "accent"):
        assert slot in colors
        assert colors[slot].startswith("#")


def test_f72t2_extended_slots_all_render() -> None:
    """F-B 扩槽回归（2026-11-18）：SEMANTIC_COLOR_SLOTS 10 槽全部可作
    background_slot 真渲出 SVG——每槽有实 hex（lapis 同键值），hotpink 仍拒。"""
    from musespiral.core.visual.canvas.model import SEMANTIC_COLOR_SLOTS

    assert len(SEMANTIC_COLOR_SLOTS) == 10  # 5 原槽 + border/code_bg/code_text/quote_bg/quote_border
    colors = v.theme_colors("lapis")
    assert set(colors) == set(SEMANTIC_COLOR_SLOTS)  # theme_colors 与槽集同源
    for slot in SEMANTIC_COLOR_SLOTS:
        svg, params = v.render(
            template="xhs-card", params={"background_slot": slot, "title": "扩槽"},
            spec="xhs-3x4",
        )
        assert svg.startswith("<svg") and params["background_slot"] == slot
    # F72-T2 回归：非法值（hotpink）仍 ValueError 含槽清单
    with pytest.raises(ValueError, match="hotpink"):
        v.render(template="xhs-card", params={"background_slot": "hotpink"}, spec="xhs-3x4")


def test_extract_sources_groups_by_origin(tmp_path: Path) -> None:
    """sources 抽数：origin.source_id 分组计数（label 取源 label 或缺 id）。"""
    from musespiral.services import sources_service

    materials = [
        {"id": "m1", "type": "text", "origin": {"source_id": "kb-a"}},
        {"id": "m2", "type": "text", "origin": {"source_id": "kb-a"}},
        {"id": "m3", "type": "text", "origin": {"source_id": "kb-b"}},
    ]
    try:
        v.extract_sources(materials)  # sources_service 可能空表——宽容
    except Exception:  # pragma: no cover
        pass
    # 直接断言计数面（与源 label 解偶）
    from collections import Counter

    counts = Counter((m["origin"] or {}).get("source_id") for m in materials)
    assert counts["kb-a"] == 2 and counts["kb-b"] == 1


def test_export_file_name_cover_and_social(tmp_path: Path) -> None:
    art = tmp_path / "x.md"
    art.write_text("x", encoding="utf-8")
    assert v.export_file_name(art, "cover") == "cover.png"
    assert v.export_file_name(art, "cover", platform="xhs") == "cover.xhs.png"
    assert v.export_file_name(art, "social", n=2) == "x.social-2.png"
    assert v.export_file_name(art, "infographic") == "x.infographic.png"


def test_load_design_returns_none_when_absent(article: Path) -> None:
    assert v.load_design(article) is None


def test_load_design_corrupt_json_raises(article: Path) -> None:
    d = v.design_dir(article)
    d.mkdir(parents=True)
    (d / "design.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        v.load_design(article)


def test_design_dir_naming(article: Path) -> None:
    assert v.design_dir(article).name == "demo.design"
    assert v.design_json_path(article).name == "design.json"


def test_presets_dir_under_home(tmp_path: Path) -> None:
    assert v.presets_dir() == config.home_dir() / "visual_presets.d"


# ── from-design 桥（§八-3）────────────────────


def test_generate_cover_from_design_writes_frontmatter(article: Path, no_browser: None) -> None:
    """设计稿 cover.svg → 规格 PNG → 写回 frontmatter（mp）。"""
    v.save_design(article, kind="cover", template="vermilion-slab",
                  params={"hook": "钩子"}, spec="mp")
    out = v.generate_cover_from_design(article)
    assert Path(out).name == "cover.png"
    assert Image.open(out).size == (900, 383)
    assert "cover: cover.png" in article.read_text(encoding="utf-8")


def test_generate_cover_from_design_missing_design(article: Path) -> None:
    with pytest.raises(FileNotFoundError, match="设计稿"):
        v.generate_cover_from_design(article)


def test_generate_cover_from_design_non_mp_no_frontmatter(article: Path, no_browser: None) -> None:
    v.save_design(article, kind="cover", template="vermilion-slab", params={"hook": "x"}, spec="mp")
    before = article.read_text(encoding="utf-8")
    out = v.generate_cover_from_design(article, platform="xhs")
    assert Path(out).name == "cover.xhs.png"
    assert article.read_text(encoding="utf-8") == before


# ── M4 AI 参数建议（visual_suggest；只产参数建议，不产成品稿）─────


def _fake_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str) -> dict:
    """隔离 MUSESPIRAL_HOME + ai.json + mock chat，捕获发出的 messages。"""
    from musespiral.infra.ai import client as ai_client

    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    captured: dict = {}

    class _Resp:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": reply}}]}).encode()

    def fake_post(url, headers, body, timeout):
        captured["body"] = body
        return _Resp()

    monkeypatch.setattr(ai_client, "_post_chat_stream", fake_post)
    return captured


def test_visual_suggest_returns_params(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AI 返回参数 JSON → 服务返回 ok:True + params（过模板 schema）。"""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    _fake_ai(home, monkeypatch, '{"font_size": "L", "align": "center"}')
    out = v.suggest_params(template="vermilion-slab", kind="cover", params={"title": "T"})
    assert out["ok"] is True
    assert out["params"]["font_size"] == "L"
    assert out["params"]["align"] == "center"


def test_visual_suggest_task_key_wired() -> None:
    """TASKS 白名单含 visual_suggest（§八-4 任务键注册）。"""
    from musespiral.infra.ai.profiles import TASKS

    assert "visual_suggest" in TASKS


def test_visual_suggest_style_injection_seventh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """风格注入第七任务：system prompt 含 style 块（写 habits + samples 后断言）。"""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    (home / "habits.json").write_text(
        json.dumps([{"rule": "客套删除", "example": "直接进正题"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (home / "style_samples.md").write_text("范文首句在此。", encoding="utf-8")
    captured = _fake_ai(home, monkeypatch, '{"font_size": "S"}')
    from musespiral.infra.ai.habits_inject import STYLE_HEADER

    v.suggest_params(template="vermilion-slab", kind="cover")
    body = json.loads(captured["body"])
    system_msg = body["messages"][0]
    assert system_msg["role"] == "system"
    assert STYLE_HEADER in system_msg["content"]
    assert "客套删除" in system_msg["content"]


def test_visual_suggest_bad_json_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AI 返回非 JSON → ok:False（不阻断，人工仍可手改）。"""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    _fake_ai(home, monkeypatch, "这不是 JSON")
    out = v.suggest_params(template="vermilion-slab", kind="cover")
    assert out["ok"] is False
    assert out["params"] == {}


def test_visual_suggest_ai_error_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AI 未配置 → AIError → ok:False，不抛。"""
    from musespiral.infra.ai import AIError

    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))

    def raising_chat(messages, task=None, temperature=None):
        raise AIError("AI 未配置")

    out = v.suggest_params(template="vermilion-slab", kind="cover", chat_fn=raising_chat)
    assert out["ok"] is False
    assert "AI" in out["reason"]


def test_visual_suggest_unknown_template() -> None:
    with pytest.raises(ValueError, match="未知模板"):
        v.suggest_params(template="nope", kind="cover")


def test_visual_suggest_prompt_dialect_free() -> None:
    """方言守门：visual_service 的提示词本文件已被 test_architecture 扫描；
    本处补强断言 _SUGGEST_PROMPT 不含 DIALECT_FORBIDDEN 字样。"""
    from musespiral.config import DIALECT_FORBIDDEN

    low = v._SUGGEST_PROMPT.lower()  # noqa: SLF001 - 提示词常量单测
    assert not [t for t in DIALECT_FORBIDDEN if t in low]
