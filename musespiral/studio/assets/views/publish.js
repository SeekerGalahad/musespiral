function filterArticles(){
return state.articles.filter(function(a){
var checks=a.checks||{},flags=a.flags||{};
if(state.filter==='draft') return articleStatus(a)==='draft';
if(state.filter==='pending') return (checks.fail||0)===0&&(checks.warn||0)===0&&articleStatus(a)==='draft'&&!flags.published;
if(state.filter==='reviewed') return articleStatus(a)==='reviewed';
if(state.filter==='published') return Boolean(flags.published);
if(state.filter==='fail') return (checks.fail||0)>0;
return true;
});
}
function qArt(iconName,label,count){
return '<span class="qa">'+I(iconName)+esc(label)+(count==null?'':' <span class="qa-n">'+count+'</span>')+'</span>';
}
function checkSummary(checks,path){
var fail=(checks&&checks.fail)||0,warn=(checks&&checks.warn)||0,pass=(checks&&checks.pass)||0;
if(fail>0) return '<span class="qcheck fail" role="link" tabindex="0" data-checks="'+(path?esc(path):'')+'" href="#article?path='+(path?encodeURIComponent(path):'')+'&tab=checks" title="有 '+fail+' 项 FAIL——进详情「检查」Tab 看原文与修复命令（点击直达）">'+I('error')+'FAIL '+fail+'</span>';
if(warn>0) return '<span class="qcheck warn" title="有 '+warn+' 项 WARN——进详情「检查」Tab 看原文">'+I('warning')+'WARN '+warn+'</span>';
return '<span class="qcheck pass">'+I('check')+'检查通过'+(pass?' '+pass+' 项':'')+'</span>';
}
function articleCard(item){
var href='#article?path='+encodeURIComponent(item.path)+'&tab=edit';
var f=item.flags||{};
var variants=Array.isArray(f.variants)?f.variants:[];
var title=esc(item.title||item.path);
var coverRel='';
if(item.cover){
var slash=item.path.lastIndexOf('/');
coverRel=(slash>=0?item.path.slice(0,slash+1):'')+item.cover;
}
var arts=[];
if(variants.length) arts.push(qArt('edit','变体 ',variants.length));
if(f.pack) arts.push(qArt('upload','物料包'));
if(f.review) arts.push(qArt('eye','对照'));
if(f.published) arts.push(qArt('publish','已发草稿'));
var md=item.media||{};
if(md.video) arts.push(qArt('video','视频文章'));
if(md.images) arts.push(qArt('image','含图 ',md.images));
return '<a class="article-card qcard" href="'+href+'">' +
'<div class="qcard-cover"'+(coverRel?' data-cover="'+esc(coverRel)+'"':'')+'>' +
'<div class="qcover-block"><span class="qcover-text">'+title+'</span></div></div>' +
'<div class="qcard-body">' +
'<h3 class="card-title qcard-title">'+title+'</h3>' +
'<div class="qcard-meta">'+cardStatusBadge(item)+'<span class="qcard-time">'+esc(item.date||'—')+'</span></div>' +
(arts.length?'<div class="qcard-arts">'+arts.join('')+'</div>':'') +
(variants.length?'<div class="tags qcard-variants">'+variants.map(function(code){
return '<span class="tag accent">'+esc(code)+Z;}).join('')+'</div>':'') +
'<div class="qcard-foot">'+checkSummary(item.checks,item.path)+'<button class="btn btn-ghost qcard-cover-btn" data-dvart="'+esc(item.path)+'" title="视觉编辑：为这篇文章生成封面/配图（选模板→存稿→导出 PNG）">🎨 封面</button><button class="btn btn-ghost qcard-fb" data-fb="'+esc(item.path)+'" title="反哺素材库：全文归档 / 摘取段落 / AI 提炼论点">→ 素材库</button></div>' +
'</div></a>';
}
function mountCovers(){
var nodes=$('article-list').querySelectorAll('[data-cover]');
for(var i=0;i<nodes.length;i++){
var host=nodes[i];
var img=document.createElement('img');
img.alt='封面';
img.className='qcover-img';
img.src='/asset?path='+encodeURIComponent(host.getAttribute('data-cover'));
img.onerror=function(){ if(this.parentNode) this.remove(); };
host.appendChild(img);
}
}
function pubCounts(){
var all=state.articles.length;
var n={all:all,draft:0,pending:0,reviewed:0,published:0,fail:0};
state.articles.forEach(function(a){
var c=a.checks||{},f=a.flags||{};
if(articleStatus(a)==='draft') n.draft += 1;
if((c.fail||0)===0&&(c.warn||0)===0&&articleStatus(a)==='draft'&&!f.published) n.pending += 1;
if(articleStatus(a)==='reviewed') n.reviewed += 1;
if(f.published) n.published += 1;
if((c.fail||0)>0) n.fail += 1;
});
return n;
}
function renderFunnel(){
var n=pubCounts();
var segs=[['draft','草稿',n.draft,'var(--viz-4,#9c5700)'],['pending','待审',n.pending,'var(--viz-1,#2c5f8a)'],['reviewed','已审',n.reviewed,'var(--viz-2,#c0392b)'],['published','已发布',n.published,'var(--viz-3,#1a7038)']];
$('funnel-band').innerHTML=segs.map(function(s,i){
var rate=i&&segs[i-1][2]?Math.round(s[2]/segs[i-1][2]*100)+'%':'—';
return '<button class="fb-seg" data-filter="'+s[0]+'" title="跳到「'+s[1]+'」筛选档" style="flex:'+(s[2]||1)+'">' +
'<span class="fb-n" style="color:'+s[3]+'">'+s[2]+'</span><span class="fb-t">'+s[1]+(i?' <span class="fb-rate">←'+rate+'</span>':'')+'</span></button>';
}).join('<span class="fb-arrow" aria-hidden="true">›</span>');
}
function updateChipCounts(){
var n=pubCounts();
$('count-all').textContent=n.all;
$('count-draft').textContent=n.draft;
$('count-pending').textContent=n.pending;
$('count-reviewed').textContent=n.reviewed;
$('count-published').textContent=n.published;
$('count-fail').textContent=n.fail;
$('queue-sub').textContent='共 '+n.all+' 篇 · 草稿 '+n.draft+' · 待审 '+n.pending+' · 已审 '+n.reviewed+' · 已发布 '+n.published;
updateNavBadge('nav-badge-publish',n.pending);
renderFunnel();
}
var EMPTY_TEXT=$('queue-empty-text').textContent;
function syncPubHash(){
var q=[];
if(state.filter!=='all')q.push('filter='+state.filter);
if(state.sview!=='grid')q.push('view='+state.sview);
history.replaceState(null,'','#publish'+(q.length?'?'+q.join('&'):''));
}
function setFilter(name){
state.filter=name;
qsel=-1;
syncPubHash();
renderPublish();
}
var qsel=-1;
function qCards(){return [].slice.call((state.sview==='list'?$('pub-rows'):$('article-list')).querySelectorAll(state.sview==='list'?'.pub-row':'.article-card'));}
function paintQSel(){
var cards=qCards(),i;
if(qsel>=cards.length)qsel=cards.length-1;
for(i=0;i<cards.length;i++)cards[i].classList.toggle('qsel',i===qsel);
if(qsel>=0&&cards[qsel])cards[qsel].scrollIntoView({block:'nearest'});}
function moveQSel(d){
var cards=qCards();
if(!cards.length)return;
qsel=qsel<0?(d>0?0:cards.length-1):Math.min(cards.length-1,Math.max(0,qsel+d));
paintQSel();}
function openQSel(){
var cards=qCards();
if(qsel>=0&&cards[qsel])location.hash=cards[qsel].getAttribute('href').slice(1);}
function pubRow(item){
var href='#article?path='+encodeURIComponent(item.path)+'&tab=edit';
var f=item.flags||{},variants=Array.isArray(f.variants)?f.variants:[];
return '<a class="pub-row" href="'+href+'">'+cardStatusBadge(item)+'<span class="pub-row-t">'+esc(item.title||item.path)+'</span><span class="pub-row-pl">'+(variants.length?variants.map(function(code){return '<span class="tag accent">'+esc(code)+Z;}).join(''):'<span class="muted">—')+'</span>'+checkSummary(item.checks,item.path)+'<button class="btn btn-ghost qcard-fb" data-fb="'+esc(item.path)+'" title="反哺素材库：全文归档 / 摘取段落 / AI 提炼论点">→素材库</button><span class="pub-row-time">'+esc(item.date||'—')+'</span></a>';
}
var FB={slug:'',mode:'full'};
function fbSlug(path){return String(path||'').replace(/\.md$/i,'').split('/').pop();}
function fbForm(){
var f=$('fb-form');
if(!f)return;
f.innerHTML=FB.mode==='excerpt'
?'<label class="field">起行 <input id="fb-s" class="input" type="number" min="1" value="1" style="width:90px"></label><label class="field">止行 <input id="fb-e" class="input" type="number" min="1" value="10" style="width:90px"></label><span class="action-hint" style="margin:0">对剥除 frontmatter 后的正文行计（1 起算、双闭区间）</span>'
:FB.mode==='distill'
?'<span class="action-hint" style="margin:0">AI 通读全文提炼 3-5 条核心论点，每条独立成一条灵感 spark（license=AI-GENERATED，可在素材库「✦ 灵感」筛出）。</span>'
:'<span class="action-hint" style="margin:0">正文全文（剥 frontmatter）存为 type=text 素材；血统记 published_from ← 本文。</span>';
}
function fbOpen(path){
FB.slug=fbSlug(path);
FB.mode='full';
$('viewer-title').textContent='反哺素材库 · '+FB.slug;
$('viewer-body').innerHTML=
'<p class="action-hint">把这篇文章回流进素材库，喂给下一轮创作。三路径均幂等——重复提交命中既有条，不重复入库。</p>'+
'<div class="chips"><button class="chip is-active" data-fbm="full">全文归档</button><button class="chip" data-fbm="excerpt">摘取段落</button><button class="chip" data-fbm="distill">提炼论点</button></div>'+
'<div class="action-row" id="fb-form"></div>'+
'<div class="action-row"><button class="btn btn-primary" id="fb-go">提交</button><button class="btn" id="fb-x">关闭</button></div>'+
'<p class="action-hint" id="fb-note"></p>';
fbForm();
$('viewer').hidden=false;
}
async function fbGo(btn){
btn.disabled=true;
var note=$('fb-note');
if(note)note.textContent='';
var body={slug:FB.slug,mode:FB.mode,confirm:true};
if(FB.mode==='excerpt'){
body.start_line=parseInt(($('fb-s')||{}).value,10);
body.end_line=parseInt(($('fb-e')||{}).value,10);
}
try{
var d=await postJSON('/api/materials/from-article',body);
if(FB.mode==='distill')toast('提炼完成：新增 '+((d.materials||[]).length)+' 条论点 spark'+(d.skipped?'（'+d.skipped+' 条槽位已存，跳过）':''),OK);
else toast(d.dedup?'反哺素材已存过（幂等命中，未重复入库）':'已归档为素材「'+(((d.material||{}).title)||FB.slug)+'」',d.dedup?WN:OK);
$('viewer').hidden=true;
loadMaterials().then(function(){renderCollRail();viewRepaint('material');});
}catch(err){if(note)note.textContent=err.message;btn.disabled=false;}
}
function renderPublish(){
var items=filterArticles();
updateChipCounts();
QA('[data-filter]').forEach(function(el){
el.classList.toggle('is-active',el.getAttribute('data-filter')===state.filter);
});
QA('[data-pview]').forEach(function(el){
el.classList.toggle('is-active',el.getAttribute('data-pview')===state.sview);
});
var grid=$('article-list'),rows=$('pub-rows');
if(state.sview==='list'){
rows.hidden=false;grid.hidden=true;grid.innerHTML='';
rows.innerHTML=items.map(pubRow).join('');
}else{
rows.hidden=true;grid.hidden=false;
grid.innerHTML=items.map(articleCard).join('');
mountCovers();
batchInit();
}
var empty=$('queue-empty');
empty.hidden=items.length>0;
if(!items.length){
var rootEmpty=!state.articles.length;
$('queue-empty-title').textContent=rootEmpty?'暂无文章':'没有符合条件的文章';
$('queue-empty-text').textContent=rootEmpty?EMPTY_TEXT:'当前筛选条件下没有文章，换个筛选试试。';
var act=$('queue-empty-action');
act.textContent=rootEmpty?'刷新队列':'查看全部';
act.setAttribute('data-empty-mode',rootEmpty?'refresh':'reset');
}
paintQSel();
}
VAPI.publish={enter:async function(route){
var qf=route.params.get('filter');
if(qf&&['all','draft','pending','reviewed','published','fail'].indexOf(qf)>=0)state.filter=qf;
var pv=route.params.get('view');
if(pv==='grid'||pv==='list')state.sview=pv;
await Promise.all([loadArticles(),loadPlatforms()]);
renderPublish();
},repaint:renderPublish};
function bindPublish(){
['batch-check','batch-pack','batch-clear'].forEach(function(id){
var b=$(id);if(!b)return;
b.addEventListener('click',function(){
if(id==='batch-check')batchRun('check');
else if(id==='batch-pack')batchRun('pack');
else{state.batch=new Set();batchSync();$('batch-result').hidden=true;}
});});
$('queue-filters').addEventListener('click',function(event){
var chip=event.target.closest('[data-filter]');
if(!chip) return;
setFilter(chip.getAttribute('data-filter'));
});
$('queue-empty-action').addEventListener('click',function(){
if(this.getAttribute('data-empty-mode')==='reset') setFilter('all');
else onRefresh();
});
var dragDepth=0;
function showDrop(on){ $('drop-overlay').hidden=!on;}
var queueView=$('view-publish');
queueView.addEventListener('dragenter',function(e){
if(!dropHasFiles(e)) return;
e.preventDefault();dragDepth++;showDrop(true);
});
queueView.addEventListener('dragover',function(e){
if(!dropHasFiles(e)) return;
e.preventDefault();
});
queueView.addEventListener('dragleave',function(e){
if(!dropHasFiles(e)) return;
dragDepth=Math.max(0,dragDepth-1);
if(!dragDepth) showDrop(false);
});
queueView.addEventListener('drop',function(e){
dragDepth=0;showDrop(false);
if(!e.dataTransfer||!e.dataTransfer.files||!e.dataTransfer.files.length) return;
e.preventDefault();
var files=[].slice.call(e.dataTransfer.files).filter(function(f){
return /\.(md|markdown)$/i.test(f.name);
});
if(!files.length){ toast('仅支持拖入 .md 文件（Markdown）',ER);return;}
handleDropFiles(files);
});
$('fetch-btn').addEventListener('click',function(e){handleFetch(e.target);});
$('view-publish').addEventListener('click',function(e){
var dvb=e.target.closest('[data-dvart]');
if(dvb){e.preventDefault();e.stopPropagation();location.hash='#design?article='+encodeURIComponent(dvb.getAttribute('data-dvart'));return;}
var b=e.target.closest('[data-fb]');
if(b){e.preventDefault();fbOpen(b.getAttribute('data-fb'));return;}
var f=e.target.closest('[data-checks]');
if(f){e.preventDefault();e.stopPropagation();var h=f.getAttribute('href');if(h)location.hash=h.slice(1);}
});
$('view-publish').addEventListener('keydown',function(e){
if(e.key!=='Enter'&&e.key!==' ')return;
var f=e.target.closest&&e.target.closest('[data-checks]');
if(f){e.preventDefault();e.stopPropagation();var h=f.getAttribute('href');if(h)location.hash=h.slice(1);}
});
$('viewer').addEventListener('click',function(e){
var m=e.target.closest('[data-fbm]');
if(m){FB.mode=m.getAttribute('data-fbm');QA('#viewer [data-fbm]').forEach(function(c){c.classList.toggle('is-active',c===m);});fbForm();return;}
var g=e.target.closest('#fb-go');
if(g){fbGo(g);return;}
if(e.target.closest('#fb-x'))$('viewer').hidden=true;
});
$('view-publish').addEventListener('contextmenu',function(e){
var c=e.target.closest('.article-card,.pub-row');
if(!c)return;
e.preventDefault();
var p=(c.getAttribute('href')||'').match(/path=([^&]+)/);
if(!p)return;
var h='#article?path='+p[1]+'&tab=';
ctxShow(e,'<a class="cp-item" href="'+h+'edit">打开</a><a class="cp-item" href="'+h+'checks">检查</a><a class="cp-item" href="'+h+'actions">创建草稿</a>');
});
$('funnel-band').addEventListener('click',function(e){
var c=e.target.closest('[data-filter]');
if(c)setFilter(c.getAttribute('data-filter'));
});
$('pub-view').addEventListener('click',function(e){
var c=e.target.closest('[data-pview]');
if(!c)return;
state.sview=c.getAttribute('data-pview');
syncPubHash();renderPublish();
});
async function showPlan(btn){
btn.disabled=true;
try{var d=await getJSON('/api/plan');
$('viewer-title').textContent='排期周历（三态）';
$('viewer-body').innerHTML=String(d.text||'').split('\n').map(function(l){return esc(l).replace(/计划:/,'<b class="badge warn">计划</b>:').replace(/已交付:/,'<b class="badge queued">已交付</b>:').replace(/已发布:/,'<b class="badge pass">已发布</b>:');}).join('\n');
$('viewer').hidden=false;
}catch(err){toast('周历读取失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
$('plan-btn').addEventListener('click',function(){showPlan(this);});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if(!$('cp').hidden||$('view-publish').hidden)return;
if(e.target.closest&&e.target.closest('input,select,textarea,button'))return;
if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();moveQSel(e.key==='ArrowDown'?1:-1);return;}
if(e.key==='Enter'&&qsel>=0){e.preventDefault();openQSel();return;}
});
}
bindPublish();
function batchSync(){var n=state.batch?state.batch.size:0;
var bar=$('batch-bar');if(bar)bar.hidden=!n;
var el=$('batch-n');if(el)el.textContent=String(n);
var list=$('article-list');if(!list)return;
[].forEach.call(list.querySelectorAll('.batch-pick'),function(cb){
cb.checked=!!(state.batch&&state.batch.has(cb.getAttribute('data-bpath')));});}
function batchToggle(path,on){if(!state.batch)state.batch=new Set();
if(on)state.batch.add(path);else state.batch.delete(path);batchSync();}
function batchInit(){
state.batch=state.batch||new Set();
var list=$('article-list');if(!list)return;
[].forEach.call(list.children,function(card){
if(card.querySelector('.batch-pick'))return;
var a=card.matches('a')?card:card.querySelector('a');if(!a)return;
var href=a.getAttribute('href')||'';var m=href.match(/path=([^&]+)/);
if(!m)return;var path=decodeURIComponent(m[1]);
var cb=document.createElement('input');cb.type='checkbox';cb.className='batch-pick';
cb.setAttribute('data-bpath',path);cb.setAttribute('aria-label','选择 '+path);
cb.addEventListener('click',function(e){e.stopPropagation();e.preventDefault();batchToggle(path,this.checked);});
card.insertBefore(cb,card.firstChild);});
batchSync();}
function batchRender(rows,totals,kind){
var el=$('batch-result');if(!el)return;el.hidden=false;
var h='<div class="batch-row head"><span>文章</span><span>失败</span><span>警告</span><span>通过</span><span>'+ (kind==='check'?'首条失败':'产物/错误') +'</span></div>';
rows.forEach(function(r){
var nm=r.path,f=r.fail,w=r.warn,p=r.pass,note=r.first_fail||r.dir||r.error||'';
if(kind==='pack'){f='—';w='—';p=(r.dir?'成功':'—');}
h+='<div class="batch-row"><span title="'+esc(nm)+'">'+esc(nm)+'</span>'
+'<span class="'+(f>0||f===-1?'bf':'')+'">'+esc(String(f))+'</span>'
+'<span class="bw">'+esc(String(w))+'</span>'
+'<span class="bp">'+esc(String(p))+'</span>'
+'<span class="be" title="'+esc(String(note))+'">'+esc(String(note).slice(0,80))+'</span></div>';});
h+='<div class="batch-row head"><span>共 '+totals.n+' 篇</span><span>失败 '+ (totals.fail||0) +'</span><span></span><span>净 '+ (totals.clean||totals.ok||0) +'</span><span>错误 '+ (totals.error||0) +'</span></div>';
el.innerHTML=h;}
async function batchRun(kind){
if(!state.batch||!state.batch.size)return toast('先勾选文章',WN);
var paths=[].slice.call(state.batch);
try{
var d;
if(kind==='check'){
d=await postJSON('/api/action/batch_check',{confirm:true,paths:paths});
batchRender(d.rows||[],d.totals||{},'check');
toast('批量检查完成:'+d.totals.n+' 篇,失败 '+d.totals.fail,d.totals.fail?WN:OK);
}else{
var pl=await askConfirm({title:'批量打包',body:'选择平台,逐篇生成物料包(不发公众号)',options:['xhs','zhihu','douyin','channels','juejin','bilibili']});
if(pl===false||pl==null)return;
d=await postJSON('/api/action/batch_pack',{confirm:true,paths:paths,platform:(typeof pl==='string'?pl:'xhs')});
batchRender(d.rows||[],d.totals||{},'pack');
toast('批量打包完成:成功 '+d.totals.ok+',失败 '+d.totals.error,d.totals.error?WN:OK);
}}catch(err){toast('批量操作失败:'+err.message,ER);}}
