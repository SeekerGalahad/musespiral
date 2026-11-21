"""信息源注册面测试（第二十期 §五第 2 行）：infra/sources/registry + services/sources_service。

覆盖：注册 upsert/回退兼容（kb_sources.json 合成——F46 级联泛化）/回环
钉死（非 127.0.0.1 一律拒绝）/健康三态（ok/down/unknown）/订阅表达式
解析（非法可读错误）/迁移件一幂等（kb_sources → sources.json 固化，
重复执行零改写）。MUSESPIRAL_HOME monkeypatch 到 tmp 隔离。
"""
from __future__ import annotations

import json

import pytest

from musespiral.infra.sources import registry
from musespiral.services import sources_service as svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _kb_entry(**overrides):
    base = {
        "id": "kb",
        "type": "kb",
        "label": "知识库",
        "enabled": True,
        "config": {"root": None, "sources": []},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": [],
    }
    base.update(overrides)
    return base


# ── 回退兼容：kb_sources.json 合成（F46 级联泛化）──────────────────


def test_synthesize_from_nothing(env) -> None:
    """无 kb_sources.json 无 kb_root.txt：root=None + 内置默认源表。"""
    entry = registry.synthesize_kb_source()
    assert entry["type"] == "kb" and entry["enabled"] is True
    assert entry["config"]["root"] is None
    keys = [s["key"] for s in entry["config"]["sources"]]
    assert keys == ["13-source-analysis", "11-learning-path", "pattern-family"]


def test_synthesize_from_legacy_root(env) -> None:
    from pathlib import Path

    (env / "kb_root.txt").write_text("/tmp/some-kb\n", encoding="utf-8")
    entry = registry.synthesize_kb_source()
    # F46 既有语义：root 经 resolve（符号链接展开，/tmp → /private/tmp）
    assert entry["config"]["root"] == str(Path("/tmp/some-kb").resolve())


def test_synthesize_from_kb_sources_json(env) -> None:
    from pathlib import Path

    (env / "kb_sources.json").write_text(
        json.dumps({"root": "/tmp/kb", "sources": [{"key": "a", "label": "甲", "relpath": "a"}]}),
        encoding="utf-8",
    )
    entry = registry.synthesize_kb_source()
    assert entry["config"]["root"] == str(Path("/tmp/kb").resolve())
    assert entry["config"]["sources"] == [{"key": "a", "label": "甲", "relpath": "a"}]


def test_load_missing_synthesizes(env) -> None:
    loaded = registry.load_sources()
    assert loaded["origin"] == "synthesized"
    assert [s["id"] for s in loaded["sources"]] == ["kb"]  # 合成只含 kb，radarhub 走安装动作


def test_load_bad_json_falls_back_to_synthesis(env) -> None:
    (env / "sources.json").write_text("{not json", encoding="utf-8")
    assert registry.load_sources()["origin"] == "synthesized"


def test_normalize_source_shapes(env) -> None:
    assert registry.normalize_source("junk") is None
    assert registry.normalize_source({"type": "kb"}) is None  # 无 id
    row = registry.normalize_source({"id": " x "})
    assert row["id"] == "x"
    assert row["enabled"] is True
    # B 采集扩展 §1.1 增键：last_refresh（最近刷新尝试）/crawl_delay（robots）
    assert row["health"] == {"status": "unknown", "last_ok": None,
                             "last_refresh": None, "crawl_delay": None}
    assert row["subscriptions"] == []
    # enabled 显式 False 保留；坏 health status 归 unknown
    row = registry.normalize_source({"id": "x", "enabled": False, "health": {"status": "weird"}})
    assert row["enabled"] is False
    assert row["health"]["status"] == "unknown"


# ── 回环钉死（SSRF 纪律延伸）────────────────────────────────────────


@pytest.mark.parametrize(
    "base",
    ["http://127.0.0.1:8787", "https://127.0.0.1:9443"],
)
def test_validate_base_url_accepts_loopback_only(base: str) -> None:
    assert registry.validate_base_url(base) == base


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:8787",        # localhost 字面拒绝
        "http://[::1]:8787",            # IPv6 回环拒绝（只钉 127.0.0.1）
        "http://10.0.0.5:8787",         # 内网地址
        "http://radar.example.com",     # 域名（解析不可控）
        "ftp://127.0.0.1:8787",         # 非 http(s) scheme
        "127.0.0.1:8787",               # 缺 scheme
        "",
        "http://",                      # 空 host
    ],
)
def test_validate_base_url_rejects_non_loopback(base: str) -> None:
    with pytest.raises(ValueError):
        registry.validate_base_url(base)


def test_register_radarhub_with_bad_base_rejected(env) -> None:
    bad = registry.default_radarhub_source()
    bad["config"]["base"] = "http://192.168.1.1:8787"
    with pytest.raises(ValueError, match="127.0.0.1"):
        svc.register_source(bad)
    assert not (env / "sources.json").exists()  # 拒绝即零落盘


def test_validate_source_shapes(env) -> None:
    with pytest.raises(ValueError, match="kebab"):
        svc.register_source(_kb_entry(id="My_Kb"))
    with pytest.raises(ValueError, match="type"):
        svc.register_source(_kb_entry(type="weird"))  # 枚举外
    # rss 本期开闸（B 采集扩展 §1.1）：无 url → 可读拒绝；IP 字面量同拒
    with pytest.raises(ValueError, match="config.url"):
        svc.register_source(_kb_entry(type="rss"))
    with pytest.raises(ValueError, match="IP 字面量"):
        svc.register_source(_kb_entry(type="rss", config={"url": "http://192.168.1.5/f.xml"}))
    with pytest.raises(ValueError, match="base"):
        svc.register_source(_kb_entry(config={"base": "http://127.0.0.1:8787"}))  # kb 不收 base
    with pytest.raises(ValueError, match="subscriptions"):
        svc.register_source(_kb_entry(subscriptions=[""]))
    with pytest.raises(ValueError, match="base"):
        svc.register_source({**registry.default_radarhub_source(), "config": {}})


# ── 注册编排（upsert/启停/移除/radarhub 安装）──────────────────────


def test_register_upsert_and_list(env) -> None:
    svc.register_source(_kb_entry())
    svc.register_source(registry.default_radarhub_source())
    ids = [s["id"] for s in svc.list_sources()]
    assert ids == ["kb", "radarhub"]
    # 同 id upsert 覆盖不重复
    svc.register_source(_kb_entry(label="知识库（改）"))
    entries = [s for s in svc.list_sources() if s["id"] == "kb"]
    assert len(entries) == 1 and entries[0]["label"] == "知识库（改）"
    assert svc.get_source("radarhub")["config"]["base"] == "http://127.0.0.1:8787"
    assert svc.get_source("nope") is None


def test_register_fixes_synthesized_kb(env) -> None:
    """首次落盘把读侧合成面一并固化（合成→权威自然切换）。"""
    svc.register_source(registry.default_radarhub_source())
    loaded = registry.load_sources()
    assert loaded["origin"] == "sources.json"
    assert {s["id"] for s in loaded["sources"]} == {"kb", "radarhub"}


def test_install_radarhub_idempotent(env) -> None:
    first = svc.install_radarhub()
    assert first["enabled"] is True
    before = (env / "sources.json").read_text(encoding="utf-8")
    again = svc.install_radarhub()
    assert again == first
    assert (env / "sources.json").read_text(encoding="utf-8") == before  # 零改写


def test_set_enabled_and_remove(env) -> None:
    svc.register_source(_kb_entry())
    off = svc.set_enabled("kb", False)
    assert off["enabled"] is False
    with pytest.raises(ValueError, match="布尔"):
        svc.set_enabled("kb", "yes")
    removed = svc.remove_source("kb")
    assert removed["id"] == "kb"
    with pytest.raises(ValueError, match="不存在"):
        svc.remove_source("kb")


# ── 健康三态（ok/down/unknown；显式验证回写）───────────────────────


def test_check_health_kb_states(env, tmp_path_factory) -> None:
    kb_root = tmp_path_factory.mktemp("kb")
    svc.register_source(_kb_entry(config={"root": str(kb_root), "sources": []}))
    health = svc.check_health("kb")
    assert health["status"] == "ok"
    assert health["last_ok"]  # ok 回写时间戳
    # root 消失 → down（保留 last_ok，宕机不抛）
    import shutil

    shutil.rmtree(kb_root)
    health = svc.check_health("kb")
    assert health["status"] == "down"
    assert health["last_ok"]


def test_check_health_kb_unconfigured_is_down(env) -> None:
    svc.register_source(_kb_entry())  # root=None
    assert svc.check_health("kb")["status"] == "down"


def test_check_health_radarhub_probes_and_writes_back(env, monkeypatch) -> None:
    """radar_hub 探测走四端点客户端（M2）：ok 载荷→ok+last_ok；宕机→down 保留快照。"""
    from musespiral.infra.sources import radarhub

    class _Fake:
        def __init__(self, base, timeout=3.0):
            pass

        def health(self):
            return {"ok": True, "time": 1.0}

    svc.register_source(registry.default_radarhub_source())
    monkeypatch.setattr(radarhub, "RadarHubClient", _Fake)
    health = svc.check_health("radarhub")
    assert health["status"] == "ok" and health["last_ok"]
    assert svc.get_source("radarhub")["health"]["status"] == "ok"  # 回写落 sources.json

    class _Down:
        def __init__(self, base, timeout=3.0):
            pass

        def health(self):
            return {"ok": False, "down": True, "error": "连接失败"}

    monkeypatch.setattr(radarhub, "RadarHubClient", _Down)
    down = svc.check_health("radarhub")
    assert down["status"] == "down" and down["last_ok"]  # 灰灯保留上次快照


# ── 订阅表达式求值（F55：line/grade/裸词条/未生成骨架；纯函数）──────


def test_line_matches_substring_case_insensitive() -> None:
    conds = svc.parse_subscription("line=embedded & grade=S")
    assert svc.line_matches(conds, "embedded_github_radar") is True
    assert svc.line_matches(conds, "Embedded_Radar") is True
    assert svc.line_matches(conds, "data_github_radar") is False
    assert svc.line_matches([], "anything") is True  # 无条件=全过


def test_item_matches_grade_multivalue_and_term() -> None:
    conds = svc.parse_subscription("grade=S,a")
    item = {"repo": "x/y", "grade": "A", "url": "u"}
    assert svc.item_matches(conds, item) is True
    assert svc.item_matches(svc.parse_subscription("grade=S"), {"grade": "B"}) is False
    term = svc.parse_subscription("rtos")
    assert svc.item_matches(term, {"description": "一个 RTOS 内核"}) is True
    assert svc.item_matches(term, {"repo": "x/y", "description": "显示库"}) is False


def test_item_matches_drafted_term() -> None:
    conds = svc.parse_subscription("未生成骨架")
    drafted = {"https://github.com/a/b"}
    assert svc.item_matches(conds, {"url": "https://github.com/a/b"}, drafted=drafted) is False
    assert svc.item_matches(conds, {"url": "https://github.com/c/d"}, drafted=drafted) is True
    with pytest.raises(ValueError, match="drafted"):
        svc.item_matches(conds, {"url": "u"})  # 未提供 drafted 集：可读错误不静默


def test_item_matches_unknown_key_readable_error() -> None:
    with pytest.raises(ValueError, match="条件键未知"):
        svc.item_matches([{"key": "stars", "value": "100"}], {"repo": "x"})
    # line 键在条目层冗余通过（雷达线阶段已消化）
    assert svc.item_matches([{"key": "line", "value": "embedded"}], {"repo": "x"}) is True


def test_check_health_dir_type(env, tmp_path) -> None:
    d = tmp_path / "notes"
    d.mkdir()

    def _dir_source(path_obj, health):
        return {"id": "notes", "type": "dir", "label": "笔记", "enabled": True,
                "config": {"path": str(path_obj)}, "health": health, "subscriptions": []}

    svc.register_source(_dir_source(d, {"status": "unknown", "last_ok": None}))
    assert svc.check_health("notes")["status"] == "ok"
    # 路径失效（带历史 last_ok 的在册源）→ down 且保留 last_ok
    import shutil

    shutil.rmtree(d)
    svc.register_source(_dir_source(d, {"status": "ok", "last_ok": "2026-11-15T00:00:00"}))
    health = svc.check_health("notes")
    assert health["status"] == "down"
    assert health["last_ok"] == "2026-11-15T00:00:00"


def test_mark_health_state_machine(env) -> None:
    svc.register_source(_kb_entry())
    with pytest.raises(ValueError, match="status"):
        svc.mark_health("kb", "yellow")
    svc.mark_health("kb", "ok")
    assert svc.get_source("kb")["health"]["last_ok"]
    down = svc.mark_health("kb", "down")
    assert down["status"] == "down" and down["last_ok"]  # down 保留 last_ok
    unknown = svc.mark_health("kb", "unknown")
    assert unknown == {"status": "unknown", "last_ok": unknown["last_ok"],
                       "last_refresh": unknown["last_refresh"],
                       "crawl_delay": unknown["crawl_delay"]}
    with pytest.raises(ValueError, match="不存在"):
        svc.mark_health("nope", "ok")


# ── 订阅表达式解析（非法可读错误；求值归 M2）───────────────────────


def test_parse_subscription_valid() -> None:
    conditions = svc.parse_subscription("line=embedded & grade=S & 未生成骨架")
    assert conditions == [
        {"key": "line", "value": "embedded"},
        {"key": "grade", "value": "S"},
        {"term": "未生成骨架"},
    ]
    assert svc.parse_subscription("  line = radar  ") == [{"key": "line", "value": "radar"}]
    assert svc.parse_subscription("单词条") == [{"term": "单词条"}]


@pytest.mark.parametrize("expr", ["", "   ", "a=1 &&b=2", "a=1 & ", "&a=1", "=value", "key=", "a=1 & =v"])
def test_parse_subscription_invalid(expr: str) -> None:
    with pytest.raises(ValueError):
        svc.parse_subscription(expr)


# ── 迁移件一：kb_sources → sources.json（幂等）─────────────────────


def test_migrate_kb_sources_idempotent(env) -> None:
    from pathlib import Path

    (env / "kb_sources.json").write_text(
        json.dumps({"root": "/tmp/kb", "sources": [{"key": "a", "label": "甲", "relpath": "a"}]}),
        encoding="utf-8",
    )
    first = svc.migrate_kb_sources()
    assert first["migrated"] is True
    persisted = (env / "sources.json").read_text(encoding="utf-8")
    assert first["source"]["config"]["root"] == str(Path("/tmp/kb").resolve())
    # 二跑：已在册零改写（迁移幂等）
    second = svc.migrate_kb_sources()
    assert second["migrated"] is False
    assert (env / "sources.json").read_text(encoding="utf-8") == persisted
    # 旧文件保留（只读兼容一个版本期）
    assert (env / "kb_sources.json").exists()


def test_migrate_kb_sources_keeps_existing_sources(env) -> None:
    """文件在册但缺 kb 条目 → 补合成条目并保留既有源。

    直接落盘构造（register 首次写盘会把合成 kb 一并固化——那是
    test_register_fixes_synthesized_kb 验证的路径，此处绕开以测补写分支）。
    """
    registry.save_sources([registry.default_radarhub_source()])
    result = svc.migrate_kb_sources()
    assert result["migrated"] is True
    assert {s["id"] for s in svc.list_sources()} == {"radarhub", "kb"}
