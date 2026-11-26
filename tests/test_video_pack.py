"""B3 视频物料包（channels/douyin/xhs）+ :::video parser/renderer 渲染锚。

正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§三/§3.2。覆盖（任务书 B3 门「物料包目录/时间点提取/README 生成」+「编辑器
:::video 渲染锚」的 parser/renderer 半边——editor.js 纯函数半边在
tests/test_editor_ui.py）：

  - build_pack 视频物料三件套：video.mp4（硬链接或复制）/ poster.jpg /
    meta.json（标题/话题/章节时间点）+ README 视频上传步骤与诚实边界；
  - 章节时间点提取：[mm:ss](#t=秒) 锚 → {t, ts, label}（label=锚上方最近
    标题——时间戳开讲的小节语义）；
  - channels 适配器 validate 扩：media.kind=video 时视频源在位校验
    （FAIL 缺源 / WARN 仅 frontmatter / PASS 双入口）；
  - douyin 封面尺寸检查（profiles cover_w×cover_h ±25% → WARN）；
  - xhs/douyin 同包结构；普通（非视频）文章零视频物料零回归；
  - pack_preview 媒体清单（video.mp4/poster.jpg/meta.json 真实体积）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.content.parser import (
    article_media_info,
    extract_video_blocks,
    parse_article,
)
from musespiral.core.content.renderer import Renderer
from musespiral.platforms import registry
from musespiral.services.publish import pack_service

_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 48


@pytest.fixture(autouse=True)
def _no_ffmpeg_first_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """poster 首帧兜底替身：ffmpeg 视为缺席——测试不依赖本机 ffmpeg，
    poster 走显式引用路径（首帧兜底路径的真实行为另由 _poster_first_frame
    纯环境分支覆盖：which()=None → False 不写文件）。"""
    monkeypatch.setattr(pack_service, "_poster_first_frame", lambda v, d: False)


def _video_article(tmp_path: Path, *, with_block: bool = True) -> Path:
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "assets" / "lec.mp4").write_bytes(_MP4)
    (tmp_path / "assets" / "f1.jpg").write_bytes(b"\xff\xd8\xff fake")
    body = (
        "---\n"
        "title: 多头注意力讲解\n"
        "tags: [深度学习, 注意力]\n"
        "media:\n"
        "  kind: video\n"
        "  src: assets/lec.mp4\n"
        "  poster: assets/f1.jpg\n"
        "  duration: 513\n"
        "---\n\n"
        "## 多头注意力的并行扩展\n\n"
        "[00:03:35](#t=215) 上一页我们学会了…\n\n"
        "[00:10:02](#t=602) 实战里先看头部数。\n\n"
    )
    if with_block:
        body += (
            ':::video{src="assets/lec.mp4" poster="assets/f1.jpg"}\n'
            "多头注意力的并行扩展\n"
            ":::\n\n"
        )
    body += "## 收尾\n\n无视频引用的普通段落。\n"
    p = tmp_path / "video.md"
    p.write_text(body, encoding="utf-8")
    return p


# ── 物料包目录结构（任务书：目录断言）─────────────────────────────


def test_channels_pack_video_structure(tmp_path: Path) -> None:
    """channels 视频物料包：四件套 + 视频三件套（video.mp4/poster.jpg/
    meta.json）齐备；video.mp4 与源同字节。"""
    article = _video_article(tmp_path)
    out = pack_service.build_pack(article, "channels")
    names = sorted(x.name for x in out.iterdir())
    assert "video.mp4" in names and "poster.jpg" in names and "meta.json" in names
    assert "README.md" in names and "payload.md" in names and "checks.txt" in names
    assert (out / "video.mp4").read_bytes() == _MP4
    assert (out / "poster.jpg").read_bytes() == b"\xff\xd8\xff fake"


def test_pack_meta_json_fields_and_chapters(tmp_path: Path) -> None:
    """meta.json：标题/话题/描述兜底/duration/poster/视频源 + 章节时间点
    （[mm:ss](#t=秒) 锚提取；label=锚上方最近标题行）。"""
    article = _video_article(tmp_path)
    out = pack_service.build_pack(article, "channels")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["platform"] == "channels"
    assert meta["title"] == "多头注意力讲解"
    assert meta["tags"] == ["深度学习", "注意力"]
    assert meta["video"] == "video.mp4"
    assert meta["poster"] == "poster.jpg"
    assert meta["duration"] == 513
    assert meta["src"] == "assets/lec.mp4"
    chapters = meta["chapters"]
    assert [c["t"] for c in chapters] == [215, 602]
    assert [c["ts"] for c in chapters] == ["00:03:35", "00:10:02"]
    # 两锚同属「多头注意力的并行扩展」小节（上方最近标题——时间戳开讲该节）
    assert all(c["label"] == "多头注意力的并行扩展" for c in chapters)


def test_pack_readme_honest_manual_upload(tmp_path: Path) -> None:
    """README：视频上传步骤 + 诚实边界（视频号无公开草稿 API——导出后
    人工上传，绝不假装自动发布）+ 章节时间点表。"""
    article = _video_article(tmp_path)
    out = pack_service.build_pack(article, "channels")
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "视频上传步骤" in readme
    assert "不自动上传" in readme and "人工上传" in readme
    assert "| 00:03:35 | `215` |" in readme  # 章节表：时间 | 秒 | 章节
    assert "video.mp4" in readme


def test_xhs_douyin_same_pack_structure(tmp_path: Path) -> None:
    """xhs/douyin 同包结构（任务书「xhs/douyin 同包结构」）：视频三件套
    同样落包；douyin 的 checks.txt 含视频物料行。"""
    article = _video_article(tmp_path)
    for platform in ("xhs", "douyin"):
        out = pack_service.build_pack(article, platform)
        names = sorted(x.name for x in out.iterdir())
        assert {"video.mp4", "poster.jpg", "meta.json"} <= set(names)
    checks = (
        json.loads("null")
        if False
        else ((article.parent / "video.douyin.pack" / "checks.txt").read_text(encoding="utf-8"))
    )
    assert "ok 视频物料: video.mp4" in checks


def test_douyin_cover_size_check(tmp_path: Path) -> None:
    """douyin 封面尺寸检查：poster.jpg 尺寸读 PIL 真值——合规 ok / 偏离
    WARN（±25% 容差，profiles cover_w×cover_h=1080×1440）。"""
    import io

    from PIL import Image

    article = _video_article(tmp_path)
    # 偏离 3:4（如 1600×900=16:9）→ WARN
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (1, 2, 3)).save(buf, "JPEG")
    (tmp_path / "assets" / "f1.jpg").write_bytes(buf.getvalue())
    out = pack_service.build_pack(article, "douyin")
    checks = (out / "checks.txt").read_text(encoding="utf-8")
    assert "WARN 抖音封面" in checks
    # 合规 3:4（1080×1440）→ ok
    buf2 = io.BytesIO()
    Image.new("RGB", (1080, 1440), (4, 5, 6)).save(buf2, "JPEG")
    (tmp_path / "assets" / "f1.jpg").write_bytes(buf2.getvalue())
    out2 = pack_service.build_pack(article, "douyin", outdir=tmp_path / "ok.pack")
    checks2 = (out2 / "checks.txt").read_text(encoding="utf-8")
    assert "ok 抖音封面" in checks2


def test_plain_article_pack_has_no_video_assets(tmp_path: Path) -> None:
    """普通（非视频）文章：零视频物料零回归——四件套照旧、无
    video.mp4/meta.json（poster 首帧兜底也缺席——ffmpeg 替身 None）。"""
    p = tmp_path / "plain.md"
    p.write_text(
        "---\ntitle: 普通图文\ntags: [随笔]\n---\n\n正文一段。\n",
        encoding="utf-8",
    )
    out = pack_service.build_pack(p, "channels")
    names = sorted(x.name for x in out.iterdir())
    assert "video.mp4" not in names and "meta.json" not in names
    assert {"README.md", "payload.md", "checks.txt"} <= set(names)


def test_missing_video_source_raises_readable(tmp_path: Path) -> None:
    """声明视频但文件不存在 → FileNotFoundError（拒绝静默产出残包）。"""
    article = _video_article(tmp_path)
    (tmp_path / "assets" / "lec.mp4").unlink()
    with pytest.raises(FileNotFoundError):
        pack_service.build_pack(article, "channels")


# ── channels 适配器 validate 扩（任务书：validate 要求视频源在位）────────


def test_channels_validate_video_source_gates(tmp_path: Path) -> None:
    """media.kind=video 三态：双入口 PASS / 仅 frontmatter WARN /
    缺源 FAIL；非视频文章校验输出与基线一致（零回归）。"""
    adapter = registry.get("channels")

    both = parse_article(_video_article(tmp_path))
    levels = [lv for lv, _ in adapter.validate(both)]
    assert "FAIL" not in levels
    assert any("视频源在位" in msg for _, msg in adapter.validate(both))

    # 仅 frontmatter（正文无 :::video 块）→ WARN
    fm_only = _video_article(tmp_path, with_block=False)
    art = parse_article(fm_only)
    warns = [msg for lv, msg in adapter.validate(art) if lv == "WARN"]
    assert any("正文无 :::video 块" in m for m in warns)

    # 缺源（kind=video 声明了但 src 空 + 正文无块）→ FAIL
    p3 = tmp_path / "nosrc.md"
    p3.write_text(
        "---\ntitle: 缺源视频文章\nmedia:\n  kind: video\n---\n\n只有文字。\n",
        encoding="utf-8",
    )
    fails = [msg for lv, msg in adapter.validate(parse_article(p3)) if lv == "FAIL"]
    assert any("缺视频源" in m for m in fails)

    # 非视频文章：xhs/douyin/manual_pack 家族零回归（标题门照旧）
    plain = tmp_path / "plain2.md"
    plain.write_text("---\ntitle: 普通图文\n---\n\n正文。\n", encoding="utf-8")
    res = adapter.validate(parse_article(plain))
    assert res == [("PASS", "标题字数合规: 4")]


# ── pack_preview 媒体清单（发布中心打包预览数据面）─────────────────


def test_pack_preview_media_inventory(tmp_path: Path) -> None:
    """pack_preview：media 键=固定名三件 + images-cards/ 的真实体积清单；
    无视频物料的普通包 media 为空列表。"""
    article = _video_article(tmp_path)
    pack_service.build_pack(article, "channels")
    preview = pack_service.pack_preview(article, "channels")
    assert preview["available"] is True
    media = {m["file"]: m["bytes"] for m in preview["media"]}
    assert "video.mp4" in media and media["video.mp4"] == len(_MP4)
    assert "poster.jpg" in media and "meta.json" in media

    plain = tmp_path / "p.md"
    plain.write_text("---\ntitle: p\n---\n\nx\n", encoding="utf-8")
    pack_service.build_pack(plain, "channels", outdir=tmp_path / "p.pack")
    preview2 = pack_service.pack_preview(plain, "channels")
    assert preview2["media"] == []


# ── :::video parser/renderer 渲染锚（编辑器插入的服务端对拍面）──────────


def test_video_block_parser_and_renderer(tmp_path: Path) -> None:
    """:::video 容器：parser 保序切段（video 块与文本块交替）+ 属性/题注
    解析；renderer 产 <figure class="md-video"><video controls …>；未闭合
    容器按普通文本回退（不吞正文不报错）。"""
    p = tmp_path / "v.md"
    p.write_text(
        "---\ntitle: 视频文章\n---\n\n"
        "## 第一节\n\n导语段落。\n\n"
        ':::video{src="a.mp4" poster="b.jpg"}\n题注一行\n:::\n\n'
        "收尾段落。\n",
        encoding="utf-8",
    )
    article = parse_article(p)
    assert [b["type"] for b in article.blocks] == [
        "heading",
        "paragraph",
        "video",
        "paragraph",
    ]
    video = article.blocks[2]
    assert video["src"] == "a.mp4" and video["poster"] == "b.jpg"
    assert video["caption"] == "题注一行"
    # raw_body 原样保留（保存/回写零改动——B3 插入块不破坏编辑器数据面）
    assert ":::video" in article.raw_body

    html = Renderer().render(article)
    assert '<figure class="md-video"><video controls src="a.mp4" poster="b.jpg"></video>' in html
    assert "<figcaption>题注一行</figcaption></figure>" in html

    # extract_video_blocks（pack/徽章共用探测面）与 article_media_info
    blocks = extract_video_blocks(article.raw_body)
    assert len(blocks) == 1 and blocks[0]["src"] == "a.mp4"
    assert article_media_info(article) == {"video": True, "images": 0}

    # 未闭合：整段回退普通文本
    p2 = tmp_path / "unclosed.md"
    p2.write_text(
        '---\ntitle: u\n---\n\n:::video{src="x.mp4"}\n这行不该被吞\n',
        encoding="utf-8",
    )
    a2 = parse_article(p2)
    assert all(b["type"] != "video" for b in a2.blocks)
    assert "这行不该被吞" in a2.raw_body


def test_video_renderer_escapes_attribute_values(tmp_path: Path) -> None:
    """XSS 锚：src/poster/题注含引号与尖括号 → html_escape 后再进属性位
    （预览 iframe 同源文档不可被注入）。"""
    p = tmp_path / "x.md"
    p.write_text(
        '---\ntitle: x\n---\n\n:::video{src="a\\" onload=\\"evil() src2" poster="<b>"}\n'
        "<script>alert(1)</script>\n:::\n",
        encoding="utf-8",
    )
    article = parse_article(p)
    html = Renderer().render(article)
    assert "<script>" not in html and "onload=" not in html
    assert "<figcaption>&lt;script&gt;" in html


# ── 编辑器前端 :::video 渲染锚（node 真实执行 ui.js 的 mdToHtml 段）──────
# 编辑器实时预览与物料包并排预览共用 ui.js 的 mdToHtml——B3 让它在行循环里
# 识别 :::video 容器（与服务端 parser/renderer 同一形态），产
# <figure class="md-video"><video controls src="/media/…">；块分割 mdBlocks
# 同步加容器分支（滚动同步的块数对齐前提）。

_UI_JS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.js"


def _run_md_node(frag: str) -> dict:
    import json as _json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构锚仍随全量跑）")
    src = _UI_JS.read_text(encoding="utf-8")
    # 段 = safeSrc…mdToHtml（BANCHOR/MVID_* 常量与两渲染函数同处，紧邻 toast 前）
    # + mdBlocks（article 视图块内，与 mdToHtml 同分割规则——滚动同步的块数对齐）
    start = src.index("function safeSrc(u,dir,img){")
    end = src.index("function toast(message,kind,action){")
    bstart = src.index("function mdBlocks(md){")
    bend = src.index("function syncPreview(){")
    pure = src[start:end] + "\n" + src[bstart:bend]
    script = (
        "'use strict';\n"
        "function esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,function(c){"
        "return{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});}\n"
        + pure
        + "\nmodule.exports={mdToHtml:mdToHtml,mdBlocks:mdBlocks,vidAttr:vidAttr,vidSrc:vidSrc};\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "md_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 断言失败:\n{proc.stdout}\n{proc.stderr}"
    return _json.loads(proc.stdout.strip().splitlines()[-1])


def test_editor_preview_renders_video_block() -> None:
    """mdToHtml：:::video 容器 → <figure class="md-video">；本地 src/poster
    重写为 /media/<dir 拼路径>；外链原样；题注 figcaption；未闭合不吞正文；
    块分割 mdBlocks 容器算单块（与 mdToHtml 块数对齐）。"""
    out = _run_md_node(r"""
var md='## 节\n\n导语。\n\n:::video{src="media/a.mp4" poster="media/a.poster.jpg"}\n题注\n:::\n\n收尾。\n';
var html=mdToHtml(md,'art/');
var ext=mdToHtml(':::video{src="https://cdn.x/a.mp4"}\n:::\n','');
var unclosed=mdToHtml(':::video{src="x.mp4"}\n这行保留\n','');
var blocks=mdBlocks(md);
var plain=mdToHtml('普通段落 ![i](p.png)\n','');
console.log(JSON.stringify({html:html,ext:ext,unclosed:unclosed,blocks:blocks,plain:plain,
  attr:vidAttr('src="a b.mp4" poster=c.jpg'),src:vidSrc('media/a.mp4','art/')}));
""")
    html = out["html"]
    assert '<figure class="md-video"><video controls src="/media/art/media/a.mp4"' in html
    assert 'poster="/media/art/media/a.poster.jpg"' in html
    assert "<figcaption>题注</figcaption></figure>" in html
    assert "<h2>节</h2>" in html and "<p>导语。</p>" in html  # 段前正文照常渲染
    assert "<p>收尾。</p>" in html  # 容器后正文继续
    assert 'src="https://cdn.x/a.mp4"' in out["ext"]  # 外链原样（零外联门不管动态 URL）
    assert "这行保留" in out["unclosed"]  # 未闭合容器不吞正文
    # 块分割：标题/段落/video 块/段落 = 4 块（容器是单块——滚动同步对齐）
    assert len(out["blocks"]) == 4, out["blocks"]
    # 普通图片仍走 /asset（两族互不干扰）
    assert '<img alt="i" src="/asset?path=p.png">' in out["plain"]
    assert out["attr"] == {"src": "a b.mp4", "poster": "c.jpg"}
    assert out["src"] == "/media/art/media/a.mp4"


def test_asset_bounds_extended_to_video_sources(tmp_path: Path) -> None:
    """越界门扩视频（B3）：:::video 的 src 指向 root 外 → 动作层 400 拒绝
    （build_pack 会把 video.mp4 复制进包=泄漏 root 外文件，与图片同红线）。"""
    import http.client
    import json
    import threading

    from musespiral import studio

    root = tmp_path / "articles"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"\x00\x00\x00\x18ftypmp42 outside")
    p = root / "v.md"
    p.write_text(
        "---\ntitle: 越界视频\n---\n\n" ':::video{src="../outside.mp4"}\n:::\n',
        encoding="utf-8",
    )
    httpd = studio.make_server(root, port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"path": "v.md", "platform": "channels", "confirm": True}).encode()
        conn.request(
            "POST", "/api/action/pack", body=body, headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        assert resp.status == 400
        assert "越界" in data["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=2)


def test_pack_overwrite_flag_d10(tmp_path: Path) -> None:
    """D10 修复锚：overwrite=True 删旧重建；缺省仍拒绝（安全默认不变）。"""
    from musespiral.services.publish import pack_service
    import pytest

    article = _video_article(tmp_path)
    d1 = pack_service.build_pack_for(article, "channels")
    assert (d1 / "README.md").is_file()
    with pytest.raises(FileExistsError):
        pack_service.build_pack_for(article, "channels")
    d2 = pack_service.build_pack_for(article, "channels", overwrite=True)
    assert d2 == d1 and (d2 / "README.md").is_file()
