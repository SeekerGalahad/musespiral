var ROOT=new URLSearchParams(location.search).get('root')||'';
var state={
articles:[],
articlesLoaded:false,
filter:'all',
detail:null,
detailPath:'',
tab:'preview',
platforms:[],
stats:null,
meta:null,
jobs:[],
nonce: 0,
editLoaded:false,
editPath:'',
savedAt:'',
saveSt:'saved',
saveErr:'',
aiModel:null,
vbody:{},
vraw:{},
sel:null,
annotations:[],
discover:null,
mats:[],
msrc:'',
mtab:'all',
mdens:'compact',
mtype:'',
mspark:'',
mconf:'', // N-4 低置信筛选（'low'=confidence<0.5；服务端 confidence 参数同值）
mquery:'',
searchMode:'auto', // FEAT-1：auto|keyword|语义搜索模式（auto 按输入形态自动选 hybrid/keyword）
searchReady:null, // FEAT-1：语义路是否就绪（null=未探测；false=降级关键词）
msort:'score',
mdrawer:'',
mcoll:'',
mtag:'',
colls:[],
collOf:{},
tagList:[],
mqTotal:0,
sview:'grid',
irange:30,
srange:'week',
radar:null,
syn:{},msel:[],
srctype:'',
ssrc:'',
settings:null,
cs:null,
csd:null,
cslug:'',
cround:0,
cmPaths:{},
cmKnown:{},
cmWork:null,
offline:false,
diskLow:false,
rfail:'',
credsOK:null,gen:{size:'1024x1024',model:''}
};
var toastTimer=null,toastLeft=0,toastHover=false,toastBound=false,toastAct=null;
function $(id){ return document.getElementById(id);}
function esc(value){
return String(value==null?'' :value).replace(/[&<>"']/g,function(ch){
return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[ch];
});
}
function rootLabel(){
return ROOT||'默认（服务 --root）';
}
async function loadMeta(){
try{
state.meta=await getJSON('/api/meta');
}catch(err){
state.meta=null;
}
}
function articlesPath(){
return '/api/articles'+(ROOT?'?root='+encodeURIComponent(ROOT):'');
}
function articlePath(path){
return '/api/article?path='+encodeURIComponent(path);
}
function htmlPath(path){
return '/api/article/html?path='+encodeURIComponent(path)+(state.nonce?'&v=' + state.nonce:'');
}
function reviewPath(path,note){
return '/api/article/review?path='+encodeURIComponent(path)+'&note='+encodeURIComponent(note) +
(state.nonce?'&v=' + state.nonce:'');
}
function variantFile(article,code){
var slash=article.lastIndexOf('/');
var dir=slash>=0?article.slice(0,slash+1):'';
var name=article.slice(dir.length);
var stem=name.replace(/\.md$/i,'');
return dir+stem+'.'+code+'.md';
}
async function request(method,path,body){
var options={method:method,headers:{}};
if(body!==undefined){
options.headers['Content-Type']='application/json';
options.body=JSON.stringify(body);
}
var res;
try{res=await fetch(path, options);}catch(err){if(!state.offline){state.offline=true;renderSys();}throw err;}
if(state.offline){state.offline=false;renderSys();}
var data={};
try{ data=await res.json();}catch(err){ data={};}
if(!res.ok) throw new Error(data.error||data.message||'HTTP '+res.status);
if(data&&data.ok===false) throw new Error(data.error||data.message||'操作失败');
return data;
}
function getJSON(path){ return request('GET',path,undefined);}
function postJSON(path,body){ return request('POST',path,body);}
var QUEUED='已入队（进度见右上任务中心）',ER='error',WN='warning',OK='success',Z='</span>',D='</div>',QA=document.querySelectorAll.bind(document),JM=$('jobs-menu'),MC=$('modal-confirm');
var EA=null,HL=null,LF=null,AB=null,AA=null,PB=null,EG=null,ES=null;
function bindEditorRefs(){EA=$('edit-area');HL=$('edit-hl');LF=$('live-frame');AB=$('annot-bubble');AA=$('edit-annotate-ai');PB=$('publish-btn');EG=$('edit-gutter');ES=$('edit-save-btn');}
var STATUS_LABELS={draft:'草稿',reviewed:'已审',published:'已发布',archived:'已归档'};
function statusBadge(s){if(!STATUS_LABELS[s])s='draft';return '<span class="badge stamp '+s+'">'+STATUS_LABELS[s]+Z;}
function cardStatusBadge(item){
var c=item.checks||{},f=item.flags||{},s=item.status||'draft';
var st=(f.published?'published':s==='reviewed'?'reviewed':s==='draft'&&(c.fail||0)===0&&(c.warn||0)===0?'pending':STATUS_LABELS[s]?s:'draft');
var lb=st==='pending'?'待审':STATUS_LABELS[st];
return '<span class="badge stamp '+st+'" title="阶段：'+lb+'（待审=检查全通过且未发布的草稿）">'+lb+Z;}
async function waitForJob(id){
for(var i=0;i<900;i++){var j=await getJSON(`/api/jobs/${id}`);
if(j.kind==='pipeline'){state.pipe={steps:(j.result||{}).steps||[]};pPaint();}
if(j.status==='done'||j.status==='failed')return j;await new Promise(function(r){setTimeout(r,800);});}
throw new Error('等待任务超时');
}
async function trackJob(id){
var j=null;
try{
j=await waitForJob(id);
if(j.status==='failed')toast('任务失败：'+(j.error||'未知错误'),ER);
else{var r=j.result||{},SS2=r.steps||[],aa=[],i2;
if(SS2.length)for(i2=0;i2<SS2.length;i2++)aa=aa.concat(SS2[i2].artifacts||[]);
var d=r.output||r.dir||(r.outputs&&r.outputs.join('、'))||(r.media_id?'media_id '+r.media_id:'')||aa.join('、');
toast('任务完成'+(d?'：'+d:''),OK);}
}catch(err){toast('任务状态未知：'+err.message,ER);}
await refreshJobs();await refreshArticleState();
return j;
}
function queued(id){toast(QUEUED,OK);return trackJob(id);}
var SYSB=[
['offline','error','后端离线','后端 8766 不可达：数据可能过期，恢复后自动消失'],
['diskLow','warning','磁盘将满','磁盘剩余不足 5GB：保存/物料包/发布写入可能失败'],
['rfail','error','渲染失败','当前文章渲染失败：预览与上传不可用，先跑 musespiral check --render 排查']];
function renderSys(){
var b=$('sys-badge'),act=SYSB.filter(function(s){return s[0]==='rfail'?!!state.rfail:!!state[s[0]];});
if(!act.length){b.hidden=true;return;}
b.hidden=false;
b.innerHTML=I(act[0][1])+'<span>'+act[0][2]+'</span>';
b.title=act.map(function(s){return s[3];}).join('；');
}
function applyHealth(h){state.diskLow=(h.disk_free_gb||99)<5;state.credsOK=!!(h.credentials&&h.credentials.configured);renderSys();}
function probeHealth(){getJSON('/api/health').then(applyHealth).catch(function(){});}
async function refreshJobs(){
try{state.jobs=(await getJSON(`/api/jobs`)).jobs||[];}catch(err){state.jobs=[];}
var n=state.jobs.filter(function(j){return j&&(j.status==='queued'||j.status==='running');}).length;
$('jobs-badge').textContent=n;$('jobs-badge').hidden=!n;
if(!JM.hidden)renderJobs();
}
var JSTAT={queued:'排队',running:'运行中',done:'完成',failed:'失败'};
var JN={adapt:'生成变体',pack:'生成物料包',publish:'创建草稿',batch_adapt:'批量生成变体',fetch:'URL 抓取',status:'标记已审',annotate_rewrite:'批注 AI 修改',sync_stats:'数据回采',compose_generate:'创作再生成',ingest:'源刷新',clip:'网页剪藏',visual_export:'导出图片',pipeline:'一键金路径',book_search:'书籍搜索',book_fetch:'取文入素材库',tts_generate:'朗读合成',plugin_pipeline:'插件流水线',agent_run:'智能体编排',topic_radar:'选题雷达'};
var jobsDismissed={};
function jobAgo(t){var d=new Date(String(t||'').replace(' ','T')),s;
if(isNaN(d))return String(t||'').slice(5,16);
s=(Date.now()-d.getTime())/1000;
return s<60?'刚刚':s<3600?Math.floor(s/60)+' 分钟前':s<86400?Math.floor(s/3600)+' 小时前':('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2)+' '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);}
function jobTarget(j){var p=j.params||{};return p.article?String(p.article).split('/').pop():(p.book&&p.book.name?'《'+p.book.name+'》':((p.url||(p.source_id?'源 '+p.source_id:(p.all?'全部源':'')))||j.id));}
function jobPct(j){var m=String(j.phase||'').match(/^(\d+)\/(\d+)$/);return m?Math.round(m[1]/m[2]*100):null;}
var SUBX='';
function stepRows(S){
var h='',i,x;S=S||[];
for(i=0;i<S.length;i++){x=S[i];
h+='<div class="syn-step'+(x.status==='done'?' done':x.status==='running'?' running':'')+'"><i></i><span>['+(i+1)+'/'+(S.length||5)+'] '+esc(x.step)+(x.detail?' · '+esc(x.detail):'')+'</span>'+(x.artifacts&&x.artifacts.length?'<span class="pipe-arts">'+esc(x.artifacts.join('、'))+'</span>':'')+'</div>'}
return h;
}
function jobRow(j){
SUBX='';
var ic={queued:'clock',running:'refresh',done:'check',failed:'error'}[j.status]||'clock',pct=jobPct(j),ops='',sub,prog='';
if(j.status==='failed')ops='<button class="btn" data-resend="'+esc(j.id)+'">重试</button>';
if(j.status==='done'||j.status==='failed')ops+='<button class="btn" data-view-job="'+esc(j.id)+'">查看</button>';
if(j.status==='running')prog='<div class="job-prog'+(pct==null?' indet':'')+'"><i'+(pct==null?'':' style="width:'+pct+'%"')+'></i></div>';
sub=j.status==='failed'?(j.error||'未知错误'):[j.phase?'进度 '+j.phase:'',((j.result&&j.result.output)?String(j.result.output).split('/').pop():'')].filter(function(x){return x;}).join(' · ');
if(j.kind==='pipeline')SUBX=stepRows((j.result||{}).steps);
return '<div class="job-row"><div class="job-top"><span class="job-st s-'+esc(j.status)+'">'+I(ic)+'</span><span class="badge '+esc(j.status)+'">'+(JSTAT[j.status]||esc(j.status))+Z+'<span class="job-desc">'+esc((JN[j.kind]||j.kind)+'（'+j.kind+'）· '+jobTarget(j))+Z+'<span class="job-time">'+jobAgo(j.created)+Z+D+(sub?'<div class="job-sub">'+esc(sub)+D:'')+(SUBX?'<div class="job-sub">'+SUBX+D:'')+prog+(ops?'<div class="job-ops">'+ops+D:'')+D;}
function renderJobs(){
var live=state.jobs.filter(function(j){return j&&(j.status==='queued'||j.status==='running');});
var shown=state.jobs.filter(function(j){return j&&!jobsDismissed[j.id];});
var done=shown.filter(function(j){return j&&j.status==='done';}).length;
JM.innerHTML='<div class="jobs-head">后台任务（'+live.length+' 进行中）<button class="jobs-clear" id="jobs-clear" type="button"'+(done?'':' disabled')+'>清空已完成</button></div>'+(shown.length?shown.map(jobRow).join(''):'<div class="job-sub" style="padding:10px 4px;">暂无任务</div>');}
var jobsPoll=null;
function jobsPollStop(){if(jobsPoll){clearInterval(jobsPoll);jobsPoll=null;}}
function viewJob(j){
$('viewer-title').textContent='任务 '+j.kind+' · '+(JSTAT[j.status]||j.status)+' · '+jobAgo(j.created);
$('viewer-body').textContent=JSON.stringify({id:j.id,kind:j.kind,status:j.status,params:j.params||{},phase:j.phase||'',error:j.error||'',result:j.result||'',created:j.created||'',finished:j.finished||''},null,2);
$('viewer').hidden=false;}
async function handleResend(id){
var j=state.jobs.find(function(x){return x&&x.id===id;});
if(!j)return;
if(j.kind==='status'){toast('该任务类型不支持重发',ER);return;}
if(j.kind==='compose_generate'){toast('请在创作台重新发起再生成',ER);return;}
var p=j.params||{},b={article:p.article,confirm:true};
if(j.kind==='adapt')b.platform=p.platform;
if(j.kind==='pack'){b.platform=p.platform;b.variant=p.variant||null;}
if(j.kind==='batch_adapt')b.platforms=p.platforms||[];
if(j.kind==='fetch')b={url:p.url,confirm:true};
var u={adapt:'/api/action/adapt',pack:'/api/action/pack',publish:'/api/action/publish',fetch:'/api/action/fetch'}[j.kind]||`/api/action/batch_adapt`;
try{var d=await postJSON(u,b);toast('已重新入队',OK);await trackJob(d.job_id);}
catch(err){toast('重发失败：'+err.message,ER);await refreshJobs();}
}
async function handleMarkReviewed(){
try{await postJSON(`/api/action/status`,{article:state.detailPath,status:'reviewed',confirm:true});
var w=Array.isArray(state.detail&&state.detail.checks)?state.detail.checks.filter(function(x){return x&&x.level==='WARN';}).length:0;
toast(w?'已标记为已审（reviewed）；仍有 '+w+' 项 WARN 未处理':'已标记为已审（reviewed）',w?WN:OK);await refreshArticleState();}
catch(err){toast('标记已审失败：'+err.message,ER);}
}
function dirOf(p){var i=String(p||'').lastIndexOf('/');return i>=0?p.slice(0,i+1):'';}
function safeSrc(u,dir,img){
if(img?/^(https?:|data:)/i.test(u):/^(https?:|#)/i.test(u))return u;
u=String(u).replace(/[?#].*$/,'').replace(/^\.\//,'');
try{u=decodeURIComponent(u);}catch(e2){}
return img?'/asset?path='+encodeURIComponent(dir+u):'#';}
function mdInl(s,dir){return s.replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g,function(m,a,u){return '<img alt="'+a+'" src="'+safeSrc(u,dir,true)+'">';}).replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g,function(m,t,u){return '<a href="'+safeSrc(u,dir,false)+'">'+t+'</a>';}).replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/`([^`\n]+)`/g,'<code>$1</code>');}
var BANCHOR=/^<!--b:[A-Za-z0-9_-]+-->[ \t]*$/;
function mdToHtml(md,dir){
var ls=String(md).replace(/\r/g,'').split('\n'),o=[],p=[],i=0;
function fl(){if(p.length){o.push('<p>'+mdInl(p.join('<br>'),dir)+'</p>');p=[];}}
while(i<ls.length){var L=ls[i];
if(BANCHOR.test(L)){i++;continue;}
if(/^```/.test(L)){fl();var b=[];i++;while(i<ls.length&&!/^```/.test(ls[i]))b.push(ls[i++]);o.push('<pre><code>'+esc(b.join('\n'))+'</code></pre>');i++;continue;}
var h=L.match(/^(#{1,4})\s+(.*)$/);
if(h){fl();o.push('<h'+h[1].length+'>'+mdInl(esc(h[2]),dir)+'</h'+h[1].length+'>');i++;continue;}
if(/^>\s?/.test(L)){fl();var q=[];while(i<ls.length&&/^>\s?/.test(ls[i]))q.push(ls[i++].replace(/^>\s?/,''));o.push('<blockquote>'+mdInl(esc(q.join('<br>')),dir)+'</blockquote>');continue;}
if(/^\s*(?:[-*]|\d+\.)\s+/.test(L)){fl();var it=[];while(i<ls.length){var m2=ls[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);if(!m2)break;it.push('<li>'+mdInl(esc(m2[1]),dir)+'</li>');i++;}o.push('<ul>'+it.join('')+'</ul>');continue;}
var e=esc(L);if(!e.trim()){fl();i++;continue;}p.push(e.trim());i++;}
fl();return o.join('\n');
}
function toast(message,kind,action){
var el=$('toast');
$('toast-text').textContent=message;
el.className='toast '+(kind==='error'||kind==='warning'?kind:'success');
var ab=$('toast-action');
toastAct=null;
if(action&&action.label){ab.hidden=false;ab.textContent=action.label;toastAct=action.fn||null;}
else ab.hidden=true;
el.hidden=false;
toastLeft=action?8000:(kind==='error'||kind==='warning')?5000:3000;
if(!toastBound){
toastBound=true;
el.addEventListener('mouseenter',function(){toastHover=true;if(toastTimer)clearTimeout(toastTimer);toastTimer=null;});
el.addEventListener('mouseleave',function(){toastHover=false;if(!$('toast').hidden)toastArm();});
ab.addEventListener('click',function(){var f=toastAct;toastHide();if(f)f();});
}
if(!toastHover)toastArm();
}
function toastArm(){if(toastTimer)clearTimeout(toastTimer);toastTimer=setTimeout(toastHide,toastLeft);}
function toastHide(){var el=$('toast');el.hidden=true;toastTimer=null;}
function askConfirm(options){
return new Promise(function(resolve){
var modal=$('modal');
$('modal-title').textContent=options.title||'确认操作';
$('modal-message').textContent=options.message||'';
MC.textContent=options.confirmText||'确认';
modal.hidden=false;
MC.focus();
function finish(value){
modal.hidden=true;
MC.removeEventListener('click',onOk);
$('modal-cancel').removeEventListener('click',onCancel);
modal.removeEventListener('click',onOverlay);
document.removeEventListener('keydown',onKey);
resolve(value);
}
function onOk(){ finish(true);}
function onCancel(){ finish(false);}
function onOverlay(event){ if(event.target===modal) finish(false);}
function onKey(e){ if(e.isComposing||e.keyCode===229)return; if(e.key==='Escape') finish(false);}
MC.addEventListener('click',onOk);
$('modal-cancel').addEventListener('click',onCancel);
modal.addEventListener('click',onOverlay);
document.addEventListener('keydown',onKey);
});
}
function detailTitle(path){
if(state.detail&&state.detail.title) return String(state.detail.title);
var item=state.articles.find(function(a){ return a.path===path;});
return item&&item.title?String(item.title) :path;
}
async function loadArticles(force){
if(state.articlesLoaded&&!force) return;
if(!state.articlesLoaded) renderSkeleton();
try{
var data=await getJSON(articlesPath());
state.articles=Array.isArray(data.articles)?data.articles :[];
state.articlesLoaded=true;
}catch(err){
state.articles=[];
toast('加载文章队列失败：'+err.message,ER);
}
renderRoot();
}
function readMdFile(file){
return new Promise(function(resolve,reject){
var r=new FileReader();
r.onload=function(){resolve(String(r.result||''));};
r.onerror=function(){reject(r.error||new Error('读取失败'));};
r.readAsText(file);
});
}
async function handleDropFiles(files){
for(var i=0;i<files.length;i++){
var f=files[i];
try{
var content=await readMdFile(f);
var d=await postJSON(`/api/article/save`,{article:f.name,content:content,confirm:true});
toast('已保存文章：'+(d.article||f.name),OK);
}catch(err){
toast('保存失败（'+f.name+'）：'+err.message,ER);
}
}
await loadArticles(true);
viewRepaint('publish');
}
async function loadPlatforms(force){
if(state.platforms.length&&!force) return;
try{
var data=await getJSON('/api/platforms');
state.platforms=Array.isArray(data)?data :[];
}catch(err){
state.platforms=[];
}
}
async function loadStats(force){
if(state.stats&&!force) return;
try{
state.stats=await getJSON('/api/stats');
}catch(err){
state.stats=null;
}
}
async function loadDetail(path,force){
if(!force&&state.detail&&state.detailPath===path) return state.detail;
try{
state.detail=await getJSON(articlePath(path));
state.detailPath=path;
}catch(err){
state.detail=null;
state.detailPath=path;
toast('加载文章详情失败：'+err.message,ER);
}
return state.detail;
}
function renderRoot(){
var label=(state.meta&&state.meta.root)||rootLabel();
$('root-path').textContent=label;
}
// 视图标题（重构设计 §2.1 顶栏 breadcrumb；与各视图 h1.view-title 同名）
var CRUMBS={sources:'源中枢',material:'素材库',books:'书籍',compose:'创作工坊',design:'视觉编辑',publish:'发布中心',insight:'洞察',settings:'设置',article:'文章详情',plugins:'插件','plugin-run':'流水线'};
function renderNav(view){
QA('[data-nav]').forEach(function(el){
el.classList.toggle('is-active',el.getAttribute('data-nav')===view);
});
var c=$('crumb-title');if(c)c.textContent=CRUMBS[view]||view;
}
// rail 徽标（重构设计 §2.1）：>0 显数、0 隐藏；数据由各 render 顺带回填（零新请求）
function updateNavBadge(id,count){
var el=$(id);if(!el)return;
el.hidden=!(count>0);
el.textContent=count>0?count:'';
}
var VIEWS=['sources','material','books','compose','design','publish','insight','article','settings','plugins','plugin-run'];
// 旧 hash 别名（P1 视图重命名，深链兼容）：queue→publish / dashboard→insight / discover→material
// PL-3：plugin-pipeline（画布按流水线定义打开）别名到 plugin-run 视图（插件系统设计 §八/评审 §2.3 拆分）
var HALIAS={queue:'publish',dashboard:'insight',discover:'material','plugin-pipeline':'plugin-run'};
function showView(view){
VIEWS.forEach(function(name){
var el=$('view-'+name);
if(el) el.hidden=name!==view;
});
renderNav(view);
}
// M3 视图懒加载（幂等 Promise；失败清缓存可重试）。视图用顶层函数声明，注入即定义。
// ARCH-1 壳 HTML 外移 + ② CSS 拆分：非 publish 视图的 HTML 骨架与专属样式都不在
// 首屏（壳内是空占位 <section id="view-<name>" hidden>）——装载三步走：先
// assets/views/<view>.css（<link> 样式先于骨架注入，进视图零裸闪）再
// assets/views/<view>.html.js（innerHTML 填充占位 + __onViewHtml 补装钩子）最后
// assets/views/<view>.js（行为脚本）；首屏 publish 的 HTML/CSS 都在壳+core.css，
// 跳过前两步不触发懒加载。样式/骨架产物由 build_ui.py 每懒视图一份（缺文件即
// 装载失败走 catch 清缓存可重试，与脚本缺文件同语义）。
var __viewPromises={};
function __loadScript(src){return new Promise(function(res,rej){
var s=document.createElement("script");s.src=src;
s.onload=res;s.onerror=function(){rej(new Error("脚本加载失败: "+src));};
document.head.appendChild(s);});}
function __loadCss(src){return new Promise(function(res,rej){
var l=document.createElement("link");l.rel="stylesheet";l.href=src;
l.onload=res;l.onerror=function(){rej(new Error("样式加载失败: "+src));};
document.head.appendChild(l);});}
// HTML 注入后的补装钩子：①mountChrome 重跑（[data-icon] 图标装配在 core 装载时
// 只覆盖壳，懒视图的图标在此补）；②article 编辑器全局重抓取；③绑定型独立产物
// （outline/rolling/dual/tts/versions/quickstart）boot 时目标 DOM 未到即早退，
// 在此重调（各文件自带幂等门，重复调用零副作用）。
var VSTANDALONE={compose:['__mdpubOutline','__mdpubRolling','__mdpubDual','__mdpubNovel'],article:['__mdpubTts','__mdpubVersions'],insight:['__mdpubQuickstart']};
function __onViewHtml(view){
mountChrome();
if(view==='article')bindEditorRefs();
if(view==='sources'){on2('src-clip-btn',function(){clipOpen('clip');});on2('src-crawl-btn',function(){clipOpen('crawl');});}
if(view==='material')on2('mat-reg-btn',function(){clipOpen('video');});
// FEAT-5 骨架屏（首访窗口）：HTML 已注入、enter 的数据在途——列表区先画骨架，
// 免「空白/空态误闪」（renderMaterials 首轮 state.mats=[] 时 mat-empty 会闪「暂无素材」）。
// 只在首访走这段（ensureView 缓存后 __onViewHtml 不再执行；回访旧列表保持不闪）。
if(view==='material'&&!(state.mats||[]).length){var ml=$('mat-list');if(ml)ml.innerHTML=skelCards(4);}
if(view==='sources'&&!matSrcs().length){var sr=$('src-rows');if(sr)sr.innerHTML='<tr><td colspan="9"><div class="skel">'+skelRows(4)+'</div></td></tr>';}
var xs=VSTANDALONE[view];
if(xs)xs.forEach(function(g){var w=window[g];if(w&&w.boot)w.boot();});
}
function ensureView(view){
if(!__viewPromises[view]){__viewPromises[view]=(view==='publish'?Promise.resolve():__loadCss("assets/views/"+view+".css").then(function(){return __loadScript("assets/views/"+view+".html.js");}).then(function(){__onViewHtml(view);})).then(function(){return __loadScript("assets/views/"+view+".js");}).catch(function(err){delete __viewPromises[view];throw new Error("视图装载失败: "+view+"（"+err.message+"）");});}
return __viewPromises[view];}
// ── 跨视图调用治理（2026-11-20 台账 A 类专项）──────────────────────────
// M3 拆分初期靠「core 预置 no-op 占位符 + 视图顶层同名函数覆盖」兜底跨块调用——
// 视图未注入时调用被静默吞掉（台账 A-1~A-5），且 matActsClick 一类局部函数在
// core 侧根本不可达（ctx 菜单点击即 ReferenceError）。治理后契约：
//   1. core 只经 VAPI 注册表触达视图函数（enter=路由入口 / repaint=任务后台
//      完成后的当前视图重绘 / article.setTab=⌘1-4·cpGo 快路径）；
//   2. 各视图脚本注入时顶层登记（ensureView 完成即就绪，消费必在注入后）；
//   3. 无视图状态依赖的装载/渲染工具（load*/dkey/renderSkeleton/pPaint 等）
//      真身归 core 段，占位符整族退役（检测器=scripts/audit_cross_view_deps.py）。
// repaint 只登记原 refreshArticleState 会重绘的五视图（books/compose/design/
// plugins/plugin-run 维持原「后台任务不触发重绘」语义，未登记即 no-op）。
var VAPI={};
// 返回 repaint 结果（Promise 或同步返回值）——调用方可 await（refreshArticleState
// 原语义即等待重绘完成），也可不 await（matIngest 等原同步触发点）。
function viewRepaint(view){var a=VAPI[view];return a&&a.repaint?a.repaint():undefined;}
function articleStatus(a){return String(a.status||'draft');}
function I(n){return typeof mdpubIcon==='function'?mdpubIcon(n):'';}
var PN={xhs:'小红书',channels:'视频号',douyin:'抖音',zhihu:'知乎'};
function routeInfo(){
var raw=(location.hash||'#publish').replace(/^#/,'');
var at=raw.indexOf('?');
return{
name:(at>=0?raw.slice(0,at) :raw)||'publish',
params:new URLSearchParams(at>=0?raw.slice(at+1):'')
};
}
async function renderRoute(){
var route=routeInfo();
// hash 路由白名单（重构设计 §2.2）：六视图 + #article 深链；旧名别名映射；未知回落 #publish
var view=HALIAS[route.name]||route.name;
if(VIEWS.indexOf(view)<0)view='publish';
showView(view);
await ensureView(view); // M3：脚本注入完成后再渲染视图
var va=VAPI[view]; // 各视图登记的路由入口（分支体见各 @view 块 VAPI.<view>=…）
if(va&&va.enter)await va.enter(route);
renderRoot();
}
var DD=[['深度','depth',40],['独特','uniqueness',30],['可读','readability',20],['适配','adaptability',10]];
async function loadSettings(){
try{state.settings=await getJSON('/api/settings');}
catch(err){state.settings=null;}
}
function matSrcs(){return (state.settings&&state.settings.sources&&state.settings.sources.list)||[];}
// 源中枢数据面（契约 §2.1-1）：GET /api/sources 与 settings.sources 同源同形，取其权威行
async function loadSources(){
try{var d=await getJSON('/api/sources');
if(state.settings)state.settings.sources=d;else state.settings={sources:d};
}catch(err){}
}
async function refreshSrcData(){await Promise.all([loadSettings(),loadSources()]);}
// 源采集（跨视图共享，归 core 段）：消费点在 @view:sources 两处（源行刷新/全部采集）
// 与 @view:material 一处（素材页头）——2026-11-20 修红：原在 @view:material，M3 懒
// 加载后直入 #sources 时占位 no-op → 源中枢两采集入口失灵；归 core 后三处消费同真身
// （loadMaterials/renderSources/renderMaterials 仍为视图占位，运行期求值，语义不变）
async function matIngest(sid){
	try{
	var d=await postJSON('/api/action/ingest',sid?{source_id:sid,confirm:true}:{all:true,confirm:true});
	await queued(d.job_id);
	await Promise.all([loadMaterials(),refreshSrcData()]);
	if(!$('view-sources').hidden)viewRepaint('sources');
	viewRepaint('material');
	}catch(err){toast('刷新任务提交失败：'+err.message,ER);}
	}
// 7 类型全量映射（重构设计 §3.2：补齐 image/video）+ 类型图标 + license 三态徽（对齐 model.py 枚举）
var MTY={text:'文档',image:'图片',video:'视频',link:'链接',radar_signal:'雷达信号',crawled:'抓取',derived:'衍生'};
var MTYI={text:'docs',image:'image',video:'video',link:'link',radar_signal:'radar',crawled:'globe',derived:'derived'};
var LIC={'CC-BY':['lic-ok','CC-BY · 可用'],'ALL-RIGHTS':['lic-warn','版权 · 注意']};
// N-4 置信度三态徽（正典=《总体方案》§二 N-4）：✓ 已核验（verified=true，人工判定）
// / ? 待核验（有 AI 自评未人工核验）/ ⚠ 低置信（confidence<0.5）。无自评且未核验
// 的原始素材不显徽（无信号不噪声）。threshold 单一权威=core（lineage.js 复用）。
var CONF_LOW=0.5;
function confBadge(m){
var t=m.triage||{},c=t.confidence;
if(t.verified)return '<span class="badge pass" title="人工核验通过（verified）">✓ 已核验'+Z;
if(c==null)return '';
return c<CONF_LOW?'<span class="badge warn" title="AI 自评置信度 '+c+'（低于 0.5），引用前先核对原文">⚠ 低置信 '+c+Z:'<span class="badge" title="AI 自评置信度 '+c+'，尚未人工核验（详情「血统」tab 可标记核验）">? 待核验 '+c+Z;
}
function licBadge(m){
var l=String(m.license||'UNKNOWN'),x=(m.payload||{}).meta||{};
if(l==='AI-GENERATED')return '<span class="lic-badge lic-ai">AI-GENERATED'+Z+(x.ai_generated?'<span class="gen-src">'+esc(x.provider)+' · '+esc(x.model)+' · '+esc(String(x.generated_at||'').replace('T',' '))+Z:'')+confBadge(m);
var v=LIC[l]||['lic-unk','许可未知'];
return '<span class="lic-badge '+v[0]+'" title="license 枚举">'+v[1]+Z+confBadge(m);
}
function renderCollRail(){
if(!$('coll-static'))return; // ARCH-1：material HTML 未注入时 rail DOM 不在——维持 VAPI 同款「未注入即 no-op」语义（注入后 enter 必重绘）
var L=state.mats||[],sn=0,ur=0,hi=0,de=0;
L.forEach(function(m){var t=m.triage||{};if(t.starred)sn++;if(!t.read){ur++;if((m.score||0)>=80)hi++;}if(m.type==='derived')de++;});
function ci(act,attrs,label,n){return '<button class="coll-item'+(act?' act':'')+'" '+attrs+'>'+label+(n==null?'':' <span class="coll-n">'+n+'</span>')+'</button>';}
$('coll-static').innerHTML=
ci(state.msrc===''&&state.mtab==='all','data-msrc="" data-mtab="all"','全部',L.length)+
ci(state.mtab==='starred','data-mtab="starred"','收藏',sn)+
ci(state.mtab==='unread','data-mtab="unread"','未读',ur);
$('coll-smart').innerHTML=
ci(state.mtab==='hiunread','data-mtab="hiunread"','◈ 高分未读',hi)+
ci(state.mtab==='derived','data-mtab="derived"','◈ 衍生素材',de)+
ci(state.mtab==='revisit','data-mtab="revisit"','◈ 待重温',null);
var srcs=matSrcs(),names={};
srcs.forEach(function(s){names[s.id]=s;});
$('coll-srcs').innerHTML=srcs.length?srcs.map(function(s){
var n=L.filter(function(m){return ((m.origin||{}).source_id||'')===s.id;}).length;
return ci(state.msrc===s.id,'data-msrc="'+esc(s.id)+'" data-mtab="all"',esc(s.label||s.id),n);
}).join(''):'<p class="action-hint">暂无源（在源中枢注册）</p>';
// M3 手工集合（F62）：色点+名+双计数（「含 m 已删」琥珀——墓碑非静默）；
// 空段整隐；双击重命名、右键重命名/删除集合
var cols=state.colls||[];
$('coll-manual-sec').hidden=!cols.length;
$('coll-manual').innerHTML=cols.map(function(c){
var n=c.active_member_count,d=c.member_count-n;
return '<button class="coll-item'+(state.mcoll===c.id?' act':'')+'" data-mcoll="'+esc(c.id)+'" title="查看集合成员（双击重命名；右键更多）"><span><i class="coll-dot" style="background:var(--'+esc(c.color)+',var(--ink-2))"></i>'+esc(c.name)+(d?' <span style="color:var(--amber)">（含 '+d+' 已删）</span>':'')+'</span><span class="coll-n">'+n+'</span></button>';
}).join('');
// M3 按标签（F63）：全库频次降序全量；空段整隐；点击过滤/再点退出
var tl=state.tagList||[];
$('coll-tags-sec').hidden=!tl.length;
$('coll-tags').innerHTML=tl.map(function(t2){
return ci(state.mtag===t2.name,'data-mtag="'+esc(t2.name)+'"','#'+esc(t2.name),t2.count);
}).join('');
}
// 入口 A（§三）：多选→AI 综合（零模式开关；AI 永不自动落库；三态点；amber 无重试）
var CK=[['channels','视频号'],['douyin','抖音'],['xhs','小红书'],['kuaishou','快手']];
// aiPost/handleKbSave 已迁 @view:settings（唯一消费点=bindSettings；原居 core 时其
// renderSettings 调用构成跨视图直呼——A 类专项 2026-11-20）
async function refreshArticleState(){
state.nonce += 1;
state.vraw={};
await loadArticles(true);
if(state.detailPath) await loadDetail(state.detailPath,true);
// 原四视图定点重绘改经 VAPI.repaint（books/compose/design/plugins/plugin-run
// 未登记=维持原「后台任务不触发重绘」语义；A 类专项前此四处直呼视图函数）
await viewRepaint(HALIAS[routeInfo().name]||routeInfo().name);
}
async function handleAdapt(platform,button){
var path=state.detailPath;
if(!path) return;
var title=detailTitle(path);
var ok=await askConfirm({
title:'生成平台变体',
message:'将「'+title+'」改写为 '+platform+' 变体；同名已存在拒绝覆盖。',
confirmText:'生成变体'
});
if(!ok) return;
button.disabled=true;
try{
var data=await postJSON('/api/action/adapt', {article: path, platform: platform, confirm: true});
await queued(data.job_id);
}catch(err){
toast('生成变体失败：'+err.message,ER);
}finally{
button.disabled=false;
}
}
async function handlePack(platform,variant,button){
var path=state.detailPath;
if(!path||!platform) return;
var ok=await askConfirm({
title:'生成物料包',
message:'为「'+detailTitle(path)+'」生成 '+platform+' 物料包四件套'+(variant?'（变体 '+variant+'）':'')+'；已存在拒绝覆盖。',
confirmText:'生成物料包'
});
if(!ok) return;
button.disabled=true;
try{
var data=await postJSON('/api/action/pack', {article: path, platform: platform, variant: variant, confirm: true});
await queued(data.job_id);
}catch(err){
toast('生成物料包失败：'+err.message,ER);
}finally{
button.disabled=false;
}
}
async function handlePublish(button){
var path=state.detailPath;
if(!path) return;
var ok=await askConfirm({
title:'创建草稿',
message:'为「'+detailTitle(path)+'」创建公众号草稿（不提交发表）；需已配置凭证与封面。',
confirmText:'创建草稿'
});
if(!ok) return;
button.disabled=true;
try{
var data=await postJSON('/api/action/publish', {article: path, confirm: true});
await queued(data.job_id);
}catch(err){
toast('创建草稿失败：'+err.message,ER);
}finally{
button.disabled=false;
}
}
async function handleBatch(button){
var path=state.detailPath;
if(!path) return;
var ok=await askConfirm({title:'四平台一键生成变体',message:'为「'+detailTitle(path)+'」批量生成 xhs/channels/douyin/zhihu 四平台变体（已存在跳过）',confirmText:'生成'});
if(!ok) return;
button.disabled=true;
try{
var data=await postJSON('/api/action/batch_adapt', {article: path, platforms: ['xhs','channels','douyin','zhihu'], confirm: true});
await queued(data.job_id);
}catch(err){
toast('批量生成失败：'+err.message,ER);
}finally{
button.disabled=false;
}
}
async function handleFetch(btn){
var url=$('fetch-url').value.trim();
if(!url){toast('请输入微信文章链接',ER);return;}
btn.disabled=true;
try{var d=await postJSON('/api/action/fetch', {url: url, confirm: true});await queued(d.job_id);}
catch(err){toast('抓取失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
async function onRefresh(){
var button=$('refresh-btn');
button.disabled=true;
try{
state.articlesLoaded=false;
state.detail=null;
state.stats=null;
state.vraw={};
state.nonce += 1;
await renderRoute();
toast('已刷新',OK);
}catch(err){
toast('刷新失败：'+err.message,ER);
}finally{
button.disabled=false;
}
}
var CP={items:[],cur:0};
var gSeq=0,GK={s:'sources',m:'material',b:'books',c:'compose',p:'publish',i:'insight'};
function cpClose(){$('cp').hidden=true;CP.items=[];CP.cur=0;}
function cpOpen(){
$('cp').hidden=false;$('cp-input').value='';cpBuild('');
$('cp-input').focus();
if(!state.articlesLoaded)loadArticles().then(function(){if(!$('cp').hidden)cpBuild($('cp-input').value);});}
function cpArts(q){
q=q.toLowerCase();
return state.articles.filter(function(a){var t=String(a.title||a.path).toLowerCase(),p=String(a.path||'').toLowerCase();return !q||t.indexOf(q)>=0||p.indexOf(q)>=0;}).slice(0,8);}
function cpFire(act){
if(act==='batch')handleBatch($('variants-batch-btn'));
if(act==='publish')handlePublish(PB);}
function cpGo(path,tab,act){
cpClose();
var aa=VAPI.article;
if(routeInfo().name==='article'&&state.detailPath===path){if(aa&&aa.setTab)aa.setTab(tab,true);cpFire(act);}
else{state.pendAct=act||'';location.hash='#article?path='+encodeURIComponent(path)+'&tab='+tab;}}
function cpBuild(q){
q=String(q||'');
var pre=q.charAt(0);
if(pre==='>'||pre==='@'||pre==='#')q=q.slice(1);
q=q.toLowerCase();
function hit(s){return !q||s.indexOf(q)>=0;}
// P4 前缀模式：# 素材（本地 state 线性扫，Enter=开详情抽屉）；@ 文章+文章操作；> 命令（跳转/操作）
if(pre==='#'){
if(!(state.mats||[]).length)loadMaterials().then(function(){if(!$('cp').hidden)cpBuild($('cp-input').value);});
CP.items=(state.mats||[]).filter(function(m){var o=m.origin||{};return hit(String(m.title||o.title||o.uri||m.id)+' '+String(o.source_id||''));}).slice(0,8).map(function(m){return{g:'素材',icon:MTYI[m.type]||'tag',t:String(m.title||((m.origin||{}).title)||((m.origin||{}).uri)||m.id),run:function(){cpClose();location.hash='#material?m='+encodeURIComponent(m.id);}};});
CP.cur=0;cpRender();return;
}
var arts=pre==='>'?[]:cpArts(q),items=[],t='';
if(!$('view-article').hidden&&state.detailPath)t=state.detailPath;
else if(arts.length)t=arts[0].path;
arts.forEach(function(a){items.push({g:'文章',icon:'file',t:String(a.title||a.path),s:STATUS_LABELS[articleStatus(a)]||'草稿',run:function(){cpClose();location.hash='#article?path='+encodeURIComponent(a.path)+'&tab=edit';}});});
if(t&&pre!=='>'){var nm=detailTitle(t);
[{g:'操作',icon:'edit',t:'生成变体',run:function(){cpGo(t,'variants','batch');}},{g:'操作',icon:'publish',t:'创建草稿（发布）',run:function(){cpGo(t,'actions','publish');}},{g:'操作',icon:'check',t:'检查',run:function(){cpGo(t,'checks','');}}].forEach(function(o){if(hit(o.t))items.push(Object.assign({s:nm},o));});}
if(pre!=='@')[['sources','源中枢','settings'],['material','素材库','search'],['books','书籍','docs'],['design','视觉编辑','image'],['compose','创作工坊','edit'],['publish','发布中心','file'],['insight','洞察','star'],['settings','设置','settings']].forEach(function(n){if(hit('跳转：'+n[1]))items.push({g:'跳转',icon:n[2],t:'跳转：'+n[1],run:function(){cpClose();location.hash='#'+n[0];}});});
if(pre!=='@'&&hit('刷新数据'))items.push({g:'跳转',icon:'refresh',t:'刷新数据',run:function(){cpClose();onRefresh();}});
CP.items=items;CP.cur=0;cpRender();}
function cpRender(){
var html='',last='',i,it;
if(!CP.items.length)html='<div class="cp-empty">没有匹配的文章或操作</div>';
for(i=0;i<CP.items.length;i++){it=CP.items[i];
if(it.g!==last){last=it.g;html+='<div class="cp-group">'+it.g+D;}
html+='<div class="cp-item'+(i===CP.cur?' is-cursor':'')+'" data-cp="'+i+'" role="option">'+I(it.icon)+'<span class="cp-t">'+esc(it.t)+Z+(it.s?'<span class="cp-s">'+esc(it.s)+Z:'')+D;}
$('cp-list').innerHTML=html;
var c=$('cp-list').querySelector('.is-cursor');
if(c)c.scrollIntoView({block:'nearest'});}
// cpMain 已随 ⌘⏎ 绑定迁 @view:article（唯一消费点=article 域键处理器；A 类专项）
// 三态主题（重构设计 §1.2/§六 P1）：light→dark→auto 轮换；auto=移除 data-theme，
// 暗色变量仍由 @media prefers-color-scheme:dark 的 html:not data-theme=light 提供；
// 选择存 localStorage 键 musespiral-theme（裁决 §八-6 缺省 localStorage）。
var THEME_ORDER=['light','dark','auto'];
function applyTheme(mode){
if(THEME_ORDER.indexOf(mode)<0)mode='auto';
if(mode==='auto')document.documentElement.removeAttribute('data-theme');
else document.documentElement.setAttribute('data-theme',mode);
try{localStorage.setItem('musespiral-theme',mode);}catch(e){}
var ic=mode==='light'?'sun':(mode==='dark'?'moon':'monitor');
var t=$('theme-icon');
if(t){t.setAttribute('data-icon',ic);t.innerHTML=I(ic);}
var b=$('theme-toggle');
if(b){var dk=mode==='dark'||(mode==='auto'&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);
b.title='主题：'+(mode==='auto'?'跟随系统（当前：'+(dk?'深色':'浅色')+'）':(dk?'深色':'浅色'))+'（点击切换）';
b.setAttribute('aria-pressed',dk?'true':'false');}
// FEAT-5 暗色补全：live 预览是独立 iframe（srcdoc 一次性注入，不随父文档 data-theme
// 变化）——切主题后若文章预览可见即重注。视图函数经 VAPI 触达（跨视图治理契约），
// article 块登记 live=renderLive；视图未注入时 VAPI.article 未登记即自然跳过。
var va=VAPI.article;
if(va&&va.live&&!$('view-article').hidden)va.live();
}
function cycleTheme(){
// 基于已应用态轮换：applyTheme 后 DOM 属性即真值（auto=无属性）
var cur=document.documentElement.getAttribute('data-theme')||'auto';
applyTheme(THEME_ORDER[(THEME_ORDER.indexOf(cur)+1)%THEME_ORDER.length]);
}
function init(){
// 切换先置 data-theme 再渲染（不闪）——主题恢复必须先于任何绘制
var savedTheme=null;
try{savedTheme=localStorage.getItem('musespiral-theme');}catch(e){}
applyTheme(savedTheme||'auto');
try{if(localStorage.getItem('musespiral-rail')==='min')document.documentElement.setAttribute('data-rail','min');}catch(e){}
renderRoot();
loadMeta().then(renderRoot); // meta 到达后显示真实 root
bindCore();
if(!location.hash) history.replaceState(null,'','#publish');
renderRoute();
refreshJobs(); // R4：任务角标初始装载
probeHealth(); // 档A：静默徽章磁盘/凭证信号
// 独立产物懒加载（quickstart E-4 §3.4 / outline G-T2 §三 / rolling G-T4 §五 / dual G-T3 §四 / tts G-T5 §六 / versions E-6 §3.7）：
// core.js 贴 M3 拆分线，合并为单循环避免触线；字面量保留供各装载门断言
// lineage.js=N-4 血统 tab（material.js 贴 40KiB 锚，同 iterations.js 判例独立产物）
// novel.js=FEAT-4 小说双件套（人物关系力导向图 + 伏笔账本；compose.js 贴 40KiB 锚同判例）
['assets/quickstart.js','assets/outline.js','assets/rolling.js','assets/dual.js','assets/tts.js','assets/versions.js','assets/iterations.js','assets/lineage.js','assets/novel.js'].forEach(function(s){var e=document.createElement('script');e.src=s;document.head.appendChild(e);});
}
init();

// B4a 剪藏/爬取/登记三态入口模态（F66 preview→人工 adopt / F67 手动粘贴→pending /
// F61 视频——URL+标题+描述三字段共用一壳，提交按模式分派）
var CM='clip';
function clipOpen(m){
CM=m;
$('clip-title').textContent=m==='clip'?'剪藏 URL':(m==='crawl'?'手动爬取':'登记视频');
$('clip-ok').textContent=m==='clip'?'抓取预览':(m==='crawl'?'抓取入待决区':'登记');
$('clip-sub').hidden=$('clip-note').hidden=m!=='video';
$('clip-url').value='';$('clip-sub').value='';$('clip-note').value='';$('clip-err').textContent='';
$('clip-form').hidden=false;$('clip-url').focus();
}
function clipSubmit0(){
var u=$('clip-url').value.trim();
if(!/^https:\/\/|^http:\/\//i.test(u)){$('clip-err').textContent='URL 须 http(s):// 开头';return;}
return u;
}
// ARCH-1：src-clip-btn/src-crawl-btn/mat-reg-btn 三入口属懒视图骨架——原 top-level
// on2 绑定迁 __onViewHtml（boot 时元素未到会抛 TypeError 中断 core 尾段执行）
on2('clip-cancel',function(){$('clip-form').hidden=true;});
$('clip-form').addEventListener('click',function(e){if(e.target===this)this.hidden=true;});
$('clip-ok').addEventListener('click',async function(){
var u=clipSubmit0();
if(!u)return;
this.disabled=true;$('clip-err').textContent='';
try{
if(CM==='video'){
var d=await postJSON('/api/materials/register',{url:u,title:$('clip-sub').value.trim(),note:$('clip-note').value.trim(),confirm:true});
$('clip-form').hidden=true;
	toast(d.dedup?'已登记过（幂等命中）':'视频已登记',d.dedup?WN:OK);
	await loadMaterials();renderCollRail();viewRepaint('material');
	}else if(CM==='crawl'){
	var dc=await postJSON('/api/ingest/crawl',{url:u,confirm:true});
	$('clip-form').hidden=true;
	await queued(dc.job_id);
	await loadMaterials();viewRepaint('sources');
}else{
var d2=await postJSON('/api/ingest/clip',{action:'preview',url:u,confirm:true});
$('clip-form').hidden=true;
var j=await waitForJob(d2.job_id);
if(j.status==='failed'){toast('抓取失败：'+(j.error||'未知错误'),ER);return;}
clipCard(j.result||{},u);
}
}catch(err){$('clip-err').textContent=err.message;}
this.disabled=false;
});
function clipCard(pv,u){
var rb={allowed:['pass','robots 允许'],disallowed:['warn','robots 不允许（手动不阻断）'],unknown:['queued','robots 未知']}[(pv.robots||{}).state]||['queued','robots 未知'];
$('viewer-title').textContent='剪藏预览（可编辑后存为素材；放弃零 API）';
$('viewer-body').innerHTML='<p class="action-hint"><span class="badge '+rb[0]+'">'+rb[1]+'</span> · 置信 '+(pv.confidence!=null?pv.confidence:'—')+' · 词数 '+(pv.word_count||0)+' · '+esc(pv.url||u)+(pv.mode==='link'?' · <span style="color:var(--amber)">置信度低于 0.5，降级为链接模式</span>':'')+'</p>'+
'<input id="cpv-t" class="input" value="'+esc(pv.title||'')+'">'+
'<select id="cpv-l" class="input"><option>UNKNOWN</option><option'+(pv.license==='CC-BY'?' selected':'')+'>CC-BY</option><option'+(pv.license==='ALL-RIGHTS'?' selected':'')+'>ALL-RIGHTS</option></select>'+
'<textarea id="cpv-x" class="set-area" rows="10"></textarea>'+
'<div class="action-row"><button class="btn btn-primary" id="cpv-a">'+(pv.mode==='link'?'存为链接':'存为素材')+'</button><button class="btn" id="cpv-d">放弃</button></div>';
$('cpv-x').value=pv.text||'';
$('cpv-a').onclick=async function(){
this.disabled=true;
try{
var r=await postJSON('/api/ingest/clip',{action:'adopt',url:pv.url||u,title:$('cpv-t').value,text:$('cpv-x').value,mode:pv.mode||'crawled',confidence:pv.confidence||0,word_count:pv.word_count||0,license:$('cpv-l').value,license_note:pv.license_note||'',confirm:true});
	toast('已存为'+(r.mode==='link'?'链接':'抓取')+'素材',OK);
	$('viewer').hidden=true;
	await loadMaterials();viewRepaint('sources');
}catch(err){toast('采纳失败：'+err.message,ER);this.disabled=false;}
};
$('cpv-d').onclick=function(){$('viewer').hidden=true;toast('已放弃（零落盘零痕迹）',OK);};
$('viewer').hidden=false;
}
// ── N-2 灵感速记 spark 浮层（2026-11-21，设计 §五 P-1）──────────────────
// ⌘⇧I/Ctrl+Shift+I 任意视图唤起（绑定在 bindCore 全局 keydown）。壳余量 73B
// 裁决=零 HTML 新增：DOM 全 JS 动态建，样式 .spark-* 落 ui.css。交互契约：
// Enter 保存 / Shift+Enter 换行（单行多行混合输入框）/ Esc 关闭（浮层自身
// keydown stopPropagation + 全局 Escape 链首项双保险）。保存 POST
// /api/materials/spark {text,confirm}——type=text+meta.sub_type=spark+origin=
// manual 全自动（裁决：不新增 MATERIAL_TYPES），同句幂等 dedup；成功后刷
// 素材数据面（renderCollRail 归 core，当前非 material 视图时 viewRepaint
// 是 VAPI no-op，零越权直呼）。
var sparkBox=null;
function sparkClose(){if(sparkBox){sparkBox.remove();sparkBox=null;}}
function sparkOpen(){
if(sparkBox)return;
sparkBox=document.createElement('div');
sparkBox.className='spark-ov';
sparkBox.innerHTML='<div class="spark-panel" role="dialog" aria-label="灵感速记">'+
'<div class="spark-head">✦ 灵感速记<span class="spark-hint">Enter 保存 · Shift+Enter 换行 · Esc 关闭</span></div>'+
'<textarea class="spark-in input" rows="3" placeholder="一句话记下，回车即入素材库…"></textarea>'+
'<div class="spark-foot"><span class="spark-err"></span><button class="btn btn-primary spark-go" type="button">存入素材库</button></div></div>';
sparkBox.addEventListener('mousedown',function(e){if(e.target===this)sparkClose();});
var ta=sparkBox.querySelector('.spark-in');
sparkBox.querySelector('.spark-go').onclick=function(){sparkSave(this);};
ta.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'){e.preventDefault();e.stopPropagation();sparkClose();return;}
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sparkSave(sparkBox.querySelector('.spark-go'));}
});
document.body.appendChild(sparkBox);
ta.focus();
}
async function sparkSave(btn){
var ta=sparkBox?sparkBox.querySelector('.spark-in'):null,err=sparkBox?sparkBox.querySelector('.spark-err'):null;
var text=ta?ta.value.trim():'';
if(!text){if(err)err.textContent='灵感内容不能为空';return;}
btn.disabled=true;
if(err)err.textContent='';
try{
var d=await postJSON('/api/materials/spark',{text:text,confirm:true});
toast(d.dedup?'灵感已存过（幂等命中，未重复入库）':'灵感已入素材库（未读，素材库「✦ 灵感」chip 可筛）',d.dedup?WN:OK);
sparkClose();
loadMaterials().then(function(){renderCollRail();viewRepaint('material');});
}catch(e2){if(err)err.textContent=e2.message;btn.disabled=false;}
}
// 跨视图共享助手（原 bindEvents 内嵌，M3 提升为全局）：拖拽判定/上下文菜单/点击绑定
function dropHasFiles(e){
return Boolean(e.dataTransfer&&[].slice.call(e.dataTransfer.types||[]).indexOf('Files')>=0);
}
// P4 右键菜单（§5.4 规则）：菜单项=行内按钮同源——同一 data 属性复用同一委托处理器
// （素材=matActsClick / 块=cmBlocksClick / 文章卡=深链锚点），危险项恒 .dg 绛色。
// A 类专项（2026-11-20）：#ctx 委托改由各视图块自绑（matActsClick 原是 bindMaterial
// 局部函数，core 直呼即 ReferenceError——右键菜单动作实打实失效；现 material/compose
// 各自在 bind 里挂 #ctx 监听，data 属性两族互斥）。
// D6 集成修复（2026-11-17）：左键 ⋯/+集合 打开菜单的同一 click 会冒泡到 document
// 级 click-away（ctxHide）闪开即关——ctxShow 停止传播；菜单项点击仍照常关闭
function ctxShow(e,html){
e.stopPropagation();
var m=$('ctx');
m.innerHTML=html;m.hidden=false;
m.style.left=Math.min(e.clientX,innerWidth-190)+'px';
m.style.top=Math.min(e.clientY,innerHeight-230)+'px';
}
function ctxHide(){$('ctx').hidden=true;}
function on2(i,f){$(i).addEventListener('click',f);}
// ── 跨视图共享真身（2026-11-20 A 类专项，取代原 no-op 占位符块）──────
// 原 M3 占位符整族退役：无视图专属状态依赖的装载器/渲染工具真身归 core——
// 调用点横跨 core+多视图（sources 素材计数、compose 选题池、insight 创作节奏
// 等在视图未注入时被占位符静默吞掉，台账 A-1/A-3/A-4/A-5）。各函数体自原
// 视图块逐字迁移，行为零变化；视图专属 render*/bind* 仍归各 @view 块。
var DNAV=['sources','material','design','compose','publish','insight','settings']; // ⌘1-7 导航序由 core 持有（2026-11-19 修红：M3 后原定义随 design 块懒加载，未访 #design 前 DNAV undefined → 视图外 ⌘1-7 全失效）
function dkey(dt){return dt.getFullYear()+'-'+('0'+(dt.getMonth()+1)).slice(-2)+'-'+('0'+dt.getDate()).slice(-2);}
// M3 查询构造（F62/F63/F64 正交 AND 全参上移服务端）：q/集合走服务端，
// 全库检索不在 state.mats 截断集上做（正典 F64 交互流 1）
function matQS(limit){
var p=['limit='+limit];
if(state.msrc)p.push('source='+encodeURIComponent(state.msrc));
if(state.mtype)p.push('type='+encodeURIComponent(state.mtype));
if(state.mspark)p.push('sub_type='+encodeURIComponent(state.mspark)); // N-2：灵感 chip 读 sub_type 非 type（枚举不动裁决）
if(state.mconf)p.push('confidence='+state.mconf); // N-4：低置信筛选（服务端 triage.confidence<0.5）
if(state.mtag)p.push('tag='+encodeURIComponent(state.mtag));
if(state.mcoll)p.push('collection='+encodeURIComponent(state.mcoll));
if(state.mquery)p.push('q='+encodeURIComponent(state.mquery));
return '/api/materials?'+p.join('&');
}
async function loadMaterials(){
try{var d=await getJSON(matQS(state.mquery||state.mcoll?500:200));
state.mats=Array.isArray(d.items)?d.items:[];
state.mqTotal=(d&&d.total!=null)?d.total:state.mats.length;
var lc=$('mat-lowconf');if(lc)lc.classList.toggle('is-active',!!state.mconf);} // N-4 chip 激活态（material.js 贴 40KiB 锚，同步归 core——spark chip 判例）
catch(err){state.mats=[];state.mqTotal=0;toast('加载素材失败：'+err.message,ER);}
}
async function loadDiscover(){
try{var d=await getJSON('/api/discover?limit=60&all=1');state.discover=Array.isArray(d.candidates)?d.candidates:[];}
catch(err){state.discover=[];toast('加载候选失败：'+err.message,ER);}
}
async function loadColls(){
try{var d=await getJSON('/api/materials/collections');state.colls=Array.isArray(d.collections)?d.collections:[];}
catch(err){state.colls=[];}
state.collOf={};
state.colls.forEach(function(c){(c.member_ids||[]).forEach(function(mid){(state.collOf[mid]=state.collOf[mid]||[]).push(c.id);});});
}
async function loadTags(){
try{state.tagList=(await getJSON('/api/materials/tags')).tags||[];}
catch(err){state.tagList=[];}
var dl=$('tag-dl');
if(dl)dl.innerHTML=state.tagList.map(function(t){return '<option value="'+esc(t.name)+'">';}).join('');
}
// F64 结果高亮（自 @view:material 迁 core：view JS 贴 40KiB 体积锚；跨视图消费）
function hl(raw){
var s=String(raw==null?'':raw);
if(!state.mquery)return esc(s);
var toks=(String(state.mquery).match(/"[^"]+"|\S+/g)||[]).map(function(t){return t.replace(/^"|"$/g,'').replace(/\*+$/,'');}).filter(Boolean);
if(!toks.length)return esc(s);
var re;
try{re=new RegExp('('+toks.map(function(t){return t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');}).join('|')+')','gi');}
catch(err){return esc(s);}
var out='',last=0,m=re.exec(s);
while(m){out+=esc(s.slice(last,m.index))+'<mark>'+esc(m[0])+'</mark>';last=m.index+m[0].length;m=re.exec(s);}
return out+esc(s.slice(last));
}
// ── FEAT-1 语义搜索前端数据面（core 共享块；2026-11-22）────────────────
// 三模式：auto=自然语言（含空格/≥6 字中文描述）走 hybrid，其余走 keyword；
// keyword/semantic 手动锁定。semanticReady=false 时所有模式自动降级 keyword。
var SEARCH_MODES={auto:'自动',keyword:'关键词',semantic:'语义'};
function effectiveMode(q){
if(!state.searchMode||state.searchMode==='auto')return looksNatural(q)?'hybrid':'keyword';
return state.searchMode; // keyword | semantic
}
function looksNatural(q){
q=String(q||'').trim();
return /\s/.test(q)||q.length>=6; // 含空格或多字词描述视为自然语言
}
function simPct(v){
if(typeof v!=='number'||!(v>0))return '';
return '<span class="sim-badge" title="语义相关度">'+Math.round(v*100)+'%</span>';
}
// 语义搜索结果应用：裁剪面条目直接进 state.mats（排序统一由 matFiltered 处理）
function applySearchResult(items){
state.mats=Array.isArray(items)?items:[];
state.mqTotal=state.mats.length;
}
async function matSearch(q){
if(!q){state.mats=[];state.mqTotal=0;state.searchReady=null;viewRepaint('material');return;}
var mode=effectiveMode(q);
try{
var d=await getJSON('/api/materials/search?q='+encodeURIComponent(q)+'&mode='+mode+'&limit=100');
state.searchReady=(d&&typeof d.semantic_ready==='boolean')?d.semantic_ready:null;
// 降级：显式语义但 embedding 不可用→自动切关键词并重搜 + 提示（不打扰）
if(d&&d.ok===false&&mode==='semantic'&&state.searchReady===false){
state.searchMode='keyword';syncMatModes();
showMatHint('语义检索暂不可用（需本地 embedding 服务），已自动切换为关键词搜索');
return matSearchBy('keyword',q);
}
hideMatHint();applySearchResult(d.items);
}catch(err){state.mats=[];state.mqTotal=0;toast('搜索失败：'+err.message,ER);}
viewRepaint('material');
}
async function matSearchBy(mode,q){
try{var d=await getJSON('/api/materials/search?q='+encodeURIComponent(q)+'&mode='+mode+'&limit=100');
applySearchResult(d.items);}catch(err){state.mats=[];state.mqTotal=0;toast('搜索失败：'+err.message,ER);}
viewRepaint('material');
}
function syncMatModes(){
QA('[data-searchmode]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-searchmode')===(state.searchMode||'auto'));});
}
function showMatHint(msg){var h=$('mat-mode-hint');if(h){h.textContent=msg;h.hidden=false;}}
function hideMatHint(){var h=$('mat-mode-hint');if(h)h.hidden=true;}
function lineageHtml(m){
var refs=m.references||[];
if(!refs.length)return '<p class="action-hint">血统：原始素材（provenance 空）</p>';
return '<div class="lineage-chain"><span class="lineage-node self">'+I(MTYI[m.type]||'tag')+esc(m.id)+'</span>'+refs.map(function(r){
var nid=esc(r.from_id);
return '<span class="lineage-op">'+esc(r.op||'ref')+Z+(r.deleted?'<span class="badge fail" title="原料已删（墓碑），链接不跳转">素材已删</span>':'<a class="lineage-node" href="#material?m='+nid+'" title="跳到原料详情">'+nid+'</a>');
}).join('')+'</div>';
}
// 语义相关摘要（详情抽屉 top-5 已随 detail 载荷下发，点击跳详情）
function matRelatedHtml(related){
if(!Array.isArray(related)||!related.length)return '';
return '<div class="sim-related"><span class="action-title" style="font-size:var(--fs-caption)">语义相关</span>'+
related.map(function(r){return '<div class="sim-rel" data-mrelated="'+esc(r.id)+'"><span class="sim-rel-t">'+esc(r.title||r.id)+'</span>'+simPct(r.similarity)+'</div>';}).join('')+'</div>';
}
function fillMatType(){
var sel=$('mat-type');
sel.innerHTML='<option value="">全部类型</option>'+Object.keys(MTY).map(function(k){return '<option value="'+k+'"'+(state.mtype===k?' selected':'')+'>'+MTY[k]+'</option>';}).join('');
syncMatModes(); // FEAT-1：进视图时同步搜索模式激活态（降级切关键词后仍准确）
// N-2 灵感 chip（2026-11-21）：挂类型筛选区（#mat-type 旁，与 7 类型筛选并列、
// .chip-spark 虚线边视觉区分）——筛 meta.sub_type=spark 非 type。建在 core 的
// fillMatType 而非 material.js：单视图 40KiB 锚余量仅 326B 装不下（本文件
// core 段余量 110KB）；fillMatType 本就是类型筛选区装配器（A 类专项口径：
// 无视图专属状态依赖的装载工具归 core）。点击=服务端过滤（matQS sub_type）
// + viewRepaint('material') 经 VAPI 重绘，激活态由 renderMaterials 同步（深链）。
var sk=$('mat-spark');
if(!sk){
sk=document.createElement('button');
sk.type='button';sk.id='mat-spark';sk.className='chip chip-spark';
sk.title='只看灵感速记（meta.sub_type=spark；⌘⇧I 随手记）';
sk.textContent='✦ 灵感';
$('mat-type').insertAdjacentElement('afterend',sk);
sk.onclick=function(){
state.mspark=state.mspark?'':'spark';
this.classList.toggle('is-active',!!state.mspark);
loadMaterials().then(function(){renderCollRail();viewRepaint('material');});
};
}
// N-4 低置信 chip（同 spark chip 判例：建在 core 的 fillMatType——material.js
// 贴 40KiB 锚装不下；服务端 confidence=low 筛选，只中「AI 给过低分」条目）
var lc=$('mat-lowconf');
if(!lc){
lc=document.createElement('button');
lc.type='button';lc.id='mat-lowconf';lc.className='chip';
lc.title='只看低置信素材（AI 自评 confidence<0.5，引用前先核对原文）';
lc.textContent='⚠ 低置信';
sk.insertAdjacentElement('afterend',lc);
lc.onclick=function(){
state.mconf=state.mconf?'':'low';
this.classList.toggle('is-active',!!state.mconf);
loadMaterials().then(function(){renderCollRail();viewRepaint('material');});
};
}
}
async function loadRadar(){try{state.radar=(await getJSON('/api/topic_radar')).snapshot}catch(e){state.radar=null}}
async function loadCompose(force){
if(state.cs&&!force)return;
try{var d=await getJSON('/api/compose');state.cs=Array.isArray(d.sessions)?d.sessions:[];}
catch(err){state.cs=[];toast('加载创作会话失败：'+err.message,ER);}
}
async function loadComposeDetail(slug,force){
if(!slug){state.csd=null;state.cslug='';state.cround=0;return;}
if(!force&&state.csd&&state.cslug===slug)return;
try{state.csd=await getJSON('/api/compose/session?slug='+encodeURIComponent(slug));state.cslug=slug;}
catch(err){state.csd=null;state.cslug='';toast('加载会话失败：'+err.message,ER);}
}
function renderSkeleton(){
var html='';
for(var i=0;i<8;i++){
html+='<div class="article-card qcard skel" aria-hidden="true">' +
'<div class="qcard-cover"><div class="skel-block"></div></div>' +
'<div class="qcard-body"><div class="skel-line w70"></div><div class="skel-line w45"></div><div class="skel-line w90"></div></div></div>';
}
$('article-list').innerHTML=html;
$('queue-empty').hidden=true;
}
// ── FEAT-5 前端交互升级（2026-11-22）────────────────────────────────
// 骨架屏通用件：首屏数据在途时替代空白/空态闪烁。skelCards=卡片墙（复用 qcard
// 结构与 .skel/.skel-line 既有样式），skelRows=行式列表（表格/清单用，样式见
// ui.css .skel-row）。纯字符串模板，调用方 innerHTML 自行覆盖。
function skelCards(n){var h='';for(var i=0;i<(n||4);i++)h+='<div class="article-card qcard skel" aria-hidden="true"><div class="qcard-body"><div class="skel-line w70"></div><div class="skel-line w45"></div><div class="skel-line w90"></div></div></div>';return h;}
function skelRows(n){var h='';for(var i=0;i<(n||3);i++)h+='<div class="skel-row" aria-hidden="true"><div class="skel-line w45"></div><div class="skel-line w70"></div><div class="skel-line w90"></div></div>';return h;}
// 当前生效主题是否深色（三态：light/dark/auto——auto 按 prefers-color-scheme 实测）。
// 供 iframe 类孤岛（live 预览 srcdoc）取色用：iframe 不继承父文档 data-theme。
function themeDark(){
var t=document.documentElement.getAttribute('data-theme');
if(t)return t==='dark';
return !!(window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);
}
// 快捷键速查（? 唤起）：数据驱动一张表，键盘可达（⌘K/⌘1-7/g 序列/⌘⇧I 既有绑定
// 的可发现层——不新增键位只补说明）。组三元组 [路由名, 展示名, 行]：视图专属段
// 仅当该路由在当前 hash 时展示（'全局' 恒展示）。
var SHORTCUTS=[
['全局','全局',[['⌘K / Ctrl+K','命令面板（文章/素材/命令混搜）'],['⌘1-7','按导航序切视图'],['g 然后 s/m/c/p/i/,','跳转 源/素材/工坊/发布/洞察/设置'],['⌘⇧I','灵感速记（任意视图）'],['/','聚焦当前视图搜索框'],['?','本速查'],['Esc','逐层关闭浮层']]],
['article','文章详情',[['⌘1-4','编辑/变体/检查/预览'],['⌘S','保存'],['⌘⏎','主操作（保存/批量/发布）']]],
['material','素材库',[['← →','灯箱上一张/下一张'],['Esc','关灯箱/抽屉']]],
['design','视觉编辑',[['Esc','收行内编辑']]]
];
function kbdPaint(){
var cur=routeInfo().name,h='';
SHORTCUTS.forEach(function(g){
if(g[0]!=='全局'&&g[0]!==cur)return;
h+='<div class="kbd-grp"><div class="kbd-grp-t">'+esc(g[1])+'</div>'+g[2].map(function(r){
return '<div class="kbd-row"><kbd>'+esc(r[0])+'</kbd><span>'+esc(r[1])+'</span></div>';}).join('')+'</div>';
});
$('kbd-body').innerHTML=h;
}
function kbdOpen(){kbdPaint();$('kbd-modal').hidden=false;$('kbd-close').focus();}
function kbdClose(){$('kbd-modal').hidden=true;}
function pPaint(){
var p=state.pipe||{},b=$('pipe-progress');
if(!b)return; // ARCH-1：article 未注入时面板 DOM 不在；重进 article 时 renderActions→pPaint 按 state.pipe 重绘，跳过不可观测
b.innerHTML=stepRows(p.steps)+(p.err?'<p class="action-hint" style="color:var(--crimson)">'+esc(p.err)+'</p>':'');
}
function hlSeg(t){
return String(t).replace(/^#{1,4}\s.+$/gm,function(m){return '<span class="tk-h">'+m+'</span>';})
.replace(/```[\s\S]*?(?:```|$)/g,function(m){return '<span class="tk-c">'+m+'</span>';})
.replace(/^&gt;.*$/gm,function(m){return '<span class="tk-q">'+m+'</span>';})
.replace(/`[^`\n]+`/g,function(m){return '<span class="tk-c">'+m+'</span>';})
.replace(/\*\*[^*\n]+?\*\*/g,function(m){return '<span class="tk-b">'+m+'</span>';})
.replace(/\[[^\]\n]+\]\([^)\n]*\)/g,function(m){return '<span class="tk-l">'+m+'</span>';})
.replace(/^(\s*)((?:[-*]|\d+\.)\s+(\[[ x]\]\s+)?)/gm,'$1<span class="tk-k">$2</span>');
}
function renderHl(sel){
var v=EA.value;
HL.innerHTML=((sel&&sel.e>sel.s)?hlSeg(esc(v.slice(0,sel.s)))+'<span class="tk-mark">'+hlSeg(esc(v.slice(sel.s,sel.e)))+'</span>'+hlSeg(esc(v.slice(sel.e))):hlSeg(esc(v)))+'\n';
}
function hideBubble(){AB.hidden=true;state.sel=null;renderHl();}
function bindCore(){
window.addEventListener('hashchange',renderRoute);
$('refresh-btn').addEventListener('click',onRefresh);
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if((e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();$('cp').hidden?cpOpen():cpClose();return;}
if(!(e.target.closest&&e.target.closest('input,select,textarea,[contenteditable]'))&&(e.metaKey||e.ctrlKey)&&e.shiftKey&&(e.key==='i'||e.key==='I')){e.preventDefault();sparkBox?sparkClose():sparkOpen();return;}
if((e.metaKey||e.ctrlKey)&&e.key>='1'&&e.key<='7'){
e.preventDefault();
var a1=VAPI.article;
if(!$('view-article').hidden&&+e.key<=4){if(a1&&a1.setTab)return a1.setTab(['edit','variants','checks','preview'][+e.key-1],true);return;}
location.hash='#'+DNAV[+e.key-1];return;
}
if(!e.metaKey&&!e.ctrlKey&&!e.altKey&&!e.target.closest('input,select,textarea')){
if(gSeq){gSeq=0;var gh=e.key===','?'settings':GK[e.key];if(gh){e.preventDefault();location.hash='#'+gh;}return;}
if(e.key==='g'){gSeq=1;setTimeout(function(){gSeq=0;},600);return;}
if(e.key==='?'){e.preventDefault();kbdOpen();return;}
if(e.key==='/'){var kf=KFOCUS[routeInfo().name];if(kf){var ke=$(kf);if(ke){e.preventDefault();ke.focus();if(ke.select)ke.select();}}return;}
}
if(e.key==='Escape'){
if(sparkBox){sparkClose();return;}
if(!$('cp').hidden){cpClose();return;}
if(!$('kbd-modal').hidden){kbdClose();return;}
if(!$('ctx').hidden){ctxHide();return;}
if(AB&&!AB.hidden)hideBubble();
if(!$('viewer').hidden)$('viewer').hidden=true;
if(!JM.hidden){JM.hidden=true;jobsPollStop();}
}
});
var KFOCUS={material:'mat-search',books:'bk-search-box',publish:'fetch-url'};
$('kbd-modal').addEventListener('click',function(e){if(e.target===this)kbdClose();});
$('kbd-close').addEventListener('click',kbdClose);
$('cp').addEventListener('click',function(e){if(e.target===this)cpClose();});
$('cp-input').addEventListener('input',function(){CP.cur=0;cpBuild(this.value);});
$('cp-input').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='ArrowDown'||e.key==='ArrowUp'){
e.preventDefault();
if(CP.items.length){CP.cur=(CP.cur+(e.key==='ArrowDown'?1:-1)+CP.items.length)%CP.items.length;cpRender();}
}else if(e.key==='Enter'){
e.preventDefault();
if(CP.items[CP.cur]){var it=CP.items[CP.cur];cpClose();it.run();}
}else if(e.key==='Escape'){e.stopPropagation();cpClose();}
});
$('cp-list').addEventListener('click',function(e){
var row=e.target.closest('[data-cp]');
if(!row)return;
var it=CP.items[+row.getAttribute('data-cp')];
if(it){cpClose();it.run();}
});
$('jobs-btn').addEventListener('click',async function(){
var menu=JM;
menu.hidden=!menu.hidden;
if(!menu.hidden){await refreshJobs();jobsPollStop();jobsPoll=setInterval(refreshJobs,3000);}
else jobsPollStop();
});
JM.addEventListener('click',function(event){
var button=event.target.closest('[data-resend]');
if(button){handleResend(button.getAttribute('data-resend'));return;}
var view=event.target.closest('[data-view-job]');
if(view){var j=state.jobs.find(function(x){return x&&x.id===view.getAttribute('data-view-job');});if(j)viewJob(j);return;}
if(event.target.closest('#jobs-clear')){
state.jobs.forEach(function(j){if(j&&j.status==='done')jobsDismissed[j.id]=1;});
renderJobs();}
});
document.addEventListener('click',function(event){
if(!$('ctx').hidden)ctxHide();
if(!event.target.closest('#jobs-wrap')){JM.hidden=true;jobsPollStop();}
});
$('settings-btn').addEventListener('click',function(){location.hash='#settings';});
$('theme-toggle').addEventListener('click',cycleTheme);
function badgeGo(hash){
if(location.hash===hash){renderRoute();return;}
location.hash=hash;
}
$('nav-badge-material').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#material?tab=unread');});
$('nav-badge-publish').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#publish?filter=pending');});
$('nav-badge-sources').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#sources');});
$('cp-btn').addEventListener('click',function(){$('cp').hidden?cpOpen():cpClose();});
$('rail-fold').addEventListener('click',function(){
var root=document.documentElement;
var fold=root.getAttribute('data-rail')==='min';
if(fold)root.removeAttribute('data-rail');else root.setAttribute('data-rail','min');
try{localStorage.setItem('musespiral-rail',fold?'max':'min');}catch(e){}
this.title=fold?'折叠导航轨':'展开导航轨';
this.setAttribute('aria-label',this.title);
});
}
var ICONS={
file:'<path d="M13.8 3H7.2A1.7 1.7 0 0 0 5.5 4.7v14.6A1.7 1.7 0 0 0 7.2 21h9.6a1.7 1.7 0 0 0 1.7-1.7V7.6L13.8 3z"/><path d="M13.5 3.2v4.6h4.7"/>',
edit:'<path d="M12.4 20h8.1"/><path d="M16.7 3.6a2 2 0 0 1 2.8 2.8L7.5 18.4 3.5 19.5l1.1-4L16.7 3.6z"/>',
eye:'<path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
check:'<path d="M4.5 12.6l4.8 4.8L19.5 6.9"/>',
warning:'<path d="M12 4L2.8 19.6h18.4L12 4z"/><path d="M12 10v4.2"/><path d="M12 17.3h.01"/>',
error:'<circle cx="12" cy="12" r="8.6"/><path d="M12 7.6v5"/><path d="M12 16.4h.01"/>',
refresh:'<path d="M20.9 11.2a9 9 0 1 0-.5 3.9"/><path d="M21 4.5v6.7h-6.7"/>',
download:'<path d="M12 3.5v11.2"/><path d="M7.5 10.6l4.5 4.5 4.5-4.5"/><path d="M4.5 20h15"/>',
upload:'<path d="M12 14.7V3.5"/><path d="M7.5 7.6L12 3.1l4.5 4.5"/><path d="M4.5 20h15"/>',
publish:'<path d="M21 3L10.8 13.2"/><path d="M21 3l-6.6 18-3.6-7.8L3 9.6 21 3z"/>',
star:'<path d="M12 3.4l2.6 5.3 5.8.9-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.6l5.8-.9L12 3.4z"/>',
clock:'<circle cx="12" cy="12" r="8.6"/><path d="M12 7.4V12l3.2 1.9"/>',
tag:'<path d="M3.6 11.7V4.4a.8.8 0 0 1 .8-.8h7.3l8.7 8.7a1.2 1.2 0 0 1 0 1.7l-6.4 6.4a1.2 1.2 0 0 1-1.7 0L3.6 11.7z"/><path d="M7.8 7.8h.01"/>',
user:'<circle cx="12" cy="8.2" r="3.9"/><path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0"/>',
search:'<circle cx="11" cy="11" r="6.6"/><path d="M15.9 15.9L20.5 20.5"/>',
settings:'<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1.5 14h5M9.5 8h5M17.5 16h5"/>',
bell:'<path d="M18 8.4a6 6 0 0 0-12 0c0 6.6-2.7 8.6-2.7 8.6h17.4S18 15 18 8.4z"/><path d="M13.8 20.6a2 2 0 0 1-3.6 0"/>',
close:'<path d="M6 6l12 12M18 6L6 18"/>',
comment:'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
'arrow-left':'<path d="M20 12H4.4"/><path d="M10.5 5.9L4.4 12l6.1 6.1"/>',
'arrow-right':'<path d="M4 12h15.6"/><path d="M13.5 5.9l6.1 6.1-6.1 6.1"/>',
plus:'<path d="M12 5.5v13M5.5 12h13"/>',
trash:'<path d="M4 7h16"/><path d="M9.5 7V4.6h5V7"/><path d="M6.3 7l.9 13.4h9.6L17.7 7"/><path d="M10.2 10.8v5.8M13.8 10.8v5.8"/>',
'external-link':'<path d="M18 13.5v5.7a1.8 1.8 0 0 1-1.8 1.8H5.8A1.8 1.8 0 0 1 4 19.2V8.8A1.8 1.8 0 0 1 5.8 7h5.7"/><path d="M15 3.5h5.5V9"/><path d="M10 14L20.5 3.5"/>',
docs:'<path d="M9.2 3h5.4l3.9 3.9V17"/><path d="M14.3 3.1v4.2h4.2"/><path d="M5.6 7h5.4l3.9 3.9v9.6a1.5 1.5 0 0 1-1.5 1.5H5.6a1.5 1.5 0 0 1-1.5-1.5V8.5A1.5 1.5 0 0 1 5.6 7z"/><path d="M7.6 13.4h4.4M7.6 16.6h3"/><path d="M18.5 19.6v.01"/>',
sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2.6v2.3M12 19.1v2.3M2.6 12h2.3M19.1 12h2.3M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6"/>',
image:'<path d="M5.2 4.7h13.6a1.7 1.7 0 0 1 1.7 1.7v11.2a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7V6.4a1.7 1.7 0 0 1 1.7-1.7z"/><circle cx="9" cy="9.6" r="1.5"/><path d="M5.5 17.6l3.7-3.7 3 3 2.7-2.7 4.6 4.6"/>',
video:'<path d="M8.4 5.9v12.2L18.9 12z"/>',
link:'<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
radar:'<circle cx="7.2" cy="12" r="1.3"/><path d="M7.2 7.7a4.3 4.3 0 0 1 0 8.6"/><path d="M7.2 3.7a8.3 8.3 0 0 1 0 16.6"/>',
globe:'<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2"/><path d="M12 3.4a13.6 13.6 0 0 1 0 17.2a13.6 13.6 0 0 1 0-17.2z"/>',
derived:'<path d="M7.6 13l3.4 3.4-3.4 3.4-3.4-3.4z"/><path d="M12.6 11.9L20.4 4.1"/><path d="M15.4 4.1h5v5"/>',
moon:'<path d="M20.4 14.2A8.6 8.6 0 0 1 9.8 3.6a8.6 8.6 0 1 0 10.6 10.6z"/>',
monitor:'<path d="M4.7 4.5h14.6c.9 0 1.7.8 1.7 1.7v9.6c0 .9-.8 1.7-1.7 1.7H4.7c-.9 0-1.7-.8-1.7-1.7V6.2c0-.9.8-1.7 1.7-1.7z"/><path d="M9.2 21h5.6M12 17.7V21"/>',
command:'<path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3"/>',
'panel-left':'<path d="M5.2 4.5h13.6c.9 0 1.7.8 1.7 1.7v11.6c0 .9-.8 1.7-1.7 1.7H5.2c-.9 0-1.7-.8-1.7-1.7V6.2c0-.9.8-1.7 1.7-1.7z"/><path d="M9.7 4.5v15"/>',
play:'<path d="M8 5.2v13.6L19 12z"/>',
flow:'<path d="M3 9h5v6H3z"/><path d="M16 9h5v6h-5z"/><path d="M8.2 12h4.6"/><path d="m11 9.6 2.4 2.4-2.4 2.4"/>'
};
var TAG='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">';
function icon(name){return ICONS[name]?TAG+ICONS[name]+'</svg>':'';}
window.mdpubIcon=icon;
function mountChrome(){
var nodes=document.querySelectorAll('[data-icon]');
for(var i=0;i<nodes.length;i++){nodes[i].innerHTML=icon(nodes[i].getAttribute('data-icon'));}
var addr=document.getElementById('server-addr');
if(addr){addr.textContent=location.host||'本地文件';}
}
mountChrome();
