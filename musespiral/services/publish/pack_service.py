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

import re
import shutil
from pathlib import Path
from typing import Any

import frontmatter

from ...browser.flows.base import read_pack
from ...core.content.parser import parse_article
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
) -> Path:
    """生成发布清单物料包目录并返回其路径。

    目录四件套：README.md / payload.md / checks.txt / cover/（cover 缺失时跳过，
    不创建 cover/）。目标目录已存在时拒绝覆盖（FileExistsError）。

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
        raise FileExistsError(f"物料包目录已存在，拒绝覆盖: {pack_dir}")

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

    # checks.txt 后写（F33B）：物料级校验（图数/封面尺寸）要先复制完
    # cover/ 与 images-cards/ 才能核对；limits 只有旧四键时输出与旧版逐行一致
    text_lines = _checks_lines(title, content, tags, limits)
    material_lines = _material_checks_lines(pack_dir, platform, raw_limits)
    if material_lines and text_lines == ["未接入平台限制表"]:
        text_lines = []  # 限制表只含物料级键时不算「未接入」
    (pack_dir / "checks.txt").write_text(
        "\n".join(text_lines + material_lines) + "\n", encoding="utf-8"
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
        diff={
            **result.to_dict(),
            "html": render_diff_html(
                result, from_label="原文", to_label=f"{platform} 平台版本"
            ),
        },
    )
    return out
