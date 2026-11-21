"""F23 长图渲染器测试 — 模板层全测（无浏览器）+ 光栅层 lazy 缺依赖结构路径。

真截图验证归属主会话（工作流不装 playwright，正典 §十八-实施与测试约定）：
本文件不依赖浏览器；缺 playwright 分支以 sys.modules 注入 None 确定性复现
（与 test_browser.py 同款手法，环境装没装 playwright 都成立）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.core.visual import poster
from musespiral.commands.poster_cmds import poster as poster_cmd
from musespiral.browser import BrowserNotInstalled

ARTICLE_MD = r"""---
title: 长图演示文章
date: 2026-11-13
digest: 一篇覆盖全部块型的长图渲染测试文章
---

## 开篇小节

质能方程 $E=mc^2$ 是最著名的公式，下面给出求和式。

$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$

```mermaid
graph TD; A-->B;
```

```math
\int_0^1 x dx
```

```python
print("hello")
```

- 要点一
- 要点二

> 引用一句话。

![示意图](assets/pic.png)

| 列A | 列B |
|---|---|
| 甲 | 乙 |
"""

DISPLAY_FORMULA = r"$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$"


@pytest.fixture()
def article_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))
    p = tmp_path / "demo.md"
    p.write_text(ARTICLE_MD, encoding="utf-8")
    return p


# ── 模板层：结构 / 全块在场 / 字号上调 / 文末标识 ──────────────────────


def test_poster_structure_and_all_blocks(article_md: Path) -> None:
    html = poster.build_poster_html(article_md)

    # 宽 1080 固定容器 + 单列竖版
    assert "width: 1080px" in html
    assert poster.POSTER_WIDTH == 1080

    # 头部大标题区：title + digest + 日期
    assert "<header" in html and "<h1" in html
    assert "长图演示文章" in html
    assert "一篇覆盖全部块型的长图渲染测试文章" in html
    assert "2026-11-13" in html

    # 全部正文块在场：标题/段落/列表/引用/表格/图片/普通代码（pygments 路径）
    assert "开篇小节" in html
    assert "质能方程" in html
    assert "要点一" in html and "要点二" in html
    assert "引用一句话。" in html
    assert "列A" in html and "甲" in html
    assert 'src="assets/pic.png"' in html
    assert "print" in html  # 普通代码块仍走 Renderer 的 pygments 高亮

    # 字号整体上调一档：lapis paragraph 16px → 19px（×1.2 取整到 0.5px）
    assert "font-size: 19px" in html
    assert "font-size: 16px" not in html  # 原始档 16px 不再出现

    # 文末标识区：musespiral 生成标注 + 主题名
    assert "由 musespiral 生成长图" in html
    assert "主题 lapis" in html


def test_poster_theme_reflected_in_footer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    p = tmp_path / "t.md"
    p.write_text("---\ntitle: T\n---\n\n正文。\n", encoding="utf-8")
    html = poster.build_poster_html(p, theme="ember")
    assert "主题 ember" in html


def test_poster_height_fluid_no_fixed_height(article_md: Path) -> None:
    """高度流式：容器无固定 height（长图高度由内容撑开）。"""
    html = poster.build_poster_html(article_md)
    assert "height: 1080px" not in html
    root = re.search(r'<div style="width: 1080px;[^"]*"', html)
    assert root is not None
    # 根容器（1080 宽）不得带 height
    assert "height" not in root.group(0)


# ── 零外部资源红线（结构断言）─────────────────────────────────────────


def test_poster_no_external_resources(article_md: Path) -> None:
    html = poster.build_poster_html(article_md)
    assert "<script" not in html  # 无脚本
    assert "<link" not in html  # 无样式/资源链接
    assert "@import" not in html and "url(http" not in html
    # 模板自身零 http(s) 引用（本夹具文章无外链，全文不应出现任何 http(s)）
    assert "http://" not in html
    assert "https://" not in html


# ── LaTeX / Mermaid 段：源码等宽呈现 ──────────────────────────────────


def test_latex_inline_formula_monospace(article_md: Path) -> None:
    html = poster.build_poster_html(article_md)
    # 行内公式 $..$ 原文进等宽字体块（monospace 字体栈）
    assert ">$E=mc^2$</code>" in html
    assert "ui-monospace" in html
    # 所在段落仍走正常段落管线（等宽 span 嵌在段落 section 内）
    assert "质能方程" in html


def test_latex_display_formula_standalone_block(article_md: Path) -> None:
    html = poster.build_poster_html(article_md)
    # 整段 $$..$$ 展示公式 → 独立等宽源码块（原文含定界符完整保留）
    assert DISPLAY_FORMULA in html
    pre = html.split("<pre", 2)[1]
    assert "ui-monospace" in pre and DISPLAY_FORMULA in pre


def test_mermaid_and_math_code_monospace_source(article_md: Path) -> None:
    html = poster.build_poster_html(article_md)
    # mermaid 源码等宽呈现（> 转义为 &gt; 属正常 HTML 转义）
    assert "graph TD; A--&gt;B;" in html
    # math 语言代码块同样走源码等宽呈现
    assert r"\int_0^1 x dx" in html
    # 两类块都以 monospace 字体栈呈现
    assert html.count("ui-monospace") >= 3  # mermaid + math + 行内公式/展示公式


# ── CLI：--html-only 路径 + 缺 playwright 可读错误（降级策略）───────────


def test_cli_html_only_default_and_custom_out(article_md: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(poster_cmd, [str(article_md), "--html-only"])
    assert result.exit_code == 0, result.output
    default_out = article_md.with_name("demo.poster.html")
    assert default_out.exists()
    text = default_out.read_text(encoding="utf-8")
    assert "width: 1080px" in text
    assert "长图演示文章" in text and "质能方程" in text and "要点一" in text  # 含全文
    assert "由 musespiral 生成长图" in text

    custom = article_md.parent / "custom.html"
    result2 = runner.invoke(poster_cmd, [str(article_md), "--html-only", "-o", str(custom)])
    assert result2.exit_code == 0, result2.output
    assert custom.exists()


def test_cli_browser_missing_readable_error(
    article_md: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sys.modules 注入 None → import 确定性抛 ImportError（无论环境装没装 playwright）
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    runner = CliRunner()
    result = runner.invoke(poster_cmd, [str(article_md)])
    assert result.exit_code == 1
    assert 'pip install "musespiral[browser]"' in result.output
    assert "playwright install chromium" in result.output
    assert "--html-only" in result.output  # 指引降级出路
    # 不落半成品：未装浏览器时 HTML/PNG 均不落盘
    assert not article_md.with_name("demo.poster.html").exists()
    assert not article_md.with_name("demo.poster.png").exists()


# ── 光栅层：lazy 缺 playwright 结构路径（无浏览器依赖）──────────────────


def test_shoot_browser_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    html_file = tmp_path / "p.html"
    html_file.write_text("<!DOCTYPE html><html><body>x</body></html>", encoding="utf-8")
    out_png = tmp_path / "p.png"
    with pytest.raises(BrowserNotInstalled) as ei:
        poster.shoot(html_file, out_png)
    assert "playwright install chromium" in str(ei.value)  # 安装提示可读
    assert not out_png.exists()  # 失败不落半成品


def test_require_browser_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(BrowserNotInstalled):
        poster.require_browser()


def test_module_top_level_imports_no_playwright() -> None:
    """结构证明（同 test_browser 源码扫描风格）：poster.py 顶层不
    import playwright——AST 只看模块级语句，函数体内的 lazy import 不受影响；
    不用 importlib.reload（重执行模块对跨测试 sys.modules 状态敏感，会闪红）。
    """
    import ast

    tree = ast.parse(Path(poster.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not any("playwright" in n for n in names), names
    assert callable(poster.build_poster_html)


# ── 光栅红线：不触网（复核 M3，§10.0）─────────────────────────────────


class _FakeRoute:
    """playwright Route 最小替身：记录 handler 选择的动作。"""

    def __init__(self, url: str) -> None:
        self.request = type("Req", (), {"url": url})()
        self.action: str | None = None

    def abort(self) -> None:
        self.action = "abort"

    def continue_(self) -> None:
        self.action = "continue"


def test_offline_route_aborts_http_passes_file() -> None:
    """回归（复核 M3）：外域 http(s) 请求一律中止、file:// 放行——正文
    透传的外域图片在截图时不发起任何网络请求。"""
    cases = [
        ("https://example.com/pic.png", "abort"),
        ("http://example.com/pic.png", "abort"),
        ("https://cdn.example.net/katex.js", "abort"),
        ("file:///tmp/musespiral/poster.html", "continue"),
        ("file:///home/user/a/b.png", "continue"),
    ]
    for url, want in cases:
        route = _FakeRoute(url)
        poster._offline_route(route)
        assert route.action == want, url


def test_shoot_wires_offline_route() -> None:
    """结构证明（源码扫描风格，同顶层无 playwright import 手法）：shoot
    给 context 挂 offline 路由——光栅不触网红线落在接线层。"""
    import inspect

    assert 'context.route("**/*", _offline_route)' in inspect.getsource(poster.shoot)
