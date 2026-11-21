"""Studio /api/materials 契约测试（第二十期 F56 发现页收编后端，M2；M3 增分诊面）。

沿 test_studio_discover.py 惯例：真实回环服务器 + http.client，tmp_path 与
MUSESPIRAL_HOME 全隔离（不触 ~/.musespiral、零外网）。覆盖：素材查询形状（items/
total/评分现算/drafted 推导）/过滤参数（starred/read/enabled/type/source/
limit）/参数校验（布尔/整数 400）/方法语义（POST 405）；M3 增：detail
（血统引用+连坐预览）/triage 三键窄门/delete 墓碑连坐（两步 confirm 数据面）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.services import materials_service as ms
from musespiral.services import sources_service as src_svc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
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
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _seed(studio_root: Path, env: Path) -> None:
    """kb 源 + 两条素材（kb text 引用 / radar_signal meta 分级）。"""
    (env / "kb" / "13-source-analysis").mkdir(parents=True)
    (env / "kb" / "13-source-analysis" / "doc.md").write_text(
        "---\ntitle: 评分文档\ncategory: 13-source-analysis\ncreated: 2020-01-01\n"
        "tags: [源码分析, Python, 测试, o/r]\n---\n\n## 概述\n\n正文。\n",
        encoding="utf-8",
    )
    src_svc.register_source(
        {
            "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
            "config": {"root": str(env / "kb"), "sources": []},
            "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
        }
    )
    ms.add_material(
        model.make_material(
            mtype="text", source_id="kb", uri="13-source-analysis/doc.md", title="评分文档",
            payload={"local_path": "13-source-analysis/doc.md"}, fetched_at="2026-11-15T09:00:00",
        )
    )
    ms.add_material(
        model.make_material(
            mtype="radar_signal", source_id="radarhub", uri="https://github.com/a/b", title="a/b",
            payload={"url": "https://github.com/a/b",
                     "meta": {"line": "embedded", "grade": "S", "score": 88,
                              "url": "https://github.com/a/b"}},
            fetched_at="2026-11-15T10:00:00",
        )
    )
    # 正稿指回 kb 文档 → drafted 推导命中
    (studio_root / "draft.md").write_text(
        "---\ntitle: 已生成\nsource: 13-source-analysis/doc.md\n---\n\n正文\n", encoding="utf-8"
    )


def test_materials_endpoint_shape_and_scoring(server, studio_root, _isolate_home) -> None:
    _seed(studio_root, _isolate_home)
    code, payload = _request(server, "GET", "/api/materials")
    assert code == 200
    assert payload["total"] == 2
    items = {m["origin"]["uri"]: m for m in payload["items"]}
    radar = items["https://github.com/a/b"]
    assert radar["score"] == 88 and radar["grade"] == "S"
    kbm = items["13-source-analysis/doc.md"]
    assert kbm["score"] == 35 and kbm["score_breakdown"]["adaptability"] == 10
    assert kbm["drafted"] is True  # 正稿 source 指回（相对形态命中）
    assert radar["drafted"] is False
    assert payload["items"][0]["origin"]["uri"] == "https://github.com/a/b"  # 88 > 35


def test_materials_endpoint_filters(server, studio_root, _isolate_home) -> None:
    _seed(studio_root, _isolate_home)
    code, payload = _request(server, "GET", "/api/materials?type=radar_signal")
    assert code == 200 and payload["total"] == 1
    assert payload["items"][0]["type"] == "radar_signal"
    code, payload = _request(server, "GET", "/api/materials?source=kb&limit=1")
    assert code == 200 and payload["total"] == 1
    code, payload = _request(server, "GET", "/api/materials?starred=false&read=false")
    assert code == 200 and payload["total"] == 2  # 默认 triage 全 False 命中
    code, payload = _request(server, "GET", "/api/materials?starred=true")
    assert code == 200 and payload["total"] == 0


def test_materials_endpoint_validation(server) -> None:
    assert _request(server, "GET", "/api/materials?starred=maybe")[0] == 400
    assert _request(server, "GET", "/api/materials?read=1")[0] == 400
    assert _request(server, "GET", "/api/materials?limit=abc")[0] == 400
    assert _request(server, "POST", "/api/materials", {})[0] == 405


def test_materials_endpoint_empty_library(server) -> None:
    code, payload = _request(server, "GET", "/api/materials")
    assert code == 200 and payload == {"items": [], "total": 0}


# ── M3：detail（连坐预览）/ triage（三键窄门）/ delete（墓碑+连坐）──


def _mid_of(server) -> str:
    code, payload = _request(server, "GET", "/api/materials")
    assert code == 200 and payload["total"] >= 1
    return payload["items"][0]["id"]


def test_materials_detail_shape_and_cascade(server, studio_root, _isolate_home) -> None:
    _seed(studio_root, _isolate_home)
    mid = _mid_of(server)
    code, payload = _request(server, "GET", f"/api/materials/detail?id={mid}")
    assert code == 200 and payload["ok"] is True
    assert payload["material"]["id"] == mid
    assert payload["material"]["references"] == []  # 无血统引用
    assert payload["cascade"] == []  # 无引用者 → 连坐空
    # 参数校验与缺失语义
    assert _request(server, "GET", "/api/materials/detail")[0] == 400
    assert _request(server, "GET", "/api/materials/detail?id=nope")[0] == 404
    assert _request(server, "POST", "/api/materials/detail", {"id": mid, "confirm": True})[0] == 405


def test_materials_triage_narrow_gate(server, studio_root, _isolate_home) -> None:
    _seed(studio_root, _isolate_home)
    mid = _mid_of(server)
    body = {"id": mid, "starred": True, "read": True, "confirm": True}
    code, payload = _request(server, "POST", "/api/materials/triage", body)
    assert code == 200 and payload["triage"]["starred"] is True and payload["triage"]["read"] is True
    # 查询面立即可见（读序合并）
    _, q = _request(server, "GET", "/api/materials?starred=true")
    assert q["total"] == 1 and q["items"][0]["id"] == mid
    # 窄门拒绝：空分诊键 / 非布尔值 / 键集外
    assert _request(server, "POST", "/api/materials/triage", {"id": mid, "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/materials/triage", {"id": mid, "starred": "x", "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/materials/triage", {"id": mid, "payload": {}, "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/materials/triage", {"id": "nope", "starred": True, "confirm": True})[0] == 400


def test_materials_delete_two_step_data_plane(server, studio_root, _isolate_home) -> None:
    """删除=墓碑+连坐清单；detail 预览与删除返回同源（两步 confirm 声明面）。"""
    _seed(studio_root, _isolate_home)
    mid = _mid_of(server)
    code, payload = _request(server, "POST", "/api/materials/delete", {"id": mid, "confirm": True})
    assert code == 200 and payload["deleted"] == mid and payload["cascade"] == []
    _, q = _request(server, "GET", "/api/materials")
    assert q["total"] == 1 and all(m["id"] != mid for m in q["items"])  # 墓碑生效，另一条在册
    assert _request(server, "POST", "/api/materials/delete", {"id": mid, "confirm": True})[0] == 400  # 重复删除
    assert _request(server, "POST", "/api/materials/delete", {"confirm": True})[0] == 400  # 缺 id
    assert _request(server, "PUT", "/api/materials/delete")[0] == 405


def test_materials_detail_cascade_with_derived(server, studio_root, _isolate_home) -> None:
    """derived 素材 provenance 指回源素材 → detail.cascade 列出引用者（徽章语义）。"""
    _seed(studio_root, _isolate_home)
    _, before = _request(server, "GET", "/api/materials")
    src_id = _mid_of(server)
    remaining = next(m["id"] for m in before["items"] if m["id"] != src_id)
    derived = model.make_material(
        mtype="derived", source_id="kb", uri="derived.md", title="衍生素材",
        payload={"local_path": "derived.md"}, fetched_at="2026-11-16T11:00:00",
    )
    derived["provenance"] = {"derived_from": [src_id], "chain": []}
    ms.add_material(derived)
    _, d = _request(server, "GET", f"/api/materials/detail?id={src_id}")
    assert [c["id"] for c in d["cascade"]] == [derived["id"]]
    _, dd = _request(server, "POST", "/api/materials/delete", {"id": src_id, "confirm": True})
    assert [c["id"] for c in dd["cascade"]] == [derived["id"]]  # 删除返回连坐同源
    # 引用者不级联删除，仍在册（kb 原素 + derived 两条）；被删源素材出列
    _, q = _request(server, "GET", "/api/materials")
    assert q["total"] == 2 and {m["id"] for m in q["items"]} == {derived["id"], remaining}
    _, det = _request(server, "GET", f"/api/materials/detail?id={derived['id']}")
    assert det["material"]["references"][0]["deleted"] is True


# ── 第二十一期 Phase A：契约组（T59 上传 / T60 文件服务 / T61 登记 /
#    T62 集合 / T63 标签 / T64 搜索）——真实回环服务器 + 隔离 home ──────

import io as _io
import re as _re
import socket as _socket
from urllib.parse import quote as _quote

from musespiral.infra import media_store as _media_store
from PIL import Image as _Image

_HOME = None  # _isolate_home 注入的隔离根（辅助函数用）


def _png(size=(60, 30)) -> bytes:
    img = _Image.new("RGB", size, (120, 40, 200))
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _lines(home) -> int:
    path = home / "materials" / "materials.jsonl"
    if not path.exists():
        return 0
    return len([x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()])


def _upload(server, data: bytes, qs: str = "confirm=1"):
    """二进制旁路 POST（body=原始字节；query 带 confirm）。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", f"/api/materials/upload?{qs}", body=data,
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))},
    )
    resp = conn.getresponse()
    out = resp.read()
    conn.close()
    return resp.status, json.loads(out)


def _get_raw(server, path: str):
    """GET 取 (status, headers, body-bytes)——文件服务魔数/响应头断言用。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    headers = dict(resp.getheaders())
    conn.close()
    return resp.status, headers, body


# ── T59 图片上传 ────────────────────────────────────────────────────


def test_t59_1_upload_png_full_chain(server, _isolate_home) -> None:
    data = _png()
    code, payload = _upload(server, data, "confirm=1&name=t")
    assert code == 200, payload
    mat = payload["material"]
    assert mat["type"] == "image"
    assert mat["origin"]["source_id"] == "upload"
    assert mat["origin"]["uri"].startswith("upload://sha256:")
    assert mat["origin"]["title"] == "t"
    meta = mat["payload"]["meta"]
    assert meta["width"] == 60 and meta["height"] == 30 and meta["format"] == "PNG"
    assert meta["sha256"] == __import__("hashlib").sha256(data).hexdigest()
    files = _isolate_home / "materials" / "files"
    assert (files / f"{mat['id']}.png").is_file()
    assert (files / f"{mat['id']}.thumb.webp").is_file()
    assert payload["dedup"] is False and payload["thumb_ready"] is True


def test_t59_2_upload_idempotent_zero_append(server, _isolate_home) -> None:
    data = _png()
    code, first = _upload(server, data)
    assert code == 200 and first["dedup"] is False
    before = _lines(_isolate_home)
    code, second = _upload(server, data)  # 同字节二传
    assert code == 200 and second["dedup"] is True
    assert second["material"]["id"] == first["material"]["id"]
    assert _lines(_isolate_home) == before  # jsonl 零追加


def test_t59_3_fake_svg_oversize_rejected(server, _isolate_home) -> None:
    # 文本字节伪装 .png
    code, payload = _upload(server, b"plain text bytes pretending to be png" * 8, "confirm=1&name=t.png")
    assert code == 400 and "格式" in payload["error"]
    # svg 字节（同源脚本判例恒拒）
    code, payload = _upload(server, b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
    assert code == 400 and "格式" in payload["error"]
    # Content-Length 声明 9MiB → 未读 body 即拒
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/api/materials/upload?confirm=1", body=b"x",
                 headers={"Content-Length": str(9 * 1024 * 1024)})
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    assert resp.status == 400 and "8MiB" in out["error"]


def test_t59_4_confirm_query_and_license_gate(server, _isolate_home) -> None:
    code, payload = _upload(server, _png(), "")  # 缺 ?confirm=1
    assert code == 400 and "confirm" in payload["error"]
    code, payload = _upload(server, _png(), "confirm=1&license=MIT")
    assert code == 400 and "license" in payload["error"]
    assert _request(server, "GET", "/api/materials/upload")[0] == 405  # GET 打此路径 → 405


# ── T60 灯箱与文件服务 ─────────────────────────────────────────────


def test_t60_1_file_variants_headers_and_magic(server, _isolate_home) -> None:
    _, up = _upload(server, _png())
    mid = up["material"]["id"]
    code, headers, body = _get_raw(server, f"/api/materials/file?id={mid}&variant=thumb")
    assert code == 200
    assert headers.get("Content-Type") == "image/webp"
    assert "immutable" in headers.get("Cache-Control", "")
    assert body[:4] == b"RIFF"
    code, headers, body = _get_raw(server, f"/api/materials/file?id={mid}&variant=full")
    assert code == 200 and headers.get("Content-Type") == "image/png"
    assert body[:4] == b"\x89PNG"


def test_t60_2_three_gates(server, studio_root, _isolate_home) -> None:
    files = _isolate_home / "materials" / "files"
    files.mkdir(parents=True)
    # 门 1：local_path 越界（../materials.jsonl）
    escape = model.make_material(
        mtype="image", source_id="upload", uri="upload://sha256:gate1", title="逃逸",
        payload={"local_path": "../materials.jsonl"}, fetched_at="2026-11-16T10:00:00",
    )
    ms.add_material(escape)
    code, payload = _request(server, "GET", f"/api/materials/file?id={escape['id']}&variant=full")
    assert code == 400
    # 门 2：.svg 后缀白名单外
    svg = model.make_material(
        mtype="image", source_id="upload", uri="upload://sha256:gate2", title="svg",
        payload={"local_path": "materials/files/00000000000a.svg"}, fetched_at="2026-11-16T10:00:00",
    )
    ms.add_material(svg)
    (files / "00000000000a.svg").write_bytes(b"<svg/>")
    code, payload = _request(server, "GET", f"/api/materials/file?id={svg['id']}&variant=full")
    assert code == 400
    # 门 3：改扩展名字节（gif 魔数贴 .png 名）
    fake = model.make_material(
        mtype="image", source_id="upload", uri="upload://sha256:gate3", title="伪png",
        payload={"local_path": "materials/files/00000000000b.png"}, fetched_at="2026-11-16T10:00:00",
    )
    ms.add_material(fake)
    (files / "00000000000b.png").write_bytes(b"GIF89a" + b"\x00" * 32)
    code, payload = _request(server, "GET", f"/api/materials/file?id={fake['id']}&variant=full")
    assert code == 400 and "魔数" in payload["error"]


def test_t60_3_id_boundaries(server, studio_root, _isolate_home) -> None:
    _, up = _upload(server, _png())
    mid = up["material"]["id"]
    assert _request(server, "GET", "/api/materials/file?id=nope00000000")[0] == 404  # 不存在
    assert _request(server, "POST", "/api/materials/file", {"confirm": True})[0] == 405
    ms.add_material(model.make_material(
        mtype="text", source_id="kb", uri="plain.md", title="文本",
        payload={"text": "正文"}, fetched_at="2026-11-16T10:00:00",
    ))
    _, q = _request(server, "GET", "/api/materials?type=text")
    text_id = q["items"][0]["id"]
    assert _request(server, "GET", f"/api/materials/file?id={text_id}")[0] == 400  # type≠image
    assert _request(server, "POST", "/api/materials/delete", {"id": mid, "confirm": True})[0] == 200
    assert _request(server, "GET", f"/api/materials/file?id={mid}")[0] == 404  # 已墓碑
    assert _request(server, "GET", "/api/materials/file")[0] == 400  # 缺 id


# ── T61 视频登记 ────────────────────────────────────────────────────


def _register(server, url: str, **extra):
    return _request(server, "POST", "/api/materials/register",
                    {"url": url, "confirm": True, **extra})


def test_t61_1_register_bilibili_normalize(server, _isolate_home) -> None:
    code, payload = _register(server, "https://www.bilibili.com/video/BVxx")
    assert code == 200, payload
    mat = payload["material"]
    assert mat["type"] == "video"
    assert mat["origin"]["source_id"] == "manual"
    assert mat["origin"]["uri"] == "video://https://www.bilibili.com/video/BVxx"  # host 小写
    assert mat["origin"]["title"] == "BVxx"  # 缺省取 URL 尾段
    assert mat["payload"]["meta"]["platform"] == "bilibili"
    assert mat["payload"]["url"] == "https://www.bilibili.com/video/BVxx"


def test_t61_2_register_idempotent(server, _isolate_home) -> None:
    _register(server, "https://www.bilibili.com/video/BVyy")
    before = _lines(_isolate_home)
    code, payload = _register(server, "https://www.bilibili.com/video/BVyy/")
    assert code == 200 and payload["dedup"] is True  # 去末尾斜杠后规范化幂等
    assert _lines(_isolate_home) == before


def test_t61_3_url_gate_and_zero_egress(server, _isolate_home, monkeypatch) -> None:
    code, payload = _register(server, "ftp://x.example/v")
    assert code == 400 and "scheme" in payload["error"]
    code, payload = _register(server, "https://u@example.com/v")
    assert code == 400 and "user" in payload["error"]
    code, payload = _register(server, "http://127.0.0.1:8766/")
    assert code == 400 and ("回环" in payload["error"] or "内网" in payload["error"])
    assert _register(server, "http://admin@example.com/")[0] == 400
    # 零出网锚：登记全链无任何外发连接/urlopen（回环测试客户端自身连接除外）
    egress: list = []
    orig_create = _socket.create_connection

    def _spy(address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost"):
            egress.append(address)
        return orig_create(address, *a, **k)

    import urllib.request as _ur

    def _no_urlopen(*a, **k):  # pragma: no cover - 被调用即记违规
        egress.append(("urlopen", a))

    monkeypatch.setattr(_socket, "create_connection", _spy)
    monkeypatch.setattr(_ur, "urlopen", _no_urlopen)
    assert _register(server, "https://www.youtube.com/watch?v=abc")[0] == 200
    assert egress == []
    assert _request(server, "GET", "/api/materials/register")[0] == 405  # GET → 405


# ── T62 手工集合 ────────────────────────────────────────────────────


def _coll(server, action: str, **body):
    return _request(server, "POST", "/api/materials/collections", {"action": action, "confirm": True, **body})


def test_t62_1_collections_crud_chain(server, _isolate_home) -> None:
    ids = []
    for i in range(3):
        _, r = _register(server, f"https://www.bilibili.com/video/BVc{i}")
        ids.append(r["material"]["id"])
    code, payload = _coll(server, "create", name="封面备选", color="jade")
    assert code == 200, payload
    cid = payload["collection"]["id"]
    assert _re.fullmatch(r"[0-9a-f]{12}", cid)
    assert payload["collection"]["member_count"] == 0
    code, payload = _coll(server, "add", id=cid, member_ids=ids)
    assert code == 200 and payload["collection"]["member_count"] == 3
    _, listing = _request(server, "GET", "/api/materials/collections")
    assert listing["collections"][0]["member_count"] == 3
    assert listing["collections"][0]["active_member_count"] == 3
    code, payload = _coll(server, "remove", id=cid, member_ids=[ids[0]])
    assert code == 200 and payload["collection"]["member_count"] == 2
    code, payload = _coll(server, "rename", id=cid, name="封面终选", color="amber")
    assert code == 200 and payload["collection"]["name"] == "封面终选"
    _, listing = _request(server, "GET", "/api/materials/collections")
    assert listing["collections"][0]["name"] == "封面终选"
    code, payload = _coll(server, "delete", id=cid)
    assert code == 200 and payload["deleted"] == cid and payload["member_count"] == 2
    _, listing = _request(server, "GET", "/api/materials/collections")
    assert listing["collections"] == []


def test_t62_2_tombstone_lazy_filter_no_rewrite(server, _isolate_home) -> None:
    _, r = _register(server, "https://www.bilibili.com/video/BVdel")
    mid = r["material"]["id"]
    _, c = _coll(server, "create", name="待删集", color="jade")
    cid = c["collection"]["id"]
    assert _coll(server, "add", id=cid, member_ids=[mid])[0] == 200
    path = _isolate_home / "materials" / "collections.json"
    before = path.read_bytes()
    assert _request(server, "POST", "/api/materials/delete", {"id": mid, "confirm": True})[0] == 200
    _, listing = _request(server, "GET", "/api/materials/collections")
    row = listing["collections"][0]
    assert row["member_count"] == 1 and row["active_member_count"] == 0  # 「n（含 1 已删）」数据面
    assert path.read_bytes() == before  # 墓碑不回写集合


def test_t62_3_delete_collection_keeps_materials(server, _isolate_home) -> None:
    _, r = _register(server, "https://www.bilibili.com/video/BVkeep")
    mid = r["material"]["id"]
    _, before = _request(server, "GET", "/api/materials")
    _, c = _coll(server, "create", name="临时集", color="slate")
    _coll(server, "add", id=c["collection"]["id"], member_ids=[mid])
    assert _coll(server, "delete", id=c["collection"]["id"])[0] == 200
    _, after = _request(server, "GET", "/api/materials")
    assert after["total"] == before["total"]
    assert mid in {m["id"] for m in after["items"]}  # 删集不删素材


def test_t62_4_collections_validation(server, _isolate_home) -> None:
    assert _coll(server, "create", name="x", color="pink")[0] == 400  # 色枚举外
    assert _coll(server, "create", name="x" * 25, color="jade")[0] == 400  # 名超 24
    assert _coll(server, "create", color="jade")[0] == 400  # 缺 name
    assert _coll(server, "nuke", id="x")[0] == 400  # action 枚举外
    assert _request(server, "POST", "/api/materials/collections",
                    {"action": "create", "name": "x", "color": "jade"})[0] == 400  # 缺 confirm
    assert _coll(server, "add", id="nope00000000", member_ids=["a" * 12])[0] == 400  # 集合不存在
    _, c = _coll(server, "create", name="v", color="jade")
    assert _coll(server, "add", id=c["collection"]["id"], member_ids=["ghost"])[0] == 400  # 素材不存在
    assert _request(server, "GET", "/api/materials/collections")[0] == 200  # GET/POST 双态


def test_t62_5_collection_filter_and_detail(server, _isolate_home) -> None:
    _, u1 = _upload(server, _png())
    _, u2 = _upload(server, _png(size=(30, 60)))  # 不同内容不同 id
    _, v = _register(server, "https://www.bilibili.com/video/BVm")
    img_ids = [u1["material"]["id"], u2["material"]["id"]]
    _, c1 = _coll(server, "create", name="集一", color="jade")
    _, c2 = _coll(server, "create", name="集二", color="amber")
    cid1, cid2 = c1["collection"]["id"], c2["collection"]["id"]
    assert _coll(server, "add", id=cid1, member_ids=img_ids + [v["material"]["id"]])[0] == 200
    assert _coll(server, "add", id=cid2, member_ids=[img_ids[0]])[0] == 200
    _, q = _request(server, "GET", f"/api/materials/collections={cid1}")  # 非法形态：无参污染
    _, q = _request(server, "GET", f"/api/materials?collection={cid1}&type=image")
    assert q["total"] == 2 and {m["id"] for m in q["items"]} == set(img_ids)  # AND 正交
    _, d = _request(server, "GET", f"/api/materials/detail?id={img_ids[0]}")
    assert len(d["material"]["collections"]) == 2  # 属 2 集 → detail.collections 长 2
    assert {c["id"] for c in d["material"]["collections"]} == {cid1, cid2}


# ── T63 标签系统 ────────────────────────────────────────────────────


def _triage(server, mid: str, **body):
    return _request(server, "POST", "/api/materials/triage", {"id": mid, "confirm": True, **body})


def test_t63_2_tags_full_replacement(server, _isolate_home) -> None:
    _, r = _register(server, "https://www.bilibili.com/video/BVtag")
    mid = r["material"]["id"]
    code, payload = _triage(server, mid, tags=["a", "b"])
    assert code == 200 and payload["triage"]["tags"] == ["a", "b"]
    code, payload = _triage(server, mid, tags=["a"])
    assert code == 200 and payload["triage"]["tags"] == ["a"]  # 全量替换非追加
    assert _triage(server, mid, tags="a")[0] == 400  # 非 list
    assert _triage(server, mid, tags=["x" * 25])[0] == 400  # 单标签 >24
    assert _triage(server, mid, tags=[f"t{i}" for i in range(13)])[0] == 400  # >12 个
    # 回读（查询面立即可见）
    _, q = _request(server, "GET", f"/api/materials?tag=a")
    assert q["total"] == 1 and q["items"][0]["triage"]["tags"] == ["a"]


def test_t63_3_narrow_gate_regression(server, _isolate_home) -> None:
    _, r = _register(server, "https://www.bilibili.com/video/BVgate")
    mid = r["material"]["id"]
    assert _triage(server, mid, payload={"text": "hack"})[0] == 400  # 窄门不宽到 payload
    assert _triage(server, mid, foo=1)[0] == 400  # 未知键仍拒
    code, payload = _triage(server, mid, starred=True, read=False)  # 三布尔旧形态照常（向后兼容）
    assert code == 200 and payload["triage"]["starred"] is True
    assert _triage(server, mid)[0] == 400  # 零可变键仍拒


def test_t63_4_tag_filter_orthogonal_legacy_rows(server, studio_root, _isolate_home) -> None:
    for i in range(2):
        _, r = _register(server, f"https://www.bilibili.com/video/BVf{i}")
        assert _triage(server, r["material"]["id"], tags=["t1", "t2"])[0] == 200
    _, q = _request(server, "GET", "/api/materials?tag=t1")
    assert q["total"] == 2
    _, r2 = _request(server, "GET", "/api/materials?tag=t1&starred=true")
    assert r2["total"] == 0  # AND：未星标
    # 存量无 tags 键旧 jsonl 行读侧补 []，查询/频次不崩
    legacy = model.make_material(
        mtype="text", source_id="kb", uri="legacy.md", title="旧行",
        payload={"text": "旧"}, fetched_at="2026-11-15T08:00:00",
    )
    ms._append({k: v for k, v in legacy.items() if k != "triage"} | {"triage": {"read": False, "starred": False, "enabled": True}})
    _, q = _request(server, "GET", "/api/materials?tag=t1")
    assert q["total"] == 2  # 旧行无 tags 不命中，读侧不崩
    _, q = _request(server, "GET", f"/api/materials?q={_quote('旧行')}")
    assert q["total"] == 1 and q["items"][0]["triage"]["tags"] == []


def test_t63_5_tags_frequency_table(server, _isolate_home) -> None:
    for i in range(3):
        _, r = _register(server, f"https://www.bilibili.com/video/BVn{i}")
        assert _triage(server, r["material"]["id"], tags=["t1", "t2"])[0] == 200
    _, t = _request(server, "GET", "/api/materials/tags")
    tags = t["tags"]
    assert {"name": "t1", "count": 3} in tags and {"name": "t2", "count": 3} in tags
    counts = [row["count"] for row in tags]
    assert counts == sorted(counts, reverse=True)  # 降序


# ── T64 跨模态搜索 ─────────────────────────────────────────────────


def test_t64_2_seven_types_hits_and_type_and(server, studio_root, _isolate_home) -> None:
    ms.add_material(model.make_material(
        mtype="text", source_id="kb", uri="a.md", title="文本素材",
        payload={"text": "端到端数据流正文"}, fetched_at="2026-11-15T10:00:00"))
    derived = model.make_material(
        mtype="derived", source_id="kb", uri="d.md", title="衍生素材标题",
        payload={"text": "加工产物"}, fetched_at="2026-11-15T11:00:00")
    ms.add_material(derived)
    ms.add_material(model.make_material(
        mtype="link", source_id="manual", uri="https://github.com/a/b", title="链接",
        payload={"url": "https://github.com/a/b"}, fetched_at="2026-11-15T12:00:00"))
    ms.add_material(model.make_material(
        mtype="crawled", source_id="manual", uri="https://x.example/p", title="剪藏",
        payload={"text": "剪藏正文关键词"}, fetched_at="2026-11-15T13:00:00"))
    ms.add_material(model.make_material(
        mtype="radar_signal", source_id="radarhub", uri="https://github.com/r/s", title="信号",
        payload={"url": "https://github.com/r/s", "meta": {"line": "embedded", "grade": "S", "score": 88}},
        fetched_at="2026-11-15T14:00:00"))
    _, up = _upload(server, _png())
    assert _triage(server, up["material"]["id"], tags=["封面图"])[0] == 200  # image：tags 域
    assert _register(server, "https://www.bilibili.com/video/BVq")[0] == 200  # video：platform 域
    hits = {
        "text": "端到端", "derived": "衍生素材标题", "image": "封面图",
        "video": "bilibili", "link": "github.com", "crawled": "剪藏正文关键词",
        "radar_signal": "line:embedded",
    }
    for mtype, q in hits.items():
        _, out = _request(server, "GET", f"/api/materials?q={_quote(q)}")
        assert out["total"] >= 1, (mtype, q, out)
        assert any(m["type"] == mtype for m in out["items"]), (mtype, q)
    _, out = _request(server, "GET", "/api/materials?q=bilibili&type=video")
    assert out["total"] == 1 and out["items"][0]["type"] == "video"  # q&type AND
    _, out = _request(server, "GET", "/api/materials?q=never-exists-word")
    assert out == {"items": [], "total": 0}


def test_t64_3_truncation_visibility(server, _isolate_home) -> None:
    for i in range(6):
        _, r = _register(server, f"https://www.bilibili.com/video/BVtr{i}")
        assert _triage(server, r["material"]["id"], tags=["批量"])[0] == 200
    _, out = _request(server, "GET", f"/api/materials?tag={_quote('批量')}&limit=5")
    assert len(out["items"]) == 5 and out["total"] == 6  # total 不随截断缩水
