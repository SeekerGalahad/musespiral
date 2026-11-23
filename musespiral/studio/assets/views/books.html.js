document.getElementById('view-books').innerHTML=`
<div class="view-head"><div>
<h1 class="view-title">书籍</h1>
<p class="view-sub" id="bk-sub">订阅书源 → 搜索书籍 → 选章入素材库（引擎取文，人工选章触发）。</p>
</div><div class="action-row">
<span class="bk-lamp" id="bk-lamp"><span class="health-dot unknown"></span>…</span>
<button class="btn btn-ghost" id="bk-subs-btn">订阅管理</button>
</div></div>
<div id="bk-search">
<input id="bk-search-box" class="input bk-search-input" placeholder="搜索书名 / 作者 / 关键词…" aria-label="搜索书籍">
<div class="empty" id="bk-guide">
<div class="empty-icon" data-icon="docs"></div>
<div class="empty-title" id="bk-guide-t">搜索结果渲染 · R4 批</div>
<div class="empty-text" id="bk-guide-x">四桶搜索与多源换源 R4 批开放；详情可从素材行《书名》深链进入。</div>
<div class="action-row empty-actions"><button class="btn btn-primary" id="bk-guide-subs">订阅管理</button><button class="btn" id="bk-guide-import">导入书源</button><button class="btn" id="bk-guide-demo" hidden>开启体验模式</button></div>
<p class="action-hint">按 Enter 搜索（引擎可搜或体验模式时）。</p>
</div>
<div id="bk-results" hidden>
<div class="bk-res-head"><b id="bk-res-n">0</b> 条结果<span class="muted" id="bk-res-meta"></span></div>
<div id="bk-res-list"></div>
</div>
</div>
<div id="bk-detail" hidden>
<a class="back-link" href="#books">返回书籍</a>
<div class="empty" id="bk-absent" hidden>
<div class="empty-title">bookengine 未就绪</div>
<div class="empty-text">详情与取文暂不可用（R1-R2 建设中）；订阅与书源管理不受影响。</div>
</div>
<div class="skel" id="bk-skel" hidden><div class="skel-line w90"></div><div class="skel-line w70"></div></div>
<div id="bk-body" hidden>
<div class="bk-grid">
<div class="bk-cover" id="bk-cover">书</div>
<div><h2 class="bk-h" id="bk-name">…</h2><p class="bk-meta" id="bk-meta"></p><p class="bk-meta" id="bk-intro"></p></div>
<aside class="bk-side"><b>来源</b><div class="bk-src" id="bk-src-now"></div><div>目录 <b id="bk-toc-n">—</b> 章</div>
<label>换源 <select id="bk-src-sel" class="input"></select></label>
<p class="action-hint">换源后已勾选章节清空。</p></aside>
</div>
<div class="toc-card">
<div class="toc-tools"><b>目录</b> <span>已选 <b id="bk-seln" class="bk-count">0</b> 章</span>
<input id="bk-filter" class="input" placeholder="标题过滤（不影响勾选）">
<span class="chips" id="bk-specs"><button class="chip is-active" data-bkspec="all">全部</button><button class="chip" data-bkspec="l5">最新 5 章</button><button class="chip" data-bkspec="l10">最新 10 章</button><button class="chip" data-bkspec="custom">自定义</button></span>
<span id="bk-range" hidden><input id="bk-from" class="input bk-num" type="number"><input id="bk-to" class="input bk-num" type="number"><button class="btn" id="bk-range-apply">设选</button></span>
</div>
<div id="bk-toc"></div>
</div>
<div class="op-bar" id="bk-opbar" hidden>
<span>已选 <b id="bk-seln2" class="bk-count">0</b> 章 · 合计约 <b id="bk-wc">0</b> 字</span>
<span><label><input type="radio" name="bk-mode" checked>分章（推荐）</label> <label title="R4 后端批"><input type="radio" name="bk-mode" disabled>合并单条</label></span>
<label class="switch" title="提炼卡形式入库（关=原始文本直入）"><input type="checkbox" id="bk-digest"><i></i>AI 提炼</label>
<span class="bk-digest-off" id="bk-digest-off" hidden>原始文本直入（不做 AI 提炼）</span>
<button class="btn btn-primary" id="bk-import-btn">↓ 入素材库</button>
</div>
<div id="bk-prog" hidden></div>
</div>
</div>
<div id="bk-subs" hidden>
<div class="empty">
<div class="empty-title">订阅与书源库</div>
<div class="empty-text">两步导入：解析预览 → 勾选落库（3906 行虚拟滚动 + 多轴过滤）；订阅表 R4 批落地，当前经弹窗与 CLI「musespiral booksource」族管理。</div>
<div class="action-row"><button class="btn btn-primary" id="im-open">导入书源</button><button class="btn" id="bk-subs-back">回搜索</button></div>
</div>
</div>
<div class="modal-overlay" id="im" hidden>
<div class="modal im-modal" role="dialog" aria-modal="true" aria-labelledby="im-title">
<div class="im-head"><h3 class="modal-title" id="im-title">导入书源</h3><span class="muted im-total" id="im-total"></span><button class="btn btn-ghost btn-icon" id="im-close" type="button" aria-label="关闭"><span data-icon="close"></span></button></div>
<p class="im-warn" id="im-warn" hidden></p>
<div class="tabs im-tabs" id="im-tabs">
<button class="tab is-active" data-imtab="paste" type="button">粘贴文本</button>
<button class="tab" data-imtab="url" type="button">从 URL</button>
<button class="tab" data-imtab="file" type="button">选择文件</button>
</div>
<div id="im-src">
<div id="im-paste"><textarea id="im-text" class="input im-area" rows="4" placeholder="粘贴书源 JSON（数组或单对象均可）"></textarea><div class="action-row"><button class="btn btn-primary" id="im-parse" type="button">解析预览</button></div></div>
<div id="im-url" hidden><input id="im-url-in" class="input" placeholder="订阅 URL（http(s) 开头，服务端 64MB 门内拉取）"><div class="action-row"><button class="btn btn-primary" id="im-parse-url" type="button">拉取并解析</button></div></div>
<div id="im-file" hidden><input id="im-file-in" type="file" accept=".json,application/json"><div class="action-row"><button class="btn btn-primary" id="im-parse-file" type="button">读取并解析</button></div></div>
<p class="action-hint" id="im-prog" hidden>拉取并解析中…<span id="im-prog-t"></span>（大包 7–20MB 需 10–25 秒，请勿关闭窗口）</p>
</div>
<div id="im-filters" hidden>
<input id="im-kw" class="input im-kw" placeholder="过滤：名称 / 分组 / URL 关键词">
<div class="chips im-chips" id="im-stat"></div>
<div class="chips im-chips" id="im-groups"></div>
<button class="im-collapse" id="im-collapse" type="button" hidden></button>
<div class="im-selrow" id="im-selrow"><label class="im-selall"><input type="checkbox" id="im-selall"><b id="im-selall-t">全选</b></label><span class="muted" id="im-shown"></span></div>
<div class="im-list" id="im-list" aria-label="书源列表"><div class="im-canvas" id="im-canvas" role="list"></div></div>
</div>
<div class="im-foot">
<span class="im-keeps">
<label class="switch" title="同名时保留本地书源名称"><input type="checkbox" id="im-keep-name"><i></i>保留本地名称</label>
<label class="switch" title="同名时保留本地分组"><input type="checkbox" id="im-keep-group"><i></i>保留本地分组</label>
<label class="switch" title="同名时保留本地启用位"><input type="checkbox" id="im-keep-enable"><i></i>保留本地启用位</label>
</span>
<span class="im-sum" id="im-sum">已选 0 条 · 新增 0 · 更新 0</span>
<span class="im-foot-btns"><button class="btn btn-secondary" id="im-cancel" type="button">取消</button><button class="btn btn-primary" id="im-apply" type="button" disabled>导入</button></span>
</div>
</div>
</div>
`;
