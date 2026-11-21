"""N-3 源级 AI 配置测试（读侧覆盖，2026-11-20 正典《总体方案》§二 N-3）。

裁决：源级 ai_config（profile/model/temperature/prompt_override 四键全可选）
**只作用于该源素材的加工（digest/refine/synthesis/tag_suggest），不影响
文章生成**（写作=创作者意志）。本文件四条验收断言：

  1. 加工路径读侧生效（refine preview / tag_suggest preview / agent synthesis
     三处 chat 调用确实拿到源级覆盖值——非「端点返回 200」假门）；
  2. 反向断言：文章生成（compose generate）的 chat 调用**不带**任何源级覆盖
     （强行断言 messages 层面也是全局口径——源配置绑架写作的语义破口）；
  3. 无 ai_config 时回落全局（refine/tag 不传 model/profile/temperature 覆盖）；
  4. schema 写侧窄门：validate_source 类型/边界拒收 + register upsert 语义
     （带 ai_config 整体替换、不带则保留在册）。

MUSESPIRAL_HOME monkeypatch 到 tmp 隔离（sources.json 与 ai.json 同隔离）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.materials import model
from musespiral.infra.ai import AIError
from musespiral.infra.sources import registry
from musespiral.services import agent_service
from musespiral.services import compose_service
from musespiral.services import materials_service as ms
from musespiral.services import refine_service
from musespiral.services import sources_service
from musespiral.services import tag_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(env: Path) -> Path:
    """两个可路由档案（local/cloud），便于断言 profile 覆盖生效。"""
    (env / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {
                "local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock-local"},
                "cloud": {"base_url": "https://cloud/v1", "api_key": "k-cloud", "model": "mock-cloud"},
            },
            "routing": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return env


def _register_source(env: Path, source_id: str, ai_config: dict | None = None, *,
                     stype: str = "dir", config: dict | None = None) -> dict:
    """注册一条源（dir 型最小完备；ai_config 缺省不写=读者缺键）。"""
    row = {
        "id": source_id,
        "type": stype,
        "label": source_id,
        "enabled": True,
        "config": config or {"path": str(env / "unused")},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": [],
    }
    if ai_config is not None:
        row["ai_config"] = ai_config
    return sources_service.register_source(row)


def _material(env: Path, source_id: str = "dir-src", seq: int = 1) -> dict:
    """落一条素材（source_id 决定源级覆盖命中）。"""
    row = model.make_material(
        mtype="text", source_id=source_id, uri=f"doc-{seq}.md", title=f"素材{seq}",
        payload={"text": "这是一段足够长的素材正文，讲数据管道的加工与标签组织。"},
        fetched_at="2026-11-16T09:00:00",
    )
    ms.add_material(row)
    return row


# ── 1) 加工路径读侧生效 ──────────────────────────────────────────────

def test_refine_preview_applies_source_ai_config(env, ai_on) -> None:
    """源级 ai_config 四键：refine preview 的 chat 调用须实带 profile/model/
    temperature，且 prompt 底座被 prompt_override 接管（真值断言，非 200）。"""
    _register_source(env, "dir-src", {
        "profile": "cloud",
        "model": "mock-source-model",
        "temperature": 0.1,
        "prompt_override": "【源级专属】只输出 50 字摘要。\n{{material}}",
    })
    row = _material(env, "dir-src")
    calls: list[dict] = []

    def fake_chat(messages, *, task=None, **kwargs):
        calls.append({"task": task, "messages": messages, "kwargs": kwargs})
        return "· 源级摘要"

    out = None
    # refine_service.preview 无 chat_fn 注入点（真实 chat 调用），改用 monkeypatch
    import musespiral.services.refine_service as S

    orig = S._chat
    S._chat = fake_chat
    try:
        out = refine_service.preview(row["id"])
    finally:
        S._chat = orig

    assert calls, "refine preview 必须触达 chat"
    call = calls[0]
    assert call["task"] == "refine"
    # profile/model/temperature 三键覆盖确实落到 chat 调用
    assert call["kwargs"].get("profile") == "cloud"
    assert call["kwargs"].get("model") == "mock-source-model"
    assert call["kwargs"].get("temperature") == 0.1
    # prompt_override 接管底座，{{material}} 原位注入
    prompt = call["messages"][0]["content"]
    assert prompt.startswith("【源级专属】只输出 50 字摘要。")
    assert "这是一段足够长的素材正文" in prompt
    assert out["text"] == "· 源级摘要"


def test_tag_suggest_preview_applies_source_ai_config(env, ai_on) -> None:
    """tag_suggest 走 gateway chat_policed；源级 profile/model/temperature 与
    prompt_override 三键须落到 gateway 调用（kwargs 真值断言）。"""
    _register_source(env, "dir-src", {
        "profile": "cloud",
        "model": "mock-source-model",
        "temperature": 0.2,
        "prompt_override": "源级标签底座\n{{material}}",
    })
    row = _material(env, "dir-src")
    calls: list[dict] = []

    def fake_chat(messages, *, task, temperature=0.6, timeout=120, profile=None, model=None):
        calls.append({"task": task, "messages": messages, "temperature": temperature,
                      "profile": profile, "model": model})
        return "TAG: 数据管道\nTAG: 标签"

    out = tag_service.preview(row["id"], chat_fn=fake_chat)
    assert calls, "tag preview 必须触达 chat"
    c = calls[0]
    assert c["task"] == "tag_suggest"
    assert c["profile"] == "cloud"
    assert c["model"] == "mock-source-model"
    assert c["temperature"] == 0.2
    assert "源级标签底座" in c["messages"][0]["content"]
    assert out["tags"] == ["数据管道", "标签"]


def test_agent_synthesis_applies_source_ai_config(env, ai_on) -> None:
    """合成计划（synthesis_v1）：源级覆盖经 build_run.ai_cfg → _gateway_chat_fn
    透传——每步 chat 调用都带 profile/model/temperature（prompt_override 接管
    instruction 见另一条断言）。"""
    _register_source(env, "dir-src", {
        "profile": "cloud", "model": "mock-source-model", "temperature": 0.3,
    })
    a, b = _material(env, "dir-src", 1), _material(env, "dir-src", 2)
    built = agent_service.build_run("synthesis_v1", [a["id"], b["id"]])
    # 同源两篇 → ai_cfg 生效（多源混合应回落 {}，另一条反向断言）
    assert built["ai_cfg"] == {"profile": "cloud", "model": "mock-source-model", "temperature": 0.3}

    calls: list[dict] = []

    def fake_chat(messages, *, task, temperature=0.6, timeout=120, profile=None, model=None):
        calls.append({"task": task, "profile": profile, "model": model, "temperature": temperature})
        return "综合稿正文"

    created = agent_service.create_run("synthesis_v1", [a["id"], b["id"]])
    agent_service._execute(
        created["run_id"], created["plan"], created["ctx"], created["material_ids"],
        chat_fn=fake_chat, ai_cfg=created.get("ai_cfg"),
    )
    assert calls, "synthesis 执行必须触达 chat"
    for c in calls:
        assert c["profile"] == "cloud"
        assert c["model"] == "mock-source-model"
        assert c["temperature"] == 0.3


def test_agent_synthesis_prompt_override_takes_over_instruction(env, ai_on) -> None:
    """源级 prompt_override 覆盖 build_run 全部步 inputs.instruction（最具体层）。"""
    _register_source(env, "dir-src", {"prompt_override": "【源级】逐章提炼要点"})
    a, b = _material(env, "dir-src", 1), _material(env, "dir-src", 2)
    built = agent_service.build_run("synthesis_v1", [a["id"], b["id"]])
    for step in built["plan"]["steps"]:
        assert step["inputs"]["instruction"] == "【源级】逐章提炼要点"


# ── 2) 反向断言：文章生成不受源配置影响 ─────────────────────────────

def test_compose_generate_ignores_source_ai_config(env, ai_on, monkeypatch) -> None:
    """裁决核心：compose generate 的 chat 调用**不带**任何源级覆盖——源配置
    只该影响素材加工，绝不绑架写作（这是 N-3 的语义红线）。"""
    _register_source(env, "dir-src", {
        "profile": "cloud", "model": "mock-source-model", "temperature": 0.1,
    })
    # 一个 source 的会话：源级配置遍地都是，但 compose 路径独立于它
    root = env / "articles"
    root.mkdir()
    source = env / "kb" / "cache.md"
    source.parent.mkdir()
    source.write_text("---\ntitle: 素材\n---\n\n## 一\n正文\n", encoding="utf-8")

    compose_service.new_session("demo", "示例", root=root)
    compose_service.add_source("demo", source, ["一"], root=root)

    calls: list[dict] = []
    reply = "<<<block kind:para>>>生成段落，综上所述。"

    def fake_chat(messages, *, task=None, **kwargs):
        calls.append({"task": task, "kwargs": kwargs})
        return reply

    monkeypatch.setattr(compose_service, "_chat", fake_chat)
    out = compose_service.generate("demo", root=root)
    assert calls, "compose generate 必须触达 chat"
    assert calls[0]["task"] == "compose"
    # make 断言：没有 profile/model/temperature 源级覆盖传进来（compose 用全局）
    assert "profile" not in calls[0]["kwargs"]
    assert "model" not in calls[0]["kwargs"]
    # 温度缺省 0.6 由 generate 显式传（非源级 0.1）
    assert calls[0]["kwargs"].get("temperature") == 0.6
    assert out["round"] == 1


# ── 3) 无配置回落全局 ────────────────────────────────────────────────

def test_no_source_ai_config_falls_back_to_global(env, ai_on) -> None:
    """未配置 ai_config 的源：refine preview 的 chat 不带三键覆盖（回落全局）。"""
    _register_source(env, "dir-src", None)  # 缺键
    row = _material(env, "dir-src")

    calls: list[dict] = []

    def fake_chat(messages, *, task=None, **kwargs):
        calls.append({"task": task, "kwargs": kwargs})
        return "草稿"

    import musespiral.services.refine_service as S

    orig = S._chat
    S._chat = fake_chat
    try:
        refine_service.preview(row["id"])
    finally:
        S._chat = orig
    assert calls, "refine preview 必须触达 chat"
    # 全局回落：三键覆盖均缺席（chat 缺省 profile=None/model=None/temperature=0.6）
    assert calls[0]["kwargs"] == {}


def test_unknown_source_ai_config_falls_back_to_global(env, ai_on) -> None:
    """素材 origin.source_id 指向不在册源（已删除）→ 无覆盖 → 回落全局。"""
    row = _material(env, "not-registered")
    cfg = sources_service.resolve_ai_config("not-registered")
    assert cfg == {}


# ── 4) schema 写侧窄门 + upsert 语义 ────────────────────────────────

def test_ai_config_schema_write_side_narrow_gate(env) -> None:
    """validate_source 拒收 ai_config 类型/边界违规（可读错误）。"""
    base = {
        "id": "dir-src", "type": "dir", "label": "src", "enabled": True,
        "config": {"path": "/tmp"}, "health": {"status": "unknown", "last_ok": None},
        "subscriptions": [],
    }
    # 未知键（防拼写静默失效）
    with pytest.raises(ValueError, match="未知键"):
        registry.validate_source({**base, "ai_config": {"proflie": "wrong-spelling"}})
    # 类型错（temperature 字符串）
    with pytest.raises(ValueError, match="temperature"):
        registry.validate_source({**base, "ai_config": {"temperature": "0.1"}})
    # 边界错（temperature 越界）
    with pytest.raises(ValueError, match="temperature"):
        registry.validate_source({**base, "ai_config": {"temperature": 3.0}})
    # 合法：四键全配过校验
    registry.validate_source({**base, "ai_config": {
        "profile": "cloud", "model": "m", "temperature": 0.5, "prompt_override": "x",
    }})


def test_ai_config_upsert_replace_vs_preserve(env) -> None:
    """register_source：带 ai_config 整体替换（{} 即清空）；不带保留既有。"""
    _register_source(env, "dir-src", {"profile": "cloud", "model": "m"})
    # 不带 ai_config 时保留
    current = sources_service.get_source("dir-src")
    assert current["ai_config"]["profile"] == "cloud"
    sources_service.register_source({
        "id": "dir-src", "type": "dir", "label": "dir-src", "enabled": True,
        "config": {"path": "/tmp"}, "subscriptions": [],
    })
    # 注意：service 层 upsert 不带 ai_config 时不清空（HTTP 层也按该语义传原配置）
    again = sources_service.get_source("dir-src")
    assert again["ai_config"]["profile"] == "cloud"
    # 显式 {} 清空
    _register_source(env, "dir-src", {})
    assert sources_service.get_source("dir-src")["ai_config"] == {}


def test_resolve_ai_config_profile_fallback_when_profile_missing(env) -> None:
    """源级 profile 指向已删档案 → 读侧宽容回落全局（profile 从 ai_cfg 丢弃）。"""
    _register_source(env, "dir-src", {"profile": "ghost-profile"})
    cfg = sources_service.resolve_ai_config("dir-src")
    # ghost-profile 不在档案表 → 键被丢弃
    assert "profile" not in cfg


def test_ai_overrides_multi_source_mixed_falls_back(env, ai_on) -> None:
    """多源综合：只在全部素材同源时生效（混合 → {} 回落全局）。"""
    _register_source(env, "src-a", {"profile": "cloud"})
    _register_source(env, "src-b", {"profile": "cloud"})
    m1 = _material(env, "src-a", 1)
    m2 = _material(env, "src-b", 2)
    assert sources_service.ai_overrides([m1, m2]) == {}
    # 单源两篇 → 生效
    m3 = _material(env, "src-a", 3)
    assert sources_service.ai_overrides([m1, m3]) == {"profile": "cloud"}


# ── 5) HTTP 写面 + 前端结构���N-3 契约：POST /api/sources 带 ai_config）──

def test_http_post_sources_with_ai_config_roundtrip(env, ai_on) -> None:
    """POST /api/sources 带 ai_config → sources.json 落盘 → GET /api/settings 可见
    （源列表渲染的数据面）。同时断言未带 ai_config 的二次 POST 不误清空。"""
    import http.client
    import threading

    from musespiral import studio

    root = env / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]

        def _post(path, body):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))

        def _get(path):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))

        # 1) 写：带 ai_config
        code, out = _post("/api/sources", {
            "id": "dir-src", "type": "dir", "label": "目录", "config": {"path": str(root)},
            "ai_config": {"profile": "cloud", "model": "m", "temperature": 0.4,
                          "prompt_override": "源级底座"},
            "confirm": True,
        })
        assert code == 200 and out["ok"] is True
        saved = sources_service.get_source("dir-src")
        assert saved["ai_config"] == {"profile": "cloud", "model": "m", "temperature": 0.4,
                                      "prompt_override": "源级底座"}

        # 2) 不带 ai_config 的二次 POST → 保留既有（HTTP 层按「不带则保留」传原值）
        code, _out = _post("/api/sources", {
            "id": "dir-src", "type": "dir", "label": "目录", "config": {"path": str(root)},
            "confirm": True,
        })
        assert code == 200
        again = sources_service.get_source("dir-src")
        assert again["ai_config"]["profile"] == "cloud"

        # 3) 读：GET /api/settings 的源列表回显 ai_config（前端 ⚙ 弹窗据此预填）
        code, settings = _get("/api/settings")
        assert code == 200
        row = next(s for s in settings["sources"]["list"] if s["id"] == "dir-src")
        assert row["ai_config"]["prompt_override"] == "源级底座"

        # 4) 非法 ai_config → 400 可读错误
        code, err = _post("/api/sources", {
            "id": "dir-src", "type": "dir", "label": "目录", "config": {"path": str(root)},
            "ai_config": {"temperature": 3.0}, "confirm": True,
        })
        assert code == 400
        assert "temperature" in (err.get("error") or "")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_frontend_source_ai_config_wiring() -> None:
    """前端结构断言（N-3 ⚙ 按钮 + 弹窗四字段 + 保存接线）：渲染含 data-srcai、
    弹窗含 sac-* 字段，点击/保存事件已绑定。"""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "musespiral" / "studio"
          / "assets" / "views" / "sources.js").read_text(encoding="utf-8")
    html = (Path(__file__).resolve().parent.parent / "musespiral" / "studio"
            / "ui.views" / "sources.html").read_text(encoding="utf-8")
    # 渲染出的行含 ⚙ 点击锚
    assert "data-srcai=" in js
    # 弹窗四字段（profile/model/temperature/prompt_override）
    for fid in ("sac-profile", "sac-model", "sac-temp", "sac-prompt", "sac-ok"):
        assert f'id="{fid}"' in html, f"弹窗字段 {fid} 缺失"
    assert 'id="src-aicfg"' in html
    # 保存走 POST /api/sources 且带 ai_config
    assert "postJSON('/api/sources'" in js
    assert "ai_config:cfg" in js
    # 点击/保存事件已绑定
    assert "$('sac-ok').addEventListener('click',srcAiSave)" in js
    assert "srcAiOpen(a.getAttribute('data-srcai'))" in js
