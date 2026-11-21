"""R4 导入弹窗 import-modal 测试（书源实弹修复与导入弹窗设计 §三 2026-11-19）。

三层断言（沿 test_studio_ui_wiring 两层惯例 + 新增第三层真实 JS 执行）：
  a) 行为断言（node 真实执行——本仓 venv 无 playwright，e2e 恒跳过；node 是
     本机前端逻辑的可执行验证面）：从 ui.js 源的 /*__IM_BEGIN__*/…
     /*__IM_END__*/ 哨兵段抽出弹窗核心函数，配 DOM 桩在 node 里跑——
     D1 虚拟滚动窗口（3906 行只渲 ≤26 行 + 边界/中段/末端三态）/
     D2 多轴过滤（关键词名称/分组/URL contains + 状态 chips + 分组 chips）/
     D3 全选（当前筛选）语义 / D4 状态徽三色 + 已跳过灰显行 /
     D5 同 URL 合并（N 版本取最新 + ▸ 展开版本子行）/
     D7 底部汇总（已选/新增/更新）/ D8 非书源包（imIngest 拒收+黄条+灰显）。
     node 不可用 → skip（诚实跳过，不伪造行为绿）。
  b) 结构断言（读构建产物 ui.html/core.js/core.css）：弹窗标记锚（三段式=
     入口 tabs+过滤/列表+底部汇总）、虚拟滚动容器 400px、importKeep 三开关、
     API 调用体字面量（confirm:true + text_or_url + selections + keep 三键）。
  c) API 回环断言（真实回环服务器 + http.client）：POST /api/booksource/import
     请求形状（confirm 闸 400 / 书源包 items+skipped+counts.dupUrls / 非书源
     包 kind=theme+message+空 items）→ POST import_apply（selections 落库 +
     deduped_from/stored 透明计数）→ GET /api/booksource 见新源。
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio

UI = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.html"
JS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.js"
CORE_JS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "core.js"
CORE_CSS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "core.css"


def _frontend_js() -> str:
    """M3（2026-11-19）：core.js + views/*.js 合并面。

    导入弹窗（R4/D1-D8）随 books 视图进 views/books.js，行为字面量断言须读合并面。
    """
    parts = [CORE_JS.read_text(encoding="utf-8")]
    vdir = CORE_JS.parent / "views"
    if vdir.is_dir():
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(vdir.glob("*.js")))
    return "\n".join(parts)

IM_BEGIN = "/*__IM_BEGIN__*/"
IM_END = "/*__IM_END__*/"


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


def _req(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return resp.status, data


def _shell() -> str:
    assert UI.exists(), f"构建产物缺失: {UI}"
    return UI.read_text(encoding="utf-8")


# ══ a) 行为断言（node 真实执行 IM 哨兵段）══════════════════════

# DOM 桩：$() 按 id 惰性建假元素（hidden/textContent/innerHTML/style/classList/
# scrollTop…）；QA 恒空表；toast/postJSON 记录调用。哨兵段内的生产函数原样执行。
_HARNESS = r"""'use strict';
var _els={},_toasts=[],_posts=[];
function $(id){if(!_els[id])_els[id]={id:id,hidden:true,textContent:'',innerHTML:'',
style:{},value:'',checked:false,disabled:false,scrollTop:0,
classList:{_s:{},toggle:function(c,f){this._s[c]=!!f;},add:function(c){this._s[c]=true;},remove:function(c){this._s[c]=false;}}};
return _els[id];}
var QA=function(){return [];};
function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(ch){
return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function toast(m,k){_toasts.push([m,k||'']);}
var WN='warning',ER='error',OK='success';
function postJSON(p,b){_posts.push([p,b]);return Promise.resolve({});}
function bkLamp(){}
var els=_els;
"""

_IM_SEGMENT = None


def _im_segment() -> str:
    """从 ui.js 源抽出弹窗哨兵段（生产函数原样，不经构建转手）。"""
    global _IM_SEGMENT
    if _IM_SEGMENT is None:
        src = JS.read_text(encoding="utf-8")
        assert IM_BEGIN in src and IM_END in src, "ui.js 缺少 IM 哨兵段"
        _IM_SEGMENT = src.split(IM_BEGIN)[1].split(IM_END)[0]
    return _IM_SEGMENT


def _run_im(script_body: str) -> dict:
    """把哨兵段 + 桩 + 断言脚本写进临时 .cjs，node 执行；输出 JSON 行为记录。

    断言失败在 node 侧 process.exit(1) 并打印原因——subprocess 非零即测试红。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/API 两层仍随全量跑）")
    script = (
        _HARNESS
        + _im_segment()
        + "\nmodule.exports={IM:IM,$:$,imGroups:imGroups,imLinear:imLinear,"
        "imMatch:imMatch,imFilter:imFilter,imWindow:imWindow,imBadge:imBadge,"
        "imChips:imChips,imPaintWin:imPaintWin,imRepaint:imRepaint,imSelN:imSelN,"
        "imSelAll:imSelAll,imSummary:imSummary,imIngest:imIngest,imNonSource:imNonSource,"
        "imRo:imRo,els:_els,toasts:_toasts,posts:_posts};\n"
        + script_body
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "im_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [node, str(path)], capture_output=True, text=True, timeout=60
        )
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_d1_virtual_window_renders_bounded_rows():
    """D1：3906 行列表任意滚动位置只渲 ≤26 行（可见 400px/30px + ±5 缓冲）。"""
    out = _run_im(r"""
var n=3906,W=imWindow;
function mid(st){var w=W(st,n);return w[1]-w[0];}
// 首屏：start 钳 0；行窗=ceil(400/30)+5=19
var w0=W(0,n);
if(w0[0]!==0||w0[1]!==19)throw new Error('首屏窗应为 [0,19)，实际 '+JSON.stringify(w0));
// 中段：scrollTop=6000 → [195,219)
var wm=W(6000,n);
if(wm[0]!==195||wm[1]!==219)throw new Error('中段窗应为 [195,219)，实际 '+JSON.stringify(wm));
// 末端：scrollTop=max=n*30-400 → end 钳 n
var we=W(n*30-400,n);
if(we[1]!==n||we[0]!==3887)throw new Error('末端窗 end 应钳 3906，实际 '+JSON.stringify(we));
// 全程上界：任意滚动位渲染行数 ≤ ceil(400/30)+2*5=24（整除处）——3906 行直渲 vs ≤26 行
for(var st=0;st<=n*30;st+=777){if(mid(st)>26)throw new Error('滚动位 '+st+' 渲染 '+mid(st)+' 行超界');}
// 短列表不越界：n=3 全可见
var w3=W(0,3);if(w3[0]!==0||w3[1]!==3)throw new Error('短列表应全渲，实际 '+JSON.stringify(w3));
console.log(JSON.stringify({first:w0,mid:wm,end:we}));
""")


def test_d5_duplicate_url_merged_with_expand():
    """D5：同 URL 合并一行（N 版本取最新）+ ▸ 展开出版本子行。"""
    out = _run_im(r"""
var items=[
 {status:'latest',selected:false,source:{bookSourceName:'起点旧版',bookSourceGroup:'玄幻',bookSourceUrl:'https://q.com'},local:null},
 {status:'isNew',selected:true,source:{bookSourceName:'起点中文',bookSourceGroup:'玄幻',bookSourceUrl:'https://q.com'},local:null},
 {status:'isNew',selected:true,source:{bookSourceName:'笔趣阁',bookSourceGroup:'都市',bookSourceUrl:'https://b.com'},local:null}];
var rows=imGroups(items,[{index:9,reason:'缺 bookSourceUrl'}]);
if(rows.length!==3)throw new Error('3 条（2 同 URL）应合并为 2 组+1 跳过行，实际 '+rows.length);
var dup=rows[0];
if(dup.n!==2)throw new Error('重复组应记 2 版本，实际 '+dup.n);
if(dup.last.source.bookSourceName!=='起点中文')throw new Error('组态应取末条（落库=最后一条），实际 '+dup.last.source.bookSourceName);
if(rows[2].t!=='s'||rows[2].idx!==9)throw new Error('跳过行应追加在末尾');
// 未展开=2 组+1 跳过=3 行；展开=2 组+2 版本子行+1 跳过=5 行
IM.groups=rows;IM.exp={};IM.sel={};IM.kw='';IM.stat='all';IM.grp='';
if(imLinear().length!==3)throw new Error('未展开应 3 行，实际 '+imLinear().length);
IM.exp['https://q.com']=true;
var lin=imLinear();
if(lin.length!==5)throw new Error('展开后应 5 行（+2 版本子行），实际 '+lin.length);
if(lin[1].t!=='v'||lin[1].v.source.bookSourceName!=='起点旧版')throw new Error('版本子行应按包内序');
// 渲染：未展开显示「2 版本取最新」；展开子行带 is-ver
IM.exp={};IM.visible=imFilter(imLinear());
$('im-list').scrollTop=0;
imPaintWin();
var html=els['im-canvas'].innerHTML;
if(html.indexOf('2 版本取最新')<0)throw new Error('合并行缺「N 版本取最新」标注：'+html);
IM.exp['https://q.com']=true;IM.visible=imFilter(imLinear());imPaintWin();
if(els['im-canvas'].innerHTML.indexOf('is-ver')<0)throw new Error('版本子行缺 is-ver');
console.log(JSON.stringify({merged:rows.length}));
""")


def test_d2_filter_keyword_status_group():
    """D2：关键词（名称/分组/URL contains）+ 状态 chips + 分组 chips 三轴。"""
    out = _run_im(r"""
function mk(n,g,u,st){return {status:st||'isNew',selected:true,source:{bookSourceName:n,bookSourceGroup:g,bookSourceUrl:u},local:null};}
IM.groups=imGroups([
 mk('起点中文','玄幻,出版','https://www.qidian.com'),
 mk('笔趣阁','都市','https://www.biquge.co'),
 mk('纵横','出版','https://www.zongheng.com','latest')],[]);
function f(){return imFilter(imLinear()).length;}
IM.kw='';IM.stat='all';IM.grp='';
if(f()!==3)throw new Error('无过滤应 3 行');
IM.kw='笔趣';if(f()!==1)throw new Error('关键词按名称应中 1，实际 '+f());
IM.kw='QIDIAN';if(f()!==1)throw new Error('关键词按 URL 应大小写无关中 1，实际 '+f());
IM.kw='出版';if(f()!==2)throw new Error('关键词按分组应中 2，实际 '+f());
IM.kw='';IM.stat='latest';if(f()!==1)throw new Error('状态=已最新应 1 行，实际 '+f());
IM.stat='isNew';if(f()!==2)throw new Error('状态=新应 2 行，实际 '+f());
IM.stat='all';IM.grp='出版';if(f()!==2)throw new Error('分组=出版应 2 行，实际 '+f());
IM.grp='玄幻';IM.kw='起点';if(f()!==1)throw new Error('三轴叠加应 1 行，实际 '+f());
IM.groups=imGroups([],[{index:2,reason:'缺 bookSourceUrl'},{index:5,reason:'name 空'}]);
IM.kw='';IM.stat='all';IM.grp='';
if(f()!==2)throw new Error('全部 chip 应含跳过行 2，实际 '+f());
IM.stat='isNew';if(f()!==0)throw new Error('状态=新不应含跳过行');
IM.stat='skipped';if(f()!==2)throw new Error('已跳过 chip 应 2，实际 '+f());
IM.kw='name';if(f()!==1)throw new Error('跳过行关键词对原因过滤应中 1，实际 '+f());
console.log(JSON.stringify({ok:1}));
""")


def test_d3_select_all_current_filter_only():
    """D3：全选=当前筛选结果（勾选只落在筛内行；清空只清筛内，筛外保留）。"""
    out = _run_im(r"""
function mk(n,g,u,st){return {status:st||'isNew',selected:false,source:{bookSourceName:n,bookSourceGroup:g,bookSourceUrl:u},local:null};}
IM.groups=imGroups([
 mk('A','玄幻','https://a.com'),
 mk('B','都市','https://b.com'),
 mk('C','玄幻','https://c.com'),
 mk('D','都市','https://d.com','latest')],[]);
IM.kw='';IM.stat='all';IM.grp='';
imRepaint();
if(els['im-selall-t'].textContent!=='全选（当前筛选 4 条）')throw new Error('全选标签应明示当前筛选条数：'+els['im-selall-t'].textContent);
imSelAll(true);
if(imSelN()!==4)throw new Error('无过滤全选应 4，实际 '+imSelN());
// 过滤到 2 条玄幻 → 标签更新为当前筛选 2 条；再全选不波及筛外（也不清筛外已有勾选）
IM.grp='玄幻';imRepaint();
if(els['im-selall-t'].textContent!=='全选（当前筛选 2 条）')throw new Error('过滤后标签应更新：'+els['im-selall-t'].textContent);
imSelAll(true);
if(imSelN()!==4)throw new Error('筛内全选后仍应 4（a,c 已选，b,d 不动），实际 '+imSelN());
// 清空只作用于当前筛选：筛内（玄幻 a,c）清空，筛外（都市 b,d）保留
imSelAll(false);
if(IM.sel['https://a.com']||IM.sel['https://c.com'])throw new Error('筛内应清空');
if(!IM.sel['https://b.com']||!IM.sel['https://d.com'])throw new Error('筛外应保留勾选');
// 汇总（D7）：已选 2 · 新增 1（b=isNew）· 更新 0（d=latest 显式勾选计入已选但不计新增/更新）
imSummary();
if(els['im-sum'].textContent!=='已选 2 条 · 新增 1 · 更新 0')throw new Error('汇总应「已选 2 条 · 新增 1 · 更新 0」，实际 '+els['im-sum'].textContent);
if(els['im-apply'].disabled)throw new Error('有选中时导入钮应可用');
console.log(JSON.stringify({sel:imSelN()}));
""")


def test_d4_badges_and_skipped_row_grayed():
    """D4：新=竹绿(success)/更新=琥珀(warning)/已最新=灰(queued)；跳过行灰显+title 原因。"""
    out = _run_im(r"""
function mk(n,g,u,st,sel){return {status:st,selected:!!sel,source:{bookSourceName:n,bookSourceGroup:g,bookSourceUrl:u},local:null};}
if(imBadge('isNew').indexOf('badge success')<0||imBadge('isNew').indexOf('新')<0)throw new Error('新徽应 success 竹绿:'+imBadge('isNew'));
if(imBadge('isUpdate').indexOf('badge warning')<0||imBadge('isUpdate').indexOf('更新')<0)throw new Error('更新徽应 warning 琥珀');
if(imBadge('latest').indexOf('badge queued')<0||imBadge('latest').indexOf('已最新')<0)throw new Error('已最新徽应 queued 灰');
IM.groups=imGroups([mk('A','玄幻','https://a.com','isNew',true)],[{index:0,reason:'缺 bookSourceUrl'}]);
IM.skipped=[{index:0,reason:'缺 bookSourceUrl'}]; // 生产路径由 imIngest 置（与 groups 同源）
IM.kw='';IM.stat='skipped';IM.grp='';imRepaint();
var html=els['im-canvas'].innerHTML;
if(html.indexOf('is-skip')<0)throw new Error('跳过行应带 is-skip 灰显类');
if(html.indexOf('已跳过')<0)throw new Error('跳过行应有已跳过徽');
if(html.indexOf('disabled')<0)throw new Error('跳过行复选框应禁用');
if(html.indexOf('title="已跳过：缺 bookSourceUrl"')<0)throw new Error('跳过行应悬浮原因');
// 已跳过 chip 的 title 聚合原因清单
imChips();
if(els['im-stat'].innerHTML.indexOf('⚠已跳过 1')<0)throw new Error('状态 chips 缺已跳过计数');
if(els['im-stat'].innerHTML.indexOf('缺 bookSourceUrl')<0)throw new Error('已跳过 chip 应悬浮原因清单');
console.log(JSON.stringify({ok:1}));
""")


def test_d6_group_chips_top10_with_counts():
    """D6：分组 chips 从 bookSourceGroup 聚合 top-10 带计数（未分组归桶）。"""
    out = _run_im(r"""
function mk(n,g){return {status:'isNew',selected:false,source:{bookSourceName:n,bookSourceGroup:g,bookSourceUrl:'https://'+n+'.com'},local:null};}
var items=[];
'ABCDEFGHIJ'.split('').forEach(function(ch,i){items.push(mk(ch,'玄幻'));});
items.push(mk('K','都市'));items.push(mk('L',''));items.push(mk('M',null));
IM.groups=imGroups(items,[]);
IM.kw='';IM.stat='all';IM.grp='';
imChips();
var h=els['im-groups'].innerHTML;
if(h.indexOf('全部分组')<0)throw new Error('分组 chips 缺「全部分组」');
if(h.indexOf('玄幻 <span class="chip-count">10')<0)throw new Error('玄幻应计 10：'+h);
if(h.indexOf('（未分组）')<0)throw new Error('空分组应归（未分组）桶');
if(h.indexOf('都市')<0)throw new Error('都市 chip 缺失');
// top-10 上限：12 个不同分组只出 10 个（计数降序）
var items2=[];
'ABCDEFGHIJKL'.split('').forEach(function(ch,i){items2.push(mk(ch,'组'+ch));});
IM.groups=imGroups(items2,[]);
imChips();
var chips=els['im-groups'].innerHTML.split('data-imgrp=').length-1; // 含「全部分组」
if(chips!==11)throw new Error('top-10 应 11 个 chip（含全部），实际 '+chips);
console.log(JSON.stringify({chips:chips}));
""")


def test_d8_non_booksource_package_readonly():
    """D8：kind≠bookSource → imIngest 拒收 + 黄条类型化说明 + 表单灰显只读。"""
    out = _run_im(r"""
var d={ok:true,kind:'theme',message:'这是主题配置包，musespiral 目前只支持书源',items:[],skipped:[],counts:{isNew:0,isUpdate:0,latest:0,dupUrls:0}};
if(imIngest(d)!==false)throw new Error('非书源包 imIngest 应返回 false');
if(IM.groups.length)throw new Error('非书源包不应产生行');
imNonSource();
if(els['im-warn'].textContent.indexOf('主题配置包')<0)throw new Error('黄条应呈现后端类型化 message');
if(els['im-warn'].hidden)throw new Error('黄条应显示');
if(els['im-src'].classList._s['is-ro']!==true)throw new Error('表单应灰显只读（is-ro）');
if(els['im-filters'].hidden!==true)throw new Error('过滤/列表区应隐藏');
// 未知类型兜底文案
if(imIngest({ok:true,kind:'unknown',message:'这是无法识别的 legado 配置包',items:[],skipped:[]})!==false)throw new Error('unknown 应拒收');
imNonSource();
if(els['im-total'].textContent.indexOf('无法识别')<0)throw new Error('unknown 应示「无法识别」');
// 书源包正常路径：selected 默认随三态（isNew/isUpdate 预选、latest 不选）
var d2={ok:true,kind:'bookSource',message:'',items:[
 {status:'isNew',selected:true,source:{bookSourceName:'A',bookSourceGroup:'g',bookSourceUrl:'https://a'},local:null},
 {status:'latest',selected:false,source:{bookSourceName:'B',bookSourceGroup:'g',bookSourceUrl:'https://b'},local:null}],
 skipped:[],counts:{isNew:1,isUpdate:0,latest:1,dupUrls:0}};
if(imIngest(d2)!==true)throw new Error('书源包应收下');
if(!IM.sel['https://a']||IM.sel['https://b'])throw new Error('默认选中应随 selected（latest 不预选）');
console.log(JSON.stringify({ok:1}));
""")


def test_im_summary_and_apply_request_shape():
    """D7 汇总 + apply 请求形状（selections 按 URL + confirm + importKeep）。"""
    out = _run_im(r"""
var d={ok:true,kind:'bookSource',message:'',items:[
 {status:'isNew',selected:true,source:{bookSourceName:'A',bookSourceGroup:'g',bookSourceUrl:'https://a'},local:null},
 {status:'latest',selected:false,source:{bookSourceName:'B',bookSourceGroup:'g',bookSourceUrl:'https://b'},local:null}],
 skipped:[],counts:{isNew:1,isUpdate:0,latest:1,dupUrls:0}};
if(imIngest(d)!==true)throw new Error('书源包应收下');
IM.text='[{"x":1}]';
imRepaint();
imSummary();
if(els['im-sum'].textContent!=='已选 1 条 · 新增 1 · 更新 0')throw new Error('汇总应「已选 1 条 · 新增 1 · 更新 0」，实际 '+els['im-sum'].textContent);
// apply 组装（与 imApply 生产代码同一形状：selections 按 URL + confirm + keep 三键）
var urls=[],i,g;for(i=0;i<IM.groups.length;i++){g=IM.groups[i];if(IM.sel[g.url])urls.push(g.url);}
var body=Object.assign({confirm:true,text_or_url:IM.text,selections:urls.map(function(u){return {bookSourceUrl:u,selected:true};})},{keep_name:true,keep_group:false,keep_enable:false});
console.log(JSON.stringify({urls:urls,body:body}));
""")
    assert out["urls"] == ["https://a"]
    assert out["body"]["confirm"] is True
    assert out["body"]["selections"] == [{"bookSourceUrl": "https://a", "selected": True}]


# ══ b) 结构断言（构建产物锚）════════════════════════════════


def test_modal_markup_three_stage_anchors() -> None:
    """三段式弹窗标记：入口 tabs + 过滤/列表（D1 容器）+ 底部汇总。"""
    src = _shell()
    assert 'id="im"' in src and 'modal im-modal' in src  # modal overlay（非 drawer）
    assert 'id="im-open"' in src and "导入书源" in src  # 订阅态入口
    assert 'data-imtab="paste"' in src and 'data-imtab="url"' in src and 'data-imtab="file"' in src
    assert 'accept=".json,application/json"' in src  # 文件入口 accept 约束
    assert 'id="im-text"' in src and 'id="im-url-in"' in src and 'id="im-file-in"' in src
    assert 'id="im-kw"' in src and 'id="im-stat"' in src and 'id="im-groups"' in src
    assert 'id="im-selall"' in src and 'id="im-selall-t"' in src  # D3 全选(当前筛选)
    assert 'id="im-list"' in src and 'id="im-canvas"' in src  # D1 虚拟滚动容器
    assert 'id="im-keep-name"' in src and 'id="im-keep-group"' in src and 'id="im-keep-enable"' in src
    assert 'id="im-sum"' in src and 'id="im-apply"' in src  # D7 汇总+导入钮
    assert 'id="im-warn"' in src  # D8 黄条
    assert 'id="im-close"' in src and 'id="im-cancel"' in src


def test_js_behavior_literals() -> None:
    """core.js 行为字面量：D1 常量/D3 标签/D5 合并标注/D8 分支/API 请求体。"""
    js = _frontend_js()
    assert "IM_ROW=30" in js and "IM_VIEW=400" in js and "IM_BUF=5" in js  # D1 参数同源
    assert "function imWindow(" in js and "function imGroups(" in js
    assert "'全选（当前筛选 '+n+' 条）'" in js  # D3 语义明示
    assert "版本取最新" in js and "data-imexp" in js  # D5 合并+展开
    assert "data-imstat" in js and "data-imgrp" in js and "data-imurl" in js
    # D4 三色徽：badge 前缀 + 三态类名（模板串接，非整词出现）
    assert "'<span class=\"badge '+(s==='isNew'?'success':s==='isUpdate'?'warning':'queued')+'\">" in js
    assert "is-skip" in js and "已跳过" in js  # D4 跳过行灰显+徽
    assert "if(!imIngest(d)){imNonSource();imSummary();return;}" in js  # D8 解析分支
    assert "if(d.kind&&d.kind!=='bookSource'){imIngest(d);imNonSource();toast(IM.msg,WN);return;}" in js  # D8 apply 分支
    assert "function imNonSource(" in js and "is-ro" in js  # D8 黄条+灰显
    assert "postJSON('/api/booksource/import',Object.assign({confirm:true,text_or_url:text},imKeep()))" in js
    assert "postJSON('/api/booksource/import_apply',Object.assign({confirm:true,text_or_url:IM.text,selections:" in js
    assert "keep_name:" in js and "keep_group:" in js and "keep_enable:" in js  # importKeep 三键
    assert "function imSelAll(" in js  # D3 生产函数（node 测试执行面）


def test_css_virtual_list_anchors() -> None:
    """core.css 锚：弹窗宽幅/400px 虚拟滚动窗/30px 行高/灰显只读。"""
    css = CORE_CSS.read_text(encoding="utf-8")
    assert ".im-modal{width:min(760px,100%)" in css
    assert ".im-list{position:relative;height:400px;overflow-y:auto" in css  # D1 容器
    assert ".im-canvas{position:relative" in css
    assert ".im-row{position:absolute" in css and "height:30px" in css  # 与 IM_ROW 同源
    assert ".im-row.is-skip{" in css  # D4 灰显
    assert ".im-src.is-ro{" in css  # D8 只读
    assert ".im-foot{" in css  # D7 底部汇总条


# ══ c) API 回环断言（真实服务器：前端发出的请求形状逐一到端）══


def test_import_preview_loopback_booksource_and_theme(server) -> None:
    """POST import：confirm 闸 400 / 书源包三态+skipped+dupUrls / 非书源包 kind+message。"""
    pkg = json.dumps([
        {"bookSourceName": "A", "bookSourceGroup": "玄幻", "bookSourceUrl": "https://a.com"},
        {"bookSourceName": "A-镜像", "bookSourceGroup": "玄幻", "bookSourceUrl": "https://a.com"},  # 重复 URL
        {"bookSourceName": "B", "bookSourceGroup": "都市", "bookSourceUrl": "https://b.com"},
        {"bookSourceName": "脏源"},  # 缺 URL → skipped
    ], ensure_ascii=False)
    st, d = _req(server, "POST", "/api/booksource/import",
                 {"text_or_url": pkg})  # 无 confirm → 闸门
    assert st == 400, "缺 confirm 必须 400（人审闸门）"
    st, d = _req(server, "POST", "/api/booksource/import", {"confirm": True, "text_or_url": pkg})
    assert st == 200 and d["ok"] is True and d["kind"] == "bookSource"
    assert len(d["items"]) == 3
    assert d["counts"]["dupUrls"] == 1, "同 URL 两条应计 1 重复"
    assert len(d["skipped"]) == 1 and "bookSourceUrl" in d["skipped"][0]["reason"]
    assert {i["status"] for i in d["items"]} == {"isNew"}
    # D8 契约：主题包 items 空 + 类型化 message + ok 仍 True
    theme = json.dumps([{"themeName": "墨水", "accentColor": "#111"}], ensure_ascii=False)
    st, d = _req(server, "POST", "/api/booksource/import", {"confirm": True, "text_or_url": theme})
    assert st == 200 and d["ok"] is True and d["kind"] == "theme"
    assert d["items"] == [] and "只支持书源" in d["message"]


def test_import_apply_loopback_selections_and_dedup(server) -> None:
    """POST import_apply：selections 按 URL 落库 + stored/deduped_from 透明 → GET 见新源。"""
    pkg = json.dumps([
        {"bookSourceName": "A", "bookSourceGroup": "玄幻", "bookSourceUrl": "https://a.com"},
        {"bookSourceName": "A-镜像", "bookSourceGroup": "玄幻", "bookSourceUrl": "https://a.com"},
        {"bookSourceName": "B", "bookSourceGroup": "都市", "bookSourceUrl": "https://b.com"},
    ], ensure_ascii=False)
    body = {
        "confirm": True,
        "text_or_url": pkg,
        "selections": [
            {"bookSourceUrl": "https://a.com", "selected": True},
            {"bookSourceUrl": "https://b.com", "selected": False},
        ],
        "keep_name": False, "keep_group": False, "keep_enable": False,
    }
    st, d = _req(server, "POST", "/api/booksource/import_apply", body)
    assert st == 200 and d["ok"] is True and d["kind"] == "bookSource"
    # 后端口径：deduped_from=落库前被选中的条目数（a.com 两条重复都算选择命中），
    # stored=按 URL 去重后实际落库数——「选择 2 条 · 去重后落库 1」透明计数
    assert d["deduped_from"] == 2
    assert d["stored"] == 1 and d["imported"] == ["https://a.com"]
    assert d["skipped"] == ["https://b.com"]
    # apply 后书源视图面刷新的数据源（GET /api/booksource）可见新源
    st, d = _req(server, "GET", "/api/booksource")
    assert st == 200
    urls = [s["bookSourceUrl"] for s in d["sources"]]
    assert "https://a.com" in urls and "https://b.com" not in urls
