function matPreview(m){
var p=m.payload||{},o=m.origin||{},t=p.text||(o&&o.title)||'';
return String(t).replace(/\s+/g,' ').slice(0,120);
}
function hbytes(n){n=+n||0;if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';return(n/1048576).toFixed(1)+' MB';}
var MEMPTY_T=$('mat-empty-title').textContent,MEMPTY_X=$('mat-empty-text').textContent;
function matRow(m){
var s=m.score,t=s==null?-1:s,lv=t>=80?'hi':t>=60?'mid':'lo',b=m.score_breakdown||{},h='',g='',i,tri=m.triage||{},o=m.origin||{},p=m.payload||{},meta=p.meta||{},bk=meta.book,st=tri.starred?'取消收藏':'收藏',tg=tri.tags||[];
if(b.depth!=null)for(i=0;i<4;i++){h+='<div class="pbar"><span class="pbar-name">'+DD[i][0]+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+(b[DD[i][1]]||0)/DD[i][2]*100+'%"></span></span><span class="pbar-n">'+(b[DD[i][1]]||0)+'/'+DD[i][2]+'</span></div>';g+=(g?'·':'')+DD[i][0]+(b[DD[i][1]]||0)+'/'+DD[i][2];}
return '<details class="mat-row'+(state.mdens==='full'?' open':'')+'" data-mid="'+esc(m.id)+'">'+
'<summary class="mat-row-sum" title="展开或收起评分与详情">'+
'<button class="btn btn-ghost btn-icon dstar'+(tri.starred?' on':'')+'" data-mstar="'+esc(m.id)+'" title="'+st+'" aria-label="'+st+'">'+I('star')+'</button>'+
(m.type==='image'?'<img class="mat-thumb" loading="lazy" data-mlight="'+esc(m.id)+'" src="'+esc('/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=thumb')+'" alt="" onerror="this.style.visibility=\'hidden\';this.nextElementSibling.style.display=\'inline-flex\'">':'')+
(m.type==='image'?'<span class="mty-ico mat-thumb-fb" title="'+esc(MTY[m.type]||m.type)+'" style="display:none">'+I('image')+'</span>':'')+
'<span class="mty-ico" title="'+esc(MTY[m.type]||m.type)+'">'+I(MTYI[m.type]||'tag')+'</span>'+
'<span class="mat-row-main"><span class="mat-row-title" data-mdrow="'+esc(m.id)+'">'+(tri.read?'':'<i class="unread-dot" title="未读"></i>')+hl(m.title||o.title||o.uri||m.id)+(m.drafted?' <span class="badge pass">已入队</span>'+Z:'')+(tri.enabled?'':' <span class="badge archived">已禁用</span>'+Z)+
(bk&&!bk.synthesis&&bk.distilled===false?' <span class="badge warn" title="AI 提炼失败，此条为原始文本；可在详情重新提炼">未提炼</span>'+Z:'')+'</span>'+
'<span class="mat-row-prev">'+hl(matPreview(m)||o.uri||m.id)+'</span>'+
'<span class="mat-row-meta"><span class="src-badge">'+esc(o.source_id||'—')+'</span>'+simPct(m.similarity)+
(bk?'<a class="bk-line" href="#books?search='+encodeURIComponent(bk.name||'')+'" title="书籍视图：'+esc(bk.name||'')+(bk.chapter_index?' 第 '+bk.chapter_index+' 章':'')+'">《'+esc(String(bk.name||'').slice(0,20))+'》'+(bk.chapter_index?'第 '+bk.chapter_index+' 章':'')+'</a>':'')+
(s==null?'':'<span class="dc-bar '+lv+'" title="评分 '+s+'"><i style="width:'+t+'%"></i>'+Z)+
licBadge(m)+
'<span class="tag">'+esc(MTY[m.type]||m.type)+(meta.line?' · '+esc(meta.line):'')+(m.grade?' · '+esc(m.grade)+' 级':'')+(meta.platform?' · '+esc(meta.platform):'')+(meta.duration_text?' · '+esc(meta.duration_text):'')+Z+
'</span></span></span>'+
'<button class="btn btn-ghost btn-icon mat-more" data-mmore="'+esc(m.id)+'" title="更多操作（右键同）" aria-label="更多操作">⋯</button>'+
'<button class="btn" data-mread="'+esc(m.id)+'">'+(tri.read?'已读':'未读')+'</button>'+
'</summary>'+
'<div class="mat-row-x">'+h+
'<div class="action-row mat-acts">'+(m.type==='text'?'<button class="btn" data-mdraft="'+esc(o.uri)+'"'+(m.drafted?' disabled':'')+'>生成骨架</button>':'')+((meta.url||p.url)?'<a class="btn" href="'+esc(meta.url||p.url)+'" target="_blank" rel="noopener">查看原文</a>':'')+'<button class="btn" data-mcolladd="'+esc(m.id)+'">+ 集合</button><button class="btn" data-mdetail="'+esc(m.id)+'">详情</button><button class="btn" data-moff="'+esc(m.id)+'">'+(tri.enabled?'禁用':'启用')+'</button><button class="btn" data-mai="'+esc(m.id)+'">AI 摘要</button><button class="btn mat-del" data-mdel="'+esc(m.id)+'">删除</button></div>'+
'<div class="tag-row" data-tagrow="'+esc(m.id)+'">'+tg.map(function(t2){return '<span class="chip-tag" data-mtagf="'+esc(t2)+'" title="点击按此标签过滤">'+esc(t2)+'<i data-mtagdel="'+esc(m.id)+'|'+esc(t2)+'" title="移除标签">×</i></span>';}).join('')+'<input class="input tag-in" maxlength="24" list="tag-dl" placeholder="输入标签，回车添加" data-mtagin="'+esc(m.id)+'"></div>'+
'<p class="action-hint">❝ '+esc(o.uri||m.id)+' · '+esc(o.source_id||'')+' · '+esc(String(m.fetched_at||'').replace('T',' '))+(g?' · '+g:'')+(s==null?'':' · 评分 '+(s==null?'—':s))+'</p></div></details>';
}
function srcGroups(L){
var names={},g={},order=[],out='';
matSrcs().forEach(function(s){names[s.id]=s.label||s.id;});
L.forEach(function(m){var k=(m.origin||{}).source_id||'—';if(!g[k]){g[k]=[];order.push(k);}g[k].push(m);});
order.forEach(function(k){out+='<h3 class="mat-src">'+esc(names[k]||k)+'<span class="chip-count">'+g[k].length+'</span></h3>'+g[k].map(matRow).join('');});
return out;}
function syncMatHash(){
var q=['src='+encodeURIComponent(state.msrc)];
if(state.mtab!=='all')q.push('tab='+state.mtab);
if(state.mdens!=='compact')q.push('dens='+state.mdens);
if(state.mtype)q.push('type='+state.mtype);
if(state.mspark)q.push('spark=1');
if(state.mcoll)q.push('coll='+encodeURIComponent(state.mcoll));
if(state.mtag)q.push('tag='+encodeURIComponent(state.mtag));
if(state.mdrawer)q.push('m='+state.mdrawer);
history.replaceState(null,'','#material'+(q.length?'?'+q.join('&'):''));
}
function matFiltered(){
var L=(state.mats||[]).slice(),tab=state.mtab;
if(state.msrc)L=L.filter(function(m){return ((m.origin||{}).source_id||'')===state.msrc;});
if(state.mtype)L=L.filter(function(m){return m.type===state.mtype;});
if(state.mtag)L=L.filter(function(m){return ((m.triage||{}).tags||[]).indexOf(state.mtag)>=0;});
if(tab==='starred')L=L.filter(function(m){return (m.triage||{}).starred;});
else if(tab==='unread')L=L.filter(function(m){return !(m.triage||{}).read;});
else if(tab==='hiunread')L=L.filter(function(m){return !(m.triage||{}).read&&(m.score||0)>=80;});
else if(tab==='derived')L=L.filter(function(m){return m.type==='derived';});
else if(tab==='revisit')L=L.filter(function(m){return (m.triage||{}).starred;}).sort(function(a,b){return String(b.fetched_at||'').localeCompare(String(a.fetched_at||''));});
if(state.msort==='relevance')L.sort(function(a,b){return (b.similarity||b.score||0)-(a.similarity||a.score||0);});
else if(state.msort==='time')L.sort(function(a,b){return String(b.fetched_at||'').localeCompare(String(a.fetched_at||''));});
else if(state.msort==='title')L.sort(function(a,b){return String(a.title||a.id).localeCompare(String(b.title||b.id),'zh');});
return L;
}
function renderMaterials(){
var L=matFiltered(),tab=state.mtab,sn=0,ur=0,df=0;
(state.mats||[]).forEach(function(m){var t2=m.triage||{};if(t2.starred)sn++;if(!t2.read)ur++;if(m.drafted)df++;});
var list=$('mat-list');
list.className='mat-list d-'+state.mdens;
list.innerHTML=state.mdens==='src'?srcGroups(L):L.map(matRow).join('');
var empty=$('mat-empty');
empty.hidden=L.length>0;
if(!L.length){
var filtered=state.msrc!==''||tab!=='all'||!!state.mtype||!!state.mspark||!!state.mquery||!!state.mtag||!!state.mcoll;
$('mat-empty-title').textContent=filtered?'没有符合条件的素材':(tab==='starred'?'暂无收藏':MEMPTY_T);
$('mat-empty-text').textContent=state.mquery?('全库未命中『'+state.mquery+'』——试试 "精确短语"、foo* 前缀，或清除其他筛选。'):(filtered?'当前筛选（来源/分诊/类型/标签/集合/搜索）下没有素材，清除筛选看全部。':(tab==='starred'?'点素材行首星标按钮收藏后，在此按收藏筛看。':MEMPTY_X));
$('mat-empty-action').hidden=!filtered;
}
$('mt-starred').textContent=sn;
$('mt-unread').textContent=ur;
updateNavBadge('nav-badge-material',ur);
QA('[data-mtab]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-mtab')===tab);});
QA('[data-mdens]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-mdens')===state.mdens);});
var sk=$('mat-spark');if(sk)sk.classList.toggle('is-active',!!state.mspark);
$('mat-coll-exit').hidden=!state.mcoll;
var cn='';(state.colls||[]).forEach(function(c){if(c.id===state.mcoll)cn=c.name;});
$('mat-sub').textContent=(state.mquery?'命中 '+(state.mqTotal>state.mats.length&&state.mats.length>=500?'前 '+state.mats.length+' / 共 '+state.mqTotal:state.mqTotal)+' 条':(state.mcoll?'集合「'+(cn||state.mcoll)+'」'+L.length+' 条':'共 '+L.length+' 条素材'))+' · 收藏 '+sn+' · 未读 '+ur+' · 已生成骨架 '+df;
if(!$('mat-drawer').hidden&&state.mdrawer)paintMatDrawer();
}
async function openMatDrawer(id){
try{
var d=await getJSON('/api/materials/detail?id='+encodeURIComponent(id));
state.mdrawer=id;state.mdrawerData=d;
paintMatDrawer(d);
$('mat-drawer').hidden=false;
syncMatHash();
}catch(err){toast('读取素材详情失败：'+err.message,ER);}
}
function paintMatDrawer(d){
if(!state.mdrawer)return;
if(!d)d=state.mdrawerData;
if(!d){var m0=matById(state.mdrawer);if(!m0)return;d={material:m0,cascade:[]};}
state.mdrawerData=d;
var m=d.material||{},casc=d.cascade||[];
var o=m.origin||{},tri=m.triage||{},p=m.payload||{},meta=p.meta||{},h='',i,b=m.score_breakdown||{};
if(b.depth!=null)for(i=0;i<4;i++)h+='<div class="pbar"><span class="pbar-name">'+DD[i][0]+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+(b[DD[i][1]]||0)/DD[i][2]*100+'%"></span></span><span class="pbar-n">'+(b[DD[i][1]]||0)+'/'+DD[i][2]+'</span></div>';
var prev=p.text||meta.summary||o.uri||'（无正文预览）';
var media='',full='/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=full';
if(m.type==='image')media='<img class="drawer-media" src="'+esc(full)+'"><button class="btn" data-mlight="'+esc(m.id)+'">灯箱查看</button>';
else if(m.type==='video'){var vu=p.local_path?full:(p.url||o.uri);media='<video class="drawer-media" controls preload="metadata" src="'+esc(vu)+'"></video><a class="btn" href="'+esc(vu)+'" target="_blank" rel="noopener">新窗口打开</a>';}
$('mat-drawer-title').innerHTML=(tri.starred?'★ ':'')+esc(m.title||o.uri||m.id);
$('mat-drawer-body').innerHTML=
'<div class="chips" style="margin:0 0 var(--space-sm)"><button class="chip" data-miit="0">内容</button><button class="chip" id="mi-t1" data-miit="1">打磨历史</button></div>'+
'<div id="mi-view">'+
(media||'<pre class="drawer-preview">'+esc(prev)+'</pre>')+
h+
'<p class="drawer-meta">'+licBadge(m)+'<span class="tag">'+esc(MTY[m.type]||m.type)+Z+'<span class="src-badge">'+esc(o.source_id||'—')+'</span> · '+esc(String(m.fetched_at||'').replace('T',' '))+'</p>'+
(meta.book?'<p class="drawer-meta bk-line">来源 <a href="#books?search='+encodeURIComponent(meta.book.name||'')+'">《'+esc(meta.book.name||'')+'》</a> · '+esc(meta.book.author||'')+(meta.book.chapter_index?' · 第 '+meta.book.chapter_index+' 章 '+esc(meta.book.chapter_title||''):'')+(meta.book.synthesis?' · 全书综述（'+(meta.book.chapters||0)+' 章）':'')+'</p>':'')+
'<div class="action-title" style="font-size:13px">血统链</div>'+lineageHtml(m)+
(casc.length?'<p class="action-hint">被 '+casc.length+' 条衍生素材引用（删除连坐面）</p>':'')+
matRelatedHtml(d.related)+
tsugBox(m)+
(meta.original_ref?'<div class="action-row"><button class="btn btn-ghost" data-morig="1" style="font-size:var(--fs-caption)">原始章节</button></div>':'')+
'</div>'+
'<div id="mi-body" hidden></div>'+
'<div class="action-row mat-acts">'+(m.type==='text'?'<button class="btn" data-mdraft="'+esc(o.uri)+'"'+(m.drafted?' disabled':'')+'>生成骨架</button>':'')+((meta.url||p.url)?'<a class="btn" href="'+esc(meta.url||p.url)+'" target="_blank" rel="noopener">查看原文</a>':'')+'<button class="btn" data-mcolladd="'+esc(m.id)+'">+ 集合</button><button class="btn" data-mstar="'+esc(m.id)+'">'+(tri.starred?'取消收藏':'收藏')+'</button><button class="btn" data-mread="'+esc(m.id)+'">'+(tri.read?'已读':'未读')+'</button><button class="btn" data-moff="'+esc(m.id)+'">'+(tri.enabled?'禁用':'启用')+'</button><button class="btn" data-mai="'+esc(m.id)+'">AI 摘要</button><button class="btn mat-del" data-mdel="'+esc(m.id)+'">删除</button></div>'+
'';
if(window.__mdpubMatIter)__mdpubMatIter.repaint(state.mdrawer);
if(window.__mdpubMatLineage)__mdpubMatLineage.repaint(state.mdrawer);
}
var tsug={};
function tsugBase(){var m=matById(state.mdrawer)||((state.mdrawerData||{}).material)||{};return (((m.triage||{}).tags)||[]).slice()}
function tsugBox(m){
if(tsug.id!==m.id)tsug={id:m.id};
var t=tsug,x=t.base||tsugBase(),h='<div class="tag-row" id="tag-suggest"><span class="action-title" style="font-size:var(--fs-caption)">建议标签</span>';
if(t.st==='load')return h+'<div class="skel"><div class="skel-line"></div><div class="skel-line w70"></div><div class="skel-line w90"></div></div></div>';
if(t.st==='no')return h+'<span class="action-hint">无建议</span></div>';
if(t.st!=='ok')return h+'<button class="btn btn-ghost" data-tsg="1" style="color:var(--indigo)">获取建议</button></div>';
var mk=t.ex.length?t.ex:x,p=[];
h+=t.tags.map(function(g,i){
if(mk.indexOf(g)>=0)return '<button class="chip-tag syn-chip off" disabled title="已有该标签">'+esc(g)+'（已有）</button>';
if(t.sel[i])p.push(g);
return '<button class="chip-tag syn-chip'+(t.sel[i]?' on':'')+'" data-tso="'+i+'">'+esc(g)+'</button>';
}).join('')+(t.cue?'<span style="color:var(--indigo);font-size:var(--fs-micro)">（缓存）</span>':'');
var u=x.concat(p);
if(u.length>12)h+='<p class="action-hint" style="color:var(--amber)">超出 12 个上限，将保留前 12（既有优先），被弃：'+esc(u.slice(12).join('、'))+'</p>';
return h+'<button class="btn btn-primary" data-tsg="ok"'+(p.length?'':' disabled title="先勾选要采纳的建议标签"')+'>采纳所选</button></div>'}
async function tsugGo(mid){
tsug={id:mid,st:'load',base:tsugBase()};paintMatDrawer();
try{var d=await postJSON('/api/action/tags_suggest',{id:mid,confirm:true});
tsug={id:mid,st:d.tags&&d.tags.length?'ok':'no',tags:d.tags||[],ex:d.existing||[],sel:{},cue:d.cache_hit,base:tsug.base};}
catch(e){tsug={id:mid,st:'err'};toast('获取建议失败：'+e.message,ER);}
paintMatDrawer()}
async function tsugAdopt(mid){
var b=(tsug.base||[]).slice();
tsug.ex.forEach(function(g){if(b.indexOf(g)<0)b.push(g)});
var p=tsug.tags.filter(function(g,i){return tsug.sel[i]&&b.indexOf(g)<0});
if(!p.length)return;
try{await postJSON('/api/materials/triage',{id:mid,tags:b.concat(p).slice(0,12),confirm:true});
tsug={};toast('标签已更新',OK);
await loadMaterials();renderMaterials();await loadTags();renderCollRail();paintMatDrawer()}
catch(e){toast('采纳失败：'+e.message,ER)}}
function closeMatDrawer(){
state.mdrawer='';
$('mat-drawer').hidden=true;
syncMatHash();
}
async function bkOrigToggle(){
var host=$('mat-drawer-body'),dv=host.querySelector('.orig-view');
var m=((state.mdrawerData||{}).material)||{},ref=(((m.payload||{}).meta||{}).original_ref||{}).path;
if(!ref)return toast('无原始章节引用（original_ref 缺失）',WN);
if(dv){dv.hasAttribute('open')?dv.removeAttribute('open'):dv.setAttribute('open','');return;}
dv=document.createElement('details');
dv.className='orig-view';dv.setAttribute('open','');
dv.innerHTML='<summary>原始章节（加载中…）</summary><div class="skel"><div class="skel-line w90"></div><div class="skel-line w70"></div></div>';
var acts=host.querySelector('.mat-acts');
acts?host.insertBefore(dv,acts):host.appendChild(dv);
try{
var d=await getJSON('/api/book/original?ref='+encodeURIComponent(ref));
dv.innerHTML='<summary>原始章节 · '+(d.chars||0)+' 字</summary>';
var pre=document.createElement('pre');
pre.textContent=d.text||'（空）';
dv.appendChild(pre);
if(d.folded){
var more=document.createElement('button');
more.className='btn btn-ghost';more.style.fontSize='var(--fs-caption)';
more.textContent='展开全部（共 '+(d.chars||0)+' 字）';
more.onclick=async function(){
more.disabled=true;
try{var f=await getJSON('/api/book/original?ref='+encodeURIComponent(ref)+'&full=1');
pre.textContent=f.text||'';more.remove();}
catch(err){toast('全文加载失败：'+err.message,ER);more.disabled=false;}};
dv.appendChild(more);}
}catch(err){dv.innerHTML='<summary>原始章节</summary><p class="action-hint" style="color:var(--crimson)">加载失败：'+esc(err.message)+'</p>';}
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
function matById(id){var i;for(i=0;i<(state.mats||[]).length;i++)if(state.mats[i].id===id)return state.mats[i];return null;}
async function matTriage(id,changes){
try{
var d=await postJSON('/api/materials/triage',Object.assign({id:id,confirm:true},changes));
var m=matById(id);if(m)m.triage=Object.assign({},m.triage||{},(d&&d.triage)||changes);
renderMaterials();toast('分诊已更新（星标/已读/启停'+(changes.tags?'/标签':'')+'）',OK);
}catch(err){toast('分诊更新失败：'+err.message,ER);}
}
async function handleMatDraft(btn){
var p=btn.getAttribute('data-mdraft');
btn.disabled=true;
try{
var d=await postJSON('/api/action/discover_draft',{path:p,confirm:true});
toast('已生成骨架：'+(d.output||p),OK);
	state.mats.forEach(function(c2){if((c2.origin||{}).uri===p)c2.drafted=true;});
renderMaterials();
await loadArticles(true);
viewRepaint('publish');
	}catch(err){toast('生成骨架失败：'+err.message,ER);btn.disabled=false;}
}
async function matDelete(id){
var d,casc,m=matById(id),title=(m&&m.title)||id;
try{d=await getJSON('/api/materials/detail?id='+encodeURIComponent(id));}
catch(err){toast('读取素材详情失败：'+err.message,ER);return;}
casc=d.cascade||[];
var k=(state.collOf[id]||[]).length;
var ok=await askConfirm({title:'删除素材 1/2 · 连坐声明',message:(casc.length?casc.length+' 条衍生素材引用「'+title+'」，删除后引用处显「素材已删」徽章（不级联删）。':'删除「'+title+'」后，引用处（如有）将显「素材已删」徽章。')+(k?'该素材属 '+k+' 个集合，删除后各集合计数将显「含已删」（不自动移除出集）。':''),confirmText:'继续'});
if(!ok)return;
ok=await askConfirm({title:'删除素材 2/2 · 终确认',message:'确认删除「'+title+'」？墓碑落册后重采不复活。',confirmText:'确认删除'});
if(!ok)return;
	try{
		var r=await postJSON('/api/materials/delete',{id:id,confirm:true});
		toast('已删除'+(((r.cascade)||[]).length?'（'+r.cascade.length+' 处引用标「素材已删」）':''),OK);
		if(state.mdrawer===id)closeMatDrawer();
		await Promise.all([loadMaterials(),loadColls()]);renderMaterials();renderCollRail();
	}catch(err){toast('删除失败：'+err.message,ER);}
	}
var refineBusy=false;
async function matAI(id){
if(refineBusy)return;
refineBusy=true;
var was=state.mdrawer;
$('mat-drawer').hidden=false;
$('mat-drawer-title').textContent='AI 摘要';
$('mat-drawer-body').innerHTML='<p class="action-hint">AI 起草中（preview 零落盘，拒绝即消失不留痕）…</p>';
try{
var d=await postJSON('/api/action/refine',{action:'preview',id:id,confirm:true});
state.mdrawer=id;
$('mat-drawer-body').innerHTML='<p class="action-hint">预览（可编辑后采纳；放弃不调 API）：</p>'+
'<textarea id="refine-area" class="set-area" rows="9"></textarea>'+
'<div class="action-row"><button class="btn btn-primary" id="refine-adopt">存为素材</button><button class="btn" id="refine-drop">放弃</button></div>';
$('refine-area').value=d.text||'';
var ad=$('refine-adopt');
ad.onclick=async function(){
ad.disabled=true;
try{var r=await postJSON('/api/action/refine',{action:'adopt',id:id,text:$('refine-area').value,confirm:true});
toast('已存为衍生素材',OK);
await loadMaterials();renderMaterials();
paintMatDrawer({material:r.material,cascade:[]});
}catch(err){toast('采纳失败：'+err.message,ER);ad.disabled=false;}
};
$('refine-drop').onclick=function(){paintMatDrawer();toast('已放弃（零落盘零痕迹）',OK);};
}catch(err){
toast('AI 摘要失败：'+err.message,ER);
if(was)paintMatDrawer();else{$('mat-drawer').hidden=true;}
}
finally{refineBusy=false;}
}
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
VAPI.material={enter:async function(route){
await loadSettings();
var msp=route.params.get('src'),mtp=route.params.get('tab'),mdp=route.params.get('dens'),mty=route.params.get('type'),mid=route.params.get('m'),mcl=route.params.get('coll'),mtg=route.params.get('tag');
if(msp!=null)state.msrc=msp;
if(mtp==='review')mtp='revisit';
if(mtp==='all'||mtp==='starred'||mtp==='unread'||mtp==='hiunread'||mtp==='derived'||mtp==='revisit')state.mtab=mtp;
if(mdp==='compact'||mdp==='src'||mdp==='full')state.mdens=mdp;
if(mty!=null)state.mtype=mty;
var msk=route.params.get('spark');if(msk!=null)state.mspark=msk?'spark':'';
if(mcl!=null)state.mcoll=mcl;
if(mtg!=null)state.mtag=mtg;
await Promise.all([loadMaterials(),loadColls(),loadTags()]);
fillMatType();
renderCollRail();
renderMaterials();
if(mid)await openMatDrawer(mid);
},repaint:renderMaterials};
function bindMaterial(){
document.addEventListener('click',function(e){
var c=e.target.closest('[data-msrc],[data-mtab],[data-mdens],[data-mcoll],[data-mtag]');
if(!c)return;
if(c.hasAttribute('data-mcoll')){
var cv=c.getAttribute('data-mcoll');
state.mcoll=(cv&&cv!==state.mcoll)?cv:'';
state.mtab='all';
return matRefetch();
}
if(c.hasAttribute('data-mtag')){
var tv=c.getAttribute('data-mtag');
state.mtag=tv!==state.mtag?tv:'';
state.mtab='all';
return matRefetch();
}
if(c.hasAttribute('data-msrc'))state.msrc=c.getAttribute('data-msrc');
if(c.hasAttribute('data-mtab'))state.mtab=c.getAttribute('data-mtab');
else if(c.hasAttribute('data-msrc'))state.mtab='all';
else state.mdens=c.getAttribute('data-mdens');
syncMatHash();renderCollRail();renderMaterials();
});
$('mat-empty-action').addEventListener('click',function(){
state.msrc='';state.mtab='all';state.mtype='';state.mspark='';state.mconf='';state.mquery='';state.mtag='';state.mcoll='';
$('mat-search').value='';$('mat-search-x').hidden=true;fillMatType();
syncMatHash();renderCollRail();renderMaterials();loadMaterials().then(renderMaterials);
});
function matActsClick(e){
var el=e.target.closest('[data-mrelated],[data-mstar],[data-mdraft],[data-mread],[data-moff],[data-mai],[data-mdel],[data-mdetail],[data-mdrow],[data-mlight],[data-mcolladd],[data-mtagdel],[data-mtagf],[data-mmore],[data-tsg],[data-tso],[data-miit],[data-morig]');
if(!el)return;
e.preventDefault();
if(el.hasAttribute('data-mrelated'))return openMatDrawer(el.getAttribute('data-mrelated'));
if(el.hasAttribute('data-morig'))return bkOrigToggle();
if(el.hasAttribute('data-miit'))return window.__mdpubMatIter&&__mdpubMatIter.tab(el.getAttribute('data-miit')==='1',state.mdrawer);
if(el.hasAttribute('data-tsg')){
var tg=el.getAttribute('data-tsg');
return tg==='ok'?tsugAdopt(state.mdrawer):tsugGo(state.mdrawer);
}
if(el.hasAttribute('data-tso')){var ti=el.getAttribute('data-tso');tsug.sel[ti]=!tsug.sel[ti];return paintMatDrawer();}
if(el.hasAttribute('data-mlight'))return lbOpen(el.getAttribute('data-mlight'));
if(el.hasAttribute('data-mmore'))return ctxShow(e,matMenu(el.getAttribute('data-mmore')));
if(el.hasAttribute('data-mcolladd'))return collMenu(e,el.getAttribute('data-mcolladd'));
if(el.hasAttribute('data-mtagf')){var tv=el.getAttribute('data-mtagf');state.mtag=(tv===state.mtag)?'':tv;state.mtab='all';return matRefetch();}
if(el.hasAttribute('data-mtagdel')){var kv=el.getAttribute('data-mtagdel').split('|'),m0=matById(kv[0]);return tagSet(kv[0],(((m0||{}).triage||{}).tags||[]).filter(function(t){return t!==kv[1];}));}
if(el.hasAttribute('data-mdraft')){if(!el.disabled)handleMatDraft(el);return;}
var id=el.getAttribute('data-mstar')||el.getAttribute('data-mread')||el.getAttribute('data-moff')||el.getAttribute('data-mai')||el.getAttribute('data-mdel')||el.getAttribute('data-mdetail')||el.getAttribute('data-mdrow'),k=el.hasAttribute('data-mstar')?'starred':el.hasAttribute('data-mread')?'read':el.hasAttribute('data-moff')?'enabled':'',m=matById(id);
if(k)return matTriage(id,(function(){var c={};c[k]=!(m&&(m.triage||{})[k]);return c;})());
if(el.hasAttribute('data-mai'))return matAI(id);
if((el.hasAttribute('data-mdetail')||el.hasAttribute('data-mdrow'))&&(e.metaKey||e.ctrlKey))return selToggle(id);
if(el.hasAttribute('data-mdetail')||el.hasAttribute('data-mdrow'))return openMatDrawer(id);
matDelete(id);
}
$('mat-list').addEventListener('click',matActsClick);
$('mat-drawer').addEventListener('click',matActsClick);
$('ctx').addEventListener('click',matActsClick);
$('mat-drawer-close').addEventListener('click',closeMatDrawer);
$('syn-btn').onclick=synRun;
$('sel-clear').onclick=function(){state.msel=[];selSync()};
$('syn-close').onclick=function(){$('syn-panel').hidden=true;if(state.syn.busy)toast('任务在后台继续，完成后可重开面板查看',WN)};
$('syn-body').onclick=function(e){var t=e.target;
if(t.closest('#syn-retry'))synRun();
else if(t.closest('#syn-shut'))$('syn-panel').hidden=true;
else if(t.closest('#syn-mat'))synAdopt('material');
else if(t.closest('#syn-cs'))synAdopt('compose')};
$('mat-gen-btn').onclick=function(){$('mat-drawer').hidden=$('syn-panel').hidden=true;$('gen-panel').hidden=false;gPaint()};
$('gen-close').onclick=function(){++gN;if(gAb)gAb.abort();$('gen-panel').hidden=true};
$('gen-run').onclick=gGo;
$('gen-cancel').onclick=function(){++gN;if(gAb)gAb.abort();state.gen.st='';gPaint()};
$('gen-panel').onclick=function(e){var c=e.target.closest('[data-size],[data-model]');if(!c)return;
if(c.hasAttribute('data-size'))state.gen.size=c.getAttribute('data-size');else state.gen.model=c.getAttribute('data-model');gPaint()};
function matMenu(id){
return '<button class="cp-item" data-mstar="'+id+'">星标切换</button><button class="cp-item" data-mai="'+id+'">AI 摘要</button><button class="cp-item dg" data-mdel="'+id+'">删除</button>';
}
$('mat-list').addEventListener('contextmenu',function(e){
var row=e.target.closest('[data-mid]');
if(!row)return;
e.preventDefault();
var id=row.getAttribute('data-mid');
ctxShow(e,matMenu(id));
});
var sDb=null;
function matRefetch(){syncMatHash();renderCollRail();
if(state.mquery)return matSearch(state.mquery);
return loadMaterials().then(renderMaterials);}
$('mat-search').addEventListener('input',function(){
state.mquery=this.value.trim();$('mat-search-x').hidden=!this.value;
if(sDb)clearTimeout(sDb);sDb=setTimeout(matRefetch,300);
});
$('mat-search').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'){e.stopPropagation();
if(this.value){this.value='';state.mquery='';$('mat-search-x').hidden=true;hideMatHint();matRefetch();}else this.blur();}
});
on2('mat-search-x',function(){$('mat-search').value='';state.mquery='';this.hidden=true;hideMatHint();matRefetch();$('mat-search').focus();});
var mm=$('mat-modes');if(mm)mm.addEventListener('click',function(e){
var b=e.target.closest('[data-searchmode]');if(!b)return;
state.searchMode=b.getAttribute('data-searchmode');syncMatModes();hideMatHint();
if(state.mquery)matSearch(state.mquery);
});
$('mat-type').addEventListener('change',function(){state.mtype=this.value;syncMatHash();renderMaterials();});
$('mat-sort').addEventListener('change',function(){state.msort=this.value;renderMaterials();});
on2('mat-ingest-btn',function(){matIngest('');});
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
$('mat-tray').addEventListener('click',function(e){
if(e.target.id==='tray-go')return trayGo();
if(e.target.id==='tray-x'){TR=[];this.hidden=true;return;}
var row=e.target.closest('.mat-uprow');
if(row&&e.target.closest('[data-trm]')){TR.splice([].indexOf.call(this.querySelectorAll('.mat-uprow'),row),1);TR.length?trayPaint():this.hidden=true;}
});
on2('mat-upload-btn',function(){$('mat-file').click();});
$('mat-file').addEventListener('change',function(){if(this.files.length)trayAdd(this.files);this.value='';});
var mDrag=0;
$('view-material').addEventListener('dragenter',function(e){if(!dropHasFiles(e))return;e.preventDefault();mDrag++;$('mat-drop').hidden=false;});
$('view-material').addEventListener('dragover',function(e){if(dropHasFiles(e))e.preventDefault();});
$('view-material').addEventListener('dragleave',function(e){if(!dropHasFiles(e))return;mDrag=Math.max(0,mDrag-1);if(!mDrag)$('mat-drop').hidden=true;});
$('view-material').addEventListener('drop',function(e){mDrag=0;$('mat-drop').hidden=true;
if(e.dataTransfer&&e.dataTransfer.files.length){e.preventDefault();trayAdd(e.dataTransfer.files);}});
document.addEventListener('paste',function(e){
if(!$('view-material').hidden&&e.clipboardData&&e.clipboardData.files.length)trayAdd(e.clipboardData.files);
});
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
$('lightbox').addEventListener('click',function(e){if(e.target===this)lbClose();});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if($('lightbox').hidden)return;
e.preventDefault();
e.stopPropagation();
if(e.key==='Escape')lbClose();
else if(e.key==='ArrowLeft')lbGo(-1);
else if(e.key==='ArrowRight')lbGo(1);
},true);
function tagN(s){return String(s).toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff-]+/g,'-').replace(/-+/g,'-').replace(/^-+|-+$/g,'');}
async function tagSet(id,tags){
try{
var d=await postJSON('/api/materials/triage',{id:id,tags:tags,confirm:true});
var m=matById(id);
if(m)m.triage=(d&&d.triage)||Object.assign({},m.triage||{},{tags:tags});
renderMaterials();await loadTags();renderCollRail();
toast('标签已更新',OK);
}catch(err){toast('标签更新失败：'+err.message,ER);}
}
async function tagAdd(mid,v){
var m=matById(mid),tags=(((m||{}).triage)||{}).tags||[],nv=tagN(v);
if(!nv)return;
if(tags.indexOf(nv)>=0){toast('标签已存在',WN);return;}
if(tags.length>=12){toast('已达 12 个标签上限，先移除再添加',WN);return;}
await tagSet(mid,tags.concat([nv]));
}
$('mat-list').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
var inp=e.target.closest('[data-mtagin]');
if(!inp)return;
if(e.key==='Enter'){e.preventDefault();tagAdd(inp.getAttribute('data-mtagin'),inp.value);inp.value='';}
});
var CF={color:'indigo'},CFC=['vermilion','indigo','jade','amber','slate'];
function cfDots(){$('cf-dots').innerHTML=CFC.map(function(c){return '<button type="button" class="cf-dot'+(CF.color===c?' on':'')+'" data-cfcol="'+c+'" style="background:var(--'+c+',var(--ink-2))"></button>';}).join('');}
on2('coll-new-btn',function(){$('cf-name').value='';cfDots();$('coll-form').hidden=false;$('cf-name').focus();});
on2('cf-cancel',function(){$('coll-form').hidden=true;});
$('coll-form').addEventListener('click',function(e){if(e.target===this)this.hidden=true;});
$('cf-dots').addEventListener('click',function(e){var d=e.target.closest('[data-cfcol]');if(d){CF.color=d.getAttribute('data-cfcol');cfDots();}});
$('cf-ok').addEventListener('click',async function(){
var n=$('cf-name').value.trim();
if(!n){toast('名称不能为空',ER);return;}
this.disabled=true;
try{await postJSON('/api/materials/collections',{action:'create',name:n,color:CF.color,confirm:true});
$('coll-form').hidden=true;await loadColls();renderCollRail();renderMaterials();toast('集合已创建',OK);
}catch(err){toast('创建失败：'+err.message,ER);}
this.disabled=false;
});
function collMenu(e,mid){
e.preventDefault();
ctxShow(e,(state.colls.length?state.colls.map(function(c){
return '<button class="cp-item" data-ctg="'+esc(c.id)+'|'+esc(mid)+'">'+((state.collOf[mid]||[]).indexOf(c.id)>=0?'✓ ':'＋ ')+esc(c.name)+' · '+c.active_member_count+'</button>';
}).join(''):'<span class="cp-item">先在左轨「+ 新建」建集合</span>'));
}
async function collTg(cid,mid){
var has=(state.collOf[mid]||[]).indexOf(cid)>=0,c=null;
state.colls.forEach(function(x){if(x.id===cid)c=x;});
if(!c)return;
try{
await postJSON('/api/materials/collections',has?{action:'remove',id:cid,member_ids:[mid],confirm:true}:{action:'add',id:cid,member_ids:(c.member_ids||[]).concat([mid]),confirm:true});
await loadColls();renderCollRail();renderMaterials();
}catch(err){toast('集合更新失败：'+err.message,ER);}
}
$('ctx').addEventListener('click',function(e){
var t=e.target.closest('[data-ctg]');
if(t){ctxHide();var kv=t.getAttribute('data-ctg').split('|');collTg(kv[0],kv[1]);}
});
}
bindMaterial();
