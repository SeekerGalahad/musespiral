"""文档上传全链集成测试（2026-11-20 格式生态线 DOC-1）。

三段联通逐段实测（不 mock 提取逻辑——真素材判例「静默失败最难查」）：
  1. 上传分流：add_uploaded() 按后缀把文档送 doc-extract.run()、图片仍走原路径；
  2. 落库：提取 text 落 type=text 素材（payload.text 与 upload://sha256 uri），
     幂等重传零追加，扫描版 PDF 可读拒绝（400 语义 ValueError）；
  3. 下游：落库素材的 payload.text 经 refine_service.preview() 真消费
     （fake chat 断言注入正文来自文档提取，而非空文/引用元数据）。

真实文件样本：PDF/DOCX 取仓内专利文档真实字节（find 实测存在），MD 用
utf-8 真文件；提取全程走 doc-extract 插件 run() 的 pdfplumber/python-docx，
零字符串 mock。MUSESPIRAL_HOME monkeypatch tmp 隔离，不触真实素材库/外网。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import materials_service as ms
from musespiral.services import refine_service as refine

REPO = Path(__file__).resolve().parents[3]
REAL_PDF = (
    REPO / "docs" / "patents"
    / "CN111966692B_针对数据仓库的数据处理方法、介质、装置和计算设备.pdf"
)
REAL_DOCX = (
    REPO / "docs" / "patents"
    / "基于大数据处理的电池工艺制程追溯方法_专利申请文件.docx"
)
REAL_EPUB = (
    REPO / "docs" / "pdf" / "金融经济" / "开放教材"
    / "CORE-The-Economy-2.0-Macroeconomics.epub"
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _pdf_bytes() -> bytes:
    assert REAL_PDF.is_file(), f"真实 PDF 样本缺席: {REAL_PDF}"
    return REAL_PDF.read_bytes()


def _docx_bytes() -> bytes:
    assert REAL_DOCX.is_file(), f"真实 DOCX 样本缺席: {REAL_DOCX}"
    return REAL_DOCX.read_bytes()


def _lines(home: Path) -> list[dict]:
    p = home / "materials" / "materials.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── 1) 上传分流 + 落库 ────────────────────────────────────────────────


def test_upload_real_pdf_lands_text_material(_isolate_home: Path) -> None:
    res = ms.add_uploaded(_pdf_bytes(), name="血缘专利.pdf")
    mat = res["material"]
    assert res["dedup"] is False
    assert mat["type"] == "text"  # 文档按正文素材落库（refine 可消费）
    assert mat["origin"]["source_id"] == "upload"
    assert mat["origin"]["uri"].startswith("upload://sha256:")
    text = mat["payload"]["text"]
    assert len(text) > 10_000          # 真 PDF 提取量级（实测 30,265）
    assert "国家知识产权局" in text
    assert mat["payload"]["meta"]["pages"] == 32
    assert _lines(_isolate_home)  # 真落库


def test_upload_real_docx_lands_text_material(_isolate_home: Path) -> None:
    res = ms.add_uploaded(_docx_bytes(), name="电池工艺.docx")
    mat = res["material"]
    assert mat["type"] == "text"
    assert len(mat["payload"]["text"]) > 500
    assert mat["payload"]["meta"]["suffix"] == ".docx"
    assert "pages" not in mat["payload"]["meta"]  # DOCX 无页概念诚实留空（不伪造）


def test_upload_real_epub_lands_text_material(_isolate_home: Path) -> None:
    assert REAL_EPUB.is_file(), f"真实 EPUB 样本缺席: {REAL_EPUB}"
    res = ms.add_uploaded(REAL_EPUB.read_bytes(), name="宏观经济学.epub")
    mat = res["material"]
    assert mat["type"] == "text"
    assert len(mat["payload"]["text"]) > 10_000  # 真书级文本量（实测 ~2M）
    assert mat["payload"]["meta"]["pages"] > 1    # EPUB 章节数


def test_upload_markdown_passthrough(_isolate_home: Path) -> None:
    data = "# 标题\n\n正文段落足够长不触发空门，供下游提炼消费。".encode("utf-8")
    res = ms.add_uploaded(data, name="note.md")
    assert "正文段落足够长" in res["material"]["payload"]["text"]


def test_upload_idempotent_zero_append(_isolate_home: Path) -> None:
    data = "# 标题\n\n正文段落足够长不触发空门。".encode("utf-8")
    a = ms.add_uploaded(data, name="note.txt")
    b = ms.add_uploaded(data, name="另起名.txt")  # 内容同 → 同 sha256 → 同 id
    assert b["dedup"] is True
    assert b["material"]["id"] == a["material"]["id"]
    assert len(_lines(_isolate_home)) == 1  # 零追加


def test_upload_rejects_scanned_pdf_readable(_isolate_home: Path) -> None:
    # 扫描版模拟（内容流零文本算子）——复用 doc-extract 测试同款最小合法 PDF
    from test_plugins_builtin_doc_extract import _scan_pdf_bytes

    with pytest.raises(ValueError) as ei:
        ms.add_uploaded(_scan_pdf_bytes(), name="scan.pdf")
    msg = str(ei.value)
    assert "扫描版" in msg and "OCR" in msg  # 可读拒绝，非静默空文


def test_upload_image_still_uses_image_path(_isolate_home: Path) -> None:
    # 回归：非文档后缀仍走图片全链（Pillow 真图字节，魔数门过）
    import io

    from PIL import Image as _Image

    buf = io.BytesIO()
    _Image.new("RGB", (30, 20), (10, 20, 30)).save(buf, "PNG")
    res = ms.add_uploaded(buf.getvalue(), name="pic.png")
    assert res["material"]["type"] == "image"
    assert res["thumb_ready"] is True


# ── 2) 服务端二进制旁路分流 ───────────────────────────────────────────


def test_server_upload_document_endpoint(_isolate_home: Path, tmp_path: Path) -> None:
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        data = "# 标题\n\n正文段落足够长不触发空门，经服务端旁路上传。".encode("utf-8")
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(
            "POST", "/api/materials/upload?confirm=1&name=doc.md", body=data,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        assert resp.status == 200, payload
        assert payload["ok"] is True
        assert payload["material"]["type"] == "text"
        assert "正文段落足够长" in payload["material"]["payload"]["text"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


# ── 3) 下游 refine.preview() 真消费文档正文 ───────────────────────────


def test_refine_preview_consumes_uploaded_document_text(
    _isolate_home: Path, monkeypatch
) -> None:
    # 配置本地无 key 档案（refine 路由命中）+ fake chat 捕获注入正文
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_chat(messages, *, task=None, **kwargs):
        calls.append(messages[0]["content"])
        return "· 摘要草稿一\n· 摘要草稿二"

    monkeypatch.setattr(refine, "_chat", fake_chat)

    res = ms.add_uploaded(_pdf_bytes(), name="血缘专利.pdf")
    out = refine.preview(res["material"]["id"])

    assert calls, "preview 应真调用 chat"
    injected = calls[0]
    # 关键：注入的是文档提取正文（国家知识产权局等真内容），不是空文/路径元数据
    assert "国家知识产权局" in injected
    assert out["text"] == "· 摘要草稿一\n· 摘要草稿二"
    assert out["id"] == res["material"]["id"]
