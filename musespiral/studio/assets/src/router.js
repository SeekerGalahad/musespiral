// 路由模块（W3-3a 骨架批，2026-11-24）。
// 现状：core.js 的 hashchange 分派 + VJS 视图装载链是全局闭包。
// 目标：export navigate()/current()/onChange() 供视图模块消费。
// 迁移纪律：与 store.js 同——先立模块，视图迁移时逐步把消费点换过来。
export function current() {
  const h = location.hash.replace(/^#/, '');
  const [view, qs] = h.split('?');
  return { view: view || 'publish', params: new URLSearchParams(qs || '') };
}
export function navigate(hash) { location.hash = hash; }
export function onChange(fn) {
  window.addEventListener('hashchange', () => fn(current()));
  return () => window.removeEventListener('hashchange', fn);
}
