"""本地书架服务 + Studio 端点测试（B1 本地书籍线，2026-11-26）。

三层覆盖：
  a) 服务层：add/reparse/chapter/toc/remove 的读写语义 + 引用型红线（库里
     没有正文副本）+ 无用书的如实错误；
  b) 端点层：真实回环 HTTP（studio.make_server）——7+1 端点 + confirm 闸门 +
     参数缺失 400 + select-章节素材化 job 全链；
  c) 契约锁：新增 4 GET + 4 POST 在契约表与路由表双侧、KINDS 21、
     PROVENANCE_OPS 扩 adopt。

D 档判例依据：引用型红线只有「磁盘上改一个字节、素材里的字跟着变」能证明
——只断言「meta 里有 path」是自证。故 a) 层有 `test_edit_file_reflected`。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import localbook_service as svc

BOOK_TEXT = (
    "作者：测试君\n"
    "\n"
    "第一章 天穹裂痕\n"
    "雨是三更时分来的。先是远处有闷雷走动，像有什么东西在天穹上探路。\n"
    "我放下手中的读数板，望向那面巨大的观测窗。\n"
    "\n"
    "第二章 观测站\n"
    "记录员走进来，手里拿着一份读数表。\n"
    "\n"
    "第三章 第三象限\n"
    "裂痕又扩大了，这一次是第三象限。\n"
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def book_file(tmp_path: Path) -> Path:
    path = tmp_path / "星河彼岸.txt"
    path.write_text(BOOK_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        from musespiral.studio.actions import _reset_runner

        _reset_runner()


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


# ── a) 服务层语义 ─────────────────────────────────────────────────


def test_add_book_writes_meta_and_chapters(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    assert meta["structure"] == "chapters" and meta["chapters"] == 3
    assert meta["chapter_hits"] == 3
    assert meta["charset"] == "utf-8"
    assert meta["author"] == "测试君"  # 首行「作者：」猜出
    assert meta["title"] == "星河彼岸"  # 文件名去扩展名
    assert meta["unstructured"] is False
    book_dir = svc.localbooks_root() / meta["id"]
    assert (book_dir / "meta.json").is_file() and (book_dir / "chapters.json").is_file()
    # 引用型红线：书架目录里只有两件元数据，**没有正文副本**
    assert sorted(p.name for p in book_dir.iterdir()) == ["chapters.json", "meta.json"]


def test_add_book_is_idempotent_by_path(book_file: Path) -> None:
    """同路径重复导入 = 覆盖重解析（同 id upsert，书架不出现两本同名书）。"""
    first = svc.add_book(str(book_file))
    second = svc.add_book(str(book_file), title="改过的书名")
    assert first["id"] == second["id"]
    assert second["title"] == "改过的书名"
    assert len(svc.list_books()["books"]) == 1


def test_add_book_gb18030_and_unstructured(tmp_path: Path, book_file: Path) -> None:
    """GB18030 编码实测（设计 §四「真实 TXT（GB18030 大文件）实测」的服务层形态）。"""
    gb = tmp_path / "云海剑歌.txt"
    gb.write_bytes(BOOK_TEXT.encode("gb18030"))
    meta = svc.add_book(str(gb))
    assert meta["charset"] == "gb18030" and meta["chapters"] == 3
    chapter = svc.book_chapter(meta["id"], 1)
    assert "雨是三更时分来的" in chapter["content"]  # 解码正确（不是乱码）
    # 无结构文本 → 定长回退并如实标注
    flat = tmp_path / "笔记.txt"
    flat.write_text("就是一些随手记的想法。" * 2000, encoding="utf-8")
    flat_meta = svc.add_book(str(flat))
    assert flat_meta["structure"] == "fixed" and flat_meta["unstructured"] is True


def test_add_book_rejects_bad_input(tmp_path: Path, book_file: Path) -> None:
    with pytest.raises(svc.LocalBookError, match="绝对路径"):
        svc.add_book("relative/path.txt")
    with pytest.raises(svc.LocalBookError, match="只支持纯文本"):
        bad = tmp_path / "movie.mp4"
        bad.write_bytes(b"\x00\x01")
        svc.add_book(str(bad))
    with pytest.raises(svc.LocalBookError, match="不存在"):
        svc.add_book(str(tmp_path / "nope.txt"))
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(svc.LocalBookError, match="空"):
        svc.add_book(str(empty))


def test_book_chapter_reflects_file_edits(book_file: Path) -> None:
    """引用型实证：磁盘上改一个字，章节正文跟着变（库里没有缓存副本）。"""
    meta = svc.add_book(str(book_file))
    before = svc.book_chapter(meta["id"], 1)["content"]
    assert "雨是三更时分来的" in before
    book_file.write_text(BOOK_TEXT.replace("三更", "五更"), encoding="utf-8")
    after = svc.book_chapter(meta["id"], 1)["content"]
    assert "五更" in after and "三更" not in after


def test_book_chapter_404_when_file_gone(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    book_file.unlink()
    info = svc.book_info(meta["id"])
    assert info["file_ok"] is False  # 书架条目保留，如实说文件没了
    with pytest.raises(svc.LocalBookError, match="不可读"):
        svc.book_chapter(meta["id"], 1)


def test_book_toc_is_bookengine_shaped(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    toc = svc.book_toc(meta["id"])
    assert toc["total"] == 3
    first = toc["chapters"][0]
    assert set(first) == {"title", "index", "url"}
    assert first["url"] == f"localbook:{meta['id']}#1"
    assert first["index"] == 1


def test_reparse_switches_rule(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    assert meta["rule"] == "cn-novel-standard" and meta["structure"] == "chapters"
    # 换到 plain-split → 如实变 fixed（换规则真的重切，不是只改 meta 字面）
    rep = svc.reparse_book(meta["id"], rule="plain-split")
    assert rep["rule"] == "plain-split" and rep["structure"] == "fixed"
    assert rep["title"] == meta["title"]  # 人工元数据保留
    assert rep["added_at"] == meta["added_at"]
    # 再换回 → 恢复 3 章
    back = svc.reparse_book(meta["id"], rule="cn-novel-standard")
    assert back["structure"] == "chapters" and back["chapters"] == 3


def test_remove_book_keeps_user_file(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    out = svc.remove_book(meta["id"])
    assert out["removed"] == meta["id"] and book_file.is_file()  # 原文件零触碰
    assert svc.list_books()["books"] == []
    with pytest.raises(svc.LocalBookError, match="书不存在"):
        svc.book_info(meta["id"])


def test_bad_book_id_rejected() -> None:
    """id 走 URL 参数 → 指纹白名单（防路径逃逸）。"""
    for bad in ("../../etc/passwd", "abc", "ABCDEF123456", ""):
        with pytest.raises(svc.LocalBookError):
            svc.book_info(bad)


def test_custom_rules_dir_loading(tmp_path: Path, monkeypatch) -> None:
    """`~/.musespiral/localbook-rules/*.json`：好规则入表、坏 JSON 计数不抛。"""
    rules_dir = svc.rules_root()
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "hua.json").write_text(
        '{"id":"cn-hua","name":"话本","chapter_regex":"^第[一二三0-9]+话","min_chapters":3}',
        encoding="utf-8",
    )
    (rules_dir / "broken.json").write_text("{oops", encoding="utf-8")
    out = svc.all_rules()
    assert out["bad"] == 1
    assert [r["id"] for r in out["rules"]][:3] == [
        "cn-novel-standard",
        "cn-classic",
        "plain-split",
    ]
    assert "cn-hua" in [r["id"] for r in out["rules"]]
    # 自定义规则能真的切书
    book = tmp_path / "话本.txt"
    book.write_text("第一话 甲\n正文\n第二话 乙\n正文\n第三话 丙\n正文\n", encoding="utf-8")
    meta = svc.add_book(str(book), rule="cn-hua")
    assert meta["structure"] == "chapters" and meta["chapters"] == 3


def test_materialize_job_adds_and_skips(book_file: Path) -> None:
    """选章素材化：首跑 N 条、二跑全 skip（同 id 幂等，题面硬要求）。"""
    meta = svc.add_book(str(book_file))
    first = svc.run_fetch(meta["id"], [1, 2])
    assert first["added_n"] == 2 and first["skipped_n"] == 0
    second = svc.run_fetch(meta["id"], [1, 2])
    assert second["added_n"] == 0 and second["skipped_n"] == 2
    assert second["skipped"] == [1, 2]
    # 素材本体：payload 只有摘录，没有整章正文（只读不复制红线）
    from musespiral.services import materials_service

    row = materials_service.get_material(first["materials"][0])
    assert row is not None
    payload = row["payload"]
    assert payload["local_book_id"] == meta["id"] and payload["chapter_n"] == 1
    assert payload["chars"] > 0 and len(payload["excerpt"]) <= svc.EXCERPT_CHARS
    assert "text" not in payload, "本地书素材不得携带整章正文（引用型红线）"
    chain = row["provenance"]["chain"]
    assert chain == [{"from_id": f"localbook:{meta['id']}:1", "op": "adopt"}]


def test_materialize_rejects_bad_chapters(book_file: Path) -> None:
    meta = svc.add_book(str(book_file))
    with pytest.raises(svc.LocalBookError, match="chapters"):
        svc.submit_to_runner(None, meta["id"], [])
    with pytest.raises(svc.LocalBookError, match="≥1 的整数"):
        svc.submit_to_runner(None, meta["id"], [0])
    with pytest.raises(svc.LocalBookError, match="上限"):
        svc.submit_to_runner(None, meta["id"], list(range(1, svc.MAX_MATERIALIZE_CHAPTERS + 2)))


# ── b) 端点层（真实回环 HTTP）─────────────────────────────────────


def test_endpoints_full_chain(server, book_file: Path) -> None:
    # 导入（confirm 闸门：缺省 400）
    code, body = _request(server, "POST", "/api/localbook/add", {"path": str(book_file)})
    assert code == 400 and "confirm" in body["error"]
    code, body = _request(
        server, "POST", "/api/localbook/add", {"path": str(book_file), "confirm": True}
    )
    assert code == 200 and body["ok"] is True
    book_id = body["book"]["id"]
    # 清单
    code, listing = _request(server, "GET", "/api/localbook/list")
    assert code == 200 and listing["total"] == 1 and listing["books"][0]["id"] == book_id
    # 详情
    code, info = _request(server, "GET", f"/api/localbook/info?id={book_id}")
    assert code == 200 and info["chapters"] == 3 and info["structure"] == "chapters"
    # 目录
    code, toc = _request(server, "GET", f"/api/localbook/toc?id={book_id}")
    assert code == 200 and toc["total"] == 3 and toc["chapters"][0]["url"].endswith("#1")
    # 正文
    code, chapter = _request(server, "GET", f"/api/localbook/chapter?id={book_id}&n=1")
    assert code == 200 and chapter["status"] == "ok" and "雨是三更时分来的" in chapter["content"]
    # 重解析（换规则）
    code, rep = _request(
        server,
        "POST",
        "/api/localbook/reparse",
        {"id": book_id, "rule": "plain-split", "confirm": True},
    )
    assert code == 200 and rep["book"]["structure"] == "fixed"
    # 下架
    code, rem = _request(server, "POST", "/api/localbook/remove", {"id": book_id, "confirm": True})
    assert code == 200 and rem["removed"] == book_id and book_file.is_file()


def test_endpoints_param_errors(server) -> None:
    code, body = _request(server, "GET", "/api/localbook/info")
    assert code == 400 and "id" in body["error"]
    code, body = _request(server, "GET", "/api/localbook/chapter?id=abcdef123456")
    assert code == 400 and "n" in body["error"]
    code, body = _request(server, "GET", "/api/localbook/chapter?id=abcdef123456&n=x")
    assert code == 400 and "整数" in body["error"]
    code, body = _request(server, "GET", "/api/localbook/info?id=abcdef123456")
    assert code == 400 and "书不存在" in body["error"]
    code, body = _request(server, "POST", "/api/localbook/add", {"confirm": True})
    assert code == 400 and "path" in body["error"]


def test_from_local_chapters_job(server, book_file: Path) -> None:
    """素材化端点 → job localbook_fetch → 轮询终态 → 素材可查。"""
    code, body = _request(
        server, "POST", "/api/localbook/add", {"path": str(book_file), "confirm": True}
    )
    book_id = body["book"]["id"]
    code, body = _request(
        server,
        "POST",
        "/api/materials/from-local-chapters",
        {"id": book_id, "chapters": [1, 2, 3], "book": {"name": "星河彼岸"}, "confirm": True},
    )
    assert code == 200 and body["ok"] is True and body["chapters"] == [1, 2, 3]
    job_id = body["job_id"]
    import time

    for _ in range(100):
        code, job = _request(server, "GET", f"/api/jobs/{job_id}")
        if job.get("status") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    assert job["kind"] == "localbook_fetch"
    result = job["result"]
    assert result["added_n"] == 3 and result["chapters_total"] == 3
    # 重跑 → 全 skip（幂等）
    code, body2 = _request(
        server,
        "POST",
        "/api/materials/from-local-chapters",
        {"id": book_id, "chapters": [1], "confirm": True},
    )
    for _ in range(100):
        code, job2 = _request(server, "GET", f"/api/jobs/{body2['job_id']}")
        if job2.get("status") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job2["result"]["skipped_n"] == 1 and job2["result"]["added_n"] == 0


def test_from_local_chapters_requires_confirm(server, book_file: Path) -> None:
    code, body = _request(
        server, "POST", "/api/localbook/add", {"path": str(book_file), "confirm": True}
    )
    book_id = body["book"]["id"]
    code, body = _request(
        server,
        "POST",
        "/api/materials/from-local-chapters",
        {"id": book_id, "chapters": [1]},
    )
    assert code == 400 and "confirm" in body["error"]


# ── c) 契约锁 ─────────────────────────────────────────────────────


def test_contract_lock_membership() -> None:
    """四处新增端点两侧都在：契约表（test_architecture）与路由表。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    for path in (
        "/api/localbook/list",
        "/api/localbook/info",
        "/api/localbook/toc",
        "/api/localbook/chapter",
    ):
        assert path in CONTRACT_GET_EXACT and path in _GET_ROUTES
    for path in (
        "/api/localbook/add",
        "/api/localbook/remove",
        "/api/localbook/reparse",
        "/api/materials/from-local-chapters",
    ):
        assert path in CONTRACT_POST_EXACT and path in _ACTION_ROUTES
    assert len(CONTRACT_GET_EXACT) == 56 and len(CONTRACT_POST_EXACT) == 91  # +render（素材查看器批）


def test_job_kind_and_provenance_op_registered() -> None:
    from musespiral.core.materials import model
    from musespiral.jobs.store import KINDS

    assert "localbook_fetch" in KINDS and len(KINDS) == 21
    assert "adopt" in model.PROVENANCE_OPS
    # 单链豁免真的生效（原始 text 携带单条 adopt 链可通过校验）
    row = model.make_material(
        mtype="text",
        source_id="localbook",
        uri="localbook:abcdef123456#1",
        title="t",
        payload={"excerpt": "x"},
        provenance={"chain": [{"from_id": "localbook:abcdef123456:1", "op": "adopt"}], "derived_from": []},
        fetched_at="2026-11-26T00:00:00",
    )
    assert model.validate_material(row) == []
    # 多链仍拒（immutable 红线不变）
    bad = {**row, "provenance": {"chain": [
        {"from_id": "a", "op": "adopt"}, {"from_id": "b", "op": "adopt"}
    ], "derived_from": []}}
    assert any("provenance" in e for e in model.validate_material(bad))


def test_chapter_cap_aligned_with_network_book() -> None:
    """截断口径与网络书同值（阅读面板同一个渲染器）。"""
    from musespiral.core.localbook.reader import CHAPTER_CAP
    from musespiral.studio.book_actions import CHAPTER_CAP as STUDIO_CAP

    assert CHAPTER_CAP == STUDIO_CAP == 200_000
