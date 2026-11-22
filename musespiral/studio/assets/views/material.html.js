document.getElementById('view-material').innerHTML=`
<div class="amb amb-material" aria-hidden="true"></div>
<div class="view-head"><div>
<h1 class="view-title">素材库</h1>
<p class="view-sub" id="mat-sub">加载中…</p>
</div>
<div class="action-row">
<div class="chips" id="mat-dens" aria-label="密度切换">
<button class="chip is-active" data-mdens="compact" title="一行一素材，展开见四维与动作">紧凑</button>
<button class="chip" data-mdens="src" title="按源分组，组头带计数">各源</button>
<button class="chip" data-mdens="full" title="全部展开，评分动作一览">沉浸</button>
</div>
<input type="file" id="mat-file" multiple accept="image/png,image/jpeg,image/gif,image/webp,.pdf,.docx,.epub,.md,.markdown,.txt" hidden>
<button class="btn" id="mat-upload-btn" title="选择/拖放/粘贴图片上传">+ 上传图片</button>
<button class="btn gen-ai" id="mat-gen-btn">AI 生成图</button>
<button class="btn" id="mat-reg-btn" title="登记视频 URL（零出网）">+ 登记视频</button>
<button class="btn" id="mat-ingest-btn" title="刷新全部源（ingest 任务，进度见任务中心）">刷新全部</button></div>
</div>
<div class="mat-layout">
<aside class="coll-rail" id="coll-rail" aria-label="素材集合">
<div class="coll-group" id="coll-static"></div>
<div id="coll-manual-sec" hidden>
<div class="coll-cap">手工集合<button class="coll-new" id="coll-new-btn" type="button">+ 新建</button></div>
<div class="coll-group" id="coll-manual"></div>
</div>
<div class="coll-cap">智能集合</div>
<div class="coll-group" id="coll-smart"></div>
<div id="coll-tags-sec" hidden>
<div class="coll-cap">按标签</div>
<div class="coll-group" id="coll-tags"></div>
</div>
<div class="coll-cap">源集合</div>
<div class="coll-group" id="coll-srcs"></div>
</aside>
<div class="mat-main">
<div class="chips" id="mat-tabs" aria-label="素材分诊">
<button class="chip is-active" data-mtab="all">全部</button>
<button class="chip" data-mtab="starred">收藏<span class="chip-count" id="mt-starred">0</span></button>
<button class="chip" data-mtab="unread">未读<span class="chip-count" id="mt-unread">0</span></button>
<button class="chip" id="mat-coll-exit" data-mcoll="" hidden>✕ 退出集合</button>
</div>
<div class="pub-toolbar">
<div class="mat-search-wrap"><input id="mat-search" class="input" placeholder="全库搜索：标题/正文/标签/meta…" aria-label="全库搜索素材"><button id="mat-search-x" type="button" class="coll-new" aria-label="清空搜索" hidden>✕ 清空</button></div>
<div class="chips mat-search-modes" id="mat-modes" aria-label="搜索模式（可手动切换）"><button class="chip is-active" data-searchmode="auto" title="自动：自然语言走混合，其余走关键词">自动</button><button class="chip" data-searchmode="keyword" title="关键词：标题/正文/标签/meta 词面匹配">关键词</button><button class="chip" data-searchmode="semantic" title="语义：按自然语言描述找相关素材（需 embedding）">语义</button></div>
<select id="mat-type" class="input" aria-label="类型筛选"><option value="">全部类型</option></select>
<select id="mat-sort" class="input" aria-label="排序"><option value="score">评分降序</option><option value="relevance">相关度</option><option value="time">时间降序</option><option value="title">标题</option></select>
</div>
<p class="action-hint mat-mode-hint" id="mat-mode-hint" hidden></p>
<div class="sel-bar" id="sel-bar" hidden><span>已选 <b id="sel-n">0</b> 篇</span><button class="btn btn-primary" id="syn-btn" disabled>AI 综合</button><button class="btn btn-ghost" id="sel-clear">取消选择</button></div>
<div class="drop-overlay" id="mat-drop" hidden>松开上传图片</div>
<div id="mat-tray" hidden></div>
<div id="mat-list" class="mat-list d-compact" aria-label="素材列表"></div>
<div class="empty" id="mat-empty" hidden>
<div class="empty-icon" data-icon="search"></div>
<div class="empty-title" id="mat-empty-title">暂无素材</div>
<div class="empty-text" id="mat-empty-text">在源中枢验证源后点「刷新」，或点右上「刷新全部」采集；采集只新增不改写。</div>
<button class="btn empty-action" id="mat-empty-action" hidden>清除筛选</button>
</div>
</div>
<aside class="drawer" id="mat-drawer" hidden aria-label="素材详情">
<div class="drawer-head"><b id="mat-drawer-title">素材</b><button class="btn btn-ghost btn-icon" id="mat-drawer-close" type="button" aria-label="关闭详情"><span data-icon="close"></span></button></div>
<div class="drawer-body" id="mat-drawer-body"><p class="action-hint">加载中…</p></div>
</aside>
<aside class="drawer" id="syn-panel" hidden aria-label="跨素材综合"><div class="drawer-head"><b>跨素材综合</b><span class="badge" id="syn-n"></span><button class="btn btn-ghost btn-icon" id="syn-close" aria-label="关闭综合面板"><span data-icon="close"></span></button></div><div class="drawer-body" id="syn-body"></div></aside>
<aside class="drawer" id="gen-panel" hidden aria-label="AI 生成图"><div class="drawer-head"><b>AI 生成图</b><button class="btn btn-ghost btn-icon" id="gen-close" type="button" aria-label="关闭生成面板"><span data-icon="close"></span></button></div><div class="drawer-body"><textarea id="gen-prompt" class="set-area" rows="3" placeholder="画面描述" aria-label="画面描述"></textarea><div class="chips gen-cfg" aria-label="尺寸与模型"><button class="chip is-active" data-size="1024x1024">1:1</button><button class="chip" data-size="1536x1024">3:2</button><button class="chip is-active" data-model="">FLUX-schnell</button><button class="chip" data-model="Qwen-Image">Qwen-Image</button></div><button class="btn btn-primary" id="gen-run">生成</button> <button class="btn" id="gen-cancel" hidden>取消</button><div id="gen-result"></div></div></aside>
</div>
<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="图片预览" title="Esc 关闭 · ←→ 切换">
<img id="lb-img" alt="原图">
<div class="lb-bar" id="lb-bar"></div>
</div>
<div class="modal-overlay" id="coll-form" hidden>
<div class="modal" role="dialog" aria-modal="true" aria-labelledby="cf-title">
<h3 class="modal-title" id="cf-title">新建集合</h3>
<div style="display:flex;flex-direction:column;gap:8px">
<input id="cf-name" class="input" maxlength="24" placeholder="名称 ≤24 字符">
<div class="chips" id="cf-dots"></div>
</div>
<div class="modal-actions"><button class="btn btn-secondary" id="cf-cancel">取消</button><button class="btn btn-primary" id="cf-ok">创建</button></div>
</div>
</div>
<datalist id="tag-dl"></datalist>
`;
