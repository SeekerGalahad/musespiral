var PR={mode:'',pipeId:'',def:null,runId:'',jobId:'',timer:null,t0:0,steps:[],status:'',error:'',artifacts:[],segs:null,ranges:null,edits:{},editIdx:-1,rngDel:{},rngAdd:{},pipes:[],miss:0,hasSegs:false,hasArticle:false,awStatus:'',verdicts:{}};
function prChainHasSegs(nodes){
return (nodes||[]).some(function(n){
var o=((n&&n.plugin)||{}).outputs||{};
return Object.keys(o).some(function(k){return o[k]&&o[k].type==='segments';});
});
}
function prApplyVerdicts(d,nodes){
PR.verdicts=(d&&d.ai_verdicts)||{};
PR.awStatus=prVerdict(PR.verdicts,'article');
PR.hasArticle=prChainHasArticle(nodes)||PR.awStatus!=='';
}
function pgFmt(sec){
sec=Math.max(0,Math.floor(+sec||0));
var h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;
function p2(n){n=String(n);return n.length<2?'0'+n:n;}
return (h?h+':':'')+p2(m)+':'+p2(s);
}
function pgFmtPair(a,b){return pgFmt(a)+' – '+pgFmt(b);}
function pgLowConf(seg){
return (seg.avg_logprob!=null&&seg.avg_logprob<-1)||(seg.no_speech_prob!=null&&seg.no_speech_prob>0.6);
}
function pgDupSet(segs){
var dup={},prev='';
(segs||[]).forEach(function(s,i){
var t=String(s.text||'').replace(/\s+/g,'');
if(t&&t===prev){dup[i]=1;dup[i-1]=1;}
prev=t;
});
return dup;
}
function pgApplyEdits(segs,edits){
return (segs||[]).map(function(s,i){
return edits[i]==null?s:Object.assign({},s,{text:edits[i],edited:true});
});
}
function pgApplyRanges(ranges,del,add){
return (ranges||[]).filter(function(r,i){return !del[i];}).concat(add||[]);
}
function prStBadge(st,draftText){
if(st==='draft')return '<span class="badge warn" title="AI 建议：可用但未经人工确认——可采纳为正式结果，或弃用">'+esc(draftText||'AI 建议')+'</span>';
if(st==='discarded')return '<span class="badge archived" title="已弃用：人工否定，下游跳过">已弃用</span>';
return '';
}
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
function prRngOps(r,i){
var st=r&&r.status,ops='';
if(!st)return '';
if(st!=='adopted')ops+='<button class="btn btn-sm" data-rngadopt="'+i+'" title="采纳为正式结果">采纳</button>';
if(st!=='discarded')ops+='<button class="btn btn-sm" data-rngdiscard="'+i+'" title="弃用（下游跳过）">弃用</button>';
return ops;
}
function prAwOps(aw){
var st=aw&&aw.status;
if(!st)return '';
return (st==='adopted'?'':'<button class="btn btn-sm" data-awadopt title="采纳为正式稿">采纳</button>')+
(st==='discarded'?'':'<button class="btn btn-sm" data-awdiscard title="弃用（下游跳过）">弃用</button>');
}
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
PR.hasArticle=false;PR.awStatus='';PR.verdicts={};
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
var aw='';
if(PR.hasArticle){
aw='<div class="pr-lines"><div class="pr-line"><span class="pr-ts">成稿</span><span class="pr-tx">'+prStBadge(PR.awStatus,'AI 建议稿')+'</span><span class="pr-seg-ops">'+prAwOps({status:PR.awStatus})+'</span></div></div>';
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
if(pick)$('pr-pick').innerHTML=prPipesHtml();
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
if(!PR.runId||!PR.hasSegs||(PR.segs!=null&&!force))return;
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
return '<div class="pr-rng"><span class="pr-seg-ts">'+pgFmtPair(r.start,r.end)+'</span>'+(r.label?'<span class="pr-tx">'+esc(r.label)+'</span>':'')+prStBadge(r.status)+'<span class="pr-seg-ops">'+prRngOps(r,i)+'</span><button class="btn btn-sm" data-delrng="'+i+'" title="从 ranges 移除（落 ranges.edited）">×</button></div>';
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
$('pr-segs-wrap').innerHTML='<div class="card pr-panel"><div class="pr-phead"><h3 class="pr-ptitle">切片 ranges</h3>'+prRangeTitleBadge(PR.ranges)+'<span class="badge queued">可增删</span></div><div class="pr-pbody">'+prRangesHtml()+'</div></div>'+
'<div class="card pr-panel"><div class="pr-phead"><h3 class="pr-ptitle">转写逐段编辑</h3><span class="badge '+(n?'pass':'queued')+'">'+(n?'已改 '+n+' 段':'未修改')+'</span><button class="btn btn-sm btn-primary" data-segsave>保存编辑</button></div><div class="pr-pbody pr-segs">'+rows+note+'</div></div>'+
'<p class="action-hint">编辑落 edited 副本（原始转写不覆盖）；低置信标记段建议先 [▶] 听原声核对再改。</p>';
}
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
PR.segs=null;PR.ranges=null;PR.edits={};PR.rngDel={};PR.rngAdd={};
PR.hasArticle=false;PR.awStatus='';PR.verdicts={};
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
$('pr-out').addEventListener('click',function(e){
if(e.target.closest('[data-awadopt]'))prAdjudicate('adopt','aw',null);
else if(e.target.closest('[data-awdiscard]'))prAdjudicate('discard','aw',null);
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
}
bindPluginRun();
