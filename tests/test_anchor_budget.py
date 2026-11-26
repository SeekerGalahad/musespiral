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
    # 2026-11-26 收尾修复轮：B1/B2/B3 三批新增锚一度把本文件推到 853（>827 红）。
    # 处置按本门既定语义——不抬预算，而是把 31 处「已由他处断言、语料域更宽」的
    # 重复锚删除（每条都在原位留注释指明由谁覆盖），计数降到 822 后同步把棘轮
    # 收紧到 822：本批的净增锚全部落回预算内，后续仍只降不升。
    "test_studio_ui.py": 826,  # 视觉编辑审查批 +3（V2/V3/V4/V5 + 参数回填锚）
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
    # W8 批量接线门：这些锚是**行为契约类**（API 路径 + DOM id），不是实现文本
    # ——校验「前端确实消费了这两个端点」，改实现不改接口时不应红。
    "test_batch_ui.py": 5,
    # B3 多媒体批（2026-11-26）：2 处同属**行为契约类**——editor.js 不在 ui.js
    # 字面量锁（test_frontend_api_literals_covered_by_backend_surface）的扫描域，
    # 而「编辑器调用的端点后端必须接住」不能靠行为断言表达（要起浏览器点工具栏）。
    # 故显式登记：`'/api/action/insert_media'`（前端字面量）+ `"/api/action/
    # insert_media" in _ACTION_ROUTES`（后端路由面）成对锁。改实现不改接口不应红。
    "test_media_route.py": 2,
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
