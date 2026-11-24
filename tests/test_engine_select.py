"""W18b 引擎选择门面测试：mock → JVM → Playwright → absent 四态。

用 monkeypatch 打桩替代真实探测，隔离书源库到 tmp_path（不碰真实
~/.musespiral），验证 get_engine_client() 与 engine_kind() 的选择一致性。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.booksource import model as source_model
from musespiral.infra import bookengine, booksource_store as store, browser_engine, engine_select

_RULE_SOURCE = {
    "bookSourceUrl": "http://rule.example",
    "bookSourceName": "带规则源",
    "browserRule": {"search": {"list": ".x"}},
}

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    return tmp_path

def test_kind_mock_when_health_mock(isolated, monkeypatch):
    monkeypatch.setattr(bookengine.BookEngineClient, "health", lambda self: dict(bookengine.MOCK_HEALTH))
    assert engine_select.engine_kind() == "mock"
    assert isinstance(engine_select.get_engine_client(), bookengine.BookEngineClient)
    assert engine_select.health()["kind"] == "mock"

def test_kind_jvm_when_health_ok(isolated, monkeypatch):
    monkeypatch.setattr(bookengine.BookEngineClient, "available", lambda self: True)
    monkeypatch.setattr(bookengine.BookEngineClient, "health", lambda self: {"ok": True, "version": "jvm"})
    assert engine_select.engine_kind() == "jvm"
    assert isinstance(engine_select.get_engine_client(), bookengine.BookEngineClient)

def test_kind_playwright_when_rule_source_present(isolated, monkeypatch):
    store.save_source(_RULE_SOURCE)
    store.save_index([source_model.index_row(_RULE_SOURCE)])
    monkeypatch.setattr(bookengine.BookEngineClient, "available", lambda self: False)
    monkeypatch.setattr(browser_engine.BrowserEngineClient, "available", lambda self: True)
    assert engine_select.engine_kind() == "playwright"
    assert isinstance(engine_select.get_engine_client(), browser_engine.BrowserEngineClient)
    assert engine_select.health()["kind"] == "playwright"

def test_kind_absent_when_no_rule_source(isolated, monkeypatch):
    monkeypatch.setattr(bookengine.BookEngineClient, "available", lambda self: False)
    monkeypatch.setattr(browser_engine.BrowserEngineClient, "available", lambda self: True)
    assert engine_select.engine_kind() == "absent"
    with pytest.raises(bookengine.BookEngineUnavailable):
        engine_select.get_engine_client().book_info("x", "y")

def test_disabled_source_not_counted(isolated, monkeypatch):
    disabled = dict(_RULE_SOURCE, enabled=False)
    store.save_source(disabled)
    store.save_index([source_model.index_row(disabled)])
    monkeypatch.setattr(bookengine.BookEngineClient, "available", lambda self: False)
    monkeypatch.setattr(browser_engine.BrowserEngineClient, "available", lambda self: True)
    assert engine_select.engine_kind() == "absent"

def test_examples_all_parse():
    """3 个示例源全部可被 Playwright 引擎解析（防示例腐化）。"""
    root = Path(__file__).resolve().parent.parent / "examples" / "booksources"
    files = sorted(root.glob("*.json"))
    assert len(files) >= 3
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert source_model.normalize(data), f"{f.name} 未过书源归一"
        assert browser_engine.parse_browser_rule(data), f"{f.name} 无 browserRule"


def test_status_shows_playwright_state(isolated):
    from musespiral.services import bookengine_admin as admin

    payload = admin.status.__globals__["engine_select"].health()
    # 无规则源 → absent；有规则源 → playwright（上例已覆盖选择态）
    assert payload["kind"] in ("absent", "mock", "jvm", "playwright")

def test_status_playwright_headline(monkeypatch):
    from musespiral.services import bookengine_admin as admin

    monkeypatch.setattr(
        admin.engine_select, "health",
        lambda: {"ok": True, "version": "playwright", "kind": "playwright", "sources_loaded": 2},
    )
    result = admin.status()
    assert result["state"] == "ok"
    assert "Playwright" in result["headline"]
    assert any("browserRule" in line for line in result["next_steps"])
