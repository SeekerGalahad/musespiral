function pendingCount(){
return state.articles.filter(function(a){var c=a.checks||{},f=a.flags||{};return (c.fail||0)===0&&(c.warn||0)===0&&articleStatus(a)==='draft'&&!f.published;}).length;}
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
if(!pub)$('stat-tiles').innerHTML='<div class="empty"><div class="empty-icon" data-icon="publish">'+I('publish')+'</div><div class="empty-title">发布第一篇后，此处显示漏斗与趋势</div><div class="empty-text">素材与草稿已在积累，发布后四项指标在此生长。</div><a class="btn btn-primary empty-action" href="#publish">去发布中心</a></div>';
else $('stat-tiles').innerHTML=[['已发布',pub],['待审',pendingCount()],['总阅读',st.total_reads||0],['平台数',state.platforms.length]].map(function(t){
return '<div class="tile"><div class="tile-value">'+esc(t[1])+D+'<div class="tile-label">'+esc(t[0])+'</div></div>';}).join('');
QA('[data-irange]').forEach(function(el){el.classList.toggle('is-active',+el.getAttribute('data-irange')===state.irange);});
renderIdeaFunnel();renderTrend();renderStatsTrend();renderWriting();renderPlatformBars();renderContrib();renderScoreDist();renderAiUsage();renderComposeAct();renderRadar();
}
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
function renderScoreDist(){
var b=[0,0,0,0],mats=state.mats||[];
mats.forEach(function(m){var s=m.score;if(s==null)b[3]++;else if(s>=80)b[2]++;else if(s>=60)b[1]++;else b[0]++;});
var max=Math.max.apply(null,b.concat(1)),lb=['<60','60-79','≥80','无分'];
$('score-dist').innerHTML=b.map(function(n,i){
return '<div class="sd-col"><span class="sd-bar" style="height:'+Math.round(n/max*100)+'%"></span><span class="sd-n">'+n+'</span><span class="sd-l">'+lb[i]+'</span></div>';
}).join('');
$('score-dist-note').textContent='评分素材 '+mats.length+' 条（radar_signal 整分 / kb text 四维现算；其余类型无分沉底）';
}
function renderAiUsage(){
var AJ={compose_generate:'创作再生成',annotate_rewrite:'批注改写',adapt:'变体生成',batch_adapt:'批量变体',fetch:'URL 抓取',ingest:'源刷新',sync_stats:'数据回采',pack:'物料包',publish:'创建草稿'};
var c={};(state.jobs||[]).forEach(function(j){if(!j)return;c[j.kind]=(c[j.kind]||0)+1;});
var ks=Object.keys(AJ).filter(function(k){return c[k];});
$('ai-usage').innerHTML=ks.length?ks.map(function(k){
return '<div class="au-row"><span class="au-k">'+AJ[k]+'</span><span class="au-n">'+c[k]+' 次</span></div>';
}).join(''):'<p class="action-hint">暂无任务历史</p>';
}
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
VAPI.insight={enter:async function(route){
var rg=route.params.get('range');
if(rg==='30'||rg==='90')state.irange=+rg;
await Promise.all([loadStats(),loadPlatforms(),loadArticles(),loadMaterials(),loadCompose(true),refreshJobs(),renderHealth(),loadRadar(),loadWriting(true)]);
renderInsight();
},repaint:renderInsight};
function bindInsight(){
$('insight-range').addEventListener('click',function(e){
var c=e.target.closest('[data-irange]');
if(!c)return;
state.irange=+c.getAttribute('data-irange');
history.replaceState(null,'','#insight'+(state.irange!==30?'?range='+state.irange:''));
renderInsight();
});
var SR=$('stats-range');
if(SR)SR.addEventListener('click',function(e){
var c=e.target.closest('[data-srange]');
if(!c)return;
state.srange=c.getAttribute('data-srange');
renderStatsTrend();
});
function radarHint(){toast('请运行 musespiral topic-radar --refresh，或等周一 08:20 自动生成',OK);}
$('radar-refresh').addEventListener('click',radarHint);
$('radar-body').addEventListener('click',function(e){if(e.target.closest('#radar-gen'))radarHint();});
}
bindInsight();
