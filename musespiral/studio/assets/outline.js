/*! G-T2 大纲模式前端（正典=《musespiral-调研吸收功能设计-2026-11-18》§三）。
 * 独立懒加载文件（同 quickstart.js/editor.js 模式）：core.js 距 M3 拆分触发线
 * 仅 241B，新功能不再塞 core.js。本文件零 ui.js 闭包依赖——slug 从 hash
 * (#compose?s=) 自取，会话详情独立 GET /api/compose/session，动作走
 * POST /api/compose/outline（confirm 闸门）。展开后派发 hashchange 让 ui.js
 * 重拉块/轮次（跨脚本唯一接缝，无共享 state）。
 */
(function(){
'use strict';

function olEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function olGet(id){return document.getElementById(id);}
function olSlug(){var m=/[?&]s=([^&]+)/.exec(location.hash||'');return m?decodeURIComponent(m[1]):'';}
function olFetch(path,opts){return fetch(path,opts).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));return d;});});}
function olPost(path,body){return olFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
function olDetail(slug){return olFetch('/api/compose/session?slug='+encodeURIComponent(slug));}

function olBadge(st){return st==='expanded'?'<span class="badge done">已展开</span>':
(st==='approved'?'<span class="badge pass">已裁决</span>':'<span class="badge queued">待裁决</span>');}

function olRender(secs,sealed){
var list=olGet('cm-outline-list'),note=olGet('cm-outline-note'),gen=olGet('cm-outline-gen'),exp=olGet('cm-outline-expand');
if(!list)return;
olState.secs=secs;olState.sealed=!!sealed;
var approved=secs.filter(function(s){return s.status==='approved';}).length;
var expanded=secs.filter(function(s){return s.status==='expanded';}).length;
if(gen)gen.disabled=!!sealed;
if(exp)exp.disabled=!!sealed||!approved;
if(note)note.textContent=secs.length
?(sealed?'已采纳封版——大纲只读。':'已裁决 '+approved+'/'+secs.length+' 节'+(expanded?' · 已展开 '+expanded+' 节':'')+' · 勾选认可方向后展开 · 拖手柄或用 ↑↓ 调序')
:'无大纲——点「AI 生成大纲」先出骨架，再逐节勾选裁决后展开。';
var drag=!sealed;
list.innerHTML=secs.map(function(s,i){
var st=s.status||'pending',off=(st==='expanded'||sealed)?' disabled':'';
// 拖拽手柄（FEAT-P1B）：行内独立手柄，不整行 draggable——避免与行内按钮/文本选择打架。
// 手柄键盘可达：聚焦后 ↑↓ 移动本节（拖拽不是唯一路径）。
return '<div class="cm-outline-row" data-ol-row="'+olEsc(s.id)+'" data-ol-idx="'+i+'"'+(drag?'':' data-ol-locked="1"')+'>'
+'<div class="cm-outline-head">'
+(drag?'<button class="ol-handle" type="button" role="button" draggable="true" data-ol-handle="'+olEsc(s.id)+'" aria-label="拖拽排序 '+olEsc(s.title)+'（聚焦后 ↑↓ 调序）"><span aria-hidden="true">⠿</span></button>':'')
+'<label class="cm-sec"><input type="checkbox" data-ol-approve="'+olEsc(s.id)+'"'
+(st!=='pending'?' checked':'')+off+'>'+olEsc(s.title)+'</label>'+olBadge(st)+'</div>'
+'<div class="cm-outline-fields"><input class="input" data-ol-dir="'+olEsc(s.id)+'" value="'+olEsc(s.direction||'')+'" placeholder="方向备注（喂本节展开 prompt）"'+off+'>'
+'<input class="input" data-ol-chars="'+olEsc(s.id)+'" value="'+olEsc(s.target_chars||0)+'" title="目标字数" style="width:64px"'+off+'></div>'
+'<button class="btn" data-ol-expand="'+olEsc(s.id)+'"'+((st!=='approved')||sealed?' disabled':'')+'>展开本节</button></div>';
}).join('');
}

var olState={secs:[],sealed:false};

// FEAT-P1B 拖拽重排（HTML5 D&D 零依赖）：仅手柄可拖（整行 draggable 会打架），
// dragover 指示线+占位条，drop 调既有 action=replace 持久化；仅可编辑态绑定。
function olMove(fromId,toIdx){
var secs=olState.secs.slice(),i=-1,k;
for(k=0;k<secs.length;k++)if(secs[k].id===fromId){i=k;break;}
if(i<0)return null;
if(toIdx<0)toIdx=0;if(toIdx>secs.length)toIdx=secs.length;
// 插入位按移除前的数组算：源在插入位之前 → 移除后整体前移一位
var eff=toIdx>i?toIdx-1:toIdx;
if(eff===i)return null;
var moved=secs.splice(i,1)[0];
secs.splice(eff,0,moved);
return secs;
}
function olClearMarks(list){
if(!list)return;
var rows=list.querySelectorAll('.cm-outline-row');
for(var i=0;i<rows.length;i++){rows[i].classList.remove('ol-drop-before','ol-drop-after','ol-drag-src');}
var ph=list.querySelector('.ol-placeholder');if(ph)ph.parentNode.removeChild(ph);
}
function olCommit(secs,focusId){
var slug=olSlug();
if(!slug)return Promise.resolve();
return olPost('/api/compose/outline',{slug:slug,action:'replace',sections:secs,confirm:true})
.then(function(){return olRefresh();})
.then(function(){if(focusId)olFocus(focusId);})
.catch(function(e){var n=olGet('cm-outline-note');if(n)n.textContent='大纲调序失败：'+e.message;olRefresh();});
}
// 键盘重排后焦点还给同一节手柄（重绘丢焦点；键盘用户需连续按 ↑↓）
function olFocus(id){
var list=olGet('cm-outline-list'),h=list&&list.querySelector('[data-ol-handle="'+id+'"]');
if(h)h.focus({preventScroll:true});
}
var olDnd={fromId:null,toIdx:null};
function olRowOf(el){return el&&el.closest?el.closest('[data-ol-row]'):null;}
var olBindDnd={
start:function(e){
var h=e.target.closest&&e.target.closest('[data-ol-handle]');
if(!h){e.preventDefault();return;}
olDnd.fromId=h.getAttribute('data-ol-handle');
if(e.dataTransfer){e.dataTransfer.effectAllowed='move';try{e.dataTransfer.setData('text/plain',olDnd.fromId);}catch(_){/**/}}
var row=olRowOf(h);if(row)row.classList.add('ol-drag-src');
},
end:function(){olDnd.fromId=null;olDnd.toIdx=null;olClearMarks(olGet('cm-outline-list'));},
over:function(e){
if(olDnd.fromId==null)return;
e.preventDefault();
if(e.dataTransfer)e.dataTransfer.dropEffect='move';
var row=olRowOf(e.target);if(!row||!row.getAttribute('data-ol-idx'))return;
var rows=this.querySelectorAll('.cm-outline-row');
for(var i=0;i<rows.length;i++)rows[i].classList.remove('ol-drop-before','ol-drop-after');
var r=row.getBoundingClientRect(),after=e.clientY>r.top+r.height/2,base=+(row.getAttribute('data-ol-idx'));
row.classList.add(after?'ol-drop-after':'ol-drop-before');
olDnd.toIdx=after?base+1:base;
var ph=this.querySelector('.ol-placeholder');
if(!ph){ph=document.createElement('div');ph.className='ol-placeholder';}
if(after)row.parentNode.insertBefore(ph,row.nextSibling);
else row.parentNode.insertBefore(ph,row);
},
leave:function(e){
if(this.contains(e.relatedTarget))return;
var rows=this.querySelectorAll('.cm-outline-row');
for(var i=0;i<rows.length;i++)rows[i].classList.remove('ol-drop-before','ol-drop-after');
},
drop:function(e){
if(olDnd.fromId==null)return;
e.preventDefault();
var idx=olDnd.toIdx;olClearMarks(this);
var secs=idx==null?null:olMove(olDnd.fromId,idx);
olDnd.fromId=null;olDnd.toIdx=null;
if(secs)olCommit(secs);
}
};

async function olRefresh(){
var slug=olSlug();
if(!slug)return;
try{var d=await olDetail(slug);
olState.secs=(d.outline&&d.outline.sections)||[];
olState.sealed=d.status!=='composing';
olRender(olState.secs,olState.sealed);
}catch(e){/* 详情加载失败静默——主 UI 已自行 toast 同类错误 */}
}

async function olAction(action,section,extra){
var slug=olSlug();
if(!slug)return;
var body={slug:slug,action:action,confirm:true};
if(section)body.section=section;
if(extra)Object.assign(body,extra);
try{await olPost('/api/compose/outline',body);
await olRefresh();
if(action==='expand')window.dispatchEvent(new Event('hashchange')); // 让 ui.js 重拉块/轮次
}catch(e){var n=olGet('cm-outline-note');if(n)n.textContent='大纲操作失败：'+e.message;}
}

// ARCH-1 幂等门：#cm-outline 骨架随 compose 视图懒注入——core 装载本文件时 DOM
// 多半未到（早退不绑 hashchange），__onViewHtml('compose') 会再调 boot()；bound
// 防双绑（重复 hashchange/click 监听=双请求）。
var olBound=false;
function olBoot(){
if(olBound)return;
if(!olGet('cm-outline'))return;
olBound=true;
var gen=olGet('cm-outline-gen'),exp=olGet('cm-outline-expand'),list=olGet('cm-outline-list');
if(gen)gen.addEventListener('click',function(){olAction('generate');});
if(exp)exp.addEventListener('click',async function(){
var ap=olState.secs.filter(function(s){return s.status==='approved';});
if(!ap.length)return;
this.disabled=true;
var i;
try{for(i=0;i<ap.length;i++)await olAction('expand',ap[i].id);}
finally{this.disabled=false;}});
if(list){
list.addEventListener('change',function(e){
var t=e.target,id=t.getAttribute&&(t.getAttribute('data-ol-approve')||t.getAttribute('data-ol-dir')||t.getAttribute('data-ol-chars'));
if(!id)return;
if(t.getAttribute('data-ol-approve'))olAction('approve',id,{approved:t.checked});
else{var row=t.closest('[data-ol-row]');
olAction('update',id,{direction:row.querySelector('[data-ol-dir]').value.trim(),target_chars:+row.querySelector('[data-ol-chars]').value||0});}});
list.addEventListener('click',function(e){
var b=e.target.closest('[data-ol-expand]');if(b)olAction('expand',b.getAttribute('data-ol-expand'));});
// FEAT-P1B 键盘重排（手柄聚焦后 ↑↓ 移动本节——拖拽不是唯一路径）：Alt 可选修饰，
// 无修饰亦可；移动即 replace 持久化。仅在可编辑态生效（sealed 时行内无手柄）。
list.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
var h=e.target.closest&&e.target.closest('[data-ol-handle]');
if(!h)return;
if(e.key!=='ArrowUp'&&e.key!=='ArrowDown')return;
e.preventDefault();
var rowId=h.getAttribute('data-ol-handle'),row=h.closest('[data-ol-row]'),idx=+(row.getAttribute('data-ol-idx'));
var to=e.key==='ArrowDown'?idx+1:idx-1;
if(to<0||to>=olState.secs.length)return;
var secs=olMove(rowId,to);
if(!secs)return;
// 乐观重绘：先画新序再落盘，失败由 olRefresh 兜底
olState.secs=secs;olRender(secs,olState.sealed);
olCommit(secs,rowId);
});
list.addEventListener('dragstart',olBindDnd.start);
list.addEventListener('dragend',olBindDnd.end);
list.addEventListener('dragover',olBindDnd.over);
list.addEventListener('dragleave',olBindDnd.leave);
list.addEventListener('drop',olBindDnd.drop);
}
window.addEventListener('hashchange',function(){olRefresh();});
olRefresh();
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',olBoot);
else olBoot();
window.__mdpubOutline={boot:olBoot,move:olMove,setState:function(x){olState.secs=x;},commit:olCommit};
})();
