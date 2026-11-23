// 单例状态仓（W3-3a 骨架批，2026-11-24）。
// 现状：core.js 有 6 个巨型全局状态对象（state/BK/IM/SC/PG/…）散落。
// 目标：ESM 化后统一从本模块 export 单例 + 订阅式重绘钩子（notify/subscribe）。
// 迁移纪律：视图迁一个域就把它的全局对象挪进来（如 export const mat = {...}），
// core.js 留 window 兼容别名一个版本期。
export const store = {
  // 迁移期壳：各域对象逐批从 core.js 挪入（当前为空壳 + 订阅机制）
};

const subs = new Set();
export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
export function notify(domain) { for (const fn of subs) { try { fn(domain); } catch (e) { /* 单订阅者异常不炸其他 */ } } }
