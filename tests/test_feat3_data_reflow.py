"""FEAT-3 数据回流自动化：发布后自动拉 stats（含在看）+ 行内 stats 字段。

- backfill_published_stats：best-effort 不抛错，回填 stats 子对象（含 zaikan）；
- sync_reads：F17 既有顶层三键之外新增 stats 子对象，旧读侧零改动。
全部 monkeypatch：不发真网络、token 走 MUSESPIRAL_HOME 隔离（同 test_analytics）。
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from musespiral import config
from musespiral.infra.storage.jsonl import append_log, summarize
from musespiral.infra.wechat.analytics import backfill_published_stats, sync_reads


def _rec(ts: str, **extra: object) -> dict[str, object]:
    return {
        "ts": ts, "action": "publish", "article": "x.md", "title": "标题",
        "media_id": "m1", "account": "default", **extra,
    }


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._payload


def _seed(monkeypatch, state: Path) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(state))
    state.mkdir(parents=True, exist_ok=True)
    (state / config.CREDENTIALS_FILE).write_text(
        json.dumps({"accounts": {"default": {"appid": "A", "secret": "S"}}, "default": "default"}),
        encoding="utf-8",
    )
    (state / config.TOKEN_FILE).write_text(
        json.dumps({"access_token": "TOK", "expires_at": time.time() + 3600}),
        encoding="utf-8",
    )


def _pub_day(n: int) -> str:
    return str(date.today() - timedelta(days=n))


# ── backfill_published_stats ──────────────────────────────────────────

def test_backfill_writes_stats_with_zaikan(tmp_path: Path, monkeypatch) -> None:
    """发表后回填：stats 子对象含阅读/在看/分享/日期，顶层三键同步，最多取最新一天。"""
    state = tmp_path / "state"
    _seed(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    day = _pub_day(3)
    append_log(log, _rec(day + "T09:00:00", article_url="https://s/a"), platform="mp")

    windows: list[dict] = []

    def fake_post(url, params=None, json=None, timeout=None, **kw):
        windows.append(dict(json))
        return _Resp({"list": [{
            "ref_date": json["begin_date"], "content_url": "https://s/a",
            "detail_list": [{
                "stat_date": json["begin_date"], "read_user": 120, "share_user": 12,
                "like_user": 5, "zaikan_user": 9,
            }],
        }]})

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", fake_post)
    done = backfill_published_stats("https://s/a", log_path=log)

    assert done["ok"] is True
    assert done["reads"] == 120 and done["shares"] == 12
    assert done["likes"] == 5 and done["zaikan"] == 9
    assert done["date"] == windows[-1]["begin_date"]
    assert done["matched"] == 1
    # 发表日 3 天前 → 窗口 [day, 昨天] 共 3 天，逐日 1 天窗
    assert len(windows) == 3
    assert all(w["begin_date"] == w["end_date"] for w in windows)

    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["stats"] == {
        "reads": 120, "shares": 12, "likes": 5, "zaikan": 9, "date": done["date"],
    }
    # 顶层三键仍同步（旧读侧 summarize 零改动）
    assert row["reads"] == 120 and row["shares"] == 12 and row["likes"] == 5
    assert summarize(log)["total_reads"] == 120


def test_backfill_best_effort_no_throw(tmp_path: Path, monkeypatch) -> None:
    """无凭证 / 网络错误 / 无 URL / 无匹配行：一律 ok=False + reason，绝不抛错。"""
    state = tmp_path / "state"  # 空目录 → 无凭证
    monkeypatch.setenv("MUSESPIRAL_HOME", str(state))
    state.mkdir(parents=True, exist_ok=True)

    def no_net(*a, **k):
        raise AssertionError("不应发起网络请求")

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", no_net)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec(_pub_day(2) + "T09:00:00", article_url="https://s/a"))

    done = backfill_published_stats("https://s/a", log_path=log)
    assert done["ok"] is False and "凭证" in done["reason"]
    assert backfill_published_stats("", log_path=log)["ok"] is False
    assert backfill_published_stats("https://s/none", log_path=log)["ok"] is False


def test_backfill_no_data_keeps_log_bytes(tmp_path: Path, monkeypatch) -> None:
    """微信返回空 list（次日才出数据）→ ok=False，日志字节不变。"""
    state = tmp_path / "state"
    _seed(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec(_pub_day(1) + "T09:00:00", article_url="https://s/a"))
    before = log.read_bytes()

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _Resp({"list": []}),
    )
    done = backfill_published_stats("https://s/a", log_path=log)
    assert done["ok"] is False and "尚无" in done["reason"]
    assert log.read_bytes() == before


# ── sync_reads 的 stats 子对象（F17 行为零变更 + FEAT-3 增列）──────────

def test_sync_reads_writes_stats_subobject(tmp_path: Path, monkeypatch) -> None:
    """sync_reads 回填顶层三键（F17 不变）之外，也写 stats 子对象（含在看）。"""
    state = tmp_path / "state"
    _seed(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    day = _pub_day(2)
    append_log(log, _rec(day + "T09:00:00", article_url="https://s/a"))

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _Resp({"list": [{
            "ref_date": day, "url": "https://s/a",
            "int_page_read_count": 77, "share_count": 7, "like_count": 2,
            "zaikan_user": 4,
        }]}),
    )
    st = sync_reads(log)
    assert st["updated"] == 1
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["reads"] == 77 and row["shares"] == 7 and row["likes"] == 2
    assert row["stats"]["zaikan"] == 4
    assert row["stats"]["reads"] == 77
    # 在看不混入 likes（F41 口径）
    assert row["likes"] == 2 and row["stats"]["likes"] == 2
