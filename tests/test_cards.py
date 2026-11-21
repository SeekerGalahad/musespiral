"""F22 小红书图文卡 — 模板层全测 + 光栅层结构测试（§十八 F22 验收口径）。

切卡规则逐条断言（变体路径钩子/分段/标签；文章路径 h2/合并/补足；卡数边界
3 与 10）+ 色板注入 + ratio 两规格 + pack 集成在 test_pack.py。
工作流 venv 不装 playwright（§十八 实施与测试约定）：raster 只测
BrowserNotInstalled 结构路径（monkeypatch 注入 None 复现缺依赖分支）与
--html-only 路径；真截图冒烟归主会话。全部 tmp_path 隔离，无网络。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.core.visual.cards import build_cards_html, raster, templates
from musespiral.core.visual.cards import compose
from musespiral.commands.cards_cmds import cards
from musespiral.browser.session import BrowserNotInstalled


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _variant_text(paragraphs: list[str], tags: str = "小红书, 笔记") -> str:
    body = "\n\n".join(paragraphs)
    return f"---\nplatform: xhs\ntitle: 变体标题\ntags: [{tags}]\n---\n\n{body}\n"


def _count(html: str, cls: str) -> int:
    return html.count(f'musespiral-card--{cls}')


# ── 变体路径：钩子 / 分段 / 标签 ─────────────────────────────


def test_variant_hook_cover_and_pair_cards(tmp_path: Path) -> None:
    """① 变体路径：首段钩子句→封面副标；其余每 2 段一内容卡；tags→结尾卡。"""
    article = _write(tmp_path, "demo.md", "---\ntitle: 文章标题\n---\n\n正文。\n")
    variant = _write(
        tmp_path,
        "demo.xhs.md",
        _variant_text(
            [
                "三步搞定缓存优化，页面快到飞起来。后面是展开。",
                "第一步，先量再猜。把指标摆在桌面上。",
                "第二步，砍掉最大的慢查询。",
                "第三步，加上缓存并验证收益。",
                "收尾：监控报警别忘配。",
            ]
        ),
    )

    html, n = build_cards_html(article, variant=variant)

    # 卡型结构：封面 1 + 内容 3（首段余文并入卡流后 5 段 2-2-1 成卡）+ 结尾 1
    # （复核 M2 修复：首段「后面是展开。」余文不再静默丢弃，见专属回归测试）
    assert n == 5
    assert _count(html, "cover") == 1
    assert _count(html, "content") == 3
    assert _count(html, "ending") == 1
    # 首段钩子句（首个完整句）作封面副标；余文并入内容卡流
    assert "三步搞定缓存优化，页面快到飞起来。" in html
    assert "后面是展开。" in html
    # 卡标题取段首句 ≤12 字
    assert "第一步，先量再猜。" in html
    assert "第三步，加上缓存并验证收益。" in html
    # 结尾卡话题来自 variant frontmatter tags
    assert "#小红书" in html and "#笔记" in html


def test_variant_first_paragraph_rest_not_dropped(tmp_path: Path) -> None:
    """回归（复核 M2）：变体首段钩子句之外的余文并入内容卡，不静默丢失。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    variant = _write(
        tmp_path,
        "a.xhs.md",
        _variant_text(
            [
                "三步搞定，快到飞起。这是首段余下部分，检查是否丢失。",
                "第二段内容甲。",
                "第三段内容乙。",
            ]
        ),
    )

    html, n = build_cards_html(article, variant=variant)

    assert "这是首段余下部分，检查是否丢失。" in html  # 余文必须在场
    assert "三步搞定，快到飞起。" in html  # 钩子仍在封面副标
    assert "第二段内容甲。" in html and "第三段内容乙。" in html
    assert n >= 3


def test_variant_long_paragraph_own_card(tmp_path: Path) -> None:
    """「每 1-2 段」的 1 段侧：超长段（>120 字）独占一卡。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    long_para = "这是一段很长的段落。" * 13  # 130 字 > 120
    variant = _write(
        tmp_path,
        "a.xhs.md",
        _variant_text(["钩子句在这里。", "短段一。", long_para, "短段二。"]),
    )

    html, n = build_cards_html(article, variant=variant)

    # 分卡：[钩子→封面] + 短段一+超长段？否——超长段独占：短段一单独一卡、
    # 超长段一卡、短段二一卡 → 3 内容卡 + 封面 + 结尾 = 5
    assert n == 5
    assert _count(html, "content") == 3
    assert long_para in html


def test_variant_tags_over_six_truncated(tmp_path: Path) -> None:
    """标签 >6 截前 6。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    variant = _write(
        tmp_path,
        "a.xhs.md",
        _variant_text(["钩子句。", "内容段。"], tags="t1, t2, t3, t4, t5, t6, t7, t8"),
    )

    html, n = build_cards_html(article, variant=variant)

    assert n == 3
    for t in ("t1", "t2", "t3", "t4", "t5", "t6"):
        assert f"#{t}" in html
    assert "#t7" not in html and "#t8" not in html


def test_variant_topic_line_not_a_card(tmp_path: Path) -> None:
    """adapt xhs 产物正文尾部的话题行不成内容卡。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    variant = _write(
        tmp_path,
        "a.xhs.md",
        _variant_text(["钩子句。", "内容段一。", "内容段二。", "#性能优化 #缓存"]),
    )

    html, n = build_cards_html(article, variant=variant)

    # 钩子 + 2 段一卡 + 话题行跳过 → 封面+1 内容+结尾 = 3
    assert n == 3
    assert _count(html, "content") == 1


def test_variant_missing_rejected(tmp_path: Path) -> None:
    """显式传入的 --variant 不存在：拒绝静默回退（与 pack 同语义）。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    with pytest.raises(FileNotFoundError, match="拒绝静默回退"):
        build_cards_html(article, variant=tmp_path / "nope.xhs.md")


# ── 文章路径：h2 切节 / >6 节合并 / 首段副标 / 补足 ──────────


def test_article_h2_sections_each_card(tmp_path: Path) -> None:
    """② 文章路径：h2 每节一卡（节标题即卡标题）；首段做封面副标。"""
    article = _write(
        tmp_path,
        "demo.md",
        "---\ntitle: 文章标题\ntags: [AI, 写作]\n---\n\n"
        "这是导语段落，讲清全文要解决的问题。\n\n"
        "## 第一节\n\n第一节内容一。\n\n第一节内容二。\n\n"
        "## 第二节\n\n第二节内容。\n\n"
        "## 结尾节\n\n收尾内容。\n",
    )

    html, n = build_cards_html(article)

    # 封面 + 3 节卡 + 结尾 = 5；节标题即卡标题
    assert n == 5
    assert _count(html, "content") == 3
    assert "第一节" in html and "第二节" in html and "结尾节" in html
    # 首段做封面副标
    assert "这是导语段落，讲清全文要解决的问题。" in html
    # 文章 frontmatter tags 收尾
    assert "#AI" in html and "#写作" in html


def test_article_over_six_sections_merged(tmp_path: Path) -> None:
    """>6 节合并相邻短节：7 个短节收到 6 张内容卡。"""
    sections = "\n\n".join(
        f"## 第{i}节\n\n第{i}节内容。" for i in range(1, 8)
    )
    article = _write(
        tmp_path, "a.md", f"---\ntitle: T\n---\n\n导语。\n\n{sections}\n"
    )

    html, n = build_cards_html(article)

    assert n == 8  # 封面 + 6 内容 + 结尾
    assert _count(html, "content") == 6
    # 内容不丢：7 节正文全部仍在（合并进卡而非丢弃）
    for i in range(1, 8):
        assert f"第{i}节内容。" in html


def test_article_min_cards_padded(tmp_path: Path) -> None:
    """卡数下界 3：仅一段导语无 h2 时，复制内容段补足一张内容卡。"""
    article = _write(
        tmp_path, "a.md", "---\ntitle: 只有导语\n---\n\n唯一的一段正文内容。\n"
    )

    html, n = build_cards_html(article)

    assert n == 3  # 封面 + 补足内容卡 + 结尾
    assert _count(html, "content") == 1
    assert "唯一的一段正文内容。" in html  # 内容段被复制成内容卡


def test_variant_max_cards_merged_tail(tmp_path: Path) -> None:
    """卡数上界 10：20 段 → 10 内容卡触发尾部合并，收到 8 内容卡。"""
    article = _write(tmp_path, "a.md", "---\ntitle: T\n---\n\n正文。\n")
    paragraphs = ["这是整篇的钩子段。"] + [f"第{i}段内容，讲一个要点。" for i in range(1, 20)]
    variant = _write(tmp_path, "a.xhs.md", _variant_text(paragraphs))

    html, n = build_cards_html(article, variant=variant)

    assert n == 10  # 封面 + 8 内容（尾部已合并） + 结尾
    assert _count(html, "content") == 8
    # 合并是并卡不是丢卡：全部段落文本仍在
    for i in (1, 10, 19):
        assert f"第{i}段内容" in html


def test_article_code_block_kept_with_code_bg(tmp_path: Path) -> None:
    """文章路径代码块保真：代码文本入卡，且用主题 code_bg 底色。"""
    article = _write(
        tmp_path,
        "a.md",
        "---\ntitle: T\n---\n\n导语。\n\n## 代码节\n\n正文段。\n\n"
        "```python\nprint('hello')\n```\n",
    )

    html, n = build_cards_html(article)

    assert n == 3  # 封面 + 代码节一卡 + 结尾
    assert "print('hello')" in html
    assert templates.load_palette("lapis")["code_bg"] in html  # #f3f4f6


def test_article_lead_code_before_first_h2_not_dropped(tmp_path: Path) -> None:
    """回归（复核 M1）：首 h2 之前导语区的代码块并入导语卡（code_bg 样式），
    不静默丢弃。"""
    article = _write(
        tmp_path,
        "a.md",
        "---\ntitle: T\n---\n\n导语第一段。\n\n"
        '```python\nprint("LEAD_CODE_SHOULD_NOT_VANISH")\n```\n\n'
        "## 第一节\n\n第一节正文。\n",
    )

    html, n = build_cards_html(article)

    assert 'print("LEAD_CODE_SHOULD_NOT_VANISH")' in html  # 代码文本在场
    # 走 code_bg 等宽块（与节内代码块同款呈现，契约见模块 docstring）
    assert templates.load_palette("lapis")["code_bg"] in html
    assert "导语第一段。" in html  # 封面副标不受影响
    assert "第一节正文。" in html


def test_article_h3_heading_text_kept_in_section(tmp_path: Path) -> None:
    """回归（复核 M1）：h3+ 小节标题不切卡，标题文本并入所在节正文——
    不在排除清单（图片/表格/hr）内的内容不得静默丢失。"""
    article = _write(
        tmp_path,
        "a.md",
        "---\ntitle: T\n---\n\n导语。\n\n## 第一节\n\n第一节正文。\n\n"
        "### 子小节标题XYZ\n\n子小节正文。\n",
    )

    html, n = build_cards_html(article)

    assert "子小节标题XYZ" in html  # h3 标题文本在场
    assert "子小节正文。" in html
    assert n == 3  # h3 不切卡：封面 + 一节一卡 + 结尾


# ── 色板注入 / ratio 规格 / 模板硬约束 ──────────────────────


def test_theme_palette_injected_and_switchable(tmp_path: Path) -> None:
    """themes 色板注入（primary/text 与 renderer 同源 load），主题可切换。"""
    article = _write(
        tmp_path, "a.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    html_lapis, _ = build_cards_html(article, theme="lapis")
    html_mint, _ = build_cards_html(article, theme="mint")

    assert "#2563eb" in html_lapis  # lapis.primary
    assert "#0d9488" in html_mint  # mint.primary
    assert "#0d9488" not in html_lapis
    # 纯内联 CSS 字体栈与安全边距
    assert "-apple-system" in html_lapis and "PingFang SC" in html_lapis
    assert "padding:96px" in html_lapis
    assert "<style" not in html_lapis  # 无样式块，全部内联


def test_ratio_two_specs(tmp_path: Path) -> None:
    """ratio 两规格：3:4=1242×1656；4:5=1242×1553；未知规格报错。"""
    article = _write(
        tmp_path, "a.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    html_34, _ = build_cards_html(article, ratio="3:4")
    html_45, _ = build_cards_html(article, ratio="4:5")

    assert 'data-w="1242" data-h="1656"' in html_34
    assert "width:1242px" in html_34 and "height:1656px" in html_34
    assert 'data-w="1242" data-h="1553"' in html_45
    assert "height:1553px" in html_45
    # 高度未写死在模板：4:5 视口规格从同一模板产出不同高度
    assert "height:1656px" not in html_45

    with pytest.raises(ValueError, match="未知卡规格"):
        build_cards_html(article, ratio="1:1")

    # raster 视口取卡组自带规格（data-w/data-h），不另写死
    assert templates.card_specs_from_html(html_34) == (1242, 1656)
    assert templates.card_specs_from_html(html_45) == (1242, 1553)


def test_single_html_contains_all_cards(tmp_path: Path) -> None:
    """单文件 HTML 含全部卡：每卡一个 section.musespiral-card 固定尺寸容器。"""
    article = _write(
        tmp_path,
        "a.md",
        "---\ntitle: T\n---\n\n导语。\n\n## 一\n\n内容。\n\n## 二\n\n内容。\n",
    )
    html, n = build_cards_html(article)

    assert html.count('class="musespiral-card ') == n == 4
    assert html.startswith("<!DOCTYPE html>")
    assert html.count("</section>") == n


# ── raster：缺 playwright → BrowserNotInstalled（结构路径） ─


def test_shoot_browser_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lazy 复用 session 的导入：缺 playwright 抛同型 BrowserNotInstalled+同提示。"""
    # sys.modules 注入 None → import 确定性抛 ImportError（装没装 playwright 都复现）
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    article = _write(
        tmp_path, "a.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    html, _ = build_cards_html(article)
    html_path = tmp_path / "cards.html"
    html_path.write_text(html, encoding="utf-8")

    with pytest.raises(BrowserNotInstalled) as ei:
        raster.shoot(html_path, tmp_path / "out")
    message = str(ei.value)
    assert 'pip install "musespiral[browser]"' in message
    assert "playwright install chromium" in message
    assert "PLAYWRIGHT_DOWNLOAD_HOST" in message  # 境内镜像提示
    # 同一错误类型：raster 抛的就是 browser.session 那个类
    assert ei.value.__class__ is BrowserNotInstalled


def test_require_browser_same_gate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """require_browser 预检同门：缺依赖同型同提示，不落文件。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(BrowserNotInstalled):
        raster.require_browser()


# ── CLI：--html-only 产 HTML / 未装浏览器可读错误 ────────────


def test_cli_html_only_produces_cards_html(tmp_path: Path) -> None:
    """--html-only：无需浏览器产单文件 HTML，≥3 卡。"""
    article = _write(
        tmp_path,
        "demo.md",
        "---\ntitle: CLI 演示\ntags: [a, b]\n---\n\n导语段。\n\n"
        "## 一\n\n内容一。\n\n## 二\n\n内容二。\n",
    )
    out = tmp_path / "out-cards"

    result = CliRunner().invoke(
        cards, [str(article), "--html-only", "-o", str(out)]
    )

    assert result.exit_code == 0, result.output
    html_file = out / "demo.html"
    assert html_file.exists()
    text = html_file.read_text(encoding="utf-8")
    assert text.count('class="musespiral-card ') >= 3
    assert "3 卡" in result.output or "4 卡" in result.output


def test_cli_no_browser_readable_error_two_ways(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未装 playwright 且未给 --html-only：可读错误提示两条出路，不落目录。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    article = _write(
        tmp_path, "a.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    out = tmp_path / "should-not-exist"

    result = CliRunner().invoke(cards, [str(article), "-o", str(out)])

    assert result.exit_code == 1
    assert "musespiral[browser]" in result.output
    assert "--html-only" in result.output  # 两条出路都在提示里
    assert not out.exists()  # 预检先行：不留半成品目录


def test_cli_refuses_existing_outdir(tmp_path: Path) -> None:
    """输出目录已存在即拒绝覆盖（adapt/pack 同惯例）。"""
    article = _write(
        tmp_path, "a.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    out = tmp_path / "cards-out"
    out.mkdir()

    result = CliRunner().invoke(
        cards, [str(article), "--html-only", "-o", str(out)]
    )

    assert result.exit_code == 1
    assert "拒绝覆盖" in result.output


def test_cli_default_outdir_next_to_article(tmp_path: Path) -> None:
    """默认输出目录 <stem>.cards/（pack xhs 探测的就是它）。"""
    article = _write(
        tmp_path, "demo.md", "---\ntitle: T\n---\n\n导语。\n\n## 节\n\n内容。\n"
    )
    result = CliRunner().invoke(cards, [str(article), "--html-only"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "demo.cards" / "demo.html").exists()


# ── compose 单元细目：段首句 ≤12 字截断 ─────────────────────


def test_card_title_truncated_to_12_chars() -> None:
    assert compose._card_title("这句话特别特别长，远远超过十二个字的限制。后续。") == "这句话特别特别长，远远超"
    assert compose._card_title("短句。后面的。") == "短句。"
    assert compose._first_sentence("无句末标点的一段") == "无句末标点的一段"
