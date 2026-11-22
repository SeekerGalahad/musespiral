"""FEAT-P1B 大纲拖拽排序（前端排序函数 node 真实执行 + 结构锚）。

覆盖：
  - olMove 纯逻辑真跑：A,B,C 拖 A 到末尾 → [B,C,A]；C 拖到最前 → [C,A,B]；
    同位零移动 → null
  - olCommit 落盘：调既有 /api/compose/outline action=replace，sections 为排序后顺序
  - 结构：手柄（data-ol-handle + aria-label）随行渲染、键盘可达、可编辑态才有手柄；
    拖拽指示线/占位条类名存在
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

OUTLINE_JS = (
    Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "outline.js"
)

# 最小 DOM/网络桩：$() 惰性造元素；fetch 记录 POST 到 _posts 并回 ok。
_HARNESS = r"""'use strict';
var _els={},_posts=[];
function _mk(id){return {id:id,hidden:true,textContent:'',innerHTML:'',style:{},value:'',checked:false,disabled:false,
classList:{_s:{},add:function(c){this._s[c]=1;},remove:function(c){delete this._s[c];},
toggle:function(c,f){if(f)this._s[c]=1;else delete this._s[c];},contains:function(c){return !!this._s[c];}},
querySelector:function(){return null;},querySelectorAll:function(){return [];},
addEventListener:function(){},focus:function(){},closest:function(){return null;},getBoundingClientRect:function(){return{top:0,left:0,height:10,width:10};}};}
function getElementById(id){if(!_els[id])_els[id]=_mk(id);return _els[id];}
global.document={readyState:'complete',getElementById:getElementById,
addEventListener:function(){},createElement:function(){return _mk('e');},querySelectorAll:function(){return [];}};
global.window={addEventListener:function(){},dispatchEvent:function(){}};
global.location={hash:'#compose?s=demo'};
global.fetch=function(url,opts){_posts.push({url:url,body:JSON.parse(opts.body)});
return Promise.resolve({ok:true,json:function(){return Promise.resolve({ok:true,status:'composing',outline:{sections:[],current_expanding:null}});}});};
global.Event=function(n){this.type=n;};
"""


def _run(body: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——排序函数需真实执行（结构断言仍随全量跑）")
    src = OUTLINE_JS.read_text(encoding="utf-8")
    script = _HARNESS + "\n" + src + "\n" + body
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "dnd.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 执行失败:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


_ABC = "var S=[{id:'A',title:'A',status:'pending'},{id:'B',title:'B',status:'pending'},{id:'C',title:'C',status:'pending'}];"


def test_drag_a_to_end_yields_bca() -> None:
    out = _run(_ABC + """
var O=window.__mdpubOutline;O.setState(S);
var moved=O.move('A',3);
if(!moved)throw new Error('move 返回 null');
if(JSON.stringify(moved.map(function(s){return s.id;}))!=='["B","C","A"]')throw new Error('排序错: '+JSON.stringify(moved.map(function(s){return s.id;})));
console.log('ok');""")
    assert out.endswith("ok")


def test_drag_c_to_front_yields_cab() -> None:
    out = _run(_ABC + """
var O=window.__mdpubOutline;O.setState(S);
var moved=O.move('C',0);
if(JSON.stringify(moved.map(function(s){return s.id;}))!=='["C","A","B"]')throw new Error('排序错');
console.log('ok');""")
    assert out.endswith("ok")


def test_noop_move_returns_null() -> None:
    out = _run(_ABC + """
var O=window.__mdpubOutline;O.setState(S);
if(O.move('B',1)!==null)throw new Error('同位应为 null');
if(O.move('A',0)!==null)throw new Error('首行上前应为 null');
console.log('ok');""")
    assert out.endswith("ok")


def test_commit_posts_replace_with_sorted_order() -> None:
    """落盘：调既有 action=replace，body.sections 为排序后的顺序（真测到持久化路径）。"""
    out = _run(_ABC + """
var O=window.__mdpubOutline;O.setState(S);
var moved=O.move('A',3);O.commit(moved);
setTimeout(function(){
var p=_posts[_posts.length-1];
if(!p||p.url.indexOf('/api/compose/outline')<0)throw new Error('未调 outline 端点');
if(p.body.action!=='replace')throw new Error('action 非 replace: '+p.body.action);
if(p.body.slug!=='demo')throw new Error('slug 错');
if(JSON.stringify(p.body.sections.map(function(s){return s.id;}))!=='["B","C","A"]')throw new Error('持久化顺序错');
console.log('ok');},30);""")
    assert out.endswith("ok")


def test_structural_anchors() -> None:
    """结构锚：手柄 data-ol-handle + aria-label、键盘 ↑↓、指示线/占位条、可编辑态才手柄。"""
    src = OUTLINE_JS.read_text(encoding="utf-8")
    assert "data-ol-handle" in src
    assert 'aria-label="拖拽排序' in src  # 手柄显式 aria
    assert "'ArrowUp'" in src and "'ArrowDown'" in src  # 键盘重排路径
    assert "ol-drop-before" in src and "ol-drop-after" in src  # 目标位置指示线
    assert "ol-placeholder" in src  # 拖拽占位条
    assert "draggable=\"true\"" in src  # 手柄原生 draggable
    # 可编辑态门：sealed 时行内无手柄（drag=!sealed）
    assert "var drag=!sealed;" in src
