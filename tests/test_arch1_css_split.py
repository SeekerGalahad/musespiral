"""ARCH-1 ② CSS 按视图拆分的等价性与装载契约回归锚（2026-11-21）。

三条机器门：
  ① 拆分前 ui.css（git HEAD）与拆分后「ui.css(core)+ui.views/*.css」的
     **CSS 规则单元多重集等价**（选择器+声明逐字节；@media 按内层单元比），
     唯一有意例外=逗号选择器组按视图拆开（.pg-detail,.pr-rungrid）。
  ② 十懒视图各一份 ui.views/<view>.css 源 + assets/views/<view>.css 产物；
     publish 无产物（首屏样式在 core.css）；每懒视图样式小于 8KiB。
  ③ ensureView 三步装载链（css→html.js→js）与 core.css 首屏样式齐备：
     :root token + data-theme=dark 块 + prefers-color-scheme 块仍在 core.css。

check counts as passed only when this file is executed（与 test_build_ui.py 的
产物对拍互补：这里专测「拆分等价性」这一属性）。
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
REPO = Path(__file__).resolve().parents[3]
LAZY = ["sources", "material", "books", "compose", "design", "insight",
        "article", "settings", "plugins", "plugin-run"]
FIRST = "publish"


def _rule_units(data: bytes) -> Counter:
    """字节级规则单元多重集（不依赖换行）：顶层 {…} 块 + @media 内层块。"""
    out: Counter = Counter()
    depth = start = i = 0
    while i < len(data):
        c = data[i:i + 1]
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                unit = data[start:i + 1].strip()
                if unit.startswith(b"@media"):
                    cond = unit[unit.index(b"("):unit.index(b")") + 1]
                    body = unit[unit.index(b"{") + 1:unit.rindex(b"}")]
                    inner_depth = inner_start = j = 0
                    while j < len(body):
                        ch = body[j:j + 1]
                        if ch == b"{":
                            inner_depth += 1
                        elif ch == b"}":
                            inner_depth -= 1
                            if inner_depth == 0:
                                out[(cond, body[inner_start:j + 1].strip())] += 1
                                inner_start = j + 1
                        j += 1
                else:
                    out[unit] += 1
                start = i + 1
        i += 1
    return out


# 基线锚=拆分提交 73f647ad66 的父提交（5a5ad5433e，拆分前 core.css 77,416B
# 单体）。基线不能追 HEAD：拆分落库后 HEAD 的 core.css 已是瘦身版，
# 级联对它全是「新增」——等价断言会永久红（2026-11-22 集成修红判例）。
_CSS_SPLIT_BASELINE_SHA = "5a5ad5433e2696d835acf6b90602a74b2b4f86d8"


def test_css_split_rule_multiset_equivalent_to_head() -> None:
    """产物级规则多重集等价：拆分前 HEAD 的 core.css（77,416B）vs 拆分后
    assets/core.css + assets/views/*.css 级联——所有 CSS 规则逐字节保留。
    唯一有意例外=逗号选择器组 .pg-detail,.pr-rungrid 按视图拆两条（声明逐字节同）。"""
    # 基线锚对象在 monorepo 历史里（对象路径 modules/musespiral/...），
    # 独立仓检出（SeekerGalahad/musespiral，CI 浅克隆）不含该对象——
    # 缺席即 skip；本地 monorepo 工作区锚可达，等价断言照常严格执行。
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{_CSS_SPLIT_BASELINE_SHA}^{{commit}}"],
        capture_output=True, cwd=REPO, check=False)
    if probe.returncode != 0:
        pytest.skip(
            f"CSS 等价基线锚 {_CSS_SPLIT_BASELINE_SHA[:10]} 在当前仓不可达"
            f"（monorepo 历史对象，独立仓/浅克隆检出缺席）: {probe.stderr.decode(errors='replace').strip()}"
        )
    original = subprocess.run(
        ["git", "show",
         f"{_CSS_SPLIT_BASELINE_SHA}:modules/musespiral/musespiral/studio/assets/core.css"],
        capture_output=True, cwd=REPO, check=True).stdout
    split = (STUDIO / "assets" / "core.css").read_bytes()
    for name in LAZY:
        split += (STUDIO / "assets" / "views" / f"{name}.css").read_bytes()
    before, after = _rule_units(original), _rule_units(split)
    # 允许的差异：① .pg-detail,.pr-rungrid 一组拆两条（plugins/plugin-run）；
    # ② FEAT-1 语义搜索（2026-11-22）在 ui.css 新增的八条规则——基线锚
    # 5a5ad5433e 早于 FEAT-1，新功能合法新增样式须在此登记（否则等价门误红）。
    missing, extra = before - after, after - before
    known_missing = {(b"(max-width:900px)",
                      b".pg-detail,.pr-rungrid{grid-template-columns:1fr}")}
    known_extra = {(b"(max-width:900px)",
                    b".pg-detail{grid-template-columns:1fr}"),
                   (b"(max-width:900px)",
                    b".pr-rungrid{grid-template-columns:1fr}"),
                   b".mat-search-wrap{display:flex;flex:1;min-width:180px;align-items:center;gap:4px}",
                   b".mat-search-modes{flex:none}",
                   b".mat-mode-hint{color:var(--amber);margin:4px 0 0}",
                   b".sim-badge{display:inline-flex;align-items:center;gap:3px;font-size:var(--fs-micro);font-variant-numeric:tabular-nums;color:var(--jade);font-weight:600;padding:1px 6px;border:1px solid var(--jade);border-radius:var(--radius-pill)}",
                   b".sim-related{margin:10px 0 0;padding-top:8px;border-top:1px solid var(--line-1)}",
                   b".sim-rel{display:flex;align-items:center;gap:6px;padding:4px 2px;font-size:var(--fs-caption);cursor:pointer;border-radius:var(--radius-btn)}",
                   b".sim-rel:hover{background:var(--wash)}",
                   b".sim-rel-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    # FEAT-2 智能组稿（2026-11-22）在 ui.views/compose.css 新增的 .sc-* 向导
    # 规则族（步骤条/素材列表/大纲编辑，19 条）——基线锚早于本批，合法新增登记。
                   b".sc-list{border:1px solid var(--line-1);border-radius:var(--radius-btn);max-height:300px;overflow:auto}",
                   b".sc-modal{width:min(560px,100%);max-height:86vh;display:flex;flex-direction:column}",
                   b".sc-outline-mats{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}",
                   b".sc-outline-ops .btn{padding:2px 8px;font-size:var(--fs-micro)}",
                   b".sc-outline-ops{display:flex;gap:4px;margin-top:4px}",
                   b".sc-outline-row .input{width:100%;font-size:var(--fs-micro);margin-bottom:4px}",
                   b".sc-outline-row{border:1px solid var(--line-1);border-left-width:4px;border-left-color:var(--indigo);border-radius:var(--radius-btn);padding:8px 10px;margin-bottom:8px}",
                   b".sc-pane{min-height:180px;flex:1;overflow:auto}",
                   b".sc-row input[type=checkbox]{margin-top:2px}",
                   b".sc-row-main{flex:1;min-width:0}",
                   b".sc-row-meta{display:flex;gap:6px;align-items:center;margin-top:3px;font-size:var(--fs-micro);color:var(--ink-3)}",
                   b".sc-row-p{display:block;color:var(--ink-2);font-size:var(--fs-micro);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
                   b".sc-row-t{display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
                   b".sc-row:last-child{border-bottom:none}",
                   b".sc-row{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-bottom:1px solid var(--line-1);font-size:12px}",
                   b".sc-step.is-active{color:var(--vermilion);border-bottom-color:var(--vermilion);font-weight:600}",
                   b".sc-step.is-done{color:var(--jade);border-bottom-color:var(--jade)}",
                   b".sc-steps{display:flex;gap:6px;margin:0 0 var(--space-md)}",
                   b".sc-step{flex:1;text-align:center;font-size:var(--fs-micro);color:var(--ink-2);border-bottom:2px solid var(--line-1);padding:4px 0}",
                   # FEAT-3 数据回流（2026-11-22）在 ui.views/insight.css 新增的 .st-*/
                   # .stats-trend 趋势 SVG 规则族（12 条）——基线锚早于本批，合法新增登记。
                   b"#stats-trend-card .chips{margin:0 0 8px}",
                   b".stats-trend svg{display:block;width:100%;height:150px;overflow:visible}",
                   b".stats-trend .st-grid{stroke:var(--line-1);stroke-width:1}",
                   b".stats-trend .st-axis{fill:var(--ink-3);font-size:10px;font-variant-numeric:tabular-nums}",
                   b".stats-trend .st-line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}",
                   b".stats-trend .st-reads{stroke:var(--vermilion)}",
                   b".stats-trend .st-zaikan{stroke:var(--amber)}",
                   b".stats-trend .st-shares{stroke:var(--viz-1)}",
                   b".stats-trend .st-dot{stroke:var(--paper-1);stroke-width:1.5}",
                   b".stats-trend .st-empty{fill:var(--ink-2);font-size:12px}",
                   b".st-legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:6px;font-size:var(--fs-micro);color:var(--ink-2)}",
                   b".st-legend i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}",
                   b".st-legend .st-reads{background:var(--vermilion)}",
                   b".st-legend .st-zaikan{background:var(--amber)}",
                   b".st-legend .st-shares{background:var(--viz-1)}",
                   # FEAT-4 小说双件套（2026-11-22）在 ui.views/compose.css 新增的
                   # .cn-* 规则族（人物关系力导向图 + 伏笔账本行，11 条）——
                   # 基线锚早于本批，合法新增登记。
                   b".cn-graph{display:block;width:100%;height:auto;margin-top:8px;border:1px solid var(--line-1);border-radius:var(--radius-btn);background:color-mix(in srgb,var(--indigo) 4%,var(--paper-1))}",
                   b".cn-g-edge{stroke:color-mix(in srgb,var(--ink-2) 45%,transparent);stroke-width:1.2}",
                   b".cn-g-node circle{fill:var(--vermilion);stroke:var(--paper-1);stroke-width:1.5}",
                   b".cn-g-node.ghost circle{fill:color-mix(in srgb,var(--ink-2) 35%,transparent);stroke-dasharray:2 2}",
                   b".cn-g-node text{font-size:10px;fill:var(--ink-1)}",
                   b".cn-g-node.ghost text{fill:var(--ink-3)}",
                   b".cn-rel{color:var(--indigo)}",
                   b".cn-fore-row{display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:4px;flex-wrap:wrap}",
                   b".cn-fore-note{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
                   b".cn-fore-ops{display:flex;gap:4px}",
                   b".cn-fore-ops .btn{padding:2px 8px;font-size:var(--fs-micro)}",
                   # FEAT-5 前端交互升级（2026-11-22）在 ui.css 新增的 .kbd-*/
                   # .skel-row/.empty-actions 规则族（快捷键速查弹层 + 行式骨架 +
                   # 空态动作行，11 条）——基线锚早于本批，合法新增登记。
                   b".kbd-modal{width:min(520px,100%)}",
                   b".kbd-body{display:flex;flex-direction:column;gap:14px;margin-top:12px}",
                   b".kbd-grp{display:flex;flex-direction:column;gap:2px}",
                   b".kbd-grp-t{font-size:var(--fs-micro);font-weight:600;letter-spacing:.5px;color:var(--ink-3)}",
                   b".kbd-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px solid color-mix(in srgb,var(--line-1) 45%,transparent);font-size:var(--fs-caption);color:var(--ink-2)}",
                   b".kbd-grp .kbd-row:last-child{border-bottom:none}",
                   b".kbd-row kbd{flex:none}",
                   b".kbd-row span{min-width:0}",
                   b".skel-row{display:flex;flex-direction:column;gap:7px;padding:12px 0;border-bottom:1px solid color-mix(in srgb,var(--line-1) 55%,transparent)}",
                   b".skel-row:last-child{border-bottom:none}",
                   b".empty-actions{justify-content:center;margin-top:6px}",
                   # UIX-REV 五角色评审修复批（2026-11-22）新规则——基线锚早于本批，
                   # 合法新增登记：①洞察空态 order 排序（#view-insight flex+order 5 条）；
                   # ②发布待审徽 .badge.stamp.pending；③源 ID 分行 .src-table meta-path 2 条；
                   # ④书源导入折叠摘要 .im-collapse 2 条。
                   b"#view-insight{display:flex;flex-direction:column}",
                   b"#view-insight .tiles{order:0}",
                   b"#view-insight .tiles:has(.empty){order:3;margin-top:14px}",
                   b"#view-insight .dash-grid{order:2}",
                   b"#view-insight #health-card{order:4}",
                   b".badge.stamp.pending{color:var(--viz-1);background:color-mix(in srgb,var(--viz-1) 12%,transparent);border-color:color-mix(in srgb,var(--viz-1) 40%,transparent)}",
                   b".src-table td:nth-child(2) .meta-path{display:block;margin-top:2px;color:var(--ink-3);font-size:11px;word-break:break-all}",
                   b".src-table td:nth-child(2) .meta-path:before{content:'ID ';color:var(--ink-3);font-family:inherit}",
                   b".im-collapse{display:block;width:100%;text-align:left;padding:8px 10px;margin:2px 0;background:color-mix(in srgb,var(--jade) 8%,var(--paper-1));border:1px solid color-mix(in srgb,var(--jade) 32%,transparent);border-radius:var(--radius-btn);color:var(--ink-1);font-size:var(--fs-caption);cursor:pointer}",
                   b".im-collapse:hover{border-color:var(--jade)}",
                   # UIX-3 无障碍批（2026-11-23）在 ui.css 新增的 focus-visible 补齐
                   # 规则（1 条多选择器——nav-item/coll-item/cp-item/[role=button]/
                   # nav-badge/switch input/im-exp/mat-thumb 键盘焦点环）——
                   # 基线锚早于本批，合法新增登记。
                   b".nav-item:focus-visible,.coll-item:focus-visible,.cp-item:focus-visible,[role=\"button\"]:focus-visible,.nav-badge:focus-visible,.switch input:focus-visible,.im-exp:focus-visible,.mat-thumb:focus-visible{outline:2px solid var(--vermilion);outline-offset:1px}",
                   # FEAT-P1B（2026-11-23）：大纲拖拽排序在 ui.views/compose.css
                   # 新增 .ol-* 8 条 + 写作节奏在 ui.views/insight.css 新增
                   # .wr-*/.streak-badge/.writing-* 13 条——基线锚早于本批，合法登记。
                   b".ol-handle{cursor:grab;border:1px solid var(--line-1);background:var(--paper-2);color:var(--ink-3);border-radius:var(--radius-btn);width:22px;height:22px;line-height:1;font-size:13px;flex:none;touch-action:none}",
                   b".ol-handle:hover{color:var(--vermilion);border-color:var(--vermilion)}",
                   b".ol-handle:active{cursor:grabbing}",
                   b".ol-handle:focus-visible{outline:2px solid var(--vermilion);outline-offset:1px}",
                   b".cm-outline-row.ol-drag-src{opacity:.45}",
                   b".cm-outline-row.ol-drop-before{box-shadow:inset 0 3px 0 -1px var(--vermilion)}",
                   b".cm-outline-row.ol-drop-after{box-shadow:inset 0 -3px 0 -1px var(--vermilion)}",
                   b".ol-placeholder{height:0;border-top:2px dashed var(--vermilion);margin:2px 0;opacity:.8;pointer-events:none}",
                   b"#writing-card .action-row{align-items:baseline}",
                   b".writing-chart svg{display:block;width:100%;height:120px;overflow:visible}",
                   b".writing-chart .wr-grid{stroke:var(--line-1);stroke-width:1}",
                   b".writing-chart .wr-axis{fill:var(--ink-3);font-size:10px;font-variant-numeric:tabular-nums}",
                   b".writing-chart .wr-bar{fill:color-mix(in srgb,var(--ink-1) 10%,transparent)}",
                   b".writing-chart .wr-bar.on{fill:var(--viz-3)}",
                   b".streak-badge{font-size:var(--fs-caption);color:var(--ink-2);margin-left:auto;font-variant-numeric:tabular-nums}",
                   b".streak-badge.hot{color:var(--vermilion);font-weight:600}",
                   b".writing-goal{margin-top:8px}",
                   b".wr-goal-row{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:var(--space-sm);font-size:var(--fs-caption)}",
                   b".wr-goal-lab{color:var(--ink-2);white-space:nowrap}",
                   b".wr-goal-n{color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
                   b".wr-empty{padding:var(--space-md) 0}"}
    assert set(missing) <= known_missing, f"有非预期规则丢失: {list(set(missing) - known_missing)[:3]}"
    assert set(extra) <= known_extra, f"有非预期规则新增: {list(set(extra) - known_extra)[:3]}"


def test_view_css_sources_and_products_complete() -> None:
    """十懒视图 CSS 源+产物齐备；publish 无产物；每视图 CSS <8KiB。"""
    for name in LAZY:
        src = STUDIO / "ui.views" / f"{name}.css"
        out = STUDIO / "assets" / "views" / f"{name}.css"
        assert src.is_file(), f"缺源: {src}"
        assert out.is_file(), f"缺产物: {out}"
        assert out.stat().st_size <= 8 * 1024, \
            f"assets/views/{name}.css 超 8KiB 锚: {out.stat().st_size}B"
    assert not (STUDIO / "ui.views" / f"{FIRST}.css").exists()
    assert not (STUDIO / "assets" / "views" / f"{FIRST}.css").exists()


def test_first_screen_css_tokens_intact() -> None:
    """首屏样式齐备：:root token/暗色双路径仍在 core.css（暗色不丢）；
    core.css 不再含视图专属选择器（.pg-/mat-/cm-/qs- 等已外移）。"""
    core = (STUDIO / "assets" / "core.css").read_text(encoding="utf-8")
    assert ":root{color-scheme:light dark;--vermilion:" in core
    assert 'html[data-theme="dark"]{color-scheme:dark;' in core
    assert "@media (prefers-color-scheme:dark)" in core
    # 视图专属 **大族** 不得漏留 core（P1-1 触控热区机制块 .cm-block-ops 按机制
    # 留 core 属已知例外；mat-acts/pg-compat 等通用类被多视图共用，留 core 正确）
    leaks = re.findall(r"\.(?:pg-grid|pg-card|pr-flow|mat-layout|mat-list|cm-grid|cm-dual|qs-modal|im-modal|sbs-grid|vgrid|ver-bar|radar-row|lightbox)[a-z0-9-]*", core)
    assert not leaks, f"视图专属样式漏留 core.css: {sorted(set(leaks))[:8]}"


def test_ensure_view_loads_css_before_html_and_js() -> None:
    """ui.js 源与 core.js 产物：ensureView 三步装载链 css→html.js→js（②装载契约）。"""
    for label, text in (("源", (STUDIO / "ui.js").read_text(encoding="utf-8")),
                        ("产物", (STUDIO / "assets" / "core.js").read_text(encoding="utf-8"))):
        assert "function __loadCss(" in text, f"{label} 缺 __loadCss"
        assert 'l.rel="stylesheet"' in text, f"{label} 的 __loadCss 未建 <link rel=stylesheet>"
        chain = text.index("function ensureView(")
        seg = text[chain:chain + 700]
        assert seg.index(".css") < seg.index(".html.js") < seg.index('.js"'), \
            f"{label} 装载顺序错（须 css→html.js→js）"
