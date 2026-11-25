"""R3 书源接驳 + R3.5 提炼管线测试（2026-11-18）。

覆盖（对应设计正典 §六验收 + 本批任务书）：

  1. 引擎 mock 四端点（infra/bookengine.py）——形状/探活/mock 开关；
  2. plans_builtin.build_book_digest 步级形态 + interp RunResult 逐章断言；
  3. AI 失败降级章（AIError 注入 → 原始文本入库 + distilled=false）；
  4. 章节缓存重跑 cache✓（同章二次跑零网络/零出账）；
  5. GET /api/book/original 路径越界拒 + .txt 白名单 + 50k 折叠；
  6. preview 零落盘（from-chapters 提交不写 materials/book_cache）；
  7. canary 零回显（api_key 不进错误文本/落库行）；
  8. 契约锁（GET+3/POST+2）+ confirm 闸门 + 引擎缺席灰灯。

测试全程零真实网络：引擎走 `MUSESPIRAL_ENGINE_MOCK=1` 或注入假 client；
AI 走 chat_fn 脚本化假件（gateway 的真实网络在 chat_fn 注入时被替换）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.agent import plans_builtin
from musespiral.core.agent.plan import validate_plan
from musespiral.core.booksource.clean import clean_chapter
from musespiral.infra import bookengine
from musespiral.infra.ai import AIError
from musespiral.infra.ai.profiles import TASKS
from musespiral.jobs.runner import JobRunner
from musespiral.jobs.store import KINDS, JobStore
from musespiral.services import book_fetch_service as bfs
from musespiral.services import materials_service as ms

CANARY_KEY = "sk-canary-DO-NOT-LEAK-0123456789"


# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.delenv("MUSESPIRAL_ENGINE_MOCK", raising=False)
    yield home


@pytest.fixture
def mock_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """引擎 mock 全开（四端点确定性假件，零网络）。"""
    monkeypatch.setenv("MUSESPIRAL_ENGINE_MOCK", "1")


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """AI 档案在位（ai.json 写入带 canary key——零回显断言用）。"""
    ( _isolate_home / "ai.json").write_text(
        json.dumps(
            {
                "default": "local",
                "profiles": {
                    "local": {
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key": CANARY_KEY,
                        "model": "canary-model",
                    }
                },
                "routing": {},
            }
        ),
        encoding="utf-8",
    )
    return _isolate_home


def _chapters(n: int = 3, *, start: int = 1) -> list[dict]:
    return [
        {"url": f"https://mock.example/book/1/c{i}", "index": i, "title": f"第 {i} 章 · mock 主题 {i}"}
        for i in range(start, start + n)
    ]


BOOK = {"name": "mock 书", "author": "mock 作者"}
SOURCE = "https://mock.example/source"
BOOK_URL = "https://mock.example/book/1"


def _scripted_chat(card_for: dict[int, str] | None = None, *, fail_indexes: set[int] | None = None):
    """脚本化 chat_fn：按 prompt 里的「第 N 章」返回提炼卡；fail_indexes 抛 AIError。

    综述步（prompt 含「全书主线」指令）返回固定综述稿。
    """
    fails = fail_indexes or set()

    def _chat(messages, *, task, temperature=0.6, timeout=120, profile=None):
        prompt = messages[0]["content"] if messages else ""
        if task == "book_digest":
            return "## 全书主线\nmock 主线\n## 章节脉络\n见各章\n## 创作选题建议\n选题 A"
        idx = None
        marker = "【章节】第 "
        pos = prompt.find(marker)
        if pos >= 0:
            tail = prompt[pos + len(marker):]
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            idx = int(digits) if digits else None
        if idx in fails:
            raise AIError(f"AI 调用失败（第 {idx} 章）")
        body = (card_for or {}).get(idx, f"## 定位\n第 {idx} 章定位\n## 要点\n- 要点 {idx}\n## 引用\n> 原句 {idx}\n## 选题\n- 选题 {idx}")
        return body

    return _chat


# ── 1. 引擎 mock 四端点 ──────────────────────────────────────────


def test_engine_mock_four_endpoints(mock_engine) -> None:
    cli = bookengine.BookEngineClient()
    assert cli.available() is True
    health = cli.health()
    assert health["ok"] is True and health.get("mock") is True

    out = cli.search("架构")
    assert out["ok"] is True and out["items"][0]["name"] == "mock 书·架构"

    info = cli.book_info(SOURCE, BOOK_URL)
    assert info["info"]["name"] == "mock 书"

    toc = cli.toc(SOURCE, BOOK_URL)
    assert len(toc["chapters"]) == 12 and toc["total"] == 12

    content = cli.content(SOURCE, BOOK_URL, "https://mock.example/book/1/c3")
    assert "第 3 章" in content["content"]
    # mock 正文带广告行/乱码行 —— 清洗链的确定性夹具
    cleaned = clean_chapter(content["content"])
    assert "天才一秒记住" not in cleaned
    assert "\ufffd" not in cleaned


def test_engine_absent_raises_unavailable(monkeypatch) -> None:
    """无 mock 且回环不可达 → BookEngineUnavailable（灰灯语义）。"""
    monkeypatch.setattr(
        bookengine, "_request_json",
        lambda *a, **k: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("未就绪")),
    )
    cli = bookengine.BookEngineClient()
    assert cli.available() is False
    assert cli.health()["absent"] is True
    with pytest.raises(bookengine.BookEngineUnavailable):
        cli.search("x")


def test_engine_shape_mismatch_degrades(monkeypatch) -> None:
    """形状不符（items 非 list）→ BookEngineUnavailable（集成故障不静默）。"""
    monkeypatch.setattr(bookengine, "_request_json", lambda *a, **k: {"ok": True, "items": "bad"})
    with pytest.raises(bookengine.BookEngineUnavailable, match="响应形状不符"):
        bookengine.BookEngineClient().search("x")


def test_engine_base_url_pinned() -> None:
    """回环钉死：base_url 是类属性常量，无构造参数、不读 env。"""
    assert bookengine.BookEngineClient.base_url == "http://127.0.0.1:8792"
    assert bookengine.BASE_URL == "http://127.0.0.1:8792"


# ── 2. build_book_digest 形态 + interp 步级 ──────────────────────


def test_build_book_digest_shape() -> None:
    p = plans_builtin.build_book_digest([{"url": f"u{i}", "title": f"t{i}"} for i in range(1, 6)])
    assert p["plan"] == plans_builtin.PLAN_BOOK_DIGEST
    # 5 章 ≥ 门槛 → 5 digest + 1 synthesis
    assert [s["id"] for s in p["steps"]] == [
        "digest_1", "digest_2", "digest_3", "digest_4", "digest_5", "book_synthesis",
    ]
    for s in p["steps"][:5]:
        assert s["task"] == "chapter_digest"
        assert s["cache"] is True
        assert s["max_out"] == plans_builtin.CHAPTER_DIGEST_MAX_OUT
        assert s["inputs"]["chapter"].startswith("{{materials[")
    synth = p["steps"][-1]
    assert synth["task"] == "book_digest" and synth["cache"] is False
    assert set(synth["carry"]) == {f"digest_{i}" for i in range(1, 6)}
    assert p["budget"]["steps"] == 6
    validate_plan(p, tasks=TASKS)


def test_build_book_digest_no_synthesis_under_five() -> None:
    p = plans_builtin.build_book_digest([{"url": "u", "title": "t"}] * 1)
    assert len(p["steps"]) == 1 and p["steps"][0]["id"] == "digest_1"
    p4 = plans_builtin.build_book_digest([{"url": f"u{i}", "title": f"t{i}"} for i in range(4)])
    assert all(s["task"] == "chapter_digest" for s in p4["steps"])
    # 显式 synthesis=True 可强制（服务层补综述路径）
    p_forced = plans_builtin.build_book_digest(
        [{"url": f"u{i}", "title": f"t{i}"} for i in range(2)], synthesis=True
    )
    assert p_forced["steps"][-1]["id"] == "book_synthesis"


def test_build_book_digest_bounds() -> None:
    with pytest.raises(ValueError, match="至少 1 个章节"):
        plans_builtin.build_book_digest([])
    with pytest.raises(ValueError, match="1-15 章"):
        plans_builtin.build_book_digest([{"url": f"u{i}", "title": f"t{i}"} for i in range(16)])
    with pytest.raises(ValueError, match="必须是非空字符串"):
        plans_builtin.build_book_digest([{"url": "", "title": "t"}])


def test_book_digest_templates_declare_structure() -> None:
    """提炼卡四节与综述三节必须在模板里逐字写出（设计 §六验收 1）。"""
    for section in ("## 定位", "## 要点", "## 引用", "## 选题"):
        assert section in plans_builtin.CHAPTER_DIGEST_INSTRUCTION
    for section in ("## 全书主线", "## 章节脉络", "## 创作选题建议"):
        assert section in plans_builtin.BOOK_SYNTHESIS_INSTRUCTION


def test_interp_run_result_step_level(mock_engine, ai_on) -> None:
    """plan 步级 RunResult：逐章 digest 槽 + 综述槽，cache_hit 逐轮可见。"""
    chapters = [
        {"index": i, "title": f"第 {i} 章", "url": f"u{i}", "cleaned": f"正文 {i} 内容" * 20}
        for i in range(1, 6)
    ]
    result = bfs._run_digest_pass(chapters, job_id="j1", chat_fn=_scripted_chat(), synthesis=True)
    assert result["status"] == "done"
    assert len(result["steps"]) == 6  # 5 digest + 1 综述
    assert [s["id"] for s in result["steps"][:5]] == [f"digest_{i}" for i in range(1, 6)]
    assert result["steps"][-1]["id"] == "book_synthesis"
    assert all(s["status"] == "done" for s in result["steps"])
    assert result["slots"]["digest_3"].startswith("## 定位")
    assert "全书主线" in result["slots"]["synthesis"]


# ── 3. 降级章（AI 失败 → 原始文本 + distilled=false）─────────────


def test_run_fetch_degrades_failed_chapter(mock_engine, ai_on) -> None:
    out = bfs.run_fetch(
        SOURCE, BOOK_URL, BOOK, _chapters(3),
        chat_fn=_scripted_chat(fail_indexes={2}),
    )
    assert out["status"] == "done"
    assert out["distilled_n"] == 2 and out["degraded_n"] == 1
    rows = {m["payload"]["meta"]["book"]["chapter_index"]: m for m in ms.list_materials() if not m.get("deleted")}
    assert len(rows) == 3
    # 降级章：原始清洗文本 + distilled=false + license UNKNOWN + 无 ai_generated
    degraded = rows[2]
    assert degraded["payload"]["meta"]["book"]["distilled"] is False
    assert degraded["license"] == "UNKNOWN"
    assert "ai_generated" not in degraded["payload"]["meta"]
    assert "天才一秒记住" not in degraded["payload"]["text"]  # 清洗生效
    # 提炼章：提炼卡 + distilled=true + AI-GENERATED
    distilled = rows[1]
    assert distilled["payload"]["meta"]["book"]["distilled"] is True
    assert distilled["license"] == "AI-GENERATED"
    assert distilled["payload"]["meta"]["ai_generated"] is True
    assert distilled["payload"]["text"].startswith("## 定位")
    # 元数据全字段 + original_ref + enabled=false + chain book_fetch
    for row in rows.values():
        book_meta = row["payload"]["meta"]["book"]
        assert book_meta["name"] == "mock 书" and book_meta["author"] == "mock 作者"
        assert book_meta["original_chars"] > 0
        assert row["payload"]["meta"]["original_ref"]["path"].endswith(".txt")
        assert row["triage"]["enabled"] is False
        assert row["provenance"]["chain"] == [{"from_id": SOURCE, "op": "book_fetch"}]


def test_run_fetch_no_ai_degrades_all(mock_engine) -> None:
    """AI 未配置（无 ai.json）→ 全章原始文本入库 + note 说明（金路径不断）。"""
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=_scripted_chat())
    assert out["status"] == "done" and out["raw_n"] == 2 and out["distilled_n"] == 0
    assert "AI 未配置" in out.get("note", "")
    for row in ms.list_materials():
        assert row["payload"]["meta"]["book"]["distilled"] is False


def test_run_fetch_digest_off(mock_engine, ai_on) -> None:
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), digest=False, chat_fn=_scripted_chat())
    assert out["raw_n"] == 2 and out["distilled_n"] == 0
    assert "未开启 AI 提炼" in out.get("note", "")


def test_run_fetch_synthesis_material(mock_engine, ai_on) -> None:
    """≥5 章 → 综述素材一条（uri .../synthesis + meta.book.synthesis）。"""
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(5), chat_fn=_scripted_chat())
    assert out["synthesis"] is not None and out["synthesis"]["chars"] > 0
    synth = next(
        m for m in ms.list_materials()
        if m["origin"]["uri"] == f"book://{bfs._book_hash(SOURCE, BOOK_URL)}/synthesis"
    )
    assert synth["payload"]["meta"]["book"]["synthesis"] is True
    assert synth["license"] == "AI-GENERATED"


def test_run_fetch_partial_success_lists_failures(monkeypatch, mock_engine, ai_on) -> None:
    """取文失败章明列在 result（部分成功是合法终态——不静默丢）。"""
    cli = bookengine.BookEngineClient()
    original = cli.content

    def _content(source_url, book_url, chapter_url):
        if chapter_url.endswith("c2"):
            raise bookengine.BookEngineUnavailable("模拟取文失败")
        return original(source_url, book_url, chapter_url)

    monkeypatch.setattr(cli, "content", _content)
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(3), client=cli, chat_fn=_scripted_chat())
    assert out["status"] == "done" and out["fetched_n"] == 2
    assert [r["index"] for r in out["fetch_failed"]] == [2]


def test_run_fetch_all_fail_is_failed(mock_engine, ai_on, monkeypatch) -> None:
    cli = bookengine.BookEngineClient()
    monkeypatch.setattr(
        cli, "content",
        lambda *a: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("全挂")),
    )
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), client=cli, chat_fn=_scripted_chat())
    assert out["status"] == "failed" and out["fetched_n"] == 0


# ── 4. 章节缓存 + cache✓ 重跑 ────────────────────────────────────


def test_chapter_cache_written_and_rerun_cache_hit(mock_engine, ai_on) -> None:
    """章节缓存落盘（book_cache/<hash>/<idx>-<title>.txt）+ 同章重跑 cache✓。"""
    out1 = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=_scripted_chat())
    cache = bfs.cache_root() / bfs._book_hash(SOURCE, BOOK_URL)
    files = sorted(cache.glob("*.txt"))
    assert len(files) == 2
    assert files[0].read_text(encoding="utf-8").strip()  # 清洗后原文非空
    assert out1["distilled_n"] == 2

    # 重跑同章：digest 步 cache=true 全命中（gateway 缓存层在新 home 下为空 →
    # 这里断言的是「同进程二次调用命中」——用第二次结果的 cache✓ 面）
    out2 = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=_scripted_chat())
    # 素材幂等：同 uri 同内容 → 零追加（既有的 added 语义；内容相同不冲突）
    assert out2["fetched_n"] == 2
    # 缓存命中证据：直接跑 plan 两遍，第二遍 steps_meta.cache_hit 全 True
    chapters = [
        {"index": i, "title": f"第 {i} 章", "url": f"u{i}", "cleaned": f"正文 {i}"}
        for i in range(1, 3)
    ]
    bfs._run_digest_pass(chapters, job_id="c1", chat_fn=_scripted_chat())
    second = bfs._run_digest_pass(chapters, job_id="c1", chat_fn=_scripted_chat())
    assert all(s["cache_hit"] is True for s in second["steps"]), second["steps"]


# ── 5. GET /api/book/original 三道门 ────────────────────────────


def test_resolve_original_ref_boundaries(_isolate_home: Path) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        bfs.resolve_original_ref("")
    with pytest.raises(ValueError, match="禁止绝对路径"):
        bfs.resolve_original_ref("/etc/passwd")
    with pytest.raises(ValueError, match="越界"):
        bfs.resolve_original_ref("../../../../etc/passwd.txt")
    with pytest.raises(ValueError, match="越界"):
        bfs.resolve_original_ref("a/../../../../etc/x.txt")
    with pytest.raises(ValueError, match=r"\.txt"):
        bfs.resolve_original_ref("abc/1-x.md")


# ── studio 夹具（真实回环，同 test_booksource_studio 形态）────────


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
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
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


def _poll_job(server, job_id: str, *, tries: int = 80) -> dict:
    import time

    for _ in range(tries):
        code, job = _request(server, "GET", f"/api/jobs/{job_id}")
        assert code == 200
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 未在预期时间内终态")


def test_original_endpoint_folds_large_text(server) -> None:
    """>50k 折叠为前 20k + folded:true；full=1 全文。"""
    root = bfs.cache_root() / "abc123"
    root.mkdir(parents=True)
    big = "x" * 60_000
    (root / "1-大章.txt").write_text(big, encoding="utf-8")
    code, out = _request(server, "GET", "/api/book/original?ref=abc123/1-%E5%A4%A7%E7%AB%A0.txt")
    assert code == 200 and out["folded"] is True
    assert out["chars"] == 60_000 and len(out["text"]) == 20_000
    code, out = _request(server, "GET", "/api/book/original?ref=abc123/1-%E5%A4%A7%E7%AB%A0.txt&full=1")
    assert code == 200 and out["folded"] is False and len(out["text"]) == 60_000


def test_original_endpoint_rejects_traversal_and_non_txt(server) -> None:
    code, out = _request(server, "GET", "/api/book/original?ref=../../etc/passwd.txt")
    assert code == 400 and out["ok"] is False
    code, out = _request(server, "GET", "/api/book/original?ref=/etc/passwd.txt")
    assert code == 400
    code, out = _request(server, "GET", "/api/book/original?ref=abc123/1-x.md")
    assert code == 400
    code, out = _request(server, "GET", "/api/book/original?ref=abc123/nope.txt")
    assert code == 404
    code, out = _request(server, "GET", "/api/book/original")
    assert code == 400 and "ref" in out["error"]


# ── 6. preview 零落盘（from-chapters 提交不写任何字节）───────────


def test_from_chapters_submit_writes_zero_bytes(
    server, mock_engine, ai_on, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 确定性门：job_fn 换成阻塞版——runner.submit 入队即起 worker 线程
    # （jobs/runner.py submit→pool.submit），mock 引擎下 job 可在断言窗口内
    # 跑完并写 materials/book_cache（负载相关竞态：2026-11-22 全量套件
    # 并发时段 1/2 复现红，隔离复跑全绿）。被测纪律是「提交期零落盘」
    # （book_actions._action_from_chapters 的 preview 零落盘），门住执行期
    # 后断言不再依赖线程调度；finally 释放防 executor atexit join 挂进程。
    gate = threading.Event()

    def _gated_job(job_id: str) -> dict:
        gate.wait()
        return {}

    monkeypatch.setattr(bfs, "job_fn", lambda *a, **k: _gated_job)
    try:
        before = {p: p.read_bytes() for p in _isolate_home.rglob("*") if p.is_file()}
        code, out = _request(
            server, "POST", "/api/materials/from-chapters",
            {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
             "chapters": _chapters(2)},
        )
        assert code == 200 and out["ok"] is True and out["job_id"]
        # 提交期零落盘：home 下文件集与内容零变化（job 运行期才写）
        after_files = {p for p in _isolate_home.rglob("*") if p.is_file()}
        new_files = after_files - set(before)
        assert all("jobs.jsonl" in p.name for p in new_files), f"提交期新增文件: {new_files}"
        for path, content in before.items():
            if path.is_file():
                assert path.read_bytes() == content, f"提交期改写了 {path}"
        assert ms.list_materials() == [] and not bfs.cache_root().exists()
    finally:
        gate.set()


def test_from_chapters_job_end_to_end(server, mock_engine, ai_on) -> None:
    """全链：POST → job book_fetch → done；素材落库 + 素材行 meta 可见。"""
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
         "chapters": _chapters(3), "digest": True},
    )
    assert code == 200 and out["ok"] is True
    job = _poll_job(server, out["job_id"])
    assert job["status"] == "done", job
    assert job["kind"] == "book_fetch"
    result = job["result"]
    assert result["fetched_n"] == 3 and result["materials"]
    # job result 只放计数无正文（正文在 materials/book_cache）
    assert "text" not in json.dumps(result)
    rows = ms.list_materials()
    assert len(rows) == 3
    code, listed = _request(server, "GET", "/api/materials")
    assert code == 200


def test_from_chapters_requires_confirm(server) -> None:
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK, "chapters": _chapters(1)},
    )
    assert code == 400 and "confirm" in out["error"]


def test_from_chapters_bad_shape_is_400(server, mock_engine) -> None:
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK, "chapters": []},
    )
    assert code == 400 and "chapters" in out["error"]
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": {},
         "chapters": _chapters(1)},
    )
    assert code == 400 and "book" in out["error"]


def test_from_chapters_engine_absent_is_ok_false(server) -> None:
    """引擎缺席（无 mock、回环不可达）→ 200 {ok:false}（灰灯语义，不是 400）。"""
    orig = bookengine.BookEngineClient.available
    bookengine.BookEngineClient.available = lambda self: False
    try:
        code, out = _request(
            server, "POST", "/api/materials/from-chapters",
            {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
             "chapters": _chapters(1)},
        )
    finally:
        bookengine.BookEngineClient.available = orig
    assert code == 200 and out["ok"] is False and "bookengine" in out["error"]


# ── 7. book_search job + 端点 ───────────────────────────────────


def test_book_search_job_and_endpoint(server, mock_engine) -> None:
    code, out = _request(server, "POST", "/api/book/search", {"confirm": True, "key": "架构"})
    assert code == 200 and out["ok"] is True
    job = _poll_job(server, out["job_id"])
    assert job["status"] == "done" and job["kind"] == "book_search"
    assert job["result"]["items"][0]["name"] == "mock 书·架构"


def test_book_search_requires_confirm_and_key(server, mock_engine) -> None:
    code, out = _request(server, "POST", "/api/book/search", {"key": "x"})
    assert code == 400 and "confirm" in out["error"]
    code, out = _request(server, "POST", "/api/book/search", {"confirm": True})
    assert code == 400 and "key" in out["error"]


def test_book_search_engine_absent(server) -> None:
    orig = bookengine.BookEngineClient.available
    bookengine.BookEngineClient.available = lambda self: False
    try:
        code, out = _request(server, "POST", "/api/book/search", {"confirm": True, "key": "x"})
    finally:
        bookengine.BookEngineClient.available = orig
    assert code == 200 and out["ok"] is False and "bookengine" in out["error"]


# ── 8. GET info/toc 代理 ────────────────────────────────────────


def test_book_info_and_toc_endpoints(server, mock_engine) -> None:
    code, info = _request(server, "GET", f"/api/book/info?sourceUrl={SOURCE}&bookUrl={BOOK_URL}")
    assert code == 200 and info["status"] == "ok" and info["info"]["name"] == "mock 书"
    code, toc = _request(server, "GET", f"/api/book/toc?sourceUrl={SOURCE}&bookUrl={BOOK_URL}")
    assert code == 200 and toc["status"] == "ok" and toc["total"] == 12
    code, out = _request(server, "GET", "/api/book/info")
    assert code == 400


def test_book_info_absent_is_status_absent(server) -> None:
    orig = bookengine.BookEngineClient.book_info
    def _boom(self, *a):
        raise bookengine.BookEngineUnavailable("未就绪")
    bookengine.BookEngineClient.book_info = _boom
    try:
        code, out = _request(server, "GET", f"/api/book/info?sourceUrl={SOURCE}&bookUrl={BOOK_URL}")
    finally:
        bookengine.BookEngineClient.book_info = orig
    assert code == 200 and out["status"] == "absent"


# ── 9. canary 零回显 ────────────────────────────────────────────


def test_canary_never_leaks(mock_engine, ai_on) -> None:
    """api_key canary 不出现在：错误文本 / 素材行 / job result / 缓存文件名。"""
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=_scripted_chat(fail_indexes={1}))
    assert CANARY_KEY not in json.dumps(out, ensure_ascii=False)
    for row in ms.list_materials():
        assert CANARY_KEY not in json.dumps(row, ensure_ascii=False)
    for path in bfs.cache_root().rglob("*"):
        assert CANARY_KEY not in str(path)


def test_ai_error_message_sanitized_in_fetch(server, mock_engine, ai_on) -> None:
    """AI 失败的错误面不进 job.error（降级吸收）——job 仍 done，result 无密钥。"""
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
         "chapters": _chapters(2)},
    )
    job = _poll_job(server, out["job_id"])
    assert job["status"] == "done"
    assert CANARY_KEY not in json.dumps(job, ensure_ascii=False)


# ── 10. 契约锁 + KINDS + 探活 ───────────────────────────────────


def test_route_surface_locked_to_contract() -> None:
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES

    get_paths = {p for p in _GET_ROUTES if p.startswith("/api/book/")}
    assert get_paths == {"/api/book/info", "/api/book/toc", "/api/book/original",
                         "/api/book/chapter"}  # W3（2026-11-24）：阅读面板读章
    post_paths = {p for p in _ACTION_ROUTES if p.startswith("/api/book/") or p == "/api/materials/from-chapters"}
    assert post_paths == {"/api/book/search", "/api/materials/from-chapters"}


def test_contract_locks_include_new_paths() -> None:
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    assert {"/api/book/info", "/api/book/toc", "/api/book/original"} <= CONTRACT_GET_EXACT
    assert {"/api/book/search", "/api/materials/from-chapters"} <= CONTRACT_POST_EXACT


def test_jobs_kinds_include_book_kinds() -> None:
    # 17→18：G-T5 TTS 朗读批（2026-11-19）新增 tts_generate；18→19：PL-2 插件
    # 流水线批（2026-11-20）新增 plugin_pipeline（计数随扩键同步；该批的具名
    # 锚在 tests/test_pipeline_plugins.py::test_jobs_kinds_include_plugin_pipeline）；
    # 19→20：P1 批（2026-11-23）新增 quality_loop（具名锚在 tests/test_quality_loop.py）
    assert {"book_search", "book_fetch", "plugin_pipeline"} <= KINDS and len(KINDS) == 20


def test_tasks_include_book_digest_keys() -> None:
    assert {"chapter_digest", "book_digest"} <= set(TASKS)
    from musespiral.infra.ai.prompts import prompt_tasks

    assert {"chapter_digest", "book_digest"} <= set(prompt_tasks())


def test_submit_requires_engine_available(mock_engine) -> None:
    """提交期探活：引擎缺席 → BookEngineUnavailable，零 job。"""
    runner = JobRunner()
    try:
        orig = bookengine.BookEngineClient.available
        bookengine.BookEngineClient.available = lambda self: False
        try:
            with pytest.raises(bookengine.BookEngineUnavailable):
                bfs.submit_to_runner(runner, SOURCE, BOOK_URL, BOOK, _chapters(1))
        finally:
            bookengine.BookEngineClient.available = orig
        assert JobStore().list(limit=None) == []
    finally:
        runner.shutdown(wait=True)


def test_submit_bad_book_rejected_zero_job(mock_engine) -> None:
    runner = JobRunner()
    try:
        with pytest.raises(bfs.BookFetchError, match="book.name"):
            bfs.submit_to_runner(runner, SOURCE, BOOK_URL, {"name": ""}, _chapters(1))
        with pytest.raises(bfs.BookFetchError, match="章节数超上限"):
            bfs.submit_to_runner(
                runner, SOURCE, BOOK_URL, BOOK, _chapters(20), digest=True
            )
        assert JobStore().list(limit=None) == []
    finally:
        runner.shutdown(wait=True)


def test_clean_chapter_rules_and_normalization() -> None:
    """清洗函数（设计 §2.1 ①）：去广告行/乱码行/空白归一 + 可配 replaceRegex。"""
    raw = (
        "第 1 章 标题\r\n\r\n正文A，保留。\r\n"
        "天才一秒记住本书首发地址：x.com\r\n"
        "本站最新网址 www.x.com\r\n"
        "乱码\ufffd\ufffd\ufffd\x1b\r\n"
        "\r\n\r\n\r\n"
        "正文B，保留。"
    )
    cleaned = clean_chapter(raw)
    assert cleaned == "第 1 章 标题\n\n正文A，保留。\n\n正文B，保留。"
    # 源级 replaceRegex 简化（(pattern, replacement) 对）
    assert clean_chapter("敏感词在这里", [("敏感词", "[已过滤]")]) == "[已过滤]在这里"
    with pytest.raises(ValueError, match="正则非法"):
        clean_chapter("x", [("([", "y")])
    assert clean_chapter("") == ""


def test_chapter_material_validates_under_model(mock_engine, ai_on) -> None:
    """book_fetch 素材行过 validate_material（豁免条二：text + 单条 book_fetch 链）。"""
    from musespiral.core.materials.model import validate_material

    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=_scripted_chat())
    assert out["materials"]
    for row in ms.list_materials():
        assert validate_material(row) == []


# ── 11. per_task 预算限额（chapter_digest 可限额 → 触顶章降级不丢内容）──


def test_per_task_chapter_digest_budget_parses() -> None:
    """`per_task.chapter_digest.daily_calls` 可设置（扩键即继承 F79 预算面）。"""
    from musespiral.infra.ai import budget as budget_mod

    key, value, state = budget_mod.apply_set("per_task.chapter_digest.daily_calls=9")
    assert key == "per_task.chapter_digest.daily_calls" and value == 9
    assert state["per_task"]["chapter_digest"] == {"daily_calls": 9}
    limits = budget_mod.limits_for("chapter_digest", state)
    assert limits["daily_calls"] == 9


def test_budget_exceeded_degrades_remaining_chapters(mock_engine, ai_on, _isolate_home: Path) -> None:
    """chapter_digest 日预算 1 次 → 第 2 章起触顶 → 剩余章降级原文（不丢内容）。

    链路：gateway budget 拒 → BudgetExceeded（budget_exceeded=True 协议位）→
    interp 立即终态 → 服务层看到「预算触顶」→ 剩余章全降级。
    """
    from musespiral.infra.ai import budget as budget_mod

    budget_mod.apply_set("per_task.chapter_digest.daily_calls=1")
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(3), chat_fn=_scripted_chat())
    assert out["status"] == "done"
    assert out["distilled_n"] == 1 and out["raw_n"] == 2
    rows = {m["payload"]["meta"]["book"]["chapter_index"]: m for m in ms.list_materials()}
    assert len(rows) == 3, "触顶章必须降级入库，不静默丢"
    assert rows[1]["payload"]["meta"]["book"]["distilled"] is True
    assert rows[2]["payload"]["meta"]["book"]["distilled"] is False
    assert rows[3]["payload"]["meta"]["book"]["distilled"] is False


# ── 11. 协作式取消（2026-11-18 集成轮，R3.5 正典 §2.2）+ 可核验性断言 ──


def test_run_fetch_cancel_before_start_lands_nothing(mock_engine) -> None:
    """首章前取消：job done 部分终态（不误报 failed）、零落库、note 明列 M/N。"""
    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(3), should_stop=lambda: True)
    assert out["ok"] is True and out["status"] == "done"
    assert out["cancelled"] is True and out["fetched_n"] == 0
    assert out["materials"] == [] and out["fetch_failed"] == []
    assert "已入库 0 章" in out["note"] and "已放弃" in out["note"]
    assert ms.list_materials() == [] and not bfs.cache_root().exists()


def test_run_fetch_cancel_during_fetch_drops_partial(mock_engine) -> None:
    """取文章界取消：已取 2 章缓存照写（内容不丢），但均放弃不落素材（放弃
    是用户显式决策——与 AI 失败降级为原文是两条路径）。"""
    calls = {"n": 0}

    def stop_after_two() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(3), should_stop=stop_after_two)
    assert out["cancelled"] is True and out["fetched_n"] == 2
    assert out["materials"] == [] and out["distilled_n"] == 0
    assert out["fetch_failed"] == []
    assert "已入库 0 章" in out["note"]
    cached = [p for p in bfs.cache_root().rglob("*.txt")]
    assert len(cached) == 2, "已取章节缓存保留（内容不丢），素材面放弃"
    assert ms.list_materials() == []


def test_run_fetch_cancel_during_digest_keeps_cards_only(mock_engine, ai_on) -> None:
    """提炼 pass 界取消：已完成章（有提炼卡）保留入库；未完成章放弃——
    **不降级为原文**（degraded_n=0），pass 内不中断（plan 原子）。"""
    state = {"pass1_done": False}

    def stop_after_pass1() -> bool:
        return state["pass1_done"]

    def chat(messages, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        if "【章节】第 2 章" in prompt:
            state["pass1_done"] = True  # pass1 在本章失败后结束（interp 步级失败即停）
            raise AIError("AI 调用失败（第 2 章）")
        return _scripted_chat()(messages, **kwargs)

    out = bfs.run_fetch(
        SOURCE, BOOK_URL, BOOK, _chapters(2),
        chat_fn=chat, should_stop=stop_after_pass1,
    )
    assert out["cancelled"] is True
    assert out["distilled_n"] == 1 and out["degraded_n"] == 0  # 取消≠AI 失败：不降级
    assert out["raw_n"] == 0 and out["fetched_n"] == 2
    assert len(out["materials"]) == 1
    assert "已入库 1 章" in out["note"] and "已放弃" in out["note"]
    rows = {m["payload"]["meta"]["book"]["chapter_index"]: m for m in ms.list_materials()}
    assert set(rows) == {1}  # 第 2 章放弃不落库
    assert rows[1]["license"] == "AI-GENERATED"


def test_job_cancel_endpoint_shapes(server, mock_engine) -> None:
    """端点闸门与终态分支：confirm 400 / 缺 job_id 400 / 未知 404；终态 job
    → 200 {ok,cancelled:false} 幂等不误报。"""
    code, out = _request(server, "POST", "/api/jobs/cancel", {"job_id": "abc"})
    assert code == 400 and "confirm" in out["error"]
    code, out = _request(server, "POST", "/api/jobs/cancel", {"confirm": True})
    assert code == 400 and "job_id" in out["error"]
    code, out = _request(server, "POST", "/api/jobs/cancel", {"confirm": True, "job_id": "00000000"})
    assert code == 404
    code, submitted = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
         "chapters": _chapters(2), "digest": False},
    )
    job = _poll_job(server, submitted["job_id"])
    assert job["status"] == "done"
    code, out = _request(server, "POST", "/api/jobs/cancel", {"confirm": True, "job_id": job["id"]})
    assert code == 200 and out["ok"] is True and out["cancelled"] is False


def test_quote_verifiability_card_quote_findable_in_cache(mock_engine, ai_on) -> None:
    """§六.1 可核验性断言（正控）：提炼卡引用段逐字出自章节清洗原文——
    落库后引用段能在 book_cache 原文中逐字找到（引用从 prompt 里的清洗正文
    原句摘取，卡→缓存全文闭环）。"""
    def quoting_chat(messages, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        lines = prompt.splitlines()
        pick = ""
        for i, line in enumerate(lines):
            if line.startswith("【章节】"):
                for follower in lines[i + 1:]:
                    if follower.strip():
                        pick = follower.strip()
                        break
                break
        assert pick, "prompt 应含章节正文行（可核验性正控前提）"
        quote = pick[:24]
        return (
            "## 定位\n定位（引用出自原文）\n## 要点\n- 要点\n"
            f"## 引用\n> {quote}\n## 选题\n- 选题"
        )

    out = bfs.run_fetch(SOURCE, BOOK_URL, BOOK, _chapters(2), chat_fn=quoting_chat)
    assert out["status"] == "done" and out["distilled_n"] == 2
    rows = {m["payload"]["meta"]["book"]["chapter_index"]: m for m in ms.list_materials()}
    for index, row in rows.items():
        body = row["payload"]["text"]
        quote = next(l[2:].strip() for l in body.splitlines() if l.startswith("> "))
        assert quote, "提炼卡应含引用段"
        cache_text = bfs.resolve_original_ref(
            row["payload"]["meta"]["original_ref"]["path"]
        ).read_text(encoding="utf-8")
        assert quote in cache_text, f"第 {index} 章引用段应能在章节缓存原文中逐字找到"
