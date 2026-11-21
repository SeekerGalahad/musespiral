/*! 素材打磨历史（P2 素材再加工循环，正典=musespiral-格式生态与素材流转能力分析-2026-11-20 §3.3）。
 * E-6 versions.js 同款独立产物（material.js 贴单 view 40KiB 锚，同 quickstart/tts 判例）：
 * 素材抽屉「打磨历史」tab——版本列表（v1/v2/…+时间+字数）+ 两版对比（服务端
 * /api/materials/iterations/diff 的 diff.html 红删绿增，复用 .ver-*、.dl-del、.dl-add）
 * + 回退（把旧版正文 POST /api/action/refine adopt 重新采纳为新版，原版保留）。
 * 自管理 DOM 与状态，零 ui.js 闭包依赖；入口=window.__mdpubMatIter.{tab,repaint}，
 * 由 @view:material 的 matActsClick 委托（data-miit）与 paintMatDrawer 重绘钩子驱动。
 */
(function(){
'use strict';
function E(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function req(p,o){return fetch(p,o).then(function(r){return r.json().then(function(d){if(!r.ok||(d&&d.ok===false))throw new Error((d&&(d.error||d.message))||('HTTP '+r.status));return d;});});}
var V={id:'',root:'',vs:[],sel:[],tab:0,st:'',diff:'',busy:0};
function paint(){
 var b=E('mi-body'),mv=E('mi-view'),t=E('mi-t1');
 if(!b)return;
 if(t)t.classList.toggle('is-active',!!V.tab);
 if(mv)mv.hidden=!!V.tab;
 b.hidden=!V.tab;
 if(!V.tab)return;
 if(V.st==='load'){b.innerHTML='<div class="skel"><div class="skel-line w90"></div><div class="skel-line w70"></div></div>';return;}
 if(V.st==='err'){b.innerHTML='<p class="action-hint" style="color:var(--crimson)">读取打磨历史失败：'+esc(V.err||'')+'</p>';return;}
 if(!(V.vs||[]).length){b.innerHTML='<p class="action-hint">尚无打磨版本——在详情点「AI 摘要」提炼并采纳后，各版本自动记录在此。</p>';return;}
 var h='<p class="action-hint">链根 <code>'+esc(V.root)+'</code> · '+V.vs.length+' 版 · 勾两版对比 · [回退]=该版正文重新采纳为新版（原版保留）</p>';
 h+=V.vs.map(function(v){
  return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line-1)">'+
  '<button class="chip-tag syn-chip'+(V.sel.indexOf(v.id)>=0?' on':'')+'" data-misel="'+esc(v.id)+'" title="勾选两版对比">v'+v.iteration+'</button>'+
  '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(v.title)+' · '+v.chars+' 字 · '+esc(String(v.ts||'').replace('T',' ').slice(0,16))+'</span>'+
  '<button class="btn btn-ghost" data-mirev="'+esc(v.id)+'" style="font-size:var(--fs-caption)"'+(V.busy?' disabled':'')+'>回退</button></div>';
 }).join('');
 h+='<div class="action-row"><button class="btn btn-primary" data-midiff="1"'+(V.sel.length===2&&!V.busy?'':' disabled title="先勾选两版"')+'>对比</button>'+
 (V.diff?'<button class="btn btn-ghost" data-midifx="1">收起对比</button>':'')+'</div>';
 h+='<div>'+V.diff+'</div>';
 b.innerHTML=h;
}
function pick(id){
 var i=V.sel.indexOf(id);
 if(i>=0)V.sel.splice(i,1);
 else{if(V.sel.length>1)V.sel.shift();V.sel.push(id);}
 V.diff='';paint();
}
async function load(id){
 V={id:id,root:'',vs:[],sel:[],tab:1,st:'load',diff:'',busy:0};paint();
 try{
  var d=await req('/api/materials/iterations?id='+encodeURIComponent(id));
  V.root=d.root||'';V.vs=d.versions||[];V.st='ok';
 }catch(e){V.st='err';V.err=e.message;}
 paint();
}
async function diff(btn){
 if(V.busy||V.sel.length!==2)return;
 V.busy=1;if(btn)btn.disabled=true;
 var s=E('mi-body').lastElementChild;
 if(s)s.innerHTML='<p class="action-hint">对比中…</p>';
 try{
  var d=await req('/api/materials/iterations/diff?a='+encodeURIComponent(V.sel[0])+'&b='+encodeURIComponent(V.sel[1]));
  V.diff=(d.diff&&d.diff.html)||'<p class="action-hint">两版内容相同。</p>';
 }catch(e){V.diff='<p class="action-hint" style="color:var(--crimson)">对比失败：'+esc(e.message)+'</p>';}
 V.busy=0;paint();
}
async function revert(vid,btn){
 var v=null;V.vs.forEach(function(x){if(x.id===vid)v=x;});
 if(!v||V.busy)return;
 if(!window.confirm('回退到 v'+v.iteration+'？\n把该版正文重新采纳为新版 v'+(v.iteration+1)+'——链上追加，原版保留。'))return;
 V.busy=1;if(btn)btn.disabled=true;
 try{
  var d=await req('/api/materials/detail?id='+encodeURIComponent(vid));
  var t=((d.material||{}).payload||{}).text||'';
  if(!t.trim())throw new Error('该版无正文面');
  await req('/api/action/refine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'adopt',id:vid,text:t,confirm:true})});
  await load(vid);
  window.dispatchEvent(new Event('hashchange'));
 }catch(e){
  V.busy=0;paint();
  var b=E('mi-body');
  if(b)b.insertAdjacentHTML('afterbegin','<p class="action-hint" style="color:var(--crimson)">回退失败：'+esc(e.message)+'</p>');
 }
}
document.addEventListener('click',function(e){
 if(!E('mi-body'))return;
 var el=e.target.closest('[data-misel],[data-midiff],[data-midifx],[data-mirev]');
 if(!el)return;
 if(el.hasAttribute('data-misel'))return pick(el.getAttribute('data-misel'));
 if(el.hasAttribute('data-midiff'))return diff(el);
 if(el.hasAttribute('data-midifx')){V.diff='';return paint();}
 if(el.hasAttribute('data-mirev'))return revert(el.getAttribute('data-mirev'),el);
});
window.__mdpubMatIter={tab:function(on,id){if(on)load(id);else{V.tab=0;paint();}},repaint:function(id){
 if(id&&id!==V.id){if(V.tab)load(id);else paint();return;}
 paint();
}};
})();
