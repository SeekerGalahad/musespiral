"""F18 theme learn 测试 — 频次归一 / 白名单防注入 / extends 生成与渲染。

全部通过 MUSESPIRAL_HOME 指向 tmp_path，绝不读写真实 ~/.musespiral；不联网
（learn_from_html 只吃 html_text，url 拉取属 CLI 集成阶段）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from musespiral.core.content.parser import ParsedArticle
from musespiral.core.content.renderer import Renderer
from musespiral.core.content.themes import load_theme
from musespiral.core.content.theme_learn import learn_from_html

_MERGE_SECTIONS = ("colors", "typography", "components", "colors_dark")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """autouse：所有测试的 musespiral 状态目录都指向临时目录，隔离真实 ~/.musespiral。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


# 已知频次的公众号式 fixture：正文色 #333333 ×3、强调 #c2410c ×2、署名 #999999 ×1；
# 字号 15px ×3 / 17px ×1；行高 1.75 ×3 / 1.5 ×1。
FIXTURE_HTML = """\
<div id="js_content">
  <section style="color: #333333; font-size: 15px; line-height: 1.75;">
    <p style="color: #333333; font-size: 15px; line-height: 1.75;">正文一段<span style="color: #c2410c;">强调</span></p>
    <p style="color: #333333; font-size: 15px; line-height: 1.75;">正文二段<span style="color: #c2410c; font-size: 17px;">重点</span></p>
    <p style="color: #999999; line-height: 1.5;">署名</p>
  </section>
</div>
"""


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── 频次归一 → 生成 YAML ──────────────────────────────────


def test_high_frequency_values_mapped(_isolated_home: Path) -> None:
    """高频值正确落入对应键：text/primary/font_size/line_height 全部取自 fixture 众数。"""
    out = learn_from_html(FIXTURE_HTML, "learned", out_dir=_isolated_home / "themes")
    data = load_yaml(out)
    assert data["extends"] == "default"
    assert data["colors"] == {"text": "#333333", "primary": "#c2410c"}
    assert data["typography"] == {"font_size": "15px", "line_height": "1.75"}
    # text-align 有收集但不映射进主题键
    assert "text_align" not in out.read_text(encoding="utf-8")


def test_color_forms_normalized(_isolated_home: Path) -> None:
    """大写 hex 归一小写、rgb() 空格归一——同色不同写法计入同一频次。"""
    html = (
        '<section style="color: #ABC">'
        '<p style="color: RGB(51, 51, 51)">a</p>'
        '<span style="color: rgb(51,51,51)">b</span>'
        '<p style="color: #abc">c</p>'
        '<p style="color: #abc">d</p>'
        "</section>"
    )
    data = load_yaml(learn_from_html(html, "normed", out_dir=_isolated_home / "themes"))
    assert data["colors"]["text"] == "#abc"  # 3 次 > rgb 2 次
    assert data["colors"]["primary"] == "rgb(51,51,51)"  # 两种写法并作一色


def test_single_color_no_primary(_isolated_home: Path) -> None:
    """只有一种颜色时没有「区别于 text 的第二色」→ 不写 primary（经 extends 继承）。"""
    html = '<p style="color: #123456">a</p><p style="color: #123456">b</p>'
    data = load_yaml(learn_from_html(html, "mono", out_dir=_isolated_home / "themes"))
    assert data["colors"] == {"text": "#123456"}


def test_duplicate_prop_in_one_style_last_wins(_isolated_home: Path) -> None:
    """同一 style 内同名属性后声明覆盖前声明（CSS 语义）。"""
    html = '<p style="color: #111111; color: #222222">x</p>'
    data = load_yaml(learn_from_html(html, "dup", out_dir=_isolated_home / "themes"))
    assert data["colors"] == {"text": "#222222"}


# ── 生成主题可加载、可渲染 ────────────────────────────────


def test_generated_theme_loads_and_renders(_isolated_home: Path) -> None:
    """验收主链路：load_theme 可加载（extends 补齐四段）→ Renderer 渲染无未解析占位。"""
    learn_from_html(FIXTURE_HTML, "learned", out_dir=_isolated_home / "themes")
    t = load_theme("learned")
    for section in _MERGE_SECTIONS:
        assert section in t and isinstance(t[section], dict), f"缺段 {section}"
    assert t["colors"]["text"] == "#333333"
    assert t["typography"]["font_size"] == "15px"

    article = ParsedArticle(
        metadata={"title": "标题"},
        blocks=[
            {"type": "heading", "level": 2, "text": "小节"},
            {"type": "paragraph", "text": "段落<code>code</code>"},
            {"type": "list", "ordered": True, "items": ["一", "二"]},
            {"type": "quote", "text": "引用"},
            {"type": "code", "lang": "python", "code": "print(1)"},
            {"type": "hr"},
        ],
    )
    html = Renderer(theme_name="learned").render(article)
    for placeholder in ("{text}", "{primary}", "{font_size}", "{line_height}", "{code_bg}"):
        assert placeholder not in html, f"渲染残留未解析占位 {placeholder}"
    assert "#333333" in html and "15px" in html  # 学习值实际进入渲染产物


# ── 白名单防注入 ──────────────────────────────────────────


def test_malicious_style_values_dropped(_isolated_home: Path) -> None:
    """expression()/url()/非法 hex/em 单位等全部被白名单丢弃，合法值保留。"""
    html = """\
<section style="color: expression(alert(1)); font-size: 14px; background: url(evil)">
  <p style="color: url(javascript:alert(1)); line-height: 25em">x</p>
  <span style="color: #12gzz; font-size: url(evil); text-align: url(x)">y</span>
  <p style="color: #336699; font-size: 16px; line-height: 1.6; text-align: center">ok</p>
  <p style="color: #336699; font-size: 16px; line-height: 1.6; text-align: justify">ok</p>
</section>
"""
    out = learn_from_html(html, "safe", out_dir=_isolated_home / "themes")
    raw = out.read_text(encoding="utf-8")
    for evil in ("expression", "url(", "javascript", "alert", "evil", "25em", "#12gzz"):
        assert evil not in raw, f"恶意/非法样式值泄漏进产物: {evil}"
    data = yaml.safe_load(raw)
    assert data["colors"] == {"text": "#336699"}  # 无第二合法色 → 无 primary
    assert data["typography"] == {"font_size": "16px", "line_height": "1.6"}


def test_whitelist_shape_boundaries(_isolated_home: Path) -> None:
    """白名单边界：四位 hex/rgba/hex带alpha/负数/百分号 rgb 一律不收。"""
    html = (
        "<section>"
        '<p style="color: #abcd">a</p>'
        '<p style="color: rgba(51, 51, 51, 0.5)">b</p>'
        '<p style="color: rgb(50%, 50%, 50%)">c</p>'
        '<p style="font-size: -15px; line-height: -1.5">d</p>'
        '<p style="color: #336699">ok</p>'
        "</section>"
    )
    data = load_yaml(learn_from_html(html, "edges", out_dir=_isolated_home / "themes"))
    assert data["colors"] == {"text": "#336699"}
    assert "typography" not in data  # 字号/行高全部不合法 → 不写该段


# ── 空样式与输出位置 ──────────────────────────────────────


def test_empty_styles_generate_default_skeleton(_isolated_home: Path) -> None:
    """空样式（乃至空字符串）HTML 生成仅含 extends 的默认骨架，不崩溃且可加载。"""
    for i, html in enumerate(
        ["<html><body><section><p>纯文本</p></section></body></html>", ""]
    ):
        name = f"blank{i}"
        out = learn_from_html(html, name, out_dir=_isolated_home / "themes")
        data = load_yaml(out)
        assert data["extends"] == "default"
        assert "colors" not in data and "typography" not in data
        t = load_theme(name)  # 全部键经 extends 继承 default
        for section in _MERGE_SECTIONS:
            assert section in t


def test_default_out_dir_is_user_theme_dir(_isolated_home: Path) -> None:
    """out_dir 缺省写入 config.home_dir()/themes/（受 MUSESPIRAL_HOME 隔离）。"""
    out = learn_from_html(FIXTURE_HTML, "where")
    assert out == _isolated_home / "themes" / "where.yaml"
    assert out.is_file()


def test_rerun_overwrites(_isolated_home: Path) -> None:
    """theme learn 是再生成动作：同名重跑直接覆盖并更新学习值。"""
    out_dir = _isolated_home / "themes"
    learn_from_html(FIXTURE_HTML, "again", out_dir=out_dir)
    learn_from_html('<p style="color: #0f766e; font-size: 17px">x</p>', "again", out_dir=out_dir)
    data = load_yaml(out_dir / "again.yaml")
    assert data["colors"] == {"text": "#0f766e"}
    assert data["typography"] == {"font_size": "17px"}


def test_invalid_name_rejected(_isolated_home: Path, tmp_path: Path) -> None:
    """主题名校验与 themes._NAME_RE 一致，路径样名字拒绝。"""
    with pytest.raises(ValueError):
        learn_from_html(FIXTURE_HTML, "../evil", out_dir=tmp_path)
