"""GitHub 图床 provider —— Contents API 创建/更新文件（R5，正典 §四-R5 + §六-V3）。

Phase C 自 musespiral/core/images/ 迁入（正典 §八 Phase C），行为零变更——
仅一处结构性调整：UploadError 的导入从模块顶部改为使用点内延迟导入
（其定义在 infra/wechat/uploader.py，而 uploader 顶部依赖本包 base——
顶部互导会构成 import 环：images/__init__ → github → uploader →
images/__init__（部分初始化））。

V3 结论（2026-11-12 curl 实测）：api.github.com 境内直连可用（本机代理反而
不可用）——默认直连，不强制镜像、不配代理前置；需要加速时经配置 cdn 模板
接管「下载 URL」（上传仍直连 API）。

远端路径按内容哈希派生（musespiral/{sha256前16}-{文件名}）——同内容同路径，
422 sha 冲突拉取现 sha 更新覆盖后返回同一 URL（幂等，无重复堆积）。

token 纪律（红线）：只经 Authorization 头传输，绝不进 URL/错误体/日志；
4xx 响应体入错误前截断打码（复用 studio.security / core.jobs.store 的
_SECRET_RE 同源经验，正则三处同步——改动须双侧对拍）。
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .base import CAP_CONTENT, ImageHost

API_BASE = "https://api.github.com"
DEFAULT_BRANCH = "main"
DEFAULT_CDN = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
_TIMEOUT = 30  # 与 wechat 上传同口径

# 秘密值打码（与 studio.security._SECRET_RE / core.jobs.store 同源规则；改动须多处同步）
_SECRET_RE = re.compile(r"(?i)\b(access_token|appid|secret|api_key|token)=([^&\s'\"),]+)")
_MAX_ERROR_CHARS = 200


def _mask(text: str) -> str:
    """错误文本脱敏：敏感值打码 + 截断。"""
    text = _SECRET_RE.sub(r"\1=***", text)
    if len(text) > _MAX_ERROR_CHARS:
        text = text[:_MAX_ERROR_CHARS] + "…"
    return text


class GitHubHost(ImageHost):
    """GitHub Contents API 图床（正文图；封面素材是微信特有概念，不在此）。"""

    name = "github"
    capabilities = frozenset({CAP_CONTENT})

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        *,
        branch: str | None = None,
        cdn: str | None = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.branch = branch or DEFAULT_BRANCH
        self.cdn = cdn or DEFAULT_CDN

    # ---- 内部 ----

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "musespiral",
        }

    def _api_url(self, remote_path: str) -> str:
        return f"{API_BASE}/repos/{self.owner}/{self.repo}/contents/{remote_path}"

    def _download_url(self, remote_path: str) -> str:
        """下载 URL：cdn 模板占位符替换（owner/repo/branch/path），path 做 URL 引用。"""
        return (
            self.cdn.replace("{owner}", self.owner)
            .replace("{repo}", self.repo)
            .replace("{branch}", self.branch)
            .replace("{path}", quote(remote_path))
        )

    def _error(self, action: str, status: int, body: str):
        # 延迟导入（见模块 docstring：uploader ↔ images 顶部互导成环）
        from ..wechat.uploader import UploadError
        hint = ""
        if status in (401, 403):
            hint = "（token 无效/过期或无 contents 写权限）"
        masked = _mask(body)
        if self.token:  # 兜底：token 原值不论以何形态混入响应体，直接替换（红线）
            masked = masked.replace(self.token, "***")
        return UploadError(f"GitHub {action}失败 HTTP {status}{hint}: {masked}")

    def _put(self, remote_path: str, payload: dict[str, Any]) -> requests.Response:
        return requests.put(
            self._api_url(remote_path),
            headers=self._headers(),
            json=payload,
            timeout=_TIMEOUT,
        )

    def _fetch_sha(self, remote_path: str) -> str:
        """422 冲突后拉取远端文件现 sha（GET ?ref=branch）。"""
        from ..wechat.uploader import UploadError  # 延迟导入（同 _error）
        resp = requests.get(
            self._api_url(remote_path),
            headers=self._headers(),
            params={"ref": self.branch},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            raise self._error("获取文件 sha", resp.status_code, resp.text)
        sha = resp.json().get("sha")
        if not sha:
            raise UploadError("GitHub 获取文件 sha 失败: 响应缺 sha 字段")
        return sha

    # ---- ImageHost 接口 ----

    def upload(self, local_path: str | Path) -> tuple[str, dict[str, Any]]:
        """上传本地图片 → (下载 URL, meta)。

        同内容幂等：远端路径按内容哈希派生，已存在（422）时拉 sha 覆盖更新，
        返回同一 URL。
        """
        from ..wechat.uploader import UploadError  # 延迟导入（同 _error）
        path = Path(local_path)
        if not path.exists():
            raise UploadError(f"图片不存在: {path}")
        data = path.read_bytes()
        remote_path = f"musespiral/{hashlib.sha256(data).hexdigest()[:16]}-{path.name}"
        payload: dict[str, Any] = {
            "message": f"musespiral: upload {path.name}",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": self.branch,
        }

        resp = self._put(remote_path, payload)
        if resp.status_code == 422:  # 文件已存在 → 拉 sha 更新覆盖（幂等）
            payload["sha"] = self._fetch_sha(remote_path)
            resp = self._put(remote_path, payload)
        if resp.status_code not in (200, 201):
            raise self._error("上传", resp.status_code, resp.text)

        body = resp.json()
        content = body.get("content") or {}
        meta = {
            "host": self.name,
            "path": remote_path,
            "sha": content.get("sha"),
            "commit": (body.get("commit") or {}).get("sha"),
            "created": resp.status_code == 201,
        }
        return self._download_url(remote_path), meta
