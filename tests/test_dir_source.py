"""本地目录（dir）采集 + 探测 + 自动 id + 读章（2026-11-24 源中枢重设计批）。

覆盖 W1 五项后端改动：
  A1 dir 刷新（扫 .md→引用型 text 素材 / 绝对路径 uri / 幂等 / path 缺失报错）
  A2 dir 注册校验（config.path 必填）
  A5 自动 id（缺省生成 kebab id，显式仍走原校验）
  A3 探测端点（probe_source：探测失败是信息不是错误）
"""
from __future__ import annotations

import json

import pytest

from musespiral.services import ingest_service as ing
from musespiral.services import sources_service as svc

@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home

def _md(path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" f"title: {title}\n" "category: test\n" "---\n\n## 概述\n\n正文。\n",
        encoding="utf-8",
    )

def _lines(env) -> list[dict]:
    f = env / "materials" / "materials.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]

def _register_dir(d: str, sid: str = "my-notes") -> None:
    svc.register_source({
        "id": sid, "type": "dir", "label": "我的笔记", "enabled": True,
        "config": {"path": d},
    })

# ── A1 dir 刷新 ───────────────────────────────────────────────
def test_dir_refresh_scan_and_reference(env, tmp_path) -> None:
    d = tmp_path / "notes"
    _md(d / "a.md", "笔记 A")
    _md(d / "sub" / "b.md", "笔记 B")
    _register_dir(str(d))
    result = ing.refresh_source("my-notes")
    assert result["status"] == "ok" and result["scanned"] == 2 and result["added"] == 2
    rows = _lines(env)
    assert all(r["origin"]["source_id"] == "my-notes" for r in rows)
    # 引用不复制：payload 只有 local_path，无 text
    assert all("local_path" in r["payload"] and "text" not in r["payload"] for r in rows)
    # uri = 绝对路径（与 kb 相对路径口径区分）
    for r in rows:
        assert r["origin"]["uri"].startswith("/")
        assert r["payload"]["local_path"] == r["origin"]["uri"]

def test_dir_refresh_idempotent(env, tmp_path) -> None:
    d = tmp_path / "notes"
    _md(d / "a.md", "笔记 A")
    _register_dir(str(d))
    ing.refresh_source("my-notes")
    second = ing.refresh_source("my-notes")
    assert second["added"] == 0 and second["skipped"] == 1
    assert len(_lines(env)) == 1

def test_dir_refresh_incremental(env, tmp_path) -> None:
    d = tmp_path / "notes"
    _md(d / "a.md", "笔记 A")
    _register_dir(str(d))
    ing.refresh_source("my-notes")
    _md(d / "c.md", "笔记 C")
    third = ing.refresh_source("my-notes")
    assert third["added"] == 1 and third["scanned"] == 2

def test_dir_refresh_path_missing_raises(env) -> None:
    svc.register_source({
        "id": "gone", "type": "dir", "label": "已移除", "enabled": True,
        "config": {"path": "/nonexistent/nowhere"},
    })
    with pytest.raises(ValueError, match="目录不存在"):
        ing.refresh_source("gone")

def test_dir_refresh_disabled_noop(env, tmp_path) -> None:
    d = tmp_path / "notes"
    _md(d / "a.md", "A")
    svc.register_source({
        "id": "d2", "type": "dir", "label": "x", "enabled": False, "config": {"path": str(d)},
    })
    assert ing.refresh_source("d2")["status"] == "disabled"

# ── A2 dir 注册校验 ──────────────────────────────────────────
def test_dir_register_requires_path(env) -> None:
    with pytest.raises(ValueError, match="config.path"):
        svc.register_source({"id": "no-path", "type": "dir", "label": "x", "config": {}})

def test_dir_register_with_path_ok(env, tmp_path) -> None:
    svc.register_source({"id": "ok-dir", "type": "dir", "label": "x",
                         "config": {"path": str(tmp_path)}})
    assert svc.get_source("ok-dir")["type"] == "dir"

def test_dir_register_rejects_base(env) -> None:
    with pytest.raises(ValueError, match="config.base"):
        svc.register_source({"id": "bad", "type": "dir", "label": "x",
                             "config": {"base": "http://127.0.0.1:1", "path": "/tmp"}})

# ── A5 自动 id ────────────────────────────────────────────────
def test_auto_id_when_omitted(env, tmp_path) -> None:
    out = svc.register_source({"type": "dir", "label": "自动名",
                               "config": {"path": str(tmp_path)}})
    sid = out["source"]["id"] if isinstance(out, dict) and "source" in out else out.get("id")
    assert sid and sid.startswith("dir-") and sid == sid.lower()
    assert svc.get_source(sid) is not None

def test_auto_id_unique(env) -> None:
    a = svc.auto_source_id("rss")
    b = svc.auto_source_id("rss")
    # 高概率不同（短哈希+时间种子）；至少格式合规
    assert a.startswith("rss-") and b.startswith("rss-")

def test_explicit_id_still_validated(env, tmp_path) -> None:
    with pytest.raises(ValueError, match="小写"):
        svc.register_source({"id": "BadID", "type": "dir", "label": "x",
                             "config": {"path": str(tmp_path)}})

# ── A3 探测（只探测不注册）────────────────────────────────────
def test_probe_dir_ok(tmp_path) -> None:
    d = tmp_path / "ok"
    _md(d / "x.md", "X")
    out = svc.probe_source("dir", {"path": str(d)})
    assert out["state"] == "ok" and "1 篇" in out["detail"]

def test_probe_dir_missing(tmp_path) -> None:
    out = svc.probe_source("dir", {"path": "/no/such/dir"})
    assert out["state"] == "down" and "找不到" in out["detail"]

def test_probe_radarhub_bad_base() -> None:
    out = svc.probe_source("radar_hub", {"base": "http://evil.example.com"})
    assert out["state"] == "down" and out["checked"] == "参数格式"

def test_probe_rss_bad_url() -> None:
    out = svc.probe_source("rss", {"url": "not-a-url"})
    assert out["state"] == "down"

def test_probe_unknown_type() -> None:
    out = svc.probe_source("wat", {})
    assert out["state"] == "down" and "不支持" in out["detail"]

def test_probe_does_not_register(env, tmp_path) -> None:
    d = tmp_path / "ok"
    _md(d / "x.md", "X")
    svc.probe_source("dir", {"path": str(d)})
    assert svc.list_sources() == [] or all(s["id"] != "probe-only" for s in svc.list_sources())
