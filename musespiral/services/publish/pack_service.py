"""发布物料包用例编排：article/variant → pack 四件套。

Phase C 修复轮：pack.build_pack 本体自顶层 pack.py 并入（正典 §2.3 骨架
顶层无 pack.py；限制校验纯规则在 core/quality/pack_checks.py）。物料包四
件套：README.md（平台上传固定指引）、payload.md（逐字段对照说明 + 正文）、
checks.txt（平台限制校验）、cover/（封面复制）。manual_pack 不是妥协，是
「自动化到人工最后一步」的物化（§十四 14.0）：机器做完改写/校验/清单，
人只做「复制粘贴 + 点发布」。

build_pack_for（Phase A 自 studio/actions._action_pack 下沉）：注入平台
档案限制表（platforms.PLIMITS）后委托 build_pack。与原实现的差异：
JobFailure/脱敏/相对路径包装不再出现——异常原样上抛，返回绝对 Path。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import frontmatter

from ...browser.flows.base import read_pack
from ...core.content.parser import extract_video_blocks, parse_article
from ...core.quality.pack_checks import (
    _checks_lines,
    _material_checks_lines,
    _normalize_limits,
)
from ...core.version.diff import diff_result, render_diff_html
from ...platforms import PLIMITS

# variant（adapt 口播稿）正文自带的「描述：」行——payload 头部描述优先取它，
# 与 README 字段对照表「描述→payload 头部描述行」的指引一致（第五期遗留 low 修复）
_DESC_LINE_RE = re.compile(r"^描述[:：]\s*(.+)$", re.MULTILINE)

# ── B3 视频物料包（2026-11-26，三线扩展设计 §3.2/§3.3）─────────────────
# channels/douyin/xhs 三平台在四件套（README/payload/checks/cover）之上，
# 当文章是视频文章（frontmatter.media.kind=video 或正文 :::video 块）时追加
# 视频物料：video.mp4（硬链接或复制——同字节零拷贝）+ poster.jpg + meta.json
# （标题/话题/章节时间点从 [mm:ss](#t=秒) 锚提取）+ README 视频上传步骤。
# 诚实边界：视频号/抖音均无公开草稿 API（与公众号不同），README 明写
# 「导出后人工上传」，绝不假装自动发布。
_VIDEO_PACK_PLATFORMS: tuple[str, ...] = ("channels", "douyin", "xhs")
# 时间戳锚（正典 §9.3 既有语法——章节时间点提取的输入面）
_TS_ANCHOR_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]\(#t=(\d+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
# ffmpeg 首帧兜底（poster 无引用时才尝试；缺席静默跳过——pack 主流程不依赖）
_FFMPEG_TIMEOUT_S = 60


def _video_pack_sources(
    var_meta: dict[str, Any], article: Any, content: str
) -> dict[str, Any] | None:
    """视频源探测（frontmatter.media 优先 > 正文首个 :::video 块）→ None 无。

    设计 §3.2 双入口：frontmatter.media{kind,src,poster,duration}（视频文章
    声明面）或编辑器插入的 :::video{src,poster} 块。variant 的 frontmatter
    同键优先（adapt 产物若保留 media）。"""
    for meta in (var_meta, article.metadata):
        media = meta.get("media")
        if isinstance(media, dict) and str(media.get("kind") or "") == "video":
            src = str(media.get("src") or "").strip()
            if src:
                return {
                    "src": src,
                    "poster": str(media.get("poster") or "").strip() or None,
                    "duration": media.get("duration"),
                }
    for block in extract_video_blocks(content):
        src = str(block.get("src") or "").strip()
        if src:
            return {
                "src": src,
                "poster": str(block.get("poster") or "").strip() or None,
                "duration": None,
            }
    return None


def _chapters_from_anchors(content: str) -> list[dict[str, Any]]:
    """[mm:ss](#t=秒) 锚 → 章节时间点清单（t=秒 int / ts=原文 / label=锚
    所属小节标题——**向上找最近标题行**：时间戳锚通常开讲新小节，章节语义
    是「该时刻进入的段落」，即其上方最近的 ## 标题）。顺序即文档序；label
    缺省空串（锚本身已在正文可回看）。"""
    lines = content.split("\n")
    out: list[dict[str, Any]] = []
    last_heading = ""
    for i, line in enumerate(lines):
        hm = _HEADING_RE.match(line)
        if hm:
            last_heading = hm.group(1).strip()
        for m in _TS_ANCHOR_RE.finditer(line):
            out.append({"t": int(m.group(2)), "ts": m.group(1), "label": last_heading})
    return out


def _link_or_copy(src: Path, dst: Path) -> None:
    """video.mp4 落包：硬链接优先（同卷零拷贝），跨卷回退 copy2（media_store.
    store_media_file 同款判例——GB 级视频不真复制是物料包的成本底线）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _poster_first_frame(video: Path, dst: Path) -> bool:
    """poster 无引用时 ffmpeg 抽首帧兜底；缺席/失败 False（不报错——封面
    缺失由 meta.json 如实记录，README 提示上传时自选封面）。"""
    if shutil.which("ffmpeg") is None:
        return False
    import subprocess

    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vframes", "1", "-q:v", "2", str(dst)],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 0


def _video_pack_readme(platform: str, meta_json: dict[str, Any]) -> str:
    """视频物料包 README 追加段（上传步骤 + 章节时间点表 + 诚实边界）。"""
    chapters = meta_json.get("chapters") or []
    rows = "".join(
        f"| {c['ts']} | `{c['t']}` | {c.get('label') or '—'} |\n" for c in chapters
    )
    honest = (
        "\n> ⚠️ 诚实边界：视频号无公开草稿 API（与公众号不同），本包**不自动上传**——"
        "导出后请人工上传。\n" if platform == "channels" else
        "\n> 提示：该平台无草稿 API，导出后人工上传。\n"
    )
    poster_line = (
        "- poster.jpg：上传为视频封面" if meta_json.get("poster") else
        "- 封面：poster.jpg 缺失（视频无引用封面且首帧抽取不可用），上传时自选"
    )
    return (
        "\n## 视频上传步骤（B3 视频物料）\n\n"
        "1. 上传 `video.mp4` 到平台发布页（拖入或选择文件）；\n"
        f"{poster_line}；\n"
        "3. 标题/话题/描述按 payload.md 头部逐字段粘贴；\n"
        "4. 章节时间点（若有）逐条粘贴进「章节/分段」编辑位：\n\n"
        + (
            f"| 时间 | 秒 | 章节 |\n|---|---|---|\n{rows}\n"
            if rows
            else "（正文无 [mm:ss](#t=) 时间戳锚——无章节时间点）\n"
        )
        + honest
    )


def _build_video_pack(
    pack_dir: Path,
    article_file: Path,
    platform: str,
    video_src: dict[str, Any],
    title: str,
    digest: str,
    tags: list[str],
    content: str,
    raw_limits: dict[str, Any] | None,
) -> list[str]:
    """视频物料三件套落包（video.mp4/poster.jpg/meta.json），返回追加的
    checks.txt 行（物料级：视频存在/大小、douyin 封面尺寸）。"""
    src = Path(video_src["src"])
    if not src.is_absolute():
        src = article_file.parent / src
    if not src.is_file():
        raise FileNotFoundError(
            f"视频源不存在: {video_src['src']}（frontmatter.media/:::video 引用的文件）"
        )
    _link_or_copy(src, pack_dir / "video.mp4")

    poster_rel: str | None = None
    if video_src.get("poster"):
        poster_src = Path(video_src["poster"])
        if not poster_src.is_absolute():
            poster_src = article_file.parent / poster_src
        if poster_src.is_file():
            shutil.copy2(poster_src, pack_dir / "poster.jpg")
            poster_rel = "poster.jpg"
    if poster_rel is None and _poster_first_frame(src, pack_dir / "poster.jpg"):
        poster_rel = "poster.jpg"

    meta_json: dict[str, Any] = {
        "platform": platform,
        "title": title,
        "desc": digest,
        "tags": tags,
        "video": "video.mp4",
        "poster": poster_rel,
        "duration": video_src.get("duration"),
        "chapters": _chapters_from_anchors(content),
        "src": str(video_src["src"]),
    }
    (pack_dir / "meta.json").write_text(
        json.dumps(meta_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # README 追加视频步骤（四件套的 README 先落——追加不覆盖平台指引）
    readme = pack_dir / "README.md"
    if readme.is_file():
        readme.write_text(
            readme.read_text(encoding="utf-8") + _video_pack_readme(platform, meta_json),
            encoding="utf-8",
        )

    video_mb = (pack_dir / "video.mp4").stat().st_size / (1024 * 1024)
    lines = [f"ok 视频物料: video.mp4 {video_mb:.1f}MiB（meta.json 含标题/话题/章节时间点）"]
    # 尺寸检查的 limits：build_pack_for 注入 PLIMITS；直调 build_pack 未传时回退
    # 同名平台档案（cover_w/cover_h 是平台事实而非调用方输入——回退不改变语义）
    lim = raw_limits or PLIMITS.get(platform) or {}
    if platform == "douyin" and lim.get("cover_w") and poster_rel:
        # 抖音封面尺寸检查（B3 任务书）：poster.jpg 偏离 profiles cover_w×cover_h
        # ±25% → WARN（PIL 读尺寸，不可读跳过——mp cover_ratio 同纪律）
        try:
            from PIL import Image

            with Image.open(pack_dir / "poster.jpg") as im:
                w, h = im.size
            target = lim["cover_w"] / lim["cover_h"]
            ratio = w / h
            if not target * 0.75 <= ratio <= target * 1.25:
                lines.append(
                    f"WARN 抖音封面: {w}×{h} 偏离建议 {lim['cover_w']}×{lim['cover_h']}"
                    f"（±25% 容差）——上传前建议裁切"
                )
            else:
                lines.append(f"ok 抖音封面: {w}×{h}（建议 {lim['cover_w']}×{lim['cover_h']}）")
        except Exception:
            pass  # 封面不可读：存在性已有 README 提示，尺寸检查跳过
    return lines


# README.md 各平台固定指引（入口 URL + 粘贴位置 + 字段对应表）。
# 表中字数为设计 §十四 PLIMITS 的建议值，仅指引文案；实际校验以注入 limits 为准。
_PLATFORM_GUIDES: dict[str, str] = {
    "channels": """\
# 视频号（channels）上传指引

## 入口
- 上传入口 URL：<https://channels.weixin.qq.com/platform>（视频号助手·发布图文/视频；个人号也可用手机端「发表视频」）

## 粘贴位置
1. 打开入口 URL 并登录视频号账号，进入「发布」编辑页；
2. 封面：上传本目录 `cover/` 内的图片（如有）；
3. 正文：打开 payload.md，复制对照说明以下的全部内容，粘贴进编辑器正文框；
4. 按「字段对应表」逐字段填入标题/描述/话题；
5. 核对 checks.txt 各项均为 ok 后再点发布。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 视频号标题建议 3-16 字 |
| 描述 | payload.md 头部「描述」行 | 描述 ≤1000 字，粘贴进简介/描述框 |
| 话题 | payload.md 头部「话题」行的 # 标签 | 发布页「添加话题」逐个填入 |
""",
    "xhs": """\
# 小红书（xhs）上传指引

## 入口
- 上传入口 URL：<https://creator.xiaohongshu.com>（小红书创作服务平台·发布笔记）

## 粘贴位置
1. 打开入口 URL 并登录小红书账号，点「发布笔记」；
2. 封面/配图：上传本目录 `cover/` 内的图片（如有），笔记建议 1-18 张图；
3. 正文：打开 payload.md，复制对照说明以下的全部内容，粘贴进笔记正文框；
4. 按「字段对应表」逐字段填入标题与话题；
5. 核对 checks.txt 各项均为 ok 后再点发布。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 笔记标题 ≤20 字 |
| 描述 | payload.md 正文（小红书无独立描述框，正文即描述） | 正文 ≤1000 字 |
| 话题 | payload.md 头部「话题」行的 # 标签 | 粘贴在正文末尾或发布页话题选择，≤10 个 |
| 图片卡 | images-cards/ 内的 PNG（如存在，`musespiral cards` 产物） | 图文笔记逐张上传，文件名序号即卡序 |
""",
    "zhihu": """\
# 知乎（zhihu）上传指引

## 入口
- 上传入口 URL：<https://zhuanlan.zhihu.com/write>（知乎·写文章）

## 粘贴位置
1. 打开入口 URL 并登录知乎账号；
2. 头图：上传本目录 `cover/` 内的图片（如有）；
3. 正文：打开 payload.md，复制对照说明以下的全部内容，粘贴进正文编辑器（富文本，保留段落即可）；
4. 按「字段对应表」填入标题、摘要并绑定话题；
5. 核对 checks.txt 各项均为 ok 后再点发布。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 文章标题 ≤100 字 |
| 描述 | payload.md 头部「描述」行 | 填入发布设置里的「摘要」 |
| 话题 | payload.md 头部「话题」行的 # 标签 | 发布页「绑定话题」逐个选择 |
""",
    "douyin": """\
# 抖音（douyin）上传指引

## 入口
- 上传入口 URL：<https://creator.douyin.com>（抖音创作服务平台·发布视频，个人号走 creator 页面）

## 粘贴位置
1. 打开入口 URL 并登录抖音账号，进入「发布视频」；
2. 封面：上传本目录 `cover/` 内的图片（如有），设为视频封面；
3. 文案：打开 payload.md，复制对照说明以下的全部内容，粘贴进视频简介/文案框；
4. 按「字段对应表」填入标题与话题；
5. 核对 checks.txt 各项均为 ok 后再点发布。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 标题/文案 ≤55 字 |
| 描述 | payload.md 头部「描述」行 | 视频简介 ≤1000 字 |
| 话题 | payload.md 头部「话题」行的 # 标签 | 发布页「添加话题」逐个填入 |
""",
    "juejin": """\
# 掘金（juejin）上传指引

## 入口
- 上传入口 URL：<https://juejin.cn/editor/drafts/new>（掘金·写文章）

## 粘贴位置
1. 打开入口 URL 并登录掘金账号；
2. 封面：上传本目录 `cover/` 内的图片（如有）；不上传时平台会自动抓正文首图兜底；
3. 正文：打开 payload.md，复制对照说明以下的全部内容，粘贴进正文编辑器（Markdown 模式，代码块原样保留）；
4. 按「字段对应表」填入标题并选择分类/标签；
5. 核对 checks.txt 各项均为 ok 后再点发布（掘金标题/封面限制以 platform list 为准）。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 字限见 checks.txt / platform list（软限，超长编辑器可能截断） |
| 描述 | payload.md 正文（掘金无独立摘要框，正文即内容） | 正文过短（少于 250 字左右）不被社区推荐，建议导语后保留完整论述 |
| 话题 | payload.md 头部「话题」行的 # 标签 | 编辑器「添加标签」逐个选择（技术分类标签更利于分发） |
""",
    "bilibili": """\
# B站专栏（bilibili）上传指引

## 入口
- 上传入口 URL：<https://member.bilibili.com/platform/upload/text/edit>（B站创作中心·专栏投稿）

## 粘贴位置
1. 打开入口 URL 并登录B站账号，进入专栏投稿编辑页；
2. 封面：上传本目录 `cover/` 内的图片（如有）；注意 APP 首页会按 3:2 裁切显示，封面主体尽量居中；
3. 正文：打开 payload.md，复制对照说明以下的全部内容，粘贴进专栏正文编辑器；
4. 按「字段对应表」填入标题并添加标签；
5. 核对 checks.txt 各项均为 ok 后再点发布（标题/封面限制以 platform list 为准；标题建议 20-30 字更利于展示）。

## 字段对应表
| 平台字段 | 取 payload 哪一段 | 说明 |
|---|---|---|
| 标题 | payload.md 头部「标题」行 | 字限见 checks.txt / platform list（建议 20-30 字） |
| 描述 | payload.md 正文（专栏无独立摘要框，正文即内容） | 分段小标题保持轻快节奏（亲和化变体已处理） |
| 话题 | payload.md 头部「话题」行的 # 标签 | 编辑页「添加标签」逐个填入 |
""",
}


def build_pack(
    article_path: str | Path,
    platform: str,
    variant: str | Path | None = None,
    limits: dict[str, int] | None = None,
    outdir: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """生成发布清单物料包目录并返回其路径。

    目录四件套：README.md / payload.md / checks.txt / cover/（cover 缺失时跳过，
    不创建 cover/）。B3 视频文章追加视频物料三件套：video.mp4（硬链接或
    复制）/ poster.jpg / meta.json（标题/话题/章节时间点，channels/douyin/
    xhs——见 _VIDEO_PACK_PLATFORMS 注）。

    - variant 显式传入时必须存在（FileNotFoundError，拒绝静默回退原文），
      其内容与标题/标签优先（adapt 产物带 platform/tags，见 §十四 F14）。
    - limits 注入键：title_max / title_min / content_max / tags_max；兼容
      platforms.profiles 键名（title_max_chars/desc_max_chars 等，自动归一）。
    - outdir 为物料包目录本身；缺省在文章旁生成 <stem>.<platform>.pack/。
    """
    if platform not in _PLATFORM_GUIDES:
        raise ValueError(
            f"不支持的平台: {platform}（可用: {'/'.join(_PLATFORM_GUIDES)}）"
        )

    article = parse_article(article_path)
    article_file = Path(article_path)
    default_dir = article_file.parent / f"{article_file.stem}.{platform}.pack"
    pack_dir = Path(outdir) if outdir else default_dir
    if pack_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"物料包目录已存在，拒绝覆盖: {pack_dir}（重打包传 overwrite=true）"
            )
        # D10（2026-11-26）：overwrite=true 删旧重建（重打包场景——目录内容
        # 是生成物可安全重建；用户显式确认后不阻塞）
        shutil.rmtree(pack_dir)

    # payload 内容与字段取材：variant 优先；显式传入但路径不存在是输入错误，拒绝静默回退
    source = "文章正文"
    content = article.raw_body
    var_meta: dict[str, Any] = {}
    if variant is not None:
        var_path = Path(variant)
        if not var_path.exists():
            raise FileNotFoundError(
                f"--variant 路径不存在: {variant}（拒绝静默回退原文，请检查路径）"
            )
        var_post = frontmatter.load(var_path)
        var_meta = dict(var_post.metadata)
        content = var_post.content
        source = f"variant {var_path.name}"

    # 原始 limits 留给物料级校验（cover_w/cover_h 等非字数键归一会丢）；
    # 文本级四键走归一结果（既有行为不变）
    raw_limits = limits
    limits = _normalize_limits(limits)

    title = str(var_meta.get("title") or article.metadata.get("title") or "")
    tags = _normalize_tags(var_meta.get("tags")) or _normalize_tags(
        article.metadata.get("tags")
    )
    desc_line = _DESC_LINE_RE.search(content) if var_meta else None
    digest = str(
        (desc_line.group(1).strip() if desc_line else "")
        or var_meta.get("digest")
        or article.metadata.get("digest")
        or ""
    )
    if not digest:
        digest = _first_prose_line(content)

    pack_dir.mkdir(parents=True)
    (pack_dir / "README.md").write_text(_PLATFORM_GUIDES[platform], encoding="utf-8")
    (pack_dir / "payload.md").write_text(
        _payload_md(platform, title, digest, tags, content, source), encoding="utf-8"
    )

    # 封面（§二 F31 探测优先级）：文章目录 cover.<platform>.png 存在即优先
    # 复制（F31 平台封面，非 mp 不写回 frontmatter——靠这里发现）；否则退
    # frontmatter.cover 指向的文件（存在即复制，不存在跳过不报错）
    cover_src = _find_article_cover(article_file, article.metadata.get("cover"), platform)
    if cover_src is not None:
        cover_dir = pack_dir / "cover"
        cover_dir.mkdir()
        shutil.copy2(cover_src, cover_dir / cover_src.name)

    # 图片卡（F22，§十八）：xhs 图文笔记——文章旁 <stem>.cards/ 目录存在即
    # 整组 PNG 复制入 images-cards/（无 PNG 或目录不存在则不创建该子目录）
    if platform == "xhs":
        cards_dir = article_file.parent / f"{article_file.stem}.cards"
        if cards_dir.is_dir():
            pngs = sorted(cards_dir.glob("*.png"))
            if pngs:
                cards_out = pack_dir / "images-cards"
                cards_out.mkdir()
                for png in pngs:
                    shutil.copy2(png, cards_out / png.name)

    # B3 视频物料包（2026-11-26）：channels/douyin/xhs 遇视频文章追加
    # video.mp4/poster.jpg/meta.json + README 视频步骤（设计 §3.2；先于
    # checks.txt 落盘——douyin 封面尺寸检查要看 poster.jpg）
    video_check_lines: list[str] = []
    if platform in _VIDEO_PACK_PLATFORMS:
        video_src = _video_pack_sources(var_meta, article, content)
        if video_src is not None:
            video_check_lines = _build_video_pack(
                pack_dir, article_file, platform, video_src,
                title, digest, tags, content, raw_limits,
            )

    # checks.txt 后写（F33B）：物料级校验（图数/封面尺寸）要先复制完
    # cover/ 与 images-cards/ 才能核对；limits 只有旧四键时输出与旧版逐行一致
    text_lines = _checks_lines(title, content, tags, limits)
    material_lines = _material_checks_lines(pack_dir, platform, raw_limits)
    if material_lines and text_lines == ["未接入平台限制表"]:
        text_lines = []  # 限制表只含物料级键时不算「未接入」
    (pack_dir / "checks.txt").write_text(
        "\n".join(text_lines + material_lines + video_check_lines) + "\n", encoding="utf-8"
    )

    return pack_dir


def _payload_md(
    platform: str,
    title: str,
    digest: str,
    tags: list[str],
    content: str,
    source: str,
) -> str:
    """payload.md 全文：文首逐字段对照说明 + 正文内容。"""
    topics = " ".join(f"#{t}" for t in tags) if tags else "无"
    header = (
        f"> 【逐字段对照】platform: {platform}（上传步骤见 README.md，各字段取材如下）\n"
        ">\n"
        f"> - 标题：{title or '无'}\n"
        f"> - 描述：{digest or '无'}\n"
        f"> - 话题：{topics}\n"
        f"> - 正文：对照说明以下的全部内容（内容来源：{source}）\n"
        "\n---\n"
    )
    return header + content.lstrip("\n")


def _find_article_cover(
    article_file: Path, fm_cover: Any, platform: str
) -> Path | None:
    """pack/check 共用的封面取材探测（§二 F31 优先级，文章目录视角）：

    cover.<platform>.png（F31 平台封面落盘名）> frontmatter.cover 指向的文件；
    两者皆无 → None。与 pack_checks._find_pack_cover（pack cover/ 目录视角）
    同一条优先级，两个视角各查各的目录。
    """
    platform_cover = article_file.parent / f"cover.{platform}.png"
    if platform_cover.is_file():
        return platform_cover
    cover = str(fm_cover or "")
    if cover:
        cand = Path(cover) if Path(cover).is_absolute() else article_file.parent / cover
        if cand.exists():
            return cand
    return None


def _normalize_tags(raw: Any) -> list[str]:
    """frontmatter.tags 归一为字符串列表（单个字符串视为一个标签）。"""
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw if str(t).strip()]
    return []


def _first_prose_line(content: str) -> str:
    """正文首个非空、非标题行（描述兜底取材）。"""
    for line in content.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def build_pack_for(
    article_path: str | Path,
    platform: str,
    *,
    variant: str | Path | None = None,
    outdir: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """生成发布物料包目录（README.md / payload.md / checks.txt / cover/）。

    variant 显式传入时必须存在（FileNotFoundError，拒绝静默回退原文）；
    目标目录已存在拒绝覆盖（FileExistsError）；不支持的平台 ValueError
    ——全部透传 build_pack 的异常语义。limits 由 platform 的能力
    档案（PLIMITS）注入，调用方无需关心键名归一。
    """
    return build_pack(
        article_path,
        platform,
        variant=variant,
        limits=PLIMITS.get(platform),
        outdir=outdir,
        overwrite=overwrite,
    )


# ── 读侧：物料包并排预览（N-5，正典=《mdpub-优化重构与后续开发总体方案-
# 2026-11-19》§二 N-5「I5 多平台并排预览」）───────────────────────────
#
# 只读零写面：清单 + 单平台预览（payload.md 头部字段 + 正文 + 与原文的
# 红删绿增 diff）。两处既有实现**只复用不重造**——payload.md 解析复用
# browser.flows.base.read_pack（browser 侧填表取材的同一份解析器，服务层
# 向下 import infra 合法；playwright 在 session 内 lazy import，本路径不触），
# diff 复用 core.version.diff（E-6 /api/article/diff 同款引擎与 HTML 产物面）。

class PackPreviewError(ValueError):
    """物料包预览业务错误（可读中文；表现层转 400）。"""


# 可成 pack 的平台集 = _PLATFORM_GUIDES 键（build_pack 的白名单同源；registry
# manual_pack 六平台与它的对拍由 tests/test_uploader_browser.py 看守）。
# platform 参数以此做白名单——它要拼进目录名 `<stem>.<platform>.pack`，
# 不白名单即放行 `../` 穿越形态。
PACK_PLATFORM_CODES: tuple[str, ...] = tuple(_PLATFORM_GUIDES)


def _pack_dir(article_file: Path, platform: str) -> Path:
    """物料包目录约定（与 build_pack 缺省落点同一命名，单一事实源=本函数 + build_pack 内联式）。"""
    return article_file.parent / f"{article_file.stem}.{platform}.pack"


def pack_inventory(article_path: str | Path) -> list[dict[str, Any]]:
    """文章的物料包清单：每个可成包平台的 `{platform, dir, available}`。

    `available` 以 payload.md 存在为准（目录在而 payload 缺 = 残包，不可用
    ——与 pack_preview 的 note 口径一致）；`dir` 仅在目录存在时给目录名。
    """
    article_file = Path(article_path)
    out: list[dict[str, Any]] = []
    for code in PACK_PLATFORM_CODES:
        d = _pack_dir(article_file, code)
        out.append(
            {
                "platform": code,
                "dir": d.name if d.is_dir() else None,
                "available": (d / "payload.md").is_file(),
            }
        )
    return out


def pack_preview(article_path: str | Path, platform: str) -> dict[str, Any]:
    """单平台物料包预览：payload 字段 + 原文 + 两版 diff（红删绿增 HTML）。

    - platform 不在白名单 → PackPreviewError（表现层转 400；同时挡路径穿越）；
    - 物料包不存在 / 缺 payload.md → **200 形状** `available:false` + 可读
      note（前端「尚未适配」提示面，不报错不打断切平台）；
    - B3（2026-11-26）：`media` 键——视频物料包的媒体清单与体积（前端打包
      预览列清单用；无视频物料=空列表）；
    - diff 口径：原文取 `parse_article(...).raw_body`（与 pack 落盘时同源），
      两侧 `.strip()` 后逐行比（行尾/首尾空白不是「改了哪一段」；无 variant
      的 pack 正文=原文，两侧 strip 后 identical）；渲染层对替换块配对行做
      **行内字符级精化**（diff.py `_inline_body`，批4 复核④——「改一个字只
      高亮那个字」，共同字符以普通文本呈现）。
    """
    if platform not in PACK_PLATFORM_CODES:
        raise PackPreviewError(
            f"不支持的平台: {platform}（可用: {'/'.join(PACK_PLATFORM_CODES)}）"
        )
    article_file = Path(article_path)
    d = _pack_dir(article_file, platform)
    out: dict[str, Any] = {
        "platform": platform,
        "dir": d.name if d.is_dir() else None,
        "available": False,
        "note": "",
        "payload": None,
        "original": None,
        "diff": None,
        "media": [],
    }
    if not d.is_dir():
        out["note"] = "尚未适配——该平台还没有物料包（先在「操作」页生成）。"
        return out
    try:
        fields = read_pack(d)
    except FileNotFoundError:
        out["note"] = "物料包缺 payload.md（目录存在但不完整），重新生成后再试。"
        return out
    except UnicodeDecodeError:
        out["note"] = "payload.md 不是 UTF-8 文本，无法预览。"
        return out
    try:
        original = parse_article(article_file).raw_body
    except Exception as exc:  # 坏 frontmatter/IO → 可读拒绝（不 500）
        raise PackPreviewError(f"文章解析失败，无法预览原文：{exc}") from None
    result = diff_result(
        original.strip(),
        fields.body,
        from_label="原文",
        to_label=f"{platform} 平台版本",
    )
    out.update(
        available=True,
        payload={
            "title": fields.title,
            "desc": fields.desc,
            "tags": list(fields.tags),
            "body": fields.body,
        },
        original=original,
        media=_pack_media_inventory(d),
        diff={
            **result.to_dict(),
            "html": render_diff_html(
                result, from_label="原文", to_label=f"{platform} 平台版本"
            ),
        },
    )
    return out


# B3：媒体清单（打包预览「列媒体清单与体积」的数据面）。固定名三件
# （video.mp4/poster.jpg/meta.json）+ images-cards/ 内 PNG——全部真实
# stat 体积；不存在的固定名跳过（如无封面视频包）。
_PACK_MEDIA_NAMES: tuple[str, ...] = ("video.mp4", "poster.jpg", "meta.json")


def _pack_media_inventory(pack_dir: Path) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    for name in _PACK_MEDIA_NAMES:
        p = pack_dir / name
        if p.is_file():
            media.append({"file": name, "bytes": p.stat().st_size})
    cards = pack_dir / "images-cards"
    if cards.is_dir():
        for png in sorted(cards.glob("*.png")):
            media.append({"file": f"images-cards/{png.name}", "bytes": png.stat().st_size})
    return media
