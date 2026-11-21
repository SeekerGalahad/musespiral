"""F22 小红书图文卡 — 模板/光栅分层（§十八 10.0）。

模板层（templates/compose，纯本地全测）：文章或 adapt 变体 → 单文件卡组
HTML（封面卡 + 内容卡 N + 结尾卡，每卡一个固定尺寸 section）。
光栅层（raster，lazy browser）：复用 browser.session 的
BrowserNotInstalled 与安装提示；未装时 CLI 走 --html-only 产 HTML。

红线沿用：模板产物是纯本地文件；光栅只 file:// 加载、不触网、不新增 secret。
"""
from __future__ import annotations

from .compose import build_cards_html
from .raster import require_browser, shoot

__all__ = ["build_cards_html", "require_browser", "shoot"]
