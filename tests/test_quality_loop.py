"""质量循环（P1-A，契约 §3.26）单元与接线测试——AI 全程注入假件，不真实联网。

覆盖任务书硬性断言：
  - mock checks FAIL → 修复被调用 → 重跑 PASS → 停止（rounds/fixes 计数）；
  - max_rounds 达到后仍 FAIL → final_result 含失败项；
  - 修复稿写 .quality_fix.md 且**原文一字节不动**（红线）；
  - AI 缺位/降级 → 明确报「AI 不可用」而非静默假成功；
  - CLI（quality-loop 命令）与 Studio（/api/action/quality_loop job）接线。
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.core_cmds import quality_loop as quality_loop_cmd
from musespiral.infra import ai
from musespiral.jobs.store import KINDS
from musespiral.services import quality_loop as ql

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _write_article(tmp_path: Path, *, cover: bool = True) -> Path:
    fm = "title: 循环样例\ndigest: 样例摘要\n"
    if cover:
        fm += "cover: cover.png\n"
    p = tmp_path / "loop_sample.md"
    p.write_text(f"---\n{fm}---\n\n# 循环样例\n\n正文一段。\n", encoding="utf-8")
    return p


def _setup_ai(tmp_path: Path) -> None:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True, exist_ok=True)
    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-ql-test", "ql-model")


# ── 服务层：mock checks 驱动的循环语义 ─────────────────────


def test_loop_fix_then_pass_stops(tmp_path, monkeypatch) -> None:
    """首轮 FAIL → AI 修复被调用 → 重跑 PASS → 即停（不再调用 checks）。"""
    _setup_ai(tmp_path)
    p = _write_article(tmp_path)
    calls = {"n": 0}

    def fake_checks(article, path, platform=None):
        calls["n"] += 1
        return [("FAIL", "封面图未设置（frontmatter.cover 必填）")] if calls["n"] == 1 else [
            ("PASS", "全部通过")
        ]

    monkeypatch.setattr(ql, "run_checks", fake_checks)
    chat_calls = {"n": 0}

    def fake_chat(messages, **kwargs):
        chat_calls["n"] += 1
        assert kwargs.get("task") == "rewrite"
        return "修复后的正文"

    out = ql.loop(p, max_rounds=3, chat_fn=fake_chat)
    assert out["rounds"] == 2
    assert len(out["fixes_applied"]) == 1 and "第 1 轮" in out["fixes_applied"][0]
    assert out["final_result"] == [("PASS", "全部通过")]
    assert calls["n"] == 2, "PASS 后必须停止（不重复跑 checks）"
    assert chat_calls["n"] == 1, "PASS 后不再调 AI"
    assert ql.fix_preview_path(p).exists()


def test_loop_max_rounds_still_fail(tmp_path, monkeypatch) -> None:
    """max_rounds 达到后仍 FAIL → final_result 含失败项（不静默假成功）。"""
    _setup_ai(tmp_path)
    p = _write_article(tmp_path)
    calls = {"n": 0}

    def fake_checks(article, path, platform=None):
        calls["n"] += 1
        return [("FAIL", "封面图未设置（frontmatter.cover 必填）")]

    monkeypatch.setattr(ql, "run_checks", fake_checks)
    out = ql.loop(p, max_rounds=2, chat_fn=lambda m, **k: "修复稿")
    assert out["rounds"] == 2  # 首轮 + 一次修复后重跑 = 2，达上限即停
    assert len(out["fixes_applied"]) == 1
    assert ("FAIL", "封面图未设置（frontmatter.cover 必填）") in out["final_result"]
    assert calls["n"] == 2


def test_loop_no_fix_single_round(tmp_path, monkeypatch) -> None:
    """auto_fix=False：只跑一轮检查，AI 零调用。"""
    p = _write_article(tmp_path)
    chat_calls = {"n": 0}

    def fake_chat(messages, **kwargs):
        chat_calls["n"] += 1
        return "不应被调用"

    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("FAIL", "x")])
    out = ql.loop(p, auto_fix=False, chat_fn=fake_chat)
    assert out["rounds"] == 1 and out["fixes_applied"] == []
    assert chat_calls["n"] == 0
    assert not ql.fix_preview_path(p).exists()


def test_loop_ai_unavailable_raises(tmp_path, monkeypatch) -> None:
    """AI 缺位（未配置）→ 明确报「AI 不可用」，不静默假成功。"""
    p = _write_article(tmp_path)  # 无 ai.json
    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("FAIL", "x")])
    with pytest.raises(ai.AIError, match="AI 不可用"):
        ql.loop(p, max_rounds=3)


def test_loop_ai_empty_response_raises(tmp_path, monkeypatch) -> None:
    """AI 配置但空响应 → AIError（AI 不可用），不把空修复当成功。"""
    _setup_ai(tmp_path)
    p = _write_article(tmp_path)
    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("FAIL", "x")])
    with pytest.raises(ai.AIError, match="AI 不可用"):
        ql.loop(p, max_rounds=3, chat_fn=lambda m, **k: "   ")


def test_loop_invalid_max_rounds(tmp_path) -> None:
    p = _write_article(tmp_path)
    with pytest.raises(ValueError, match="max_rounds"):
        ql.loop(p, max_rounds=0)


# ── 红线：原文零触碰 + 预览件形态 ──────────────────────────


def test_fix_preview_never_touches_original(tmp_path, monkeypatch) -> None:
    """真实 checks（封面缺失 FAIL）+ 假 AI：.quality_fix.md 存在且原文不变。"""
    _setup_ai(tmp_path)
    p = _write_article(tmp_path, cover=False)  # 真实 run_checks → 封面缺失 FAIL
    original = p.read_bytes()

    def fake_chat(messages, **kwargs):
        # FAIL 清单进入 prompt（AI 知道要修什么）
        assert "封面图未设置" in messages[-1]["content"]
        return "# 修复后正文\n\n新内容。"

    out = ql.loop(p, max_rounds=3, chat_fn=fake_chat)
    assert p.read_bytes() == original, "原文一字节不动（红线）"
    preview = ql.fix_preview_path(p)
    assert preview.exists()
    text = preview.read_text(encoding="utf-8")
    assert "修复后正文" in text
    assert "title: 循环样例" in text  # frontmatter 沿袭原文
    assert "ai_model: ql-model" in text
    assert len(out["fixes_applied"]) >= 1


# ── CLI ───────────────────────────────────────────────────


def test_cli_pass_exit_zero(tmp_path, monkeypatch) -> None:
    _setup_ai(tmp_path)
    p = _write_article(tmp_path)
    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("PASS", "全部通过")])
    r = CliRunner().invoke(quality_loop_cmd, [str(p)])
    assert r.exit_code == 0, r.output
    assert "质量循环完成：1 轮检查，0 次修复" in r.output


def test_cli_fail_with_no_fix_exit_one(tmp_path, monkeypatch) -> None:
    p = _write_article(tmp_path)
    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("FAIL", "封面图未设置")])
    r = CliRunner().invoke(quality_loop_cmd, [str(p), "--no-fix"])
    assert r.exit_code == 1
    assert "❌ [FAIL] 封面图未设置" in r.output or "封面图未设置" in r.output
    assert not ql.fix_preview_path(p).exists()


def test_cli_ai_unavailable_readable(tmp_path, monkeypatch) -> None:
    """CLI：AI 未配置 + 有 FAIL → 可读「AI 不可用」错误，exit 1 无栈。"""
    p = _write_article(tmp_path)
    monkeypatch.setattr(ql, "run_checks", lambda *a, **k: [("FAIL", "封面图未设置")])
    r = CliRunner().invoke(quality_loop_cmd, [str(p)])
    assert r.exit_code == 1
    assert "AI 不可用" in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_cli_preview_hint_after_fix(tmp_path, monkeypatch) -> None:
    _setup_ai(tmp_path)
    p = _write_article(tmp_path)
    seq = {"n": 0}

    def fake_checks(article, path, platform=None):
        seq["n"] += 1
        return [("FAIL", "x")] if seq["n"] == 1 else [("PASS", "ok")]

    monkeypatch.setattr(ql, "run_checks", fake_checks)
    # CLI 不注入 chat_fn（生产路径）——mock 治理通道模块属性（同进程单点）
    monkeypatch.setattr(
        ql, "chat_policed", lambda messages, **kw: {"text": "修复后的正文", "cache_hit": False}
    )
    r = CliRunner().invoke(quality_loop_cmd, [str(p), "--max-rounds", "3"])
    assert r.exit_code == 0, r.output
    assert "loop_sample.quality_fix.md" in r.output
    assert "人工确认后手动替换" in r.output


# ── Studio 接线（真实回环服务器）───────────────────────────


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    shutil.copy(FIXTURES / "cover.png", root / "cover.png")
    (root / "assets").mkdir()
    shutil.copy(FIXTURES / "assets" / "fig1.png", root / "assets" / "fig1.png")
    return root


@pytest.fixture
def server(studio_root: Path):
    from musespiral import studio

    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _post(server, path: str, body: dict) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    return resp.status, payload


def _get_job(server, job_id: str, timeout: float = 10.0) -> dict:
    import time

    host, port = server.server_address[:2]
    deadline = time.time() + timeout
    while time.time() < deadline:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/api/jobs/{job_id}")
        resp = conn.getresponse()
        job = json.loads(resp.read())
        conn.close()
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} 未到终态")


def test_jobs_kinds_include_quality_loop() -> None:
    """具名锚：quality_loop 入白名单（19→20，计数随扩键同步）。"""
    assert "quality_loop" in KINDS and len(KINDS) == 21  # B1 +localbook_fetch


def test_studio_quality_loop_all_pass_zero_ai(server, studio_root: Path) -> None:
    """样例文章 checks 全过 → 单轮完成，AI 零调用、零修复、零预览件。"""
    code, payload = _post(server, "/api/action/quality_loop",
                          {"article": "sample_article.md", "confirm": True})
    assert code == 200 and payload["ok"] is True
    job = _get_job(server, payload["job_id"])
    assert job["status"] == "done" and job["kind"] == "quality_loop"
    result = job["result"]
    assert result["rounds"] == 1
    assert result["fixes_applied"] == []
    assert result["final_result"], "检查报告原样返回"
    assert all(item["level"] != "FAIL" for item in result["final_result"])
    assert "output" not in result
    assert not (studio_root / "sample_article.quality_fix.md").exists()


def test_studio_quality_loop_mock_ai(server, studio_root: Path, tmp_path: Path, monkeypatch) -> None:
    """mock AI：FAIL 文章 → job done + 预览件 + 原文不动 + result.output。"""
    broken = studio_root / "broken.md"
    broken.write_text("---\ntitle: 破文\ndigest: 摘要\n---\n\n# 破文\n\n正文。\n", encoding="utf-8")
    original = broken.read_bytes()
    _setup_ai(tmp_path)  # MUSESPIRAL_HOME = tmp_path/musespiral-home

    import musespiral.services.quality_loop as ql_mod

    seq = {"n": 0}

    def fake_checks(article, path, platform=None):
        seq["n"] += 1
        return [("FAIL", "封面图未设置")] if seq["n"] == 1 else [("PASS", "ok")]

    monkeypatch.setattr(ql_mod, "run_checks", fake_checks)
    monkeypatch.setattr(
        ql_mod, "chat_policed",
        lambda messages, **kw: {"text": "修复后的正文", "cache_hit": False},
    )

    code, payload = _post(server, "/api/action/quality_loop",
                          {"article": "broken.md", "max_rounds": 2, "confirm": True})
    assert code == 200 and payload["ok"] is True
    job = _get_job(server, payload["job_id"])
    assert job["status"] == "done"
    result = job["result"]
    assert result["rounds"] == 2
    assert result["output"] == "broken.quality_fix.md"
    assert result["fixes_applied"] and "1 项 FAIL" in result["fixes_applied"][0]
    assert broken.read_bytes() == original  # 红线：原文零触碰
    preview = studio_root / "broken.quality_fix.md"
    assert preview.exists() and "修复后的正文" in preview.read_text(encoding="utf-8")


def test_studio_quality_loop_gates(server, studio_root: Path) -> None:
    """confirm 闸门 400；max_rounds 非法 400 零 job；AI 缺位落 job.error 可读。"""
    code, payload = _post(server, "/api/action/quality_loop", {"article": "sample_article.md"})
    assert code == 400 and "confirm" in payload.get("error", "")

    code, payload = _post(server, "/api/action/quality_loop",
                          {"article": "sample_article.md", "max_rounds": 9, "confirm": True})
    assert code == 400 and "max_rounds" in payload["error"]

    code, payload = _post(server, "/api/action/quality_loop",
                          {"article": "no-such.md", "confirm": True})
    assert code == 404

    broken = studio_root / "broken2.md"
    broken.write_text("---\ntitle: t\n---\n\n正文\n", encoding="utf-8")
    # AI 未配置（无 ai.json）：FAIL 文章 → job failed，error 明确「AI 不可用」
    code, payload = _post(server, "/api/action/quality_loop",
                          {"article": "broken2.md", "confirm": True})
    assert code == 200 and payload["ok"] is True
    job = _get_job(server, payload["job_id"])
    assert job["status"] == "failed"
    assert "AI 不可用" in (job.get("error") or "")
    assert not (studio_root / "broken2.quality_fix.md").exists()
