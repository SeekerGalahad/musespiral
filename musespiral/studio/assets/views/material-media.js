var gAb=null,gN=0;
function gPaint(){
var g=state.gen,st=g.st||'',b=$('gen-result');
QA('.gen-cfg .chip').forEach(function(c){var k=c.hasAttribute('data-size');
c.classList.toggle('is-active',c.getAttribute(k?'data-size':'data-model')===(k?g.size:g.model))});
$('gen-cancel').hidden=st!=='gen';$('gen-run').disabled=st==='gen';
b.innerHTML=st==='bad'?'<p class="gen-fail">'+esc(g.err)+'</p><button class="btn" id="gen-re">重试</button>':g.pv?'<img alt="" src="'+esc(g.pv.url)+'"><p class="action-hint">'+esc(g.pv.model)+' · '+esc(g.pv.provider)+'</p><button class="btn btn-primary" id="gen-ok">存为素材</button><button class="btn" id="gen-re">重新生成</button>':st==='gen'?'<div class="skel"><div class="skel-line w90"></div></div>':'';
if($('gen-re'))$('gen-re').onclick=gGo;if($('gen-ok'))$('gen-ok').onclick=gAdopt;
}
function gGo(){
var g=state.gen,p=$('gen-prompt').value.trim();
if(!p)return toast('请先填写提示词',WN);
var n=++gN,b={prompt:p,size:g.size,confirm:true};
if(g.model)b.params={model:g.model};
g.st='gen';g.err='';gPaint();gAb=new AbortController();
fetch('/api/action/generate_image',{method:'POST',signal:gAb.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(r){return r.json().then(function(j){
if(j.ok===false)throw Error(j.error||'生成失败');
if(n===gN){g.st='';g.pv=j;gPaint()}})}).catch(function(e){if(n!==gN)return;g.st='bad';g.err=e.name==='AbortError'?'已取消':e.message;gPaint()});
}
function gAdopt(){
var g=state.gen;if(!g.pv)return;
postJSON('/api/action/adopt_generated',{preview_id:g.pv.preview_id,title:$('gen-prompt').value.trim().slice(0,40),confirm:true}).then(function(){
toast('已存为素材（默认不进推荐）',OK);g.st='';g.pv=null;gPaint();return loadMaterials().then(renderMaterials)}).catch(function(e){toast('采纳失败：'+e.message,ER)});
}
function selSync(){var n=state.msel.length,ok=n>1&&n<9,b=$('syn-btn');$('sel-bar').hidden=!n;
$('sel-n').textContent=n;b.disabled=!ok;b.title=ok?'':'需选 2-8 篇有正文面的素材'}
function selToggle(id){if(state.syn.busy)return;var i=state.msel.indexOf(id);
i<0?state.msel.push(id):state.msel.splice(i,1);selSync();
try{if(!localStorage.mdpubSynTip){localStorage.mdpubSynTip=1;toast('已进入多选，选中 2-8 篇后点 AI 综合',OK)}}catch(e){}}
function synSteps(r){var S=(r||{}).steps||[],u=(r||{}).budget||{},mx=((r||{}).limits||{}).steps||'—',h=S.length?'':'<p class="action-hint">等待步级进度…</p>';
S.forEach(function(x,i){var f=x.status||'';
h+='<div class="syn-step'+(f==='done'?' done':f==='running'?' running':'')+'"><i></i><span>['+(i+1)+'/'+(S.length||mx)+'] '+esc(x.id||'')+' · <code>'+(x.chars_in||0)+'→'+(x.chars_out||0)+' 字</code>'+(x.cache_hit?' <em>cache✓</em>':'')+'</span></div>'});
return h+'<p class="action-hint" style="color:var(--ink-3);font-size:var(--fs-micro)">已用 '+(u.in_chars||0)+'/'+(u.out_chars||0)+' 字 · 步 '+S.length+'/'+mx+'</p>'}
function renderSyn(){var S=state.syn,st=S.st;$('syn-n').textContent=state.msel.length+' 篇';
$('syn-body').innerHTML=st==='failed'?'<p class="action-hint" style="color:var(--crimson)">'+esc(S.err||'运行失败')+'</p><div class="action-row"><button class="btn" id="syn-retry">重试</button><button class="btn btn-ghost" id="syn-shut">关闭</button></div>'
:st==='budget_stopped'?'<p class="action-hint" style="color:var(--amber)">预算已触顶，已完成 '+(S.done||0)+'/'+(S.max||0)+' 步</p><a class="btn" href="#settings">去调整预算</a>'
:st==='done'?synSteps(S.run)+'<div class="syn-doc">'+mdToHtml(S.text||'','')+'</div><div class="action-row"><button class="btn btn-primary" id="syn-mat">存为素材</button><button class="btn" id="syn-cs">发起创作会话</button></div>'
:st==='running'?synSteps(S.run):'<p class="action-hint">'+(st==='submitting'?'提交中…':'选中 2-8 篇素材后点「AI 综合」开始。')+'</p>'}
async function synPoll(id){for(var i=0;i<600;i++){var S=state.syn;
try{var r=(await getJSON('/api/agent/runs?id='+encodeURIComponent(id))).run||{},t=String(r.status||'');
S.run=r;S.done=(r.steps||[]).length;S.max=(r.limits||{}).steps||'—';
if(t==='done'){S.st='done';(r.steps||[]).forEach(function(x){if(x.task==='agent_synthesize')S.text=x.out_text||''})}
else if(t==='budget_stopped'||t==='failed'||t==='interrupted'){S.st=t==='budget_stopped'?'budget_stopped':'failed';S.err=String(r.error||(t==='interrupted'?'任务被中断':'运行失败'))}
else{S.st='running';renderSyn();await new Promise(function(z){setTimeout(z,900)});continue}
S.busy=0;selSync();return renderSyn()}
catch(e){S.st='failed';S.err=e.message;S.busy=0;selSync();return renderSyn()}}}
async function synRun(){var n=state.msel.length;if(n<2||n>8)return;
state.syn={st:'submitting',busy:1};$('syn-panel').hidden=false;renderSyn();selSync();
try{var d=await postJSON('/api/agent/run',{plan:'synthesis_v1',material_ids:state.msel,confirm:true});
state.syn.runId=d.run_id;state.syn.st='running';renderSyn();synPoll(d.run_id)}
catch(e){state.syn={st:'failed',err:e.message};renderSyn();selSync();toast('综合失败：'+e.message,ER)}}
async function synAdopt(t){try{var d=await postJSON('/api/agent/adopt',{run_id:state.syn.runId,target:t,confirm:true});
if(t==='compose'){toast('已建会话：'+(d.slug||''),OK);location.hash='#compose'}
else{toast('已存为素材，去素材库查看',OK);await loadMaterials();renderMaterials()}
$('syn-panel').hidden=true}catch(e){toast('采纳失败：'+e.message,ER)}}
var TR=[],trT=null,UPX=/\.(png|jpe?g|gif|webp|pdf|docx|epub|md|markdown|txt)$/i;
function trayAdd(fs){
var n=0;
[].slice.call(fs).forEach(function(f){var bad=!UPX.test(f.name),big=f.size>8388608;
TR.push({f:f,st:bad?'bad':big?'big':'ok',msg:bad?'仅图片/PDF/DOCX/EPUB/MD/TXT':(big?'超 8MiB 上限':'')});n++;});
if(!n)return;
if(trT){clearTimeout(trT);trT=null;}
$('mat-tray').hidden=false;trayPaint();
}
function trayPaint(){
$('mat-tray').innerHTML='<div class="mat-tray-head">上传 '+TR.length+' 张<button class="btn btn-primary" id="tray-go"'+(TR.some(function(r){return r.st==='ok'||r.st==='fail';})?'':' disabled')+'>上传</button><button class="btn" id="tray-x">收起</button></div>'+TR.map(function(r){
return '<div class="mat-uprow"><span class="up-dot '+r.st+'"></span><span class="up-name">'+esc(r.f.name)+' · '+hbytes(r.f.size)+'</span>'+(r.msg?'<span class="up-msg">'+esc(r.msg)+'</span>':'')+'<button class="coll-new" data-trm="0">✕</button></div>';
}).join('');
}
async function trayGo(){
for(var i=0;i<TR.length;i++){var r=TR[i];
if(r.st!=='ok'&&r.st!=='fail')continue;
r.st='send';r.msg='';trayPaint();
try{
var res=await fetch('/api/materials/upload?confirm=1&name='+encodeURIComponent(r.f.name),{method:'POST',body:r.f});
var d={};try{d=await res.json();}catch(e2){}
if(!res.ok)throw new Error(d.error||'HTTP '+res.status);
r.st='done';r.msg=d.dedup?'幂等命中':'';
}catch(err){r.st='fail';r.msg=err.message;}
trayPaint();
}
var bad=TR.filter(function(x){return x.st==='fail';}).length;
await Promise.all([loadMaterials(),loadTags()]);
renderCollRail();renderMaterials();
toast(bad?'上传完成，'+bad+' 张失败（可重传）':'上传完成',bad?WN:OK);
if(!bad)trT=setTimeout(function(){TR=[];$('mat-tray').hidden=true;},3000);
}
var LB={ids:[],i:0};
function lbOpen(id){
LB.ids=matFiltered().filter(function(m){return m.type==='image';}).map(function(m){return m.id;});
LB.i=LB.ids.indexOf(id);if(LB.i<0){LB.ids.push(id);LB.i=LB.ids.length-1;}
$('lightbox').hidden=false;lbPaint();
}
function lbGo(d){var n=LB.i+d;if(n<0||n>=LB.ids.length)return;LB.i=n;lbPaint();}
function lbClose(){$('lightbox').hidden=true;$('lb-img').removeAttribute('src');}
function lbPaint(){
var id=LB.ids[LB.i],m=id?matById(id):null;
if(!m)return lbClose();
var im=$('lb-img'),meta=(m.payload||{}).meta||{};
im.classList.remove('lb-on');
im.onload=function(){im.classList.add('lb-on');};
im.onerror=function(){im.classList.remove('lb-on');$('lb-bar').innerHTML='<span style="color:var(--crimson)">图片缺失或已删（←→ 仍可导航）</span>';};
im.src='/api/materials/file?id='+encodeURIComponent(id)+'&variant=full';
$('lb-bar').innerHTML=esc(m.title||id)+(meta.width?' · '+meta.width+'×'+meta.height:'')+(meta.bytes?' · '+hbytes(meta.bytes):'')+' · '+licBadge(m)+' · 标签 '+esc(((m.triage||{}).tags||[]).join('、')||'无');
}
function bindMatMedia(){
$('syn-btn').onclick=synRun;
$('sel-clear').onclick=function(){state.msel=[];selSync()};
$('syn-close').onclick=function(){$('syn-panel').hidden=true;if(state.syn.busy)toast('任务在后台继续，完成后可重开面板查看',WN)};
$('syn-body').onclick=function(e){var t=e.target;
if(t.closest('#syn-retry'))synRun();
else if(t.closest('#syn-shut'))$('syn-panel').hidden=true;
else if(t.closest('#syn-mat'))synAdopt('material');
else if(t.closest('#syn-cs'))synAdopt('compose')};
$('gen-close').onclick=function(){++gN;if(gAb)gAb.abort();$('gen-panel').hidden=true};
$('gen-run').onclick=gGo;
$('gen-cancel').onclick=function(){++gN;if(gAb)gAb.abort();state.gen.st='';gPaint()};
$('gen-panel').onclick=function(e){var c=e.target.closest('[data-size],[data-model]');if(!c)return;
if(c.hasAttribute('data-size'))state.gen.size=c.getAttribute('data-size');else state.gen.model=c.getAttribute('data-model');gPaint()};
$('mat-tray').addEventListener('click',function(e){
if(e.target.id==='tray-go')return trayGo();
if(e.target.id==='tray-x'){TR=[];this.hidden=true;return;}
var row=e.target.closest('.mat-uprow');
if(row&&e.target.closest('[data-trm]')){TR.splice([].indexOf.call(this.querySelectorAll('.mat-uprow'),row),1);TR.length?trayPaint():this.hidden=true;}
});
$('lightbox').addEventListener('click',function(e){if(e.target===this)lbClose();});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if($('lightbox').hidden){
if(e.key==='Escape'){
if(!$('gen-panel').hidden){if(gAb)gAb.abort();$('gen-panel').hidden=true;}
else if(!$('syn-panel').hidden)$('syn-panel').hidden=true;
}
return;
}
e.preventDefault();
e.stopPropagation();
if(e.key==='Escape')lbClose();
else if(e.key==='ArrowLeft')lbGo(-1);
else if(e.key==='ArrowRight')lbGo(1);
},true);
}
bindMatMedia();
