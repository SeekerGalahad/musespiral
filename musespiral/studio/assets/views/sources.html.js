document.getElementById('view-sources').innerHTML=`
<div class="amb amb-sources" aria-hidden="true"></div>
<div class="view-head">
<div>
<h1 class="view-title">源中枢</h1>
<p class="view-sub">源是内容的进水口：这里接上的每个源，都会把内容送进素材库。</p>
</div>
<div class="action-row">
<button class="btn btn-ghost" id="src-verify-all-btn" title="逐源手动探测健康（无自动轮询）">验证全部</button>
<button class="btn" id="src-add-btn"><span data-icon="plus"></span>添加源</button>
<button class="btn btn-primary" id="src-ingest-all-btn" title="刷新全部源（ingest 任务，进度见任务中心）"><span data-icon="refresh"></span>刷新全部</button>
</div>
</div>
<div class="chips src-stats" id="src-stats" aria-label="概览"></div>

<section class="src-pipeline" id="src-pipeline" aria-label="在内容流水线中的位置">
<ol class="pl-chain">
<li><span class="pl-node is-here"><span data-icon="download"></span>源中枢<span class="pl-role">输入</span></span></li>
<li class="pl-sep" aria-hidden="true"><span data-icon="arrow-right"></span></li>
<li><a class="pl-node" href="#material">素材库<span class="pl-role">汇集</span></a></li>
<li class="pl-sep" aria-hidden="true"><span data-icon="arrow-right"></span></li>
<li><a class="pl-node" href="#compose">创作工坊<span class="pl-role">处理</span></a></li>
<li class="pl-sep" aria-hidden="true"><span data-icon="arrow-right"></span></li>
<li><a class="pl-node" href="#publish">发布中心<span class="pl-role">输出</span></a></li>
</ol>
<p class="pl-note">和「插件」页是同一套分法：<b>输入 → 处理 → 输出</b>。源就是这条线的输入端，它只做一件事：把别处的内容取回来，放进素材库。</p>
<button class="btn btn-ghost btn-sm pl-fold" id="src-pl-fold" aria-expanded="false">精简</button>
</section>

<section class="src-search" aria-label="统一搜索">
<div class="src-search-box">
<span data-icon="search"></span>
<input class="input" id="src-q" type="search" placeholder="搜书名、作者，或粘贴网址直接阅读" aria-label="搜索书籍、素材，或粘贴网址阅读网页" autocomplete="off" spellcheck="false">
<button class="btn btn-ghost btn-icon" id="src-q-clear" aria-label="清空搜索" hidden><span data-icon="close"></span></button>
<span class="kbd" id="src-q-kbd" aria-hidden="true">/</span>
</div>
<p class="engine-line" id="src-engine-line" aria-live="polite"></p>
</section>

<div id="src-overview">
<section class="src-sec" aria-labelledby="src-mine-title">
<div class="sec-head">
<h2 id="src-mine-title">我的源</h2>
<span class="sec-sub" id="src-mine-sub"></span>
<div class="chips sec-tools" id="src-filters" role="group" aria-label="按范围筛选源"></div>
</div>
<div id="src-mine-body"></div>
</section>
<section class="src-sec" aria-labelledby="src-more-title">
<div class="sec-head">
<h2 id="src-more-title">还能接什么</h2>
<span class="sec-sub">每种源只需要你告诉它一件事：去哪里取。</span>
</div>
<div class="con-grid" id="src-con-grid"></div>
</section>
</div>

<section class="results" id="src-results" aria-live="polite" hidden></section>

<div class="empty" id="src-empty" hidden>
<div class="empty-icon" data-icon="settings"></div>
<div class="empty-title">还没有内容源</div>
<div class="empty-text">源是采集的起点：从下面挑一种，告诉它去哪里取内容，点「刷新」后素材会出现在素材库。</div>
<button class="btn btn-primary empty-action" id="src-empty-add">添加第一个源</button>
</div>

<aside class="drawer" id="src-drawer" hidden aria-label="源详情">
<div class="drawer-head"><b id="src-drawer-title">源</b><button class="btn btn-ghost btn-icon" id="src-drawer-close" type="button" aria-label="关闭"><span data-icon="close"></span></button></div>
<div class="drawer-body" id="src-drawer-body"></div>
</aside>

<div class="modal-overlay" id="src-form" hidden>
<div class="modal modal-lg" role="dialog" aria-modal="true" aria-labelledby="sf-title">
<h3 class="modal-title" id="sf-title">添加源</h3>
<p class="action-hint">先选一种源，再告诉它去哪里取内容。名字可以不改，会自动起。</p>
<div class="sf-body">
<div id="sf-types" class="sf-types" role="radiogroup" aria-label="源类型"></div>
<form id="sf-fields" novalidate></form>
</div>
<div id="sf-test" aria-live="polite"></div>
<div class="modal-actions"><button class="btn" id="sf-test-btn">测试连接</button><button class="btn btn-secondary" id="sf-cancel">取消</button><button class="btn btn-primary" id="sf-ok">接上</button></div>
</div>
</div>

<div class="modal-overlay" id="src-aicfg" hidden>
<div class="modal" role="dialog" aria-modal="true">
<h3 class="modal-title" id="sac-sub"></h3>
<div style="display:flex;flex-direction:column;gap:8px">
<select id="sac-profile" class="input"></select>
<input id="sac-model" class="input" placeholder="model">
<input id="sac-temp" class="input" type="number" placeholder="温度 0-2">
<textarea id="sac-prompt" class="set-area" rows="5" placeholder="prompt_override"></textarea>
</div>
<div class="modal-actions"><button class="btn btn-secondary" id="sac-cancel">取消</button><button class="btn btn-primary" id="sac-ok">保存</button></div>
</div>
</div>
`;
