var DV={art:'',kind:'social',tpl:'xhs-card',spec:'xhs-3x4',params:{},svg:'',w:0,h:0,k:1,st:'idle',dirty:false,sel:-1,dpos:{},order:null,undo:[],redo:[],q:0,h:0,drag:null,pendSpec:'',slots:[],slotHexes:{},recent:[],loadedOnce:false,slotFail:'',tplFail:'',tpls:[],tmeta:{},bg:null,lprops:{},pick:null};
var DSPECS=['xhs-3x4','xhs-4x5','square'],DSIZE={'xhs-3x4':[1242,1656],'xhs-4x5':[1242,1553],'square':[1080,1080]};
var DTXT={'xhs-card':{1:'tag',2:'title',3:'hook'}};
var DBGTS=['solid','linear','radial','pattern','image'],DBG_LABEL={solid:'纯色',linear:'线性渐变',radial:'径向渐变',pattern:'纹样',image:'图片'};
var DPATS=['dots','grid','waves','paper'];
var DFITS=['cover','contain'],DMASKS=['none','rounded','circle','arch'];
var DBLENDS=['normal','multiply','screen','overlay'];
function dvSet(st){DV.st=st;dvSync();}
function dvSnap(){return{p:Object.assign({},DV.params),s:DV.svg,dp:JSON.stringify(DV.dpos),o:DV.order?DV.order.slice():null,spec:DV.spec,bg:DV.bg?JSON.parse(JSON.stringify(DV.bg)):null,lp:JSON.parse(JSON.stringify(DV.lprops))};}
function dvCommit(pre){DV.undo.push(pre);if(DV.undo.length>50)DV.undo.shift();DV.redo=[];DV.dirty=true;dvSet('ready');}
function dvRestore(s){DV.params=Object.assign({},s.p);DV.svg=s.s;DV.dpos=JSON.parse(s.dp);DV.order=s.o?s.o.slice():null;DV.spec=s.spec;DV.bg=s.bg?JSON.parse(JSON.stringify(s.bg)):null;DV.lprops=s.lp?JSON.parse(JSON.stringify(s.lp)):{};DV.sel=-1;dvMount();dvSync();}
function dvTravel(back){
if(DV.st!=='ready')return;
var src=back?DV.undo:DV.redo;
if(!src.length)return;
DV.q++;
(back?DV.redo:DV.undo).push(dvSnap());
dvRestore(src.pop());
DV.dirty=true;dvSet('ready');
}
function dvIsBgNode(n,ord){
var cls=String(n.getAttribute('class')||'');
if(cls.indexOf('dvbg')>=0||cls.indexOf('dvclip')>=0)return true;
return ord===0&&n.tagName==='rect'&&+n.getAttribute('width')===DV.w;
}
function dvMount(){
var stage=$('dv-stage');
stage.innerHTML=DV.svg;
var svgEl=stage.querySelector('svg');
if(!svgEl){DV.svg='';dvSet('idle');return;}
DV.w=+svgEl.getAttribute('width')||DV.w;DV.h=+svgEl.getAttribute('height')||DV.h;
var kids=[],nodes=svgEl.children,i,n;
for(i=0;i<nodes.length;i++){n=nodes[i];
if(dvIsBgNode(n,i))continue;
kids.push(n);}
if(!DV.order||DV.order.length!==kids.length)DV.order=kids.map(function(x,j){return j;});
else DV.order.forEach(function(oi){svgEl.appendChild(kids[oi]);});
kids.forEach(function(n2,j){
n2.setAttribute('data-o',j);
var t=DTXT[DV.tpl]&&DTXT[DV.tpl][j];
if(t)n2.setAttribute('data-t',t);
var p=DV.dpos[j],lp=DV.lprops[j]||{};
if(p)n2.setAttribute('transform','translate('+p[0]+','+p[1]+')');
if(lp.opacity!=null&&lp.opacity!==1)n2.setAttribute('opacity',String(lp.opacity));else n2.removeAttribute('opacity');
if(lp.blend&&lp.blend!=='normal')n2.setAttribute('style','mix-blend-mode:'+lp.blend);else n2.removeAttribute('style');
n2.style.display=lp.visible===false?'none':'';
if(lp.fill&&n2.tagName==='text')n2.setAttribute('fill',lp.fill);
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
dvLayersPaint();dvPropPaint();
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
var r=await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:DV.spec,article:DV.art,background:dvBgOut()});
if(n!==DV.q)return;
DV.svg=r.svg;
if(r.params)DV.params=Object.assign({},DV.params,r.params);
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
DV.bg=(d.background&&typeof d.background==='object'&&d.background.kind)?d.background:null;
DV.lprops={};DV.dpos={};
(d.elements||[]).forEach(function(rw){
var i=rw.i;
DV.lprops[i]={opacity:rw.opacity!=null?rw.opacity:1,locked:!!rw.locked,visible:rw.visible!==false,blend:rw.blend||'normal',fill:rw.fill||''};
if(rw.dx||rw.dy)DV.dpos[i]=[rw.dx||0,rw.dy||0];
});
var zs=(d.elements||[]).slice().sort(function(a,b){return a.z-b.z;}).map(function(rw){return rw.i;});
if(zs.length)DV.order=zs;
dvTplsPaint();dvSpecsPaint();
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
await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:DV.spec,svg:DV.svg,background:dvBgOut(),elements:dvLayersOut()});
DV.dirty=false;
dvSet('ready');
toast('已存稿（版本池先行）',OK);
}catch(err){
dvSet('ready');
toast('存稿失败：'+err.message,ER);
}
}
function dvBgOut(){
if(!DV.bg||!DV.bg.kind)return null;
if(DV.bg.kind==='image'&&!DV.bg.image)return null;
var bg={kind:DV.bg.kind,color:DV.bg.color||'#ffffff',angle:+DV.bg.angle||135,pattern:DV.bg.pattern||'dots',image:DV.bg.image||'',overlay_alpha:+DV.bg.overlay_alpha||0};
if(DV.bg.kind==='linear'||DV.bg.kind==='radial'){
var st=DV.bg.stops&&DV.bg.stops.length===2?DV.bg.stops:[[0,'#ffffff'],[1,'#dbeafe']];
bg.stops=[[+st[0][0]||0,st[0][1]],[+st[1][0]||1,st[1][1]]];
}
return bg;
}
function dvLayersOut(){
var o=DV.order||[];
return o.map(function(i,pos){
var lp=DV.lprops[i]||{},d=DV.dpos[i]||[0,0];
var row={i:i,z:pos,opacity:lp.opacity!=null?lp.opacity:1,locked:!!lp.locked,visible:lp.visible!==false,blend:lp.blend||'normal',dx:Math.round(d[0]),dy:Math.round(d[1])};
if(lp.fill)row.fill=lp.fill;
return row;
});
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
var r=cur?{svg:DV.svg}:await postJSON('/api/visual/render',{confirm:true,template:DV.tpl,params:DV.params,spec:sp,article:DV.art,background:dvBgOut()});
var wh=cur?[DV.w,DV.h]:DSIZE[sp];
await dvRast(r.svg,wh[0],wh[1],stem+'.'+(cur?'social':sp)+'.png');
}
toast('已导出 PNG×'+specs.length+'（浏览器下载目录——自动化/无弹窗环境请改用 B 路导出）',OK);
}else{
if(DV.dirty)await dvSave();
for(var j=0;j<specs.length;j++){
if(specs[j]!==DV.spec)await postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,template:DV.tpl,params:DV.params,spec:specs[j]});
var d=await postJSON('/api/visual/export',{confirm:true,article:DV.art,kind:DV.kind});
var job=await waitForJob(d.job_id),rr=job.result||{};
if(job.status==='failed'||rr.ok===false)throw new Error(rr.error||job.error||'导出失败');
toast('已导出：'+(rr.png||'').split('/').pop()+' '+rr.size[0]+'×'+rr.size[1]+'（在文章旁 .design/ 目录）',OK);
}
}
}catch(err){toast('导出失败：'+err.message,ER);}
dvSet('ready');
}
async function dvLoadSlots(){
try{var d=await getJSON('/api/visual/templates');DV.slotHexes=d.slot_hexes||{};
DV.slots=((d.items||[])[0]||{}).color_slots||Object.keys(DV.slotHexes);
DV.tpls=(d.items||[]);
DV.tpls.forEach(function(m){DV.tmeta[m.id]=m;});
DV.slotFail='';DV.tplFail='';
dvTplsPaint();
}catch(err){DV.slotFail=err.message||'服务未响应';DV.tplFail=DV.slotFail;}
try{DV.recent=JSON.parse(localStorage.getItem('musespiral-dv-recent')||'[]');}catch(e){}
}
async function dvSlotsRetry(){await dvLoadSlots();dvColorChips();}
function dvRecentAdd(s){
DV.recent=[s].concat(DV.recent.filter(function(x){return x!==s;})).slice(0,5);
try{localStorage.setItem('musespiral-dv-recent',JSON.stringify(DV.recent));}catch(e){}
}
function dvSwatch(s){
return '<button class="chip'+(DV.params.background_slot===s?' is-active':'')+'" data-dslot="'+esc(s)+'" title="'+esc(s)+'" style="background:'+esc(DV.slotHexes[s]||'var(--paper-2)')+'"></button>';
}
function dvColorChips(){
if(DV.slotFail){$('dv-colors').innerHTML=loadFailHTML('色槽列表',DV.slotFail,'dvSlotsRetry');return;}
var slots=DV.slots.length?DV.slots:['primary','text','muted','background','accent'];
var rec=DV.recent.filter(function(s){return slots.indexOf(s)>=0;});
$('dv-colors').innerHTML=(rec.length?'<span class="dv-cap">最近</span>'+rec.map(dvSwatch).join(''):'')+slots.map(dvSwatch).join('');
}
var DTPL_LABEL={'vermilion-slab':'朱砂厚板','indigo-slab':'靛蓝厚板','xhs-card':'社媒卡','points-three':'三要点','article-long':'图文长页','xhs-cover-pro':'小红书封面','interview-card':'访谈卡','list-three':'三点卡','quote-poster':'金句海报','chapter-divider':'章节分隔'};
function dvTplsPaint(){
var host=$('dv-tpls');
if(DV.tplFail){host.innerHTML=loadFailHTML('模板注册表',DV.tplFail,'dvSlotsRetry');return;}
if(!DV.tpls.length){host.innerHTML='<p class="dv-hint">模板加载中…</p>';return;}
host.innerHTML=DV.tpls.map(function(m){
return '<button class="dv-tpl'+(DV.tpl===m.id?' is-active':'')+'" data-dtpl="'+esc(m.id)+'"><b>'+esc(DTPL_LABEL[m.id]||m.id)+'</b><span>'+esc(m.kind)+'</span></button>';
}).join('');
}
function dvSetTpl(id){
if(id===DV.tpl)return;
var m=DV.tmeta[id];
if(!m)return;
if(DV.dirty){toast('先存稿再换模板',WN);return;}
var pre=dvSnap();
DV.tpl=id;DV.kind=m.kind;DV.params={};DV.bg=null;DV.lprops={};DV.dpos={};DV.order=null;DV.sel=-1;
DV.spec=(m.specs&&m.specs.indexOf(DV.spec)>=0)?DV.spec:m.specs[0];
dvTplsPaint();dvSpecsPaint();
dvCommit(pre);
dvSelect(-1);dvSet('loading');dvRenderNow();
}
function dvSpecsPaint(){
var m=DV.tmeta[DV.tpl],specs=(m&&m.specs)||DSPECS;
var LABEL={'xhs-3x4':'3:4','xhs-4x5':'4:5','square':'1:1','wide16x9':'16:9','mp':'mp','article-long':'长图'};
DSPECS=specs.slice();
$('dv-specs').innerHTML=specs.map(function(sp){return '<button class="chip'+(DV.spec===sp?' is-active':'')+'" data-dspec="'+esc(sp)+'">'+esc(LABEL[sp]||sp)+'</button>';}).join('');
}
function dvLayerName(i,n){
if(!n)return '图层'+i;
if(n.tagName==='text'){
var t=n.getAttribute('data-t');
if(t)return t;
var s=(n.textContent||'').trim().slice(0,10);
return s||'文字';
}
if(n.tagName==='image')return '图片';
return {rect:'矩形',circle:'圆',line:'线',path:'路径',g:'组'}[n.tagName]||n.tagName;
}
function dvLayersPaint(){
var host=$('dv-layers');
if(!host)return;
if(!DV.svg){host.innerHTML='<p class="dv-hint">先载入设计稿</p>';return;}
var o=DV.order||[],rows=[];
for(var j=o.length-1;j>=0;j--){
var i=o[j],lp=DV.lprops[i]||{};
var n=$('dv-stage').querySelector('[data-o="'+i+'"]');
rows.push('<div class="dv-lrow'+(DV.sel===i?' is-active':'')+(lp.locked?' is-locked':'')+(lp.visible===false?' eyehidden':'')+'" data-lrow="'+i+'" draggable="true">'
+'<span class="dv-lname">'+esc(dvLayerName(i,n))+'</span><span class="dv-lz">z'+j+'</span>'
+'<button class="dv-leye'+(lp.visible===false?' is-off':'')+'" data-dleye="'+i+'" title="'+(lp.visible===false?'显示':'隐藏')+'">'+(lp.visible===false?'⭘':'👁')+'</button>'
+'<button class="dv-lok'+(lp.locked?' is-on':'')+'" data-dlok="'+i+'" title="'+(lp.locked?'解锁':'锁定')+'">'+(lp.locked?'🔒':'⭘')+'</button></div>');
}
host.innerHTML=rows.join('')||'<p class="dv-hint">无元素</p>';
}
function dvLp(i){var lp=DV.lprops[i];if(!lp){lp={opacity:1,locked:false,visible:true,blend:'normal',fill:''};DV.lprops[i]=lp;}return lp;}
function dvToggleEye(i){
var pre=dvSnap(),lp=dvLp(i),n=$('dv-stage').querySelector('[data-o="'+i+'"]');
lp.visible=lp.visible===false?true:false;
if(n)n.style.display=lp.visible===false?'none':'';
dvCommit(pre);dvLayersPaint();
}
function dvToggleLock(i){
var pre=dvSnap(),lp=dvLp(i);
lp.locked=!lp.locked;
dvCommit(pre);dvLayersPaint();
}
var dvDragRow=null;
function dvRowDrop(target,before){
var o=DV.order||[];
var src=o.indexOf(dvDragRow),tgt=o.indexOf(target);
if(src<0||tgt<0||src===tgt)return;
var pre=dvSnap();
o.splice(src,1);
tgt=o.indexOf(target);
o.splice(before?tgt:tgt+1,0,dvDragRow);
dvCommit(pre);
dvMount();dvSelect(dvDragRow);
}
function dvSelNode(){return DV.sel<0?null:$('dv-stage').querySelector('[data-o="'+DV.sel+'"]');}
function dvTextParam(n){
var t=n.getAttribute('data-t');
if(t&&t in DV.params)return{key:t,full:String(DV.params[t]||''),seg:-1};
var txt=(n.textContent||'').trim();
var ks=Object.keys(DV.params);
for(var a=0;a<ks.length;a++){
var v=DV.params[ks[a]];
if(typeof v==='string'&&v===txt)return{key:ks[a],full:v,seg:-1};
}
if(DV.params.paragraphs){
var segs=String(DV.params.paragraphs).split('\n\n');
for(var b=0;b<segs.length;b++){
if(segs[b].trim()===txt)return{key:'paragraphs',full:DV.params.paragraphs,seg:b};
}
}
return null;
}
function dvImageSlotKey(n){
var href=n.getAttribute('href')||'';
var m=DV.tmeta[DV.tpl],schema=(m&&m.params_schema)||{};
var first=null;
for(var k in schema){
if(schema[k].type!=='image_slot')continue;
if(!first)first=k;
var v=DV.params[k];
var src=(v&&typeof v==='object')?v.src:v;
if(src&&src===href)return k;
}
return first;
}
var DSLOT_LABEL={hero_image:'头图',avatar:'头像'};
function dvEmptyImageSlots(){
var m=DV.tmeta[DV.tpl],schema=(m&&m.params_schema)||{},out=[];
for(var k in schema){
if(schema[k].type!=='image_slot')continue;
var v=DV.params[k];
if(!((v&&typeof v==='object')?v.src:v))out.push(k);
}
return out;
}
function dvPropPaint(){
var host=$('dv-prop-el'),n=dvSelNode(),empty=dvEmptyImageSlots();
if(!n&&!empty.length){host.hidden=true;host.innerHTML='';return;}
var html='';
if(!n){
html='<p class="dv-prop-title">空图片槽</p>'+empty.map(function(k){
return '<div class="dv-prop-row"><label>'+esc(DSLOT_LABEL[k]||k)+'</label><button class="btn" data-dvpick="slot" data-dvslot="'+esc(k)+'">从素材库选图</button></div>';
}).join('')+'<p class="dv-hint">选图后点画布图片元素可调 fit/蒙版</p>';
host.innerHTML=html;host.hidden=false;return;
}
var i=DV.sel,lp=dvLp(i);
html+='<p class="dv-prop-title">元素属性 · '+esc(dvLayerName(i,n))+'</p>';
if(n.tagName==='text'){
var tp=dvTextParam(n);
html+='<div class="dv-prop-row"><label>内容</label><input class="input" id="dv-prop-text" data-dvkey="'+esc(tp?tp.key:'')+'" value="'+esc(tp?tp.full:'')+'"'+(tp?'':' disabled')+'></div>';
if(tp&&tp.key==='paragraphs')html+='<p class="dv-hint">多段正文（空行分段）——编辑的是整个 paragraphs 槽</p>';
html+='<div class="dv-prop-row"><label>颜色</label><input type="color" id="dv-prop-fill" value="'+esc(lp.fill||'#1f2937')+'"></div>';
html+='<p class="dv-hint">字号/对齐用下方「排版」chips</p>';
}else if(n.tagName==='image'){
var key=dvImageSlotKey(n);
if(key){
var v=DV.params[key],cur=(v&&typeof v==='object')?v:{src:'',fit:'cover',mask:'none'};
html+='<div class="dv-prop-row"><label>图片</label><button class="btn" data-dvpick="slot" data-dvslot="'+esc(key)+'">从素材库选图</button></div>';
html+='<div class="dv-prop-row"><label>填充</label><div class="chips">'+DFITS.map(function(f){return '<button class="chip'+(cur.fit===f?' is-active':'')+'" data-dvfit="'+esc(key)+'" data-fv="'+f+'">'+(f==='cover'?'铺满':'完整')+'</button>';}).join('')+'</div></div>';
html+='<div class="dv-prop-row"><label>蒙版</label><div class="chips">'+DMASKS.map(function(mk){return '<button class="chip'+(cur.mask===mk?' is-active':'')+'" data-dvmask="'+esc(key)+'" data-fv="'+mk+'">'+({none:'无',rounded:'圆角',circle:'圆形',arch:'拱形'}[mk])+'</button>';}).join('')+'</div></div>';
}else html+='<p class="dv-hint">该图片元素不属模板槽位（仅可调层级/透明度）</p>';
}
if(empty.length&&n.tagName!=='image'){
html+='<p class="dv-prop-title">空图片槽</p>'+empty.map(function(k){
return '<div class="dv-prop-row"><label>'+esc(DSLOT_LABEL[k]||k)+'</label><button class="btn" data-dvpick="slot" data-dvslot="'+esc(k)+'">选图</button></div>';
}).join('');
}
html+='<div class="dv-prop-row"><label>透明</label><input type="range" min="0" max="1" step="0.05" value="'+(lp.opacity!=null?lp.opacity:1)+'" id="dv-prop-op" style="flex:1"></div>';
html+='<div class="dv-prop-row"><label>混合</label><div class="chips">'+DBLENDS.map(function(bl){return '<button class="chip'+((lp.blend||'normal')===bl?' is-active':'')+'" data-dvblend="'+bl+'">'+bl+'</button>';}).join('')+'</div></div>';
host.innerHTML=html;
host.hidden=false;
}
function dvBgParamsPaint(){
var host=$('dv-bg-params'),bg=DV.bg;
QA('#dv-bgt .chip').forEach(function(c){c.classList.toggle('is-active',bg&&c.getAttribute('data-dbgt')===bg.kind);});
if(!bg){host.innerHTML='<p class="dv-hint">默认=模板背景色槽（v1 语义）</p>';return;}
if(bg.kind==='solid'){
host.innerHTML='<div class="dv-prop-row"><label>颜色</label><input type="color" data-dvbg="color" value="'+esc(bg.color||'#ffffff')+'"></div>';
}else if(bg.kind==='linear'||bg.kind==='radial'){
var st=(bg.stops&&bg.stops.length===2)?bg.stops:[[0,'#ffffff'],[1,'#dbeafe']];
host.innerHTML='<div class="dv-prop-row"><label>起色</label><input type="color" data-dvbg="s0" value="'+esc(st[0][1])+'"></div>'
+'<div class="dv-prop-row"><label>止色</label><input type="color" data-dvbg="s1" value="'+esc(st[1][1])+'"></div>'
+(bg.kind==='linear'?'<div class="dv-prop-row"><label>角度</label><input class="input" type="number" min="0" max="360" data-dvbg="angle" value="'+(+bg.angle||135)+'"></div>':'');
}else if(bg.kind==='pattern'){
host.innerHTML='<div class="dv-prop-row"><label>底色</label><input type="color" data-dvbg="color" value="'+esc(bg.color||'#ffffff')+'"></div>'
+'<div class="dv-prop-row"><label>纹样</label><div class="chips dv-pat">'+DPATS.map(function(pt){return '<button class="chip'+((bg.pattern||'dots')===pt?' is-active':'')+'" data-dvpat="'+pt+'" title="'+pt+'">'+({dots:'·点',grid:'▦格',waves:'〜波',paper:'▤纸'}[pt])+'</button>';}).join('')+'</div></div>';
}else{
host.innerHTML='<div class="dv-prop-row"><label>图片</label><button class="btn" data-dvpick="bg">从素材库选图</button></div>'
+'<div class="dv-prop-row"><label>蒙纱</label><input type="range" min="0" max="1" step="0.05" value="'+(+bg.overlay_alpha||0)+'" data-dvbg="overlay_alpha" style="flex:1"><span class="dv-lz">'+Math.round(+bg.overlay_alpha||0)+'</span></div>'
+'<div class="dv-prop-row"><label>纱色</label><input type="color" data-dvbg="color" value="'+esc(bg.color||'#000000')+'"></div>';
}
}
function dvSetBgKind(kind){
var m=DV.tmeta[DV.tpl];
var allowed=(m&&m.backgrounds)||['solid'];
if(allowed.indexOf(kind)<0){toast('当前模板不允许'+DBG_LABEL[kind]+'背景',WN);return;}
var pre=dvSnap();
if(!DV.bg||!DV.bg.kind||DV.bg.kind!==kind){
DV.bg=DV.bg||{};
DV.bg.kind=kind;
if(kind==='linear'||kind==='radial'){if(!DV.bg.stops||DV.bg.stops.length!==2)DV.bg.stops=[[0,'#ffffff'],[1,'#dbeafe']];}
if(kind==='image'){DV.bg.color=DV.bg.color||'#000000';DV.bg.overlay_alpha=DV.bg.overlay_alpha==null?0.35:DV.bg.overlay_alpha;}
}
dvCommit(pre);
dvBgParamsPaint();
dvGo();
}
function dvBgParam(name,val){
if(!DV.bg)return;
var pre=dvSnap();
if(name==='s0'||name==='s1'){
if(!DV.bg.stops||DV.bg.stops.length!==2)DV.bg.stops=[[0,'#ffffff'],[1,'#dbeafe']];
DV.bg.stops[name==='s0'?0:1]=[+DV.bg.stops[name==='s0'?0:1][0]||0,val];
}else if(name==='overlay_alpha'||name==='angle'){
DV.bg[name]=+val||0;
}else DV.bg[name]=val;
dvCommit(pre);
dvGo();
}
async function dvPickOpen(mode,slot){
DV.pick={mode:mode,slot:slot||''};
$('dv-pick').hidden=false;
await dvPickLoad();
}
async function dvPickLoad(){
try{
var d=await getJSON('/api/materials?type=image&limit=60');
var items=(d.items||[]).filter(function(m){return m.type==='image';});
$('dv-pick-grid').innerHTML=items.length?items.map(function(m){
return '<button class="dv-pick-cell" data-dpick="'+esc(m.id)+'"><img loading="lazy" src="/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=thumb" alt=""><span>'+esc(m.title||m.id)+'</span></button>';
}).join(''):'<p class="dv-hint">暂无图片素材——用左下「上传本机图」或去素材库导入。</p>';
}catch(err){
$('dv-pick-grid').innerHTML=loadFailHTML('图片素材',err.message,'dvPickLoad');
}
}
async function dvPickSel(id){
try{
var res=await fetch('/api/materials/file?id='+encodeURIComponent(id)+'&variant=full');
if(!res.ok)throw new Error('HTTP '+res.status);
var blob=await res.blob();
var url=await new Promise(function(r2,rj2){var fr=new FileReader();fr.onload=function(){r2(String(fr.result));};fr.onerror=function(){rj2(new Error('读取失败'));};fr.readAsDataURL(blob);});
dvAssignImage(url);
}catch(err){toast('取图失败：'+err.message,ER);}
}
function dvAssignImage(dataUrl){
$('dv-pick').hidden=true;
var pre=dvSnap();
if(DV.pick.mode==='bg'){
DV.bg=DV.bg||{};
DV.bg.kind='image';DV.bg.image=dataUrl;
DV.bg.color=DV.bg.color||'#000000';
DV.bg.overlay_alpha=DV.bg.overlay_alpha==null?0.35:DV.bg.overlay_alpha;
dvBgParamsPaint();
}else{
var key=DV.pick.slot;
var v=DV.params[key],cur=(v&&typeof v==='object')?v:{fit:'cover',mask:'none'};
DV.params[key]=Object.assign({},cur,{src:dataUrl});
dvPropPaint();
}
dvCommit(pre);
dvGo();
}
async function dvPickUpload(file){
try{
var res=await fetch('/api/materials/upload?confirm=1&name='+encodeURIComponent(file.name),{method:'POST',body:file});
var d={};try{d=await res.json();}catch(e2){}
if(!res.ok)throw new Error(d.error||'HTTP '+res.status);
toast('已上传'+(d.dedup?'（幂等命中）':''),OK);
await dvPickLoad();
if(d.material&&d.material.id)dvPickSel(d.material.id);
}catch(err){toast('上传失败：'+err.message,ER);}
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
dvLayersPaint();dvTplsPaint();dvSpecsPaint();dvBgParamsPaint();dvPropPaint();
}
async function dvFillArts(){
await loadArticles();
var sel=$('dv-art'),cur=sel.value,opts='<option value="">选择文章…</option>';
state.articles.forEach(function(a){opts+='<option value="'+esc(a.path)+'">'+esc(a.title||a.path)+'</option>';});
sel.innerHTML=opts;
if(cur)sel.value=cur;
var dl=$('dv-art-list');
if(dl){
dl.innerHTML=state.articles.map(function(a){return '<option value="'+esc(a.title||a.path)+'">'+esc(a.path)+'</option>';}).join('');
var flt=$('dv-art-filter');
if(flt&&!flt._bound){
flt._bound=1;
flt.addEventListener('input',function(){
var q=this.value.trim().toLowerCase();
var found=state.articles.find(function(a){return String(a.title||a.path).toLowerCase().indexOf(q)>=0;});
if(found)sel.value=found.path;
});
flt.addEventListener('change',function(){
var q=this.value.trim();
var found=state.articles.find(function(a){return String(a.title||a.path)===q||String(a.path)===q;});
if(found){sel.value=found.path;dvLoad();}
});
}
}
}
async function dvEnter(){
await dvFillArts();
await dvLoadSlots();dvColorChips();dvBgParamsPaint();
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
if((DV.lprops[i]||{}).locked){toast('图层已锁定（图层列表解锁后可编辑）',WN);return;}
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
if(t.classList.contains('modal-overlay')){t.hidden=true;DV.pendSpec='';DV.pick=null;return;}
var b=t.closest('[data-dspec],[data-dslot],[data-dfs],[data-dal],[data-exspec],[data-exroute],[data-dtpl],[data-dbgt],[data-dvpat],[data-dleye],[data-dlok],[data-lrow],[data-dvfit],[data-dvmask],[data-dvblend],[data-dvpick]');
if(!b)return;
if(b.hasAttribute('data-dspec'))return dvSetSpec(b.getAttribute('data-dspec'));
if(b.hasAttribute('data-dtpl'))return dvSetTpl(b.getAttribute('data-dtpl'));
if(b.hasAttribute('data-exspec')||b.hasAttribute('data-exroute')){
b.parentElement.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('is-active',c===b);});
return;
}
if(b.hasAttribute('data-dbgt'))return dvSetBgKind(b.getAttribute('data-dbgt'));
if(b.hasAttribute('data-dvpat')){if(DV.bg){var pre=dvSnap();DV.bg.pattern=b.getAttribute('data-dvpat');dvCommit(pre);dvBgParamsPaint();dvGo();}return;}
if(b.hasAttribute('data-dleye'))return dvToggleEye(+b.getAttribute('data-dleye'));
if(b.hasAttribute('data-dlok'))return dvToggleLock(+b.getAttribute('data-dlok'));
if(b.hasAttribute('data-lrow'))return dvSelect(+b.getAttribute('data-lrow'));
if(b.hasAttribute('data-dvfit')){var fk=b.getAttribute('data-dvfit'),fv=b.getAttribute('data-fv'),fvv=DV.params[fk];DV.params[fk]=Object.assign({},(fvv&&typeof fvv==='object')?fvv:{}, {fit:fv});dvCommit(dvSnap());dvPropPaint();dvGo();return;}
if(b.hasAttribute('data-dvmask')){var mk=b.getAttribute('data-dvmask'),mv=b.getAttribute('data-fv'),mvv=DV.params[mk];DV.params[mk]=Object.assign({},(mvv&&typeof mvv==='object')?mvv:{}, {mask:mv});dvCommit(dvSnap());dvPropPaint();dvGo();return;}
if(b.hasAttribute('data-dvblend')){var bi=+b.getAttribute('data-dvblend');if(DV.sel>=0){var pre2=dvSnap();dvLp(DV.sel).blend=b.getAttribute('data-dvblend');var nn=dvSelNode();if(nn)nn.setAttribute('style','mix-blend-mode:'+DV.lprops[DV.sel].blend);dvCommit(pre2);dvPropPaint();}return;}
if(b.hasAttribute('data-dvpick'))return dvPickOpen(b.getAttribute('data-dvpick'),b.getAttribute('data-dvslot')||'');
var key=b.hasAttribute('data-dslot')?'background_slot':b.hasAttribute('data-dfs')?'font_size':'align';
var v=b.getAttribute('data-dslot')||b.getAttribute('data-dfs')||b.getAttribute('data-dal');
if(v===DV.params[key])return;
var pre=dvSnap();
DV.params[key]=v;
if(key==='background_slot'){dvRecentAdd(v);dvColorChips();}
dvCommit(pre);dvGo();
});
$('view-design').addEventListener('input',function(e){
var el=e.target;
if(el.id==='dv-prop-text'){
var key=el.getAttribute('data-dvkey');
if(!key||!(key in DV.params))return;
var pre=dvSnap();
DV.params[key]=el.value;
dvCommit(pre);dvGo();return;
}
if(el.id==='dv-prop-fill'){var n2=dvSelNode();if(n2&&DV.sel>=0){var pre3=dvSnap();dvLp(DV.sel).fill=el.value;n2.setAttribute('fill',el.value);dvCommit(pre3);}return;}
if(el.id==='dv-prop-op'){var n3=dvSelNode();if(n3&&DV.sel>=0){var lp=dvLp(DV.sel);lp.opacity=+el.value;n3.setAttribute('opacity',String(lp.opacity));}return;}
var bgk=el.getAttribute('data-dvbg');
if(bgk){dvBgParam(bgk,el.value);
if(bgk==='overlay_alpha'){var sp=el.parentElement&&el.parentElement.querySelector('.dv-lz');if(sp)sp.textContent=Math.round(+el.value||0);}
}
});
$('dv-layers').addEventListener('dragstart',function(e){
var row=e.target.closest('[data-lrow]');
if(!row)return;
dvDragRow=+row.getAttribute('data-lrow');
e.dataTransfer.effectAllowed='move';
try{e.dataTransfer.setData('text/plain',String(dvDragRow));}catch(err2){}
});
$('dv-layers').addEventListener('dragover',function(e){
var row=e.target.closest('[data-lrow]');
if(row&&dvDragRow!=null&&+row.getAttribute('data-lrow')!==dvDragRow){e.preventDefault();return;}
if(!row)e.preventDefault();
});
$('dv-layers').addEventListener('drop',function(e){
var row=e.target.closest('[data-lrow]');
e.preventDefault();
if(!row||dvDragRow==null)return;
var rect=row.getBoundingClientRect(),before=e.clientY<rect.top+rect.height/2;
dvRowDrop(+row.getAttribute('data-lrow'),before);
dvDragRow=null;
});
$('dv-layers').addEventListener('dragend',function(){dvDragRow=null;});
$('dv-pick-grid').addEventListener('click',function(e){
var c=e.target.closest('[data-dpick]');
if(c)dvPickSel(c.getAttribute('data-dpick'));
});
on('dv-pick-cancel',function(){$('dv-pick').hidden=true;DV.pick=null;});
$('dv-pick-file').addEventListener('change',function(){
var f=this.files&&this.files[0];
this.value='';
if(f)dvPickUpload(f);
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
if(!$('dv-pick').hidden){$('dv-pick').hidden=true;DV.pick=null;return;}
dvSelect(-1);
}
});
}
bindDesign();
