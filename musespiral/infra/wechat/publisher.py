"""WeChat draft publisher — create draft (default) and optionally submit.

Default behaviour: only create a draft via /cgi-bin/draft/add.
The user reviews in the WeChat backend and publishes manually.

`submit_publish` is opt-in and calls /cgi-bin/freepublish/submit.

Phase C 自顶层 musespiral/publisher.py 迁入（正典 §八 Phase C），行为零变更——
发布日志 I/O 经同层 infra/storage/jsonl（publish_log 共用件，正典
§2.3 infra/storage/）。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from ... import config
from ..storage.jsonl import append_log, default_log_path


class PublishError(RuntimeError):
    pass


def create_draft(
    token: str,
    title: str,
    content_html: str,
    thumb_media_id: str,
    author: str = "",
    digest: str = "",
    *,
    article: str = "",
    account: str = "default",
    platform: str = "mp",
    log_path: str | Path | None = None,
) -> str:
    """Create a draft article. Returns the draft media_id.

    On success, appends an {"action": "draft", ...} record to the publish log
    (config.home_dir()/config.PUBLISH_LOG by default; override with log_path).
    platform 透传到日志记录（F13 多平台化，缺省 "mp"）。
    """
    payload = {
        "articles": [
            {
                "article_type": "news",
                "title": title,
                "author": author,
                "digest": digest,
                "content": content_html,
                "content_source_url": "",
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
        ]
    }
    resp = requests.post(
        f"{config.WECHAT_API_BASE}/cgi-bin/draft/add",
        params={"access_token": token},
        json=payload,
        timeout=30,
    )
    data = resp.json()
    media_id = data.get("media_id")
    if not media_id:
        raise PublishError(f"创建草稿失败: {data}")
    append_log(
        log_path if log_path is not None else default_log_path(),
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": "draft",
            "article": article,
            "title": title,
            "media_id": media_id,
            "account": account,
        },
        platform=platform,
    )
    return media_id


def submit_publish(
    token: str,
    draft_media_id: str,
    *,
    article: str = "",
    title: str = "",
    account: str = "default",
    article_url: str = "",
    platform: str = "mp",
    log_path: str | Path | None = None,
) -> str:
    """Submit a draft for publication. Returns publish_id (async).

    On success, appends an {"action": "publish", ...} record to the publish
    log — same shape as draft records, plus publish_id / article_url.
    platform 透传到日志记录（F13 多平台化，缺省 "mp"）。
    """
    resp = requests.post(
        f"{config.WECHAT_API_BASE}/cgi-bin/freepublish/submit",
        params={"access_token": token},
        json={"media_id": draft_media_id},
        timeout=30,
    )
    data = resp.json()
    if data.get("errcode") != 0:
        raise PublishError(f"提交发表失败: {data}")
    publish_id = data["publish_id"]
    append_log(
        log_path if log_path is not None else default_log_path(),
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": "publish",
            "article": article,
            "title": title,
            "media_id": draft_media_id,
            "publish_id": publish_id,
            "article_url": article_url,
            "account": account,
        },
        platform=platform,
    )
    return publish_id


def check_publish_status(token: str, publish_id: str, max_tries: int = 10) -> dict[str, Any]:
    """Poll publish status until done or timeout. Returns final response."""
    for _ in range(max_tries):
        resp = requests.post(
            f"{config.WECHAT_API_BASE}/cgi-bin/freepublish/get",
            params={"access_token": token},
            json={"publish_id": publish_id},
            timeout=30,
        )
        data = resp.json()
        status = data.get("publish_status")
        if status == 0:
            return data
        if status not in (1, None):
            raise PublishError(f"发表失败 status={status}: {data}")
        time.sleep(6)
    raise TimeoutError("发表状态长时间未确认，请到后台发表记录核对")
