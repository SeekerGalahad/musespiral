"""/api/visual 族 HTTP 契约测试（F71-T5/F72-T5/F74-T6；契约 §3.5）。

覆盖：POST /api/visual/{render,save,export,preset} 的 confirm 闸门 + 参数
校验 + 形状断言；GET /api/visual/{design,templates,presets}；F74-T6 预设
非 kebab → 400 + 落盘 + 列表可见；F72-T5 恶意 SVG → 200{ok:false} 无 svg。
真实回环服务器 + tmp_path + MUSESPIRAL_HOME 全隔离；无浏览器环境降 C 路。
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

from musespiral import studio


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def article(studio_root: Path) -> Path:
    path = studio_root / "demo.md"
    path.write_text(
        "---\ntitle: 缓存优化三步走\ndigest: 摘要\n---\n\n首段钩子。\n\n## 甲\n内容\n\n## 乙\n内容\n",
        encoding="utf-8",
    )
    return path


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
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


# ── POST /api/visual/render ─────────────────────────────────


def test_render_requires_confirm(server) -> None:
    code, payload = _request(server, "POST", "/api/visual/render",
                            {"template": "vermilion-slab", "spec": "mp"})
    assert code == 400 and "confirm" in payload["error"]


def test_render_ok_shape(server, article: Path) -> None:
    """正常 → 200 svg 字段以 `<svg` 开头、过白名单复验（F71-T5）。"""
    code, payload = _request(server, "POST", "/api/visual/render", {
        "confirm": True, "template": "vermilion-slab", "spec": "mp", "article": "demo.md",
    })
    assert code == 200
    assert payload["ok"] is True
    svg = payload["svg"]
    assert svg.startswith("<svg")
    assert 'viewBox="0 0 900 383"' in svg
    # 白名单复验（经 service verify_svg）
    from musespiral.services.visual_service import verify_svg
    assert verify_svg(svg) == svg


def test_render_missing_template(server) -> None:
    code, _ = _request(server, "POST", "/api/visual/render",
                      {"confirm": True, "spec": "mp"})
    assert code == 400


def test_render_invalid_spec(server) -> None:
    code, payload = _request(server, "POST", "/api/visual/render", {
        "confirm": True, "template": "vermilion-slab", "spec": "a4",
    })
    assert code == 400 and "spec" in payload["error"]


def test_render_unknown_template_400(server) -> None:
    code, payload = _request(server, "POST", "/api/visual/render", {
        "confirm": True, "template": "nope", "spec": "mp",
    })
    assert code == 400 and "未知模板" in payload["error"]


def test_render_color_slot_violation_400(server) -> None:
    """F72-T2 色槽限定：任意 hex 拒收 400 + 槽清单。"""
    code, payload = _request(server, "POST", "/api/visual/render", {
        "confirm": True, "template": "xhs-card", "spec": "xhs-3x4",
        "params": {"background_slot": "#ff00aa"},
    })
    assert code == 400
    assert "色槽" in payload["error"]


# ── POST /api/visual/save ───────────────────────────────────


def test_save_requires_confirm(server) -> None:
    code, _ = _request(server, "POST", "/api/visual/save",
                      {"article": "demo.md", "kind": "cover", "template": "vermilion-slab"})
    assert code == 400


def test_save_ok_shape(server, article: Path) -> None:
    code, payload = _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "demo.md", "kind": "cover",
        "template": "vermilion-slab", "params": {"hook": "钩子"},
    })
    assert code == 200 and payload["ok"] is True
    assert payload["dir"].endswith("demo.design")
    assert "design.json" in payload["files"] and "cover.svg" in payload["files"]
    assert (studio_root := article.parent) / "demo.design" / "design.json"


def test_save_invalid_kind(server, article: Path) -> None:
    code, payload = _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "demo.md", "kind": "nope",
        "template": "vermilion-slab",
    })
    assert code == 400


def test_save_article_not_found(server) -> None:
    code, payload = _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "nope.md", "kind": "cover", "template": "vermilion-slab",
    })
    assert code == 404


# ── POST /api/visual/export ─────────────────────────────────


def test_export_requires_confirm(server) -> None:
    code, _ = _request(server, "POST", "/api/visual/export",
                      {"article": "demo.md", "kind": "cover"})
    assert code == 400


def test_export_no_design_ok_false(server, article: Path) -> None:
    """设计稿缺席 → 200 {ok:false}（F72 错误补充，不进 job）。"""
    code, payload = _request(server, "POST", "/api/visual/export", {
        "confirm": True, "article": "demo.md", "kind": "cover",
    })
    assert code == 200 and payload["ok"] is False
    assert "先存稿" in payload["error"]


def test_export_creates_visual_export_job(server, article: Path, monkeypatch) -> None:
    """设计稿存 → export 进 job（kind=visual_export），轮询至 done，result 含 png/size/route。"""
    import time

    from musespiral.studio import actions as studio_actions

    studio_actions._reset_runner()
    _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "demo.md", "kind": "cover",
        "template": "vermilion-slab", "params": {"hook": "钩子"},
    })
    code, payload = _request(server, "POST", "/api/visual/export", {
        "confirm": True, "article": "demo.md", "kind": "cover",
    })
    assert code == 200 and payload["ok"] is True
    job_id = payload["job_id"]
    # 轮询至 done
    deadline = time.time() + 30
    job: dict = {}
    while time.time() < deadline:
        c, job = _request(server, "GET", f"/api/jobs/{job_id}")
        if c == 200 and job.get("status") in ("done", "failed"):
            break
        time.sleep(0.2)
    assert job.get("status") == "done", job
    assert job.get("kind") == "visual_export"
    result = job.get("result") or {}
    assert result["size"] == [900, 383]
    assert result["route"] == "C"
    assert result["png"].endswith("cover.png")


# ── POST /api/visual/preset ─────────────────────────────────


def test_preset_requires_confirm(server) -> None:
    code, _ = _request(server, "POST", "/api/visual/preset",
                      {"id": "p1", "template": "vermilion-slab"})
    assert code == 400


def test_preset_non_kebab_400(server) -> None:
    """F74-T6：id 非 kebab → 400。"""
    code, payload = _request(server, "POST", "/api/visual/preset", {
        "confirm": True, "id": "我的模板 1", "template": "vermilion-slab",
    })
    assert code == 400 and "kebab" in payload["error"]


def test_preset_ok_and_listed(server) -> None:
    """正常 → 200 {ok, id, path} 且 GET presets items 含该行（F74-T6）。"""
    code, payload = _request(server, "POST", "/api/visual/preset", {
        "confirm": True, "id": "my-cover-1", "template": "vermilion-slab",
        "params": {"font_size": "L"}, "label": "朱砂大封面",
    })
    assert code == 200 and payload["ok"] is True
    assert "~/.musespiral" in payload["path"]
    c2, presets = _request(server, "GET", "/api/visual/presets")
    assert c2 == 200
    assert any(row["id"] == "my-cover-1" for row in presets["items"])


# ── GET /api/visual/design ──────────────────────────────────


def test_design_get_requires_article(server) -> None:
    code, payload = _request(server, "GET", "/api/visual/design")
    assert code == 400 and "article" in payload["error"]


def test_design_get_not_found(server) -> None:
    code, _ = _request(server, "GET", "/api/visual/design?article=demo.md")
    assert code == 404


def test_design_get_ok(server, article: Path) -> None:
    _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "demo.md", "kind": "cover",
        "template": "vermilion-slab", "params": {"hook": "钩子"},
    })
    code, payload = _request(server, "GET", "/api/visual/design?article=demo.md&kind=cover")
    assert code == 200
    assert payload["template"] == "vermilion-slab"
    assert payload["spec"] == "mp"
    assert "<svg" in payload["svg"]


def test_design_get_malicious_svg_ok_false(server, article: Path) -> None:
    """F72-T5：手改 design 目录夹带 `<script>` → 200{ok:false}，无 svg 字段。"""
    _request(server, "POST", "/api/visual/save", {
        "confirm": True, "article": "demo.md", "kind": "cover",
        "template": "vermilion-slab", "params": {"hook": "钩子"},
    })
    (article.parent / "demo.design" / "cover.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    code, payload = _request(server, "GET", "/api/visual/design?article=demo.md&kind=cover")
    assert code == 200 and payload["ok"] is False
    assert "svg" not in payload


# ── GET /api/visual/templates ───────────────────────────────


def test_templates_get_four_items(server) -> None:
    code, payload = _request(server, "GET", "/api/visual/templates")
    assert code == 200
    ids = {row["id"] for row in payload["items"]}
    assert ids == {"vermilion-slab", "indigo-slab", "xhs-card", "points-three"}


def test_templates_get_zero_secrets(server) -> None:
    code, payload = _request(server, "GET", "/api/visual/templates")
    text = json.dumps(payload, ensure_ascii=False)
    for leak in ("api_key", "token", "secret", "password"):
        assert leak not in text


def test_templates_get_has_slot_colors(server) -> None:
    code, payload = _request(server, "GET", "/api/visual/templates")
    for row in payload["items"]:
        assert row["slot_colors"]
        assert "params_schema" in row
        assert row["color_slots"]


def test_templates_get_slot_hexes(server) -> None:
    """F-B 色板升级（2026-11-18）：顶层 slot_hexes={槽→hex} 与 color_slots
    同源同键集、值恒 #hex 形态（色块化 chip 的 style background 数据面）。"""
    from musespiral.core.visual.canvas.model import SEMANTIC_COLOR_SLOTS

    code, payload = _request(server, "GET", "/api/visual/templates")
    assert code == 200
    hexes = payload["slot_hexes"]
    assert set(hexes) == set(SEMANTIC_COLOR_SLOTS)  # 与槽集单一事实源同键
    for slot, hexv in hexes.items():
        assert hexv.startswith("#") and len(hexv) in (4, 7, 9), f"{slot}={hexv!r} 非 hex 形态"
    text = json.dumps(payload, ensure_ascii=False)
    for leak in ("api_key", "token", "secret", "password"):
        assert leak not in text  # 扩展不引入 secret 面


# ── GET /api/visual/presets ─────────────────────────────────


def test_presets_get_empty(server) -> None:
    code, payload = _request(server, "GET", "/api/visual/presets")
    assert code == 200 and payload["items"] == []


# ── 方法面：/api/visual GET 端点用 POST 走 405 ─────────────


def test_templates_post_405(server) -> None:
    """GET 端点用 POST → 405（方法不符由 server 方法表负责）。"""
    code, _ = _request(server, "POST", "/api/visual/templates", {"confirm": True})
    assert code == 405
