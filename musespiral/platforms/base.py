"""平台适配器抽象基类（§十四 F12）。

route 三档即 §14.0 平台路线分档，不越档硬上自动化:
  - official_api: 官方 API 完备，create_draft/publish/status 全实现
  - manual_pack:  无发布 API，机器备料 + 人工粘贴发布，API 三件套显式 NotImplementedError
  - browser:      浏览器自动化（第六期占位，本期不实现）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from ..core.content.parser import ParsedArticle
from ..core.quality.checks import CheckResult

Route = Literal["official_api", "manual_pack", "browser"]


class PlatformAdapter(ABC):
    """所有平台适配器的公共接口。

    子类必须定义三个类属性:
      name:         平台代码（registry 注册键，如 "mp"/"xhs"）
      route:        发布路线分档（§14.0）
      capabilities: 能力集合，{"draft","publish","image","video",...} 的子集

    validate 返回与 checklist.CheckResult 同构的 (level, message) 列表，
    可直接复用 format_report / has_failures 汇报。article_path 为可选参数:
    封面比例等需要解析文章相对路径的检查依赖它，缺省时这些项跳过。
    """

    name: str
    route: Route
    capabilities: set[str]

    @abstractmethod
    def validate(
        self, article: ParsedArticle, article_path: str | Path | None = None
    ) -> list[CheckResult]:
        """按平台限制校验文章，返回 (level, message) 列表。"""

    @abstractmethod
    def build_payload(self, article: ParsedArticle) -> dict[str, Any]:
        """article → 平台载荷（结构化中间表示，纯本地不触网）。"""

    # ---- API 三件套: 仅 official_api 路线的子类实现，基类显式拒绝 ----

    def create_draft(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError(
            f"平台 {self.name} 路线为 {self.route}，不支持 create_draft（仅 official_api 路线实现）"
        )

    def publish(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError(
            f"平台 {self.name} 路线为 {self.route}，不支持 publish（仅 official_api 路线实现）"
        )

    def status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"平台 {self.name} 路线为 {self.route}，不支持 status（仅 official_api 路线实现）"
        )
