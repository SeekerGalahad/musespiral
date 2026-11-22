"""infra/embedding.py 单测（ARCH-5）——全部 mock HTTP 接缝，不真调 Ollama。

纪律：测试不依赖外部服务在线。mock 对象是 infra/embedding._request()
这个唯一 HTTP 接缝（其 docstring 声明的接缝点）；缓存隔离靠
MUSESPIRAL_HOME 指 tmp 目录（config.home_dir 调用时读 env）。
缓存精度：float32 BLOB 落盘，断言用 pytest.approx（1e-5）。
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
from typing import Callable

import pytest

import musespiral.infra.embedding as emb

# ---- mock 接缝工具 ------------------------------------------------------------


def _fake_vec(text: str) -> list[float]:
    """确定性假向量：文本散列 → 4 维非零向量（dim 与 bge-m3 无关，本层不钉维度）。"""
    digest = emb.hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 + 0.1 for b in digest[:4]]


def _install_fake(monkeypatch: pytest.MonkeyPatch, handler: Callable) -> list[tuple]:
    """把 _request 换成 handler（method, path, body) -> bytes），返回调用记录列表。"""
    calls: list[tuple[str, str, bytes | None]] = []

    def fake(method: str, path: str, body: bytes | None, timeout: int) -> bytes:
        calls.append((method, path, body))
        return handler(method, path, body)

    monkeypatch.setattr(emb, "_request", fake)
    return calls


def _ok_handlers() -> tuple[Callable, Callable]:
    """默认好端点：/api/tags 列 bge-m3:latest；/api/embed 按假向量回。"""

    def tags(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"models": [{"name": "bge-m3:latest"}]}).encode()

    def embed(method: str, path: str, body: bytes | None) -> bytes:
        payload = json.loads((body or b"{}").decode())
        return json.dumps({"embeddings": [_fake_vec(t) for t in payload["input"]]}).encode()

    def route(method: str, path: str, body: bytes | None) -> bytes:
        if method == "GET" and path == "/api/tags":
            return tags(method, path, body)
        if method == "POST" and path == "/api/embed":
            return embed(method, path, body)
        raise AssertionError(f"fake 收到意外请求: {method} {path}")

    return route, embed


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """缓存隔离：MUSESPIRAL_HOME 指 tmp（config.home_dir 调用时读 env）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MUSESPIRAL_EMBED_MODEL", raising=False)
    monkeypatch.delenv("MUSESPIRAL_OLLAMA_HOST", raising=False)


# ---- embed()：batch / 顺序 / 缓存 ------------------------------------------------


def test_embed_empty_no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, lambda m, p, b: b"{}")
    assert emb.embed([]) == []
    assert calls == []  # 空输入不发起任何请求


def test_embed_batch_one_call_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    route, _ = _ok_handlers()
    calls = _install_fake(monkeypatch, route)
    texts = ["注意力机制", "余弦相似度", "素材检索"]
    out = emb.embed(texts)
    # 一次调 Ollama（batch 承诺），input 顺序与返回顺序一致
    post_calls = [c for c in calls if c[0] == "POST"]
    assert len(post_calls) == 1
    sent = json.loads(post_calls[0][2].decode())["input"]  # type: ignore[union-attr]
    assert sent == texts
    assert out == [_fake_vec(t) for t in texts]


def test_embed_cache_hit_skips_second_http(monkeypatch: pytest.MonkeyPatch) -> None:
    route, _ = _ok_handlers()
    calls = _install_fake(monkeypatch, route)
    first = emb.embed(["注意力机制"])
    posts_after_first = len([c for c in calls if c[0] == "POST"])
    second = emb.embed(["注意力机制"])
    # 第二次零 HTTP，结果与首次等值（float32 落盘精度，逐向量 approx）
    assert len([c for c in calls if c[0] == "POST"]) == posts_after_first
    assert len(second) == len(first) == 1
    assert second[0] == pytest.approx(first[0], abs=1e-5)


def test_embed_partial_cache_only_misses_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    route, _ = _ok_handlers()
    calls = _install_fake(monkeypatch, route)
    emb.embed(["已缓存文本"])
    emb.embed(["已缓存文本", "新文本"])
    last_post = [c for c in calls if c[0] == "POST"][-1]
    sent = json.loads(last_post[2].decode())["input"]
    assert sent == ["新文本"]  # 命中的不重发


def test_embed_model_key_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    # key = sha256(model + NUL + text)：换模型不撞旧向量
    assert emb._cache_key("bge-m3", "同一文本") != emb._cache_key("bge-m3-v2", "同一文本")
    route, _ = _ok_handlers()
    calls = _install_fake(monkeypatch, route)
    monkeypatch.setenv("MUSESPIRAL_EMBED_MODEL", "m2")
    emb.embed(["文本A"])
    monkeypatch.setenv("MUSESPIRAL_EMBED_MODEL", "m1")
    emb.embed(["文本A"])
    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == 2  # m2 的缓存对 m1 不可见——两次都真实发起请求


def test_embed_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_EMBED_MODEL", "custom-model")
    route, embed = _ok_handlers()
    calls = _install_fake(monkeypatch, route)
    emb.embed(["文本"])
    sent = json.loads([c for c in calls if c[0] == "POST"][0][2].decode())
    assert sent["model"] == "custom-model"


# ---- embed()：失败面（可读中文，不哑错） ----------------------------------------


def test_embed_shape_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"embeddings": [[1.0, 2.0]]}).encode()  # 请求 2 条只回 1 条

    _install_fake(monkeypatch, lambda m, p, b: bad(m, p, b) if p == "/api/embed" else b"{}")
    with pytest.raises(emb.EmbeddingError, match="条数"):
        emb.embed(["a", "b"])


def test_embed_row_not_list_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"embeddings": ["不是数组"]}).encode()

    _install_fake(monkeypatch, lambda m, p, b: bad(m, p, b) if p == "/api/embed" else b"{}")
    with pytest.raises(emb.EmbeddingError, match="非数组"):
        emb.embed(["a"])


def test_embed_dim_inconsistent_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"embeddings": [[1.0, 2.0], [1.0, 2.0, 3.0]]}).encode()

    _install_fake(monkeypatch, lambda m, p, b: bad(m, p, b) if p == "/api/embed" else b"{}")
    with pytest.raises(emb.EmbeddingError, match="维度不一致"):
        emb.embed(["a", "b"])


def test_http_error_converted_with_code_and_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # seam 底层：urlopen 抛 HTTPError → EmbeddingError 带状态码 + 安装引导
    def raise_404(request, timeout):  # noqa: ANN001
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "not found",
            email.message.Message(),
            io.BytesIO(b"model not found"),
        )

    monkeypatch.setattr(emb.urllib.request, "urlopen", raise_404)
    with pytest.raises(emb.EmbeddingError, match=r"404.*bge-m3"):
        emb.embed(["文本"])


def test_conn_error_converted_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_conn(request, timeout):  # noqa: ANN001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(emb.urllib.request, "urlopen", raise_conn)
    with pytest.raises(emb.EmbeddingError, match="ollama pull bge-m3"):
        emb.embed(["文本"])


# ---- embed_available()：探测从不抛错 --------------------------------------------


def test_available_true_with_tagged_model(monkeypatch: pytest.MonkeyPatch) -> None:
    route, _ = _ok_handlers()
    _install_fake(monkeypatch, route)
    assert emb.embed_available() is True


def test_available_true_with_bare_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # 裸名（不带 :latest tag）也算就位
    def tags(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"models": [{"name": "bge-m3"}]}).encode()

    _install_fake(monkeypatch, lambda m, p, b: tags(m, p, b))
    assert emb.embed_available() is True


def test_available_false_when_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def tags(method: str, path: str, body: bytes | None) -> bytes:
        return json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()

    _install_fake(monkeypatch, lambda m, p, b: tags(m, p, b))
    assert emb.embed_available() is False


def test_available_false_on_conn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_conn(method: str, path: str, body: bytes | None) -> bytes:
        raise urllib.error.URLError("connection refused")

    _install_fake(monkeypatch, raise_conn)
    assert (
        emb.embed_available() is False
    )  # 探测从不抛错# ---- 缓存底层：float32 打包 + 坏行宽容 ------------------------------------------


def test_pack_roundtrip_float32_precision() -> None:
    vec = [0.123456789, -1.5, 3.25, 1e-4]
    out = emb._unpack(emb._pack(vec), dim=4)
    assert out == pytest.approx(vec, abs=1e-6)  # float32 精度足够相似度用途


def test_unpack_bad_rows_tolerated() -> None:
    # 坏行宽容：长度不符 / 非 float 字节（7 字节不可按 float32 对齐）按未命中 None 处理
    assert emb._unpack(emb._pack([1.0, 2.0]), dim=3) is None
    assert emb._unpack(b"garbage", dim=1) is None  # 7 字节 → array.frombytes 抛 ValueError
    assert emb._unpack(emb._pack([1.0]), dim=1) == pytest.approx([1.0])
