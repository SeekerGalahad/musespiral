document.getElementById('view-settings').innerHTML=`
<div class="amb amb-settings" aria-hidden="true"></div>
<div class="view-head"><div><h1 class="view-title">设置</h1><p class="view-sub" id="settings-sub">身份与存储 + AI 双柱 + 风格底座（写 ~/.musespiral/，均需确认）</p></div><button class="btn" id="cred-verify-btn" title="手动重新探测凭证/AI/图床/磁盘状态">验证状态</button></div>
<div class="set-grid">
<div class="action-group"><h3 class="action-title">身份与存储（KB 源）</h3>
<p class="action-hint" id="kb-origin"></p>
<div class="action-row"><input id="kb-root-input" class="input" style="flex:1;min-width:200px" placeholder="/abs/kb-root"><button class="btn btn-primary" id="kb-save-btn">保存</button></div>
<table class="set-table"><thead><tr><th>key</th><th>标签</th><th>路径</th><th>态</th><th></th></tr></thead><tbody id="kb-sources-body"></tbody></table>
<div class="action-row"><input id="kb-add-key" class="input" placeholder="key"><input id="kb-add-label" class="input" placeholder="标签"><input id="kb-add-rel" class="input" placeholder="根内相对路径"><button class="btn" id="kb-add-btn">加</button></div>
<p class="action-hint">信息源连接与采集已迁「源中枢」（#sources）；此处 KB 子源仅供发现页筛选。</p>
</div>
<div class="action-group"><h3 class="action-title">AI 档案与路由</h3>
<p class="action-hint muted">未配置时先跑 CLI：musespiral ai config --profile &lt;名&gt;</p>
<table class="set-table"><thead><tr><th>档案</th><th>base_url</th><th>模型</th><th>key</th><th></th></tr></thead><tbody id="ai-profiles-body"></tbody></table>
<div class="action-row"><input id="ai-p-name" class="input" placeholder="名"><input id="ai-p-url" class="input" placeholder="base_url"><input id="ai-p-model" class="input" placeholder="模型"><input id="ai-p-key" class="input" placeholder="API Key（本地可空）"><button class="btn" id="ai-p-save">存</button></div>
<div class="action-row"><label class="field">默认 <select id="ai-default-select"></select></label><button class="btn" id="ai-default-save">设默认</button><label class="field">路由 <select id="ai-route-task"></select>→<select id="ai-route-profile"></select></label><button class="btn" id="ai-route-save">设路由</button></div>
<p class="action-hint">key 永不回显：表格只显三态（已配置 / 未配置 / 免凭据·本地）。</p>
<span class="health-dot unknown" id="media-dot"></span><span class="action-hint" id="media-txt">文生图：未探测</span><button class="btn" id="media-verify-btn">验证</button><p class="action-hint">近 7 日用量</p><div id="ai-meter"></div>
</div>
<div class="action-group"><h3 class="action-title">风格与底座</h3>
<p class="action-hint" id="style-habits"></p>
<textarea id="style-area" class="set-area" rows="6"></textarea>
<div class="action-row"><button class="btn btn-primary" id="style-save">保存风格资产</button></div>
<div class="action-row"><label class="field">底座任务 <select id="prompt-task-select"></select></label><span class="action-hint" id="prompt-state"></span></div>
<textarea id="prompt-area" class="set-area" rows="8"></textarea>
<div class="action-row"><button class="btn btn-primary" id="prompt-save">保存覆盖</button></div>
</div>
</div>
`;
