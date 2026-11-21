/*! 素材血统 tab（N-4 置信度+血统，正典=《musespiral-优化重构与后续开发总体方案-
 * 2026-11-19.md》§二 N-4 +《musespiral-素材闭环与专业编辑器设计-2026-11-18.md》
 * §2.2 血统图 UI）。iterations.js 同款独立产物（material.js 贴单 view 40KiB 锚，
 * 同 quickstart/tts 判例）：素材抽屉「血统」tab——
 *   正向树：provenance.chain（这条素材从哪来 → 经过什么加工，节点可点击回溯）
 *   反向列表：谁引用了这条（cascade 连坐面——衍生素材清单，可点击跳转）
 *   人工核验：POST /api/materials/verify 标记/取消 triage.verified（与 AI 自评
 *   confidence 正交——「AI 自评和人工核验必须可区分」）；三态徽与行内 confBadge
 *   同口径（✓ 已核验 / ? 待核验 / ⚠ 低置信<0.5）。
 * 自管理 DOM 与状态，零 ui.js 闭包依赖；入口=window.__mdpubMatLineage.{tab,repaint}，
 * repaint 由 @view:material 的 paintMatDrawer 钩子驱动（chip 与容器由本文件自注入）。
 */
(function(){
'use strict';
function E(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function req(p,o){return fetch(p,o).then(function(r){return r.json().then(function(d){if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));return d;});});}
var MTY={text:'文档',image:'图片',video:'视频',link:'链接',radar_signal:'雷达信号',crawled:'抓取',derived:'衍生'};
var OPS={summarize:'提炼摘要',synthesize:'跨素材综合',generate:'AI 生成',book_fetch:'书源取文',plugin_pipeline:'插件流水线',published_from:'文章反哺',distill:'论点提炼'};
var LOW=0.5; // 与 core.js CONF_LOW 同值（行内徽与筛选同口径；改值两处同步）
var V={id:'',tab:0,st:'',detail:null};
/* chip + 容器自注入（paintMatDrawer 每次重绘洗掉 DOM，repaint 时补挂） */
function ensure(host){
if(!host)return;
var chips=host.querySelector('.chips');
if(chips&&!E('mi-t2')){
var b=document.createElement('button');
b.type='button';b.id='mi-t2';b.className='chip';b.setAttribute('data-miit','2');
b.textContent='血统';
chips.appendChild(b);
}
if(!E('mi-lin')){
var dv=document.createElement('div');
dv.id='mi-lin';dv.hidden=true;
var anchor=E('mi-body');
anchor?anchor.insertAdjacentElement('afterend',dv):host.appendChild(dv);
}
}
function paint(){
var host=E('mat-drawer-body'),b=E('mi-lin'),t=E('mi-t2');
if(!host||!b)return;
if(t)t.classList.toggle('is-active',!!V.tab);
var mv=E('mi-view'),mb=E('mi-body');
if(mv)mv.hidden=!!V.tab;
if(mb)mb.hidden=!!V.tab;
b.hidden=!V.tab;
if(!V.tab)return;
if(V.st==='load'){b.innerHTML='<div class="skel"><div class="skel-line w90"></div><div class="skel-line w70"></div></div>';return;}
if(V.st==='err'){b.innerHTML='<p class="action-hint" style="color:var(--crimson)">读取血统失败：'+esc(V.err||'')+'</p>';return;}
var m=((V.detail||{}).material)||{},t3=((m.triage)||{}),c=t3.confidence;
/* 三态行 + 人工核验按钮（verified=人工判定，confidence=AI 自评——两字段不混写） */
var h='<div class="action-row" style="flex-wrap:wrap;gap:6px;align-items:center">';
if(t3.verified)h+='<span class="badge pass">✓ 已核验</span><span class="action-hint">人工核验通过</span><button class="btn btn-ghost" data-mver="0">取消核验</button>';
else if(c!=null&&c<LOW)h+='<span class="badge warn">⚠ 低置信 '+c+'</span><span class="action-hint">AI 自评低于 '+LOW+'，引用前先核对原文</span><button class="btn btn-primary" data-mver="1">标记核验</button>';
else if(c!=null)h+='<span class="badge">? 待核验 '+c+'</span><span class="action-hint">有 AI 自评，尚未人工核验</span><button class="btn" data-mver="1">标记核验</button>';
else h+='<span class="action-hint">无 AI 自评（原始采集物）</span><button class="btn" data-mver="1">标记核验</button>';
h+='</div>';
/* 正向树：这条素材从哪来 → 经过什么加工（provenance.chain，ref_statuses 就地态） */
var refs=(V.detail&&V.detail.material&&V.detail.material.references)||[];
h+='<div class="action-title" style="font-size:13px;margin:10px 0 4px">来源链（从哪来 · 经过什么加工）</div>';
if(!refs.length)h+='<p class="action-hint">原始素材（provenance 空，无上游）</p>';
else{
h+='<div class="lineage-chain"><span class="lineage-node self">'+esc(m.id)+'</span>';
refs.forEach(function(r){
var nid=esc(r.from_id);
h+='<span class="lineage-op">'+esc(OPS[r.op]||r.op||'引用')+' ↓</span>';
h+=(r.deleted?'<span class="lineage-node" style="border-color:var(--line-2);color:var(--ink-3)" title="原料已删（墓碑），不可跳转">'+nid+' · 已删</span>':'<a class="lineage-node" href="#material?m='+nid+'" title="跳到原料详情">'+nid+'</a>');
});
h+='</div>';
}
/* 反向列表：谁引用了这条（cascade=衍生素材清单，点击回溯跳转） */
var casc=(V.detail&&V.detail.cascade)||[];
h+='<div class="action-title" style="font-size:13px;margin:10px 0 4px">被引用（谁从我派生）</div>';
if(!casc.length)h+='<p class="action-hint">暂无衍生素材引用此条</p>';
else{
h+='<p class="action-hint">'+casc.length+' 条素材引用此条（删除连坐面）</p>';
casc.forEach(function(x){
h+='<div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--line-1)">'+
'<span class="tag">'+esc(MTY[x.type]||x.type||'素材')+'</span>'+
'<a href="#material?m='+esc(x.id)+'" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(x.title||x.id)+'</a>'+
'<span class="action-hint">'+esc(x.id)+'</span></div>';
});
}
b.innerHTML=h;
}
async function load(id){
V={id:id,tab:1,st:'load',detail:null};paint();
try{
V.detail=await req('/api/materials/detail?id='+encodeURIComponent(id));
V.st='ok';
}catch(e){V.st='err';V.err=e.message;}
paint();
}
window.__mdpubMatLineage={
tab:function(id){
if(!E('mi-lin'))return;
if(V.tab&&V.id===id){V.tab=0;paint();return;} // 再点一次回到内容 tab
load(id);
},
repaint:function(id){
var host=E('mat-drawer-body');
if(!host||!id)return;
if(V.id!==id)V={id:id,tab:0,st:'',detail:null}; // 换素材（含首开 V.id=''）重置回内容 tab
ensure(host);
paint();
/* 血统 tab 激活时抽屉重绘（核验后等）需重新拉详情（内容可能已变） */
if(V.tab)load(id);
}
};
/* tab 切换 + 核验动作（自挂委托——不改 material.js 的 matActsClick 分派）。
 * 点 内容/打磨历史（data-miit=0/1，material.js 委托）时本文件同步熄灭血统 tab。 */
document.addEventListener('click',function(e){
if(!E('mi-lin'))return;
if(e.target.closest('[data-miit="0"],[data-miit="1"]')){if(V.tab){V.tab=0;paint();}return;}
var v=e.target.closest('[data-mver]');
if(v){
var ok=v.getAttribute('data-mver')==='1',id=V.id;
v.disabled=true;
req('/api/materials/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,verified:ok,confirm:true})}).then(function(){
return load(id); // 重拉详情就地刷新三态
}).then(function(){
window.dispatchEvent(new Event('hashchange')); // 行内徽章随全量重绘更新（iterations.js revert 同款）
}).catch(function(err){
v.disabled=false;
var b=E('mi-lin');
if(b)b.insertAdjacentHTML('afterbegin','<p class="action-hint" style="color:var(--crimson)">核验更新失败：'+esc(err.message)+'</p>');
});
return;
}
var t=e.target.closest('[data-miit="2"]');
if(t&&V.id){window.__mdpubMatLineage.tab(V.id);}
});
})();
