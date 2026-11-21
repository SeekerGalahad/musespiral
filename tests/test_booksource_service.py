"""书源服务测试（R0）：零落盘断言 / 订阅状态机 / 乐观时间戳 / 引擎降级 / URL 门。

MUSESPIRAL_HOME monkeypatch 到 tmp 隔离；网络一律注入 fetcher（零真实联网）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.booksource import merge
from musespiral.infra import bookengine, booksource_fetch, booksource_store
from musespiral.services import booksource_service as svc


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch) -> Path:
    """隔离 MUSESPIRAL_HOME + 清进程内待确认缓存（跨用例不串味）。"""
    h = tmp_path / "musespiral-home"
    h.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(h))
    svc._PENDING.clear()
    yield h
    svc._PENDING.clear()


def _src(url: str, *, name: str = "", lu: int = 0, group: str | None = None) -> dict:
    out = {"bookSourceUrl": url, "bookSourceName": name or url, "lastUpdateTime": lu}
    if group is not None:
        out["bookSourceGroup"] = group
    return out


def _dump(*sources: dict) -> str:
    return json.dumps(list(sources), ensure_ascii=False)


def _snapshot(home: Path) -> dict[str, bytes]:
    """~/.musespiral 全量字节快照（零落盘断言的取证面）。"""
    return {
        str(p.relative_to(home)): p.read_bytes()
        for p in sorted(home.rglob("*")) if p.is_file()
    }


# ── 导入：预览零落库（字节断言）─────────────────────────────────


def test_import_preview_writes_zero_bytes(home: Path) -> None:
    """预览零落库：预览前后 ~/.musespiral 字节完全一致（红线机器门）。"""
    before = _snapshot(home)
    preview = svc.import_preview(_dump(_src("https://a.com"), _src("https://b.com")))
    assert preview["counts"] == {"isNew": 2, "isUpdate": 0, "latest": 0, "dupUrls": 0}
    assert preview["kind"] == "bookSource" and preview["skipped"] == []
    assert _snapshot(home) == before, "import 预览不得落盘任何字节"
    assert not svc.list_subs() and not svc.list_sources()


def test_import_preview_projects_light_source(home: Path) -> None:
    """设计 §3.3 投影承诺：预览 items[].source 只含 url/name/group 三字段，
    不载规则大字段（exploreUrl/ruleSearch/header…）——20MB 包的 19.9MB 响应回归门。"""
    rich = _src("https://rich.com", name="rich", group="玄幻")
    rich.update({"ruleSearch": {"bookList": ".list"}, "exploreUrl": "http://e",
                 "header": '{"k":"v"}', "loginUrl": "http://login", "mainJs": "code()"})
    preview = svc.import_preview(_dump(rich))
    source = preview["items"][0]["source"]
    assert source == {
        "bookSourceUrl": "https://rich.com",
        "bookSourceName": "rich",
        "bookSourceGroup": "玄幻",
    }
    assert not any(k in source for k in ("ruleSearch", "exploreUrl", "header",
                                         "loginUrl", "mainJs")), "预览不得回传规则大字段"


def test_import_apply_lands_full_source_not_projection(home: Path) -> None:
    """投影数据不丢：落库走内部全字段路径，规则大字段必须原样落盘（防丢数据门）。"""
    rich = _src("https://rich.com", name="rich")
    rich.update({"ruleSearch": {"bookList": ".list"}, "exploreUrl": "http://e",
                 "header": '{"k":"v"}', "customOrder": 9})
    out = svc.import_apply(_dump(rich))
    assert out["stored"] == 1
    landed = booksource_store.load_source("https://rich.com")
    assert landed["ruleSearch"] == {"bookList": ".list"}
    assert landed["exploreUrl"] == "http://e"
    assert landed["header"] == '{"k":"v"}'
    assert landed["customOrder"] == 9


def test_import_preview_zero_write_on_populated_home(home: Path) -> None:
    """预览零落库（非空 home 版）：三件套齐备后 preview 不改任何既有字节。

    复核轮补强：既有零写门在空 home 取证（before={} 平凡成立，只证明
    「不新建文件」）；本用例先 add_sub+apply 落三件套，再对同 URL 旧版
    （latest）与新版（isUpdate）各预览一次，断言 subs/index/源文件全量
    字节不变——未来 preview 触改既有文件的回归会被捕获。
    """
    svc.add_sub("https://sub.example.com/pkg.json")
    svc.import_apply(_dump(_src("https://a.com", lu=200)))
    assert set(_snapshot(home)), "前置：home 三件套已非空"
    before = _snapshot(home)

    older = svc.import_preview(_dump(_src("https://a.com", lu=100)))
    assert older["items"][0]["status"] == "latest"
    newer = svc.import_preview(_dump(_src("https://a.com", lu=300)))
    assert newer["items"][0]["status"] == "isUpdate"
    assert _snapshot(home) == before, "预览不得改写既有 subs/index/源文件任何字节"


def test_import_apply_persists_and_indexes(home: Path) -> None:
    """apply 落库：单源文件 + 索引行（三件套两件）。"""
    result = svc.import_apply(_dump(_src("https://a.com", name="A", group="小说")))
    assert result["imported"] == ["https://a.com"]

    stored = booksource_store.load_source("https://a.com")
    assert stored is not None and stored["bookSourceName"] == "A"
    rows = svc.list_sources()
    assert len(rows) == 1
    assert rows[0]["bookSourceName"] == "A" and rows[0]["group"] == "小说"
    assert rows[0]["imported_at"], "索引行要带 imported_at"
    assert "ruleSearch" not in rows[0], "索引是轻量投影，不载规则大字段"


def test_import_apply_three_states_and_selections(home: Path) -> None:
    """三态：isNew/isUpdate 默认落、latest 默认跳；selections 可覆盖。"""
    svc.import_apply(_dump(_src("https://a.com", lu=100)))
    preview = svc.import_preview(_dump(_src("https://a.com", lu=50), _src("https://b.com")))
    by_url = {i["source"]["bookSourceUrl"]: i for i in preview["items"]}
    assert by_url["https://a.com"]["status"] == merge.STATUS_LATEST
    assert by_url["https://a.com"]["selected"] is False
    assert by_url["https://b.com"]["status"] == merge.STATUS_NEW

    # 默认：latest 跳过、new 落库
    out = svc.import_apply(_dump(_src("https://a.com", lu=50), _src("https://b.com")))
    assert out["imported"] == ["https://b.com"]
    assert out["skipped"] == ["https://a.com"]
    assert booksource_store.load_source("https://a.com")["lastUpdateTime"] == 100

    # 显式勾选 latest → 落库（用户手选覆盖默认）
    out = svc.import_apply(
        _dump(_src("https://a.com", lu=50)),
        [{"bookSourceUrl": "https://a.com", "selected": True}],
    )
    assert out["imported"] == ["https://a.com"]
    assert booksource_store.load_source("https://a.com")["lastUpdateTime"] == 50


def test_import_preview_applies_keep_preferences(home: Path) -> None:
    """importKeep 在预览就体现（用户看到的即落库形态）。"""
    svc.import_apply(_dump(_src("https://a.com", name="本地名", group="本地组", lu=1)))
    preview = svc.import_preview(
        _dump(_src("https://a.com", name="导入名", group="导入组", lu=2)),
        keep_name=True, keep_group=True,
    )
    source = preview["items"][0]["source"]
    assert source["bookSourceName"] == "本地名"
    assert source["bookSourceGroup"] == "本地组"


def test_import_apply_dirty_index_keeps_imported_values(home: Path) -> None:
    """脏索引降级收口（导入路径）：源文件缺失（索引残留）时导入值原样落盘。

    索引投影无 bookSourceGroup/customOrder/enabledExplore 规范键——修复前
    apply_keep 拿投影回退行合并会把分组洗成 None、customOrder 归 0、
    enabledExplore 重置 True；修复后合并偏好只在 _local_full 全字段源上
    应用，脏索引下导入值不洗。三态判定仍走投影（lastUpdateTime 不误判
    isNew——见首断言）。
    """
    src = {"bookSourceUrl": "https://a.com", "bookSourceName": "甲",
           "lastUpdateTime": 100, "bookSourceGroup": "本地组",
           "customOrder": 7, "enabledExplore": False}
    svc.import_apply(_dump(src))
    # 制造脏索引：删源文件、索引行残留
    booksource_store.source_file("https://a.com").unlink()
    assert booksource_store.load_source("https://a.com") is None
    assert svc.list_sources(), "索引行仍在（脏索引形态）"

    newer = {"bookSourceUrl": "https://a.com", "bookSourceName": "甲新",
             "lastUpdateTime": 200, "bookSourceGroup": "导入组",
             "customOrder": 9, "enabledExplore": False}
    preview = svc.import_preview(_dump(newer))
    assert preview["items"][0]["status"] == merge.STATUS_UPDATE, "投影 lastUpdateTime 仍参与三态"

    out = svc.import_apply(_dump(newer))
    assert out["imported"] == ["https://a.com"]
    landed = booksource_store.load_source("https://a.com")
    assert landed["bookSourceGroup"] == "导入组", "分组不得被投影回退行洗成 None"
    assert landed["customOrder"] == 9, "customOrder 不得被投影回退行归 0"
    assert landed["enabledExplore"] is False, "enabledExplore 不得被投影回退行重置 True"


def test_refresh_silent_dirty_index_keeps_imported_values(
    home: Path, monkeypatch
) -> None:
    """脏索引降级收口（静默刷新路径）：同上语义，走 refresh_subscription。

    修复前静默刷新的「继承本地分组」+apply_keep 拿投影回退行会把导入
    分组洗成 None/customOrder 归 0；修复后 _local_full 缺席即不合并。
    """
    src = {"bookSourceUrl": "https://a.com", "bookSourceName": "甲",
           "lastUpdateTime": 100, "bookSourceGroup": "本地组", "customOrder": 7}
    svc.import_apply(_dump(src))
    booksource_store.source_file("https://a.com").unlink()

    svc.add_sub("https://sub.example.com/pkg.json")
    newer = {"bookSourceUrl": "https://a.com", "bookSourceName": "甲新",
             "lastUpdateTime": 200, "bookSourceGroup": "订阅组", "customOrder": 9}
    monkeypatch.setattr(booksource_fetch, "fetch", lambda _u: _dump(newer))
    out = svc.refresh_subscription("https://sub.example.com/pkg.json", force=True)
    assert out["action"] == "updated" and out["imported"] == ["https://a.com"]

    landed = booksource_store.load_source("https://a.com")
    assert landed["bookSourceGroup"] == "订阅组", "静默刷新不得洗分组（投影回退无此键）"
    assert landed["customOrder"] == 9, "静默刷新不得归零 customOrder"


def test_import_preview_reports_local_projection(home: Path) -> None:
    """三态清单带本地投影（前端 diff 展示用）。"""
    svc.import_apply(_dump(_src("https://a.com", name="旧名", lu=10)))
    preview = svc.import_preview(_dump(_src("https://a.com", name="新名", lu=20)))
    item = preview["items"][0]
    assert item["status"] == merge.STATUS_UPDATE
    assert item["local"] == {
        "bookSourceName": "旧名", "bookSourceGroup": "", "lastUpdateTime": 10,
    }


def test_import_rejects_bad_json_readably() -> None:
    with pytest.raises(ValueError, match="不是书源"):
        svc.import_preview("{bad json")


def test_import_url_branch_uses_injected_fetcher(home: Path) -> None:
    """URL 导入经注入 fetcher（生产态=infra/booksource_fetch.fetch）。"""
    body = _dump(_src("https://fetched.com"))
    preview = svc.import_preview("https://seed.example.com/list.json", fetcher=lambda _u: body)
    assert preview["items"][0]["source"]["bookSourceUrl"] == "https://fetched.com"


# ── 列表/详情过滤 ────────────────────────────────────────────────


def test_list_filters_by_group_and_query(home: Path) -> None:
    svc.import_apply(_dump(
        _src("https://a.com", name="张三丰", group="小说,玄幻"),
        _src("https://b.com", name="李四", group="出版"),
    ))
    assert [r["bookSourceUrl"] for r in svc.list_sources(group="玄幻")] == ["https://a.com"]
    assert [r["bookSourceUrl"] for r in svc.list_sources(group="出版")] == ["https://b.com"]
    assert [r["bookSourceUrl"] for r in svc.list_sources(q="张三")] == ["https://a.com"]
    assert [r["bookSourceUrl"] for r in svc.list_sources(q="b.com")] == ["https://b.com"]
    assert len(svc.list_sources()) == 2


def test_get_source_returns_full_rules(home: Path) -> None:
    """单源详情含规则组（列表不载的字段在这里给）。"""
    source = _src("https://a.com")
    source["ruleSearch"] = {"bookList": ".li"}
    svc.import_apply(_dump(source))
    got = svc.get_source("https://a.com")
    assert got is not None and got["ruleSearch"] == {"bookList": ".li"}
    assert svc.get_source("https://nope.com") is None
    assert svc.get_source("") is None


# ── 订阅 CRUD ───────────────────────────────────────────────────


def test_add_sub_requires_https() -> None:
    """https 强制（订阅拉取最简门第 1 条，注册期就执法）。"""
    with pytest.raises(booksource_fetch.BookSourceFetchError, match="https"):
        svc.add_sub("http://insecure.example.com/list.json")


def test_add_sub_upsert_preserves_state(home: Path) -> None:
    row = svc.add_sub("https://s.example.com/list.json", name="订阅一", update_interval_h=6)
    assert row["name"] == "订阅一" and row["update_interval_h"] == 6
    assert row["fetch_state"] == "pending" and row["last_fetch"] is None

    svc.refresh_subscription(
        row["url"], fetcher=lambda _u: _dump(_src("https://a.com")), now=1000.0
    )
    again = svc.add_sub("https://s.example.com/list.json", update_interval_h=12)
    assert again["last_fetch"] == 1000.0, "upsert 不得清掉 last_fetch"
    assert again["name"] == "订阅一", "空 name 不覆盖既有名称"
    assert again["update_interval_h"] == 12


def test_add_sub_rejects_bad_interval() -> None:
    with pytest.raises(ValueError, match="update_interval_h"):
        svc.add_sub("https://s.example.com/x.json", update_interval_h=-1)


def test_del_sub_and_missing(home: Path) -> None:
    svc.add_sub("https://s.example.com/x.json")
    assert svc.del_sub("https://s.example.com/x.json")["ok"] is True
    assert svc.list_subs() == []
    with pytest.raises(ValueError, match="订阅不存在"):
        svc.del_sub("https://s.example.com/x.json")


# ── 订阅刷新状态机 ──────────────────────────────────────────────


def test_refresh_interval_gate_skips_without_writing(home: Path) -> None:
    """间隔闸门：未到期 → skipped，且 last_fetch 不被改写、fetcher 不被调用。"""
    svc.add_sub("https://s.example.com/x.json", update_interval_h=6)
    svc.refresh_subscription(
        "https://s.example.com/x.json",
        fetcher=lambda _u: _dump(_src("https://a.com")),
        now=1000.0,
    )
    calls: list[str] = []

    def should_not_run(url: str) -> str:
        calls.append(url)
        return _dump(_src("https://a.com"))

    out = svc.refresh_subscription(
        "https://s.example.com/x.json", fetcher=should_not_run, now=1000.0 + 6 * 3600 - 1
    )
    assert out["action"] == "skipped"
    assert calls == [], "闸门未过不得发起网络请求"
    assert svc.list_subs()[0]["last_fetch"] == 1000.0


def test_refresh_force_bypasses_interval_gate(home: Path) -> None:
    """force=True 旁路闸门（Studio 手动刷新的显式语义）。"""
    svc.add_sub("https://s.example.com/x.json", update_interval_h=999)
    svc.refresh_subscription(
        "https://s.example.com/x.json", fetcher=lambda _u: _dump(_src("https://a.com")), now=1000.0
    )
    out = svc.refresh_subscription(
        "https://s.example.com/x.json",
        force=True, fetcher=lambda _u: _dump(_src("https://b.com")), now=1001.0,
    )
    assert out["action"] == "updated"
    assert booksource_store.load_source("https://b.com") is not None


def test_refresh_silent_updates_directly_and_inherits_group(home: Path) -> None:
    """silent_update=true：直接落库，且**继承本地分组**（legado 原语义）。"""
    svc.import_apply(_dump(_src("https://a.com", name="旧名", group="我的分组", lu=1)))
    svc.add_sub("https://s.example.com/x.json", silent_update=True)
    out = svc.refresh_subscription(
        "https://s.example.com/x.json",
        fetcher=lambda _u: _dump(_src("https://a.com", name="新名", group="订阅分组", lu=2)),
        now=1000.0,
    )
    assert out["action"] == "updated" and out["imported"] == ["https://a.com"]
    stored = booksource_store.load_source("https://a.com")
    assert stored["bookSourceName"] == "新名", "静默更新用订阅内容"
    assert stored["bookSourceGroup"] == "我的分组", "分组继承本地"
    assert svc.list_subs()[0]["fetch_state"] == "ok"


def test_refresh_non_silent_returns_pending_without_writing(home: Path) -> None:
    """silent_update=false：进待确认缓存，书源库零落库。"""
    svc.add_sub("https://s.example.com/x.json", silent_update=False)
    before = _snapshot(home)
    out = svc.refresh_subscription(
        "https://s.example.com/x.json",
        fetcher=lambda _u: _dump(_src("https://a.com")), now=1000.0,
    )
    assert out["action"] == "pending"
    assert len(out["items"]) == 1
    assert booksource_store.load_source("https://a.com") is None, "待确认不得落库"
    # 除订阅表（last_fetch 乐观戳 + fetch_state）外无新增文件
    after = set(_snapshot(home))
    assert after - set(before) == set(), f"意外落盘: {after - set(before)}"
    assert svc.list_subs()[0]["fetch_state"] == "pending"


def test_pending_cache_take_is_destructive(home: Path) -> None:
    svc.add_sub("https://s.example.com/x.json", silent_update=False)
    svc.refresh_subscription(
        "https://s.example.com/x.json", fetcher=lambda _u: _dump(_src("https://a.com")), now=1000.0
    )
    taken = svc.pending("https://s.example.com/x.json")
    assert len(taken) == 1 and taken[0]["bookSourceUrl"] == "https://a.com"
    assert svc.pending("https://s.example.com/x.json") == []


def test_refresh_failure_keeps_optimistic_timestamp(home: Path) -> None:
    """乐观时间戳：拉取失败 fetch_state=failed，**last_fetch 不回滚**（legado 同语义）。"""
    svc.add_sub("https://s.example.com/x.json")
    out = svc.refresh_subscription(
        "https://s.example.com/x.json",
        fetcher=lambda _u: (_ for _ in ()).throw(booksource_fetch.BookSourceFetchError("网络错误")),
        now=1234.0,
    )
    assert out["action"] == "failed" and "网络错误" in out["error"]
    sub = svc.list_subs()[0]
    assert sub["last_fetch"] == 1234.0, "乐观时间戳不回滚"
    assert sub["fetch_state"] == "failed" and "网络错误" in sub["error"]


def test_refresh_unchanged_sources_are_skipped(home: Path) -> None:
    """已是最新的源不重复落库（imported 为空）。"""
    svc.import_apply(_dump(_src("https://a.com", lu=100)))
    svc.add_sub("https://s.example.com/x.json")
    out = svc.refresh_subscription(
        "https://s.example.com/x.json",
        fetcher=lambda _u: _dump(_src("https://a.com", lu=50)), now=1000.0,
    )
    assert out["action"] == "updated" and out["imported"] == []


def test_refresh_unknown_sub_readable_error() -> None:
    with pytest.raises(ValueError, match="订阅不存在"):
        svc.refresh_subscription("https://nope.example.com/x.json")


def test_refresh_all_tolerates_individual_failures(home: Path) -> None:
    svc.add_sub("https://a.example.com/x.json")
    svc.add_sub("https://b.example.com/x.json")

    def fetch(url: str) -> str:
        if "a.example" in url:
            raise booksource_fetch.BookSourceFetchError("坏了")
        return _dump(_src("https://b.com"))

    results = svc.refresh_all(force=True, fetcher=fetch)
    by_url = {r["url"]: r for r in results}
    assert by_url["https://a.example.com/x.json"]["action"] == "failed"
    assert by_url["https://b.example.com/x.json"]["action"] == "updated"


# ── 引擎降级 ────────────────────────────────────────────────────


def test_engine_status_absent_is_return_value(monkeypatch) -> None:
    """引擎缺席 → status=absent（正常返回值，不是异常）。"""
    def unavailable(_path: str, *, timeout: float):
        raise bookengine.BookEngineUnavailable("bookengine 未就绪")

    monkeypatch.setattr(bookengine, "_get", unavailable)
    status = svc.engine_status()
    assert status["ok"] is True and status["status"] == "absent"
    assert status["base_url"] == "http://127.0.0.1:8792"


def test_engine_status_ok_passes_through(monkeypatch) -> None:
    monkeypatch.setattr(
        bookengine, "_get", lambda _p, *, timeout: {"ok": True, "version": "0.1", "sources_loaded": 3}
    )
    status = svc.engine_status()
    assert status == {
        "ok": True, "status": "ok", "base_url": "http://127.0.0.1:8792",
        "version": "0.1", "sources_loaded": 3,
    }


def test_engine_client_available_and_loopback_pinned(monkeypatch) -> None:
    """回环钉死不可配置 + 四方法实调签名（R3：引擎缺席 → BookEngineUnavailable）。"""
    monkeypatch.delenv("MUSESPIRAL_ENGINE_MOCK", raising=False)
    client = bookengine.BookEngineClient(timeout=0.2)
    assert client.base_url == "http://127.0.0.1:8792"
    assert bookengine.BASE_URL == "http://127.0.0.1:8792"
    with pytest.raises(TypeError):
        bookengine.BookEngineClient(base_url="http://evil.example.com")  # type: ignore[call-arg]
    calls = {
        "search": lambda: client.search("关键词"),
        "book_info": lambda: client.book_info("https://s", "https://b"),
        "toc": lambda: client.toc("https://s", "https://b"),
        "content": lambda: client.content("https://s", "https://b", "https://c"),
    }
    for name, call in calls.items():
        with pytest.raises(bookengine.BookEngineUnavailable):
            call()


def test_engine_available_false_when_unreachable(monkeypatch) -> None:
    """真实回环探测（8792 没服务时 → False，不抛）。"""
    monkeypatch.delenv("MUSESPIRAL_ENGINE_MOCK", raising=False)
    assert bookengine.BookEngineClient(timeout=0.2).available() is False
