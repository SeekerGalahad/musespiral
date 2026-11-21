"""平台注册表 —— register / get / list_platforms（§十四 F12）。

`musespiral platform list` 的能力矩阵表（平台/通道/能力/限制摘要）以此为
数据源；导入本模块即完成内置平台注册（当前仅 mp）。
"""
from __future__ import annotations

from . import profiles
from .base import PlatformAdapter
from .wechat import WeChatAdapter

REGISTRY: dict[str, PlatformAdapter] = {}


def register(adapter: PlatformAdapter) -> None:
    """注册适配器（以 adapter.name 为键；重名后者覆盖前者）。"""
    REGISTRY[adapter.name] = adapter


def get(code: str) -> PlatformAdapter:
    """按平台代码取适配器；未注册转成可读 ValueError。"""
    try:
        return REGISTRY[code]
    except KeyError:
        known = "/".join(sorted(REGISTRY)) or "（空）"
        raise ValueError(f"未知平台: {code}（已注册: {known}）") from None


def list_platforms() -> list[PlatformAdapter]:
    """按注册顺序列出全部适配器。"""
    return list(REGISTRY.values())


# 内置平台注册（导入即生效）
register(WeChatAdapter())


class _ManualPackAdapter(PlatformAdapter):
    """manual_pack 路线通用适配器（§14.0 四平台：机器备料 + 人工粘贴发布）。

    validate 做轻量标题限制校验（profiles 的 title_*_chars），完整限制校验
    由 pack 的 checks.txt 承担；build_payload 返回原文中间表示，平台形态
    改写走 adapt 模块（§十四 F14）。（遗留批修复：此前能力矩阵只列 mp，
    四个 manual_pack 平台未进注册表。）
    """

    def __init__(self, name: str, capabilities: set[str]) -> None:
        self.name = name
        self.route = "manual_pack"
        self.capabilities = capabilities

    def validate(self, article, article_path=None):  # type: ignore[no-untyped-def]
        lim = profiles.get_limits(self.name)
        title = str(article.metadata.get("title", ""))
        n = len(title)
        if not title:
            return [("FAIL", "标题为空（frontmatter.title 必填）")]
        max_c = lim.get("title_max_chars")
        min_c = lim.get("title_min_chars")
        if max_c is not None and n > max_c:
            return [("FAIL", f"标题 {n} 字超出 {self.name} {max_c} 字上限")]
        if min_c is not None and n < min_c:
            return [("FAIL", f"标题 {n} 字低于 {self.name} {min_c} 字下限")]
        return [("PASS", f"标题字数合规: {n}")]

    def build_payload(self, article):  # type: ignore[no-untyped-def]
        return {
            "title": str(article.metadata.get("title", "")),
            "content": article.raw_body,
        }


# 内置 manual_pack 平台注册（能力矩阵补全；capabilities 为展示口径）
register(_ManualPackAdapter("xhs", {"text", "image"}))
register(_ManualPackAdapter("channels", {"video", "text"}))
register(_ManualPackAdapter("douyin", {"video", "text"}))
register(_ManualPackAdapter("zhihu", {"text", "image"}))
# F42 两新平台（裁决④）：无公开 API，走 manual_pack + 浏览器上传同型；
# capabilities 沿 zhihu 同款（manual_pack 平台无 API draft）
register(_ManualPackAdapter("juejin", {"text", "image"}))
register(_ManualPackAdapter("bilibili", {"text", "image"}))
