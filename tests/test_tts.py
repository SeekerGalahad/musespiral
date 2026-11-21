"""G-T5 TTS 朗读（正典=《musespiral-调研吸收功能设计-2026-11-18》§六；2026-11-19）。

**全部用例零外部网络**：`edge_tts.Communicate` 在每个触碰合成路径的用例里都被
替换成假件（`_FakeCommunicate`，写一段带 MP3 帧同步字的字节）。没有任何用例
真调微软端点——微软端点不可达/限流不该让本仓测试转红，也不该让 CI 打外部网络。

断言面（任务书验收面逐条对应）：
  a) 声音白名单：7 种中文声音在册、越界拒、缺省取 Yunxi、CLI Choice 同源派生；
  b) 语速校验：`10%`/`+10%`/`-10%`/`0%` 规整、`>±100%` 与形态非法拒；
  c) 服务层：Communicate 收到 (文本, voice, rate)、产物落 `<stem>.mp3`、
     临时文件不留、空文本拒、错误页字节拒；
  d) 朗读脚本：标题+正文进、代码块/表格/图片不进、行内标签与实体剥净；
  e) 端点层（真实回环服务器）：契约锁 POST+1/GET+1、confirm 闸门、article
     缺失/越界/不存在、voice 越界 400、rate 非法 400、金路径 job done 且
     result 有 path/voice/rate/bytes、GET /api/tts/file 三道门（404/魔数）；
  f) KINDS 扩键：tts_generate 在白名单、计数锁随批演进（现 19——PL-2
     插件流水线批 +plugin_pipeline，本文件只锚 tts_generate 在册）；
  g) CLI：`musespiral tts <article>` 落盘、`--voice` 白名单外由 click 拒（exit 2）；
  h) 前端：tts.js 纯函数（path 解析/URL 编码/摘要/按钮态）、结构锚
     （按钮 + audio + 装载钩子）、零外联、回环逐字节一致。
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral import studio
from musespiral.cli import cli
from musespiral.core.tts.voice import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    RATE_MAX_PERCENT,
    VOICE_NAMES,
    looks_like_mp3,
    normalize_rate,
    validate_voice,
)
from musespiral.services import tts_service

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
TTS_JS = STUDIO / "assets" / "tts.js"
CORE_JS = STUDIO / "assets" / "core.js"
UI_JS = STUDIO / "ui.js"
SHELL = STUDIO / "ui.html"
TMPL = STUDIO / "ui.tmpl.html"

# 一段合法的 MP3 头（帧同步字 0xFFFB + 后续字节）；内容本身不需要可解码
# ——被测的是「落盘/回放管道」，不是 MP3 编解码器。
MP3_BODY = b"\xff\xfb\x90\x00" + b"\x11" * 240


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


class _FakeCommunicate:
    """edge_tts.Communicate 假件：记录参数，save_sync 写一段带 MP3 头的字节。

    `fails` 置真时 save_sync 抛 OSError（模拟网络/端点不可达）；`payload`
    可改写成非 MP3 字节（模拟远端返回错误页）。
    """

    calls: list[dict] = []
    fails = False
    payload = MP3_BODY
    instance_count = 0

    def __init__(self, text: str, voice: str, *, rate: str = "+0%", **kwargs) -> None:
        type(self).instance_count += 1
        type(self).calls.append({"text": text, "voice": voice, "rate": rate})
        self._opts = kwargs

    def save_sync(self, audio_fname, metadata_fname=None) -> None:  # noqa: ANN001
        if type(self).fails:
            raise OSError("模拟端点不可达")
        Path(audio_fname).write_bytes(type(self).payload)


@pytest.fixture
def fake_tts(monkeypatch) -> type[_FakeCommunicate]:
    """把 edge_tts.Communicate 换成假件（**零真实网络**——本文件所有合成路径都过这里）。"""
    import edge_tts

    _FakeCommunicate.calls = []
    _FakeCommunicate.fails = False
    _FakeCommunicate.payload = MP3_BODY
    _FakeCommunicate.instance_count = 0
    monkeypatch.setattr(edge_tts, "Communicate", _FakeCommunicate)
    return _FakeCommunicate


def _article(root: Path, name: str = "post.md", body: str | None = None) -> Path:
    path = root / name
    path.write_text(
        body
        if body is not None
        else (
            "---\ntitle: 一篇文章的标题\n---\n\n第一段正文，用于朗读。\n\n"
            "## 小节标题\n\n第二段正文。\n\n```python\nprint('不应被朗读')\n```\n"
        ),
        encoding="utf-8",
    )
    return path


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    try:
        return resp.status, json.loads(data), ctype
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.status, data, ctype


def _wait_job(server, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, job, _ = _request(server, "GET", f"/api/jobs/{job_id}")
        assert code == 200
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


@pytest.fixture
def server(root: Path):
    from musespiral.studio.actions import _reset_runner

    _reset_runner()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _reset_runner()


# ══ a) 声音白名单 ════════════════════════════════════════════════════


def test_voice_whitelist_contains_design_recommended_voices() -> None:
    """设计 §6.2 两种推荐声音在册（Yunxi 男声自然 / Xiaoxiao 女声温柔）。"""
    assert "zh-CN-YunxiNeural" in VOICE_NAMES
    assert "zh-CN-XiaoxiaoNeural" in VOICE_NAMES
    # 任务书点名 Yunyang 亦须在册
    assert "zh-CN-YunyangNeural" in VOICE_NAMES
    assert len(VOICE_NAMES) == 7
    assert DEFAULT_VOICE == "zh-CN-YunxiNeural"


def test_voice_validation_accepts_whitelist_rejects_others() -> None:
    assert validate_voice(None) == DEFAULT_VOICE
    assert validate_voice("  zh-CN-XiaoxiaoNeural  ") == "zh-CN-XiaoxiaoNeural"
    for bad in ("en-US-EmmaNeural", "zh-cn-yunxineural", "yunxi", 42):
        with pytest.raises(ValueError) as exc:
            validate_voice(bad)
        assert "白名单" in str(exc.value)


def test_cli_choice_derives_from_whitelist() -> None:
    """CLI 的 --voice 选项从 VOICE_NAMES 派生（单一事实源，加声音两侧同时生效）。"""
    from musespiral.commands.tts_cmds import tts as tts_cmd

    opt = next(p for p in tts_cmd.params if p.name == "voice")
    assert tuple(opt.type.choices) == VOICE_NAMES


# ══ b) 语速校验 ══════════════════════════════════════════════════════


def test_rate_normalization() -> None:
    assert normalize_rate(None) == DEFAULT_RATE == "+0%"
    assert normalize_rate("10%") == "+10%"
    assert normalize_rate("+10%") == "+10%"
    assert normalize_rate("-10%") == "-10%"
    assert normalize_rate("0%") == "+0%"
    assert normalize_rate(" -100% ") == "-100%"
    assert normalize_rate("+100%") == "+100%"


def test_rate_rejects_out_of_range_and_malformed() -> None:
    for bad in (f"{RATE_MAX_PERCENT + 1}%", f"-{RATE_MAX_PERCENT + 1}%", "abc", "10", "+10", "1.5%"):
        with pytest.raises(ValueError):
            normalize_rate(bad)


# ══ c) 服务层（全 mock，零网络）════════════════════════════════════


def test_synthesize_passes_voice_and_rate_to_communicate(fake_tts, tmp_path: Path) -> None:
    """Communicate 收到 (文本, voice, rate)——参数不被吞、不被改写。"""
    out = tmp_path / "x.mp3"
    result = tts_service.synthesize("一段要朗读的文字。", "zh-CN-XiaoxiaoNeural", "-10%", out_path=out)
    assert fake_tts.calls == [
        {"text": "一段要朗读的文字。", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%"}
    ]
    assert out.is_file() and out.read_bytes() == MP3_BODY
    assert result["voice"] == "zh-CN-XiaoxiaoNeural"
    assert result["rate"] == "-10%"
    assert result["bytes"] == len(MP3_BODY)
    assert result["chars"] == len("一段要朗读的文字。")


def test_synthesize_article_writes_stem_mp3_beside_article(fake_tts, root: Path) -> None:
    """产物路径 = 文章同目录 `<stem>.mp3`（设计 §6.2）；临时文件不留。"""
    article = _article(root)
    result = tts_service.synthesize_article(article)
    target = root / "post.mp3"
    assert Path(result["output"]) == target
    assert target.is_file() and target.read_bytes() == MP3_BODY
    assert sorted(p.name for p in root.iterdir()) == ["post.md", "post.mp3"]
    # 朗读脚本：标题进、正文进、代码块不进
    text = fake_tts.calls[0]["text"]
    assert "一篇文章的标题" in text and "第一段正文" in text
    assert "print(" not in text, "代码块不应进朗读脚本"


def test_synthesize_rejects_empty_text_and_bad_payload(fake_tts, tmp_path: Path) -> None:
    with pytest.raises(tts_service.TTSServiceError) as exc:
        tts_service.synthesize("   \n ", out_path=tmp_path / "e.mp3")
    assert "为空" in str(exc.value)

    fake_tts.payload = b"<html>error page</html>"
    with pytest.raises(tts_service.TTSSynthesisError) as exc:
        tts_service.synthesize("正文。", out_path=tmp_path / "bad.mp3")
    assert "不是 MP3" in str(exc.value)
    assert not (tmp_path / "bad.mp3").exists(), "坏产物不得落成 .mp3"
    assert not list(tmp_path.glob("*.tmp")), "临时文件必须清理"


def test_synthesize_maps_network_error_to_readable(fake_tts, tmp_path: Path) -> None:
    fake_tts.fails = True
    with pytest.raises(tts_service.TTSSynthesisError) as exc:
        tts_service.synthesize("正文。", out_path=tmp_path / "n.mp3")
    assert "语音合成失败" in str(exc.value)
    assert not list(tmp_path.glob("*.tmp"))


def test_synthesize_rejects_bad_voice_and_rate_before_network(fake_tts, tmp_path: Path) -> None:
    """白名单/形态校验在 Communicate 之前——越界零实例化（也不出网）。"""
    with pytest.raises(ValueError):
        tts_service.synthesize("正文。", "xx-XX-Nope", out_path=tmp_path / "v.mp3")
    with pytest.raises(ValueError):
        tts_service.synthesize("正文。", None, "500%", out_path=tmp_path / "r.mp3")
    assert fake_tts.instance_count == 0


def test_edge_tts_single_egress() -> None:
    """**出口单一性锚**：`import edge_tts` 只出现在 services/tts_service（唯一合成点）。

    本仓的 AI 出网有 gateway 唯一咽喉；TTS 是**另一条出口**（Microsoft TTS
    端点，非 LLM），它同样必须只有一个：任何别处起第二个 Communicate 就等于
    绕过了 service 的参数校验/原子落盘/魔数复核。domain（core/tts）尤其必须
    零依赖——它只是白名单与脚本抽取的纯计算面。
    """
    pkg = Path(__file__).resolve().parent.parent / "musespiral"
    hits: list[str] = []
    for path in pkg.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import edge_tts") or stripped.startswith("from edge_tts"):
                hits.append(path.relative_to(pkg).as_posix())
    assert hits == ["services/tts_service.py"], f"edge_tts 导入点不唯一: {hits}"
    # 域层纯计算面：core/tts 零 edge_tts / 零网络库
    for path in (pkg / "core" / "tts").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for bad in ("edge_tts", "import requests", "urllib.request", "aiohttp"):
            assert bad not in text, f"core/tts/{path.name} 违反纯层纪律: {bad}"


def test_mp3_magic_table_is_single_sourced() -> None:
    """魔数表单一持有：services 落盘前与 studio 回放前共用 core/tts 同一张表。"""
    from musespiral.core.tts import MP3_MAGIC

    assert looks_like_mp3(MP3_BODY) is True
    assert looks_like_mp3(b"<html>") is False
    assert looks_like_mp3(b"") is False
    assert MP3_MAGIC is not None
    # 两处都不再各自定义魔数元组（此前各一份，属判例式漂移源）
    pkg = Path(__file__).resolve().parent.parent / "musespiral"
    for rel in ("services/tts_service.py", "studio/tts_actions.py"):
        text = (pkg / rel).read_text(encoding="utf-8")
        assert 'b"\\xff\\xfb"' not in text, f"{rel} 又自建了魔数表（应 import core.tts）"


def test_missing_edge_tts_gives_readable_guidance(monkeypatch, tmp_path: Path) -> None:
    """依赖缺席（源码目录未 pip install）→ TTSUnavailableError 可读引导，不炸包。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("No module named 'edge_tts'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(tts_service.TTSUnavailableError) as exc:
        tts_service.synthesize("正文。", out_path=tmp_path / "x.mp3")
    assert "pip install edge-tts" in str(exc.value)


# ══ d) 朗读脚本抽取 ═════════════════════════════════════════════════


def test_speech_text_skips_code_table_image_and_strips_inline_markup() -> None:
    from musespiral.core.content.parser import parse_article
    from musespiral.core.tts.script import speech_text, strip_markup

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.md"
        p.write_text(
            "---\ntitle: 标题\n---\n\n段落含 <strong>加粗</strong> 与 &amp; 符号。\n\n"
            "- 列表甲\n- 列表乙\n\n> 引用一句\n\n```\ncode_here()\n```\n\n"
            "| 列A | 列B |\n|---|---|\n| 1 | 2 |\n\n![图](a.png)\n",
            encoding="utf-8",
        )
        text = speech_text(parse_article(p))
    assert text.splitlines() == [
        "标题", "段落含 加粗 与 & 符号。", "列表甲", "列表乙", "引用一句",
    ]
    assert strip_markup("") == ""
    assert strip_markup("&amp;amp; 套娃") == "& 套娃"


def test_speech_text_empty_when_only_unspoken_blocks() -> None:
    from musespiral.core.content.parser import ParsedArticle
    from musespiral.core.tts.script import speech_text

    art = ParsedArticle(metadata={}, blocks=[
        {"type": "code", "lang": "py", "code": "x=1"},
        {"type": "table", "html": "<table></table>"},
        {"type": "hr"},
    ])
    assert speech_text(art) == ""


# ══ e) 端点层（真实回环服务器，全 mock 合成）════════════════════════


def test_contract_locks_include_tts_paths() -> None:
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    assert "/api/action/tts_generate" in _ACTION_ROUTES
    assert "/api/action/tts_generate" in CONTRACT_POST_EXACT
    assert "/api/tts/file" in _GET_ROUTES
    assert "/api/tts/file" in CONTRACT_GET_EXACT


def test_api_tts_generate_job_path(server, root: Path, fake_tts) -> None:
    """金路径：job done + result{path,voice,rate,bytes,chars} + 产物落盘。"""
    _article(root)
    code, payload, _ = _request(server, "POST", "/api/action/tts_generate", {
        "confirm": True, "article": "post.md",
        "voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%",
    })
    assert code == 200 and payload["ok"] is True
    job = _wait_job(server, payload["job_id"])
    assert job["status"] == "done", job
    assert job["kind"] == "tts_generate"
    result = job["result"]
    assert result["path"] == "post.mp3"
    assert result["voice"] == "zh-CN-XiaoxiaoNeural" and result["rate"] == "+10%"
    assert result["bytes"] == len(MP3_BODY)
    assert (root / "post.mp3").is_file()
    # job 参数留痕（任务面板可读）
    assert job["params"]["article"] == "post.md"


def test_api_tts_generate_confirm_gate_and_shape_400s(server, root: Path, fake_tts) -> None:
    _article(root)
    # confirm 缺失
    code, err, _ = _request(server, "POST", "/api/action/tts_generate", {"article": "post.md"})
    assert code == 400 and "confirm:true" in err["error"]
    # article 缺失
    code, err, _ = _request(server, "POST", "/api/action/tts_generate", {"confirm": True})
    assert code == 400 and "article" in err["error"]
    # article 越界
    code, err, _ = _request(server, "POST", "/api/action/tts_generate",
                            {"confirm": True, "article": "../etc/passwd.md"})
    assert code == 400
    # 文章不存在 → 404（_resolve_file）
    code, err, _ = _request(server, "POST", "/api/action/tts_generate",
                            {"confirm": True, "article": "nope.md"})
    assert code == 404
    # voice 白名单外 → 400（零 job）
    code, err, _ = _request(server, "POST", "/api/action/tts_generate",
                            {"confirm": True, "article": "post.md", "voice": "en-US-EmmaNeural"})
    assert code == 400 and "白名单" in err["error"]
    # rate 非法 → 400
    code, err, _ = _request(server, "POST", "/api/action/tts_generate",
                            {"confirm": True, "article": "post.md", "rate": "fast"})
    assert code == 400 and "语速" in err["error"]
    # 全部形状失败都不得建 job
    code, jobs, _ = _request(server, "GET", "/api/jobs")
    assert code == 200 and jobs["jobs"] == []
    assert fake_tts.instance_count == 0, "形状校验必须先于合成"


def test_api_tts_generate_synthesis_failure_lands_in_job_error(server, root: Path, fake_tts) -> None:
    """远端不可达 → job failed + error 可读（不是 500，也不是静默 done）。"""
    fake_tts.fails = True
    _article(root)
    code, payload, _ = _request(server, "POST", "/api/action/tts_generate",
                                {"confirm": True, "article": "post.md"})
    assert code == 200 and payload["ok"] is True
    job = _wait_job(server, payload["job_id"])
    assert job["status"] == "failed"
    assert "语音合成失败" in job["error"]


def test_api_tts_file_serves_mp3_and_enforces_gates(server, root: Path) -> None:
    """GET /api/tts/file：金路径 200 audio/mpeg；缺 id 400 / 非法 404 / 非 mp3 404。"""
    (root / "post.mp3").write_bytes(MP3_BODY)
    (root / "post.md").write_text("---\ntitle: t\n---\n正文。\n", encoding="utf-8")
    (root / "fake.mp3").write_bytes(b"not an mp3 at all")

    code, body, ctype = _request(server, "GET", "/api/tts/file?id=post.mp3")
    assert code == 200 and body == MP3_BODY
    assert ctype == "audio/mpeg"

    code, err, _ = _request(server, "GET", "/api/tts/file")
    assert code == 400 and "id" in err["error"]

    code, _, _ = _request(server, "GET", "/api/tts/file?id=../post.mp3")
    assert code == 400  # 穿越

    code, _, _ = _request(server, "GET", "/api/tts/file?id=missing.mp3")
    assert code == 404  # 不存在

    code, err, _ = _request(server, "GET", "/api/tts/file?id=post.md")
    assert code == 404 and "仅 .mp3" in err["error"]  # 后缀门

    code, err, _ = _request(server, "GET", "/api/tts/file?id=fake.mp3")
    assert code == 404 and "不是 MP3" in err["error"]  # 魔数门


# ══ f) KINDS 扩键 ═══════════════════════════════════════════════════


def test_jobs_kinds_include_tts_generate() -> None:
    """tts_generate 入白名单（17→18），存量项只增不减（18→19：PL-2 插件
    流水线批 +plugin_pipeline，具名锚在 tests/test_pipeline_plugins.py）。"""
    from musespiral.jobs.store import KINDS

    assert "tts_generate" in KINDS
    assert len(KINDS) == 19
    for kind in (
        "adapt", "pack", "publish", "batch_adapt", "status", "annotate_rewrite",
        "fetch", "sync_stats", "compose_generate", "ingest", "clip",
        "visual_export", "agent_run", "topic_radar", "pipeline",
        "book_search", "book_fetch", "plugin_pipeline",
    ):
        assert kind in KINDS


# ══ g) CLI ═══════════════════════════════════════════════════════════


def test_cli_tts_writes_mp3(fake_tts, root: Path) -> None:
    article = _article(root)
    result = CliRunner().invoke(
        cli, ["tts", str(article), "--voice", "zh-CN-YunjianNeural", "--rate", "+10%"],
        prog_name="musespiral",
    )
    assert result.exit_code == 0, result.output
    assert (root / "post.mp3").is_file()
    assert "朗读已生成" in result.output
    assert fake_tts.calls[0]["voice"] == "zh-CN-YunjianNeural"
    assert fake_tts.calls[0]["rate"] == "+10%"


def test_cli_tts_rejects_voice_outside_whitelist(fake_tts, root: Path) -> None:
    """click.Choice 在参数层拒（exit 2），零合成、零产物。"""
    article = _article(root)
    result = CliRunner().invoke(cli, ["tts", str(article), "--voice", "en-US-EmmaNeural"],
                                prog_name="musespiral")
    assert result.exit_code == 2
    assert not (root / "post.mp3").exists()
    assert fake_tts.instance_count == 0


def test_cli_tts_failure_exits_one(fake_tts, root: Path) -> None:
    fake_tts.fails = True
    article = _article(root)
    result = CliRunner().invoke(cli, ["tts", str(article)], prog_name="musespiral")
    assert result.exit_code == 1
    assert "语音合成失败" in result.output


# ══ h) 前端：node 纯函数 + 结构锚 + 回环 ═════════════════════════════


def _run_node(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环仍随全量跑）")
    src = TTS_JS.read_text(encoding="utf-8")
    pure = src[src.index("/*__TTS_BEGIN__*/"):src.index("/*__TTS_END__*/")]
    script = ("'use strict';\n" + pure +
              "\nmodule.exports={ttsPath:ttsPath,ttsUrl:ttsUrl,ttsSummary:ttsSummary,"
              "ttsBtnState:ttsBtnState};\n" + frag)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "tts_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_tts_path_extraction_only_in_article_view() -> None:
    out = _run_node(r"""
console.log(JSON.stringify({
a:ttsPath('#article?path=posts%2Fa.md&tab=edit'),
b:ttsPath('#article?tab=edit&path=posts%2Fa.md'),
c:ttsPath('#publish?path=posts%2Fa.md'),
d:ttsPath('#article'),
e:ttsPath(''),
f:ttsPath('#article?path=bad%ZZ')
}));
""")
    assert out["a"] == "posts/a.md" and out["b"] == "posts/a.md"
    assert out["c"] == "" and out["d"] == "" and out["e"] == ""
    assert out["f"] == "", "非法百分号编码不得抛（catch 后回落空串）"


def test_tts_url_and_summary_and_btn_state() -> None:
    out = _run_node(r"""
console.log(JSON.stringify({
u1:ttsUrl('posts/a b.md'),
u2:ttsUrl(''),
s1:ttsSummary({path:'posts/a.mp3',bytes:2048,chars:120}),
s2:ttsSummary({}),
b1:ttsBtnState(true),b2:ttsBtnState(false)
}));
""")
    assert out["u1"] == "/api/tts/file?id=posts%2Fa%20b.md"
    assert out["u2"] == "/api/tts/file?id="
    assert out["s1"] == "a.mp3 · 120 字 · 2 KB"
    assert out["s2"] == ""
    assert out["b1"] == "⏳ 合成中…" and out["b2"] == "🔊 朗读"


def test_tts_frontend_structure_and_loader() -> None:
    """结构锚：详情工具条按钮 + 播放栏 + audio；装载钩子；实现不内联 core.js。"""
    shell = TMPL.read_text(encoding="utf-8")
    built = SHELL.read_text(encoding="utf-8")
    js = TTS_JS.read_text(encoding="utf-8")
    ui = UI_JS.read_text(encoding="utf-8")
    core = CORE_JS.read_text(encoding="utf-8")

    for token in ('id="tts-btn"', 'id="tts-bar"', 'id="tts-audio"', 'id="tts-note"',
                  "🔊 朗读", "<audio"):
        assert token in shell, token
    # 按钮在详情工具条内（detail-head 之后、tabs 之前）
    head = built.index('class="detail-head"')
    tabs = built.index('id="detail-tabs"')
    assert head < built.index('id="tts-btn"') < tabs
    # 端点 + confirm
    assert "/api/action/tts_generate" in js and "confirm:true" in js
    assert "/api/tts/file?id=" in js
    # 独立产物懒加载，实现不塞 core.js
    assert "assets/tts.js" in ui and "assets/tts.js" in core
    assert "ttsPath" not in core, "朗读实现不得内联进 core.js（M3 200KiB 拆分线）"
    # 任务面板认识新 kind
    assert "tts_generate:'朗读合成'" in core
    # 零外联
    for bad in ("http://", "https://", "//cdn", "unpkg", "jsdelivr"):
        assert bad not in js, f"tts.js 出现外联字样: {bad}"


def test_tts_asset_served(tmp_path: Path) -> None:
    """/assets/tts.js：200 + application/javascript + 与源逐字节一致。"""
    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/assets/tts.js")
        resp = conn.getresponse()
        body = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        assert resp.status == 200
        assert ctype.startswith("application/javascript"), ctype
        assert body == TTS_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
