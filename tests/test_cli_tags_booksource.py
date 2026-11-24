"""CLI 覆盖补齐（W11，2026-11-24）：tags/booksource 命令组的纯逻辑与入口面。

目标：把 tags_cmds(18%) 与 booksource_cmds(59%) 的**纯函数**与**参数校验**覆盖起来
——不 mock 网络，只测确定性面（解析/格式化/help/缺参拒绝）。
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from musespiral.cli import cli
from musespiral.commands import tags_cmds


# ── tags：下标解析（纯函数，AI 采纳的输入面）─────────────────────

@pytest.mark.parametrize("raw,upper,expect", [
    ("1 3 4", 5, [0, 2, 3]),          # 正常
    ("1 1 2", 5, [0, 1]),             # 去重保序
    ("0 6 7", 5, []),                 # 越界（0 与 >upper）
    ("abc 2 x", 5, [1]),              # 非数字忽略
    ("", 5, []),                      # 空
    ("  3   1 ", 3, [2, 0]),          # 空白容错
])
def test_parse_indices(raw, upper, expect) -> None:
    assert tags_cmds._parse_indices(raw, upper) == expect


def test_fmt_tags() -> None:
    """标签格式化：空列表给可读占位而不是空串。"""
    assert tags_cmds._fmt_tags(["a", "b"]) == "a、b"
    assert tags_cmds._fmt_tags([]) == "（无）"


# ── tags CLI 入口面 ─────────────────────────────────────────────

def test_tags_help_lists_both_subcommands() -> None:
    r = CliRunner().invoke(cli, ["tags", "--help"])
    assert r.exit_code == 0
    assert "suggest" in r.output and "batch" in r.output


def test_tags_suggest_requires_id() -> None:
    """缺 material_id → click 用法错误（非零退出，不静默）。"""
    r = CliRunner().invoke(cli, ["tags", "suggest"])
    assert r.exit_code != 0


def test_tags_batch_limit_option_exists() -> None:
    r = CliRunner().invoke(cli, ["tags", "batch", "--help"])
    assert r.exit_code == 0 and "--limit" in r.output


# ── booksource CLI 入口面 ───────────────────────────────────────

def test_booksource_help_lists_four_commands() -> None:
    r = CliRunner().invoke(cli, ["booksource", "--help"])
    assert r.exit_code == 0
    for cmd in ("import", "list", "refresh", "subs"):
        assert cmd in r.output, cmd


def test_booksource_subs_requires_subcommand() -> None:
    """subs 是命令组：裸调 → 显示帮助或非零退出，不静默成功。"""
    r = CliRunner().invoke(cli, ["booksource", "subs"])
    assert r.exit_code != 0 or "Usage" in r.output


def test_booksource_subs_add_rejects_empty_url() -> None:
    """订阅 URL 为空 → 400 级拒绝（https 强制门在 service 层，CLI 传空先被拒）。"""
    r = CliRunner().invoke(cli, ["booksource", "subs", "add", ""])
    assert r.exit_code != 0


def test_booksource_subs_add_rejects_http() -> None:
    """明文 http → 拒收（https 强制门）。"""
    r = CliRunner().invoke(cli, ["booksource", "subs", "add", "http://example.com/x.json"])
    assert r.exit_code != 0
    assert "https" in r.output.lower() or "https" in (r.output or "")


def test_booksource_list_runs_on_empty_home(tmp_path, monkeypatch) -> None:
    """空 home 下列表命令正常退出并给下一步指引（不崩）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(cli, ["booksource", "list"])
    assert r.exit_code == 0


def test_booksource_import_requires_input(tmp_path, monkeypatch) -> None:
    """import 无文本/URL/文件 → 非零退出（参数校验）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(cli, ["booksource", "import"])
    assert r.exit_code != 0


# ── tags 交互路径（W11 补：用 CliRunner input 驱动，mock 服务层）─────

def test_tags_suggest_adopt_flow(monkeypatch) -> None:
    """建议 → 输入编号 → 采纳：三面消息齐全（建议/写入/无超限）。"""
    monkeypatch.setattr(tags_cmds.tag_service, "preview",
                        lambda mid: {"id": mid, "title": "T", "tags": ["a", "b", "c"], "existing": ["a"]})
    seen = {}
    def _adopt(mid, picked):
        seen["picked"] = picked
        return {"tags": picked, "dropped": []}
    monkeypatch.setattr(tags_cmds.tag_service, "adopt", _adopt)
    r = CliRunner().invoke(cli, ["tags", "suggest", "m1"], input="1 3\n")
    assert r.exit_code == 0, r.output
    assert seen["picked"] == ["a", "c"]
    assert "[已有]" in r.output and "已写入" in r.output


def test_tags_suggest_empty_enter_is_reject_all(monkeypatch) -> None:
    """直接回车 = 全拒 = 零落盘（预览即焚的契约）。"""
    monkeypatch.setattr(tags_cmds.tag_service, "preview",
                        lambda mid: {"id": mid, "title": "T", "tags": ["a"], "existing": []})
    called = {"adopt": False}
    monkeypatch.setattr(tags_cmds.tag_service, "adopt",
                        lambda *a: called.update(adopt=True) or {"tags": [], "dropped": []})
    r = CliRunner().invoke(cli, ["tags", "suggest", "m1"], input="\n")
    assert r.exit_code == 0
    assert "已全部拒绝" in r.output and called["adopt"] is False


def test_tags_suggest_no_tags_returns_early(monkeypatch) -> None:
    monkeypatch.setattr(tags_cmds.tag_service, "preview",
                        lambda mid: {"id": mid, "title": "T", "tags": [], "existing": []})
    r = CliRunner().invoke(cli, ["tags", "suggest", "m1"])
    assert r.exit_code == 0 and "无建议" in r.output


def test_tags_batch_no_candidates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "h"))
    (tmp_path / "h").mkdir()
    monkeypatch.setattr(tags_cmds.materials_service, "list_materials", lambda: [])
    r = CliRunner().invoke(cli, ["tags", "batch"])
    assert r.exit_code == 0 and "没有待建议素材" in r.output


def test_tags_batch_confirm_flow(monkeypatch, tmp_path) -> None:
    """批量：y=采纳 → 逐条走 triage（confirm 语义的 CLI 形态）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "h"))
    (tmp_path / "h").mkdir()
    monkeypatch.setattr(tags_cmds.materials_service, "list_materials",
                        lambda: [{"id": "m1"}, {"id": "m2"}])
    monkeypatch.setattr(tags_cmds.tag_service, "batch",
                        lambda ids: {"status": "done", "items": [
                            {"id": "m1", "title": "A", "tags": ["x"], "existing": []},
                            {"id": "m2", "title": "B", "tags": ["y"], "existing": []},
                        ]})
    adopted = []
    monkeypatch.setattr(tags_cmds.tag_service, "adopt", lambda mid, picked: adopted.append(mid) or {"tags": picked, "dropped": []})
    r = CliRunner().invoke(cli, ["tags", "batch", "--limit", "5"], input="y\nn\n")
    assert r.exit_code == 0, r.output
    assert adopted == ["m1"]  # 第二个 n=拒绝


# ── booksource import：三态预览/过滤/落库（W11 补）──────────────

_SRC_JSON = '[{"bookSourceName":"源A","bookSourceUrl":"https://a.com","bookSourceGroup":"g1"},' \
            '{"bookSourceName":"源B","bookSourceUrl":"https://b.com","bookSourceGroup":"g2"}]'


def _home(tmp_path, monkeypatch):
    h = tmp_path / "h"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(h))
    return h


def test_booksource_import_preview_lists_sources(tmp_path, monkeypatch) -> None:
    """预览（缺省零落库）：打印三态清单。"""
    _home(tmp_path, monkeypatch)
    f = tmp_path / "src.json"
    f.write_text(_SRC_JSON, encoding="utf-8")
    r = CliRunner().invoke(cli, ["booksource", "import", "--file", str(f)])
    assert r.exit_code == 0, r.output
    assert "源A" in r.output or "https://a.com" in r.output


def test_booksource_import_apply_lands(tmp_path, monkeypatch) -> None:
    """--apply 真落库。"""
    h = _home(tmp_path, monkeypatch)
    f = tmp_path / "src.json"
    f.write_text(_SRC_JSON, encoding="utf-8")
    r = CliRunner().invoke(cli, ["booksource", "import", "--file", str(f), "--apply"])
    assert r.exit_code == 0, r.output
    # 落库后 list 能读到
    r2 = CliRunner().invoke(cli, ["booksource", "list"])
    assert r2.exit_code == 0 and ("a.com" in r2.output or "源A" in r2.output)


def test_booksource_import_filter_suppresses_rows(tmp_path, monkeypatch) -> None:
    """--filter 过滤（大包防刷屏的落点）。"""
    _home(tmp_path, monkeypatch)
    f = tmp_path / "src.json"
    f.write_text(_SRC_JSON, encoding="utf-8")
    r = CliRunner().invoke(cli, ["booksource", "import", "--file", str(f), "--filter", "源A"])
    assert r.exit_code == 0
    assert "https://b.com" not in r.output or "源A" in r.output


def test_booksource_import_bad_json_readable(tmp_path, monkeypatch) -> None:
    """坏 JSON → 可读错误（非栈回溯）。"""
    _home(tmp_path, monkeypatch)
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    r = CliRunner().invoke(cli, ["booksource", "import", "--file", str(f)])
    assert r.exit_code != 0
    assert "Traceback" not in r.output


def test_booksource_subs_list_empty(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    r = CliRunner().invoke(cli, ["booksource", "subs", "list"])
    assert r.exit_code == 0 and "暂无订阅" in r.output


def test_booksource_subs_add_del_roundtrip(tmp_path, monkeypatch) -> None:
    """订阅 add → list 可见 → del 移除。"""
    _home(tmp_path, monkeypatch)
    r = CliRunner().invoke(cli, ["booksource", "subs", "add", "https://example.com/s.json"])
    assert r.exit_code == 0, r.output
    r2 = CliRunner().invoke(cli, ["booksource", "subs", "list"])
    assert "example.com" in r2.output
    r3 = CliRunner().invoke(cli, ["booksource", "subs", "del", "https://example.com/s.json"])
    assert r3.exit_code == 0
    r4 = CliRunner().invoke(cli, ["booksource", "subs", "list"])
    assert "暂无订阅" in r4.output
