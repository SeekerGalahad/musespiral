"""本地知识库目录源（Phase C 迁入 infra/sources/，正典 §八） — 扫描 Markdown 笔记并从笔记生成文章草稿。

R6 自顶层 notes.py 全量迁入（正典 §四-R6 迁移映射表：notes.scan →
core/sources/markdown_dir.py；notes.py 留 re-export 兼容）。函数体逐字节
保留，仅模块 docstring 与归属说明更新。

知识库是一个纯 .md 文件目录（Markdown 生态），每篇笔记可带 YAML
frontmatter（title/tags/date）。无任何 API 调用；输出是可编辑的文章文件，
发布是人触发的独立步骤。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import frontmatter


@dataclass
class Note:
    path: Path
    title: str
    tags: list[str]
    date: str
    chars: int
    is_article: bool  # already has musespiral-required frontmatter
    # F11e：库内正文（notessql 查询时填充，供 --from-db 草稿在源文件缺失时
    # 兜底）；目录扫描（scan/_load_note）不填，保持字段最小占用。
    content: str = ""


_SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".venv", "venv",
    "site-packages", ".tox", ".mypy_cache", "dist", "build", ".musespiral",
}


def _iter_md(root: Path, include_articles: bool) -> Iterator[Path]:
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith("."):
            continue
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part in _SKIP_DIRS or part.endswith(".musespiral") for part in rel_parts):
            continue
        if not include_articles and p.parent.name.endswith(".musespiral"):
            continue
        yield p


def _load_note(path: Path) -> Note | None:
    try:
        post = frontmatter.load(path)
    except Exception:
        return None
    md = post.metadata
    text = post.content.strip()
    if not text:
        return None
    title = str(md.get("title") or _first_line_title(text) or path.stem)
    tags = md.get("tags") or md.get("tag") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    date = str(md.get("date") or _mtime_date(path))
    return Note(
        path=path,
        title=title,
        tags=[str(t) for t in tags],
        date=date,
        chars=len(text),
        is_article=("cover" in md and "title" in md),
    )


def _first_line_title(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _mtime_date(path: Path) -> str:
    return _dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def scan(root: str | Path, *, min_chars: int = 200, include_articles: bool = False) -> list[Note]:
    """Scan a directory for candidate notes, newest first."""
    base = Path(root).expanduser().resolve()
    if not base.exists():
        raise FileNotFoundError(f"知识库目录不存在: {base}")
    notes: list[Note] = []
    for p in _iter_md(base, include_articles):
        note = _load_note(p)
        if note and note.chars >= min_chars:
            notes.append(note)
    notes.sort(key=lambda n: (n.date, n.chars), reverse=True)
    return notes


def scaffold_article(
    note_path: str | Path,
    out_path: str | Path,
    *,
    cover: str = "./assets/cover.png",
    theme: str = "lapis",
    content: str | None = None,
) -> Path:
    """Create an article file from a note, preserving the body and adding
    the frontmatter musespiral requires. Never overwrites an existing file.

    F11e: content 显式指定正文时优先生效（--from-db 草稿在源文件缺失时
    用库内 content 列兜底），此时不读取 note_path——它仅用于标题回退
    （正文一级标题 → 文件名 stem）。
    """
    src = Path(note_path)
    dst = Path(out_path)
    if dst.exists():
        raise FileExistsError(f"目标文章已存在，拒绝覆盖: {dst}")

    if content is None:
        post = frontmatter.load(src)
        body = post.content.strip()
        md = post.metadata
    else:
        # 兜底正文来自库 content 列，按纯正文处理（不解析 frontmatter）
        body = content.strip()
        md = {}
    title = str(md.get("title") or _first_line_title(body) or src.stem)
    digest = _auto_digest(body)

    meta = {
        "title": title,
        "author": str(md.get("author", "")),
        "digest": digest,
        "cover": cover,
        "theme": theme,
        "code_theme": str(md.get("code_theme", "monokai")),
        "tags": md.get("tags", []),
        "status": "draft",
    }
    new_post = frontmatter.Post(body, **meta)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(frontmatter.dumps(new_post, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dst


def _auto_digest(body: str, max_bytes: int = 110) -> str:
    """Derive a short digest from the first meaningful paragraph, capped by bytes."""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "```", "---", "!")):
            continue
        clean = line.replace("*", "").replace("`", "")
        out: list[str] = []
        used = 0
        for ch in clean:
            used += len(ch.encode("utf-8"))
            if used > max_bytes:
                break
            out.append(ch)
        return "".join(out).rstrip()
    return ""
