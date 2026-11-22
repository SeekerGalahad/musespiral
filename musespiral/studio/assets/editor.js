/*! musespiral 编辑器一期全量落地（F1，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.1）。
 * 选型 CodeMirror 5.65.18——本地 vendor（assets/editor/），零外联红线不破（本地文件非 CDN）。
 *
 * 装载契约（ui.js 仅两处接线，其余全部自管理）：
 *   - mountEdit：首次进编辑 tab 注入本文件一次，此后每次进入调 __mdpubEditor.activate(EA)；
 *   - 文档 keydown 头部 e.defaultPrevented 让渡——CM 已消费的键（⌘K/⌘F…）不再触发全局快捷键。
 *   - 本文件全部 keydown（F11/查找条/目标输入）带 isComposing/229 头部守卫——与 ui.js
 *     既有 IME 惯例一致（组合中的 Enter/Esc 只服 IME，不触发编辑器动作）。
 *
 * 与既有链路的接合（textarea 仍是唯一数据面）：
 *   fromTextArea 把 #edit-area 替换为 CM 实例（textarea 隐藏保留）；CM change →
 *   cm.save() 写回 EA.value → 派发 input 事件——ui.js 既有 ⌘S 保存（读 EA.value）、
 *   实时预览（scheduleLive）、状态栏（updateStatusbar）零改动继续工作。
 *   文章切换：mountEdit 写 EA.value → activate 检出差异 → cm.setValue（程序性
 *   置入不派发 input，避免装载即 dirty）。
 *
 * 一期功能清单：markdown 高亮 / 14 钮工具栏 / ⌘F 搜索替换 / ⌘B ⌘I ⌘K 快捷键 /
 * 括号引号自动配对（addon）/ Tab 列表智能缩进 / F11 专注模式（隐藏面板+段落聚焦）/
 * 打字机滚动（当前行居中）/ 大纲侧栏（H1-H3+分节字数+点击跳转）/ 字数目标进度条
 * （localStorage 持久）/ 阅读时长（400 字/分）/ 写作冲刺 25min+本期字数 /
 * 状态栏（总字数·本节·段落·AI 块·⏱阅读·保存态·同步滚动开关）/ CM↔预览同步滚动。
 * E-2 内联研究（§3.2）：⌘⇧S / 工具栏📚 → 编辑器底部素材搜索面板 → debounce 300ms
 * 实时搜 /api/materials（q+limit=8）→ 结果行一键插引用（`> 来自[素材名]：摘录`）
 * 或插图片（`![](path)`，image 走 /api/materials/file 内容寻址 URL）→ 面板保持可
 * 连续插入 → Esc 收起、光标回编辑器——全程 0 次切视图 0 次复制粘贴。
 * E-3 AI 微操作（§3.3）：选中 >10 字浮出 5 钮 → POST /api/editor/ai-action →
 * diff 弹窗红删绿增 → 接受写回（<!--ai:block--> 包裹）/ 拒绝零写回。
 * 三线交界契约（E-2×E-3 集成批）：选中优先级>搜索——AI 弹窗开着或选区达
 * 浮出门槛时，⌘⇧S/📚 不开搜索面板不夺焦点（关面板恒允许）；反向不设限——
 * 搜索面板开着时选区照常浮出。Esc 收拾顺序=选中浮出→查找条→内联面板。
 */
(function(){
'use strict';

/*__ED_BEGIN__*/
// ── 纯函数区（tests/test_editor_ui.py 的 node 行为断言抽取本哨兵段执行，不触 DOM）──

function edCount(t){return String(t==null?'':t).replace(/\s+/g,'').length;}
// 阅读时长：400 字/分钟，不足 1 分钟且非空按 1 分钟计
function edRead(c){c=+c||0;return c<1?0:Math.max(1,Math.ceil(c/400));}
// AI 块锚（与 ui.js BANCHOR 同式）：行首 <!--b:id--> 独占行；围栏代码块内不算
var ED_AI_RE=/^<!--b:[A-Za-z0-9_-]+-->[ \t]*$/;
function edAI(t){
var ls=String(t==null?'':t).replace(/\r/g,'').split('\n'),n=0,f=false,i;
for(i=0;i<ls.length;i++){var L=ls[i];
if(/^```/.test(L)){f=!f;continue;}
if(f)continue;
if(ED_AI_RE.test(L))n++;}
return n;}
// 大纲：H1-H3（围栏内 # 不算标题）；chars=该节标题下一行到下一同级/更高级标题前的字数
function edOutline(t){
var ls=String(t==null?'':t).replace(/\r/g,'').split('\n'),h=[],i,f=false,m;
for(i=0;i<ls.length;i++){var L=ls[i];
if(/^```/.test(L)){f=!f;continue;}
if(f)continue;
m=L.match(/^(#{1,3})\s+(.*)$/);
if(m)h.push({line:i,level:m[1].length,title:m[2].trim()||'(无标题)',chars:0});}
var j,e;
for(i=0;i<h.length;i++){
e=ls.length;
for(j=i+1;j<h.length;j++)if(h[j].level<=h[i].level){e=h[j].line;break;}
h[i].chars=edCount(ls.slice(h[i].line+1,e).join('\n'));}
return h;}
// 光标所在节：line 之前最近的标题下标；无标题（导语区）返回 -1
function edSection(out,line){
var i,sec=-1;
for(i=0;i<out.length;i++){if(out[i].line<=line)sec=i;else break;}
return sec;}
// 选区包裹切换（B/I/S/行内码）：已包同标记则脱、未包则包；返回 [text,s2,e2]
function edWrap(t,s,e,open,close){
if(s>e){var w=s;s=e;e=w;}
var v=String(t==null?'':t);
if(s>=open.length&&v.slice(s-open.length,s)===open&&v.slice(e,e+close.length)===close)
return[v.slice(0,s-open.length)+v.slice(s,e)+v.slice(e+close.length),s-open.length,e-open.length];
return[v.slice(0,s)+open+v.slice(s,e)+close+v.slice(e),s+open.length,e+open.length];}
// 选区内行前缀切换（H1-H3/引用）：已有该前缀→去除；有其他前缀→替换；无→加
function edLinePrefix(t,s,e,mark){
var v=String(t==null?'':t).replace(/\r/g,'');
var a=v.lastIndexOf('\n',s-1)+1,b=v.indexOf('\n',e);if(b<0)b=v.length;
var lines=v.slice(a,b).split('\n'),re=/^(#{1,6}\s+|>\s?)/,i;
for(i=0;i<lines.length;i++){
lines[i]=re.test(lines[i])?(lines[i].slice(0,mark.length)===mark?lines[i].replace(re,''):lines[i].replace(re,mark)):mark+lines[i];}
var out=lines.join('\n');
return[v.slice(0,a)+out+v.slice(b),a,a+out.length];}
// 写作冲刺计字：只累计新增（删除不回冲）
function edSprintAdd(prev,next){var d=(+next||0)-(+prev||0);return d>0?d:0;}
// E-2 内联研究（§3.2）：引用行合成——标题剥方括号/换行防破链接格式，摘录压空白截 120
function edQuote(title,text){
var t=String(title==null?'':title).replace(/[\[\]\r\n]/g,' ').replace(/\s+/g,' ').trim()||'未命名素材';
var x=String(text==null?'':text).replace(/\s+/g,' ').trim().slice(0,120);
return x?('> 来自['+t+']：'+x):('> 来自['+t+']');}
// E-2 图片素材：![](path)——剥空白与圆括号防破 Markdown 链接语法
function edImgMd(path){return '![]('+String(path==null?'':path).replace(/[\s()]+/g,'')+')';}
// E-2 结果行摘要：payload.text 压空白截 80，空则落 payload.url
function edMatExcerpt(m){
var p=(m||{}).payload||{};
var t=String(p.text==null?'':p.text).replace(/\s+/g,' ').trim();
if(!t)t=String(p.url==null?'':p.url).trim();
return t.slice(0,80);}
// E-2 结果行元信息：image=宽×高，video=时长，text=字数（edCount 同口径），其余空
function edMatMeta(m){
var mm=m||{},p=mm.payload||{},meta=p.meta||{};
if(mm.type==='image'){var w=+meta.width||0,h=+meta.height||0;return(w&&h)?(w+'×'+h):'';}
if(mm.type==='video')return String(meta.duration_text||'');
var c=edCount(p.text);return c>0?(c+' 字'):'';}
// ── E-3 AI 微操作（§3.3）：纯函数区 ──
// 浮出条件：选中文本 >10 字（去空白口径同 edCount——纯空白不算有内容）
function edAIMenuOn(sel){return edCount(sel)>10;}
// 选区上下文：选中文字前后各扩 max 字符，尽量落在段落边界（段落间空行对齐）
function edCtx(t,s,e,max){
var v=String(t==null?'':t).replace(/\r/g,'');max=+max||600;
if(s>e){var w=s;s=e;e=w;}
var a=Math.max(0,s-max),b=Math.min(v.length,e+max);
var pre=v.slice(a,s),post=v.slice(e,b),k=pre.lastIndexOf('\n\n');
if(k>=0)pre=pre.slice(k+2);
var m=post.indexOf('\n\n');
if(m>=0)post=post.slice(0,m);
return (pre+post).trim();}
// 接受写回定位：'index'=按发起时的字符索引（原文未变）；
// 'selection'=索引已失效但当前选区内容仍是原文（用户只是点到别处又选回来）；
// 'stale'=两路都不符（正文已改且选区已变——拒写，绝不覆盖用户新文字）
function edPlace(doc,s,e,original,selText){
var v=String(doc==null?'':doc),o=String(original==null?'':original);
if(!o)return'stale';
if(s>=0&&e>=s&&e<=v.length&&v.slice(s,e)===o)return'index';
return String(selText==null?'':selText)===o?'selection':'stale';}
// 接受写回的完整纯函数：返回 {doc,s,e}——新正文 + 写回后光标位置。
// where='stale' → 原样返回（调用方提示不写）。doc 与 s/e 都按字符索引（与 CM indexFromPos 同口径）。
function edAccept(doc,s,e,original,selText,gen){
var v=String(doc==null?'':doc);
var where=edPlace(v,s,e,original,selText);
if(where==='stale')return{doc:v,s:s,e:e,where:where};
var a,b;
if(where==='index'){a=s;b=e;}
else{
// 选区路径：重算当前选区字符索引（selText 必等于 original，长度一致）
var o=String(original==null?'':original),t=String(selText==null?'':selText),at=v.indexOf(t);
a=at<0?0:at;b=a+o.length;
}
var text=edAIMark(gen);
if(!text)return{doc:v,s:a,e:b,where:'empty'};
return{doc:v.slice(0,a)+text+v.slice(b),s:a,e:a+text.length,where:where};}
// 拒绝写回的语义：正文零改动（只关闭弹窗）——把这条承诺做成可断言函数
function edReject(doc){return{doc:String(doc==null?'':doc)};}
// 接受后的 AI 标记包裹（§3.3：可追溯+可重生成）：首尾标记各独占一行。
// 与块协议锚 <!--b:id--> 区分——那是创作台块锚，本标记是编辑器微操作留痕。
function edAIMark(gen){
var g=String(gen==null?'':gen).replace(/\r/g,'').replace(/^\s+|\s+$/g,'');
return g?'<!--ai:block-->\n'+g+'\n<!--/ai:block-->':'';}
// AI 微操作块计数（围栏代码块内不计——与 edAI 同纪律）
var ED_AIM_RE=/^<!--ai:block-->[ \t]*$/;
function edAIMarkCount(t){
var ls=String(t==null?'':t).replace(/\r/g,'').split('\n'),n=0,f=false,i;
for(i=0;i<ls.length;i++){var L=ls[i];
if(/^```/.test(L)){f=!f;continue;}
if(f)continue;
if(ED_AIM_RE.test(L))n++;}
return n;}
// diff 行（红删绿增的数据面）：[{t:0同|-1删|1增, s:段落}]，同类型连续片段合并。
// 粒度：两侧都单行且 ≤1500 字符 → 逐字符（段落微调的红绿位置才准确）；
// 否则逐行（长文本/多行选区逐字符会碎成噪声）。
function edDiffRows(a,b){
var A=String(a==null?'':a).replace(/\r/g,''),B=String(b==null?'':b).replace(/\r/g,'');
var byLine=A.indexOf('\n')>=0||B.indexOf('\n')>=0||A.length>1500||B.length>1500;
var la=byLine?A.split('\n'):A.split(''),lb=byLine?B.split('\n'):B.split('');
var n=la.length,m=lb.length,i,j;
var d=[];
for(i=0;i<=n;i++)d.push(new Int32Array(m+1));
for(i=n-1;i>=0;i--)for(j=m-1;j>=0;j--)
d[i][j]=la[i]===lb[j]?d[i+1][j+1]+1:(d[i+1][j]>=d[i][j+1]?d[i+1][j]:d[i][j+1]);
var SEP=byLine?'\n':'',out=[];
function put(t,s){var last=out[out.length-1];
if(last&&last.t===t)last.s+=s+SEP;else out.push({t:t,s:s+SEP});}
var x=0,y=0;
while(x<n&&y<m){
if(la[x]===lb[y]){put(0,la[x]);x++;y++;}
else if(d[x+1][y]>=d[x][y+1]){put(-1,la[x]);x++;}
else{put(1,lb[y]);y++;}}
while(x<n)put(-1,la[x++]);
while(y<m)put(1,lb[y++]);
if(SEP&&out.length){var e=out[out.length-1];e.s=e.s.slice(0,e.s.length-1);}
return out;}
// diff 计数（弹窗头部「+N −N」数据面）
function edDiffStat(rows){
var add=0,del=0,i;
for(i=0;i<rows.length;i++){if(rows[i].t>0)add+=rows[i].s.length;else if(rows[i].t<0)del+=rows[i].s.length;}
return{add:add,del:del};}
/*__ED_END__*/

/*__GT_BEGIN__*/
// ── G-T1 Ghost Text 控制器（2026-11-19，正典=《musespiral-调研吸收功能设计-2026-11-18》§二）──
// AI 灰色预判续写：用户停笔 800ms 且光标在行尾/空行 → POST ghost-write →
// 灰色半透明渲染 → Tab 采纳 / Esc 忽略 / 继续打字自动消失；采纳后 cooldown 3s
// 防连发。本哨兵段是状态机段（tests/test_editor_ghost.py 抽出在 node 里配
// 最小 CM 桩跑行为断言），只经 document.getElementById 触状态栏 hint（无
// document 环境（node）自动跳过），不触碰编辑器其余状态。
//
// 显示机制（为什么是 setBookmark widget 而非 markText）：CM5 markText 只能给
// 「已在文档里的文字」上样式——collapsed mark(pos,pos) 零宽不可见；而设计稿
// 三条约束「不可编辑 / 打字自动消失 / 采纳后正常色 + Tab=replaceSelection」
// 合起来要求 ghost 落稿前不进文档缓冲。故渲染走书签内联 widget
// （span.cm-ghost-text，文本不进缓冲——⌘S 保存/字数统计/大纲全不受污染，
// 真·不可编辑）；Tab 采纳才 replaceSelection 落成正文（采纳后正常色）。
// Tab 分支不 return CodeMirror.Pass → CM5 自行 preventDefault（extraKeys 语义）。
var GT_DEB=800,GT_CD=3000,GT_CTX_SPAN=1,gtMark=null,gtText='',gtTimer=null,
gtHold=false,gtCdAt=0,gtSeq=0;
// 缺省发送（测试以 gtSend= 注入假件；零外联——相对路径走同源 Studio）
function gtSend(payload){
return fetch('/api/editor/ghost-write',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify(payload)}).then(function(r){return r.json();});}
// 触发谓词：光标在行尾（含空行——ch=0=len）才允许 ghost（§二 交互设计）
function edGhostAtEnd(line,ch){var L=String(line==null?'':line);return ch>=L.length;}
// 请求上下文：当前行 ±span 行（§2.3「当前段落（前后各1段）」，getLine 口径）
function edGhostCtx(doc,line,span){
var ls=String(doc==null?'':doc).replace(/\r/g,'').split('\n');
var a=Math.max(0,line-(span||0)),b=Math.min(ls.length-1,line+(span||0)),out=[],i;
for(i=a;i<=b;i++)out.push(ls[i]);
return out.join('\n');}
// 文章 frontmatter title/tags（§2.3：请求携带 frontmatter 元信息，选填）
function edGhostMeta(doc){
var v=String(doc==null?'':doc).replace(/\r/g,''),m=v.match(/^---\n([\s\S]*?)\n---/),
out={title:'',tags:[]},ls,i,mm;
if(!m)return out;
ls=m[1].split('\n');
for(i=0;i<ls.length;i++){
mm=ls[i].match(/^title:\s*(.+?)\s*$/);
if(mm){out.title=mm[1];continue;}
mm=ls[i].match(/^tags:\s*(.+?)\s*$/);
if(mm)out.tags=mm[1].replace(/^\[/,'').replace(/\]$/,'').split(/[,，、]\s*/)
.map(function(s){return s.trim();}).filter(Boolean);}
return out;}
// cooldown 纯判定（now-cdAt≥cd 才允许新请求；gtAccept 时钟为准）
function edGhostCooled(now,cdAt,cd){return (now-cdAt)>=cd;}
// cursorActivity 接线点：ghost 在屏 → 光标一动即退场（Tab 采纳走 gtAccept
// 不经此）；Esc 保持态 → 不自动请求；否则行尾/空行 → 重置 800ms 防抖
function gtCursor(cm){
if(!cm)return;
if(gtMark){gtClear();return;}
if(gtHold)return;
gtSchedule(cm);}
function gtSchedule(cm){
if(gtTimer)clearTimeout(gtTimer);
gtTimer=setTimeout(function(){gtTimer=null;gtFire(cm);},GT_DEB);}
// 防抖到点：cooldown / 焦点 / 位置 / 上下文四道闸 → 发请求（序号守卫丢过期响应）
function gtFire(cm){
if(!cm||gtHold)return;
if(!edGhostCooled(Date.now(),gtCdAt,GT_CD))return;
try{if(!cm.hasFocus())return;}catch(x){}
var cur=cm.getCursor(),line=cm.getLine(cur.line)||'';
if(!edGhostAtEnd(line,cur.ch))return;
var doc=cm.getValue(),context=edGhostCtx(doc,cur.line,GT_CTX_SPAN);
if(!context.replace(/\s+/g,''))return;
var meta=edGhostMeta(doc),seq=++gtSeq,payload={confirm:true,context:context};
if(meta.title)payload.title=meta.title;
if(meta.tags&&meta.tags.length)payload.tags=meta.tags;
Promise.resolve(gtSend(payload)).then(function(d){
if(seq!==gtSeq)return;
if(!d||d.ok!==true||!d.ghost)return;
var pos=cm.getCursor();
if(!edGhostAtEnd(cm.getLine(pos.line)||'',pos.ch))return;
gtShow(cm,d.ghost,pos);})
.catch(function(){});}
function gtShow(cm,ghost,pos){
gtClear();
var s=document.createElement('span');
s.className='cm-ghost-text';
s.setAttribute('data-ghost','1');
s.textContent=String(ghost==null?'':ghost);
gtMark=cm.setBookmark(pos,{widget:s,insertLeft:false});
gtText=s.textContent;
gtHint(true);}
// Tab 采纳：ghost 落稿（replaceSelection 正常色）+ 记 cooldown 时间戳；
// 返回采纳文本（真值）供 extraKeys Tab 分支短路——无 ghost 时返回 null
// 让位给既有 edSmartTab（智能缩进不受伤）
function gtAccept(cm){
if(!gtMark||!cm)return null;
var t=gtText;
gtClear();
cm.replaceSelection(t,'end','+ghost');
gtCdAt=Date.now();
return t;}
function gtClear(){
if(gtTimer){clearTimeout(gtTimer);gtTimer=null;}
if(gtMark){try{gtMark.clear();}catch(x){}}
gtMark=null;gtText='';
gtHint(false);}
function gtHint(on){
if(typeof document==='undefined')return;
var el=document.getElementById('ed-ghost-hint');
if(el)el.hidden=!on;}
// 文档变更接线点（用户打字/粘贴/程序性 setValue）：在屏 ghost 立即退场，
// 同时解除 Esc 保持态（新输入=重新允许自动请求，§二「继续打字→清+重新防抖」）
function gtTyping(){
gtHold=false;
if(gtMark)gtClear();}
// Esc 忽略：清屏 + 进入保持态（不再自动请求，直到用户再输入新字符）
function gtEscape(cm){
gtClear();
gtHold=true;
if(cm&&cm.hasFocus)try{cm.focus();}catch(x){}}
// 离开文章视图/切文章：全量复位（含序号前推——丢弃飞行中的过期响应）
function gtReset(){
gtClear();gtHold=false;gtCdAt=0;gtSeq++;}
/*__GT_END__*/

// ── 常量与状态 ──────────────────────────────────────────────
// vendor 全本地相对路径（零外联）：core → markdown mode → 两 edit addon →
// 两 search addon；CSS 顺序=base → 纸墨主题（vendor 定制，须在 base 后）→
// editor.css（布局与 UI chrome 最后加载，冲突处后到者胜）。
var VENDOR_JS=['assets/editor/codemirror.min.js','assets/editor/mode/markdown/markdown.min.js',
'assets/editor/addon/edit/closebrackets.min.js','assets/editor/addon/edit/matchbrackets.min.js',
'assets/editor/addon/search/searchcursor.min.js','assets/editor/addon/search/search.min.js'];
var VENDOR_CSS=['assets/editor/codemirror.min.css','assets/editor/theme/paper-ink.css','assets/editor.css'];
var GOAL_KEY='musespiral.ed.goal',SPRINT_MS=25*60*1000;
var $id=function(i){return document.getElementById(i);};
var edCM=null,edTA=null,edLoading=false,edProg=false;
var edTypewriter=false,edFocus=false,edSprint=null,edSync=true;
var edCurLine=null,edDeb=null,edOutlineCache=[];
var edEchoP=-1,edEchoE=-1,edLockP=false;
function edEsc(s){return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}

// ── 装载（vendor 链式加载；失败静默回落 textarea 原功能）──
function edLink(href){var l=document.createElement('link');l.rel='stylesheet';l.href=href;document.head.appendChild(l);}
function edChain(list,done){var i=0;(function next(){
if(i>=list.length)return done(false);
var s=document.createElement('script');s.src=list[i++];
s.onload=next;
s.onerror=function(){done(true);};
document.head.appendChild(s);})();}
function activate(ta){
edTA=ta||edTA;
if(!edTA)return;
if(edCM){edResync();return;}
if(edLoading)return;
edLoading=true;
VENDOR_CSS.forEach(edLink);
edChain(VENDOR_JS,function(err){
edLoading=false;
if(err||typeof CodeMirror==='undefined')return;
edBuild();edResync();});
}
function edResync(){
if(!edCM)return;
if(edCM.getValue()!==edTA.value){
edProg=true;
try{edCM.setValue(edTA.value);}finally{edProg=false;}
}
try{edCM.refresh();}catch(x){}
edRefresh();
}

// ── CM 实例化 + 自管理 UI（工具栏/搜索条/大纲/状态栏扩展）──
function edBuild(){
var host=$id('ed-stack');
if(!host)return;
edCM=CodeMirror.fromTextArea(edTA,{
mode:'markdown',lineNumbers:true,autoCloseBrackets:true,matchBrackets:true,
tabSize:2,indentUnit:2,theme:'paper-ink',lineWrapping:true,
extraKeys:{
'Cmd-B':function(){edWrapSel('**','**');},'Ctrl-B':function(){edWrapSel('**','**');},
'Cmd-I':function(){edWrapSel('*','*');},'Ctrl-I':function(){edWrapSel('*','*');},
'Cmd-K':edLinkSel,'Ctrl-K':edLinkSel,
'Cmd-F':function(){edFindShow();},'Ctrl-F':function(){edFindShow();},
'Cmd-Shift-F':function(){edFindShow();},'Ctrl-Shift-F':function(){edFindShow();},
// CM5 keyName 合成序=Alt→Ctrl→Cmd→Shift（Shift 最前置）——⌘⇧S 实际派发
// 「Shift-Cmd-S」/「Shift-Ctrl-S」，extraKeys 精确匹配（vendor Ro/Go 无规范化）
'Shift-Cmd-S':edRpToggle,'Shift-Ctrl-S':edRpToggle,
// G-T1：Tab 先问 ghost——有在屏 ghost 采纳（gtAccept 真值短路；CM5 对非
// Pass 返回自行 preventDefault），无则落回智能缩进
'Tab':function(cm){if(gtAccept(cm))return;edSmartTab(cm);},'Shift-Tab':function(cm){cm.execCommand('indentLess');},
// Esc 收拾顺序（三线交界批定契 + G-T1 追加）：ghost（最瞬态：非 UI 元素，
// 只清书签不动文档）→ 选中浮出（AI 菜单）→ 查找条 → 内联搜索面板；
// 四者全收才 Pass。ghost 排最前=预判续写最先被 Esc 单独退场。
'Esc':function(cm){if(gtMark)gtEscape(cm);else if(edAiMenuVis())edAiHide();else if(edBarOpen())edFindHide();else if(edRpOpen())edRpHide();else return CodeMirror.Pass;}
}});
var stage=host.closest?host.closest('.edit-stage'):null;
if(stage)stage.classList.add('ed-cm');
var col=host.closest?host.closest('.ed-col'):null;
if(!col)return;
var layout=col.parentNode;
// 工具栏（16 钮，§3.1：B I S │ H1 H2 H3 │ 引用 链接 代码 表格 图片 │ 素材 AI 全屏 │ 专注 打字机）
// UIX #9：专注/打字机原只在状态栏细按钮（截图里几乎看不见）——增显式工具栏入口，
// 复用既有 edFocusToggle/edTwToggle，is-on 态由两个函数同步切换。
var ED_SEP_AFTER=[3,6,11,14];
var bar=document.createElement('div');bar.className='ed-bar';bar.id='ed-bar';
bar.innerHTML=ED_BTNS.map(function(b,i){
return(i>0&&ED_SEP_AFTER.indexOf(i)>=0?'<span class="ed-sep"></span>':'')
+'<button type="button" class="btn ed-tb" data-edb="'+b[0]+'" title="'+edEsc(b[2])+'" aria-label="'+edEsc(b[2])+'">'+edEsc(b[1])+'</button>';
}).join('');
bar.addEventListener('click',function(ev){
var b=ev.target.closest('[data-edb]');
if(!b)return;
edToolbar(b.getAttribute('data-edb'));});
col.insertBefore(bar,col.firstChild);
// 搜索替换条（隐藏待 ⌘F）
var find=document.createElement('div');find.className='ed-find';find.id='ed-find';find.hidden=true;
find.innerHTML='<input type="text" id="ed-find-q" placeholder="查找…" aria-label="查找">'
+'<span class="ed-find-n" id="ed-find-n"></span>'
+'<button type="button" class="btn ed-tb" data-edf="prev" title="上一个（⇧⏎）" aria-label="上一个匹配">↑</button>'
+'<button type="button" class="btn ed-tb" data-edf="next" title="下一个（⏎）" aria-label="下一个匹配">↓</button>'
+'<input type="text" id="ed-find-r" placeholder="替换为…" aria-label="替换为">'
+'<button type="button" class="btn ed-tb" data-edf="rep">替换</button>'
+'<button type="button" class="btn ed-tb" data-edf="all">全部</button>'
+'<button type="button" class="btn ed-tb" data-edf="close" title="Esc 关闭" aria-label="关闭查找条">✕</button>';
find.addEventListener('click',function(ev){
var b=ev.target.closest('[data-edf]');
if(!b)return;
var k=b.getAttribute('data-edf');
if(k==='close')edFindHide();
else if(k==='next')edFindGo(false);
else if(k==='prev')edFindGo(true);
else if(k==='rep')edFindReplace();
else if(k==='all')edFindReplaceAll();});
find.querySelector('#ed-find-q').addEventListener('input',edFindCount);
find.querySelector('#ed-find-q').addEventListener('keydown',function(ev){
if(ev.isComposing||ev.keyCode===229)return;
if(ev.key==='Enter'){ev.preventDefault();edFindGo(ev.shiftKey);}
if(ev.key==='Escape')edFindHide();});
find.querySelector('#ed-find-r').addEventListener('keydown',function(ev){
if(ev.isComposing||ev.keyCode===229)return;
if(ev.key==='Enter'){ev.preventDefault();edFindReplace();}
if(ev.key==='Escape')edFindHide();});
col.insertBefore(find,bar.nextSibling);
// 大纲侧栏（编辑列左侧）
if(layout){
var ol=document.createElement('aside');ol.className='ed-outline';ol.id='ed-outline';
ol.innerHTML='<p class="ed-ol-title">📑 大纲</p><div class="ed-ol-list" id="ed-ol-list"></div>';
ol.addEventListener('click',function(ev){
var a=ev.target.closest('[data-ol]');
if(!a||!edCM)return;
var h=edOutlineCache[+a.getAttribute('data-ol')];
if(!h)return;
edCM.setCursor({line:h.line,ch:0});
edCM.scrollIntoView({line:h.line,ch:0},Math.round(edCM.getScrollInfo().clientHeight/2));
edCM.focus();});
layout.insertBefore(ol,layout.firstChild);}
// 状态栏扩展（既有 保存按钮 之前插入；本节/段落/AI块/阅读/目标/同步/专注/打字机/冲刺）
var st=col.querySelector('.ed-status');
if(st){
var ex=document.createElement('span');ex.className='ed-extra';ex.id='ed-extra';
ex.innerHTML='<span>本节 <b id="ed-sect">0</b></span>'
+'<span>段落 <b id="ed-paras">0</b></span>'
+'<span>AI 块 <b id="ed-aib">0</b></span>'
+'<span>⏱ <b id="ed-read">0 分钟</b></span>'
+'<span class="ed-goal" id="ed-goal"></span>'
// G-T1 ghost 在屏时的操作提示（gtHint 显隐）
+'<span class="ed-ghost-hint" id="ed-ghost-hint" hidden>⌨ Tab 采纳 · Esc 忽略</span>'
+'<button type="button" class="btn ed-mini" id="ed-sync" title="编辑↔预览同步滚动开关">同步 开</button>'
+'<button type="button" class="btn ed-mini" id="ed-focus-btn" title="专注模式（F11）：仅留编辑+状态栏，其余段落淡出">专注</button>'
+'<button type="button" class="btn ed-mini" id="ed-tw" title="打字机滚动：当前行恒居视口中央">打字机</button>'
+'<button type="button" class="btn ed-mini" id="ed-sprint" title="写作冲刺：25 分钟番茄钟+本期新增字数">⏱ 冲刺</button>';
var saveBtn=st.querySelector('#edit-save-btn');
st.insertBefore(ex,saveBtn);
$id('ed-sync').addEventListener('click',function(){
edSync=!edSync;this.textContent='同步 '+(edSync?'开':'关');});
$id('ed-focus-btn').addEventListener('click',edFocusToggle);
$id('ed-tw').addEventListener('click',function(){edTwToggle();});
$id('ed-sprint').addEventListener('click',edSprintToggle);}
// E-2 内联研究面板（编辑器底部、状态栏之上；⌘⇧S / 工具栏📚 开合，Esc 收起）
var rp=document.createElement('div');rp.className='ed-rp';rp.id='ed-rp';rp.hidden=true;
rp.innerHTML='<div class="ed-rp-head"><span class="ed-rp-t">📚 内联搜索</span>'
+'<input type="text" id="ed-rp-q" class="input" placeholder="搜索素材库（标题/标签/正文）…" aria-label="内联搜索素材">'
+'<span class="ed-rp-n" id="ed-rp-n"></span>'
+'<button type="button" class="btn ed-tb" data-rp="close" title="收起（Esc），光标回编辑器">✕</button></div>'
+'<div class="ed-rp-list" id="ed-rp-list"><p class="ed-rp-empty">输入关键词搜索素材（标题/标签/正文）…</p></div>';
rp.addEventListener('click',function(ev){
var b=ev.target.closest('[data-rpins]');
if(b){edRpInsert(b.getAttribute('data-rpins'));return;}
if(ev.target.closest('[data-rp="close"]'))edRpHide();});
rp.addEventListener('keydown',function(ev){
if(ev.isComposing||ev.keyCode===229)return;
if(ev.key==='Escape'){ev.preventDefault();edRpHide();}});
var rpin=rp.querySelector('#ed-rp-q');
if(rpin)rpin.addEventListener('input',function(){
if(rpDeb)clearTimeout(rpDeb);
rpDeb=setTimeout(edRpFetch,RP_DEB);});
if(st)col.insertBefore(rp,st);else col.appendChild(rp);
// E-3 AI 微操作：浮动菜单（选中 >10 字浮出）+ diff 弹窗（红删绿增）
// 菜单挂 .ed-stack 内（absolute 定位到选区上方）；弹窗挂 body（fixed 遮罩）。
edAiBuild(host);
// CM 事件
edCM.on('change',function(cm){
cm.save();
gtTyping();  // G-T1：任何文档变更 → 在屏 ghost 退场 + 解除 Esc 保持态
if(edSprint&&edSprint.on){
var len=edCount(cm.getValue());
edSprint.chars+=edSprintAdd(edSprint.len,len);
edSprint.len=len;edSprintPaint();}
if(!edProg)edTA.dispatchEvent(new Event('input',{bubbles:true}));
if(edDeb)clearTimeout(edDeb);
edDeb=setTimeout(edRefresh,200);});
edCM.on('cursorActivity',function(){
edSect();
if(edFocus)edDimLine();
if(edTypewriter)edCenter();
edAiSync();
gtCursor(edCM);  // G-T1：行尾/空行 → 重置 800ms 防抖（ghost 在屏则先退场）
});
edCM.on('scroll',function(){
if(edAiEl&&!edAiEl.hidden)edAiHide();  // 滚动即收起浮出菜单（下一次 cursorActivity 重新定位）
if(!edSync||!edCM)return;
var si=edCM.getScrollInfo(),den=si.height-si.clientHeight;
if(den<=0)return;
if(edEchoE>=0){var sw=Math.abs(si.top-edEchoE)<3;edEchoE=-1;if(sw)return;}
var d=edFrameDoc();if(!d)return;
var b=d.documentElement,d2=b.scrollHeight-b.clientHeight;
if(d2<=0)return;
var t=Math.max(0,(si.top/den)*d2);
edLockP=true;b.scrollTop=t;edEchoP=t;
setTimeout(function(){edLockP=false;},0);});
// 预览→编辑（包一层 LF.onload：既有 ui.js 监听照跑——它只写隐藏 textarea，无害；
// 当前文档若已就绪再直挂一次，__edSync 标记防同一文档重复挂）
var LF=$id('live-frame');
if(LF){
var orig=LF.onload;
LF.onload=function(){
if(orig)orig.apply(this,arguments);
edAttachPreview();};
edAttachPreview();}
edGoalPaint();edSprintPaint();edPaintSyncBtn();
}
var ED_BTNS=[
['bold','B','粗体（⌘B）'],['italic','I','斜体（⌘I）'],['strike','S','删除线'],
['h1','H1','一级标题'],['h2','H2','二级标题'],['h3','H3','三级标题'],
['quote','❝','引用'],['link','链接','链接（⌘K）'],['code','</>','行内码 / 围栏代码块'],
['table','表格','插入表格'],['image','图片','插入图片'],
['mat','📚','内联素材搜索（⌘⇧S）：编辑器底部实时搜素材库，点击即插入引用'],
['ai','✨AI','创作工坊 AI 块生成（F3 微操作落地前的入口）'],
['full','⛶','全屏'],
['focus','专注','专注模式（F11）：仅留编辑区，其它面板收起'],
['tw','打字机','打字机滚动：当前行恒居视口中央']];

// ── 工具栏动作 ──
function edToolbar(k){
if(!edCM)return;
if(k==='bold')edWrapSel('**','**');
else if(k==='italic')edWrapSel('*','*');
else if(k==='strike')edWrapSel('~~','~~');
else if(k==='h1')edApply(edLinePrefixMark('# '));
else if(k==='h2')edApply(edLinePrefixMark('## '));
else if(k==='h3')edApply(edLinePrefixMark('### '));
else if(k==='quote')edApply(edLinePrefixMark('> '));
else if(k==='link')edLinkSel();
else if(k==='code')edCodeSel();
else if(k==='table')edSnippet('\n| 列A | 列B | 列C |\n| --- | --- | --- |\n|  |  |  |\n');
else if(k==='image')edSnippet('\n![图片描述](cover.png)\n');
else if(k==='mat')return edRpToggle();
else if(k==='ai')location.hash='#compose';
else if(k==='full')edFullscreen();
else if(k==='focus'){edFocusToggle();syncModeBtn('focus','ed-focus-btn',edFocus);}
else if(k==='tw'){edTwToggle();syncModeBtn('tw','ed-tw',edTypewriter);}
if(k==='focus'||k==='tw')return; // 保持编辑器光标，避免切换后滚动回顶部
edCM.focus();
}
function edLinePrefixMark(m){return function(t,s,e){return edLinePrefix(t,s,e,m);};}
// 纯函数变换套用到 CM 当前选区（fn(text,s,e)→[t2,s2,e2]）
function edApply(fn){
var c=edCM;
var s=c.indexFromPos(c.getCursor('from')),e=c.indexFromPos(c.getCursor('to'));
var r=fn(c.getValue(),s,e);
var end=c.posFromIndex(c.getValue().length);
c.replaceRange(r[0],c.posFromIndex(0),end);
c.setSelection(c.posFromIndex(r[1]),c.posFromIndex(r[2]));
}
function edWrapSel(open,close){edApply(function(t,s,e){return edWrap(t,s,e,open,close);});}
function edSnippet(text){
var c=edCM,cur=c.getCursor();
c.replaceRange(text,cur);
c.setCursor({line:cur.line+1,ch:0});
}
function edLinkSel(){
var c=edCM;
var s=c.indexFromPos(c.getCursor('from')),e=c.indexFromPos(c.getCursor('to')),v=c.getValue();
var inner=s===e?'链接文字':v.slice(s,e);
edApply(function(){return[v.slice(0,s)+'['+inner+'](url)'+v.slice(e),s+1,s+1+inner.length];});
}
function edCodeSel(){
var c=edCM;
if(c.somethingSelected()&&c.getSelection().indexOf('\n')<0){edWrapSel('`','`');return;}
var sel=c.somethingSelected()?c.getSelection():'';
var from=c.getCursor('from'),to=c.getCursor('to');
c.replaceRange('\n```\n'+sel+'\n```\n',from,to);
c.setCursor({line:from.line+2,ch:0});
}
function edSmartTab(cm){
var cur=cm.getCursor();
var list=/^(\s*)(?:[-*+]|\d+[.)])\s+/.test(cm.getLine(cur.line));
if(cm.somethingSelected()||list||cur.ch===0)cm.execCommand('indentMore');
else cm.execCommand('insertSoftTab');
}
function edFullscreen(){
if(document.fullscreenElement)document.exitFullscreen();
else if(document.documentElement.requestFullscreen)document.documentElement.requestFullscreen();
}

// ── 专注模式（F11）+ 段落聚焦 + 打字机 ──
// UIX #9：工具栏与状态栏两套开关的 is-on 态互相同步（两边操作同一状态）
function syncModeBtn(tbKey,statusId,on){
var tb=$id('ed-bar')&&$id('ed-bar').querySelector('[data-edb="'+tbKey+'"]');
if(tb)tb.classList.toggle('is-on',on);
var sb=$id(statusId);
if(sb)sb.classList.toggle('is-on',on);}
function edFocusToggle(){
edFocus=!edFocus;
var panel=$id('panel-edit');
if(panel)panel.classList.toggle('editor-focus',edFocus);
syncModeBtn('focus','ed-focus-btn',edFocus);
if(edFocus)edDimLine();else edUndim();
}
function edDimLine(){
if(!edCM)return;
if(edCurLine!=null){try{edCM.removeLineClass(edCurLine,'wrap','ed-cur');}catch(x){}}
edCurLine=edCM.getCursor().line;
edCM.addLineClass(edCurLine,'wrap','ed-cur');
var panel=$id('panel-edit');
if(panel)panel.classList.add('ed-para');
}
function edUndim(){
if(edCM&&edCurLine!=null){try{edCM.removeLineClass(edCurLine,'wrap','ed-cur');}catch(x){}}
edCurLine=null;
var panel=$id('panel-edit');
if(panel)panel.classList.remove('ed-para');
}
function edTwToggle(on){
edTypewriter=typeof on==='boolean'?on:!edTypewriter;
syncModeBtn('tw','ed-tw',edTypewriter);
if(!edCM)return;
if(edTypewriter){edCM.scrollCursorIntoView=edCenter;edCenter();}
else delete edCM.scrollCursorIntoView;
}
function edCenter(){
if(!edCM)return;
var si=edCM.getScrollInfo();
var co=edCM.cursorCoords({line:edCM.getCursor().line,ch:0},'local');
edCM.scrollTo(null,Math.max(0,co.top-si.clientHeight/2));
}
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.key!=='F11'||e.defaultPrevented)return;
var v=$id('view-article'),p=$id('panel-edit');
if(!v||v.hidden||!p||p.hidden)return;
e.preventDefault();
edFocusToggle();
});

// ── 大纲 + 状态栏统计 ──
function edRefresh(){
if(!edCM)return;
edOutlinePaint();
edStats();
}
function edOutlinePaint(){
var list=$id('ed-ol-list');
if(!list)return;
var out=edOutline(edCM?edCM.getValue():'');
edOutlineCache=out;
list.innerHTML=out.length?out.map(function(h,i){
return'<a class="ed-ol-item lv'+h.level+'" data-ol="'+i+'"><span class="ed-ol-t">'+edEsc(h.title)+'</span><span class="ed-ol-c">'+h.chars+'</span></a>';
}).join(''):'<p class="ed-ol-empty">无标题——行首 # 起草大纲</p>';
}
function edStats(){
if(!edCM)return;
var v=edCM.getValue(),ls=v.split('\n'),c=edCount(v);
var el=$id('ed-chars');
if(el)el.textContent=c;
edOutlineCache=edOutlineCache.length?edOutlineCache:edOutline(v);
edSect();
var p=$id('ed-paras');
if(p)p.textContent=v.split(/\n\s*\n/).filter(function(x){return x.trim();}).length;
var a=$id('ed-aib');
if(a)a.textContent=edAI(v);
var r=$id('ed-read');
if(r)r.textContent=edRead(c)+' 分钟';
edGoalPaint();
}
function edSect(){
var el=$id('ed-sect');
if(!el||!edCM)return;
var v=edCM.getValue(),out=edOutlineCache.length?edOutlineCache:edOutline(v);
var sec=edSection(out,edCM.getCursor().line);
el.textContent=sec>=0?out[sec].chars:edCount(ls2(v,out).join('\n'));
function ls2(text,o){return text.split('\n').slice(0,o.length?o[0].line:undefined);}
}

// ── 字数目标（localStorage）──
function edGoalGet(){var n=+localStorage.getItem(GOAL_KEY)||0;return n>0?n:0;}
function edGoalPaint(){
var wrap=$id('ed-goal');
if(!wrap)return;
var g=edGoalGet(),c=edCM?edCount(edCM.getValue()):0;
if(!g){wrap.innerHTML='<button type="button" class="btn ed-mini" id="ed-goal-btn">🎯 设目标</button>';}
else{
var pct=Math.min(100,Math.round(c/g*100));
wrap.innerHTML='<span class="ed-goal-bar" title="字数目标进度 '+pct+'%"><i style="width:'+pct+'%"></i></span>'
+'<button type="button" class="btn ed-mini" id="ed-goal-btn">'+c+'/'+g+' 字</button>';}
var b=$id('ed-goal-btn');
if(b)b.addEventListener('click',edGoalEdit);
}
function edGoalEdit(){
var wrap=$id('ed-goal');
if(!wrap)return;
wrap.innerHTML='<input type="number" min="0" id="ed-goal-in" aria-label="目标字数">';
var inp=$id('ed-goal-in');
inp.value=edGoalGet()||'';
inp.focus();inp.select();
var done=false;
function fin(cancel){
if(done)return;done=true;
if(!cancel){
var v=Math.max(0,Math.floor(+inp.value||0));
try{localStorage.setItem(GOAL_KEY,String(v));}catch(x){}}
edGoalPaint();}
inp.addEventListener('keydown',function(ev){
if(ev.isComposing||ev.keyCode===229)return;
if(ev.key==='Enter'){ev.preventDefault();fin(false);}
if(ev.key==='Escape')fin(true);});
inp.addEventListener('blur',function(){fin(false);});
}

// ── 写作冲刺（25min 番茄钟 + 本期新增字数）──
function edSprintToggle(){
if(edSprint&&edSprint.on){
clearInterval(edSprint.timer);edSprint=null;edSprintPaint();return;}
edSprint={on:true,end:Date.now()+SPRINT_MS,chars:0,len:edCM?edCount(edCM.getValue()):0};
edSprint.timer=setInterval(edSprintTick,1000);
edSprintTick();
}
function edSprintTick(){
if(!edSprint)return;
if(Date.now()>=edSprint.end){
clearInterval(edSprint.timer);
edSprint={on:false,done:'冲刺完成 +'+edSprint.chars+' 字'};
setTimeout(function(){if(edSprint&&!edSprint.on){edSprint=null;edSprintPaint();}},8000);
}
edSprintPaint();
}
function edSprintPaint(){
var el=$id('ed-sprint');
if(!el)return;
if(!edSprint){el.textContent='⏱ 冲刺';el.classList.remove('is-on');return;}
if(!edSprint.on){el.textContent=edSprint.done;el.classList.add('is-on');return;}
var s=Math.max(0,Math.floor((edSprint.end-Date.now())/1000));
el.textContent='⏱ '+('0'+Math.floor(s/60)).slice(-2)+':'+('0'+s%60).slice(-2)+' · 本期 '+edSprint.chars+' 字';
el.classList.add('is-on');
}

// ── 搜索替换（SearchCursor 直驱；⌘F 开 / ⌘⇧F 开 / Enter 下一个 / ⇧Enter 上一个 / Esc 关）──
function edBarOpen(){var b=$id('ed-find');return!!(b&&!b.hidden);}
function edFindShow(){
var bar=$id('ed-find');
if(!bar)return;
bar.hidden=false;
var q=$id('ed-find-q');
q.focus();q.select();
edFindCount();
}
function edFindHide(){
var bar=$id('ed-find');
if(bar)bar.hidden=true;
if(edCM)edCM.focus();
}
function edFindGo(back){
if(!edCM)return;
var q=$id('ed-find-q').value;
if(!q)return;
var from=back?edCM.getCursor('from'):edCM.getCursor('to');
var cur=edCM.getSearchCursor(q,from,{caseFold:true});
var hit=back?cur.findPrevious():cur.findNext();
if(!hit){
var last=edCM.lastLine();
var wp=back?{line:last,ch:edCM.getLine(last).length}:{line:0,ch:0};
cur=edCM.getSearchCursor(q,wp,{caseFold:true});
hit=back?cur.findPrevious():cur.findNext();}
if(hit){
edCM.setSelection(cur.from(),cur.to());
edCM.scrollIntoView({from:cur.from(),to:cur.to()},40);}
}
function edFindCount(){
var el=$id('ed-find-n');
if(!el||!edCM)return;
var q=$id('ed-find-q').value;
if(!q){el.textContent='';return;}
var cur=edCM.getSearchCursor(q,{line:0,ch:0},{caseFold:true}),n=0;
while(cur.findNext())n++;
el.textContent=n?n+' 处':'无匹配';
}
function edFindReplace(){
if(!edCM)return;
var q=$id('ed-find-q').value;
if(!q)return;
if(edCM.getSelection()&&edCM.getSelection().toLowerCase()===q.toLowerCase())
edCM.replaceSelection($id('ed-find-r').value,'around');
edFindGo(false);
edFindCount();
}
function edFindReplaceAll(){
if(!edCM)return;
var q=$id('ed-find-q').value;
if(!q)return;
var r=$id('ed-find-r').value,n=0;
var cur=edCM.getSearchCursor(q,{line:0,ch:0},{caseFold:true});
edCM.operation(function(){while(cur.findNext()){cur.replace(r);n++;}});
edFindCount();
edRefresh();
}

// ── E-2 内联研究面板（§3.2：编辑器底部搜素材库，0 次切视图 0 次复制）──
// 开合：⌘⇧S（CM 聚焦走 extraKeys，此处兜编辑器外焦点）/ 工具栏📚 / Esc 收起。
var RP_TY={text:'📖',image:'🖼️',video:'🎬',link:'🔗',radar_signal:'📡',crawled:'🌐',derived:'🧩'};
var RP_DEB=300,RP_LIMIT=8,rpDeb=null,rpSeq=0,rpItems=[];
function edRpOpen(){var p=$id('ed-rp');return!!(p&&!p.hidden);}
function edRpShow(){
var p=$id('ed-rp');
if(!p)return;
p.hidden=false;
var q=$id('ed-rp-q');
if(q){q.focus();q.select();}}
function edRpHide(){
var p=$id('ed-rp');
if(p)p.hidden=true;
if(rpDeb){clearTimeout(rpDeb);rpDeb=null;}
if(edCM)edCM.focus();}
// ── 三线交界裁决（E-2×E-3 集成批，选中优先级>搜索）──
// edAiMenuVis：选中浮出菜单当前可见。edAiSelActive：选中态拥有舞台——
// AI diff 弹窗开着，或当前选区达浮出门槛（>10 字）。此态下 ⌘⇧S/📚 不开
// 搜索面板也不夺焦点（开向争用=选中赢）；面板已开时收起恒允许（关向不设限）。
function edAiMenuVis(){return!!(edAiEl&&!edAiEl.hidden);}
function edAiSelActive(){
if(edAiDlg&&!edAiDlg.hidden)return true;
return!!(edCM&&edAIMenuOn(edCM.getSelection()));}
function edRpToggle(){
if(edRpOpen())return edRpHide();
if(edAiSelActive())return;  // 选中优先级>搜索：AI 态不开面板（收起选区/关弹窗后即可开）
edRpShow();}
function edRpHint(t){
var l=$id('ed-rp-list');
if(l)l.innerHTML='<p class="ed-rp-empty">'+edEsc(t||'输入关键词搜索素材（标题/标签/正文）…')+'</p>';}
// debounce 300ms → GET /api/materials?q=&limit=8；序号守卫丢弃过期响应（debounce 竞态）
function edRpFetch(){
var q=$id('ed-rp-q');
if(!q)return;
var kw=q.value.trim(),seq=++rpSeq;
var n=$id('ed-rp-n');
if(n)n.textContent=kw?'…':'';
if(!kw){edRpHint('');return;}
edRpHint('搜索中…');
fetch('/api/materials?q='+encodeURIComponent(kw)+'&limit='+RP_LIMIT)
.then(function(r){return r.json();})
.then(function(d){
if(seq!==rpSeq)return;
var items=(d&&Array.isArray(d.items))?d.items:[];
rpItems=items;
var n=$id('ed-rp-n');
if(n)n.textContent=d&&d.total!=null?(items.length+'/'+d.total+' 条'):'';
var list=$id('ed-rp-list');
if(!list)return;
if(!items.length){edRpHint('无匹配素材——换个关键词试试');return;}
list.innerHTML=items.map(function(m,i){
var t=m.title||(m.origin||{}).title||m.id;
return'<div class="ed-rp-row"><span class="ed-rp-ico" title="'+edEsc(m.type||'')+'">'+(RP_TY[m.type]||'📄')+'</span>'
+'<span class="ed-rp-main"><span class="ed-rp-tt" title="'+edEsc(t)+'">'+edEsc(t)+'</span>'
+'<span class="ed-rp-x" title="'+edEsc(edMatExcerpt(m))+'">'+edEsc(edMatExcerpt(m))+'</span></span>'
+'<span class="ed-rp-meta">'+edEsc(edMatMeta(m))+'</span>'
+'<button type="button" class="btn ed-mini" data-rpins="'+i+'">'+(m.type==='image'?'插入图片':'插入引用')+'</button></div>';
}).join('');
})
.catch(function(){if(seq===rpSeq){rpItems=[];edRpHint('搜索失败（Studio 服务不可达）');}});}
// 光标处插入：行中起插先补换行、尾随换行收段落；replaceSelection('end') 光标落插入文本后
function edMatInsert(text){
if(!edCM)return;
var cur=edCM.getCursor();
var line=edCM.getLine(cur.line)||'';
var pre=(line.replace(/[ \t]+$/,'')&&cur.ch>0)?'\n':'';
edCM.replaceSelection(pre+text+'\n','end');}
function edRpInsert(i){
var m=rpItems[+i];
if(!m||!edCM)return;
var p=m.payload||{};
if(m.type==='image'){
edMatInsert(edImgMd('/api/materials/file?id='+encodeURIComponent(m.id)+'&variant=full'));}
else{
edMatInsert(edQuote(m.title||(m.origin||{}).title||'未命名素材',p.text||p.url||''));}
var q=$id('ed-rp-q');
if(q)q.focus();}
// ⌘⇧S（Ctrl+Shift+S）：编辑器域内开合内联搜索；e.defaultPrevented 让渡沿 F1 惯例
document.addEventListener('keydown',function(e){
if(e.isComposing||e.keyCode===229)return;
if(e.defaultPrevented)return;
if(!(e.metaKey||e.ctrlKey)||!e.shiftKey)return;
if(e.key!=='s'&&e.key!=='S')return;
var v=$id('view-article'),p=$id('panel-edit');
if(!v||v.hidden||!p||p.hidden)return;
e.preventDefault();
edRpToggle();
});

// ── E-3 AI 微操作（§3.3：选中 → 浮动菜单 5 动作 → diff 弹窗 → 接受写回）──
// 链路：CM cursorActivity → 选区 >10 字 → 菜单定位到选区上方 → 点动作 →
// POST /api/editor/ai-action（同步，前端渲染加载态）→ diff 弹窗红删绿增 →
// [接受] CM replaceSelection(gen 包裹 <!--ai:block-->) / [拒绝] 仅关闭（零写回）。
var AI_ACTS=[
['expand','✨','扩写','补足细节与衔接'],
['summarize','📝','摘要','压成 1-3 句'],
['rewrite','🔄','改写','换个说法'],
['continue','💡','续写','顺文风续 2-3 句'],
['qa','❓','问答','就这段问 AI']
];
var AI_CTX_MAX=600;
var edAiEl=null,edAiDlg=null,edAiKeep=null,edAiGen='';
// 菜单与弹窗骨架（只建一次；edBuild 时调用）
function edAiBuild(host){
if(edAiEl||!host)return;
var m=document.createElement('div');
m.className='ed-ai';m.id='ed-aimenu';m.hidden=true;
m.setAttribute('role','toolbar');
m.innerHTML=AI_ACTS.map(function(a){
return'<button type="button" class="btn ed-mini ed-ai-b" data-edai="'+a[0]+'" title="'+edEsc(a[3])+'">'
+a[1]+' '+edEsc(a[2])+'</button>';}).join('');
m.addEventListener('mousedown',function(ev){ev.preventDefault();});  // 点按钮不夺走选区
m.addEventListener('click',function(ev){
var b=ev.target.closest('[data-edai]');
if(b)edAiRun(b.getAttribute('data-edai'));});
host.appendChild(m);
edAiEl=m;
// diff 弹窗（modal overlay + 红删绿增；复用 core.css 的 .modal-overlay 语义）
var d=document.createElement('div');
d.className='modal-overlay ed-ai-dlg';d.id='ed-ai-dlg';d.hidden=true;
d.setAttribute('aria-hidden','true');
d.innerHTML='<div class="modal ed-ai-box" role="dialog" aria-modal="true" aria-labelledby="ed-ai-title">'
+'<div class="ed-ai-head"><h3 class="modal-title" id="ed-ai-title">✨ AI 扩写</h3>'
+'<span class="ed-ai-stat" id="ed-ai-stat"></span></div>'
+'<div class="ed-ai-sec"><p class="ed-ai-lab ed-ai-del-lab">── 原文 ──</p>'
+'<div class="ed-ai-body ed-ai-del" id="ed-ai-old"></div></div>'
+'<div class="ed-ai-sec"><p class="ed-ai-lab ed-ai-add-lab">++ AI 产出 ──</p>'
+'<div class="ed-ai-body ed-ai-add" id="ed-ai-new"></div></div>'
+'<div class="ed-ai-err" id="ed-ai-err" hidden></div>'
+'<div class="modal-actions"><button type="button" class="btn btn-secondary" id="ed-ai-rej">拒绝</button>'
+'<button type="button" class="btn btn-primary" id="ed-ai-acc">接受·标记AI生成</button></div></div>';
document.body.appendChild(d);
edAiDlg=d;
d.querySelector('#ed-ai-rej').addEventListener('click',function(){edAiClose();});
d.querySelector('#ed-ai-acc').addEventListener('click',edAiAccept);
d.addEventListener('click',function(ev){if(ev.target===d)edAiClose();});
}
function edAiBusySet(on){
var acc=$id('ed-ai-acc'),rej=$id('ed-ai-rej'),err=$id('ed-ai-err');
if(acc){acc.disabled=on;acc.textContent=on?'生成中…':'接受·标记AI生成';}
if(rej)rej.disabled=false;
if(on&&err){err.hidden=true;err.textContent='';}
}
// cursorActivity → 菜单开合 + 定位（选区上方；避让编辑区顶边）
function edAiSync(){
if(!edAiEl||!edCM)return;
if(edAiDlg&&!edAiDlg.hidden)return;  // 弹窗开着不动菜单
var sel=edCM.getSelection();
if(!edAIMenuOn(sel)){edAiHide();return;}
var from=edCM.getCursor('from'),to=edCM.getCursor('to');
var co=edCM.cursorCoords({line:from.line,ch:from.ch},'local');
var si=edCM.getScrollInfo(),top=co.top-si.top-38;
edAiEl.hidden=false;
edAiEl.style.top=Math.max(0,top)+'px';
edAiEl.style.left=Math.max(0,Math.min(co.left,Math.max(0,si.width-320)))+'px';
}
function edAiHide(){if(edAiEl)edAiEl.hidden=true;}
function edAiClose(){
if(edAiDlg)edAiDlg.hidden=true;
edAiKeep=null;edAiGen='';
if(edCM)edCM.focus();
}
function edAiTitle(action){
var i,lab='AI';
for(i=0;i<AI_ACTS.length;i++)if(AI_ACTS[i][0]===action)lab=AI_ACTS[i][1]+' AI '+AI_ACTS[i][2];
return lab;
}
// 点动作：取选区+上下文 → POST → 渲染 diff（同步调用，前端 loading 即刻可见）
function edAiRun(action){
if(!edCM)return;
var sel=edCM.getSelection();
if(!edAIMenuOn(sel))return;
var s=edCM.indexFromPos(edCM.getCursor('from')),e=edCM.indexFromPos(edCM.getCursor('to'));
if(s>e){var w=s;s=e;e=w;}
var doc=edCM.getValue();
edAiKeep={action:action,s:s,e:e,text:sel};
edAiGen='';
edAiHide();
if(!edAiDlg)return;
$id('ed-ai-title').textContent=edAiTitle(action);
$id('ed-ai-stat').textContent='';
$id('ed-ai-old').textContent=sel;
$id('ed-ai-new').textContent='';
edAiDlg.hidden=false;
edAiBusySet(true);
var orig=edAiDlg.querySelector('#ed-ai-new');
if(orig)orig.textContent='正在生成（'+edAiTitle(action)+'）…';
fetch('/api/editor/ai-action',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({confirm:true,action:action,text:sel,context:edCtx(doc,s,e,AI_CTX_MAX)})})
.then(function(r){return r.json().then(function(j){
if(!r.ok)throw new Error((j&&j.error)||('HTTP '+r.status));
return j;});})
.then(function(j){
if(!j||j.ok!==true)throw new Error((j&&j.error)||'生成失败');
edAiPaint(j.gen||'',sel);
})
.catch(function(err){
var box=$id('ed-ai-new');
if(box)box.textContent='';
var er=$id('ed-ai-err');
if(er){er.hidden=false;er.textContent='生成失败：'+(err&&err.message?err.message:'未知错误');}
edAiBusySet(false);
var acc=$id('ed-ai-acc');
if(acc)acc.disabled=true;  // 无产出不可接受（拒绝仍可关闭）
});}
// diff 渲染：原文红底删除线、AI 产出绿底（§3.3 红删绿增）
function edAiPaint(gen,sel){
edAiGen=String(gen==null?'':gen);
var rows=edDiffRows(sel,edAiGen),st=edDiffStat(rows);
var oldH=[],newH=[],i,r;
for(i=0;i<rows.length;i++){
r=rows[i];
if(r.t>0)newH.push('<ins>'+edEsc(r.s)+'</ins>');
else if(r.t<0)oldH.push('<del>'+edEsc(r.s)+'</del>');
else{oldH.push('<span>'+edEsc(r.s)+'</span>');newH.push('<span>'+edEsc(r.s)+'</span>');}
}
var o=$id('ed-ai-old'),n=$id('ed-ai-new'),s=$id('ed-ai-stat');
if(o)o.innerHTML=oldH.join('')||'<span class="ed-ai-none">（原文为空）</span>';
if(n)n.innerHTML=newH.join('')||'<span class="ed-ai-none">（AI 无产出）</span>';
if(s)s.textContent='+'+(st.add||0)+' −'+(st.del||0);
edAiBusySet(false);
}
// [接受]：写回纯函数 edAccept 决定落点（索引命中 / 选区命中 / 拒写），
// 本函数只负责套用到 CM（保留撤销栈：整篇一次 replaceRange 而非逐段拼）
function edAiAccept(){
if(!edCM||!edAiKeep||!edAiGen){edAiClose();return;}
var doc=edCM.getValue();
var selNow=edCM.getSelection();
var r=edAccept(doc,edAiKeep.s,edAiKeep.e,edAiKeep.text,selNow,edAiGen);
if(r.where==='stale'){
var er=$id('ed-ai-err');
if(er){er.hidden=false;er.textContent='正文已改动且选区已变——为免覆盖你的新文字，本次未写回（可重新选中再生成）。';}
return;
}
if(r.where==='empty'){edAiClose();return;}
var end=edCM.posFromIndex(doc.length);
edCM.replaceRange(r.doc,edCM.posFromIndex(0),end,'+ai');
edCM.setCursor(edCM.posFromIndex(r.e));
edAiClose();
edRefresh();
}

// ── 同步滚动（CM↔预览 iframe；双层防回环沿 ui.js sLock/sEcho 纪律）──
function edFrameDoc(){
var f=$id('live-frame');
try{return f&&f.contentDocument&&f.contentDocument.body?f.contentDocument:null;}
catch(x){return null;}
}
function edAttachPreview(){
if(!edCM)return;
var d=edFrameDoc();
if(!d||d.__edSync)return;
d.__edSync=true;
d.addEventListener('scroll',function(){
if(!edSync||edLockP||!edCM)return;
var b=d.documentElement,d2=b.scrollHeight-b.clientHeight;
if(d2<=0)return;
if(edEchoP>=0){var sw=Math.abs(b.scrollTop-edEchoP)<2;edEchoP=-1;if(sw)return;}
var si=edCM.getScrollInfo(),den=si.height-si.clientHeight;
if(den<=0)return;
edCM.scrollTo(null,Math.max(0,(b.scrollTop/d2)*den));
edEchoE=edCM.getScrollInfo().top;
});
}
function edPaintSyncBtn(){
var b=$id('ed-sync');
if(b)b.textContent='同步 '+(edSync?'开':'关');
}

// ── 离开文章视图的销毁语义：退专注/收搜索/收内联面板/收 AI 菜单与弹窗/收 ghost ──
function edSuspend(){
if(edFocus)edFocusToggle();
if(edBarOpen())edFindHide();
if(edRpOpen())edRpHide();
edAiHide();
if(edAiDlg&&!edAiDlg.hidden)edAiClose();
gtReset();  // G-T1：ghost 全量复位（书签清/计时清/保持态清/序号前推丢过期响应）
}
(function(){
var v=$id('view-article');
if(window.MutationObserver&&v){
new MutationObserver(function(){
var el=$id('view-article');
if(el&&el.hidden)edSuspend();
}).observe(v,{attributes:true,attributeFilter:['hidden']});}
})();

window.__mdpubEditor={
activate:activate,
suspend:edSuspend,
get cm(){return edCM;}
};
})();
