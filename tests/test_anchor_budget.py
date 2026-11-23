"""实现字符串锚预算门（2026-11-24 W4 测试治理批）。

实测盘点：34 个测试文件共 **1,015 处** `assert "文本" in src` 形态的源码字符串锚，
其中约 775 处是「实现文本类」（纯文案/HTML 片段——改实现就红，但不抓真 bug），
126 处是「行为契约类」（API 路径/事件绑定/data-*——有保留价值）。

**本门不是要求立刻清零**，而是：
  1. 冻结存量（每文件锚数不得**净增**——只许减少）；
  2. 新测试文件从第一行起就受预算约束（0 锚起步，用行为断言）。

防腐判例：本批实测中 `test_studio_ui.py` 锚死 `v0.5.1` 版本号与
`class="mat-row open"` 的 bug 文本——每次发版/修 bug 必红，维护成本高于价值。
"""
from __future__ import annotations

import re
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent

# 存量预算（2026-11-24 盘点值；只许降不许升——改一处实现文本锚为行为断言后，
# 在此同步减数。全量清零目标不设时限，以「新代码零增长」为硬约束）
BUDGET = {
    "test_studio_ui.py": 827,
    "test_studio_ui_wiring.py": 65,
    "test_booksource_import_modal.py": 22,
    "test_studio.py": 13,
    "test_compose_smart.py": 10,
    "test_compose_novel_ui.py": 8,
    "test_editor_ui.py": 7,
    "test_outline_dnd.py": 6,
    "test_compose_rolling_ui.py": 5,
    "test_source_ai_config.py": 5,
    # 盘点补登（门首跑自抓的 1-3 处小存量——同样只降不升）
    "test_architecture.py": 1,
    "test_compose_dual.py": 1,
    "test_cover.py": 1,
    "test_editor_ai.py": 2,
    "test_editor_ghost.py": 2,
    "test_pack_preview.py": 1,
    "test_tts.py": 2,
    "test_version_diff.py": 1,
    "test_writing_stats.py": 3,
}

_ANCHOR = re.compile(r'assert\s+(?:f?)"([^"]{8,})"\s+in\s+(?:src|all_src|js)\b')


def _count(path: pathlib.Path) -> int:
    return len(_ANCHOR.findall(path.read_text(encoding="utf-8")))


def test_anchor_budget_not_exceeded() -> None:
    """每文件锚数 ≤ 预算（只降不升的棘轮）。"""
    exceeded = []
    for name, cap in BUDGET.items():
        p = TESTS / name
        if not p.exists():
            continue
        n = _count(p)
        if n > cap:
            exceeded.append(f"{name}: {n} > {cap}")
    assert not exceeded, (
        f"实现字符串锚超出预算（棘轮只降不升）: {exceeded}；"
        f"若必须新增锚，请同时把等量旧锚改为行为断言并更新 BUDGET"
    )


def test_new_test_files_zero_anchor() -> None:
    """预算表之外的新测试文件：源码字符串锚必须为 0（从源头防腐）。"""
    offenders = []
    for p in sorted(TESTS.glob("test_*.py")):
        if p.name in BUDGET or p.name == "test_anchor_budget.py":
            continue
        n = _count(p)
        if n > 0:
            offenders.append(f"{p.name}: {n}")
    assert not offenders, (
        f"新测试文件含源码字符串锚（防腐门：新代码用行为断言，不锚实现文本）: {offenders}；"
        f"若确属行为契约（API 路径/事件绑定），请登记进 BUDGET 并注明理由"
    )
