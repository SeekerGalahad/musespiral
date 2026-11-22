/*! 小说双件套（FEAT-4，正典=《musespiral-极限架构与功能扩展总规划-2026-11-21》§FEAT-4）。
 * 创作工坊右栏两折叠区：🕸 人物关系力导向图（挂卡 meta.relations，SVG 自绘
 * 零依赖——不引入 D3）+ 📌 伏笔账本（session.foreshadowing 三态 planted/
 * escalated/payoff 徽章 + 推进/回收/删 + 埋伏笔）。
 *
 * 装载契约（同 rolling.js/lineage.js 判例——compose.js 贴 40KiB 锚，新功能
 * 一律独立产物不占视图产物）：
 *   - slug 自取 hash（#compose?s=），不依赖 ui.js 的 state 闭包；
 *   - 重载时机：hashchange + #cm-novel childList 变化（renderCompose 每次
 *     重建小说区——挂卡/摘卡/kind 切换/生成后自动跟上）+ 自身动作完成；
 *   - kind 条件显隐：非 novel 会话两折叠区 hidden（同 cnH 的 kind 双态纪律）；
 *   - 只消费两形态：GET /api/compose/session（详情 characters/foreshadowing）
 *     与 POST /api/compose/novel-summary（action=foreshadow_add/update/remove）。
 * prompt 注入的唯一事实源在后端（build_prompt 的 {{foreshadowing}} 插槽与
 * render_character 的关系段）——本文件只管展示与操作，绝不拼 prompt。
 * 力导向布局是纯函数 + 固定迭代数：同输入恒同输出（确定性收敛，重绘不跳动）。
 */
(function(){
'use strict';
var NV={busy:false};
function el(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function slug(){
if(!/^#compose(\?|$)/.test(location.hash))return '';
var kv=String(location.hash).split('?')[1]||'';
var hit=kv.split('&').filter(function(p){return p.slice(0,2)==='s=';})[0];
return hit?decodeURIComponent(hit.slice(2)):'';}
function req(path,opt){
return fetch(path,opt).then(function(r){return r.json().then(function(d){
if(!r.ok||(d&&d.ok===false))throw new Error((d&&d.error)||('HTTP '+r.status));
return d;});});}

// ── 人物关系：SVG 力导向（纯函数；排斥×弹簧×向心×阻尼的简化实现）──
function forceLayout(nn,edges,w,h,iters){
var px=[],py=[],vx=[],vy=[],cx=w/2,cy=h/2,R=Math.min(w,h)/2-34,i,j,k;
for(i=0;i<nn;i++){var a=2*Math.PI*i/nn-Math.PI/2;
px.push(cx+R*Math.cos(a));py.push(cy+R*Math.sin(a));vx.push(0);vy.push(0);}
for(k=0;k<iters;k++){
var fx=[],fy=[];
for(i=0;i<nn;i++){fx.push((cx-px[i])*0.02);fy.push((cy-py[i])*0.02);}
for(i=0;i<nn;i++)for(j=i+1;j<nn;j++){                       // 节点两两排斥（库仑）
var dx=px[j]-px[i],dy=py[j]-py[i],d2=dx*dx+dy*dy||0.01,d=Math.sqrt(d2),f=2600/d2,ux=dx/d,uy=dy/d;
fx[i]-=ux*f;fy[i]-=uy*f;fx[j]+=ux*f;fy[j]+=uy*f;}
for(var e=0;e<edges.length;e++){                            // 边弹簧（胡克）拉向目标长
i=edges[e][0];j=edges[e][1];
var dx2=px[j]-px[i],dy2=py[j]-py[i],dlen=Math.sqrt(dx2*dx2+dy2*dy2)||0.01,f2=(dlen-72)*0.02,ux2=dx2/dlen,uy2=dy2/dlen;
fx[i]+=ux2*f2;fy[i]+=uy2*f2;fx[j]-=ux2*f2;fy[j]-=uy2*f2;}
for(i=0;i<nn;i++){                                          // 阻尼积分 + 边界钳制
vx[i]=(vx[i]+fx[i])*0.85;vy[i]=(vy[i]+fy[i])*0.85;
px[i]+=vx[i];py[i]+=vy[i];
if(px[i]<28)px[i]=28;if(px[i]>w-28)px[i]=w-28;
if(py[i]<20)py[i]=20;if(py[i]>h-20)py[i]=h-20;}}
var out=[];for(i=0;i<nn;i++)out.push([px[i],py[i]]);
return out;
}
function graphSvg(cards){
var by={},nodes=[],edges=[];
(cards||[]).forEach(function(c){
if(!c||!c.name)return;
if(by[c.name]==null){by[c.name]=nodes.length;nodes.push({name:c.name,ghost:0});}
(c.relations||[]).forEach(function(r){
var to=String(r.to||'').trim();if(!to)return;
if(by[to]==null){by[to]=nodes.length;nodes.push({name:to,ghost:1});}   // 关系提及·未挂卡=幽灵节点
edges.push({a:by[c.name],b:by[to],relation:String(r.relation||'')});});
});
if(nodes.length<2)return '<p class="action-hint">挂两张以上带「关系」的人物卡后此处显示力导向关系图（在「新建人物」里按「人物名=关系」逐行登记）。</p>';
var w=300,h=216,pos=forceLayout(nodes.length,edges.map(function(e){return [e.a,e.b];}),w,h,140);
var L=edges.map(function(e){
var p=pos[e.a],q=pos[e.b];
return '<line x1="'+p[0].toFixed(1)+'" y1="'+p[1].toFixed(1)+'" x2="'+q[0].toFixed(1)+'" y2="'+q[1].toFixed(1)+'" class="cn-g-edge"><title>'+esc(nodes[e.a].name+' ↔ '+nodes[e.b].name+(e.relation?'：'+e.relation:''))+'</title></line>';
}).join('');
var N=nodes.map(function(nd,i){
var p=pos[i];
return '<g class="cn-g-node'+(nd.ghost?' ghost':'')+'"><circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="'+(nd.ghost?5:8)+'"><title>'+esc(nd.name+(nd.ghost?'（关系提及·未挂卡）':''))+'</title></circle><text x="'+p[0].toFixed(1)+'" y="'+(p[1]-(nd.ghost?9:13)).toFixed(1)+'" text-anchor="middle">'+esc(nd.name)+'</text></g>';
}).join('');
return '<svg class="cn-graph" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="人物关系力导向图">'+L+N+'</svg>'+
'<p class="action-hint">实心=挂卡人物 · 虚线=关系提及（未挂卡）；悬停边看关系描述。</p>';
}

// ── 伏笔账本面板（三态徽章；回收走 status=payoff 留痕，删=误录清理）──
var FORE_L={planted:'埋设',escalated:'推进',payoff:'已回收'};
function foreHtml(d){
var F=(d&&d.foreshadowing)||[],open=0;
F.forEach(function(x){if(x.status!=='payoff')open++;});
var rows=F.map(function(x){
var cls=x.status==='payoff'?'pass':(x.status==='escalated'?'warn':'queued');
var ops=(x.status!=='payoff'
?'<button class="btn" data-nvfn="es" data-fid="'+esc(x.id)+'">↗ 推进</button><button class="btn" data-nvfn="pay" data-fid="'+esc(x.id)+'">✓ 回收</button>'
:'<span class="tag done">留痕</span>')+
'<button class="btn mat-del" data-nvfn="rm" data-fid="'+esc(x.id)+'">×</button>';
return '<div class="cn-fore-row"><span class="badge '+cls+'">'+(FORE_L[x.status]||'埋设')+'</span><span class="cn-fore-note" title="'+esc(x.note)+'">'+esc(x.chapter?x.chapter+'：':'')+esc(x.note)+'</span><span class="cn-fore-ops">'+ops+'</span></div>';
}).join('');
return '<p class="action-hint">未回收 '+open+' / 共 '+F.length+' 条（生成 prompt 自动带未回收清单——仅提示不拦截）</p>'+
(rows||'<p class="action-hint">尚无伏笔——新章埋下的线索（物件/悬念/未交代之事）记在这里，AI 写作时会带着回收。</p>')+
'<div class="action-row"><input id="cm-fore-note" class="input" style="flex:1;min-width:120px" placeholder="伏笔描述（如：文件内容未交代）"><input id="cm-fore-ch" class="input" style="width:88px" placeholder="章节 选填"><button class="btn" id="cm-fore-add">＋ 埋伏笔</button></div>';
}

function paint(d){
var rel=el('cm-rel'),fore=el('cm-fore');
if(!rel||!fore)return;
var ok=d&&d.kind==='novel';
rel.hidden=fore.hidden=!ok;
if(!ok)return;
el('cm-rel-view').innerHTML=graphSvg((d&&d.characters)||[]);
el('cm-fore-view').innerHTML=foreHtml(d);
var add=el('cm-fore-add');
if(add)add.onclick=function(){addFore(this);};
var host=el('cm-fore-view');
host.onclick=function(ev){
var b=ev.target.closest('[data-nvfn]');
if(!b)return;
foreAct(b,b.getAttribute('data-nvfn'),b.getAttribute('data-fid'));};
}
async function load(){
var s=slug();
if(!s){paint(null);return;}
try{paint(await req('/api/compose/session?slug='+encodeURIComponent(s)));}catch(e){}}
async function post(body){
var s=slug();
if(!s){alert('先建会话');return;}
await req('/api/compose/novel-summary',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(Object.assign({slug:s,confirm:true},body))});}
async function addFore(btn){
if(NV.busy)return;
var note=(el('cm-fore-note')||{}).value||'';
note=String(note).trim();
if(!note){alert('伏笔描述不能为空');return;}
NV.busy=true;if(btn)btn.disabled=true;
try{await post({action:'foreshadow_add',note:note,chapter:String((el('cm-fore-ch')||{}).value||'').trim()});
await load();}
catch(e){alert('埋伏笔失败：'+e.message);}
finally{NV.busy=false;if(btn)btn.disabled=false;}}
async function foreAct(btn,kind,fid){
if(NV.busy)return;
NV.busy=true;if(btn)btn.disabled=true;
try{
if(kind==='rm')await post({action:'foreshadow_remove',id:fid});
else await post({action:'foreshadow_update',id:fid,status:kind==='es'?'escalated':'payoff'});
await load();}
catch(e){alert('伏笔操作失败：'+e.message);}
finally{NV.busy=false;if(btn)btn.disabled=false;}}
var nvBound=false; // ARCH-1 幂等门（骨架懒注入后 __onViewHtml 重调 boot；防双绑）
function boot(){
if(nvBound)return;
if(!el('cm-fore'))return;
nvBound=true;
window.addEventListener('hashchange',load);
// renderCompose 每次重建 #cm-novel（挂卡/摘卡/kind 切换/生成/裁决后）——
// 盯它 childList 即「会话数据可能变了」的通用重载信号（rolling.js 盯
// #cm-rounds 同款判例；此处盯 cm-novel 因本面板依赖 characters/kind 两面）。
var n=el('cm-novel');
if(n&&window.MutationObserver)new MutationObserver(load).observe(n,{childList:true});
load();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
else boot();
window.__mdpubNovel={slug:slug,esc:esc,paint:paint,boot:boot,graphSvg:graphSvg,foreHtml:foreHtml};
})();
