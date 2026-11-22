"""I1/E-4 极速启动测试（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.4）。

三层断言（沿 test_editor_ui.py 的三层惯例）：
  a) 行为断言（node 真实执行——从 assets/quickstart.js 的 /*__QS_BEGIN__*/…
     /*__QS_END__*/ 哨兵段抽纯函数，配最小桩在 node 里跑）：slug 生成 / HTML 转义 /
     跳转 hash / 进度百分比 / 弹窗结构（选题标题+素材 checkbox+模式 chips+双按钮）/
     单块流式 HTML（heading vs para）/ 就绪按钮文案。node 不可用 → skip（诚实跳过）。
  b) 结构断言（读源 quickstart.js + 构建产物 core.js + ui.css + ui.js 装载钩子）：
     端点链 compose/new→add→generate /jobs 轮询 / 逐块揭示 / 动态装载一行钩子 /
     零外联（无 http(s)://）。
  c) 服务回环断言（真实回环服务器 + http.client）：/assets/quickstart.js 200 +
     Content-Type + 产物逐字节一致 + `../` 穿越 400（安全四门同 _studio_asset_response）。
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
QS_JS = STUDIO / "assets" / "quickstart.js"
CORE_JS = STUDIO / "assets" / "core.js"
UI_JS = STUDIO / "ui.js"
UI_CSS = STUDIO / "ui.css"

QS_BEGIN = "/*__QS_BEGIN__*/"
QS_END = "/*__QS_END__*/"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def studio_root(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    import threading
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _req(server, method: str, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    headers = dict(resp.getheaders())
    conn.close()
    return resp.status, headers, body


# ══ a) 行为断言（node 真实执行 QS 哨兵段）════════════════════

_QS_SEGMENT = None


def _qs_segment() -> str:
    global _QS_SEGMENT
    if _QS_SEGMENT is None:
        src = QS_JS.read_text(encoding="utf-8")
        assert QS_BEGIN in src and QS_END in src, "quickstart.js 缺少 QS 哨兵段"
        _QS_SEGMENT = src.split(QS_BEGIN)[1].split(QS_END)[0]
    return _QS_SEGMENT


def _run_qs(script_body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环两层仍随全量跑）")
    script = (
        "'use strict';\n" + _qs_segment() + "\n"
        "module.exports={qsSlug:qsSlug,qsEsc:qsEsc,qsWriteHash:qsWriteHash,qsPhasePct:qsPhasePct,"
        "qsModalHtml:qsModalHtml,qsBlockHtml:qsBlockHtml,qsReadyHtml:qsReadyHtml,QS_MODES:QS_MODES};\n"
        + script_body
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "qs_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_qs_slug_is_stable_and_prefixed():
    out = _run_qs(r"""
if(qsSlug(123)!==qsSlug(123))throw new Error('同 ts 应稳定');
if(qsSlug(123).indexOf('qs-')!==0)throw new Error('slug 应以 qs- 开头');
console.log(JSON.stringify({a:qsSlug(123),b:qsSlug(123)}));
""")
    assert out["a"] == out["b"] and out["a"].startswith("qs-")


def test_qs_escape_prevents_injection():
    out = _run_qs(r"""
var s='<img src=x onerror="boom">\'';
var e=qsEsc(s);
if(e.indexOf('<')>=0||e.indexOf('"')>=0||e.indexOf("'")>=0)throw new Error('未转义: '+e);
if(e.indexOf('&lt;')<0)throw new Error('应含 &lt;: '+e);
console.log(JSON.stringify({n:e.length}));
""")
    assert out["n"] > 0


def test_qs_write_hash_lands_in_editor():
    """F1（评审修复批）：开始写作落点=文章编辑器深链（正典 §3.4「跳转到
    编辑器（骨架已就位）」）——path 编码 + tab=edit，而非 compose 会话页。"""
    out = _run_qs(r"""
var h=qsWriteHash('qs a/b.md');
if(h!=='#article?path=qs%20a%2Fb.md&tab=edit')throw new Error('编辑器深链编码错: '+h);
if(qsWriteHash('x.md').indexOf('#article?path=x.md&tab=edit')!==0)throw new Error('常规路径错');
console.log(JSON.stringify({h:h}));
""")
    assert out["h"] == "#article?path=qs%20a%2Fb.md&tab=edit"


def test_qs_phase_pct_parses_progress():
    out = _run_qs(r"""
if(qsPhasePct('3/4')!==75)throw new Error('3/4 应 75');
if(qsPhasePct('x')!==null)throw new Error('无进度应 null');
if(qsPhasePct('')!==null)throw new Error('空应 null');
console.log(JSON.stringify({p:qsPhasePct('1/2')}));
""")
    assert out["p"] == 50


def test_qs_modal_structure_title_mats_modes_buttons():
    out = _run_qs(r"""
var h=qsModalHtml('Wan 架构拆解',[{title:'提炼卡',uri:'a.md'},{title:'我的文章',path:'b.md'}],'观点');
function has(s){if(h.indexOf(s)<0)throw new Error('弹窗缺 '+s);}
has('极速启动');has('<strong>Wan 架构拆解</strong>');
has('data-qs-mat="a.md"');has('data-qs-mat="b.md"');  // 挂载素材 checkbox
has('提炼卡');has('我的文章');
QS_MODES.forEach(function(m){has('data-qs-mode="'+m+'"');});  // 四模式 chips
has('is-active');  // 当前模式高亮
has('id="qs-cancel"');has('id="qs-go"');has('30秒出骨架');has('🚀');
if((h.match(/data-qs-mat=/g)||[]).length!==2)throw new Error('应 2 个素材');
if((h.match(/data-qs-mode=/g)||[]).length!==4)throw new Error('应 4 个模式');
console.log(JSON.stringify({len:h.length}));
""")
    assert out["len"] > 0


def test_qs_modal_empty_materials_degrades():
    out = _run_qs(r"""
var h=qsModalHtml('纯选题',[],'技术');
if(h.indexOf('暂无素材')<0)throw new Error('空素材应给提示');
if(h.indexOf('data-qs-mat=')>=0)throw new Error('空素材不应有 checkbox');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_qs_block_html_heading_vs_para():
    out = _run_qs(r"""
var hd=qsBlockHtml({kind:'heading',text:'# 标题'});
var pa=qsBlockHtml({kind:'para',text:'第一行\n第二行 <b>'});
if(hd.indexOf('qs-bh')<0||hd.indexOf('<h4')<0)throw new Error('heading 渲染错: '+hd);
if(pa.indexOf('qs-bp')<0||pa.indexOf('<br>')<0)throw new Error('para 换行渲染错: '+pa);
if(pa.indexOf('&lt;b&gt;')<0)throw new Error('块文本应转义: '+pa);
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_qs_ready_button_states():
    out = _run_qs(r"""
var h=qsReadyHtml();
if(h.indexOf('disabled')<0||h.indexOf('qs-ready')<0)throw new Error('就绪按钮初态错');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


# ══ b) 结构断言（源 + 产物）════════════════════════════════════

def test_quickstart_wiring_structure():
    src = QS_JS.read_text(encoding="utf-8")
    core = CORE_JS.read_text(encoding="utf-8")
    ui_js = UI_JS.read_text(encoding="utf-8")
    # CSS 语料=ui.css(core)+ui.views/*.css 合并（ARCH-1 ② 拆分等价域；qs-* 现落
    # assets/views/insight.css 源 ui.views/insight.css）
    css = (UI_CSS.read_text(encoding="utf-8") + "\n" + "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(UI_CSS.parent.glob("ui.views/*.css"))))
    # 端点链：new → add → generate → 轮询 jobs →（F1）decide keep-all → adopt
    for endpoint in ("/api/compose/new", "/api/compose/add", "/api/compose/generate",
                     "/api/jobs/", "/api/compose/decide", "/api/compose/adopt"):
        assert endpoint in src, f"quickstart.js 缺端点 {endpoint}"
    # F1：keep 快速裁决 + 编辑器深链；F2：生成期 partial 重绘 + 无增量降级揭示
    for token in ("qReveal", "qPaintPartial", "x.partial", "'keep'",
                  "qsWriteHash", "location.hash", "MutationObserver"):
        assert token in src, f"quickstart.js 缺交互 token {token}"
    # 旧落点退役：跳创作工坊的 hash 已删（正典 §3.4 落点=编辑器）
    assert "qsJumpHash" not in src and "#compose?s=" not in src
    # 骨架模式四键
    for mode in ("技术", "观点", "教程", "对比"):
        assert mode in src
    # 动态装载钩子：ui.js 一处注入 quickstart.js，且已编进产物 core.js
    assert "assets/quickstart.js" in ui_js
    assert "assets/quickstart.js" in core
    # CSS 流式区 + mono 字体
    assert ".qs-stream" in css and "monospace" in css
    # 零外联
    assert "http://" not in src and "https://" not in src


def test_quickstart_is_standalone_not_in_core_body():
    """quickstart 全部逻辑在独立产物，core.js 只多一行装载钩子（避开 200KiB 门）。"""
    core = CORE_JS.read_text(encoding="utf-8")
    assert "function qRun()" not in core, "quickstart 逻辑误编进 core.js"
    assert "qsModalHtml" not in core, "quickstart 纯函数误编进 core.js"


# ══ c) 服务回环断言 ══════════════════════════════════════════

def test_quickstart_asset_served_and_byte_equal(server):
    status, headers, body = _req(server, "GET", "/assets/quickstart.js")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("application/javascript")
    assert body == QS_JS.read_bytes(), "/assets/quickstart.js 与源产物不一致"


def test_quickstart_traversal_rejected(server):
    status, _, _ = _req(server, "GET", "/assets/../ui.js")
    assert status == 400
