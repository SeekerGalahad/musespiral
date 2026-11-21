"""F45 供应商无关连通检查——逐任务最小真实请求 + 错误分型（ai check 数据面）。

`musespiral ai check` 的实现层：对当前路由（或 --profile 覆盖）逐任务发一条
「回复：OK」级最小真实请求——走 chat() 既有流式/超时/降级语义（本模块
不自带网络代码，_post_chat_stream 仍是唯一网络接缝）；不发内容、零 token
消耗级。

纪律（设计 §三）：报告仅 stdout 不落盘——本函数零打印零写盘，渲染归
ai_cmds 命令层；ai.json 只读不写（--profile 覆盖是调用参数，不改路由）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .client import chat
from .profiles import TASKS, AIError, resolve_profile_name

# 最小请求内容（「回复：OK」级：只验证连通/鉴权/端点，不携带任何正文）
CHECK_PROMPT = "回复：OK"
# 诊断用短超时（默认 120s 是生成场景预算；逐任务体检 30s 足以暴露不通，
# 仍走 chat() 既有超时机制——本值只是调用参数）
CHECK_TIMEOUT = 30


@dataclass
class TaskCheckResult:
    """一任务一行：任务 × 生效档案 × 通/不通（错误分型）。"""

    task: str
    profile: str        # 生效档案名（--profile 覆盖值或 routing/default 解析值）
    ok: bool
    kind: str           # "通" | 错误分型（classify_error 口径）
    detail: str         # 通=回复首段；不通=错误消息首段（截断）
    elapsed: float      # 秒（monotonic 差）


def classify_error(msg: str) -> str:
    """AIError 消息 → 错误分型（ai check 汇总表的归类口径，纯函数）。

    分型对齐 client.py 既有错误消息格式：
    - "AI 未配置…"                     → 未配置
    - "AI 返回 401/403…"               → 认证失败
    - "AI 返回 404…"                   → 端点/模型不存在
    - "AI 返回 429…"                   → 限流
    - 其余 "AI 返回 NNN…"              → HTTP NNN
    - "调用 AI 失败…"（URLError/超时） → 网络/超时
    - "AI 响应格式异常/为空…"           → 响应异常
    - 其余                             → 未知错误
    """
    if "AI 未配置" in msg:
        return "未配置"
    if "AI 返回 " in msg:
        code = msg.split("AI 返回 ", 1)[1].split(":", 1)[0].strip()
        if code in ("401", "403"):
            return f"认证失败({code})"
        if code == "404":
            return "端点/模型不存在(404)"
        if code == "429":
            return "限流(429)"
        return f"HTTP {code}" if code else "HTTP 错误"
    if msg.startswith("调用 AI 失败"):
        return "网络/超时"
    if msg.startswith("AI 响应格式异常") or msg.startswith("AI 响应为空"):
        return "响应异常"
    return "未知错误"


def _oneline(text: str, limit: int) -> str:
    """压成单行并截断（表格单元格用；错误消息可能多行/超长）。"""
    s = " ".join(text.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def run_check(
    tasks: list[str] | None = None,
    *,
    profile: str | None = None,
    timeout: int = CHECK_TIMEOUT,
) -> list[TaskCheckResult]:
    """逐任务发最小真实请求并汇总结果（零打印零落盘，渲染归命令层）。

    tasks 缺省=全部任务白名单（profiles.TASKS）；profile 给定时覆盖路由
    （全部任务统一走该档案，解析展示名也用该值）。
    """
    task_list = list(tasks) if tasks else list(TASKS)
    results: list[TaskCheckResult] = []
    for t in task_list:
        prof_name = profile or resolve_profile_name(t) or "（无档案）"
        t0 = time.monotonic()
        try:
            reply = chat(
                [{"role": "user", "content": CHECK_PROMPT}],
                task=t,
                temperature=0.0,
                timeout=timeout,
                profile=profile,
            )
            results.append(TaskCheckResult(
                task=t, profile=prof_name, ok=True, kind="通",
                detail=_oneline(reply, 16), elapsed=time.monotonic() - t0,
            ))
        except AIError as e:
            results.append(TaskCheckResult(
                task=t, profile=prof_name, ok=False,
                kind=classify_error(str(e)),
                detail=_oneline(str(e), 80), elapsed=time.monotonic() - t0,
            ))
    return results
