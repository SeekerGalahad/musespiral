function matPreview(m){
var p=m.payload||{},o=m.origin||{},t=p.text||(o&&o.title)||'';
return String(t).replace(/\s+/g,' ').slice(0,120);
}
function hbytes(n){n=+n||0;if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';return(n/1048576).toFixed(1)+' MB';}
var MEMPTY_T=$('mat-empty-title').textContent,MEMPTY_X=$('mat-empty-text').textContent;
function matRow(m){
var s=m.score,t=s==null?-1:s,lv=t>=80?'hi':t>=60?'mid':'lo',b=m.score_breakdown||{},h='',g='',i,tri=m.triage||{},o=m.origin||{},p=m.payload||{},meta=p.meta||{},bk=meta.book,st=tri.starred?'取消收藏':'收藏',tg=tri.tags||[];
if(b.depth!=null)for(i=0;i<4;i++){h+='<div class="pbar"><span class="pbar-name">'+DD[i][0]+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+(b[DD[i][1]]||0)/DD[i][2]*100+'%"></span></span><span class="pbar-n">'+(b[DD[i][1]]||0)+'/'+DD[i][2]+'</span></div>';g+=(g?'·':'')+DD[i][0]+(b[DD[i][1]]||0)+'/'+DD[i][2];}
return '<details class="mat-row"'+(state.mdens==='full'?' open':'')+' data-mid="'+esc(m.id)+'">'+
'<summary class="mat-row-sum" title="展开或收起评分与详情">'+
'<button class="btn btn-ghost btn-icon dstar'+(tri.starred?' on':'')+'" data-mstar="'+esc(m.id)+'" title="'+st+'" aria-label="'+st+'">'+I('star')+'</button>'+
(m.type==='image'?'<img class="mat-thumb" loading="lazy" data-mlight="'+esc(m.id)+'" src="'+esc('/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=thumb')+'" alt="" tabindex="0" role="button" aria-label="查看大图" onerror="this.style.visibility=\'hidden\';this.nextElementSibling.style.display=\'inline-flex\'">':'')+
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
'<div class="action-row mat-acts">'+(m.type==='text'?'<button class="btn" data-mdraft="'+esc(o.uri)+'"'+(m.drafted?' disabled':'')+'>生成骨架</button>':'')+((meta.url||p.url)?'<a class="btn" href="'+esc(meta.url||p.url)+'" target="_blank" rel="noopener">查看原文</a>':'')+'<button class="btn" data-mcolladd="'+esc(m.id)+'">+ 集合</button><button class="btn" data-mcm="'+esc(m.id)+'">加入创作</button><button class="btn" data-mdetail="'+esc(m.id)+'">详情</button><button class="btn" data-moff="'+esc(m.id)+'">'+(tri.enabled?'禁用':'启用')+'</button><button class="btn" data-mai="'+esc(m.id)+'">AI 摘要</button><button class="btn mat-del" data-mdel="'+esc(m.id)+'">删除</button></div>'+
'<div class="tag-row" data-tagrow="'+esc(m.id)+'">'+tg.map(function(t2){return '<span class="chip-tag" data-mtagf="'+esc(t2)+'" title="点击按此标签过滤">'+esc(t2)+'<i data-mtagdel="'+esc(m.id)+'|'+esc(t2)+'" title="移除标签" tabindex="0" role="button" aria-label="移除标签 '+esc(t2)+'">×</i></span>';}).join('')+'<input class="input tag-in" maxlength="24" list="tag-dl" placeholder="输入标签，回车添加" data-mtagin="'+esc(m.id)+'"></div>'+
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
var __matParts={};
function matPart(name){
if(!__matParts[name])__matParts[name]=__loadScript('assets/views/material-'+name+'.js').catch(function(err){delete __matParts[name];throw err;});
return __matParts[name];
}
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
if(mid){await matPart('drawer');await openMatDrawer(mid);}
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
var el=e.target.closest('[data-mrelated],[data-mstar],[data-mdraft],[data-mread],[data-moff],[data-mai],[data-mdel],[data-mdetail],[data-mdrow],[data-mlight],[data-mcolladd],[data-mcm],[data-mtagdel],[data-mtagf],[data-mmore],[data-tsg],[data-tso],[data-miit],[data-morig]');
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
if(el.hasAttribute('data-mlight'))return matPart('media').then(function(){lbOpen(el.getAttribute('data-mlight'));});
if(el.hasAttribute('data-mcm'))return matPart('drawer').then(function(){matToCompose(el.getAttribute('data-mcm'),e);});
if(el.hasAttribute('data-mmore'))return ctxShow(e,matMenu(el.getAttribute('data-mmore')));
if(el.hasAttribute('data-mcolladd'))return matPart('drawer').then(function(){collMenu(e,el.getAttribute('data-mcolladd'));});
if(el.hasAttribute('data-mtagf')){var tv=el.getAttribute('data-mtagf');state.mtag=(tv===state.mtag)?'':tv;state.mtab='all';return matRefetch();}
if(el.hasAttribute('data-mtagdel')){var kv=el.getAttribute('data-mtagdel').split('|'),m0=matById(kv[0]);return tagSet(kv[0],(((m0||{}).triage||{}).tags||[]).filter(function(t){return t!==kv[1];}));}
if(el.hasAttribute('data-mdraft')){if(!el.disabled)handleMatDraft(el);return;}
var id=el.getAttribute('data-mstar')||el.getAttribute('data-mread')||el.getAttribute('data-moff')||el.getAttribute('data-mai')||el.getAttribute('data-mdel')||el.getAttribute('data-mdetail')||el.getAttribute('data-mdrow'),k=el.hasAttribute('data-mstar')?'starred':el.hasAttribute('data-mread')?'read':el.hasAttribute('data-moff')?'enabled':'',m=matById(id);
if(k)return matTriage(id,(function(){var c={};c[k]=!(m&&(m.triage||{})[k]);return c;})());
if(el.hasAttribute('data-mai'))return matPart('drawer').then(function(){matAI(id);});
if((el.hasAttribute('data-mdetail')||el.hasAttribute('data-mdrow'))&&(e.metaKey||e.ctrlKey))return matPart('media').then(function(){selToggle(id);});
if(el.hasAttribute('data-mdetail')||el.hasAttribute('data-mdrow'))return matPart('drawer').then(function(){openMatDrawer(id);});
matDelete(id);
}
$('mat-list').addEventListener('click',matActsClick);
$('mat-drawer').addEventListener('click',matActsClick);
$('ctx').addEventListener('click',matActsClick);
$('mat-drawer-close').addEventListener('click',function(){matPart('drawer').then(closeMatDrawer);});
$('mat-gen-btn').onclick=function(){$('mat-drawer').hidden=$('syn-panel').hidden=true;$('gen-panel').hidden=false;matPart('media').then(gPaint)};
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
on2('mat-upload-btn',function(){$('mat-file').click();});
$('mat-file').addEventListener('change',function(){var f=[].slice.call(this.files);this.value='';if(f.length)matPart('media').then(function(){trayAdd(f);});});
var mDrag=0;
$('view-material').addEventListener('dragenter',function(e){if(!dropHasFiles(e))return;e.preventDefault();mDrag++;$('mat-drop').hidden=false;});
$('view-material').addEventListener('dragover',function(e){if(dropHasFiles(e))e.preventDefault();});
$('view-material').addEventListener('dragleave',function(e){if(!dropHasFiles(e))return;mDrag=Math.max(0,mDrag-1);if(!mDrag)$('mat-drop').hidden=true;});
$('view-material').addEventListener('drop',function(e){mDrag=0;$('mat-drop').hidden=true;
if(e.dataTransfer&&e.dataTransfer.files.length){e.preventDefault();var f=[].slice.call(e.dataTransfer.files);matPart('media').then(function(){trayAdd(f);});}});
document.addEventListener('paste',function(e){
if(!$('view-material').hidden&&e.clipboardData&&e.clipboardData.files.length){var f=[].slice.call(e.clipboardData.files);matPart('media').then(function(){trayAdd(f);});}
});
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
if(inp&&e.key==='Enter'){e.preventDefault();tagAdd(inp.getAttribute('data-mtagin'),inp.value);inp.value='';return;}
var kb=e.target.closest('[data-mlight],[data-mtagdel]');
if(kb&&(e.key==='Enter'||e.key===' ')){e.preventDefault();kb.click();}
});
on2('coll-new-btn',function(){matPart('drawer').then(function(){$('cf-name').value='';cfDots();openDlg('coll-form','#cf-name');});});
}
bindMaterial();
