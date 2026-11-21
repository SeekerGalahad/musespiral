"""书源域集成冒烟 + 交界锁（2026-11-18 集成接线轮）。

R3+R3.5（后端全链）× R4 #books（前端详情态）两批落地的**接缝面**机器锁。
五场景全走 `studio.make_server` 真实 HTTP + `MUSESPIRAL_ENGINE_MOCK=1`（引擎 mock
分离纪律：mock 假件只在 `infra/bookengine`，service/studio/前端不感知开关——
本文件含该纪律的源面守门）：

  1. **mock↔前端灰灯对齐**：mock 开 → `/api/booksource/engine` status=ok
     version=mock（前端绿灯「可搜」数据面）；mock 关+引擎缺席 → absent
     （灰灯卡数据面）+ from-chapters 200 {ok:false}（灰灯提交语义）；
  2. **详情态载入目录**：GET /api/book/info+toc 出 mock 12 章目录；
  3. **from-chapters 提交→job→前端进度双源**：job done 计数 result +
     `GET /api/agent/runs?id=<job_id>` 步级明细（open/finish_attached_run
     挂接 agent_runs/<job_id>——本轮补的接缝；5 章含综述步）；
  4. **原始章节按需加载**：/api/book/original 出缓存全文（mock 广告行已被
     清洗链剔除——清洗链自证）；
  5. **素材行徽数据面**：/api/materials 行带 meta.book 全字段 + license
     AI-GENERATED（提炼卡）/UNKNOWN（降级原文，distilled=false 前端琥珀徽）。

降级优先双锁：AI 未配置 → 全章原文入库 + **无幽灵 run**（404）；提炼异常
路径不炸宿主 job（run 面如实记 failed）。
"""
from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from musespiral.core.agent.plans_builtin import build_book_digest
from musespiral.infra import bookengine
from musespiral.jobs.store import JobStore
from musespiral.services import agent_service
from musespiral.services import book_fetch_service as bfs
from musespiral.services import materials_service as ms

BOOK = {"name": "mock 书", "author": "mock 作者"}
SOURCE = "https://mock.example/source"
BOOK_URL = "https://mock.example/book/1"

_SYNTH_TEXT = "## 全书主线\nmock 主线贯穿五章。\n## 章节脉络\n见各章提炼卡。\n## 创作选题建议\n- 选题 A"


def _card_for(prompt: str) -> str:
    """按 prompt 里的「第 N 章」生成四节提炼卡；引用段**逐字摘自该章清洗正文**
    （§六.1 可核验性——卡→素材→book_cache 原文闭环的端到端正控）。"""
    idx = "?"
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
        idx = digits or "?"
    lines = prompt.splitlines()
    quote = ""
    for i, line in enumerate(lines):
        if line.startswith("【章节】"):
            for follower in lines[i + 1:]:
                if follower.strip():
                    quote = follower.strip()[:24]
                    break
            break
    assert quote, "prompt 应含章节正文行（可核验性正控前提）"
    return (
        f"## 定位\n第 {idx} 章定位\n## 要点\n- 要点 {idx}\n"
        f"## 引用\n> {quote}\n## 选题\n- 选题 {idx}"
    )


class _StubAIHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容最小桩（非流式 JSON 分支——client.chat 双格式解析实 path）。

    类属性供用例编排：`delay`=每请求人工延迟（秒，中飞取消时序窗）；
    `fail_on`=prompt 命中子串时回 500（步级 AI 失败——取消测试的放弃章）。
    引用段**逐字摘自 prompt 里的章节清洗正文**——§六.1 可核验性断言的端到端
    形态（卡→素材正文→book_cache 原文闭环）。"""

    delay = 0.0
    fail_on: str | None = None

    def log_message(self, *args: object) -> None:  # 安静
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 协议名
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        prompt = ""
        messages = body.get("messages")
        if isinstance(messages, list) and messages:
            prompt = str((messages[0] or {}).get("content") or "")
        delay = type(self).delay
        if delay:
            time.sleep(delay)
        if type(self).fail_on and type(self).fail_on in prompt:
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        text = _SYNTH_TEXT if "全书主线" in prompt else _card_for(prompt)
        payload = json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def ai_stub(_isolate_home: Path) -> str:
    """本地 OpenAI 兼容桩 + ai.json 指桩（真实 gateway HTTP 链路，零外网）。"""
    httpd = HTTPServer(("127.0.0.1", 0), _StubAIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "stub",
            "profiles": {"stub": {"base_url": base, "api_key": "", "model": "stub-model"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.delenv("MUSESPIRAL_ENGINE_MOCK", raising=False)
    yield home


@pytest.fixture
def mock_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_ENGINE_MOCK", "1")


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
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


def _request(server, method: str, path: str, body: dict | None = None):
    import http.client

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


def _chapters(n: int) -> list[dict]:
    return [
        {"url": f"https://mock.example/book/1/c{i}", "index": i,
         "title": f"第 {i} 章 · mock 主题 {i}"}
        for i in range(1, n + 1)
    ]


def _poll_job(server, job_id: str, *, tries: int = 200) -> dict:
    for _ in range(tries):
        code, job = _request(server, "GET", f"/api/jobs/{job_id}")
        assert code == 200
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 未在预期时间内终态")


# ── 交界 1：mock 开关 ↔ 前端灰灯数据面 ──────────────────────────


def test_mock_on_lamp_green_and_detail_loads(server, mock_engine) -> None:
    """mock 开 → 引擎灯数据面 ok（绿灯可搜）+ 详情态 info/toc 正常载入。"""
    code, eng = _request(server, "GET", "/api/booksource/engine")
    assert code == 200
    assert eng["ok"] is True and eng["status"] == "ok"  # 前端 bkLamp 的 ok 判据
    assert eng["version"] == "mock" and eng["sources_loaded"] == 1
    q = f"sourceUrl={quote(SOURCE, safe='')}&bookUrl={quote(BOOK_URL, safe='')}"
    code, info = _request(server, "GET", f"/api/book/info?{q}")
    assert code == 200 and info["status"] == "ok"
    assert info["info"]["name"] == "mock 书" and info["info"]["tocUrl"]
    code, toc = _request(server, "GET", f"/api/book/toc?{q}")
    assert code == 200 and toc["status"] == "ok"
    assert toc["total"] == 12 and len(toc["chapters"]) == 12  # 详情态目录 12 章
    assert toc["chapters"][0]["title"].startswith("第 1 章")


def test_mock_off_absent_is_gray_lamp_not_error(server, monkeypatch) -> None:
    """mock 关 + 引擎缺席 → 灯/详情/提交三面全灰灯 200 语义（降级优先不炸）。"""
    monkeypatch.setattr(
        bookengine, "_get",
        lambda _p, *, timeout: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("未就绪")),
    )
    monkeypatch.setattr(
        bookengine, "_request_json",
        lambda *a, **k: (_ for _ in ()).throw(bookengine.BookEngineUnavailable("未就绪")),
    )
    code, eng = _request(server, "GET", "/api/booksource/engine")
    assert code == 200 and eng["status"] == "absent"  # 前端灰灯数据面
    q = f"sourceUrl={quote(SOURCE, safe='')}&bookUrl={quote(BOOK_URL, safe='')}"
    code, info = _request(server, "GET", f"/api/book/info?{q}")
    assert code == 200 and info["status"] == "absent"  # 详情态灰灯卡数据面
    code, toc = _request(server, "GET", f"/api/book/toc?{q}")
    assert code == 200 and toc["status"] == "absent"
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL,
         "book": BOOK, "chapters": _chapters(1)},
    )
    assert code == 200 and out["ok"] is False and "bookengine" in out["error"]


# ── 冒烟主链：提交→job→步级双源→素材行徽→原始章节 ────────────────


def test_smoke_from_chapters_mock_runs_plan(server, mock_engine, ai_stub) -> None:
    """五场景合一：mock 引擎供文 + 本地 AI 桩跑 plan → done；步级 run 双源、
    素材行徽、原始章节按需加载全数据面实证（5 章含综述步）。"""
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
         "chapters": _chapters(5), "digest": True},
    )
    assert code == 200 and out["ok"] is True and out["job_id"]
    job = _poll_job(server, out["job_id"])
    assert job["status"] == "done" and job["kind"] == "book_fetch"
    result = job["result"]
    assert result["fetched_n"] == 5 and result["distilled_n"] == 5 and result["raw_n"] == 0
    assert result["synthesis"] and result["synthesis"]["chars"] > 0

    # 步级双源第二源：agent_runs/<job_id>（本轮接缝——open/finish_attached_run）
    code, run = _request(server, "GET", f"/api/agent/runs?id={out['job_id']}")
    assert code == 200 and run["ok"] is True
    run = run["run"]
    assert run["plan"] == "book_digest" and run["status"] == "done"
    ids = [s["id"] for s in run["steps"]]
    assert ids == [f"digest_{i}" for i in range(1, 6)] + ["book_synthesis"]
    assert all(s["status"] == "done" and s["chars_out"] > 0 for s in run["steps"])
    assert run["steps"][0]["out_text"], "steps/<n>.json 正文应并入读面（read_run 合并）"

    # 素材行徽数据面（前端 matRow 的 bk=meta.book 渲染源）
    code, listed = _request(server, "GET", "/api/materials")
    assert code == 200 and listed["total"] == 6  # 5 章 + 1 综述
    rows = {r["payload"]["meta"].get("book", {}).get("chapter_index"): r for r in listed["items"]
            if isinstance(r["payload"].get("meta"), dict) and r["payload"]["meta"].get("book")}
    assert len(rows) == 6
    chapter_row = rows[1]
    bk = chapter_row["payload"]["meta"]["book"]
    assert bk["name"] == "mock 书" and bk["author"] == "mock 作者"
    assert bk["distilled"] is True and bk["chapter_title"].startswith("第 1 章")
    assert bk["source_name"] == ""  # mock 源未导入书源库——展示位宽容空串
    assert chapter_row["license"] == "AI-GENERATED"
    assert chapter_row["payload"]["meta"]["ai_generated"] is True
    ref = chapter_row["payload"]["meta"]["original_ref"]["path"]
    assert ref and chapter_row["payload"]["meta"]["original_ref"]["chars"] > 0

    # 原始章节按需加载（T4）：mock 广告行已被清洗链剔除
    code, orig = _request(server, "GET", f"/api/book/original?ref={quote(ref, safe='')}")
    assert code == 200 and orig["ok"] is True and orig["folded"] is False
    assert orig["chars"] == chapter_row["payload"]["meta"]["original_ref"]["chars"]
    assert "天才一秒记住" not in orig["text"]  # clean_chapter 广告行剔除实证
    code, full = _request(server, "GET", f"/api/book/original?ref={quote(ref, safe='')}&full=1")
    assert code == 200 and full["folded"] is False and full["text"] == orig["text"]
    # §六.1 可核验性断言（端到端，设计验收 1）：提炼卡引用段逐字能在章节缓存
    # 原文中找到（桩引用=从 prompt 清洗正文原句摘取——卡→素材正文→缓存闭环）
    body = chapter_row["payload"]["text"]
    quote_line = next(l[2:].strip() for l in body.splitlines() if l.startswith("> "))
    assert quote_line and quote_line in orig["text"]


# ── 降级优先双锁 ────────────────────────────────────────────────


def test_no_ai_degrades_all_and_no_ghost_run(server, mock_engine) -> None:
    """AI 未配置：全章原文入库（license UNKNOWN + distilled=false 前端琥珀徽
    数据面）+ 无幽灵 run（agent runs 404——前端退 jobs phase 粗粒度源）。"""
    code, out = _request(
        server, "POST", "/api/materials/from-chapters",
        {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
         "chapters": _chapters(2), "digest": True},
    )
    assert code == 200 and out["ok"] is True
    job = _poll_job(server, out["job_id"])
    assert job["status"] == "done"
    result = job["result"]
    assert result["distilled_n"] == 0 and result["raw_n"] == 2
    assert "AI 未配置" in (result.get("note") or "")
    code, listed = _request(server, "GET", "/api/materials")
    assert code == 200
    for row in listed["items"]:
        bk = (row["payload"].get("meta") or {}).get("book") or {}
        if bk:
            assert row["license"] == "UNKNOWN" and bk["distilled"] is False
    code, _ = _request(server, "GET", f"/api/agent/runs?id={out['job_id']}")
    assert code == 404  # 无 AI plan 不建 run——不产生假进度源


def test_interrupted_book_job_run_reads_interrupted(_isolate_home: Path) -> None:
    """book_fetch 孤儿（进程崩溃遗 running run 目录）读侧标 interrupted——
    挂接 run 的读侧一致语义（agent_run 同款，§F75.2）。"""
    job = JobStore().create("book_fetch", {"sourceUrl": SOURCE})
    agent_service.open_attached_run(job["id"], build_book_digest(
        [{"url": "https://mock.example/book/1/c1", "title": "第 1 章"}]))
    assert agent_service.read_run(job["id"])["status"] == "running"
    JobStore().update(job["id"], status="failed",
                      error="interrupted（进程重启，任务中断；可在任务面板重发）")
    assert agent_service.read_run(job["id"])["status"] == "interrupted"


# ── 引擎 mock 分离（四不可让步之一）的源面守门 ────────────────────


def test_mock_switch_stays_inside_infra() -> None:
    """MUSESPIRAL_ENGINE_MOCK/mock_enabled 只允许出现在 infra/bookengine.py——
    mock 是引擎缺席期的测试联调假件，service/studio/前端一律不得分支。"""
    root = Path(__file__).resolve().parent.parent / "musespiral"
    leaks: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "bookengine.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "MUSESPIRAL_ENGINE_MOCK" in text or "mock_enabled" in text:
            leaks.append(str(path.relative_to(root)))
    assert not leaks, f"mock 开关泄漏出 infra 层: {leaks}"


# ── 修1 端到端：入库进度条「取消」=POST /api/jobs/cancel（正典 §2.2）──


def test_smoke_cancel_midflight_via_endpoint(server, mock_engine, ai_stub) -> None:
    """中飞取消全链：running job 上 POST cancel → 检查点收束（已完成章保留、
    未完成章放弃）→ job done+result.cancelled+note 明列 M/N。时序窗=桩延迟。"""
    _StubAIHandler.delay = 0.2          # 每次提炼 chat 0.2s——取消的时序窗
    _StubAIHandler.fail_on = "第 2 章"   # 第 2 章步级失败（正常循环会降级重跑；
    try:                                 #  取消后改为放弃——两条路径分流的实证）
        code, out = _request(
            server, "POST", "/api/materials/from-chapters",
            {"confirm": True, "sourceUrl": SOURCE, "bookUrl": BOOK_URL, "book": BOOK,
             "chapters": _chapters(3), "digest": True},
        )
        assert code == 200 and out["ok"] is True
        job_id = out["job_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            _, j = _request(server, "GET", f"/api/jobs/{job_id}")
            if j["status"] in ("running", "done"):
                break
            time.sleep(0.02)
        code, ack = _request(server, "POST", "/api/jobs/cancel",
                             {"confirm": True, "job_id": job_id})
        assert code == 200 and ack["ok"] is True and ack["cancelled"] is True
        job = _poll_job(server, job_id)
        assert job["status"] == "done"  # 部分成功是合法终态（非 failed）
        result = job["result"]
        assert result.get("cancelled") is True
        landed = len(result["materials"])
        assert landed <= 2  # 已完成章保留（0-2 张卡，取决于旗标落点）
        assert "已入库" in (result.get("note") or "") and "已放弃" in (result.get("note") or "")
        rows = ms.list_materials()
        assert len(rows) == landed
        for row in rows:
            assert row["license"] == "AI-GENERATED"  # 只保留提炼卡形态，无原文降级
    finally:
        _StubAIHandler.delay = 0.0
        _StubAIHandler.fail_on = None
