"""Studio 书源端点测试（R0）：9 端点 + confirm 闸门 + 契约锁 + 引擎灰灯。

真实回环服务器（与 test_ingest_studio 同款）+ MUSESPIRAL_HOME 隔离；网络注入
不落地——URL 导入走 test 内的假 fetcher（经 service 层直接调用）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import booksource_service as svc


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    svc._PENDING.clear()
    yield home
    svc._PENDING.clear()


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data.decode("utf-8")) if data else None


def _src(url: str, name: str = "", lu: int = 0, group: str | None = None) -> dict:
    out = {"bookSourceUrl": url, "bookSourceName": name or url, "lastUpdateTime": lu}
    if group is not None:
        out["bookSourceGroup"] = group
    return out


# ── POST 面：confirm 闸门 ────────────────────────────────────────


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/booksource/import", {"text_or_url": "[]"}),
        ("/api/booksource/import_apply", {"text_or_url": "[]"}),
        ("/api/booksource/refresh", {}),
        ("/api/booksource/subs/add", {"url": "https://s.example.com/x.json"}),
        ("/api/booksource/subs/del", {"url": "https://s.example.com/x.json"}),
    ],
)
def test_post_requires_confirm(server, path: str, body: dict) -> None:
    """五个 POST 全部要 confirm:true（人审闸门，缺省 400）。"""
    code, payload = _request(server, "POST", path, body)
    assert code == 400
    assert "confirm" in payload["error"]


# ── 导入两段式 ──────────────────────────────────────────────────


def test_import_preview_then_apply(server, _isolate_home: Path) -> None:
    """金路径：预览（零落库）→ apply 落库 → 列表可见。"""
    text = json.dumps([_src("https://a.com", "A", group="小说")])

    code, preview = _request(
        server, "POST", "/api/booksource/import", {"confirm": True, "text_or_url": text}
    )
    assert code == 200 and preview["ok"] is True
    assert preview["counts"] == {"isNew": 1, "isUpdate": 0, "latest": 0, "dupUrls": 0}
    assert preview["items"][0]["source"]["bookSourceName"] == "A"
    assert not any(_isolate_home.rglob("*.json")), "预览必须零落库"

    code, applied = _request(
        server, "POST", "/api/booksource/import_apply", {"confirm": True, "text_or_url": text}
    )
    assert code == 200 and applied["imported"] == ["https://a.com"]

    code, listed = _request(server, "GET", "/api/booksource")
    assert code == 200 and len(listed["sources"]) == 1


def test_import_apply_with_selections(server) -> None:
    """selections 显式选择覆盖默认三态。"""
    text = json.dumps([_src("https://a.com")])
    _request(server, "POST", "/api/booksource/import_apply", {"confirm": True, "text_or_url": text})
    text2 = json.dumps([_src("https://a.com", lu=5), _src("https://b.com")])
    code, out = _request(
        server, "POST", "/api/booksource/import_apply",
        {
            "confirm": True, "text_or_url": text2,
            "selections": [
                {"bookSourceUrl": "https://a.com", "selected": True},
                {"bookSourceUrl": "https://b.com", "selected": False},
            ],
        },
    )
    assert code == 200
    assert out["imported"] == ["https://a.com"] and out["skipped"] == ["https://b.com"]


def test_import_bad_input_is_400(server) -> None:
    code, payload = _request(
        server, "POST", "/api/booksource/import", {"confirm": True, "text_or_url": "{bad"}
    )
    assert code == 400 and payload["ok"] is False


def test_import_missing_text_is_400(server) -> None:
    code, payload = _request(server, "POST", "/api/booksource/import", {"confirm": True})
    assert code == 400 and "text_or_url" in payload["error"]


# ── GET 面 ─────────────────────────────────────────────────────


def test_list_filters(server) -> None:
    svc.import_apply(json.dumps([_src("https://a.com", "张三", group="小说"), _src("https://b.com", "李四")]))
    code, out = _request(server, "GET", "/api/booksource?group=%E5%B0%8F%E8%AF%B4")
    assert code == 200 and [r["bookSourceUrl"] for r in out["sources"]] == ["https://a.com"]
    code, out = _request(server, "GET", "/api/booksource?q=%E6%9D%8E")
    assert code == 200 and [r["bookSourceUrl"] for r in out["sources"]] == ["https://b.com"]


def test_one_returns_rules_and_404(server) -> None:
    source = _src("https://a.com", "A")
    source["ruleToc"] = {"chapterList": ".ch"}
    svc.import_apply(json.dumps([source]))
    code, out = _request(server, "GET", "/api/booksource/one?url=https%3A%2F%2Fa.com")
    assert code == 200 and out["source"]["ruleToc"] == {"chapterList": ".ch"}
    code, out = _request(server, "GET", "/api/booksource/one?url=https%3A%2F%2Fnope.com")
    assert code == 404
    code, out = _request(server, "GET", "/api/booksource/one")
    assert code == 400


def test_subs_crud_endpoints(server) -> None:
    code, out = _request(
        server, "POST", "/api/booksource/subs/add",
        {"confirm": True, "url": "https://s.example.com/x.json", "name": "订阅", "update_interval_h": 6},
    )
    assert code == 200 and out["sub"]["name"] == "订阅"

    code, out = _request(server, "GET", "/api/booksource/subs")
    assert code == 200 and len(out["subs"]) == 1

    code, out = _request(
        server, "POST", "/api/booksource/subs/del",
        {"confirm": True, "url": "https://s.example.com/x.json"},
    )
    assert code == 200 and out["ok"] is True
    code, out = _request(server, "GET", "/api/booksource/subs")
    assert out["subs"] == []


def test_subs_add_http_url_is_400(server) -> None:
    """https 强制在端点面也是 400（可读错误）。"""
    code, out = _request(
        server, "POST", "/api/booksource/subs/add",
        {"confirm": True, "url": "http://plain.example.com/x.json"},
    )
    assert code == 400 and "https" in out["error"]


def test_refresh_endpoint_reports_action(server, monkeypatch) -> None:
    """刷新端点：engine 无关；单条/全部两形态；失败 200 返回 action=failed。"""
    from musespiral.infra import booksource_fetch

    svc.add_sub("https://s.example.com/x.json")
    monkeypatch.setattr(booksource_fetch, "fetch", lambda _u: json.dumps([_src("https://a.com")]))
    code, out = _request(
        server, "POST", "/api/booksource/refresh",
        {"confirm": True, "url": "https://s.example.com/x.json"},
    )
    assert code == 200 and out["action"] == "updated"

    monkeypatch.setattr(
        booksource_fetch, "fetch",
        lambda _u: (_ for _ in ()).throw(booksource_fetch.BookSourceFetchError("网络错误")),
    )
    code, out = _request(
        server, "POST", "/api/booksource/refresh",
        {"confirm": True, "url": "https://s.example.com/x.json"},
    )
    assert code == 200 and out["action"] == "failed"


def test_engine_endpoint_reports_absent(server, monkeypatch) -> None:
    """引擎灰灯数据源：缺席返回 status=absent（不是错误码）。"""
    from musespiral.infra import bookengine

    monkeypatch.setattr(
        bookengine, "_get",
        lambda _p, *, timeout: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("未就绪")),
    )
    code, out = _request(server, "GET", "/api/booksource/engine")
    assert code == 200 and out["status"] == "absent"
    assert out["base_url"] == "http://127.0.0.1:8792"


# ── 集成冒烟（R0 收口：端点族全 200 + URL 导入 mock）──────────────


def test_endpoint_family_smoke_and_url_import_mock(server, monkeypatch) -> None:
    """集成冒烟：9 端点（4 GET+5 POST）全 200 + URL 导入经假订阅文本。

    fetcher 注入=monkeypatch `infra.booksource_fetch.fetch`（service 层调用期
    才解析模块属性，与 refresh 测试同通道）——URL 导入走 ② 拉取分支拉回
    ③ JSON 解析，全程零真实网络。
    """
    from musespiral.infra import bookengine, booksource_fetch

    # 读面：空库/空订阅 200（one 空库按契约是 404，落库后回来补 200）
    for path in ("/api/booksource", "/api/booksource/subs"):
        code, payload = _request(server, "GET", path)
        assert code == 200 and payload["ok"] is True, path
    monkeypatch.setattr(
        bookengine, "_get",
        lambda _p, *, timeout: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("未就绪")),
    )
    code, payload = _request(server, "GET", "/api/booksource/engine")
    assert code == 200 and payload["status"] == "absent"

    # URL 导入（② 分支）：fetcher 注入假订阅文本 → 三态预览零落库
    fake_pkg = json.dumps([_src("https://a.com", "甲"), _src("https://b.com", "乙")])
    monkeypatch.setattr(booksource_fetch, "fetch", lambda _u: fake_pkg)
    code, preview = _request(
        server, "POST", "/api/booksource/import",
        {"confirm": True, "text_or_url": "https://sub.example.com/pkg.json"},
    )
    assert code == 200 and preview["ok"] is True
    assert preview["counts"] == {"isNew": 2, "isUpdate": 0, "latest": 0, "dupUrls": 0}

    # 落库 → 列表/单源读回 200
    code, applied = _request(
        server, "POST", "/api/booksource/import_apply",
        {"confirm": True, "text_or_url": "https://sub.example.com/pkg.json"},
    )
    assert code == 200 and set(applied["imported"]) == {"https://a.com", "https://b.com"}
    code, listed = _request(server, "GET", "/api/booksource")
    assert code == 200 and len(listed["sources"]) == 2
    code, one = _request(server, "GET", "/api/booksource/one?url=https%3A%2F%2Fa.com")
    assert code == 200 and one["source"]["bookSourceName"] == "甲"

    # 写面：订阅增/刷新/删全 200（刷新走同一假 fetcher；全 latest → updated 空清单）
    code, out = _request(
        server, "POST", "/api/booksource/subs/add",
        {"confirm": True, "url": "https://sub.example.com/pkg.json", "name": "冒烟订阅"},
    )
    assert code == 200 and out["sub"]["name"] == "冒烟订阅"
    code, out = _request(
        server, "POST", "/api/booksource/refresh",
        {"confirm": True, "url": "https://sub.example.com/pkg.json"},
    )
    assert code == 200 and out["action"] == "updated" and out["imported"] == []
    code, out = _request(
        server, "POST", "/api/booksource/subs/del",
        {"confirm": True, "url": "https://sub.example.com/pkg.json"},
    )
    assert code == 200 and out["ok"] is True


# ── 契约锁 ─────────────────────────────────────────────────────


def test_route_surface_locked_to_contract() -> None:
    """9 端点（4 GET + 5 POST）与契约表同批锁定（§3.9 GET+4/POST+5）。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES

    get_paths = {p for p in _GET_ROUTES if p.startswith("/api/booksource")}
    post_paths = {p for p in _ACTION_ROUTES if p.startswith("/api/booksource")}
    assert get_paths == {
        "/api/booksource", "/api/booksource/one",
        "/api/booksource/subs", "/api/booksource/engine",
    }
    assert post_paths == {
        "/api/booksource/import", "/api/booksource/import_apply",
        "/api/booksource/refresh",
        "/api/booksource/subs/add", "/api/booksource/subs/del",
    }


def test_materials_meta_book_schema_constants() -> None:
    """Material meta.book schema 扩位：常量与 book_fetch 血统动作（R0 定 schema；
    R3.5 提炼批 2026-11-18 写入并扩 original_chars/distilled 两键）。"""
    from musespiral.core.materials import model

    assert model.META_BOOK_KEYS == (
        "name", "author", "source_name", "chapter_index", "chapter_title",
        "original_chars", "distilled",
    )
    assert "book_fetch" in model.PROVENANCE_OPS

def test_import_default_selection_uses_group_any_not_last() -> None:
    """默认选中按「组内任一 selected」而非「末条 last.selected」（2026-11-23 判例）。

    旧实现 `g.last.selected`：同 URL 多版本时末条可能是 latest（不选中），
    整组被漏选——实测 3906 源包 preview 后 UI 显示 0 选中（后端返回 30 条
    selected=True）。新实现由前端 imGroups 产 `sel` 字段；本测试锚定源文本
    含 anySel 逻辑且不再读 last.selected 做选中判据。
    """
    from pathlib import Path as _P
    # 读**构建源** ui.js（真源）——assets/views/books.js 是产物，改产物会被 build 覆盖
    js = (
        _P(__file__).resolve().parent.parent
        / "musespiral" / "studio" / "ui.js"
    ).read_text(encoding="utf-8")
    assert "anySel" in js, "imGroups 应产组级 anySel"
    assert "g.sel" in js, "默认选中应读 g.sel"
    assert "g.last.selected" not in js, "不得回退到末条 last.selected 判选中"
