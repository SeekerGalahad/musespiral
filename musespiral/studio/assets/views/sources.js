var SRC_TYPES={
rss:{name:'RSS 订阅',icon:'radar',name2:'订阅地址',gets:'文章链接',net:true,mtype:'link',
hint:'填一个 RSS 或 Atom 地址，每次刷新取回新文章的链接。'},
radar_hub:{name:'雷达枢纽',icon:'radar',gets:'开源仓库信号',net:false,mtype:'radar_signal',
hint:'读你本机的雷达枢纽，按筛选条件把值得关注的仓库登记成素材。'},
kb:{name:'知识库',icon:'docs',gets:'文档条目',net:false,mtype:'text',
hint:'把知识库目录里的文档登记成素材。正文不复制，只记引用。'},
dir:{name:'本地目录',icon:'file',gets:'文档条目',net:false,mtype:'text',
hint:'扫一个本地文件夹里的 .md 文档，记成素材（只记路径，不复制正文）。'},
booksource:{name:'书源',icon:'file',gets:'可搜索的书',net:true,mtype:'text',
hint:'订阅书源包后，用书名搜索、挑章节，再选章入素材库。'},
web:{name:'网页',icon:'globe',gets:'网页正文',net:true,mtype:'text',
hint:'粘贴网址，读完再决定存不存。不点确认就不会入库。'}
};
var SRC_ADD_TYPES=['rss','radar_hub','kb','dir'];
var SRC_CATALOG=['web','booksource','rss','radar_hub','kb','dir'];
var SRC_FIELDS={
rss:[{k:'url',label:'订阅地址',req:true,ph:'博客的 feed 地址（https 开头）',help:'RSS 或 Atom 地址。博客页面上的「订阅」链接就是它。'}],
radar_hub:[{k:'base',label:'雷达服务地址',req:true,value:'http://127.0.0.1:8787',help:'只认本机地址（localhost 回环），不接受外网地址。'},
{k:'subs',label:'订阅筛选',ta:true,help:'每行一条筛选条件；留空就全部拉取。'}],
kb:[{k:'root',label:'知识库文件夹',req:true,ph:'/Users/你/Documents/notes',help:'填文件夹的完整路径。目录里的文档会登记成素材，正文不复制。'}],
dir:[{k:'path',label:'本地文件夹',req:true,ph:'/Users/你/Documents/写的东西',help:'填文件夹的完整路径。扫描其中的 .md 文档，只记路径不复制正文。'}]
};
function srcStatusText(s){
if(s.enabled===false)return '已停用 · 不参与刷新';
var h=(s.health||{}).status||'unknown';
return h==='ok'?'正常':h==='down'?'异常':'还没验证';
}
function srcScopeTag(t){
var c=SRC_TYPES[t]||{};return c.net?'<span class="tag accent">会访问外网</span>':'<span class="tag">只在本机</span>';
}
function srcGives(s){
var cfg=s.config||{},c=SRC_TYPES[s.type]||{};
if(s.type==='radar_hub'){var n=(s.subscriptions||[]).length;return n?n+' 条订阅筛选':'雷达服务';}
if(s.type==='kb'){var r=cfg.root?String(cfg.root).split('/').filter(Boolean).pop():null;return r?'文件夹 '+esc(r):'未配置目录';}
if(s.type==='dir'){var d=cfg.path?String(cfg.path).split('/').filter(Boolean).pop():null;return d?'文件夹 '+esc(d):'未配置目录';}
if(s.type==='rss'){try{if(cfg.url)return esc(new (URL)(cfg.url).host);}catch(e){}return '订阅地址';}
if(s.type==='internal'){return '文章目录';}
return esc(c.name||s.type);
}
function srcStatsChips(list){
var on=list.filter(function(s){return s.enabled!==false;});
var bad=on.filter(function(s){return (s.health||{}).status==='down';}).length;
var unk=on.filter(function(s){return ((s.health||{}).status||'unknown')==='unknown';}).length;
var mats=(state.mats||[]).length, unread=(state.mats||[]).filter(function(m){return !(m.triage||{}).read;}).length;
if(!list.length)return '';
var h='<span class="st-chip">'+on.length+'/'+list.length+' 个源在用</span>';
if(bad)h+='<button class="st-chip bad" data-srcscope="attention" aria-label="只看需要处理的源">'+bad+' 个需要处理'+I('arrow-right')+'</button>';
else if(unk)h+='<span class="st-chip warn"><span class="st-dot warn"></span>'+unk+' 个还没验证</span>';
else h+='<span class="st-chip"><span class="st-dot ok"></span>全部正常</span>';
h+='<span class="st-chip">素材库 <b>'+mats+'</b> 条</span>';
if(unread)h+='<a class="st-chip" href="#material?tab=unread">未读 <b>'+unread+'</b>'+I('arrow-right')+'</a>';
return h;
}
function srcCard(s){
var c=SRC_TYPES[s.type]||{name:s.type,icon:'settings',gets:'内容'},off=s.enabled===false;
var h=(s.health||{}).status||'unknown',down=!off&&h==='down';
var busy=state.srcBusy&&state.srcBusy[s.id];
var cfg=s.config||{},d=s._failLast||{};
var acts='';
if(off)acts='<button class="btn" data-srcenable="'+esc(s.id)+'">'+I('play')+'启用</button>';
else{
acts='<button class="btn" data-srcrefresh="'+esc(s.id)+'"'+(busy?' disabled':'')+'>'+'<span class="hx'+(busy?' spin':'')+'" data-icon="refresh"></span>'+(busy?'处理中':'刷新')+'</button>';
if(s.type==='booksource')acts+='<button class="btn" data-srcsearch="1">'+I('search')+'去搜索</button>';
else acts+='<button class="btn btn-ghost" data-srcmats="'+esc(s.id)+'"'+((s._count||0)?'':' disabled')+'>看素材'+I('arrow-right')+'</button>';
}
return '<article class="src-card'+(off?' is-off':'')+'" data-card="'+esc(s.id)+'">'+
'<div class="src-top"><span class="src-glyph">'+I(c.icon)+'</span><div class="src-id">'+
'<h3 class="src-name">'+esc(s.label||s.id)+'</h3>'+
'<p class="src-meta'+(down?' is-down':'')+'"><span class="st-dot '+(off?'':h)+'"></span><span>'+srcStatusText(s)+'</span><span>·</span><span>'+esc(c.name)+'</span>'+srcScopeTag(s.type)+'</p>'+
'</div><button class="btn btn-ghost btn-icon" data-srcmenu="'+esc(s.id)+'" aria-label="「'+esc(s.label||s.id)+'」的更多操作" aria-haspopup="menu">'+I('settings')+'</button></div>'+
'<p class="src-io"><span class="io-k">读</span><b>'+srcGives(s)+'</b>'+I('arrow-right')+'<span class="io-k">得</span><b>'+esc(c.gets||'')+'</b></p>'+
(down?'<p class="src-alert">'+I('warning')+' '+esc((s.health||{}).last_error||'连接失败，点「验证」看原因')+'</p>':'')+
'<dl class="src-stats-row"><div><dt>素材</dt><dd>'+(s._count||0)+'</dd></div>'+
((s._unread||0)?'<div><dt>未读</dt><dd><button data-srcunread="'+esc(s.id)+'" aria-label="查看未读素材 '+(s._unread)+' 条">'+(s._unread)+'</button></dd></div>':'')+
(s.type==='booksource'&&s._books?'<div><dt>书源</dt><dd>'+s._books+'</dd></div>':'')+
'<div><dt>'+((s.health||{}).last_refresh?'上次刷新':'上次正常')+'</dt><dd>'+esc((s.health||{}).last_refresh||(s.health||{}).last_ok||'从未')+'</dd></div></dl>'+
'<div class="src-acts">'+acts+'</div></article>';
}
function srcNotice(kind,icon,title,text,acts){
return '<div class="notice-card'+(kind?' is-'+kind:'')+'"><span class="n-ic">'+I(icon)+'</span><div class="n-body"><h3>'+title+'</h3><p>'+text+'</p>'+(acts?'<div class="n-act">'+acts+'</div>':'')+'</div></div>';
}
function srcCountsDecor(list){
var mats=state.mats||[],cnt={},unread={};
mats.forEach(function(m){var k=(m.origin||{}).source_id;if(!k)return;cnt[k]=(cnt[k]||0)+1;if(!(m.triage||{}).read)unread[k]=(unread[k]||0)+1;});
var sc=((state.settings||{}).sources||{}).counts||{};
list.forEach(function(s){s._count=cnt[s.id]||sc[s.id]||0;s._unread=unread[s.id]||0;});
}
function srcInScope(s){
if(state.srcscope==='local')return !(SRC_TYPES[s.type]||{}).net;
if(state.srcscope==='net')return !!(SRC_TYPES[s.type]||{}).net;
if(state.srcscope==='attention')return (s.health||{}).status==='down'||s.enabled===false;
return true;
}
function renderSources(){
var list=matSrcs();
srcCountsDecor(list);
$('src-stats').innerHTML=srcStatsChips(list);
var gd=$('src-guide');
if(gd)gd.hidden=!(state.articlesLoaded&&!(state.articles||[]).length);
$('sources-sub' in window?'':'');
var sub=$('src-mine-sub');
var shown=list.filter(srcInScope);
if(sub)sub.textContent=(list.length?list.length+' 个'+(state.srcscope!=='all'?'，当前显示 '+shown.length+' 个':''):'');
var f=$('src-filters');
if(list.length>6){
f.hidden=false;
f.innerHTML=[['all','全部'],['local','只在本机'],['net','会访问外网'],['attention','需要处理']].map(function(p){
return '<button class="chip'+(state.srcscope===p[0]?' is-active':'')+'" data-srcscope="'+p[0]+'" aria-pressed="'+(state.srcscope===p[0])+'">'+p[1]+'</button>';
}).join('');
}else{f.hidden=true;f.innerHTML='';}
var body=$('src-mine-body');
if(state.srcLoading){
body.innerHTML='<div class="src-grid" role="status" aria-label="正在读取源清单">'+[0,1,2].map(function(){return '<div class="src-card"><div class="skel skel-line w45"></div><div class="skel skel-line w90"></div><div class="skel skel-line w70"></div><div class="skel skel-line w45"></div></div>';}).join('')+'</div>';
}else if(!list.length&&(state.srcFail||state.setFail)){
body.innerHTML=loadFailHTML('源清单',state.srcFail||state.setFail,'srcFailRetry');
}else if(!list.length){
body.innerHTML=srcNotice('empty','download','还没有接上任何源','源是内容的进水口。从下面挑一种，告诉它去哪里取内容，点「刷新」后素材就会出现在素材库。','<button class="btn btn-primary" data-srcadd="rss">'+I('plus')+'添加源</button>');
}else if(!shown.length){
body.innerHTML=srcNotice('','settings','这个范围内没有源','换一个范围看看。','<button class="btn" data-srcscope="all">看全部源</button>');
}else{
body.innerHTML='<div class="src-grid">'+shown.map(srcCard).join('')+'</div>';
}
renderSrcCatalogue();
updateNavBadge('nav-badge-sources',list.filter(function(s){return s.enabled!==false&&(s.health||{}).status==='down';}).length);
}
function srcFailRetry(){return refreshSrcData().then(function(){renderSources();renderSrcEngine();});}
function renderSrcCatalogue(){
var have={};matSrcs().forEach(function(s){have[s.type]=s;});
$('src-con-grid').innerHTML=SRC_CATALOG.map(function(t){
var c=SRC_TYPES[t],has=have[t],off=t==='dir'&&false;
var tag=off?'<span class="tag">暂不可用</span>':(has?'<span class="tag done">已接</span>':'');
var note=esc(c.hint),act='';
if(t==='web')act='<button class="btn btn-sm" data-srcwebread="1">'+I('file')+'读一篇</button>';
else if(t==='booksource')act=has?'<button class="btn btn-sm" data-srcsearch="1">'+I('search')+'去搜索</button><a class="btn btn-ghost btn-sm" href="#books?tab=subs">管理</a>':'<a class="btn btn-sm" href="#books?tab=subs">去订阅</a>';
else if(has)act='<button class="btn btn-ghost btn-sm" data-srcjump="'+esc(has.id)+'">查看</button>';
else act='<button class="btn btn-sm" data-srcadd="'+t+'">接上</button>';
return '<article class="con-tile"><div class="con-top"><span class="src-glyph">'+I(c.icon)+'</span><span class="con-name">'+esc(c.name)+'</span>'+tag+srcScopeTag(t)+'</div>'+
'<p class="con-io"><span>给「'+esc(c.name2||c.name)+'」</span>'+I('arrow-right')+'<span>得「'+esc(c.gets)+'」</span></p>'+
'<p class="con-note">'+note+'</p><div class="con-act">'+act+'</div></article>';
}).join('');
}
function renderSrcEngine(){
var el=$('src-engine-line');
if(!el)return;
var R=state.srcResults;
if(R&&(R.engineOff||R.booksFail)){
el.innerHTML='<span class="st-dot warn"></span><span>'+(R.engineOff?'上次搜索：bookengine 未启动，书籍腿不可用（素材/网页不受影响）':'上次书籍搜索失败：'+esc(R.booksFail))+'</span>'+(R.engineOff?'<button class="link" data-srcenginehelp="1" aria-expanded="false">启动方法</button><span id="src-eng-more" hidden>在终端执行 <code>~/GLMProjects/bookengine-jvm/scripts/ctl.sh start</code>，然后刷新本页。</span>':'<button class="btn btn-sm" data-srcretry="1" style="margin-left:8px">重试搜索</button>');
return;
}
var eng=(state.settings||{}).booksource_engine||{};
try{
getJSON('/api/booksource/engine').then(function(d){
if(d&&d.ok){el.innerHTML='<span class="st-dot '+(d.mock?'warn':'ok')+'"></span><span>'+(d.mock?'书籍搜索处于体验模式：用假数据跑通全流程（musespiral bookengine demo off 关闭）':'书籍搜索由 bookengine 提供 · '+esc(d.version||'引擎')+' · '+((d.sources_loaded||0).toLocaleString())+' 个书源可搜')+'</span>';}
else{el.innerHTML='<span class="st-dot warn"></span><span>书籍搜索暂不可用：bookengine 未启动。素材和网页搜索不受影响。</span><button class="link" data-srcenginehelp="1" aria-expanded="false">启动方法</button><span id="src-eng-more" hidden>在终端执行 <code>~/GLMProjects/bookengine-jvm/scripts/ctl.sh start</code>，然后刷新本页。</span>';}
}).catch(function(){el.innerHTML='<span class="st-dot warn"></span><span>引擎状态读取失败，书籍搜索可能不可用。</span>';});
}catch(e){}
}
async function srcCustomToggle(id,en){
try{await postJSON('/api/sources/enabled',{id:id,enabled:en,confirm:true});await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');toast(en?'源已启用（进采集循环）':'源已停用（不进采集循环，素材保留）',OK);}
catch(err){toast('启停失败：'+err.message,ER);}
}
async function srcCustomVerify(id){
try{var d=await postJSON('/api/sources/verify',{id:id,confirm:true});var st=(d.health&&d.health.status)==='ok';
await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');
toast('「'+id+'」探测：'+(st?'正常':'down/未配置（灰灯非失败）'),st?OK:WN);}
catch(err){toast('验证失败：'+err.message,ER);}
}
async function srcCustomVerifyAll(){
var list=matSrcs(),okc=0;
for(var i=0;i<list.length;i++){
try{var d=await postJSON('/api/sources/verify',{id:list[i].id,confirm:true});if((d.health&&d.health.status)==='ok')okc++;}
catch(err){}
}
await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');
toast('全部源探测完成：'+okc+'/'+list.length+' 正常（结果见状态灯）',OK);
}
function srcAddOpen(type){
state.srcAddType=(type&&SRC_FIELDS[type])?type:'rss';
state.srcAddTest=null;
renderSrcAdd();
openDlg('src-form','#sf-fields .input');
}
function renderSrcAdd(){
var t=state.srcAddType;
$('sf-types').innerHTML=SRC_ADD_TYPES.map(function(k){
var c=SRC_TYPES[k],has=matSrcs().some(function(s){return s.type===k;});
return '<button type="button" class="sf-type" role="radio" data-srcpick="'+k+'" aria-checked="'+(t===k)+'">'+
'<span class="src-glyph">'+I(c.icon)+'</span><span><span class="t">'+esc(c.name)+(has?' · 已接':'')+'</span><span class="d">得「'+esc(c.gets)+'」</span></span></button>';
}).join('');
$('sf-fields').innerHTML='<div class="sf-field"><label for="sf-label">起个名字（可不填）</label><input class="input" id="sf-label" placeholder="'+esc(SRC_TYPES[t].name)+'" autocomplete="off"><p class="help">留空就叫「'+esc(SRC_TYPES[t].name)+'」，源 id 会自动生成。</p></div>'+
(SRC_FIELDS[t]||[]).map(function(x){
return '<div class="sf-field"><label for="sf-'+x.k+'">'+x.label+(x.req?' <span class="req" aria-hidden="true">*</span>':'')+'</label>'+
(x.ta?'<textarea class="input" id="sf-'+x.k+'" rows="3"></textarea>':'<input class="input" id="sf-'+x.k+'" value="'+esc(x.value||'')+'" placeholder="'+esc(x.ph||'')+'" autocomplete="off">')+
'<p class="help">'+x.help+'</p><p class="err" id="sf-'+x.k+'-err" aria-live="polite"></p></div>';
}).join('');
$('sf-test').innerHTML='';
$('sf-fields').querySelectorAll('.input').forEach(function(inp){inp.addEventListener('blur',function(){srcAddValidate(inp);});});
}
function srcFieldDef(id){
var k=id.slice(3).replace(/-err$/,'');
return (SRC_FIELDS[state.srcAddType]||[]).find(function(x){return x.k===k;});
}
function srcAddValidate(inp){
var d=srcFieldDef(inp.id);if(!d)return true;
var v=inp.value.trim(),err=$('sf-'+d.k+'-err'),msg='';
if(d.req&&!v)msg='这一项要填。';
else if(v&&d.k==='url'&&!/^https?:\/\/[^\s]+\.[^\s]+/.test(v))msg='请填完整网址，以 http 或 https 开头。';
else if(v&&d.k==='base'&&!/^https?:\/\/127\.0\.0\.1(:\d+)?/.test(v))msg='只认本机地址：应以 http 冒双斜杠 127.0.0.1 开头。';
else if(v&&(d.k==='root'||d.k==='path')&&v[0]!=='/')msg='请填以 / 开头的完整路径。';
if(msg)inp.setAttribute('aria-invalid','true');else inp.removeAttribute('aria-invalid');
if(err)err.textContent=msg;
return !msg;
}
function srcAddValidateAll(){
var first=null;
$('sf-fields').querySelectorAll('.input').forEach(function(inp){if(!srcAddValidate(inp)&&!first)first=inp;});
if(first)first.focus();
return !first;
}
function srcAddConfig(){
var t=state.srcAddType,val=function(k){var el=$('sf-'+k);return el?el.value.trim():'';};
if(t==='rss')return {url:val('url')};
if(t==='radar_hub')return {base:val('base')};
if(t==='kb')return {root:val('root')};
if(t==='dir')return {path:val('path')};
return {};
}
async function srcAddTest(){
if(!srcAddValidateAll()){$('sf-test').innerHTML='';return;}
$('sf-test').innerHTML='<p class="sf-test-result">'+I('clock')+'<span>正在探测…</span></p>';
try{
var d=await postJSON('/api/sources/probe',{type:state.srcAddType,config:srcAddConfig(),confirm:true});
var ok=d.state==='ok';
$('sf-test').innerHTML='<p class="sf-test-result '+(ok?'ok':'bad')+'" role="status">'+I(ok?'check':'warning')+'<span>'+esc(d.detail||'')+'</span></p>';
}catch(err){$('sf-test').innerHTML='<p class="sf-test-result bad" role="status">'+I('warning')+'<span>探测失败：'+esc(err.message)+'</span></p>';}
}
async function srcAddSubmit(){
if(!srcAddValidateAll()){toast('先补全标了 * 的必填项。',ER);return;}
var t=state.srcAddType,c=SRC_TYPES[t],cfg=srcAddConfig();
var label=$('sf-label').value.trim()||c.name;
var subs=t==='radar_hub'?($('sf-subs')&&$('sf-subs').value||'').split('\n').map(function(s){return s.trim();}).filter(Boolean):[];
try{
var d=await postJSON('/api/sources',{type:t,label:label,config:cfg,subscriptions:subs,enabled:true,confirm:true});
closeDlg('src-form');
state.srcscope='all';
await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');renderCollRail();
var newId=(d&&d.source&&d.source.id)||(d&&d.id)||'';
toast('已接上「'+label+'」'+(newId?'（id '+newId+'）':'')+'。点「刷新」开始取内容。',OK);
}catch(err){toast('接入失败：'+err.message,ER);}
}
function srcById(id){return matSrcs().filter(function(s){return s.id===id;})[0]||null;}
function srcDrawerOpen(id){
var s=srcById(id);if(!s)return;
var cfg=s.config||{},h=s.health||{},c=SRC_TYPES[s.type]||{},one=function(k,label,val,help){
return '<div class="sf-field"><label for="sd-'+k+'">'+label+'</label><input class="input" id="sd-'+k+'" value="'+esc(val||'')+'"><p class="help">'+help+'</p></div>';
};
var readFields='';
if(s.type==='radar_hub')readFields=one('base','雷达服务地址',cfg.base,'只认本机地址（127.0.0.1）。')+
'<div class="sf-field"><label for="sd-subs">订阅筛选</label><textarea class="input" id="sd-subs" rows="2">'+esc((s.subscriptions||[]).join('\n'))+'</textarea><p class="help">每行一条；留空就全部拉取。</p></div>';
else if(s.type==='kb')readFields=one('root','知识库文件夹',cfg.root,'目录里的文档会登记成素材；正文不复制，只记引用。');
else if(s.type==='dir')readFields=one('path','本地文件夹',cfg.path,'扫描其中的 .md 文档；只记路径，不复制正文。');
else if(s.type==='rss')readFields=one('url','订阅地址',cfg.url,'RSS 或 Atom 地址。');
else if(s.type==='booksource')readFields='<p class="action-hint">订阅在「书籍 → 订阅管理」里增删。</p>';
else readFields='<p class="action-hint">这个源不需要配置。</p>';
$('src-drawer-title').textContent=s.label||s.id;
$('src-drawer-body').innerHTML=
'<dl class="kv"><dt>类型</dt><dd>'+esc(c.name||s.type)+'</dd><dt>状态</dt><dd>'+esc(srcStatusText(s))+'</dd><dt>边界</dt><dd>'+(c.net?'会访问外网（只在你点刷新或搜索时请求）':'只读本机内容，不出网')+'</dd></dl>'+
'<div class="action-title" style="font-size:13px;margin-top:12px">读什么</div>'+readFields+
'<div class="action-title" style="font-size:13px;margin-top:12px">得什么</div><p class="action-hint">'+esc(c.gets||'')+(c.mtype?'（素材类型 '+esc(c.mtype)+'）':'')+'</p>'+
(s.type==='booksource'||s.type==='web'?'':'<div class="action-title" style="font-size:13px;margin-top:12px">AI 加工</div><button class="btn" data-srcai="'+esc(s.id)+'">源级 AI 配置（只影响该源素材）</button>')+
'<div class="action-row" style="margin-top:14px"><button class="btn" data-srcverifyin="'+esc(s.id)+'">验证</button><button class="btn" data-srcenable="'+esc(s.id)+'">'+(s.enabled===false?'启用':'停用')+'</button><button class="btn btn-primary" data-srcsave="'+esc(s.id)+'">保存配置</button></div>';
$('src-drawer').hidden=false;
}
async function srcDrawerSave(id){
var s=srcById(id);if(!s){toast('源已失效',ER);return;}
var cfg=Object.assign({},s.config||{}),g=function(k){var el=$('sd-'+k);return el?el.value.trim():null;};
if(g('base')!==null)cfg.base=g('base');
if(g('root')!==null)cfg.root=g('root');
if(g('path')!==null)cfg.path=g('path');
if(g('url')!==null)cfg.url=g('url');
var subs=g('subs')!==null?g('subs').split('\n').map(function(x){return x.trim();}).filter(Boolean):(s.subscriptions||[]);
try{await postJSON('/api/sources',{id:s.id,type:s.type,label:s.label,config:cfg,enabled:s.enabled!==false,subscriptions:subs,ai_config:s.ai_config||{},confirm:true});
await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');toast('已保存「'+(s.label||s.id)+'」的配置',OK);}
catch(err){toast('保存失败：'+err.message,ER);}
}
function srcAiOpen(id){
var s=srcById(id);if(!s)return;
var cfg=s.ai_config||{};
var ap=(((state.settings||{}).ai||{}).profiles||{}).profiles||{};
$('sac-sub').textContent='源 '+(s.label||s.id)+' · '+esc(s.id);
$('sac-profile').innerHTML='<option value="">（全局路由）</option>'+Object.keys(ap).map(function(n){return '<option'+(n===cfg.profile?' selected':'')+'>'+esc(n)+'</option>';}).join('');
$('sac-model').value=cfg.model||'';
$('sac-temp').value=(cfg.temperature==null?'':cfg.temperature);
$('sac-prompt').value=cfg.prompt_override||'';
$('src-aicfg').setAttribute('data-id',id);
openDlg('src-aicfg','#sac-profile');
}
async function srcAiSave(){
var id=$('src-aicfg').getAttribute('data-id'),s=srcById(id);if(!s){toast('源已失效',ER);return;}
var cfg={},p=$('sac-profile').value,m=$('sac-model').value.trim(),t=$('sac-temp').value.trim(),po=$('sac-prompt').value;
if(p)cfg.profile=p;if(m)cfg.model=m;
if(t!==''){var tv=Number(t);if(!isFinite(tv)||tv<0||tv>2){toast('temperature 必须是 0-2 之间',ER);return;}cfg.temperature=tv;}
if(po.trim())cfg.prompt_override=po;
try{await postJSON('/api/sources',{id:s.id,type:s.type,label:s.label,config:s.config,enabled:s.enabled!==false,subscriptions:s.subscriptions||[],ai_config:cfg,confirm:true});
toast('「'+id+'」AI 配置已保存（只影响该源素材加工）',OK);closeDlg('src-aicfg');await refreshSrcData();if(!$('view-sources').hidden)viewRepaint('sources');}
catch(err){toast('保存失败：'+err.message,ER);}
}
function srcIsUrl(q){return /^(https?:\/\/)?([\w-]+\.)+[a-z]{2,}(:\d+)?([/?#]\S*)?$/i.test(q);}
function srcIsSymbolOnly(q){return /^[\s\p{P}\p{S}]+$/u.test(q);}
var srcWaitTimer=null,srcWaitT0=0;
function srcWaitStop(){if(srcWaitTimer){clearInterval(srcWaitTimer);srcWaitTimer=null;}}
function srcWaitTick(){var w=$('src-wait');if(w)w.textContent='已等待 '+Math.max(0,Math.round((Date.now()-srcWaitT0)/1000))+'s';}
function srcWaitStart(){if(!srcWaitTimer){srcWaitT0=Date.now();srcWaitTimer=setInterval(srcWaitTick,5000);}}
function srcSearchRender(){
var q=state.srcQ.trim(),box=$('src-results');
if(!q){srcWaitStop();box.hidden=true;$('src-overview').hidden=false;$('src-q-clear').hidden=true;$('src-q-kbd').textContent='/';renderSrcEngine();return;}
box.hidden=false;$('src-overview').hidden=true;$('src-q-clear').hidden=false;$('src-q-kbd').textContent='Esc';
var R=state.srcResults,loading=state.srcSearching;
var head='<div class="results-head"><h2>“'+esc(q)+'”</h2>'+(R&&!loading?'<span class="results-sum">'+[R.url?'网页 1':'',R.books?'书籍 '+R.books.length:'',R.mats?'素材 '+R.mats.length:''].filter(Boolean).join(' · ')+'</span>':'')+
'<button class="btn btn-ghost btn-sm" data-srcclear="1">'+I('close')+'返回源总览</button></div>';
if(loading||!R){srcWaitStart();box.innerHTML=head+'<div role="status" aria-label="正在搜索"><p class="muted" id="src-wait" style="font-size:12px;margin:0 0 10px">已等待 0s</p>'+[0,1,2].map(function(){return '<div class="skel skel-line w90" style="margin-bottom:12px"></div>';}).join('')+'</div>';return;}
srcWaitStop();
if(srcIsSymbolOnly(q)){box.innerHTML=head+srcNotice('','search','这个关键词没法搜','「'+esc(q)+'」里只有符号。试试书名、作者名，或直接粘贴网址。','');return;}
var h=head;
if(R.url){h+='<section class="rgroup"><h3 class="rg-h">'+I('globe')+'网页 <span class="cnt">1</span><span class="rg-src">读完再决定存不存</span></h3><button class="rg-row" data-srcweb="'+esc(R.url)+'">'+
'<span class="rg-glyph">'+I('globe')+'</span><span class="rg-body"><span class="rg-title">'+esc(R.urlHost)+'</span><span class="rg-meta">'+esc(R.urlPath)+'</span></span><span class="rg-go">打开阅读'+I('arrow-right')+'</span></button></section>';}
h+='<section class="rgroup"><h3 class="rg-h">'+I('file')+'书籍 <span class="cnt">'+(R.booksFail?'✕':(R.books?R.books.length:0))+'</span><span class="rg-src">'+(R.engineOff?'bookengine 未启动':'bookengine · 引擎在线')+'</span></h3>';
if(R.booksFail){h+='<p class="rg-note is-warn">'+I('warning')+'<span>引擎读取失败：'+esc(R.booksFail)+'。这不是「没有找到」——引擎侧出了问题，重试一次或稍后再试。</span></p><div style="padding:6px 8px 4px"><button class="btn btn-sm" data-srcretry="1">'+I('refresh')+'重试搜索</button></div>';}
else if(R.engineOff){h+='<p class="rg-note is-warn">'+I('warning')+'<span>书籍搜索暂不可用：bookengine 未启动。上面的素材和网页结果不受影响。</span></p>';}
else if(!R.books||!R.books.length){h+='<p class="rg-note"><span class="st-dot"></span><span>书籍里没找到「'+esc(q)+'」。试试更短的书名，或只输作者名。</span></p>';}
else{h+='<div>'+R.books.map(function(b,idx){return '<button class="rg-row" data-srcbook="'+idx+'"><span class="rg-cover" style="background:var(--indigo)">'+esc((b.name||'?').slice(0,2))+'</span><span class="rg-body"><span class="rg-title">'+esc(b.name)+'</span><span class="rg-meta">'+esc([b.author,b.originName||b.origin].filter(Boolean).join(' · '))+'</span></span><span class="rg-go">阅读'+I('arrow-right')+'</span></button>';}).join('')+'</div>';}
h+='</section>';
h+='<section class="rgroup"><h3 class="rg-h">'+I('docs')+'素材 <span class="cnt">'+(R.matsFail?'✕':(R.mats?R.mats.length:0))+'</span><span class="rg-src">来自素材库</span></h3>';
if(R.matsFail)h+='<p class="rg-note is-warn">'+I('warning')+'<span>素材库读取失败：'+esc(R.matsFail)+'。这不是「没有匹配」——素材服务没响应。</span></p><div style="padding:6px 8px 4px"><button class="btn btn-sm" data-srcretry="1">'+I('refresh')+'重试搜索</button></div>';
else if(!R.mats||!R.mats.length)h+='<p class="rg-note"><span class="st-dot"></span><span>素材里没有匹配「'+esc(q)+'」的内容。</span></p>';
else h+='<div>'+R.mats.map(function(m,idx){return '<a class="rg-row" href="#material"><span class="rg-glyph">'+I('file')+'</span><span class="rg-body"><span class="rg-title">'+esc(m.title||'(无题)')+'</span><span class="rg-meta">'+esc(((m.origin||{}).source_id||'')+' · '+(m.fetched_at||'').slice(0,10))+'</span></span><span class="rg-go">查看</span></a>';}).join('')+'</div>';
h+='</section>';
box.innerHTML=h;
}
async function srcDoSearch(){
var q=state.srcQ.trim();if(!q)return;
var tok=++state.srcTok;
state.srcSearching=true;srcSearchRender();
srcWaitT0=Date.now();
var R={url:null,urlHost:'',urlPath:'',books:[],mats:[],engineOff:false,booksFail:'',matsFail:''};
if(srcIsUrl(q)){
var full=/^https?:\/\//i.test(q)?q:('https'+':'+'//'+q);
try{var u=new (URL)(full);R.url=full;R.urlHost=u.host;R.urlPath=u.pathname+u.search;}catch(e){}
}
try{var md=await getJSON('/api/materials?q='+encodeURIComponent(q)+'&limit=20');R.mats=(md.list||[]).slice(0,20);}
catch(err){R.matsFail=err.message||'服务未响应';}
if(!R.url&&!srcIsSymbolOnly(q)){
try{
var eng=await getJSON('/api/booksource/engine');
if(!(eng&&eng.ok)){R.engineOff=true;}
else{
var sub=await postJSON('/api/book/search',{key:q,confirm:true});
var job=await waitForJob(sub.job_id);
if(job&&job.status==='failed')R.booksFail=(job.error||'引擎读取失败');
else R.books=(job&&job.result&&job.result.items)||[];
}
}catch(err){R.booksFail=err.message||'引擎读取失败';}
}
if(tok!==state.srcTok)return;
state.srcResults=R;state.srcSearching=false;srcSearchRender();
renderSrcEngine();
}
async function srcOpenReaderWeb(url){
try{await ensureView('reader');VAPI.reader.openWeb(url);}catch(err){toast('阅读面板装载失败：'+err.message,ER);}
}
async function srcOpenReaderBook(idx){
var b=state.srcResults&&state.srcResults.books&&state.srcResults.books[idx];
if(!b){toast('书籍结果已失效，请重新搜索',ER);return;}
try{await ensureView('reader');VAPI.reader.openBook(b);}catch(err){toast('阅读面板装载失败：'+err.message,ER);}
}
VAPI.sources={enter:async function(route){
var sp=route.params.get('src');
if(sp!=null)state.ssrc=sp;
await Promise.all([refreshSrcData(),loadMaterials(),refreshJobs(),loadArticles(true)]);
renderSources();renderSrcEngine();
},repaint:async function(){await loadMaterials();renderSources();}};
function bindSources(){
if(!state.srcInit){
state.srcscope='all';state.srcQ='';state.srcResults=null;state.srcSearching=false;state.srcTok=0;state.srcBusy={};state.srcLoading=false;
state.srcInit=true;
}
var body=$('src-mine-body');
body.addEventListener('click',function(e){
var add=e.target.closest('[data-srcadd]');if(add){srcAddOpen(add.getAttribute('data-srcadd'));return;}
var v=e.target.closest('[data-srcverify]');if(v){srcCustomVerify(v.getAttribute('data-srcverify'));return;}
var r=e.target.closest('[data-srcrefresh]');if(r){matIngest(r.getAttribute('data-srcrefresh'));return;}
var en=e.target.closest('[data-srcenable]');if(en){var s=srcById(en.getAttribute('data-srcenable'));srcCustomToggle(en.getAttribute('data-srcenable'),!(s&&s.enabled!==false));return;}
var mn=e.target.closest('[data-srcmenu]');if(mn){srcDrawerOpen(mn.getAttribute('data-srcmenu'));return;}
var mt=e.target.closest('[data-srcmats]');if(mt){state.msrc=mt.getAttribute('data-srcmats');state.mtab='all';location.hash='#material?src='+encodeURIComponent(state.msrc);return;}
var ur=e.target.closest('[data-srcunread]');if(ur){state.msrc=ur.getAttribute('data-srcunread');state.mtab='unread';location.hash='#material?src='+encodeURIComponent(state.msrc)+'&tab=unread';return;}
var card=e.target.closest('[data-card]');if(card&&!e.target.closest('button,a')){srcDrawerOpen(card.getAttribute('data-card'));return;}
});
body.addEventListener('change',function(e){
var t=e.target;if(t&&t.getAttribute&&t.getAttribute('data-srcen'))srcCustomToggle(t.getAttribute('data-srcen'),t.checked);
});
$('src-cat-grid' in window?'src-con-grid':'src-con-grid').addEventListener('click',function(e){
var add=e.target.closest('[data-srcadd]');if(add){srcAddOpen(add.getAttribute('data-srcadd'));return;}
var jump=e.target.closest('[data-srcjump]');if(jump){srcDrawerOpen(jump.getAttribute('data-srcjump'));return;}
var srch=e.target.closest('[data-srcsearch]');if(srch){$('src-q').focus();return;}
var web=e.target.closest('[data-srcwebread]');if(web){srcOpenReaderWeb('http'+'s:'+'//'+'example.org/notes/rule-engine');return;}
});
$('src-filters').addEventListener('click',function(e){
var c=e.target.closest('[data-srcscope]');if(!c)return;state.srcscope=c.getAttribute('data-srcscope');renderSources();
});
$('src-engine-line').addEventListener('click',function(e){
var h=e.target.closest('[data-srcenginehelp]');
if(h){var more=$('src-eng-more');if(more){more.hidden=!more.hidden;h.setAttribute('aria-expanded',String(!more.hidden));}return;}
var rt=e.target.closest('[data-srcretry]');if(rt)srcDoSearch();
});
$('src-stats').addEventListener('click',function(e){
var c=e.target.closest('[data-srcscope]');if(!c)return;state.srcscope=c.getAttribute('data-srcscope');renderSources();
});
$('src-drawer').addEventListener('click',function(e){
if(e.target.closest('#src-drawer-close')){this.hidden=true;return;}
var v=e.target.closest('[data-srcverifyin]');if(v){srcCustomVerify(v.getAttribute('data-srcverifyin'));return;}
var en=e.target.closest('[data-srcenable]');if(en){srcCustomToggle(en.getAttribute('data-srcenable'));return;}
var sv=e.target.closest('[data-srcsave]');if(sv){srcDrawerSave(sv.getAttribute('data-srcsave'));return;}
var ai=e.target.closest('[data-srcai]');if(ai){srcAiOpen(ai.getAttribute('data-srcai'));return;}
});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&!$('src-drawer').hidden)$('src-drawer').hidden=true;
});
$('src-add-btn').addEventListener('click',function(){srcAddOpen('rss');});
$('src-verify-all-btn').addEventListener('click',srcCustomVerifyAll);
$('src-ingest-all-btn').addEventListener('click',function(){matIngest('');});
$('src-pl-fold').addEventListener('click',function(){
var f=$('src-pipeline');var folded=!f.classList.contains('folded');f.classList.toggle('folded',folded);
this.setAttribute('aria-expanded',String(folded));this.textContent=folded?'展开说明':'精简';
});
$('sf-types').addEventListener('click',function(e){
var p=e.target.closest('[data-srcpick]');if(!p)return;state.srcAddType=p.getAttribute('data-srcpick');renderSrcAdd();
});
$('src-form').addEventListener('click',function(e){if(e.target===this||e.target.id==='sf-cancel')closeDlg('src-form');});
$('sf-test-btn').addEventListener('click',srcAddTest);
$('sf-ok').addEventListener('click',srcAddSubmit);
$('sf-fields').addEventListener('keydown',function(e){if(e.key==='Enter'&&e.target.tagName!=='TEXTAREA'){e.preventDefault();srcAddSubmit();}});
$('src-aicfg').addEventListener('click',function(e){if(e.target===this||e.target.id==='sac-cancel')closeDlg('src-aicfg');});
$('sac-ok').addEventListener('click',srcAiSave);
var srcDeb=null;
$('src-q').addEventListener('input',function(){
state.srcQ=this.value;clearTimeout(srcDeb);
if(!state.srcQ.trim()){state.srcResults=null;state.srcSearching=false;srcSearchRender();return;}
srcDeb=setTimeout(srcDoSearch,380);
});
$('src-q-clear').addEventListener('click',function(){state.srcQ='';$('src-q').value='';state.srcResults=null;srcSearchRender();$('src-q').focus();});
$('src-results').addEventListener('click',function(e){
var rt=e.target.closest('[data-srcretry]');if(rt){srcDoSearch();return;}
var c=e.target.closest('[data-srcclear]');if(c){state.srcQ='';$('src-q').value='';state.srcResults=null;srcSearchRender();return;}
var w=e.target.closest('[data-srcweb]');if(w){srcOpenReaderWeb(w.getAttribute('data-srcweb'));return;}
var b=e.target.closest('[data-srcbook]');if(b){srcOpenReaderBook(b.getAttribute('data-srcbook'));return;}
});
document.addEventListener('keydown',function(e){
if(e.key==='/'&&!e.isComposing){
var a=document.activeElement,typing=a&&(/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)||a.isContentEditable);
if(!typing&&!$('view-sources').hidden){e.preventDefault();$('src-q').focus();}
}
if(e.key==='Escape'&&!$('view-sources').hidden&&$('src-results')&&!$('src-results').hidden){
state.srcQ='';$('src-q').value='';state.srcResults=null;srcSearchRender();
}
});
}
bindSources();
