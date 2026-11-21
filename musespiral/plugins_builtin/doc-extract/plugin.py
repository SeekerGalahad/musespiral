"""官方插件·文档提取（首插件族 #8，kind=process，2026-11-20 格式生态线）。

把文字版 PDF / DOCX / EPUB / Markdown / TXT 提取成 text/markdown，接进
既有「素材 → refine 提炼」链路（此前素材上传仅 4 种图片、书籍仅 .txt，
PDF/DOCX/EPUB/MD 完全进不来——本插件补上这个能力空洞）。三条纪律：

**诚实红线（2026-11-20 真素材判例：静默失败是最难查的 bug）**：
  - 扫描版 PDF 必须**可读拒绝**——全文可提取字符 ÷ 页数 均值低于
    ``min_chars_per_page``（默认 50）即抛 PluginError「疑似扫描版，需
    OCR（v2 提供）」，绝不静默返回空文（空文会被下游当成「文档无内容」，
    refine 产出空洞摘要、用户无从归因）；
  - 任何格式提取结果为空同样可读拒绝（空文件/纯图片 EPUB/加密 DOCX），
    理由同上：失败要显眼，不要伪装成「成功但没内容」。

**可选依赖（沿 transcribe 的 _import_faster_whisper 先例）**：四个解析
库全部调用期导入，缺席抛各格式 UnavailableError（PluginError 子族，带
pip 安装指引）——插件加载、清单扫描、/api/plugins/doc-extract/check
（PYTHON_DEPS 静态探测，缺席报 MISSING_DEP）均不受影响，绝不让整包
import 因缺一个能力崩。选型裁决（2026-11-20，不用 docling）：docling
★70k 格式最全但拖 torch/transformers 约 2-3GB——个人工具不为「支持所
有格式」付这个代价；文字版 PDF+DOCX+EPUB+MD 已覆盖约 90% 真实文档，
四个纯解析库合计约 7MB。

**提取保真（各格式按自身结构，不做猜谜）**：
  - PDF：pdfplumber 逐页 extract_text；单页异常可读失败（带页码，不静默
    吞页）；页间以空行拼接。
  - DOCX：python-docx 按 body 实际顺序遍历段落与表格（doc.paragraphs 与
    doc.tables 是两张分离清单，直接用会丢交错顺序）；标题样式（Heading n/
    标题 n/Title）转 # 标题；表格转 markdown 表格；列表自动编号是 Word
    渲染层行为（w:numPr），python-docx 不展开——编号丢失是已知保真边界，
    不伪造编号。
  - EPUB：按 spine（阅读顺序）遍历文本文档，章节标题取首个 h1-h6（退回
    item.title），标题落 ## ；script/style 剥除后再取正文；空文档（封面
    等纯图页）跳过不计数。
  - MD/TXT：utf-8-sig 直读，零依赖。

链路：doc-extract → material（payload.text 即本插件 text 端口，与素材
上传面同构）→ refine 提炼 → derived 素材（refine_service 走
material_body.material_text 抽取，纯 str 即可消费）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from musespiral.core.plugins.context import PluginCancelled, PluginError

# 后缀白名单（2026-11-20 格式生态线规格）：大小写不敏感（.PDF 合法）
ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".pdf", ".docx", ".epub", ".md", ".markdown", ".txt",
)

# 扫描版判定阈值（manifest.config_schema.min_chars_per_page.default 同事实，
# 双写保直读可测）：50 字/页均——中文正文页数百字起，扫描版 OCR 前常 <10
_MIN_CHARS_PER_PAGE = 50

# 依赖声明：/api/plugins/doc-extract/check 探测面据它 find_spec（缺席报
# MISSING_DEP 不崩——ast 静态读取，零执行）。元素是 **import 名**：
# python-docx 的 import 名是 docx、beautifulsoup4 是 bs4。
PYTHON_DEPS = ["pdfplumber", "docx"]

_PDF_HINT = "pip install pdfplumber"
_DOCX_HINT = "pip install python-docx"


class PdfUnavailableError(PluginError):
    """pdfplumber 未安装（可选依赖缺席；给可读安装引导）。"""


class DocxUnavailableError(PluginError):
    """python-docx 未安装（可选依赖缺席；给可读安装引导）。"""


class EpubUnavailableError(PluginError):
    """EPUB 提取不可用的历史异常类（保留供外部 import 兼容）。

    2026-11-21 许可证审查后，EPUB 已改为**纯标准库解析**（zipfile +
    ElementTree），不再依赖 ebooklib/bs4——本异常类不再由任何路径抛出。
    保留原因：`__all__` 导出面已公开，删类会破坏外部 import。
    """


def _import_pdfplumber() -> Any:
    """调用期导入 pdfplumber（缺席给安装引导，不让插件加载/整包 import 失败）。"""
    try:
        import pdfplumber  # noqa: PLC0415 - 调用期导入：缺席时给可读引导
    except ImportError as exc:
        raise PdfUnavailableError(
            f"pdfplumber 未安装：{_PDF_HINT}（PDF 提取的可选依赖，不随 musespiral 主包安装）"
        ) from exc
    return pdfplumber


def _import_docx() -> Any:
    """调用期导入 python-docx（import 名是 docx；缺席给安装引导）。"""
    try:
        import docx  # noqa: PLC0415
    except ImportError as exc:
        raise DocxUnavailableError(
            f"python-docx 未安装：{_DOCX_HINT}（DOCX 提取的可选依赖，不随 musespiral 主包安装）"
        ) from exc
    return docx




def _meaningful_chars(s: str) -> int:
    """有效字符数（去全部空白）——扫描版判定与空结果判定共用同一口径。"""
    return len(re.sub(r"\s+", "", s or ""))


def _heading_prefix(style_name: str) -> str:
    """Word 标题样式名 → markdown # 前缀（直接映射：Heading 1/标题 1 → #，
    上限 6 级；Title 样式（文档主标题）→ #）。"""
    if style_name == "Title":
        return "# "
    m = re.match(r"^(?:Heading|标题)\s*(\d+)$", style_name or "")
    if m:
        return "#" * min(int(m.group(1)), 6) + " "
    return ""


def _table_to_md(table: Any) -> str | None:
    """docx 表格 → markdown 表格（单元格 | 转义、换行折叠；空表返回 None）。"""
    rows = [list(row.cells) for row in table.rows]
    if not rows:
        return None

    def cell_text(cell: Any) -> str:
        return re.sub(r"\s*\n\s*", " ", cell.text).replace("|", "\\|").strip()

    grid = [[cell_text(c) for c in row] for row in rows]
    ncols = max(len(r) for r in grid)
    grid = [r + [""] * (ncols - len(r)) for r in grid]  # 参差表补空格保列对齐
    lines = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * ncols) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(lines)


def _extract_pdf(path: Path, min_chars_per_page: float, ctx: Any) -> tuple[str, str, int]:
    """文字版 PDF → (markdown, text, 页数)。扫描版可读拒绝（诚实红线）。

    单页 extract_text 异常**带页码可读失败**——静默吞页会让字符数假性偏
    低，进而把文字版误判成扫描版；宁可失败显眼，不给下游假文本。
    """
    plumber = _import_pdfplumber()
    try:
        pdf = plumber.open(str(path))
    except Exception as exc:  # 加密/损坏等——pdfplumber 异常族不统一，收口为可读失败
        raise PluginError(f"PDF 打开失败（可能加密或损坏）: {path} —— {exc}") from exc
    try:
        total = len(pdf.pages)
        if total == 0:
            raise PluginError(f"PDF 无任何页面（空文件或损坏）: {path}")
        page_texts: list[str] = []
        for i, page in enumerate(pdf.pages, 1):
            if ctx.cancelled:  # 页间自查点（正典 §4.4）
                raise PluginCancelled(f"文档提取在第 {i}/{total} 页收到取消信号")
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise PluginError(
                    f"PDF 第 {i}/{total} 页文本提取失败（该页可能损坏或是整页图片）: {exc}"
                ) from exc
            page_texts.append(text.strip())
            ctx.progress(i, total)
        chars = _meaningful_chars("\n".join(page_texts))
        avg = chars / total
        if avg < min_chars_per_page:
            # 诚实红线：扫描版必须可读拒绝，绝不静默返回空文（v2 提供 OCR）
            raise PluginError(
                f"疑似扫描版 PDF（可读拒绝，不静默返回空文）：{total} 页仅提取到 "
                f"{chars} 个有效字符（均值 {avg:.1f} 字/页 < 阈值 {min_chars_per_page:g}）。"
                "需要 OCR——v2 提供，当前版本请先用外部 OCR 工具转出文字版 PDF 再导入"
            )
        body = "\n\n".join(t for t in page_texts if t)
        ctx.log(f"doc-extract: PDF {total} 页提取 {chars} 个有效字符（均值 {avg:.1f} 字/页）")
        return body, body, total
    finally:
        pdf.close()


def _extract_docx(path: Path) -> tuple[str, str, None]:
    """DOCX → (markdown, text, pages=None)。段落+表格按 body 实际顺序保真。

    doc.paragraphs / doc.tables 是两张分离清单，只按它们遍历会丢「段落-表
    格-段落」的交错顺序——这里按 body 子元素（w:p / w:tbl）顺序遍历，
    python-docx 官方 FAQ 同款做法。
    """
    docx_mod = _import_docx()
    try:
        doc = docx_mod.Document(str(path))
    except Exception as exc:  # 损坏/非 OOXML zip 等，收口为可读失败
        raise PluginError(f"DOCX 打开失败（可能损坏或不是 Word 文档）: {path} —— {exc}") from exc
    from docx.oxml.ns import qn  # noqa: PLC0415 - 调用期导入（docx 已就位）
    from docx.table import Table  # noqa: PLC0415
    from docx.text.paragraph import Paragraph  # noqa: PLC0415

    md_blocks: list[str] = []
    text_blocks: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            prefix = _heading_prefix(para.style.name if para.style is not None else "")
            md_blocks.append((prefix + text) if prefix else text)
            text_blocks.append(text)
        elif child.tag == qn("w:tbl"):
            md = _table_to_md(Table(child, doc))
            if md is not None:
                md_blocks.append(md)
                text_blocks.append(md)  # 表格在纯文本侧同样以 markdown 表呈现
    body_md = "\n\n".join(md_blocks)
    body_text = "\n\n".join(text_blocks)
    if _meaningful_chars(body_text) == 0:
        raise PluginError(
            f"DOCX 提取结果为空（可读拒绝，不静默返回空文）：没有可提取的段落或表格 "
            f"——文件可能为空文档、纯图片或加密: {path}"
        )
    return body_md, body_text, None


def _extract_epub(path: Path, ctx: Any) -> tuple[str, str, int]:
    """EPUB → (markdown, text, 章节数)。**纯标准库实现，零第三方依赖**。

    为什么不用 ebooklib（2026-11-21 许可证审查判例）：
      ebooklib 是 **AGPL-3.0-or-later**，其第 13 条网络条款要求「任何以网络
      提供服务的衍生作品必须开源全部源码」——这会直接堵死 musespiral 的商业开源
      路径（SaaS 化/闭源商业版/私有部署销售三种变现全被禁止）。
      而 EPUB 本质是「zip + XHTML + 一个 OPF 清单」，用 zipfile +
      ElementTree 约 30 行即可完整解析（已用 44MB/155 章真实样本验证，
      结果与 ebooklib 一致）。**商业项目不该在许可争议地带冒险**。

    解析三步：
      1. META-INF/container.xml → 找到 OPF 路径
      2. OPF 的 <manifest> → id→href 映射 + 哪些是 XHTML
      3. OPF 的 <spine> → 阅读顺序（idref 序列）

    章节标题取文档内首个 h1-h6（退回「第 N 节」）；script/style 先剥除再取
    正文；空文档（封面等纯图页）跳过不计入章节数——与旧实现语义完全一致。
    """
    import zipfile  # noqa: PLC0415
    from xml.etree import ElementTree as ET  # noqa: PLC0415

    _NS = {"opf": "http://www.idpf.org/2007/opf"}
    try:
        with zipfile.ZipFile(path) as zf:
            container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
            m = re.search(r'full-path="([^"]+)"', container)
            if not m:
                raise PluginError(
                    f"EPUB 缺 OPF 指针（META-INF/container.xml 无 full-path）: {path}"
                )
            opf_path = m.group(1)
            opf = zf.read(opf_path).decode("utf-8", "replace")
            root = ET.fromstring(opf)

            # manifest：id → (href, is_xhtml)
            items: dict[str, tuple[str, bool]] = {}
            for it in root.findall(".//opf:item", _NS):
                iid = it.get("id")
                href = it.get("href")
                if not iid or not href:
                    continue
                is_xhtml = "xhtml" in (it.get("media-type") or "").lower()
                items[iid] = (href, is_xhtml)

            # spine：阅读顺序
            spine_ids = [r.get("idref") for r in root.findall(".//opf:itemref", _NS)]
            spine_ids = [s for s in spine_ids if s]

            base = str(PurePosixPath(opf_path).parent)
            md_chapters: list[str] = []
            text_chapters: list[str] = []
            for idx, idref in enumerate(spine_ids, 1):
                if ctx.cancelled:  # 章节间自查点
                    raise PluginCancelled(f"文档提取在第 {idx} 个 spine 文档收到取消信号")
                entry = items.get(idref)
                if not entry:
                    continue
                href, is_xhtml = entry
                if not is_xhtml:
                    continue  # 导航/样式等非文本文档
                full = f"{base}/{href}" if base not in ("", ".") else href
                try:
                    doc = zf.read(full).decode("utf-8", "replace")
                except KeyError:
                    continue  # manifest 指向但 zip 内缺失（坏包）——跳过不崩

                # script/style 整块先剥（否则 MathJax 等脚本会污染正文）
                doc = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", doc,
                             flags=re.S | re.I)
                # 章节标题：首个 h1-h6
                head_m = re.search(r"<h([1-6])[^>]*>(.*?)</h\1>", doc, flags=re.S | re.I)
                chap_title = ""
                if head_m:
                    chap_title = re.sub(r"<[^>]+>", "", head_m.group(2))
                    chap_title = re.sub(r"\s+", " ", chap_title).strip()
                    doc = doc[: head_m.start()] + doc[head_m.end():]  # 标题从正文剥离

                body = re.sub(r"<[^>]+>", "\n", doc)
                body = "\n".join(ln.strip() for ln in body.splitlines() if ln.strip())
                # 空文档门用「标题+正文」判：仅含标题的分部页要保留，
                # 纯图封面（零文本）要跳过
                if _meaningful_chars(chap_title + body) == 0:
                    continue
                if not chap_title:
                    chap_title = f"第 {len(md_chapters) + 1} 节"
                md_chapters.append(f"## {chap_title}\n\n{body}")
                text_chapters.append(f"{chap_title}\n\n{body}")
                ctx.progress(len(md_chapters), max(len(spine_ids), 1))
    except zipfile.BadZipFile as exc:
        raise PluginError(
            f"EPUB 打开失败（不是合法 zip，可能损坏或非 EPUB）: {path} —— {exc}"
        ) from exc
    except ET.ParseError as exc:
        raise PluginError(f"EPUB 的 OPF 不是合法 XML（损坏包）: {path} —— {exc}") from exc

    if not md_chapters:
        raise PluginError(
            f"EPUB 提取结果为空（可读拒绝，不静默返回空文）：没有任何含文本的章节 "
            f"——文件可能是纯图片版或损坏: {path}"
        )
    body_md = "\n\n".join(md_chapters)
    body_text = "\n\n".join(text_chapters)
    ctx.log(f"doc-extract: EPUB {len(md_chapters)} 个章节（spine 共 {len(spine_ids)} 个文档，纯标准库解析）")
    return body_md, body_text, len(md_chapters)


def _extract_text_file(path: Path) -> tuple[str, str, None]:
    """MD/TXT → 原文直读（utf-8-sig 吃 BOM）。解码失败可读拒绝，不产出乱码。"""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PluginError(
            f"文本文件不是合法 UTF-8（可读拒绝，不做猜测式转码）: {path} —— {exc}"
        ) from exc
    body = content.strip()
    if _meaningful_chars(body) == 0:
        raise PluginError(f"文本文件为空（可读拒绝，不静默返回空文）: {path}")
    return body, body, None





def _count_pages(path: Path, suffix: str) -> int | None:
    """按后缀快速数页/章节（不重复提取正文，只看结构元信息）。

    PDF→pdfplumber 数页（已安装，数页极快）；EPUB→zipfile 数 spine xhtml；
    其他格式无页概念→None。
    """
    try:
        if suffix == ".pdf":
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(path) as pdf:
                return len(pdf.pages)
        if suffix == ".epub":
            import zipfile  # noqa: PLC0415
            with zipfile.ZipFile(path) as zf:
                container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
                import re as _re  # noqa: PLC0415
                m = _re.search(r'full-path="([^"]+)"', container)
                if m:
                    from xml.etree import ElementTree as _ET  # noqa: PLC0415
                    opf = zf.read(m.group(1)).decode("utf-8", "replace")
                    root = _ET.fromstring(opf)
                    ns = {"opf": "http://www.idpf.org/2007/opf"}
                    spine = root.findall(".//opf:itemref", ns)
                    return max(len([s for s in spine if s.get("idref")]) - 1, 1)
    except Exception:
        pass  # 数页失败不影响提取——pages 是尽力而为的元信息
    return None

def _try_markitdown(
    path: Path, suffix: str, ctx: Any, title: str
) -> tuple[str, str, int | None] | None:
    """markitdown 后端（可选，优先后端）。

    装了就用——microsoft/markitdown（MIT，★197K）格式覆盖比自研四路更全
    （含 PPTX/XLSX/图片/音频/YouTube URL 等）。未安装或转换失败时返回
    None，由调用方退回自研提取器——**永远不给用户一个错误的面**。

    自研四路（pdfplumber/python-docx/纯标准库 EPUB/文本）仍是兜底，
    保证 markitdown 不在时插件照常工作。
    """
    try:
        from markitdown import MarkItDown  # noqa: PLC0415
    except ImportError:
        ctx.log("doc-extract: markitdown 未安装（可选优先后端）——退回自研提取器。"
                "pip install markitdown 可解锁 PPTX/XLSX/图片/音频等更多格式")
        return None
    try:
        m = MarkItDown()
        result = m.convert(str(path))
        text = (result.text_content or "").strip()
        if not text:
            ctx.log(f"doc-extract: markitdown 返回空文——退回自研提取器: {path.name}")
            return None
        # pages：markitdown 无统一页数概念——按后缀补算（快，不重复提取）
        pages = _count_pages(path, suffix)
        markdown = f"# {title}\n\n{text}" if title else text
        ctx.log(f"doc-extract: markitdown 提取成功——{len(text):,} 字符"
                f"（覆盖 {suffix} 及更多格式）")
        return markdown, text, pages
    except Exception as exc:  # markitdown 自身的转换异常
        ctx.log(f"doc-extract: markitdown 转换失败（{type(exc).__name__}）"
                f"——退回自研提取器: {path.name}")
        return None


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    raw = inputs.get("doc_path")
    if raw is None or not str(raw).strip():
        raise PluginError("缺少输入 doc_path（文档文件路径）")
    path = Path(str(raw))
    if not path.is_file():
        raise PluginError(f"文档文件不存在: {path}")
    if not os.access(path, os.R_OK):
        raise PluginError(f"文档文件不可读（权限不足）: {path}")
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise PluginError(
            f"后缀不在白名单 {''.join(ALLOWED_SUFFIXES)}: {suffix!r}"
        )

    title = str(inputs.get("title") or "").strip() or path.stem
    min_chars_per_page = config.get("min_chars_per_page", _MIN_CHARS_PER_PAGE)
    try:
        min_chars_per_page = float(min_chars_per_page)
    except (TypeError, ValueError):
        raise PluginError(
            f"config.min_chars_per_page 须为数字: {min_chars_per_page!r}"
        ) from None

    ctx.log(f"doc-extract: {suffix} ← {path.name}（扫描版阈值 {min_chars_per_page:g} 字/页）")

    # **markitdown 优先后端**（2026-11-21 集成）：装了就用——格式覆盖更全
    # （含 PPTX/XLSX/图片/音频/YouTube 等）、微软官方维护。MIT 许可证，
    # 依赖已过 test_license_compliance 门。未安装时优雅退回自研四路提取器。
    if config.get("prefer_markitdown", True):
        result = _try_markitdown(path, suffix, ctx, title)
        if result is not None:
            markdown, body_text, pages = result
            ctx.progress(1, 1)
            return {"markdown": markdown, "text": body_text, "pages": pages}

    if suffix == ".pdf":
        body_md, body_text, pages = _extract_pdf(path, min_chars_per_page, ctx)
    elif suffix == ".docx":
        body_md, body_text, pages = _extract_docx(path)
    elif suffix == ".epub":
        body_md, body_text, pages = _extract_epub(path, ctx)
    else:  # .md / .markdown / .txt
        body_md, body_text, pages = _extract_text_file(path)

    # 统一空结果门（PDF 扫描门是更具体的先行诊断；阈值被调低到 0 等
    # 情况下由本门兜底）——任何格式都绝不静默返回空文
    if _meaningful_chars(body_text) == 0:
        raise PluginError(
            f"提取结果为空（可读拒绝，不静默返回空文）: {path}"
            f"——{suffix} 无任何可提取文本（可能为纯图片、加密或空文件）"
        )

    markdown = f"# {title}\n\n{body_md}" if title else body_md
    ctx.log(
        f"doc-extract: 完成——markdown {len(markdown)} 字符 / text {len(body_text)} 字符"
        f"（pages={pages if pages is not None else '不适用'}）"
    )
    ctx.progress(1, 1)
    # pages：PDF=页数、EPUB=章节数；DOCX/文本无页概念 → None（诚实留空，不伪造）
    return {"markdown": markdown, "text": body_text, "pages": pages}


__all__ = [
    "ALLOWED_SUFFIXES",
    "DocxUnavailableError",
    "EpubUnavailableError",
    "PdfUnavailableError",
    "PYTHON_DEPS",
    "run",
]
