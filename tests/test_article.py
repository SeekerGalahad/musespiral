"""core.article 状态机与回链（R3）单元测试。

迁移矩阵全枚举 / set_status 仅改 status 行（正文与其余键字节原样）/ 旧文
读作 draft 不回写 / mark_published 人审闸门 / body_sha1 契约 / status
命令与 publish 成功置位（publish_flow 全程 mock，不真实联网）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.cli import cli
from musespiral.commands.status_cmds import status as status_cmd
from musespiral.core.article import (
    ARCHIVED,
    DRAFT,
    PUBLISHED,
    REVIEWED,
    STATUSES,
    body_sha1,
    mark_published,
    read_status,
    set_status,
)

# 合法迁移全枚举基线（正典 §四-R3：draft→reviewed、reviewed→published、
# 任意→draft、draft/published→archived；draft→draft 为幂等重置不落盘）
LEGAL = {
    (DRAFT, DRAFT),
    (DRAFT, REVIEWED),
    (DRAFT, ARCHIVED),
    (REVIEWED, DRAFT),
    (REVIEWED, PUBLISHED),
    (PUBLISHED, DRAFT),
    (PUBLISHED, ARCHIVED),
    (ARCHIVED, DRAFT),
}


def _article(tmp_path: Path, status: str | None = None, body: str = "正文第一段。\n\n## 小节\n\n要点。\n") -> Path:
    fm = "---\ntitle: 样例\ntags: [测试, 状态机]\n"
    if status is not None:
        fm += f"status: {status}\n"
    fm += "---\n\n"
    p = tmp_path / "a.md"
    p.write_text(fm + body, encoding="utf-8")
    return p


# ---- 迁移矩阵全枚举 ----


@pytest.mark.parametrize("cur", STATUSES)
@pytest.mark.parametrize("new", STATUSES)
def test_transition_matrix_full_enumeration(tmp_path: Path, cur: str, new: str) -> None:
    """16 组合逐一过 set_status：合法迁移落盘生效，非法迁移 ValueError。"""
    p = _article(tmp_path, status=cur)
    if (cur, new) in LEGAL:
        old = set_status(p, new)
        assert old == cur
        assert read_status(p) == new
    else:
        with pytest.raises(ValueError, match="非法状态迁移"):
            set_status(p, new)
        assert read_status(p) == cur  # 拒绝后状态不动


# ---- set_status 只改 status 键（字节级）----


def test_set_status_replaces_only_status_line_bytes(tmp_path: Path) -> None:
    """重写后除 status 行外逐字节一致：inline 列表/注释/键序/CRLF 全保留。"""
    p = tmp_path / "rich.md"
    p.write_bytes(
        "---\r\n"
        "title: 样例  # 标题注释\r\n"
        "tags: [性能, 缓存]\r\n"
        "status: draft\r\n"
        "digest: \"摘要保持原样\"\r\n"
        "cover: cover.png\r\n"
        "---\r\n\r\n"
        "正文第一段。\r\n\r\n"
        "## 小节\r\n\r\n"
        "要点。\r\n".encode("utf-8")
    )
    old_lines = p.read_text(encoding="utf-8", newline="").splitlines(keepends=True)
    set_status(p, REVIEWED)
    new_lines = p.read_text(encoding="utf-8", newline="").splitlines(keepends=True)
    # 唯一差异：status 行值替换（行尾风格原样），其余行逐字节一致
    assert new_lines == [*old_lines[:3], "status: reviewed\r\n", *old_lines[4:]]


def test_set_status_inserts_single_line_when_key_missing(tmp_path: Path) -> None:
    """缺 status 键时只在闭合界定符前插入一行，其余行逐字节不动。"""
    p = _article(tmp_path, status=None)
    before = p.read_text(encoding="utf-8").splitlines(keepends=True)
    set_status(p, REVIEWED)
    after = p.read_text(encoding="utf-8").splitlines(keepends=True)
    assert after == [*before[:3], "status: reviewed\n", *before[3:]]  # 插在闭合 --- 前
    assert read_status(p) == REVIEWED


def test_set_status_without_frontmatter_prepends_block(tmp_path: Path) -> None:
    """整篇无 frontmatter：文件头补最小 status 块，原字节整体下移为正文。"""
    p = tmp_path / "raw.md"
    p.write_text("裸正文一段。\n\n## 小节\n", encoding="utf-8")
    set_status(p, REVIEWED)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\nstatus: reviewed\n---\n")
    assert text.endswith("\n裸正文一段。\n\n## 小节\n")
    assert read_status(p) == REVIEWED


def test_draft_to_draft_is_legal_noop_no_writeback(tmp_path: Path) -> None:
    """draft→draft 合法（任意→draft 的幂等重置）但不落盘。"""
    p = _article(tmp_path, status=None)  # 缺 status 键，读作 draft
    before = p.read_bytes()
    assert set_status(p, DRAFT) == DRAFT
    assert p.read_bytes() == before  # 不回写（与读取侧「缺省不回写」同哲学）


def test_atomic_rewrite_leaves_no_temp_files(tmp_path: Path) -> None:
    p = _article(tmp_path, status=DRAFT)
    set_status(p, REVIEWED)
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "a.md"]
    assert leftovers == []


# ---- 读取侧 ----


def test_read_status_missing_reads_draft_no_writeback(tmp_path: Path) -> None:
    """旧文章缺 status 读作 draft；读取绝不回写。"""
    p = _article(tmp_path, status=None)
    before = p.read_bytes()
    assert read_status(p) == DRAFT
    assert p.read_bytes() == before
    p2 = tmp_path / "nofm.md"  # 整篇无 frontmatter 同口径
    p2.write_text("正文而已。\n", encoding="utf-8")
    assert read_status(p2) == DRAFT


def test_read_status_unknown_value_returned_as_is(tmp_path: Path) -> None:
    """未知状态值原样返回（校验只在写入侧）；其唯一出路是人工重置回 draft。"""
    p = _article(tmp_path, status="bogus")
    assert read_status(p) == "bogus"
    with pytest.raises(ValueError, match="非法状态迁移"):
        set_status(p, REVIEWED)
    set_status(p, DRAFT)  # 恢复路径
    assert read_status(p) == DRAFT


# ---- expect / 参数校验 ----


def test_set_status_expect_mismatch_rejected(tmp_path: Path) -> None:
    p = _article(tmp_path, status=DRAFT)
    with pytest.raises(ValueError, match="期望 reviewed，实际 draft"):
        set_status(p, PUBLISHED, expect=REVIEWED)
    assert read_status(p) == DRAFT


def test_set_status_unknown_new_status_rejected(tmp_path: Path) -> None:
    p = _article(tmp_path, status=DRAFT)
    with pytest.raises(ValueError, match="未知状态"):
        set_status(p, "bogus")
    with pytest.raises(ValueError, match="未知状态"):
        set_status(p, "")  # click.Choice 之外的空串也在此拦截


# ---- mark_published（人审闸门）----


@pytest.mark.parametrize("cur", [DRAFT, PUBLISHED, ARCHIVED])
def test_mark_published_rejected_unless_reviewed(tmp_path: Path, cur: str) -> None:
    p = _article(tmp_path, status=cur)
    with pytest.raises(ValueError, match="--set reviewed"):
        mark_published(p)
    assert read_status(p) == cur


def test_mark_published_from_reviewed(tmp_path: Path) -> None:
    p = _article(tmp_path, status=REVIEWED)
    mark_published(p)
    assert read_status(p) == PUBLISHED


# ---- body_sha1（R3 回链契约）----


def test_body_sha1_contract() -> None:
    assert re.fullmatch(r"[0-9a-f]{12}", body_sha1("正文内容"))
    assert body_sha1("  正文内容 \n") == body_sha1("正文内容")  # strip 归一化
    assert body_sha1("正文A") != body_sha1("正文B")  # 内容变更可识别漂移
    assert body_sha1("") == body_sha1("\n")  # 空正文稳定


# ---- CLI：musespiral status（已注册进 cli.py；此处直接对命令对象断言）----


def test_status_cmd_readonly_display(tmp_path: Path) -> None:
    p = _article(tmp_path, status=DRAFT)
    result = CliRunner().invoke(status_cmd, [str(p)], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert "当前状态: draft" in result.output
    assert "reviewed" in result.output  # 合法迁移提示


def test_status_cmd_set_transitions(tmp_path: Path) -> None:
    p = _article(tmp_path, status=DRAFT)
    result = CliRunner().invoke(status_cmd, [str(p), "--set", "reviewed"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert "draft → reviewed" in result.output
    assert read_status(p) == REVIEWED


def test_status_cmd_illegal_set_exits_nonzero(tmp_path: Path) -> None:
    p = _article(tmp_path, status=ARCHIVED)
    result = CliRunner().invoke(status_cmd, [str(p), "--set", "reviewed"], prog_name="musespiral")
    assert result.exit_code == 1
    assert "非法状态迁移" in result.output
    assert read_status(p) == ARCHIVED


def test_status_cmd_published_not_manually_settable(tmp_path: Path) -> None:
    """--set 不接受 published：publish 成功是唯一自动入位路径。"""
    p = _article(tmp_path, status=REVIEWED)
    result = CliRunner().invoke(status_cmd, [str(p), "--set", "published"], prog_name="musespiral")
    assert result.exit_code != 0
    assert read_status(p) == REVIEWED


# ---- CLI：publish 成功置位（publish_flow mock，网络全 mock）----


def _invoke_publish(monkeypatch, tmp_path: Path, article: Path):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 渲染器/日志隔离到 tmp
    monkeypatch.setattr("musespiral.platforms.wechat.publish_flow", lambda *a, **k: "MEDIA77")
    return CliRunner().invoke(cli, ["publish", str(article), "--skip-check"])


def test_publish_success_keeps_reviewed(tmp_path, monkeypatch) -> None:
    """C1（第十四期）：publish=建草稿——已 reviewed 保持，不再自动置 published。"""
    p = _article(tmp_path, status=REVIEWED, body="# 标题\n\n正文段。\n")
    result = _invoke_publish(monkeypatch, tmp_path, p)
    assert result.exit_code == 0, result.output
    assert "草稿创建成功" in result.output
    assert read_status(p) == REVIEWED


def test_publish_success_from_draft_marks_reviewed_no_warn(tmp_path, monkeypatch) -> None:
    """C1：draft→reviewed 自动置位成功，不再出「状态置位失败」WARN。"""
    p = _article(tmp_path, status=None, body="# 标题\n\n正文段。\n")
    result = _invoke_publish(monkeypatch, tmp_path, p)
    assert result.exit_code == 0, result.output
    assert "草稿创建成功" in result.output
    assert "⚠️" not in result.output  # 原 mark_published 冲突恒 WARN 的回归锚
    assert read_status(p) == REVIEWED
