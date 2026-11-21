"""B 采集扩展集成测试（§五 5.2）：rss 源注册→刷新→link 素材（T65-6/7/8/9）
+ 去重更新三段语义（T69-2/3/4/5/6）——mock fetcher，测试不联网。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.infra import fetcher
from musespiral.infra.sources import radarhub
from musespiral.services import ingest_service as ing
from musespiral.services import sources_service as svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _feed_body(titles: list[str], *, second_title_override: str | None = None) -> bytes:
    items = []
    for i, title in enumerate(titles, start=1):
        items.append(
            f"<item><title>{title}</title>"
            f"<link>https://blog.example.com/post-{i}/?utm_source=rss</link>"
            f"<guid>urn:uuid:item-{i}</guid>"
            f"<pubDate>Mon, 0{i} Jan 2026 08:00:00 +0000</pubDate></item>"
        )
    body = ('<?xml version="1.0"?><rss version="2.0"><channel>'
            "<title>测试源</title>" + "".join(items) + "</channel></rss>")
    if second_title_override is not None:
        body = body.replace(f"<title>{titles[1]}</title>",
                            f"<title>{second_title_override}</title>", 1)
    return body.encode("utf-8")


class _FakeFetch:
    """fetcher.fetch 替身：预制 body；记录调用次数与 mode。"""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, *, mode: str = "auto"):
        self.calls.append((url, mode))
        return fetcher.FetchResult(
            final_url=url, status=200, content_type="application/rss+xml",
            body=self.body, robots={"state": "allowed", "crawl_delay": 60},
        )


@pytest.fixture
def fake_fetch(monkeypatch):
    ff = _FakeFetch(_feed_body(["甲", "乙"]))
    monkeypatch.setattr(ing.fetcher, "fetch", ff)
    return ff


def _register_rss(env, *, url: str = "https://blog.example.com/feed.xml",
                  interval=None, max_items=None, sid: str = "zhipu-blog") -> dict:
    cfg: dict = {"url": url}
    if interval is not None:
        cfg["interval_minutes"] = interval
    if max_items is not None:
        cfg["max_items"] = max_items
    return svc.register_source(
        {
            "id": sid, "type": "rss", "label": "测试 RSS", "enabled": True,
            "config": cfg, "health": {"status": "unknown", "last_ok": None},
            "subscriptions": [],
        }
    )


def _lines(env) -> list[dict]:
    path = env / "materials" / "materials.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _raw_lines(env) -> str:
    path = env / "materials" / "materials.jsonl"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── T65-6 刷新产 link 素材（meta 四键 + guid 优先 uri）──────────────


def test_t65_6_refresh_rss_produces_link_materials(env, fake_fetch) -> None:
    _register_rss(env)
    result = ing.refresh_source("zhipu-blog")
    assert result["status"] == "ok" and result["added"] == 2
    assert result["skipped"] == 0 and result["drifted"] == 0
    rows = _lines(env)
    assert all(r["type"] == "link" for r in rows)
    first = rows[0]
    meta = first["payload"]["meta"]
    assert set(("feed_title", "published", "url", "guid")) <= set(meta)  # meta 四键
    assert meta["feed_title"] == "测试源"
    assert meta["published"].startswith("2026-01-0")
    assert meta["url"] == "https://blog.example.com/post-1/"  # canonical 剔 utm
    assert first["origin"]["uri"] == "urn:uuid:item-1"  # guid 优先
    assert first["license"] == "ALL-RIGHTS"  # 无 CC 标记基线（T65-1 口径）
    assert svc.get_source("zhipu-blog")["health"]["status"] == "ok"
    assert svc.get_source("zhipu-blog")["health"]["last_refresh"]  # F65-C-2 增键


def test_t65_6b_fetch_refusal_is_down_zero_materials(env, monkeypatch) -> None:
    """拒/网络错 → status=down+可读 error+零素材零半写（F65-D 红线）。"""
    def _refused(url, *, mode="auto"):
        raise fetcher.RefusedError("robots 不允许（自动流 fail-closed 拒绝）")
    monkeypatch.setattr(ing.fetcher, "fetch", _refused)
    _register_rss(env)
    result = ing.refresh_source("zhipu-blog")
    assert result["status"] == "down" and result["added"] == 0
    assert "robots" in result["error"]
    assert _lines(env) == []
    assert svc.get_source("zhipu-blog")["health"]["status"] == "down"
    assert svc.get_source("zhipu-blog")["health"]["last_refresh"]  # down 也写


def test_t65_6c_bad_feed_is_down_readable_error(env, monkeypatch) -> None:
    from musespiral.infra.sources import rss as rss_feed

    def _bad(url, *, mode="auto"):
        return fetcher.FetchResult(url, 200, "text/html", b"<html><body>hi</body></html>", {})
    monkeypatch.setattr(ing.fetcher, "fetch", _bad)
    _register_rss(env)
    result = ing.refresh_source("zhipu-blog")
    assert result["status"] == "down"
    assert "非 RSS 2.0/Atom" in result["error"] and "换 Atom 链接" in result["error"]
    assert _lines(env) == []


# ── T65-7 注册期 IP 字面量/userinfo 拒 ──────────────────────────────


def test_t65_7_register_rejects_ip_literal_and_userinfo(env) -> None:
    with pytest.raises(ValueError, match="IP 字面量"):
        _register_rss(env, url="http://192.168.1.1/feed.xml")
    with pytest.raises(ValueError, match="用户凭据"):
        _register_rss(env, url="https://u:p@example.com/feed")
    with pytest.raises(ValueError, match="http\\(s\\)"):
        _register_rss(env, url="ftp://example.com/feed")
    assert svc.list_sources() == [] or all(s["type"] != "rss" for s in svc.list_sources())


# ── T65-8 max_items 截首不尾 ────────────────────────────────────────


def test_t65_8_max_items_truncates_head(env, monkeypatch) -> None:
    titles = [f"条目{i}" for i in range(1, 11)]
    monkeypatch.setattr(ing.fetcher, "fetch", _FakeFetch(_feed_body(titles)))
    _register_rss(env, max_items=3)
    result = ing.refresh_source("zhipu-blog")
    assert result["added"] == 3
    rows = _lines(env)
    assert [r["origin"]["title"] for r in rows] == titles[:3]  # 入库=feed 首部
    assert rows[0]["origin"]["title"] == "条目1"  # rows[0] == feed 第 1 条


# ── T69-2 等值重采跳过（旧行逐字节不变）────────────────────────────


def test_t69_2_equal_refetch_skips(env, fake_fetch) -> None:
    _register_rss(env)
    ing.refresh_source("zhipu-blog")
    before = _raw_lines(env)
    second = ing.refresh_source("zhipu-blog")
    assert second["added"] == 0 and second["skipped"] == 2 and second["drifted"] == 0
    assert _raw_lines(env) == before  # 旧行逐字节不变（fetched_at 不前进）


# ── T69-3 漂移=首见冻结（drifted 计数+旧行逐字节不变）───────────────


def test_t69_3_drift_frozen_first_seen_wins(env, monkeypatch) -> None:
    ff = _FakeFetch(_feed_body(["甲", "乙"]))
    monkeypatch.setattr(ing.fetcher, "fetch", ff)
    _register_rss(env)
    ing.refresh_source("zhipu-blog")
    before = _raw_lines(env)
    count_before = len(_lines(env))
    ff.body = _feed_body(["甲", "乙"], second_title_override="乙（站方已编辑）")
    second = ing.refresh_source("zhipu-blog")  # 不 raise
    assert second["added"] == 0 and second["skipped"] == 1 and second["drifted"] == 1
    assert _raw_lines(env) == before  # 旧行逐字节不变（首见冻结）
    assert len(_lines(env)) == count_before  # 素材总数不变


# ── T69-4 墓碑不复活（skipped 计数含墓碑）───────────────────────────


def test_t69_4_tombstone_not_resurrected(env, fake_fetch) -> None:
    from musespiral.services import materials_service as ms

    _register_rss(env)
    ing.refresh_source("zhipu-blog")
    victim = _lines(env)[1]["id"]
    ms.delete_material(victim)
    result = ing.refresh_source("zhipu-blog")
    assert result["added"] == 0
    assert result["skipped"] == 2  # 等值 1 + 墓碑 1
    assert ms.get_material(victim) is None  # 不复活


# ── T69-5 radar 流 fail-fast 回归零变更（drift_policy 只对 rss/clip 生效）──


class _FakeRadarClient:
    scored_path = ""

    def __init__(self, base: str, timeout: float = 3.0) -> None:
        self.base = base

    def radars(self):
        return {"radars": [{"id": "embedded_github_radar"}]}

    def outputs(self, radar_id: str):
        return {"outputs": [{"id": "curated", "files": [
            {"name": "scored.json", "path": _FakeRadarClient.scored_path}]}]}

    def health(self):
        return {"ok": True}


def test_t69_5_radar_drift_still_fail_fast(env, tmp_path, monkeypatch) -> None:
    scored = tmp_path / "scored.json"
    scored.write_text(json.dumps({"items": [
        {"repo": "a/one", "grade": "S", "score": 88,
         "url": "https://github.com/a/one", "license": "CC-BY-4.0"},
    ]}), encoding="utf-8")
    _FakeRadarClient.scored_path = str(scored)
    monkeypatch.setattr(radarhub, "RadarHubClient", _FakeRadarClient)
    svc.register_source({
        "id": "radarhub", "type": "radar_hub", "label": "雷达", "enabled": True,
        "config": {"base": "http://127.0.0.1:8787"},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": ["grade=S"],
    })
    first = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert first["status"] == "ok" and first["added"] == 1
    # 漂移注入：改 scored 条目内容 → 同 id 异内容 → 仍 raise（fail-fast 零变更）
    scored.write_text(json.dumps({"items": [
        {"repo": "a/one", "grade": "S", "score": 99,  # score 漂移
         "url": "https://github.com/a/one", "license": "CC-BY-4.0"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        ing.refresh_source("radarhub", articles_root=tmp_path)


# ── T69-6 rss 刷新 result 形状锁（drifted:int 键）────────────────────


def test_t69_6_result_has_drifted_int_key(env, fake_fetch) -> None:
    _register_rss(env)
    result = ing.refresh_source("zhipu-blog")
    assert isinstance(result.get("drifted"), int) and result["drifted"] == 0
    assert set(("added", "skipped", "drifted")) <= set(result)


# ── 串行锁（复用 _REFRESH_LOCK——T65 组既有资产，B 端点沿用）─────────


def test_refresh_rss_serializes_under_module_lock(env, fake_fetch) -> None:
    import threading

    _register_rss(env)
    held, release, finished = threading.Event(), threading.Event(), threading.Event()
    box = {}

    def blocker() -> None:
        with ing._REFRESH_LOCK:
            held.set()
            assert release.wait(timeout=5)

    def worker() -> None:
        box["result"] = ing.refresh_source("zhipu-blog")
        finished.set()

    t = threading.Thread(target=blocker)
    t.start()
    assert held.wait(timeout=2)
    w = threading.Thread(target=worker)
    w.start()
    assert not finished.wait(timeout=0.3), "rss 刷新在模块锁被占时应阻塞（串行队列）"
    release.set()
    t.join(timeout=5)
    assert finished.wait(timeout=5)
    w.join(timeout=5)
    assert box["result"]["status"] == "ok"
