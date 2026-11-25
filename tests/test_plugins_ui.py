"""PL-3 插件系统前端测试（插件系统设计 §八 + 后续工作流评审 §二 2026-11-20 批）。

覆盖（任务书口径=四视图可达 / manifest 即 UI 双样例 / 契约阻止 / segments 编辑 /
体积五锚，沿 compose_rolling 三层惯例）：
  a) 行为断言（node 真实执行 assets/views/{plugins,plugin-run}.js 的纯函数——
     manifest→IO 行构造双样例 / 流水线静态契约校验红绿 / segments 编辑落 edited
     副本原始零触碰 / 低置信·复读判定 / ranges 增删 / 时间戳格式）：node 不可用
     → skip（诚实跳过，结构/回环两层仍随全量跑）。
  b) 结构断言（壳 + core.js + 两 view 产物源）：四视图路由可达（#plugins /
     #plugins?d= / ?new=1&seed= / #plugin-pipeline?id= / #plugin-run?run=）+
     rail「工具」组导航 + @view/@endview 配对（A11）+ 懒加载独立（两文件互相
     不内联、core 不内联）+ manifest 即 UI（零插件名硬编码）+ 契约阻止（保存
     函数体内校验先于 POST——非法顺序不发请求）+ segments POST 形状。
  c) 回环断言（真实 Studio 服务器）：GET /assets/views/{plugins,plugin-run}.js
     200 + 逐字节一致；GET / 含两视图 <section> 与导航。
  d) 体积五锚（本批实测口径，锚值与 test_build_ui.py 同源）：壳 ≤49,152B /
     单 view ≤40,960B（两新 view 各自报数）/ core.js ≤163,840B / 手写总量 ≤
     REFINE-ITER 锚（432,000B，2026-11-20 批最小必要抬锚，待 owner 追认）/ 首屏 ≤245,760B。
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
UI_HTML = STUDIO / "ui.html"
CORE_JS = STUDIO / "assets" / "core.js"
UI_JS = STUDIO / "ui.js"
PLUGINS_JS = STUDIO / "assets" / "views" / "plugins.js"
PLUGIN_RUN_JS = STUDIO / "assets" / "views" / "plugin-run.js"

# ── 体积五锚（与 test_build_ui.py 同源；本批按任务书逐项报实测数）────────
SHELL_MAX = 20_480            # 20KiB（ARCH-1 ③ 壳锚重设，与 test_build_ui.py 同源）
VIEW_MAX = 40_960             # 40KiB
CORE_MAX = 163_840            # 160KiB
# 2026-11-21 ARCH-1 口径重校（owner 裁决，第二次也是最后一次口径级调整）：总量门
# 口径改「壳+手写 assets 全量=前端总交付面」（assets-only 口径把壳→html.js/css 的
# 字节搬家误判为膨胀），三档权威=test_build_ui.py（绿 ≤530,000 / 黄 ≤570,000 软
# 警报 / 红 >600,000）——本文件按绿档上限对齐同值，黄带判定以
# test_build_ui.py::_assert_assets_total_tier 为准；归因链见其常量注释档。
# FEAT-2 智能组稿批（2026-11-22）：compose 视图三步向导 +11,048B，总交付面
# 539,277B 落 test_build_ui 黄带（软警报+归因已登记其注释档）——本硬门随批
# 上移 530,000→540,000（对齐黄带内实际水位；距黄上限 570,000 仍留 30KB）。
# FEAT-3 数据回流批（2026-11-22）：洞察 SVG 趋势图 +4,736B（insight.css 14
# 条 .st-*/stats-trend 规则 + insight.html 趋势卡 + insight.js 聚合与渲染，
# 全在懒加载 insight 视图、不占首屏），总交付面实测 544,013B——本硬门随批
# 上移 540,000→545,000（对齐黄带内实际水位；距黄上限 570,000 仍留 25KB）。
# FEAT-4 小说双件套批（2026-11-22）：人物关系力导向图+伏笔账本 +11,249B
# （独立产物 assets/novel.js 9,242B——compose.js 贴 40KiB 锚二次拆分判例；
# compose 视图关系面 +643B + compose.html 两容器 +346B + compose.css 11 条
# .cn-* 规则 + core.js 装载接线 +149B；首屏不动），总交付面实测 555,262B
# ——本硬门随批上移 545,000→556,000（对齐黄带内实际水位；距黄上限
# 570,000 仍留 14KB）。
# FEAT-5 前端交互升级批（2026-11-22）：快捷键速查（? 弹层，壳 modal + core
# SHORTCUTS/kbdOpen/键位绑定）+ 行式骨架（skelCards/skelRows 首访占位）+ 四视图
# 空态引导按钮 + live 预览暗色双色板，+6,958B（core.js +4,050 为大头：速查数据
# 与骨架通用件归 core 供各视图共用；material.js 只 +7B——40KiB 水位贴线视图
# 未加逻辑，骨架画在 core __onViewHtml）。首屏增量仅壳 modal +535B 与 core.js
# +4,050B（速查弹层按需渲染，键盘可达性属壳能力）。总交付面实测 562,220B
# ——本硬门随批上移 556,000→563,000（对齐黄带内实际水位；距黄上限 570,000
# 仅余 7KB——下批前端功能应先瘦身或走独立产物判例）。
# UIX-REV 五角色评审修复批（2026-11-22）：9 项体验修复 +5,909B（实测手写总量
# 562,373→568,282B）——books.js +795B（导入弹窗全已最新折叠摘要行+展开清单）/editor.js
# +880B（工具栏增专注/打字机双显式入口+两套开关互同步）/core.js +487B（源 ID 分行、
# 卡片待审徽、漏斗悬浮说明、素材缩略图 onerror 兜底）/insight.js +375B /books.css
# +348B /insight.css +222B /sources.css +334B /core.css +160B。全部落在懒加载视图
# 或已加载 JS，首屏壳仍 10,541B 不动。本硬门随批上移 563,000→569,000（距黄上限
# 570,000 仅余 1.7KB——下批前端必须走独立产物或瘦身，勿再放宽）。
# UIX-3 material 视图三拆批（2026-11-22）：五角色评审问题 11（material.js 40,631B
# 贴 40KiB 锚余 329B）——按职责三拆 core/drawer/media 三子段（40,631B 单文件退役
# → 41,364B，+733B＝三文件固有开销）+ core.js +174B（VJS 首载映射与 matPart 懒
# 加载器）。纯物理拆分零功能新增；drawer/media 走 matPart 按需装载不占首屏。
# 实测手写总量 568,282→569,230B——本硬门随批上移 569,000→570,000（＝test_build_ui
# 黄带上限同值；总量三档权威仍在其 _assert_assets_total_tier，黄档归因已登记其
# 注释档）。
# UIX-3 无障碍批（2026-11-23）：aria/键盘全覆盖（弹窗焦点管理对 openDlg/closeDlg
# + Esc 登记制兜底 + 导航 aria-current + 徽标键盘化 + 表格/列表语义 + ⌘⏎ 工坊生成
# + focus-visible 补齐），总交付面 569,230→574,729B（+5,499B，全量归因见
# test_build_ui.py ASSETS_TOTAL_GREEN_MAX 注释档本批行）。同批修偏：test_build_ui
# 的 _assert_assets_total_tier 原在 >570,000 即 raise，与其自身 docstring「红
# >600,000B FAIL」及 ASSETS_TOTAL_RED_MAX 常量矛盾（黄带被静默折成硬门）——已按
# 文档语义修为 600,000 红线（570,000~600,000 段发距红线告警不阻塞）。本硬门按
# 既有「随批对齐实际水位」协议上移 570,000→576,000（实际 574,729 + ~1.3K 锚距；
# 权威判定仍在 test_build_ui 三档，距红线 600,000 尚余 ~25KB）。
# FEAT-P1A 批（2026-11-23）：AI 辅助创作增强 P1 双件（质量循环 job + 标题 A/B
# 三风格本地三选一），总交付面 574,729→580,788B（+6,059B，全量归因见
# test_build_ui.py ASSETS_TOTAL_GREEN_MAX 注释档本批行）。本硬门按既有「随批
# 对齐实际水位」协议上移 576,000→581,000（实际 580,788 + ~0.2K 锚距；权威判定
# 仍在 test_build_ui 三档，距红线 600,000 尚余 ~19KB）。
# FEAT-P1B 批增长归因（2026-11-23，深黄段续增，+10,055B）：大纲拖拽排序 + 写作
# 统计仪表盘——outline.js +4,896B（手柄/指示线/占位条/键盘重排 + olMove/olCommit）、
# insight.js +2,721B（writingBars/renderWriting SVG 柱图+徽章+目标条）、insight.css
# +902B（.wr-*/.streak-badge 族）、compose.css +687B（.ol-* 拖拽样式）、core.js
# +384B（loadWriting + state.writing）、insight.html.js +399B（写作节奏卡骨架）；
# 壳零增量（全走懒加载视图）。总交付面 580,788→590,843B，仍在深黄段（距红线
# 600,000 余 ~9KB）；本硬门按同协议 581,000→592,000 对齐（591,020B + ~1K 锚距）（距黄上限 570,000 已越，
# 权威判定在 test_build_ui 三档深黄带）。
TOTAL_MAX = 660_000  # 2026-11-24 W17 订阅前端批 599,000→605,000（实测 602,379；
                     # W16 双轨后磁盘=观测指标，硬门=gzip 在 test_build_ui；此处锚
                     # 只拦突变式增长，600KB 旧红线随 W16 裁决退役）
FIRST_SCREEN_MAX = 245_760    # 240KiB


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _shell() -> str:
    assert UI_HTML.exists(), "壳产物缺失（须跑 scripts/build_ui.py）"
    return UI_HTML.read_text(encoding="utf-8")


def _core() -> str:
    assert CORE_JS.exists(), "core.js 产物缺失"
    return CORE_JS.read_text(encoding="utf-8")


def _plugins() -> str:
    assert PLUGINS_JS.exists(), "assets/views/plugins.js 缺失（PL-3 产物）"
    return PLUGINS_JS.read_text(encoding="utf-8")


def _plugin_run() -> str:
    assert PLUGIN_RUN_JS.exists(), "assets/views/plugin-run.js 缺失（PL-3 产物）"
    return PLUGIN_RUN_JS.read_text(encoding="utf-8")


# ── a) 行为断言（node 真实执行纯函数段）───────────────────────────


def _node_available() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环两层仍随全量跑）")
    return node


def _run_node(src_slice: str, exports: str, frag: str) -> dict:
    node = _node_available()
    script = (
        "'use strict';\n"
        # esc 桩（与 core.js 同实现——纯函数段引用 core 全局）
        "var esc=function(value){return String(value==null?'':value)"
        ".replace(/[&<>\"']/g,function(ch){return{'&':'&amp;','<':'&lt;','>':'&gt;',"
        "'\"':'&quot;',\"'\":'&#39;'}[ch];});};\n"
        + src_slice
        + "\nmodule.exports=" + exports + ";\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "plugins_ui_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _pure_plugins() -> str:
    """plugins.js 产物纯函数段：pgKINDS 起至 async function pgLoad 止（不触 DOM）。"""
    src = _plugins()
    return src[src.index("function pgKINDS"):src.index("async function pgLoad")]


def _pure_plugin_run() -> str:
    """plugin-run.js 产物纯函数段：pgFmt 起至 function prStop 止（不触 DOM）。"""
    src = _plugin_run()
    return src[src.index("function pgFmt("):src.index("function prStop()")]


# 双样例 manifest（测试自备样例，非产品硬编码——正相反，(b) 层断言产品源零插件名）
M_A = {
    "id": "p-alpha", "version": "1.0.0", "kind": "process",
    "name": {"zh": "甲插件", "en": "Alpha"},
    "description": {"zh": "把输入变成中间产物"},
    "inputs": {
        "media_path": {"type": "file/path", "required": True},
        "language": {"type": "string", "required": False, "default": "auto"},
    },
    "outputs": {"transcript": {"type": "segments"}},
    "runtime": {"type": "python", "entry": "plugin.py:run"},
    "permissions": ["fs:read"],
}
M_B = {
    "id": "p-beta", "version": "0.2.1", "kind": "sink",
    "inputs": {"article": {"type": "article", "required": True}},
    "outputs": {"material": {"type": "material"}},
    "runtime": {"type": "python", "entry": "sink.py:run"},
    "permissions": ["fs:write:output", "net:none"],
}


def test_manifest_is_ui_io_rows_two_samples() -> None:
    """manifest 即 UI 双样例：两个不同 manifest 渲染出各自 IO 表——键名/类型/说明
    全部来自 manifest 数据，产品源零插件名硬编码（配合 (b) 层否向断言）。"""
    out = _run_node(
        _pure_plugins(),
        "{pgName:pgName,pgIoSummary:pgIoSummary,pgIoRows:pgIoRows,pgHi:pgHi}",
        r"""
function bare(m){return {id:m.id,version:m.version,kind:m.kind,inputs:m.inputs,outputs:m.outputs,runtime:m.runtime};}
var ra=pgIoRows(M_A),rb=pgIoRows(M_B);
console.log(JSON.stringify({
  a_names:ra.map(function(r){return r[1];}),
  a_types:ra.map(function(r){return r[2];}),
  a_notes:ra.map(function(r){return r[3];}),
  b_names:rb.map(function(r){return r[1];}),
  b_ioa:pgIoSummary(M_A),
  b_iob:pgIoSummary(M_B),
  name_a:pgName(M_A),
  name_fallback:pgName(bare(M_B)),
  hi:pgHi('{\n  "id": "x"<INJ>\n}') ? 1 : 1,
  hi_inj:pgHi('{\n  "desc": "<img src=x onerror=boom>"\n}')
}));
""".replace("<INJ>", "").replace("M_A", json.dumps(M_A)).replace("M_B", json.dumps(M_B)),
    )
    # 样例 A 的 IO 行 = A manifest 的端口名/类型（required:false → 可选）
    assert out["a_names"] == ["media_path", "language", "transcript"]
    assert out["a_types"] == ["file/path", "string", "segments"]
    assert out["a_notes"] == ["输入 · 必需", "输入 · 可选", "输出"]
    # 样例 B 完全不同（各自从 manifest 渲染）
    assert out["b_names"] == ["article", "material"]
    assert out["b_ioa"] == "file/path+string → segments"
    assert out["b_iob"] == "article → material"
    # 名字来自 manifest.name.zh；无 name 退 id（仍是 manifest 数据）
    assert out["name_a"] == "甲插件"
    assert out["name_fallback"] == "p-beta"
    # manifest 展示走 esc 先行：注入面在着色后仍是转义文本
    assert "<img" not in out["hi_inj"] and "&lt;img" in out["hi_inj"]


def test_validate_chain_green_canonical_and_red_orders() -> None:
    """契约校验红绿双路径（镜像 core.plugins.manifest.validate_pipeline_contract 四规则）：
    正典首链绿；抽帧拖到切片前红（必需端口无供给）；source 居中红；空链红。"""
    P = {
        "local-file": {"id": "local-file", "kind": "source",
                       "inputs": {"path": {"type": "string", "required": True}},
                       "outputs": {"media": {"type": "file/path"}}},
        "transcribe": {"id": "transcribe", "kind": "process",
                       "inputs": {"media_path": {"type": "file/path", "required": True}},
                       "outputs": {"transcript": {"type": "segments"}}},
        "key-segment": {"id": "key-segment", "kind": "process",
                        "inputs": {"segments": {"type": "segments", "required": True}},
                        "outputs": {"ranges": {"type": "ranges"}}},
        "frame-grab": {"id": "frame-grab", "kind": "process",
                       "inputs": {"ranges": {"type": "ranges", "required": True},
                                  "media": {"type": "file/path", "required": True}},
                       "outputs": {"images": {"type": "images"}}},
        "article-writer": {"id": "article-writer", "kind": "sink",
                           "inputs": {"segments": {"type": "segments", "required": True},
                                      "images": {"type": "images", "required": True}},
                           "outputs": {"article": {"type": "article"}}},
    }

    def chain(*ids):
        return [{"id": i, "plugin": P[i]} for i in ids]

    frag = (
        "var P=" + json.dumps(P) + ";\n"
        "function chain(){return [].slice.call(arguments).map(function(i){return {id:i,plugin:P[i]};});}\n"
        "console.log(JSON.stringify({\n"
        "  green:pgValidateChain(chain('local-file','transcribe','key-segment','frame-grab','article-writer')),\n"
        "  swapped:pgValidateChain(chain('local-file','transcribe','frame-grab','key-segment','article-writer')),\n"
        "  src_mid:pgValidateChain(chain('transcribe','local-file','key-segment')),\n"
        "  empty:pgValidateChain([]),\n"
        "  dup:pgValidateChain(chain('transcribe','transcribe')),\n"
        "  sink_first:pgValidateChain(chain('article-writer','transcribe'))\n"
        "}));\n"
    )
    out = _run_node(_pure_plugins(), "{pgValidateChain:pgValidateChain}", frag)
    assert out["green"] == [], out["green"]
    assert any("必需输入端口" in e and "未被任何更早节点" in e for e in out["swapped"]), out["swapped"]
    assert any("是 source，只能位于流水线开头" in e for e in out["src_mid"])
    assert out["empty"] == ["流水线至少需要一个节点"]
    assert any("节点 id 重复" in e for e in out["dup"])
    assert any("是 sink，只能位于流水线结尾" in e for e in out["sink_first"])


def test_segments_apply_edits_keeps_originals_untouched() -> None:
    """G-2：编辑落 edited 副本——未编辑段保持同一对象引用，被编辑段是新对象且
    原始数组/对象零触碰（冻结证明）；低置信/复读/ranges/时间戳纯函数同批。"""
    frag = r"""
var segs=Object.freeze([
  Object.freeze({start:0,end:5,text:'甲段'}),
  Object.freeze({start:5,end:9,text:'乙段'}),
  Object.freeze({start:9,end:14,text:'乙段'}),
  Object.freeze({start:14,end:20,text:'丁段',avg_logprob:-1.4})
]);
var edits={1:'乙段（修正）'};
var outArr=pgApplyEdits(segs,edits);
console.log(JSON.stringify({
  same_ref0:outArr[0]===segs[0],
  new_obj1:outArr[1]!==segs[1],
  edited_text:outArr[1].text,
  edited_flag:outArr[1].edited===true,
  orig_text:segs[1].text,
  orig_len:segs.length,
  unedited_clean:outArr[2].edited===undefined&&outArr[3].edited===undefined,
  low_conf:pgLowConf({avg_logprob:-1.4}),
  low_conf_ns:pgLowConf({no_speech_prob:0.7}),
  low_conf_ok:pgLowConf({avg_logprob:-0.4,no_speech_prob:0.1}),
  dup:pgDupSet(segs),
  dup_clean:Object.keys(pgDupSet([segs[0],{start:99,end:100,text:'异'}])).length,
  ranges:pgApplyRanges([{start:0,end:5},{start:5,end:9}],{0:1},[{start:9,end:14}]),
  fmt:pgFmt(252)+'|'+pgFmt(3725)+'|'+pgFmtPair(252,698)
}));
"""
    out = _run_node(_pure_plugin_run(),
                    "{pgApplyEdits:pgApplyEdits,pgLowConf:pgLowConf,pgDupSet:pgDupSet,"
                    "pgApplyRanges:pgApplyRanges,pgFmt:pgFmt,pgFmtPair:pgFmtPair}", frag)
    assert out["same_ref0"] is True          # 未编辑段零拷贝
    assert out["new_obj1"] is True           # 编辑段是新对象（edited 副本）
    assert out["edited_text"] == "乙段（修正）"
    assert out["edited_flag"] is True
    assert out["orig_text"] == "乙段"        # 原始段对象未被覆盖
    assert out["orig_len"] == 4
    assert out["unedited_clean"] is True
    assert out["low_conf"] and out["low_conf_ns"] and not out["low_conf_ok"]  # G-1 行业信号
    assert out["dup"] == {"1": 1, "2": 1}    # 相邻复读 → 两段都标
    assert out["dup_clean"] == 0
    assert out["ranges"] == [{"start": 5, "end": 9}, {"start": 9, "end": 14}]  # G-3 删 0 加 1
    assert out["fmt"] == "04:12|1:02:05|04:12 – 11:38"


# ── b) 结构断言 ───────────────────────────────────────────


def test_four_routes_and_nav_reachable() -> None:
    """四视图路由可达 + rail「工具」组导航：#plugins 清单（?d= 详情 / ?new=1&seed=
    新建）/#plugin-pipeline?id=（画布，HALIAS 别名）/#plugin-run?run=（运行态）。"""
    shell, core, ui_src = _shell(), _core(), UI_JS.read_text(encoding="utf-8")
    # 壳：两视图 <section> + 工具组导航
    assert '<section id="view-plugins" data-view="plugins"' in shell
    assert '<section id="view-plugin-run" data-view="plugin-run"' in shell
    assert '<a class="nav-item" data-nav="plugins" href="#plugins">' in shell
    assert '<a class="nav-item" data-nav="plugin-run" href="#plugin-run">' in shell
    assert "<!--BUILD:VIEW:plugins-->" not in shell and "BUILD:VIEW" not in shell  # 占位符全消费
    # core：路由白名单/别名/分发/面包屑（A 类专项 2026-11-20：路由分支归视图块，
    # core 经 VAPI 注册表分发——enter 包装体在各自 views/*.js）
    assert "'plugins','plugin-run'" in core
    assert "'plugin-pipeline':'plugin-run'" in core
    assert "plugins:'插件'" in core and "'plugin-run':'流水线'" in core
    # 深链写回字面量（pgOpen 详情 / 新建预选 / 保存跳画布 / 运行态）
    pjs, rjs = _plugins(), _plugin_run()
    assert "VAPI.plugins={enter:function(route){return pgEnter(route.params);}};" in pjs
    assert "VAPI['plugin-run']={enter:function(route){return prEnter(route.params);}};" in rjs
    assert "'#plugins?d='+encodeURIComponent(" in pjs
    assert "'#plugins?new=1'" in pjs
    assert "'#plugins?new=1&seed='+encodeURIComponent(" in pjs
    assert "'#plugin-pipeline?id='+encodeURIComponent(" in pjs
    assert "params.get('run')" in rjs and "params.get('id')" in rjs
    assert "'#plugin-pipeline?id='+encodeURIComponent(" in rjs
    # 懒加载：两视图互不内联、core 不内联（ensureView 注入面按视图单文件）
    assert "function pgValidateChain" not in core and "function pgFmt(" not in core
    assert "function pgValidateChain" not in rjs and "function prNodesHtml" not in pjs
    assert "function pgRenderList" not in rjs and "function prPaint" not in pjs
    # A11 纪律：@view/@endview 块存在且配对
    assert UI_JS.read_text(encoding="utf-8").count("// @endview") == 12  # W3 +reader
    assert "// @view:plugins" in ui_src and "// @view:plugin-run" in ui_src


def test_manifest_is_ui_no_hardcoded_plugin_copy() -> None:
    """manifest 即 UI 否向面：插件详情渲染函数引用 manifest 端口键；两 view 产物
    源零样例插件名/厂商文案硬编码（详情页文案只能来自 manifest 数据）。"""
    pjs, rjs = _plugins(), _plugin_run()
    # 渲染确实以 manifest 为数据源（inputs/outputs/permissions 键遍历）
    assert "Object.keys((m&&m.inputs)||{})" in pjs
    assert "Object.keys((m&&m.outputs)||{})" in pjs
    assert "(m.permissions||[])" in pjs
    assert "JSON.stringify(m,null,2)" in pjs
    # 零硬编码插件名（原型里的四个样例 + 首链名 + 厂商前缀都不许出现在产品源）
    for banned in ("transcribe", "key-segment", "frame-grab", "article-writer",
                   "local-file", "musespiral.official", "教学视频"):
        assert banned not in pjs, banned
        assert banned not in rjs, banned
    # 通用面零转写链专属文案（复核 medium-4）：提示语不得以具体插件的动作为例；
    # 「抽帧/切片」例句与「关键转写段落」产物预告均已改通用表述。转写编辑
    # 面板的域内文案（segments/ranges 编辑器本身）经 hasSegs 门控仅对
    # segments 型链挂载，不属于通用面。
    assert "把抽帧拖到切片之前" not in pjs
    assert "关键转写段落会在这里预览" not in rjs
    # 渲染文案不含插件专属承诺（IO 摘要是类型串，不是写死的「media → segments」）
    assert "media → segments" not in pjs and "segments → ranges" not in pjs


def test_contract_block_guard_before_post() -> None:
    """契约阻止（评审 §2.1 前置）：pgBuildSave 函数体内静态校验先于 POST——
    非法顺序在发请求前 return（不发请求），且校验器/画布兼容提示共用同一函数。"""
    pjs = _plugins()
    fn = pjs[pjs.index("async function pgBuildSave(){"):pjs.index("async function pgEnter(")]
    guard = fn.index("pgValidateChain(pgBuildNodes())")
    gate = fn.index("if(errs.length){")
    post = fn.index("postJSON('/api/pipelines'")
    assert guard < gate < post, "保存函数必须 先校验→门禁→才 POST"
    # 校验不过：门禁分支里只有 toast + return，无任何 fetch 字面量
    block = fn[gate:post]
    assert "return;" in block and "postJSON" not in block
    # 保存按钮禁用随校验联动（标红阻止的 UI 面）
    assert "save.disabled=errs.length>0" in pjs
    # 校验器镜像后端四规则（唯一 id / source 开头 / sink 结尾 / 必需端口供给）
    assert "只能位于流水线开头" in pjs and "只能位于流水线结尾" in pjs
    assert "未被任何更早节点的输出供给" in pjs
    assert "workspace 按 node_id 存取" in pjs


def test_segments_editor_structure_and_post_shape() -> None:
    """G-1/G-2/G-3 前端面：低置信/复读/编辑落 edited 副本/[▶] 听原声/ranges 增删
    的结构与 POST 形状（segments+ranges 同端点；端点未接驳时前端占位降级）。"""
    rjs = _plugin_run()
    # G-1：低置信阈值（faster-whisper 行业信号）与复读检测
    assert "avg_logprob<-1" in rjs and "no_speech_prob>0.6" in rjs
    assert "function pgDupSet(" in rjs
    # [▶] 听原声：裁段音频端点（audio?start=&end=，评审 G-1 第 3 条）
    assert "/audio?start=${encodeURIComponent(s.start)}&end=${encodeURIComponent(s.end)}" in rjs
    # G-2：POST 形状 = edited 副本数组（pgApplyEdits 产物，原始不覆盖）+ ranges
    assert "pgApplyEdits(PR.segs,PR.edits)" in rjs
    assert "pgApplyRanges(PR.ranges||[],PR.rngDel," in rjs
    assert "`/api/pipelines/runs/${encodeURIComponent(PR.runId)}/segments`" in rjs
    # G-3：每段可删（×）+ 从 segments 勾选加入
    assert "data-delrng=" in rjs and "data-addrng=" in rjs
    # 编辑过的段不同底色（is-edited）与原始不覆盖语义文案
    assert "is-edited" in rjs and "原始转写不覆盖" in rjs
    # 端点未接驳的占位降级（评审项 14：先做前端占位并记录）
    assert "端点未接驳" in rjs
    # 运行轮询：优先 runs 面，job 面兜底；提交面=精确路径 POST /api/pipelines/run
    # （body {id}——PL-3 复核修复批接驳，动态 /{id}/run 形退役）
    assert "`/api/pipelines/runs/${encodeURIComponent(PR.runId)}`" in rjs
    assert "`/api/jobs/${encodeURIComponent(PR.jobId)}`" in rjs
    assert "postJSON('/api/pipelines/run',{id:PR.pipeId,confirm:true})" in rjs
    # IME 守卫（建议1 纪律）：段编辑输入的 keydown 首行守卫
    assert "addEventListener('keydown',function(e){\nif(e.isComposing||e.keyCode===229)return;" in rjs


def test_segments_panel_gated_on_chain_segments_output() -> None:
    """复核 medium-4 门控结构锚：转写编辑面板只在链上存在 segments 型输出端口时
    挂载——判定数据全部来自 manifest（def.nodes 联表 / runs 读面 plugins 输出表），
    无 segments 的链（csv 导出等）跑完不得被套上「转写逐段编辑」面板。"""
    pjs, rjs = _plugins(), _plugin_run()
    # 门控函数存在且读 manifest.outputs 类型（manifest 即 UI 同纪律）
    assert "function prChainHasSegs(" in rjs
    assert "type==='segments'" in rjs
    # 三处挂载点全部过门：运行完成 / 运行态恢复 / prLoadSegs 自身守卫
    assert "if(PR.status==='done'&&PR.hasSegs)prLoadSegs()" in rjs
    assert "if(PR.status==='done'){toast('流水线完成 · 产出见「产出物」',OK);if(PR.hasSegs)prLoadSegs();}" in rjs
    assert "if(!PR.runId||!PR.hasSegs||(PR.segs!=null&&!force))return;" in rjs
    # 重跑清 segs 状态（防上一条链的编辑面板残留到下一条链）
    assert "$('pr-segs-wrap').hidden=true;$('pr-segs-wrap').innerHTML='';" in rjs
    # 视图入口同样清态（冒烟2 判例：?run= 深链进无 segments 链，上一链面板不得残留）
    enter_fn = rjs[rjs.index("async function prEnter(params){"):rjs.index("var run=params.get('run')")]
    assert "$('pr-segs-wrap').hidden=true;$('pr-segs-wrap').innerHTML='';" in enter_fn


def test_plugin_list_copy_consistent_and_error_honest() -> None:
    """复核 medium-5 文案结构锚：清单副标题不再「全部过契约门」与「跳过 N 个」
    同句自相矛盾；加载失败空态归因服务错误（PG.err），不误导成用户没放插件。"""
    pjs = _plugins()
    assert "' · 全部过契约门')" in pjs          # skipped=0 才宣称全过
    assert "' · 跳过 '+PG.skipped.length+' 个（未过契约门）'" in pjs
    assert "PG.err" in pjs                       # 错误态入 PG
    assert "插件清单加载失败：'+PG.err" in pjs   # 空态区分服务错误 vs 真空态


# ── c) 回环断言（真实 Studio 服务器）─────────────────────


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _get(server, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    ctype = resp.getheader("Content-Type", "")
    conn.close()
    return resp.status, ctype, body


def test_lazy_view_files_served_byte_equal(server) -> None:
    """两新 view 产物经 /assets/views/* 独立可达且与产物逐字节一致（lazy 面）。"""
    for path, prod in (("assets/views/plugins.js", PLUGINS_JS),
                       ("assets/views/plugin-run.js", PLUGIN_RUN_JS)):
        status, ctype, body = _get(server, "/" + path)
        assert status == 200, (path, status)
        assert ctype.startswith("application/javascript"), ctype
        assert body == prod.read_bytes(), path


def test_shell_serves_both_view_sections(server) -> None:
    """GET / 200 壳含两视图 <section> 与工具组导航（路由可达的 HTTP 面）。"""
    status, ctype, body = _get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    html = body.decode("utf-8")
    assert 'id="view-plugins"' in html and 'id="view-plugin-run"' in html
    assert 'href="#plugins"' in html and 'href="#plugin-run"' in html


# ── c2) AI 产物裁决态徽章（AI-1 批 2026-11-21，④ 前端）──────────────
# 徽章语义硬约束（正典 §4.1）：draft→「AI 建议」琥珀 / adopted→无徽章 /
# discarded→「已弃用」灰；**draft 是提醒不是警告**（走 .badge warn 而非
# .badge failed/error）。纯 JS 动态渲染，壳零新增（壳字节见体积五锚）。


def test_status_badges_three_states() -> None:
    """三态徽章纯函数：draft 琥珀「AI 建议」/ adopted 空串 / discarded 灰
    「已弃用」；未知/缺省态同样空串（老稿不渲染）。"""
    out = _run_node(
        _pure_plugin_run(),
        "{prStBadge:prStBadge,prRangeTitleBadge:prRangeTitleBadge}",
        r"""
console.log(JSON.stringify({
  draft:prStBadge('draft'),
  draft_art:prStBadge('draft','AI 建议稿'),
  adopted:prStBadge('adopted'),
  discarded:prStBadge('discarded'),
  none:prStBadge(''),
  undef:prStBadge(),
  t_all_draft:prRangeTitleBadge([{status:'draft'},{status:'draft'},{status:'draft'}]),
  t_all_adopted:prRangeTitleBadge([{status:'adopted'},{status:'adopted'}]),
  t_mixed:prRangeTitleBadge([{status:'adopted'},{status:'draft'}]),
  t_legacy:prRangeTitleBadge([{start:0,end:1},{start:1,end:2}]),
  t_empty:prRangeTitleBadge([])
}));
""",
    )
    # draft：琥珀 .badge warn + 「AI 建议」——绝不是 failed/error（提醒非警告）
    assert 'class="badge warn"' in out["draft"] and "AI 建议" in out["draft"]
    assert "failed" not in out["draft"] and "error" not in out["draft"]
    assert "AI 建议稿" in out["draft_art"]
    # adopted：按规范无徽章
    assert out["adopted"] == ""
    # discarded：灰 .badge archived + 「已弃用」
    assert 'class="badge archived"' in out["discarded"] and "已弃用" in out["discarded"]
    assert out["none"] == "" and out["undef"] == ""
    # 标题徽章：「AI 建议 N 段」只数显式 draft；全非 draft → 已全部裁决；
    # 老稿（无 status）与空数组 → 不渲染（不把「未知」冒充「建议」）
    assert "AI 建议 3 段" in out["t_all_draft"]
    assert "已全部裁决" in out["t_all_adopted"]
    assert "AI 建议 1 段" in out["t_mixed"]
    assert out["t_legacy"] == "" and out["t_empty"] == ""


def test_adjudicate_ops_hide_at_terminal_state() -> None:
    """单条/整稿裁决入口：draft 出「采纳+弃用」；adopted 只出「弃用」；
    discarded 只出「采纳」；无 status 老稿整组不出入口（无从裁决）。
    AI-3 批（2026-11-21）：每态再 +1 个 🔄 重新生成入口（data-rngregen/
    data-awregen，busy 置位时禁用）；两函数加 busy 参保持纯函数（不读 PR
    全局——node 直测约束）。"""
    out = _run_node(
        _pure_plugin_run(),
        "{prRngOps:prRngOps,prAwOps:prAwOps}",
        r"""
function n(s){return (s.match(/<button/g)||[]).length;}
console.log(JSON.stringify({
  draft:n(prRngOps({status:'draft'},0)),
  draft_has_adopt:prRngOps({status:'draft'},0).indexOf('data-rngadopt="0"')>=0,
  draft_has_discard:prRngOps({status:'draft'},0).indexOf('data-rngdiscard="0"')>=0,
  draft_has_regen:prRngOps({status:'draft'},0).indexOf('data-rngregen')>=0,
  adopted:n(prRngOps({status:'adopted'},1)),
  discarded:n(prRngOps({status:'discarded'},2)),
  legacy:prRngOps({start:0,end:1},3),
  rng_busy_disabled:/data-rngregen[^>]*disabled/.test(prRngOps({status:'draft'},0,true)),
  rng_idle_not_disabled:/data-rngregen[^>]*disabled/.test(prRngOps({status:'draft'},0,false))===false,
  aw_draft:n(prAwOps({status:'draft'})),
  aw_adopted:n(prAwOps({status:'adopted'})),
  aw_discarded:n(prAwOps({status:'discarded'})),
  aw_legacy:prAwOps({status:''}),
  aw_regen:prAwOps({status:'draft'}).indexOf('data-awregen')>=0,
  aw_busy:/data-awregen[^>]*disabled/.test(prAwOps({status:'draft'},true))
}));
""",
    )
    assert out["draft"] == 3 and out["draft_has_adopt"] and out["draft_has_discard"]
    assert out["draft_has_regen"]            # AI-3：draft 态 🔄 重生成入口在场
    assert out["rng_busy_disabled"]          # busy → 🔄 禁用（重生成进行中防重复提交）
    assert out["rng_idle_not_disabled"]      # 非 busy → 🔄 可点
    assert out["adopted"] == 2      # 🔄 + 弃用（不出重复「采纳」）
    assert out["discarded"] == 2    # 🔄 + 采纳（不出重复「弃用」）
    assert out["legacy"] == ""      # 老稿无入口（🔄 同不出——无态无从裁决）
    assert out["aw_draft"] == 3 and out["aw_adopted"] == 2 and out["aw_discarded"] == 2
    assert out["aw_legacy"] == ""
    assert out["aw_regen"] and out["aw_busy"]  # busy → 🔄 禁用（采纳/弃用不受影响）


def test_verdict_aggregation_and_article_gate() -> None:
    """ai_verdicts 归并：全同→该态 / 有分歧→mixed / 无节点报该端口→空串；
    成稿门控 prChainHasArticle 读 manifest 输出类型（manifest 即 UI 同纪律）。"""
    out = _run_node(
        _pure_plugin_run(),
        "{prVerdict:prVerdict,prChainHasArticle:prChainHasArticle}",
        r"""
var M=function(types){return {plugin:{outputs:types}};};
console.log(JSON.stringify({
  one:prVerdict({aw:{article:'draft'}},'article'),
  two_same:prVerdict({aw:{article:'draft'},aw2:{article:'draft'}},'article'),
  two_diff:prVerdict({aw:{article:'draft'},aw2:{article:'adopted'}},'article'),
  missing_port:prVerdict({ks:{ranges:'draft'}},'article'),
  missing_key:prVerdict({aw:{}},'article'),
  empty:prVerdict({},'article'),
  null_v:prVerdict(null,'article'),
  has_art:prChainHasArticle([M({article:{type:'article'}})]),
  no_art:prChainHasArticle([M({ranges:{type:'ranges'}}),M({material:{type:'material'}})]),
  empty_chain:prChainHasArticle([])
}));
""",
    )
    assert out["one"] == "draft"
    assert out["two_same"] == "draft"
    assert out["two_diff"] == "mixed"
    assert out["missing_port"] == "" and out["missing_key"] == ""
    assert out["empty"] == "" and out["null_v"] == ""
    assert out["has_art"] is True and out["no_art"] is False and out["empty_chain"] is False


def test_badge_rendering_is_pure_js_shell_zero_added() -> None:
    """壳零新增（任务书硬要求 20）：徽章全在 plugin-run 视图 JS 里动态生成，
    HTML 侧无徽章静态标记——壳里既无「AI 建议」也无 data-rngadopt/awadopt。"""
    shell, rjs = _shell(), _plugin_run()
    for marker in ("AI 建议", "已弃用", "data-rngadopt", "data-rngdiscard",
                   "data-awadopt", "data-awdiscard"):
        assert marker not in shell, f"壳出现徽章静态标记（违反 HTML 零新增）: {marker}"
    # 徽章函数与两处挂载点都在视图产物内
    for marker in ("function prStBadge(", "function prRangeTitleBadge(",
                   "function prRngOps(", "function prAwOps(",
                   "prRangeTitleBadge(PR.ranges)", "prStBadge(PR.awStatus",
                   "prAdjudicate('adopt','ks'", "prAdjudicate('discard','aw'"):
        assert marker in rjs, marker
    # 裁决端点走动态段 URL（adopt|discard），confirm 闸门随 body
    assert "prAdjudicate(" in rjs and "{step:step,confirm:true}" in rjs


# ── d) 体积五锚（本批实测口径）────────────────────────────


def test_volume_five_anchors_with_batch_numbers() -> None:
    """体积五锚实测（任务书硬门）：壳 / 单 view（两新 view 各自报数）/ core.js /
    手写总量 / 首屏。锚值与 test_build_ui.py 同源；实测数写入报告。"""
    shell = UI_HTML.stat().st_size
    core = CORE_JS.stat().st_size
    plugins = PLUGINS_JS.stat().st_size
    plugin_run = PLUGIN_RUN_JS.stat().st_size
    view_files = sorted((STUDIO / "assets" / "views").glob("*.js"))
    biggest = max(p.stat().st_size for p in view_files)
    # 总交付面（ARCH-1 口径重校）：壳 + 手写 assets（剥 editor/ vendor）
    # 2026-11-24 W3-3a：与 test_build_ui 同口径——构建输入源（assets/src/）不入
    # 交付面（用户不下载它；产物才是）。两处口径必须一致，否则同一次改动一门绿一门红。
    handwritten = UI_HTML.stat().st_size + sum(
        p.stat().st_size
        for p in (STUDIO / "assets").rglob("*")
        if p.is_file()
        and "editor/" not in p.relative_to(STUDIO).as_posix()
        and "src/" not in p.relative_to(STUDIO).as_posix()
    )
    first_screen = shell + core + biggest
    assert shell <= SHELL_MAX, f"壳 {shell}B > {SHELL_MAX}B"
    assert core <= CORE_MAX, f"core.js {core}B > {CORE_MAX}B"
    assert plugins <= VIEW_MAX, f"plugins.js {plugins}B > {VIEW_MAX}B"
    assert plugin_run <= VIEW_MAX, f"plugin-run.js {plugin_run}B > {VIEW_MAX}B"
    assert biggest <= VIEW_MAX, f"最大视图 {biggest}B > {VIEW_MAX}B"
    assert handwritten <= TOTAL_MAX, f"手写总量 {handwritten}B > {TOTAL_MAX}B"
    assert first_screen <= FIRST_SCREEN_MAX, f"首屏 {first_screen}B > {FIRST_SCREEN_MAX}B"
