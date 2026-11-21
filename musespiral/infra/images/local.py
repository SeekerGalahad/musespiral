"""本地恒等图床 provider —— upload 返回原路径（R5 缺省，零网络）。

Phase C 自 musespiral/core/images/ 迁入（正典 §八 Phase C），行为零变更。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import CAP_CONTENT, ImageHost


class LocalHost(ImageHost):
    """恒等图床：不做任何上传，URL 即原路径（配置缺省时的兜底）。"""

    name = "local"
    capabilities = frozenset({CAP_CONTENT})

    def upload(self, local_path: str | Path) -> tuple[str, dict[str, Any]]:
        src = str(local_path)
        return src, {"host": self.name, "path": src}
