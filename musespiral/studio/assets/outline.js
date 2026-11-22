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
var approved=secs.filter(function(s){return s.status==='approved';}).length;
var expanded=secs.filter(function(s){return s.status==='expanded';}).length;
if(gen)gen.disabled=!!sealed;
if(exp)exp.disabled=!!sealed||!approved;
if(note)note.textContent=secs.length
?('已裁决 '+approved+'/'+secs.length+' 节'+(expanded?' · 已展开 '+expanded+' 节':'')+' · 勾选认可方向后展开')
:'无大纲——点「AI 生成大纲」先出骨架，再逐节勾选裁决后展开。';
list.innerHTML=secs.map(function(s){
var st=s.status||'pending',off=(st==='expanded'||sealed)?' disabled':'';
return '<div class="cm-outline-row" data-ol-row="'+olEsc(s.id)+'">'
+'<div class="cm-outline-head"><label class="cm-sec"><input type="checkbox" data-ol-approve="'+olEsc(s.id)+'"'
+(st!=='pending'?' checked':'')+off+'>'+olEsc(s.title)+'</label>'+olBadge(st)+'</div>'
+'<div class="cm-outline-fields"><input class="input" data-ol-dir="'+olEsc(s.id)+'" value="'+olEsc(s.direction||'')+'" placeholder="方向备注（喂本节展开 prompt）"'+off+'>'
+'<input class="input" data-ol-chars="'+olEsc(s.id)+'" value="'+olEsc(s.target_chars||0)+'" title="目标字数" style="width:64px"'+off+'></div>'
+'<button class="btn" data-ol-expand="'+olEsc(s.id)+'"'+((st!=='approved')||sealed?' disabled':'')+'>展开本节</button></div>';
}).join('');
}

var olState={secs:[],sealed:false};

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
}
window.addEventListener('hashchange',function(){olRefresh();});
olRefresh();
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',olBoot);
else olBoot();
window.__mdpubOutline={boot:olBoot};
})();
