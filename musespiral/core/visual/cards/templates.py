"""F22 卡组 HTML 模板 — 三型卡，纯内联 CSS（§十八 F22）。

- 三型卡：封面卡（大标题+副标钩子）/ 内容卡（卡标题+正文段）/ 结尾卡
  （互动引导+话题标签）；
- 样式约束：纯内联 CSS（无 <style> 块）、字体栈 -apple-system/PingFang SC、
  安全边距 96px、themes 色板注入（primary/text/code_bg，经 musespiral.themes.load_theme
  与 renderer 同源加载，用户目录覆盖与 extends 继承同样生效）；
- 规格：ratio 3:4（1242×1656）与 4:5（1242×1552.5 取整 1242×1553）。
  高度不写死在模板文本里——由 ratio 常量在渲染时注入每卡的固定尺寸容器
  （section.musespiral-card，光栅层按容器 element.screenshot 逐卡拆截），
  页面本身不设固定高。
"""
from __future__ import annotations

import html as _html
import re

from ...content.themes import load_theme

# 两规格像素尺寸（宽恒 1242；4:5 的 1552.5 按设计取整 1553）
RATIOS: dict[str, tuple[int, int]] = {
    "3:4": (1242, 1656),
    "4:5": (1242, 1553),
}

# 安全边距（设计 §十八 F22：安全边距 96px）
SAFE_MARGIN = 96

FONT_STACK = "-apple-system,'PingFang SC','Microsoft YaHei',sans-serif"


def card_size(ratio: str) -> tuple[int, int]:
    """ratio（"3:4"/"4:5"）→ (宽, 高)；未知规格抛 ValueError。"""
    size = RATIOS.get(ratio)
    if size is None:
        raise ValueError(f"未知卡规格 ratio: {ratio}（可选: {'/'.join(RATIOS)}）")
    return size


def load_palette(theme: str) -> dict[str, str]:
    """themes 色板 → 卡模板用键（与 renderer 同源 load_theme）。"""
    colors = load_theme(theme).get("colors", {})
    return {
        "primary": str(colors.get("primary", "#2563eb")),
        "text": str(colors.get("text", "#1f2937")),
        "muted": str(colors.get("muted", "#6b7280")),
        "background": str(colors.get("background", "#ffffff")),
        "code_bg": str(colors.get("code_bg", "#f3f4f6")),
    }


def _esc(text: str) -> str:
    return _html.escape(text, quote=False)


def _container(
    kind: str, size: tuple[int, int], palette: dict[str, str], extra: str = ""
) -> str:
    """卡容器 section 开标签：class 标型别 + data-w/data-h 供光栅层取视口尺寸。"""
    w, h = size
    return (
        f'<section class="musespiral-card musespiral-card--{kind}" data-w="{w}" data-h="{h}" '
        f'style="box-sizing:border-box;width:{w}px;height:{h}px;padding:{SAFE_MARGIN}px;'
        f"margin:0 auto 24px;background:{palette['background']};overflow:hidden;"
        f"font-family:{FONT_STACK};display:flex;flex-direction:column;"
        f'justify-content:center;{extra}">'
    )


def cover_card(
    title: str, hook: str, *, size: tuple[int, int], palette: dict[str, str]
) -> str:
    """封面卡：大标题（56px）+ 副标钩子（40px），primary 色强调条。"""
    hook_html = (
        f'<p style="margin:32px 0 0;font-size:40px;line-height:1.5;'
        f"color:{palette['muted']};\">{_esc(hook)}</p>"
        if hook
        else ""
    )
    return (
        _container("cover", size, palette)
        + (
            f'<div style="width:96px;height:12px;background:{palette["primary"]};'
            'border-radius:6px;margin-bottom:48px;"></div>'
        )
        + (
            f'<h1 style="margin:0;font-size:56px;line-height:1.35;font-weight:700;'
            f"color:{palette['text']};\">{_esc(title)}</h1>"
        )
        + hook_html
        + "</section>"
    )


def content_card(
    title: str,
    paragraphs: list[str],
    *,
    size: tuple[int, int],
    palette: dict[str, str],
    code: str = "",
) -> str:
    """内容卡：卡标题（44px，primary 色）+ 正文段（36px；段内换行转 <br>）。

    code 非空时在正文段后附代码块（code_bg 底色的等宽块，色板注入项）。
    """
    body = "".join(
        f'<p style="margin:0 0 28px;font-size:36px;line-height:1.7;'
        f"color:{palette['text']};\">{_esc(p).replace(chr(10), '<br>')}</p>"
        for p in paragraphs
    )
    code_html = (
        f'<pre style="margin:8px 0 0;padding:28px;border-radius:16px;'
        f"background:{palette['code_bg']};font-size:28px;line-height:1.6;"
        'white-space:pre-wrap;font-family:Menlo,Consolas,monospace;">'
        f"{_esc(code)}</pre>"
        if code
        else ""
    )
    return (
        _container("content", size, palette)
        + (
            f'<h2 style="margin:0 0 40px;font-size:44px;line-height:1.4;'
            f"font-weight:700;color:{palette['primary']};\">{_esc(title)}</h2>"
        )
        + body
        + code_html
        + "</section>"
    )


def ending_card(
    tags: list[str],
    *,
    size: tuple[int, int],
    palette: dict[str, str],
    guide: str = "觉得有用就点个赞，关注不迷路",
) -> str:
    """结尾卡：互动引导（44px）+ 话题标签胶囊（#tag，primary 描边）。"""
    chips = "".join(
        f'<span style="display:inline-block;margin:12px 12px 0 0;padding:12px 28px;'
        f"border:3px solid {palette['primary']};border-radius:999px;font-size:32px;"
        f"color:{palette['primary']};\">#{_esc(t)}</span>"
        for t in tags
    )
    return (
        _container("ending", size, palette)
        + (
            f'<p style="margin:0;font-size:44px;line-height:1.5;font-weight:700;'
            f"color:{palette['text']};\">{_esc(guide)}</p>"
        )
        + f'<div style="margin-top:40px;">{chips}</div>'
        + "</section>"
    )


def document(sections: list[str], *, title: str = "") -> str:
    """卡组单文件 HTML：全部卡纵向堆叠，页面不设固定高（高度由各卡容器自带）。"""
    body = "\n".join(sections)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh">\n'
        '<head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title></head>\n"
        '<body style="margin:0;background:#e5e7eb;">\n'
        f"{body}\n"
        "</body>\n</html>\n"
    )


def card_specs_from_html(html_text: str) -> tuple[int, int]:
    """从卡组 HTML 读回首卡的 (宽, 高)——raster 设视口用（data-w/data-h）。"""
    m = re.search(r'data-w="(\d+)"[^>]*data-h="(\d+)"', html_text)
    if m is None:
        raise ValueError("卡组 HTML 缺少 data-w/data-h 卡容器属性")
    return int(m.group(1)), int(m.group(2))
