/*! 双模型对比面板（G-T3，正典=《musespiral-调研吸收功能设计-2026-11-18》§四）。
 * 创作工坊右栏「⚖️ 对比模式」折叠区：档案 select×2 + 并行生成 + 双栏结果
 * + 选 A / 保留双版 / 选 B。零 ui.js 闭包依赖（同 outline.js/rolling.js：
 * slug 从 hash 自取，档案从 GET /api/settings 读），独立懒加载产物。
 *
 * 端点：POST /api/compose/dual（generate|pick|keep 三形态同一路由，见契约 §3.15）。
 * - generate → 两版原文 + usage（不落 rounds）；
 * - pick → 选定版替换 session 末轮 blocks，派发 hashchange 让 ui.js 重拉块/轮次；
 * - keep → 两版各存 draft_v_a.md / draft_v_b.md。
 */
(function(){
'use strict';

/*__DUAL_BEGIN__*/
// ── 纯函数区（tests 的 node 行为断言抽取本段执行，不触 DOM）──
function dEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function dSlug(){if(!/^#compose(\?|$)/.test(location.hash||''))return '';var m=/[?&]s=([^&]+)/.exec(location.hash||'');return m?decodeURIComponent(m[1]):'';}
// usage 展示：A 2000 tok · B 1800 tok（本仓无 token 计价口径，只显示字符数——诚实）
function dUsage(u){u=u||{};return (u.profile||'')+' · 入 '+(u.chars_in||0)+' 字 · 出 '+(u.chars_out||0)+' 字'+(u.cache_hit?' · 缓存':'');}
function dProfileOptions(profiles,selected){
var names=Object.keys(profiles||{});
if(!names.length)return '<option value="">（无已配置档案）</option>';
return names.map(function(n){return '<option value="'+dEsc(n)+'"'+(n===selected?' selected':'')+'>'+dEsc(n)+'</option>';}).join('');}
/*__DUAL_END__*/

function dEl(id){return document.getElementById(id);}
function dFetch(path,opts){return fetch(path,opts).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));return d;});});}
function dPost(body){return dFetch('/api/compose/dual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({confirm:true},body))});}
var D={busy:false,a:'',b:'',ta:'',tb:''};

function dLoadProfiles(){
return dFetch('/api/settings').then(function(d){
var st=(d&&d.ai&&d.ai.profiles)||{},ps=st.profiles||{},def=st.default||'';
var selA=dEl('cm-dual-a'),selB=dEl('cm-dual-b');
if(!selA||!selB)return;
var names=Object.keys(ps);
selA.innerHTML=dProfileOptions(ps,names[0]||def);
selB.innerHTML=dProfileOptions(ps,names[1]||names[0]||def);
D.a=selA.value;D.b=selB.value;
}).catch(function(){});
}

function dPaintResult(r){
D.ta=r.text_a||'';D.tb=r.text_b||'';
dEl('cm-dual-ha').textContent=(r.usage_a&&r.usage_a.profile)||'A';
dEl('cm-dual-hb').textContent=(r.usage_b&&r.usage_b.profile)||'B';
dEl('cm-dual-ta').textContent=D.ta;
dEl('cm-dual-tb').textContent=D.tb;
dEl('cm-dual-ua').textContent=dUsage(r.usage_a);
dEl('cm-dual-ub').textContent=dUsage(r.usage_b);
dEl('cm-dual-result').hidden=false;
dEl('cm-dual-acts').hidden=false;
dEl('cm-dual-note').textContent='两版已就绪——选一版进裁决流，或两版都保留为草稿。';
}

async function dGenerate(btn){
if(D.busy)return;
var s=dSlug();
if(!s){dEl('cm-dual-note').textContent='先建会话再对比生成';return;}
var pa=(dEl('cm-dual-a')||{}).value,pb=(dEl('cm-dual-b')||{}).value;
if(!pa||!pb){dEl('cm-dual-note').textContent='需要两个已配置档案';return;}
if(pa===pb){dEl('cm-dual-note').textContent='两个档案相同——对比无意义，请选不同档案';return;}
D.busy=true;if(btn)btn.disabled=true;
dEl('cm-dual-note').textContent='并行生成中…';
try{
var r=await dPost({action:'generate',slug:s,profile_a:pa,profile_b:pb});
dPaintResult(r);
}catch(e){dEl('cm-dual-note').textContent='对比失败：'+e.message;}
finally{D.busy=false;if(btn)btn.disabled=false;}
}

async function dPick(pick,btn){
var s=dSlug();if(!s||!D.ta||!D.tb)return;
var text=pick==='a'?D.ta:D.tb;
if(btn)btn.disabled=true;
try{
await dPost({action:'pick',slug:s,text:text,pick:pick});
dEl('cm-dual-note').textContent='已选 '+pick.toUpperCase()+' 版进入裁决流';
window.dispatchEvent(new Event('hashchange')); // 让 ui.js 重拉块/轮次
}catch(e){dEl('cm-dual-note').textContent='采纳失败：'+e.message;}
finally{if(btn)btn.disabled=false;}
}

async function dKeep(btn){
var s=dSlug();if(!s||!D.ta||!D.tb)return;
if(btn)btn.disabled=true;
try{
var r=await dPost({action:'keep',slug:s,text_a:D.ta,text_b:D.tb,profile_a:D.a,profile_b:D.b});
dEl('cm-dual-note').textContent='双版已存为草稿：'+(r.drafts||[]).map(function(p){return p.split('/').pop();}).join('、');
}catch(e){dEl('cm-dual-note').textContent='保存失败：'+e.message;}
finally{if(btn)btn.disabled=false;}
}

function dBoot(){
if(!dEl('cm-dual'))return;
dEl('cm-dual-gen').addEventListener('click',function(){dGenerate(this);});
dEl('cm-dual-pick-a').addEventListener('click',function(){dPick('a',this);});
dEl('cm-dual-pick-b').addEventListener('click',function(){dPick('b',this);});
dEl('cm-dual-keep').addEventListener('click',function(){dKeep(this);});
dLoadProfiles();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',dBoot);
else dBoot();
window.__mdpubDual={dEsc:dEsc,dSlug:dSlug,dUsage:dUsage,dProfileOptions:dProfileOptions};
})();
