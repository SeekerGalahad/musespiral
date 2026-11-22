var PG={list:[],skipped:[],byId:{},filter:'',mode:'list',check:{},err:''};
var PG_PERM={'fs:read':'读输入文件（workspace 与 inputs 路径）','fs:write:tmp':'写临时目录','fs:write:output':'写产物目录','net:none':'零外联','net:http':'允许 HTTP 出网（安装时需明确批准）','exec:subprocess':'可启动子进程（安装时需明确批准）'};
function pgKINDS(){return{source:'输入',process:'处理',sink:'输出'};}
function pgKindIcon(kind){return kind==='source'?'download':kind==='sink'?'publish':'settings';}
function pgName(m){return (m&&m.name&&m.name.zh)||(m&&m.id)||'';}
function pgDesc(m){return (m&&m.description&&m.description.zh)||'';}
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
function pgHi(json){
return esc(json).split('\n').map(function(l){
var m=l.match(/^(\s*)&quot;([^&]+)&quot;:(\s)(.*)$/);
if(!m)return l;
var v=m[4],s=v.match(/^(&quot;.*&quot;)(,?)$/);
return m[1]+'<span class="pg-k">&quot;'+m[2]+'&quot;</span>:'+m[3]+(s?'<span class="pg-s">'+s[1]+'</span>'+s[2]:v);
}).join('\n');
}
function pgTypeCompat(a,b){return typeof a==='string'&&typeof b==='string'&&a===b;}
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
'<span class="pg-topmain"><span class="pg-name">'+esc(pgName(m))+'</span><span class="pg-vendor">'+esc(m.id)+' · v'+esc(m.version)+'</span></span>'+
'<span class="badge '+(m.uses_ai?'running':'pass')+'" title="'+(m.uses_ai?'声明 uses_ai：AI 由平台受管注入':'契约门全过')+'">'+(m.uses_ai?'AI':'就绪')+'</span></div>'+
'<p class="pg-desc">'+esc(pgDesc(m))+'</p>'+
'<div class="pg-foot"><span class="badge queued">'+esc(ks[m.kind]||m.kind)+'</span><span class="pg-io">'+esc(pgIoSummary(m))+'</span></div></article>';
}
function pgRenderList(){
pgChips();
var L=PG.list.filter(function(it){return !PG.filter||it.manifest.kind===PG.filter;});
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
var errs=pgValidateChain(pgBuildNodes());
if(errs.length){toast('契约校验未过：'+errs[0],ER);return;}
var name=$('pg-build-name').value.trim(),pid=pgSlug(name);
try{
await postJSON('/api/pipelines',{id:pid,name:name||pid,nodes:pgBuildNodes(),confirm:true});
toast('流水线已保存：'+pid,OK);
location.hash='#plugin-pipeline?id='+encodeURIComponent(pid);
}catch(err){toast('保存失败：'+err.message,ER);}
}
async function pgEnter(params){
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
$('pg-refresh-btn').addEventListener('click',async function(){
this.disabled=true;
try{
await postJSON('/api/plugins/refresh',{confirm:true});
}catch(err){toast('重扫端点未接驳（后端批），改用清单直读：'+err.message,WN);}
await pgLoad(true);
pgRenderList();
this.disabled=false;
toast('已重扫插件目录：已装 '+PG.list.length+' 个'+(PG.skipped.length?' · 跳过 '+PG.skipped.length+' 个':''),PG.skipped.length?WN:OK);
});
$('pg-new-btn').addEventListener('click',function(){location.hash='#plugins?new=1';});
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
