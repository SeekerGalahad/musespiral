"""渲染级自动验收（§十二 F3）— 对渲染后 HTML 做发布前检查。

render_checks + 渲染级检查（外链残留/图片外域/封面比例/标签白名单）+
正文图片 src 收集辅助 _collect_image_srcs；格式级检查与共享结果类型在
同包 checks.py（后端架构正典 §八 Phase B，自 musespiral/checklist.py 拆出；
旧路径为 re-export 垫片）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PIL import Image

from ..content.parser import ParsedArticle
from ..content.renderer import _FOOTNOTES_OL_STYLE

if TYPE_CHECKING:
    # 共享结果类型定义在 checks.py；仅类型引用，避免 checks→render_checks 运行时环
    from .checks import CheckResult


# ---- 正文图片收集辅助（§十三 F7）----

# 解析器把行内图片以 <img src="..."> 形式嵌进段落文本（parser._BlockCollector.image），
# 故正文图片需同时扫 image block 的 src 与各 block 文本里的 <img> 标签
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.IGNORECASE)


def _collect_image_srcs(blocks: list[dict]) -> list[str]:
    """收集正文全部图片 src（image block 的 src + 文本内嵌 <img>），去重保序。"""
    srcs: list[str] = []
    for b in blocks:
        if b.get("type") == "image" and b.get("src"):
            srcs.append(b["src"])
        elif isinstance(b.get("text"), str):
            srcs.extend(_IMG_SRC_RE.findall(b["text"]))
    return list(dict.fromkeys(srcs))


# ---- 渲染级自动验收（§十二 F3）----

_A_HREF_RE = re.compile(r'<a\b[^>]*\bhref="(https?://[^"]+)"', re.IGNORECASE)
# 公众号会过滤的标签（含闭合形式），\b 防止 <links> 之类误报
_FORBIDDEN_TAG_RE = re.compile(r"<\s*/?\s*(script|iframe|link)\b", re.IGNORECASE)

_WECHAT_HOST = "mp.weixin.qq.com"  # 微信内链豁免域
_MMBIZ_HOST = "mmbiz.qpic.cn"  # 微信图床域
_COVER_RATIO_TARGET = 2.35  # 官方推荐封面比例 900×383
_COVER_RATIO_TOLERANCE = 0.25  # 允许 ±25% 偏差

# 渲染器生成的参考资料 <ol>（外链脚注化的预期归宿）——剥离后再扫外链，
# 否则脚注列表自身的 <a href="外链"> 会被误判为外链残留（§十二 F3/F4 集成裁决）。
_FOOTNOTE_SECTION_RE = re.compile(
    re.escape(f'<ol style="{_FOOTNOTES_OL_STYLE}">') + r".*?</ol>",
    re.DOTALL,
)


def _strip_footnote_section(html: str) -> str:
    """去掉渲染器生成的参考资料列表，返回剩余 HTML。"""
    return _FOOTNOTE_SECTION_RE.sub("", html)


def render_checks(html: str, article: ParsedArticle, article_path: str | Path) -> list[CheckResult]:
    """对渲染后 HTML 做发布前自动验收（check --render / publish 内部调用）。

    四项检查:
      1. 外链残留: 非 mp.weixin.qq.com 的 <a href="http..."> → FAIL；
         linkfoot: false 时降级 WARN（开关即用户显式选择，机器不再否决，§十三 F11a）
         （渲染器生成的参考资料列表是外链的预期归宿，剥离后不计入）
      2. 正文图片: 外域 <img src>（既非 mmbiz.qpic.cn 也非本地路径）→ WARN；
         data:/file: 协议 → WARN（微信转存必失败，§十三 F11c）
      3. 封面比例: PIL 读尺寸，宽高比偏离 2.35:1 超 ±25% → WARN
      4. 标签白名单: 含 <script>/<iframe>/<link> 标签 → FAIL

    封面路径解析与 run_checks 一致（相对文章目录）；封面缺失或 PIL
    无法读取尺寸时该项跳过（存在性已由 run_checks 覆盖）。
    """
    results: list[CheckResult] = []

    # 1. 外链残留（脚注参考资料段豁免；linkfoot:false → WARN）
    foreign = [
        href
        for href in _A_HREF_RE.findall(_strip_footnote_section(html))
        if urlparse(href).netloc.lower() != _WECHAT_HOST
    ]
    foreign = list(dict.fromkeys(foreign))
    if foreign:
        if article.metadata.get("linkfoot", True):
            results.append(
                ("FAIL", f"外链残留 {len(foreign)} 处（微信内不可点，应转脚注）: {', '.join(foreign)}")
            )
        else:
            results.append(
                (
                    "WARN",
                    f"外链残留 {len(foreign)} 处（linkfoot:false 保留原样为显式选择，微信内不可点）: "
                    f"{', '.join(foreign)}",
                )
            )
    else:
        results.append(("PASS", "无外链残留（mp.weixin.qq.com 内链豁免）"))

    # 2. 正文图片外域 + data:/file: 协议
    srcs = list(dict.fromkeys(_IMG_SRC_RE.findall(html)))
    bad_srcs: list[str] = []
    scheme_srcs: list[str] = []
    for src in srcs:
        parsed = urlparse(src)
        if parsed.scheme in ("data", "file"):
            scheme_srcs.append(src)  # 微信转存必失败（F11c）
        elif parsed.scheme in ("http", "https"):
            if parsed.netloc.lower() != _MMBIZ_HOST:
                bad_srcs.append(src)
        elif src.startswith("//"):
            bad_srcs.append(src)  # 协议相对地址既非 mmbiz 也非本地路径
    if scheme_srcs:
        results.append(
            ("WARN", f"{len(scheme_srcs)} 张图片为 data:/file: 协议（微信转存必失败）: {', '.join(scheme_srcs)}")
        )
    if bad_srcs:
        results.append(
            ("WARN", f"{len(bad_srcs)} 张图片为外域地址（发布时微信转存会失败）: {', '.join(bad_srcs)}")
        )
    elif srcs and not scheme_srcs:
        results.append(("PASS", f"正文 {len(srcs)} 张图片来源合规（mmbiz/本地路径）"))
    elif not srcs:
        results.append(("PASS", "正文无图片"))

    # 3. 封面比例
    cover = str(article.metadata.get("cover", ""))
    if cover:
        cover_path = Path(cover) if Path(cover).is_absolute() else Path(article_path).parent / cover
        width = height = 0
        if cover_path.exists():
            try:
                with Image.open(cover_path) as im:
                    width, height = im.size
            except Exception:
                width = height = 0
        if width and height:
            ratio = width / height
            low = _COVER_RATIO_TARGET * (1 - _COVER_RATIO_TOLERANCE)
            high = _COVER_RATIO_TARGET * (1 + _COVER_RATIO_TOLERANCE)
            if not low <= ratio <= high:
                results.append(
                    ("WARN", f"封面宽高比 {ratio:.2f}:1 偏离推荐 2.35:1 超 ±25% ({width}×{height})")
                )
            else:
                results.append(("PASS", f"封面宽高比 {ratio:.2f}:1 在 2.35:1 ±25% 内"))

    # 4. 标签白名单
    tags = sorted({m.group(1).lower() for m in _FORBIDDEN_TAG_RE.finditer(html)})
    if tags:
        results.append(("FAIL", f"HTML 含公众号禁止标签 <{'/'.join(tags)}>（会被过滤）"))
    else:
        results.append(("PASS", "无 <script>/<iframe>/<link> 标签"))

    return results
