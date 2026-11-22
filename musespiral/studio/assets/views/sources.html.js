document.getElementById('view-sources').innerHTML=`
<div class="amb amb-sources" aria-hidden="true"></div>
<div class="view-head">
<div>
<h1 class="view-title">源中枢</h1>
<p class="view-sub" id="sources-sub">加载中…</p>
</div>
<div class="action-row">
<button class="btn" id="src-add-btn"><span data-icon="plus"></span>注册源</button>
<button class="btn" id="src-clip-btn" title="URL→预览→人工确认入库（零落盘预览）">剪藏 URL</button>
<button class="btn" id="src-crawl-btn" title="单页→待决区（无自动抓取，确认闸绝对）">手动爬取</button>
<button class="btn" id="src-verify-all-btn" title="逐源手动探测健康（无自动轮询）">验证全部</button>
<button class="btn btn-primary" id="src-ingest-all-btn" title="刷新全部源（ingest 任务，进度见任务中心）"><span data-icon="refresh"></span>刷新全部</button>
</div>
</div>
<div class="eco-tiles" id="eco-tiles"></div>
<div class="pub-toolbar">
<div class="chips" id="src-filters" aria-label="类型筛选">
<button class="chip is-active" data-srctype="">全部类型</button>
<button class="chip" data-srctype="radar_hub">雷达</button>
<button class="chip" data-srctype="kb">知识库</button>
<button class="chip" data-srctype="dir">目录</button>
<button class="chip" data-srctype="rss">RSS</button>
</div>
<span class="action-hint">健康灯只在手动验证或刷新时回写，无自动轮询；进入本页自动拉一次全部源状态。</span>
</div>
<table class="set-table src-table" aria-label="内容源列表"><thead><tr><th>健康</th><th>源</th><th>类型</th><th>订阅</th><th>素材（未读）</th><th>上次正常</th><th>近 7 次失败</th><th>启停</th><th></th></tr></thead><tbody id="src-rows"></tbody></table>
<div class="empty" id="src-empty" hidden>
<div class="empty-icon" data-icon="settings"></div>
<div class="empty-title">还没有内容源</div>
<div class="empty-text">源是采集的起点：注册源（radar_hub / kb / dir / rss 四型）后点「刷新全部」，素材会汇入素材库供综合与创作引用。健康灯只在手动验证或刷新时回写。</div>
<button class="btn btn-primary empty-action" id="src-empty-add">注册第一个源</button>
</div>
<aside class="drawer" id="src-drawer" hidden aria-label="源详情">
<div class="drawer-head"><b id="src-drawer-title">源</b><button class="btn btn-ghost btn-icon" id="src-drawer-close" type="button" aria-label="关闭"><span data-icon="close"></span></button></div>
<div class="drawer-body" id="src-drawer-body"></div>
</aside>
<div class="modal-overlay" id="src-form" hidden>
<div class="modal" role="dialog" aria-modal="true">
<h3 class="modal-title">注册源（POST /api/sources）</h3>
<div style="display:flex;flex-direction:column;gap:8px">
<select id="sf-type" class="input"><option value="radar_hub">radar_hub</option><option value="kb">kb</option><option value="dir">dir</option><option value="rss">rss</option></select>
<input id="sf-id" class="input" placeholder="id（kebab-case）">
<input id="sf-base" class="input" placeholder="base：radar-hub 回环 URL（host=127.0.0.1）">
<textarea id="sf-subs" class="set-area" rows="3" placeholder="订阅（每行一条，仅 radar_hub）"></textarea>
<input id="sf-root" class="input" placeholder="kb 根绝对路径" hidden>
<input id="sf-path" class="input" placeholder="目录绝对路径" hidden>
<input id="sf-url" class="input" placeholder="url：RSS 订阅地址（http/https）" hidden>
</div>
<div class="modal-actions"><button class="btn btn-secondary" id="sf-cancel">取消</button><button class="btn btn-primary" id="sf-ok">注册</button></div>
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
