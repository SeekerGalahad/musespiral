"""订阅拉取最简门测试（R0.5）：https 强制 / 30s 超时 / 64MB 流式截断 / 去 UA 后缀。

零真实联网：`urlopen` 经 monkeypatch 替换为假响应对象（read(n) 有状态——
F-X2 流式按 CHUNK 64KB 循环读，假响应需按偏移推进）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from musespiral.infra import booksource_fetch as bf


class _FakeResp:
    """假 urlopen 上下文管理器（read(n) 按偏移推进，模拟真实流）。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.read_lens: list[int] = []  # 每次实际返回的字节数

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            out = self._data[self._offset:]
        else:
            out = self._data[self._offset:self._offset + n]
        self._offset += len(out)
        self.read_lens.append(len(out))
        return out


def _patch_urlopen(monkeypatch, resp) -> list[tuple[str, dict[str, str], float]]:
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, dict(request.header_items()), timeout))
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(bf, "urlopen", fake_urlopen)
    return calls


def test_validate_url_requires_https() -> None:
    """https 强制（明文 http 拒收）。"""
    with pytest.raises(bf.BookSourceFetchError, match="必须为 https"):
        bf.validate_url("http://plain.example.com/list.json")
    assert bf.validate_url("https://ok.example.com/list.json") == "https://ok.example.com/list.json"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://x/y", "https://", "not a url"])
def test_validate_url_rejects_shapes(bad: str) -> None:
    with pytest.raises(bf.BookSourceFetchError):
        bf.validate_url(bad)


def test_fetch_enforces_30s_timeout(monkeypatch) -> None:
    calls = _patch_urlopen(monkeypatch, _FakeResp(b"[]"))
    bf.fetch("https://ok.example.com/list.json")
    assert calls[0][2] == 30.0


def test_fetch_rejects_body_over_64mb(monkeypatch) -> None:
    """响应体 ≤64MB（F-X2：2MB→64MB）：超限流式读满即停报错，不无限读。

    包体超限**两整块**：读完跨限块（1 块余量）即停——第三块不再读，
    证明「读满上限即停」而非读完全部响应体。
    """
    oversized = b"[" + b"0" * (bf.MAX_BODY_BYTES + 2 * bf.CHUNK_BYTES) + b"]"
    resp = _FakeResp(oversized)
    _patch_urlopen(monkeypatch, resp)
    with pytest.raises(bf.BookSourceFetchError, match="64MB 上限"):
        bf.fetch("https://ok.example.com/list.json")
    assert sum(resp.read_lens) == bf.MAX_BODY_BYTES + bf.CHUNK_BYTES < len(oversized)


def test_fetch_accepts_body_at_limit(monkeypatch) -> None:
    """恰好在 64MB 上限内 → 正常返回（边界不误拒）。"""
    data = b"a" * bf.MAX_BODY_BYTES
    _patch_urlopen(monkeypatch, _FakeResp(data))
    assert len(bf.fetch("https://ok.example.com/list.json")) == bf.MAX_BODY_BYTES


def test_fetch_streams_in_chunks(monkeypatch) -> None:
    """F-X2 流式：>CHUNK(64KB) 的响应按块循环 read（非一次性 resp.read）。"""
    data = b"x" * (bf.CHUNK_BYTES * 3 + 5)  # 3 块 + 尾块
    resp = _FakeResp(data)
    _patch_urlopen(monkeypatch, resp)
    assert bf.fetch("https://ok.example.com/list.json") == data.decode()
    assert len(resp.read_lens) == 5, "3 满块 + 1 尾块(5B) + 1 空块(循环终止)"
    assert sum(resp.read_lens) == len(data)
    assert resp.read_lens[:3] == [bf.CHUNK_BYTES] * 3


def test_tempfile_removed_after_fetch(monkeypatch) -> None:
    """F-X2：流式临时文件用完即删（finally；异常路径也删）。"""
    def _temp_paths() -> set[Path]:
        return set(Path(tempfile.gettempdir()).glob("musespiral-booksource-*.json"))

    assert not _temp_paths(), "前置：临时目录无残留"
    _patch_urlopen(monkeypatch, _FakeResp(b'[{"bookSourceUrl":"https://a.com"}]'))
    bf.fetch("https://ok.example.com/list.json")
    assert not _temp_paths(), "fetch 完成后临时文件已删除"

    oversized = b"[" + b"0" * (bf.MAX_BODY_BYTES + 10) + b"]"
    _patch_urlopen(monkeypatch, _FakeResp(oversized))
    with pytest.raises(bf.BookSourceFetchError):
        bf.fetch("https://ok.example.com/list.json")
    assert not _temp_paths(), "超限异常路径同样无残留"


def test_request_without_ua_suffix_strips_and_sends_null_ua(monkeypatch) -> None:
    """"#requestWithoutUA" 后缀：剥掉后缀 + 发 UA: null（legado 同语义）。"""
    calls = _patch_urlopen(monkeypatch, _FakeResp(b"[]"))
    bf.fetch("https://ok.example.com/list.json#requestWithoutUA")
    url, headers, _timeout = calls[0]
    assert url == "https://ok.example.com/list.json"
    assert headers.get("User-agent") == "null"


def test_default_ua_when_no_suffix(monkeypatch) -> None:
    """无后缀 → 发默认 UA（非 "null"）。"""
    calls = _patch_urlopen(monkeypatch, _FakeResp(b"[]"))
    bf.fetch("https://ok.example.com/list.json")
    assert calls[0][1]["User-agent"] != "null"


def test_http_error_and_network_error_are_readable(monkeypatch) -> None:
    from urllib.error import HTTPError, URLError

    _patch_urlopen(monkeypatch, HTTPError("https://x/y", 404, "Not Found", {}, None))
    with pytest.raises(bf.BookSourceFetchError, match="HTTP 404"):
        bf.fetch("https://ok.example.com/list.json")

    _patch_urlopen(monkeypatch, URLError("Name or service not known"))
    with pytest.raises(bf.BookSourceFetchError, match="网络错误"):
        bf.fetch("https://ok.example.com/list.json")


def test_fetch_or_none_never_raises(monkeypatch) -> None:
    from urllib.error import URLError

    _patch_urlopen(monkeypatch, URLError("boom"))
    text, error = bf.fetch_or_none("https://ok.example.com/list.json")
    assert text is None and "网络错误" in error


def test_gbk_fallback_decode(monkeypatch) -> None:
    """非 utf-8 响应退 gb18030（社区源包偶有 GBK）。"""
    _patch_urlopen(monkeypatch, _FakeResp("书源".encode("gb18030")))
    assert bf.fetch("https://ok.example.com/list.json") == "书源"


def test_undecodable_body_readable_error(monkeypatch) -> None:
    _patch_urlopen(monkeypatch, _FakeResp(b"\xff\xfe\x00\x01"))
    with pytest.raises(bf.BookSourceFetchError, match="无法解码"):
        bf.fetch("https://ok.example.com/list.json")
