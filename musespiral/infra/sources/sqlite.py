"""SQLite/FTS5 笔记源（Phase C 迁入 infra/sources/，正典 §八） — 直连笔记库（如 FavVault）取候选笔记。

R6 自顶层 notessql.py 全量迁入（正典 §四-R6：notessql 全量迁移，
原模块留 re-export 兼容）。函数体逐字节保留，仅两处归属调整：
docstring 更新 + `from .notes import ...` 改为 `from .markdown_dir import ...`
（Note/_first_line_title 现居本包）。

FavVault 等笔记库的正文在 SQLite 里，markdown_dir.scan 的目录扫描覆盖不到。
本模块做只读查询：表名与列名映射全部可配（不绑死任何笔记库 schema），
v1 只做全表扫描 + 字数过滤，不做 FTS MATCH（notes search 预留）。

复用 markdown_dir.Note 数据类：content 用于字数过滤与 chars 统计，并随
Note 的 content 字段返回（F11e：草稿源文件缺失时兜底正文）；草稿默认仍走
path 列指向的源文件。坏库/缺表/缺列统一抛出带库路径与表名的 ValueError。
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import frontmatter

from .markdown_dir import Note, _first_line_title

# 逻辑字段 -> 实际列名的默认映射
DEFAULT_COLS: dict[str, str] = {
    "title": "title",
    "content": "content",
    "path": "path",
    "mtime": "mtime",
}

_KEYS = ("title", "content", "path", "mtime")


def _quote_ident(name: str) -> str:
    """转义 SQL 标识符（表名/列名来自用户配置，不能裸拼）。"""
    return '"' + name.replace('"', '""') + '"'


def _connect(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"笔记库不存在: {p}")
    return sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)


def _resolve_id_column(
    con: sqlite3.Connection, db_path: str | Path, table: str
) -> tuple[str, str | None]:
    """解析笔记 id 列（F11d 去 rowid 硬编码）。

    返回 (id 的 SQL 表达式, 主键列名)；主键列名非 None 表示 WITHOUT ROWID
    表在用显式主键作 id。探测规则：先试 SELECT rowid —— 可用即 rowid 表
    （含 FTS5 虚拟表与 INTEGER PRIMARY KEY 别名）；报「no such column」则
    是无 rowid 的对象（WITHOUT ROWID 表/视图），改用 PRAGMA table_info 的
    主键列（复合主键取首列）；连主键也没有 → ValueError 指明根因。
    其余错误（表不存在/坏库）原样冒出，交给调用方统一转可读 ValueError。
    """
    tbl = _quote_ident(table)
    try:
        con.execute(f"SELECT rowid FROM {tbl} LIMIT 0")
        return "rowid", None
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc):
            raise
        pk_cols = [
            r[1]
            for r in sorted(con.execute(f"PRAGMA table_info({tbl})"), key=lambda r: r[5])
            if r[5]
        ]
        if not pk_cols:
            raise ValueError(
                f"笔记库 {Path(db_path)} 的表 {table!r} 无 rowid 也无主键列，"
                "无法定位笔记 id（根因: 视图或未定义 PRIMARY KEY 的表，"
                "请改为查询带主键的实体表）"
            ) from None
        return _quote_ident(pk_cols[0]), pk_cols[0]


def _fetch(
    db_path: str | Path,
    table: str,
    cols: Mapping[str, str],
    id_value: Any | None = None,
) -> list[dict[str, Any]]:
    """按 cols 映射取行并归一为逻辑字段字典；坏库/缺表/缺列转 ValueError。

    id 列经 _resolve_id_column 解析（rowid 表用 rowid、WITHOUT ROWID 表用
    主键列）。id_value 非 None 时按该列过滤单行（get_by_id 用）；rowid 表
    要求 id 可转 int，主键列表原样绑定（TEXT 主键可传 str）。
    """
    if not cols.get("content"):
        raise ValueError("cols 映射缺少 content 键（正文列必需）")
    present = [k for k in _KEYS if cols.get(k)]
    select = ", ".join(f"{_quote_ident(cols[k])} AS {_quote_ident(k)}" for k in present)
    tbl = _quote_ident(table)
    try:
        con = _connect(db_path)
        try:
            id_expr, pk_col = _resolve_id_column(con, db_path, table)
            where, params = "", ()
            if id_value is not None:
                if pk_col is None:  # rowid 表：id 必须可转 int
                    try:
                        rid = int(id_value)
                    except (TypeError, ValueError):
                        raise ValueError(f"无效的笔记 id: {id_value!r}") from None
                    where, params = " WHERE rowid = ?", (rid,)
                else:  # WITHOUT ROWID 表：主键值原样绑定
                    where, params = f" WHERE {id_expr} = ?", (id_value,)
            # SQLite 对不存在的双引号标识符有「退化为字符串字面量」的兼容，
            # 列名写错时 SELECT 仍成功并返回垃圾串 —— 先用 PRAGMA 校验列存在
            existing = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
            if existing:  # 空结果 = 表不存在，交给下方 SELECT 报可读错误
                missing = [cols[k] for k in present if cols[k] not in existing]
                if missing:
                    raise ValueError(
                        f"笔记库 {Path(db_path)} 的表 {table!r} 缺少列: {', '.join(missing)}"
                        "（请检查表名或列名映射 cols）"
                    )
            sql = f"SELECT {id_expr}, {select} FROM {tbl}{where}"
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"无法读取笔记库 {Path(db_path)} 的表 {table!r}: {exc}") from exc
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {"rowid": row[0]}  # 逻辑 id：rowid 或 WITHOUT ROWID 表的主键值
        for i, key in enumerate(present, start=1):
            item[key] = row[i]
        out.append(item)
    return out


def _mtime_str(value: Any) -> str:
    """mtime 兼容数字时间戳与 ISO 文本，统一为字符串。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return _dt.date.fromtimestamp(value).isoformat()
        except (OverflowError, OSError, ValueError):  # 毫秒级等异常纪元
            return str(value)
    return str(value)


def _has_article_frontmatter(content: str) -> bool:
    """与 markdown_dir._load_note 同规则：已带 musespiral 所需 frontmatter 视为成稿。"""
    try:
        md = frontmatter.loads(content).metadata
    except Exception:
        return False
    return "cover" in md and "title" in md


def _to_note(row: Mapping[str, Any], content: str) -> Note:
    path_val = str(row.get("path") or "")
    title = str(row.get("title") or "").strip()
    if not title:
        title = (
            _first_line_title(content)
            or (Path(path_val).stem if path_val else "")
            or f"rowid {row.get('rowid')}"
        )
    return Note(
        path=Path(path_val),
        title=title,
        tags=[],
        date=_mtime_str(row.get("mtime")),
        chars=len(content),
        is_article=_has_article_frontmatter(content),
        content=content,  # F11e：库内正文随 Note 返回，供草稿源文件缺失时兜底
    )


def list_from_db(
    db_path: str | Path,
    table: str = "notes",
    cols: Mapping[str, str] | None = None,
    *,
    min_chars: int = 200,
    limit: int = 20,
) -> list[Note]:
    """全表扫描笔记库，按正文字数过滤，日期新→旧取前 limit 条。"""
    rows = _fetch(db_path, table, cols or DEFAULT_COLS)
    notes: list[Note] = []
    for row in rows:
        content = row["content"] or ""
        if len(content) < min_chars:
            continue
        notes.append(_to_note(row, content))
    notes.sort(key=lambda n: (n.date, n.chars), reverse=True)
    return notes[:limit]


def get_by_id(
    db_path: str | Path,
    rowid: int | str,
    table: str = "notes",
    cols: Mapping[str, str] | None = None,
) -> Note | None:
    """按笔记 id 取单篇（供草稿流程定位源文件）；不存在返回 None。

    id 列由表结构决定（F11d）：rowid 表传 rowid（int），WITHOUT ROWID 表
    传主键值（int 或 str 均可）。返回的 Note 带 content 库内正文（F11e）。

    复合主键表按首列过滤命中多行时抛 ValueError（第四期遗留 low 修复：
    静默取第一行可能取到非预期笔记）。
    """
    rows = _fetch(db_path, table, cols or DEFAULT_COLS, id_value=rowid)
    if not rows:
        return None
    if len(rows) > 1:
        raise ValueError(
            f"id={rowid!r} 在表 {table} 命中 {len(rows)} 行（复合主键首列不唯一），"
            "无法确定唯一笔记——请改用唯一键列或细化查询"
        )
    row = rows[0]
    return _to_note(row, row["content"] or "")
