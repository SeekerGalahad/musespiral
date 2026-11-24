// M3 手写 core 源（扁平全局，视图脚本注入后按全局名互访，零 import）
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
writing:null,
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
// ARCH-1 壳 HTML 外移：EA/HL/LF/AB/AA/PB/EG/ES 属 #view-article 骨架（壳里只剩空
// 占位 section），装载期抓取改惰性——bindEditorRefs 在 article HTML 注入后由
// __onViewHtml 回填（注入前 null；Esc 链/pPaint 已加 null 守卫，编辑器函数族
// 消费必在注入后——A 类专项「消费必在注入后」契约不变）。JM/MC 属壳，照旧抓取。
var EA=null,HL=null,LF=null,AB=null,AA=null,PB=null,EG=null,ES=null;
function bindEditorRefs(){EA=$('edit-area');HL=$('edit-hl');LF=$('live-frame');AB=$('annot-bubble');AA=$('edit-annotate-ai');PB=$('publish-btn');EG=$('edit-gutter');ES=$('edit-save-btn');}
var STATUS_LABELS={draft:'草稿',reviewed:'已审',published:'已发布',archived:'已归档'};
function statusBadge(s){if(!STATUS_LABELS[s])s='draft';return '<span class="badge stamp '+s+'">'+STATUS_LABELS[s]+Z;}
// UIX #6：卡片状态徽标区分草稿/待审/已审/已发布——头部统计四段齐全但卡片原只按
// frontmatter.status 出「草稿」，认不出哪几篇在待审。口径与 pubCounts/filterArticles
// 完全一致：published 标记 > status=reviewed > 无 FAIL/WARN 的 draft=待审 > 草稿。
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
// 静默即正常徽章（沉淀·18/17）：三态数据表+hover 细分文案，正常态零渲染
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
var JN={adapt:'生成变体',pack:'生成物料包',publish:'创建草稿',batch_adapt:'批量生成变体',fetch:'URL 抓取',status:'标记已审',annotate_rewrite:'批注 AI 修改',sync_stats:'数据回采',compose_generate:'创作再生成',ingest:'源刷新',clip:'网页剪藏',visual_export:'导出图片',pipeline:'一键金路径',book_search:'书籍搜索',book_fetch:'取文入素材库',tts_generate:'朗读合成',plugin_pipeline:'插件流水线',agent_run:'智能体编排',topic_radar:'选题雷达',quality_loop:'质量循环'};
var jobsDismissed={};
function jobAgo(t){var d=new Date(String(t||'').replace(' ','T')),s;
if(isNaN(d))return String(t||'').slice(5,16);
s=(Date.now()-d.getTime())/1000;
return s<60?'刚刚':s<3600?Math.floor(s/60)+' 分钟前':s<86400?Math.floor(s/3600)+' 小时前':('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2)+' '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);}
function jobTarget(j){var p=j.params||{};return p.article?String(p.article).split('/').pop():(p.book&&p.book.name?'《'+p.book.name+'》':((p.url||(p.source_id?'源 '+p.source_id:(p.all?'全部源':'')))||j.id));}
function jobPct(j){var m=String(j.phase||'').match(/^(\d+)\/(\d+)$/);return m?Math.round(m[1]/m[2]*100):null;}
var SUBX='';
// E1 步级行（{step,status,detail,artifacts}）：任务面板与操作 Tab 同源同形
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
openDlg('viewer','#viewer-close');}
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
// UIX #3：本地图片路径规范化——mdInl 捕获的 URL 可能带 ./ 前缀、查询/锚点片段
// 或百分号编码（如 scene.png?v=2 / %E6%B5%B7.png），直拼进 /asset?path= 会 404
// 裂图。此处在信任边界做规范化；解码失败（非法 %）保留原串不抛错。
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
// ── UIX-3 无障碍批（2026-11-23）：弹窗焦点管理对 ──────────────────────────
// openDlg：记触发元素（data-dlg-from，仅当可寻址）→ 显弹 → 焦点移入弹内首个交互
// 元素（focusSel 可指定，缺省查 input/select/textarea/button——[hidden] 输入跳过）。
// closeDlg：关弹 → 焦点还给触发元素（触发元素已随视图隐藏/不可聚焦时 focus() 静默
// 无操作，无害）。壳/视图弹窗共用；已开着再 openDlg 为幂等 no-op（不覆盖 from）。
function openDlg(id,focusSel){var el=$(id);if(!el||!el.hidden)return;var p=document.activeElement;if(p&&p.id&&p!==document.body)el.setAttribute('data-dlg-from','#'+p.id);el.hidden=false;var f=focusSel?el.querySelector(focusSel):el.querySelector('input:not([hidden]),select,textarea,button:not([disabled])');if(f)f.focus({preventScroll:true});}
function closeDlg(id){var el=$(id);if(!el||el.hidden)return;el.hidden=true;var s=el.getAttribute('data-dlg-from'),b=s&&document.querySelector(s);if(b)b.focus({preventScroll:true});}
// Esc 兜底登记表：只收「纯 hidden 关闭」的弹窗（壳 clip-form + 视图 src-form/
// src-aicfg/coll-form/cv-pm/sc-modal/cm-ch-modal）；有专属收尾态的（#im 状态机、
// design 双层、askConfirm、cp/kbd）由各自处理器收口，不进登记——防 Esc 双关或漏状态复位。
var DM_ESC=['clip-form','src-form','src-aicfg','coll-form','cv-pm','sc-modal','cm-ch-modal'];
function askConfirm(options){
return new Promise(function(resolve){
var modal=$('modal'),from=document.activeElement;
$('modal-title').textContent=options.title||'确认操作';
$('modal-message').textContent=options.message||'';
MC.textContent=options.confirmText||'确认';
modal.hidden=false;
MC.focus();
function finish(value){
modal.hidden=true;
if(from&&from.focus&&from!==MC)from.focus({preventScroll:true}); // UIX-3：焦点还给触发者
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
// FEAT-P1B：写作节奏读面（纯读）；真身归 core 段（A 类专项：load* 工具归 core，
// 消费点=@view:insight 的 renderWriting；失败置 null 由视图走空态引导不报错）。
async function loadWriting(force){
if(state.writing&&!force) return;
try{
state.writing=await getJSON('/api/writing_stats');
}catch(err){
state.writing=null;
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
var on=el.getAttribute('data-nav')===view;
el.classList.toggle('is-active',on);
// UIX-3 无障碍：当前视图导航项标 aria-current=page（读屏/键盘用户的「你在哪」）
if(on)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');
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
// UIX-3 视图子拆分首载映射：material 三拆后首载 -core（drawer/media 由视图内 matPart 按需拉取）
var VJS={material:'material-core.js'};
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
if(!__viewPromises[view]){__viewPromises[view]=(view==='publish'?Promise.resolve():__loadCss("assets/views/"+view+".css").then(function(){return __loadScript("assets/views/"+view+".html.js");}).then(function(){__onViewHtml(view);})).then(function(){return __loadScript("assets/views/"+(VJS[view]||view+".js"));}).catch(function(err){delete __viewPromises[view];throw new Error("视图装载失败: "+view+"（"+err.message+"）");});}
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
function cpClose(){closeDlg('cp');CP.items=[];CP.cur=0;}
function cpOpen(){
openDlg('cp','#cp-input');$('cp-input').value='';cpBuild('');
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
openDlg('clip-form','#clip-url');
}
function clipSubmit0(){
var u=$('clip-url').value.trim();
if(!/^https:\/\/|^http:\/\//i.test(u)){$('clip-err').textContent='URL 须 http(s):// 开头';return;}
return u;
}
// ARCH-1：src-clip-btn/src-crawl-btn/mat-reg-btn 三入口属懒视图骨架——原 top-level
// on2 绑定迁 __onViewHtml（boot 时元素未到会抛 TypeError 中断 core 尾段执行）
on2('clip-cancel',function(){closeDlg('clip-form');});
$('clip-form').addEventListener('click',function(e){if(e.target===this)closeDlg('clip-form');});
$('clip-ok').addEventListener('click',async function(){
var u=clipSubmit0();
if(!u)return;
this.disabled=true;$('clip-err').textContent='';
try{
if(CM==='video'){
var d=await postJSON('/api/materials/register',{url:u,title:$('clip-sub').value.trim(),note:$('clip-note').value.trim(),confirm:true});
closeDlg('clip-form');
	toast(d.dedup?'已登记过（幂等命中）':'视频已登记',d.dedup?WN:OK);
	await loadMaterials();renderCollRail();viewRepaint('material');
	}else if(CM==='crawl'){
	var dc=await postJSON('/api/ingest/crawl',{url:u,confirm:true});
	closeDlg('clip-form');
	await queued(dc.job_id);
	await loadMaterials();viewRepaint('sources');
}else{
var d2=await postJSON('/api/ingest/clip',{action:'preview',url:u,confirm:true});
closeDlg('clip-form');
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
['compose','创作工坊',[['⌘⏎','按反馈再生成']]],
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
function kbdOpen(){kbdPaint();openDlg('kbd-modal','#kbd-close');}
function kbdClose(){closeDlg('kbd-modal');}
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
if(e.defaultPrevented)return; // F1：CM5 编辑器已消费的键（⌘K/⌘F…）不再触发全局快捷键
if((e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();$('cp').hidden?cpOpen():cpClose();return;}
// N-2 灵感速记（2026-11-21）：⌘⇧I/Ctrl+Shift+I 任意视图唤起 spark 浮层
// （零摩擦——按下到存盘两秒内；与 ⌘K 同层绑定，防浏览器 DevTools 抢占尽力拦截）
// 验收项 6 防误触：输入框内（含 CM5 隐藏 textarea / contenteditable）不劫持——
// 与下方 g 序列同款守卫；用户在编辑器里按 Ctrl+Shift+I 是文本操作，不是开浮层。
// 守卫只折进本条件（不用 blanket return），输入框内 ⌘K/⌘1-7/Escape 行为零变更。
if(!(e.target.closest&&e.target.closest('input,select,textarea,[contenteditable]'))&&(e.metaKey||e.ctrlKey)&&e.shiftKey&&(e.key==='i'||e.key==='I')){e.preventDefault();sparkBox?sparkClose():sparkOpen();return;}
// ⌘1-7 分域（深构 §5.1/§八-4 裁决；2026-11-17 集成轮自 design 域修为全局）：
// 视图外管七视图导航；#article 内 ⌘1-4 仍是 Tab 切换（彼视图优先）、⌘5-7 导航。
// ⌘1-4 的 setTab 经 VAPI.article（A 类专项：core 不再直呼 article 视图函数）
if((e.metaKey||e.ctrlKey)&&e.key>='1'&&e.key<='7'){
e.preventDefault();
var a1=VAPI.article;
// #article 内 ⌘1-4 视图装载窗口（ensureView 未完成、VAPI.article 未登记）消费为
// no-op，绝不回落 DNAV 导航——否则窗口期 ⌘2 会被劫持到 #material，违反「彼视图
// 优先」契约（2026-11-22 e2e 实测判例：15s 内 panel-variants 恒 hidden、快照落
// #material 暂无素材）。
if(!$('view-article').hidden&&+e.key<=4){if(a1&&a1.setTab)return a1.setTab(['edit','variants','checks','preview'][+e.key-1],true);return;}
location.hash='#'+DNAV[+e.key-1];return;
}
// P4 g 序列（§5.1）：首键 g 后 600ms 内待次键（g s/m/c/p/i 与 g ,）；输入框内不劫持
if(!e.metaKey&&!e.ctrlKey&&!e.altKey&&!e.target.closest('input,select,textarea')){
if(gSeq){gSeq=0;var gh=e.key===','?'settings':GK[e.key];if(gh){e.preventDefault();location.hash='#'+gh;}return;}
if(e.key==='g'){gSeq=1;setTimeout(function(){gSeq=0;},600);return;}
// FEAT-5 键盘可达：? 唤快捷键速查；/ 聚焦当前视图搜索框（映射见 KFOCUS——
// 各视图主搜索入口统一入口键，零视图块改动）。二者沿用 g 序列同款「输入框外」守卫。
if(e.key==='?'){e.preventDefault();kbdOpen();return;}
if(e.key==='/'){var kf=KFOCUS[routeInfo().name];if(kf){var ke=$(kf);if(ke){e.preventDefault();ke.focus();if(ke.select)ke.select();}}return;}
}
if(e.key==='Escape'){
if(sparkBox){sparkClose();return;}
if(!$('cp').hidden){cpClose();return;}
if(!$('kbd-modal').hidden){kbdClose();return;} // FEAT-5：速查弹层与其他浮层同层可关
var dEsc=DM_ESC.filter(function(d){var m=$(d);return m&&!m.hidden;});
if(dEsc.length){dEsc.forEach(closeDlg);return;} // UIX-3：登记制弹窗 Esc 兜底（纯 hidden 关闭族；有专属收尾的 im/design 不在此列）
if(!$('ctx').hidden){ctxHide();return;}
if(AB&&!AB.hidden)hideBubble(); // ARCH-1：article 未注入时 AB 为 null（注入前隐藏态语义等价——跳过）
if(!$('viewer').hidden)closeDlg('viewer');
if(!JM.hidden){JM.hidden=true;jobsPollStop();}
}
});
// FEAT-5：/ 的视图→搜索框映射（无主搜索框的视图不映射，/ 静默——不抢浏览器快捷键）
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
// §2.3 徽标深链：未读→素材库未读档、待审→发布中心待审档、失败源→源中枢（源总表健康灯/
// 近 7 次失败列可见；sources 的 tab=health 暂无消费端——源总表无 health/subs Tab，记视图层待办）
function badgeGo(hash){
if(location.hash===hash){renderRoute();return;} // 同 hash 不触发 hashchange，手动重跑
location.hash=hash;
}
$('nav-badge-material').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#material?tab=unread');});
$('nav-badge-publish').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#publish?filter=pending');});
$('nav-badge-sources').addEventListener('click',function(e){e.preventDefault();e.stopPropagation();badgeGo('#sources');});
// UIX-3 键盘可达：rail 徽标是带点击行为的 span——补 tabindex/role=button + Enter/Space
// 激活（Tab 序不跳过深链入口；焦点环样式见 core.css .nav-badge:focus-visible）
QA('.nav-badge').forEach(function(b){
b.setAttribute('tabindex','0');b.setAttribute('role','button');
b.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();b.click();}});
});
$('cp-btn').addEventListener('click',function(){$('cp').hidden?cpOpen():cpClose();});
$('rail-fold').addEventListener('click',function(){
var root=document.documentElement;
var fold=root.getAttribute('data-rail')==='min';
if(fold)root.removeAttribute('data-rail');else root.setAttribute('data-rail','min');
try{localStorage.setItem('musespiral-rail',fold?'max':'min');}catch(e){}
this.title=fold?'折叠导航轨':'展开导航轨';
this.setAttribute('aria-label',this.title);
});
// cred-verify-btn 绑定已迁 @view:settings（按钮属设置 DOM，renderSettings 调用
// 因此视图内闭环——A 类专项 2026-11-20）
}
/*__MDPUB_UI_SPLIT__*/
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
// D2 素材类型图标 6 枚（终评 §三 P1-D2：image/video 原同用 eye 零区分）——
// 矩形+山+日 / 播放三角 / 链两环 / 同心弧+点 / 地球经纬 / 菱形+箭头
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
// PL-3 插件系统 2 枚（2026-11-20）：play=运行按钮/日志行；flow=流水线节点链（导航与画布箭头）
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
// @view:sources
// 源中枢（重构设计 §3.1/契约 §2.1）：生态带 + 源总表 + 失败聚合（任务中心 ingest 历史按源计，零新后端）
function srcFailCounts(){
var seen={},c={};
(state.jobs||[]).forEach(function(j){
if(!j||j.kind!=='ingest')return;
var sid=(j.params||{}).source_id;
if(!sid)return;
seen[sid]=(seen[sid]||0)+1;
if(seen[sid]<=7&&j.status==='failed')c[sid]=(c[sid]||0)+1;
});
return c;
}
function syncSrcHash(){history.replaceState(null,'','#sources'+(state.ssrc?'?src='+encodeURIComponent(state.ssrc):''));}
function renderSources(){
var list=matSrcs(),sc=((state.settings||{}).sources)||{},counts=sc.counts||{},mats=state.mats||[];
var unread={},today={},td=dkey(new Date());
mats.forEach(function(m){var k=(m.origin||{}).source_id;if(!k)return;
if(unread[k]==null){unread[k]=0;today[k]=0;}
if(!(m.triage||{}).read)unread[k]++;
if(String(m.fetched_at||'').slice(0,10)===td)today[k]++;});
var tNew=0,tUnread=0;Object.keys(today).forEach(function(k){tNew+=today[k];tUnread+=unread[k];});
var fails=srcFailCounts(),en=0,bad=0;
list.forEach(function(s){if(s.enabled)en++;if((s.health||{}).status==='down'||fails[s.id])bad++;});
$('eco-tiles').innerHTML=[
['启用源',en+'/'+list.length,''],
['今日新增素材',tNew,''],
['未读素材',tUnread,''],
['失败源',bad,bad?'amber':'']
].map(function(t){
return '<div class="eco-tile '+(t[2]||'')+'"><span class="eco-n">'+esc(t[1])+'</span><span class="eco-l">'+esc(t[0])+'</span></div>';
}).join('');
var rows=list.filter(function(s){return !state.srctype||s.type===state.srctype;});
$('src-rows').innerHTML=rows.length?rows.map(function(s){
var h=s.health||{},st=h.status||'unknown',un=unread[s.id]||0,n=counts[s.id]||0;
var dot='<span class="health-dot '+st+'" title="'+(st==='ok'?'正常（上次 '+esc(h.last_ok||'刚刚')+'）':st==='down'?'down（上次正常 '+esc(h.last_ok||'—')+'）':'未探测（手动验证或刷新时回写）')+(h.last_refresh?'；上次拉取 '+esc(h.last_refresh):'')+'">'+(st==='down'?'!':st==='ok'?'':'·')+'</span>';
return '<tr'+(state.ssrc===s.id?' class="sel"':'')+' data-srcrow="'+esc(s.id)+'"><td>'+dot+'</td><td>'+esc(s.label||s.id)+'<span class="meta-path" title="源 ID：'+esc(s.id)+'">'+esc(s.id)+Z+'</td><td><span class="tag">'+esc(s.type)+Z+'</td><td>'+((s.subscriptions||[]).length||'—')+'</td><td>'+n+(un?' <span class="badge warn">'+un+' 未读</span>'+Z:'')+'</td><td>'+esc(h.last_ok||'从未')+'</td><td>'+(fails[s.id]?'<span class="badge fail" title="近 7 次拉取失败 '+fails[s.id]+' 次">'+fails[s.id]+Z:'—')+'</td><td><input type="checkbox" aria-label="启用源" data-srcen="'+esc(s.id)+'"'+(s.enabled?' checked':'')+'></td><td><button class="btn" data-srcverify="'+esc(s.id)+'">验证</button> <button class="btn" data-srcrefresh="'+esc(s.id)+'">刷新</button> <button class="btn" title="源级 AI 配置（只影响该源素材加工）" aria-label="源级 AI 配置" data-srcai="'+esc(s.id)+'">⚙</button></td></tr>';
}).join(''):'<tr><td colspan="9" class="action-hint">暂无此类型源</td></tr>';
$('src-empty').hidden=list.length>0;
$('sources-sub').textContent='共 '+list.length+' 源 · 启用 '+en+' · 素材 '+(sc.total_materials!=null?sc.total_materials:mats.length)+' 条';
updateNavBadge('nav-badge-sources',bad); // §2.1 源位徽标=失败源数（health down 或近 7 次拉取失败，>0 显琥珀）
QA('[data-srctype]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-srctype')===state.srctype);});
}
function refreshSourcesView(){
if(!$('view-sources').hidden)renderSources();
}
async function srcVerify(id){
	try{var d=await postJSON('/api/sources/verify',{id:id,confirm:true});var st=(d.health&&d.health.status)==='ok';
	await refreshSrcData();refreshSourcesView();
	toast('「'+id+'」探测：'+(st?'正常':'down/未配置（灰灯非失败）'),st?OK:WN);}
	catch(err){toast('验证失败：'+err.message,ER);}
	}
async function srcVerifyAll(){
	var list=matSrcs(),okc=0;
	for(var i=0;i<list.length;i++){
	try{var d=await postJSON('/api/sources/verify',{id:list[i].id,confirm:true});if((d.health&&d.health.status)==='ok')okc++;}
	catch(err){}
	}
	await loadSettings();refreshSourcesView();
	toast('全部源探测完成：'+okc+'/'+list.length+' 正常（结果见健康灯）',OK);
	}
// P2 源注册表单（契约 §2.1-2）：类型三选 → 参数域联动 → POST /api/sources（confirm 闸门）
function sfTypeSync(){
var t=$('sf-type').value;
$('sf-base').hidden=$('sf-subs').hidden=t!=='radar_hub';
$('sf-root').hidden=t!=='kb';
$('sf-path').hidden=t!=='dir';
$('sf-url').hidden=t!=='rss';
}
function sfOpen(){sfTypeSync();openDlg('src-form','#sf-id');}async function sfSubmit(){
var t=$('sf-type').value,id=$('sf-id').value.trim(),cfg={};
var K={radar_hub:['base','sf-base'],kb:['root','sf-root'],dir:['path','sf-path'],rss:['url','sf-url']}[t];
cfg[K[0]]=$(K[1]).value.trim();
if(!cfg[K[0]]){toast(t+' 必填 config.'+K[0],ER);return;}
var subs=$('sf-subs').value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
try{await postJSON('/api/sources',{id:id,type:t,config:cfg,subscriptions:subs,confirm:true});
toast('源已注册：'+id,OK);
closeDlg('src-form');
await refreshSrcData();renderSources();renderCollRail();
}catch(err){toast('注册失败：'+err.message,ER);}
}
// P2 源详情抽屉：行点击展开（订阅列表/健康/启停/验证/刷新/两步删除）
function srcById(id){return matSrcs().filter(function(s){return s.id===id;})[0]||null;}
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
if(p)cfg.profile=p;
if(m)cfg.model=m;
if(t!==''){var tv=Number(t);if(!isFinite(tv)||tv<0||tv>2){toast('temperature 必须是 0-2 之间',ER);return;}cfg.temperature=tv;}
if(po.trim())cfg.prompt_override=po;
try{
await postJSON('/api/sources',{id:s.id,type:s.type,label:s.label,config:s.config,enabled:s.enabled,subscriptions:s.subscriptions||[],ai_config:cfg,confirm:true});
toast('「'+id+'」AI 配置已保存（只影响该源素材加工，不影响文章生成）',OK);
closeDlg('src-aicfg');
await refreshSrcData();renderSources();
}catch(err){toast('保存失败：'+err.message,ER);}
}
function paintSrcDrawer(id){
var s=srcById(id);
if(!s){$('src-drawer').hidden=true;return;}
var h=s.health||{},cfg=s.config||{},subs=s.subscriptions||[];
$('src-drawer-title').textContent=s.label||s.id;
$('src-drawer-body').innerHTML=
'<p class="action-hint">'+esc(s.id)+' · '+esc(s.type)+(s.enabled?'':' · 已禁用')+' · 健康 '+(h.status==='ok'?'正常':h.status==='down'?'down':'未探测')+' · 参数 '+esc(cfg.base||cfg.root||cfg.path||cfg.url||'—')+'</p>'+
'<div class="action-title" style="font-size:13px">订阅（'+subs.length+'）</div>'+
(subs.length?'<pre class="drawer-preview">'+esc(subs.join('\n'))+'</pre>':'<p class="action-hint">无订阅</p>')+
'<div class="action-row mat-acts"><button class="btn" data-srcverify="'+esc(s.id)+'">验证</button><button class="btn" data-srcen2="'+esc(s.id)+'">'+(s.enabled?'禁用':'启用')+'</button><button class="btn mat-del" data-srcdel="'+esc(s.id)+'">删除…</button></div>';
$('src-drawer').hidden=false;
}
// 两步删除：契约 §2.1 无源删除端点（仅注册/验证/启停）——降级为禁用下线，不虚构 API
async function srcDelete(id){
var s=srcById(id),ok=await askConfirm({title:'删除源 1/2',message:'删除「'+((s&&s.label)||id)+'」？素材不连坐。',confirmText:'继续'});
if(!ok)return;
ok=await askConfirm({title:'删除源 2/2',message:'无删除端点（契约仅注册/启停）：以禁用作等效下线。',confirmText:'禁用下线'});
if(!ok)return;
try{await postJSON('/api/sources/enabled',{id:id,enabled:false,confirm:true});await refreshSrcData();refreshSourcesView();paintSrcDrawer(id);toast('已禁用下线',WN);}
catch(err){toast('下线失败：'+err.message,ER);}
}
async function srcToggle(id,en){
	try{await postJSON('/api/sources/enabled',{id:id,enabled:en,confirm:true});await refreshSrcData();refreshSourcesView();toast(en?'源已启用（进采集循环）':'源已禁用（不进采集循环）',OK);}
	catch(err){toast('启停失败：'+err.message,ER);await refreshSrcData();refreshSourcesView();}
	}
// 路由入口（原 renderRoute sources 分支整体迁入——A 类专项：分支归视图）
VAPI.sources={enter:async function(route){
var sp=route.params.get('src');
if(sp!=null)state.ssrc=sp;
await Promise.all([refreshSrcData(),loadMaterials(),refreshJobs()]);
renderSources();
},repaint:async function(){await loadMaterials();renderSources();}};
function bindSources(){

$('src-empty-add').addEventListener('click',sfOpen); // FEAT-5：空态引导 CTA（与头「注册源」同路 sfOpen）
$('src-rows').addEventListener('click',function(e){
var v=e.target.closest('[data-srcverify]');
if(v){srcVerify(v.getAttribute('data-srcverify'));return;}
var r=e.target.closest('[data-srcrefresh]');
if(r){matIngest(r.getAttribute('data-srcrefresh'));return;}
var a=e.target.closest('[data-srcai]');
if(a){srcAiOpen(a.getAttribute('data-srcai'));return;}
var row=e.target.closest('[data-srcrow]');
if(row){state.ssrc=row.getAttribute('data-srcrow');syncSrcHash();renderSources();paintSrcDrawer(state.ssrc);}
});
$('src-drawer').addEventListener('click',function(e){
if(e.target.closest('#src-drawer-close')){this.hidden=true;return;}
var v=e.target.closest('[data-srcverify]');
if(v){srcVerify(v.getAttribute('data-srcverify'));return;}
var en=e.target.closest('[data-srcen2]');
if(en){var s=srcById(en.getAttribute('data-srcen2'));srcToggle(en.getAttribute('data-srcen2'),!(s&&s.enabled));return;}
var d=e.target.closest('[data-srcdel]');
if(d)srcDelete(d.getAttribute('data-srcdel'));
});
// UIX-3 键盘可达：源详情抽屉 Esc 收口（纯面板，无状态机——直接 hidden）
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&!$('src-drawer').hidden)$('src-drawer').hidden=true;
});
$('src-add-btn').addEventListener('click',sfOpen);
$('src-form').addEventListener('click',function(e){if(e.target===this||e.target.id==='sf-cancel')closeDlg('src-form');});
$('sf-type').addEventListener('change',sfTypeSync);
$('sf-ok').addEventListener('click',sfSubmit);
$('src-aicfg').addEventListener('click',function(e){if(e.target===this||e.target.id==='sac-cancel')closeDlg('src-aicfg');});
$('sac-ok').addEventListener('click',srcAiSave);
$('src-rows').addEventListener('change',function(e){
var t=e.target;
if(t&&t.getAttribute&&t.getAttribute('data-srcen'))srcToggle(t.getAttribute('data-srcen'),t.checked);
});
$('src-filters').addEventListener('click',function(e){
var c=e.target.closest('[data-srctype]');
if(!c)return;
state.srctype=c.getAttribute('data-srctype');
renderSources();
});
$('src-ingest-all-btn').addEventListener('click',function(){matIngest('');});
$('src-verify-all-btn').addEventListener('click',srcVerifyAll);
}
bindSources();
// @endview
// @view:material
function matPreview(m){
var p=m.payload||{},o=m.origin||{},t=p.text||(o&&o.title)||'';
return String(t).replace(/\s+/g,' ').slice(0,120);
}
// fillMatType/matQS/loadMaterials/loadDiscover/loadColls/loadTags 真身已迁 core 段
// 跨视图共享块（消费点=core 路由+sources/compose/insight 多块；A 类专项 2026-11-20）
function hbytes(n){n=+n||0;if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(1)+' KB';return(n/1048576).toFixed(1)+' MB';}
// hl() 真身已迁 core（体积锚）
var MEMPTY_T=$('mat-empty-title').textContent,MEMPTY_X=$('mat-empty-text').textContent;
// mat-row（重构设计 §3.2/§四-4）：类型图标+标题+两行 clamp 预览+源徽+评分微条+license 徽；
// 星标/已读双开关位置恒定（行首/行尾——FreshRSS 肌肉记忆范式）；chevron 展开四维与动作
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
// R3.5 T3：「未提炼」琥珀徽（distilled=false 降级标记；AI-GENERATED=来源，两个维度可并存）
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
'<div class="tag-row" data-tagrow="'+esc(m.id)+'">'+tg.map(function(t2){return '<span class="chip-tag" data-mtagf="'+esc(t2)+'" title="点击按此标签过滤">'+esc(t2)+'<i data-mtagdel="'+esc(m.id)+'|'+esc(t2)+'" title="移除标签" tabindex="0" role="button" aria-label="移除标签 '+esc(t2)+'">×</i></span>';}).join('')+'<input class="input tag-in" maxlength="24" list="tag-dl" placeholder="输入标签，回车添加" data-mtagin="'+esc(m.id)+'"></div>'+
'<p class="action-hint">❝ '+esc(o.uri||m.id)+' · '+esc(o.source_id||'')+' · '+esc(String(m.fetched_at||'').replace('T',' '))+(g?' · '+g:'')+(s==null?'':' · 评分 '+(s==null?'—':s))+'</p></div></details>';
}
function srcGroups(L){
var names={},g={},order=[],out='';
matSrcs().forEach(function(s){names[s.id]=s.label||s.id;});
L.forEach(function(m){var k=(m.origin||{}).source_id||'—';if(!g[k]){g[k]=[];order.push(k);}g[k].push(m);});
order.forEach(function(k){out+='<h3 class="mat-src">'+esc(names[k]||k)+'<span class="chip-count">'+g[k].length+'</span></h3>'+g[k].map(matRow).join('');});
return out;}
// hash 白名单（契约 §2.2）：src/tab/dens/type/m + M3 扩 coll/tag（集合页/标签过滤深链）
function syncMatHash(){
var q=['src='+encodeURIComponent(state.msrc)];
if(state.mtab!=='all')q.push('tab='+state.mtab);
if(state.mdens!=='compact')q.push('dens='+state.mdens);
if(state.mtype)q.push('type='+state.mtype);
if(state.mspark)q.push('spark=1'); // N-2 灵感筛选深链（chip 建于 core.fillMatType）
if(state.mcoll)q.push('coll='+encodeURIComponent(state.mcoll));
if(state.mtag)q.push('tag='+encodeURIComponent(state.mtag));
if(state.mdrawer)q.push('m='+state.mdrawer);
history.replaceState(null,'','#material'+(q.length?'?'+q.join('&'):''));
}
// 列表域三件套：全库搜索（服务端 q，F64）+ 类型/标签筛选 + 排序（评分缺省=API 序）
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
// 左集合轨（重构设计 §3.2）：静态分区 + 智能集合（规则徽）+ 源集合（动态）
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
updateNavBadge('nav-badge-material',ur); // §2.1 素材位徽标=未读数（全量口径，非筛选后）
QA('[data-mtab]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-mtab')===tab);});
QA('[data-mdens]').forEach(function(el){el.classList.toggle('is-active',el.getAttribute('data-mdens')===state.mdens);});
var sk=$('mat-spark');if(sk)sk.classList.toggle('is-active',!!state.mspark); // N-2 灵感 chip 激活态（深链/清筛选后同步）
$('mat-coll-exit').hidden=!state.mcoll;
var cn='';(state.colls||[]).forEach(function(c){if(c.id===state.mcoll)cn=c.name;});
$('mat-sub').textContent=(state.mquery?'命中 '+(state.mqTotal>state.mats.length&&state.mats.length>=500?'前 '+state.mats.length+' / 共 '+state.mqTotal:state.mqTotal)+' 条':(state.mcoll?'集合「'+(cn||state.mcoll)+'」'+L.length+' 条':'共 '+L.length+' 条素材'))+' · 收藏 '+sn+' · 未读 '+ur+' · 已生成骨架 '+df;
if(!$('mat-drawer').hidden&&state.mdrawer)paintMatDrawer();
}

// UIX-3 素材视图三拆（material-core/drawer/media）：core 随 ensureView 首载；抽屉/
// AI 面板/灯箱两子文件按需装载（三步链同款幂等 Promise，失败清缓存可重试）。
// 调用点=matActsClick 委托/VAPI ?m= 深链/mat-drawer-close/coll-new/gen·upload 入口；
// 「抽屉/灯箱可见 ⇒ 子文件已载」不变量成立的调用点免门直呼（renderMaterials 尾绘
// paintMatDrawer/matDelete 关抽屉/data-mrelated·tsg·tso·morig 抽屉内委托/灯箱键序）。
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

// 路由入口（原 renderRoute material 分支整体迁入——A 类专项：分支归视图）
VAPI.material={enter:async function(route){
await loadSettings();
var msp=route.params.get('src'),mtp=route.params.get('tab'),mdp=route.params.get('dens'),mty=route.params.get('type'),mid=route.params.get('m'),mcl=route.params.get('coll'),mtg=route.params.get('tag');
if(msp!=null)state.msrc=msp;
if(mtp==='review')mtp='revisit';
if(mtp==='all'||mtp==='starred'||mtp==='unread'||mtp==='hiunread'||mtp==='derived'||mtp==='revisit')state.mtab=mtp;
if(mdp==='compact'||mdp==='src'||mdp==='full')state.mdens=mdp;
if(mty!=null)state.mtype=mty;
var msk=route.params.get('spark');if(msk!=null)state.mspark=msk?'spark':''; // N-2 灵感筛选深链
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
// 素材动作统一委托（行/抽屉/展开区同源同函数——重构设计 §5.4 规则；
// M3 扩：mlight 灯箱/mcolladd 集合面板/mtagdel·mtagf 标签）
function matActsClick(e){
var el=e.target.closest('[data-mrelated],[data-mstar],[data-mdraft],[data-mread],[data-moff],[data-mai],[data-mdel],[data-mdetail],[data-mdrow],[data-mlight],[data-mcolladd],[data-mtagdel],[data-mtagf],[data-mmore],[data-tsg],[data-tso],[data-miit],[data-morig]');
if(!el)return;
e.preventDefault();
if(el.hasAttribute('data-mrelated'))return openMatDrawer(el.getAttribute('data-mrelated')); // 抽屉可见⇒drawer 已载，免门
if(el.hasAttribute('data-morig'))return bkOrigToggle();
// P2 打磨历史 tab（iterations.js 持 tab 态与面板渲染；本块只委托）
if(el.hasAttribute('data-miit'))return window.__mdpubMatIter&&__mdpubMatIter.tab(el.getAttribute('data-miit')==='1',state.mdrawer);
if(el.hasAttribute('data-tsg')){
var tg=el.getAttribute('data-tsg');
return tg==='ok'?tsugAdopt(state.mdrawer):tsugGo(state.mdrawer);
}
if(el.hasAttribute('data-tso')){var ti=el.getAttribute('data-tso');tsug.sel[ti]=!tsug.sel[ti];return paintMatDrawer();}
if(el.hasAttribute('data-mlight'))return matPart('media').then(function(){lbOpen(el.getAttribute('data-mlight'));});
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
// #ctx 右键菜单同源委托（A 类专项 2026-11-20）：matActsClick 是 bindMaterial 局部
// 函数，原 core bindCore 直呼全局同名=ReferenceError（右键菜单星标/AI 摘要/删除
// 实际全失效）——素材域动作改由本块自挂（与 data-ctg 集合委托同款同节点）。
$('ctx').addEventListener('click',matActsClick);
$('mat-drawer-close').addEventListener('click',function(){matPart('drawer').then(closeMatDrawer);});
// 三入口合并修（2026-11-17）：下行绑定曾被 E3 批整行覆盖——mat-drawer-close 的 ×
// 按钮失效（真浏览器复验），此处恢复原绑定行
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
// F64 全库搜索：防抖 300ms 服务端 q（全过滤参数正交拼参——state.mats 只持截断集）
// FEAT-1：有 query 时经 matSearch 走三模式统一搜索面；无 query 回原 /api/materials
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
// FEAT-1 模式指示器：手动切关键词/语义/自动；切换即按当前 query 重搜
var mm=$('mat-modes');if(mm)mm.addEventListener('click',function(e){
var b=e.target.closest('[data-searchmode]');if(!b)return;
state.searchMode=b.getAttribute('data-searchmode');syncMatModes();hideMatHint();
if(state.mquery)matSearch(state.mquery);
});
$('mat-type').addEventListener('change',function(){state.mtype=this.value;syncMatHash();renderMaterials();});
$('mat-sort').addEventListener('change',function(){state.msort=this.value;renderMaterials();});
on2('mat-ingest-btn',function(){matIngest('');});
// ══ M3 多模态（F59-F64）+ B4a 采集入口（前端接线批；体积门 208KiB 纪律下的极简面）══
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
// F63 标签：chips ×移除/点击过滤 + 行内输入（原生 datalist 建议层——频次表 Top 填充；
// 规范化预览/频次徽因体积门降级，记债）+ 12 上限琥珀提示；窄门=POST triage tags 全量替换
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
// UIX-3 键盘可达：缩略图/标签 ×（role=button tabindex=0）Enter/Space 激活——
// 走既有 click 委托 matActsClick，键鼠同一代码路径
var kb=e.target.closest('[data-mlight],[data-mtagdel]');
if(kb&&(e.key==='Enter'||e.key===' ')){e.preventDefault();kb.click();}
});
on2('coll-new-btn',function(){matPart('drawer').then(function(){$('cf-name').value='';cfDots();openDlg('coll-form','#cf-name');});});
}
bindMaterial();
// @material-drawer
// 素材详情子文件（UIX-3 三拆；core 载入后经 matPart 抽屉段按需拉取——进抽屉才载）：
// 详情抽屉 openMatDrawer/paintMatDrawer/closeMatDrawer + AI 标签建议 tsug 族 +
// 原始章节 bkOrigToggle + AI 摘要 refine（matAI）+ 手工集合域（CF/collMenu/collTg
// 与 cf-* 绑定——「+ 集合」入口行/抽屉动作排共用，集合委托 data-ctg 在此自挂）。
// 素材详情抽屉（重构设计 §3.2）：预览+四维+license+血统链（provenance 首次渲染）+连坐+动作排
// lineageHtml() 真身已迁 core（体积锚）
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
// F-A（2026-11-18）：媒体本体按 type 分流——image 顶部原图（file full 非 thumb）+灯箱入口；
// video 播放条（local_path 走 file；外链 payload.url／o.uri）+新窗口；余类型维持 pre 正文。
var media='',full='/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=full';
if(m.type==='image')media='<img class="drawer-media" src="'+esc(full)+'"><button class="btn" data-mlight="'+esc(m.id)+'">灯箱查看</button>';
else if(m.type==='video'){var vu=p.local_path?full:(p.url||o.uri);media='<video class="drawer-media" controls preload="metadata" src="'+esc(vu)+'"></video><a class="btn" href="'+esc(vu)+'" target="_blank" rel="noopener">新窗口打开</a>';}
$('mat-drawer-title').innerHTML=(tri.starred?'★ ':'')+esc(m.title||o.uri||m.id);
$('mat-drawer-body').innerHTML=
// P2 打磨历史 tab（iterations.js 独立产物驱动；壳零新增，骨架全 JS 渲染）
'<div class="chips" style="margin:0 0 var(--space-sm)"><button class="chip" data-miit="0">内容</button><button class="chip" id="mi-t1" data-miit="1">打磨历史</button></div>'+
'<div id="mi-view">'+
(media||'<pre class="drawer-preview">'+esc(prev)+'</pre>')+
h+
'<p class="drawer-meta">'+licBadge(m)+'<span class="tag">'+esc(MTY[m.type]||m.type)+Z+'<span class="src-badge">'+esc(o.source_id||'—')+'</span> · '+esc(String(m.fetched_at||'').replace('T',' '))+'</p>'+
// R3.5 T3：抽屉来源行（book 元数据——书名深链回 #books）
(meta.book?'<p class="drawer-meta bk-line">来源 <a href="#books?search='+encodeURIComponent(meta.book.name||'')+'">《'+esc(meta.book.name||'')+'》</a> · '+esc(meta.book.author||'')+(meta.book.chapter_index?' · 第 '+meta.book.chapter_index+' 章 '+esc(meta.book.chapter_title||''):'')+(meta.book.synthesis?' · 全书综述（'+(meta.book.chapters||0)+' 章）':'')+'</p>':'')+
'<div class="action-title" style="font-size:13px">血统链</div>'+lineageHtml(m)+
(casc.length?'<p class="action-hint">被 '+casc.length+' 条衍生素材引用（删除连坐面）</p>':'')+
matRelatedHtml(d.related)+
tsugBox(m)+
// R3.5 T4：「原始章节」btn-ghost（.mat-acts 前）——按需加载抽屉内 details 就地展开
(meta.original_ref?'<div class="action-row"><button class="btn btn-ghost" data-morig="1" style="font-size:var(--fs-caption)">原始章节</button></div>':'')+
'</div>'+
'<div id="mi-body" hidden></div>'+
'<div class="action-row mat-acts">'+(m.type==='text'?'<button class="btn" data-mdraft="'+esc(o.uri)+'"'+(m.drafted?' disabled':'')+'>生成骨架</button>':'')+((meta.url||p.url)?'<a class="btn" href="'+esc(meta.url||p.url)+'" target="_blank" rel="noopener">查看原文</a>':'')+'<button class="btn" data-mcolladd="'+esc(m.id)+'">+ 集合</button><button class="btn" data-mstar="'+esc(m.id)+'">'+(tri.starred?'取消收藏':'收藏')+'</button><button class="btn" data-mread="'+esc(m.id)+'">'+(tri.read?'已读':'未读')+'</button><button class="btn" data-moff="'+esc(m.id)+'">'+(tri.enabled?'禁用':'启用')+'</button><button class="btn" data-mai="'+esc(m.id)+'">AI 摘要</button><button class="btn mat-del" data-mdel="'+esc(m.id)+'">删除</button></div>'+
'';
if(window.__mdpubMatIter)__mdpubMatIter.repaint(state.mdrawer);
if(window.__mdpubMatLineage)__mdpubMatLineage.repaint(state.mdrawer); // N-4 血统 tab（chip/容器由 lineage.js 自注入）
}
// 入口 B（设计 §四）：AI 标签建议——只建议不落库。chips 默认全部未选（「AI 只建议」的
// 字面落实）；[采纳所选] 才走既有 /api/materials/triage 全量替换写面。打开详情不静默调 AI。
var tsug={};
// 入口 B（§四）：AI 标签建议——只建议不落库（chips 默认全未选，[采纳所选] 才写
// triage.tags 全量替换；基准=素材自身 tags 既有优先，超 12 事前告知被弃项）；
// 打开详情不静默调 AI；cache 徽只在服务端真回 cache_hit 时显示
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
// UIX-3 无障碍批：详情抽屉 Esc 收口（走 closeMatDrawer 真身保 hash 同步；抽屉
// 可见 ⇔ 本子文件已载）。综合/生成两面板的 Esc 由 media 段 capture 处理器兼收。
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&!$('mat-drawer').hidden)closeMatDrawer();
});
// T4：原始章节按需加载（details 就地展开；>50k 折叠两段：首 20k+展开全部）。
// 2026-11-20 A 类专项（台账 A-2）自 @view:books 迁入：按钮由 paintMatDrawer 渲染、
// 点击经 matActsClick 委托、DOM 落点=#mat-drawer-body——消费链与 DOM 全在素材
// 抽屉，books 块真身仅在直入 #books 后被 material 顶到（占位 no-op）才可用。
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

// P3 refine 真流（契约 §2.2-5）：preview 零落盘→预览卡（可编辑）→人工「存为素材」
// adopt 落 derived；放弃零 API（面板即焚，AI 只标记不否决红线）。
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

// F62 手工集合：新建模态（五色点）+ ctx 菜单勾选（add=全量替换/remove=差集，服务端语义）
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
// @material-media
// 素材媒体·AI 面板子文件（UIX-3 三拆；经 matPart 媒体段按需拉取——首触才载）：
// AI 综合 syn 族（多选→步级面板→采纳）/ 文生图 gen 族 / 上传 tray 族 / 灯箱 LB 族；
// 面板与灯箱绑定收口 bindMatMedia（文件装载即挂；core 只留首触入口门与预检）。
// G1 文生图：idle→生成中→预览|失败（预览=服务端临时文件 URL，零 b64）
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

// F59 上传三入口（按钮/拖放/粘贴）→ 预检（扩展名/8MiB）→ 顺序 POST 二进制旁路；
// tray 行三态点（license 缺省 UNKNOWN——选项 UI 因体积门降级，正典 F59 交互流 4 记债）
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
// F60 灯箱：matFiltered 可见集 image 子序键盘导航（←/→/Esc）；信息条常驻
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
// 入口 A 绑定（关闭不取消任务：任务在 jobs 层继续，重开面板可续看）；
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
// F60 灯箱键盘（Esc/←/→，开启即吞全部单键）——原居 core bindCore，对 lbClose/lbGo
// 的调用属跨视图直呼（A 类专项 2026-11-20 下沉本块；capture 先于 core 的 bubble
// 处理器，stopPropagation 保持「灯箱开启即吞 g 序列/⌘K」原语义）。
// UIX-3 无障碍批：灯箱关闭态同处理器兼收本段两面板（生成 gen / 综合 syn）的
// Esc——面板可见 ⇔ 本文件已载，函数域内 gAb 直引安全；不吞传播，其余 Esc 链照走。
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
// @endview
// @view:books
// ── R4 #books 书籍视图 + R3.5 提炼四触点（2026-11-18）───────────────
// 详情态=GET /api/book/info|toc（引擎代理，absent→灰灯卡）；入库=POST
// /api/materials/from-chapters（confirm 闸在端点，提交零落盘）→job
// book_fetch 步级轮询（jobs phase + agent runs 步明细双源）。
var BK={bookUrl:'',sourceUrl:'',name:'',info:null,toc:[],gs:[],sel:{},filter:'',spec:'all',job:null,pid:0};
// T1：AI 提炼 switch 默认开；localStorage musespiral-book-digest 记忆上次选择
function bkDigestInit(){try{BK.digest=localStorage.getItem('musespiral-book-digest')!=='0'}catch(e){BK.digest=true}
var b=$('bk-digest');if(b){b.checked=BK.digest;$('bk-digest-off').hidden=BK.digest;}}
function bkSub(m){$('bk-search').hidden=m!=='search';$('bk-detail').hidden=m!=='detail';$('bk-subs').hidden=m!=='subs';}
function bkMode(m,kw){bkSub(m);if(m==='search'){bkLamp();if(kw)$('bk-search-box').value=kw;}}
// 引擎三态灰灯（2026-11-23 用户反馈批）：未建设/体验模式/可搜——「未建设不是
// 故障」，须把「为什么 + 现在能干什么 + 想试怎么办」讲清楚（旧版只有一句「未就绪」，
// 用户无法判断是坏了还是没建）
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
// 体验按钮只在未建设时出现（已可搜/体验模式中不重复引导）
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
// 换源 select：多源候选来自搜索态（R4）；变更=重拉 info/toc 并清空勾选
$('bk-src-sel').innerHTML='<option value="'+esc(BK.sourceUrl)+'" selected>当前源</option><option disabled>多源候选 · R4</option>';
$('bk-toc-n').textContent=BK.toc.length;
// 卷分组（标题「第N卷/部/集」切组；无卷名→单组）；500+ 章按需渲染：仅首卷
// 行集内联，其余卷 data-pending 首次展开才渲染（见 toggle 捕获监听）
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
// 范围 chips（单选；点击即批量设勾选态——非提交；custom 起止由「设选」应用）
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
// 过滤只是视图：隐藏不匹配行并展开各组（勾选态在 BK.sel，恒不受影响）
function bkFilter(){
if(!BK.filter)return;
$('bk-toc').querySelectorAll('.toc-vol').forEach(function(d){
var pi=d.querySelector('.toc-rows[data-pending]');if(pi)bkRenderRows(pi);
d.setAttribute('open','');
d.querySelectorAll('.toc-row').forEach(function(r){
r.style.display=r.querySelector('.t').textContent.indexOf(BK.filter)>=0?'':'none';});});
}
// T2：入库提交（上限前门=后端同款；confirm 闸由端点承载）
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
// 步级行=.syn-step 三态（竹绿 done/朱砂脉冲 running/灰未到）+cache✓ 青黛徽
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
// T4：原始章节 bkOrigToggle 已迁 @view:material（消费链与 DOM 均在素材抽屉；
// 台账 A-2/A 类专项 2026-11-20）
// ── R4 导入弹窗 import-modal（书源实弹修复与导入弹窗设计 §三 2026-11-18）──
// 状态机 idle→parsing→previewing→importing（设计 §3.3）。解析=POST
// /api/booksource/import（零落库）；落库=import_apply（selections 按 URL +
// importKeep 三开关，后端 deduped_from/stored 透明计数）。
// D1 虚拟滚动：行高 30px 视窗 400px，滚动按 scrollTop 算窗口 ±5 行、行
// absolute 定位——3906 行只渲可见 ~20 行。D2 关键词(名称/分组/URL contains)
// +状态 chips+分组 chips(top-10 数据驱动)。D3「全选（当前筛选 N 条）」。
// D4 状态徽：新=success 竹绿/更新=warning 琥珀/已最新=queued 灰/已跳过=灰显
// 行+title 悬浮原因。D5 同 URL 合并一行「名称（N 版本取最新）」+▸ 展开看版本。
// D7 底部实时汇总。D8 kind≠bookSource→黄条类型化说明+表单灰显只读。
/*__IM_BEGIN__*/
var IM={st:'idle',tab:'paste',text:'',groups:[],skipped:[],counts:null,sel:{},exp:{},kw:'',stat:'all',grp:'',visible:[],kind:'bookSource',msg:'',allLatestOpen:false};
var IM_ROW=30,IM_VIEW=400,IM_BUF=5,IM_GTOP=10;
function imGroups(items,skipped){ // D5：同 URL 一组（首见序），组态取末条（apply 落库=最后一条）
var by={},order=[],rows=[],i,u,g;
for(i=0;i<items.length;i++){u=items[i].source.bookSourceUrl;
if(!by[u]){by[u]={url:u,items:[]};order.push(u);}
by[u].items.push(items[i]);}
for(i=0;i<order.length;i++){g=by[order[i]];
var anySel=false,j;for(j=0;j<g.items.length;j++){if(g.items[j].selected){anySel=true;break;}}
rows.push({t:'g',url:g.url,items:g.items,last:g.items[g.items.length-1],sel:anySel,n:g.items.length});}
for(i=0;i<skipped.length;i++)rows.push({t:'s',idx:skipped[i].index,reason:skipped[i].reason||'条目缺少 bookSourceUrl'});
return rows;}
function imLinear(){ // ▸ 展开的版本子行插在组行后（选中态按 URL 记在组上）
var out=[],i,k,g;
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];out.push(g);
if(g.t==='g'&&IM.exp[g.url])for(k=0;k<g.items.length;k++)out.push({t:'v',v:g.items[k],url:g.url});}
return out;}
function imGtok(s){ // legado 分组常为逗号多值（玄幻,出版）→ 归一为空格分隔 token 串
return (s||'').replace(/[,，;；]+/g,' ').trim();}
function imMatch(r){ // D2 三轴过滤；版本子行随父组；跳过行只认 已跳过/全部 chip 与关键词（对原因）
var kw=IM.kw.toLowerCase(),s,g;
if(r.t==='v')return true;
if(r.t==='s'){if(IM.stat!=='all'&&IM.stat!=='skipped')return false;
if(IM.grp)return false;
return !kw||String(r.reason).toLowerCase().indexOf(kw)>=0;}
s=r.last.status;g=r.last.source.bookSourceGroup||'';
var hay=((r.last.source.bookSourceName||'')+'\n'+g+'\n'+r.url).toLowerCase();
if(IM.stat!=='all'&&IM.stat!==s)return false;
if(IM.grp){ // 分组 chip=token 级匹配（多值组任一 token 命中即中；（未分组）=空 token）
if(IM.grp==='（未分组）'){if(imGtok(g))return false;}
else if((' '+imGtok(g)+' ').indexOf(' '+IM.grp+' ')<0)return false;}
return !kw||hay.indexOf(kw)>=0;}
function imFilter(rows){var out=[],i;for(i=0;i<rows.length;i++)if(imMatch(rows[i]))out.push(rows[i]);return out;}
function imWindow(st,n){ // D1：scrollTop→[start,end) 行窗（±5 缓冲）
var a=Math.max(0,Math.floor(st/IM_ROW)-IM_BUF),b=Math.min(n,Math.ceil((st+IM_VIEW)/IM_ROW)+IM_BUF);
return [a,Math.max(a,b)];}
function imBadge(s){ // D4 状态徽：复用 badge 三色（success=竹绿新/warning=琥珀更新/queued=灰）
return '<span class="badge '+(s==='isNew'?'success':s==='isUpdate'?'warning':'queued')+'">'+(s==='isNew'?'新':s==='isUpdate'?'更新':'已最新')+'</span>';}
function imChips(){ // D2 状态 chips（合并行口径）+ D6 分组 chips（token 聚合 top-10）
var c={isNew:0,isUpdate:0,latest:0},i,j,g,gs={},go=[],h,k,p,
SB=[['all','全部'],['isNew','新'],['isUpdate','更新'],['latest','已最新']];
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t!=='g')continue; // 跳过行无 last/selected，不进状态与分组统计（走 IM.skipped 计数）
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
function imPaintWin(){ // D1：canvas 撑总高，行 absolute top；只写 [start,end) 窗
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
function imRepaint(){ // 过滤/勾选/展开变化后的重画（窗口+chips+全选标签+汇总同步）
IM.visible=imFilter(imLinear());
var n=0,sel=0,i,r;
for(i=0;i<IM.visible.length;i++){r=IM.visible[i];if(r.t==='g'){n++;if(IM.sel[r.url])sel++;}}
$('im-selall-t').textContent='全选（当前筛选 '+n+' 条）'; // D3 语义明示
$('im-shown').textContent='显示 '+IM.visible.length+' 行';
$('im-selall').checked=n>0&&sel===n;
imChips();imPaintWin();imSummary();imCollapseSync();}
// UIX #7：全部「已最新」（无新/无更新/无跳过）且未加任何筛选时，把 22 行平铺
// 折叠为一行摘要——信息密度过高时默认收起，点「展开清单」才看全表。独立节点实现，
// 不改 chips/全选/汇总既有口径（回归断言不受影响）。
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
function imSelAll(v){ // D3：全选/清空=当前筛选结果（IM.visible 组行；跳过行不可选）
var i,r;for(i=0;i<IM.visible.length;i++){r=IM.visible[i];if(r.t==='g')IM.sel[r.url]=v;}
imRepaint();}
function imSummary(){ // D7：已选按 URL 计；新增/更新按组终态
var n=imSelN(),x=0,y=0,i,g;
for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t==='g'&&IM.sel[g.url]){if(g.last.status==='isNew')x++;else if(g.last.status==='isUpdate')y++;}}
$('im-sum').textContent='已选 '+n+' 条 · 新增 '+x+' · 更新 '+y;
$('im-apply').disabled=!n;}
function imIngest(d){ // 预览响应→IM 状态；非书源包返回 false（D8 由调用方呈现）
if(!d||d.kind!=='bookSource'){IM.kind=(d&&d.kind)||'unknown';IM.msg=(d&&d.message)||'';
IM.groups=[];IM.skipped=[];IM.counts=null;IM.sel={};IM.exp={};return false;}
IM.kind='bookSource';IM.msg='';
IM.groups=imGroups(d.items||[],d.skipped||[]);
IM.skipped=d.skipped||[];IM.counts=d.counts||{};IM.sel={};IM.exp={};
var i,g;for(i=0;i<IM.groups.length;i++){g=IM.groups[i];
if(g.t==='g'&&g.sel)IM.sel[g.url]=true;}
return true;}
function imRo(on){ // D8：表单灰显只读（可读边界说明，不是冷漠拒绝）
$('im-src').classList.toggle('is-ro',on);
$('im-filters').hidden=on;}
function imNonSource(){ // D8 呈现（解析与 apply 两处共用）
$('im-warn').textContent=IM.msg;$('im-warn').hidden=false;
$('im-total').textContent='非书源包 · '+(IM.kind==='unknown'?'无法识别':IM.kind);
imRo(true);}
function imTab(t){IM.tab=t;
QA('#im-tabs .tab').forEach(function(x){x.classList.toggle('is-active',x.getAttribute('data-imtab')===t);});
$('im-paste').hidden=t!=='paste';$('im-url').hidden=t!=='url';$('im-file').hidden=t!=='file';}
function imKeep(){return {keep_name:$('im-keep-name').checked,keep_group:$('im-keep-group').checked,keep_enable:$('im-keep-enable').checked};}
function imBtns(on){QA('#im-parse,#im-parse-url,#im-parse-file').forEach(function(b){b.disabled=on;});}
function imOpen(){ // 回 idle：清三入口与预览（状态机 §3.3 首态）
IM.st='idle';IM.text='';IM.allLatestOpen=false;imIngest(null);
IM.kw='';IM.stat='all';IM.grp='';IM.visible=[];
$('im-text').value='';$('im-url-in').value='';$('im-file-in').value='';$('im-kw').value='';
$('im-warn').hidden=true;$('im-prog').hidden=true;imRo(false);
$('im-total').textContent='';
$('im-canvas').innerHTML='';$('im-canvas').style.height='0px';
imTab('paste');imSummary();
openDlg('im','#im-tabs .tab');}
function imClose(){closeDlg('im');IM.st='idle';}
async function imParse(text){ // parsing→previewing；importKeep 开关变更亦经此重解析（预览即落库形态）
if(!text||!text.trim())return toast('内容为空：先粘贴 JSON / 填 URL / 选文件',WN);
IM.st='parsing';IM.text=text;
$('im-prog').hidden=false;imBtns(true);
// 真实耗时可见（2026-11-23 判例：大包 7-20MB 需 10-25s，旧提示写「1-2s」致用户
// 误判卡死而关窗口——虚假快提示比无提示更危险）
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
async function imApply(btn){ // importing→done；apply 后关窗+书源视图面刷新（引擎灯重探）
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
/*__IM_END__*/
// 路由入口（原 renderRoute books 分支整体迁入——A 类专项：分支归视图）
// R4 #books 三子态（hash 驱动）：默认搜索（占位）/ 详情 ?book=&source= / 订阅 ?tab=subs
VAPI.books={enter:function(route){
if(route.params.get('tab')==='subs')bkMode('subs');
else if(route.params.get('book')&&route.params.get('source'))bkOpen(route.params.get('book'),route.params.get('source'),route.params.get('name')||'');
else bkMode('search',route.params.get('search'));
}};
function bindBooks(){
// ── R4 #books + R3.5 提炼触点绑定 ──
bkDigestInit();
$('bk-subs-btn').addEventListener('click',function(){location.hash='#books?tab=subs';});
$('bk-subs-back').addEventListener('click',function(){location.hash='#books';});
// FEAT-5 空态引导：搜索态占位引导卡的可行动按钮（现在就能做的事，而非等待 R4 批）
$('bk-guide-subs').addEventListener('click',function(){location.hash='#books?tab=subs';});
$('bk-guide-import').addEventListener('click',imOpen);
bkResBind();
// 体验模式一键开（2026-11-23）：引擎未建设期也能跑通「搜索→选书→选章→入素材库」
// ——后端写用户配置文件后提示重启（launchd 进程不继承 shell env）
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

// 书籍搜索（2026-11-23 接线批）：后端 job book_search 早已就绪，前端此前只是
// 「R4 批开放」占位 toast——体验模式一开就等于把可用功能藏起来。本接线：
// POST /api/book/search → job → 轮询 → 渲染结果卡（点结果进详情态）
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
// details toggle 不冒泡——捕获监听承接（500+ 章按需渲染的展开时点）
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
// 取消=POST /api/jobs/cancel（2026-11-18 集成轮落地，正典 §2.2）：协作式取消
// 于检查点生效（取文逐章/提炼逐 pass）——已完成章保留入库，未完成章放弃；
// 终态回报 job done+result.cancelled，M/N 由 bkPoll 终态 toast 明列。
$('bk-prog').addEventListener('click',function(e){
var b=e.target.closest('[data-bkcancel]');if(!b)return;
b.disabled=true;
postJSON('/api/jobs/cancel',{job_id:BK.job.id,confirm:true}).then(function(d){
if(d.cancelled){toast('取消请求已发送——正在于检查点收束（已完成章保留）',WN);}
else{toast('任务已终态，无需取消',WN);b.disabled=false;}
}).catch(function(err){toast('取消失败：'+err.message,ER);b.disabled=false;});
});
// ── R4 导入弹窗绑定（#books 订阅态入口 + 三入口 tabs + 过滤/勾选/滚动委托）──
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
$('im-list').addEventListener('scroll',imPaintWin); // D1：滚动只重画窗口
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
// @endview
// @view:compose
// Phase C F71 封面工坊（视觉设计，/api/visual 族真预览；/api/action/cover 保留为 CLI 对齐面）
// 评审补批 P1-3：面板自 #article·actions 搬入 #compose 右栏折叠区（cm-visual）；
// 取材自 CV.art（compose=会话正稿 d.final_article），空值则纯参数渲染（article:null）
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
closeDlg('cv-pm');toast('模板已保存：'+id,OK);
CV.pr=(await getJSON('/api/visual/presets')).items||[];cvChips();
}catch(err){toast('模板保存失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
var CM_ACT={keep:'保留',adopt_edited:'改后采纳',discard:'已丢弃'};
function blockHtml(b){return mdToHtml(b.text,'');}
// loadCompose/loadComposeDetail 真身已迁 core 段共享块（消费点含 core 路由+insight
// 数据装载；台账 A-5——A 类专项 2026-11-20）
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
// ── N-2「我的素材」直引（2026-11-21，设计 §3.3 引用自己）────────────────
// POST /api/compose/add 带 material_id（三选一扩展面）——prompt 侧取素材
// payload.text，池内虚拟定位符 material://<id>；移除走 removeCmSource 同一
// 后端（material:// 原样匹配，store.py N-2 分支）。与文件路径形态互不影响。
async function cmAddMaterial(id){
if(!state.cslug){toast('先建会话再入素材',ER);return;}
try{var d=await postJSON('/api/compose/add',{slug:state.cslug,material_id:id,confirm:true});
state.cmPaths[d.path]=d.path;
toast('素材已入池：'+d.path,OK);
await loadComposeDetail(state.cslug,true);renderCompose();}
catch(err){toast('加入素材失败：'+err.message,ER);}
}
// F-C（2026-11-18）：素材撤池——confirm 连坐文案=素材勾选章节/全文+已生成轮次
// 保留（块归因 source_ref 在生产代码无赋值处，原「草稿中 N 块」计数恒 0 为
// 死代码，2026-11-18 集成修复判例）；POST remove_source（响应 sections→成功
// toast）→ 池行恢复「加入」+ 右栏 cm-sources 重拉。
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
// ── N-2 素材池双 tab（2026-11-21）────────────────────────────────────────
// 「发现候选」（原有 ≥80 高分池）+「✦ 我的素材」（站内直引素材库——复用
// GET /api/materials 数据面，只列带 payload.text 可进 prompt 的行；加入走
// cmAddMaterial(material_id)，移除走 removeCmSource 的 material://<id> 定位符（同
// 一后端）。tab 态/检索词为视图局部变量（CMTAB/cmMtQ）；我的素材清单
// cmMine 首次切入异步拉取（limit=200 一次，检索纯客户端过滤不回打 API）。
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
// P3 块结构操作（契约 §2.3-6）：结构面参数不进 compose 端点——插入/拆分/合并
// 操作 UI 工作副本 state.cmWork（轮内暂存，切轮/再生成即弃）；凡产生正式块
// 文本变更的（拆分上半/合并全文）同步走既有 decide(adopt_edited) 持久化，
// 被并掉的正式块走 decide(discard)。本地块无服务器写面，显式徽标+提示。
// 缓存键=slug+轮号（2026-11-20 复核 HIGH 修复）：只比轮号时，跨会话切换
// （两会话末轮号相同）不换工作副本——块区串台画上一会话的缓存块，且其上的
// 裁决以当前 slug 落库=打错会话。cmWorkBlocks 是 cmWork 唯一建/验点，
// renderCmBlocks 唯一调用方，键上补 slug 即全路径闭环。
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
// E-5 小说 §3.6：kind 条件渲染——两折叠区（前情提要/人物卡）novel 专属，
// article 整体销毁不残留（清空销毁非 hidden）；**类型切换 chips 例外常驻**
// （2026-11-20 复核 MEDIUM 修复：正典 N-1 线框「会话类型切换（右栏顶部）」
// 未条件化——chips 只画在 novel 面板里会让 article 会话无切换入口，
// UI 单向门：切出去回不来。chips 零壳字节，常驻不加壳锚压力）。
// FEAT-4（2026-11-22）：人物关系力导向图 + 伏笔账本面板落独立产物
// assets/novel.js（compose.js 贴 40KiB 锚，同 lineage.js 判例二次拆分）；
// 本块只留轻量关系面——挂卡行关系展示 + 建卡弹窗关系输入。
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
+'<p class="action-hint">已摘要 '+(d.novel_summary_chapters||0)+' 章 / 共 '+((d.novel||{}).chapters||0)+' 章'+CS;
c+=CD+'👥 人物卡</summary>';
cs.forEach(function(x){
var rel=(x.relations||[]).map(function(r){return esc(r.to)+(r.relation?'='+esc(r.relation):'');}).join('、');
c+='<p class="action-hint">'+esc(x.name)+(x.role?' · '+esc(x.role):'')+(x.speech_style?' · '+esc(x.speech_style):'')+(rel?' · <span class="cn-rel">'+rel+'</span>':'')+cnL('×','rm',x.id)+'</p>';});
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
if(!d){CN.e=0;CN.c=[];h.innerHTML='';return cnConf();}  // 未选会话：整区空（含 chips）
// article：只留类型切换 chips（novel 两折叠区销毁不残留）——切换入口常驻
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
if($('cm-ch-modal')){openDlg('cm-ch-modal','#cm-chf-name');return;}
[['name','姓名'],['role','角色'],['appearance','外貌'],['speech_style','说话风格']].forEach(function(x){
w+='<input class="input" id="cm-chf-'+x[0]+'" placeholder="'+x[1]+'">';});
$('cm-novel').insertAdjacentHTML('beforeend','<div class="modal-overlay" id="cm-ch-modal"><div class="modal" role="dialog" aria-modal="true" aria-label="新建人物"><h3 class="modal-title">新建人物</h3><div class="cv-col">'+w+'<textarea class="input set-area" id="cm-chf-key_events" rows="3" placeholder="关键事件（每行一条）"></textarea><textarea class="input set-area" id="cm-chf-relations" rows="3" placeholder="人物关系（每行： 人物名=关系，如 陈默=宿敌）——关系图见下方🕸折叠区"></textarea></div><div class="modal-actions"><button class="btn" data-cn="mclose">取消</button><button class="btn btn-primary" data-cn="create">建卡</button></div></div></div>');
openDlg('cm-ch-modal','#cm-chf-name');
}
// FEAT-4 关系输入解析：每行「人物名=关系」（无 = 则只记对方名，关系留空）
function cnParseRelations(text){
return String(text||'').split('\n').map(function(s){return s.trim();}).filter(Boolean).map(function(s){
var i=s.indexOf('=');
return i<0?{to:s,relation:''}:{to:s.slice(0,i).trim(),relation:s.slice(i+1).trim()};});
}
async function cnCreate(el){
var fv=function(k){var e=$('cm-chf-'+k);return e?String(e.value||'').trim():'';},name=fv('name'),d;
if(!name)return toast('姓名不能为空',ER);
try{d=await cnPost({action:'character_create',name:name,role:fv('role'),appearance:fv('appearance'),speech_style:fv('speech_style'),key_events:fv('key_events').split('\n').map(function(s){return s.trim();}).filter(Boolean),relations:cnParseRelations(fv('relations'))});
if((d.material||{}).id)await cnPost({action:'character_add',id:d.material.id});
closeDlg('cm-ch-modal');toast('已建卡:'+name,OK);
await loadComposeDetail(state.cslug,true);renderCompose();
}catch(err){toast('建卡失败：'+err.message,ER);}
}
function renderCompose(){
var L=state.cs||[],d=state.csd,R;
var act=0;L.forEach(function(s){if(s.status==='composing')act++;});
updateNavBadge('nav-badge-compose',act); // §2.1 创作位徽标=进行中会话数（清单载荷 status，服务端已下发）
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
if(d.final_article&&CV.art!==d.final_article)cvInit(d.final_article); // P1-3：右栏视觉折叠区取材=会话正稿
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
// 路由入口（原 renderRoute compose 分支整体迁入——A 类专项：分支归视图）
VAPI.compose={enter:async function(route){
var cs=route.params.get('s')||route.params.get('slug')||state.cslug||'';
var cr=route.params.get('r');
await Promise.all([loadCompose(true),loadDiscover()]);
await loadComposeDetail(cs,true);
if(cr&&+cr>0)state.cround=+cr;
renderCompose();
}};
// ── FEAT-2 智能组稿：三步向导（主题 → 语义搜素材 → 大纲与会话）──
// 三步均可回退（scBack：2→1、3→2，确认后 back 隐藏防重复建会话）；步骤 2 支持全选/
// 取消全选/相似度排序/手动追加搜索；步骤 3 确认即建会话+素材入池+AI 三段式大纲，
// 大纲可就地编辑（改标题/删段/调序），进入工坊时整表覆写保存。
var SC={step:1,topic:'',items:[],sel:{},sorted:true,outline:null,outlineSource:null,slug:'',done:false};
function scSelectedIds(){return SC.items.filter(function(m){return SC.sel[m.id];}).map(function(m){return m.id;});}
function scSec(id){var S=(SC.outline||{}).sections||[],i;for(i=0;i<S.length;i++)if(S[i].id===id)return S[i];return null;}
function scTitleOf(mid){var m=null,i,L=SC.items;for(i=0;i<L.length;i++)if(L[i].id===mid){m=L[i];break;}return m?String(m.title||mid).slice(0,24):mid;}
function scMerge(a,b){var seen={},out=[];a.forEach(function(m){seen[m.id]=1;out.push(m);});b.forEach(function(m){if(!seen[m.id]){seen[m.id]=1;out.push(m);}});return out;}
function scReset(){SC.step=1;SC.topic='';SC.items=[];SC.sel={};SC.sorted=true;SC.outline=null;SC.outlineSource=null;SC.slug='';SC.done=false;$('sc-topic').value='';$('sc-q').value='';scPaint();}
function scOpen(){scReset();openDlg('sc-modal','#sc-topic');}
function scClose(){closeDlg('sc-modal');}
function scGoto(n){SC.step=n;scPaint();}
function scBack(){if(SC.step>1&&!SC.done)scGoto(SC.step-1);} // 三步向导可回退（确认后停用）
function scRow(m){
var sim=(typeof m.similarity==='number'&&m.similarity>0)?Math.round(m.similarity*100)+'%':'';
return '<label class="sc-row"><input type="checkbox" data-sc-mid="'+esc(m.id)+'"'+(SC.sel[m.id]?' checked':'')+'>'+
'<span class="sc-row-main"><span class="sc-row-t" title="'+esc(m.title||m.id)+'">'+esc(m.title||m.id)+'</span>'+
'<span class="sc-row-p">'+esc(m.preview||m.uri||'')+'</span>'+
'<span class="sc-row-meta"><span class="src-badge">'+esc(m.source_id||'—')+'</span>'+(sim?'<span class="sim-badge" title="语义相关度">'+sim+'</span>':'')+'<span>'+esc((m.matched_lanes||[]).join('+'))+'</span></span></span></label>';
}
function scOutlineHtml(){
var S=(SC.outline&&SC.outline.sections)||[];
if(!S.length)return '<p class="action-hint">（未生成大纲——进入工坊后可用「AI 生成大纲」）</p>';
return S.map(function(s,i){
return '<div class="sc-outline-row">'+
'<input class="input" data-sc-title="'+esc(s.id)+'" value="'+esc(s.title)+'" placeholder="段落标题">'+
'<textarea class="input" rows="2" data-sc-pts="'+esc(s.id)+'" placeholder="要点（每行一条，来自素材内容）">'+esc((s.points||[]).join('\n'))+'</textarea>'+
'<div class="sc-outline-mats">'+(s.material_ids||[]).map(function(mid){return '<span class="tag" title="引用素材 '+esc(mid)+'">'+esc(scTitleOf(mid))+'</span>';}).join('')+'</div>'+
'<div class="sc-outline-ops"><button class="btn" data-sc-up="'+esc(s.id)+'"'+(i===0?' disabled':'')+'>↑</button><button class="btn" data-sc-dn="'+esc(s.id)+'"'+(i===S.length-1?' disabled':'')+'>↓</button><button class="btn mat-del" data-sc-del="'+esc(s.id)+'">✕ 删除段落</button></div></div>';
}).join('');
}
function scPaint(){
var steps=$('sc-steps').children,i;
for(i=0;i<steps.length;i++)steps[i].className='sc-step'+(i+1===SC.step?' is-active':(i+1<SC.step?' is-done':''));
$('sc-pane1').hidden=SC.step!==1;
$('sc-pane2').hidden=SC.step!==2;
$('sc-pane3').hidden=SC.step!==3;
$('sc-back').hidden=SC.step===1||SC.done;
$('sc-next').textContent=SC.step===1?'下一步：搜索素材':(SC.done?'进入工坊':'确认并生成');
if(SC.step===2){
var L=SC.items.slice();
if(SC.sorted)L.sort(function(a,b){return (b.similarity||0)-(a.similarity||0);});
$('sc-list').innerHTML=L.length?L.map(scRow).join(''):'<p class="action-hint" style="padding:10px;margin:0">没有命中素材——换个说法，或先在素材库采集。</p>';
$('sc-count').textContent='已选 '+scSelectedIds().length+'/'+SC.items.length;
$('sc-sort').classList.toggle('is-active',SC.sorted);
}
if(SC.step===3&&!SC.done){
$('sc-hint3').textContent='将创建会话「'+SC.topic+'」，'+scSelectedIds().length+' 篇素材全部入池，AI 基于素材内容生成三段式大纲。';
$('sc-outline').innerHTML='';
}else if(SC.step===3&&SC.done){
$('sc-hint3').textContent=(SC.outlineSource==='ai'?'AI 大纲已生成（基于选中素材内容）':'AI 暂不可用，已按素材要点生成兜底大纲')+'——可改标题/删段/调序，进入工坊后逐节裁决再展开。';
$('sc-outline').innerHTML=scOutlineHtml();
}
}
async function scProbe(q){
try{var d=await postJSON('/api/compose/smart-start',{topic:q,limit:10,confirm:true});
SC.items=scMerge(SC.items,d.suggestions||[]);
if(!SC.items.length)toast('没有命中素材——换个说法试试',WN);
scPaint();}
catch(err){toast('搜索失败：'+err.message,ER);}
}
async function scNext(btn){
if(SC.step===1){
SC.topic=$('sc-topic').value.trim();
if(!SC.topic){toast('先输入主题描述',ER);return;}
btn.disabled=true;
try{await scProbe(SC.topic);}finally{btn.disabled=false;}
if(SC.items.length)scGoto(2);
return;
}
if(SC.step===2){
var ids=scSelectedIds();
if(!ids.length){toast('至少勾选一篇素材',ER);return;}
btn.disabled=true;
try{var d=await postJSON('/api/compose/smart-start',{topic:SC.topic,material_ids:ids,generate_outline:true,confirm:true});
SC.slug=d.slug;SC.outline=d.outline;SC.outlineSource=d.outline_source;SC.done=true;
toast('会话已建：'+d.slug+'（素材 '+d.attached+' 篇入池）',OK);scGoto(3);}
catch(err){toast('创建失败：'+err.message,ER);}
finally{btn.disabled=false;}
return;
}
if(!SC.done)return;
btn.disabled=true;
try{
var secs=(SC.outline||{}).sections||[];
if(secs.length)await postJSON('/api/compose/outline',{slug:SC.slug,action:'replace',sections:secs,confirm:true});
scClose();
location.hash='#compose?s='+encodeURIComponent(SC.slug);
}catch(err){toast('大纲保存失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
function bindCompose(){
$('cv-tpl').addEventListener('click',function(e){var b=e.target.closest('[data-cvt]');if(!b||b.getAttribute('data-cvt')===CV.tpl)return;CV.tpl=b.getAttribute('data-cvt');cvChips();cvEdit();});
$('cv-title').addEventListener('input',cvEdit);
$('cv-hook').addEventListener('input',cvEdit);
$('cv-fs').addEventListener('change',cvEdit);
$('cv-al').addEventListener('change',cvEdit);
$('cv-x').addEventListener('click',function(){cvExport(this);});
$('cv-pt').addEventListener('click',function(){$('cv-pid').value='';$('cv-pl').value='';openDlg('cv-pm','#cv-pid');});
$('cv-pm-x').addEventListener('click',function(){closeDlg('cv-pm');});
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
// N-2 双 tab + 我的素材直引（2026-11-21）：tab 切换/搜索为视图局部态；
// material:// 虚拟定位符加入走 cmAddMaterial、移除复用 removeCmSource。
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
if(e.target.id==='cm-mt-q'){cmMtQ=e.target.value.trim();cmMinePaint();} // 只重绘行区，输入框不失焦
});
$('cm-sources').addEventListener('change',function(e){
var t=e.target,path=t.getAttribute&&t.getAttribute('data-cm-sec');
if(!path||t.type!=='checkbox')return;
var boxes=[].slice.call($('cm-sources').querySelectorAll('input[type=checkbox]')).filter(function(x){return x.checked&&x.getAttribute('data-cm-sec')===path;});
addCmSource(path,boxes.map(function(x){return x.value;}));
});
$('cm-blocks').addEventListener('click',cmBlocksClick);
// #ctx 右键菜单同源委托（A 类专项 2026-11-20）：块菜单项=data-cm-*/data-cmop，
// 与素材域 data-m* 属性两族互斥——由本块自挂（原 core 直呼占位 no-op）
$('ctx').addEventListener('click',cmBlocksClick);
$('cm-rounds').addEventListener('click',function(e){
var c=e.target.closest('[data-cm-round]');
if(c){state.cround=+c.getAttribute('data-cm-round');state.cmWork=null;renderCmRounds();renderCmBlocks();}
});
$('cm-generate-btn').addEventListener('click',function(){state.cmWork=null;cmGenerate(this);});
$('cm-adopt-btn').addEventListener('click',cmAdopt);
// UIX-3 键盘可达：⌘⏎/Ctrl+⏎ =「按反馈再生成」（article 域同款主操作键——工坊
// 核心动线键盘直达；向导开着时让位：sc-modal 内 Enter=下一步既有绑定优先，本
// 处检查 sc-modal 已关。article 块的 ⌘⏎ 处理器在本视图 hidden 不消费，零冲突）
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&!$('view-compose').hidden&&$('sc-modal').hidden&&(!$('cm-ch-modal')||$('cm-ch-modal').hidden)){e.preventDefault();state.cmWork=null;cmGenerate($('cm-generate-btn'));}
});
// E-5 小说区（#cm-novel 内 DOM 由 kind 条件创建/销毁，委托挂在外层稳定容器上）
$('cm-novel').addEventListener('click',function(e){
var b=e.target.closest('[data-cn]'),cmd=b&&b.getAttribute('data-cn');
if(!cmd)return;
if(cmd==='mclose'){closeDlg('cm-ch-modal');return;}
if(cmd==='create'){cnCreate(b);return;}
cnAct(cmd,b.getAttribute('data-id'),b);
});
// FEAT-2 智能组稿向导：入口/三步导航/素材勾选与排序/追加搜索/大纲编辑
$('cm-smart-btn').addEventListener('click',scOpen);
$('sc-cancel').addEventListener('click',scClose);
$('sc-back').addEventListener('click',scBack);
$('sc-next').addEventListener('click',function(){scNext(this);});
$('sc-topic').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'){e.preventDefault();$('sc-next').click();}
});
$('sc-search-btn').addEventListener('click',function(){var q=$('sc-q').value.trim();if(q)scProbe(q);});
$('sc-q').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'){e.preventDefault();$('sc-search-btn').click();}
});
$('sc-all').addEventListener('click',function(){SC.items.forEach(function(m){SC.sel[m.id]=1;});scPaint();});
$('sc-none').addEventListener('click',function(){SC.sel={};scPaint();});
$('sc-sort').addEventListener('click',function(){SC.sorted=!SC.sorted;scPaint();});
$('sc-list').addEventListener('change',function(e){
var t=e.target,mid=t.getAttribute&&t.getAttribute('data-sc-mid');
if(!mid||t.type!=='checkbox')return;
if(t.checked)SC.sel[mid]=1;else delete SC.sel[mid];
$('sc-count').textContent='已选 '+scSelectedIds().length+'/'+SC.items.length;
});
$('sc-outline').addEventListener('input',function(e){
var t=e.target,id=t.getAttribute&&(t.getAttribute('data-sc-title')||t.getAttribute('data-sc-pts')),s;
if(!id)return;
s=scSec(id);
if(!s)return;
if(t.hasAttribute('data-sc-title'))s.title=t.value;
else{s.points=t.value.split('\n').map(function(x){return x.trim();}).filter(Boolean);s.direction=s.points.join('；');}
});
$('sc-outline').addEventListener('click',function(e){
var b=e.target.closest('[data-sc-up],[data-sc-dn],[data-sc-del]'),secs=(SC.outline||{}).sections||[],i,j,tmp;
if(!b||!secs.length)return;
if(b.hasAttribute('data-sc-del')){
SC.outline.sections=secs.filter(function(s){return s.id!==b.getAttribute('data-sc-del');});
scPaint();return;
}
for(i=0;i<secs.length;i++)if(secs[i].id===b.getAttribute('data-sc-up')||secs[i].id===b.getAttribute('data-sc-dn'))break;
if(i>=secs.length)return;
j=b.hasAttribute('data-sc-dn')?i+1:i-1;
if(j<0||j>=secs.length)return;
tmp=secs[i];secs[i]=secs[j];secs[j]=tmp;
scPaint();
});
}
bindCompose();
// @endview
// @view:design
// Phase C F72 社媒图编辑器（#design 视图；正典 C 档 §二 F72 十步交互流）：
// SVG 图元=DOM 节点，点选/拖动全走原生指针事件（零命中检测代码，零依赖红线）；
// 编辑先稿后导出；A 路导出 dataURI 天然离线；AI 不参与。撤销栈=内存快照
// N=50（⌘Z/⇧⌘Z，刷新即弃——存稿才是持久化边界）。契约实测（visual_actions.py
// + visual_payload.py + visual_service.py）：render{template,params,spec,article}
// →{ok,svg,params}；design GET ?article=→404 无稿；save{article,kind,template,
// params,spec,svg}（svg 显式给→白名单复验落盘，手工位移/层级随 svg 进稿）；
// export→job（设计稿缺席同步 200+{ok:false}）。xhs-card schema 仅 title/hook/
// tag/font_size/align/background_slot 六槽（templates.py:197-206）——无几何/行距
// 槽，行距控件按实测契约不设（发即 400 未知槽）。
var DV={art:'',kind:'social',tpl:'xhs-card',spec:'xhs-3x4',params:{},svg:'',w:0,h:0,k:1,st:'idle',dirty:false,sel:-1,dpos:{},order:null,undo:[],redo:[],q:0,h:0,drag:null,pendSpec:'',slots:[],slotHexes:{},recent:[],loadedOnce:false};
var DSPECS=['xhs-3x4','xhs-4x5','square'],DSIZE={'xhs-3x4':[1242,1656],'xhs-4x5':[1242,1553],'square':[1080,1080]};
var DTXT={'xhs-card':{1:'tag',2:'title',3:'hook'}};
function dvSet(st){DV.st=st;dvSync();}
function dvSnap(){return{p:Object.assign({},DV.params),s:DV.svg,dp:JSON.stringify(DV.dpos),o:DV.order?DV.order.slice():null,spec:DV.spec};}
function dvCommit(pre){DV.undo.push(pre);if(DV.undo.length>50)DV.undo.shift();DV.redo=[];DV.dirty=true;dvSet('ready');}
function dvRestore(s){DV.params=Object.assign({},s.p);DV.svg=s.s;DV.dpos=JSON.parse(s.dp);DV.order=s.o?s.o.slice():null;DV.spec=s.spec;DV.sel=-1;dvMount();dvSync();}
function dvTravel(back){
if(DV.st!=='ready')return;
var src=back?DV.undo:DV.redo;
if(!src.length)return;
DV.q++;
(back?DV.redo:DV.undo).push(dvSnap());
dvRestore(src.pop());
DV.dirty=true;dvSet('ready');
}
function dvMount(){
var stage=$('dv-stage');
stage.innerHTML=DV.svg;
var svgEl=stage.querySelector('svg');
if(!svgEl){DV.svg='';dvSet('idle');return;}
DV.w=+svgEl.getAttribute('width')||DV.w;DV.h=+svgEl.getAttribute('height')||DV.h;
var kids=[],nodes=svgEl.children,i,n;
for(i=0;i<nodes.length;i++){n=nodes[i];
if(i===0&&n.tagName==='rect'&&+n.getAttribute('width')===DV.w)continue;
kids.push(n);}
if(!DV.order||DV.order.length!==kids.length)DV.order=kids.map(function(x,j){return j;});
else DV.order.forEach(function(oi){svgEl.appendChild(kids[oi]);});
kids.forEach(function(n2,j){
n2.setAttribute('data-o',j);
var t=DTXT[DV.tpl]&&DTXT[DV.tpl][j];
if(t)n2.setAttribute('data-t',t);
var p=DV.dpos[j];
if(p)n2.setAttribute('transform','translate('+p[0]+','+p[1]+')');
});
}
function dvHandles(){
var svgEl=$('dv-stage').querySelector('svg');
if(!svgEl)return;
var old=svgEl.querySelector('.dv-hd');
if(old)old.remove();
if(DV.sel<0)return;
var n=$('dv-stage').querySelector('[data-o="'+DV.sel+'"]');
if(!n)return;
var b=n.getBBox(),hh=9/(DV.k||1),h2=hh/2,cs=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]],g='<g class="dv-hd"><rect x="'+(b.x-4)+'" y="'+(b.y-4)+'" width="'+(b.width+8)+'" height="'+(b.height+8)+'" vector-effect="non-scaling-stroke"/>';
cs.forEach(function(c){g+='<rect class="h" x="'+(c[0]-h2)+'" y="'+(c[1]-h2)+'" width="'+hh+'" height="'+hh+'" vector-effect="non-scaling-stroke"/>';});
svgEl.insertAdjacentHTML('beforeend',g+'</g>');
}
function dvSelect(i){
DV.sel=i;
dvHandles();
$('dv-fwd').disabled=$('dv-bwd').disabled=i<0;
}
function dvMove(dir){
if(DV.sel<0||!DV.order)return;
var o=DV.order,at=o.indexOf(DV.sel),to=at+dir;
if(at<0||to<0||to>=o.length)return;
var pre=dvSnap();
o.splice(to,0,o.splice(at,1)[0]);
dvCommit(pre);
dvMount();dvSelect(DV.sel);
}
function dvGo(){clearTimeout(DV.h);DV.h=setTimeout(dvRenderNow,300);}
async function dvRenderNow(){
if(!DV.art)return;
var n=++DV.q;
try{
var r=await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:DV.spec,article:DV.art});
if(n!==DV.q)return;
DV.svg=r.svg;
dvMount();dvSet('ready');
}catch(err){
if(DV.svg)dvMount();
toast('渲染失败：'+err.message,ER);
}
}
async function dvLoad(){
var path=$('dv-art').value;
if(!path){toast('先选择文章',WN);return;}
DV.art=path;DV.loadedOnce=true;
dvSet('loading');
try{
var d=await getJSON('/api/visual/design?article='+encodeURIComponent(path)).catch(function(err){
if(!/无设计稿|HTTP 404/.test(err.message))throw err;
return postJSON('/api/visual/render',{confirm:true,template:'xhs-card',spec:DV.spec,article:path});
});
DV.tpl=d.template||'xhs-card';DV.kind=d.kind||'social';DV.spec=d.spec||DV.spec;
DV.params=Object.assign({},d.params||{});
DV.svg=d.svg||'';DV.dpos={};DV.order=null;DV.sel=-1;DV.undo=[];DV.redo=[];DV.dirty=false;
dvMount();dvSet('ready');
}catch(err){
dvSet('idle');
toast('载入失败：'+err.message,ER);
}
}
function dvSetSpec(spec){
if(spec===DV.spec)return;
if(DV.dirty){DV.pendSpec=spec;$('dv-sw').hidden=false;return;}
dvApplySpec(spec);
}
function dvApplySpec(spec){
if(DSPECS.indexOf(spec)<0)return;
DV.dpos={};DV.order=null;DV.sel=-1;DV.spec=spec;DV.dirty=true;
dvSelect(-1);dvSet('loading');dvRenderNow();
}
async function dvSave(){
if(!DV.art)return;
dvSet('saving');
try{
await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:DV.spec,svg:DV.svg});
DV.dirty=false;
dvSet('ready');
toast('已存稿（版本池先行）',OK);
}catch(err){
dvSet('ready');
toast('存稿失败：'+err.message,ER);
}
}
function dvRast(svg,w,h,name){
return new Promise(function(res,rej){
var img=new Image();
img.onload=function(){
var c=document.createElement('canvas');c.width=w;c.height=h;
c.getContext('2d').drawImage(img,0,0,w,h);
c.toBlob(function(b){
if(!b)return rej(new Error('A 路光栅失败'));
var fr=new FileReader();
fr.onload=function(){var a=document.createElement('a');a.href=String(fr.result);a.download=name;a.click();res();};
fr.onerror=function(){rej(new Error('A 路光栅失败'));};
fr.readAsDataURL(b);
},'image/png');
};
img.onerror=function(){rej(new Error('A 路光栅失败'));};
img.src='data:image/svg+xml,'+encodeURIComponent(svg);
});
}
async function dvExportGo(){
$('dv-ex').hidden=true;
var all=document.querySelector('#dv-ex-specs .is-active').getAttribute('data-exspec')==='all';
var route=document.querySelector('#dv-ex-route .is-active').getAttribute('data-exroute');
var specs=all?DSPECS:[DV.spec];
dvSet('exporting');
try{
if(route==='a'){
var stem=DV.art.replace(/^.*\//,'').replace(/\.md$/i,'');
for(var i=0;i<specs.length;i++){
var sp=specs[i],cur=sp===DV.spec;
var r=cur?{svg:DV.svg}:await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:sp,article:DV.art});
var wh=cur?[DV.w,DV.h]:DSIZE[sp];
await dvRast(r.svg,wh[0],wh[1],stem+'.'+(cur?'social':sp)+'.png');
}
toast('已导出 PNG×'+specs.length,OK);
}else{
if(DV.dirty)await dvSave();
for(var j=0;j<specs.length;j++){
if(specs[j]!==DV.spec)await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:specs[j]});
var d=await postJSON('/api/visual/export',{confirm:true,article:DV.art,kind:DV.kind});
var job=await waitForJob(d.job_id),rr=job.result||{};
if(job.status==='failed'||rr.ok===false)throw new Error(rr.error||job.error||'导出失败');
toast('已导出：'+(rr.png||'').split('/').pop()+' '+rr.size[0]+'×'+rr.size[1],OK);
}
}
}catch(err){toast('导出失败：'+err.message,ER);}
dvSet('ready');
}
// F-B（2026-11-18）：色槽 chip 改圆形色点（背景=服务端 slot_hexes、title=槽名）；
// 槽集随服务端 slots（扩槽自动跟随）；最近 5 槽（localStorage）置顶一行。
async function dvLoadSlots(){
try{var d=await getJSON('/api/visual/templates');DV.slotHexes=d.slot_hexes||{};
DV.slots=((d.items||[])[0]||{}).color_slots||Object.keys(DV.slotHexes);
DV.recent=JSON.parse(localStorage.getItem('musespiral-dv-recent')||'[]');}catch(e){}
}
function dvRecentAdd(s){
DV.recent=[s].concat(DV.recent.filter(function(x){return x!==s;})).slice(0,5);
try{localStorage.setItem('musespiral-dv-recent',JSON.stringify(DV.recent));}catch(e){}
}
function dvSwatch(s){
return '<button class="chip'+(DV.params.background_slot===s?' is-active':'')+'" data-dslot="'+esc(s)+'" title="'+esc(s)+'" style="background:'+esc(DV.slotHexes[s]||'var(--paper-2)')+'"></button>';
}
function dvColorChips(){
var slots=DV.slots.length?DV.slots:['primary','text','muted','background','accent'];
var rec=DV.recent.filter(function(s){return slots.indexOf(s)>=0;});
$('dv-colors').innerHTML=(rec.length?'<span class="dv-cap">最近</span>'+rec.map(dvSwatch).join(''):'')+slots.map(dvSwatch).join('');
}
function dvSync(){
[['dspec',DV,'spec'],['dfs',DV.params,'font_size','M'],['dal',DV.params,'align','left'],['dslot',DV.params,'background_slot','background']].forEach(function(it){
var cur=it[1][it[2]];
if(cur==null)cur=it[3]||'';
QA('[data-'+it[0]+']').forEach(function(c){c.classList.toggle('is-active',c.getAttribute('data-'+it[0])===cur);});
});
$('dv-dirty').hidden=!DV.dirty;
$('dv-save-btn').disabled=!DV.dirty||DV.st!=='ready';
$('dv-export-btn').disabled=DV.st!=='ready';
$('dv-undo').hidden=!DV.undo.length;
if(DV.undo.length)$('dv-undo').textContent='撤销 '+DV.undo.length+' 步 · ⌘Z/⇧⌘Z';
$('dv-skel').hidden=DV.st!=='loading';
$('dv-stage').hidden=!DV.svg;
$('dv-empty').hidden=!!DV.svg||DV.st==='loading';
}
async function dvFillArts(){
await loadArticles();
var sel=$('dv-art'),cur=sel.value,opts='<option value="">选择文章…</option>';
state.articles.forEach(function(a){opts+='<option value="'+esc(a.path)+'">'+esc(a.title||a.path)+'</option>';});
sel.innerHTML=opts;
if(cur)sel.value=cur;
}
async function dvEnter(){
await dvFillArts();
await dvLoadSlots();dvColorChips();
if(DV.loadedOnce)return;
var path=new URLSearchParams((location.hash||'').split('?')[1]||'').get('article')||state.detailPath||($('dv-art').options[1]||{}).value;
if(path){$('dv-art').value=path;dvLoad();}
}

// 路由入口（原 renderRoute design 分支 `await dvEnter()` 迁入——A 类专项）
VAPI.design={enter:dvEnter};
function bindDesign(){
// F72 编辑器事件：点选/拖动全走 SVG DOM 原生指针事件（零依赖红线）；8px 网格吸附
var on=function(id,fn){$(id).addEventListener('click',fn);};
$('dv-stage').addEventListener('pointerdown',function(e){
if(DV.st!=='ready')return;
var svgEl=$('dv-stage').querySelector('svg');
if(!svgEl)return;
DV.k=svgEl.getBoundingClientRect().width/(DV.w||1)||1;
var n=e.target.closest('[data-o]');
if(!n){dvSelect(-1);return;}
var i=+n.getAttribute('data-o');
if(DV.sel!==i)dvSelect(i);
DV.drag={n:n,i:i,sx:e.clientX,sy:e.clientY,moved:false,pre:dvSnap()};
if(n.setPointerCapture)n.setPointerCapture(e.pointerId);
e.preventDefault();
});
$('dv-stage').addEventListener('pointermove',function(e){
if(!DV.drag)return;
var d=DV.drag,dx=e.clientX-d.sx,dy=e.clientY-d.sy;
if(!d.moved&&Math.abs(dx)<3&&Math.abs(dy)<3)return;
if(!d.moved){d.moved=true;d.n.classList.add('dv-drag');}
var k=DV.k,gx=Math.round(dx/k/8)*8,gy=Math.round(dy/k/8)*8;
d.gx=gx;d.gy=gy;
d.n.setAttribute('transform','translate('+gx+','+gy+')');
});
$('dv-stage').addEventListener('pointerup',function(){
if(!DV.drag)return;
var d=DV.drag;DV.drag=null;
d.n.classList.remove('dv-drag');
if(!d.moved)return;
if(d.gx||d.gy){DV.dpos[d.i]=[d.gx,d.gy];dvCommit(d.pre);}
else d.n.removeAttribute('transform');
dvHandles();
});
$('dv-stage').addEventListener('dblclick',function(e){
if(DV.st!=='ready')return;
var n=e.target.closest('[data-t]');
if(!n)return;
var key=n.getAttribute('data-t'),tin=$('dv-tin'),s=$('dv-stage').querySelector('svg').getBoundingClientRect(),r=$('dv-wrap').getBoundingClientRect(),b=n.getBBox(),k=s.width/(DV.w||1);
tin.style.left=(s.left-r.left+b.x*k)+'px';
tin.style.top=(s.top-r.top+(b.y+b.height)*k+4)+'px';
tin.style.width=Math.max(140,b.width*k+28)+'px';
tin.setAttribute('data-dtk',key);
tin.value=String(DV.params[key]==null?'':DV.params[key]);
tin.hidden=false;tin.focus();tin.select();
});
$('dv-tin').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter')this.blur();
else if(e.key==='Escape'){this.removeAttribute('data-dtk');this.hidden=true;}
});
$('dv-tin').addEventListener('blur',function(){
var key=this.getAttribute('data-dtk');
this.hidden=true;
if(!key)return;
this.removeAttribute('data-dtk');
var v=this.value,pre=dvSnap();
if(String(DV.params[key]==null?'':DV.params[key])===v)return;
DV.params[key]=v;
dvCommit(pre);
dvGo();
});
// 视图级委托：chip 组（规格/色槽/字号/对齐/导出弹层）+ 弹层遮罩关闭（一次绑定）
$('view-design').addEventListener('click',function(e){
var t=e.target;
if(t.classList.contains('modal-overlay')){t.hidden=true;DV.pendSpec='';return;}
var b=t.closest('[data-dspec],[data-dslot],[data-dfs],[data-dal],[data-exspec],[data-exroute]');
if(!b)return;
if(b.hasAttribute('data-dspec'))return dvSetSpec(b.getAttribute('data-dspec'));
if(b.hasAttribute('data-exspec')||b.hasAttribute('data-exroute')){
b.parentElement.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('is-active',c===b);});
return;
}
var key=b.hasAttribute('data-dslot')?'background_slot':b.hasAttribute('data-dfs')?'font_size':'align';
var v=b.getAttribute('data-dslot')||b.getAttribute('data-dfs')||b.getAttribute('data-dal');
if(v===DV.params[key])return;
var pre=dvSnap();
DV.params[key]=v;
if(key==='background_slot'){dvRecentAdd(v);dvColorChips();}
dvCommit(pre);dvGo();
});
on('dv-fwd',function(){dvMove(1);});
on('dv-bwd',function(){dvMove(-1);});
on('dv-load-btn',function(){dvLoad();});
on('dv-empty-load',function(){dvLoad();}); // FEAT-5：空态引导 CTA 与头「载入」同路
on('dv-save-btn',function(){dvSave();});
on('dv-export-btn',function(){$('dv-ex').hidden=false;});
$('dv-art').addEventListener('change',function(){
if(DV.dirty){toast('先存稿再换文章',WN);this.value=DV.art||'';return;}
if(this.value)dvLoad();
});
on('dv-sw-cancel',function(){$('dv-sw').hidden=true;DV.pendSpec='';});
on('dv-sw-drop',function(){$('dv-sw').hidden=true;var s=DV.pendSpec;DV.pendSpec='';dvApplySpec(s);});
$('dv-sw-save').addEventListener('click',async function(){var s=DV.pendSpec;$('dv-sw').hidden=true;DV.pendSpec='';await dvSave();dvApplySpec(s);});
on('dv-ex-go',function(){dvExportGo();});
// F72 编辑器域快捷键：⌘Z/⇧⌘Z 撤销重做（表外新增，编辑器域内生效）；Esc 逐层收
// （切换确认/导出弹层 → 取消选中）。⌘1-7 七视图导航在全局 keydown（深构 §5.1/
// §八-4 分域裁决：视图外管导航、#article 内 ⌘1-4 管 tab）——原 design 域内实现
// 属作用域缺陷，2026-11-17 集成轮修复移全局。
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if($('view-design').hidden)return;
if(e.target.closest&&e.target.closest('input,textarea,select'))return;
var m=e.metaKey||e.ctrlKey;
if(m&&(e.key==='z'||e.key==='Z')){e.preventDefault();e.shiftKey?dvTravel(0):dvTravel(1);return;}
if(e.key==='Escape'){
if(!$('dv-sw').hidden){$('dv-sw').hidden=true;DV.pendSpec='';return;}
if(!$('dv-ex').hidden){$('dv-ex').hidden=true;return;}
dvSelect(-1);
}
});
}
bindDesign();
// @endview
// @view:publish
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
function checkSummary(checks){
var fail=(checks&&checks.fail)||0,warn=(checks&&checks.warn)||0,pass=(checks&&checks.pass)||0;
if(fail>0) return '<span class="qcheck fail" title="有 '+fail+' 项 FAIL——进详情「检查」Tab 看原文与修复命令">'+I('error')+'FAIL '+fail+'</span>';
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
return '<a class="article-card qcard" href="'+href+'">' +
'<div class="qcard-cover"'+(coverRel?' data-cover="'+esc(coverRel)+'"':'')+'>' +
'<div class="qcover-block"><span class="qcover-text">'+title+'</span></div></div>' +
'<div class="qcard-body">' +
'<h3 class="card-title qcard-title">'+title+'</h3>' +
'<div class="qcard-meta">'+cardStatusBadge(item)+'<span class="qcard-time">'+esc(item.date||'—')+'</span></div>' +
(arts.length?'<div class="qcard-arts">'+arts.join('')+'</div>':'') +
(variants.length?'<div class="tags qcard-variants">'+variants.map(function(code){
return '<span class="tag accent">'+esc(code)+Z;}).join('')+'</div>':'') +
'<div class="qcard-foot">'+checkSummary(item.checks)+'<button class="btn btn-ghost qcard-fb" data-fb="'+esc(item.path)+'" title="反哺素材库：全文归档 / 摘取段落 / AI 提炼论点">→ 素材库</button></div>' +
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
// 漏斗带（重构设计 §3.4）：四级计数=articles 载荷前端聚合；viz 序列着色；段可点=筛选深链
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
updateNavBadge('nav-badge-publish',n.pending); // §2.1 发布位徽标=待审数
renderFunnel();
}
var EMPTY_TEXT=$('queue-empty-text').textContent;
// renderSkeleton 真身已迁 core 段共享块（消费点=core loadArticles——直入
// #article/⌘K 首载时 publish 未注入也能出骨架；A 类专项 2026-11-20）
// 视图内筛选/视图形态一律 replaceState（契约 §4.3：筛选不污染历史）
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
return '<a class="pub-row" href="'+href+'">'+cardStatusBadge(item)+'<span class="pub-row-t">'+esc(item.title||item.path)+'</span><span class="pub-row-pl">'+(variants.length?variants.map(function(code){return '<span class="tag accent">'+esc(code)+Z;}).join(''):'<span class="muted">—')+'</span>'+checkSummary(item.checks)+'<button class="btn btn-ghost qcard-fb" data-fb="'+esc(item.path)+'" title="反哺素材库：全文归档 / 摘取段落 / AI 提炼论点">→素材库</button><span class="pub-row-time">'+esc(item.date||'—')+'</span></a>';
}
// ── N-2 文章反哺（2026-11-21，设计 §3.2 三路径）────────────────────────
// 文章 → 素材库回程：mode=full 整篇归档（剥 frontmatter，provenance 记
// published_from）/ excerpt 行区间摘取（start_line/end_line 1-based 双闭，
// 对剥除 frontmatter 后的正文行计）/ distill AI 抽 3-5 条核心论点→多条
// spark（license=AI-GENERATED）。POST /api/materials/from-article（同步瞬时，
// 不走 job）；幂等（uri 锚 article://<slug>…，重复提交 dedup 命中不追加）；
// distill 未配置 AI → 可读降级面（err 文案回显 #fb-note）。弹窗复用 #viewer
// 通用面板（clipCard 同款——表单 innerHTML 进 viewer-body，viewJob 等既有
// 用法互不干扰：模式切换/提交/关闭全走 #viewer 委托，id 前缀 fb- 独占）。
// slug=文章文件名主干（服务端只认 articles_root 直下 <slug>.md，子目录文章
// 由 400 可读错误暴露——不做前端静默截断）。
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
updateChipCounts(); // D4 统计行（设计终评 §三 P1-D4）：头部先算——列表渲染异常也不失更新
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
batchInit();  // W8：给新渲染的卡片注入批量复选框（同段定义，幂等）
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
// 路由入口（原 renderRoute publish 分支整体迁入——A 类专项：分支归视图）
VAPI.publish={enter:async function(route){
var qf=route.params.get('filter');
if(qf&&['all','draft','pending','reviewed','published','fail'].indexOf(qf)>=0)state.filter=qf;
var pv=route.params.get('view');
if(pv==='grid'||pv==='list')state.sview=pv;
await Promise.all([loadArticles(),loadPlatforms()]);
renderPublish();
},repaint:renderPublish};
function bindPublish(){
// W8 批量操作绑定（2026-11-24）
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
// N-2 文章反哺接线（2026-11-21）：文章卡/行内 [→ 素材库] 按钮（button 在 <a>
// 内，preventDefault 断锚点导航）；fb 弹窗三路径切换/提交/关闭走 #viewer 委托
// （id 前缀 fb- 独占——viewJob/clipCard 等既有 viewer 用法零干扰）
$('view-publish').addEventListener('click',function(e){
var b=e.target.closest('[data-fb]');
if(b){e.preventDefault();fbOpen(b.getAttribute('data-fb'));}
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
// 发布中心（§3.4）：漏斗段点击=筛选深链；网格/列表切换（replaceState）；排期周历未接线降级 toast
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
// P4 排期周历：GET /api/plan（plan_service 只读）→ 三态着色（计划/已交付/已发布）
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
// P4 队列键盘导航（↑↓/Enter，§6.1）——原居 core bindCore，对 moveQSel/openQSel
// 的调用属跨视图直呼（A 类专项 2026-11-20 下沉本块；core 剩余分支不消费方向键，
// 此处绑定注册在后仍先于浏览器默认行为，条件与原文逐字一致）
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
// 批量操作（W8，2026-11-24）：多选 → batch_check / batch_pack。
// 独立于键盘导航的 qsel（那是单选）；选择态记为 state.batch（Set<path>）。
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
// @endview
// @view:insight
function pendingCount(){
return state.articles.filter(function(a){var c=a.checks||{},f=a.flags||{};return (c.fail||0)===0&&(c.warn||0)===0&&articleStatus(a)==='draft'&&!f.published;}).length;}
// dkey/loadRadar 真身已迁 core 段共享块（消费点含 @view:sources 源表「今日新增」
// 与 core 路由装载——台账 A-3；A 类专项 2026-11-20）
function renderTrend(){
var R=state.irange===90?89:29;
var days=[],i,d=new Date(),max=1,seen={},evs=[];
for(i=R;i>=0;i--)days.push({k:dkey(new Date(d.getTime()-i*864e5)),n:0,p:0,g:0});
if($('trend-title')){$('trend-title').textContent='发布趋势（近 '+state.irange+' 天）';$('trend-from').textContent=state.irange+' 天前';}
var bp=(state.stats&&state.stats.by_platform)||{};
Object.keys(bp).forEach(function(c){((bp[c]||{}).recent||[]).forEach(function(r){
if(!r||!r.ts)return;
var k=r.ts+'|'+c+'|'+r.action+'|'+(r.title||r.article||'');
if(seen[k])return;seen[k]=1;evs.push(r);});});
evs.forEach(function(r){var k=String(r.ts).slice(0,10);
for(var j2=0;j2<days.length;j2++)if(days[j2].k===k){days[j2].n++;if(r.action==='publish')days[j2].p++;else days[j2].g++;break;}});
days.forEach(function(x){if(x.n>max)max=x.n;});
$('trend-chart').innerHTML=days.map(function(x){
return '<i'+(x.n?' class="on"':'')+' style="height:'+(x.n?Math.max(8,Math.round(x.n/max*100)):4)+'%" title="'+x.k+' · '+(x.n?x.n+' 条（发表 '+x.p+' · 草稿 '+x.g+'）':'无记录')+'"></i>';}).join('');}
// FEAT-3 数据回流：阅读/在看/分享趋势（SVG 自绘，按周/月聚合）。数据来自
// stats.recent 每篇的 stats 子对象（发表后自动回填 / 健康卡「回采」），零新端点。
function statsBuckets(mode){
var rows=(state.stats&&state.stats.recent)||[],m={},order=[];
rows.forEach(function(r){
if(!r||r.action!=='publish'||!r.ts)return;
var d=String(r.ts).slice(0,10),k;
if(mode==='month')k=d.slice(0,7);
else{var dt=new Date(d+'T00:00:00'),wd=(dt.getDay()+6)%7;dt.setDate(dt.getDate()-wd);k=dkey(dt);}
var s=r.stats||{},rd=+(s.reads!=null?s.reads:r.reads)||0,zk=+s.zaikan||0,sh=+(s.shares!=null?s.shares:r.shares)||0;
if(!m[k]){m[k]={reads:0,zaikan:0,shares:0};order.push(k);}
m[k].reads+=rd;m[k].zaikan+=zk;m[k].shares+=sh;});
order.sort();return order.map(function(k){return{k:k,reads:m[k].reads,zaikan:m[k].zaikan,shares:m[k].shares};});}
function renderStatsTrend(){
var el=$('stats-trend');if(!el)return;
var mode=state.srange==='month'?'month':'week';
QA('[data-srange]').forEach(function(e){e.classList.toggle('is-active',e.getAttribute('data-srange')===mode);});
var b=statsBuckets(mode).slice(-8),n=b.length,note=$('stats-trend-note');
if(!n){el.innerHTML='<svg viewBox="0 0 300 150" role="img" aria-label="阅读在看分享趋势"><text class="st-empty" x="150" y="75" text-anchor="middle">发表并回填数据后，此处显示趋势</text></svg>';
if(note)note.textContent='暂无回填数据（发表后自动回采，或点健康卡「回采」）';return;}
var W=300,H=150,PL=32,PR=8,PT=10,PB=24,max=1;
b.forEach(function(x){max=Math.max(max,x.reads,x.zaikan,x.shares);});
function SX(i){return PL+(n===1?0.5:i/(n-1))*(W-PL-PR);}
function SY(v){return PT+(1-v/max)*(H-PT-PB);}
function line(key){return b.map(function(x,i){return(i?'L':'M')+SX(i).toFixed(1)+' '+SY(x[key]).toFixed(1);}).join(' ');}
function dots(key,cls){return b.map(function(x,i){return'<circle class="st-dot '+cls+'" cx="'+SX(i).toFixed(1)+'" cy="'+SY(x[key]).toFixed(1)+'" r="2.5"><title>'+esc(x.k)+' · '+x[key]+'</title></circle>';}).join('');}
var grids=[0,0.5,1].map(function(f){var y=SY(max*f);return'<line class="st-grid" x1="'+PL+'" y1="'+y+'" x2="'+(W-PR)+'" y2="'+y+'"></line><text class="st-axis" x="'+(PL-4)+'" y="'+(y+3)+'" text-anchor="end">'+Math.round(max*f)+'</text>';}).join('');
var labels=b.map(function(x,i){return'<text class="st-axis" x="'+SX(i).toFixed(1)+'" y="'+(H-6)+'" text-anchor="middle">'+esc(x.k.slice(5))+'</text>';}).join('');
el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="阅读在看分享趋势">'+grids+
'<path class="st-line st-reads" d="'+line('reads')+'"></path><path class="st-line st-zaikan" d="'+line('zaikan')+'"></path><path class="st-line st-shares" d="'+line('shares')+'"></path>'+
dots('reads','st-reads')+dots('zaikan','st-zaikan')+dots('shares','st-shares')+labels+'</svg>'+
'<div class="st-legend"><span><i class="st-reads"></i>阅读</span><span><i class="st-zaikan"></i>在看</span><span><i class="st-shares"></i>分享</span></div>';
if(note)note.textContent=(mode==='month'?'按月':'按周')+'聚合 · 最近 '+n+' 个'+(mode==='month'?'月':'周')+' · 阅读 '+b.reduce(function(s,x){return s+x.reads;},0)+' / 在看 '+b.reduce(function(s,x){return s+x.zaikan;},0)+' / 分享 '+b.reduce(function(s,x){return s+x.shares;},0);}
var PNN={mp:'公众号',kuaishou:'快手'};
function renderPlatformBars(){
var bp=(state.stats&&state.stats.by_platform)||{};
var rows=state.platforms.map(function(p){var g=bp[p.code]||{};return{name:p.name||PN[p.code]||PNN[p.code]||p.code,pub:g.publishes||0,dft:g.drafts||0};});
if(!rows.length){$('platform-bars').innerHTML='<p class="action-hint">平台列表为空</p>';return;}
var max=1;rows.forEach(function(r){if(r.pub>max)max=r.pub;});
$('platform-bars').innerHTML=rows.map(function(r){
return '<div class="pbar"><span class="pbar-name">'+esc(r.name)+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+Math.round(r.pub/max*100)+'%"></span></span><span class="pbar-n">'+r.pub+' 篇'+(r.dft?' · 草稿 '+r.dft:'')+'</span></div>';}).join('');}
function hcard(ok,m,l,d,f){
return '<div class="hcard '+(ok?'ok':'warn')+'" data-health="'+m+'">'+(typeof mdpubIcon==='function'?mdpubIcon(ok?'check':'warning'):'')+'<div><b>'+l+'</b>'+(d?' <span class="muted">'+esc(d)+Z:'')+(ok||!f?'':'<code class="fix-cmd">$ '+f+'</code>')+D+D;}
async function renderHealth(){
var g=$('health-grid'),h;
try{h=await getJSON('/api/health');}catch(err){h=null;}
if(!h){g.className='hcards';g.innerHTML=hcard(false,'health','环境健康','健康信息不可用','');return;}
var c=h.credentials||{},a=h.ai||{},im=h.imagehost||{},ck=h.cookies||{},j=h.jobs||{},s='';
s+=hcard(c.configured,'credentials','凭证',(c.accounts||0)+' 账号','musespiral config set …');
s+=hcard(a.configured,'ai','AI',a.model||'','musespiral ai config …');
s+=hcard(im.default,'imagehost','图床',(im.hosts||[]).join('、'),'musespiral imagehost config …');
s+=hcard((j.queued||0)<10,'jobs','任务','排队 '+(j.queued||0)+' / 运行 '+(j.running||0),'');
s+=hcard((h.disk_free_gb||0)>=5,'disk','磁盘',(h.disk_free_gb||'—')+' GB','');
for(var i=0;i<CK.length;i++)s+=hcard(ck[CK[i][0]],'cookies',CK[i][1],'','musespiral upload login …');
s+='<div class="hcard ok" data-health="data"><div><b>数据</b> <button class="btn" type="button" id="sync-stats-btn">回采</button></div></div>';
g.className='hcards';g.innerHTML=s;
var SB=g.querySelector('#sync-stats-btn');
if(SB)SB.onclick=async function(){SB.disabled=true;
try{var d=await postJSON('/api/action/sync_stats',{confirm:true});await queued(d.job_id);await loadStats(true);renderInsight();}
catch(err){toast('回采失败：'+err.message,ER);}finally{SB.disabled=false;}};}
function renderInsight(){
var st=state.stats||{},pub=st.total_publish||0;
// P1-4 洞察零态引导：零发布时瓷砖区换引导卡（一张卡横跨四砖位，.tiles .empty），非零态保持原四砖
if(!pub)$('stat-tiles').innerHTML='<div class="empty"><div class="empty-icon" data-icon="publish">'+I('publish')+'</div><div class="empty-title">发布第一篇后，此处显示漏斗与趋势</div><div class="empty-text">素材与草稿已在积累，发布后四项指标在此生长。</div><a class="btn btn-primary empty-action" href="#publish">去发布中心</a></div>';
else $('stat-tiles').innerHTML=[['已发布',pub],['待审',pendingCount()],['总阅读',st.total_reads||0],['平台数',state.platforms.length]].map(function(t){
return '<div class="tile"><div class="tile-value">'+esc(t[1])+D+'<div class="tile-label">'+esc(t[0])+'</div></div>';}).join('');
QA('[data-irange]').forEach(function(el){el.classList.toggle('is-active',+el.getAttribute('data-irange')===state.irange);});
renderIdeaFunnel();renderTrend();renderStatsTrend();renderWriting();renderPlatformBars();renderContrib();renderScoreDist();renderAiUsage();renderComposeAct();renderRadar();
}
// 入口 C §五 选题雷达（纯只读 GET /api/topic_radar）。零落库零 POST——刷新/立即生成只弹
// CLI 引导 toast；kind 徽三色 radar=viz-1 青黛/kb=viz-5 石板/star=jade；AI 角度行青黛左边线
var RK={radar:'var(--viz-1)',kb:'var(--viz-5)',star:'var(--jade)'};
function rr(c){var r=c.refs,s=+c.score||0,q=c.reasons||[],e=c.excluded,
t='<span data-t>'+esc(c.title)+'</span>',o;
if(r.material_id)t='<a data-t href="#material?m='+encodeURIComponent(r.material_id)+'">'+esc(c.title)+'</a>';
else if(r.url)t='<a data-t href="'+esc(r.url)+'" target="_blank" rel="noopener noreferrer">'+esc(c.title)+'</a>';
o='<div class="radar-row"><span class="badge" style="color:'+(RK[c.kind]||'var(--ink-2)')+'">'+esc(c.kind)+Z+t+
'<span class="dc-bar '+(s>79?'hi':s>59?'mid':'lo')+'"><i style="width:'+Math.min(s,100)+'%"></i>'+Z+'<span>'+esc(q[0])+Z;
if(q[1])o+='<details><summary>+'+(q.length-1)+'</summary>'+esc(q.slice(1).join('；'))+'</details>';
if(r.path)o+='<code>'+esc(r.path)+'</code>';
return o+(e?'<span>≈'+Math.round(e.similarity*100)/100+' '+esc(e.matched_title)+Z:'')+(c.ai_angle?'<em>AI 角度：'+esc(c.ai_angle)+'</em>':'')+D}
function renderRadar(){var sn=state.radar,b=$('radar-body'),d=[],k=[],x=[];
if(!sn)return b.innerHTML='<div class="empty"><div class="empty-title">尚无选题快照</div><div class="empty-text">周一 08:20 自动生成；或运行 musespiral topic-radar 立即生成。</div><button class="btn empty-action" type="button" id="radar-gen">立即生成</button></div>';
d=sn.inputs.down_sources||[];
sn.candidates.forEach(function(c){(c.excluded?x:k).push(c)});
b.innerHTML='<p class="action-hint">'+(d.length?'<span class="act-dot" style="background:var(--amber)"></span><span style="color:var(--amber)">外部腿缺失（'+esc(d.join('、'))+'）</span> · ':'')+
esc(sn.generated_at.replace('T',' '))+' · AI 增强 '+sn.ai_enhanced_n+(k.length>8?' · 前 8 / '+k.length+' 候选（全量见 CLI）':'')+'</p>'+
k.slice(0,8).map(rr).join('')+(x.length?'<details id="radar-ex"><summary class="action-hint">已写过排除 '+x.length+' 项（相似度 ≥0.6，点开回看）</summary>'+x.map(rr).join('')+'</details>':'')}
// 创意漏斗（重构设计 §3.5）：mats 的 triage/drafted + articles 状态机——载荷前端已有，此处开读；段可点深链
function renderIdeaFunnel(){
var mats=state.mats||[],arts=state.articles||[],starred=0,drafted=0;
mats.forEach(function(m){var t=m.triage||{};if(t.starred)starred++;if(m.drafted)drafted++;});
var published=arts.filter(function(a){return (a.flags||{}).published;}).length;
	var segs=[
	['采集',mats.length,'#material','var(--viz-1,#2c5f8a)','入库素材总数'],
	['收藏',starred,'#material?tab=starred','var(--viz-4,#9c5700)','素材行首星标数'],
	['骨架',drafted,'#material','var(--viz-5,#5d6d7e)','素材已生成骨架数'],
	['成稿',arts.length,'#publish?filter=draft','var(--viz-2,#c0392b)','草稿文章数（含manual新建，可不经骨架）'],
	['发布',published,'#publish?filter=published','var(--viz-3,#1a7038)','已发布文章数']];
	$('idea-funnel').innerHTML=segs.map(function(s,i){
	var rate=i&&segs[i-1][1]?Math.round(s[1]/segs[i-1][1]*100)+'%':'—';
	var jump=(i&&s[1]>segs[i-1][1])?'（本阶段 > 上一阶段：来源不限于上一阶段）':'';
	var tip='「'+s[0]+'」'+s[1]+' · '+s[4]+jump+' · '+((i&&segs[i-1][1])?'较上一阶段 '+rate:'起点')+' · 点击深链 '+s[2];
	return '<a class="fb-seg" href="'+s[2]+'" title="'+esc(tip)+'" style="flex:'+(s[1]||1)+'">' +
'<span class="fb-n" style="color:'+s[3]+'">'+s[1]+'</span><span class="fb-t">'+s[0]+(i?' <span class="fb-rate">←'+rate+'</span>':'')+'</span></a>';
}).join('<span class="fb-arrow" aria-hidden="true">›</span>');
}
// 源贡献（§3.5）：mats 按 origin.source_id 聚合（素材量/收藏），点击行→源中枢
function renderContrib(){
var mats=state.mats||[],g={},order=[];
mats.forEach(function(m){var k=(m.origin||{}).source_id||'—';if(!g[k]){g[k]={n:0,star:0};order.push(k);}g[k].n++;if((m.triage||{}).starred)g[k].star++;});
if(!order.length){$('contrib-bars').innerHTML='<p class="action-hint">暂无素材（采集后此处见源贡献）</p>';return;}
var names={};matSrcs().forEach(function(s){names[s.id]=s.label||s.id;});
order.sort(function(a,b){return g[b].n-g[a].n;});
var top=order.slice(0,8),max=g[top[0]].n||1;
$('contrib-bars').innerHTML=top.map(function(k){
return '<a class="contrib-row" href="#sources?src='+encodeURIComponent(k)+'" title="查看源详情"><span class="pbar-name">'+esc(names[k]||k)+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+Math.round(g[k].n/max*100)+'%"></span></span><span class="pbar-n">'+g[k].n+' 条 · 收藏 '+g[k].star+'</span></a>';
}).join('');
}
// 评分分布（§3.5）：四桶 mini 直方（<60/60-79/≥80/无分）
function renderScoreDist(){
var b=[0,0,0,0],mats=state.mats||[];
mats.forEach(function(m){var s=m.score;if(s==null)b[3]++;else if(s>=80)b[2]++;else if(s>=60)b[1]++;else b[0]++;});
var max=Math.max.apply(null,b.concat(1)),lb=['<60','60-79','≥80','无分'];
$('score-dist').innerHTML=b.map(function(n,i){
return '<div class="sd-col"><span class="sd-bar" style="height:'+Math.round(n/max*100)+'%"></span><span class="sd-n">'+n+'</span><span class="sd-l">'+lb[i]+'</span></div>';
}).join('');
$('score-dist-note').textContent='评分素材 '+mats.length+' 条（radar_signal 整分 / kb text 四维现算；其余类型无分沉底）';
}
// AI 用量（§3.5）：任务中心 job 历史按 kind 分类计数（新在前 50 条口径）
function renderAiUsage(){
var AJ={compose_generate:'创作再生成',annotate_rewrite:'批注改写',adapt:'变体生成',batch_adapt:'批量变体',fetch:'URL 抓取',ingest:'源刷新',sync_stats:'数据回采',pack:'物料包',publish:'创建草稿'};
var c={};(state.jobs||[]).forEach(function(j){if(!j)return;c[j.kind]=(c[j.kind]||0)+1;});
var ks=Object.keys(AJ).filter(function(k){return c[k];});
$('ai-usage').innerHTML=ks.length?ks.map(function(k){
return '<div class="au-row"><span class="au-k">'+AJ[k]+'</span><span class="au-n">'+c[k]+' 次</span></div>';
}).join(''):'<p class="action-hint">暂无任务历史</p>';
}
// 创作节奏（§3.5）：compose_generate job 的 30 天活动点阵
function renderComposeAct(){
var days=[],d=new Date(),i,seen={};
for(i=29;i>=0;i--)days.push(dkey(new Date(d.getTime()-i*864e5)));
days.forEach(function(k){seen[k]=0;});
var total=0;(state.jobs||[]).forEach(function(j){
if(!j||j.kind!=='compose_generate')return;
var k=String(j.created||'').slice(0,10);
if(seen[k]!=null){seen[k]++;total++;}});
$('compose-act').innerHTML=days.map(function(k){
return '<i class="act-dot'+(seen[k]?' on':'')+'" title="'+k+(seen[k]?' · '+seen[k]+' 次再生成':' · 无')+'"></i>';}).join('');
$('compose-act-note').textContent='近 30 天再生成 '+total+' 次 · 会话 '+(state.cs||[]).length+' 个';
}
// FEAT-P1B 写作节奏（写作统计仪表盘）：近 30 天每日字数增量柱状图（SVG 自绘，
// 沿 renderStatsTrend 模式）+ 连续天数徽章 + 周目标进度条。数据源=/api/writing_stats
// （rounds 历史按日增量，pure-read）；空数据/无 session → 引导文案，不报错。
function writingBars(daily){
var W=300,H=100,PL=34,PR=6,PT=8,PB=18,n=daily.length,bw,ma=1,i;
if(!n)return '';
for(i=0;i<n;i++)if(daily[i].chars_added>ma)ma=daily[i].chars_added;
bw=Math.max(1,(W-PL-PR)/n-1);
function y(v){return PT+(1-v/ma)*(H-PT-PB);}
var grid=[0,.5,1].map(function(f){var yy=y(ma*f).toFixed(1);return '<line class="wr-grid" x1="'+PL+'" y1="'+yy+'" x2="'+(W-PR)+'" y2="'+yy+'"></line><text class="wr-axis" x="'+(PL-4)+'" y="'+yy+'" text-anchor="end">'+Math.round(ma*f)+'</text>';}).join('');
var bars=daily.map(function(x,k){
var h=x.chars_added?Math.max(2,Math.round(x.chars_added/ma*(H-PT-PB))):0,xx=(PL+k*(bw+1)).toFixed(1);
return '<rect class="wr-bar'+(h?' on':'')+'" x="'+xx+'" y="'+(H-PB-h).toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+Math.max(h,1)+'" rx="1"><title>'+esc(x.date)+' · '+x.chars_added+' 字'+(x.sessions?' · '+x.sessions+' 个会话':'')+'</title></rect>';}).join('');
var labs=daily.map(function(x,k){return k%5?'':'<text class="wr-axis" x="'+(PL+k*(bw+1)+bw/2).toFixed(1)+'" y="'+(H-4)+'" text-anchor="middle">'+esc(x.date.slice(5))+'</text>';}).join('');
return '<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="近 30 天每日字数增量">'+grid+bars+labs+'</svg>';
}
function renderWriting(){
var w=state.writing,c=$('writing-chart'),g=$('writing-goal'),b=$('writing-streak'),n=$('writing-note');
if(!c)return;
var daily=(w&&w.daily)||[],st=(w&&w.streak)||{},wk=(w&&w.weekly)||{},cur=st.current||0,i,sum=0,act=0,wrote=wk.written||0,tgt=wk.target||0,pct=wk.pct||0;
if(!w||w.empty||!daily.length){
c.innerHTML='<div class="empty wr-empty"><div class="empty-title">还没有写作记录</div><div class="empty-text">在创作工坊生成第一轮草稿后，这里会按天累计你的字数节奏。</div><a class="btn btn-primary empty-action" href="#compose">去创作工坊</a></div>';
if(g)g.innerHTML='';if(b){b.textContent='';b.className='streak-badge';}if(n)n.textContent='近 30 天暂无字数增量（无会话/无轮次历史）';
return;
}
c.innerHTML=writingBars(daily);
if(b){b.textContent=cur?('🔥 '+cur+' 天连续'):('最长 '+(st.longest||0)+' 天');b.className='streak-badge'+(cur>=3?' hot':'');}
if(g)g.innerHTML='<div class="wr-goal-row"><span class="wr-goal-lab">本周目标</span><span class="pbar-track"><span class="pbar-fill" style="width:'+(tgt>0?Math.min(100,pct):0)+'%"></span></span><span class="wr-goal-n">'+wrote+' / '+tgt+' 字'+(pct>=100?' ✓':'')+'</span></div>';
for(i=0;i<daily.length;i++){sum+=daily[i].chars_added;if(daily[i].chars_added>0)act++;}
if(n)n.textContent='近 30 天共 '+sum+' 字 · 活跃 '+act+' 天 · 历史最长连续 '+(st.longest||0)+' 天';
}
// 路由入口（原 renderRoute insight 分支整体迁入——A 类专项：分支归视图）
VAPI.insight={enter:async function(route){
var rg=route.params.get('range');
if(rg==='30'||rg==='90')state.irange=+rg;
await Promise.all([loadStats(),loadPlatforms(),loadArticles(),loadMaterials(),loadCompose(true),refreshJobs(),renderHealth(),loadRadar(),loadWriting(true)]);
renderInsight();
},repaint:renderInsight};
function bindInsight(){
// 洞察（§3.5）：range 30/90 replaceState + 趋势重绘
$('insight-range').addEventListener('click',function(e){
var c=e.target.closest('[data-irange]');
if(!c)return;
state.irange=+c.getAttribute('data-irange');
history.replaceState(null,'','#insight'+(state.irange!==30?'?range='+state.irange:''));
renderInsight();
});
// FEAT-3：数据趋势周/月粒度切换（只重绘 SVG，零请求）
var SR=$('stats-range');
if(SR)SR.addEventListener('click',function(e){
var c=e.target.closest('[data-srange]');
if(!c)return;
state.srange=c.getAttribute('data-srange');
renderStatsTrend();
});
// [重新生成]/[立即生成] 只弹 CLI 引导 toast（零新端点，不发 POST）
function radarHint(){toast('请运行 musespiral topic-radar --refresh，或等周一 08:20 自动生成',OK);}
$('radar-refresh').addEventListener('click',radarHint);
// 空态 [立即生成] 走 #radar-body 委托（body 是静态节点，innerHTML 重绘不丢监听）
$('radar-body').addEventListener('click',function(e){if(e.target.closest('#radar-gen'))radarHint();});
}
bindInsight();
// @endview
// @view:article
function editConfirm(o){return askConfirm(o);}
// FEAT-5 暗色补全：live 预览 iframe 色板拆基座+深色覆写两段。iframe 是 srcdoc 孤岛
// ——不继承父文档 data-theme，prefers-color-scheme 媒体查询也只反映系统态而非
// 「显式选了深色」；renderLive 时按 themeDark() 取当前生效态拼装，切主题由
// applyTheme 的同步钩子重注（ui.js core）。
var LIVE_CSS='body{margin:0;padding:24px 20px;font:15px/1.85 -apple-system,"PingFang SC",sans-serif;color:#1a1a1a;word-break:break-word}h1,h2,h3{line-height:1.35;margin:1.2em 0 .5em}h1{font-size:22px}h2{font-size:19px}h3{font-size:17px}img{max-width:100%}a{color:#c0392b}pre{background:#f4f4f2;padding:10px;border-radius:6px;overflow:auto;font:13px/1.6 Menlo,monospace}code{background:#f4f4f2;padding:1px 5px}blockquote{margin:1em 0;padding:2px 14px;border-left:3px solid #c0392b;color:#555}';
var LIVE_CSS_DARK='body{background:#211f1e;color:#e6e2dc}a{color:#e8604f}pre,code{background:#262423}blockquote{border-left-color:#e8604f;color:#a09a92}';
// hlSeg/renderHl/hideBubble 真身已迁 core 段共享块（EA/HL/AB 皆 core 顶部抓取的
// 编辑器全局；hideBubble 消费点含 core Esc 链——A 类专项 2026-11-20）
var liveTimer=null,sLock=false,bubbleTimer=null,sEcho={p:-1,e:-1};
function renderLive(){LF.srcdoc='<!DOCTYPE html><meta charset="utf-8"><style>'+LIVE_CSS+(themeDark()?LIVE_CSS_DARK:'')+'</style>'+mdToHtml(EA.value,dirOf(state.detailPath));}
function scheduleLive(){if(liveTimer)clearTimeout(liveTimer);liveTimer=setTimeout(renderLive,300);}
// 块级映射同步滚动（沉淀·模式 6/doocs useScrollSync 移植）：mdBlocks 与 mdToHtml 同一
// 分割规则→块数对齐，scrollTop→块索引+块内比例双向映射；双层防回环：sLock 一帧标志 +
// sEcho 程序赋值目标，回声 scroll 与目标差 <2px 吞掉（Chrome/Fx scroll 事件推迟到下一帧）。
var EDLH=20,EDPT=12,PVS='p,h1,h2,h3,h4,pre,blockquote,ul';
function mdBlocks(md){
var ls=String(md).replace(/\r/g,'').split('\n'),o=[],ps=-1,as=-1,i=0;
function cp(end){if(ps>=0)o.push([ps,end]);ps=-1;}
while(i<ls.length){var L=ls[i];
if(BANCHOR.test(L)){if(as<0&&ps<0)as=i;i++;continue;}
if(/^```/.test(L)){cp(i);var a=as>=0?as:i,j=i+1;while(j<ls.length&&!/^```/.test(ls[j]))j++;o.push([a,Math.min(j+1,ls.length)]);as=-1;i=Math.min(j+1,ls.length);continue;}
if(/^(#{1,4})\s+/.test(L)){cp(i);o.push([as>=0?as:i,i+1]);as=-1;i++;continue;}
if(/^>\s?/.test(L)){cp(i);var q=as>=0?as:i;while(i<ls.length&&/^>\s?/.test(ls[i]))i++;o.push([q,i]);as=-1;continue;}
if(/^\s*(?:[-*]|\d+\.)\s+/.test(L)){cp(i);var u=as>=0?as:i;while(i<ls.length&&/^\s*(?:[-*]|\d+\.)\s+/.test(ls[i]))i++;o.push([u,i]);as=-1;continue;}
if(!L.trim()){cp(i);i++;continue;}
if(ps<0)ps=as>=0?as:i;as=-1;i++;}
cp(ls.length);return o;}
function syncPreview(){
try{
var d=LF.contentDocument;if(!d||!d.body)return;
var bs=mdBlocks(EA.value),els=d.querySelectorAll(PVS);
if(!bs.length||!els.length)return;
var lf=Math.max(0,(EA.scrollTop-EDPT)/EDLH),bi=bs.length-1,fr=0,i,sp,n;
for(i=0;i<bs.length;i++){sp=bs[i];n=sp[1]-sp[0];if(lf<sp[1]||i===bs.length-1){bi=i;fr=n?Math.min(1,Math.max(0,(lf-sp[0])/n)):0;break;}}
var el=els[Math.min(bi,els.length-1)],b=d.documentElement;
sLock=true;var t=Math.max(0,el.offsetTop+fr*Math.max(0,el.offsetHeight-6));b.scrollTop=t;sEcho.p=t;setTimeout(function(){sLock=false;},0);
}catch(err){}}
LF.onload=function(){
var d=LF.contentDocument;if(!d)return;
d.addEventListener('scroll',function(){
if(sLock)return;
var b=d.documentElement;
if(sEcho.p>=0){var sw=Math.abs(b.scrollTop-sEcho.p)<2;sEcho.p=-1;if(sw)return;}
var bs=mdBlocks(EA.value),els=d.querySelectorAll(PVS);
if(!bs.length||!els.length)return;
var y=b.scrollTop,bi=els.length-1,fr=0,i,el,h;
for(i=0;i<els.length;i++){el=els[i];h=el.offsetHeight;if(y<el.offsetTop+h||i===els.length-1){bi=i;fr=h?Math.min(1,Math.max(0,(y-el.offsetTop)/h)):0;break;}}
var sp=bs[Math.min(bi,bs.length-1)],n=sp[1]-sp[0];
sLock=true;var t=Math.max(0,(sp[0]+(n?fr*n:0))*EDLH+EDPT);EA.scrollTop=t;sEcho.e=t;setTimeout(function(){sLock=false;},0);
});
};
// 状态四态元数据对象（建议3/沉淀·模式 17，深审 H2 清偿）：UI 只消费元数据；纸墨色义
// 映射复用既有 badge 类——saved=青竹(done)/saving=琥珀(warn)/dirty=墨灰(queued)/failed=暗朱(fail)。
var ST4={saved:{c:'done',t:'已保存'},saving:{c:'warn',t:'保存中…'},dirty:{c:'queued',t:'有未保存修改'},failed:{c:'fail',t:'保存失败'}};
function setSaveSt(st,why){
state.saveSt=st;
if(st==='failed')state.saveErr=why||state.saveErr||'未知错误';
var m=ST4[st],el=$('ed-saved');
el.className='badge '+m.c;
el.textContent=(st==='saved'&&state.savedAt)?m.t+' '+state.savedAt:m.t;
el.title=st==='failed'?state.saveErr:(st==='dirty'?'⌘S / Ctrl+S 保存':'');
}
function updateStatusbar(){
$('ed-chars').textContent=EA.value.replace(/\s+/g,'').length;
setSaveSt(state.saveSt);
$('ed-ai').textContent=state.aiModel||'未配置';
}
async function mountEdit(){
var p=state.detailPath;if(!p)return;
if(state.editPath!==p||!state.editLoaded){
try{var b=await getJSON(`/api/article/body?path=${encodeURIComponent(p)}`);
EA.value=b.content||'';state.editPath=p;state.editLoaded=true;state.savedAt='';state.saveSt='saved';state.saveErr='';}
catch(err){toast('载入正文失败：'+err.message,ER);return;}
}
// F1 编辑器（深度评审 §3.1）：首次进编辑 tab 动态装载 assets/editor.js（CM5 本地
// vendor，零外联），此后每次进入交其自同步正文（EA.value 差异→cm.setValue）；
// 装载失败静默回落 textarea 原功能。工具栏/大纲/状态栏扩展全部由 editor.js 自管理。
if(window.__mdpubEditor)window.__mdpubEditor.activate(EA);
else if(!state._edq){state._edq=1;var _es=document.createElement('script');_es.src='assets/editor.js';_es.onload=function(){window.__mdpubEditor&&window.__mdpubEditor.activate(EA);};document.head.appendChild(_es);}
if(state.aiModel===null){getJSON('/api/health').then(function(h){state.aiModel=(h&&h.ai&&h.ai.model)||'';updateStatusbar();}).catch(function(){state.aiModel='';updateStatusbar();});}
syncGutter();renderHl();renderLive();updateStatusbar();
await loadAnnotations();
}
function syncGutter(){
var n=EA.value.split('\n').length,out='',i;
for(i=1;i<=n;i++)out+=i+'\n';
EG.textContent=out;
AA.disabled=!state.annotations.length;
}
async function loadAnnotations(){
try{state.annotations=(await getJSON(`/api/annotations?path=${encodeURIComponent(state.detailPath)}`)).annotations||[];}
catch(err){state.annotations=[];}
renderAnnotations();syncGutter();
}
function renderAnnotations(){
var a=state.annotations;
$('annot-title').textContent='批注（'+a.length+'）';
$('annot-list').innerHTML=a.length?a.map(function(x){
var range=x.line_start===x.line_end?('第 '+esc(x.line_start)+' 行'):('第 '+esc(x.line_start)+'–'+esc(x.line_end)+' 行');
return '<div class="annot-row"><div class="annot-meta"><b>'+range+'</b><span>'+esc(String(x.created||'').slice(0,16).replace('T',' '))+Z+'</div>'+
'<div>'+esc(x.comment)+D+
'<div class="annot-ops"><button class="btn" data-annot-ai="1">按批注修改（全文）</button>'+
'<button class="btn" data-annot-del="'+esc(x.id)+'">删除</button></div></div>';
}).join(''):'<p class="action-hint">选中正文一段文字，点浮动气泡添加批注。</p>';
}
async function handleSaveBody(){
var p=state.detailPath;if(!p)return;
var ok=await editConfirm({title:'保存正文',message:'仅替换「'+detailTitle(p)+'」的正文（frontmatter 不动）；旧版留 .md.bak。',confirmText:'保存'});
if(!ok)return;
ES.disabled=true;setSaveSt('saving');
try{await postJSON(`/api/article/save`,{article:p,content:EA.value,confirm:true});
var t=new Date();state.savedAt=('0'+t.getHours()).slice(-2)+':'+('0'+t.getMinutes()).slice(-2);
setSaveSt('saved');
toast('正文已保存（旧版在 .md.bak）',OK);await refreshArticleState();}
catch(err){setSaveSt('failed',err.message);toast('保存失败：'+err.message,ER);}
finally{ES.disabled=false;}
}
function toggleAnnotate(){
var side=$('annot-side');side.hidden=!side.hidden;
$('edit-annotate-toggle').textContent='批注栏：'+(side.hidden?'关':'开');
}
async function annotPost(url,body,msg){
try{ await postJSON(url,Object.assign({path:state.detailPath,confirm:true},body));await loadAnnotations();}
catch(err){ toast(msg+'：'+err.message,ER);}
}
function maybeBubble(){
var s=EA.selectionStart,e=EA.selectionEnd;
if(s===e||s>e||!EA.value.slice(s,e).trim()){hideBubble();return;}
state.sel={s:s,e:e,ls:EA.value.slice(0,s).split('\n').length,le:EA.value.slice(0,e).split('\n').length};
renderHl(state.sel);
var mark=HL.querySelector('.tk-mark');
if(!mark){hideBubble();return;}
$('ab-lines').textContent=state.sel.ls===state.sel.le?('第 '+state.sel.ls+' 行'):('第 '+state.sel.ls+'–'+state.sel.le+' 行');
state.bubAt=Date.now();AB.hidden=false;
var host=$('ed-stack').getBoundingClientRect(),r=mark.getBoundingClientRect();
AB.style.left=Math.max(4,Math.min(r.left-host.left,host.width-292))+'px';
AB.style.top=Math.max(0,Math.min(r.top-host.top-AB.offsetHeight-8>=0?r.top-host.top-AB.offsetHeight-8:r.bottom-host.top+8,host.height-AB.offsetHeight))+'px';
$('annot-input').value='';$('annot-input').focus({preventScroll:true});
}
function submitAnnot(){
if(!state.sel)return;
var c=$('annot-input').value.trim();
if(!c){toast('批注内容不能为空',ER);return;}
var sel=state.sel;hideBubble();
annotPost(`/api/annotations/add`,{line_start:sel.ls,line_end:sel.le,comment:c},'添加批注失败');
}
async function handleAnnotateDel(id){annotPost(`/api/annotations/delete`,{id:id},'删除批注失败');}
async function handleAnnotateAI(){
var p=state.detailPath;if(!p||!state.annotations.length)return;
AA.disabled=true;
try{
var d=await postJSON(`/api/action/annotate_rewrite`,{article:p,confirm:true});
var j=await waitForJob(d.job_id);
if(j.status==='failed')toast('任务失败：'+(j.error||'未知错误'),ER);
else{
var out=j.result&&j.result.output;
toast('AI 修改稿已生成'+(out?'：'+out:''),OK,{label:'查看对照',fn:function(){
if(!out){toast('无修改稿输出',ER);return;}
getJSON(`/api/article/body?path=${encodeURIComponent(out)}`).then(function(b){
$('viewer-title').textContent='批注修改对照（- 原文 / + 修改稿）';
$('viewer-body').innerHTML=diffHtml(EA.value,b.content||'');
$('viewer').hidden=false;
}).catch(function(err){toast('对照稿读取失败：'+err.message,ER);});
}});
}
}catch(err){toast('AI 修改提交失败：'+err.message,ER);}
finally{AA.disabled=false;await loadAnnotations();}
}
function diffHtml(a,b){
var A=String(a).replace(/\r/g,'').split('\n'),B=String(b).replace(/\r/g,'').split('\n'),n=A.length,m=B.length,d=[],i,j;
for(i=0;i<=n;i++)d.push(new Array(m+1).fill(0));
for(i=n-1;i>=0;i--)for(j=m-1;j>=0;j--)d[i][j]=A[i]===B[j]?d[i+1][j+1]+1:Math.max(d[i+1][j],d[i][j+1]);
var t=[],i=0,j=0;
while(i<n&&j<m){if(A[i]===B[j]){t.push([0,A[i]]);i++;j++;}
else if(d[i+1][j]>=d[i][j+1]){t.push([-1,A[i]]);i++;}else{t.push([1,B[j]]);j++;}}
while(i<n)t.push([-1,A[i++]]);while(j<m)t.push([1,B[j++]]);
var out=[],run=0,k,cur;
function flush(){if(run>3)out.push('⋯ '+(run-3)+' 行相同');run=0;}
for(k=0;k<t.length;k++){
cur=t[k];
if(cur[0]===0){run++;if(run<=3)out.push(esc(cur[1]));continue;}
flush();
out.push('<span class="dl-'+(cur[0]>0?'add':'del')+'">'+(cur[0]>0?'+ ':'- ')+esc(cur[1])+'</span>');
}
flush();
return out.join('\n');
}
function setTab(tab,updateHash){
state.tab=tab;
QA('[data-tab]').forEach(function(el){
var on=el.getAttribute('data-tab')===tab;
el.classList.toggle('is-active',on);
el.setAttribute('aria-selected',on?'true':'false'); // UIX-3：tab 选中态对读屏可见
});
QA('[data-panel]').forEach(function(el){
el.hidden=el.getAttribute('data-panel')!==tab;
});
if(updateHash&&state.detailPath){
location.hash='#article?path='+encodeURIComponent(state.detailPath)+'&tab='+tab;
}
if(tab==='preview') mountPreview();
if(tab==='review') mountReview();
if(tab==='actions') renderActions(state.detailPath);
if(tab==='edit') mountEdit();
if(tab==='variants') renderVariants();
}
function mountPreview(){
var detail=state.detail||{};
var stage=$('preview-stage');
var empty=$('preview-empty');
var frame=$('preview-frame');
if(detail.renderable===false){
stage.hidden=true;
empty.hidden=false;
frame.removeAttribute('src');
return;
}
stage.hidden=false;
empty.hidden=true;
var src=htmlPath(state.detailPath);
if(frame.getAttribute('src')!==src) frame.src=src;
}
function mountReview(){
var detail=state.detail||{};
var stage=$('review-stage');
var empty=$('review-empty');
if(!detail.note){
stage.hidden=true;
empty.hidden=false;
return;
}
stage.hidden=false;
empty.hidden=true;
var frame=$('review-frame');
if(!frame){
frame=document.createElement('iframe');
frame.id='review-frame';
frame.className='review-frame';
frame.title='对照审阅';
stage.appendChild(frame);
}
var src=reviewPath(state.detailPath,detail.note);
if(frame.getAttribute('src')!==src) frame.src=src;
}
var RENDER_RE=/渲染|外链|外域|协议|宽高比|禁止标签|mermaid|LaTeX|图片来源/;
function fixCmd(m){
if(/摘要/.test(m))return 'musespiral digest <文章>';
if(/主题/.test(m))return 'musespiral theme list';
return 'musespiral check --render <文章>';
}
function renderChecks(detail){
var checks=Array.isArray(detail.checks)?detail.checks:[],counts={PASS:0,WARN:0,FAIL:0},i;
checks.forEach(function(x){var l=x&&(x.level||x[0]);if(counts[l]!=null)counts[l]+=1;});
var msgs=function(l){return checks.filter(function(x){return (x.level||x[0])===l;}).map(function(x){return String((x.message!=null?x.message:x[1])||'');}).join('；');};
var wt=counts.WARN?' title="'+esc(msgs('WARN'))+'"':'',ft=counts.FAIL?' title="'+esc(msgs('FAIL'))+'"':'';
$('check-summary').innerHTML='<span class="badge pass">PASS '+counts.PASS+Z+'<span class="badge warn"'+wt+'>WARN '+counts.WARN+Z+'<span class="badge fail"'+ft+'>FAIL '+counts.FAIL+Z;
function row(x){var raw=x&&(x.level||x[0]),l=counts[raw]!=null?raw:'PASS',lo=l.toLowerCase(),m=String((x&&x.message!=null?x.message:x[1])||'');
return '<div class="check-row '+lo+'"><span class="badge '+lo+'">'+esc(l)+Z+'<span class="check-msg">'+esc(m)+(l==='PASS'?'':'<code class="fix-cmd">$ '+fixCmd(m)+'</code>')+'</span></div>';}
function grp(label,items,body){
if(!(items&&items.length)&&!body)return '';
return '<details class="action-group"'+(items&&items.some(function(x){return (x.level||x[0])==='FAIL';})?' open':'')+'><summary style="cursor:pointer;font-weight:600">'+label+'</summary>'+
(items?items.map(row).join(''):body)+'</details>';}
var g={format:[],render:[]};
for(i=0;i<checks.length;i++)g[RENDER_RE.test(String(checks[i].message||''))?'render':'format'].push(checks[i]);
var note=(state.detail&&state.detail.note)||'';
var fc='<div class="check-row"><span class="badge queued">CLI</span><span class="check-msg">AI 事实自检不在 check 门内（只标记不否决）：列出无原笔记依据的增量事实。<code class="fix-cmd">$ musespiral ai check-facts <文章> --note '+(note?esc(note):'&lt;原笔记&gt;')+'</code></span></div>';
$('check-list').innerHTML=checks.length?grp('格式检查',g.format)+grp('渲染检查',g.render)+grp('事实自检',null,fc):'<div class="check-empty">无检查项</div>';
}
function renderActions(path){
var manual=state.platforms.filter(function(p){ return p.route==='manual_pack';});
var official=state.platforms.filter(function(p){ return p.route==='official_api';});
var item=state.articles.find(function(a){ return a.path===path;});
var flags=(item&&item.flags)||{};
var variants=Array.isArray(flags.variants)?flags.variants :[];
$('adapt-buttons').innerHTML=manual.length?manual.map(function(p){
return '<button class="btn" data-adapt="'+esc(p.code)+'">' +
'生成 <span class="platform-code">'+esc(p.code)+'</span> 变体</button>';
}).join(''):'<span class="action-hint">暂无 manual_pack 平台</span>';
$('pack-platform').innerHTML=manual.map(function(p){
return '<option value="'+esc(p.code)+'">'+esc(p.name||p.code)+'（'+esc(p.code)+'）</option>';
}).join('');
$('pack-variant').innerHTML='<option value="">无（用原文）</option>'+variants.map(function(code){
var rel=variantFile(path,code);
return '<option value="'+esc(rel)+'">'+esc(code)+' 变体</option>';
}).join('');
PB.disabled=official.length===0;
PB.title=official.length?'仅创建公众号草稿，不提交发表':'无 official_api 平台';
var g=pubGaps(),gb=$('actions-gap');gb.hidden=!g;gb.textContent=g;
pPaint();
}
// E1 金路径：POST /api/action/pipeline → job kind=pipeline（步级进度见任务中心）
// pPaint 真身已迁 core 段共享块（消费点含 core waitForJob 轮询——A 类专项 2026-11-20）
function pRun(){
var path=state.detailPath;
if(!path)return toast('先打开一篇文章',WN);
state.pipe={steps:[]};pPaint();
postJSON('/api/action/pipeline',{article:path,confirm:true}).then(function(d){
if(d.job_id){$('jobs-menu').hidden=false;jobsPollStop();jobsPoll=setInterval(refreshJobs,3000)}
return trackJob(d.job_id).then(function(j){
if(j){state.pipe={steps:((j.result||{}).steps||[]),err:j.status==='failed'?j.error:''};pPaint()}})}).catch(function(e){state.pipe.err=e.message;pPaint();toast('金路径提交失败：'+e.message,ER)});
}
// 物料缺口角标（沉淀·22）：封面/FAIL/凭证三前置计数，修复即清零；只标记不锁死
function pubGaps(){
var d=state.detail||{},n=0;
if(!(d.meta&&d.meta.cover))n++;
if(Array.isArray(d.checks)&&d.checks.some(function(x){return (x.level||x[0])==='FAIL';}))n++;
if(state.credsOK===false)n++;
return n;
}
function variantLocal(t){
var n=t.replace(/\s+/g,'').length,m=t.match(/^#\s+(.+)$/m),h=m?m[1].trim():'';
if(!h)return{title:'',chars:n,ok:false,msg:'无 # 一级标题'};
if(n<30)return{title:h,chars:n,ok:false,msg:'正文过短（'+n+' 字）'};
return{title:h,chars:n,ok:true,msg:'本地校验通过'};
}
async function renderVariants(){
sbsMount();
var it=state.articles.find(function(a){return a.path===state.detailPath;}),vs=(it&&it.flags&&it.flags.variants)||[],i,rel;
for(i=0;i<vs.length;i++){rel=variantFile(state.detailPath,vs[i]);
if(state.vraw[rel])continue;
try{var b=await getJSON(`/api/article/body?path=${encodeURIComponent(rel)}`);
state.vraw[rel]=Object.assign({content:b.content||''},variantLocal(b.content||''));}
catch(err){state.vraw[rel]={content:'',title:'',chars:0,ok:false,msg:'读取失败'};}}
var html=vs.map(function(c){
rel=variantFile(state.detailPath,c);var v=state.vraw[rel]||{content:'',chars:null,ok:false,msg:'读取中…'};
return '<div class="card vcard"><div class="vcard-head"><span class="platform-code">'+esc(c)+'</span><b>'+esc(PN[c]||c)+'</b>'+
'<span class="badge '+(v.ok?'pass':'warn')+'">'+(v.ok?I('check'):I('warning'))+' '+esc(v.msg)+Z+'</div>'+
'<h4 class="vcard-title">'+esc(v.title||'…')+'</h4>'+
'<div class="vcard-meta">'+(v.chars!=null?v.chars:'…')+' 字</div>'+
'<div class="vcard-ops"><button class="btn" data-vview="'+esc(rel)+'">预览全文</button>'+
'<button class="btn" data-vadapt="'+esc(c)+'">重新生成</button>'+
'<button class="btn" data-vpack="'+esc(c)+'" data-vrel="'+esc(rel)+'">生成物料包</button></div></div>';}).join('');
$('variant-grid').innerHTML=html||'<div class="empty" style="grid-column:1/-1"><div class="empty-icon" data-icon="star">'+I('star')+'</div><div class="empty-title">还没有平台变体</div><div class="empty-text">一稿多态：用下方按钮生成 xhs / channels / douyin / zhihu 四平台改写稿。</div></div>';
}
function openViewer(rel){
function show(v){$('viewer-title').textContent=rel;$('viewer-body').textContent=v||'（空文件）';$('viewer').hidden=false;}
var hit=state.vraw[rel];
if(hit){show(hit.content);return;}
getJSON(`/api/article/body?path=${encodeURIComponent(rel)}`).then(function(b){state.vraw[rel]=Object.assign({content:b.content||''},variantLocal(b.content||''));show(b.content);})
.catch(function(err){toast('读取变体失败：'+err.message,ER);});
}
/*__SBS_BEGIN__*/
// N-5 多平台并排预览·纯函数区（tests/test_pack_preview.py 的 node 行为断言
// 抽取本段执行，不触 DOM、不引用本段外符号）。并排双栏：左=原文（只读
// markdown 渲染），右=平台版本（物料包 payload.md）；差异高亮用服务端
// core/version/diff.py 的红删绿增 HTML（前端只贴 html，不另写 diff）。
function sbsEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
// 平台下拉项：manual_pack 平台全集；packs 为 {code:available} 映射，有包标 ✓
function sbsOptions(platforms,packs){
var map=packs||{};
return (Array.isArray(platforms)?platforms:[]).map(function(p){
var code=String(p&&p.code||p||''),name=String((p&&p.name)||code||''),has=map[code]===true;
return '<option value="'+sbsEsc(code)+'">'+sbsEsc(name)+'（'+sbsEsc(code)+'）'+(has?' ✓':'')+'</option>';
}).join('');
}
// 右栏头部物料字段 chips（payload 头部：标题/描述/话题）
function sbsMeta(payload){
if(!payload)return '';
var bits=[];
if(payload.title)bits.push('<span class="tag">标题 '+sbsEsc(payload.title)+'</span>');
if(payload.desc)bits.push('<span class="tag">描述 '+sbsEsc(payload.desc)+'</span>');
if(Array.isArray(payload.tags)&&payload.tags.length)bits.push('<span class="tag accent">'+payload.tags.map(function(t){return '#'+sbsEsc(t);}).join(' ')+'</span>');
return bits.length?'<div class="sbs-meta">'+bits.join('')+'</div>':'';
}
function sbsEmptyNote(note){return '<div class="empty sbs-empty"><div class="empty-title">尚未适配</div><div class="empty-text">'+sbsEsc(note||'该平台还没有物料包——在「操作」页「生成物料包」后回来看并排对照。')+'</div></div>';}
/*__SBS_END__*/
var SBS={path:'',packs:null,platform:''};
// 并排区骨架动态注入（壳贴顶 49,152B 硬锚仅余 28B——AI-3 批「新 UI 全 JS
// 动态渲染、壳零增量」同款纪律；注入一次，幂等守卫 sbs-grid 存在性）
// ↑ 注释内不写字符串字面量（test_real_sources_string_spans_preserved：
//   压缩剥注释后 naive 提取器会把注释里的引号串计入 before 导致对拍红）
function sbsEnsure(){
if($('sbs-grid'))return;
var panel=$('panel-variants');
if(!panel)return;
panel.insertAdjacentHTML('afterbegin',
'<div class="action-group sbs-group"><h3 class="action-title">平台适配预览</h3>'+
'<div class="action-row"><label class="field">平台 <select class="input" id="sbs-platform"></select></label>'+
'<span class="action-hint" id="sbs-note">并排对照：左原文 · 右平台版本（物料包 payload 正文）；下方红删绿增为两版差异。</span></div>'+
'<div class="sbs-grid" id="sbs-grid">'+
'<div class="sbs-col"><div class="sbs-head">原文</div><div class="sbs-body" id="sbs-left"></div></div>'+
'<div class="sbs-col"><div class="sbs-head">平台版本</div><div class="sbs-body" id="sbs-right"></div></div></div>'+
'<div id="sbs-diff"></div></div>');
$('sbs-platform').addEventListener('change',function(){sbsLoad(this.value);});
}
function sbsMount(){
sbsEnsure();
var p=state.detailPath||'';
if(!p){$('sbs-note').textContent='先打开一篇文章';return;}
if(SBS.path!==p||!SBS.packs){
SBS.path=p;SBS.packs=null;SBS.platform='';
getJSON('/api/article/pack?path='+encodeURIComponent(p)).then(function(d){
SBS.packs={};
(d.packs||[]).forEach(function(x){SBS.packs[x.platform]=x.available;});
sbsFillPlatforms();
}).catch(function(err){SBS.packs={};sbsFillPlatforms();$('sbs-note').textContent='物料包清单读取失败：'+err.message;});
}else sbsFillPlatforms();
}
function sbsFillPlatforms(){
var manual=state.platforms.filter(function(p){return p.route==='manual_pack';});
var sel=$('sbs-platform');
if(!manual.length){sel.innerHTML='';$('sbs-note').textContent='暂无 manual_pack 平台';return;}
sel.innerHTML=sbsOptions(manual,SBS.packs);
var want=SBS.platform,ok=want&&[].some.call(sel.options,function(o){return o.value===want;});
if(!ok){
var hit=manual.filter(function(p){return SBS.packs&&SBS.packs[p.code];})[0]||manual[0];
want=hit.code;SBS.platform=want;
}
sel.value=want;
sbsLoad(want);
}
function sbsLoad(platform){
if(!platform)return;
SBS.platform=platform;
var note=$('sbs-note');
$('sbs-right').innerHTML='<p class="action-hint">读取中…</p>';
$('sbs-diff').innerHTML='';
getJSON('/api/article/pack?path='+encodeURIComponent(state.detailPath)+'&platform='+encodeURIComponent(platform)).then(function(d){
if(SBS.platform!==platform)return;  // 迟到响应丢弃（用户已切走）
sbsRender(d);
}).catch(function(err){note.textContent='读取失败：'+err.message;});
}
function sbsRender(d){
var diff=d.diff||{};
$('sbs-left').innerHTML=mdToHtml(d.original||'',dirOf(state.detailPath));
if(!d.available){
$('sbs-right').innerHTML=sbsEmptyNote(d.note);
$('sbs-note').textContent=d.note||'尚未适配';
$('sbs-diff').innerHTML='';
return;
}
$('sbs-note').textContent='与原文差异：+'+(diff.added||0)+' 行 / −'+(diff.removed||0)+' 行'+(diff.identical?'（无变体直出，正文与原文一致）':'');
$('sbs-right').innerHTML=sbsMeta(d.payload)+'<div class="sbs-render">'+mdToHtml((d.payload||{}).body||'',dirOf(state.detailPath))+'</div>';
$('sbs-diff').innerHTML=diff.html||'';
}
// ── P1 双件（2026-11-23，契约 §3.26）：质量循环 job + 标题 A/B 三风格 ──
async function handleQualityLoop(btn){
var path=state.detailPath;
if(!path)return toast('先打开一篇文章',WN);
btn.disabled=true;
$('ql-result').innerHTML='<span class="badge queued" role="status">运行中…</span><span class="check-msg">多轮检查与 AI 修复进行中（每轮一次 AI 调用）</span>';
try{
var d=await postJSON('/api/action/quality_loop',{article:path,max_rounds:+($('ql-rounds').value||3),confirm:true});
var j=await waitForJob(d.job_id);
if(j.status==='failed'){$('ql-result').innerHTML='';toast('质量循环失败：'+(j.error||'未知错误'),ER);}
else{renderQualityResult(j.result||{});toast('质量循环完成：'+((j.result||{}).rounds||'?')+' 轮',OK);}
}catch(err){$('ql-result').innerHTML='';toast('质量循环提交失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
function renderQualityResult(r){
var checks=Array.isArray(r.final_result)?r.final_result:[],fails=checks.filter(function(x){return (x.level||x[0])==='FAIL';});
var fixes=Array.isArray(r.fixes_applied)?r.fixes_applied:[];
var h='<div class="check-row"><span class="badge '+(fails.length?'fail':'pass')+'">'+(fails.length?'仍有 '+fails.length+' 项 FAIL':'全部通过')+Z+'<span class="check-msg">'+esc(r.rounds||1)+' 轮 · '+(fixes.length?esc(fixes.join('；')):'无修复')+'</span></div>';
h+=fails.map(function(x){return '<div class="check-row fail"><span class="badge fail">'+esc(x.level||x[0])+Z+'<span class="check-msg">'+esc(x.message||x[1]||'')+'</span></div>';}).join('');
if(r.output)h+='<div class="check-row"><span class="badge queued">预览</span><span class="check-msg">修复稿 <code>'+esc(r.output)+'</code>（原文未动） <button class="btn" data-ql-diff="'+esc(r.output)+'" aria-label="查看修复对比">查看修复对比</button></span></div>';
$('ql-result').innerHTML=h;
}
async function handleQualityDiff(rel){
try{
var a=await getJSON(`/api/article/body?path=${encodeURIComponent(state.detailPath)}`);
var b=await getJSON(`/api/article/body?path=${encodeURIComponent(rel)}`);
$('viewer-title').textContent='质量循环修复对比（- 原文 / + 修复稿）';
$('viewer-body').innerHTML=diffHtml(a.content||'',b.content||'');
openDlg('viewer');
}catch(err){toast('修复稿读取失败：'+err.message,ER);}
}
async function handleTitleAB(btn){
var path=state.detailPath;
if(!path)return toast('先打开一篇文章',WN);
btn.disabled=true;
$('ab-result').innerHTML='<span class="badge queued" role="status">生成中…</span>';
try{
var d=await postJSON('/api/action/title_ab',{article:path,confirm:true});
renderTitleAB(d.variants||[],d.source||'heuristic');
}catch(err){$('ab-result').innerHTML='';toast('A/B 标题生成失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
function renderTitleAB(vs,source){
var h='<p class="action-hint">候选来源：'+(source==='ai'?'AI（三风格一次生成）':'启发式兜底（AI 未配置或调用失败，三风格仍互异）')+'——三选一；应用写回 frontmatter.title（旧版入版本池）。</p>';
h+=vs.map(function(v,i){
return '<div class="card vcard"><div class="vcard-head"><span class="platform-code">'+esc(v.style||'')+'</span><b>候选 '+(i+1)+'</b></div>'+
'<h4 class="vcard-title">'+esc(v.title||'')+'</h4>'+
'<div class="vcard-meta">'+esc(v.rationale||'')+'</div>'+
(v.cover_spec?'<div class="vcard-meta">封面 spec：'+esc(v.cover_spec.size||'')+' · 「'+esc(v.cover_spec.headline||'')+'」 · '+(v.cover_spec.palette||[]).join('/')+'</div>':'')+
'<div class="vcard-ops"><button class="btn btn-primary" data-ab-apply="'+esc(v.title||'')+'" aria-label="应用标题：'+esc(v.title||'')+'">应用此标题</button></div></div>';}).join('');
$('ab-result').innerHTML=h;
}
async function handleTitleApply(title,btn){
var path=state.detailPath;
if(!path)return;
var ok=await editConfirm({title:'应用 A/B 标题',message:'将「'+detailTitle(path)+'」的标题写回为：'+title+'（旧版入 .md.bak 版本池；frontmatter 整体重写，键序可能重排）',confirmText:'应用'});
if(!ok)return;
btn.disabled=true;
try{
await postJSON('/api/action/title_ab',{article:path,mode:'apply',title:title,confirm:true});
toast('标题已写回（旧版在版本池）',OK);
await refreshArticleState();renderDetail();
}catch(err){toast('写回失败：'+err.message,ER);}
finally{btn.disabled=false;}
}
function renderDetail(){
var path=state.detailPath;
var detail=state.detail||{};
var item=state.articles.find(function(a){ return a.path===path;});
state.rfail=(detail.renderable===false)?path:'';
renderSys();
$('detail-title').textContent=String(detail.title||(item&&item.title)||path||'文章');
var meta=detail.meta&&typeof detail.meta==='object'?detail.meta :{};
var bits=[];
if(meta.date) bits.push(esc(meta.date));
if(meta.author) bits.push(esc(meta.author));
if(meta.theme) bits.push('主题 '+esc(meta.theme));
if(item&&item.chars!=null) bits.push(esc(item.chars)+' 字');
bits.push('<span class="meta-path">'+esc(path)+Z);
$('detail-meta').innerHTML=bits.join('<span class="meta-dot">·</span>');
$('mark-reviewed-btn').hidden=String(detail.status||'draft')!=='draft';
renderChecks(detail);
setTab(state.tab||'edit',false);
renderActions(path);
}
// 路由入口（原 renderRoute else 兜底分支整体迁入——A 类专项：分支归视图）
VAPI.article={enter:async function(route){
var path=route.params.get('path')||'';
var tab=route.params.get('tab')||'edit';
state.tab=['edit','variants','checks','preview','review','versions','actions'].indexOf(tab)>=0?tab:'edit';
if(!path){
location.hash='#publish';
return;
}
await Promise.all([loadArticles(),loadPlatforms()]);
await loadDetail(path);
renderDetail();
if(state.pendAct){var act=state.pendAct;state.pendAct='';cpFire(act);}
},repaint:renderDetail,setTab:setTab,live:renderLive}; // live=FEAT-5 主题切换同步 live 预览（core applyTheme 经 VAPI 触达）
function cpMain(){
if(state.tab==='edit')handleSaveBody();
else if(state.tab==='variants')handleBatch($('variants-batch-btn'));
else if(state.tab==='actions')handlePublish(PB);
else toast('当前 Tab 无主操作（⌘⏎ 适用于编辑/变体/操作）',ER);}
function bindArticle(){
$('detail-tabs').addEventListener('click',function(event){
var tab=event.target.closest('[data-tab]');
if(!tab) return;
setTab(tab.getAttribute('data-tab'),true);
});
$('adapt-buttons').addEventListener('click',function(event){
var button=event.target.closest('[data-adapt]');
if(!button) return;
handleAdapt(button.getAttribute('data-adapt'),button);
});
$('variant-grid').addEventListener('click',function(event){
var view=event.target.closest('[data-vview]');
if(view){ openViewer(view.getAttribute('data-vview'));return;}
var adapt=event.target.closest('[data-vadapt]');
if(adapt){ handleAdapt(adapt.getAttribute('data-vadapt'),adapt);return;}
var pack=event.target.closest('[data-vpack]');
if(pack){ handlePack(pack.getAttribute('data-vpack'),pack.getAttribute('data-vrel')||null,pack);}
});
$('variants-batch-btn').addEventListener('click',function(){handleBatch($('variants-batch-btn'));});
$('pack-btn').addEventListener('click',function(){ handlePack($('pack-platform').value,$('pack-variant').value||null,$('pack-btn'));});
PB.addEventListener('click',function(){ handlePublish(PB);});
$('batch-btn').addEventListener('click',function(){handleBatch($('batch-btn'));});
$('pipe-run').onclick=pRun;
$('ql-btn').addEventListener('click',function(){handleQualityLoop($('ql-btn'));});
$('ql-result').addEventListener('click',function(event){
var b=event.target.closest('[data-ql-diff]');
if(b) handleQualityDiff(b.getAttribute('data-ql-diff'));
});
$('ab-btn').addEventListener('click',function(){handleTitleAB($('ab-btn'));});
$('ab-result').addEventListener('click',function(event){
var b=event.target.closest('[data-ab-apply]');
if(b) handleTitleApply(b.getAttribute('data-ab-apply'),b);
});
$('mark-reviewed-btn').addEventListener('click',handleMarkReviewed);
ES.addEventListener('click',handleSaveBody);
$('edit-annotate-toggle').addEventListener('click',toggleAnnotate);
AA.addEventListener('click',handleAnnotateAI);
$('annot-list').addEventListener('click',function(event){
var b=event.target.closest('[data-annot-del]');
if(b) handleAnnotateDel(b.getAttribute('data-annot-del'));
if(event.target.closest('[data-annot-ai]')) handleAnnotateAI();
});
$('annot-ok').addEventListener('click',submitAnnot);
$('annot-cancel').addEventListener('click',hideBubble);
$('annot-input').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'){e.preventDefault();submitAnnot();}
if(e.key==='Escape')hideBubble();
});
$('viewer-close').addEventListener('click',function(){ closeDlg('viewer');});
EA.addEventListener('input',function(){state.saveSt='dirty';syncGutter();renderHl();scheduleLive();updateStatusbar();hideBubble();});
EA.addEventListener('scroll',function(){
EG.scrollTop=EA.scrollTop;HL.scrollTop=EA.scrollTop;HL.scrollLeft=EA.scrollLeft;
if(!state.bubAt||Date.now()-state.bubAt>300)hideBubble();
if(sEcho.e>=0){var sw=Math.abs(EA.scrollTop-sEcho.e)<2;sEcho.e=-1;if(sw)return;}
syncPreview();
});
EA.addEventListener('mouseup',maybeBubble);
EA.addEventListener('keyup',function(e){if(e.shiftKey||e.key.indexOf('Arrow')===0){if(bubbleTimer)clearTimeout(bubbleTimer);bubbleTimer=setTimeout(function(){bubbleTimer=null;maybeBubble();},300);}});
// ⌘S 保存 / ⌘⏎ 主操作——原居 core bindCore，对 handleSaveBody/cpMain 的调用属
// 跨视图直呼（A 类专项 2026-11-20 下沉本块；core 剩余分支不消费 ⌘S/⌘⏎，分支体
// 与原文逐字一致）。!e.shiftKey：⌘⇧S 是编辑器内联素材搜索（editor.js E-2），
// ⌘S 才是保存——原分支不区分 Shift，⌘⇧S 会被误当保存截走（2026-11-19 E-2 实测判例）
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if((e.metaKey||e.ctrlKey)&&(e.key==='s'||e.key==='S')&&!e.shiftKey){
if(!$('view-article').hidden&&state.tab==='edit'){e.preventDefault();handleSaveBody();}
return;
}
if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){
if(!$('view-article').hidden){e.preventDefault();cpMain();}
return;
}
});
}
bindArticle();
// @endview
// @view:settings
var KBST=[];
// F1 用量条（pbar 对齐仪表盘 viz；命中不出账）——#ai-meter 属设置 DOM，随本视图
// 块持有（2026-11-19 修红：原在 @view:sources，M3 懒加载后直入 #settings 时占位
// no-op 未被覆盖 → 用量条永不渲染）
function loadAiMeter(){
var b=$('ai-meter');if(!b)return;
Promise.all([getJSON('/api/ai/usage?days=7').catch(function(e){return{err:e.message}}),getJSON('/api/ai/budget').catch(function(){return{}})]).then(function(r){
var u=r[0],bd=(r[1]||{}).budget||{},by={},mx=1,ks,o;
if(u.err)return b.innerHTML='<p class="action-hint">用量读取失败：'+esc(u.err)+'</p>';
(u.rows||[]).forEach(function(x){var k=x.task+' · '+x.profile;o=by[k]||(by[k]={c:0,h:0});
o.c+=+x.calls;o.h+=+x.hits});
ks=Object.keys(by);ks.forEach(function(k){mx=Math.max(mx,by[k].c)});
b.innerHTML=(ks.length?ks.slice(0,8).map(function(k){o=by[k];
return '<div class="pbar"><span class="pbar-name">'+esc(k)+'</span><span class="pbar-track"><span class="pbar-fill" style="width:'+Math.round(o.c/mx*100)+'%"></span></span><span class="pbar-n">'+o.c+' 次</span></div>'}).join(''):'<p class="action-hint">近 7 日无 AI 调用</p>')+
'<p class="action-hint">日预算 '+(bd.daily_calls||'—')+' 次 / '+(bd.daily_out_chars||'—')+' 字</p>';
});}
// F1 media 验证（pbar 对齐仪表盘 viz；命中不出账）——#media-verify-btn/#media-dot/
// #media-txt 属设置 DOM，唯一消费点=bindSettings 一行绑定，随本视图块持有
// （2026-11-20 修红：原在 @view:sources，M3 懒加载后直入 #settings 时本块未注入，
// onclick 捕获的是占位 no-op → 验证按钮无反应、健康灯永不更新）
var MEDH={ok:'正常',down:'不可达'};
function mediaVerify(){
var b=$('media-verify-btn');b.disabled=true;
postJSON('/api/action/media_verify',{confirm:true}).then(function(d){
var h=d.health||{},s=h.status,t=MEDH[s]||'未配置';
$('media-dot').className='health-dot '+({ok:'ok',down:'down'}[s]||'unknown');
$('media-txt').textContent='文生图：'+t+(h.detail?'（'+h.detail+'）':'');
toast('文生图连接探测：'+t,s==='ok'?OK:WN)}).catch(function(e){toast('验证失败：'+e.message,ER)}).then(function(){b.disabled=false});
}
function kbTable(){
$('kb-sources-body').innerHTML=KBST.length?KBST.map(function(s,i){var st=s.exists==null?'':(s.exists?I('check'):I('warning')+'缺失');
return '<tr><td>'+esc(s.key)+'</td><td>'+esc(s.label)+'</td><td>'+esc(s.relpath)+'</td><td>'+st+'</td><td><button class="btn" data-kb-del="'+i+'">删</button></td></tr>';}).join(''):'<tr><td colspan="5" class="action-hint">无子源（扫描回退根本身）</td></tr>';
}
// aiPost/handleKbSave 自 core 段迁入（唯一消费点=bindSettings；A 类专项 2026-11-20）
async function aiPost(body,msg){
try{await postJSON('/api/settings/ai',Object.assign({confirm:true},body));toast(msg+'（下次 AI 调用生效）',OK);await loadSettings();renderSettings();}
catch(err){toast('保存失败：'+err.message,ER);}
}
async function handleKbSave(){
var root=$('kb-root-input').value.trim();
if(!root){toast('KB 根不能为空',ER);return;}
var sources=KBST.map(function(s){return {key:s.key,label:s.label,relpath:s.relpath};});
var ok=await askConfirm({title:'保存 KB 设置',message:'写 kb_sources.json（放宽扫描边界）：'+root+' · 源 '+sources.length+' 项。',confirmText:'保存'});if(!ok)return;
try{await postJSON('/api/settings/kb',{root:root,sources:sources,confirm:true});toast('KB 设置已保存——素材页下次采集起用新边界',OK);await loadSettings();renderCollRail();renderSettings();}
catch(err){toast('保存失败：'+err.message,ER);}
}
function pickPrompt(){
var t=$('prompt-task-select').value,ov=((state.settings&&state.settings.ai&&state.settings.ai.prompts)||{}).overrides||{};
$('prompt-area').value=ov[t]||'';
$('prompt-state').textContent=ov[t]?'已覆盖内置缺省（保存即整体覆盖）':'未覆盖（用内置缺省）；填写即建 prompts.d/'+t+'.txt';
}
function renderSettings(){
var st=state.settings;
if(!st){$('settings-sub').textContent='设置读取失败';return;}
var kb=st.kb||{},ai=st.ai||{},ap=ai.profiles||{},names=Object.keys(ap.profiles||{});
$('kb-root-input').value=kb.root||'';
$('kb-origin').innerHTML='根：'+(kb.root?(I(kb.root_exists?'check':'warning')+' '+esc(kb.root)):'未配置')+' · 源：'+(kb.origin==='kb_sources.json'?'kb_sources.json':kb.origin==='kb_root.txt'?'kb_root.txt 回退+内置默认源表':'内置默认源表');
KBST=(kb.sources||[]).slice();kbTable();
// 凭据三态（沉淀·24）：本地端点无 key=免凭据；key 永不回显（UI 层不渲染，非打码）
var KC={ok:'<span class="badge pass">已配置</span>',miss:'<span class="badge warn">未配置</span>',free:'<span class="badge queued">免凭据·本地</span>'};
$('ai-profiles-body').innerHTML=names.length?names.map(function(n){var p=ap.profiles[n];
var lk=p.has_key?'ok':(/localhost|127\.0\.0\.1/i.test(p.base_url||'')?'free':'miss');
return '<tr><td>'+esc(n)+(n===ap.default?' ★':'')+'</td><td>'+esc(p.base_url)+'</td><td>'+esc(p.model)+'</td><td>'+KC[lk]+'</td><td><button class="btn" data-ai-edit="'+esc(n)+'">编辑</button></td></tr>';}).join(''):'<tr><td colspan="5" class="action-hint">尚无档案（musespiral ai config）</td></tr>';
$('ai-default-select').innerHTML=names.map(function(n){return '<option'+(n===ap.default?' selected':'')+'>'+esc(n)+'</option>';}).join('');
$('ai-route-task').innerHTML=(ap.tasks||[]).map(function(t){var r=(ap.routing||{})[t];return '<option>'+esc(t)+(r?' → '+esc(r):'')+'</option>';}).join('');
$('ai-route-profile').innerHTML=names.map(function(n){return '<option>'+esc(n)+'</option>';}).join('');
var sty=ai.style||{};
$('style-habits').textContent='style_samples.md · habits '+(sty.habits_count||0)+' 条';
$('style-area').value=sty.content||'';
var pr=ai.prompts||{},ov=pr.overrides||{};
$('prompt-task-select').innerHTML=(pr.tasks||[]).map(function(t){return '<option value="'+esc(t)+'">'+esc(t)+'</option>';}).join('');
pickPrompt();
}
// 路由入口（原 renderRoute settings 分支整体迁入——A 类专项：分支归视图）
VAPI.settings={enter:async function(){
await loadSettings();
renderSettings();
loadAiMeter();
}};
function bindSettings(){
$('cred-verify-btn').addEventListener('click',async function(){
var b=this;b.disabled=true;
try{
var h=await getJSON('/api/health'),c=h.credentials||{},a=h.ai||{},im=h.imagehost||{};
applyHealth(h);
await loadSettings();renderSettings();
toast('验证完成：凭证 '+(c.error?'不可读':(c.configured?c.accounts+' 账号':'未配置'))+' · AI '+(a.configured?(a.model||'已配置'):'未配置')+' · 图床 '+(im.default||'local')+' · 磁盘 '+(h.disk_free_gb||'—')+' GB',OK);
}catch(err){toast('验证失败：'+err.message,ER);}
finally{b.disabled=false;}
});
$('media-verify-btn').onclick=mediaVerify;
$('kb-save-btn').addEventListener('click',handleKbSave);
$('kb-add-btn').addEventListener('click',function(){
var k=$('kb-add-key').value.trim(),l=$('kb-add-label').value.trim(),r=$('kb-add-rel').value.trim();
if(!k||!l||!r){toast('key/标签/路径均必填',ER);return;}
if(KBST.some(function(s){return s.key===k;})){toast('key 已存在：'+k,ER);return;}
KBST.push({key:k,label:l,relpath:r});
$('kb-add-key').value=$('kb-add-label').value=$('kb-add-rel').value='';kbTable();
});
$('kb-sources-body').addEventListener('click',function(e){
var b=e.target.closest('[data-kb-del]');
if(b){KBST.splice(+b.getAttribute('data-kb-del'),1);kbTable();}
});
$('ai-profiles-body').addEventListener('click',function(e){
var b=e.target.closest('[data-ai-edit]');if(!b)return;
var n=b.getAttribute('data-ai-edit'),p=((state.settings.ai.profiles.profiles)||{})[n]||{};
$('ai-p-name').value=n;$('ai-p-url').value=p.base_url||'';$('ai-p-model').value=p.model||'';$('ai-p-key').value='';
$('ai-p-key').placeholder=p.has_key?'已配置，输入以更换':'API Key（本地可空）';
});
$('ai-p-save').addEventListener('click',function(){
var n=$('ai-p-name').value.trim();if(!n){toast('档案名不能为空',ER);return;}
var b={action:'save_profile',name:n,base_url:$('ai-p-url').value.trim(),model:$('ai-p-model').value.trim()};
if($('ai-p-key').value)b.api_key=$('ai-p-key').value;
aiPost(b,'档案已保存：'+n);
});
$('ai-default-save').addEventListener('click',function(){aiPost({action:'set_default',name:$('ai-default-select').value},'默认档案已设置');});
$('ai-route-save').addEventListener('click',function(){aiPost({action:'set_route',task:$('ai-route-task').value,profile:$('ai-route-profile').value},'路由已设置');});
$('style-save').addEventListener('click',function(){aiPost({action:'save_style',content:$('style-area').value},'风格资产已保存');});
$('prompt-task-select').addEventListener('change',pickPrompt);
$('prompt-save').addEventListener('click',function(){aiPost({action:'save_prompt',task:$('prompt-task-select').value,content:$('prompt-area').value},'覆盖已保存');});
}
bindSettings();
// @endview

// @view:plugins
// 插件系统·清单/详情/新建三子态（插件系统设计 §八 PL-3；评审 §2.3 拆分：画布/
// 运行/segments 归 plugin-run 块）。manifest 即 UI（§8.2 硬纪律）：详情页 I/O 表、
// manifest 展示、权限、依赖全部从 manifest 渲染——本文件零插件 id/名称硬编码。
// hash 子状态：#plugins 清单 / ?d=<id> 详情 / ?new=1 新建（&seed=<id> 预选首插件）。
var PG={list:[],skipped:[],byId:{},filter:'',mode:'list',check:{},err:''};
// 平台权限词表释义（core.plugins.manifest.PERMISSIONS §7.1——平台词表非插件文案）
var PG_PERM={'fs:read':'读输入文件（workspace 与 inputs 路径）','fs:write:tmp':'写临时目录','fs:write:output':'写产物目录','net:none':'零外联','net:http':'允许 HTTP 出网（安装时需明确批准）','exec:subprocess':'可启动子进程（安装时需明确批准）'};
function pgKINDS(){return{source:'输入',process:'处理',sink:'输出'};}
function pgKindIcon(kind){return kind==='source'?'download':kind==='sink'?'publish':'settings';}
function pgName(m){return (m&&m.name&&m.name.zh)||(m&&m.id)||'';}
function pgDesc(m){return (m&&m.description&&m.description.zh)||'';}
// UIX #5：卡片描述只留用户价值句——manifest desc 倾倒工程术语（chat_policed(task=…)、
// MISSING_DEP、JSON anchors、文件路径等）对非工程用户是噪音。取首个句末/换行前的
// 短句，剥代码片段与括号术语，限长；详情页 pgDesc 原文照旧（工程用户可看全）。
function pgDescBrief(m){
var s=pgDesc(m).replace(/\r/g,'');
s=s.split(/[\n。；;]/)[0]||s;                       // 首句（价值概述通常在第一句）
s=s.replace(/`[^`]*`/g,'').replace(/\[[^\]]*\]\([^)]*\)/g,''); // 代码片段+markdown 链接
s=s.replace(/\([^)]*(task=|JSON|dep|node_id|v1\s*契约)[^)]*\)/gi,''); // 纯工程术语括号
s=s.replace(/\b(MISSING_DEP|JSON\s+anchors?|frontmatter|artifacts?|config\.[a-z_.]+)\b/gi,'');
s=s.replace(/\s{2,}/g,' ').replace(/^[，,、\s]+|[，,、\s]+$/g,'').trim();
if(s.length>52)s=s.slice(0,52).replace(/[，,、\s]\S*$/,'')+'…';
return s||'见详情';}
function pgIoSummary(m){
var a=Object.keys((m&&m.inputs)||{}).map(function(k){return m.inputs[k].type;});
var b=Object.keys((m&&m.outputs)||{}).map(function(k){return m.outputs[k].type;});
return (a.join('+')||'—')+' → '+(b.join('+')||'—');
}
function pgIoRows(m){
var rows=[];
Object.keys((m&&m.inputs)||{}).forEach(function(k){var p=m.inputs[k]||{};rows.push(['in',k,p.type||'',p.required===false?'输入 · 可选':'输入 · 必需']);});
Object.keys((m&&m.outputs)||{}).forEach(function(k){rows.push(['out',k,(m.outputs[k]||{}).type||'','输出']);});
return rows;
}
// manifest 语法着色（行级解析；esc 先行——注入面只过 esc，本函数只加 span）
function pgHi(json){
return esc(json).split('\n').map(function(l){
var m=l.match(/^(\s*)&quot;([^&]+)&quot;:(\s)(.*)$/);
if(!m)return l;
var v=m[4],s=v.match(/^(&quot;.*&quot;)(,?)$/);
return m[1]+'<span class="pg-k">&quot;'+m[2]+'&quot;</span>:'+m[3]+(s?'<span class="pg-s">'+s[1]+'</span>'+s[2]:v);
}).join('\n');
}
// 契约兼容判定（core.plugins.types.types_compatible 前端镜像：完全相等才兼容）
function pgTypeCompat(a,b){return typeof a==='string'&&typeof b==='string'&&a===b;}
// 流水线静态契约校验（core.plugins.manifest.validate_pipeline_contract 前端镜像，
// 四规则同语义：唯一非空 id / source 仅开头 / sink 仅结尾 / 必需输入端口须由更早
// 节点供给）——新建页保存前标红阻止，非法顺序不发生任何请求（评审 §2.1「契约校验前置」）
function pgValidateChain(nodes){
var errs=[],seen={},i,n=nodes?nodes.length:0;
if(!n){errs.push('流水线至少需要一个节点');return errs;}
for(i=0;i<n;i++){
var id=nodes[i].id;
if(!id||!String(id).trim())errs.push('节点['+i+'] 缺少非空 id');
else if(seen[id])errs.push('节点 id 重复: '+id+'（workspace 按 node_id 存取，重复会互相覆盖）');
else seen[id]=1;
}
for(i=0;i<n;i++){
var m=nodes[i].plugin;
if(!m)continue;
var label=(nodes[i].id&&nodes[i].id!==m.id)?nodes[i].id+'（'+m.id+'）':(nodes[i].id||('#'+i));
if(m.kind==='source'&&i!==0)errs.push('节点['+i+'] '+label+' 是 source，只能位于流水线开头');
if(m.kind==='sink'&&i!==n-1)errs.push('节点['+i+'] '+label+' 是 sink，只能位于流水线结尾');
if(i===0)continue;
var outs=[];
for(var j=0;j<i;j++){var pm=nodes[j]&&nodes[j].plugin;if(!pm)continue;Object.keys(pm.outputs||{}).forEach(function(k){outs.push(pm.outputs[k].type);});}
Object.keys(m.inputs||{}).forEach(function(pk){
var p=m.inputs[pk]||{};
if(p.required===false)return;
var fed=false;
for(var t=0;t<outs.length;t++){if(pgTypeCompat(outs[t],p.type)){fed=true;break;}}
if(!fed)errs.push('节点['+i+'] '+label+' 必需输入端口 '+pk+'（type='+p.type+'）未被任何更早节点的输出供给');
});
}
return errs;
}
async function pgLoad(force){
if(PG.list.length&&!force)return;
try{
var d=await getJSON('/api/plugins');
PG.list=d.plugins||[];PG.skipped=d.skipped||[];PG.byId={};PG.err='';
PG.list.forEach(function(it){PG.byId[it.manifest.id]=it.manifest;});
}catch(err){PG.list=[];PG.skipped=[];PG.byId={};PG.err=err.message;toast('加载插件清单失败：'+err.message,ER);}
}
async function pgCheck(id){
try{PG.check[id]=await getJSON(`/api/plugins/${encodeURIComponent(id)}/check`);}
catch(err){PG.check[id]={deps:[],err:err.message};toast('依赖检查端点未接驳（后端批）：'+err.message,WN);}
pgRenderDetail(id);
}
function pgShow(mode){
PG.mode=mode;
$('pg-list-wrap').hidden=mode!=='list';
$('pg-detail-wrap').hidden=mode!=='detail';
$('pg-build-wrap').hidden=mode!=='build';
window.scrollTo({top:0,behavior:'instant'});
}
function pgChips(){
var ks=pgKINDS(),c={all:PG.list.length};
PG.list.forEach(function(it){var k=it.manifest.kind;c[k]=(c[k]||0)+1;});
$('pg-kinds').innerHTML=['all','source','process','sink'].map(function(k){
var v=k==='all'?'':k;
return '<button class="chip'+(PG.filter===v?' is-active':'')+'" data-pgkind="'+v+'">'+(k==='all'?'全部':ks[k])+'<span class="chip-count">'+(c[k]||0)+'</span></button>';
}).join('');
}
function pgCard(it){
var m=it.manifest,ks=pgKINDS(),ic=pgKindIcon(m.kind);
return '<article class="pg-card" data-pgopen="'+esc(m.id)+'" tabindex="0" role="button">'+
'<div class="pg-top"><span class="pg-glyph">'+I(ic)+'</span>'+
'<span class="pg-topmain"><span class="pg-name">'+esc(pgName(m))+'</span><span class="pg-vendor">'+esc(m.id)+' · v'+esc(m.version)+'</span>'+
(it.source==='user'?'<span class="pg-src user">用户装</span>':'<span class="pg-src">内置</span>')+'</span>'+
'<span class="badge '+(m.uses_ai?'running':'pass')+'" title="'+(m.uses_ai?'声明 uses_ai：AI 由平台受管注入':'契约门全过')+'">'+(m.uses_ai?'AI':'就绪')+'</span></div>'+
'<p class="pg-desc" title="'+esc(pgDesc(m))+'">'+esc(pgDescBrief(m))+'</p>'+
'<div class="pg-foot"><span class="badge queued">'+esc(ks[m.kind]||m.kind)+'</span><span class="pg-io">'+esc(pgIoSummary(m))+'</span></div></article>';
}
function pgRenderList(){
pgChips();
var L=PG.list.filter(function(it){return !PG.filter||it.manifest.kind===PG.filter;});
// 复核 medium-5：有跳过目录时不再宣称「全部过契约门」（自相矛盾）；加载失败
// 不误导成「用户没放插件」——空态文本区分 服务错误 vs 真空态。
$('pg-sub').textContent='已装 '+PG.list.length+' 个'+(PG.skipped.length?' · 跳过 '+PG.skipped.length+' 个（未过契约门）':' · 全部过契约门');
$('pg-grid').innerHTML=L.map(pgCard).join('');
$('pg-grid').hidden=!L.length&&!!PG.list.length;
var empty=$('pg-empty');
empty.hidden=PG.list.length>0;
if(!PG.list.length)$('pg-empty-text').textContent=PG.err?('插件清单加载失败：'+PG.err+'（服务异常或端点不可达——已放目录未被读取，请稍后重试）'):(PG.skipped.length?('有 '+PG.skipped.length+' 个目录未过契约门：'+PG.skipped.map(function(s){return (String(s.dir||'').split('/').pop()||'（目录）')+'——'+s.reason;}).join('；')):'把插件目录放进插件区（manifest.json 六必需键齐全）后点「重扫」；详情页由 manifest 自动生成，无手写文案。');
}
function pgSrow(k,v){return '<div class="pg-srow"><span>'+esc(k)+'</span><span>'+v+'</span></div>';}
function pgRenderDetail(id){
var m=PG.byId[id];
if(!m){pgRenderList();pgShow('list');return;}
var ks=pgKINDS(),ic=pgKindIcon(m.kind);
var ioRows=pgIoRows(m).map(function(r){
return '<tr><td>'+esc(r[1])+'</td><td>'+esc(r[2])+'</td><td>'+esc(r[3])+'</td></tr>';
}).join('');
var caps=(m.permissions||[]).map(function(p){
return '<div class="pg-cap"><span class="pg-cap-i">'+I(PG_PERM[p]?'check':'warning')+'</span><span>'+esc(p)+(PG_PERM[p]?'（'+esc(PG_PERM[p])+'）':'')+'</span></div>';
}).join('')||'<p class="action-hint">无权限声明</p>';
var deps=PG.check[id],depsList=deps&&deps.deps?deps.deps:null;
var depRows=(m.requires||[]).map(function(r){
var d=depsList?depsList.filter(function(x){return r===x.cmd||r.indexOf(x.cmd)===0;})[0]:null;
var st=!depsList?['queued','待检查']:(d&&d.found?['pass',esc(d.path||'已就绪')]:['fail','未找到']);
return '<div class="pg-srow"><span>'+esc(r)+'</span><span class="badge '+st[0]+'">'+st[1]+'</span></div>';
}).join('')||'<p class="action-hint">无外部依赖声明</p>';
$('pg-detail-wrap').innerHTML='<div class="pg-detail"><div class="pg-detailmain">'+
'<div class="card pg-hero"><span class="pg-hero-glyph">'+I(ic)+'</span><div class="pg-heromain"><h1 class="pg-h1">'+esc(pgName(m))+'</h1><div class="pg-vendor">'+esc(m.id)+' · v'+esc(m.version)+'</div><p class="pg-herodesc">'+esc(pgDesc(m)||'（manifest 未带描述）')+'</p></div><span class="badge queued">'+esc(ks[m.kind]||m.kind)+(m.uses_ai?' · AI':'')+'</span></div>'+
'<div class="pg-sec"><h2 class="pg-sec-title">输入 / 输出契约</h2><table class="pg-table"><thead><tr><th>名称</th><th>类型</th><th>说明</th></tr></thead><tbody>'+(ioRows||'<tr><td colspan="3" class="action-hint">无端口</td></tr>')+'</tbody></table></div>'+
'<div class="pg-sec"><h2 class="pg-sec-title">manifest.json</h2><div class="pg-manifest">'+pgHi(JSON.stringify(m,null,2))+'</div></div>'+
'</div><div class="pg-side">'+
'<div class="card pg-sidecard">'+pgSrow('版本',esc(m.version))+pgSrow('类型',esc(ks[m.kind]||m.kind))+pgSrow('运行时',esc((m.runtime||{}).type||'—'))+pgSrow('入口','<span class="pg-mono">'+esc((m.runtime||{}).entry||'—')+'</span>')+((m.runtime||{}).timeout_s?pgSrow('超时',esc(m.runtime.timeout_s)+'s'):'')+'</div>'+
'<div class="card pg-sidecard"><div class="pg-sidecap">权限</div><div class="pg-caps">'+caps+'</div></div>'+
'<div class="card pg-sidecard"><div class="pg-sidecap">依赖检查</div>'+depRows+'<div class="action-row"><button class="btn btn-sm" data-pgcheck="'+esc(m.id)+'">重查依赖</button></div></div>'+
'<button class="btn btn-primary pg-wide" data-pgtobuild="'+esc(m.id)+'">'+I('play')+'用它建流水线</button>'+
'<button class="btn pg-wide" data-pgback>'+I('arrow-left')+'返回清单</button>'+
'</div></div>';
pgShow('detail');
}
function pgBuildNodes(){
var src=null,sink=null,procs=[];
(PG.build.order||[]).forEach(function(id){
var m=PG.byId[id];if(!m)return;
if(m.kind==='source')src=m;else if(m.kind==='sink')sink=m;else procs.push(m);
});
var nodes=[];
// plugin 传 id 字符串而非完整 manifest——后端 plan_pipeline 按 id 从插件区
// 解析 manifest。传对象会被判「缺少 plugin（插件 id）」拒收（2026-11-20 实测：
// UI 形状 REJECTED / id 字符串 ACCEPTED）。
if(src)nodes.push({id:src.id,plugin:src.id});
procs.forEach(function(m){nodes.push({id:m.id,plugin:m.id});});
if(sink)nodes.push({id:sink.id,plugin:sink.id});
return nodes;
}
function pgSlug(name){
var s=String(name||'').toLowerCase().replace(/[^a-z0-9_-]+/g,'-').replace(/^-+|-+$/g,'');
return s||'pl-'+Date.now().toString(36);
}
function pgPickHtml(m){
var on=PG.build.order.indexOf(m.id)>=0;
return '<button class="pg-pick'+(on?' is-on':'')+'" data-pgpick="'+esc(m.id)+'"><span class="pg-pick-glyph">'+I(pgKindIcon(m.kind))+'</span><span class="pg-pick-main"><span class="pg-pick-name">'+esc(pgName(m))+'</span><span class="pg-pick-sub">'+esc(pgIoSummary(m))+'</span></span><span class="pg-check">'+I('check')+'</span></button>';
}
function pgRenderBuild(){
var steps=['source','process','sink'].map(function(k,ix){
var list=PG.list.filter(function(it){return it.manifest.kind===k;});
return '<div class="card pg-step"><div class="pg-step-head"><span class="pg-step-n">'+(ix+1)+'</span><strong>'+['选输入','选处理（可多选，按点击顺序串联）','选输出'][ix]+'</strong><span class="pg-step-kind">kind='+k+'</span></div><div class="pg-step-body"><div class="pg-picks">'+(list.length?list.map(function(it){return pgPickHtml(it.manifest);}).join(''):'<p class="action-hint">暂无此类插件——先到清单确认已装</p>')+'</div></div></div>';
}).join('');
$('pg-build-wrap').innerHTML='<div class="view-head"><div><h1 class="view-title">新建流水线</h1><p class="view-sub">按顺序挑节点：一个输入 → 若干处理 → 一个输出；保存前做静态契约校验</p></div></div>'+
'<div class="pg-steps">'+steps+'</div>'+
'<div class="pg-compat" id="pg-compat"></div>'+
'<div class="action-row pg-build-actions"><input id="pg-build-name" class="input" placeholder="流水线名称（缺省自动生成 id）"><button class="btn" data-pgcancel>取消</button><button class="btn btn-primary" id="pg-save-btn">保存并打开流水线</button></div>';
pgBuildHint();
}
function pgBuildHint(){
var errs=pgValidateChain(pgBuildNodes());
var el=$('pg-compat');
if(el){
el.className='pg-compat'+(errs.length?' pg-compat-err':'');
el.innerHTML='<span class="pg-compat-i">'+I(errs.length?'warning':'check')+'</span><span>'+(errs.length?('契约未满足：'+esc(errs.join('；'))+' —— 修正后再保存（保存按钮已锁定）。'):'契约提示：每个节点的必需输入端口须由更早节点的输出供给（类型完全相等才兼容）；把消费方放到它的供给方之前，这里会标红并阻止保存。')+'</span>';
}
var save=$('pg-save-btn');
if(save)save.disabled=errs.length>0;
return errs;
}
async function pgBuildSave(){
var errs=pgValidateChain(pgBuildNodes()); // 前端阻止：非法顺序不发生任何请求
if(errs.length){toast('契约校验未过：'+errs[0],ER);return;}
var name=$('pg-build-name').value.trim(),pid=pgSlug(name);
try{
await postJSON('/api/pipelines',{id:pid,name:name||pid,nodes:pgBuildNodes(),confirm:true});
toast('流水线已保存：'+pid,OK);
location.hash='#plugin-pipeline?id='+encodeURIComponent(pid);
}catch(err){toast('保存失败：'+err.message,ER);}
}
async function pgEnter(params){
// FEAT-5 骨架屏：清单在途时网格先画骨架卡（首次进入 PG.list 空即数据未到；重访有缓存不闪）
if(!PG.list.length&&!PG.err){$('pg-grid').innerHTML=skelCards(4);$('pg-grid').hidden=false;$('pg-empty').hidden=true;}
await pgLoad();
var d=params.get('d'),seed=params.get('seed')||'';
if(params.get('new')!=null){
PG.build={order:(seed&&PG.byId[seed])?[seed]:[]};
pgRenderBuild();pgShow('build');
return;
}
if(d)pgRenderDetail(d);
else{pgRenderList();pgShow('list');}
}
// 路由入口（原 renderRoute plugins 分支 `await pgEnter(route.params)` 迁入；
// return 透传 Promise——renderRoute 的 await 语义与原分支逐字等价）
VAPI.plugins={enter:function(route){return pgEnter(route.params);}};
function bindPlugins(){
$('pg-kinds').addEventListener('click',function(e){
var c=e.target.closest('[data-pgkind]');
if(!c)return;
PG.filter=c.getAttribute('data-pgkind')||'';
pgRenderList();
});
$('pg-grid').addEventListener('click',function(e){
var c=e.target.closest('[data-pgopen]');
if(c)location.hash='#plugins?d='+encodeURIComponent(c.getAttribute('data-pgopen'));
});
// UIX-3 键盘可达：插件卡 role=button tabindex=0 的键激活等价物（Enter/Space 开详情）
$('pg-grid').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key!=='Enter'&&e.key!==' ')return;
var c=e.target.closest('[data-pgopen]');
if(c){e.preventDefault();location.hash='#plugins?d='+encodeURIComponent(c.getAttribute('data-pgopen'));}
});
$('pg-refresh-btn').addEventListener('click',async function(){
this.disabled=true;
try{
await postJSON('/api/plugins/refresh',{confirm:true}); // 重扫（正典 §十二；v1 refresh ≡ 全量重扫）
}catch(err){toast('重扫端点未接驳（后端批），改用清单直读：'+err.message,WN);}
await pgLoad(true);
pgRenderList();
this.disabled=false;
toast('已重扫插件目录：已装 '+PG.list.length+' 个'+(PG.skipped.length?' · 跳过 '+PG.skipped.length+' 个':''),PG.skipped.length?WN:OK);
});
$('pg-new-btn').addEventListener('click',function(){location.hash='#plugins?new=1';});
// FEAT-5 空态引导：真空态「你可以做什么」双 CTA（重扫=把目录放进插件区后的确认动作）
$('pg-empty-new').addEventListener('click',function(){location.hash='#plugins?new=1';});
$('pg-empty-rescan').addEventListener('click',function(){$('pg-refresh-btn').click();});
$('pg-detail-wrap').addEventListener('click',function(e){
var b=e.target.closest('[data-pgback]');
if(b){location.hash='#plugins';return;}
var c=e.target.closest('[data-pgcheck]');
if(c){pgCheck(c.getAttribute('data-pgcheck'));return;}
var t=e.target.closest('[data-pgtobuild]');
if(t)location.hash='#plugins?new=1&seed='+encodeURIComponent(t.getAttribute('data-pgtobuild'));
});
$('pg-build-wrap').addEventListener('click',function(e){
var p=e.target.closest('[data-pgpick]');
if(p){
var id=p.getAttribute('data-pgpick'),ix=PG.build.order.indexOf(id);
if(ix>=0)PG.build.order.splice(ix,1);else PG.build.order.push(id);
pgRenderBuild();
return;
}
if(e.target.closest('[data-pgcancel]')){location.hash='#plugins';return;}
if(e.target.closest('#pg-save-btn'))pgBuildSave();
});
}
bindPlugins();
// @endview

// @view:plugin-run
// 插件系统·画布/运行/segments（PL-3 + 评审 §二 G-1/G-2/G-3）。画布两形态：
// #plugin-pipeline?id=<pid>（流水线定义+运行按钮，hash 别名入本视图）/
// #plugin-run?run=<rid>（运行态：节点进度+日志+产物）；裸 #plugin-run=流水线清单。
// G-1 低置信告警+复读标记+[▶] 听原声、G-2 逐段编辑（落 edited 副本，原始不覆盖）、
// G-3 ranges 增删——segments/audio 三端点 PL-6 批已接驳（GET/POST segments
// 编辑面 + audio 裁段原声；POST 走精确路径 /api/pipelines/runs/segments +
// body run_id，PL-3 先例）。
// AI-3 重生成（2026-11-21，裁决三件套最后一件）：每条 range 与成稿旁 [🔄]——
// POST /api/pipelines/runs/<run_id>/regenerate（step ks/aw，feedback 可选改进
// 要求）→ job 面轮询 → 重拉 runs+segments；旧版后端自动归档 outputs.vN。
var PR={mode:'',pipeId:'',def:null,runId:'',jobId:'',timer:null,t0:0,steps:[],status:'',error:'',artifacts:[],segs:null,ranges:null,edits:{},editIdx:-1,rngDel:{},rngAdd:{},pipes:[],miss:0,hasSegs:false,hasArticle:false,awStatus:'',verdicts:{},regen:0,regenOpen:''};
// hasSegs 门控（复核 medium-4）：链上存在 segments 型输出端口才挂载转写编辑面板
// ——判定数据全部来自 manifest（def.nodes 联表 / runs 读面 plugins 输出表），
// 「manifest 即 UI」同纪律；无 segments 的链（如 csv 导出）跑完不再被套上转写面板。
function prChainHasSegs(nodes){
return (nodes||[]).some(function(n){
var o=((n&&n.plugin)||{}).outputs||{};
return Object.keys(o).some(function(k){return o[k]&&o[k].type==='segments';});
});
}
// 读面回落（GET runs → PR）：verdicts 归并 + 成稿端口是否存在（AI-1 批）。
// hasArticle 双源判定：链上 manifest 输出类型（「manifest 即 UI」同纪律）或
// 读面 ai_verdicts 有 article 端口——节点被卸载时 manifest 表可能缺项，读面兜底。
function prApplyVerdicts(d,nodes){
PR.verdicts=(d&&d.ai_verdicts)||{};
PR.awStatus=prVerdict(PR.verdicts,'article');
PR.hasArticle=prChainHasArticle(nodes)||PR.awStatus!=='';
}
// 纯函数段（pgFmt..pgApplyRanges）不触 DOM——tests/test_plugins_ui.py node 直跑
function pgFmt(sec){
sec=Math.max(0,Math.floor(+sec||0));
var h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;
function p2(n){n=String(n);return n.length<2?'0'+n:n;}
return (h?h+':':'')+p2(m)+':'+p2(s);
}
function pgFmtPair(a,b){return pgFmt(a)+' – '+pgFmt(b);}
// G-1：低置信段（faster-whisper 行业信号：avg_logprob<-1.0 或 no_speech_prob>0.6）
function pgLowConf(seg){
return (seg.avg_logprob!=null&&seg.avg_logprob<-1)||(seg.no_speech_prob!=null&&seg.no_speech_prob>0.6);
}
// G-1：相邻段文本重复指数集（复读循环/疑似幻听的典型特征→标红提示）
function pgDupSet(segs){
var dup={},prev='';
(segs||[]).forEach(function(s,i){
var t=String(s.text||'').replace(/\s+/g,'');
if(t&&t===prev){dup[i]=1;dup[i-1]=1;}
prev=t;
});
return dup;
}
// G-2：编辑落「edited 副本」——原始段对象零触碰（下游 key-segment 优先读 edited）
function pgApplyEdits(segs,edits){
return (segs||[]).map(function(s,i){
return edits[i]==null?s:Object.assign({},s,{text:edits[i],edited:true});
});
}
// G-3：ranges 增删——del 是下标删除集，add 是追加数组
function pgApplyRanges(ranges,del,add){
return (ranges||[]).filter(function(r,i){return !del[i];}).concat(add||[]);
}
// ④ AI 产物裁决态徽章（AI-1 批 2026-11-21，正典=AI 产物裁决机制设计 §4.1）。
// draft=「AI 建议」琥珀（**提醒不是警告**：产物可用，只是未经人确认）/ adopted=
// 无徽章 / discarded=「已弃用」灰。纯字符串函数（不触 DOM，node 直测）。
// 注意 draft 走 .badge.warn（琥珀）而非 .badge.failed——draft 从不代表出错。
function prStBadge(st,draftText){
if(st==='draft')return '<span class="badge warn" title="AI 建议：可用但未经人工确认——可采纳为正式结果，或弃用">'+esc(draftText||'AI 建议')+'</span>';
if(st==='discarded')return '<span class="badge archived" title="已弃用：人工否定，下游跳过">已弃用</span>';
return '';
}
// ranges 标题徽章：「AI 建议 N 段」——N 只数显式 draft 态（无 status 的旧稿不计，
// 不把「未知」冒充「建议」）；全非 draft 且有态 → 「已全部裁决」。
function prRangeTitleBadge(ranges){
var n=0,any=false;
(ranges||[]).forEach(function(r){
if(!r||!r.status)return;
any=true;
if(r.status==='draft')n++;
});
if(!any)return '';
return n>0?'<span class="badge warn">AI 建议 '+n+' 段</span>':'<span class="badge pass">已全部裁决</span>';
}
// 单条裁决入口（每条旁 ✓/✗）：已终态不出重复入口（adopted 不再出「采纳」）；
// 无 status 的老稿整组不出口（不知道态就无从裁决）。🔄=重新生成（AI-3 批）
// ——**整组重跑**（后端 step 粒度），旧版自动归档可对照；busy=重生成进行中
// （调用方传 PR.regen，纯函数不读全局——node 直测同其余纯函数段）。
function prRngOps(r,i,busy){
var st=r&&r.status,ops='';
if(!st)return '';
ops+='<button class="btn btn-sm" data-rngregen title="重新生成切片（整组重跑，旧版自动归档 outputs.vN 可对照；可填改进要求）"'+(busy?' disabled':'')+'>🔄</button>';
if(st!=='adopted')ops+='<button class="btn btn-sm" data-rngadopt="'+i+'" title="采纳为正式结果">采纳</button>';
if(st!=='discarded')ops+='<button class="btn btn-sm" data-rngdiscard="'+i+'" title="弃用（下游跳过）">弃用</button>';
return ops;
}
// 成稿裁决入口（单产物整稿）：同上不出重复入口；无状态不出入口。🔄 同 AI-3。
function prAwOps(aw,busy){
var st=aw&&aw.status;
if(!st)return '';
return '<button class="btn btn-sm" data-awregen title="重新生成成稿（旧版自动归档可对照；可填改进要求，如「更简洁」）"'+(busy?' disabled':'')+'>🔄</button>'+
(st==='adopted'?'':'<button class="btn btn-sm" data-awadopt title="采纳为正式稿">采纳</button>')+
(st==='discarded'?'':'<button class="btn btn-sm" data-awdiscard title="弃用（下游跳过）">弃用</button>');
}
// 读面聚合（GET runs 的 ai_verdicts={节点id:{端口:态}} → 按端口归并成单值）：
// 全同 → 该态；有分歧 → mixed；无任何节点报该端口 → 空串（不渲染徽章——
// 老 run 的零回归路径）。纯函数，node 直测。
function prVerdict(verdicts,port){
var st='';
Object.keys(verdicts||{}).forEach(function(n){
var p=(verdicts[n]||{})[port];
if(p==null)return;
st=st&&st!==p?'mixed':p;
});
return st;
}
function prChainHasArticle(nodes){
return (nodes||[]).some(function(n){
var o=((n&&n.plugin)||{}).outputs||{};
return Object.keys(o).some(function(k){return o[k]&&o[k].type==='article';});
});
}
function prKindIcon(kind){return kind==='source'?'download':kind==='sink'?'publish':'settings';}
function p2k(n){n=String(n);return n.length<2?'0'+n:n;}
function prStop(){if(PR.timer){clearInterval(PR.timer);PR.timer=null;}}
function prReset(){
prStop();
PR.runId='';PR.jobId='';PR.steps=[];PR.status='';PR.error='';PR.artifacts=[];
PR.segs=null;PR.ranges=null;PR.edits={};PR.editIdx=-1;PR.rngDel={};PR.rngAdd={};PR.hasSegs=false;
PR.hasArticle=false;PR.awStatus='';PR.verdicts={};PR.regen=0;PR.regenOpen='';
$('pr-segs-wrap').hidden=true;$('pr-segs-wrap').innerHTML='';
prPaint();
}
async function prLoadPipes(){
try{
var d=await getJSON('/api/pipelines');
PR.pipes=d.pipelines||d.pipes||[];
}catch(err){PR.pipes=[];}
}
async function prLoadPipe(id){
try{
PR.def=await getJSON(`/api/pipelines/${encodeURIComponent(id)}`);
PR.hasSegs=prChainHasSegs(PR.def&&PR.def.nodes);
PR.hasArticle=prChainHasArticle(PR.def&&PR.def.nodes);
}catch(err){PR.def=null;toast('读取流水线失败：'+err.message,ER);}
}
async function prRun(){
if(!PR.def||!PR.def.nodes||!PR.def.nodes.length){toast('流水线无节点可运行',WN);return;}
var btn=$('pr-run-btn');
btn.disabled=true;
PR.segs=null;PR.ranges=null;PR.edits={};PR.rngDel={};PR.rngAdd={};PR.editIdx=-1;
PR.hasArticle=false;PR.awStatus='';PR.verdicts={};
$('pr-segs-wrap').hidden=true;$('pr-segs-wrap').innerHTML='';
try{
var d=await postJSON('/api/pipelines/run',{id:PR.pipeId,confirm:true});
PR.runId=d.run_id||'';PR.jobId=d.job_id||'';
PR.steps=[];PR.status='running';PR.error='';PR.artifacts=[];PR.t0=Date.now();PR.miss=0;
prPaint();prPoll();
}catch(err){
toast('运行提交失败：'+err.message,ER);
btn.disabled=false;
}
}
function prPoll(){
prStop();
PR.timer=setInterval(async function(){
var d=await prFetchRun();
if(!d)return;
PR.status=d.status||'running';
if(d.steps)PR.steps=d.steps;
if(d.artifacts)PR.artifacts=d.artifacts;
prApplyVerdicts(d,(d.plugins||[]).map(function(p){return {plugin:{outputs:(p.outputs||{})}};}).concat((PR.def&&PR.def.nodes)||[]));
PR.error=d.error||'';
PR.steps.forEach(function(s){if(s.t==null&&PR.t0)s.t=(Date.now()-PR.t0)/1000;});
prPaint();
if(['done','failed','cancelled'].indexOf(PR.status)>=0){
prStop();
var b=$('pr-run-btn');if(b)b.disabled=false;
if(PR.status==='done'){toast('流水线完成 · 产出见「产出物」',OK);if(PR.hasSegs)prLoadSegs();}
else toast('流水线'+(PR.status==='cancelled'?'已取消':'失败')+(PR.error?'：'+PR.error:''),PR.status==='cancelled'?WN:ER);
}
},800);
}
async function prFetchRun(){
try{
if(PR.runId)return await getJSON(`/api/pipelines/runs/${encodeURIComponent(PR.runId)}`);
if(PR.jobId){
var j=await getJSON(`/api/jobs/${encodeURIComponent(PR.jobId)}`);
return j.result||{status:j.status};
}
}catch(err){
PR.miss++;
if(PR.miss===10){prStop();var b=$('pr-run-btn');if(b)b.disabled=false;toast('运行状态端点未接驳（后端批）：'+err.message,WN);}
}
return null;
}
async function prRestore(){
var d=await prFetchRun();
if(d){
PR.status=d.status||'';PR.steps=d.steps||[];PR.artifacts=d.artifacts||[];PR.error=d.error||'';
var pl=(d.plugins||[]).map(function(p){return {plugin:{outputs:(p.outputs||{})}};});
PR.hasSegs=prChainHasSegs(pl);
prApplyVerdicts(d,pl);
prPaint();
if(PR.status==='done'&&PR.hasSegs)prLoadSegs();
else if(['running','queued'].indexOf(PR.status)>=0)prPoll();
}else toast('运行态读取失败（端点未接驳？）',WN);
}
function prNodeList(){
if(PR.def&&PR.def.nodes&&PR.def.nodes.length){
return PR.def.nodes.map(function(n){
var m=n.plugin||{},ins=Object.keys(m.inputs||{}).map(function(k){return (m.inputs[k]||{}).type;}),outs=Object.keys(m.outputs||{}).map(function(k){return (m.outputs[k]||{}).type;});
return {id:n.id,name:n.id,icon:prKindIcon(m.kind),ins:ins.join('+')||'—',outs:outs.join('+')||'—'};
});
}
return PR.steps.map(function(s){return {id:s.step,name:s.step,icon:'flow',ins:'—',outs:'—'};});
}
function prStepOf(id){
for(var i=0;i<PR.steps.length;i++){if(PR.steps[i].step===id)return PR.steps[i];}
return null;
}
function prNodesHtml(){
var L=prNodeList();
if(!L.length)return '<div class="empty" style="padding:24px"><div class="empty-title">尚无节点</div><div class="empty-text">流水线还没有节点。</div></div>';
var fresh=true;
return L.map(function(n,ix){
var st=prStepOf(n.id),cls='pr-node',stText='待运行',bar=0;
if(st&&st.status==='ok'){cls+=' is-done';stText='完成';bar=100;}
else if(st&&st.status==='failed'){cls+=' is-failed';stText='失败';}
else if(st&&st.status==='cancelled'){stText='已取消';}
else if(PR.status==='running'&&fresh){cls+=' is-running';stText='运行中';fresh=false;}
var arrow=ix<L.length-1?'<span class="pr-arrow">'+I('arrow-right')+'</span>':'';
return '<div class="'+cls+'"><div class="pr-node-io"><span>in</span><span title="'+esc(n.ins)+'">'+esc(n.ins)+'</span><span title="'+esc(n.outs)+'">'+esc(n.outs)+'</span></div>'+
'<div class="pr-node-body"><span class="pr-node-icon">'+I(n.icon)+'</span><span class="pr-node-main"><span class="pr-node-name" title="'+esc(n.name)+'">'+esc(n.name)+'</span><span class="pr-node-st">'+stText+'</span></span></div>'+
'<div class="pr-node-bar"><i style="width:'+bar+'%"></i></div></div>'+arrow;
}).join('');
}
function prLogsHtml(){
if(!PR.steps.length)return '<div class="pr-log"><span class="pr-log-t">—</span><span class="pr-log-i">'+I('clock')+'</span><span class="pr-log-msg">'+(PR.status==='running'?'已提交，等待状态端点…':'点「运行」开始')+'</span></div>';
var rows=PR.steps.map(function(s,i){
var k=s.status==='ok'?'ok':s.status==='failed'?'err':s.status==='cancelled'?'warn':'run';
var ic=s.status==='ok'?'check':s.status==='failed'?'error':s.status==='cancelled'?'warning':'refresh';
var msg=s.detail||(s.status==='ok'?'完成':s.status||'…');
return '<div class="pr-log '+k+'"><span class="pr-log-t">'+(s.t!=null?pgFmt(s.t):p2k(i+1))+'</span><span class="pr-log-i">'+I(ic)+'</span><span class="pr-log-msg">'+esc('['+s.step+'] '+msg)+'</span></div>';
});
if(PR.error)rows.push('<div class="pr-log err"><span class="pr-log-t">!</span><span class="pr-log-i">'+I('error')+'</span><span class="pr-log-msg">'+esc(PR.error)+'</span></div>');
return rows.join('');
}
function prOutHtml(){
if(PR.status!=='done')return '<div class="empty" style="padding:24px"><div class="empty-title">'+(PR.status==='failed'?'运行失败':'尚无产出')+'</div><div class="empty-text">'+(PR.status==='failed'?'见运行日志定位失败环节。':'运行完成后，产物会在这里预览。')+'</div></div>';
var arts=(PR.artifacts||[]).map(function(a){
var p=typeof a==='string'?a:(a.path||a.name||'');
return '<span class="badge pass" title="'+esc(p)+'">'+esc(String(p).split('/').pop()||p)+'</span>';
}).join(' ')||'<span class="action-hint">无登记产物</span>';
// ④ 成稿状态徽章（AI-1 批）：链上有 article 型输出才出现——标题旁「AI 建议稿」
// 琥珀（draft 可用但未经人确认，不是错误）、「已弃用」灰；adopted 按规范无徽章。
// 裁决入口与 ranges 同语义（采纳/弃用各自不出重复入口）。
var aw='';
if(PR.hasArticle){
	aw='<div class="pr-lines"><div class="pr-line"><span class="pr-ts">成稿</span><span class="pr-tx">'+prStBadge(PR.awStatus,'AI 建议稿')+'</span><span class="pr-seg-ops">'+prAwOps({status:PR.awStatus},PR.regen)+'</span></div>'+(PR.regenOpen==='aw'?'<div class="pr-line">'+prRegenBar('aw')+'</div>':'')+'</div>';
}
var lines='';
if(PR.segs&&PR.segs.length){
lines='<div class="pr-lines">'+PR.segs.slice(0,4).map(function(s){
return '<div class="pr-line"><span class="pr-ts">'+pgFmt(s.start)+'</span><span class="pr-tx">'+esc(s.text)+'</span></div>';
}).join('')+'</div><p class="action-hint">时间戳锚可回跳原处；逐段核对与编辑见下方「转写逐段编辑」。</p>';
}
return '<div class="pr-out">'+arts+aw+lines+'</div>';
}
function prPaint(){
var pick=PR.mode==='pick';
$('pr-pick').hidden=!pick;
if(pick)$('pr-pick').innerHTML=prPipesHtml(); // pick=流水线清单（prPipesHtml 此前未接线，裸 #plugin-run 空白——冒烟发现）
$('pr-flow').hidden=pick;
$('pr-panels').hidden=pick;
$('pr-back-btn').hidden=pick;
$('pr-reset-btn').hidden=pick;
$('pr-run-btn').hidden=PR.mode!=='pipe';
$('pr-title').textContent=PR.mode==='pick'?'流水线':((PR.def&&PR.def.name)?String(PR.def.name):'流水线画布');
$('pr-sub').textContent=pick?('共 '+PR.pipes.length+' 条已存流水线'):(PR.mode==='run'?'运行 '+PR.runId:(PR.pipeId||''));
var b=$('pr-badge');
b.textContent={running:'运行中',done:'完成',failed:'失败',cancelled:'已取消'}[PR.status]||'待运行';
b.className='badge '+({running:'running',done:'done',failed:'failed'}[PR.status]||'queued');
var ob=$('pr-out-badge');
ob.textContent=PR.status==='done'?'已生成':'未生成';
ob.className='badge '+(PR.status==='done'?'done':'queued');
$('pr-flow').innerHTML=prNodesHtml();
$('pr-logs').innerHTML=prLogsHtml();
$('pr-out').innerHTML=prOutHtml();
}
function prPipesHtml(){
if(!PR.pipes.length)return '<div class="empty"><div class="empty-icon">'+I('flow')+'</div><div class="empty-title">暂无流水线</div><div class="empty-text">到「插件」清单新建一条（输入 → 处理 → 输出），保存后在这里运行。</div><button class="btn btn-primary" data-prnew>去新建</button></div>';
return '<div class="pr-pipes">'+PR.pipes.map(function(p){
var n=(p.nodes||[]).length;
return '<button class="pr-pipe" data-propen="'+esc(p.id)+'"><span class="pr-pipe-i">'+I('flow')+'</span><span class="pr-pipe-main"><span class="pr-pipe-name">'+esc(p.name||p.id)+'</span><span class="pr-pipe-sub">'+esc(p.id)+' · '+n+' 节点</span></span><span class="pr-pipe-go">'+I('play')+'</span></button>';
}).join('')+'</div>';
}
async function prLoadSegs(force){
if(!PR.runId||!PR.hasSegs||(PR.segs!=null&&!force))return; // 无 segments 链不挂载转写面板（复核 medium-4）
try{
var d=await getJSON(`/api/pipelines/runs/${encodeURIComponent(PR.runId)}/segments`);
PR.segs=d.segments||[];PR.ranges=d.ranges||[];
prPaintSegs();
}catch(err){
$('pr-segs-wrap').hidden=false;
$('pr-segs-wrap').innerHTML='<div class="card pr-panel"><div class="pr-phead"><h3 class="pr-ptitle">转写编辑（segments / ranges）</h3><span class="badge queued">无转写产物</span></div><div class="pr-pbody"><p class="action-hint">该运行没有可编辑的转写段落（链上无转写节点、运行早停或已被清理）。'+esc(err.message||'')+'</p></div></div>';
}
}
function prSegRow(s,i,dup){
var edit=PR.edits[i]!=null,low=pgLowConf(s),editing=PR.editIdx===i;
var txt=edit?PR.edits[i]:s.text;
var flags=(low?'<span class="pr-flag" title="低置信（avg_logprob<-1.0 或 no_speech_prob>0.6），建议 [▶] 核对">'+I('warning')+'</span>':'')+(dup[i]?'<span class="pr-flag pr-flag-dup" title="与相邻段文本重复（疑似复读/幻听）">复</span>':'')+(edit?'<span class="badge pass">已改</span>':'');
var body=editing?'<input class="input pr-seg-in" id="pr-seg-in" value="'+esc(txt)+'" data-editin="'+i+'"><span class="pr-seg-ops"><button class="btn btn-sm btn-primary" data-saveedit="'+i+'">存</button><button class="btn btn-sm" data-canceledit>取消</button></span>'
:'<span class="pr-seg-text">'+esc(txt)+'</span><span class="pr-seg-ops"><button class="btn btn-sm" data-playseg="'+i+'" title="听原声（裁段音频）">▶</button><button class="btn btn-sm" data-editseg="'+i+'">编辑</button>'+(PR.ranges!=null?'<button class="btn btn-sm" data-addrng="'+i+'"'+(PR.rngAdd[i]?' disabled':'')+'>+段</button>':'')+'</span>';
return '<div class="pr-seg'+(edit?' is-edited':'')+(low?' is-low':'')+(dup[i]?' is-dup':'')+'"><span class="pr-seg-ts">'+pgFmt(s.start)+'</span>'+body+flags+'</div>';
}
function prRangesHtml(){
if(PR.ranges==null)return '';
if(!PR.ranges.length&&!Object.keys(PR.rngAdd).length)return '<p class="action-hint">无切片 ranges（切片节点跑完后出现；也可在下表从 segments 勾选加入）。</p>';
var rows=PR.ranges.map(function(r,i){
if(PR.rngDel[i])return '';
// ④ 裁决态（AI-1 批）：每条旁徽章 + 采纳/弃用入口。无 status 的老稿不出徽章
// 也不出入口（不知道态就无从裁决——裁决端点自会按产物现状盖态）。
	return '<div class="pr-rng"><span class="pr-seg-ts">'+pgFmtPair(r.start,r.end)+'</span>'+(r.label?'<span class="pr-tx">'+esc(r.label)+'</span>':'')+prStBadge(r.status)+'<span class="pr-seg-ops">'+prRngOps(r,i,PR.regen)+'</span><button class="btn btn-sm" data-delrng="'+i+'" title="从 ranges 移除（落 ranges.edited）">×</button></div>';
}).join('');
var adds=Object.keys(PR.rngAdd).map(function(k){var a=PR.rngAdd[k];return '<div class="pr-rng is-add"><span class="pr-seg-ts">'+pgFmtPair(a.start,a.end)+'</span><span class="badge pass">新增</span></div>';}).join('');
return '<div class="pr-rngs">'+rows+adds+'</div>';
}
function prPaintSegs(){
if(PR.segs==null)return;
var dup=pgDupSet(PR.segs),cap=200,n=Object.keys(PR.edits).length;
var rows=PR.segs.slice(0,cap).map(function(s,i){return prSegRow(s,i,dup);}).join('');
var note=PR.segs.length>cap?'<p class="action-hint">仅显示前 '+cap+' 段（共 '+PR.segs.length+' 段）。</p>':'';
$('pr-segs-wrap').hidden=false;
$('pr-segs-wrap').innerHTML='<div class="card pr-panel"><div class="pr-phead"><h3 class="pr-ptitle">切片 ranges</h3>'+prRangeTitleBadge(PR.ranges)+'<span class="badge queued">可增删</span></div><div class="pr-pbody">'+prRegenBar('ks')+prRangesHtml()+'</div></div>'+
'<div class="card pr-panel"><div class="pr-phead"><h3 class="pr-ptitle">转写逐段编辑</h3><span class="badge '+(n?'pass':'queued')+'">'+(n?'已改 '+n+' 段':'未修改')+'</span><button class="btn btn-sm btn-primary" data-segsave>保存编辑</button></div><div class="pr-pbody pr-segs">'+rows+note+'</div></div>'+
'<p class="action-hint">编辑落 edited 副本（原始转写不覆盖）；低置信标记段建议先 [▶] 听原声核对再改。</p>';
}
// ⑤ 裁决动作（AI-1 批契约 §3.19）：adopt/discard 两端点同形——POST 后重拉
// runs 读面（ai_verdicts 与 ranges 的 status 都是同一份生效文档的回读，避免
// 前端自算态与后端分叉）。indices 缺省=全集；step 取 ks 或 aw。
async function prAdjudicate(action,step,indices){
if(!PR.runId)return;
var body={step:step,confirm:true};
if(indices!=null)body.indices=indices;
try{
var d=await postJSON(`/api/pipelines/runs/${encodeURIComponent(PR.runId)}/${action}`,body);
toast((action==='adopt'?'已采纳':'已弃用')+'（'+(d.changed||0)+'/'+(d.total||0)+' 项）',OK);
if(PR.hasSegs)await prLoadSegs(true);
var rd=await prFetchRun();
if(rd)prApplyVerdicts(rd,(rd.plugins||[]).map(function(p){return {plugin:{outputs:(p.outputs||{})}};}).concat((PR.def&&PR.def.nodes)||[]));
	if(PR.hasArticle||step==='aw')prPaint();
	else prPaintSegs();
	}catch(err){toast((action==='adopt'?'采纳':'弃用')+'失败：'+err.message,WN);}
}
// 重新生成（AI-3 批 2026-11-21）：🔄 点击 → **行内小输入条**（改进要求可空；
// 不用 window.prompt——自定义弹层体系红线，test_studio_ui 否向锁）→
// POST regenerate（长动作：ks/aw 是 AI 节点，端点提交 job）→ 轮询 jobs 面
// 至终态 → 重拉 runs（ai_verdicts）+ segments。PR.regen 置位期间禁用全部 🔄
// （防重复提交）；PR.regenOpen 记开条态（ks/aw 单开，输入框 id 唯一）。
function prRegenBar(step){
if(PR.regenOpen!==step)return '';
var what=step==='ks'?'切片':'成稿';
return '<div class="pr-rng is-add" style="flex-wrap:wrap;gap:6px">'+
'<span class="badge warn">🔄 重新生成'+what+'</span>'+
'<input class="input pr-seg-in" id="pr-regen-in" style="flex:1;min-width:160px" placeholder="改进要求（可留空，如「更简洁」「保留原话更多」）" data-regenin>'+
'<span class="pr-seg-ops"><button class="btn btn-sm btn-primary" data-regengo="'+step+'"'+(PR.regen?' disabled':'')+'>重新生成</button><button class="btn btn-sm" data-regencancel>取消</button></span>'+
'<span class="action-hint">旧版自动归档（outputs.vN）可对照；新产物为「AI 建议」待重新裁决</span></div>';
}
function prRegenOpen(step){
if(!PR.runId||PR.regen)return;
PR.regenOpen=step;
if(step==='ks'){if(PR.segs!=null)prPaintSegs();}
else prPaint();
var inp=$('pr-regen-in');
if(inp)inp.focus();
}
function prRegenCancel(){
PR.regenOpen='';
prPaint();if(PR.segs!=null)prPaintSegs();
}
async function prRegenGo(step){
if(!PR.runId||PR.regen)return;
var inp=$('pr-regen-in');
var fb=inp?String(inp.value).trim():'';
PR.regenOpen='';
await prRegenSubmit(step,fb);
}
async function prRegenSubmit(step,fb){
var what=step==='ks'?'切片':'成稿';
PR.regen=1;prPaint();if(PR.segs!=null)prPaintSegs();
try{
var d=await postJSON(`/api/pipelines/runs/${encodeURIComponent(PR.runId)}/regenerate`,{step:step,feedback:fb,confirm:true});
toast(what+'重生成已提交（job '+(d.job_id||'')+'）…',OK);
await prRegenPoll(d.job_id,what);
}catch(err){toast(what+'重生成失败：'+err.message,ER);PR.regen=0;prPaint();if(PR.segs!=null)prPaintSegs();}
}
function prRegenPoll(jobId,what){
return new Promise(function(res){
var miss=0;
var t=setInterval(async function(){
try{
var j=await getJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
if(j.status==='done'||j.status==='failed'){
clearInterval(t);PR.regen=0;PR.regenOpen='';
if(j.status==='failed')toast(what+'重生成失败：'+(j.error||''),ER);
else toast(what+'已重生成（旧版归档，新产物为 AI 建议待裁决）',OK);
await prRestore();
if(PR.hasSegs)await prLoadSegs(true);
prPaint();if(PR.segs!=null)prPaintSegs();
res();return;
}
}catch(err){miss++;if(miss>=10){clearInterval(t);PR.regen=0;prPaint();res();return;}}
},800);
});
}
async function prSaveSegs(){
if(PR.segs==null)return;
var body={run_id:PR.runId,segments:pgApplyEdits(PR.segs,PR.edits),ranges:pgApplyRanges(PR.ranges||[],PR.rngDel,Object.keys(PR.rngAdd).map(function(k){return PR.rngAdd[k];})),confirm:true};
try{
await postJSON('/api/pipelines/runs/segments',body);
toast('已保存：segments.edited / ranges.edited（原始不覆盖）',OK);
PR.rngAdd={};PR.rngDel={};
prPaintSegs();
}catch(err){toast('保存失败：'+err.message,WN);}
}
function prPlay(i){
var s=PR.segs&&PR.segs[i];
if(!s)return;
var a=$('pr-audio');
a.src=`/api/pipelines/runs/${encodeURIComponent(PR.runId)}/audio?start=${encodeURIComponent(s.start)}&end=${encodeURIComponent(s.end)}`;
a.onerror=function(){toast('裁段音频播放失败（源媒体被移动/删除或 ffmpeg 不可用）',WN);a.onerror=null;};
var p=a.play();
if(p&&p.catch)p.catch(function(){});
}
async function prEnter(params){
prStop();
PR.editIdx=-1;
// 视图入口清转写编辑态（冒烟2 发现）：上一条链的 segments 面板/编辑副本
// 不得残留进本入口——门控（hasSegs）只决定「是否重新挂载」，不负责清旧 DOM。
PR.segs=null;PR.ranges=null;PR.edits={};PR.rngDel={};PR.rngAdd={};
PR.hasArticle=false;PR.awStatus='';PR.verdicts={};PR.regen=0;PR.regenOpen='';
$('pr-segs-wrap').hidden=true;$('pr-segs-wrap').innerHTML='';
var run=params.get('run'),id=params.get('id');
if(run){
PR.mode='run';PR.runId=run;PR.pipeId='';PR.def=null;
prPaint();
await prRestore();
}else if(id){
PR.mode='pipe';PR.pipeId=id;PR.runId='';PR.jobId='';
await prLoadPipe(id);
prPaint();
}else{
PR.mode='pick';PR.pipeId='';PR.runId='';PR.def=null;
await prLoadPipes();
prPaint();
}
}
// 路由入口（原 renderRoute plugin-run 分支 `await prEnter(route.params)` 迁入；
// return 透传 Promise——renderRoute 的 await 语义与原分支逐字等价）
VAPI['plugin-run']={enter:function(route){return prEnter(route.params);}};
function bindPluginRun(){
$('pr-pick').addEventListener('click',function(e){
if(e.target.closest('[data-prnew]')){location.hash='#plugins?new=1';return;}
var b=e.target.closest('[data-propen]');
if(b)location.hash='#plugin-pipeline?id='+encodeURIComponent(b.getAttribute('data-propen'));
});
$('pr-run-btn').addEventListener('click',prRun);
$('pr-reset-btn').addEventListener('click',prReset);
$('pr-back-btn').addEventListener('click',function(){location.hash='#plugins';});
	// ④ 产出物区成稿裁决入口（AI-1 批）：#pr-out 此前只读渲染，本批起承接
	// 采纳/弃用两动作（事件委托与 #pr-segs-wrap 同款，DOM 零新增）；AI-3 批
	// 增 🔄 重新生成（同委托分流：开行内输入条 / 提交 / 取消）。
	$('pr-out').addEventListener('click',function(e){
	var t;
	if(e.target.closest('[data-awadopt]'))prAdjudicate('adopt','aw',null);
	else if(e.target.closest('[data-awdiscard]'))prAdjudicate('discard','aw',null);
	else if(e.target.closest('[data-awregen]'))prRegenOpen('aw');
	else if((t=e.target.closest('[data-regengo]')))prRegenGo(t.getAttribute('data-regengo'));
	else if(e.target.closest('[data-regencancel]'))prRegenCancel();
	});
$('pr-segs-wrap').addEventListener('click',function(e){
var t;
if((t=e.target.closest('[data-playseg]')))prPlay(+t.getAttribute('data-playseg'));
else if((t=e.target.closest('[data-editseg]'))){PR.editIdx=+t.getAttribute('data-editseg');prPaintSegs();}
else if((t=e.target.closest('[data-saveedit]'))){
var i=+t.getAttribute('data-saveedit'),inp=$('pr-seg-in');
if(inp){var v=String(inp.value).replace(/\s+/g,' ').trim();if(v)PR.edits[i]=v;else delete PR.edits[i];}
PR.editIdx=-1;prPaintSegs();
}
else if(e.target.closest('[data-canceledit]')){PR.editIdx=-1;prPaintSegs();}
else if((t=e.target.closest('[data-addrng]'))){
var j=+t.getAttribute('data-addrng'),s=PR.segs&&PR.segs[j];
if(s&&!PR.rngAdd[j])PR.rngAdd[j]={start:s.start,end:s.end};
prPaintSegs();
}
else if((t=e.target.closest('[data-delrng]'))){PR.rngDel[+t.getAttribute('data-delrng')]=1;prPaintSegs();}
	else if((t=e.target.closest('[data-rngadopt]')))prAdjudicate('adopt','ks',[+t.getAttribute('data-rngadopt')]);
	else if((t=e.target.closest('[data-rngdiscard]')))prAdjudicate('discard','ks',[+t.getAttribute('data-rngdiscard')]);
	else if(e.target.closest('[data-rngregen]'))prRegenOpen('ks');
	else if((t=e.target.closest('[data-regengo]')))prRegenGo(t.getAttribute('data-regengo'));
	else if(e.target.closest('[data-regencancel]'))prRegenCancel();
	else if(e.target.closest('[data-segsave]'))prSaveSegs();
	});
$('pr-segs-wrap').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'&&e.target&&e.target.getAttribute&&e.target.getAttribute('data-editin')!=null){
e.preventDefault();
var btn=$('pr-segs-wrap').querySelector('[data-saveedit]');
if(btn)btn.click();
}
});
	// 重生成输入条 Enter=提交（document 级——条可能在 #pr-out（aw）或
	// #pr-segs-wrap（ks），两容器各自的 keydown 委托只认自己的输入；单开态
	// document.querySelector 取到的即当前条）
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter'&&e.target&&e.target.getAttribute&&e.target.getAttribute('data-regenin')!=null){
e.preventDefault();
var go=document.querySelector('[data-regengo]');
if(go)go.click();
}
});
}
bindPluginRun();
// @endview
