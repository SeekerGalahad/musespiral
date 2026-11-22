/*! 版本历史面板（E-6，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.7）。
 * 文章详情「版本历史」tab：版本下拉×2 + 对比 → 服务端渲染的 diff（红删绿增）
 * + 按版恢复（先应用内 askConfirm 弹层，再 POST /api/article/restore）。
 * 独立产物（core.js 已贴 M3 200KiB 线，同 quickstart/outline/rolling/dual/tts）：
 * ui.js 的 setTab 进 versions tab 时调 window.__mdpubVersions.mount()；本文件自管理
 * DOM 与状态，文章路径自取 hash（#article?path=），不依赖 ui.js 的 state 闭包。
 * 两态一个端点：GET /api/article/diff（无 v1/v2 → 清单；有 → 清单+diff）。
 * diff 算法只在服务端一份（core/version/diff.py），前端只贴 html。
 * 零外联：只用相对路径 /api/article/diff 与 /api/article/restore。
 */
(function(){
'use strict';
/*__VER_BEGIN__*/
// 纯函数区（tests 的 node 行为断言抽取本段执行，不触 DOM）
function verPath(hash){
var raw=String(hash||'');
if(!/^#article(\?|$)/.test(raw))return '';
var q=raw.indexOf('?');if(q<0)return '';
var ps=raw.slice(q+1).split('&'),i,kv;
for(i=0;i<ps.length;i++){
kv=ps[i].split('=');
if(kv[0]==='path'&&kv.length>1){try{return decodeURIComponent(kv.slice(1).join('='));}catch(e){return '';}}}
return '';
}
function verEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
// 下拉项：当前版 → 「当前版」；池内版 → 「标签 · 首行摘要」
function verOptions(versions){
var list=Array.isArray(versions)?versions:[];
if(!list.length)return '<option value="">（版本池为空）</option>';
return list.map(function(v){
var label=v.current?'当前版':String(v.label||v.id||''),sum=String(v.summary||'').trim();
if(!v.current&&sum)label+=' · '+sum;
return '<option value="'+verEsc(v.id)+'">'+verEsc(label)+'</option>';}).join('');
}
// 缺省对：旧版=池内最旧一版（无池则当前版），新版=当前版
function verDefaultPairs(versions){
var list=Array.isArray(versions)?versions:[];
var cur=list.filter(function(v){return v.current;})[0]||null;
var pool=list.filter(function(v){return !v.current;});
var old=pool.length?pool[pool.length-1]:cur;
return {a:old?String(old.id):'',b:cur?String(cur.id):''};
}
function verRestoreLabel(v){return v?('恢复到 '+String(v.label||v.id||'')+'（覆盖当前版）'):'恢复到该版';}
/*__VER_END__*/
function VEl(id){return document.getElementById(id);}
function VReq(p,o){return fetch(p,o).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));
return d;});});}
var V={busy:false,versions:[],loaded:false,paths:''};
var VHINT='版本池由每次保存自动留档（滚动保留 5 份，MUSESPIRAL_BAK_KEEP 可调）。选两版对比；diff 底部可按版恢复（恢复可再恢复）。';
function vNote(t){var n=VEl('ver-note');if(n)n.textContent=t||'';}
function vStage(h){var s=VEl('ver-stage');if(s)s.innerHTML=h||'';}
function vPaintSel(){
var a=VEl('ver-a'),b=VEl('ver-b');if(!a||!b)return;
var o=verOptions(V.versions),d=verDefaultPairs(V.versions),ka=a.value,kb=b.value;
a.innerHTML=o;b.innerHTML=o;
a.value=V.versions.some(function(v){return String(v.id)===ka;})?ka:d.a;
b.value=V.versions.some(function(v){return String(v.id)===kb;})?kb:d.b;
}
function vPaintRestore(pick){
var host=VEl('ver-restore-host');if(!host)return;
if(!pick||pick.current){host.innerHTML='';return;}
host.innerHTML='<button class="btn" id="ver-restore-btn">'+verEsc(verRestoreLabel(pick))+'</button>';
VEl('ver-restore-btn').addEventListener('click',function(){vRestore(pick,this);});
}
async function vLoad(){
var path=verPath(location.hash);V.paths=path;
if(!path)return;
try{
var d=await VReq('/api/article/diff?path='+encodeURIComponent(path));
V.versions=Array.isArray(d.versions)?d.versions:[];V.loaded=true;
vPaintSel();
vNote(V.versions.length<=1?'还没有历史版本——每次保存正文时旧版才会入池。'
:'版本池 '+(V.versions.length-1)+' 份（新→旧，旧单版本恒排尾部）。'+VHINT);
}catch(e){vNote('读取版本池失败：'+e.message);}
}
async function vDiff(btn){
if(V.busy)return;
var path=verPath(location.hash);if(!path){vNote('先打开一篇文章');return;}
var a=(VEl('ver-a')||{}).value,b=(VEl('ver-b')||{}).value;
if(!a||!b){vNote('选择一个版本再对比');return;}
if(a===b){vNote('两个下拉选了同一版本——请选不同的两版');return;}
V.busy=true;if(btn)btn.disabled=true;vStage('<p class="action-hint">对比中…</p>');
try{
var d=await VReq('/api/article/diff?path='+encodeURIComponent(path)+
'&v1='+encodeURIComponent(a)+'&v2='+encodeURIComponent(b)),df=d.diff||{};
vStage((df.html||'<p class="action-hint">无差异</p>')+(df.identical?'':'<div class="ver-restore-host" id="ver-restore-host"></div>'));
if(!df.identical){
vPaintRestore(V.versions.filter(function(v){return String(v.id)===a;})[0]);
vNote('差异：+'+df.added+' 行 / −'+df.removed+' 行（绯红=删除，竹绿=新增）。');
}else vNote('两版内容相同。');
}catch(e){vStage('');vNote('对比失败：'+e.message);}
finally{V.busy=false;if(btn)btn.disabled=false;}
}
async function vRestore(pick,btn){
var path=verPath(location.hash);if(!path||!pick)return;
// fix-6：系统确认框退役，走应用内 askConfirm 弹层（自定义弹层红线）。
var okRestore=await askConfirm({title:'恢复版本',message:'把《'+(pick.label||pick.id)+'》恢复为当前正文？\n当前版会先入版本池（撤销可再恢复）。',confirmText:'恢复'});
if(!okRestore)return;
if(btn)btn.disabled=true;
try{
await VReq('/api/article/restore',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({article:path,version:String(pick.id),confirm:true})});
vNote('已恢复 '+(pick.label||pick.id)+'；当前版已入池。重新载入正文…');
await vLoad();vStage('');
window.dispatchEvent(new Event('hashchange'));
}catch(e){vNote('恢复失败：'+e.message);}
finally{if(btn)btn.disabled=false;}
}
var vBound=false; // ARCH-1 幂等门（#view-article 骨架懒注入后 __onViewHtml 重调 vBoot；防双绑）
function vBoot(){
if(vBound)return;
if(!VEl('panel-versions'))return;
vBound=true;
var btn=VEl('ver-diff-btn');
if(btn)btn.addEventListener('click',function(){vDiff(this);});
// 进本 tab 的判定全部在本文件（core.js 贴 M3 拆分线，setTab 里不再加钩子）：
// ① tab 按钮点击委派；② 深链/后退（hash 带 tab=versions）；③ 首次装载。
var tabs=VEl('detail-tabs');
if(tabs)tabs.addEventListener('click',function(e){
var b=e.target.closest('[data-tab]');
if(b&&b.getAttribute('data-tab')==='versions')vMount();
});
// 切文章即清（旧 diff 属于上一篇文章——不能按 tab 名判断归属）
window.addEventListener('hashchange',function(){
if(verPath(location.hash)===V.paths){vMaybe();return;}
V.loaded=false;V.versions=[];
vStage('');vNote(VHINT);
if(VEl('ver-a'))VEl('ver-a').innerHTML='';
if(VEl('ver-b'))VEl('ver-b').innerHTML='';
vMaybe();
});
vMaybe();
}
function vMaybe(){if(/[?&]tab=versions(?:&|$)/.test(location.hash))vMount();}
// 装载入口：未装载过才拉（重复进入不重复请求）
function vMount(){if(!V.loaded)vLoad();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',vBoot);
else vBoot();
window.__mdpubVersions={verPath:verPath,verOptions:verOptions,verDefaultPairs:verDefaultPairs,
verRestoreLabel:verRestoreLabel,mount:vMount,boot:vBoot};
})();
