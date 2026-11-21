"""B 采集扩展集成测试（§五 5.2）：采集调度 tick（T68-1…T68-5）——
due 判定边界/crawl-delay 抬升/dry-run 零网络零落盘/interval 硬门。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from musespiral.infra import fetcher
from musespiral.services import ingest_service as ing
from musespiral.services import sources_service as svc

NOW = datetime(2026, 11, 16, 12, 0, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _rss_source(sid: str = "blog", *, interval: int | None = None, max_items: int | None = None,
                last_refresh: str | None = None, crawl_delay=None) -> dict:
    cfg: dict = {"url": "https://blog.example.com/feed.xml"}
    if interval is not None:
        cfg["interval_minutes"] = interval
    if max_items is not None:
        cfg["max_items"] = max_items
    health: dict = {"status": "unknown", "last_ok": None}
    if last_refresh is not None:
        health["last_refresh"] = last_refresh
    if crawl_delay is not None:
        health["crawl_delay"] = crawl_delay
    return {
        "id": sid, "type": "rss", "label": "rss", "enabled": True,
        "config": cfg, "health": health, "subscriptions": [],
    }


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── T68-1 due 边界（≥ 即 due，含相等）───────────────────────────────


def test_t68_1_due_boundary_inclusive() -> None:
    due = _rss_source(interval=360, last_refresh=_iso(NOW - timedelta(minutes=360)))
    assert ing.is_due(due, now=NOW) is True  # 边界=相等 → due
    not_due = _rss_source(interval=360, last_refresh=_iso(NOW - timedelta(minutes=359)))
    assert ing.is_due(not_due, now=NOW) is False
    never = _rss_source(interval=360)  # last_refresh 缺失 → due
    assert ing.is_due(never, now=NOW) is True


# ── T68-2 crawl-delay 抬升下限（max(interval, ceil(delay/60))）──────


def test_t68_2_crawl_delay_raises_floor() -> None:
    source = _rss_source(interval=360, crawl_delay=86400,
                         last_refresh=_iso(NOW))  # 24h delay → 下限抬至 1440min
    assert ing.is_due(source, now=NOW + timedelta(minutes=1000)) is False  # 1000min 仍不到
    assert ing.is_due(source, now=NOW + timedelta(minutes=1441)) is True  # 1441min due


# ── T68-3 dry-run：[due]/[skip] 各一 + 零网络 + last_refresh 逐字节不变 ──


def test_t68_3_dry_run_zero_network_zero_write(env, monkeypatch) -> None:
    calls: list[str] = []

    def _no_network(url, *, mode="auto"):
        calls.append(url)
        raise AssertionError("dry-run 不得出网")

    monkeypatch.setattr(ing.fetcher, "fetch", _no_network)
    svc.register_source(_rss_source("due-src", last_refresh="2026-11-01T00:00:00"))
    svc.register_source(_rss_source(
        "fresh-src", last_refresh=_iso(datetime.now() - timedelta(minutes=5))))
    before = (env / "sources.json").read_text(encoding="utf-8")
    rows = ing.tick(dry_run=True)
    by_id = {r["source_id"]: r for r in rows}
    assert by_id["due-src"]["action"] == "due"
    assert by_id["fresh-src"]["action"] == "skip" and by_id["fresh-src"]["reason"] == "未到"
    assert calls == []  # mock fetcher 零调用
    assert (env / "sources.json").read_text(encoding="utf-8") == before  # 逐字节不变
    assert not (env / "materials" / "materials.jsonl").exists()  # materials 零追加


# ── T68-4 实跑：due 源刷新 + last_refresh 前进 + result 三键 ─────────


def test_t68_4_tick_refreshes_due_source(env, monkeypatch) -> None:
    body = (
        '<rss version="2.0"><channel><title>t</title>'
        '<item><title>一</title><link>https://blog.example.com/1/</link>'
        "<guid>urn:uuid:tick-1</guid></item></channel></rss>"
    ).encode()

    def _fake(url, *, mode="auto"):
        return fetcher.FetchResult(url, 200, "application/rss+xml", body,
                                   {"state": "allowed", "crawl_delay": None})

    monkeypatch.setattr(ing.fetcher, "fetch", _fake)
    svc.register_source(_rss_source("blog", last_refresh="2020-01-01T00:00:00"))
    rows = ing.tick(dry_run=False)
    assert len(rows) == 1
    result = rows[0]
    assert result["status"] == "ok" and result["added"] == 1
    for key in ("added", "skipped", "drifted"):
        assert key in result
    after = svc.get_source("blog")["health"]["last_refresh"]
    assert isinstance(after, str) and after > "2020-01-01T00:00:00"  # ISO after>before


# ── T68-5 interval 下限 15 硬门（防打爆源站）────────────────────────


def test_t68_5_interval_lower_bound_rejected(env) -> None:
    with pytest.raises(ValueError, match="15-10000 的整数"):
        svc.register_source(_rss_source(interval=1))
    with pytest.raises(ValueError, match="15-10000 的整数"):
        svc.register_source(_rss_source(interval=10001))
    # 合法边界值 15/10000 可注册
    svc.register_source(_rss_source("min-ok", interval=15))
    svc.register_source(_rss_source("max-ok", interval=10000))
    assert {s["id"] for s in svc.list_sources() if s["type"] == "rss"} == {"min-ok", "max-ok"}


def test_t68_5b_max_items_bounds(env) -> None:
    with pytest.raises(ValueError, match="1-500 的整数"):
        svc.register_source(_rss_source(max_items=0))
    svc.register_source(_rss_source("ok", max_items=500))
