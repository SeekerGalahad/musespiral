"""Studio 设置面 API 契约与安全红线（第十八期 F46/F47，M1 Group A）。

覆盖（沿 test_studio.py 惯例：真实回环服务器 + http.client，tmp_path 与
MUSESPIRAL_HOME 全隔离，不触 ~/.musespiral、零网络）：
  - GET /api/settings 两域形状：kb 域（root/root_exists/sources/origin——
    读侧级联 kb_sources.json → kb_root.txt → 内置默认源表）+ ai 域
    （profiles key 恒 has_key 布尔 / style 内容与 habits 条数 / prompts
    任务表+覆盖表+插槽）；
  - POST /api/settings/kb：confirm 闸门；root 相对路径/不存在 400；
    relpath 绝对路径/../ 上跳/缺失目录 400（越界拒绝）；重复 key 400；
    快乐路径落盘 kb_sources.json 且 knowledge_root() 随之切换（新文件优先）；
  - POST /api/settings/ai：save_profile 的 key write-only（响应无明文、
    空值=保留旧值）；set_default/set_route（未知任务 400）；save_style；
    save_prompt 方言守门（违规 400 且不落盘 / 合规落盘并进 GET overrides）；
  - 方法纪律：GET 走 /api/settings、POST 走两域路由、未知子路径 404。
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest

from musespiral import config, studio

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """知识库根 fixture：13-source-analysis / 11-learning-path / 模式家族 三源目录。"""
    root = tmp_path / "kb"
    (root / "13-source-analysis").mkdir(parents=True)
    (root / "11-learning-path").mkdir()
    (root / "13-source-analysis" / "模式家族").mkdir()
    return root


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    return root


@pytest.fixture
def server(studio_root: Path) -> studio.ThreadingHTTPServer:
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
    return resp.status, json.loads(data) if data else None


# ── GET /api/settings：两域形状 ────────────────────────────


def test_settings_get_kb_builtin_defaults(server) -> None:
    """零配置：kb 域 origin=builtin、root None、内置默认源表三源。"""
    code, d = _request(server, "GET", "/api/settings")
    assert code == 200
    kb = d["kb"]
    assert kb["root"] is None and kb["root_exists"] is False
    assert kb["origin"] == "builtin"
    assert [s["key"] for s in kb["sources"]] == [
        "13-source-analysis", "11-learning-path", "pattern-family",
    ]
    assert {s["label"] for s in kb["sources"]} == {"源码分析", "学习路径", "模式家族"}


def test_settings_get_ai_domain_shape(server) -> None:
    """ai 域三面：profiles（key 布尔）/style（内容+habits 条数）/prompts（任务+覆盖+插槽）。"""
    code, d = _request(server, "GET", "/api/settings")
    assert code == 200
    ai = d["ai"]
    assert set(ai) == {"profiles", "style", "prompts"}
    st = ai["profiles"]
    assert st["profiles"] == {} and st["default"] is None
    assert "compose" in st["tasks"]  # 创作台任务键在可覆盖清单
    assert ai["style"] == {"content": "", "habits_count": 0}
    pr = ai["prompts"]
    assert pr["overrides"] == {}
    # G-T4 滚动摘要批（2026-11-19）：slots 四槽 → 五槽（增 {{rolling}}，设计 §五）
    # E-5 小说模式批（2026-11-19）：五槽 → 七槽（增 {{novel}}/{{characters}}，
    # 评审 §3.6 前情提要 + 人物卡；空则整节缺席、缺槽回补仍注入）
    # FEAT-4 小说双件套批（2026-11-22）：七槽 → 八槽（增 {{foreshadowing}} 未
    # 回收伏笔清单，session.foreshadowing 的 planted/escalated 条目）
    assert set(pr["slots"]) == {
        "{{style}}", "{{sources}}", "{{novel}}", "{{characters}}",
        "{{foreshadowing}}", "{{rolling}}", "{{draft}}", "{{decisions}}"}


def test_settings_get_kb_root_txt_fallback(server, kb, tmp_path) -> None:
    """读侧兼容：仅旧 kb_root.txt → root 生效 + 内置默认源表（origin=kb_root.txt）。"""
    home = Path(config.home_dir())
    home.mkdir(parents=True, exist_ok=True)
    (home / config.KB_ROOT_FILE).write_text(str(kb), encoding="utf-8")
    code, d = _request(server, "GET", "/api/settings")
    assert code == 200
    assert d["kb"]["origin"] == config.KB_ROOT_FILE
    assert Path(d["kb"]["root"]) == kb.resolve()
    assert d["kb"]["root_exists"] is True
    assert [s["key"] for s in d["kb"]["sources"]] == ["13-source-analysis", "11-learning-path", "pattern-family"]
    # knowledge_root() 同一读侧（既有消费方零迁移）
    assert config.knowledge_root() == kb.resolve()


# ── POST /api/settings/kb：confirm + 存在性 + 越界拒绝 ─────


def test_settings_kb_requires_confirm(server, kb) -> None:
    code, d = _request(server, "POST", "/api/settings/kb", {"root": str(kb)})
    assert code == 400 and d["ok"] is False


def test_settings_kb_rejects_relative_and_missing_root(server, kb) -> None:
    for bad in ("kb", "docs/kb", str(kb.parent / "no-such-dir")):
        code, d = _request(server, "POST", "/api/settings/kb", {"root": bad, "confirm": True})
        assert code == 400, bad
        assert d["ok"] is False


def test_settings_kb_rejects_bad_relpaths(server, kb) -> None:
    """relpath 绝对路径 / .. 上跳（含解析后等于根的 ../<name>）/ 缺失目录 → 400。"""
    cases = [
        {"key": "a", "label": "A", "relpath": str(kb)},                    # 绝对路径
        {"key": "b", "label": "B", "relpath": "../kb"},                    # .. 上跳
        {"key": "c", "label": "C", "relpath": "13-source-analysis/../../.."},  # 逃逸
        {"key": "d", "label": "D", "relpath": "no-such-dir"},              # 目录缺失
        {"key": "e", "label": "E", "relpath": "13-source-analysis/x.md"},  # 非目录
    ]
    for src in cases:
        code, d = _request(server, "POST", "/api/settings/kb",
                           {"root": str(kb), "sources": [src], "confirm": True})
        assert code == 400, src


def test_settings_kb_rejects_duplicate_keys_and_shapes(server, kb) -> None:
    dup = [
        {"key": "x", "label": "1", "relpath": "13-source-analysis"},
        {"key": "x", "label": "2", "relpath": "11-learning-path"},
    ]
    code, _ = _request(server, "POST", "/api/settings/kb", {"root": str(kb), "sources": dup, "confirm": True})
    assert code == 400
    code, _ = _request(server, "POST", "/api/settings/kb", {"root": str(kb), "sources": "bad", "confirm": True})
    assert code == 400
    code, _ = _request(server, "POST", "/api/settings/kb",
                       {"root": str(kb), "sources": [{"key": "x"}], "confirm": True})
    assert code == 400  # 缺 label/relpath


def test_settings_kb_requires_root_first(server) -> None:
    """根未配置且未随请求提供 → 400（先 set-root 再管源）。"""
    code, d = _request(server, "POST", "/api/settings/kb",
                       {"sources": [{"key": "a", "label": "A", "relpath": "x"}], "confirm": True})
    assert code == 400 and d["ok"] is False


def test_settings_kb_happy_path_writes_and_switches_root(server, kb) -> None:
    sources = [
        {"key": "src", "label": "源码分析", "relpath": "13-source-analysis"},
        {"key": "fam", "label": "模式家族", "relpath": "13-source-analysis/模式家族"},
    ]
    code, d = _request(server, "POST", "/api/settings/kb",
                       {"root": str(kb), "sources": sources, "confirm": True})
    assert code == 200 and d["ok"] is True
    assert Path(d["root"]) == kb.resolve()
    # 落盘 kb_sources.json 且读侧级联切换（新文件优先于 kb_root.txt）
    path = config.kb_sources_path()
    assert path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [s["key"] for s in saved["sources"]] == ["src", "fam"]
    assert config.knowledge_root() == kb.resolve()
    # GET 回读：origin=file、每源 exists 校验态
    code, d = _request(server, "GET", "/api/settings")
    assert d["kb"]["origin"] == config.KB_SOURCES_FILE
    assert [s["exists"] for s in d["kb"]["sources"]] == [True, True]


def test_settings_kb_empty_sources_allowed(server, kb) -> None:
    """空源表=用户清空（合法态：发现页仅「全部」档），不是回退内置缺省。"""
    code, d = _request(server, "POST", "/api/settings/kb", {"root": str(kb), "sources": [], "confirm": True})
    assert code == 200
    code, d = _request(server, "GET", "/api/settings")
    assert d["kb"]["sources"] == [] and d["kb"]["origin"] == config.KB_SOURCES_FILE


# ── POST /api/settings/ai：write-only key / 路由 / 风格 / prompts ──


def test_settings_ai_profile_key_write_only(server) -> None:
    secret = "sk-test-123456789"
    code, d = _request(server, "POST", "/api/settings/ai", {
        "action": "save_profile", "name": "cloud",
        "base_url": "https://api.example.com/v1", "model": "m1",
        "api_key": secret, "confirm": True,
    })
    assert code == 200 and d == {"ok": True, "name": "cloud", "has_key": True}
    code, d = _request(server, "GET", "/api/settings")
    prof = d["ai"]["profiles"]["profiles"]["cloud"]
    assert prof["has_key"] is True and prof["configured"] is True
    assert "api_key" not in prof  # 无掩码串字段——key 态恒布尔
    assert secret not in json.dumps(d)  # 全响应无明文（write-only 红线）


def test_settings_ai_profile_update_keeps_old_key(server) -> None:
    _request(server, "POST", "/api/settings/ai", {
        "action": "save_profile", "name": "cloud",
        "base_url": "https://api.example.com/v1", "model": "m1",
        "api_key": "sk-keep-me", "confirm": True,
    })
    # 更新不带 api_key（空值）→ 保留旧 key；模型名更新生效
    code, d = _request(server, "POST", "/api/settings/ai", {
        "action": "save_profile", "name": "cloud",
        "base_url": "https://api.example.com/v1", "model": "m2", "confirm": True,
    })
    assert code == 200 and d["has_key"] is True
    code, d = _request(server, "GET", "/api/settings")
    prof = d["ai"]["profiles"]["profiles"]["cloud"]
    assert prof["model"] == "m2" and prof["has_key"] is True


def test_settings_ai_default_and_route(server) -> None:
    for name, url in (("a", "https://a/v1"), ("b", "https://b/v1")):
        _request(server, "POST", "/api/settings/ai", {
            "action": "save_profile", "name": name, "base_url": url,
            "model": "m", "api_key": "", "confirm": True,
        })
    code, d = _request(server, "POST", "/api/settings/ai",
                       {"action": "set_default", "name": "b", "confirm": True})
    assert code == 200 and d["default"] == "b"
    code, _ = _request(server, "POST", "/api/settings/ai",
                       {"action": "set_default", "name": "nope", "confirm": True})
    assert code == 400
    code, d = _request(server, "POST", "/api/settings/ai",
                       {"action": "set_route", "task": "digest", "profile": "a", "confirm": True})
    assert code == 200 and d["routing"] == {"digest": "a"}
    code, _ = _request(server, "POST", "/api/settings/ai",
                       {"action": "set_route", "task": "bogus", "profile": "a", "confirm": True})
    assert code == 400


def test_settings_ai_style_save(server) -> None:
    code, d = _request(server, "POST", "/api/settings/ai",
                       {"action": "save_style", "content": "范文一段。", "confirm": True})
    assert code == 200
    code, d = _request(server, "GET", "/api/settings")
    assert d["ai"]["style"]["content"] == "范文一段。"
    assert (config.home_dir() / "style_samples.md").read_text(encoding="utf-8") == "范文一段。"


def test_settings_ai_prompt_dialect_gate_and_cascade(server) -> None:
    """覆盖级联：违规拒写（400 且不落盘）→ 合规落盘 → GET overrides 命中；未知任务 400。"""
    code, d = _request(server, "POST", "/api/settings/ai", {
        "action": "save_prompt", "task": "compose",
        "content": "请模仿 glm-4 的口吻", "confirm": True,
    })
    assert code == 400 and "glm" in d["error"]
    assert not (config.home_dir() / "prompts.d" / "compose.txt").exists()
    code, d = _request(server, "POST", "/api/settings/ai", {
        "action": "save_prompt", "task": "compose",
        "content": "底座 {{style}} {{sources}} {{draft}} {{decisions}}", "confirm": True,
    })
    assert code == 200 and d["saved"] == "compose.txt"
    code, d = _request(server, "GET", "/api/settings")
    assert d["ai"]["prompts"]["overrides"]["compose"].startswith("底座 {{style}}")
    code, _ = _request(server, "POST", "/api/settings/ai",
                       {"action": "save_prompt", "task": "bogus", "content": "x", "confirm": True})
    assert code == 400


def test_settings_ai_unknown_action(server) -> None:
    code, _ = _request(server, "POST", "/api/settings/ai", {"action": "nuke", "confirm": True})
    assert code == 400


# ── 方法纪律 ───────────────────────────────────────────────


def test_settings_method_discipline(server) -> None:
    code, _ = _request(server, "GET", "/api/settings/kb")   # POST 路由 GET → 405
    assert code == 405
    code, _ = _request(server, "POST", "/api/settings")     # GET 路由 POST → 405
    assert code == 405
    code, _ = _request(server, "POST", "/api/settings/other", {"confirm": True})  # 未知子路径 → 404
    assert code == 404
