"""本地 embedding 基础设施（ARCH-5）——Ollama bge-m3 推理封装 + sha256 缓存 + 可用性探测。

正典=docs/项目方案/musespiral-极限架构与功能扩展总规划-2026-11-21.md §ARCH-5。

分工（两层不互相 import）：
  - 本模块 = 外部交互层（Ollama REST /api/embed、sqlite 缓存）；
  - 纯数学（余弦/top-k/归一化）= core/materials/vector.py（零 I/O）；
  - 编排（素材向量索引、三路合并搜索）= services/search_service.py。

端点：POST {host}/api/embed  body {"model","input":[...]} → {"embeddings":[[...]]}，
input 收列表即一次 batch（2026-11-22 实测 bge-m3 dim=1024）；旧版
/api/embeddings（单条 prompt 口径）不使用——batch 是本接口的承诺。

缓存：~/.musespiral/embed_cache.sqlite（MUSESPIRAL_HOME 覆写）——
表 embed_cache(key TEXT PRIMARY KEY, dim INTEGER, vec BLOB)；key =
sha256(model + "\\x00" + text)，模型换型天然隔离不撞旧向量；vec 为
float32 数组字节（array('f')，1024 维 ≈ 4KB/条，相似度用途精度足够）。
坏行宽容：unpack 失败/长度不符按缓存未命中处理，不抛错。

环境覆写：MUSESPIRAL_OLLAMA_HOST / MUSESPIRAL_EMBED_MODEL（默认
http://127.0.0.1:11434 / bge-m3），同 config.home_dir 的 env 惯例。

失败面：一律 EmbeddingError 可读中文——Ollama 未运行/未拉模型给
安装引导；响应形状异常（条数不符）点名条数差。embed_available()
探测从不抛错（任何异常按「未就绪」False）。

测试纪律：单测不真调 Ollama——HTTP 接缝收敛在 _request() 单点，
monkeypatch 它即可全链路模拟（与 infra/ai/client._post_chat_stream
同款）；缓存经 MUSESPIRAL_HOME 指 tmp 目录隔离。
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

OLLAMA_HOST = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3"
INSTALL_HINT = "Ollama 未运行或未就绪。安装: brew install ollama && ollama pull bge-m3"
DEFAULT_TIMEOUT = 120


class EmbeddingError(RuntimeError):
    """embedding 推理失败（连接/HTTP/响应形状）——错误文本自带可读引导。"""


def _host() -> str:
    return os.environ.get("MUSESPIRAL_OLLAMA_HOST", OLLAMA_HOST).rstrip("/")


def _model() -> str:
    return os.environ.get("MUSESPIRAL_EMBED_MODEL", EMBED_MODEL)


# ---- HTTP 接缝（单点，测试 monkeypatch 这里） ---------------------------------


def _request(method: str, path: str, body: bytes | None, timeout: int) -> bytes:
    """Ollama REST 调用唯一接缝——失败统一转 EmbeddingError 可读中文。"""
    req = urllib.request.Request(
        _host() + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise EmbeddingError(f"Ollama 返回 {e.code}: {detail}（{INSTALL_HINT}）") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise EmbeddingError(f"{INSTALL_HINT}（原因: {e}）") from e


# ---- 可用性探测 ----------------------------------------------------------------


def embed_available(timeout: int = 5) -> bool:
    """探测 Ollama + 目标模型是否就位（/api/tags 模型名单比对）——从不抛错。

    模型名按 `bge-m3` 或 `bge-m3:latest` 两种形态都算就位（Ollama 列表
    带 tag 后缀，裸名视同 latest）。
    """
    try:
        raw = _request("GET", "/api/tags", None, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        # 探测是布尔门，任何异常（连接/解析/意外）都按「未就绪」False，
        # 让 services 层走降级路径而不是被探测本身炸掉
        return False
    model = _model()
    names = {str(m.get("name", "")) for m in data.get("models", []) if isinstance(m, dict)}
    return any(n == model or n.split(":", 1)[0] == model for n in names)


# ---- 缓存（float32 BLOB，坏行宽容） --------------------------------------------


def embed_cache_path() -> Path:
    """缓存库路径（随 MUSESPIRAL_HOME 解析——测试 monkeypatch env 即隔离）。"""
    return config.home_dir() / "embed_cache.sqlite"


def _cache_key(model: str, text: str) -> str:
    """sha256(model + NUL + text)——模型换型天然隔离（同文本不同模型不同键）。"""
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def _pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _unpack(blob: bytes, dim: int) -> list[float] | None:
    """float32 字节 → 向量；坏行（解包失败/长度不符）返回 None 按未命中处理。"""
    arr = array.array("f")
    try:
        arr.frombytes(bytes(blob))
    except (ValueError, OverflowError):
        return None
    if len(arr) != dim:
        return None
    return arr.tolist()


def _connect() -> sqlite3.Connection:
    path = embed_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS embed_cache ("
        "key TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL)"
    )
    return con


def _cache_get_many(keys: list[str]) -> list[list[float] | None]:
    out: list[list[float] | None] = [None] * len(keys)
    con = _connect()
    try:
        cur = con.cursor()
        for idx, key in enumerate(keys):
            row = cur.execute("SELECT dim, vec FROM embed_cache WHERE key = ?", (key,)).fetchone()
            if row is None:
                continue
            dim, blob = int(row[0]), row[1]
            vec = _unpack(blob, dim)
            if vec is not None:
                out[idx] = vec
    finally:
        con.close()
    return out


def _cache_put_many(rows: list[tuple[str, int, list[float]]]) -> None:
    if not rows:
        return
    con = _connect()
    try:
        con.executemany(
            "INSERT OR REPLACE INTO embed_cache(key, dim, vec) VALUES (?, ?, ?)",
            [(key, dim, _pack(vec)) for key, dim, vec in rows],
        )
        con.commit()
    finally:
        con.close()


# ---- batch 向量化（同文本缓存；未命中合并为一次 batch 请求） -------------------


def embed(texts: list[str]) -> list[list[float]]:
    """批量向量化，返回顺序与输入一致；texts 为空返回 []。失败抛 EmbeddingError。

    先查缓存，未命中的文本合并成一次 POST /api/embed（一次调 Ollama），
    结果按输入序拼回并落缓存。
    """
    if not texts:
        return []
    model = _model()
    keys = [_cache_key(model, t) for t in texts]
    cached = _cache_get_many(keys)
    misses = [i for i, vec in enumerate(cached) if vec is None]
    fresh: list[tuple[int, list[float]]] = []
    if misses:
        payload = json.dumps({"model": model, "input": [texts[i] for i in misses]}).encode("utf-8")
        data = json.loads(
            _request("POST", "/api/embed", payload, DEFAULT_TIMEOUT).decode("utf-8", "replace")
        )
        got = data.get("embeddings")
        if not isinstance(got, list) or len(got) != len(misses):
            n = len(got) if isinstance(got, list) else 0
            raise EmbeddingError(f"Ollama 响应形状异常: embeddings 条数 {n} != 请求 {len(misses)}")
        for i, raw_vec in zip(misses, got):
            if not isinstance(raw_vec, list):
                raise EmbeddingError(f"Ollama 响应形状异常: embeddings[{i}] 非数组")
            vec = [float(x) for x in raw_vec]
            fresh.append((i, vec))
        dim = len(fresh[0][1])
        if any(len(vec) != dim for _, vec in fresh):
            raise EmbeddingError("Ollama 响应形状异常: 同批向量维度不一致")
        _cache_put_many([(keys[i], dim, vec) for i, vec in fresh])

    results: dict[int, list[float]] = {}
    for i, vec in fresh:
        results[i] = vec
    for i, vec in enumerate(cached):
        if vec is not None:
            results[i] = vec
    return [results[i] for i in range(len(texts))]
