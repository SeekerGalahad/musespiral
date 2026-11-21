"""infra/images —— 图床抽象（R5，正典 §四-R5 + §八 Phase C）。

Phase C 自 musespiral/core/images/ 迁入，行为零变更。providers：local（恒等，
缺省）/ github（Contents API）；微信图床实现在 infra/wechat/uploader.py
（publish 流程内建，token+文章路径注入），本包不聚合 re-export 其名——
uploader 顶部依赖本包 base，import 期互导会成环（见 github.py docstring）。
ImageUploader / UploadError / WeChatHost 请从 musespiral.infra.wechat 导入。

resolve_host 选择次序：文章 frontmatter imagehost > 配置 default > local。

用途边界（诚实声明，防过度设计，正典 §四-R5）：公众号正文图最终仍走微信
uploadimg（微信对外域图的转存机制未验证，见 §六-V1 留真机首跑）；GitHub
图床服务于源笔记图片集中管理/其他平台物料包场景。
"""
from __future__ import annotations

from typing import Any

from .base import (
    CAP_CONTENT,
    CAP_COVER,
    IMAGEHOST_FILE,
    ImageHost,
    imagehost_path,
    load_host_config,
    save_host_config,
)
from .github import GitHubHost
from .local import LocalHost

__all__ = [
    "CAP_CONTENT",
    "CAP_COVER",
    "IMAGEHOST_FILE",
    "ImageHost",
    "GitHubHost",
    "LocalHost",
    "imagehost_path",
    "load_host_config",
    "save_host_config",
    "resolve_host",
]


def resolve_host(article_metadata: dict[str, Any] | None = None) -> ImageHost:
    """按选择次序解析图床：frontmatter imagehost > 配置 default > local。

    article_metadata 为文章 frontmatter 映射（ParsedArticle.metadata 同形）。
    github 需 imagehost.json 配置齐全（owner/repo/token），缺项可读报错；
    wechat 由 publish 流程内建（token 注入），不走此选择器。
    """
    from ..wechat.uploader import WeChatHost  # 延迟导入（见模块 docstring 环说明）

    name = ""
    if article_metadata:
        override = article_metadata.get("imagehost")
        if isinstance(override, str) and override.strip():
            name = override.strip()

    cfg = load_host_config()
    if not name:
        default = cfg.get("default")
        name = default.strip() if isinstance(default, str) else ""

    if not name or name == LocalHost.name:
        return LocalHost()

    if name == GitHubHost.name:
        hosts = cfg.get("hosts")
        gh = hosts.get("github") if isinstance(hosts, dict) else None
        gh = gh if isinstance(gh, dict) else {}
        missing = [k for k in ("owner", "repo", "token") if not gh.get(k)]
        if missing:
            raise ValueError(
                f"github 图床未配置完整（缺 {','.join(missing)}）——"
                "运行 musespiral imagehost config --github-owner ... --github-repo ... --github-token ..."
            )
        return GitHubHost(
            str(gh["owner"]),
            str(gh["repo"]),
            str(gh["token"]),
            branch=str(gh["branch"]) if gh.get("branch") else None,
            cdn=str(gh["cdn"]) if gh.get("cdn") else None,
        )

    if name == WeChatHost.name:
        raise ValueError(
            "wechat 图床由 publish 流程内建管理（token/文章路径注入），"
            "不支持 imagehost 选择；正文图发布请走 musespiral publish"
        )

    raise ValueError(f"未知图床: {name}（可用: local / github）")
