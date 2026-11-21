"""F45 ai check——汇总形状 / 错误分型 / --profile 覆盖（mock 网络层）。

网络层全程 mock：monkeypatch client._post_chat_stream（既有测试接缝，
test_ai.py 同款）返回 SSE 流——真实走 chat() 的流式解析/错误包装路径；
零真实网络、零真实 token。ai.json 写在 MUSESPIRAL_HOME 临时目录（不触真实
~/.musespiral）。
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.ai_cmds import ai_check
from musespiral.infra.ai import AIError
from musespiral.infra.ai.check import CHECK_PROMPT, classify_error, run_check
from musespiral.infra.ai.client import _sse_lines
from musespiral.infra.ai.profiles import TASKS
import musespiral.infra.ai.client as _client

_CLOUD = {"base_url": "https://cloud/v1", "api_key": "sk-cloud", "model": "big-model"}
_LOCAL = {"base_url": "http://127.0.0.1:11434/v1", "api_key": "", "model": "tiny-model"}


def _write_ai(monkeypatch, tmp_path: Path, data: dict) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _state(routing: dict | None = None) -> dict:
    return {
        "default": "cloud",
        "profiles": {"cloud": _CLOUD, "local": _LOCAL},
        "routing": routing or {},
    }


def _mock_net(monkeypatch, calls: list, fail_on_url: str | None = None, exc: Exception | None = None):
    """按 url 返回 SSE「OK」流并记录 (url, body)；fail_on_url 命中即抛 exc。"""
    def fake(url, headers, body, timeout):
        calls.append((url, headers, json.loads(body.decode("utf-8"))))
        if fail_on_url and url.startswith(fail_on_url):
            raise exc
        return io.BytesIO(b"".join(_sse_lines("OK")))

    monkeypatch.setattr(_client, "_post_chat_stream", fake)


@pytest.fixture(autouse=True)
def _no_real_net(monkeypatch):
    """保险丝：任何未显式 mock 的请求直接失败（测试绝不通真网）。"""
    monkeypatch.setattr(_client, "_post_chat_stream", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("测试未 mock 网络层却发起了请求")))


# ── run_check 数据面 ───────────────────────────────────────


def test_run_check_summary_shape(monkeypatch, tmp_path):
    """汇总形状：7 任务全行，任务×档案×通（routing 命中行档案名随路由变）。"""
    _write_ai(monkeypatch, tmp_path, _state(routing={"check_facts": "local"}))
    calls: list = []
    _mock_net(monkeypatch, calls)

    rs = run_check()

    assert [r.task for r in rs] == list(TASKS)          # 逐任务一行，白名单序
    assert all(r.ok and r.kind == "通" for r in rs)
    assert all(r.detail == "OK" for r in rs)
    prof = {r.task: r.profile for r in rs}
    assert prof["check_facts"] == "local"               # 路由命中
    assert prof["rewrite"] == "cloud"                   # 无路由走默认
    assert len(calls) == len(TASKS)
    for url, headers, body in calls:                    # 请求确为「回复：OK」级零内容
        assert body["messages"] == [{"role": "user", "content": CHECK_PROMPT}]
        assert body["temperature"] == 0.0
        assert body["stream"] is True


def test_classify_error_matrix():
    """错误分型全口径（对齐 client.py 既有错误消息格式）。"""
    assert classify_error("AI 未配置（base_url/model 缺失）。请先运行: …") == "未配置"
    assert classify_error("AI 返回 401: nope") == "认证失败(401)"
    assert classify_error("AI 返回 403: forbidden") == "认证失败(403)"
    assert classify_error("AI 返回 404: model missing") == "端点/模型不存在(404)"
    assert classify_error("AI 返回 429: slow down") == "限流(429)"
    assert classify_error("AI 返回 500: boom") == "HTTP 500"
    assert classify_error("调用 AI 失败: <urlopen error timed out>") == "网络/超时"
    assert classify_error("AI 响应格式异常: Expecting value") == "响应异常"
    assert classify_error("AI 响应为空（未收到内容）") == "响应异常"
    assert classify_error("别的什么错") == "未知错误"


def test_run_check_error_typing_per_task(monkeypatch, tmp_path):
    """分型集成：routing 指向 local 的任务 401，其余通——分型落在对应行。"""
    _write_ai(monkeypatch, tmp_path, _state(routing={"digest": "local"}))
    calls: list = []
    _mock_net(monkeypatch, calls, fail_on_url=_LOCAL["base_url"],
              exc=AIError("AI 返回 401: Invalid API key"))

    rs = run_check()

    by_task = {r.task: r for r in rs}
    assert by_task["digest"].ok is False
    assert by_task["digest"].kind == "认证失败(401)"
    assert "401" in by_task["digest"].detail
    assert by_task["rewrite"].ok and by_task["rewrite"].kind == "通"


def test_run_check_unconfigured_typing(monkeypatch, tmp_path):
    """档案存在但空（未配置档案）→ 逐行「未配置」分型，不炸整表。"""
    _write_ai(monkeypatch, tmp_path, {
        "default": "cloud",
        "profiles": {"cloud": {"base_url": "", "api_key": "", "model": ""}},
    })
    calls: list = []
    _mock_net(monkeypatch, calls)
    rs = run_check(["title"])
    assert rs[0].ok is False and rs[0].kind == "未配置"
    assert calls == []                                  # 未配置不发请求


# ── --profile 覆盖（含 wire 级证据） ─────────────────────────


def test_profile_override_reaches_wire(monkeypatch, tmp_path):
    """--profile 覆盖路由：展示名与请求体 model/URL 全部切到指定档案。"""
    _write_ai(monkeypatch, tmp_path, _state(routing={"check_facts": "local"}))
    calls: list = []
    _mock_net(monkeypatch, calls)

    rs = run_check(profile="local")

    assert all(r.profile == "local" for r in rs)        # 汇总表档案列
    assert all(url.startswith(_LOCAL["base_url"]) and body["model"] == _LOCAL["model"]
               for url, _h, body in calls)              # wire 级：真的走了 local


def test_chat_profile_param_beats_routing(monkeypatch, tmp_path):
    """chat(profile=…) 显式档案优先于 task 路由（client 层最小证据）。"""
    _write_ai(monkeypatch, tmp_path, _state(routing={"adapt": "local"}))
    calls: list = []
    _mock_net(monkeypatch, calls)
    from musespiral.infra.ai import chat
    out = chat([{"role": "user", "content": "x"}], task="adapt", profile="cloud")
    assert out == "OK"
    assert calls[0][0] == _CLOUD["base_url"] + "/chat/completions"


# ── CLI 层（ai check 命令） ─────────────────────────────────


def test_cli_summary_table_stdout_only(monkeypatch, tmp_path):
    """stdout 汇总表：表头/逐行 任务×档案×结果/汇总行；且零落盘。"""
    _write_ai(monkeypatch, tmp_path, _state())
    calls: list = []
    _mock_net(monkeypatch, calls)
    r = CliRunner().invoke(ai_check, [])
    assert r.exit_code == 0, r.output
    assert "任务" in r.output and "AI 连通性检查" in r.output
    for t in TASKS:
        assert t in r.output                            # 任务列齐全
    assert "cloud" in r.output and "✅" in r.output
    assert f"汇总: {len(TASKS)}/{len(TASKS)} 通" in r.output
    assert set(p.name for p in tmp_path.iterdir()) == {"ai.json"}   # 不落盘
    assert json.loads((tmp_path / "ai.json").read_text())["routing"] == {}  # ai.json 不被改写


def test_cli_task_filter_single(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, _state())
    calls: list = []
    _mock_net(monkeypatch, calls)
    r = CliRunner().invoke(ai_check, ["--task", "title"])
    assert r.exit_code == 0, r.output
    assert "rewrite" not in r.output
    assert "汇总: 1/1 通" in r.output
    assert len(calls) == 1


def test_cli_profile_missing_no_request(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, _state())
    calls: list = []
    _mock_net(monkeypatch, calls)
    r = CliRunner().invoke(ai_check, ["--profile", "nope"])
    assert r.exit_code == 1
    assert "档案不存在: nope" in r.output and "cloud/local" in r.output
    assert calls == []                                  # 预检挡在发请求之前


def test_cli_no_profiles_short_circuit(monkeypatch, tmp_path):
    _write_ai(monkeypatch, tmp_path, {"profiles": {}})
    calls: list = []
    _mock_net(monkeypatch, calls)
    r = CliRunner().invoke(ai_check, [])
    assert r.exit_code == 1
    assert "AI 未配置" in r.output
    assert calls == []


def test_cli_failure_exit_code_and_breakdown(monkeypatch, tmp_path):
    """有不通 → exit 1 + 分型汇总行（错误分型进汇总）。"""
    _write_ai(monkeypatch, tmp_path, _state(routing={"digest": "local", "title": "local"}))
    calls: list = []
    _mock_net(monkeypatch, calls, fail_on_url=_LOCAL["base_url"],
              exc=AIError("AI 返回 401: Invalid API key"))
    r = CliRunner().invoke(ai_check, [])
    assert r.exit_code == 1
    assert "❌" in r.output and "认证失败(401)" in r.output
    assert f"汇总: {len(TASKS) - 2}/{len(TASKS)} 通" in r.output
    assert "认证失败(401)×2" in r.output
