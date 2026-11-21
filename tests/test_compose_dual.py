"""G-T3 双模型对比出稿（正典=《musespiral-调研吸收功能设计-2026-11-18》§四；2026-11-19）。

七层断言（任务书验收面）+ 一条并发行为锚：
  1) mock 双 profile 返回不同文本（同 prompt、并行、usage_a/usage_b 各自计）；
  2) 选 A → 替换 session 末轮 blocks 进正常裁决流（decisions 清空、rounds/N.md 同步）；
  3) 选 B → 同上（选的是 B 文本）；
  4) 保留双版 → draft_v_a.md / draft_v_b.md 两份独立草稿；
  5) 不存在 profile → 400；
  6) confirm 缺失 → 400；
  7) 契约锁 compose/dual + 前端结构（合成端点 generate|pick|keep 三形态）；
  8) **并发行为锚**（集成修复批 2026-11-19）：barrier 探针证明两路 chat
     同时在飞——退回串行实现即红（详见 test_dual_compose_is_really_parallel）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra.ai import gateway as gateway_mod
from musespiral.services import compose_service as svc

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
DUAL_JS = STUDIO / "assets" / "dual.js"
CORE_JS = STUDIO / "assets" / "core.js"
UI_JS = STUDIO / "ui.js"
SHELL = STUDIO / "ui.html"
COMPOSE_HTML = STUDIO / "ui.views" / "compose.html"

REPLY_A = "<<<block kind:heading>>># A 版标题\n\n<<<block kind:para>>>A 版独有正文段落。"
REPLY_B = "<<<block kind:heading>>># B 版标题\n\n<<<block kind:para>>>B 版独有正文段落。"
REPLY_SEED = "<<<block kind:para>>>种子轮正文。"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """两个已配置档案（本地无 key）——generate 端点 load_profile 校验要过。"""
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "localA",
            "profiles": {
                "localA": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock-a"},
                "localB": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock-b"},
            },
            "routing": {},
        }),
        encoding="utf-8",
    )
    return _isolate_home


def _fake_chat_calls(reply_a: str = REPLY_A, reply_b: str = REPLY_B):
    """gateway 形态假件：按 profile 分派不同正文，记录每次调用的 prompt/profile。"""
    calls: list[dict] = []

    def fake(messages, *, task, temperature=0.6, timeout=120, profile=None):
        calls.append({"task": task, "profile": profile, "content": messages[0]["content"]})
        return reply_a if profile == "localA" else reply_b

    return fake, calls


@pytest.fixture
def server(root: Path):
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: dict | None = None):
    import http.client

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
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _block_texts(slug: str, root: Path) -> list[str]:
    session, _ = svc._load_session(slug, root)
    return [b.text for r in session.rounds for b in r.blocks]


# ══ 1) 服务层：双 profile 返回不同文本 / 同 prompt / 真并行 ═══════════

def test_dual_compose_distinct_texts_same_prompt(root: Path, monkeypatch) -> None:
    fake, calls = _fake_chat_calls()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.new_session("dual", "对比", root=root)
    out = svc.dual_compose("dual", "localA", "localB", root=root)

    assert out["ok"] and out["ok_a"] and out["ok_b"]
    assert out["text_a"] == REPLY_A and out["text_b"] == REPLY_B
    assert out["usage_a"]["profile"] == "localA"
    assert out["usage_b"]["profile"] == "localB"
    assert out["usage_a"]["chars_out"] > 0 and out["usage_b"]["chars_out"] > 0
    assert len(calls) == 2
    assert {c["profile"] for c in calls} == {"localA", "localB"}
    # 关键前提：两版 prompt 必须逐字一致（「供应商可转换的科学验证」）
    assert calls[0]["content"] == calls[1]["content"]
    assert all(c["task"] == "compose" for c in calls)
    # 零落盘：对比不写 rounds（选定才落）
    d = root / "dual.compose" / "rounds"
    assert not d.exists() or not list(d.glob("*.md"))


def test_dual_compose_is_really_parallel(root: Path) -> None:
    """并发行为锚：两路 chat 必须**同时在飞**（屏障探针，串行实现必红）。

    为什么需要这条：`test_dual_compose_distinct_texts_same_prompt` 只断言
    「两版文本不同 + prompt 逐字一致」——把 `compose_service.py` 里
    `with ThreadPoolExecutor(max_workers=2) as pool:` 那段换成串行 for，
    那几条断言全部照旧成立，「对比耗时=max 而非 sum」这条设计承诺（§四）
    就只剩 docstring 兜着，无任何回归拦截。本用例用 `threading.Barrier(2)` 做
    并发探针：只有两路 `chat_fn` 真同时在场时屏障才放行；串行时首路等满
    timeout → `BrokenBarrierError` → 转断言失败并指出退化点。

    chat_fn 走 dual_compose 的注入参（不经 monkeypatch 全局），屏障超时
    取 5s——远大于线程池派发开销，又远小于用例级超时，抖动不会误报。
    """
    barrier = threading.Barrier(2, timeout=5)
    reached: list[str] = []
    lock = threading.Lock()

    def barrier_chat(messages, *, task, temperature=0.6, timeout=120, profile=None):
        with lock:
            reached.append(profile)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            raise AssertionError(
                f"并发探针失败：{profile} 在 2 方屏障上等到超时——另一路未同时在场，"
                "双模型对比已退化为串行（耗时=sum 而非 max）"
            ) from None
        return REPLY_A if profile == "localA" else REPLY_B

    svc.new_session("par", "并行", root=root)
    out = svc.dual_compose("par", "localA", "localB", root=root, chat_fn=barrier_chat)

    assert out["text_a"] == REPLY_A and out["text_b"] == REPLY_B
    assert sorted(reached) == ["localA", "localB"], "两路都应抵达屏障（=都在飞行中）"


def test_dual_compose_rejects_adopted_session(root: Path, monkeypatch) -> None:
    fake, _ = _fake_chat_calls()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.new_session("sealed", "已封版", root=root)
    svc.generate("sealed", root=root, chat_fn=lambda *a, **k: REPLY_SEED)
    # 裁决 + 采纳
    s, _ = svc._load_session("sealed", root)
    for b in s.rounds[-1].blocks:
        svc.decide("sealed", s.rounds[-1].n, b.id, "keep", root=root)
    svc.adopt("sealed", root=root)
    with pytest.raises(svc.ComposeError, match="已采纳"):
        svc.dual_compose("sealed", "localA", "localB", root=root)


# ══ 2/3) 选 A / 选 B 替换末轮块，进正常裁决流 ════════════════════════

@pytest.mark.parametrize("pick,expected", [("a", REPLY_A), ("b", REPLY_B)])
def test_apply_dual_pick_replaces_latest_round(
    pick: str, expected: str, root: Path, monkeypatch
) -> None:
    monkeypatch.setattr(gateway_mod, "_real_chat", _fake_chat_calls()[0])
    svc.new_session("pickme", "选定", root=root)
    svc.generate("pickme", root=root, chat_fn=lambda *a, **k: REPLY_SEED)
    before, _ = svc._load_session("pickme", root)
    assert before.rounds[-1].n == 1
    # 先给种子轮一个裁决，验证选定后被清空（旧裁决针对被替换的稿，不能沿用）
    svc.decide("pickme", 1, before.rounds[-1].blocks[0].id, "keep", root=root)

    result = svc.apply_dual_pick("pickme", expected, root=root, pick=pick)
    assert result["ok"] and result["round"] == 1 and result["pick"] == pick
    after, d = svc._load_session("pickme", root)
    assert len(after.rounds) == 1, "替换末轮而非新增轮"
    texts = [b.text for b in after.rounds[-1].blocks]
    chosen = "A 版独有正文段落。" if pick == "a" else "B 版独有正文段落。"
    assert any(chosen in t for t in texts), texts
    assert after.rounds[-1].decisions == [], "选定稿重置裁决"
    # rounds/1.md 存证同步为选定稿
    assert (d / "rounds" / "1.md").read_text(encoding="utf-8") == expected
    assert (d / "rounds" / "1.decisions.json").read_text(encoding="utf-8").strip() == "[]"


def test_apply_dual_pick_no_round_creates_first(root: Path) -> None:
    svc.new_session("fresh", "首稿", root=root)
    result = svc.apply_dual_pick("fresh", REPLY_A, root=root, pick="a")
    assert result["round"] == 1
    assert "A 版独有正文段落。" in " ".join(_block_texts("fresh", root))


# ══ 4) 保留双版 → 两份独立草稿 ══════════════════════════════════════

def test_keep_both_drafts_two_files(root: Path) -> None:
    svc.new_session("keepme", "双版", root=root)
    result = svc.keep_both_drafts(
        "keepme", REPLY_A, REPLY_B, root=root, profile_a="localA", profile_b="localB"
    )
    assert result["ok"] and len(result["drafts"]) == 2
    d = root / "keepme.compose"
    fa, fb = d / "draft_v_a.md", d / "draft_v_b.md"
    assert fa.is_file() and fb.is_file()
    assert "A 版独有正文段落。" in fa.read_text(encoding="utf-8")
    assert "B 版独有正文段落。" in fb.read_text(encoding="utf-8")
    assert "profile" not in result["drafts"][0]  # 相对路径，无绝对泄漏
    # 草稿落在 .compose 内（扫描器排除，不进文章队列）
    assert all(".compose" in p for p in result["drafts"])


# ══ 5/6/7) API 层：confirm / 不存在 profile / 契约锁 ══════════════════

def test_confirm_gate_and_profile_validation(
    server, ai_on: Path, monkeypatch
) -> None:
    from musespiral.studio.actions import _ACTION_ROUTES
    from tests.test_architecture import CONTRACT_POST_EXACT

    fake, _ = _fake_chat_calls()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    # 路由 + 契约锁
    assert "/api/compose/dual" in _ACTION_ROUTES
    assert "/api/compose/dual" in CONTRACT_POST_EXACT
    # 建会话
    code, _ = _request(server, "POST", "/api/compose/new",
                       {"confirm": True, "slug": "api", "title": "API"})
    assert code == 200

    # confirm 缺失 → 400（server 动作层统一闸门）
    code, err = _request(server, "POST", "/api/compose/dual",
                         {"action": "generate", "slug": "api",
                          "profile_a": "localA", "profile_b": "localB"})
    assert code == 400 and "confirm:true" in err["error"]

    # 不存在 profile → 400
    code, err = _request(server, "POST", "/api/compose/dual",
                         {"confirm": True, "action": "generate", "slug": "api",
                          "profile_a": "localA", "profile_b": "NO_SUCH_PROFILE"})
    assert code == 400 and "profile_b" in err["error"] and "NO_SUCH_PROFILE" in err["error"]

    # action 枚举外 → 400
    code, err = _request(server, "POST", "/api/compose/dual",
                         {"confirm": True, "action": "bogus", "slug": "api"})
    assert code == 400 and "generate|pick|keep" in err["error"]


def test_api_generate_pick_keep_roundtrip(server, ai_on: Path, monkeypatch, root: Path) -> None:
    fake, calls = _fake_chat_calls()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    _request(server, "POST", "/api/compose/new", {"confirm": True, "slug": "api2"})

    # generate → 两版
    code, body = _request(server, "POST", "/api/compose/dual", {
        "confirm": True, "action": "generate", "slug": "api2",
        "profile_a": "localA", "profile_b": "localB",
    })
    assert code == 200 and body["ok"]
    assert body["text_a"] == REPLY_A and body["text_b"] == REPLY_B
    assert body["usage_a"]["profile"] == "localA"
    assert body["usage_b"]["profile"] == "localB"

    # pick → 末轮替换为 A 版
    code, body = _request(server, "POST", "/api/compose/dual", {
        "confirm": True, "action": "pick", "slug": "api2", "text": REPLY_A, "pick": "a",
    })
    assert code == 200 and body["ok"] and body["pick"] == "a"
    assert any("A 版独有正文段落。" in t for t in _block_texts("api2", root))

    # keep → 两份草稿
    code, body = _request(server, "POST", "/api/compose/dual", {
        "confirm": True, "action": "keep", "slug": "api2",
        "text_a": REPLY_A, "text_b": REPLY_B,
        "profile_a": "localA", "profile_b": "localB",
    })
    assert code == 200 and body["ok"] and len(body["drafts"]) == 2
    assert all((root / p).is_file() for p in body["drafts"])


# ══ 前端：行为（node 纯函数）+ 结构 + 回环 ════════════════════════════

def _run_node(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环仍随全量跑）")
    src = DUAL_JS.read_text(encoding="utf-8")
    pure = src[src.index("/*__DUAL_BEGIN__*/"):src.index("/*__DUAL_END__*/")]
    script = (
        "'use strict';\n"
        "var location={hash:''};\n"
        "function setHash(h){location.hash=h;}\n"
        + pure
        + "\nmodule.exports={dEsc:dEsc,dSlug:dSlug,dUsage:dUsage,dProfileOptions:dProfileOptions,setHash:setHash};\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "dual_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_dual_slug_and_usage_and_escape() -> None:
    out = _run_node(r"""
setHash('#compose?s=demo%2Fx');
var a=dSlug();
setHash('#material?s=demo');
var b=dSlug();
var u=dUsage({profile:'localA',chars_in:100,chars_out:200,cache_hit:false});
var e=dEsc('<img src=x>');
console.log(JSON.stringify({a:a,b:b,u:u,e:e}));
""")
    assert out["a"] == "demo/x" and out["b"] == ""
    assert "localA" in out["u"] and "200" in out["u"]
    assert "<" not in out["e"] and "&lt;" in out["e"]


def test_dual_profile_options_two_distinct() -> None:
    out = _run_node(r"""
var h=dProfileOptions({localA:{},localB:{}},'localB');
console.log(JSON.stringify({h:h}));
""")
    assert out["h"].count("<option") == 2
    assert 'value="localB" selected' in out["h"]


def test_dual_frontend_structure_and_loader() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    panel = COMPOSE_HTML.read_text(encoding="utf-8")
    js = DUAL_JS.read_text(encoding="utf-8")
    ui = UI_JS.read_text(encoding="utf-8")
    core = CORE_JS.read_text(encoding="utf-8")
    # 右栏面板
    for token in ("cm-dual", "cm-dual-a", "cm-dual-b", "cm-dual-gen",
                  "cm-dual-result", "cm-dual-pick-a", "cm-dual-keep", "cm-dual-pick-b"):
        assert f'id="{token}"' in panel, token
    assert "⚖️ 对比模式" in panel
    # 端点三形态 + confirm
    assert "/api/compose/dual" in js and "confirm:true" in js
    for action in ("'generate'", "'pick'", "'keep'"):
        assert action in js, action
    # 独立产物懒加载（合成循环里字面量保留），不塞 core.js 实现
    assert "assets/dual.js" in ui and "assets/dual.js" in core
    assert "dPaintResult" not in core, "对比实现不得内联进 core.js"
    # 零外联
    for bad in ("http://", "https://", "//cdn", "unpkg", "jsdelivr"):
        assert bad not in js, f"dual.js 出现外联字样: {bad}"


def test_dual_asset_served(tmp_path: Path) -> None:
    import http.client

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/assets/dual.js")
        resp = conn.getresponse()
        body = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        assert resp.status == 200
        assert ctype.startswith("application/javascript"), ctype
        assert body == DUAL_JS.read_bytes()
    finally:
        httpd.shutdown()
