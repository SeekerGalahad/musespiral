"""F25 多提供商 AI 路由——档案制/任务路由/向后兼容/收敛断言。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.infra import ai
from musespiral.infra.ai import client as _client


def _write_ai(monkeypatch, tmp_path: Path, data: dict) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---- v1 兼容（旧扁平单配置读作 default 档案） ----


def test_v1_flat_config_reads_as_default(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {"base_url": "https://a/v1", "api_key": "k", "model": "m"})
    cfg = ai.load_ai_config()
    assert cfg["base_url"] == "https://a/v1"
    assert ai.ai_configured()


# ---- v2 档案 + 任务路由 ----


def test_profiles_and_routing(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {
        "default": "cloud",
        "profiles": {
            "cloud": {"base_url": "https://cloud/v1", "api_key": "sk", "model": "big"},
            "local": {"base_url": "http://127.0.0.1:11434/v1", "api_key": "", "model": "qwen3:8b"},
        },
        "routing": {"check_facts": "local"},
    })
    assert ai.load_ai_config("check_facts")["model"] == "qwen3:8b"   # 路由命中
    assert ai.load_ai_config("adapt")["model"] == "big"              # 无路由走默认
    assert ai.ai_configured("check_facts")                            # 本地无 key 也 configured


def test_routing_to_deleted_profile_falls_back(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {
        "default": "cloud",
        "profiles": {"cloud": {"base_url": "https://c/v1", "api_key": "k", "model": "c-m"}},
        "routing": {"adapt": "gone"},   # 指向已删档案
    })
    assert ai.load_ai_config("adapt")["model"] == "c-m"


def test_save_use_route_cli_roundtrip(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {"profiles": {}})
    ai.save_ai_profile("cloud", "https://c/v1", "sk-1234567890abcdef", "m1")
    ai.save_ai_profile("local", "http://l/v1", "", "m2", set_default=False)
    assert ai.load_ai_config()["model"] == "m1"      # 首写为默认
    ai.set_default_profile("local")
    assert ai.load_ai_config()["model"] == "m2"
    ai.set_route("check_facts", "cloud")
    assert ai.load_ai_config("check_facts")["model"] == "m1"
    st = ai.list_profiles()
    assert set(st["profiles"]) == {"cloud", "local"}
    masked = st["profiles"]["cloud"]["api_key"]
    assert masked.startswith("sk-123") and "abcdef" not in masked  # 掩码保留前 6 位，尾部完整值不出现
    assert st["routing"] == {"check_facts": "cloud"}


def test_route_unknown_task_rejected(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {"profiles": {"a": {"base_url": "u", "model": "m"}}})
    with pytest.raises(ai.AIError, match="未知任务"):
        ai.set_route("bogus", "a")
    with pytest.raises(ai.AIError, match="档案不存在"):
        ai.set_route("adapt", "nope")


# ---- chat() 统一入口行为 ----


def test_chat_local_no_key_no_auth_header(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {
        "default": "local",
        "profiles": {"local": {"base_url": "http://l/v1", "api_key": "", "model": "m"}},
    })
    captured = {}

    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"], captured["headers"], captured["json"] = url, headers, json
        return _R()

    monkeypatch.setattr(_client, "_post_chat_stream", lambda u,h,b,to: ai.adapt_mock(fake_post, u, h, b, to))
    assert ai.chat([{"role": "user", "content": "x"}], task="adapt") == "hello"
    assert "Authorization" not in captured["headers"]  # 本地无 key 不带头
    assert captured["url"] == "http://l/v1/chat/completions"


def test_chat_sse_leading_comment_lines(monkeypatch, tmp_path):
    """SSE 流前导注释行（: keepalive 等网关心跳）不得误判为普通 JSON——
    magpie 本地网关上游慢启动时实测先发 ": keepalive" 再发数据，
    旧版 startswith("data: ") 首行判定会落 JSON 分支抛「响应格式异常」。"""
    import io
    _write_ai(monkeypatch, tmp_path, {
        "default": "local",
        "profiles": {"local": {"base_url": "http://l/v1", "api_key": "", "model": "m"}},
    })
    sse = "\n".join([
        ": keepalive",
        ": keepalive",
        'data: {"choices":[{"delta":{"reasoning_content":"思考中"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"可用"}}]}',
        "data: [DONE]",
        "",
    ]).encode("utf-8")
    monkeypatch.setattr(_client, "_post_chat_stream", lambda u, h, b, to: io.BytesIO(sse))
    assert ai.chat([{"role": "user", "content": "x"}], task="adapt") == "可用"


def test_chat_unconfigured_raises(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {"profiles": {}})
    with pytest.raises(ai.AIError, match="AI 未配置"):
        ai.chat([{"role": "user", "content": "x"}])


# ---- chat_stream 增量形态（E-4 流式骨架批）----


def test_chat_stream_emits_deltas_in_order(monkeypatch, tmp_path):
    """SSE 逐 delta 到达即回调（顺序保持），最终返回拼接全文——生成期
    流式发布的数据面。坏块跳过 / [DONE] 截断 / 注释行容忍与 chat() 同款。"""
    import io
    _write_ai(monkeypatch, tmp_path, {
        "default": "local",
        "profiles": {"local": {"base_url": "http://l/v1", "api_key": "", "model": "m"}},
    })
    sse = "\n".join([
        ": keepalive",
        'data: {"choices":[{"delta":{"content":"<<<block kind:heading>>>题"}}]}',
        "",
        'data: {"choices":[{"delta":{"reasoning_content":"思考"}}]}',  # 无 content 键：零回调
        'data: not-json',  # 坏块跳过
        'data: {"choices":[{"delta":{"content":"文"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"绝不该出现"}}]}',  # DONE 后截断
        "",
    ]).encode("utf-8")
    monkeypatch.setattr(_client, "_post_chat_stream", lambda u, h, b, to: io.BytesIO(sse))
    got: list[str] = []
    out = ai.chat_stream([{"role": "user", "content": "x"}], task="compose", on_delta=got.append)
    assert got == ["<<<block kind:heading>>>题", "文"], got
    assert out == "<<<block kind:heading>>>题文"


def test_chat_stream_json_fallback_no_callbacks(monkeypatch, tmp_path):
    """非 SSE 响应（旧 mock / 纯 JSON 端点）：无增量可回调——on_delta 零调用、
    全文照常返回（增量是尽力语义；前端降级为完成后揭示）。"""
    import io
    _write_ai(monkeypatch, tmp_path, {
        "default": "local",
        "profiles": {"local": {"base_url": "http://l/v1", "api_key": "", "model": "m"}},
    })
    monkeypatch.setattr(_client, "_post_chat_stream", lambda u, h, b, to: io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "整段"}}]}).encode()))
    got: list[str] = []
    out = ai.chat_stream([{"role": "user", "content": "x"}], task="compose", on_delta=got.append)
    assert got == [] and out == "整段"


def test_chat_stream_empty_raises_and_request_shape_shared(monkeypatch, tmp_path):
    """空响应抛 AIError（与 chat() 同错误面）；请求体与 chat() 共用同一拼装点
    （同 task/temperature 下字节一致——F25 收敛语义不因新增量入口回退）。"""
    import io
    _write_ai(monkeypatch, tmp_path, {
        "default": "local",
        "profiles": {"local": {"base_url": "http://l/v1", "api_key": "k", "model": "m"}},
    })
    bodies = {}

    def fake_post(url, headers, body, timeout):
        bodies[url] = body
        return io.BytesIO(b"data: [DONE]\n")

    monkeypatch.setattr(_client, "_post_chat_stream", fake_post)
    with pytest.raises(ai.AIError, match="AI 响应为空"):
        ai.chat_stream([{"role": "user", "content": "x"}], task="compose")
    assert bodies["http://l/v1/chat/completions"] == json.dumps({
        "model": "m", "messages": [{"role": "user", "content": "x"}],
        "temperature": 0.6, "stream": True,
    }).encode("utf-8")


# ---- 收敛断言：四消费方不再各自拼 URL ----


def test_consumers_no_url_concat():
    """F25 技术债清偿锚定：消费方源码不再含 chat/completions（唯一拼接点=ai.py）。"""
    import musespiral.core.adaptation.generator, musespiral.core.quality.digest, musespiral.core.quality.factcheck
    import musespiral.studio.actions
    for mod in (musespiral.core.adaptation.generator, musespiral.core.quality.digest, musespiral.core.quality.factcheck, musespiral.studio.actions):
        import inspect
        src = inspect.getsource(mod)
        assert "chat/completions" not in src, f"{mod.__name__} 仍含 URL 拼接"
