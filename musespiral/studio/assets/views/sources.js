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
updateNavBadge('nav-badge-sources',bad);
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
VAPI.sources={enter:async function(route){
var sp=route.params.get('src');
if(sp!=null)state.ssrc=sp;
await Promise.all([refreshSrcData(),loadMaterials(),refreshJobs()]);
renderSources();
},repaint:async function(){await loadMaterials();renderSources();}};
function bindSources(){
$('src-empty-add').addEventListener('click',sfOpen);
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
