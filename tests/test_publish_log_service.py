"""publish_log_service 单元测试（第十四期 F36/F36c/F37，正典 §五）。

覆盖：双位置合并去重三例（同键去重 root 优先 / action↔result 键位并存 /
平台或 ts 不同并存）；demo_ 前缀过滤；append_uploaded / append_manual
行形状；坏行宽容。全部走 tmp（MUSESPIRAL_HOME 隔离），不触真实 ~/.musespiral。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.services import publish_log_service as pls


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return path


def _home_log() -> Path:
    from musespiral.infra.storage.jsonl import default_log_path

    return default_log_path()


# ---- 合并去重三例 ----


def test_merge_same_key_dedups_root_wins(tmp_path: Path) -> None:
    """例一：四键全同 → 只留一份，root 侧优先（root 行在前）。"""
    root = tmp_path / "arts"
    row = {
        "ts": "2026-11-14T20:17:37", "action": "draft", "article": "01_quick_start.md",
        "platform": "mp", "media_id": "M1",
    }
    home_copy = {**row, "title": "home 副本"}
    root_copy = {**row, "title": "root 副本"}
    _write_jsonl(pls.root_log_path(root), [root_copy])
    _write_jsonl(_home_log(), [home_copy])

    merged = pls.merge_logs(root)
    assert len(merged) == 1
    assert merged[0]["title"] == "root 副本"  # root 优先：以工作区日志为准


def test_merge_same_key_except_action_or_result_coexist(tmp_path: Path) -> None:
    """例二：ts/article/platform 相同、action↔result 不同 → 键位不同，并存。"""
    root = tmp_path / "arts"
    draft = {"ts": "2026-11-10T09:00:00", "action": "draft", "article": "b.md",
             "platform": "xhs", "media_id": "M1", "title": "草稿"}
    uploaded = {"ts": "2026-11-10T09:00:00", "platform": "xhs", "article": "b.md",
                "result": "uploaded"}
    _write_jsonl(pls.root_log_path(root), [draft])
    _write_jsonl(_home_log(), [uploaded])

    merged = pls.merge_logs(root)
    assert len(merged) == 2
    assert {(m.get("action"), m.get("result")) for m in merged} == {
        ("draft", None),
        (None, "uploaded"),
    }


def test_merge_different_platform_or_ts_coexist(tmp_path: Path) -> None:
    """例三：同 ts+article 换平台、同文章换 ts → 均非同键，全部保留。"""
    root = tmp_path / "arts"
    rows = [
        {"ts": "2026-11-12T10:00:00", "action": "publish", "article": "a.md",
         "platform": "mp", "title": "mp 版"},
        {"ts": "2026-11-12T10:00:00", "action": "publish", "article": "a.md",
         "platform": "zhihu", "title": "zhihu 版"},
        {"ts": "2026-11-12T22:00:00", "action": "publish", "article": "a.md",
         "platform": "mp", "title": "mp 版·晚"},
    ]
    _write_jsonl(pls.root_log_path(root), rows[:1])
    _write_jsonl(_home_log(), rows[1:])

    merged = pls.merge_logs(root)
    assert len(merged) == 3
    assert [r["title"] for r in merged] == ["mp 版", "zhihu 版", "mp 版·晚"]  # root 在前


# ---- demo 过滤 / 坏行宽容 ----


def test_merge_filters_demo_rows_both_sides(tmp_path: Path) -> None:
    """media_id 前缀 demo_ 的行两侧都剔除；非 demo 与无 media_id 的行保留。"""
    root = tmp_path / "arts"
    _write_jsonl(
        pls.root_log_path(root),
        [
            {"ts": "2026-11-11T10:30:00", "action": "draft", "article": "03_photo_essay.md",
             "title": "演示行", "media_id": "demo_draft_media_id_001", "platform": "mp"},
            {"ts": "2026-11-12T10:30:00", "action": "draft", "article": "real.md",
             "title": "真实行", "media_id": "realMid", "platform": "mp"},
        ],
    )
    _write_jsonl(
        _home_log(),
        [
            {"ts": "2026-11-13T10:30:00", "action": "publish", "article": "demo2.md",
             "title": "演示发布", "media_id": "demo_pub_9", "platform": "mp"},
            {"ts": "2026-11-13T11:00:00", "platform": "xhs", "article": "u.md",
             "result": "uploaded"},  # 型一行无 media_id：不受 demo 过滤影响
        ],
    )

    merged = pls.merge_logs(root)
    assert [r.get("title", r.get("article")) for r in merged] == ["真实行", "u.md"]


def test_merge_tolerates_bad_lines_and_missing_files(tmp_path: Path) -> None:
    """坏行（非法 JSON/非对象/空行）跳过不崩；两侧文件缺失返回空。"""
    root = tmp_path / "arts"
    log = pls.root_log_path(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "not-json\n"
        "\n"
        "[1, 2, 3]\n"
        '{"ts": "2026-11-14T20:00:00", "action": "draft", "article": "ok.md",'
        ' "platform": "mp", "media_id": "M"}\n',
        encoding="utf-8",
    )

    merged = pls.merge_logs(root)
    assert len(merged) == 1 and merged[0]["article"] == "ok.md"

    assert pls.merge_logs(tmp_path / "no-such-root") == []  # 双侧均缺 → 空


# ---- append 行形状 ----


def test_append_uploaded_row_shape(tmp_path: Path) -> None:
    """型一：{ts, platform, article, result=uploaded} 四键落 home 日志。"""
    row = pls.append_uploaded("b.md", "xhs")
    assert set(row) == {"ts", "platform", "article", "result"}
    assert row["result"] == "uploaded" and row["platform"] == "xhs" and row["article"] == "b.md"

    lines = _home_log().read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1]) == row  # 原子追加到尾行
    assert pls.merge_logs(None)[-1] == row  # 经单一持有者读回可见


def test_append_manual_row_shape_with_and_without_url(tmp_path: Path) -> None:
    """型二：manual:true 的 publish 行；url 缺省不写键、给值则入行。"""
    row = pls.append_manual("c.md", "mp", "标题甲")
    assert set(row) == {"ts", "action", "platform", "article", "title", "manual"}
    assert row["action"] == "publish" and row["manual"] is True

    row2 = pls.append_manual("c.md", "mp", "标题乙", url="https://mp.weixin.qq.com/s/x")
    assert row2["url"] == "https://mp.weixin.qq.com/s/x"

    lines = [json.loads(x) for x in _home_log().read_text(encoding="utf-8").splitlines()]
    assert lines[-2] == row and lines[-1] == row2


def test_append_log_path_override_targets_root_side(tmp_path: Path) -> None:
    """log_path 显式改指 root 侧（Studio 工作区日志场景），不落 home。"""
    root = tmp_path / "arts"
    pls.append_manual("a.md", "mp", "标题", log_path=pls.root_log_path(root))
    assert pls.root_log_path(root).is_file()
    assert not _home_log().exists()
    assert pls.merge_logs(root)[0]["article"] == "a.md"  # root 侧可读回


# ---- summarize（合并读序上的统计聚合，第十四期集成） ----


def test_summarize_merges_dual_logs_and_excludes_demo(tmp_path: Path) -> None:
    """统计口径与周历同源：root∪home 合并计数、demo_ 前缀行判除。

    root 放 1 条 demo 行 + 1 条真实 draft；home 放 1 条真实 publish——
    聚合应只见 2 条真实行（demo 不进 total/by_platform/recent 任何一处）。
    """
    root = tmp_path / "arts"
    _write_jsonl(
        pls.root_log_path(root),
        [
            {
                "ts": "2026-11-11T10:30:00", "action": "draft", "article": "03_photo_essay.md",
                "title": "演示行", "media_id": "demo_draft_media_id_001", "platform": "mp",
            },
            {
                "ts": "2026-11-12T09:00:00", "action": "draft", "article": "a.md",
                "title": "root 真实草稿", "platform": "mp",
            },
        ],
    )
    _write_jsonl(
        _home_log(),
        [
            {
                "ts": "2026-11-13T09:00:00", "action": "publish", "article": "a.md",
                "title": "home 真实发表", "platform": "xhs",
            },
        ],
    )

    s = pls.summarize(root)
    assert s["total_draft"] == 1 and s["total_publish"] == 1  # demo 行不计
    assert set(s["by_platform"]) == {"mp", "xhs"}
    assert len(s["recent"]) == 2
    assert all(r.get("media_id") != "demo_draft_media_id_001" for r in s["recent"])
    assert all("演示行" != r.get("title") for r in s["by_platform"]["mp"]["recent"])


def test_summarize_structure_parity_with_jsonl_summarize(tmp_path: Path) -> None:
    """返回结构与 infra jsonl.summarize 逐键一致（同一聚合实现，口径不漂移）。"""
    from musespiral.infra.storage import jsonl

    rows = [
        {"ts": "2026-11-12T10:00:00", "action": "draft", "article": "a.md", "platform": "mp"},
        {"ts": "2026-11-12T11:00:00", "action": "publish", "article": "a.md", "platform": "xhs"},
    ]
    _write_jsonl(_home_log(), rows)

    assert pls.summarize(None) == jsonl.summarize(_home_log())
