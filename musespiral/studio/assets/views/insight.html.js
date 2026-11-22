document.getElementById('view-insight').innerHTML=`
<div class="amb amb-insight" aria-hidden="true"></div>
<div class="view-head">
<div>
<h1 class="view-title">洞察</h1>
<p class="view-sub">什么在变好：创意漏斗 · 源贡献 · 创作节奏 · AI 用量</p>
</div>
<div class="chips" id="insight-range" aria-label="时间范围">
<button class="chip is-active" data-irange="30">30 天</button>
<button class="chip" data-irange="90">90 天</button>
</div>
</div>
<div class="action-group" id="radar-card"><div class="action-row"><h3 class="action-title">选题雷达</h3><button class="btn btn-ghost" id="radar-refresh" style="margin-left:auto">重新生成</button></div><div id="radar-body"></div></div>
<div class="tiles" id="stat-tiles"></div>
<div class="action-group">
<h3 class="action-title">创意漏斗</h3>
<div class="funnel-band idea" id="idea-funnel" aria-label="创意漏斗"></div>
</div>
<div class="dash-grid">
<div class="dash-col">
<div class="action-group"><h3 class="action-title" id="trend-title">发布趋势（近 30 天）</h3><div class="trend" id="trend-chart" aria-label="近 30 天发布趋势"></div><div class="trend-x"><span id="trend-from">30 天前</span><span>今天</span></div></div>
<div class="action-group" id="stats-trend-card"><h3 class="action-title" id="stats-trend-title">阅读趋势</h3><div class="chips" id="stats-range" aria-label="趋势粒度"><button class="chip is-active" data-srange="week">按周</button><button class="chip" data-srange="month">按月</button></div><div id="stats-trend" class="stats-trend"></div><p class="action-hint" id="stats-trend-note"></p></div>
<div class="action-group"><h3 class="action-title">源贡献</h3><div id="contrib-bars"></div></div>
<div class="action-group"><h3 class="action-title">按平台分布</h3><div id="platform-bars"></div></div>
</div>
<div class="dash-col">
<div class="action-group"><h3 class="action-title">创作节奏</h3><div id="compose-act" class="act-dots"></div><p class="action-hint" id="compose-act-note"></p></div>
<div class="action-group"><h3 class="action-title">评分分布</h3><div id="score-dist" class="score-dist"></div><p class="action-hint" id="score-dist-note"></p></div>
<div class="action-group"><h3 class="action-title">AI 用量</h3><div id="ai-usage" class="ai-usage"></div></div>
</div>
</div>
<details class="action-group" id="health-card">
<summary class="health-sum">环境健康（运维前提，默认收起）</summary>
<div id="health-grid" class="hcards muted">加载中…</div>
</details>
`;
