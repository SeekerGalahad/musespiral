var RD={pane:null,fs:1,mode:'paper',focus:false,phase:'',url:'',book:null,n:1,range:'one',opener:null};
function rdScroll(){return $('rd-scroll');}
function rdFoot(){return $('rd-foot');}
function rdOpen(kind,data){
var p=$('reader-pane');RD.opener=document.activeElement;
if(RD.pane===null){RD.mode=document.documentElement.getAttribute('data-theme')==='dark'?'night':'paper';}
RD.pane=kind;RD.book=(data&&data.book)||null;RD.url=(data&&data.url)||'';RD.n=(data&&data.n)||1;RD.range='one';
p.hidden=false;$('rd-scrim').hidden=false;
p.setAttribute('aria-label',kind==='book'?'书籍阅读':'网页阅读');
if(kind==='web'){
RD.phase='loading';rdRenderWeb();
var tok=(RD.webTok||0)+1;RD.webTok=tok;
setTimeout(async function(){
if(RD.pane!=='web'||RD.webTok!==tok)return;
try{
var d=await postJSON('/api/ingest/clip',{action:'preview',url:RD.url,confirm:true});
var j=await waitForJob(d.job_id);
var r=(j&&j.result)||{};
if(j.status==='failed'){RD.phase='fail';RD.err=(j.error||'读取失败');}
else if(r.mode!=='crawled'||!r.text){RD.phase='empty';}
else{RD.phase='ok';RD.article={title:r.title||'(无题)',text:r.text,chars:r.word_count||r.text.length,url:RD.url};}
}catch(err){RD.phase='fail';RD.err=err.message;}
rdRenderWeb();
var c=$('reader-pane [data-rd="close"]');if(c)c.focus();
},60);
}else{
$('rd-title').textContent=(RD.book&&RD.book.name)||'书籍';
rdRenderBookDetail();
var c=$('reader-pane [data-rd="close"]');if(c)c.focus();
}
void p.offsetWidth;p.classList.add('open');
}
function rdClose(now){
var p=$('reader-pane');RD.pane=null;RD.webTok=(RD.webTok||0)+1;
p.classList.remove('open');$('rd-scrim').hidden=true;
if(now){p.hidden=true;rdScroll().innerHTML='';rdFoot().innerHTML='';return;}
setTimeout(function(){if(!RD.pane){p.hidden=true;rdScroll().innerHTML='';rdFoot().innerHTML='';}},260);
var o=RD.opener;if(o&&o.focus&&document.contains(o)&&o.offsetParent!==null)o.focus();
}
function rdTop(){
var title=RD.pane==='book'?((RD.book&&RD.book.name)||'书籍'):'网页阅读';
return '<div class="rd-head"><button class="btn btn-ghost btn-icon" data-rd="close" aria-label="关闭阅读面板"><span data-icon="close"></span></button>'+
'<b class="rd-title">'+esc(title)+'</b><div class="rd-tools">'+
'<div class="seg" role="group" aria-label="正文字号"><button data-rd="fs" data-d="-1" aria-label="缩小正文">A−</button><button data-rd="fs" data-d="1" aria-label="放大正文">A＋</button></div>'+
'<div class="seg" role="group" aria-label="底色"><button data-rd="mode" data-v="paper" aria-pressed="'+(RD.mode==='paper')+'">纸张</button><button data-rd="mode" data-v="night" aria-pressed="'+(RD.mode==='night')+'">夜间</button></div>'+
'<button class="btn btn-ghost btn-icon" data-rd="focus" aria-label="专注阅读"><span data-icon="external-link"></span></button></div></div>';
}
function rdApply(){
var p=$('reader-pane');p.dataset.mode=RD.mode;p.classList.toggle('focus',RD.focus);
p.style.setProperty('--read-scale',RD.fs);
rdScroll().classList.toggle('plain',RD.mode==='paper'&&RD.pane==='book');
}
function rdRenderBookDetail(){
var b=RD.book||{};
rdApply();
rdScroll().innerHTML='<div class="rd-body"><div class="bk-head">'+
'<span class="bk-cover">'+esc((b.name||'?').slice(0,2))+'</span><div class="bk-info"><h1>'+esc(b.name||'(无题)')+'</h1>'+
'<p class="bk-author">'+esc([b.author,b.kind,b.wordCount].filter(Boolean).join(' · '))+'</p>'+
'<button class="bk-origin" data-rd="swap" aria-label="换源（当前 '+esc(b.originName||b.origin||'')+'）">来源：'+esc(b.originName||b.origin||'—')+'</button></div></div>'+
(b.intro?'<p class="bk-intro">'+esc(b.intro)+'</p>':'')+
'<div class="rd-sec-head"><h2>目录</h2><span class="muted" style="font-size:12px">由 bookengine 解析</span></div><div class="bk-toc" id="rd-toc"><p class="action-hint">正在取目录…</p></div></div>';
rdFoot().innerHTML='<span class="muted" style="font-size:12px">点章节先读，再决定入不入素材库。</span>';
var src=b.origin||'',bu=b.bookUrl||'';
getJSON('/api/book/toc?sourceUrl='+encodeURIComponent(src)+'&bookUrl='+encodeURIComponent(bu)).then(function(d){
var box=$('rd-toc');if(!box)return;
if(!d||d.status==='absent'){box.innerHTML='<p class="action-hint">引擎未就绪，目录读取失败。</p>';return;}
var ch=(d.chapters||[]).slice(0,200);
if(!ch.length){box.innerHTML='<p class="action-hint">这本书没解析出目录。</p>';return;}
RD.chapters=ch;
box.innerHTML=ch.map(function(c,i){return '<button data-rd="chapter" data-n="'+(i+1)+'"><span class="n">'+(i+1)+'</span><span>'+esc(c.title||'(无题)')+'</span></button>';}).join('');
}).catch(function(err){var box=$('rd-toc');if(box)box.innerHTML='<p class="action-hint">目录读取失败：'+esc(err.message)+'</p>';});
}
async function rdRenderChapter(n){
try{
var b=RD.book||{},src=b.origin||'',bu=b.bookUrl||'';
var ch=(RD.chapters||[])[n-1];
if(!ch||!ch.url){toast('缺章节地址，请回目录重选',ER);return;}
var d=await getJSON('/api/book/chapter?sourceUrl='+encodeURIComponent(src)+'&bookUrl='+encodeURIComponent(bu)+'&chapterUrl='+encodeURIComponent(ch.url));
if(!d||d.status==='absent'){toast('引擎未就绪，取章失败',ER);return;}
RD.n=n;RD.title=(ch&&ch.title)||d.title||('第 '+n+' 章');
rdApply();
rdScroll().innerHTML='<article class="rd-article"><p class="ch-kicker">'+esc((b.name||'')+' · 第 '+n+' 章')+'</p>'+
'<h1>'+esc(RD.title)+'</h1>'+String(d.content||'').split(/\n\s*\n/).map(function(p){return '<p>'+esc(p)+'</p>';}).join('')+
(d.truncated?'<p class="demo-flag">正文过长，已截断至 '+((d.chars||0).toLocaleString())+' 字。</p>':'')+'</article>';
rdScroll().scrollTop=0;
rdFoot().innerHTML='<button class="btn" data-rd="prev"'+(n<=1?' disabled':'')+'>上一章</button>'+
'<button class="btn" data-rd="next">下一章</button><span class="spacer"></span>'+
'<div class="seg" role="group" aria-label="入库范围"><button data-rd="range" data-v="one" aria-pressed="'+(RD.range==='one')+'">本章</button><button data-rd="range" data-v="l5" aria-pressed="'+(RD.range==='l5')+'">最新 5 章</button><button data-rd="range" data-v="l10" aria-pressed="'+(RD.range==='l10')+'">最新 10 章</button></div>'+
'<button class="btn btn-primary" data-rd="ingest">'+I('download')+'选章入素材库</button>';
}catch(err){toast('取章失败：'+err.message,ER);}
}
function rdRenderWeb(){
rdApply();
if(RD.phase==='loading'){
rdScroll().innerHTML='<div class="rd-article" role="status" aria-label="正在读取网页">'+[14,26,13,15,15,15,15].map(function(h,i){return '<div class="skel skel-line" style="height:'+h+'px;width:'+[38,82,28,100,96,100,62][i]+'%;margin-bottom:12px"></div>';}).join('')+'</div>';
rdFoot().innerHTML='';
return;
}
if(RD.phase==='fail'||RD.phase==='empty'){
var fail=RD.phase==='fail';
rdScroll().innerHTML='<div class="rd-state" role="'+(fail?'alert':'status')+'"><h2>'+(fail?'没能读出这个网页':'这个网页里没找到正文')+'</h2>'+
'<p>'+(fail?esc(RD.err||'对方拒绝了请求（需要登录或防爬限制）。'):'可能是视频页、PDF，或需要登录才能看的内容。')+'</p>'+
'<div class="n-act"><button class="btn" data-rd="again">重试</button><button class="btn" data-rd="ext">打开原网页</button><button class="btn" data-rd="inbox">存入待决区</button></div></div>';
rdFoot().innerHTML='';
return;
}
var a=RD.article||{};
rdScroll().innerHTML='<article class="rd-article"><p class="rd-src">'+I('globe')+'<span>'+esc((function(){try{return new (URL)(RD.url).host;}catch(e){return RD.url;}})())+'</span>'+
'<button class="link" data-rd="ext">打开原网页'+I('external-link')+'</button></p>'+
'<h1>'+esc(a.title||'(无题)')+'</h1><p class="rd-byline">约 '+Math.max(1,Math.round((a.chars||0)/400))+' 分钟 · 提取 '+(a.chars||0).toLocaleString()+' 字（演示数据）</p>'+
String(a.text||'').split(/\n\s*\n/).map(function(p){return '<p>'+esc(p)+'</p>';}).join('')+'</article>';
rdFoot().innerHTML='<button class="btn btn-primary" data-rd="save">'+I('download')+'存入素材库</button>'+
'<button class="btn" data-rd="inbox">存入待决区</button><button class="btn btn-ghost" data-rd="copy">'+I('copy')+'复制正文</button>';
}
async function rdSave(){
var a=RD.article||{};
var ok=await askConfirm({title:'存入素材库？',message:'「'+(a.title||'(无题)')+'」· 来自 '+(function(){try{return new (URL)(RD.url).host;}catch(e){return RD.url;}})()+' · 约 '+(a.chars||0)+' 字。存入后可在素材库找到它。',confirmText:'存入'});
if(!ok)return;
try{
await postJSON('/api/ingest/clip',{action:'adopt',url:RD.url,title:a.title,text:a.text,mode:'crawled',confidence:0.9,word_count:a.chars,license:'UNKNOWN',confirm:true});
closeDlg('rd-confirm');rdClose();await loadMaterials();toast('已存入素材库',OK);
}catch(err){toast('入库失败：'+err.message,ER);}
}
function readerOpenWeb(url){rdOpen('web',{url:url});}
function readerOpenBook(book){rdOpen('book',{book:book});}
function bindReader(){
document.addEventListener('click',function(e){
if(!RD.pane||$('reader-pane').hidden)return;
var t=e.target.closest('[data-rd]');if(!t)return;
var act=t.getAttribute('data-rd');
if(act==='close'){rdClose();return;}
if(act==='fs'){RD.fs=Math.min(1.4,Math.max(.8,Math.round((RD.fs+ +t.getAttribute('data-d')*.1)*10)/10));rdApply();return;}
if(act==='mode'){RD.mode=t.getAttribute('data-v');rdApply();return;}
if(act==='focus'){RD.focus=!RD.focus;rdApply();return;}
if(act==='chapter'){rdRenderChapter(+t.getAttribute('data-n'));return;}
if(act==='prev'){rdRenderChapter(Math.max(1,RD.n-1));return;}
if(act==='next'){rdRenderChapter(RD.n+1);return;}
if(act==='range'){RD.range=t.getAttribute('data-v');rdRenderChapter(RD.n);return;}
if(act==='ingest'){
var range={one:1,l5:5,l10:10}[RD.range]||1;
var list=(RD.chapters||[]).slice(RD.n-1,RD.n-1+range).map(function(c,i){return {url:c.url,index:RD.n+i,title:c.title};});
var b=RD.book||{};
postJSON('/api/materials/from-chapters',{confirm:true,sourceUrl:b.origin||'',bookUrl:b.bookUrl||'',book:{name:b.name||'',author:b.author||''},chapters:list,digest:false})
.then(function(d){queued(d.job_id);rdClose();})
.catch(function(err){toast('提交失败：'+err.message,ER);});
return;
}
if(act==='swap'){
var opts=['笔趣阁','天域小说网','00小说网'];
var cur=(RD.book&&(RD.book.originName||RD.book.origin))||opts[0];
var next=opts[(opts.indexOf(cur)+1)%opts.length]||opts[0];
RD.book.originName=next;rdRenderBookDetail();toast('已换到「'+next+'」（演示，目录将按新源重取）',OK);return;
}
if(act==='ext'){var w=window.open(RD.url,'_blank','noopener,noreferrer');if(!w)toast('浏览器拦截了新窗口',WN);return;}
if(act==='again'){rdOpen('web',{url:RD.url});return;}
if(act==='inbox'){
postJSON('/api/ingest/crawl',{url:RD.url,confirm:true}).then(function(){rdClose();toast('已放入待决区，稍后在素材库确认',OK);})
.catch(function(err){toast('失败：'+err.message,ER);});return;
}
if(act==='save'){rdSave();return;}
if(act==='copy'){
var text=(RD.article||{}).title+'\n\n'+((RD.article||{}).text||'');
if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(text).then(function(){toast('已复制正文',OK);}).catch(function(){toast('复制失败：浏览器未授权',ER);});
else toast('复制失败：浏览器未授权',ER);
return;
}
});
$('rd-scrim').addEventListener('click',function(){rdClose();});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Escape'&&RD.pane&&!$('reader-pane').hidden){
var top=dlgStack&&dlgStack.length?dlgStack[dlgStack.length-1]:null;
if(top)return;
rdClose();return;
}
});
}
bindReader();
VAPI.reader={openWeb:readerOpenWeb,openBook:readerOpenBook,close:rdClose};
