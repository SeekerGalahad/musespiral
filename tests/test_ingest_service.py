"""采集编排测试（第二十期 §五第 4 行）：services/ingest_service。

覆盖：kb 刷新产 text 素材（引用不复制断言：payload 无 text、local_path
指回 KB 相对路径）/幂等（二跑零追加）/增量（新文档只补新行）/墓碑不复活
（人工删除优先于重采）/失败不半写（批内冲突零落盘）/radar 刷新产
radar_signal（meta 四键 line/grade/score/url + license_note）/grade 多值
与裸词条与「未生成骨架」过滤/跨线同仓去重/宕机=灰灯（status=down+健康
回写，非失败）/订阅非法可读错误/未知条件键可读错误/refresh_all 单源失败
不阻塞/串行锁阻塞行为/jobs kind=ingest 入册。
"""
from __future__ import annotations

import json
import threading

import pytest

from musespiral.infra.sources import radarhub
from musespiral.services import ingest_service as ing
from musespiral.services import sources_service as svc

SUB_SOURCES = [
    {"key": "13-source-analysis", "label": "源码分析", "relpath": "13-source-analysis"},
    {"key": "11-learning-path", "label": "学习路径", "relpath": "11-learning-path"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _doc(path, title: str, tags: str = "[源码分析, Python, 测试, a/b]") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"title: {title}\n"
        "category: 13-source-analysis\n"
        "created: 2020-01-01\n"
        f"tags: {tags}\n"
        "---\n\n## 概述\n\n正文若干行。\n",
        encoding="utf-8",
    )


def _setup_kb(env, tmp_path):
    """建 kb 源（root=临时知识库，双子源）并落两篇文档。返回 kb 根。"""
    kb = tmp_path / "kb"
    _doc(kb / "13-source-analysis" / "a.md", "文档甲")
    _doc(kb / "11-learning-path" / "b.md", "文档乙", tags="[学习路径, 测试]")
    svc.register_source(
        {
            "id": "kb",
            "type": "kb",
            "label": "知识库",
            "enabled": True,
            "config": {"root": str(kb), "sources": SUB_SOURCES},
            "health": {"status": "unknown", "last_ok": None},
            "subscriptions": [],
        }
    )
    return kb


def _lines(env):
    path = env / "materials" / "materials.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── kb 源刷新（scan 管道 → text 素材，引用不复制）──────────────────


def test_kb_refresh_reference_not_copy(env, tmp_path) -> None:
    kb = _setup_kb(env, tmp_path)
    result = ing.refresh_source("kb")
    assert result["status"] == "ok" and result["added"] == 2
    rows = _lines(env)
    by_uri = {r["origin"]["uri"]: r for r in rows}
    assert set(by_uri) == {"13-source-analysis/a.md", "11-learning-path/b.md"}  # KB 相对路径
    for row in rows:
        assert row["type"] == "text"
        assert row["origin"]["source_id"] == "kb"
        assert "text" not in row["payload"]  # 引用不复制：正文不入库
        assert row["payload"]["local_path"] == row["origin"]["uri"]  # 指回 KB 文档
        assert not (kb / row["origin"]["uri"]).is_dir()


def test_kb_refresh_idempotent_and_incremental(env, tmp_path) -> None:
    kb = _setup_kb(env, tmp_path)
    first = ing.refresh_source("kb")
    second = ing.refresh_source("kb")
    assert second["added"] == 0 and second["skipped"] == 2  # 幂等：零追加
    assert len(_lines(env)) == 2
    _doc(kb / "13-source-analysis" / "c.md", "文档丙")
    third = ing.refresh_source("kb")
    assert third["added"] == 1 and third["skipped"] == 2  # 只补新行
    assert len(_lines(env)) == 3


def test_kb_refresh_idempotent_across_time(env, tmp_path, monkeypatch) -> None:
    """跨秒/跨日重采幂等（生产每日调度形态）：fetched_at 前进不得撞 immutable。

    回归锚（2026-11-16 真 radar-hub 复测抓出，kb/radar 同病）：重采的
    fetched_at 必然前进，既有实现把全部候选交 add_many 全行比较，等值永不
    成立 → 稳定 ValueError；背靠背同秒跑的既有幂等测试走运相等掩盖了缺陷。
    """
    _setup_kb(env, tmp_path)
    clock = {"t": "2026-11-15T04:30:00"}
    monkeypatch.setattr(ing, "_now_iso", lambda: clock["t"])
    ing.refresh_source("kb")
    clock["t"] = "2026-11-16T04:30:00"  # 次日调度：采集时钟前进
    second = ing.refresh_source("kb")
    assert second["added"] == 0 and second["skipped"] == 2
    rows = _lines(env)
    assert len(rows) == 2  # 既有行零追加
    assert all(r["fetched_at"] == "2026-11-15T04:30:00" for r in rows)  # 零改写


def test_kb_refresh_tombstone_not_resurrected(env, tmp_path) -> None:
    from musespiral.services import materials_service as ms

    _setup_kb(env, tmp_path)
    ing.refresh_source("kb")
    target = _lines(env)[0]["id"]
    ms.delete_material(target)
    assert len(_lines(env)) == 3  # 墓碑行追加
    result = ing.refresh_source("kb")
    assert result["added"] == 0  # 已删行不复活
    assert result["skipped"] == 2  # 墓碑 1 + 存量等值 1
    assert ms.get_material(target) is None


def test_kb_refresh_conflict_is_zero_write(env, tmp_path) -> None:
    """失败不半写：库内同 id 异内容 → 整批零落盘（新增行也不写）。"""
    from musespiral.core.materials import model

    kb = _setup_kb(env, tmp_path)
    ing.refresh_source("kb")
    # 预置冲突：同 id 不同 title（模拟既有行与重采内容漂移）
    conflict = model.make_material(
        mtype="text",
        source_id="kb",
        uri="13-source-analysis/a.md",
        title="冲突版",
        payload={"local_path": "13-source-analysis/a.md"},
        fetched_at="2020-01-01T00:00:00",
    )
    # 冲突行落进库（手工追加模拟既有行与重采漂移——同 id 异内容）
    (env / "materials").mkdir(parents=True, exist_ok=True)
    with (env / "materials" / "materials.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(conflict, ensure_ascii=False) + "\n")
    _doc(kb / "13-source-analysis" / "c.md", "文档丙")  # 本应新增的行
    before = len(_lines(env))
    with pytest.raises(ValueError, match="immutable"):
        ing.refresh_source("kb")
    assert len(_lines(env)) == before  # 零残留（c.md 未入库）


def test_kb_refresh_missing_root_readable_error(env) -> None:
    svc.register_source(
        {
            "id": "kb",
            "type": "kb",
            "label": "知识库",
            "enabled": True,
            "config": {"root": None, "sources": []},
            "health": {"status": "unknown", "last_ok": None},
            "subscriptions": [],
        }
    )
    with pytest.raises(ValueError, match="root"):
        ing.refresh_source("kb")
    assert _lines(env) == []


def test_refresh_disabled_source_is_noop(env, tmp_path) -> None:
    _setup_kb(env, tmp_path)
    svc.set_enabled("kb", False)
    result = ing.refresh_source("kb")
    assert result["status"] == "disabled" and _lines(env) == []


# ── radar_hub 源刷新（订阅求值 → radar_signal 素材）────────────────


def _scored_file(tmp_path, items):
    path = tmp_path / "curated" / "scored.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": len(items), "items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _radar_items():
    return [
        {"repo": "aaa/one", "grade": "S", "score": 88, "url": "https://github.com/aaa/one",
         "license": "MIT", "description": "实时操作系统内核"},
        {"repo": "bbb/two", "grade": "A", "score": 66, "url": "https://github.com/bbb/two",
         "license": "CC-BY-4.0", "description": "构建工具链"},
        {"repo": "ccc/three", "grade": "B", "score": 55, "url": "https://github.com/ccc/three",
         "license": "", "description": "显示库"},
        {"repo": "ddd/nourl", "grade": "S", "score": 90, "license": "MIT"},  # 缺 url/repo 字段残缺
    ]


class _FakeClient:
    """四端点假客户端（monkeypatch 到 radarhub.RadarHubClient 位）。"""

    scored_path = ""
    radar_ids = ("embedded_github_radar", "data_github_radar")
    down = False

    def __init__(self, base: str, timeout: float = 3.0) -> None:
        self.base = base

    def radars(self):
        if self.down:
            return {"down": True, "error": "连接失败"}
        return {"radars": [{"id": rid} for rid in self.radar_ids]}

    def outputs(self, radar_id: str):
        return {
            "outputs": [
                {"id": "curated", "files": [{"name": "scored.json", "path": self.scored_path}]},
                {"id": "docs", "files": [{"name": "01-x.md", "path": "/tmp/docs/01-x.md"}]},
            ]
        }

    def health(self):
        return {"down": True} if self.down else {"ok": True, "time": 1.0}

    def overview(self):  # 四端点形状完备（ingest 不消费）
        return {}


@pytest.fixture
def fake_radarhub(monkeypatch, tmp_path):
    _FakeClient.scored_path = str(_scored_file(tmp_path, _radar_items()))
    _FakeClient.down = False
    monkeypatch.setattr(radarhub, "RadarHubClient", _FakeClient)
    return _FakeClient


def _setup_radar(env, subscriptions):
    source = {
        "id": "radarhub",
        "type": "radar_hub",
        "label": "雷达枢纽",
        "enabled": True,
        "config": {"base": "http://127.0.0.1:8787"},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": list(subscriptions),
    }
    return svc.register_source(source)


def test_radar_refresh_signal_materials_meta_four_keys(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["line=embedded & grade=S,A"])
    articles = tmp_path / "articles"
    articles.mkdir()
    result = ing.refresh_source("radarhub", articles_root=articles)
    assert result["status"] == "ok" and result["added"] == 2  # S+A 两项；nourl 残缺计 invalid
    assert result["invalid"] == 1 and result["lines"] == ["embedded_github_radar"]
    rows = _lines(env)
    by_uri = {r["origin"]["uri"]: r for r in rows}
    one = by_uri["https://github.com/aaa/one"]
    assert one["type"] == "radar_signal" and one["origin"]["source_id"] == "radarhub"
    meta = one["payload"]["meta"]
    assert meta["line"] == "embedded_github_radar"  # meta 四键
    assert meta["grade"] == "S"
    assert meta["score"] == 88
    assert meta["url"] == "https://github.com/aaa/one"
    assert meta["license_note"] == "MIT"  # 枚举外声明留档
    assert one["license"] == "UNKNOWN"  # MIT 不在枚举（CC-BY|ALL-RIGHTS|UNKNOWN）
    two = by_uri["https://github.com/bbb/two"]
    assert two["payload"]["meta"]["grade"] == "A" and two["license"] == "CC-BY"  # CC-BY 前缀映射
    assert svc.get_source("radarhub")["health"]["status"] == "ok"  # 可达回写 ok


def test_radar_refresh_term_filter(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["line=embedded & 实时"])
    articles = tmp_path / "articles"
    articles.mkdir()
    result = ing.refresh_source("radarhub", articles_root=articles)
    assert result["added"] == 1  # 只有描述含「实时」的 aaa/one
    assert [r["origin"]["uri"] for r in _lines(env)] == ["https://github.com/aaa/one"]


def test_radar_refresh_drafted_filter(env, tmp_path, fake_radarhub) -> None:
    """「未生成骨架」：正稿 frontmatter.source 指回的信号视为已消化被滤除。"""
    articles = tmp_path / "articles"
    articles.mkdir()
    (articles / "draft.md").write_text(
        "---\ntitle: x\nsource: https://github.com/aaa/one\n---\n\n正文\n", encoding="utf-8"
    )
    _setup_radar(env, ["line=embedded & grade=S & 未生成骨架"])
    result = ing.refresh_source("radarhub", articles_root=articles)
    assert result["added"] == 0  # 唯一 S 级（aaa/one）已 drafted；nourl 残缺不计


def test_radar_refresh_cross_line_dedupe_first_wins(env, tmp_path, fake_radarhub, monkeypatch) -> None:
    """同仓双线命中：id 相同取首线（embedded），不产生同 id 异内容冲突。"""
    # 两线 scored.json 指同一文件 → 同仓同 url
    _setup_radar(env, ["grade=S,A"])
    result = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert result["status"] == "ok" and result["deduped"] >= 1  # 第二线的两项并入首线
    assert result["added"] == 2
    rows = _lines(env)
    assert {r["payload"]["meta"]["line"] for r in rows} == {"embedded_github_radar"}


def test_radar_refresh_down_is_grey_not_failure(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["line=embedded"])
    fake_radarhub.down = True
    result = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert result["status"] == "down" and result["added"] == 0
    assert result["detail"]
    assert svc.get_source("radarhub")["health"]["status"] == "down"  # 灰灯回写
    assert _lines(env) == []


def test_radar_refresh_bad_subscription_readable_error(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["line=embedded &"])  # 尾段为空
    with pytest.raises(ValueError, match="段为空"):
        ing.refresh_source("radarhub", articles_root=tmp_path)
    assert _lines(env) == []


def test_radar_refresh_unknown_condition_key_readable_error(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["stars=100"])
    with pytest.raises(ValueError, match="条件键未知"):
        ing.refresh_source("radarhub", articles_root=tmp_path)
    assert _lines(env) == []


def test_radar_refresh_bad_scored_file_readable_error(env, tmp_path, fake_radarhub) -> None:
    bad = tmp_path / "curated" / "scored.json"
    bad.write_text("{not json", encoding="utf-8")
    fake_radarhub.scored_path = str(bad)
    _setup_radar(env, ["line=embedded"])
    with pytest.raises(ValueError, match="评分台账不可读"):
        ing.refresh_source("radarhub", articles_root=tmp_path)
    assert _lines(env) == []


def test_radar_refresh_idempotent(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, ["line=embedded & grade=S"])
    ing.refresh_source("radarhub", articles_root=tmp_path)
    second = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert second["added"] == 0 and second["skipped"] == 1


def test_radar_refresh_idempotent_across_time(env, tmp_path, fake_radarhub, monkeypatch) -> None:
    """radar 侧跨时重采幂等：与 kb 同病同修（真 hub +37min 复测的原始形态）。"""
    _setup_radar(env, ["line=embedded & grade=S"])
    clock = {"t": "2026-11-15T19:05:00"}
    monkeypatch.setattr(ing, "_now_iso", lambda: clock["t"])
    ing.refresh_source("radarhub", articles_root=tmp_path)
    clock["t"] = "2026-11-16T19:05:00"  # 次日调度：采集时钟前进
    second = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert second["added"] == 0 and second["skipped"] == 1
    rows = _lines(env)
    assert len(rows) == 1 and rows[0]["fetched_at"] == "2026-11-15T19:05:00"  # 零改写


def test_radar_refresh_triaged_material_not_conflict(env, tmp_path, fake_radarhub, monkeypatch) -> None:
    """用户分诊后的素材重采不冲突：triage 是用户态非内容面，指纹剔除。"""
    from musespiral.services import materials_service as mats

    _setup_radar(env, ["line=embedded & grade=S"])
    clock = {"t": "2026-11-15T19:05:00"}
    monkeypatch.setattr(ing, "_now_iso", lambda: clock["t"])
    ing.refresh_source("radarhub", articles_root=tmp_path)
    mats.update_triage(_lines(env)[0]["id"], {"starred": True})
    clock["t"] = "2026-11-16T19:05:00"
    second = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert second["added"] == 0 and second["skipped"] == 1  # 不 raise、不改写
    assert any(r.get("triage", {}).get("starred") for r in _lines(env))  # 分诊态保留


def test_radar_no_subscriptions_ingests_nothing(env, tmp_path, fake_radarhub) -> None:
    _setup_radar(env, [])
    result = ing.refresh_source("radarhub", articles_root=tmp_path)
    assert result["status"] == "ok" and result["added"] == 0 and result["lines"] == []
    assert _lines(env) == []


# ── refresh_all（顺序单步；失败不阻塞）/ 串行锁 / jobs kind ─────────


def test_refresh_all_continues_past_failure(env, tmp_path, fake_radarhub) -> None:
    _setup_kb(env, tmp_path)
    _setup_radar(env, ["line=embedded & grade=S"])
    svc.register_source(
        {
            "id": "broken", "type": "kb", "label": "坏源", "enabled": True,
            "config": {"root": None, "sources": []},
            "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
        }
    )
    results = {r["source_id"]: r for r in ing.refresh_all(articles_root=tmp_path)}
    assert results["kb"]["status"] == "ok" and results["kb"]["added"] == 2
    assert results["broken"]["status"] == "failed" and "root" in results["broken"]["error"]
    assert results["radarhub"]["status"] == "ok"  # 坏源不阻塞后续源


def test_refresh_source_serializes_under_module_lock(env, tmp_path) -> None:
    """串行队列行为验证：锁被占时刷新阻塞，释放后完成。"""
    _setup_kb(env, tmp_path)
    held, release, finished = threading.Event(), threading.Event(), threading.Event()
    box = {}

    def blocker() -> None:
        with ing._REFRESH_LOCK:
            held.set()
            assert release.wait(timeout=5)

    def worker() -> None:
        box["result"] = ing.refresh_source("kb")
        finished.set()

    blocker_thread = threading.Thread(target=blocker)
    blocker_thread.start()
    assert held.wait(timeout=2)
    worker_thread = threading.Thread(target=worker)
    worker_thread.start()
    assert not finished.wait(timeout=0.3), "刷新在模块锁被占时应阻塞（串行队列）"
    release.set()
    blocker_thread.join(timeout=5)
    assert finished.wait(timeout=5)
    worker_thread.join(timeout=5)
    assert box["result"]["status"] == "ok"


def test_jobs_store_accepts_ingest_kind(env) -> None:
    from musespiral.jobs.store import JobStore

    job = JobStore().create("ingest", {"source_id": "kb"})
    assert job["kind"] == "ingest" and job["status"] == "queued"
