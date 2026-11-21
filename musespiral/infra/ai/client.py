"""统一 chat 入口（Phase B 自 musespiral/ai.py 拆分）——网络调用 + 流式解析。

Uses any OpenAI-compatible chat completions endpoint (OpenAI / 通义 / 豆包 /
本地 /v1 兼容端点). 档案与路由取自 profiles.load_ai_config。

统一走 stream:true + SSE 逐块拼接（monk.party 实测仅支持流式）。

Hard rule: this module only produces text for human review. It never
publishes. 失败抛 AIError。
"""
from __future__ import annotations

import json

from .profiles import AIError, load_ai_config, load_profile


# ---- 统一 chat 入口（四消费方收敛点，消灭各自 URL 拼接） ------------------


def _post_chat_stream(url: str, headers: dict, body: bytes, timeout: int):
    """发起流式 POST 并返回可迭代的响应行（SSE 字节行）——测试 mock 的接缝点。"""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise AIError(f"AI 返回 {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise AIError(f"调用 AI 失败: {e}") from e


def _sse_lines(text: str) -> list[bytes]:
    """把完整文本编码为 SSE 流块（测试助手：模拟 LLM 一次输出整段）。"""
    return [
        f'data: {{"choices":[{{"delta":{{"content":"{text}"}}}}]}}\n'.encode(),
        b"data: [DONE]\n",
    ]



def adapt_mock(resp_or_fn, url="", headers=None, body=b"{}", timeout=120):
    """测试适配：接受旧 requests-mock 函数或响应对象，转为 BytesIO。
    chat() 的双格式解析（JSON 分支）直接可用。"""
    import io
    if callable(resp_or_fn):
        try:
            resp = resp_or_fn(url, headers=headers or {}, json=json.loads(body) if body else {}, timeout=timeout)
        except AIError:
            raise
        except Exception as e:
            raise AIError(f"调用 AI 失败: {e}") from e
    else:
        resp = resp_or_fn
    return io.BytesIO(json.dumps(resp.json()).encode())


def _chat_request(
    messages: list[dict[str, str]],
    *,
    task: str | None,
    temperature: float,
    profile: str | None,
    model: str | None = None,
) -> tuple[str, dict[str, str], bytes]:
    """chat/chat_stream 共用请求前置：档案路由 + 头 + URL + 请求体字节。

    抽出为单一拼装点（F25 收敛语义不变——两入口消费同一前置，绝不各拼 URL）。
    `model` 显式给出时覆盖档案 model（N-3 源级 AI 配置的 model 键）——
    base_url/api_key 仍取档案，只换模型名。
    """
    cfg = load_profile(profile) if profile else load_ai_config(task)
    effective_model = model or cfg.get("model")
    if not (cfg.get("base_url") and effective_model):
        raise AIError("AI 未配置（base_url/model 缺失）。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": effective_model, "messages": messages,
        "temperature": temperature, "stream": True,
    }).encode("utf-8")
    return url, headers, body


def chat(
    messages: list[dict[str, str]],
    *,
    task: str | None = None,
    temperature: float = 0.6,
    timeout: int = 120,
    profile: str | None = None,
    model: str | None = None,
) -> str:
    """按任务路由取档案并调用 chat completions（流式），返回拼接后正文。

    统一走 stream:true + SSE 逐块拼接（monk.party 实测仅支持流式）。
    api_key 为空时不带 Authorization 头（本地 Ollama）。失败抛 AIError。
    profile 显式指定档案名时覆盖路由（F45 ai check --profile 用；与 task
    同时给时 profile 优先），档案不存在视同未配置走通用报错。
    model 给出时覆盖档案 model（N-3 源级 AI 配置读侧覆盖用）。
    """
    url, headers, body = _chat_request(
        messages, task=task, temperature=temperature, profile=profile, model=model)
    resp = _post_chat_stream(url, headers, body, timeout)

    # 双格式解析（最大兼容）：先看首行是否 SSE 流式（data: ...），是则逐块
    # 拼接 delta.content；否则当普通 JSON 响应解析 message.content——旧测试
    # mock（返回 .json() 对象）与纯流式端点均直接可用
    raw = resp.read() if hasattr(resp, "read") else b"".join(resp)
    text = raw.decode("utf-8", "replace").strip()

    if any(_l.startswith("data: ") for _l in text.splitlines()):
        # SSE 流式（首行允许前导 SSE 注释行——": keepalive" 等网关心跳，
        # magpie/monk.party 等本地网关在上游慢启动时先发注释行再发数据）
        parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                parts.append(delta.get("content", ""))
            except (json.JSONDecodeError, IndexError, KeyError):
                continue  # 跳过坏块，不中断整体
        result = "".join(parts).strip()
    else:
        # 普通 JSON（非流式 / 旧 mock）
        try:
            data = json.loads(text)
            result = data["choices"][0]["message"]["content"].strip()
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
            raise AIError(f"AI 响应格式异常: {e}") from e

    if not result:
        raise AIError("AI 响应为空（未收到内容）")
    return result


def chat_stream(
    messages: list[dict[str, str]],
    *,
    task: str | None = None,
    temperature: float = 0.6,
    timeout: int = 120,
    profile: str | None = None,
    model: str | None = None,
    on_delta=None,
) -> str:
    """chat() 的增量形态（E-4 流式骨架批，2026-11-19）：SSE delta 到达即回调
    ``on_delta(片段)``，最终仍返回拼接全文。

    与 chat() 同请求体同错误面（共用 _chat_request 与空响应 AIError）；区别
    只在**逐行迭代响应**而非一次 read——真 SSE 端点上每个 delta 到达即刻
    出回调，消费方（compose job 的 partial 进度）得以生成期发布。兼容面：
    非 SSE 响应（旧 mock / 纯 JSON 端点）无增量可回调，走 JSON 兜底只返回
    全文——增量是尽力语义，不是硬承诺。坏块跳过、[DONE] 截断、前导注释行
    容忍均与 chat() 同款。
    """
    url, headers, body = _chat_request(
        messages, task=task, temperature=temperature, profile=profile, model=model)
    resp = _post_chat_stream(url, headers, body, timeout)

    parts: list[str] = []
    raw_lines: list[bytes] = []
    saw_sse = False
    for raw in resp:  # 逐行惰性迭代——真流式的关键（BytesIO mock 同样可迭代）
        raw_lines.append(raw)
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        saw_sse = True
        payload = line[len("data: "):]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
        except (json.JSONDecodeError, IndexError, KeyError):
            continue  # 跳过坏块，不中断整体
        if delta:
            parts.append(delta)
            if on_delta is not None:
                on_delta(delta)

    if saw_sse:
        result = "".join(parts).strip()
    else:
        # 普通 JSON（非流式 / 旧 mock）：无增量可回调
        text = b"".join(raw_lines).decode("utf-8", "replace").strip()
        try:
            data = json.loads(text)
            result = data["choices"][0]["message"]["content"].strip()
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
            raise AIError(f"AI 响应格式异常: {e}") from e

    if not result:
        raise AIError("AI 响应为空（未收到内容）")
    return result
