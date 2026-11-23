"""F31 封面生成 — 四路线纯逻辑（§二 F31，规格唯一事实源=platforms.profiles §一表）。

四条派生路线（互斥，缺省 title-card）：
  title-card   标题字卡：HTML 模板（标题+一句钩子）→光栅，复用 cards 模板与
               光栅管线；无浏览器环境走 Pillow 兜底直绘（CI/冒烟即此路径，
               BrowserNotInstalled 静默降级，不报错——封面必须有产出）
  from-cards   取文章旁 <stem>.cards/ 首卡（排序后首张 PNG）中心裁目标规格
  from-poster  取文章旁 <stem>.poster.png 头区（顶部按目标宽高比裁）目标规格
  from-image   用户源图 PIL 中心裁+缩放至目标规格

裁剪纪律：先裁到目标宽高比再缩放到精确 (cover_w, cover_h)；输出恒 PNG，
带透明通道（RGBA/LA/P）时拍到底色转 RGB。红线沿用 visual 包：模板纯本地、
光栅只 file:// 加载、零新增第三方依赖（Pillow 为核心依赖）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 封面落盘命名（2026-11-24 W2 断环批：自 services/cover_service 上移——纯逻辑，
# 且 visual_service 与 cover_service 互依（visual 需本函数、cover 需 visual 的
# from-design 编排）。命名规则属领域常量，落 core 层两侧皆可单向依赖，环即断。
DEFAULT_COVER_NAME = "cover.png"


def cover_filename(platform: str | None) -> str:
    """平台 → 封面落盘文件名（文章目录内相对名，即写回 frontmatter 的值）。"""
    return DEFAULT_COVER_NAME if platform in (None, "mp") else f"cover.{platform}.png"

from ...platforms.profiles import PLIMITS
from ..content.parser import parse_article
from .cards import raster as cards_raster
from .cards import templates as cards_templates

# 路线名（CLI 旗标归一后的内部枚举；from-image 的源图路径由调用方另行传入）
# from-design（F71 桥，设计 §八-3 已批）：读 <stem>.design/cover.svg → B 路光栅
# → 目标规格——第五路线只增不删（§七 快照纪律：既有四路线基线不变）。
# 注：from-design 的实际光栅/兜底编排在 services/visual_service（需 infra 光栅
# 件与 C 路重建，domain 层不承担服务编排；本层只声明枚举与设计稿路径定位）。
ROUTES = ("title-card", "from-cards", "from-poster", "from-image", "from-design")

# 默认平台：未给 --platform 时按 mp 规格派生（§二 F31「默认（mp 规格或未给）」）
DEFAULT_PLATFORM = "mp"

# Pillow 兜底直绘的 CJK 字体候选（macOS/Linux 常见路径；全缺失时退默认位图字体，
# 尺寸断言不受影响——兜底路径的产品约束是「无浏览器也有封面」而非排版完美）
_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)


def cover_size(platform: str | None) -> tuple[int, int]:
    """平台 → (cover_w, cover_h)；None 按 mp（默认规格）。

    规格只取 platforms.profiles PLIMITS（§一 表唯一事实源）；未知平台与
    zhihu（无强制封面，不设键）抛可读 ValueError——zhihu 明确提示不派生。
    """
    code = platform or DEFAULT_PLATFORM
    limits = PLIMITS.get(code)
    if limits is None:
        known = "/".join(sorted(PLIMITS))
        raise ValueError(f"未知平台: {code}（已知平台: {known}）")
    w, h = limits.get("cover_w"), limits.get("cover_h")
    if not (isinstance(w, int) and isinstance(h, int)):
        raise ValueError(
            f"{code} 无强制封面规格（§一 表），不派生封面"
            "（知乎头图自由比例，可直接用任意图片）"
            if code == "zhihu"
            else f"{code} 未配置封面规格（cover_w/cover_h）"
        )
    return w, h


# ── title-card：HTML 模板（复用 cards 管线）+ Pillow 兜底直绘 ──


def build_title_card_html(
    article_path: str | Path,
    *,
    size: tuple[int, int],
    theme: str = "lapis",
    title: str | None = None,
    hook: str | None = None,
) -> str:
    """标题字卡 HTML：复用 cards 的 cover_card 模板（大标题+副标钩子）单卡成页。

    尺寸容器与 data-w/data-h 由 cards 模板注入，光栅层（cards.raster.shoot）
    按 `.musespiral-card` 容器 element.screenshot 直出目标尺寸——这就是
    「复用 cards 渲染管线」的接驳点。title/hook 缺省自文章解析取材。
    """
    t, h = _title_and_hook(article_path, title, hook)
    palette = cards_templates.load_palette(theme)
    section = cards_templates.cover_card(t, h, size=size, palette=palette)
    return cards_templates.document([section], title=t)


def draw_title_card(
    article_path: str | Path,
    *,
    size: tuple[int, int],
    theme: str = "lapis",
    title: str | None = None,
    hook: str | None = None,
) -> Image.Image:
    """Pillow 兜底直绘标题字卡（无浏览器环境的保底路径）。

    版式镜像 cards cover_card：色板底色 + primary 强调条 + 大标题 + 钩子
    副标；字号按宽度等比（1242 宽对应 cards 的 56/40px 基准）。CJK 字体
    全缺失时退位图字体（仍产出精确尺寸的合法 PNG）。
    """
    t, h = _title_and_hook(article_path, title, hook)
    palette = cards_templates.load_palette(theme)
    w, height = size
    img = Image.new("RGB", (w, height), palette["background"])
    draw = ImageDraw.Draw(img)

    margin = max(48, round(w * 96 / 1242))  # 安全边距：1242 宽对应 cards 的 96px
    # 强调条：cards cover_card 的 96×12 圆角条按宽度等比
    bar_w, bar_h = round(w * 96 / 1242), max(4, round(w * 12 / 1242))
    draw.rounded_rectangle(
        [margin, margin, margin + bar_w, margin + bar_h],
        radius=max(2, bar_h // 2),
        fill=palette["primary"],
    )

    title_font = _load_font(round(w * 56 / 1242))
    hook_font = _load_font(round(w * 40 / 1242))
    y = margin + bar_h + round(margin * 0.5)
    for line in _wrap_text(t, title_font, w - 2 * margin):
        draw.text((margin, y), line, font=title_font, fill=palette["text"])
        y += round(title_font.size * 1.35)
        if y > height:  # 与卡片容器 overflow:hidden 同语义：超高裁掉不报错
            break
    if h:
        y = max(y + round(hook_font.size * 0.8), margin + bar_h + round(margin * 0.5))
        for line in _wrap_text(h, hook_font, w - 2 * margin):
            draw.text((margin, y), line, font=hook_font, fill=palette["muted"])
            y += round(hook_font.size * 1.5)
            if y > height:
                break
    return img


def render_title_card(
    article_path: str | Path,
    *,
    size: tuple[int, int],
    theme: str = "lapis",
) -> Image.Image:
    """标题字卡 → PIL Image：先试 cards 光栅（浏览器），缺浏览器兜底 Pillow 直绘。

    BrowserNotInstalled 是预期环境（本仓 venv 默认不带 playwright），静默降级
    到 draw_title_card——封面必须有产出，这与 cards 命令「报错给两条出路」
    不同（那里产物本身依赖浏览器，这里有不依赖浏览器的等价路径）。
    """
    html = build_title_card_html(article_path, size=size, theme=theme)
    article_file = Path(article_path)
    try:
        with tempfile.TemporaryDirectory(prefix="musespiral-cover-") as tmp:
            html_path = Path(tmp) / "title-card.html"
            html_path.write_text(html, encoding="utf-8")
            pngs = cards_raster.shoot(html_path, tmp)
            with Image.open(pngs[0]) as im:
                return _to_rgb(im)
    except Exception:
        # 缺 playwright（BrowserNotInstalled）→ Pillow 兜底；其余异常（截图
        # 中途失败等）同样兜底直绘，保证封面生成不因浏览器环境劣化而失败
        return draw_title_card(article_path, size=size, theme=theme)


# ── 三条裁剪路线：取材定位 + 裁剪 ────────────────────────────


def first_card_path(article_path: str | Path) -> Path:
    """<stem>.cards/ 首卡路径（目录内排序后首张 PNG——cards 光栅按
    `<stem>-01.png` 递增命名，排序首张即首卡）。目录不存在或无 PNG 抛
    FileNotFoundError（拒绝静默回退，与 pack/cards 同语义）。"""
    cards_dir = Path(article_path).parent / f"{Path(article_path).stem}.cards"
    if not cards_dir.is_dir():
        raise FileNotFoundError(
            f"卡组目录不存在: {cards_dir}（--from-cards 需先 musespiral cards 生成）"
        )
    pngs = sorted(cards_dir.glob("*.png"))
    if not pngs:
        raise FileNotFoundError(f"卡组目录内无 PNG: {cards_dir}")
    return pngs[0]


def poster_png_path(article_path: str | Path) -> Path:
    """<stem>.poster.png 长图路径；不存在抛 FileNotFoundError。"""
    poster = Path(article_path).with_name(f"{Path(article_path).stem}.poster.png")
    if not poster.exists():
        raise FileNotFoundError(
            f"长图不存在: {poster}（--from-poster 需先 musespiral poster 生成）"
        )
    return poster


def crop_box_center(src_w: int, src_h: int, ratio: float) -> tuple[int, int, int, int]:
    """中心裁到目标宽高比的裁剪框（纯函数）：等比留最大矩形，居中取。

    ratio = 目标宽/目标高。源比源更宽（w/h > ratio）时左右裁，否则上下裁。
    """
    if src_w / src_h > ratio:
        crop_h = src_h
        crop_w = round(src_h * ratio)
    else:
        crop_w = src_w
        crop_h = round(src_w / ratio)
    x0 = (src_w - crop_w) // 2
    y0 = (src_h - crop_h) // 2
    return (x0, y0, x0 + crop_w, y0 + crop_h)


def center_crop_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """中心裁到目标宽高比 → 缩放到精确 (w, h)（§二 F31 裁剪纪律）。"""
    w, h = size
    box = crop_box_center(im.width, im.height, w / h)
    return _to_rgb(im.resize(size, Image.Resampling.LANCZOS, box=box))


def top_crop_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """头区裁（from-poster）：自顶部按目标宽高比取最大矩形再缩放。

    长图高度不足以整比时退中心裁（crop_box_center），保证输出恒为精确规格
    且不拉伸变形。
    """
    w, h = size
    need_h = round(im.width * h / w)
    if im.height >= need_h:
        return _to_rgb(
            im.resize(size, Image.Resampling.LANCZOS, box=(0, 0, im.width, need_h))
        )
    return center_crop_resize(im, size)


# ── 统一入口 ────────────────────────────────────────────────


def generate_cover_image(
    article_path: str | Path,
    *,
    route: str = "title-card",
    platform: str | None = None,
    image: str | Path | None = None,
    theme: str = "lapis",
) -> Image.Image:
    """四路线统一生成：文章 → 目标规格 PIL Image（RGB）。

    - route 必须是 ROUTES 之一；from-image 必须给 image 且文件存在
      （FileNotFoundError，拒绝静默换路线）；
    - platform 经 cover_size 取规格（None→mp；zhihu/未知 ValueError）。
    """
    if route not in ROUTES:
        raise ValueError(f"未知封面路线: {route}（可选: {'/'.join(ROUTES)}）")
    if route == "from-design":
        # from-design 的光栅/C 路兜底编排需 infra 光栅件（domain 层不 import），
        # 由 services.visual_service.generate_cover_from_design 承担；此处拒绝
        # 静默落到 from-poster 分支（防误走）。
        raise ValueError(
            "from-design 路线由 services.visual_service 编排（读 <stem>.design/"
            "cover.svg → B 路光栅 → 目标规格），不经 core.visual.cover.generate_cover_image"
        )
    size = cover_size(platform)
    article_file = Path(article_path)

    if route == "title-card":
        return render_title_card(article_file, size=size, theme=theme)

    if route == "from-image":
        if image is None:
            raise ValueError("--from-image 需要源图路径")
        src = Path(image)
        if not src.exists():
            raise FileNotFoundError(f"源图不存在: {src}")
        with Image.open(src) as im:
            return center_crop_resize(im, size)

    if route == "from-cards":
        with Image.open(first_card_path(article_file)) as im:
            return center_crop_resize(im, size)

    # from-poster：头区裁
    with Image.open(poster_png_path(article_file)) as im:
        return top_crop_resize(im, size)


# ── 内部：取材 / 字体 / 换行 / 底色 ─────────────────────────


def title_and_hook(
    article_path: str | Path, title: str | None = None, hook: str | None = None
) -> tuple[str, str]:
    """标题与钩子取材（公开包装：F71 服务层自动取材缺省键复用同口径，
    避免复制 _title_and_hook 的逻辑——语义与 _title_and_hook 完全一致）。"""
    return _title_and_hook(article_path, title, hook)


def _title_and_hook(
    article_path: str | Path, title: str | None, hook: str | None
) -> tuple[str, str]:
    """标题与钩子取材：显式入参优先，缺省自文章（frontmatter.title 兜底
    文件名；钩子=首个正文段的首句——口径同 cards compose 的封面副标）。"""
    if title is not None and hook is not None:
        return title, hook
    article = parse_article(article_path)
    if title is None:
        title = str(
            article.metadata.get("title") or Path(article_path).stem
        )
    if hook is None:
        first_para = next(
            (
                str(b.get("text", "")).strip()
                for b in article.blocks
                if b.get("type") == "paragraph" and str(b.get("text", "")).strip()
            ),
            "",
        )
        hook = _first_sentence(first_para)
    return title, hook


def _first_sentence(text: str) -> str:
    """首个完整句（到句末标点）；无句末标点取首行（与 cards compose 同口径）。"""
    stripped = text.strip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0]
    for i, ch in enumerate(first_line):
        if ch in "。！？!?…":
            return first_line[: i + 1]
    return first_line


def _load_font(px: int) -> ImageFont.ImageFont:
    """按像素取 CJK 字体：候选逐一尝试，全缺失退 Pillow 默认位图字体。"""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """按显示宽度逐字换行（CJK 无空格，逐字符累积到超宽即断行）。"""
    if not text:
        return []
    lines: list[str] = []
    buf = ""
    for ch in text:
        if ch == "\n":
            lines.append(buf)
            buf = ""
            continue
        if font.getlength(buf + ch) > max_width and buf:
            lines.append(buf)
            buf = ch
        else:
            buf += ch
    lines.append(buf)
    return lines


def _to_rgb(im: Image.Image) -> Image.Image:
    """输出纪律：带透明通道拍白底转 RGB（§二 F31「RGBA→RGB 白底」）。"""
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return im.convert("RGB")


__all__ = [
    "DEFAULT_PLATFORM",
    "ROUTES",
    "build_title_card_html",
    "center_crop_resize",
    "cover_size",
    "crop_box_center",
    "draw_title_card",
    "first_card_path",
    "generate_cover_image",
    "poster_png_path",
    "render_title_card",
    "title_and_hook",
    "top_crop_resize",
]
