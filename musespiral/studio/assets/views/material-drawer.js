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
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&!$('mat-drawer').hidden)closeMatDrawer();
});
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
var CF={color:'indigo'},CFC=['vermilion','indigo','jade','amber','slate'];
function cfDots(){$('cf-dots').innerHTML=CFC.map(function(c){return '<button type="button" class="cf-dot'+(CF.color===c?' on':'')+'" data-cfcol="'+c+'" style="background:var(--'+c+',var(--ink-2))" aria-label="集合颜色 '+c+'"></button>';}).join('');}
on2('cf-cancel',function(){closeDlg('coll-form');});
$('coll-form').addEventListener('click',function(e){if(e.target===this)closeDlg('coll-form');});
$('cf-dots').addEventListener('click',function(e){var d=e.target.closest('[data-cfcol]');if(d){CF.color=d.getAttribute('data-cfcol');cfDots();}});
$('cf-ok').addEventListener('click',async function(){
var n=$('cf-name').value.trim();
if(!n){toast('名称不能为空',ER);return;}
this.disabled=true;
try{await postJSON('/api/materials/collections',{action:'create',name:n,color:CF.color,confirm:true});
closeDlg('coll-form');await loadColls();renderCollRail();renderMaterials();toast('集合已创建',OK);
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
