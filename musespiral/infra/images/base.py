"""图床抽象基座（R5，正典 §四-R5；参照 doocs/md 多图床）。

Phase C 自 musespiral/core/images/ 迁入（正典 §八 Phase C），行为零变更——
仅 docstring 归属说明更新（相对导入层级不变：infra/images/base.py →
infra/images → infra → musespiral 仍为三层）。

ImageHost 只约束「正文图」语义：upload(local_path) -> (url, meta)。
微信封面素材（add_material → media_id，非 URL）是 wechat 特有能力，
经 CAP_COVER 声明、WeChatHost.upload_cover 暴露，不进基类接口。

imagehost.json（~/.musespiral/，随 MUSESPIRAL_HOME 漂移）结构：
  {"default": "github", "hosts": {"github": {"owner","repo","token",
   "branch"?,"cdn"?}}}
配置文件缺省（不存在）→ 解析侧回退 local（见 __init__.resolve_host）；
存在但损坏（非合法 JSON）→ 可读报错，绝不静默降级（行为诚实红线）。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from ... import config

IMAGEHOST_FILE = "imagehost.json"

# 能力常量：正文图（返回可访问 URL）/ 封面素材（微信 media_id）
CAP_CONTENT = "content"
CAP_COVER = "cover"


class ImageHost(ABC):
    """图床 provider 抽象：name 标识 + capabilities 能力声明 + upload 语义。"""

    name: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[str]] = frozenset({CAP_CONTENT})

    @abstractmethod
    def upload(self, local_path: str | Path) -> tuple[str, dict[str, Any]]:
        """上传本地图片，返回 (可访问 URL, meta)。

        meta 为 provider 自定的元数据（sha/远端路径等），供调用方记账。
        """

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


def imagehost_path() -> Path:
    """图床配置路径：config.home_dir()/imagehost.json（每次现读，随 MUSESPIRAL_HOME 漂移）。"""
    return config.home_dir() / IMAGEHOST_FILE


def load_host_config() -> dict[str, Any]:
    """读 imagehost.json；文件不存在 → {}（缺省回退 local 由 resolve_host 兜底）。"""
    path = imagehost_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"图床配置损坏（{path.name} 非合法 JSON）: 第 {e.lineno} 行 {e.msg}"
        ) from None
    if not isinstance(data, dict):
        raise ValueError(f"图床配置结构错误（{path.name} 顶层应为映射）")
    return data


def save_host_config(data: dict[str, Any]) -> Path:
    """写 imagehost.json（与 config.save_credentials 同法：先建目录再整写）。"""
    path = imagehost_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
