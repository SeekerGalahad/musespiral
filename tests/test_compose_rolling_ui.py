"""G-T4 滚动摘要前端（正典=《musespiral-吸收功能设计-2026-11-18》§五）。

三层断言（沿 quickstart/editor 的三层惯例）：
  a) 行为断言（node 真实执行 assets/rolling.js 的纯函数——esc / slug 提取 /
     展示态 paint）：node 不可用 → skip（诚实跳过）。
  b) 结构断言（壳 + core.js + rolling.js 源）：右栏 details#cm-rolling 折叠区
     结构（只读 + 刷新 + 编辑三件套）/ 端点两形态字面量 / ui.js 动态装载钩子 /
     零外联（rolling.js 无 http(s):// 与 CDN 引用）/ core.js 未内联本功能
     （独立产物，不占 200KiB 触发线）。
  c) 回环断言（真实 Studio 服务器）：GET /assets/rolling.js 200 + 逐字节一致。
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from musespiral import studio

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
ROLLING_JS = STUDIO / "assets" / "rolling.js"
CORE_JS = STUDIO / "assets" / "core.js"
UI_JS = STUDIO / "ui.js"
UI_HTML = STUDIO / "ui.html"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _shell() -> str:
    assert UI_HTML.exists(), "壳产物缺失（须跑 scripts/build_ui.py）"
    return UI_HTML.read_text(encoding="utf-8")


def _rolling() -> str:
    assert ROLLING_JS.exists(), "assets/rolling.js 缺失（G-T4 独立产物）"
    return ROLLING_JS.read_text(encoding="utf-8")


# ══ a) 行为断言（node 真实执行 rolling.js 纯函数）═════════════════════


def _run(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环两层仍随全量跑）")
    src = _rolling()
    # 抽纯函数段（不触 DOM 的字节区间）：el/esc/slug 在 req 之前，paint 在 req 之后
    pure = src[src.index("function el("):src.index("function req(")]
    pure += src[src.index("function paint("):src.index("async function load(")]
    script = (
        "'use strict';\n"
        "var location={hash:''};\n"
        "var _el={};\n"
        "var document={getElementById:function(id){return _el[id]||null;}};\n"
        "function mkEl(){var e={textContent:'',style:{}};\n"
        "Object.defineProperty(e,'innerHTML',{set:function(h){var re=/id=\"([^\"]+)\"/g,m;"
        "while((m=re.exec(h))){if(!_el[m[1]])_el[m[1]]=mkEl();}}});return e;}\n"
        "function seed(id){_el[id]=mkEl();return _el[id];}\n"
        + pure
        + "\nmodule.exports={esc:esc,slug:slug,paint:paint,seed:seed,drop:function(id){delete _el[id];}};\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "rolling_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_rolling_esc_prevents_injection() -> None:
    out = _run(r"""
var s='<img src=x onerror="boom">\'';
var e=esc(s);
if(e.indexOf('<')>=0||e.indexOf('"')>=0)throw new Error('未转义: '+e);
console.log(JSON.stringify({e:e}));
""")
    assert "&lt;" in out["e"] and "&#39;" in out["e"]


def test_rolling_slug_from_hash() -> None:
    out = _run(r"""
location.hash='#compose?s=demo%2Fx';
var a=slug();
location.hash='#material?s=demo';
var b=slug();
location.hash='#compose';
var c=slug();
console.log(JSON.stringify({a:a,b:b,c:c}));
""")
    assert out["a"] == "demo/x"  # decodeURIComponent 生效
    assert out["b"] == ""  # 非创作工坊视图不取 slug
    assert out["c"] == ""  # 无 query 串


def test_rolling_paint_shows_empty_hint_and_progress() -> None:
    """展示态：空摘要给行动引导文案；有摘要给正文 + 距下次重算字数。"""
    out = _run(r"""
seed('cm-rolling-view');seed('cm-rolling-text');seed('cm-rolling-note');
paint({rolling_summary:'',rolling_summary_chars:0,rolling_summary_level:0});
var empty=el('cm-rolling-text').textContent;
paint({rolling_summary:'三解法已讲完。',rolling_summary_chars:1800,rolling_summary_level:1});
console.log(JSON.stringify({empty:empty,body:el('cm-rolling-text').textContent,
note:el('cm-rolling-note').textContent}));
""")
    assert "暂无摘要" in out["empty"]
    assert out["body"] == "三解法已讲完。"
    assert "1800" in out["note"] and "1200" in out["note"]  # 3000-1800=1200


def test_rolling_paint_restores_view_after_edit_state() -> None:
    """编辑态恢复回归（浏览器实测抓到的真缺陷）：edit() 用 textarea 替换
    #cm-rolling-view 内容后，paint 必须重建只读骨架——否则保存/取消后
    折叠区永久停在编辑态、正文不再更新。
    """
    out = _run(r"""
// 模拟 edit() 之后的 DOM：view 里只剩 textarea + 按钮行，无 cm-rolling-text
seed('cm-rolling-view');seed('cm-rolling-note');
delete _el['cm-rolling-text'];
paint({rolling_summary:'保存后的摘要。',rolling_summary_chars:1800,rolling_summary_level:1});
console.log(JSON.stringify({text:el('cm-rolling-text').textContent,
hasNode:!!el('cm-rolling-text')}));
""")
    assert out["hasNode"] is True and out["text"] == "保存后的摘要。"


# ══ b) 结构断言 ══════════════════════════════════════════════════


def test_rolling_details_block_in_shell() -> None:
    """右栏折叠区结构：details#cm-rolling + 只读展示 + 刷新 + 编辑 + 进度提示。
    ARCH-1：compose 骨架源在 ui.views/compose.html（壳内为懒注入占位）。"""
    shell = (UI_HTML.parent / "ui.views" / "compose.html").read_text(encoding="utf-8")
    assert 'id="cm-rolling"' in shell
    assert "📖 滚动摘要" in shell
    assert 'id="cm-rolling-view"' in shell and 'id="cm-rolling-text"' in shell
    assert 'id="cm-rolling-refresh"' in shell and 'id="cm-rolling-edit"' in shell
    assert 'id="cm-rolling-note"' in shell


def test_rolling_endpoints_and_forms() -> None:
    """两形态端点字面量：GET session 取数 / POST rolling-summary 刷新与手改。"""
    src = _rolling()
    assert "/api/compose/session?slug=" in src
    assert "/api/compose/rolling-summary" in src
    assert "{slug:s,confirm:true}" in src  # 刷新形态（无 summary）
    assert "summary:String(text||'')" in src  # 手改形态
    assert "confirm:true" in src  # confirm 闸门
    # 编辑态三件套
    for token in ("cm-rolling-area", "cm-rolling-save", "cm-rolling-cancel"):
        assert token in src


def test_rolling_loaded_dynamically_not_inlined() -> None:
    """独立产物纪律：ui.js 一行钩子装载；core.js 只含该钩子、不含本功能实现。"""
    ui = UI_JS.read_text(encoding="utf-8")
    core = CORE_JS.read_text(encoding="utf-8")
    assert "assets/rolling.js" in ui
    assert "assets/rolling.js" in core
    assert "cm-rolling-edit" not in core, "滚动摘要实现不得内联进 core.js（200KiB 触发线）"
    assert "function paint(" not in core and "function paint(" in _rolling()


def test_rolling_no_external_refs() -> None:
    """零外联：滚动摘要产物无 http(s):// / CDN / 协议相对引用。"""
    src = _rolling()
    for bad in ("http://", "https://", "//cdn", "unpkg", "jsdelivr"):
        assert bad not in src, f"滚动摘要产物出现外联字样: {bad}"


# ══ c) 回环断言：/assets/rolling.js ═══════════════════════════════


def test_rolling_asset_served(tmp_path: Path) -> None:
    """/assets/rolling.js：200 + application/javascript + 与源逐字节一致。"""
    import threading

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/assets/rolling.js")
        resp = conn.getresponse()
        body = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        assert resp.status == 200
        assert ctype.startswith("application/javascript"), ctype
        assert body == ROLLING_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
