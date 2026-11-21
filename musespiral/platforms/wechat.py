"""微信公众号适配器 —— 既有 token/uploader/publisher 的 wrapper（§十四 F12）。

route=official_api（素材/草稿/发表全 API，§14.0）。wrapper 只做编排，
不复制底层逻辑:
  validate      → 按 profiles mp 限制出结果（标题字节/摘要字节/封面比例）
  build_payload → Renderer 渲染 + digest 兜底（frontmatter 缺省取正文前 54 字）
  create_draft  → get_access_token → ImageUploader 上传封面/正文图 → publisher.create_draft
  publish/status → publisher.submit_publish / check_publish_status
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

from .. import config
from ..core.content.parser import ParsedArticle
from ..core.content.renderer import Renderer
from ..core.quality.checks import CheckResult
from ..infra.wechat.publisher import check_publish_status, create_draft, submit_publish
from ..infra.wechat.token import get_access_token
from ..infra.wechat.uploader import ImageUploader
from . import profiles
from .base import PlatformAdapter

_MMBIZ_PREFIX = "https://mmbiz.qpic.cn"  # 微信图床地址，已是它则无需上传
# 正文图 src 从渲染 HTML 收集（与 render_checks._IMG_SRC_RE 同源正则）。
# 真机②实证 P0：解析器按设计**不产 image 块**（行内图留在段落文本，
# parser._BlockCollector.image），按 blocks type=="image" 过滤恒为空——
# 任何带图文章真发布都会零上传、本地 src 残留被微信剥掉（mock 测试用
# 合成 blocks 掩盖了该断裂，真跑才曝）。
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.IGNORECASE)


def publish_flow(
    article: ParsedArticle,
    article_path: str | Path,
    html: str,
    *,
    account: str | None = None,
    log_path: str | Path | None = None,
    echo=None,
) -> str:
    """公众号发布编排的单一实现（cli.publish 与 WeChatAdapter 共用）。

    步骤：封面校验上传 → 正文图**容错**上传替换（单图失败告警不中断，
    与 cli.publish 历史行为一致）→ digest 兜底 → 创建草稿并记日志。
    echo 传 click.echo 时输出进度提示（CLI 路径），缺省静默（适配器路径）。

    （第五期复核 low 修复：此前 cli.publish 与适配器各维护一份编排且宽容
    口径不一致，存在漂移风险——收敛到本函数。）
    """
    say = echo or (lambda *a, **k: None)
    cover = str(article.metadata.get("cover", ""))
    if not cover:
        raise ValueError("缺少封面图（frontmatter.cover 必填）")
    token = get_access_token(account)
    uploader = ImageUploader(token, article_path)
    say("上传封面图...")
    thumb_media_id = uploader.upload_cover(cover)

    content_srcs = [
        s for s in dict.fromkeys(_IMG_SRC_RE.findall(html)) if s and not s.startswith(_MMBIZ_PREFIX)
    ]
    if content_srcs:
        say(f"上传 {len(content_srcs)} 张正文图片...")
    for src in content_srcs:  # 已是 mmbiz 地址的跳过（去重保序，同图多处只传一次）
        try:
            wx_url = uploader.upload_content_image(src)
            html = html.replace(f'src="{src}"', f'src="{wx_url}"')
        except Exception as e:  # 单图失败不中断整篇（cli.publish 历史宽容口径）
            say(f"  ⚠️ 图片上传失败 {src}: {e}")

    title = str(article.metadata.get("title", "无标题"))
    digest = str(article.metadata.get("digest", ""))
    if not digest:
        # 渲染后 HTML 去标签取正文前 54 字（与历史 cli.publish 同口径）
        digest = re.sub(r"<[^>]+>", "", html)[:54].strip()
    say("创建草稿...")
    return create_draft(
        token,
        title,
        html,
        thumb_media_id,
        str(article.metadata.get("author", "")),
        digest,
        article=str(article_path),
        account=account or "default",
        platform="mp",
        log_path=log_path,
    )


class WeChatAdapter(PlatformAdapter):
    """微信公众号（mp）——官方 API 完备路线的首个适配器。"""

    name = "mp"
    route = "official_api"
    capabilities = {"draft", "publish", "image"}

    # ---- 平台限制校验 ----

    def validate(
        self, article: ParsedArticle, article_path: str | Path | None = None
    ) -> list[CheckResult]:
        limits = profiles.get_limits(self.name)
        results: list[CheckResult] = []
        md = article.metadata

        # 标题字节（微信 API 按 UTF-8 字节限制）
        title = str(md.get("title", ""))
        title_bytes = len(title.encode("utf-8"))
        if title_bytes == 0:
            results.append(("FAIL", "标题为空（frontmatter.title 必填）"))
        elif title_bytes > limits["title_max_bytes"]:
            results.append(
                ("FAIL", f"标题超过 {limits['title_max_bytes']} 字节: 当前 {title_bytes} 字节")
            )
        else:
            results.append(("PASS", f"标题长度合规: {title_bytes} 字节"))

        # 摘要字节
        digest = str(md.get("digest", ""))
        digest_bytes = len(digest.encode("utf-8"))
        if digest_bytes > limits["digest_max_bytes"]:
            results.append(
                ("FAIL", f"摘要超过 {limits['digest_max_bytes']} 字节: 当前 {digest_bytes} 字节")
            )
        elif digest_bytes == 0:
            results.append(("WARN", "摘要为空，将自动取正文前 54 字"))
        else:
            results.append(("PASS", f"摘要长度合规: {digest_bytes} 字节"))

        # 封面比例（与 render_checks 同口径: PIL 读尺寸 ±25% 容差；
        # article_path 缺省、封面缺失或不可读时跳过——存在性由 run_checks 覆盖）
        cover = str(md.get("cover", ""))
        if cover and article_path is not None:
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
                target = limits["cover_ratio"]
                tol = limits["cover_ratio_tolerance"]
                low, high = target * (1 - tol), target * (1 + tol)
                if not low <= ratio <= high:
                    results.append(
                        (
                            "WARN",
                            f"封面宽高比 {ratio:.2f}:1 偏离推荐 {target}:1 超 ±{int(tol * 100)}% ({width}×{height})",
                        )
                    )
                else:
                    results.append(
                        ("PASS", f"封面宽高比 {ratio:.2f}:1 在 {target}:1 ±{int(tol * 100)}% 内")
                    )

        return results

    # ---- 平台载荷（结构化中间表示，纯本地）----

    def build_payload(self, article: ParsedArticle) -> dict[str, Any]:
        html = self._build_renderer(article).render(article)
        title = str(article.metadata.get("title", "无标题"))
        digest = str(article.metadata.get("digest", ""))
        if not digest:
            # 与 publish 命令同口径: 渲染后 HTML 去标签取正文前 54 字
            digest = re.sub(r"<[^>]+>", "", html)[:54].strip()
        return {
            "title": title,
            "html": html,
            "digest": digest,
            "author": str(article.metadata.get("author", "")),
            "cover": str(article.metadata.get("cover", "")),
        }

    @staticmethod
    def _build_renderer(article: ParsedArticle) -> Renderer:
        """按 frontmatter > 默认值构建渲染器（与 cli._build_renderer 同口径）。"""
        t = article.metadata.get("theme") or config.DEFAULT_THEME
        ct = article.metadata.get("code_theme") or config.DEFAULT_CODE_THEME
        return Renderer(theme_name=t, code_theme=ct, dark=bool(article.metadata.get("dark", False)))

    # ---- official_api 三件套（编排既有模块）----

    def create_draft(
        self,
        article: ParsedArticle,
        article_path: str | Path,
        *,
        account: str | None = None,
        log_path: str | Path | None = None,
    ) -> str:
        """上传封面/正文图并创建草稿，返回草稿 media_id（编排见模块级 publish_flow）。"""
        payload = self.build_payload(article)
        return publish_flow(
            article, article_path, payload["html"], account=account, log_path=log_path
        )

    def publish(
        self,
        draft_media_id: str,
        *,
        article: str = "",
        title: str = "",
        account: str | None = None,
        log_path: str | Path | None = None,
    ) -> str:
        """提交草稿发表，返回 publish_id（异步）。"""
        token = get_access_token(account)
        return submit_publish(
            token,
            draft_media_id,
            article=article,
            title=title,
            account=account or "default",
            log_path=log_path,
        )

    def status(
        self, publish_id: str, *, account: str | None = None, max_tries: int = 10
    ) -> dict[str, Any]:
        """轮询发表状态直至完成，返回最终响应。"""
        return check_publish_status(get_access_token(account), publish_id, max_tries=max_tries)
