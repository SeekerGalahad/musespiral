"""N-5 多平台并排预览（正典=《mdpub-优化重构与后续开发总体方案-2026-11-19》§二 N-5，
契约 §3.22）。

断言面（任务书验收面逐条对应）：
  a) 服务层清单：pack_inventory 六平台逐个给 `{platform, dir, available}`，
     available 以 payload.md 存在为准（目录在而 payload 缺 = 残包不可用）；
  b) 服务层预览：payload 头部字段（标题/描述/话题）与正文（--- 以下）解析
     正确；diff 红删绿增**真反映内容差异**（变体改写行进 ins、原文被换行进
     del），替换块配对行做**行内字符级精化**（批4 复核④「改一个字只高亮
     那个字」）；无变体直出的 pack 两侧 strip 后 identical（html 为一句提示）；
  c) 优雅降级：无包 → available:false + 「尚未适配」note（不抛错）；残包
     （缺 payload.md / 非 UTF-8）同形；platform 白名单外（含 `../` 穿越
     形态）→ PackPreviewError；文章 frontmatter 损坏 → PackPreviewError；
  d) 端点层（真实回环服务器）：双态（无 platform 只回清单 / 有则附 payload
     与 diff）、400 面（缺 path / 白名单外平台）、404（文章不存在）、
     残包 200 available:false、契约锁（CONTRACT_GET_EXACT + 实路由表）；
  e) 前端：SBS 纯函数（node 真实执行 ui.js 源标记区——构建产物剥注释，标记
     只在源）、结构锚（tmpl 平台适配 tab + 构建产物 article.js 函数与端点
     字面量）、零外联。

复用纪律对拍：diff 引擎与 E-6 同源（core/version/diff），payload 解析与
browser 侧填表同源（browser.flows.base.read_pack）——本文件含两处「不重造」
的实证断言。
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import pack_service
from musespiral.services.pack_service import (
    PACK_PLATFORM_CODES,
    PackPreviewError,
    pack_inventory,
    pack_preview,
)

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
UI_JS = STUDIO / "ui.js"
TMPL = STUDIO / "ui.tmpl.html"
ARTICLE_JS = STUDIO / "assets" / "views" / "article.js"

_ORIG = "原文第一段，讲一个完整的观点。\n原文第二段，收束上文。\n"
_VAR = "描述：小红书口播描述\n\n小红书第一段，完全不同的改写稿。\n原文第二段，收束上文。\n"


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


def _article(root: Path, name: str = "post.md", body: str = _ORIG) -> Path:
    path = root / name
    path.write_text(f"---\ntitle: 测试文章\n---\n\n{body}", encoding="utf-8")
    return path


def _xhs_pack(art: Path, body: str = _VAR, *, with_variant: bool = True) -> Path:
    """造一个 xhs 物料包（有变体=真差异；无变体=直出 identical）。"""
    variant = None
    if with_variant:
        variant = art.parent / "post.xhs.md"
        variant.write_text(
            f"---\ntitle: 小红书版\ntags: [测试, 旅行]\n---\n\n{body}",
            encoding="utf-8",
        )
    return pack_service.build_pack_for(art, "xhs", variant=variant)


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
    return resp.status, json.loads(data)


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


# ══ a) 服务层清单 ════════════════════════════════════════════════


def test_inventory_lists_all_pack_platforms(root: Path) -> None:
    """清单覆盖全部可成包平台（channels/xhs/zhihu/douyin/juejin/bilibili）；无包全 False。"""
    art = _article(root)
    items = pack_inventory(art)
    assert [i["platform"] for i in items] == list(PACK_PLATFORM_CODES)
    assert all(i["available"] is False and i["dir"] is None for i in items)


def test_inventory_marks_existing_pack(root: Path) -> None:
    """已建包的平台 available=True 且给目录名；其余平台不受影响。"""
    art = _article(root)
    d = _xhs_pack(art)
    items = {i["platform"]: i for i in pack_inventory(art)}
    assert items["xhs"]["available"] is True
    assert items["xhs"]["dir"] == d.name
    assert items["zhihu"]["available"] is False


def test_inventory_counts_broken_pack_as_unavailable(root: Path) -> None:
    """目录在而 payload.md 缺 = 残包：available=False（预览侧 note 同口径）。"""
    art = _article(root)
    d = _xhs_pack(art)
    (d / "payload.md").unlink()
    items = {i["platform"]: i for i in pack_inventory(art)}
    assert items["xhs"]["available"] is False and items["xhs"]["dir"] == d.name


# ══ b) 服务层预览：payload 解析 + diff 真差异 ════════════════════


def test_preview_parses_payload_header_and_body(root: Path) -> None:
    """payload 头部（标题/描述/话题）与 --- 以下正文各自正确。"""
    art = _article(root)
    _xhs_pack(art)
    p = pack_preview(art, "xhs")
    assert p["available"] is True and p["platform"] == "xhs"
    assert p["dir"] == "post.xhs.pack"
    assert p["payload"]["title"] == "小红书版"
    assert p["payload"]["desc"] == "小红书口播描述"
    assert p["payload"]["tags"] == ["测试", "旅行"]
    # 变体正文自带的「描述：」行现状语义=提取为头部但不从正文剥除（第五期 low
    # 修复口径：payload 头部描述优先取它，正文原样保留）
    assert "小红书第一段" in p["payload"]["body"]
    assert "描述：小红书口播描述" in p["payload"]["body"]
    assert "【逐字段对照】" not in p["payload"]["body"]  # 对照区不混进正文
    assert p["original"].startswith("原文第一段")  # frontmatter 不含


def test_preview_diff_reflects_real_content_changes(root: Path) -> None:
    """差异高亮真反映内容差异：改写行进 ins（竹绿）、被替换的原行进 del（绯红）、
    未变行不标记；hunks 非空、identical=False。"""
    art = _article(root)
    _xhs_pack(art)
    p = pack_preview(art, "xhs")
    diff = p["diff"]
    assert diff["identical"] is False
    assert diff["added"] > 0 and diff["removed"] > 0
    html = diff["html"]
    assert '<ins class="dl-add">小红书第一段，完全不同的改写稿。</ins>' in html
    assert '<del class="dl-del">原文第一段，讲一个完整的观点。</del>' in html
    # 未被改动的行以普通上下文行出现（不带 del/ins 标记）
    assert '<span class="ver-tx">原文第二段，收束上文。</span>' in html


def test_preview_diff_single_char_change_highlights_only_that_char(root: Path) -> None:
    """批4 复核④验收：「改一个字只高亮那个字」。

    中文散文段落=单行——行级整段红/绿在单字微调场景不可接受；diff 引擎的
    行内字符级精化（core/version/diff `_inline_body`，配对 del/ins 行按字符
    SequenceMatcher 拆段）经 pack_preview 全链（payload 解析→diff→HTML）
    端到端生效：实改字进标签、共同文本作普通片段。
    """
    art = _article(root, body="基本原理，是理解整段的上下文。\n")
    _xhs_pack(art, body="基本原裏，是理解整段的上下文。\n")
    p = pack_preview(art, "xhs")
    assert p["available"] is True and p["diff"]["identical"] is False
    html = p["diff"]["html"]
    assert '<del class="dl-del">理</del>' in html
    assert '<ins class="dl-add">裏</ins>' in html
    # 整段红/绿的旧行级形态不再出现；未变字符以普通文本呈现
    assert '<del class="dl-del">基本原理' not in html
    assert '<ins class="dl-add">基本原裏' not in html
    assert "，是理解整段的上下文。" in html


def test_preview_identical_for_pack_without_variant(root: Path) -> None:
    """无变体直出（pack 用原文正文）→ 两侧 strip 后 identical，html 为提示句。"""
    art = _article(root)
    _xhs_pack(art, with_variant=False)
    p = pack_preview(art, "xhs")
    assert p["available"] is True
    assert p["diff"]["identical"] is True
    assert p["diff"]["hunks"] == []
    assert "两版内容相同" in p["diff"]["html"]
    assert p["payload"]["body"].strip() == _ORIG.strip()


def test_preview_diff_escapes_untrusted_body(root: Path) -> None:
    """正文是不可信输入：变体里的 <script> 在 diff 里只以转义文本出现。"""
    art = _article(root)
    _xhs_pack(art, body="<script>alert(1)</script>\n第二行\n")
    p = pack_preview(art, "xhs")
    html = p["diff"]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html


# ══ c) 优雅降级与拒绝面 ═════════════════════════════════════════


def test_preview_without_pack_is_graceful(root: Path) -> None:
    """无包不是错误：available=False + 「尚未适配」note，payload/diff 为空。"""
    art = _article(root)
    p = pack_preview(art, "zhihu")
    assert p["available"] is False
    assert "尚未适配" in p["note"]
    assert p["payload"] is None and p["diff"] is None


def test_preview_broken_pack_notes_respecific(root: Path) -> None:
    """缺 payload.md 的残包 → available=False + 可读 note（不抛 FileNotFoundError）。"""
    art = _article(root)
    d = _xhs_pack(art)
    (d / "payload.md").unlink()
    p = pack_preview(art, "xhs")
    assert p["available"] is False and "payload.md" in p["note"]


def test_preview_non_utf8_payload_is_graceful(root: Path) -> None:
    """payload.md 坏字节 → available=False + UTF-8 note（不崩、不吐 U+FFFD 伪文）。"""
    art = _article(root)
    d = _xhs_pack(art)
    (d / "payload.md").write_bytes(b"\xff\xfe\x00broken")
    p = pack_preview(art, "xhs")
    assert p["available"] is False and "UTF-8" in p["note"]


def test_preview_rejects_platform_outside_whitelist(root: Path) -> None:
    """白名单外平台（含 ../ 穿越、mp 官方平台、空串）→ PackPreviewError。"""
    art = _article(root)
    for bad in ("../evil", "mp", "", "xhs.pack/../../other"):
        with pytest.raises(PackPreviewError):
            pack_preview(art, bad)


def test_preview_rejects_broken_article(root: Path) -> None:
    """文章 frontmatter 损坏 → PackPreviewError 可读拒绝（不 500）。

    pack 目录手造（build_pack_for 也要 parse 文章，会在夹具期先炸）。"""
    art = root / "bad.md"
    art.write_text("---\n: : :\nbroken\n---\n\n正文\n", encoding="utf-8")
    d = art.parent / "bad.xhs.pack"
    d.mkdir()
    (d / "payload.md").write_text("> 【逐字段对照】\n\n---\n正文\n", encoding="utf-8")
    with pytest.raises(PackPreviewError) as err:
        pack_preview(art, "xhs")
    assert "文章解析失败" in str(err.value)


# ══ d) 端点层（真实回环）═════════════════════════════════════════


def test_get_pack_two_modes(server, root: Path) -> None:
    """GET 双态：无 platform 只回清单；有则附 payload + diff html。"""
    art = _article(root)
    _xhs_pack(art)

    code, d = _request(server, "GET", "/api/article/pack?path=post.md")
    assert code == 200 and d["article"] == "post.md"
    packs = {x["platform"]: x for x in d["packs"]}
    assert packs["xhs"]["available"] is True
    assert packs["douyin"]["available"] is False

    code, d = _request(server, "GET", "/api/article/pack?path=post.md&platform=xhs")
    assert code == 200 and d["available"] is True
    assert d["payload"]["title"] == "小红书版"
    assert '<ins class="dl-add">小红书第一段，完全不同的改写稿。</ins>' in d["diff"]["html"]


def test_get_pack_missing_pack_is_200_available_false(server, root: Path) -> None:
    """切到无包平台 = 200 available:false（前端「尚未适配」提示面，不 40x）。"""
    _article(root)
    code, d = _request(server, "GET", "/api/article/pack?path=post.md&platform=douyin")
    assert code == 200
    assert d["available"] is False and "尚未适配" in d["note"]
    assert d["payload"] is None and d["diff"] is None


def test_get_pack_400_surface(server, root: Path) -> None:
    """400 面：缺 path / 白名单外平台（含穿越形态）。"""
    _article(root)
    for qs in ("", "?path=post.md&platform=../evil", "?path=post.md&platform=mp"):
        code, d = _request(server, "GET", "/api/article/pack" + qs)
        assert code == 400, (qs, code, d)
        assert d["ok"] is False


def test_get_pack_404_for_missing_article(server, root: Path) -> None:
    """不存在的文章 → 404（与 /api/article 族同口径）。"""
    code, _d = _request(server, "GET", "/api/article/pack?path=nope.md")
    assert code == 404
    code, _d = _request(server, "GET", "/api/article/pack?path=nope.md&platform=xhs")
    assert code == 404


def test_pack_endpoint_follows_root_boundary(server, root: Path) -> None:
    """path 越界（../ / 绝对路径）→ 400（_resolve_file 边界，与 /api/article 族同款）。"""
    _article(root)
    for qs in ("?path=../outside.md", "?path=/etc/passwd"):
        code, _d = _request(server, "GET", "/api/article/pack" + qs)
        assert code == 400, qs


def test_contract_locks_include_pack_preview() -> None:
    """GET /api/article/pack 在契约集且路由表实装；POST 面零新增不破 62。"""
    from musespiral.studio.routing import _GET_ROUTES
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    assert "/api/article/pack" in CONTRACT_GET_EXACT
    assert "/api/article/pack" in set(_GET_ROUTES)
    # 复用对拍：POST 零新增——PACK_PLATFORM_CODES 与 build_pack 白名单同源
    assert set(PACK_PLATFORM_CODES) == set(pack_service._PLATFORM_GUIDES)


# ══ e) 前端 ══════════════════════════════════════════════════════


def _run_node(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环仍随全量跑）")
    # 标记区只存在于 ui.js 源（构建产物剥块注释；versions.js 先例为独立文件
    # 不经压缩，故从产物抽；@view:article 块经压缩，须从开发态正典抽）。
    src = UI_JS.read_text(encoding="utf-8")
    begin, end = "/*__SBS_BEGIN__*/", "/*__SBS_END__*/"
    pure = src.split(begin, 1)[1].split(end, 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.js"
        path.write_text(pure + "\n" + frag, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_sbs_js_pure_functions() -> None:
    """node 真实执行：下拉项（manual 全集 + 有包 ✓ 标）/ 转义 / meta chips / 空态。"""
    out = _run_node(r"""
const platforms=[
  {code:'xhs',name:'小红书'},
  {code:'channels',name:'视频号'},
  {code:'douyin',name:'抖音'}];
console.log(JSON.stringify({
  opts: sbsOptions(platforms, {xhs:true}),
  optsNoPack: sbsOptions(platforms, null),
  optsEmpty: sbsOptions([], {}),
  esc: sbsEsc('<a>&"'),
  meta: sbsMeta({title:'T1', desc:'D1', tags:['a','b']}),
  metaNone: sbsMeta(null),
  metaPartial: sbsMeta({title:'', desc:'', tags:[]}),
  note: sbsEmptyNote('还没有物料包')
}));
""")
    opts = out["opts"]
    assert '<option value="xhs">小红书（xhs） ✓</option>' in opts
    assert '<option value="channels">视频号（channels）</option>' in opts  # 无包不标 ✓
    assert '<option value="douyin">抖音（douyin）</option>' in opts
    assert out["optsNoPack"] == (
        '<option value="xhs">小红书（xhs）</option>'
        '<option value="channels">视频号（channels）</option>'
        '<option value="douyin">抖音（douyin）</option>'
    )
    assert out["optsEmpty"] == ""
    assert out["esc"] == "&lt;a&gt;&amp;&quot;"
    assert '<span class="tag">标题 T1</span>' in out["meta"]
    assert '<span class="tag">描述 D1</span>' in out["meta"]
    assert '<span class="tag accent">#a #b</span>' in out["meta"]
    assert out["metaNone"] == "" and out["metaPartial"] == ""
    assert "尚未适配" in out["note"] and "还没有物料包" in out["note"]


def test_sbs_js_structure_and_loader() -> None:
    """结构锚：article 视图骨架的平台适配 tab（原「变体」改名；ARCH-1 起骨架源
    在 ui.views/article.html，壳内为懒注入占位）+ panel-variants；
    构建产物 article.js 含 SBS 函数与端点字面量（压缩剥注释不剥函数）。"""
    tmpl = (STUDIO / "ui.views" / "article.html").read_text(encoding="utf-8")
    assert 'data-tab="variants"' in tmpl
    assert "平台适配" in tmpl  # tab 标签（N-5 改名）
    assert 'id="panel-variants"' in tmpl
    built = ARTICLE_JS.read_text(encoding="utf-8")
    for anchor in (
        "function sbsMount", "function sbsEnsure", "function sbsRender",
        "function sbsOptions", "id=\"sbs-grid\"", "id=\"sbs-left\"", "id=\"sbs-right\"",
        "id=\"sbs-diff\"", "id=\"sbs-platform\"",
    ):
        assert anchor in built, anchor
    assert "/api/article/pack" in built  # 前端消费端点（契约 §3.22）
    assert 'data-panel="variants"' in tmpl


def test_sbs_js_no_external_refs() -> None:
    """零外联：views/article.js 源只用相对 /api 路径，无 http(s)/协议相对引用。"""
    src = ARTICLE_JS.read_text(encoding="utf-8")
    assert "http://" not in src and "https://" not in src
    assert "/api/article/pack" in src


def test_shell_anchor_untouched_by_sbs() -> None:
    """壳零增量纪律：并排区骨架由 JS 动态注入——壳模板不得出现 sbs 结构 id。"""
    tmpl = TMPL.read_text(encoding="utf-8")
    for anchor in ("sbs-grid", "sbs-left", "sbs-right", "sbs-diff", "sbs-platform"):
        assert f'id="{anchor}"' not in tmpl, f"{anchor} 应由 JS 注入，不进壳（49,152B 硬锚）"
