// 全局依赖桥（W3-3c 试迁批，2026-11-24）。
//
// 背景：core.js 是全局脚本，视图代码依赖 esc/toast/postJSON 等全局名。
// ESM 模块有自己的作用域，看不到这些全局——除非显式取用。
//
// 设计（零改动策略）：本模块提供**惰性 getter**（调用时才查 window），
// 这样 core.js 的全局定义时序不影响模块加载，也允许 3e 阶段逐步替换实现。
// 迁移完成后各视图改成直接 import api.js（不再经 deps），本桥即可删。
export const g = {
  get esc() { return window.esc; },
  get toast() { return window.toast; },
  get openDlg() { return window.openDlg; },
  get closeDlg() { return window.closeDlg; },
  get waitForJob() { return window.waitForJob; },
  get trackJob() { return window.trackJob; },
  get matPart() { return window.matPart; },
  get state() { return window.state; },
};
export const esc = (s) => g.esc(s);
export const toast = (...a) => g.toast(...a);
