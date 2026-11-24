"""批量操作前端接线门（W8，2026-11-24）。

W6 做了 batch_check/batch_pack 后端但前端未接（实测 15 个零消费端点中的 2 个）。
本门锁住「前端确实消费这两个端点」——防再次出现「后端有、前端无入口」的孤岛。
"""
from __future__ import annotations

import re
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"


def _js() -> str:
    return (STUDIO / "ui.js").read_text(encoding="utf-8")


def test_publish_view_consumes_batch_endpoints() -> None:
    """publish 视图段必须调用两个批量端点（前端消费面从 0 变 2）。"""
    js = _js()
    assert "/api/action/batch_check" in js, "前端未消费 batch_check（后端孤岛）"
    assert "/api/action/batch_pack" in js, "前端未消费 batch_pack（后端孤岛）"


def test_batch_ui_has_select_and_result_surfaces() -> None:
    """三件套齐全：多选框注入 + 工具条 + 结果表渲染。"""
    js = _js()
    assert "function batchInit" in js and "batch-pick" in js, "缺多选注入"
    assert "function batchSync" in js and "batch-bar" in js, "缺工具条同步"
    assert "function batchRender" in js and "batch-result" in js, "缺结果表"


def test_batch_dom_in_publish_view() -> None:
    """批量条 DOM 必须在 publish 视图 section 内（否则首次渲染找不到）。"""
    html = (STUDIO / "ui.views" / "publish.html").read_text(encoding="utf-8")
    assert 'id="batch-bar"' in html and 'id="batch-result"' in html
    # 必须在 </section> 之前
    section_end = html.rindex("</section>")
    assert html.index('id="batch-bar"') < section_end, "批量条须在视图 section 内"


def test_batch_requires_selection_guard() -> None:
    """未选择时不得发请求（防止空 paths → 400 噪声）。"""
    js = _js()
    m = re.search(r"async function batchRun\(kind\)\{(.{0,200})", js, re.S)
    assert m and "state.batch.size" in m.group(1), "batchRun 应先校验已选"
