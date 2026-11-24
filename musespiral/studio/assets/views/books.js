async function bsLoad(){
var el=$('bs-list');if(!el)return;
el.innerHTML='<div class="skel"><div class="skel-line w90"></div></div>';
try{var d=await getJSON('/api/booksource/subs');
var subs=d.subs||[];
if(!subs.length){el.innerHTML='<div class="empty"><div class="empty-text">暂无订阅。在上方填入书源包 URL 后点「新增/更新」。</div></div>';return;}
var h='';
subs.forEach(function(s){
var st=s.fetch_state==='ok'?'<span class="bs-st ok">正常</span>':
  s.fetch_state==='failed'?'<span class="bs-st failed" title="'+esc(s.error||'')+'">失败</span>':
  '<span class="bs-st pending">待确认</span>';
var last=s.last_fetch?new Date(s.last_fetch*1000).toLocaleString():'从未';
h+='<div class="bs-row"><span class="bs-url" title="'+esc(s.url)+'">'+esc(s.url)+'</span>'
+'<span>'+esc(String(s.update_interval_h||24))+'h</span>'
+'<span>'+(s.silent_update?'静默':'确认')+'</span>'
+st
+'<span title="上次刷新：'+esc(last)+'">'+esc(last.slice(5,16))+'</span>'
+'<span><button class="btn btn-ghost" data-bsrefresh="'+esc(s.url)+'" aria-label="刷新此订阅">刷新</button> '
+'<button class="btn btn-ghost" data-bsdel="'+esc(s.url)+'" aria-label="删除订阅">删除</button></span></div>';
});
el.innerHTML=h;
}catch(err){el.innerHTML='<div class="empty"><div class="empty-text">加载失败：'+esc(err.message)+'</div></div>';}}
async function bsAdd(){
var url=$('bs-url').value.trim();if(!url)return toast('填入订阅 URL',WN);
var iv=parseInt($('bs-interval').value)||24;
var silent=$('bs-silent').checked;
try{
await postJSON('/api/booksource/subs/add',{confirm:true,url:url,update_interval_h:iv,silent_update:silent});
toast('订阅已保存：'+url.slice(0,50),OK);
$('bs-url').value='';bsLoad();
}catch(err){toast('保存失败：'+err.message,ER);}}
async function bsRefresh(url){
try{
await postJSON('/api/booksource/refresh',{confirm:true,url:url});
toast('刷新已触发'+(url?'：'+url.slice(0,40):'（全部）'),OK);
setTimeout(bsLoad,1500);
}catch(err){toast('刷新失败：'+err.message,ER);}}
function bindBookSubs(){
['bs-add','bs-refresh-all'].forEach(function(id){
var b=$(id);if(!b)return;
b.addEventListener('click',function(){
if(id==='bs-add')bsAdd();
else if(id==='bs-refresh-all')bsRefresh('');
});});
$('bs-list').addEventListener('click',function(e){
var r=e.target.closest('[data-bsrefresh]');if(r){bsRefresh(r.getAttribute('data-bsrefresh'));return;}
var d=e.target.closest('[data-bsdel]');if(!d)return;
var u=d.getAttribute('data-bsdel');
askConfirm({title:'删除订阅',body:u.slice(0,80),confirmText:'删除'}).then(function(ok){
if(!ok)return;
postJSON('/api/booksource/subs/del',{confirm:true,url:u})
.then(function(){toast('已删除',OK);bsLoad();})
.catch(function(err){toast('删除失败：'+err.message,ER);});
});});
var origBkSub=bkSub;
bkSub=function(m){origBkSub(m);if(m==='subs')bsLoad();};
}
var BK={bookUrl:'',sourceUrl:'',name:'',info:null,toc:[],gs:[],sel:{},filter:'',spec:'all',job:null,pid:0};
function bkDigestInit(){try{BK.digest=localStorage.getItem('musespiral-book-digest')!=='0'}catch(e){BK.digest=true}
var b=$('bk-digest');if(b){b.checked=BK.digest;$('bk-digest-off').hidden=BK.digest;}}
function bkSub(m){$('bk-search').hidden=m!=='search';$('bk-detail').hidden=m!=='detail';$('bk-subs').hidden=m!=='subs';}
function bkMode(m,kw){bkSub(m);if(m==='search'){bkLamp();if(kw)$('bk-search-box').value=kw;}}
async function bkLamp(){
var d=null;
try{d=await getJSON('/api/booksource/engine');}catch(err){}
var st=(d&&d.status)||'absent',ver=(d&&d.version)||'';
var mock=(st==='ok'&&(ver==='mock'||d.mock)),ok=(st==='ok');
var dotCls=ok?'ok':'unknown';
var label=ok?(mock?'体验模式':'可搜'):'未建设';
$('bk-lamp').innerHTML='<span class="health-dot '+dotCls+'" title="'+(ok?(mock?'mock 假数据可走全流程':'bookengine 在线可搜'):'引擎未建设（R1-R2 分期未开工），非故障')+'"></span>bookengine '+label;
$('bk-search-box').disabled=!ok;
if(ok&&mock){
$('bk-guide-t').textContent='体验模式：用假数据跑通全流程';
$('bk-guide-x').textContent='搜索→选书→选章→入素材库全链可体验，内容为确定性假件。关体验模式：musespiral bookengine demo off';
}else if(ok){
$('bk-guide-t').textContent='搜索书名 / 作者 / 关键词';
$('bk-guide-x').textContent='四桶搜索与多源换源；详情可从素材行《书名》深链进入。';
}else{
$('bk-guide-t').textContent='引擎未建设，但书源管理全部可用';
$('bk-guide-x').textContent='缺的只是「搜索书籍→选章入素材库」（需 JVM 引擎抓取，R1-R2 分期）。书源导入/订阅/库管理不受影响；想先体验完整流程可开体验模式。';
}
var demoBtn=$('bk-guide-demo');if(demoBtn)demoBtn.hidden=!!ok;
}
async function bkOpen(bu,su,name){
bkSub('detail');
BK.bookUrl=bu;BK.sourceUrl=su;BK.name=name||'';BK.info=null;BK.toc=[];BK.gs=[];BK.sel={};BK.spec='all';bkChips();BK.filter='';$('bk-filter').value='';
$('bk-absent').hidden=true;$('bk-skel').hidden=false;$('bk-body').hidden=true;$('bk-prog').hidden=true;
var q='sourceUrl='+encodeURIComponent(su)+'&bookUrl='+encodeURIComponent(bu),r;
try{r=await Promise.all([getJSON('/api/book/info?'+q),getJSON('/api/book/toc?'+q)]);}
catch(err){toast('详情读取失败：'+err.message,ER);return bkMode('search');}
$('bk-skel').hidden=true;
if(r[0].status==='absent'||r[1].status==='absent'){$('bk-absent').hidden=false;return bkLamp();}
$('bk-body').hidden=false;
BK.info=r[0].info||{};if(BK.info.name)BK.name=BK.info.name;
BK.toc=r[1].chapters||[];
bkPaint();
}
function bkPaint(){
var i=BK.info||{};
$('bk-cover').textContent=(BK.name||'书').slice(0,2);
$('bk-name').textContent=BK.name||'（未命名）';
$('bk-meta').textContent=[i.author,i.kind,i.wordCount,i.lastChapter].filter(Boolean).join(' · ');
$('bk-intro').textContent=i.intro||'';
$('bk-src-now').textContent=BK.sourceUrl;$('bk-src-now').title=BK.sourceUrl;
$('bk-src-sel').innerHTML='<option value="'+esc(BK.sourceUrl)+'" selected>当前源</option><option disabled>多源候选 · R4</option>';
$('bk-toc-n').textContent=BK.toc.length;
var gs=[],cur={name:'',items:[]},re=/^(第[0-9一二三四五六七八九十百千两]+[卷部集])/;
BK.toc.forEach(function(c){var m=re.exec(String(c.title||''));
if(m&&m[1]!==cur.name){if(cur.items.length)gs.push(cur);cur={name:m[1],items:[]};}cur.items.push(c);});
if(cur.items.length)gs.push(cur);
if(!gs.length)gs=[{name:'正文',items:[]}];
BK.gs=gs;
var h='',j,g;
for(j=0;j<gs.length;j++){g=gs[j];
h+='<details class="toc-vol"'+(j?'':' open')+'><summary>▍'+esc(g.name||'正文')+' <span class="chip-count">'+g.items.length+' 章 · 已选 <b data-bkn="'+j+'">0</b></span> <button class="btn btn-ghost" data-bkvsel="'+j+'" title="全选/反选本卷">全选</button></summary><div class="toc-rows"'+(j?' data-pending="'+j+'"':'')+'>'+(j?'':g.items.map(bkRow).join(''))+'</div></details>';}
$('bk-toc').innerHTML=h;
bkSync();
}
function bkRow(c){
return '<label class="toc-row"><input type="checkbox" data-bkurl="'+esc(c.url)+'"'+(BK.sel[c.url]?' checked':'')+'><span class="idx">'+(c.index!=null?c.index:'·')+'</span><span class="t" title="'+esc(c.title)+'">'+esc(c.title)+'</span></label>';}
function bkSelN(){var n=0,u;for(u in BK.sel)if(BK.sel[u])n++;return n;}
function bkSync(){
var n=bkSelN();
$('bk-seln').textContent=n;$('bk-seln2').textContent=n;
var w=String((BK.info||{}).wordCount||''),m=w.match(/([\d.]+)/);
$('bk-wc').textContent=BK.toc.length&&n&&m?Math.round((/万/.test(w)?parseFloat(m[1])*1e4:+m[1])/BK.toc.length*n):0;
$('bk-opbar').hidden=!n;
$('bk-toc').querySelectorAll('input[data-bkurl]').forEach(function(x){x.checked=!!BK.sel[x.getAttribute('data-bkurl')];});
$('bk-toc').querySelectorAll('[data-bkn]').forEach(function(b,ix){var g=BK.gs[ix];
if(g)b.textContent=g.items.filter(function(c){return BK.sel[c.url];}).length;});
}
function bkChips(){QA('#bk-specs .chip').forEach(function(c){c.classList.toggle('is-active',c.getAttribute('data-bkspec')===BK.spec);});}
function bkSpecSet(spec){
BK.spec=spec;bkChips();$('bk-range').hidden=spec!=='custom';
if(spec==='custom')return;
BK.sel={};
var n=spec==='l5'?5:spec==='l10'?10:0;
(spec==='all'?BK.toc:BK.toc.slice(-n)).forEach(function(c){BK.sel[c.url]=true;});
bkSync();
}
function bkRangeApply(){
var a=+$('bk-from').value||1,b=+$('bk-to').value||BK.toc.length,t;
if(b<a){t=a;a=b;b=t;}
a=Math.max(1,a);b=Math.min(BK.toc.length,b);
BK.sel={};
for(t=a-1;t<b;t++)BK.sel[BK.toc[t].url]=true;
bkSync();
}
function bkVolSel(gi){
var g=BK.gs[gi];if(!g)return;
var all=g.items.every(function(c){return BK.sel[c.url];});
g.items.forEach(function(c){BK.sel[c.url]=!all;});
BK.spec='';bkChips();bkSync();
}
function bkRenderRows(pi){
var g=BK.gs[+pi.getAttribute('data-pending')];if(!g)return;
pi.innerHTML=g.items.map(bkRow).join('');
pi.removeAttribute('data-pending');
if(BK.filter)bkFilter();
}
function bkFilter(){
if(!BK.filter)return;
$('bk-toc').querySelectorAll('.toc-vol').forEach(function(d){
var pi=d.querySelector('.toc-rows[data-pending]');if(pi)bkRenderRows(pi);
d.setAttribute('open','');
d.querySelectorAll('.toc-row').forEach(function(r){
r.style.display=r.querySelector('.t').textContent.indexOf(BK.filter)>=0?'':'none';});});
}
async function bkImport(btn){
var chs=BK.toc.filter(function(c){return BK.sel[c.url];}).map(function(c){return {url:c.url,index:c.index,title:c.title};});
if(!chs.length)return toast('请先勾选章节',WN);
if(BK.digest&&chs.length>15)return toast('提炼模式上限 15 章（原始直入 30 章）',WN);
if(!BK.digest&&chs.length>30)return toast('原始直入上限 30 章',WN);
btn.disabled=true;
try{
var d=await postJSON('/api/materials/from-chapters',{confirm:true,sourceUrl:BK.sourceUrl,bookUrl:BK.bookUrl,book:{name:BK.name,author:(BK.info||{}).author||''},chapters:chs,digest:!!BK.digest});
BK.job={id:d.job_id,st:'run',chs:chs.length,digest:!!BK.digest};++BK.pid;
bkProg();$('bk-prog').hidden=false;
bkPoll(d.job_id,BK.pid);
}catch(err){toast('提交失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
function bkSteps(run,n){
var S=(run||{}).steps||[],h='',i,x,hasSyn=S.some(function(s){return s.id==='book_synthesis';});
for(i=0;i<S.length;i++){x=S[i];
h+='<div class="syn-step'+(x.status==='done'?' done':x.status==='running'?' running':'')+'"><i></i><span>['+(i+1)+'/'+(S.length||n)+'] '+esc(x.id==='book_synthesis'?'全书综述':String(x.id).replace(/^digest_/,'第 ')+' 章提炼')+' · <code>'+(x.chars_in||0)+'→'+(x.chars_out||0)+' 字</code>'+(x.cache_hit?' <em>cache✓</em>':'')+'</span></div>';}
if(n>=5&&!hasSyn)h+='<div class="syn-step"><i></i><span>['+(S.length+1)+'/'+Math.max(S.length+1,n)+'] 全书综述 · 等待（≥5 章追加）</span></div>';
return h;}
function bkProg(j,run){
var p=$('bk-prog'),J=BK.job;
if(!J){p.hidden=true;return;}
var pct=j?jobPct(j):null,ff=(j&&(j.result||{}).fetch_failed)||[],h='<div class="bk-prog-head"><b>'+(J.digest?'提炼中':'取文中')+'</b>'+(j&&j.phase?' <span class="muted">'+esc(j.phase)+'</span>':'')+(pct!=null?'<span class="job-prog"><i style="width:'+pct+'%"></i></span>':'')+(J.st==='run'?'<button class="btn" data-bkcancel="1">取消</button>':'')+'</div>';
if(J.digest)h+=run?bkSteps(run,J.chs):'<div class="skel"><div class="skel-line"></div><div class="skel-line w70"></div></div>';
if(J.st==='done'){
var r=(j&&j.result)||{};
h+='<p class="action-hint">'+(r.cancelled?'<span style="color:var(--amber)">已取消</span>——':'')+(r.fetched_n||0)+'/'+(r.chapters_total||J.chs)+' 章已入素材库（提炼 '+(r.distilled_n||0)+' · 原文 '+(r.raw_n||0)+'）'+(r.synthesis?' · 含综述':'')+'</p>'+(r.cancelled?'<p class="action-hint" style="color:var(--ink-2)">未完成章已放弃（已完成章保留入库）</p>':'')+'<div class="action-row"><a class="btn btn-primary" href="'+esc('#material?q=book:"'+BK.name+'"')+'">去素材库查看</a></div>'+
(ff.length?'<p class="action-hint" style="color:var(--amber)">'+ff.length+' 章未完成：'+ff.map(function(x){return '第 '+(x.index!=null?x.index:'?')+' 章'+esc(x.title?' '+x.title:'');}).join('、')+'</p>':'');
}else if(J.st==='failed')h+='<p class="action-hint" style="color:var(--crimson)">任务失败：'+esc((j&&j.error)||'未知错误')+'</p>';
p.innerHTML=h;p.hidden=false;
}
async function bkPoll(id,pid){
for(var i=0;i<1200;i++){
if(pid!==BK.pid||!BK.job)return;
var j=null,run=null;
try{j=await getJSON(`/api/jobs/${id}`);}catch(err){}
if(j&&BK.job.digest){try{run=(await getJSON('/api/agent/runs?id='+encodeURIComponent(id))).run||null;}catch(e2){}}
if(j&&(j.status==='done'||j.status==='failed')){
BK.job.st=j.status;bkProg(j,run);
var r=j.result||{},ff=r.fetch_failed||[],M=(r.materials||[]).length,N=(r.chapters_total||BK.job.chs)-M;
if(j.status==='done')toast(r.cancelled?('已取消：已入库 '+M+' 章，'+N+' 章未完成'):(r.fetched_n||0)+' 章已入素材库（提炼 '+(r.distilled_n||0)+(r.degraded_n?' · 降级 '+r.degraded_n:'')+'）'+(ff.length?'，'+ff.length+' 章未完成':''),r.cancelled?WN:ff.length?WN:OK);
else toast('取文失败：'+(j.error||'未知错误'),ER);
refreshJobs();return;}
if(j){BK.job.st='run';bkProg(j,run);}
await new Promise(function(z){setTimeout(z,900)});
}
}
var IM={st:'idle',tab:'paste',text:'',groups:[],skipped:[],counts:null,sel:{},exp:{},kw:'',stat:'all',grp:'',visible:[],kind:'bookSource',msg:'',allLatestOpen:false};
var IM_ROW=30,IM_VIEW=400,IM_BUF=5,IM_GTOP=10;
function imGroups(items,skipped){
var by={},order=[],rows=[],i,u,g;
for(i=0;i<items.length;i++){u=items[i].source.bookSourceUrl;
if(!by[u]){by[u]={url:u,items:[]};order.push(u);}
by[u].items.push(items[i]);}
for(i=0;i<order.length;i++){g=by[order[i]];
var anySel=false,j;for(j=0;j<g.items.length;j++){if(g.items[j].selected){anySel=true;break;}}
rows.push({t:'g',url:g.url,items:g.items,last:g.items[g.items.length-1],sel:anySel,n:g.items.length});}
for(i=0;i<skipped.length;i++)rows.push({t:'s',idx:skipped[i].index,reason:skipped[i].reason||'条目缺少 bookSourceUrl'});
return rows;}
function imLinear(){
var out=[],i,k,g;
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];out.push(g);
if(g.t==='g'&&IM.exp[g.url])for(k=0;k<g.items.length;k++)out.push({t:'v',v:g.items[k],url:g.url});}
return out;}
function imGtok(s){
return (s||'').replace(/[,，;；]+/g,' ').trim();}
function imMatch(r){
var kw=IM.kw.toLowerCase(),s,g;
if(r.t==='v')return true;
if(r.t==='s'){if(IM.stat!=='all'&&IM.stat!=='skipped')return false;
if(IM.grp)return false;
return !kw||String(r.reason).toLowerCase().indexOf(kw)>=0;}
s=r.last.status;g=r.last.source.bookSourceGroup||'';
var hay=((r.last.source.bookSourceName||'')+'\n'+g+'\n'+r.url).toLowerCase();
if(IM.stat!=='all'&&IM.stat!==s)return false;
if(IM.grp){
if(IM.grp==='（未分组）'){if(imGtok(g))return false;}
else if((' '+imGtok(g)+' ').indexOf(' '+IM.grp+' ')<0)return false;}
return !kw||hay.indexOf(kw)>=0;}
function imFilter(rows){var out=[],i;for(i=0;i<rows.length;i++)if(imMatch(rows[i]))out.push(rows[i]);return out;}
function imWindow(st,n){
var a=Math.max(0,Math.floor(st/IM_ROW)-IM_BUF),b=Math.min(n,Math.ceil((st+IM_VIEW)/IM_ROW)+IM_BUF);
return [a,Math.max(a,b)];}
function imBadge(s){
return '<span class="badge '+(s==='isNew'?'success':s==='isUpdate'?'warning':'queued')+'">'+(s==='isNew'?'新':s==='isUpdate'?'更新':'已最新')+'</span>';}
function imChips(){
var c={isNew:0,isUpdate:0,latest:0},i,j,g,gs={},go=[],h,k,p,
SB=[['all','全部'],['isNew','新'],['isUpdate','更新'],['latest','已最新']];
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t!=='g')continue;
c[g.last.status]++;
var parts=imGtok(g.last.source.bookSourceGroup).split(' ');
for(j=0;j<parts.length;j++){p=parts[j]||'（未分组）';
if(!gs[p]){gs[p]=0;go.push(p);}gs[p]++;}}
go.sort(function(a,b){return gs[b]-gs[a]||(a<b?-1:1);});go=go.slice(0,IM_GTOP);
h='';
for(i=0;i<SB.length;i++){k=SB[i][0];
h+='<button class="chip'+(IM.stat===k?' is-active':'')+'" data-imstat="'+k+'" type="button">'+SB[i][1]+' '+(k==='all'?IM.groups.length+IM.skipped.length:c[k])+'</button>';}
h+='<button class="chip'+(IM.stat==='skipped'?' is-active':'')+'" data-imstat="skipped" type="button" title="'+esc(IM.skipped.map(function(x){return '第 '+((x.index||0)+1)+' 条 '+(x.reason||'');}).join('；'))+'">⚠已跳过 '+IM.skipped.length+'</button>';
$('im-stat').innerHTML=h;
h='<button class="chip'+(IM.grp===''?' is-active':'')+'" data-imgrp="" type="button">全部分组</button>';
for(i=0;i<go.length;i++)h+='<button class="chip'+(IM.grp===go[i]?' is-active':'')+'" data-imgrp="'+esc(go[i])+'" type="button">'+esc(go[i])+' <span class="chip-count">'+gs[go[i]]+'</span></button>';
$('im-groups').innerHTML=h;}
function imPaintWin(){
var rows=IM.visible,n=rows.length,win=imWindow($('im-list').scrollTop,n),h='',i,r,
P='<div class="im-row" role="listitem" style="top:',C='<div class="im-row is-ver" role="listitem" style="top:',S='<div class="im-row is-skip" role="listitem" style="top:';
$('im-canvas').style.height=(n*IM_ROW)+'px';
for(i=win[0];i<win[1];i++){r=rows[i];
if(r.t==='g'){var s=r.last.source,nm=esc(s.bookSourceName||'（未命名）'),gp=esc(s.bookSourceGroup||'');
h+=P+i*IM_ROW+'px"><input type="checkbox" data-imurl="'+esc(r.url)+'" aria-label="书源 '+nm+'"'+(IM.sel[r.url]?' checked':'')+'><button class="im-exp" data-imexp="'+esc(r.url)+'" type="button" aria-label="展开或收起版本" aria-expanded="'+(IM.exp[r.url]?'true':'false')+'">'+(IM.exp[r.url]?'▾':'▸')+'</button><span class="im-name" title="'+nm+'">'+nm+(r.n>1?'（'+r.n+' 版本取最新）':'')+'</span><span class="im-grp" title="'+gp+'">'+gp+'</span>'+imBadge(r.last.status)+'</div>';}
else if(r.t==='v'){var v=r.v;
h+=C+i*IM_ROW+'px"><span class="im-verdot">└</span><span class="im-name">'+esc(v.source.bookSourceName||'（未命名）')+'</span><span class="im-grp">'+esc(v.source.bookSourceGroup||'')+'</span>'+imBadge(v.status)+'</div>';}
else h+=S+i*IM_ROW+'px" title="已跳过：'+esc(r.reason)+'"><input type="checkbox" disabled><span class="im-name">（第 '+(r.idx+1)+' 条）</span><span class="im-grp">'+esc(r.reason)+'</span><span class="badge queued">已跳过</span></div>';}
$('im-canvas').innerHTML=h;}
function imRepaint(){
IM.visible=imFilter(imLinear());
var n=0,sel=0,i,r;
for(i=0;i<IM.visible.length;i++){r=IM.visible[i];if(r.t==='g'){n++;if(IM.sel[r.url])sel++;}}
$('im-selall-t').textContent='全选（当前筛选 '+n+' 条）';
$('im-shown').textContent='显示 '+IM.visible.length+' 行';
$('im-selall').checked=n>0&&sel===n;
imChips();imPaintWin();imSummary();imCollapseSync();}
function imCollapseSync(){
var cb=$('im-collapse'),lst=$('im-list'),sr=$('im-selrow');
if(!cb||!lst)return;
var allLatest=IM.groups.length>0&&!IM.skipped.length;
for(var i=0;i<IM.groups.length;i++)if(IM.groups[i].t==='g'&&IM.groups[i].last.status!=='latest'){allLatest=false;break;}
var plain=allLatest&&!IM.kw&&IM.stat==='all'&&!IM.grp;
cb.hidden=!plain;
if(!plain){lst.hidden=false;if(sr)sr.hidden=false;return;}
cb.textContent=(IM.allLatestOpen?'▾ 收起清单':'▸ 全部 '+IM.groups.length+' 源均为「已最新」，无需导入 — 展开清单');
lst.hidden=!IM.allLatestOpen;
if(sr)sr.hidden=!IM.allLatestOpen;}
function imSelN(){var n=0,u;for(u in IM.sel)if(IM.sel[u])n++;return n;}
function imSelAll(v){
var i,r;for(i=0;i<IM.visible.length;i++){r=IM.visible[i];if(r.t==='g')IM.sel[r.url]=v;}
imRepaint();}
function imSummary(){
var n=imSelN(),x=0,y=0,i,g;
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t==='g'&&IM.sel[g.url]){if(g.last.status==='isNew')x++;else if(g.last.status==='isUpdate')y++;}}
$('im-sum').textContent='已选 '+n+' 条 · 新增 '+x+' · 更新 '+y;
$('im-apply').disabled=!n;}
function imIngest(d){
if(!d||d.kind!=='bookSource'){IM.kind=(d&&d.kind)||'unknown';IM.msg=(d&&d.message)||'';
IM.groups=[];IM.skipped=[];IM.counts=null;IM.sel={};IM.exp={};return false;}
IM.kind='bookSource';IM.msg='';
IM.groups=imGroups(d.items||[],d.skipped||[]);
IM.skipped=d.skipped||[];IM.counts=d.counts||{};IM.sel={};IM.exp={};
var i,g;for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t==='g'&&g.sel)IM.sel[g.url]=true;}
return true;}
function imRo(on){
$('im-src').classList.toggle('is-ro',on);
$('im-filters').hidden=on;}
function imNonSource(){
$('im-warn').textContent=IM.msg;$('im-warn').hidden=false;
$('im-total').textContent='非书源包 · '+(IM.kind==='unknown'?'无法识别':IM.kind);
imRo(true);}
function imTab(t){IM.tab=t;
QA('#im-tabs .tab').forEach(function(x){x.classList.toggle('is-active',x.getAttribute('data-imtab')===t);});
$('im-paste').hidden=t!=='paste';$('im-url').hidden=t!=='url';$('im-file').hidden=t!=='file';}
function imKeep(){return {keep_name:$('im-keep-name').checked,keep_group:$('im-keep-group').checked,keep_enable:$('im-keep-enable').checked};}
function imBtns(on){QA('#im-parse,#im-parse-url,#im-parse-file').forEach(function(b){b.disabled=on;});}
function imOpen(){
IM.st='idle';IM.text='';IM.allLatestOpen=false;imIngest(null);
IM.kw='';IM.stat='all';IM.grp='';IM.visible=[];
$('im-text').value='';$('im-url-in').value='';$('im-file-in').value='';$('im-kw').value='';
$('im-warn').hidden=true;$('im-prog').hidden=true;imRo(false);
$('im-total').textContent='';
$('im-canvas').innerHTML='';$('im-canvas').style.height='0px';
imTab('paste');imSummary();
openDlg('im','#im-tabs .tab');}
function imClose(){closeDlg('im');IM.st='idle';}
async function imParse(text){
if(!text||!text.trim())return toast('内容为空：先粘贴 JSON / 填 URL / 选文件',WN);
IM.st='parsing';IM.text=text;
$('im-prog').hidden=false;imBtns(true);
var _t0=Date.now(),_tick=setInterval(function(){var el=$('im-prog-t');if(el)el.textContent=' 已等 '+Math.round((Date.now()-_t0)/1000)+'s';},1000);
try{
var d=await postJSON('/api/booksource/import',Object.assign({confirm:true,text_or_url:text},imKeep()));
if(!imIngest(d)){imNonSource();imSummary();return;}
IM.st='previewing';
IM.kw='';IM.stat='all';IM.grp='';$('im-kw').value='';
$('im-warn').hidden=true;imRo(false);
$('im-total').textContent='共 '+IM.groups.length+' 源'+(IM.counts.dupUrls?' · 重复 URL '+IM.counts.dupUrls:'')+(IM.skipped.length?' · 跳过 '+IM.skipped.length:'');
$('im-list').scrollTop=0;
imRepaint();
}catch(err){toast('解析失败：'+err.message,ER);}
finally{clearInterval(_tick);$('im-prog').hidden=true;setTimeout(function(){var el=$('im-prog-t');if(el)el.textContent='';},300);imBtns(false);}}
async function imApply(btn){
var urls=[],i,g;for(i=0;i<IM.groups.length;i++){g=IM.groups[i];if(IM.sel[g.url])urls.push(g.url);}
if(!urls.length)return toast('请先勾选要导入的书源（可配合过滤与全选当前筛选）',WN);
IM.st='importing';btn.disabled=true;
try{
var d=await postJSON('/api/booksource/import_apply',Object.assign({confirm:true,text_or_url:IM.text,selections:urls.map(function(u){return {bookSourceUrl:u,selected:true};})},imKeep()));
if(d.kind&&d.kind!=='bookSource'){imIngest(d);imNonSource();toast(IM.msg,WN);return;}
toast('已导入 '+(d.stored||0)+' 源（选择 '+(d.deduped_from||0)+' 条 · 去重后落库）',OK);
imClose();bkLamp();
}catch(err){toast('导入失败：'+err.message,ER);}
finally{btn.disabled=false;if(IM.st==='importing')IM.st='previewing';}}
VAPI.books={enter:function(route){
if(route.params.get('tab')==='subs')bkMode('subs');
else if(route.params.get('book')&&route.params.get('source'))bkOpen(route.params.get('book'),route.params.get('source'),route.params.get('name')||'');
else bkMode('search',route.params.get('search'));
}};
function bindBooks(){
bindBookSubs();
bkDigestInit();
$('bk-subs-btn').addEventListener('click',function(){location.hash='#books?tab=subs';});
$('bk-subs-back').addEventListener('click',function(){location.hash='#books';});
$('bk-guide-subs').addEventListener('click',function(){location.hash='#books?tab=subs';});
$('bk-guide-import').addEventListener('click',imOpen);
bkResBind();
$('bk-guide-demo').addEventListener('click',async function(){
var btn=this;btn.disabled=true;
try{
await postJSON('/api/action/bookengine_demo',{confirm:true,mode:'on'});
toast('体验模式已开——请重启 Studio 生效：~/.musespiral/local/ctl.sh restart',OK);
}catch(err){toast('开启失败：'+err.message,ER);}
finally{btn.disabled=false;}});
$('bk-search-box').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229||e.key!=='Enter')return;
if(!this.value.trim())return;
bkSearch(this.value.trim());});
async function bkSearch(key){
var box=$('bk-search-box');if(box.disabled)return;
$('bk-guide').hidden=true;$('bk-results').hidden=false;
$('bk-res-n').textContent='…';$('bk-res-meta').textContent=' 搜索中';
$('bk-res-list').innerHTML='<div class="skel"><div class="skel-line w90"></div><div class="skel-line w70"></div></div>';
try{
var d=await postJSON('/api/book/search',{confirm:true,key:key});
if(!d.ok){bkResEmpty(d.error||d.message||'搜索失败');return;}
var j=await waitForJob(d.job_id);
var r=j.result||{};
if(j.status!=='done'){bkResEmpty(r.error||('搜索失败（'+j.status+'）'));return;}
bkResPaint(key,r);
}catch(err){bkResEmpty(err.message||'搜索失败');}
}
function bkResEmpty(msg){
$('bk-res-n').textContent='0';$('bk-res-meta').textContent='';
$('bk-res-list').innerHTML='<div class="empty"><div class="empty-text">'+esc(msg)+'</div></div>';
}
function bkResPaint(key,r){
var items=r.items||[];
$('bk-res-n').textContent=String(items.length);
var meta=[];if(r.total!=null)meta.push('共 '+r.total);if(r.searched!=null)meta.push('搜 '+r.searched+' 源');
$('bk-res-meta').textContent=meta.length?' · '+meta.join(' · '):'';
if(!items.length){bkResEmpty('没有找到与「'+key+'」相关的书');return;}
var h='',i,it;
for(i=0;i<items.length;i++){it=items[i]||{};
h+='<button class="bk-res-row" type="button" data-bku="'+esc(it.bookUrl||'')+'" data-bks="'+esc(it.origin||'')+'" data-bkn="'+esc(it.name||'')+'" aria-label="打开书籍 '+esc(it.name||'')+'">'
+'<span class="bk-res-name">'+esc(it.name||'(未命名)')+'</span>'
+'<span class="bk-res-author">'+esc(it.author||'')+'</span>'
+(it.kind?'<span class="bk-res-kind">'+esc(it.kind)+'</span>':'')
+(it.lastChapter?'<span class="bk-res-ch">'+esc(it.lastChapter)+'</span>':'')
+'<span class="bk-res-src">'+esc(it.originName||'')+'</span></button>';}
$('bk-res-list').innerHTML=h;
}
function bkResBind(){var el=$('bk-res-list');if(!el)return;
el.addEventListener('click',function(e){
var row=e.target.closest('[data-bku]');if(!row)return;
var bu=row.getAttribute('data-bku'),su=row.getAttribute('data-bks'),nm=row.getAttribute('data-bkn');
if(bu)bkOpen(bu,su,nm);});}
$('bk-filter').addEventListener('input',function(){
var v=this.value.trim(),had=!!BK.filter;BK.filter=v;
if(!v){if(had)bkPaint();return;}
bkFilter();});
$('bk-specs').addEventListener('click',function(e){var c=e.target.closest('[data-bkspec]');if(c)bkSpecSet(c.getAttribute('data-bkspec'));});
$('bk-range-apply').addEventListener('click',bkRangeApply);
$('bk-toc').addEventListener('change',function(e){
var t=e.target;if(!t.getAttribute||!t.getAttribute('data-bkurl'))return;
BK.sel[t.getAttribute('data-bkurl')]=t.checked;
BK.spec='';bkChips();bkSync();});
$('bk-toc').addEventListener('click',function(e){
var vs=e.target.closest('[data-bkvsel]');if(!vs)return;
e.preventDefault();e.stopPropagation();
bkVolSel(+vs.getAttribute('data-bkvsel'));});
$('bk-toc').addEventListener('toggle',function(e){
var d=e.target;
if(d.tagName==='DETAILS'&&d.open){
var pi=d.querySelector('.toc-rows[data-pending]');
if(pi)bkRenderRows(pi);}},true);
$('bk-src-sel').addEventListener('change',function(){
var v=this.value;if(!v)return;
BK.sourceUrl=v;bkOpen(BK.bookUrl,v,BK.name);});
$('bk-digest').addEventListener('change',function(){
BK.digest=this.checked;$('bk-digest-off').hidden=BK.digest;
try{localStorage.setItem('musespiral-book-digest',this.checked?'1':'0')}catch(e){}});
$('bk-import-btn').addEventListener('click',function(){bkImport(this);});
$('bk-prog').addEventListener('click',function(e){
var b=e.target.closest('[data-bkcancel]');if(!b)return;
b.disabled=true;
postJSON('/api/jobs/cancel',{job_id:BK.job.id,confirm:true}).then(function(d){
if(d.cancelled){toast('取消请求已发送——正在于检查点收束（已完成章保留）',WN);}
else{toast('任务已终态，无需取消',WN);b.disabled=false;}
}).catch(function(err){toast('取消失败：'+err.message,ER);b.disabled=false;});
});
$('im-open').addEventListener('click',imOpen);
$('im-close').addEventListener('click',imClose);
$('im-cancel').addEventListener('click',imClose);
$('im').addEventListener('click',function(e){if(e.target===this)imClose();});
$('im-tabs').addEventListener('click',function(e){var t=e.target.closest('[data-imtab]');if(t)imTab(t.getAttribute('data-imtab'));});
$('im-parse').addEventListener('click',function(){imParse($('im-text').value);});
$('im-parse-url').addEventListener('click',function(){imParse($('im-url-in').value);});
$('im-file-in').addEventListener('change',function(){
var f=this.files&&this.files[0];if(!f)return;
f.text().then(function(t){return imParse(t);}).catch(function(err){toast('读取文件失败：'+err.message,ER);});});
$('im-collapse').addEventListener('click',function(){IM.allLatestOpen=!IM.allLatestOpen;imCollapseSync();});
$('im-list').addEventListener('scroll',imPaintWin);
$('im-kw').addEventListener('input',function(){IM.kw=this.value.trim();imRepaint();});
$('im-stat').addEventListener('click',function(e){var c=e.target.closest('[data-imstat]');if(!c)return;IM.stat=c.getAttribute('data-imstat');imRepaint();});
$('im-groups').addEventListener('click',function(e){var c=e.target.closest('[data-imgrp]');if(!c)return;IM.grp=c.getAttribute('data-imgrp');imRepaint();});
$('im-selall').addEventListener('change',function(){imSelAll(this.checked);});
$('im-canvas').addEventListener('change',function(e){
var u=e.target.getAttribute&&e.target.getAttribute('data-imurl');if(!u)return;
IM.sel[u]=e.target.checked;imRepaint();});
$('im-canvas').addEventListener('click',function(e){
var b=e.target.closest('[data-imexp]');if(!b)return;
var u=b.getAttribute('data-imexp');
if(IM.exp[u])delete IM.exp[u];else IM.exp[u]=true;
imRepaint();});
QA('#im-keep-name,#im-keep-group,#im-keep-enable').forEach(function(s){
s.addEventListener('change',function(){if(IM.text&&IM.st==='previewing')imParse(IM.text);});});
$('im-apply').addEventListener('click',function(){imApply(this);});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&!$('im').hidden)imClose();});
}
bindBooks();
