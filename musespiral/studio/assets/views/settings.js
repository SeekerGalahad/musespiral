var KBST=[];
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
if(!st){$('settings-sub').textContent='设置读取失败：'+(state.setFail||'服务未响应');
var kb=$('kb-sources-body');
if(kb)kb.innerHTML='<tr><td colspan="5">'+loadFailHTML('设置',state.setFail,'setFailRetry')+'</td></tr>';
return;}
var kb=st.kb||{},ai=st.ai||{},ap=ai.profiles||{},names=Object.keys(ap.profiles||{});
$('kb-root-input').value=kb.root||'';
$('kb-origin').innerHTML='根：'+(kb.root?(I(kb.root_exists?'check':'warning')+' '+esc(kb.root)):'未配置')+' · 源：'+(kb.origin==='kb_sources.json'?'kb_sources.json':kb.origin==='kb_root.txt'?'kb_root.txt 回退+内置默认源表':'内置默认源表');
KBST=(kb.sources||[]).slice();kbTable();
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
function setFailRetry(){return loadSettings().then(renderSettings);}
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
