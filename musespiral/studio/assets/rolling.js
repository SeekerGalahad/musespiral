/*! 滚动摘要面板（G-T4，正典=《musespiral-调研吸收功能设计-2026-11-18》§五）。
 * 创作工坊右栏「📖 滚动摘要」折叠区：只读 + 手动刷新 + 用户手改 textarea。
 *
 * 装载契约（ui.js 仅一行动态注入本文件，其余全部自管理——同 quickstart.js：
 * core.js 已抵 200KiB 触发线，新功能一律独立产物不占 core.js）：
 *   - slug 自取 hash（#compose?s=），不依赖 ui.js 的 state 闭包；
 *   - 重载时机：hashchange + #cm-rounds childList 变化（renderCompose 每次
 *     重建它——generate/decide/adopt 后自动跟上，含 adopt 后异步摘要落盘
 *     那一次）+ 自身动作完成；
 *   - 只消费两形态：GET /api/compose/session（详情三字段）与
 *     POST /api/compose/rolling-summary（无 summary=刷新；有=手改）。
 * 摘要注入 prompt 的唯一事实源在后端（compose_service.build_prompt 的
 * {{rolling}} 插槽）——本文件只管展示与编辑，绝不拼 prompt。
 */
(function(){
'use strict';
var R={busy:false};
function el(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function slug(){
if(!/^#compose(\?|$)/.test(location.hash))return '';
var kv=String(location.hash).split('?')[1]||'';
var hit=kv.split('&').filter(function(p){return p.slice(0,2)==='s=';})[0];
return hit?decodeURIComponent(hit.slice(2)):'';}
function req(path,opt){
return fetch(path,opt).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&d.error)||('HTTP '+r.status));
return d;});});}
function paint(d){
var host=el('cm-rolling-view');
if(!host)return;
// 编辑/保存后恢复只读骨架：edit() 把 view 内容换成 textarea，paint 必须
// 能重建展示结构（浏览器实测抓到的真缺陷——否则保存后停在编辑态）。
if(!el('cm-rolling-text')){
host.innerHTML='<p class="action-hint" id="cm-rolling-text"></p>';
}
var t=el('cm-rolling-text'),n=el('cm-rolling-note');
var s=String((d&&d.rolling_summary)||'').trim();
t.textContent=s||'暂无摘要——正稿字数跨过 1500 字后自动生成（入稿即触发），也可点刷新手动生成。';
t.style.whiteSpace='pre-wrap';
if(n){var c=+(d&&d.rolling_summary_chars)||0,next=((+(d&&d.rolling_summary_level)||0)+1)*1500;
n.textContent=c?('已写 '+c+' 字 · 距下次自动重算 '+(next-c>0?next-c:0)+' 字'):'';}}
async function load(){
var s=slug();
if(!s){paint(null);return;}
try{paint(await req('/api/compose/session?slug='+encodeURIComponent(s)));}catch(e){}}
async function refresh(btn){
if(R.busy)return;
var s=slug();
if(!s){alert('先建会话再生成摘要');return;}
R.busy=true;if(btn)btn.disabled=true;
try{await req('/api/compose/rolling-summary',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:s,confirm:true})});
await load();}
catch(e){alert('摘要刷新失败：'+e.message);}
finally{R.busy=false;if(btn)btn.disabled=false;}}
function edit(){
var h=el('cm-rolling-view');
if(!h)return;
var cur=h.textContent.trim();
h.innerHTML='<textarea class="input set-area" id="cm-rolling-area" rows="4"></textarea>'+
'<div class="action-row"><button class="btn btn-primary" id="cm-rolling-save">保存</button>'+
'<button class="btn" id="cm-rolling-cancel">取消</button></div>';
var a=el('cm-rolling-area');
a.value=cur.indexOf('暂无摘要')===0?'':cur;
a.focus();a.select();
el('cm-rolling-cancel').onclick=load;
el('cm-rolling-save').onclick=function(){save(a.value,this);};}
async function save(text,btn){
var s=slug();
if(!s)return;
if(btn)btn.disabled=true;
try{await req('/api/compose/rolling-summary',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({slug:s,summary:String(text||''),confirm:true})});
await load();}
catch(e){alert('摘要保存失败：'+e.message);}
finally{if(btn)btn.disabled=false;}}
var rsBound=false; // ARCH-1 幂等门（骨架懒注入后 __onViewHtml 重调 boot；防双绑）
function boot(){
if(rsBound)return;
if(!el('cm-rolling'))return;
rsBound=true;
el('cm-rolling-refresh').addEventListener('click',function(){refresh(this);});
el('cm-rolling-edit').addEventListener('click',edit);
window.addEventListener('hashchange',load);
var r=el('cm-rounds');
if(r&&window.MutationObserver)new MutationObserver(load).observe(r,{childList:true});
load();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
else boot();
window.__mdpubRolling={slug:slug,esc:esc,paint:paint,boot:boot};
})();
