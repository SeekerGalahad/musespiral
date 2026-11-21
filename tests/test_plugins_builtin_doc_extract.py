"""doc-extract 插件测试（2026-11-20 格式生态线）：真实文件提取、扫描版拒绝、缺依赖容错。

核心不变量（本插件的存在意义——真素材判例「静默失败最难查」）：
1. 三格式真实文件提取：PDF/DOCX/EPUB 各有真实文件全链验证（PDF 用仓内
   真实专利样本 + 最小合法 PDF 夹具；DOCX 用 python-docx 真实生成；EPUB
   用 ebooklib 真实写出）——**不 mock 提取逻辑本身**；
2. 扫描版 PDF **可读拒绝**（含「扫描版/OCR」字样的 PluginError），绝不
   静默返回空文——空文会被下游 refine 当成「文档无内容」；
3. 四个解析库全部可选依赖：全缺席时插件模块 import 正常（不让整包崩）、
   运行给可读 MISSING_DEP 指引、MD/TXT 标准库路径不受影响；
4. 下游联通：text 端口是纯 str，塞进 material payload.text 后
   material_body.material_text（refine 的公共抽取面）可直接消费。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from musespiral.core.plugins.context import PluginCancelled, PluginError
from musespiral.core.plugins.manifest import expected_dirname, parse_manifest

PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent
    / "musespiral" / "plugins_builtin" / "doc-extract"
)
PLUGIN_PATH = PLUGIN_DIR / "plugin.py"

# 仓内真实文字版 PDF 样本（专利公开文本，32 页中文）——库外真实文件，
# 缺席（如浅 checkout）则跳过该用例，不造假样本
REAL_PDF = (
    Path(__file__).resolve().parents[3]
    / "docs" / "patents"
    / "CN111966692B_针对数据仓库的数据处理方法、介质、装置和计算设备.pdf"
)


def _load(name: str = "doc_extract_plugin") -> Any:
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


docx_extract = _load()


class _Ctx:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.logs: list[str] = []
        self.progress_calls: list[tuple[int, int]] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        self.progress_calls.append((done, total))


# ── 真实最小 PDF 构造（合法 PDF 语法 + xref；pdfplumber/pdfminer 可解析）──


def _build_pdf(content: bytes) -> bytes:
    """单页最小 PDF：catalog/pages/page/content/font 五对象，偏移真实计算。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


def _text_pdf_bytes() -> bytes:
    """文字版：Helvetica 真文本算子（有效字符 >50/页阈值）。"""
    line = b"Doc extract sample sentence for the text page fixture. "
    content = b"".join(
        b"BT /F1 12 Tf 72 %d Td (%s) Tj ET" % (720 - i * 24, line) for i in range(4)
    )
    return _build_pdf(content)


def _scan_pdf_bytes() -> bytes:
    """扫描版模拟：内容流只有画线算子、零文本算子——提取 0 字符（真实
    文件走真实 pdfminer 解析，不 mock extract_text）。"""
    return _build_pdf(b"0 0 0 rg 1 w 72 72 m 500 500 l S")


@pytest.fixture()
def text_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "text.pdf"
    p.write_bytes(_text_pdf_bytes())
    return p


@pytest.fixture()
def scan_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "scan.pdf"
    p.write_bytes(_scan_pdf_bytes())
    return p


@pytest.fixture()
def real_docx(tmp_path: Path) -> Path:
    """python-docx **真实生成**的 .docx：标题样式段落 + 普通段落 + 表格 +
    尾部段落（验证 body 交错顺序保真——doc.paragraphs/doc.tables 分离清单
    会丢序，本插件按 w:p/w:tbl 遍历）。"""
    docx_mod = docx_extract._import_docx()
    doc = docx_mod.Document()
    doc.add_heading("第一章 概述", level=1)
    doc.add_paragraph("这是第一段正文。")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "名称"
    table.rows[0].cells[1].text = "数值"
    table.rows[1].cells[0].text = "重试"
    table.rows[1].cells[1].text = "3次"
    doc.add_paragraph("表格之后的收尾段落。")
    p = tmp_path / "real.docx"
    doc.save(str(p))
    return p


@pytest.fixture()
def real_epub(tmp_path: Path) -> Path:
    """**纯标准库构造**的真实 EPUB：封面（纯图页）+ 两章。

    为什么不用 ebooklib 构造（2026-11-21 许可证审查判例）：ebooklib 是
    AGPL-3.0，即便只用于测试也会让「本项目依赖 AGPL」这一事实成立——
    商业开源要彻底干净，测试依赖同样不能留。
    改用 zipfile + 手写 container.xml/OPF/xhtml，与真实 EPUB 结构完全一致。
    """
    import zipfile

    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">doc-extract-test</dc:identifier>'
        '<dc:title>测试书</dc:title><dc:language>zh</dc:language></metadata>'
        '<manifest>'
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest>'
        '<spine><itemref idref="cover"/><itemref idref="c1"/><itemref idref="c2"/></spine>'
        '</package>'
    )
    # 封面：纯图页（零文本）——提取层应跳过，不制造空章
    cover = '<html><body><img src="images/cover.jpg" alt="cover"/></body></html>'
    ch1 = ('<html><body><h1>第一章</h1>'
           '<p>第一章正文内容，足够多的字符。</p></body></html>')
    # 第二章带 script：验证 script 整块剥除（否则 evil() 会污染正文）
    ch2 = ('<html><body><h2>第二章</h2><script>evil()</script>'
           '<p>第二章正文内容，足够多的字符。</p></body></html>')

    p = tmp_path / "real.epub"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/cover.xhtml", cover)
        zf.writestr("OEBPS/ch1.xhtml", ch1)
        zf.writestr("OEBPS/ch2.xhtml", ch2)
    return p


# ── manifest 契约 ────────────────────────────────────────────────────


def test_manifest_parse_and_dirname() -> None:
    import json

    data = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    plugin, errors = parse_manifest(data)
    assert plugin is not None, errors
    assert plugin.id == "doc-extract"
    assert plugin.kind == "process"
    assert plugin.runtime.type == "python"
    assert expected_dirname(plugin.id) == PLUGIN_DIR.name
    # 规格三端口 + 后缀白名单六后缀
    assert set(plugin.outputs) == {"markdown", "text", "pages"}
    assert plugin.outputs["markdown"].type == "text/markdown"
    assert tuple(sorted(docx_extract.ALLOWED_SUFFIXES)) == (
        ".docx", ".epub", ".markdown", ".md", ".pdf", ".txt",
    )


def test_python_deps_declared() -> None:
    from musespiral.infra import plugins_loader as loader

    deps = loader.declared_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, PLUGIN_DIR
    )
    # 2026-11-21 许可证审查：ebooklib（AGPL-3.0）与 bs4 已移除——
    # EPUB 改纯标准库解析（zipfile + ElementTree），零第三方依赖。
    assert deps == ["pdfplumber", "docx"]


# ── PDF：文字版提取 + 扫描版可读拒绝 ────────────────────────────────


def test_pdf_text_extraction(text_pdf: Path) -> None:
    ctx = _Ctx()
    out = docx_extract.run({"doc_path": str(text_pdf)}, {}, ctx)
    assert set(out) >= {"markdown", "text", "pages"}
    assert out["pages"] == 1
    # 有效字符 > 50/页阈值（fixture 四行样板句）
    assert len(out["text"].strip()) > 50
    assert "Doc extract sample sentence" in out["text"]
    assert out["text"] == out["markdown"].split("\n\n", 1)[1]  # markdown=标题+正文


def test_pdf_real_sample() -> None:
    """仓内真实专利 PDF（32 页中文文字版）：非空提取、页数正确。"""
    if not REAL_PDF.is_file():
        pytest.skip(f"仓内真实 PDF 样本缺席: {REAL_PDF}")
    ctx = _Ctx()
    out = docx_extract.run({"doc_path": str(REAL_PDF), "title": "血缘专利"}, {}, ctx)
    assert out["pages"] == 32
    assert len(out["text"]) > 10_000  # 真实文本量级（实测 30,265）
    assert "国家知识产权局" in out["text"]
    assert out["markdown"].startswith("# 血缘专利")


def test_scanned_pdf_rejected_not_silent(scan_pdf: Path) -> None:
    """扫描版（0 字符/页）必须可读拒绝——含「扫描版」与「OCR」的可读
    PluginError，绝不静默返回空文。"""
    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(scan_pdf)}, {}, _Ctx())
    msg = str(ei.value)
    assert "扫描版" in msg and "OCR" in msg
    assert "50" in msg  # 阈值出现在消息里（可解释）


def test_scanned_threshold_zero_still_no_silent_empty(scan_pdf: Path) -> None:
    """阈值调 0 放行扫描门后，统一空结果门仍拒绝——任何路径都不静默返回空文。"""
    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(scan_pdf)}, {"min_chars_per_page": 0}, _Ctx())
    msg = str(ei.value)
    assert "扫描版" not in msg  # 扫描门被 0 阈值放行（诊断不误报）
    assert "为空" in msg        # 统一空结果门兜底——绝不静默返回空文


# ── DOCX：段落/表格保真 ─────────────────────────────────────────────


def test_docx_extraction_order_and_table(real_docx: Path) -> None:
    ctx = _Ctx()
    out = docx_extract.run({"doc_path": str(real_docx), "title": "测试文档"}, {}, ctx)
    assert out["pages"] is None  # DOCX 无页概念，诚实留空不伪造
    md = out["markdown"]
    # 标题样式 → #
    assert "# 第一章 概述" in md
    # 表格 → markdown 表格（含表头与分隔行）
    assert "| 名称 | 数值 |" in md
    assert "| --- | --- |" in md
    assert "| 重试 | 3次 |" in md
    # body 顺序：标题 → 首段 → 表格 → 尾段（分离清单遍历会丢这个序）
    i_head = md.index("# 第一章 概述")
    i_para1 = md.index("这是第一段正文。")
    i_table = md.index("| 名称 | 数值 |")
    i_tail = md.index("表格之后的收尾段落。")
    assert i_head < i_para1 < i_table < i_tail


# ── EPUB：spine 顺序 + 章节标题 + script 剥除 + 空页跳过 ────────────


def test_epub_extraction_chapters(real_epub: Path) -> None:
    ctx = _Ctx()
    out = docx_extract.run({"doc_path": str(real_epub)}, {}, ctx)
    md = out["markdown"]
    # 封面（空文本）跳过不计章节；两章标题落 ##
    assert out["pages"] == 2
    assert "## 第一章" in md and "## 第二章" in md
    # script 剥除：evil() 不出现在正文
    assert "evil()" not in md
    # 章节标题从正文剥离（防首行重复）
    first_chap = md.split("## 第一章", 1)[1]
    assert first_chap.lstrip("\n").startswith("第一章正文内容")


# ── MD/TXT：直读与拒绝 ──────────────────────────────────────────────


def test_md_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# 标题\n\n正文段落。", encoding="utf-8")
    out = docx_extract.run({"doc_path": str(p)}, {}, _Ctx())
    assert "正文段落。" in out["text"]
    assert out["pages"] is None
    # title 缺省取文件名词干
    assert out["markdown"].startswith("# note")


def test_txt_bom_and_gbk_rejected(tmp_path: Path) -> None:
    bom = tmp_path / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbfBOM \xe5\x86\x85\xe5\xae\xb9")  # utf-8 BOM + 内容
    out = docx_extract.run({"doc_path": str(bom)}, {}, _Ctx())
    assert "BOM" in out["text"] and "\ufeff" not in out["text"]

    gbk = tmp_path / "gbk.txt"
    gbk.write_bytes("中文内容测试".encode("gb18030"))
    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(gbk)}, {}, _Ctx())
    assert "UTF-8" in str(ei.value)


def test_empty_and_missing_and_suffix_gates(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n ", encoding="utf-8")
    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(empty)}, {}, _Ctx())
    assert "为空" in str(ei.value)

    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(tmp_path / "nope.pdf")}, {}, _Ctx())
    assert "不存在" in str(ei.value)

    bad = tmp_path / "x.xyz"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(PluginError) as ei:
        docx_extract.run({"doc_path": str(bad)}, {}, _Ctx())
    assert "白名单" in str(ei.value)

    # 大小写不敏感：.PDF 合法
    up = tmp_path / "up.PDF"
    up.write_bytes(_text_pdf_bytes())
    out = docx_extract.run({"doc_path": str(up)}, {}, _Ctx())
    assert out["pages"] == 1


# ── 可选依赖：全缺席 → 加载正常 + 可读指引 + 标准库路径不受影响 ────


def test_missing_deps_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = {}
    for name in ("pdfplumber", "docx"):
        saved[name] = sys.modules.pop(name, None)
        monkeypatch.setitem(sys.modules, name, None)  # import → ImportError
    try:
        # 模块本身加载正常（不让整包 import 失败——edge-tts 纪律）
        mod = _load("doc_extract_plugin_nodeps")
        # PDF 路径可读指引
        p = tmp_path / "t.pdf"
        p.write_bytes(_text_pdf_bytes())
        with pytest.raises(PluginError) as ei:
            mod.run({"doc_path": str(p)}, {}, _Ctx())
        assert "pdfplumber 未安装" in str(ei.value)
        assert "pip install pdfplumber" in str(ei.value)
        # DOCX / EPUB 同族可读指引
        d = tmp_path / "t.docx"
        d.write_bytes(b"PK\x03\x04")
        with pytest.raises(PluginError) as ei:
            mod.run({"doc_path": str(d)}, {}, _Ctx())
        assert "python-docx 未安装" in str(ei.value)
        # EPUB：2026-11-21 许可证审查后改为**纯标准库解析**（零依赖）——
        # 缺 pdfplumber/docx 不影响 EPUB。此处用坏包验证「可读拒绝不崩」，
        # 而非验证「缺依赖提示」（它已不需要任何依赖）。
        e = tmp_path / "t.epub"
        e.write_bytes(b"PK\x03\x04not-a-real-zip")
        with pytest.raises(PluginError) as ei:
            mod.run({"doc_path": str(e)}, {}, _Ctx())
        assert "EPUB" in str(ei.value)  # 可读拒绝，不静默、不抛裸 zipfile 异常
        # MD/TXT 标准库路径不受缺依赖影响
        m = tmp_path / "t.md"
        m.write_text("# 标题\n\n正文足够长不触发空门。", encoding="utf-8")
        out = mod.run({"doc_path": str(m)}, {}, _Ctx())
        assert "正文足够长" in out["text"]
    finally:
        for name, v in saved.items():
            if v is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = v


# ── 取消与产物可序列化 ──────────────────────────────────────────────


def test_cancel_checkpoint(text_pdf: Path) -> None:
    with pytest.raises(PluginCancelled):
        docx_extract.run({"doc_path": str(text_pdf)}, {}, _Ctx(cancelled=True))


def test_outputs_json_serializable(text_pdf: Path, real_docx: Path) -> None:
    for path in (text_pdf, real_docx):
        out = docx_extract.run({"doc_path": str(path)}, {}, _Ctx())
        json.dumps(out, ensure_ascii=False)  # 引擎 inline 路径要求


# ── 下游联通：text → material payload.text → refine 公共抽取面 ─────


def test_downstream_refine_material_body(text_pdf: Path) -> None:
    from musespiral.services import material_body

    out = docx_extract.run({"doc_path": str(text_pdf), "title": "样本"}, {}, _Ctx())
    material = {
        "id": "test-doc-extract",
        "type": "text",
        "origin": {"title": "样本"},
        "payload": {"text": out["text"]},
    }
    body = material_body.material_text(material, max_chars=20_000)
    assert "Doc extract sample sentence" in body
