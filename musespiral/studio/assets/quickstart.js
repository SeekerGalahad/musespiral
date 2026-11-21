/*! 极速启动（I1 / E-4，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.4）。
 * 从洞察页选题雷达候选到可写骨架的最短路径：弹窗就地挂载素材/选模式 →
 * POST compose/new+add+generate → 轮询 job → 骨架逐块流式揭示 → 开始写作。
 *
 * 装载契约（ui.js 仅一行动态注入本文件，其余全部自管理）：
 *   - boot 时对 #radar-body 立 MutationObserver（renderRadar 每次 innerHTML 重建
 *     后自动重新装饰候选行，不依赖 ui.js 的 state.radar 闭包）；
 *   - 快照独立 GET /api/topic_radar 自取，按标题映射候选——零 ui.js 耦合；
 *   - 「开始写作」落点=文章编辑器深链（评审修复批 F1，正典 §3.4「跳转到
 *     编辑器（骨架已就位）」）：对末轮全块 POST compose/decide keep（快速
 *     裁决）→ POST compose/adopt（采纳入稿，正稿 <slug>.md 落队列）→
 *     #article?path=<正稿>&tab=edit；创作工坊会话仍在 compose 视图可寻，
 *     要逐块精裁决的用户走工坊而非这条快车道。
 *
 * 流式形态（评审修复批 F2，§3.4「不是干等」）：后端 compose job 走
 * chat_stream 增量——生成期把 partial 块（[{kind,text}]）与 phase 节流写进
 * job 行；前端 600ms 轮询在等待期即重绘 #qs-stream（上一块定格、尾块打字，
 * ChatGPT 式观感），完成直接定稿。非 SSE 端点无增量（chat_stream JSON 兜底
 * 零回调）——前端降级为完成后 220ms/块错峰揭示，两端语义自洽。骨架模式
 * chips 记入会话 title 供追溯；compose 任务模板当前无 mode 插槽，故模式
 * 暂不影响生成内容（ponytail: 若要模式真正左右 prompt，加一个模板插槽，
 * 别在本文件拼 prompt）。
 */
(function(){
'use strict';

/*__QS_BEGIN__*/
// ── 纯函数区（tests/test_quickstart_ui.py 的 node 行为断言抽取本段执行，不触 DOM）──

var QS_MODES=['技术','观点','教程','对比'];
function qsSlug(ts){return 'qs-'+((ts==null?Date.now():ts)).toString(36);}
function qsEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
// 开始写作落点（F1）：采纳后的正稿直接进文章编辑器深链（tab=edit）
function qsWriteHash(articlePath){return '#article?path='+encodeURIComponent(articlePath)+'&tab=edit';}
function qsPhasePct(phase){var m=String(phase||'').match(/^(\d+)\/(\d+)$/);
return m&&+m[2]>0?Math.round(+m[1]/+m[2]*100):null;}
// 弹窗结构：选题标题 + 挂载素材 checkbox + 骨架模式 chips + 双按钮。
function qsModalHtml(title,mats,mode){
var ms=Array.isArray(mats)?mats:[],cur=mode||QS_MODES[0];
return '<div class="qs-modal" role="dialog" aria-modal="true" aria-labelledby="qs-title">'+
'<h3 class="modal-title" id="qs-title">⚡ 极速启动</h3>'+
'<p class="qs-topic">选题：<strong>'+qsEsc(title)+'</strong></p>'+
'<div class="qs-sec"><span class="qs-label">挂载素材</span><div class="qs-mats">'+
(ms.length?ms.map(function(m,i){var p=m.uri||m.path||m.id||'';
return '<label class="qs-mat"><input type="checkbox" data-qs-mat="'+qsEsc(p)+'"'+
(i===0?' checked':'')+'>'+qsEsc(m.title||p)+'</label>';}).join(''):'<span class="action-hint">暂无素材——将用纯选题生成骨架</span>')+
'</div></div>'+
'<div class="qs-sec"><span class="qs-label">骨架模式</span><div class="chips qs-modes">'+
QS_MODES.map(function(m){return '<button type="button" class="chip'+(m===cur?' is-active':'')+
'" data-qs-mode="'+qsEsc(m)+'">'+qsEsc(m)+'</button>';}).join('')+'</div></div>'+
'<div class="modal-actions"><button class="btn btn-secondary" id="qs-cancel">取消</button>'+
'<button class="btn btn-primary" id="qs-go">🚀 30秒出骨架</button></div></div>';
}
// 单块流式渲染：heading 用 h4，其余段落；转义 + 换行转 <br>。
function qsBlockHtml(block){
var b=block||{},t=qsEsc(b.text||'').replace(/\n/g,'<br>');
return b.kind==='heading'?'<h4 class="qs-bh">'+t+'</h4>':'<p class="qs-bp">'+t+'</p>';
}
// 骨架就绪按钮（完成后出现）。
function qsReadyHtml(){return '<button class="btn btn-primary qs-ready" id="qs-ready" disabled>⏳ 生成骨架中…</button>';}
/*__QS_END__*/

var QS={title:'',mode:QS_MODES[0],slug:'',cands:{},running:false,streamed:false};
function qGet(id){return document.getElementById(id);}
function qFetch(path,opt){return fetch(path,opt).then(function(r){
return r.json().then(function(d){
if(!r.ok||d&&d.ok===false)throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));
return d;});});}
function qPost(path,body){return qFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(Object.assign({confirm:true},body||{}))});}

// 用选题标题映射候选（rr 渲染的主行与 snapshot.candidates 顺序一致；标题兜底）。
function qSnapshot(){
return qFetch('/api/topic_radar').then(function(d){
var sn=d&&d.snapshot,map={};
if(sn&&Array.isArray(sn.candidates))sn.candidates.forEach(function(c){map[c.title]=c;});
QS.cands=map;});
}
function qMaterials(){
return qFetch('/api/materials?limit=12').then(function(d){
return (d&&Array.isArray(d.items)?d.items:[]).map(function(m){
return {uri:(m.origin||{}).uri||m.path||m.id,title:m.title||m.id,id:m.id};});}).catch(function(){return [];});
}

// 装饰候选行：每个 .radar-row 找到其标题对应的候选，注入 [⚡极速启动]（幂等）。
function qDecorate(){
var body=qGet('radar-body');if(!body)return;
QA_RADIOS(body).forEach(function(row){
if(row.querySelector('.qs-launch'))return;
var t=row.querySelector('[data-t]'),title=t?t.textContent.trim():'';
if(!title||!QS.cands[title])return;
var btn=document.createElement('button');
btn.type='button';btn.className='btn qs-launch';btn.setAttribute('data-qs-title',title);
btn.textContent='⚡ 极速启动';
var host=row.querySelector('.dc-bar')||row.lastElementChild||row;
host.parentNode.insertBefore(btn,host.nextSibling);
});
}
var QA_RADIOS=function(root){return root.querySelectorAll('.radar-row');};

function qOpenModal(title){
QS.title=title;QS.mode=QS_MODES[0];
qMaterials().then(function(mats){
var ov=qGet('qs-overlay');
if(!ov){ov=document.createElement('div');ov.id='qs-overlay';ov.className='modal-overlay';
document.body.appendChild(ov);}
ov.innerHTML=qsModalHtml(title,mats,QS.mode);
ov.hidden=false;
ov.querySelectorAll('[data-qs-mode]').forEach(function(ch){
ch.addEventListener('click',function(){QS.mode=ch.getAttribute('data-qs-mode');
ov.querySelectorAll('[data-qs-mode]').forEach(function(x){x.classList.toggle('is-active',x===ch);});});});
qGet('qs-cancel').addEventListener('click',qClose);
ov.addEventListener('click',function(e){if(e.target===ov)qClose();});
qGet('qs-go').addEventListener('click',qRun);
});
}
function qClose(){var ov=qGet('qs-overlay');if(ov){ov.hidden=true;ov.innerHTML='';}}

function qSetProgress(pct,label){
var bar=qGet('qs-prog'),txt=qGet('qs-prog-label');
if(bar)bar.style.width=(pct==null?100:Math.max(3,pct))+'%';
if(txt&&label)txt.textContent=label;
}
// 轮询 job → 完成后逐块揭示。
function qWaitJob(jobId,onTick){
return new Promise(function(resolve,reject){
var tries=0;
(function tick(){
qFetch('/api/jobs/'+encodeURIComponent(jobId)).then(function(j){
onTick(j);
if(j.status==='done')return resolve(j);
if(j.status==='failed')return reject(new Error(j.error||'任务失败'));
if(++tries>300)return reject(new Error('生成超时'));
setTimeout(tick,600);
}).catch(reject);
})();});
}
function qReveal(blocks){
var area=qGet('qs-stream');if(!area)return Promise.resolve();
var list=Array.isArray(blocks)?blocks:[],i=0;
return new Promise(function(resolve){
(function next(){
if(i>=list.length){qSetProgress(100,'骨架就绪');resolve();return;}
area.insertAdjacentHTML('beforeend','<div class="qs-block">'+qsBlockHtml(list[i])+'</div>');
i++;qSetProgress(Math.round(i/list.length*100),'骨架 '+i+'/'+list.length);
area.scrollTop=area.scrollHeight;
setTimeout(next,220);
})();});
}
// 生成期/终态共用的整幅重绘（F2）：上一块定格、尾块随增量打字。
function qPaintPartial(list){
var area=qGet('qs-stream');if(!area)return;
var arr=Array.isArray(list)?list:[];
area.innerHTML=arr.map(function(b){return '<div class="qs-block">'+qsBlockHtml(b)+'</div>';}).join('');
if(arr.length)qSetProgress(null,'生成中 · '+arr.length+' 块');
area.scrollTop=area.scrollHeight;
}
// F1 开始写作：末轮全块 keep 快速裁决 → 采纳入稿 → 文章编辑器深链。
// 失败留在弹窗（qs-err 可读 + 就绪钮复点了重来），绝不半跳。
function qWrite(slug,round,blocks){
if(QS.running)return;QS.running=true;
qSetProgress(100,'入稿中…');
var st=qGet('qs-stream');
var chain=Promise.resolve();
(blocks||[]).forEach(function(b){
chain=chain.then(function(){return qPost('/api/compose/decide',{slug:slug,round:round,block:b.id,action:'keep'});});});
chain
.then(function(){return qPost('/api/compose/adopt',{slug:slug});})
.then(function(d){
qClose();
location.hash=qsWriteHash(d&&d.article||'');
QS.running=false;})
.catch(function(err){
if(st)st.insertAdjacentHTML('beforeend','<p class="qs-err">入稿失败：'+qsEsc(err&&err.message||'未知错误')+'</p>');
qSetProgress(100,'失败');
QS.running=false;
var r=qGet('qs-ready');if(r)r.disabled=false;});
}
async function qRun(){
if(QS.running)return;QS.running=true;
QS.streamed=false;
var go=qGet('qs-go');if(go)go.disabled=true;
var ov=qGet('qs-overlay'),title=QS.title||'未命名选题';
try{
var slug=qsSlug();
var body={slug:slug,title:'['+QS.mode+'] '+title};
await qPost('/api/compose/new',body);
var mats=ov?Array.prototype.slice.call(ov.querySelectorAll('[data-qs-mat]:checked')):[];
for(var i=0;i<mats.length;i++){
await qPost('/api/compose/add',{slug:slug,path:mats[i].getAttribute('data-qs-mat')}).catch(function(){});
}
QS.slug=slug;
ov.innerHTML='<div class="qs-modal" role="dialog" aria-modal="true">'+
'<h3 class="modal-title">⚡ 生成骨架 · '+qsEsc(title)+'</h3>'+
'<div class="qs-prog-wrap"><div class="qs-prog-track"><div class="qs-prog" id="qs-prog"></div></div>'+
'<span class="qs-prog-label" id="qs-prog-label">连接中…</span></div>'+
'<div class="qs-stream" id="qs-stream"></div>'+
'<div class="modal-actions"><button class="btn btn-primary qs-ready" id="qs-ready" disabled>⏳ 生成骨架中…</button></div></div>';
var job=await qPost('/api/compose/generate',{slug:slug});
// 轮询期即见骨架（F2）：job 行带 partial（SSE 增量发布）就整幅重绘；
// 无 partial（非 SSE 端点）保持进度条干等，完成后走 qReveal 错峰降级。
var j=await qWaitJob(job.job_id,function(x){
if(Array.isArray(x.partial)&&x.partial.length){QS.streamed=true;qPaintPartial(x.partial);}
else qSetProgress(null,x.phase?'进度 '+x.phase:'生成中…');});
var blocks=(j.result&&j.result.blocks)||[];
var round=(j.result&&j.result.round)||1;
if(QS.streamed){qPaintPartial(blocks);qSetProgress(100,'骨架就绪');}
else await qReveal(blocks);
var ready=qGet('qs-ready');if(ready){ready.disabled=false;ready.textContent='骨架就绪 → 开始写作';
ready.addEventListener('click',function(){qWrite(slug,round,blocks);});}
}catch(err){
var st=qGet('qs-stream');if(st)st.insertAdjacentHTML('beforeend',
'<p class="qs-err">生成失败：'+qsEsc(err.message)+'</p>');
qSetProgress(100,'失败');
}finally{QS.running=false;}
}

function boot(){
qGet('radar-body');
if(!qGet('radar-body'))return;
qSnapshot().then(qDecorate).catch(function(){});
new MutationObserver(qDecorate).observe(qGet('radar-body'),{childList:true,subtree:true});
// 全局委托：装饰按钮点击（快照可能晚于行渲染，重新拉一次再开）。
document.addEventListener('click',function(e){
var b=e.target.closest?e.target.closest('.qs-launch'):null;
if(!b)return;
var title=b.getAttribute('data-qs-title');
qSnapshot().then(function(){qOpenModal(title);});
});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
else boot();

window.__mdpubQuickstart={qsSlug:qsSlug,qsEsc:qsEsc,qsWriteHash:qsWriteHash,qsPhasePct:qsPhasePct,
qsModalHtml:qsModalHtml,qsBlockHtml:qsBlockHtml,qsReadyHtml:qsReadyHtml,QS_MODES:QS_MODES};
})();
