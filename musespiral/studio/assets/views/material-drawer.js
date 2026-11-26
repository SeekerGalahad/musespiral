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
var m=d.material||((d.id&&d.type)?d:{}),casc=d.cascade||[];
var o=m.origin||{},tri=m.triage||{},p=m.payload||{},meta=p.meta||{},h='',i,b=m.score_breakdown||{};
if(b.depth!=null)for(i=0;i<4;i++)h+='<div class="pbar"><span class="pbar-name">'+DD[i][0]+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+(b[DD[i][1]]||0)/DD[i][2]*100+'%"></span></span><span class="pbar-n">'+(b[DD[i][1]]||0)+'/'+DD[i][2]+'</span></div>';
var prev=p.text||meta.summary||o.uri||'（无正文预览）';
var media='',full='/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=full';
var isLocalbook=String(o.uri||'').indexOf('localbook:')===0;
var txtView=(m.type==='text'||m.type==='crawled')&&(p.text||p.local_path||isLocalbook);
if(m.type==='image')media='<img class="drawer-media" src="'+esc(full)+'"><button class="btn" data-mlight="'+esc(m.id)+'">灯箱查看</button>';
else if(m.type==='video'&&p.local_path){
var vp=meta.poster?('/media/'+meta.poster):'';
media='<video class="drawer-media" controls preload="metadata"'+(vp?' poster="'+esc(vp)+'"':'')+' src="'+esc('/media/'+p.local_path)+'"></video>'+
(meta.duration?'<p class="drawer-meta"><span class="tag">⏱ '+fmtDur(meta.duration)+(meta.bytes?' · '+hbytes(meta.bytes):'')+'</span></p>':'')+
'<a class="btn" href="'+esc('/media/'+p.local_path)+'" target="_blank" rel="noopener">新窗口打开</a>';}
else if(m.type==='video'){var vu=p.url||o.uri;media='<video class="drawer-media" controls preload="metadata" src="'+esc(vu)+'"></video><a class="btn" href="'+esc(vu)+'" target="_blank" rel="noopener">新窗口打开</a>';}
$('mat-drawer-title').innerHTML=(tri.starred?'★ ':'')+esc(m.title||o.uri||m.id);
$('mat-drawer-body').innerHTML=
'<div class="chips" style="margin:0 0 var(--space-sm)"><button class="chip" data-miit="0">内容</button><button class="chip" id="mi-t1" data-miit="1">打磨历史</button></div>'+
'<div id="mi-view">'+
(media||(txtView?'<div id="mat-txt-view">'+matTextSkeleton(m,p,isLocalbook)+'</div>':'<pre class="drawer-preview">'+esc(prev)+'</pre>'))+
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
'<div class="action-row mat-acts">'+(m.type==='text'?'<button class="btn" data-mdraft="'+esc(o.uri)+'"'+(m.drafted?' disabled':'')+'>生成骨架</button>':'')+((meta.url||p.url)?'<a class="btn" href="'+esc(meta.url||p.url)+'" target="_blank" rel="noopener">查看原文</a>':'')+'<button class="btn" data-mcolladd="'+esc(m.id)+'">+ 集合</button><button class="btn" data-mcm="'+esc(m.id)+'">加入创作</button><button class="btn" data-mstar="'+esc(m.id)+'">'+(tri.starred?'取消收藏':'收藏')+'</button><button class="btn" data-mread="'+esc(m.id)+'">'+(tri.read?'已读':'未读')+'</button><button class="btn" data-moff="'+esc(m.id)+'">'+(tri.enabled?'禁用':'启用')+'</button><button class="btn" data-mai="'+esc(m.id)+'">AI 摘要</button><button class="btn mat-del" data-mdel="'+esc(m.id)+'">删除</button></div>'+
'';
if(txtView)matPaintText(m,p,isLocalbook);
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
async function matToCompose(mid,e){
if(e)e.preventDefault();
await loadCompose(true);
var L=(state.cs||[]).filter(function(s2){return s2.status==='composing';});
if(!L.length){location.hash='#compose';toast('新建会话后，在素材池「我的素材」里找到它',WN);return;}
if(L.length===1)return matComposeAdd(L[0].slug,mid);
state.mcpMid=mid;
var sel=$('mcp-sel');sel.innerHTML=L.map(function(s2){return '<option value="'+esc(s2.slug)+'">'+esc(s2.title||s2.slug)+'</option>';}).join('');
openDlg('mat-cm-pick','#mcp-sel');
}
async function matComposeAdd(slug,mid){
try{var d=await postJSON('/api/compose/add',{slug:slug,material_id:mid,confirm:true});
state.cslug=slug;location.hash='#compose?s='+encodeURIComponent(slug);
toast('已入创作池：'+(d.path||mid)+'（在「我的素材」tab 可见）',OK);}
catch(err){toast('加入创作失败：'+err.message,ER);}
}
$('mcp-ok')&&$('mcp-ok').addEventListener('click',function(){
var v=$('mcp-sel').value;if(!v)return;
var mid=state.mcpMid;closeDlg('mat-cm-pick');matComposeAdd(v,mid);
});
$('mcp-x')&&$('mcp-x').addEventListener('click',function(){closeDlg('mat-cm-pick');});
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
var MAT_TXT_CAP=30000;
function matLooksMd(s){return /^#{1,3}\s|\*\*|^>\s|```|^\|/m.test(s||'');}
function matTextSkeleton(m,p,isLocalbook){
var chars=p.chars||String(p.text||'').length||(isLocalbook?(p.excerpt||'').length:0);
var big=chars>MAT_TXT_CAP;
var lb=isLocalbook&&!p.text;
return '<p class="action-hint" id="mat-txt-loading">'+(lb?'正在准备本章内容…':(big?'正在渲染（截取前 3 万字，共 '+chars.toLocaleString()+' 字）…':'正在渲染 '+chars.toLocaleString()+' 字…'))+'</p>';
}
function matPaintText(m,p,isLocalbook){
var host=document.getElementById('mat-txt-view');
if(!host)return;
var chars=p.chars||String(p.text||'').length||(isLocalbook?(p.excerpt||'').length:0);
var big=chars>MAT_TXT_CAP,lb=isLocalbook&&!p.text;
var isMd=matLooksMd(p.text||'')||matLooksMd(p.excerpt||'')||!p.text; // 引用型正文在文件（现读必渲染）
var chips='<div class="chips" style="margin:0 0 8px">'+
(lb?'':'<span class="chip">'+(chars?chars.toLocaleString()+' 字':'—')+'</span>')+
(big?'<span class="chip" style="color:var(--amber);border-color:rgba(217,119,6,.4)">已截取前 3 万字</span>':'')+
(lb?'<span class="chip">本地书 · 现取</span>':'')+
(isMd?'<button class="chip" id="mat-txt-md">渲染视图</button><button class="chip" id="mat-txt-raw">原文</button>':'')+
'</div>';
if(lb){
host.innerHTML=chips+'<pre class="drawer-preview">'+esc(p.excerpt||'（本章摘录为空）')+'</pre>'+
'<div style="text-align:center;padding:10px 0"><button class="btn btn-primary" id="mat-lb-full" data-id="'+esc(m.id)+'">读取本章全文（现取'+(chars?' '+chars.toLocaleString()+' 字':'')+'）</button><p class="action-hint" style="margin-top:4px">本地书正文永远读磁盘原文件，按需现取不复制</p></div>';
matBindLbFull(m);
return;
}
host.innerHTML=chips+'<div id="mat-txt-mdv" class="mat-mdframe"><p class="action-hint">渲染中…</p></div><pre class="drawer-preview" id="mat-txt-pre"'+(isMd?' hidden':'')+'>'+esc(String(p.text||'').slice(0,big?MAT_TXT_CAP:undefined))+((big)?'\n\n（……已截取，点下方加载全部……）':'')+'</pre>'+
(big?'<div style="text-align:center;padding:9px 0 3px"><button class="btn btn-primary" id="mat-txt-all">加载全部（共 '+chars.toLocaleString()+' 字）</button></div>':'');
if(isMd){matLoadRender(m,false);matBindView();}
matBindAll(m,big);
}
function matBindLbFull(m){
var b=document.getElementById('mat-lb-full');
if(!b)return;
b.addEventListener('click',function(){
b.disabled=true;b.textContent='正在读取本章…';
getJSON('/api/materials/render?id='+encodeURIComponent(m.id)).then(function(d){
if(!d||d.ok===false){b.textContent='读取失败：'+esc((d||{}).error||'服务未响应');b.disabled=false;return;}
var host=document.getElementById('mat-txt-view');
var n=(d.chars||0).toLocaleString();
host.innerHTML='<div class="chips" style="margin:0 0 8px"><span class="chip">'+n+' 字</span>'+(d.truncated?'<span class="chip" style="color:var(--amber)">已截取</span>':'')+'</div>'+
'<div class="mat-mdframe" id="mat-txt-mdv"></div>';
var f=document.getElementById('mat-txt-mdv');
if(f)f.innerHTML='<iframe sandbox style="width:100%;height:360px;border:0;display:block" srcdoc="'+esc(d.html)+'"></iframe>';
}).catch(function(err){b.textContent='读取失败：'+esc(err.message);b.disabled=false;});
});
}
function matLoadRender(m,isFull){
var v=document.getElementById('mat-txt-mdv');
if(!v)return;
v.innerHTML='<p class="action-hint">渲染中…</p>';
getJSON('/api/materials/render?id='+encodeURIComponent(m.id)+(isFull?'&full=1':'')).then(function(d){
if(!d||d.ok===false){v.innerHTML='<p class="action-hint">渲染失败：'+esc((d||{}).error||'服务未响应')+'</p>';return;}
v.innerHTML='<iframe sandbox style="width:100%;height:360px;border:0;display:block" srcdoc="'+esc(d.html)+'"></iframe>';
}).catch(function(err){v.innerHTML='<p class="action-hint">渲染失败：'+esc(err.message)+'</p>';});
}
function matBindView(){
var md=document.getElementById('mat-txt-md'),pre=document.getElementById('mat-txt-pre'),mdv=document.getElementById('mat-txt-mdv');
if(!md||!pre||!mdv)return;
md.addEventListener('click',function(){md.classList.add('is-active');document.getElementById('mat-txt-raw').classList.remove('is-active');mdv.hidden=false;pre.hidden=true;});
document.getElementById('mat-txt-raw').addEventListener('click',function(){
this.classList.add('is-active');md.classList.remove('is-active');mdv.hidden=true;pre.hidden=false;
// 引用型素材（kb/localbook）payload 无 text——原文视图经 file 端点现读
if(!pre.textContent.trim()||pre.textContent.indexOf('已截取')>=0){
var mid=(state.mdrawerData&&state.mdrawerData.id)||(state.mdrawerData&&state.mdrawerData.material&&state.mdrawerData.material.id)||'';
getJSON('/api/materials/render?id='+encodeURIComponent(mid)+'&full=1').then(function(d){
if(d&&d.html){pre.textContent='（正文见渲染视图；原文长度 '+(d.chars||0).toLocaleString()+' 字）\n'+(d.plain||'');}
}).catch(function(){});
}
});
}
function matBindAll(m,big){
var b=document.getElementById('mat-txt-all');
if(!b)return;
b.addEventListener('click',function(){
b.disabled=true;b.textContent='加载中…';
var pre=document.getElementById('mat-txt-pre');
if(pre){var p=(state.mdrawerData&&state.mdrawerData.material&&state.mdrawerData.material.payload)||{};pre.textContent=String(p.text||'');pre.hidden=pre.hidden;}
matLoadRender(m,true);
b.textContent='已加载全部';
});
}
