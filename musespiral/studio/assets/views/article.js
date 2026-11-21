function editConfirm(o){return askConfirm(o);}
var LIVE_CSS='body{margin:0;padding:24px 20px;font:15px/1.85 -apple-system,"PingFang SC",sans-serif;color:#1a1a1a;word-break:break-word}h1,h2,h3{line-height:1.35;margin:1.2em 0 .5em}h1{font-size:22px}h2{font-size:19px}h3{font-size:17px}img{max-width:100%}a{color:#c0392b}pre{background:#f4f4f2;padding:10px;border-radius:6px;overflow:auto;font:13px/1.6 Menlo,monospace}code{background:#f4f4f2;padding:1px 5px}blockquote{margin:1em 0;padding:2px 14px;border-left:3px solid #c0392b;color:#555}';
var liveTimer=null,sLock=false,bubbleTimer=null,sEcho={p:-1,e:-1};
function renderLive(){LF.srcdoc='<!DOCTYPE html><meta charset="utf-8"><style>'+LIVE_CSS+'</style>'+mdToHtml(EA.value,dirOf(state.detailPath));}
function scheduleLive(){if(liveTimer)clearTimeout(liveTimer);liveTimer=setTimeout(renderLive,300);}
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
el.classList.toggle('is-active',el.getAttribute('data-tab')===tab);
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
function pRun(){
var path=state.detailPath;
if(!path)return toast('先打开一篇文章',WN);
state.pipe={steps:[]};pPaint();
postJSON('/api/action/pipeline',{article:path,confirm:true}).then(function(d){
if(d.job_id){$('jobs-menu').hidden=false;jobsPollStop();jobsPoll=setInterval(refreshJobs,3000)}
return trackJob(d.job_id).then(function(j){
if(j){state.pipe={steps:((j.result||{}).steps||[]),err:j.status==='failed'?j.error:''};pPaint()}})}).catch(function(e){state.pipe.err=e.message;pPaint();toast('金路径提交失败：'+e.message,ER)});
}
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
$('variant-grid').innerHTML=html||'<div class="empty" style="grid-column:1/-1"><div class="empty-icon" data-icon="star"></div><div class="empty-title">还没有平台变体</div><div class="empty-text">一稿多态：用下方按钮生成 xhs / channels / douyin / zhihu 四平台改写稿。</div></div>';
}
function openViewer(rel){
function show(v){$('viewer-title').textContent=rel;$('viewer-body').textContent=v||'（空文件）';$('viewer').hidden=false;}
var hit=state.vraw[rel];
if(hit){show(hit.content);return;}
getJSON(`/api/article/body?path=${encodeURIComponent(rel)}`).then(function(b){state.vraw[rel]=Object.assign({content:b.content||''},variantLocal(b.content||''));show(b.content);})
.catch(function(err){toast('读取变体失败：'+err.message,ER);});
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
},repaint:renderDetail,setTab:setTab};
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
$('viewer-close').addEventListener('click',function(){ $('viewer').hidden=true;});
EA.addEventListener('input',function(){state.saveSt='dirty';syncGutter();renderHl();scheduleLive();updateStatusbar();hideBubble();});
EA.addEventListener('scroll',function(){
EG.scrollTop=EA.scrollTop;HL.scrollTop=EA.scrollTop;HL.scrollLeft=EA.scrollLeft;
if(!state.bubAt||Date.now()-state.bubAt>300)hideBubble();
if(sEcho.e>=0){var sw=Math.abs(EA.scrollTop-sEcho.e)<2;sEcho.e=-1;if(sw)return;}
syncPreview();
});
EA.addEventListener('mouseup',maybeBubble);
EA.addEventListener('keyup',function(e){if(e.shiftKey||e.key.indexOf('Arrow')===0){if(bubbleTimer)clearTimeout(bubbleTimer);bubbleTimer=setTimeout(function(){bubbleTimer=null;maybeBubble();},300);}});
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
