"""微信图床上传 —— Phase C 自 musespiral/core/images/wechat.py 迁入（正典 §八
Phase C：uploader.py(re-export) 删除，实现落位 infra/wechat/uploader.py）。

ImageUploader 函数体逐字节保留，仅两类机械调整：
  - 相对导入层级 from .base import → from ..images.base import
    （ImageHost 抽象基座同批迁至 infra/images/）
  - 模块 docstring 与归属说明更新

Content images use /cgi-bin/media/uploadimg  → returns WeChat CDN URL
Cover images use /cgi-bin/material/add_material?type=image → returns media_id

Cache is stored in <article_dir>/<article_stem>.musespiral/meta.json keyed by
file content hash, so re-publishing skips already-uploaded images.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from ... import config
from ..images.base import CAP_CONTENT, CAP_COVER, ImageHost


class UploadError(RuntimeError):
    pass


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_image(path: Path) -> None:
    """Pre-upload validation (constraints moved local, per lesson learned)."""
    if not path.exists():
        raise UploadError(f"图片不存在: {path}")
    size = path.stat().st_size
    if size > config.IMAGE_MAX_BYTES:
        raise UploadError(
            f"图片超过 2MB 限制: {path.name} = {size / 1024 / 1024:.1f}MB"
        )


def _compress_if_needed(path: Path) -> Path:
    """Compress image if it exceeds 2MB; return the (possibly new) path."""
    if path.stat().st_size <= config.IMAGE_MAX_BYTES:
        return path
    with Image.open(path) as img:
        out = path.with_suffix(".compressed.jpg")
        img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
    if out.stat().st_size > config.IMAGE_MAX_BYTES:
        raise UploadError(f"压缩后仍超过 2MB: {out.name}")
    return out


class ImageUploader:
    def __init__(self, token: str, article_path: str | Path) -> None:
        self.token = token
        self.article_path = Path(article_path)
        self.meta_dir = self.article_path.parent / f"{self.article_path.stem}.musespiral"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file = self.meta_dir / "meta.json"
        self.meta: dict[str, Any] = self._load_meta()

    def _load_meta(self) -> dict[str, Any]:
        if self.meta_file.exists():
            try:
                return json.loads(self.meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"images": {}, "cover": None}

    def _save_meta(self) -> None:
        self.meta_file.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _resolve_path(self, src: str) -> Path:
        p = Path(src)
        if not p.is_absolute():
            p = self.article_path.parent / p
        return p

    def upload_content_image(self, src: str) -> str:
        """Upload a content image, return WeChat CDN URL. Cached by hash."""
        path = self._resolve_path(src)
        _validate_image(path)
        path = _compress_if_needed(path)
        sha = _file_hash(path)

        cached = self.meta.get("images", {}).get(src)
        if cached and cached.get("sha") == sha and cached.get("url"):
            return cached["url"]

        with path.open("rb") as f:
            resp = requests.post(
                f"{config.WECHAT_API_BASE}/cgi-bin/media/uploadimg",
                params={"access_token": self.token},
                files={"media": f},
                timeout=30,
            )
        data = resp.json()
        url = data.get("url")
        if not url:
            raise UploadError(f"正文图片上传失败: {data}")

        self.meta.setdefault("images", {})[src] = {"sha": sha, "url": url}
        self._save_meta()
        return url

    def upload_cover(self, src: str) -> str:
        """Upload cover image as permanent material, return media_id. Cached."""
        path = self._resolve_path(src)
        _validate_image(path)
        path = _compress_if_needed(path)
        sha = _file_hash(path)

        cached = self.meta.get("cover")
        if cached and cached.get("sha") == sha and cached.get("media_id"):
            return cached["media_id"]

        with path.open("rb") as f:
            resp = requests.post(
                f"{config.WECHAT_API_BASE}/cgi-bin/material/add_material",
                params={"access_token": self.token, "type": "image"},
                files={"media": f},
                timeout=30,
            )
        data = resp.json()
        media_id = data.get("media_id")
        if not media_id:
            raise UploadError(f"封面上传失败: {data}")

        self.meta["cover"] = {"src": src, "sha": sha, "media_id": media_id}
        self._save_meta()
        return media_id

    def get_cached_url(self, src: str) -> str | None:
        return self.meta.get("images", {}).get(src, {}).get("url")

    def get_cached_cover_media_id(self) -> str | None:
        c = self.meta.get("cover")
        return c.get("media_id") if c else None


class WeChatHost(ImageHost):
    """ImageHost 适配壳：token/文章路径保持注入（publish 流程内建，不经 imagehost 选择）。"""

    name = "wechat"
    capabilities = frozenset({CAP_CONTENT, CAP_COVER})

    def __init__(self, token: str, article_path: str | Path) -> None:
        self.uploader = ImageUploader(token, article_path)

    def upload(self, local_path: str | Path) -> tuple[str, dict[str, Any]]:
        src = str(local_path)
        url = self.uploader.upload_content_image(src)
        return url, {"host": self.name, "src": src}

    def upload_cover(self, src: str) -> str:
        """封面素材上传（CAP_COVER）：返回 media_id（非 URL，故不在基类接口）。"""
        return self.uploader.upload_cover(src)
