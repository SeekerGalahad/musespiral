var CV={tpl:'vermilion-slab',pr:[],svg:'',h:0,q:0,art:''};
function cvP(){return{title:$('cv-title').value,hook:$('cv-hook').value,font_size:$('cv-fs').value,align:$('cv-al').value};}
function cvGo(){
if(!CV.svg)$('cv-prev').innerHTML='<div class="cv-skel"></div>';
var n=++CV.q;
postJSON('/api/visual/render',{confirm:true,template:CV.tpl,params:cvP(),spec:'mp',article:CV.art||null}).then(function(d){
if(n!==CV.q)return;
CV.svg=d.svg;$('cv-prev').innerHTML=d.svg;
if(!$('cv-title').value){$('cv-title').value=(d.params&&d.params.title)||'';$('cv-hook').value=(d.params&&d.params.hook)||'';}
}).catch(function(err){if(CV.svg)$('cv-prev').innerHTML=CV.svg;toast('预览失败：'+err.message,ER);});
}
function cvEdit(){clearTimeout(CV.h);CV.h=setTimeout(cvGo,300);}
function cvChips(){
var h=[['vermilion-slab','朱砂拓'],['indigo-slab','青黛拓']].map(function(t){return '<button class="chip'+(CV.tpl===t[0]?' is-active':'')+'" data-cvt="'+t[0]+'">'+t[1]+'</button>';}).join(''),m=(CV.pr||[]).filter(function(p){return p.kind==='cover'&&p.template;});
if(m.length)h+='<span class="cv-grp">我的模板</span>'+m.map(function(p){return '<button class="chip" data-cvt="'+esc(p.template)+'" title="预设 '+esc(p.id)+'">'+esc(p.label||p.id)+'</button>';}).join('');
$('cv-tpl').innerHTML=h;
}
function cvInit(path){
CV.tpl='vermilion-slab';CV.svg='';CV.q++;CV.art=path||'';
$('cv-title').value='';$('cv-hook').value='';$('cv-prev').innerHTML='';
cvChips();cvGo();
getJSON('/api/visual/presets').then(function(d){CV.pr=d.items||[];cvChips();}).catch(function(){});
}
async function cvExport(btn){
btn.disabled=true;
try{
await postJSON('/api/visual/save',{confirm:true,article:CV.art,kind:'cover',template:CV.tpl,params:cvP(),spec:'mp'});
var d=await postJSON('/api/visual/export',{confirm:true,article:CV.art,kind:'cover'});
var j=await waitForJob(d.job_id),r=j.result||{};
if(j.status==='failed'||r.ok===false)toast('导出失败：'+(r.error||j.error||'未知错误'),ER);
else toast('导出完成：'+(String(r.png||'cover.png').split('/').pop())+' '+((r.size&&r.size[0])?r.size[0]+'×'+r.size[1]:''),OK);
await refreshArticleState();
}catch(err){toast('导出失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
async function cvPreset(btn){
var id=$('cv-pid').value.trim();
if(!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(id)){toast('模板 id 须为 kebab（小写字母/数字/连字符）',ER);return;}
btn.disabled=true;
try{
await postJSON('/api/visual/preset',{confirm:true,id:id,label:$('cv-pl').value.trim()||id,template:CV.tpl,params:cvP(),kind:'cover'});
$('cv-pm').hidden=true;toast('模板已保存：'+id,OK);
CV.pr=(await getJSON('/api/visual/presets')).items||[];cvChips();
}catch(err){toast('模板保存失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
var CM_ACT={keep:'保留',adopt_edited:'改后采纳',discard:'已丢弃'};
function blockHtml(b){return mdToHtml(b.text,'');}
async function cmNew(){
var slug=$('cm-new-slug').value.trim();
if(!slug){toast('slug 不能为空',ER);return;}
try{await postJSON('/api/compose/new',{slug:slug,title:$('cm-new-title').value.trim()||slug,confirm:true});
$('cm-new-slug').value=$('cm-new-title').value='';
await loadCompose(true);toast('会话已建：'+slug,OK);
location.hash='#compose?s='+encodeURIComponent(slug);return true;}
catch(err){toast('建会话失败：'+err.message,ER);}
}
async function addCmSource(path,sections){
if(!state.cslug){toast('先建会话再入素材',ER);return;}
var body={slug:state.cslug,path:path,confirm:true};
if(sections)body.sections=sections;
try{var d=await postJSON('/api/compose/add',body);
var nm=String(d.path||path).split('/').pop();
state.cmPaths[nm]=path;
if(d.known_sections)state.cmKnown[nm]=d.known_sections;
$('cm-add-path').value='';
toast('素材已入池：'+nm,OK);
await loadComposeDetail(state.cslug,true);renderCompose();}
catch(err){toast('加入素材失败：'+err.message,ER);}
}
async function cmAddMaterial(id){
if(!state.cslug){toast('先建会话再入素材',ER);return;}
try{var d=await postJSON('/api/compose/add',{slug:state.cslug,material_id:id,confirm:true});
state.cmPaths[d.path]=d.path;
toast('素材已入池：'+d.path,OK);
await loadComposeDetail(state.cslug,true);renderCompose();}
catch(err){toast('加入素材失败：'+err.message,ER);}
}
async function removeCmSource(path){
if(!state.cslug){toast('先建会话',ER);return;}
var nm=path.split('/').pop(),ps=((state.csd||{}).sources||[]).filter(function(s){return s.path===nm;})[0],ns=(ps||{}).sections||[];
var msg='移除「'+nm+'」？'+(ns.length?'该素材已选 '+ns.length+' 章，移除后后续再生成不再取其内容。':'该素材为全文入池，移除后后续再生成不再取其内容。')+(((state.csd||{}).rounds||[]).length?'已生成轮次保留，不再回改。':'');
if(!await askConfirm({title:'移除',message:msg,confirmText:'移除'}))return;
try{var d=await postJSON('/api/compose/remove_source',{slug:state.cslug,path:path,confirm:true});
toast('已移出:'+nm+(d.sections&&d.sections.length?'（已选 '+d.sections.length+' 章）':''),OK);
await loadComposeDetail(state.cslug,true);renderCompose();}
catch(err){toast('移除失败：'+err.message,ER);}
}
var CMTAB='disc',cmMtQ='',cmMine=null;
function cmMineRows(added){
var L=(cmMine||[]).filter(function(m){
if(!(m.payload||{}).text)return false;
if(cmMtQ){var hay=(String(m.title||'')+' '+String(m.payload.text||'')).toLowerCase();if(hay.indexOf(cmMtQ.toLowerCase())<0)return false;}
return true;
}).slice(0,20);
return L.length?L.map(function(m){
var vp='material://'+m.id;
return '<div class="cm-pool-row"><span class="badge '+(m.score>=80?'pass':'queued')+'">'+(m.score==null?'—':m.score)+'</span><span class="cm-pool-t" title="'+esc(m.title||(m.origin||{}).title||m.id)+'">'+esc(m.title||(m.origin||{}).title||m.id)+'</span>'+(added[vp]?'<button class="btn mat-del cm-rm" data-cm-mtrm="'+esc(m.id)+'">移除</button>':'<button class="btn" data-cm-mtadd="'+esc(m.id)+'">加入</button>')+'</div>';
}).join(''):'<p class="action-hint">'+(cmMtQ?'素材库未命中『'+esc(cmMtQ)+'』':'素材库暂无带正文的素材——先在素材库采集，或 ⌘⇧I 记一条灵感。')+'</p>';
}
function cmMinePaint(){
var host=$('cm-mine-list');
if(host)host.innerHTML=cmMineRows(cmAddedMap());
}
function cmAddedMap(){
var added={};
((state.csd&&state.csd.sources)||[]).forEach(function(s){added[s.path]=1;});
return added;
}
function cmMineLoad(){
getJSON('/api/materials?limit=200').then(function(d){cmMine=Array.isArray(d.items)?d.items:[];renderCmPool();}).catch(function(){cmMine=[];renderCmPool();});
}
function renderCmPool(){
var added=cmAddedMap(),cand;
cand=(state.discover||[]).filter(function(c){return (c.score||0)>=80;}).slice(0,6);
$('cm-pool').innerHTML=
'<div class="chips" style="margin-bottom:8px"><button class="chip'+(CMTAB==='disc'?' is-active':'')+'" data-cmt="disc">发现候选</button><button class="chip'+(CMTAB==='mine'?' is-active':'')+'" data-cmt="mine">✦ 我的素材</button></div>'+
(CMTAB==='mine'
?'<div class="pub-toolbar" style="margin-bottom:8px"><input id="cm-mt-q" class="input" placeholder="搜我的素材（标题/正文）…" value="'+esc(cmMtQ)+'"></div><div id="cm-mine-list">'+cmMineRows(added)+'</div>'
:(cand.length?cand.map(function(c){
var nm=String(c.path).split('/').pop();
return '<div class="cm-pool-row"><span class="badge '+(c.score>=90?'pass':'warn')+'">'+c.score+'</span><span class="cm-pool-t" title="'+esc(c.path)+'">'+esc(c.title||nm)+'</span>'+(added[nm]?'<button class="btn mat-del cm-rm" data-cm-rm="'+esc(c.path)+'">移除</button>':'<button class="btn" data-cm-add="'+esc(c.path)+'">加入</button>')+'</div>';
}).join(''):'<p class="action-hint">发现引擎暂无 ≥80 候选——用下方手动添加，或先在素材页刷新采集。</p>'));
}
function renderCmSources(){
var ss=(state.csd&&state.csd.sources)||[];
$('cm-sources').innerHTML=ss.length?ss.map(function(s){
var known=state.cmKnown[s.path],path=state.cmPaths[s.path],secs;
if(known&&path){
secs='<div class="cm-secs">'+known.map(function(k){
return '<label class="cm-sec"><input type="checkbox" data-cm-sec="'+esc(path)+'" value="'+esc(k)+'"'+(s.sections.indexOf(k)>=0?' checked':'')+'>'+esc(k)+'</label>';
}).join('')+'<span class="muted">已选 '+s.sections.length+'/'+known.length+' 章'+(s.sections.length?'':'（未勾选=全文，生成时走预算节选）')+'</span></div>';
}else{
secs='<p class="action-hint">'+(s.sections.length?'已选 '+s.sections.length+' 章：'+esc(s.sections.join('、')):'全文（未勾选章节，生成时走预算节选）')+(path?'':'；重新加入素材可勾选章节')+'</p>';
}
return '<div class="cm-src"><div class="cm-src-n"><span class="tag accent">'+esc(s.path)+'</span></div>'+secs+'</div>';
}).join(''):'<p class="action-hint">尚无素材——从上方候选池或手动路径加入。</p>';
}
function renderCmBudget(){
var n=((state.csd&&state.csd.sources)||[]).length;
$('cm-budget').innerHTML='<p class="action-hint">预算仪表：'+n+' 篇素材 × 2000 字/素材，会话总预算 8000 字（超限自动节选并标注）</p><div class="pbar-track"><span class="pbar-fill" style="width:'+Math.min(100,Math.round(n*2000/8000*100))+'%"></span></div>';
}
function cmRound(n){var R=(state.csd&&state.csd.rounds)||[],i;for(i=0;i<R.length;i++)if(R[i].n===n)return R[i];return null;}
function cmPrevIds(r){
var ids={},prev=r.n>1?cmRound(r.n-1):null;
(prev?prev.blocks:(state.csd.draft||[])).forEach(function(b){ids[b.id]=1;});
return ids;
}
function cmWorkBlocks(r){
if(state.cmWork&&state.cmWork.slug===state.cslug&&state.cmWork.round===state.cround)return state.cmWork.list;
state.cmWork={slug:state.cslug,round:state.cround,locN:0,seq:0,list:(r.blocks||[]).map(function(b){return {id:b.id,kind:b.kind,text:b.text};})};
return state.cmWork.list;
}
function cmBlockById(id){
var w=state.cmWork,i=cmIdx(id);
if(i>=0)return w.list[i];
var r=cmRound(state.cround),L=r?(r.blocks||[]):[];
for(i=0;i<L.length;i++)if(L[i].id===id)return L[i];
return null;
}
function cmIdx(id){
var w=state.cmWork;
if(!(w&&w.round===state.cround))return -1;
for(var i=0;i<w.list.length;i++)if(w.list[i].id===id)return i;
return -1;
}
function cmLocId(){state.cmWork.seq+=1;return 'loc-'+state.cmWork.seq;}
function cmIns(id,after){
var w=state.cmWork,i=cmIdx(id);
if(i<0)return;
w.list.splice(after?i+1:i,0,{id:cmLocId(),kind:'para',text:'',loc:1});
w.locN++;renderCmBlocks();
}
function cmSplit(id){
var w=state.cmWork,i=cmIdx(id),b=i>=0?w.list[i]:null,t,cut;
if(!b)return;
t=b.text||'';
if(t.length<10){toast('正文过短',WN);return;}
cut=t.indexOf('\n\n',Math.floor(t.length/3));
if(cut<0)cut=Math.floor(t.length/2);
b.text=t.slice(0,cut);
w.list.splice(i+1,0,{id:cmLocId(),kind:b.kind,text:t.slice(cut).replace(/^\n+/,''),loc:1});
w.locN++;renderCmBlocks();
cmDecide(id,'adopt_edited',{text:b.text});
}
function cmMerge(id){
var w=state.cmWork,i=cmIdx(id),b=i>=0?w.list[i]:null,n,nl;
if(!b)return;
n=w.list[i+1];
if(!n){toast('没有下块可合并',WN);return;}
b.text=(b.text||'')+((b.text||'')&&n.text?'\n\n':'')+(n.text||'');
nl=n.loc;
w.list.splice(i+1,1);
if(nl)w.locN--;
renderCmBlocks();
cmDecide(id,'adopt_edited',{text:b.text});
if(!nl)cmDecide(n.id,'discard',{direction:'已并入上块'});
}
function cmDelLoc(id){
var w=state.cmWork,i=cmIdx(id);
if(i<0||!w.list[i].loc)return;
w.list.splice(i,1);w.locN--;renderCmBlocks();
}
function cmEdit(id){
var b=cmBlockById(id),host=b&&cmBlockEl(b.id);
if(!b||!host)return;
host=host.querySelector('.cm-block-body');
host.innerHTML='<textarea class="input cm-edit-area" rows="5"></textarea><div class="action-row" style="margin-top:6px"><button class="btn btn-primary" data-cm-save="'+esc(b.id)+'">保存裁决</button><button class="btn" data-cm-cancel="1">取消</button></div>';
host.querySelector('textarea').value=b.text;
host.querySelector('textarea').focus({preventScroll:true});
}
function cmBlockRow(b,dec,inh){
var st=dec?dec.action:'',ops;
if(b.loc)ops='<button class="btn" data-cm-edit="'+esc(b.id)+'">编辑</button><button class="btn" data-cmop="rm '+esc(b.id)+'">移除</button>';
else ops='<button class="btn" data-cm-keep="'+esc(b.id)+'">保留</button><button class="btn" data-cm-edit="'+esc(b.id)+'">编辑</button><button class="btn" data-cm-drop="'+esc(b.id)+'">丢弃</button><button class="btn" data-cmop="i0 '+esc(b.id)+'">前插</button><button class="btn" data-cmop="i1 '+esc(b.id)+'">后插</button><button class="btn" data-cmop="sp '+esc(b.id)+'">拆分</button><button class="btn" data-cmop="mg '+esc(b.id)+'">合并</button>';
return '<div class="cm-block '+(inh?'inh':'new')+(st?' st-'+st:'')+'" data-cm-block="'+esc(b.id)+'">'+
'<div class="cm-block-head"><code class="platform-code">'+esc(b.id)+'</code>'+
(b.loc?'<span class="badge warn">本地块</span>':(inh?'<span class="badge running">锚继承</span>':'<span class="badge queued">AI 新块</span>'))+
(b.kind!=='para'?'<span class="tag">'+esc(b.kind)+'</span>':'')+
(st?'<span class="badge '+(st==='keep'?'pass':st==='adopt_edited'?'warn':'fail')+'">'+CM_ACT[st]+(st==='discard'&&dec.direction?'·'+esc(dec.direction):'')+Z:'')+
'<span class="cm-block-ops">'+ops+'</span></div>'+
'<div class="cm-block-body">'+blockHtml(b)+'</div></div>';
}
function renderCmBlocks(){
var r=cmRound(state.cround),dm={},prev,bl;
$('cm-round-title').textContent=r?('第 '+r.n+' 轮 AI 草稿 · '+r.blocks.length+' 块'):'尚未生成';
$('cm-round-meta').textContent=r?('已裁决 '+(r.decisions||[]).length+'/'+r.blocks.length):'';
if(!r){state.cmWork=null;$('cm-blocks').innerHTML='';$('cm-blocks-empty').hidden=false;return;}
bl=cmWorkBlocks(r);
prev=cmPrevIds(r);
(r.decisions||[]).forEach(function(d){dm[d.block]=d;});
$('cm-blocks').innerHTML=(state.cmWork.locN?'<p class="action-hint">本地块 '+state.cmWork.locN+' 个不随入稿——请并入正式块「保存裁决」。</p>':'')+bl.map(function(b){return cmBlockRow(b,dm[b.id],prev[b.id]);}).join('');
$('cm-blocks-empty').hidden=true;
}
function renderCmRounds(){
var R=(state.csd&&state.csd.rounds)||[];
if(!state.cround||!cmRound(state.cround))state.cround=R.length?R[R.length-1].n:0;
$('cm-rounds').innerHTML=R.map(function(r){
return '<button class="chip'+(r.n===state.cround?' is-active':'')+'" data-cm-round="'+r.n+'">第 '+r.n+' 轮<span class="chip-count">'+(r.decisions||[]).length+'/'+r.blocks.length+'</span></button>';
}).join('')||'<p class="action-hint">尚无轮次</p>';
}
function renderCmDiff(){
var R=(state.csd&&state.csd.rounds)||[],out=[],last,prev,am={};
if(R.length<2){$('cm-diff').innerHTML='<p class="action-hint">'+(R.length?'首轮无可对照（再生成一轮后显示按块对照）':'生成后此处显示 N-1 ↔ N 按块对照')+'</p>';return;}
prev=R[R.length-2].blocks;last=R[R.length-1].blocks;
prev.forEach(function(b){am[b.id]=b;});
prev.forEach(function(b){if(!am[b.id]||!last.some(function(x){return x.id===b.id;}))out.push('<div class="cm-diffrow del">'+I('error')+'− '+esc(b.id)+' 上轮块未再现</div>');});
last.forEach(function(b){
if(!am[b.id])out.push('<div class="cm-diffrow add">'+I('plus')+'+ '+esc(b.id)+' 新块</div>');
else if(am[b.id].text!==b.text)out.push('<div class="cm-diffrow chg">'+I('edit')+'~ '+esc(b.id)+' 改写（锚继承）</div>');
});
$('cm-diff').innerHTML='<p class="action-title" style="font-size:13px">对照（第 '+(R.length-1)+' ↔ '+R.length+' 轮 · 按块）</p>'+(out.length?out.join(''):'<p class="action-hint">两轮块集一致</p>');
}
var CN={e:0,c:[],cs:[]},CR='<div class="action-row">',CE='</div>',CD='<details class="action-group"><summary class="health-sum">',CS='</details>';
function cnL(t,c,i){return '<button class="btn" data-cn="'+c+'"'+(i?' data-id="'+esc(i)+'"':'')+'>'+t+'</button>';}
function cnChips(d){
var k=d&&d.kind==='novel'?'novel':'article';
return '<div class="chips"><button class="chip'+(k==='novel'?' is-active':'')+'" data-cn="k:novel">📖 小说模式</button><button class="chip'+(k==='article'?' is-active':'')+'" data-cn="k:article">📝 文章模式</button></div>';
}
function cnH(d){
var c=cnChips(d),cs=CN.cs;
c+=CD+'📖 前情提要</summary>'+(CN.e
?'<textarea class="input set-area" id="cm-ns-area" rows="5"></textarea>'+CR+'<button class="btn btn-primary" data-cn="save">保存</button>'+CE
:'<p class="action-hint" id="cm-ns-text"></p>'+CR+cnL('🔄 AI 刷新','refresh')+cnL('✏️ 编辑','edit')+CE)
+'<p class="action-hint">已摘要 '+(d.novel_summary_chapters||0)+' 章 / 共 '+((d.novel||{}).chapters||0)+' 章</p>'+CS;
c+=CD+'👥 人物卡</summary>';
cs.forEach(function(x){c+='<p class="action-hint">'+esc(x.name)+(x.role?' · '+esc(x.role):'')+(x.speech_style?' · '+esc(x.speech_style):'')+cnL('×','rm',x.id)+'</p>';});
if(!cs.length)c+='<p class="action-hint">尚无人物卡</p>';
return c+CR+cnL('＋ 挂已有卡','pick')+cnL('＋ 新建人物','new')+CE+'<div id="cm-ch-pool"></div>'+CS;
}
function cnConf(){
var bl=$('cm-blocks'),h=$('cm-conf-tip'),L=CN.c;
if(!bl)return;
if(!h){bl.insertAdjacentHTML('beforebegin','<div id="cm-conf-tip" hidden></div>');h=$('cm-conf-tip');}
h.hidden=!L.length;
h.innerHTML=L.length?'<div class="action-group" style="border-left-width:4px;border-color:var(--amber);background:color-mix(in srgb,var(--amber) 8%,var(--paper-1));margin-bottom:10px"><b style="color:var(--amber);font-size:12px">'+I('warning')+' 人物卡冲突 '+L.length+' 条 · 仅提示，正文未改动</b>'+L.map(function(x){
return '<p class="action-hint">· '+esc([x.block,x.character,x.rule,x.snippet,x.message].filter(Boolean).join(' | '))+'</p>';}).join('')+CE:'';
}
function cnPaint(d){
var h=$('cm-novel'),a,t,ok=d&&d.kind==='novel';
if(!h)return;
CN.cs=ok?d.characters||[]:[];
if(!d){CN.e=0;CN.c=[];h.innerHTML='';return cnConf();}
if(!ok){CN.e=0;CN.c=[];h.innerHTML=cnChips(d);return cnConf();}
h.innerHTML=cnH(d);
a=$('cm-ns-area');t=$('cm-ns-text');
if(a){a.value=d.novel_summary||'';a.focus();}
else if(t)t.textContent=(d.novel_summary||'').trim()||'暂无提要';
cnConf();
}
function cnPost(b){b.confirm=true;b.slug=state.cslug;return postJSON('/api/compose/novel-summary',b);}
async function cnAct(cmd,id,el){
var p,h={},cs;
if(!state.cslug)return;
if(el)el.disabled=true;
try{
if(cmd==='edit'||cmd==='cancel'){CN.e=cmd==='edit'?1:0;return cnPaint(state.csd||{});}
if(cmd==='new'||cmd==='create')return cmd==='new'?cnNew():cnCreate(el);
if(cmd==='pick'){
p=$('cm-ch-pool');
if(p.innerHTML){p.innerHTML='';return;}
CN.cs.forEach(function(v){h[v.id]=1;});
cs=((await getJSON('/api/materials?limit=500')).items||[]).filter(function(x){return ((x.payload||{}).meta||{}).sub_type==='character'&&!h[x.id];});
p.innerHTML=cs.map(function(x){return '<button class="chip" data-cn="add" data-id="'+esc(x.id)+'">'+esc(x.payload.meta.name||x.id)+'</button>';}).join('');
return;
}
if(cmd==='save'){await cnPost({summary:($('cm-ns-area')||{}).value||''});CN.e=0;}
else if(cmd==='refresh')await cnPost({});
else if(cmd[0]==='k')await cnPost({action:'kind',kind:cmd.slice(2)});
else await cnPost({action:cmd==='rm'?'character_remove':'character_add',id:id});
await loadComposeDetail(state.cslug,true);renderCompose();
}catch(err){toast('失败：'+err.message,ER);}
finally{if(el)el.disabled=false;}
}
function cnNew(){
var w='',i;
if($('cm-ch-modal')){$('cm-ch-modal').hidden=false;return;}
[['name','姓名'],['role','角色'],['appearance','外貌'],['speech_style','说话风格']].forEach(function(x){
w+='<input class="input" id="cm-chf-'+x[0]+'" placeholder="'+x[1]+'">';});
$('cm-novel').insertAdjacentHTML('beforeend','<div class="modal-overlay" id="cm-ch-modal"><div class="modal"><h3 class="modal-title">新建人物</h3><div class="cv-col">'+w+'<textarea class="input set-area" id="cm-chf-key_events" rows="3" placeholder="关键事件（每行一条）"></textarea></div><div class="modal-actions"><button class="btn" data-cn="mclose">取消</button><button class="btn btn-primary" data-cn="create">建卡</button></div></div></div>');
$('cm-chf-name').focus();
}
async function cnCreate(el){
var fv=function(k){var e=$('cm-chf-'+k);return e?String(e.value||'').trim():'';},name=fv('name'),d;
if(!name)return toast('姓名不能为空',ER);
try{d=await cnPost({action:'character_create',name:name,role:fv('role'),appearance:fv('appearance'),speech_style:fv('speech_style'),key_events:fv('key_events').split('\n').map(function(s){return s.trim();}).filter(Boolean)});
if((d.material||{}).id)await cnPost({action:'character_add',id:d.material.id});
$('cm-ch-modal').hidden=true;toast('已建卡:'+name,OK);
await loadComposeDetail(state.cslug,true);renderCompose();
}catch(err){toast('建卡失败：'+err.message,ER);}
}
function renderCompose(){
var L=state.cs||[],d=state.csd,R;
var act=0;L.forEach(function(s){if(s.status==='composing')act++;});
updateNavBadge('nav-badge-compose',act);
$('cm-sessions').innerHTML=L.map(function(s){
return '<button class="chip'+(s.slug===state.cslug?' is-active':'')+'" data-cm-sel="'+esc(s.slug)+'">'+esc(s.title||s.slug)+'<span class="chip-count">'+(s.rounds||0)+' 轮</span></button>';
}).join('');
$('cm-empty').hidden=L.length>0;
$('cm-grid').hidden=!(d&&state.cslug);
if(!d){cnPaint(null);$('compose-sub').textContent=L.length?('共 '+L.length+' 个会话 · 点上方 chip 进入三栏工作区'):'尚无会话——填 slug 建一个';return;}
R=d.rounds||[];
$('compose-sub').textContent=d.title+' · '+(d.status==='composing'?'创作中':'已采纳')+(d.final_article?' · 正稿 '+d.final_article:'')+' · 素材 '+d.sources.length+' · 轮次 '+R.length;
$('cm-generate-btn').disabled=d.status!=='composing';
$('cm-adopt-btn').disabled=!(d.status==='composing'&&R.length);
if(d.final_article&&CV.art!==d.final_article)cvInit(d.final_article);
cnPaint(d);
renderCmPool();renderCmSources();renderCmBudget();renderCmRounds();renderCmBlocks();renderCmDiff();
}
async function cmDecide(bid,action,extra){
if(!state.cslug)return;
try{await postJSON('/api/compose/decide',Object.assign({slug:state.cslug,round:state.cround,block:bid,action:action,confirm:true},extra||{}));
await loadComposeDetail(state.cslug,true);renderCompose();}
catch(err){toast('裁决失败：'+err.message,ER);}
}
function cmBlockEl(id){return $('cm-blocks').querySelector('[data-cm-block="'+id+'"]');}
function cmBlocksClick(e){
var el=e.target.closest('[data-cm-keep]'),sb;
if(el){cmDecide(el.getAttribute('data-cm-keep'),'keep');return;}
el=e.target.closest('[data-cm-edit]');
if(el){cmEdit(el.getAttribute('data-cm-edit'));return;}
el=e.target.closest('[data-cm-save]');
if(el){
sb=cmBlockById(el.getAttribute('data-cm-save'));
if(sb&&sb.loc){sb.text=el.parentNode.parentNode.querySelector('textarea').value;renderCmBlocks();toast('本地块已暂存（不随采纳入稿）',WN);}
else cmDecide(el.getAttribute('data-cm-save'),'adopt_edited',{text:el.parentNode.parentNode.querySelector('textarea').value});
return;
}
if(e.target.closest('[data-cm-cancel]')){renderCmBlocks();return;}
el=e.target.closest('[data-cm-drop]');
if(el){
var host=cmBlockEl(el.getAttribute('data-cm-drop'));
if(host&&!host.querySelector('.cm-drop-row')){
var row=document.createElement('div');
row.className='cm-drop-row';
row.innerHTML='<input class="input" placeholder="丢弃方向备注（喂下轮 prompt）：为何弃/往哪改…"><button class="btn btn-danger" data-cm-dropok="'+esc(el.getAttribute('data-cm-drop'))+'">确认丢弃</button>';
host.querySelector('.cm-block-head').appendChild(row);
row.querySelector('input').focus({preventScroll:true});
}
return;
}
el=e.target.closest('[data-cm-dropok]');
if(el){cmDecide(el.getAttribute('data-cm-dropok'),'discard',{direction:el.parentNode.querySelector('input').value.trim()});return;}
el=e.target.closest('[data-cmop]');
if(el){
var pv=el.getAttribute('data-cmop').split(' '),bid=pv[1];
if(pv[0]==='i0')cmIns(bid,0);
else if(pv[0]==='i1')cmIns(bid,1);
else if(pv[0]==='sp')cmSplit(bid);
else if(pv[0]==='mg')cmMerge(bid);
else cmDelLoc(bid);
}
}
async function cmGenerate(btn){
if(!state.cslug)return;
btn.disabled=true;
try{var d=await postJSON('/api/compose/generate',{slug:state.cslug,confirm:true});
toast(QUEUED,OK);var j=await trackJob(d.job_id);
await loadComposeDetail(state.cslug,true);
var R=(state.csd&&state.csd.rounds)||[];
CN.c=((j&&j.result)||{}).conflicts||[];
state.cround=R.length?R[R.length-1].n:0;
renderCompose();}
catch(err){toast('再生成提交失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
async function cmAdopt(){
if(!state.cslug)return;
var ok=await askConfirm({title:'采纳入稿',message:'本轮裁决合并入 draft.md，正稿 '+state.cslug+'.md 入文章队列（自动产出 habits 训练对）；采纳后会话封版不可再生成。',confirmText:'采纳入稿'});
if(!ok)return;
try{var d=await postJSON('/api/compose/adopt',{slug:state.cslug,confirm:true});
toast('已入稿：'+(d.article||state.cslug+'.md')+'（habits 学习 '+(d.habits_learned||0)+' 对）',OK);
await loadCompose(true);await loadComposeDetail(state.cslug,true);renderCompose();await loadArticles(true);viewRepaint('publish');}
catch(err){toast('采纳失败：'+err.message,ER);}
}
VAPI.compose={enter:async function(route){
var cs=route.params.get('s')||route.params.get('slug')||state.cslug||'';
var cr=route.params.get('r');
await Promise.all([loadCompose(true),loadDiscover()]);
await loadComposeDetail(cs,true);
if(cr&&+cr>0)state.cround=+cr;
renderCompose();
}};
function bindCompose(){
$('cv-tpl').addEventListener('click',function(e){var b=e.target.closest('[data-cvt]');if(!b||b.getAttribute('data-cvt')===CV.tpl)return;CV.tpl=b.getAttribute('data-cvt');cvChips();cvEdit();});
$('cv-title').addEventListener('input',cvEdit);
$('cv-hook').addEventListener('input',cvEdit);
$('cv-fs').addEventListener('change',cvEdit);
$('cv-al').addEventListener('change',cvEdit);
$('cv-x').addEventListener('click',function(){cvExport(this);});
$('cv-pt').addEventListener('click',function(){$('cv-pid').value='';$('cv-pl').value='';$('cv-pm').hidden=false;$('cv-pid').focus();});
$('cv-pm-x').addEventListener('click',function(){$('cv-pm').hidden=true;});
$('cv-pm-ok').addEventListener('click',function(){cvPreset(this);});
$('cm-blocks').addEventListener('contextmenu',function(e){
var b=e.target.closest('[data-cm-block]');
if(!b)return;
e.preventDefault();
var id=b.getAttribute('data-cm-block'),w=cmBlockById(id);
ctxShow(e,w&&w.loc?'<button class="cp-item" data-cm-edit="'+id+'">编辑</button><button class="cp-item dg" data-cmop="rm '+id+'">移除</button>':'<button class="cp-item" data-cm-keep="'+id+'">保留</button><button class="cp-item" data-cm-edit="'+id+'">编辑</button><button class="cp-item" data-cm-drop="'+id+'">丢弃</button>');
});
$('cm-new-btn').addEventListener('click',cmNew);
$('cm-new-slug').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'){e.preventDefault();$('cm-new-title').focus();}
});
$('cm-new-title').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'){e.preventDefault();cmNew().then(function(ok){if(ok)$('cm-blocks').focus();});}
});
$('cm-add-btn').addEventListener('click',function(){
var p=$('cm-add-path').value.trim();
if(!p){toast('请输入素材路径',ER);return;}
addCmSource(p);
});
$('cm-sessions').addEventListener('click',function(e){
var c=e.target.closest('[data-cm-sel]');
if(c)location.hash='#compose?s='+encodeURIComponent(c.getAttribute('data-cm-sel'));
});
$('cm-pool').addEventListener('click',function(e){
var t=e.target.closest('[data-cmt]');
if(t){CMTAB=t.getAttribute('data-cmt');if(CMTAB==='mine'&&!cmMine)cmMineLoad();else renderCmPool();return;}
var a=e.target.closest('[data-cm-mtadd]');
if(a&&!a.disabled){cmAddMaterial(a.getAttribute('data-cm-mtadd'));return;}
var r2=e.target.closest('[data-cm-mtrm]');
if(r2){removeCmSource('material://'+r2.getAttribute('data-cm-mtrm'));return;}
var r=e.target.closest('[data-cm-rm]');
if(r){removeCmSource(r.getAttribute('data-cm-rm'));return;}
var b=e.target.closest('[data-cm-add]');
if(b&&!b.disabled)addCmSource(b.getAttribute('data-cm-add'));
});
$('cm-pool').addEventListener('input',function(e){
if(e.target.id==='cm-mt-q'){cmMtQ=e.target.value.trim();cmMinePaint();}
});
$('cm-sources').addEventListener('change',function(e){
var t=e.target,path=t.getAttribute&&t.getAttribute('data-cm-sec');
if(!path||t.type!=='checkbox')return;
var boxes=[].slice.call($('cm-sources').querySelectorAll('input[type=checkbox]')).filter(function(x){return x.checked&&x.getAttribute('data-cm-sec')===path;});
addCmSource(path,boxes.map(function(x){return x.value;}));
});
$('cm-blocks').addEventListener('click',cmBlocksClick);
$('ctx').addEventListener('click',cmBlocksClick);
$('cm-rounds').addEventListener('click',function(e){
var c=e.target.closest('[data-cm-round]');
if(c){state.cround=+c.getAttribute('data-cm-round');state.cmWork=null;renderCmRounds();renderCmBlocks();}
});
$('cm-generate-btn').addEventListener('click',function(){state.cmWork=null;cmGenerate(this);});
$('cm-adopt-btn').addEventListener('click',cmAdopt);
$('cm-novel').addEventListener('click',function(e){
var b=e.target.closest('[data-cn]'),cmd=b&&b.getAttribute('data-cn');
if(!cmd)return;
if(cmd==='mclose'){$('cm-ch-modal').hidden=true;return;}
if(cmd==='create'){cnCreate(b);return;}
cnAct(cmd,b.getAttribute('data-id'),b);
});
}
bindCompose();
