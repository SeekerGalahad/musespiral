"""B3 多媒体与视频发布：/media/ 静态路由 + 本地视频登记元数据 + 插入媒体。

正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§三（原型段③）。三族覆盖（任务书 B3 门）：

  - /media/<rel> 静态路由安全面：恶意路径 ≥3 用例（../ 穿越 / 绝对路径 /
    白名单外后缀）+ 正常取图（root 域）+ 素材仓域（materials/ 前缀 poster）
    + materials/ 前缀越界（敏感文件双门）；
  - register_local_video 元数据纪律：文件落仓（内容寻址幂等）+
    ffprobe/ffmpeg 缺席/失败**如实置空不报错**（monkeypatch 替身，确定性）；
  - insert_media（编辑器「插入媒体」服务面）：素材文件复制进文章 media/
    返回 root 相对引用；URL 登记视频（无文件面）→ 400 可读指引。

:::video 块的 parser/renderer 锚与物料包断言在 tests/test_video_pack.py；
editor.js 纯函数锚在 tests/test_editor_ui.py（edVideoMd/edDurFmt）。
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra import media_store
from musespiral.services.materials import upload as materials_upload

# ── 隔离与服务器（test_studio_ui_wiring 同款：root 与 home 分离的两域）──


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    (root / "img").mkdir(parents=True)
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


def _req(server, method: str, path: str, body: bytes | dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if isinstance(body, dict):
        body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if body is not None:
        headers["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = raw
    conn.close()
    return resp.status, dict(resp.getheaders()), data


def _real_png() -> bytes:
    """Pillow 可验的真实 PNG（上传链 inspect_image 解码器级校验要求）。"""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 8), (200, 30, 40)).save(buf, "PNG")
    return buf.getvalue()


_PNG = _real_png()
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 48  # ftyp 头（本地登记不做魔数门）


# ── /media/ 静态路由：安全门 + 正常取图 ──────────────────────────


def test_media_route_normal_image_from_root(server, studio_root: Path) -> None:
    """正常取图：root 域白名单扩展名 200 + Content-Type 按后缀真值。"""
    (studio_root / "img" / "a.png").write_bytes(_PNG)
    status, headers, body = _req(server, "GET", "/media/img/a.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == _PNG


def test_media_route_serves_material_store_poster(server, studio_root: Path) -> None:
    """素材仓域：materials/ 前缀（poster 首帧）经 home 基根服务——抽屉/卡片消费面。"""
    poster_dir = media_store.files_dir()
    poster_dir.mkdir(parents=True, exist_ok=True)
    (poster_dir / "abc123.poster.jpg").write_bytes(b"\xff\xd8\xff poster")
    status, headers, body = _req(server, "GET", "/media/materials/files/abc123.poster.jpg")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert body == b"\xff\xd8\xff poster"


def test_media_route_rejects_parent_traversal(server, studio_root: Path, tmp_path: Path) -> None:
    """恶意路径 ①：../ 穿越到 root 外（白名单后缀真穿越，不是被后缀门碰巧拦下）。"""
    (tmp_path / "evil.png").write_bytes(_PNG)  # root 外的同后缀文件
    status, _, _ = _req(server, "GET", "/media/../evil.png")
    assert status == 400  # _resolve_inside：resolve 后越界 → 400


def test_media_route_rejects_absolute_path(server) -> None:
    """恶意路径 ②：绝对路径（含 //etc 形态——urlparse 吃掉首 / 后成 /etc/…）。"""
    status, _, _ = _req(server, "GET", "/media//etc/hosts")
    assert status == 400
    status2, _, _ = _req(server, "GET", "/media/etc/hosts")
    assert status2 == 404  # 相对形态落在 root 内但后缀不在白名单


def test_media_route_rejects_non_whitelisted_suffix(server, studio_root: Path) -> None:
    """恶意路径 ③：白名单外后缀（.py/.json/.svg 恒拒——svg 同源脚本执行面）。"""
    (studio_root / "img" / "a.py").write_text("print(1)")
    status, _, _ = _req(server, "GET", "/media/img/a.py")
    assert status == 404
    (studio_root / "img" / "a.svg").write_text("<svg/>")
    status, _, _ = _req(server, "GET", "/media/img/a.svg")
    assert status == 404


def test_media_route_materials_prefix_cannot_escape(
    server, tmp_path: Path, _isolate_home: Path
) -> None:
    """materials/ 前缀越界：敏感文件（credentials.json 形态）出不了
    「materials/ 子树 + 白名单」双门——home 根下的文件即便伪装媒体后缀也拒。"""
    (_isolate_home / "credentials.json").write_text('{"k":"v"}')
    (_isolate_home / "fake.png").write_bytes(_PNG)  # home 根（非 materials/ 子树）的媒体后缀
    status, _, _ = _req(server, "GET", "/media/materials/../../credentials.json")
    assert status in (400, 404)  # resolve 后越界（根域先拒）或后缀门先拒
    status2, _, _ = _req(server, "GET", "/media/materials/../fake.png")
    assert status2 == 400  # resolve=home/fake.png 不在 materials 子树 → 400


def test_media_route_get_only_and_missing(server) -> None:
    """POST /media/ → 405（只读旁路）；不存在 → 404（不 500）。"""
    status, _, _ = _req(server, "POST", "/media/img/a.png")
    assert status == 405
    status2, _, _ = _req(server, "GET", "/media/img/nope.png")
    assert status2 == 404


# ── register_local_video：元数据纪律（ffprobe/ffmpeg 缺席如实置空）──────


def _fake_video(tmp_path: Path) -> Path:
    p = tmp_path / "lec.mp4"
    p.write_bytes(_MP4)
    return p


@pytest.fixture
def _probe_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测替身：ffprobe/ffmpeg 缺席——probe 契约面返回如实空值（内部捕获
    已在 probe 真身实现，本替身替换整个函数故走返回值形态）；本机装了
    ffmpeg 也不影响确定性。"""

    def _empty(*a, **k):
        return {"duration": None, "poster": None}

    monkeypatch.setattr(media_store, "probe_video_meta", _empty)


def test_register_local_video_honest_empty_meta(
    tmp_path: Path, _isolate_home: Path, _probe_disabled
) -> None:
    """本地视频登记：文件落仓 + meta 含 bytes/sha256；duration/poster 缺席
    **不写键**（前端按缺省处理）——不报错、不写假值。"""
    src = _fake_video(tmp_path)
    result = materials_upload.register_local_video(str(src), title="讲座")
    m = result["material"]
    assert result["dedup"] is False
    assert m["type"] == "video"
    assert m["origin"]["title"] == "讲座"
    meta = m["payload"]["meta"]
    assert meta["bytes"] == len(_MP4)
    assert len(meta["sha256"]) == 64
    assert "duration" not in meta and "poster" not in meta  # 如实空：键都不落
    # 文件真实落仓（硬链接或复制）且 local_path 指向它
    stored = _isolate_home / m["payload"]["local_path"]
    assert stored.is_file() and stored.read_bytes() == _MP4


def test_register_local_video_dedup_and_gates(
    tmp_path: Path, _isolate_home: Path, _probe_disabled
) -> None:
    """同文件重登记幂等（零追加）；非白名单后缀 / 相对路径 / 不存在 → ValueError。"""
    src = _fake_video(tmp_path)
    r1 = materials_upload.register_local_video(str(src))
    r2 = materials_upload.register_local_video(str(src))
    assert r2["dedup"] is True and r2["material"]["id"] == r1["material"]["id"]
    with pytest.raises(ValueError):
        materials_upload.register_local_video(str(tmp_path / "a.avi"))
    with pytest.raises(ValueError):
        materials_upload.register_local_video("lec.mp4")  # 相对路径歧义拒
    with pytest.raises(FileNotFoundError):
        materials_upload.register_local_video(str(tmp_path / "nope.mp4"))


def test_register_local_video_probed_meta(
    tmp_path: Path, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测成功路径（替身回真值）：duration float 落 meta、poster 落仓内相对路径。"""
    monkeypatch.setattr(
        media_store,
        "probe_video_meta",
        lambda mid, p: {"duration": 513.2, "poster": media_store.poster_rel_path(mid)},
    )
    src = _fake_video(tmp_path)
    result = materials_upload.register_local_video(str(src))
    meta = result["material"]["payload"]["meta"]
    assert meta["duration"] == 513.2
    assert meta["poster"] == f"materials/files/{result['material']['id']}.poster.jpg"


def test_register_local_endpoint_shape(server, tmp_path: Path, _probe_disabled) -> None:
    """POST /api/materials/register-local：confirm 闸门 + 404（文件不存在）可读面。"""
    status, _, data = _req(
        server,
        "POST",
        "/api/materials/register-local",
        {"path": str(tmp_path / "nope.mp4"), "confirm": True},
    )
    assert status == 404 and data["ok"] is False
    status2, _, _ = _req(server, "POST", "/api/materials/register-local", {"path": "/x.mp4"})
    assert status2 == 400  # 缺 confirm（server 层人审闸门）


# ── insert_media：编辑器「插入媒体」服务面 ──────────────────────────


def _register_image(studio_root: Path) -> str:
    (studio_root / "up.png").write_bytes(_PNG)
    result = materials_upload.add_uploaded_image(_PNG, name="up.png")
    return result["material"]["id"]


def _register_local_video_id(tmp_path: Path) -> str:
    src = _fake_video(tmp_path)
    return materials_upload.register_local_video(str(src), title="lec")["material"]["id"]


def test_insert_media_image_copies_into_article_media(
    server, studio_root: Path, tmp_path: Path
) -> None:
    """图片：素材文件复制进 <article_dir>/media/<id>.png，返回 root 相对引用。"""
    mid = _register_image(studio_root)
    article = studio_root / "post.md"
    article.write_text("---\ntitle: t\n---\n\n正文\n", encoding="utf-8")
    status, _, data = _req(
        server,
        "POST",
        "/api/action/insert_media",
        {"id": mid, "path": "post.md", "confirm": True},
    )
    assert status == 200 and data["ok"] is True
    assert data["kind"] == "image"
    assert data["rel"] == f"media/{mid}.png"
    assert (studio_root / "media" / f"{mid}.png").read_bytes() == _PNG
    # 幂等：重插同 id 覆盖同内容（内容寻址，无增殖）
    status2, _, data2 = _req(
        server,
        "POST",
        "/api/action/insert_media",
        {"id": mid, "path": "post.md", "confirm": True},
    )
    assert status2 == 200 and data2["rel"] == data["rel"]


def test_insert_media_video_with_poster(
    server,
    studio_root: Path,
    tmp_path: Path,
    _isolate_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """视频：本体与 poster 首帧都复制；响应带 kind/rel/poster_rel/duration。"""
    monkeypatch.setattr(
        media_store,
        "probe_video_meta",
        lambda mid, p: {"duration": 42.0, "poster": media_store.poster_rel_path(mid)},
    )
    # 探测替身只回路径不落文件——手动落 poster 文件模拟 ffmpeg 产物
    src = _fake_video(tmp_path)
    mid = materials_upload.register_local_video(str(src))["material"]["id"]
    poster = _isolate_home / "materials" / "files" / f"{mid}.poster.jpg"
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"\xff\xd8\xff poster")

    article = studio_root / "vpost.md"
    article.write_text("---\ntitle: v\n---\n\n正文\n", encoding="utf-8")
    status, _, data = _req(
        server,
        "POST",
        "/api/action/insert_media",
        {"id": mid, "path": "vpost.md", "confirm": True},
    )
    assert status == 200
    assert data["kind"] == "video"
    assert data["rel"] == f"media/{mid}.mp4"
    assert data["poster_rel"] == f"media/{mid}.poster.jpg"
    assert data["duration"] == 42.0
    assert (studio_root / "media" / f"{mid}.mp4").is_file()
    assert (studio_root / "media" / f"{mid}.poster.jpg").read_bytes() == b"\xff\xd8\xff poster"


def test_insert_media_rejects_url_registered_video(server, studio_root: Path) -> None:
    """URL 登记视频（无文件面）→ 400 可读指引（改本地登记）；未知 id → 404。"""
    materials_upload.register_video("https://www.bilibili.com/video/BV1xx411c7mD")
    url_mid = None
    # 取刚登记的 video id（register_video 返回带 id）
    from musespiral.services.materials import crud as _crud

    for row in reversed(_crud.list_materials()):
        if row.get("type") == "video" and (row.get("payload") or {}).get("url"):
            url_mid = row["id"]
            break
    assert url_mid is not None
    article = studio_root / "post.md"
    article.write_text("---\ntitle: t\n---\n\nx\n", encoding="utf-8")
    status, _, data = _req(
        server,
        "POST",
        "/api/action/insert_media",
        {"id": url_mid, "path": "post.md", "confirm": True},
    )
    assert status == 400 and "本地视频登记" in data["error"]
    status2, _, _ = _req(
        server,
        "POST",
        "/api/action/insert_media",
        {"id": "deadbeef0000", "path": "post.md", "confirm": True},
    )
    assert status2 == 404


# ── 文章预览：:::video 渲染 + src/poster 重写为 /media/<rel> ──────────


def test_article_html_rewrites_video_block_sources(server, studio_root: Path) -> None:
    """/api/article/html：:::video 块渲染为 <figure class="md-video"><video
    controls>，本地 src/poster 重写为 /media/<root 相对>（图片族仍走 /asset）。"""
    (studio_root / "assets").mkdir()
    (studio_root / "assets" / "lec.mp4").write_bytes(_MP4)
    (studio_root / "assets" / "f1.jpg").write_bytes(b"\xff\xd8\xff fake")
    article = studio_root / "v.md"
    article.write_text(
        "---\ntitle: 视频\n---\n\n"
        ':::video{src="assets/lec.mp4" poster="assets/f1.jpg"}\n题注\n:::\n',
        encoding="utf-8",
    )
    status, _, body = _req(server, "GET", f"/api/article/html?path={article.name}")
    assert status == 200
    html = body if isinstance(body, str) else body.decode("utf-8")
    assert '<figure class="md-video"><video controls' in html
    assert 'src="/media/assets/lec.mp4"' in html
    assert 'poster="/media/assets/f1.jpg"' in html
    assert "<figcaption>题注</figcaption>" in html


# ── 前端两处（任务书 5）：素材视图缩略/徽章 + 发布中心媒体徽章/清单 ──────
# 结构锚（构建产物不剥函数体，符号与字面量都在）；行为面在 test_video_pack
# 的 mdToHtml node 断言与 test_editor_ui 的 edVideoMd/edDurFmt。

_STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
_VIEWS = _STUDIO / "assets" / "views"


def test_material_view_video_thumbnail_and_duration_badge() -> None:
    """素材列表（material-core.js）：video 卡有 poster 时首帧缩略（/media/ 素材仓
    域）+ 失败回退类型图标；类型徽章有 duration 时出 ⏱ 时长（fmtDur）；
    抽屉（material-drawer.js）内嵌 <video controls> + poster + /media/ src。"""
    core = (_VIEWS / "material-core.js").read_text(encoding="utf-8")
    assert "meta.poster" in core and "'/media/'+meta.poster" in core
    assert "fmtDur(meta.duration)" in core and "⏱" in core
    assert "function fmtDur(s){" in core
    drawer = (_VIEWS / "material-drawer.js").read_text(encoding="utf-8")
    assert '<video class="drawer-media" controls preload="metadata"' in drawer
    assert "esc('/media/'+p.local_path)" in drawer
    assert 'poster="' in drawer  # poster 属性（本地登记视频带首帧时）


def test_publish_card_media_badges_and_pack_media_list() -> None:
    """发布中心（publish.js）：文章卡媒体徽章（🎬 视频文章 / 🖼 含图 N 张，数据面
    = /api/articles 的 media 键）；article 视图（article.js）打包预览列媒体清单
    与体积（sbsMedia）。"""
    pub = (_VIEWS / "publish.js").read_text(encoding="utf-8")
    assert "md.video" in pub and "qArt('video','视频文章')" in pub
    assert "md.images" in pub and "qArt('image','含图 '" in pub
    art = (_VIEWS / "article.js").read_text(encoding="utf-8")
    assert "function sbsMedia(media){" in art and "sbsBytes(x.bytes)" in art
    assert "媒体清单：" in art and "d.media" in art


def test_articles_payload_media_key(server, studio_root: Path) -> None:
    """GET /api/articles：media 键（video 布尔 + images 计数）——徽章数据面。"""
    (studio_root / "v.md").write_text(
        '---\ntitle: 视频文章\n---\n\n![](a.png)\n\n:::video{src="x.mp4"}\n:::\n',
        encoding="utf-8",
    )
    (studio_root / "plain.md").write_text("---\ntitle: 普通\n---\n\n正文\n", encoding="utf-8")
    status, _, data = _req(server, "GET", "/api/articles")
    assert status == 200
    by = {a["path"]: a for a in data["articles"]}
    assert by["v.md"]["media"] == {"video": True, "images": 1}
    assert by["plain.md"]["media"] == {"video": False, "images": 0}


def test_editor_insert_media_literal_has_backend_route() -> None:
    """editor.js 的 /api/action/insert_media 字面量必须被 _ACTION_ROUTES 接住
    （editor.js 不在 ui.js 字面量锁的扫描域内——本门补该域的「新调的没接」）。"""
    from musespiral.studio.actions import _ACTION_ROUTES

    src = (_STUDIO / "assets" / "editor.js").read_text(encoding="utf-8")
    assert "'/api/action/insert_media'" in src
    assert "/api/action/insert_media" in _ACTION_ROUTES
    # 素材选取读面沿用既有 /api/materials（type= 过滤），零新 GET
    assert "'/api/materials?type='" in src


def test_media_route_cjk_and_space_filenames(server, studio_root: Path) -> None:
    """URL 编码防护面（任务书 §3.4「URL 编码防护」）：CJK/空格文件名 percent
    编码后服务端 unquote 命中真文件（文章目录可以是中文名）。"""
    from urllib.parse import quote

    (studio_root / "素材 图.png").write_bytes(_PNG)
    status, headers, body = _req(server, "GET", "/media/" + quote("素材 图.png"))
    assert status == 200 and body == _PNG
    assert headers["Content-Type"] == "image/png"
