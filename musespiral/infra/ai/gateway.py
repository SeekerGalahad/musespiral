"""AI 治理网关（Phase D F79，L1）——chat() 之上的**唯一组合层**。

五段序（设计 §二 F79.1 表格，顺序即语义）：

    ① 查缓存  → ② 预算（命中则跳过） → ③ chat() 原函数 → ④ 记账 → ⑤ 写缓存

**为什么这是一层而不是零散装饰器**：缓存/预算/台账三者共享同一个 key 与
同一份「这次调用花多少」的事实；拆开写会让三者口径各自漂移（如台账按
prompt 计、缓存按模板计）。合成一处，五元组只算一次，读写的顺序也被
钉死（cache 在 budget **之前**——缓存的意义即免计费复跑）。

**分层纪律（架构门 F75-8）**：本模块只 import 同层
（`client` / `profiles` / `prompts` / `cache` / `usage` / `budget`）与
`musespiral.config`；**不 import services/studio/core、不知道任何业务概念**。
`client.chat` 的签名与行为零改动（本期硬约束）——它仍是唯一 LLM HTTP 出口，
gateway 只是在它外面套一层判定与记账。

**与 chat() 的调用关系**：`chat_policed` 内部调 `client.chat`（缺省），
测试可经 `chat_fn` 参数注入假件（不 monkeypatch 全局，避免污染同进程其他
用例）。生产消费方（agent 步 / tag_suggest / 选题雷达增强）走本模块；
既有六个直调 chat() 的消费方**保持现状不算违规**（零迁移承诺，设计 §三
依赖规则 3）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from . import budget as budget_mod
from . import cache as cache_mod
from . import usage as usage_mod
from .client import chat as _real_chat
from .profiles import AIError, resolve_profile_name
from .prompts import load_override

ChatFn = Callable[..., str]

# AI-3 复核 fix-5（2026-11-21）：重生成要「AI 再来一版」——prompt 不变时
# key-segment 的 chat_policed(cache=True) 会命中旧缓存（零 LLM 调用、稿子逐字
# 相同），造成假重生成。ContextVar 让 services 层（不写插件、不改 cache 参数）
# 在 regenerate_node 作用域内一次性旁路缓存读取；仍照常记账/写缓存（新稿入
# 缓存覆盖旧条目）。ContextVar 而非全局：单人 Studio 多线程，作用域隔离不串味。
_bypass_cache: ContextVar[bool] = ContextVar("musespiral_ai_bypass_cache", default=False)


@contextmanager
def bypass_cache() -> Iterator[None]:
    """作用域内强制 chat_policed 不读缓存（重生成专用；退出即恢复）。"""
    token = _bypass_cache.set(True)
    try:
        yield
    finally:
        _bypass_cache.reset(token)


class BudgetExceeded(AIError):
    """日预算触顶（可读引导错误，非崩溃；AIError 族既有脱敏面复用）。

    `budget_exceeded = True` 是**跨层协议位**：`core/agent/interp` 不得
    import infra，故它靠这个属性（而非 isinstance）识别限额态——限额态
    不因重试改变，必须立即终态（设计 §二 F75.2 ⑥）。
    """

    budget_exceeded = True


def _chars_in(messages: list[dict[str, str]]) -> int:
    """输入字符数：各条 content 长度之和（无 tokenizer，字符口径三处一致）。"""
    total = 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            total += len(content)
    return total


def chat_policed(
    messages: list[dict[str, str]],
    *,
    task: str,
    temperature: float = 0.6,
    timeout: int = 120,
    profile: str | None = None,
    model: str | None = None,
    cache: bool = False,
    run_id: str | None = None,
    template: str | None = None,
    chat_fn: ChatFn | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """经治理层的一次 chat 调用 → `{text, cache_hit, chars_in, chars_out}`。

    参数：
      messages   `[{role, content}]`（插入序即语义；prompt_sha 保序计算）
      task       TASKS 白名单键（决定路由档案、per_task 预算、模板覆盖）
      temperature 温度（`repr(float(...))` 归一并参与 key）
      profile    显式档案覆盖（不给则按 task 路由解析）
      model      **显式模型覆盖**（N-3 源级 AI 配置读侧覆盖用；None=档案
                 model）。给定时折入缓存 key 的 profile 分量（`<档案>@<model>`）
                 ——同档案换模型必须 miss，否则旧模型产出被误命中。
      cache      **调用点显式 opt-in**（设计 §八-3：compose/rewrite/annotate
                 类生成任务恒 false；tag_suggest/雷达增强恒 true；agent 步按
                 plan 声明）
      run_id     编排步关联 id（只进 usage 行，便于按 run 对账）
      template   **生效模板文本**（F79.1 口径：template_sha=sha1(生效模板)）。
                 由知道内置缺省的消费方传入（agent_service 的
                 `step_templates(plan)`、tag_service 的
                 `load_override(task) or DEFAULT_PROMPT` 等）——gateway 是 L1
                 组合层不持有任何任务的内置模板（D2：不知道业务概念）。
                 缺省 None → 回退 `load_override(task)`（仅覆盖文件参与
                 hash 的历史口径）。**传入即接管**：内置缺省一改 → 调用方
                 传入的 template 变 → key 变 → 旧缓存自然失效（F79-1
                 「template_sha 变更全失效」对内置缺省同样成立）。
      chat_fn    测试注入点（缺省 `client.chat`——真实网络）
      now        时间戳注入点（缺省现取 iso 秒；测试传定值以便断言）

    命中缓存时**不查预算、不调 chat、不写缓存**，只记一行 `cache_hit:true`
    计量（不出账）；未命中则对账 → 调用 → 出账 → 写缓存。
    """
    profile_name = profile or resolve_profile_name(task)
    # 缓存 key 的档案分量：有 model 覆盖时折入 `<档案>@<model>`（同档案换模型
    # 必须 miss）；无覆盖时保持原口径（既有缓存条目全套 key 不变）
    key_profile = f"{profile_name or ''}@{model}" if model else profile_name
    # 生效模板：调用方显式传入 > 覆盖文件（gateway 无内置缺省知识，见参数注）
    template_value = template if template is not None else load_override(task)
    template_digest = cache_mod.template_sha(template_value)
    prompt_digest = cache_mod.prompt_sha(messages)
    key = cache_mod.cache_key(
        task=task,
        profile=key_profile,
        temperature=temperature,
        template_sha_value=template_digest,
        prompt_sha_value=prompt_digest,
    )
    stamp = now if now is not None else _now_iso()
    chars_in = _chars_in(messages)

    # ① 查缓存（cache 在 budget 之前：命中即免计费复跑）
    # 重生成作用域（bypass_cache）强制 miss——「再来一版」必须真调 LLM。
    if cache and not _bypass_cache.get():
        hit_text = cache_mod.read_text(key)
        if hit_text is not None:
            usage_mod.append(
                ts=stamp,
                task=task,
                profile=profile_name,
                cache_hit=True,
                chars_in=chars_in,
                chars_out=len(hit_text),
                run_id=run_id,
            )
            return {
                "text": hit_text,
                "cache_hit": True,
                "chars_in": chars_in,
                "chars_out": len(hit_text),
            }

    # ② 预算对账（仅未命中路径；命中已在上面返回）
    day = stamp[:10]
    today = usage_mod.today_summary(day)
    per_task = usage_mod.task_summary(task, day)
    ok, reason = budget_mod.check(task, today, task_usage=per_task)
    if not ok:
        raise BudgetExceeded(reason or f"任务 {task} 预算触顶")

    # ③ 调用 chat()（原函数原样；签名与行为零改动）
    caller = chat_fn if chat_fn is not None else _real_chat
    call_kwargs: dict[str, Any] = {
        "task": task,
        "temperature": temperature,
        "timeout": timeout,
        "profile": profile,
    }
    if model:
        call_kwargs["model"] = model
    text = caller(messages, **call_kwargs)
    if not isinstance(text, str):
        raise AIError("AI 响应格式异常: gateway 收到非字符串返回")

    # ④ 记账（未命中行 = 出账行）
    usage_mod.append(
        ts=stamp,
        task=task,
        profile=profile_name,
        cache_hit=False,
        chars_in=chars_in,
        chars_out=len(text),
        run_id=run_id,
    )

    # ⑤ 写缓存（仅该调用点 opt-in 时；条目上限 LRU 由 cache 内部收口）
    if cache:
        cache_mod.put(
            key=key,
            task=task,
            profile=profile_name,
            temperature=temperature,
            template_sha_value=template_digest,
            text=text,
            created_at=stamp,
        )

    return {
        "text": text,
        "cache_hit": False,
        "chars_in": chars_in,
        "chars_out": len(text),
    }


def _now_iso() -> str:
    """ISO 秒级时间戳（与 jobs.store.now_iso 同口径；本模块不 import 上层）。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
