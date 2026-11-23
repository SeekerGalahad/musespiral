// 统一请求层（W3-3a 骨架批，2026-11-24）。
// 目的：把 core.js 里散落的 fetch 封装（request/getJSON/postJSON + waitForJob）
// 收成单一模块——ESM 化后各视图模块统一从这里 import，不再依赖全局函数。
// 语义与 core.js 逐字对齐（迁移期两者并存，视图迁完一个消费点少一个全局引用）。
export async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  let data = {};
  try { data = await res.json(); } catch (err) { data = {}; }
  if (!res.ok) throw new Error(data.error || data.message || 'HTTP ' + res.status);
  if (data && data.ok === false) throw new Error(data.error || data.message || '操作失败');
  return data;
}
export const getJSON = (path) => request('GET', path, undefined);
export const postJSON = (path, body) => request('POST', path, body);
export async function waitForJob(id) {
  for (let i = 0; i < 900; i++) {
    const j = await getJSON(`/api/jobs/${id}`);
    if (j.status === 'done' || j.status === 'failed') return j;
    await new Promise((r) => setTimeout(r, 800));
  }
  throw new Error('等待任务超时');
}
