var DV={art:'',kind:'social',tpl:'xhs-card',spec:'xhs-3x4',params:{},svg:'',w:0,h:0,k:1,st:'idle',dirty:false,sel:-1,dpos:{},order:null,undo:[],redo:[],q:0,h:0,drag:null,pendSpec:'',slots:[],slotHexes:{},recent:[],loadedOnce:false};
var DSPECS=['xhs-3x4','xhs-4x5','square'],DSIZE={'xhs-3x4':[1242,1656],'xhs-4x5':[1242,1553],'square':[1080,1080]};
var DTXT={'xhs-card':{1:'tag',2:'title',3:'hook'}};
function dvSet(st){DV.st=st;dvSync();}
function dvSnap(){return{p:Object.assign({},DV.params),s:DV.svg,dp:JSON.stringify(DV.dpos),o:DV.order?DV.order.slice():null,spec:DV.spec};}
function dvCommit(pre){DV.undo.push(pre);if(DV.undo.length>50)DV.undo.shift();DV.redo=[];DV.dirty=true;dvSet('ready');}
function dvRestore(s){DV.params=Object.assign({},s.p);DV.svg=s.s;DV.dpos=JSON.parse(s.dp);DV.order=s.o?s.o.slice():null;DV.spec=s.spec;DV.sel=-1;dvMount();dvSync();}
function dvTravel(back){
if(DV.st!=='ready')return;
var src=back?DV.undo:DV.redo;
if(!src.length)return;
DV.q++;
(back?DV.redo:DV.undo).push(dvSnap());
dvRestore(src.pop());
DV.dirty=true;dvSet('ready');
}
function dvMount(){
var stage=$('dv-stage');
stage.innerHTML=DV.svg;
var svgEl=stage.querySelector('svg');
if(!svgEl){DV.svg='';dvSet('idle');return;}
DV.w=+svgEl.getAttribute('width')||DV.w;DV.h=+svgEl.getAttribute('height')||DV.h;
var kids=[],nodes=svgEl.children,i,n;
for(i=0;i<nodes.length;i++){n=nodes[i];
if(i===0&&n.tagName==='rect'&&+n.getAttribute('width')===DV.w)continue;
kids.push(n);}
if(!DV.order||DV.order.length!==kids.length)DV.order=kids.map(function(x,j){return j;});
else DV.order.forEach(function(oi){svgEl.appendChild(kids[oi]);});
kids.forEach(function(n2,j){
n2.setAttribute('data-o',j);
var t=DTXT[DV.tpl]&&DTXT[DV.tpl][j];
if(t)n2.setAttribute('data-t',t);
var p=DV.dpos[j];
if(p)n2.setAttribute('transform','translate('+p[0]+','+p[1]+')');
});
}
function dvHandles(){
var svgEl=$('dv-stage').querySelector('svg');
if(!svgEl)return;
var old=svgEl.querySelector('.dv-hd');
if(old)old.remove();
if(DV.sel<0)return;
var n=$('dv-stage').querySelector('[data-o="'+DV.sel+'"]');
if(!n)return;
var b=n.getBBox(),hh=9/(DV.k||1),h2=hh/2,cs=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]],g='<g class="dv-hd"><rect x="'+(b.x-4)+'" y="'+(b.y-4)+'" width="'+(b.width+8)+'" height="'+(b.height+8)+'" vector-effect="non-scaling-stroke"/>';
cs.forEach(function(c){g+='<rect class="h" x="'+(c[0]-h2)+'" y="'+(c[1]-h2)+'" width="'+hh+'" height="'+hh+'" vector-effect="non-scaling-stroke"/>';});
svgEl.insertAdjacentHTML('beforeend',g+'</g>');
}
function dvSelect(i){
DV.sel=i;
dvHandles();
$('dv-fwd').disabled=$('dv-bwd').disabled=i<0;
}
function dvMove(dir){
if(DV.sel<0||!DV.order)return;
var o=DV.order,at=o.indexOf(DV.sel),to=at+dir;
if(at<0||to<0||to>=o.length)return;
var pre=dvSnap();
o.splice(to,0,o.splice(at,1)[0]);
dvCommit(pre);
dvMount();dvSelect(DV.sel);
}
function dvGo(){clearTimeout(DV.h);DV.h=setTimeout(dvRenderNow,300);}
async function dvRenderNow(){
if(!DV.art)return;
var n=++DV.q;
try{
var r=await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:DV.spec,article:DV.art});
if(n!==DV.q)return;
DV.svg=r.svg;
dvMount();dvSet('ready');
}catch(err){
if(DV.svg)dvMount();
toast('渲染失败：'+err.message,ER);
}
}
async function dvLoad(){
var path=$('dv-art').value;
if(!path){toast('先选择文章',WN);return;}
DV.art=path;DV.loadedOnce=true;
dvSet('loading');
try{
var d=await getJSON('/api/visual/design?article='+encodeURIComponent(path)).catch(function(err){
if(!/无设计稿|HTTP 404/.test(err.message))throw err;
return postJSON('/api/visual/render',{confirm:true,template:'xhs-card',spec:DV.spec,article:path});
});
DV.tpl=d.template||'xhs-card';DV.kind=d.kind||'social';DV.spec=d.spec||DV.spec;
DV.params=Object.assign({},d.params||{});
DV.svg=d.svg||'';DV.dpos={};DV.order=null;DV.sel=-1;DV.undo=[];DV.redo=[];DV.dirty=false;
dvMount();dvSet('ready');
}catch(err){
dvSet('idle');
toast('载入失败：'+err.message,ER);
}
}
function dvSetSpec(spec){
if(spec===DV.spec)return;
if(DV.dirty){DV.pendSpec=spec;$('dv-sw').hidden=false;return;}
dvApplySpec(spec);
}
function dvApplySpec(spec){
if(DSPECS.indexOf(spec)<0)return;
DV.dpos={};DV.order=null;DV.sel=-1;DV.spec=spec;DV.dirty=true;
dvSelect(-1);dvSet('loading');dvRenderNow();
}
async function dvSave(){
if(!DV.art)return;
dvSet('saving');
try{
await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:DV.spec,svg:DV.svg});
DV.dirty=false;
dvSet('ready');
toast('已存稿（版本池先行）',OK);
}catch(err){
dvSet('ready');
toast('存稿失败：'+err.message,ER);
}
}
function dvRast(svg,w,h,name){
return new Promise(function(res,rej){
var img=new Image();
img.onload=function(){
var c=document.createElement('canvas');c.width=w;c.height=h;
c.getContext('2d').drawImage(img,0,0,w,h);
c.toBlob(function(b){
if(!b)return rej(new Error('A 路光栅失败'));
var fr=new FileReader();
fr.onload=function(){var a=document.createElement('a');a.href=String(fr.result);a.download=name;a.click();res();};
fr.onerror=function(){rej(new Error('A 路光栅失败'));};
fr.readAsDataURL(b);
},'image/png');
};
img.onerror=function(){rej(new Error('A 路光栅失败'));};
img.src='data:image/svg+xml,'+encodeURIComponent(svg);
});
}
async function dvExportGo(){
$('dv-ex').hidden=true;
var all=document.querySelector('#dv-ex-specs .is-active').getAttribute('data-exspec')==='all';
var route=document.querySelector('#dv-ex-route .is-active').getAttribute('data-exroute');
var specs=all?DSPECS:[DV.spec];
dvSet('exporting');
try{
if(route==='a'){
var stem=DV.art.replace(/^.*\//,'').replace(/\.md$/i,'');
for(var i=0;i<specs.length;i++){
var sp=specs[i],cur=sp===DV.spec;
var r=cur?{svg:DV.svg}:await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:sp,article:DV.art});
var wh=cur?[DV.w,DV.h]:DSIZE[sp];
await dvRast(r.svg,wh[0],wh[1],stem+'.'+(cur?'social':sp)+'.png');
}
toast('已导出 PNG×'+specs.length,OK);
}else{
if(DV.dirty)await dvSave();
for(var j=0;j<specs.length;j++){
if(specs[j]!==DV.spec)await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:specs[j]});
var d=await postJSON('/api/visual/export',{confirm:true,article:DV.art,kind:DV.kind});
var job=await waitForJob(d.job_id),rr=job.result||{};
if(job.status==='failed'||rr.ok===false)throw new Error(rr.error||job.error||'导出失败');
toast('已导出：'+(rr.png||'').split('/').pop()+' '+rr.size[0]+'×'+rr.size[1],OK);
}
}
}catch(err){toast('导出失败：'+err.message,ER);}
dvSet('ready');
}
async function dvLoadSlots(){
try{var d=await getJSON('/api/visual/templates');DV.slotHexes=d.slot_hexes||{};
DV.slots=((d.items||[])[0]||{}).color_slots||Object.keys(DV.slotHexes);
DV.recent=JSON.parse(localStorage.getItem('musespiral-dv-recent')||'[]');}catch(e){}
}
function dvRecentAdd(s){
DV.recent=[s].concat(DV.recent.filter(function(x){return x!==s;})).slice(0,5);
try{localStorage.setItem('musespiral-dv-recent',JSON.stringify(DV.recent));}catch(e){}
}
function dvSwatch(s){
return '<button class="chip'+(DV.params.background_slot===s?' is-active':'')+'" data-dslot="'+esc(s)+'" title="'+esc(s)+'" style="background:'+esc(DV.slotHexes[s]||'var(--paper-2)')+'"></button>';
}
function dvColorChips(){
var slots=DV.slots.length?DV.slots:['primary','text','muted','background','accent'];
var rec=DV.recent.filter(function(s){return slots.indexOf(s)>=0;});
$('dv-colors').innerHTML=(rec.length?'<span class="dv-cap">最近</span>'+rec.map(dvSwatch).join(''):'')+slots.map(dvSwatch).join('');
}
function dvSync(){
[['dspec',DV,'spec'],['dfs',DV.params,'font_size','M'],['dal',DV.params,'align','left'],['dslot',DV.params,'background_slot','background']].forEach(function(it){
var cur=it[1][it[2]];
if(cur==null)cur=it[3]||'';
QA('[data-'+it[0]+']').forEach(function(c){c.classList.toggle('is-active',c.getAttribute('data-'+it[0])===cur);});
});
$('dv-dirty').hidden=!DV.dirty;
$('dv-save-btn').disabled=!DV.dirty||DV.st!=='ready';
$('dv-export-btn').disabled=DV.st!=='ready';
$('dv-undo').hidden=!DV.undo.length;
if(DV.undo.length)$('dv-undo').textContent='撤销 '+DV.undo.length+' 步 · ⌘Z/⇧⌘Z';
$('dv-skel').hidden=DV.st!=='loading';
$('dv-stage').hidden=!DV.svg;
$('dv-empty').hidden=!!DV.svg||DV.st==='loading';
}
async function dvFillArts(){
await loadArticles();
var sel=$('dv-art'),cur=sel.value,opts='<option value="">选择文章…</option>';
state.articles.forEach(function(a){opts+='<option value="'+esc(a.path)+'">'+esc(a.title||a.path)+'</option>';});
sel.innerHTML=opts;
if(cur)sel.value=cur;
}
async function dvEnter(){
await dvFillArts();
await dvLoadSlots();dvColorChips();
if(DV.loadedOnce)return;
var path=new URLSearchParams((location.hash||'').split('?')[1]||'').get('article')||state.detailPath||($('dv-art').options[1]||{}).value;
if(path){$('dv-art').value=path;dvLoad();}
}
VAPI.design={enter:dvEnter};
function bindDesign(){
var on=function(id,fn){$(id).addEventListener('click',fn);};
$('dv-stage').addEventListener('pointerdown',function(e){
if(DV.st!=='ready')return;
var svgEl=$('dv-stage').querySelector('svg');
if(!svgEl)return;
DV.k=svgEl.getBoundingClientRect().width/(DV.w||1)||1;
var n=e.target.closest('[data-o]');
if(!n){dvSelect(-1);return;}
var i=+n.getAttribute('data-o');
if(DV.sel!==i)dvSelect(i);
DV.drag={n:n,i:i,sx:e.clientX,sy:e.clientY,moved:false,pre:dvSnap()};
if(n.setPointerCapture)n.setPointerCapture(e.pointerId);
e.preventDefault();
});
$('dv-stage').addEventListener('pointermove',function(e){
if(!DV.drag)return;
var d=DV.drag,dx=e.clientX-d.sx,dy=e.clientY-d.sy;
if(!d.moved&&Math.abs(dx)<3&&Math.abs(dy)<3)return;
if(!d.moved){d.moved=true;d.n.classList.add('dv-drag');}
var k=DV.k,gx=Math.round(dx/k/8)*8,gy=Math.round(dy/k/8)*8;
d.gx=gx;d.gy=gy;
d.n.setAttribute('transform','translate('+gx+','+gy+')');
});
$('dv-stage').addEventListener('pointerup',function(){
if(!DV.drag)return;
var d=DV.drag;DV.drag=null;
d.n.classList.remove('dv-drag');
if(!d.moved)return;
if(d.gx||d.gy){DV.dpos[d.i]=[d.gx,d.gy];dvCommit(d.pre);}
else d.n.removeAttribute('transform');
dvHandles();
});
$('dv-stage').addEventListener('dblclick',function(e){
if(DV.st!=='ready')return;
var n=e.target.closest('[data-t]');
if(!n)return;
var key=n.getAttribute('data-t'),tin=$('dv-tin'),s=$('dv-stage').querySelector('svg').getBoundingClientRect(),r=$('dv-wrap').getBoundingClientRect(),b=n.getBBox(),k=s.width/(DV.w||1);
tin.style.left=(s.left-r.left+b.x*k)+'px';
tin.style.top=(s.top-r.top+(b.y+b.height)*k+4)+'px';
tin.style.width=Math.max(140,b.width*k+28)+'px';
tin.setAttribute('data-dtk',key);
tin.value=String(DV.params[key]==null?'':DV.params[key]);
tin.hidden=false;tin.focus();tin.select();
});
$('dv-tin').addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key==='Enter')this.blur();
else if(e.key==='Escape'){this.removeAttribute('data-dtk');this.hidden=true;}
});
$('dv-tin').addEventListener('blur',function(){
var key=this.getAttribute('data-dtk');
this.hidden=true;
if(!key)return;
this.removeAttribute('data-dtk');
var v=this.value,pre=dvSnap();
if(String(DV.params[key]==null?'':DV.params[key])===v)return;
DV.params[key]=v;
dvCommit(pre);
dvGo();
});
$('view-design').addEventListener('click',function(e){
var t=e.target;
if(t.classList.contains('modal-overlay')){t.hidden=true;DV.pendSpec='';return;}
var b=t.closest('[data-dspec],[data-dslot],[data-dfs],[data-dal],[data-exspec],[data-exroute]');
if(!b)return;
if(b.hasAttribute('data-dspec'))return dvSetSpec(b.getAttribute('data-dspec'));
if(b.hasAttribute('data-exspec')||b.hasAttribute('data-exroute')){
b.parentElement.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('is-active',c===b);});
return;
}
var key=b.hasAttribute('data-dslot')?'background_slot':b.hasAttribute('data-dfs')?'font_size':'align';
var v=b.getAttribute('data-dslot')||b.getAttribute('data-dfs')||b.getAttribute('data-dal');
if(v===DV.params[key])return;
var pre=dvSnap();
DV.params[key]=v;
if(key==='background_slot'){dvRecentAdd(v);dvColorChips();}
dvCommit(pre);dvGo();
});
on('dv-fwd',function(){dvMove(1);});
on('dv-bwd',function(){dvMove(-1);});
on('dv-load-btn',function(){dvLoad();});
on('dv-empty-load',function(){dvLoad();});
on('dv-save-btn',function(){dvSave();});
on('dv-export-btn',function(){$('dv-ex').hidden=false;});
$('dv-art').addEventListener('change',function(){
if(DV.dirty){toast('先存稿再换文章',WN);this.value=DV.art||'';return;}
if(this.value)dvLoad();
});
on('dv-sw-cancel',function(){$('dv-sw').hidden=true;DV.pendSpec='';});
on('dv-sw-drop',function(){$('dv-sw').hidden=true;var s=DV.pendSpec;DV.pendSpec='';dvApplySpec(s);});
$('dv-sw-save').addEventListener('click',async function(){var s=DV.pendSpec;$('dv-sw').hidden=true;DV.pendSpec='';await dvSave();dvApplySpec(s);});
on('dv-ex-go',function(){dvExportGo();});
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if($('view-design').hidden)return;
if(e.target.closest&&e.target.closest('input,textarea,select'))return;
var m=e.metaKey||e.ctrlKey;
if(m&&(e.key==='z'||e.key==='Z')){e.preventDefault();e.shiftKey?dvTravel(0):dvTravel(1);return;}
if(e.key==='Escape'){
if(!$('dv-sw').hidden){$('dv-sw').hidden=true;DV.pendSpec='';return;}
if(!$('dv-ex').hidden){$('dv-ex').hidden=true;return;}
dvSelect(-1);
}
});
}
bindDesign();
