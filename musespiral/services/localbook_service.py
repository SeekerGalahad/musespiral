"""本地 TXT 书架服务（B1 本地书籍线，L3）——引用型元数据 + 选章素材化 job。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2（后端设计）与 §1.4（素材化链路）。

存储（设计 §1.2 原文形态，`~/.musespiral/localbooks/` 随 MUSESPIRAL_HOME）::

    index.json            # [{id, path, title, author, charset, chapters, size, added_at}]
    <id>/chapters.json    # [{n, title, start, end}] 字符偏移
    <id>/meta.json        # 编码探测结果、章节规则命中统计

**红线：文件本身绝不复制**（设计 §五）——meta 只存用户磁盘上的绝对路径；
`chapter`/素材化时现读现切（`core.localbook.reader.slice_chapter`）。库里
没有正文副本，用户删掉 TXT 后书架如实报「文件不可读」，而不是给一份陈旧
快照（见 `_open_book` 的 404 分支）。

三条 job/异步边界（设计 §1.2 端点表「job：大文件解析」）：
  - `add`：同步（单书解析：读+探测+切章，几十 MB 的 TXT 在 1s 级）；
  - `reparse`：同步（同上，换规则重切）；
  - `localbook_fetch`：**job**（选章入素材库 N 章 × 每章读文+落库，与网络书
    `book_fetch` 同形态同纪律：提交期零落盘，正文与素材全部 job 运行期写）。

幂等（设计 §1.4 素材化链路 + 本批题面「同 id 重复 skip」）：素材 id 由
`(source_id, type, uri)` 稳定散列，uri=`localbook:<book_id>#<n>` 恒同 →
同章重采命中 `materials_service.add_material` 的等值分支，零追加。
`source_id=localbook` 是本域的虚拟源标记（`book`/`ai_gen`/`synthesis` 先例：
不进 sources.json 注册表，uri 承载全部身份）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

from .. import config
from ..core.localbook import chapters as lb_chapters
from ..core.localbook import charset as lb_charset
from ..core.localbook import rules as lb_rules
from ..core.localbook import toc as lb_toc
from ..core.localbook.reader import slice_chapter
from ..core.materials import model as material_model
from ..infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ..jobs.store import now_iso as _now_iso
from . import materials_service

# 书架根（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移）
LOCALBOOKS_DIR = "localbooks"
INDEX_FILE = "index.json"
# 自定义规则目录（设计 §1.3：`~/.musespiral/localbook-rules/*.json`）
RULES_DIR = "localbook-rules"

# 素材虚拟源标记（book/ai_gen/synthesis 同款：不进 sources.json 注册表）
LOCALBOOK_SOURCE_ID = "localbook"
# 素材 uri 前缀（幂等锚：同书同章恒同 uri → 稳定素材 id）
LOCALBOOK_URI_SCHEME = "localbook"

# 单文件读入上限（fail-closed 的防御面，不是产品限制）：整书要进内存解码，
# 超过 64MB 的 TXT 通常是误选文件（视频/压缩包改名），可读拒绝优于 OOM。
MAX_FILE_BYTES = 64 * 1024 * 1024
# 允许的扩展名（引用型只读面：只认纯文本，避免误把二进制当书解析）
ALLOWED_SUFFIXES = (".txt", ".text")
# 摘录上限（素材 payload.excerpt，设计 §1.4 素材化链路的展示面）
EXCERPT_CHARS = 500
# 素材化单次章数上限（32 = 一天读得完的量；更多请分批——与 book_fetch 同
# 「更多章请分批」的可读错误口径）
MAX_MATERIALIZE_CHAPTERS = 32

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_AUTHOR_RE = re.compile(r"^(?:作\s*者|著\s*者|著)[:：\s]*(.{1,40})$")


class LocalBookError(ValueError):
    """书架操作的可读错误（服务层校验失败——studio 端点转 400）。"""


# ── 路径与索引 ─────────────────────────────────────────────────────


def localbooks_root() -> Path:
    """书架根目录（~/.musespiral/localbooks；每次现读 home_dir）。"""
    return config.home_dir() / LOCALBOOKS_DIR


def rules_root() -> Path:
    """自定义规则目录（~/.musespiral/localbook-rules/*.json）。"""
    return config.home_dir() / RULES_DIR


def _book_dir(book_id: str) -> Path:
    """单书目录；id 必须在指纹白名单内（防路径逃逸——id 会进 URL 参数）。"""
    bid = (book_id or "").strip()
    if not _ID_RE.match(bid):
        raise LocalBookError(f"书 id 非法（须 12 位十六进制）: {book_id!r}")
    return localbooks_root() / bid


def _load_index() -> list[dict[str, Any]]:
    """宽容读索引（缺失/坏 JSON/非列表 → 空表，不崩读侧）。"""
    try:
        data = json.loads((localbooks_root() / INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("books") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and _ID_RE.match(row["id"]):
            out.append(row)
    return out


def _save_index(rows: list[dict[str, Any]]) -> None:
    """整文件原子写（tmp + os.replace；父目录自建）。"""
    path = localbooks_root() / INDEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        path,
        (json.dumps({"books": rows}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _write_json(path: Path, data: Any) -> None:
    """单文件原子写（meta.json / chapters.json 共用件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _book_id(path: Path) -> str:
    """书 id = sha1(resolve 后绝对路径)[:12]。

    为什么锚**路径**而不是文件内容：引用型语义下「同一本书」= 磁盘上同一个
    文件；按内容散列会让「用户复制一份同名副本」变成两本书（合理），但也会让
    「文件被编辑过」变成另一本书（丢弃在读状态与素材关联）。路径锚 + 重解析
    是用户可理解的心智模型。
    """
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


# ── 规则加载（I/O 落点：core 只解析 JSON 文本）──────────────────────


def load_rules() -> tuple[list[dict[str, Any]], int]:
    """`~/.musespiral/localbook-rules/*.json` → (自定义规则表, 坏条目数)。

    坏 JSON/坏 schema **跳过并计数**（设计 §1.3）——不抛：一个社区规则写错
    不该让整个书架不可用；计数由 `rules()` 读面如实上报。
    """
    root = rules_root()
    if not root.is_dir():
        return [], 0
    texts: list[tuple[str, str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            texts.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            texts.append((path.name, ""))  # 读不了的按坏条目计
    return lb_rules.load_rules_from_texts(texts)


def all_rules() -> dict[str, Any]:
    """规则 profile 清单（前端「可切换 profile」下拉的数据源）。

    设计 §1.4「规则 profile（可切换重解析）」：内置三档 + 用户自定义 +
    坏条目计数。**当前未挂 HTTP 读面**（契约 GET+4 不含 rules 端点）——
    内置三档在 `book_info` 的 `rule/rule_name` 与前端常量里够用；本函数
    留给 CLI/将来「规则管理」读面复用，并在单测里直接消费。
    """
    custom, bad = load_rules()
    return {"rules": lb_rules.all_rules(custom), "bad": bad}


# ── 单书解析（add/reparse/chapter 共用的读-解-切链）──────────────────


def _resolve_path(raw_path: str) -> Path:
    """用户给的路径 → 绝对 Path（展开 ~；存在性/后缀/大小三门）。"""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise LocalBookError("缺少必填字段 path（TXT 文件绝对路径）")
    path = Path(raw_path.strip()).expanduser()
    if not path.is_absolute():
        raise LocalBookError(f"只接受绝对路径（避免相对路径歧义）: {raw_path}")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise LocalBookError(
            f"只支持纯文本文件（{', '.join(ALLOWED_SUFFIXES)}）: {path.name}"
        )
    if not path.is_file():
        raise LocalBookError(f"文件不存在或不可读: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise LocalBookError(f"文件不可读: {exc}") from None
    if size > MAX_FILE_BYTES:
        raise LocalBookError(
            f"文件过大（{size // (1024 * 1024)}MB > {MAX_FILE_BYTES // (1024 * 1024)}MB 上限）"
            "——本地书整本解析需入内存，超大文件请先切分"
        )
    if size == 0:
        raise LocalBookError("文件为空（0 字节）")
    return path


def read_book_text(path: Path) -> tuple[str, dict[str, Any]]:
    """读整书 → `(text, charset_info)`；解码失败按探测结果 replace 兜底。

    探测（`core.localbook.charset.detect_bytes`）只看文件头样本，整本解码
    用探测出的编码；个别坏字节（文件尾部截断的半个汉字）以 `errors="replace"`
    兜底——**不让一个坏字节让整本书读不了**，代价是极少数位置出现 U+FFFD。
    """
    raw = path.read_bytes()
    info = lb_charset.detect_bytes(raw)
    try:
        text = raw.decode(info["charset"], errors="replace")
    except LookupError:  # 理论不可达（探测只产出已知编码名）；保守回退
        text = raw.decode("utf-8", errors="replace")
        info = {**info, "charset": "utf-8", "confidence": 0.3}
    # BOM 已由 utf-8-sig 剥除；UTF-16 的 BOM 由各自 codec 的 decode 自然剥除
    text = text.lstrip("\ufeff")
    return text, info


def _guess_title(path: Path) -> str:
    """书名缺省=文件名去扩展名（设计 §1.4「标题」位；用户不填时的唯一可用来源）。"""
    return path.stem.strip() or path.name


def _guess_author(text: str) -> str:
    """作者猜测：前 30 行里找 `作者：XXX` / `著者：XXX`（设计 §1.4「作者(从首行猜)」）。

    猜不到就是空串——**不编造**：前端显示「作者未知」比显示一个错的作者名好。
    """
    for line in text.splitlines()[:30]:
        m = _AUTHOR_RE.match(line.strip())
        if m:
            author = m.group(1).strip()
            if author:
                return author[:40]
    return ""


def _parse_book(
    text: str, rule: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, int]:
    """按规则切章 → (chapters 原始偏移, toc 规整目录, structure, hits)。"""
    chapters, structure, hits = lb_chapters.parse_with_rule(text, rule)
    toc = lb_toc.clean_toc(chapters, book_id="")  # url 由调用方按 id 补齐
    return chapters, toc, structure, hits


def _toc_with_id(chapters: Sequence[dict[str, Any]], book_id: str) -> list[dict[str, Any]]:
    """目录的 url 需要 book id（`localbook:<id>#<n>` 伪 URL）——按 id 重算一遍。"""
    return lb_toc.clean_toc(chapters, book_id)


def _resolve_rule(rule_id: str | None) -> dict[str, Any]:
    custom, _bad = load_rules()
    return lb_rules.get_rule(rule_id, custom)


def _parse_and_store(book_id: str, text: str, rule: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """解析并落 chapters.json + meta 统计（add 与 reparse 共用的写面）。"""
    chapters, _toc, structure, hits = _parse_book(text, rule)
    _write_json(_book_dir(book_id) / "chapters.json", chapters)
    meta = {
        **meta,
        "rule": rule["id"],
        "rule_name": rule["name"],
        "structure": structure,
        "chapters": len(chapters),
        "chapter_hits": hits,
        "chars": len(text),
        "unstructured": structure != "chapters",
        "parsed_at": _now_iso(),
    }
    _write_json(_book_dir(book_id) / "meta.json", meta)
    return meta


def add_book(raw_path: str, *, title: str = "", author: str = "", rule: str = "") -> dict[str, Any]:
    """导入一本书 → 统计 dict（同书重复导入 = 覆盖重解析，不产生第二本）。

    返回 `{id, title, author, charset, confidence, chapters, structure,
    chapter_hits, chars, size, unstructured, bytes_per_note?}`；`unstructured`
    =true 时 `structure="fixed"`（前端如实显示「未识别章节结构」）。
    """
    path = _resolve_path(raw_path)
    book_id = _book_id(path)
    text, info = read_book_text(path)
    rule_row = _resolve_rule(rule or None)
    base_meta = {
        "id": book_id,
        "path": str(path),
        "title": (title or "").strip() or _guess_title(path),
        "author": (author or "").strip() or _guess_author(text),
        "charset": info["charset"],
        "charset_confidence": info["confidence"],
        "bom": info.get("bom", False),
        "size": path.stat().st_size,
        "added_at": _now_iso(),
    }
    meta = _parse_and_store(book_id, text, rule_row, base_meta)
    _upsert_index(meta)
    return meta


def _upsert_index(meta: dict[str, Any]) -> None:
    """索引行 upsert（同 id 覆盖原位——更新不重排书架）。"""
    rows = _load_index()
    row = {
        "id": meta["id"],
        "path": meta["path"],
        "title": meta["title"],
        "author": meta.get("author", ""),
        "charset": meta.get("charset", ""),
        "chapters": meta.get("chapters", 0),
        "structure": meta.get("structure", ""),
        "chars": meta.get("chars", 0),
        "size": meta.get("size", 0),
        "added_at": meta.get("added_at", ""),
    }
    for i, existing in enumerate(rows):
        if existing.get("id") == row["id"]:
            rows[i] = row
            break
    else:
        rows.append(row)
    _save_index(rows)


def reparse_book(book_id: str, *, rule: str = "") -> dict[str, Any]:
    """换规则重解析（文本处理插件入口）——meta 保留 title/author/added_at。"""
    meta = _load_meta(book_id)
    path = Path(str(meta.get("path") or ""))
    if not path.is_file():
        raise LocalBookError(f"原文件已不可读（可能被移动/删除）: {meta.get('path')}")
    text, info = read_book_text(path)
    rule_row = _resolve_rule(rule or None)
    base = {
        **{k: meta.get(k) for k in ("id", "path", "title", "author", "size", "added_at")},
        "charset": info["charset"],
        "charset_confidence": info["confidence"],
        "bom": info.get("bom", False),
    }
    updated = _parse_and_store(book_id, text, rule_row, base)
    _upsert_index(updated)
    return updated


# ── 读面 ───────────────────────────────────────────────────────────


def list_books() -> dict[str, Any]:
    """书架清单（索引顺序=导入顺序；行里带 enough 的展示字段）。"""
    rows = _load_index()
    return {"ok": True, "books": rows, "total": len(rows)}


def _load_meta(book_id: str) -> dict[str, Any]:
    """单书 meta（缺失 → LocalBookError 404 语义由端点层转）。"""
    meta = _read_json(_book_dir(book_id) / "meta.json")
    if not isinstance(meta, dict) or not meta.get("id"):
        raise LocalBookError(f"书不存在或未完成导入: {book_id!r}")
    return meta


def book_info(book_id: str) -> dict[str, Any]:
    """详情读面：meta + 章节数 + 规则命中 + 文件现存状态。

    `file_ok=false` 是**如实的可选态**（用户的 TXT 被移动/删除）：书架条目
    保留（元数据与素材关联还在），阅读与素材化会拒绝并说明原因。
    """
    meta = _load_meta(book_id)
    path = Path(str(meta.get("path") or ""))
    return {
        "ok": True,
        "book": meta,
        "file_ok": path.is_file(),
        "chapters": int(meta.get("chapters") or 0),
        "structure": meta.get("structure") or "",
        "unstructured": bool(meta.get("unstructured")),
    }


def _load_chapters(book_id: str) -> list[dict[str, Any]]:
    rows = _read_json(_book_dir(book_id) / "chapters.json")
    if not isinstance(rows, list):
        raise LocalBookError(f"章节索引缺失（请重解析）: {book_id!r}")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("n"), int)]


def book_toc(book_id: str) -> dict[str, Any]:
    """目录读面 → bookengine 同形 `{chapters: [{title,index,url}], total}`。"""
    _load_meta(book_id)  # 存在性校验（不存在即 404 语义）
    chapters = _load_chapters(book_id)
    toc = _toc_with_id(chapters, book_id)
    return {"ok": True, "status": "ok", "chapters": toc, "total": len(toc)}


def _read_chapter(meta: dict[str, Any], chapters: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """现读现切一章（引用型：正文只在此刻存在，不落库）。"""
    row = next((c for c in chapters if int(c.get("n") or 0) == int(n)), None)
    if row is None:
        raise LocalBookError(f"章节不存在: 第 {n} 章（本书共 {len(chapters)} 个章节条目）")
    path = Path(str(meta.get("path") or ""))
    if not path.is_file():
        raise LocalBookError(f"原文件已不可读（可能被移动/删除）: {meta.get('path')}")
    text, _info = read_book_text(path)
    sliced = slice_chapter(text, int(row.get("start") or 0), int(row.get("end") or 0))
    return {
        "ok": True,
        "status": "ok",
        "n": int(n),
        "title": str(row.get("title") or ""),
        "content": sliced["content"],
        "chars": sliced["chars"],
        "orig_chars": sliced["orig_chars"],
        "truncated": sliced["truncated"],
    }


def book_chapter(book_id: str, n: int) -> dict[str, Any]:
    """单章读面（`GET /api/localbook/chapter?id=&n=` 的数据面）。"""
    meta = _load_meta(book_id)
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise LocalBookError(f"n 必须是 ≥1 的整数: {n!r}")
    return _read_chapter(meta, _load_chapters(book_id), n)


def remove_book(book_id: str) -> dict[str, Any]:
    """下架一本书：删 `<id>/` 目录 + 索引行（**用户原文件零触碰**）。

    已入素材库的章节素材保留（它们是独立素材；血统链 `from_id` 指向
    `localbook:<id>:<n>`，删除后 lineage 显示「已删」徽章——与既有引用状态
    语义一致，不做级联删除）。
    """
    meta = _load_meta(book_id)
    target = _book_dir(book_id)
    removed: list[str] = []
    for name in ("chapters.json", "meta.json"):
        path = target / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    try:
        target.rmdir()  # 目录里若有用户放的杂项 → 非空，留目录不报错
    except OSError:
        pass
    rows = [r for r in _load_index() if r.get("id") != book_id]
    _save_index(rows)
    return {"ok": True, "removed": book_id, "title": meta.get("title", ""), "files": removed}


# ── 素材化（选章入素材库 → job localbook_fetch）────────────────────


def _excerpt(content: str) -> str:
    """摘录：压空白后取前 EXCERPT_CHARS 字符（展示面，不是正文面）。"""
    flat = re.sub(r"\s+", " ", content).strip()
    return flat[:EXCERPT_CHARS]


def _make_chapter_material(
    *,
    book_meta: dict[str, Any],
    n: int,
    title: str,
    content: str,
    fetched_at: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    """一章一条素材（type=text，uri=`localbook:<id>#<n>`，payload 只放摘录）。

    **payload 不放整章正文**（设计 §1.4 写的是 `payload={local_book_id,
    chapter_n, chars, excerpt≤500}`，且 §五 红线是「只读不复制」）——正文
    永远在用户磁盘上，需要全文时经 `GET /api/localbook/chapter` 现取。
    这条与网络书 `book_fetch`（正文落 payload + 章节缓存）**是有意的差异**：
    网络书没有第二份可信来源，本地书有；把整章复制进素材库等于绕开红线。

    provenance.chain 单链 `{from_id: "localbook:<id>:<n>", op: "adopt"}`——
    `adopt` 是本批新增的血统动作（`model.PROVENANCE_OPS` 扩键 + 单链豁免，
    与 book_fetch/plugin_pipeline 同族：血统只由采纳动作写入）。
    """
    meta = {
        "local_book": {
            "id": book_meta["id"],
            "title": book_meta.get("title", ""),
            "author": book_meta.get("author", ""),
            "chapter_n": n,
            "chapter_title": title,
            "chars": len(content),
            "path": book_meta.get("path", ""),
            "charset": book_meta.get("charset", ""),
        },
        "case": case,
    }
    return material_model.make_material(
        mtype="text",
        source_id=LOCALBOOK_SOURCE_ID,
        uri=f"{LOCALBOOK_URI_SCHEME}:{book_meta['id']}#{n}",
        title=f"《{book_meta.get('title') or '本地书'}》第 {n} 章 {title}".strip(),
        license="UNKNOWN",
        payload={
            "local_book_id": book_meta["id"],
            "chapter_n": n,
            "chars": len(content),
            "excerpt": _excerpt(content),
            "meta": meta,
        },
        provenance={
            "chain": [{"from_id": f"{LOCALBOOK_URI_SCHEME}:{book_meta['id']}:{n}", "op": "adopt"}],
            "derived_from": [],
        },
        triage={"read": False, "starred": False, "enabled": False, "tags": []},
        fetched_at=fetched_at,
    )


def run_fetch(
    book_id: str,
    chapter_numbers: Sequence[int],
    *,
    case: dict[str, Any] | None = None,
    on_phase=None,
    should_stop=None,
) -> dict[str, Any]:
    """job 执行体：逐章现读现切 → 素材落库（幂等同章 skip）。

    返回 `{ok, status, book, chapters_total, added_n, skipped_n, skipped,
    failed:[…], materials:[id…]}`——正文不进 job result（jobs result 只存计数
    纪律，与 book_fetch 同款）。

    幂等判定（题面「同 id 重复 skip」）：落库前先 `get_material(id)` 探活——
    已存在即 skip（不调 add_material，避免等值比较的成本与歧义）；不存在才写。
    素材 id 由 uri 稳定散列，同书同章恒同 id，所以重跑同一批章 = 全 skip、
    零追加。
    """
    meta = _load_meta(book_id)
    chapters = _load_chapters(book_id)
    total = len(chapter_numbers)
    fetched_at = _now_iso()
    added_ids: list[str] = []
    skipped: list[int] = []
    failed: list[dict[str, Any]] = []
    for i, n in enumerate(chapter_numbers, 1):
        if should_stop is not None and should_stop():
            break
        if on_phase is not None:
            on_phase(f"选章入素材库 {i}/{total} 第 {n} 章")
        try:
            sliced = _read_chapter(meta, chapters, int(n))
        except LocalBookError as exc:
            failed.append({"n": int(n), "error": str(exc)})
            continue
        row = _make_chapter_material(
            book_meta=meta,
            n=int(n),
            title=sliced["title"],
            content=sliced["content"],
            fetched_at=fetched_at,
            case=case or {},
        )
        if materials_service.get_material(row["id"]) is not None:
            skipped.append(int(n))
            continue
        try:
            saved = materials_service.add_material(row)
        except ValueError as exc:
            if "immutable" in str(exc):  # 竞态窗口：探活后被别处写入
                skipped.append(int(n))
                continue
            failed.append({"n": int(n), "error": str(exc)})
            continue
        added_ids.append(saved["id"])
    return {
        "ok": True,
        "status": "done",
        "book": {"id": book_id, "title": meta.get("title", "")},
        "chapters_total": total,
        "added_n": len(added_ids),
        "skipped_n": len(skipped),
        "skipped": sorted(set(skipped)),
        "failed": failed,
        "materials": added_ids,
    }


def job_fn(
    book_id: str,
    chapter_numbers: list[int],
    *,
    case: dict[str, Any] | None = None,
    runner: Any = None,
) -> Any:
    """把一次选章素材化包成 jobs 闭包（kind=localbook_fetch）。"""

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(text: str) -> None:
            if runner is not None:
                runner.set_phase(job_id, text)

        def _stop() -> bool:
            return bool(runner is not None and runner.is_cancelled(job_id))

        return run_fetch(
            book_id, chapter_numbers, case=case, on_phase=_phase, should_stop=_stop
        )

    return _run


def _check_chapter_numbers(chapters: Any) -> list[int]:
    """入参章号校验：非空、整数、≤ 上限、去重保序。"""
    if not isinstance(chapters, (list, tuple)) or not chapters:
        raise LocalBookError("缺少必填字段 chapters（非空章号数组）")
    out: list[int] = []
    seen: set[int] = set()
    for i, value in enumerate(chapters):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise LocalBookError(f"chapters[{i}] 必须是 ≥1 的整数: {value!r}")
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    if len(out) > MAX_MATERIALIZE_CHAPTERS:
        raise LocalBookError(
            f"章节数超上限：{len(out)} 章（单次 ≤{MAX_MATERIALIZE_CHAPTERS} 章，更多请分批）"
        )
    return out


def submit_to_runner(
    runner: Any,
    book_id: str,
    chapters: Any,
    *,
    case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """提交一次素材化 job（kind=localbook_fetch）→ `{job_id, chapters}`。

    构造期校验（章号形状/上限/书存在/文件还在）全部在 `runner.submit` **之前**
    ——校验不通过抛 LocalBookError，零 job 零落盘（book_fetch 同纪律）。
    """
    meta = _load_meta(book_id)
    numbers = _check_chapter_numbers(chapters)
    path = Path(str(meta.get("path") or ""))
    if not path.is_file():
        raise LocalBookError(f"原文件已不可读（可能被移动/删除）: {meta.get('path')}")
    fn = job_fn(book_id, numbers, case=case, runner=runner)
    job_id = runner.submit(
        "localbook_fetch",
        {"id": book_id, "chapters": numbers, "book": {"name": meta.get("title", "")}},
        fn,
    )
    return {"job_id": job_id, "chapters": numbers}


__all__ = [
    "ALLOWED_SUFFIXES",
    "EXCERPT_CHARS",
    "LOCALBOOKS_DIR",
    "LOCALBOOK_SOURCE_ID",
    "LOCALBOOK_URI_SCHEME",
    "LocalBookError",
    "MAX_FILE_BYTES",
    "MAX_MATERIALIZE_CHAPTERS",
    "RULES_DIR",
    "add_book",
    "all_rules",
    "book_chapter",
    "book_info",
    "book_toc",
    "job_fn",
    "list_books",
    "load_rules",
    "localbooks_root",
    "read_book_text",
    "remove_book",
    "reparse_book",
    "rules_root",
    "run_fetch",
    "submit_to_runner",
]
