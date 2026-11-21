/*! TTS 朗读面板（G-T5，正典=《musespiral-调研吸收功能设计-2026-11-18》§六）。
 * 文章详情工具条「🔊 朗读」按钮 → job tts_generate → 完成后内嵌
 * <audio controls> 播放（/api/tts/file?id=<root 相对路径>）。
 *
 * 装载契约（与 outline.js/rolling.js/dual.js 同款：core.js 已贴 M3 200KiB
 * 拆分线，新功能一律独立产物不占 core.js）：ui.js 仅在懒加载循环里加一个
 * 文件名，本文件自管理 DOM 与状态——hash 自取当前文章路径，不依赖 ui.js
 * 的 state 闭包。
 *
 * 为什么不复用 ui.js 的 waitForJob：那是 core.js 内部闭包函数，跨文件不可见；
 * 此处自带一个最小轮询（800ms，与 waitForJob 同节拍同上限），失败/超时可读。
 *
 * 零外联：只用相对路径 /api/action/tts_generate 与 /api/tts/file。
 */
(function(){
'use strict';

/*__TTS_BEGIN__*/
// ── 纯函数区（tests 的 node 行为断言抽取本段执行，不触 DOM）──
// 当前文章路径：只认 #article?path=，其它视图返回空串（按钮点了也无意义）
function ttsPath(hash){
var raw=String(hash||'');
if(!/^#article(\?|$)/.test(raw))return '';
var q=raw.indexOf('?');
if(q<0)return '';
var pairs=raw.slice(q+1).split('&'),i,kv;
for(i=0;i<pairs.length;i++){
kv=pairs[i].split('=');
if(kv[0]==='path'&&kv.length>1){
try{return decodeURIComponent(kv.slice(1).join('='));}catch(e){return '';}
}}
return '';
}
// 产物 URL：把 root 相对路径塞进 /api/tts/file?id=（encodeURIComponent 全编码）
function ttsUrl(rel){
return '/api/tts/file?id='+encodeURIComponent(String(rel||''));
}
// 摘要行：文件名 · 字数 · 字节（人读的三项，不显示内部字段名）
function ttsSummary(r){
r=r||{};
var name=String(r.path||r.output||'').split('/').pop();
var kb=Math.max(0,Math.round((+r.bytes||0)/1024));
return name+(r.chars?' · '+r.chars+' 字':'')+(kb?' · '+kb+' KB':'');
}
// 按钮态：合成中禁用并换文案（避免重复提交同一 job）
function ttsBtnState(busy){
return busy?'⏳ 合成中…':'🔊 朗读';
}
/*__TTS_END__*/

function ttsEl(id){return document.getElementById(id);}
function ttsReq(path,opt){return fetch(path,opt).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));
return d;});});}
var T={job:null,busy:false};

function ttsNote(text){var n=ttsEl('tts-note');if(n)n.textContent=text||'';}

function ttsShow(rel,result){
var bar=ttsEl('tts-bar'),audio=ttsEl('tts-audio');
if(!bar||!audio)return;
audio.src=ttsUrl(rel);
bar.hidden=false;
ttsNote(ttsSummary(result));
}

async function ttsWait(id){
for(var i=0;i<900;i++){
var j=await ttsReq('/api/jobs/'+encodeURIComponent(id));
if(j.status==='done'||j.status==='failed')return j;
await new Promise(function(r){setTimeout(r,800);});
}
throw new Error('等待任务超时');
}

async function ttsGenerate(btn){
if(T.busy)return;
var rel=ttsPath(location.hash);
if(!rel){ttsNote('先打开一篇文章再朗读');return;}
T.busy=true;
if(btn){btn.disabled=true;btn.textContent=ttsBtnState(true);}
ttsNote('合成中（长文需数十秒，进度见右上任务中心）…');
try{
var sub=await ttsReq('/api/action/tts_generate',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({article:rel,confirm:true})});
var job=await ttsWait(sub.job_id);
if(job.status==='failed')throw new Error(job.error||'合成失败');
var r=job.result||{};
ttsShow(r.path||r.output||'',r);
ttsNote('朗读已就绪：'+ttsSummary(r));
}catch(e){ttsNote('朗读失败：'+e.message);}
finally{
T.busy=false;
if(btn){btn.disabled=false;btn.textContent=ttsBtnState(false);}
}
}

function ttsClose(){
var bar=ttsEl('tts-bar'),audio=ttsEl('tts-audio');
if(audio){try{audio.pause();}catch(e){}audio.removeAttribute('src');try{audio.load();}catch(e){}}
if(bar)bar.hidden=true;
ttsNote('');
}

function ttsBoot(){
var btn=ttsEl('tts-btn');
if(!btn)return;
btn.addEventListener('click',function(){ttsGenerate(btn);});
var close=ttsEl('tts-close');
if(close)close.addEventListener('click',ttsClose);
// 切文章：旧音频属于上一篇文章——收起（避免「看 A 听 B」的错配）
window.addEventListener('hashchange',function(){if(!ttsEl('tts-bar').hidden)ttsClose();});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ttsBoot);
else ttsBoot();
window.__mdpubTts={ttsPath:ttsPath,ttsUrl:ttsUrl,ttsSummary:ttsSummary,ttsBtnState:ttsBtnState};
})();
