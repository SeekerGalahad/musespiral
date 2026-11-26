"""F1 编辑器一期测试（CM5 vendor 替换 textarea，正典=《musespiral-专业创作者视角
深度评审与改进-2026-11-18》§3.1；2026-11-19 落地批）。

三层断言（沿 test_booksource_import_modal 的三层惯例；真实浏览器行为面另见
tests/e2e/test_studio_editor_e2e.py——本仓 venv 装有 playwright 时随全量跑）：
  a) 行为断言（node 真实执行——从 assets/editor.js 源的 /*__ED_BEGIN__*/…
     /*__ED_END__*/ 哨兵段抽出纯函数，配最小桩在 node 里跑）：
     字数口径 edCount / 阅读时长 edRead（400 字/分）/ AI 块锚扫描 edAI（围栏内
     不计）/ 大纲 edOutline（H1-H3+分节字数+围栏内 # 不算）/ 光标所在节
     edSection / 选区包裹切换 edWrap（已包则脱）/ 行前缀切换 edLinePrefix
     （H1-H3/引用，已有则替换或去除）/ 冲刺计字 edSprintAdd（只累计新增）。
     node 不可用 → skip（诚实跳过，不伪造行为绿）。
  b) 结构断言（读手写源+构建产物）：editor.js 装载契约锚（vendor 路径/CM5 配置
     字面量/extraKeys/F11/editor-focus/localStorage 目标/25min 冲刺/400 字阅读
     /SearchCursor）；editor.css 主题与布局锚（cm-s-paper-ink/CodeMirror 填满/
     专注模式隐藏面/段落聚焦 0.3）；ui.js 两处接线锚（mountEdit 动态装载 +
     defaultPrevented 让渡）。
  c) 服务回环断言（真实回环服务器 + http.client）：/assets/editor.js 与
     assets/editor/ 三层嵌套 vendor 200 + Content-Type；`../` 穿越 400。
  d) 零外联硬门（手写面）：editor.js/editor.css 无 http(s)://、无协议相对引用、
     无 @import、无 CSS url()（vendor 是本地制品，由 test_build_ui 的
     VENDOR_PINNED 钉死，不在本门扫描面）。
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
EDITOR_JS = STUDIO / "assets" / "editor.js"
EDITOR_CSS = STUDIO / "assets" / "editor.css"
UI_JS = STUDIO / "ui.js"
UI_HTML = STUDIO / "ui.html"


def _frontend_js() -> str:
    """M3（2026-11-19）：core.js + views/*.js 合并面。

    按视图拆分后 mountEdit/activate 接线随 article 视图进 views/article.js，
    「接线已进构建产物」的断言须读合并面而非仅 core.js。
    """
    parts = [(STUDIO / "assets" / "core.js").read_text(encoding="utf-8")]
    vdir = STUDIO / "assets" / "views"
    if vdir.is_dir():
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(vdir.glob("*.js")))
    return "\n".join(parts)

ED_BEGIN = "/*__ED_BEGIN__*/"
ED_END = "/*__ED_END__*/"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
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


# ══ a) 行为断言（node 真实执行 ED 哨兵段）════════════════════

_ED_SEGMENT = None


def _ed_segment() -> str:
    """从 assets/editor.js 源抽出纯函数哨兵段（生产函数原样，不经构建转手）。"""
    global _ED_SEGMENT
    if _ED_SEGMENT is None:
        src = EDITOR_JS.read_text(encoding="utf-8")
        assert ED_BEGIN in src and ED_END in src, "editor.js 缺少 ED 哨兵段"
        _ED_SEGMENT = src.split(ED_BEGIN)[1].split(ED_END)[0]
    return _ED_SEGMENT


def _run_ed(script_body: str) -> dict:
    """哨兵段 + 断言脚本写临时 .cjs 交 node 执行；失败在 node 侧抛错（非零即红）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环两层仍随全量跑）")
    script = (
        "'use strict';\n" + _ed_segment() + "\n"
        "module.exports={edCount:edCount,edRead:edRead,edAI:edAI,edOutline:edOutline,"
        "edSection:edSection,edWrap:edWrap,edLinePrefix:edLinePrefix,edSprintAdd:edSprintAdd,"
        "edQuote:edQuote,edImgMd:edImgMd,edMatExcerpt:edMatExcerpt,edMatMeta:edMatMeta,"
        "edVideoMd:edVideoMd,edDurFmt:edDurFmt};\n"
        + script_body
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "ed_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_ed_count_non_whitespace_semantics():
    """edCount 与 ui.js 既有 ed-chars 同口径：去全部空白后计数。"""
    out = _run_ed(r"""
if(edCount('# 标题\n正文 abc')!==8)throw new Error('字数应去空白计 8（#+标题+正文+abc），实际 '+edCount('# 标题\n正文 abc'));
if(edCount('')!==0)throw new Error('空串应 0');
if(edCount('　')!==0)throw new Error('全角空格也应剔除');
console.log(JSON.stringify({ok:edCount('a b\tc\nd')}));
""")
    assert out == {"ok": 4}


def test_ed_read_400_chars_per_minute():
    """阅读时长：400 字/分；空文 0；非空不足 400 按 1 分钟计。"""
    out = _run_ed(r"""
if(edRead(0)!==0)throw new Error('空文应 0 分钟');
if(edRead(1)!==1)throw new Error('1 字应 1 分钟');
if(edRead(400)!==1)throw new Error('400 字应 1 分钟');
if(edRead(401)!==2)throw new Error('401 字应 2 分钟');
if(edRead(1200)!==3)throw new Error('1200 字应 3 分钟');
console.log(JSON.stringify({r800:edRead(800)}));
""")
    assert out == {"r800": 2}


def test_ed_ai_block_scan_skips_fences():
    """AI 块=行首 <!--b:id--> 独占行（与 ui.js BANCHOR 同式）；围栏内不算。"""
    out = _run_ed(r"""
var t='<!--b:abc12-->\n正文\n<!--b:xyz-9-->\n```\n<!--b:in-fence-->\n```\n<!--b:tail_1-->';
if(edAI(t)!==3)throw new Error('应 3 个（围栏内不计），实际 '+edAI(t));
if(edAI('<!--b:a--> 注释后同跟文字')!==0)throw new Error('非独占行不算');
if(edAI('')!==0)throw new Error('空文 0');
console.log(JSON.stringify({n:edAI(t)}));
""")
    assert out == {"n": 3}


def test_ed_outline_levels_fence_and_section_chars():
    """大纲：H1-H3 解析；围栏内 # 不算标题；chars=到下一同级/更高级标题前的
    节内全部字符（含子标题行的标记与文字，去空白——与总字数同口径）。"""
    out = _run_ed(r"""
var t='# 一级\n甲甲甲\n## 二级\n乙乙乙乙\n### 三级\n丙丙\n# 又一级\n丁';
var o=edOutline(t);
if(o.length!==4)throw new Error('应 4 个标题，实际 '+o.length);
if(o[0].line!==0||o[0].level!==1||o[0].title!=='一级')throw new Error('H1 解析错: '+JSON.stringify(o[0]));
if(o[1].level!==2||o[2].level!==3)throw new Error('层级错');
// 分节字数（去空白全计，含子标题行）：H1 节=甲3+「##二级」4+乙4+「###三级」5+丙2=18
if(o[0].chars!==18)throw new Error('H1 节应 18 字，实际 '+o[0].chars);
// H2 节=乙4+「###三级」5+丙2=11
if(o[1].chars!==11)throw new Error('H2 节应 11 字，实际 '+o[1].chars);
// H3 节=丙2
if(o[2].chars!==2)throw new Error('H3 节应 2 字，实际 '+o[2].chars);
// 末节到文末=丁1
if(o[3].chars!==1)throw new Error('末节应 1 字，实际 '+o[3].chars);
// 围栏内 # 不算标题
var f='```\n# 假标题\n```\n# 真标题';
var of=edOutline(f);
if(of.length!==1||of[0].title!=='真标题')throw new Error('围栏内 # 不应成标题: '+JSON.stringify(of));
// 标题行围栏开合成对处理（```text 语言标注）
var g='```js\n# x\n```';
if(edOutline(g).length!==0)throw new Error('带语言围栏内的 # 也不应成标题');
console.log(JSON.stringify({n:o.length,chars:[o[0].chars,o[1].chars,o[2].chars,o[3].chars]}));
""")
    assert out == {"n": 4, "chars": [18, 11, 2, 1]}


def test_ed_section_cursor_lookup():
    """光标所在节：最近一个 line<=cursor 的标题下标；无标题（导语区）-1。"""
    out = _run_ed(r"""
var o=edOutline('# A\n甲\n## B\n乙\n# C\n丙');
if(edSection(o,0)!==0)throw new Error('标题行本身属该节');
if(edSection(o,1)!==0)throw new Error('line1 在 A 节');
if(edSection(o,3)!==1)throw new Error('line3 在 B 节');
if(edSection(o,5)!==2)throw new Error('line5 在 C 节');
if(edSection(o,99)!==2)throw new Error('越界钳末节');
var empty=edOutline('无标题导语\n正文');
if(edSection(empty,0)!==-1)throw new Error('无标题应 -1（导语区）');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_ed_wrap_toggle_semantics():
    """包裹切换：未包则包（选区落在标记内）；已包同标记则脱。"""
    out = _run_ed(r"""
var r=edWrap('hello',0,5,'**','**');
if(r[0]!=='**hello**'||r[1]!==2||r[2]!==7)throw new Error('包裹结果错: '+JSON.stringify(r));
var u=edWrap('**hello**',2,7,'**','**');
if(u[0]!=='hello'||u[1]!==0||u[2]!==5)throw new Error('解包结果错: '+JSON.stringify(u));
// 空选区：插入空标记对，光标落中间
var e=edWrap('ab',1,1,'`','`');
if(e[0]!=='a``b'||e[1]!==2||e[2]!==2)throw new Error('空选区插入错: '+JSON.stringify(e));
// 相邻但不同标记不算已包
var n=edWrap('*hello**',1,6,'**','**');
if(n[0]!=='***hello****')throw new Error('不同标记应视为未包: '+n[0]);
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_ed_line_prefix_toggle():
    """行前缀切换（H1-H3/引用）：无→加；同→删；异（含其他标题级）→替换。"""
    out = _run_ed(r"""
var a=edLinePrefix('正文',0,2,'## ');
if(a[0]!=='## 正文')throw new Error('应加前缀: '+a[0]);
var b=edLinePrefix('## 正文',0,4,'## ');
if(b[0]!=='正文')throw new Error('同前缀应删: '+b[0]);
var c=edLinePrefix('# 正文',0,3,'### ');
if(c[0]!=='### 正文')throw new Error('异级应替换: '+c[0]);
var d=edLinePrefix('> 引用',0,3,'## ');
if(d[0]!=='## 引用')throw new Error('引用应被替换: '+d[0]);
// 多行选区逐行
var m=edLinePrefix('a\nb',0,3,'# ');
if(m[0]!=='# a\n# b')throw new Error('多行应逐行: '+m[0]);
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_ed_sprint_add_counts_only_growth():
    """冲刺计字：只累计新增（删除不回冲、不出现负数）。"""
    out = _run_ed(r"""
if(edSprintAdd(100,150)!==50)throw new Error('增长 50');
if(edSprintAdd(150,80)!==0)throw new Error('回删应 0');
if(edSprintAdd(0,10)!==10)throw new Error('从 0 起算');
if(edSprintAdd(10,10)!==0)throw new Error('持平 0');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


# ══ a2) E-2 内联研究纯函数（引用行/图片 Markdown/摘要/元信息）══════════


def test_ed_quote_citation_format():
    """E-2 引用行（§3.2 契约格式 `> 来自[素材名]：摘录`）：截 120、压空白、
    标题剥方括号/换行防破链接格式、空摘录降级为无冒号短式、空标题兜底。"""
    out = _run_ed(r"""
var a=edQuote('Wan 架构拆解','性能追平商业产品，\n社区生态比性能更重要');
if(a!=='> 来自[Wan 架构拆解]：性能追平商业产品， 社区生态比性能更重要')throw new Error('基本格式错: '+a);
// 摘录压空白（含换行与连续空格）+ 截 120（120 字符处截断，不含省略号）
var long=new Array(200).join('字');
var b=edQuote('T',long);
if(b.indexOf('> 来自[T]：')!==0)throw new Error('前缀错');
if(b.length!==('> 来自[T]：'.length+120))throw new Error('应恰截 120: '+b.length);
// 标题剥方括号与换行（[] 是 Markdown 链接保留字，换行破坏引用行）
var c=edQuote('标[题]\n名','x');
if(c!=='> 来自[标 题 名]：x')throw new Error('标题清洗错: '+c);
// 空摘录：无冒号短式；空标题兜底'未命名素材'
if(edQuote('T','')!=='> 来自[T]')throw new Error('空摘录应短式');
if(edQuote('','   ')!=='> 来自[未命名素材]')throw new Error('空标题应兜底');
// 空白折叠：换行变空格而非吞掉
var d=edQuote('T','a\n\n  b');
if(d!=='> 来自[T]：a b')throw new Error('空白折叠错: '+d);
console.log(JSON.stringify({ok:1,len:b.length}));
""")
    assert out == {"ok": 1, "len": len("> 来自[T]：") + 120}


def test_ed_video_md_and_dur_fmt():
    """B3（2026-11-26）:::video 容器生成与时长徽章（node 真实执行哨兵段纯函数）。

    edVideoMd：src 必有 / poster 选填 / caption 压空白——产 `:::video{src="…"}`
    容器块（服务端 parser._VIDEO_OPEN_RE 的带引号属性形态上游）；属性值过
    edEsc（引号→&quot;，不截断容器头）。edDurFmt：秒→m:ss / h:mm:ss。
    """
    out = _run_ed(r"""
// 基本形态：src only，无 poster 无 caption
var v1=edVideoMd('media/abc123.mp4','','');
if(v1!=='\n:::video{src="media/abc123.mp4"}\n:::\n')throw new Error('基础形态错: '+JSON.stringify(v1));
// src+poster+caption（多空白压单空格）
var v2=edVideoMd('media/a.mp4','media/a.poster.jpg','  多头   注意力  ');
if(v2!=='\n:::video{src="media/a.mp4" poster="media/a.poster.jpg"}\n多头 注意力\n:::\n')throw new Error('全参形态错: '+JSON.stringify(v2));
// 属性值含引号 → &quot;（不截断容器头；服务端解析的带引号形态仍成立）
var v3=edVideoMd('a"b.mp4','','');
if(v3.indexOf('src="a&quot;b.mp4"')<0)throw new Error('引号应转义: '+JSON.stringify(v3));
if(v3.indexOf('\n:::\n')<0)throw new Error('闭合行缺失: '+JSON.stringify(v3));
// 时长徽章：<1h 不补零小时；≥1h 补零分
if(edDurFmt(215)!=='3:35')throw new Error('3:35 错: '+edDurFmt(215));
if(edDurFmt(11)!=='0:11')throw new Error('0:11 错: '+edDurFmt(11));
if(edDurFmt(3661)!=='1:01:01')throw new Error('1:01:01 错: '+edDurFmt(3661));
if(edDurFmt(0)!=='')throw new Error('0 应空串');
if(edDurFmt(null)!=='')throw new Error('null 应空串');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


def test_ed_img_md_and_excerpt_meta():
    """E-2 图片 Markdown 与结果行摘要/元信息：![](path) 剥空白圆括号；
    摘录 text 优先 url 兜底截 80；meta 按 image 宽高 / video 时长 / text 字数分派。"""
    out = _run_ed(r"""
// 图片：![](path) 格式；path 剥空白与圆括号（Markdown 链接语法保留字）
if(edImgMd('/api/materials/file?id=abc123&variant=full')!=='![](/api/materials/file?id=abc123&variant=full)')throw new Error('图片格式错');
if(edImgMd(' a (1).png')!=='![](a1.png)')throw new Error('应剥空白与圆括号');
if(edImgMd('')!=='![]()')throw new Error('空路径应空参');
// 摘要：payload.text 优先、url 兜底、截 80
var m1={type:'text',payload:{text:'甲乙丙\n丁'.repeat(40),url:'http://x'}};
if(edMatExcerpt(m1).length!==80)throw new Error('text 应截 80: '+edMatExcerpt(m1).length);
if(edMatExcerpt({type:'link',payload:{url:'https://e.cn/a'}})!=='https://e.cn/a')throw new Error('url 兜底错');
if(edMatExcerpt({type:'text',payload:{}})!=='')throw new Error('全空应空串');
// meta：image=宽×高（缺尺寸空串）；video=时长；text=字数（去空白口径）
if(edMatMeta({type:'image',payload:{meta:{width:1280,height:720}}})!=='1280×720')throw new Error('图片尺寸错');
if(edMatMeta({type:'image',payload:{}})!=='')throw new Error('缺尺寸应空串');
if(edMatMeta({type:'video',payload:{meta:{duration_text:'12:34'}}})!=='12:34')throw new Error('视频时长错');
if(edMatMeta({type:'text',payload:{text:'a b c'}})!=='3 字')throw new Error('字数应去空白计 3');
if(edMatMeta({type:'radar_signal',payload:{}})!=='')throw new Error('其余类型应空串');
console.log(JSON.stringify({ok:1}));
""")
    assert out == {"ok": 1}


# ══ b) 结构断言（手写源 + 构建产物锚）══════════════════════


def test_editor_js_wiring_anchors():
    """editor.js 装载契约锚：vendor 相对路径 / CM5 配置字面量 / extraKeys /
    F11 / 专注 / localStorage 目标 / 冲刺 25min / 阅读速度 400 / SearchCursor。"""
    src = EDITOR_JS.read_text(encoding="utf-8")
    # vendor 全本地相对路径（零外联；CSS 链=base→纸墨主题→editor.css）
    for rel in ("assets/editor/codemirror.min.js",
                "assets/editor/mode/markdown/markdown.min.js",
                "assets/editor/addon/edit/closebrackets.min.js",
                "assets/editor/addon/edit/matchbrackets.min.js",
                "assets/editor/addon/search/searchcursor.min.js",
                "assets/editor/addon/search/search.min.js",
                "assets/editor/codemirror.min.css",
                "assets/editor/theme/paper-ink.css"):
        assert rel in src, rel
    # CM5 实例配置（任务书 §1 逐项）
    for token in ("mode:'markdown'", "lineNumbers:true", "autoCloseBrackets:true",
                  "matchBrackets:true", "tabSize:2", "indentUnit:2",
                  "theme:'paper-ink'", "lineWrapping:true", "fromTextArea"):
        assert token in src, token
    # 快捷键 extraKeys（⌘B/⌘I/⌘K/⌘F/⌘⇧F + Tab 智能缩进 + Esc）
    for key in ("'Cmd-B'", "'Ctrl-B'", "'Cmd-I'", "'Ctrl-I'", "'Cmd-K'",
                "'Cmd-F'", "'Cmd-Shift-F'", "'Tab'", "'Shift-Tab'", "'Esc'"):
        assert key in src, key
    # 一期功能锚
    for token in ("getSearchCursor",  # 搜索替换 SearchCursor 直驱
                  "'F11'", "editor-focus", "ed-para", "scrollCursorIntoView",  # 专注+打字机
                  "GOAL_KEY", "localStorage", "SPRINT_MS=25*60*1000",  # 目标+冲刺
                  "edRead", "400",  # 阅读时长
                  "__mdpubEditor"):  # 对 ui.js 暴露的装载契约
        assert token in src, token
    # 工具栏 17 钮全在（UIX #9 增 focus/tw 显式专注+打字机入口；
    # B3 2026-11-26 增 media 插入媒体）
    for btn in ("bold", "italic", "strike", "h1", "h2", "h3", "quote", "link",
                "code", "table", "image", "media", "mat", "ai", "full", "focus", "tw"):
        assert f"data-edb=\"{btn}\"" in src or f"['{btn}'" in src, btn
    # 数据回写契约：change → save() 写回 textarea → 派发 input（ui.js 链路零改动）
    assert "cm.save()" in src and "dispatchEvent(new Event('input'" in src


def test_editor_css_theme_and_layout_anchors():
    """editor.css：paper-ink 主题 / CM 填满 ed-stack / 专注模式隐藏面 / 段落聚焦。"""
    src = EDITOR_CSS.read_text(encoding="utf-8")
    for token in ("cm-s-paper-ink", ".ed-stack>.CodeMirror",
                  ".editor-focus .ed-outline", ".editor-focus .pv-stage",
                  ".ed-para .CodeMirror-code>div:not(.ed-cur){opacity:.3",
                  ".ed-bar{", ".ed-outline{", ".ed-goal-bar", ".ed-find[hidden]"):
        assert token in src, token


def test_editor_js_inline_research_anchors():
    """E-2 内联研究结构锚（§3.2）：⌘⇧S 两级绑定 / 工具栏📚 / 面板 DOM /
    debounce 300ms / GET /api/materials?q=&limit=8 / 插入引用与图片两格式 /
    Esc 收起回编辑器 / 面板保持。"""
    src = EDITOR_JS.read_text(encoding="utf-8")
    # 快捷键：CM extraKeys（编辑器内）+ 文档级兜底（编辑器外焦点，defaultPrevented 让渡）。
    # 键名序沿 CM5 keyName 合成（Alt→Ctrl→Cmd→Shift，Shift 最前置）——⌘⇧S=「Shift-Cmd-S」
    for key in ("'Shift-Cmd-S'", "'Shift-Ctrl-S'", "edRpToggle"):
        assert key in src, key
    assert "if(!(e.metaKey||e.ctrlKey)||!e.shiftKey)return;" in src
    # 工具栏 📚 按钮（原「素材库跳转」入口改为内联面板开合）
    assert "['mat','📚'" in src and "return edRpToggle()" in src
    # 面板 DOM + debounce + 搜索端点（q + limit=8，与 §3.2 交互流一致）
    for token in ("rp.id='ed-rp'", 'id="ed-rp-q"', 'id="ed-rp-list"',
                  "RP_DEB=300", "RP_LIMIT=8",
                  "'/api/materials?q='+encodeURIComponent(kw)+'&limit='+RP_LIMIT",
                  "setTimeout(edRpFetch,RP_DEB)", "seq!==rpSeq",
                  # 标题取值兜底链：接口行无顶层 title（title 在 origin.title）
                  "m.title||(m.origin||{}).title||m.id"):
        assert token in src, token
    # 插入两格式：引用行（replaceSelection 语义）与图片（/api/materials/file 内容寻址）
    for token in ("function edMatInsert", "replaceSelection(pre+text+'\\n','end')",
                  "edImgMd('/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=full')",
                  "edQuote(m.title||(m.origin||{}).title||'未命名素材'"):
        assert token in src, token
    # Esc 收起 → 光标回编辑器（面板 Esc + CM Esc 分支 + 离开视图收起）
    for token in ("if(ev.key==='Escape'){ev.preventDefault();edRpHide();}",
                  "else if(edRpOpen())edRpHide();", "if(edRpOpen())edRpHide();"):
        assert token in src, token
    # 插入后面板保持 + 焦点回搜索框（可继续搜下一个）
    assert "if(q)q.focus();" in src
    # 专注模式隐藏内联面板（与 ed-find 同语义）
    assert "editor-focus .ed-rp" in EDITOR_CSS.read_text(encoding="utf-8")


def test_materials_query_for_inline_panel(server, studio_root):
    """E-2 面板数据回环：GET /api/materials?q=<kw>&limit=8（面板 fetch 语义）——
    q 命中标题/正文（mat_search 命中域）、total 与 items 分离（F64 截断可见性）、
    limit 生效。真实回环服务器 + 落库两条素材。"""
    from musespiral.core.materials import model
    from musespiral.services import materials_service

    materials_service.add_material(model.make_material(
        mtype="text", source_id="kb", uri="docs/wan.md", title="Wan 架构拆解",
        license="CC-BY", payload={"text": "性能追平商业产品，社区生态比性能更重要"},
        fetched_at="2026-11-19T00:00:00"))
    materials_service.add_material(model.make_material(
        mtype="image", source_id="media_gen", uri="gen://abc",
        title="AI 对比图", license="AI-GENERATED",
        payload={"local_path": "materials/files/bb.png",
                 "meta": {"width": 1280, "height": 720}},
        fetched_at="2026-11-19T01:00:00"))
    # 命中标题词
    status, headers, body = _req(server, "GET", "/api/materials?q=Wan&limit=8")
    assert status == 200, status
    data = json.loads(body)
    assert isinstance(data.get("items"), list) and isinstance(data.get("total"), int)
    assert data["total"] >= 1
    assert any(m["origin"]["title"] == "Wan 架构拆解" for m in data["items"])
    # 命中正文词（q 搜 payload.text）
    _, _, body2 = _req(server, "GET", "/api/materials?q=%E7%A4%BE%E5%8C%BA%E7%94%9F%E6%80%81&limit=8")
    data2 = json.loads(body2)
    assert data2["total"] == 1 and data2["items"][0]["origin"]["title"] == "Wan 架构拆解"
    # 不命中的词 → 空集（面板显示「无匹配素材」占位）
    _, _, body3 = _req(server, "GET", "/api/materials?q=%E4%B8%8D%E5%AD%98%E5%9C%A8%E8%AF%8D&limit=8")
    data3 = json.loads(body3)
    assert data3["items"] == [] and data3["total"] == 0


def test_ui_js_minimal_wiring_anchors():
    """ui.js 两处接线锚（F1 最小改动）：mountEdit 动态装载 + 全局快捷键让渡；
    既有编辑链路字面量原样保留（保存读 EA.value / input 监听 / 块级同步滚动）。"""
    src = UI_JS.read_text(encoding="utf-8")
    # 接线一：mountEdit 首次装载 assets/editor.js（本地相对路径，零外联）
    assert "window.__mdpubEditor.activate(EA)" in src
    assert "_es.src='assets/editor.js'" in src
    # 接线二：CM 已消费的键不再触发全局快捷键
    assert "if(e.defaultPrevented)return;" in src
    # 既有链路原样（行为零变更的源面证据）
    for token in ("content:EA.value,confirm:true",  # ⌘S 保存读 textarea（CM change 即回写）
                  "EA.addEventListener('input'",  # input 派发即驱动 dirty/预览/状态栏
                  "function syncPreview(){",  # 块级映射（textarea 路径保留为降级面）
                  "EG.scrollTop=EA.scrollTop"):  # 行号列同步（降级面）
        assert token in src
    core = _frontend_js()
    assert "window.__mdpubEditor.activate(EA)" in core  # 接线随构建进产物（M3 后在 views/article.js）


def test_editor_assets_shipped_and_referenced_from_shell():
    """手写编辑器产物在包内（assets/editor.js|css）且 ui.html 零改动（运行时注入）。"""
    assert EDITOR_JS.is_file() and EDITOR_CSS.is_file()
    shell = UI_HTML.read_text(encoding="utf-8")
    # 壳不静态引用 editor（动态 createElement 注入，壳引用清单保持 core.css+core.js）
    assert "assets/editor.js" not in shell
    assert "assets/editor" not in shell


# ══ c) 服务回环（/assets/* 静态面覆盖编辑器产物与嵌套 vendor）════


def test_assets_editor_served_with_content_type(server):
    """editor.js/editor.css 与三层嵌套 vendor 经 /assets/* 200 + 正确 Content-Type。"""
    for rel, ctype in (
        ("assets/editor.js", "application/javascript"),
        ("assets/editor.css", "text/css"),
        ("assets/editor/codemirror.min.js", "application/javascript"),
        ("assets/editor/codemirror.min.css", "text/css"),
        ("assets/editor/mode/markdown/markdown.min.js", "application/javascript"),
        ("assets/editor/addon/search/searchcursor.min.js", "application/javascript"),
    ):
        status, headers, body = _req(server, "GET", f"/{rel}")
        assert status == 200 and body, (rel, status)
        assert headers.get("Content-Type") == ctype, (rel, headers.get("Content-Type"))


def test_assets_editor_traversal_rejected(server):
    """vendor 嵌套路径的 `../` 穿越拒绝（_resolve_inside root 边界，400 不泄根外文件）。"""
    status, _, _ = _req(server, "GET", "/assets/editor/../../pyproject.toml")
    assert status == 400, status


# ══ d) 零外联硬门（手写面）════════════════════════════════


def test_editor_handwritten_no_external_references():
    """editor.js/editor.css 零外联：无 http(s)://、无协议相对 src/href、无
    @import、无 CSS url()（与 test_studio_ui 的 M1 硬门同口径，覆盖到手写新面）。"""
    for path in (EDITOR_JS, EDITOR_CSS):
        blob = path.read_text(encoding="utf-8")
        assert re.search(r"https?://", blob, re.IGNORECASE) is None, path
        assert re.search(r"(?:src|href)\s*=\s*[\"']//", blob, re.IGNORECASE) is None, path
        assert "@import" not in blob, path
        assert re.search(r"(?i)(?<![A-Za-z0-9])url\s*\(", blob) is None, path
