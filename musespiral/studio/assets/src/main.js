// ESM 入口（W3-3b 壳接批，2026-11-24）。
//
// 双轨期设计（关键约束）：core.js 承载全部业务且是非模块全局脚本；
// 本入口只做两件事——① 把三模块挂到 window.__ms 供后续批次迁移时按需取用；
// ② 提供就绪信号。**不重写任何业务逻辑**，故零行为变化风险。
//
// 迁移路径（3c 起）：视图模块 `import { getJSON } from './api.js'`，
// 逐步替换对全局 getJSON/postJSON 的依赖；全部迁完后 3e 删 ui.js。
import * as api from './api.js';
import * as router from './router.js';
import * as store from './store.js';

const bridge = { api, router, store, ready: false };

// core.js 是 defer 脚本，浏览器保证 defer 脚本按文档序执行完才触发 DOMContentLoaded；
// 本模块也是 defer（module 默认 defer）且在 core.js 之后，故此处 DOM 已可用。
function boot() {
  bridge.ready = true;
  window.__ms = bridge;
  window.dispatchEvent(new CustomEvent('ms:esm-ready', { detail: bridge }));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}

export default bridge;
