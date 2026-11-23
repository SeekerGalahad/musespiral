"""F6 notessql 单测 — 用 tmp_path 临时库覆盖字数过滤/limit/取正文/坏库缺表报错。"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

import pytest

from musespiral.infra.sources import sqlite as notessql
from musespiral.infra.sources.markdown_dir import Note


def _ts(y: int, m: int, d: int) -> float:
    return _dt.datetime(y, m, d, 10, 0, 0).timestamp()


def _sqlite_treats_view_rowid_as_error() -> bool:
    """能力探测：当前 sqlite 构建对「SELECT rowid FROM 视图」是否报错。

    不能按版本号判断——sqlite 官方 ≥3.36 报 no such column，但 Debian/Ubuntu
    发行版构建带 ``-DSQLITE_ALLOW_ROWID_IN_VIEW`` 兼容开关（debian/rules
    CFLAGS，2026-11-23 CI ubuntu-26.04 runner 实证 sqlite 3.46.1 同样放行），
    版本 ≥3.36 也可返回 NULL rowid 而不报错。放行时 _resolve_id_column 的
    rowid 探测成功走 rowid 分支，「无 rowid 也无主键」的可读错误契约前提
    不成立，视图分支断言（本文件该用例）按环境前提跳过；macOS 自带库与
    python-build-standalone 构建无此开关，探测为 False，断言照常执行。
    """
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE TABLE _probe_t (x TEXT)")
        con.execute("CREATE VIEW _probe_v AS SELECT x FROM _probe_t")
        try:
            con.execute('SELECT rowid FROM "_probe_v" LIMIT 0')
            return False  # 放行（发行版兼容开关）→ 契约前提不成立
        except sqlite3.OperationalError:
            return True  # 报错（官方 ≥3.36 行为）→ 契约成立
    finally:
        con.close()


def _make_db(tmp_path: Path) -> tuple[Path, dict[int, str]]:
    """建含 FTS5 表的临时笔记库，返回 (db 路径, rowid->正文 映射)。"""
    db = tmp_path / "vault.db"
    contents = {
        1: "短" * 50,  # 低于默认 200 字门槛
        2: "甲" * 250,
        3: "乙" * 300,
        4: "# 无标题长文\n" + "余" * 220,  # title 为 NULL，回退取正文一级标题
        5: "---\ntitle: 成稿E\ncover: ./assets/c.png\n---\n\n" + "正" * 400,
        6: "",  # 空正文（NULL 场景等价，应被过滤）
    }
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE notes (title TEXT, content TEXT, path TEXT, mtime REAL)"
    )
    rows = [
        ("短笔记", contents[1], "vault/short.md", _ts(2026, 10, 15)),
        ("长笔记A", contents[2], "vault/a.md", _ts(2026, 11, 1)),
        ("长笔记B", contents[3], "vault/b.md", _ts(2026, 11, 5)),
        (None, contents[4], "vault/c.md", _ts(2026, 10, 1)),
        ("成稿E", contents[5], "vault/e.md", _ts(2026, 11, 9)),
        ("空笔记", None, "vault/empty.md", _ts(2026, 11, 10)),
    ]
    con.executemany("INSERT INTO notes (title, content, path, mtime) VALUES (?, ?, ?, ?)", rows)
    # FavVault 式 FTS5 外容表，验证与 FTS 虚拟表共存
    con.execute(
        "CREATE VIRTUAL TABLE notes_fts USING fts5("
        "title, content, content='notes', content_rowid='rowid')"
    )
    con.execute("INSERT INTO notes_fts (notes_fts) VALUES ('rebuild')")
    con.commit()
    con.close()
    return db, contents


def test_list_filters_by_chars_and_orders_newest_first(tmp_path):
    db, contents = _make_db(tmp_path)
    notes = notessql.list_from_db(db)
    # 短笔记(50字)与空正文被过滤，其余 4 条按日期新→旧
    assert [n.title for n in notes] == ["成稿E", "长笔记B", "长笔记A", "无标题长文"]
    assert all(isinstance(n, Note) for n in notes)
    top = notes[0]
    assert top.chars == len(contents[5])
    assert top.date == "2026-11-09"
    assert top.path == Path("vault/e.md")
    assert top.is_article is True  # 带 title+cover frontmatter
    assert notes[1].is_article is False
    assert notes[3].tags == []


def test_list_custom_min_chars(tmp_path):
    db, _ = _make_db(tmp_path)
    notes = notessql.list_from_db(db, min_chars=300)
    assert [n.title for n in notes] == ["成稿E", "长笔记B"]


def test_list_limit(tmp_path):
    db, _ = _make_db(tmp_path)
    assert len(notessql.list_from_db(db, limit=2)) == 2
    assert [n.title for n in notessql.list_from_db(db, limit=2)] == ["成稿E", "长笔记B"]
    # 默认 limit=20：合格 4 条全返回
    assert len(notessql.list_from_db(db)) == 4


def test_get_by_id_hit_and_miss(tmp_path):
    db, contents = _make_db(tmp_path)
    note = notessql.get_by_id(db, 3)
    assert note is not None
    assert note.title == "长笔记B"
    assert note.chars == len(contents[3])
    assert note.date == "2026-11-05"
    assert notessql.get_by_id(db, 99999) is None


def test_custom_table_and_cols_mapping(tmp_path):
    db = tmp_path / "other.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE entries (note_name TEXT, body TEXT, file_path TEXT, updated TEXT)")
    con.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?)", ("自定义列", "文" * 220, "x/y.md", "2026-05-01")
    )
    con.commit()
    con.close()
    cols = {"title": "note_name", "content": "body", "path": "file_path", "mtime": "updated"}
    notes = notessql.list_from_db(db, table="entries", cols=cols)
    assert [n.title for n in notes] == ["自定义列"]
    assert notes[0].date == "2026-05-01"  # ISO 文本 mtime 原样透传
    got = notessql.get_by_id(db, 1, table="entries", cols=cols)
    assert got is not None and got.title == "自定义列"


def test_fts5_table_direct_query(tmp_path):
    """列映射可省 path/mtime：直接查 FTS5 表也能工作。"""
    db, _ = _make_db(tmp_path)
    notes = notessql.list_from_db(
        db, table="notes_fts", cols={"title": "title", "content": "content"}
    )
    assert [n.title for n in notes] == ["成稿E", "长笔记B", "长笔记A", "无标题长文"]
    got = notessql.get_by_id(db, 3, table="notes_fts", cols={"title": "title", "content": "content"})
    assert got is not None and got.title == "长笔记B" and got.date == ""


def test_missing_table_error_is_readable(tmp_path):
    db, _ = _make_db(tmp_path)
    with pytest.raises(ValueError) as ei:
        notessql.list_from_db(db, table="missing_table")
    assert "missing_table" in str(ei.value)
    assert str(db) in str(ei.value)


def test_bad_db_file_error_is_readable(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_text("这不是一个 SQLite 文件", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        notessql.list_from_db(bad)
    assert str(bad) in str(ei.value)
    assert "notes" in str(ei.value)


def test_db_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        notessql.list_from_db(tmp_path / "nope.db")


def test_invalid_rowid_and_missing_content_col(tmp_path):
    db, _ = _make_db(tmp_path)
    with pytest.raises(ValueError):
        notessql.get_by_id(db, "abc")
    with pytest.raises(ValueError, match="content"):
        notessql.list_from_db(db, cols={"title": "title"})


# ── F11d：WITHOUT ROWID 表用主键列作 id；无 rowid 无主键报可读错误 ──


def _make_without_rowid_db(tmp_path: Path) -> Path:
    """TEXT 主键 WITHOUT ROWID 表临时库（SQLite 要求此类表必带 PRIMARY KEY）。"""
    db = tmp_path / "worowid.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE notes (note_id TEXT PRIMARY KEY, title TEXT, "
        "content TEXT, path TEXT, mtime REAL) WITHOUT ROWID"
    )
    con.executemany(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
        [
            ("a1", "键A", "甲" * 250, "vault/a.md", _ts(2026, 11, 1)),
            ("b2", "键B", "乙" * 260, "vault/b.md", _ts(2026, 11, 6)),
        ],
    )
    con.commit()
    con.close()
    return db


def test_without_rowid_text_pk_list_and_get(tmp_path):
    db = _make_without_rowid_db(tmp_path)
    notes = notessql.list_from_db(db)  # 主键列作 id，list 正常
    assert [n.title for n in notes] == ["键B", "键A"]  # 日期新→旧
    got = notessql.get_by_id(db, "b2")  # TEXT 主键值可作 id
    assert got is not None and got.title == "键B"
    assert notessql.get_by_id(db, "zzz") is None


def test_without_rowid_int_pk_get(tmp_path):
    db = tmp_path / "worowid_int.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT, "
        "content TEXT, path TEXT, mtime REAL) WITHOUT ROWID"
    )
    con.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
        (7, "整数键", "丙" * 210, "vault/c.md", "2026-11-08"),
    )
    con.commit()
    con.close()
    notes = notessql.list_from_db(db)
    assert [n.title for n in notes] == ["整数键"]
    got = notessql.get_by_id(db, 7)  # int 主键 id 原样绑定
    assert got is not None and got.title == "整数键"
    assert notessql.get_by_id(db, 8) is None


@pytest.mark.skipif(
    not _sqlite_treats_view_rowid_as_error(),
    reason="当前 sqlite 构建允许视图 rowid（Debian/Ubuntu -DSQLITE_ALLOW_ROWID_IN_VIEW "
    "兼容开关，能力探测见 _sqlite_treats_view_rowid_as_error），「无 rowid 也无主键」"
    "错误路径在该环境不可达，视图分支断言前提不成立",
)
def test_view_without_rowid_or_pk_readable_error(tmp_path):
    """无 rowid 也无主键（视图）→ ValueError 指明根因；list/get 同样报错。"""
    db = tmp_path / "view.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE base (title TEXT, content TEXT, path TEXT, mtime REAL)")
    con.execute("CREATE VIEW notes AS SELECT title, content, path, mtime FROM base")
    con.execute(
        "INSERT INTO base VALUES ('底稿', '丁' * 220, 'vault/d.md', '2026-11-01')"
    )
    con.commit()
    con.close()
    with pytest.raises(ValueError) as ei:
        notessql.list_from_db(db)
    assert "无 rowid 也无主键" in str(ei.value)
    assert "视图" in str(ei.value)
    assert "notes" in str(ei.value)
    with pytest.raises(ValueError, match="无 rowid 也无主键"):
        notessql.get_by_id(db, 1)


# ── F11e：get_by_id 带库内 content，scaffold_article 接受显式正文兜底 ──


def test_get_by_id_returns_db_content(tmp_path):
    db, contents = _make_db(tmp_path)
    note = notessql.get_by_id(db, 3)
    assert note is not None
    assert note.content == contents[3]  # 库内正文随 Note 返回
    assert note.chars == len(note.content)


def test_scaffold_article_with_explicit_content(tmp_path):
    """源文件不存在时用库 content 兜底生成草稿（CLI 提示在集成阶段接线）。"""
    from musespiral.infra.sources.markdown_dir import scaffold_article

    db, contents = _make_db(tmp_path)
    note = notessql.get_by_id(db, 4)  # path 列指向 vault/c.md，tmp_path 下并不存在
    assert note is not None
    assert note.content == contents[4]
    assert not (tmp_path / str(note.path)).exists()

    out = scaffold_article(note.path, tmp_path / "out" / "c.md", content=note.content)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # 正文保留（# 标题行 + 正文段），frontmatter 由 scaffold 补齐
    assert "# 无标题长文" in text
    assert contents[4].splitlines()[1][:30] in text
    assert "status: draft" in text
    # 库正文无 frontmatter 时标题回退正文一级标题（与 _to_note 的回退一致）
    assert "title: 无标题长文" in text


def test_get_by_id_composite_pk_multiple_rows_raises(tmp_path) -> None:
    """第四期遗留 low#4：复合主键首列命中多行时报可读错误，不静默取第一行。"""
    import sqlite3

    from musespiral.infra.sources.sqlite import get_by_id

    db = tmp_path / "wr.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes(user_id INT, note_key TEXT, title TEXT, content TEXT, path TEXT, mtime INT, PRIMARY KEY(user_id, note_key)) WITHOUT ROWID")
    con.execute("INSERT INTO notes VALUES(1, 'a', '甲', ?, 'a.md', 1)", ("x" * 300,))
    con.execute("INSERT INTO notes VALUES(1, 'b', '乙', ?, 'b.md', 2)", ("y" * 300,))
    con.commit()
    con.close()

    with pytest.raises(ValueError, match="命中 2 行"):
        get_by_id(db, 1)
