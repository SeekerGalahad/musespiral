"""F4 外链脚注增强 — 微信内链豁免 + linkfoot 开关（design §十二 F4）。"""
from __future__ import annotations

from pathlib import Path

from musespiral.core.content.parser import parse_article
from musespiral.core.content.renderer import Renderer, _convert_links_to_footnotes

WECHAT_URL = "https://mp.weixin.qq.com/s/abc123"
EXTERNAL_URL = "https://github.com/wagic/researchEverything"


def _render_md(tmp_path: Path, name: str, md_text: str) -> str:
    """Write a temp markdown file, parse and render it."""
    path = tmp_path / name
    path.write_text(md_text, encoding="utf-8")
    return Renderer().render(parse_article(path))


def test_mixed_links_wechat_kept_external_footnoted(tmp_path: Path) -> None:
    """混合链接：内链保留可点 <a>，外链转角标且文末参考资料含 URL。"""
    html = _render_md(
        tmp_path,
        "mixed.md",
        f"""---
title: 混合链接
---

前面是[微信内链]({WECHAT_URL})，后面是[外部链接]({EXTERNAL_URL})。
""",
    )
    # 内链豁免：正文保留可点的 <a>，且不进参考资料
    assert f'<a href="{WECHAT_URL}">微信内链</a>' in html
    assert html.count(WECHAT_URL) == 1
    # 外链转角标：正文不再有外链 <a>（仅参考资料处出现一次）
    assert "外部链接<sup>[1]</sup>" in html
    assert html.count(f'<a href="{EXTERNAL_URL}"') == 1
    assert f"[1] {EXTERNAL_URL}" in html


def test_linkfoot_false_disables_conversion(tmp_path: Path) -> None:
    """linkfoot: false 时完全关闭脚注化，链接原样保留。"""
    html = _render_md(
        tmp_path,
        "off.md",
        f"""---
title: 关闭脚注
linkfoot: false
---

[外部链接]({EXTERNAL_URL}) 和 [微信内链]({WECHAT_URL}) 都保持原样。
""",
    )
    assert f'<a href="{EXTERNAL_URL}">外部链接</a>' in html
    assert f'<a href="{WECHAT_URL}">微信内链</a>' in html
    assert "<sup>" not in html
    # 无参考资料列表：URL 只在正文出现一次，且无 [1] 条目
    assert html.count(EXTERNAL_URL) == 1
    assert f"[1] {EXTERNAL_URL}" not in html


def test_no_links_article_unaffected(tmp_path: Path) -> None:
    """无链接文章渲染不受脚注逻辑影响。"""
    html = _render_md(
        tmp_path,
        "plain.md",
        """---
title: 无链接
---

## 小标题

普通段落，没有任何链接。

- 列表项一
- 列表项二
""",
    )
    assert "普通段落，没有任何链接。" in html
    assert "列表项一" in html
    assert "<a href" not in html
    assert "<sup>" not in html


def test_wechat_prefix_requires_https() -> None:
    """豁免仅限 https://mp.weixin.qq.com/ 前缀；http 内链仍转脚注。"""
    html, links = _convert_links_to_footnotes('<a href="http://mp.weixin.qq.com/s/x">旧协议</a>')
    assert links == ["http://mp.weixin.qq.com/s/x"]
    assert "旧协议<sup>[1]</sup>" in html
