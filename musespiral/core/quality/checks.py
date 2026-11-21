"""Pre-publish checklist（格式级）— validate article before any API call.

run_checks + 格式级检查（标题/封面/摘要/图片/代码/主题/frontmatter/LaTeX
检测）+ 共享结果类型 CheckLevel/CheckResult + 报告工具（format_report /
has_failures）；渲染级检查在同包 render_checks.py（后端架构正典 §八
Phase B，自 musespiral/checklist.py 拆出；旧路径为 re-export 垫片）。

Returns a list of (level, message) tuples:
  PASS / WARN / FAIL
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from ... import config
from ..content import themes
from ..content.parser import ParsedArticle
from .banned_words import load_words, scan
from .render_checks import _collect_image_srcs

CheckLevel = Literal["PASS", "WARN", "FAIL"]
CheckResult = tuple[CheckLevel, str]


# ---- 公式检测辅助（§十三 F7）----

_LATEX_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)  # 块级 $$...$$
# 行内成对 $...$；环视排除紧邻 $ 的情况，防止 $$...$$ 被行内模式重复计数
_LATEX_INLINE_RE = re.compile(r"(?<!\$)\$([^$\n]+?)\$(?!\$)")
# CJK 汉字/中文标点/全角符号 —— 出现即视为中文语境而非数学式
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_LATEX_OP_RE = re.compile(r"[\\=+*/^_{}<>]")


def _is_math_body(inner: str) -> bool:
    """判断 $...$ / $$...$$ 内的内容是否像 ASCII 数学式样。

    反货币误报口径（§十三 F7-7c）：内容无中文、含字母/数字、且含运算符
    或 LaTeX 命令符 —— "$5 and $10" 这类纯数字/单词配对不命中；
    "$1 = 7.2 CNY" 这类以数字开头且全程无上下标/命令符的汇率句同样放行
    （数学式极少以裸数字开头且不用 ^ _ { }）。
    """
    if _CJK_RE.search(inner):
        return False
    if not re.search(r"[A-Za-z0-9]", inner):
        return False
    if re.match(r"\d", inner) and not re.search(r"[\^_{}]", inner):
        return False  # 货币/价格语境：$1 = 7.2 …（第四期遗留 low 修复）
    return bool(_LATEX_OP_RE.search(inner))


def _detect_latex(blocks: list[dict]) -> list[str]:
    """在非代码 block 文本中找公众号无法渲染的 LaTeX 数学式样。"""
    hits: list[str] = []
    for b in blocks:
        if b.get("type") == "code":
            continue  # 代码里的 $（如 shell 变量）不是数学式
        text = b.get("text")
        if not isinstance(text, str):
            continue
        # 行内 code（`x` 或渲染后的 <code>x</code>）里的 $ 不是数学式
        text = re.sub(r"`[^`\n]*`", " ", text)
        text = re.sub(r"<code>.*?</code>", " ", text, flags=re.DOTALL)
        for m in _LATEX_BLOCK_RE.finditer(text):
            if _is_math_body(m.group(1)):
                hits.append(m.group(0))
        for m in _LATEX_INLINE_RE.finditer(text):
            if _is_math_body(m.group(1)):
                hits.append(m.group(0))
    return list(dict.fromkeys(hits))


def run_checks(
    article: ParsedArticle, article_path: str | Path, platform: str | None = None
) -> list[CheckResult]:
    results: list[CheckResult] = []
    base = Path(article_path).parent
    md = article.metadata

    # Title
    title = str(md.get("title", ""))
    title_bytes = len(title.encode("utf-8"))
    if title_bytes == 0:
        results.append(("FAIL", "标题为空（frontmatter.title 必填）"))
    elif title_bytes > config.TITLE_MAX_BYTES:
        results.append(("FAIL", f"标题超过 {config.TITLE_MAX_BYTES} 字节: 当前 {title_bytes} 字节"))
    else:
        results.append(("PASS", f"标题长度合规: {title_bytes} 字节"))

    # Cover
    cover = md.get("cover", "")
    if not cover:
        results.append(("FAIL", "封面图未设置（frontmatter.cover 必填）"))
    else:
        cover_path = base / cover if not Path(cover).is_absolute() else Path(cover)
        if not cover_path.exists():
            results.append(("FAIL", f"封面图不存在: {cover}"))
        else:
            size_mb = cover_path.stat().st_size / 1024 / 1024
            if size_mb > 2:
                results.append(("WARN", f"封面图较大: {size_mb:.1f}MB（建议 ≤ 2MB）"))
            else:
                results.append(("PASS", f"封面图存在: {cover} ({size_mb:.1f}MB)"))

    # Digest
    digest = str(md.get("digest", ""))
    digest_bytes = len(digest.encode("utf-8"))
    if digest_bytes > config.DIGEST_MAX_BYTES:
        results.append(("FAIL", f"摘要超过 {config.DIGEST_MAX_BYTES} 字节: 当前 {digest_bytes} 字节"))
    elif digest_bytes == 0:
        results.append(("WARN", "摘要为空，将自动取正文前 54 字"))
    else:
        results.append(("PASS", f"摘要长度合规: {digest_bytes} 字节"))

    # Content images（F7-7a：本地文件缺失 → FAIL 列缺失清单；网络图不在此列，走 F3 渲染级外域检查）
    image_srcs = _collect_image_srcs(article.blocks)
    if not image_srcs:
        results.append(("WARN", "正文无图片，纯文字文章吸引力较低"))
    else:
        local_srcs, web_srcs = [], []
        for src in image_srcs:
            parsed = urlparse(src)
            if parsed.scheme or parsed.netloc:
                web_srcs.append(src)
            else:
                local_srcs.append(src)
        missing = [
            src
            for src in local_srcs
            if not (base / src if not Path(src).is_absolute() else Path(src)).exists()
        ]
        oversize = [
            src
            for src in local_srcs
            if src not in missing
            and (base / src if not Path(src).is_absolute() else Path(src)).stat().st_size
            > config.IMAGE_MAX_BYTES
        ]
        if missing:
            results.append(
                ("FAIL", f"{len(missing)} 张正文图片文件缺失（裂图必现）: {', '.join(missing)}")
            )
        elif oversize:
            results.append(("FAIL", f"{len(oversize)} 张图片超过 2MB: {', '.join(oversize)}"))
        elif local_srcs:
            note = f"，另有 {len(web_srcs)} 张网络图" if web_srcs else ""
            results.append(("PASS", f"正文 {len(local_srcs)} 张本地图片齐全且 ≤ 2MB{note}"))
        else:
            results.append(("PASS", f"正文 {len(web_srcs)} 张网络图（存在性由渲染级外域检查覆盖）"))

    # Code blocks with language
    code_blocks = [b for b in article.blocks if b["type"] == "code"]
    no_lang = [b for b in code_blocks if not b.get("lang")]
    if no_lang:
        results.append(("WARN", f"{len(no_lang)} 个代码块未标注语言，高亮效果会变差"))
    elif code_blocks:
        results.append(("PASS", f"{len(code_blocks)} 个代码块均标注了语言"))

    # Mermaid / LaTeX（F7-7c：公众号不渲染 → WARN，不 FAIL）
    mermaid_blocks = [b for b in code_blocks if b.get("lang") == "mermaid"]
    if mermaid_blocks:
        results.append(
            ("WARN", f"{len(mermaid_blocks)} 个 mermaid 代码块（公众号不渲染，请转图片）")
        )
    latex_hits = _detect_latex(article.blocks)
    if latex_hits:
        results.append(("WARN", f"{len(latex_hits)} 处 LaTeX 公式（公众号不渲染，请转图片）"))

    # Banned words（F33A：恒 WARN 不 FAIL，只标记不否决——与 AI 红线同语义；
    # 无命中不输出条目，保持既有 clean 文章的报告输出零变化）
    banned_hits = scan(article.raw_body, load_words())
    if banned_hits:
        detail = "、".join(f"{w}×L{ln}" for w, ln in banned_hits)
        results.append(("WARN", f"违禁词 {len(banned_hits)} 处: {detail}（只标记不否决）"))

    # Required frontmatter
    missing = [k for k in ("title", "cover") if not md.get(k)]
    if missing:
        results.append(("FAIL", f"缺少必填 frontmatter: {', '.join(missing)}"))

    # Theme — 动态枚举（内置 ∪ 用户目录，§十二 F2）
    theme = md.get("theme")
    if theme:
        known = [n for n, _ in themes.list_all()]
        if theme not in known:
            results.append(("WARN", f"主题 {theme} 不是已安装主题（可用: {'/'.join(known)}）"))

    # 平台规格预检（F33：--platform <code> 时字限/图数/封面三项只对该平台生效）
    if platform:
        results.extend(platform_precheck(article, article_path, platform))

    return results


def platform_precheck(
    article: ParsedArticle, article_path: str | Path, platform_code: str
) -> list[CheckResult]:
    """按 PLIMITS 对指定平台做字限/图数/封面三项预检（check --platform 消费）。

    字限口径同 title.py：mp 用 title_max_bytes（字节），其余平台
    title_max_chars / title_min_chars（字数）；图数对齐 pack 口径
    （count_pack_images 同语义：卡组 images-cards/*.png + 封面占一坑，正文
    内联图不计——平台上传的是卡组+封面，不是 markdown 内联图）；封面用
    cover_w/cover_h（F31 规格表唯一事实源），取材按 F31 优先级：文章目录
    cover.<platform>.png > frontmatter.cover 文件。键缺失即该平台不设该项
    规格，静默跳过（如 zhihu 无强制封面）。未知平台由 profiles.get_limits
    抛 ValueError（命令层转为用法报错）。
    """
    from ...platforms import profiles  # domain 同层，惰性导入（pack_checks 注入惯例）

    lim = profiles.get_limits(platform_code)
    results: list[CheckResult] = []
    md = article.metadata
    base = Path(article_path).parent

    # 封面取材探测（§二 F31 优先级，与 pack_service._find_article_cover 同语义）：
    # 文章目录 cover.<platform>.png 优先（F31 平台封面——非 mp 不写回 frontmatter，
    # 靠这里发现），退 frontmatter.cover 指向的文件；两者皆无 → None
    cover_src: Path | None = None
    platform_cover = base / f"cover.{platform_code}.png"
    if platform_cover.is_file():
        cover_src = platform_cover
    else:
        fm_cover = str(md.get("cover", ""))
        if fm_cover:
            cand = Path(fm_cover) if Path(fm_cover).is_absolute() else base / fm_cover
            if cand.exists():
                cover_src = cand

    # ① 字限
    title = str(md.get("title", ""))
    if title:
        max_b = lim.get("title_max_bytes")
        if max_b is not None:
            n = len(title.encode("utf-8"))
            if n > max_b:
                results.append(("FAIL", f"{platform_code} 标题超限: {n} 字节 > {max_b} 字节"))
            else:
                results.append(("PASS", f"{platform_code} 标题字限合规: {n}/{max_b} 字节"))
        else:
            n = len(title)
            max_c, min_c = lim.get("title_max_chars"), lim.get("title_min_chars")
            if max_c is not None and n > max_c:
                results.append(("FAIL", f"{platform_code} 标题超限: {n} 字 > {max_c} 字"))
            elif min_c is not None and n < min_c:
                results.append(("FAIL", f"{platform_code} 标题低于下限: {n} 字 < {min_c} 字"))
            elif max_c is not None:
                results.append(("PASS", f"{platform_code} 标题字限合规: {n}/{max_c} 字"))

    # ② 图数（对齐 pack 口径 count_pack_images：上传给平台的图 = images-cards/
    # 卡组 + 封面；正文内联图不作为平台图上传——文本+卡组的 xhs 常规流不能
    # 假阳性 FAIL。上限 None = 不设上限，如 zhihu）
    img_min, img_max = lim.get("images_min"), lim.get("images_max")
    if img_min is not None or img_max is not None:
        cards_dir = base / f"{Path(article_path).stem}.cards"
        n_img = len(list(cards_dir.glob("*.png"))) if cards_dir.is_dir() else 0
        if cover_src is not None:
            n_img += 1
        if img_min is not None and n_img < img_min:
            results.append(("FAIL", f"{platform_code} 图片不足: {n_img} 张 < 最少 {img_min} 张"))
        elif img_max is not None and n_img > img_max:
            results.append(("FAIL", f"{platform_code} 图片超限: {n_img} 张 > 上限 {img_max} 张"))
        else:
            results.append(("PASS", f"{platform_code} 图片数合规: {n_img} 张"))

    # ③ 封面规格（cover_w/cover_h；mp 兼容键 cover_ratio 不在此消费）
    cw, ch = lim.get("cover_w"), lim.get("cover_h")
    if cw and ch:
        if cover_src is None:
            fm_cover = str(md.get("cover", ""))
            if fm_cover:  # frontmatter 指了封面但文件缺失/读不到 → WARN（同旧口径）
                results.append(("WARN", f"{platform_code} 封面无法读取尺寸: {fm_cover}"))
        else:
            dims = None
            try:
                from PIL import Image  # 已是核心依赖；惰性导入保持模块导入零 PIL

                with Image.open(cover_src) as im:
                    dims = im.size
            except OSError:  # 损坏/非图片 → 无法核对尺寸，WARN 提示（存在性由基础项覆盖）
                dims = None
            if dims is None:
                results.append(("WARN", f"{platform_code} 封面无法读取尺寸: {cover_src.name}"))
            elif dims != (cw, ch):
                results.append(("FAIL", f"{platform_code} 封面尺寸不符: {dims[0]}×{dims[1]} ≠ {cw}×{ch}"))
            else:
                results.append(("PASS", f"{platform_code} 封面规格合规: {cw}×{ch}"))

    return results


def format_report(results: list[CheckResult]) -> str:
    lines = []
    fails = sum(1 for lvl, _ in results if lvl == "FAIL")
    warns = sum(1 for lvl, _ in results if lvl == "WARN")
    for lvl, msg in results:
        marker = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[lvl]
        lines.append(f"{marker} [{lvl}] {msg}")
    header = f"\n{'=' * 50}\n审核结果: {fails} 失败, {warns} 警告, {len(results) - fails - warns} 通过\n{'=' * 50}"
    return "\n".join(lines) + header


def has_failures(results: list[CheckResult]) -> bool:
    return any(lvl == "FAIL" for lvl, _ in results)
