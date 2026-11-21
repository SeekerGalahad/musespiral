"""CLI discover 命令族逐字节快照（第二十期 §〇/§六-M2：收编前后输出一致）。

发现页收编（素材库）不得改变 CLI 行为——本测试是「收编前基线 vs 当前
代码」的固定 fixture 对拍（正典 §六 M2「CLI 不变断言」，快照只增不删）：

  - 基线：tests/fixtures/discover_cli/golden/*.txt|json（2026-11-15 于
    M2 动工前自真实 CLI 捕获——git stash 式对拍的落盘形态）；
  - 复跑：同 fixture KB（tests/fixtures/discover_cli/kb）+ 隔离 MUSESPIRAL_HOME
    （discover_cli/home，空目录——scan/suggest 只读 discovered.json 零写入）；
  - 逐字节比较，唯一归一化 = fixture 根绝对路径前缀 → <FIXTURES>（扫描器
    输出绝对路径，机器/克隆位置相关；其余字节零容忍）。

确定性设计：fixture 文档 created 落 2018-2020（F27 新鲜度维恒 0 分——
输出与运行日期无关）；无 ai-knowledge-base 目录名（族词典恒 None）。
基线漂移（输出变更）= 测试红：要么是回归（修代码），要么是有意变更
（重新捕获基线并只在快照「只增」语义下提交）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.cli import cli

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discover_cli"
GOLDEN = FIXTURES / "golden"
KB = FIXTURES / "kb"

# 收编前后对拍面（与基线捕获时同参）：scan 文本/JSON/--all + suggest
_CASES = {
    "scan.txt": ["discover", "scan", "--source", str(KB)],
    "scan.json": ["discover", "scan", "--source", str(KB), "--json"],
    "scan-all.txt": ["discover", "scan", "--source", str(KB), "--all"],
    "suggest.txt": ["discover", "suggest", "--source", str(KB), "--limit", "3"],
}


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch) -> None:
    """MUSESPIRAL_HOME 指向固定空 fixture home（不触 ~/.musespiral；scan/suggest 零写入）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(FIXTURES / "home"))


def _normalize(output: str, golden: bytes) -> bytes:
    """唯一归一化：fixture 根绝对路径前缀 → <FIXTURES>。

    只在基线本身含 <FIXTURES>（路径承载面：scan）时断言替换真实发生——
    suggest 输出无路径，零替换零容忍直比。
    """
    if b"<FIXTURES>" in golden:
        assert str(FIXTURES) in output, "输出未含 fixture 根路径——基线与复跑环境已不同源"
    return output.replace(str(FIXTURES), "<FIXTURES>").encode("utf-8")


@pytest.mark.parametrize("golden_name", sorted(_CASES))
def test_discover_cli_output_byte_identical(golden_name: str) -> None:
    """收编前后 musespiral discover scan/suggest 输出逐字节一致（固定 fixture 快照对拍）。"""
    result = CliRunner().invoke(cli, _CASES[golden_name], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    expected = (GOLDEN / golden_name).read_bytes()
    assert _normalize(result.output, expected) == expected, (
        f"CLI 输出与收编前基线不一致: {golden_name}——若是回归请修代码；"
        "若是有意变更请按 golden 捕获流程重录（快照只增不删）"
    )


def test_golden_covers_all_cases() -> None:
    """快照只增不删：golden 目录恰覆盖全部对拍面（缺=漏录，多=过期）。"""
    assert {p.name for p in GOLDEN.iterdir()} == set(_CASES)
