"""书源订阅拉取（R0.5，L1）——第四条出口线的最简门（设计 §3.4 末段 + §二 F-X2）。

最简门（三条，按序）：

    1. **https 强制**：订阅 URL 用户显式提供的规则包地址，明文拒；
    2. **超时 30s**；
    3. **响应体 ≤64MB（F-X2：2MB→64MB）**：aoaostar 主流包 7-20MB，
       2MB 门等于订阅功能对主流水源不可用——64MB 留余量。

F-X2 流式：`resp.read(CHUNK)` 循环读，**边读边写临时文件**（64KB 一段），
读满上限即停报错（不无限读）；读完再从临时文件 json 解码——20MB 级包
不再一次性驻留内存两份。临时文件用完即删（finally，异常路径也删）。

robots 不适用：订阅 URL 是用户显式提供的规则包地址（设计 §3.4 裁决原文），
与 fetcher 五段门的「自动发现式抓取」语义不同，不入 robots 闸门。

`#requestWithoutUA` 后缀（legado 同款）：请求时剥掉后缀且发 `User-Agent: null`
——某些源站据此区分客户端。

失败一律 raise BookSourceFetchError（可读中文），调用方落 fetch_state=failed。
"""
from __future__ import annotations

import os
import tempfile

from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# 最简门参数（设计 §3.4 + R0.5 F-X2）
TIMEOUT_SECONDS = 30.0
MAX_BODY_BYTES = 64 * 1024 * 1024  # 64MB（F-X2：2MB→64MB）
CHUNK_BYTES = 64 * 1024  # 流式读块大小（F-X2：chunked 循环写临时文件）
# 去 UA 请求的后缀（legado `#requestWithoutUA` 逐字）
NO_UA_SUFFIX = "#requestWithoutUA"
# 去 UA 请求的 UA 字面量（legado `header(AppConst.UA_NAME, "null")` 逐字）
NO_UA_VALUE = "null"
_DEFAULT_UA = "musespiral-booksource/0.4"


class BookSourceFetchError(ValueError):
    """订阅拉取失败（协议/超时/限额/HTTP 状态；可读中文）。"""


def validate_url(url: str) -> str:
    """https 强制门：scheme 必须 https 且有 host。合法返回剥后缀后的 URL。"""
    if not isinstance(url, str) or not url.strip():
        raise BookSourceFetchError("订阅 URL 不能为空")
    raw = url.strip()
    target = strip_no_ua(raw)
    try:
        parsed = urlparse(target)
        parsed.port  # 端口合法性（ValueError 向上）
    except ValueError as exc:
        raise BookSourceFetchError(f"订阅 URL 非法: {exc}") from None
    if parsed.scheme != "https":
        raise BookSourceFetchError(f"订阅 URL 必须为 https（明文 http 拒收）: {raw}")
    if not parsed.hostname:
        raise BookSourceFetchError(f"订阅 URL 缺少主机名: {raw}")
    return raw


def strip_no_ua(url: str) -> str:
    """剥 `#requestWithoutUA` 后缀（返回真实目标 URL）。"""
    if url.endswith(NO_UA_SUFFIX):
        return url[: -len(NO_UA_SUFFIX)]
    return url


def wants_no_ua(url: str) -> bool:
    return url.endswith(NO_UA_SUFFIX)


def _stream_to_tempfile(resp) -> str:
    """chunked 循环读响应体 → 临时文件；超限即停报错。返回临时文件路径。"""
    total = 0
    fd, path = tempfile.mkstemp(prefix="musespiral-booksource-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            while True:
                chunk = resp.read(CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise BookSourceFetchError(
                        f"响应体超过 {MAX_BODY_BYTES // (1024 * 1024)}MB 上限（流式读满即停）"
                    )
                tmp.write(chunk)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def fetch(url: str) -> str:
    """拉取订阅文本（https + 30s + 64MB 流式；失败 BookSourceFetchError）。"""
    raw = validate_url(url)
    target = strip_no_ua(raw)
    headers = {"User-Agent": NO_UA_VALUE if wants_no_ua(raw) else _DEFAULT_UA}
    try:
        with urlopen(Request(target, headers=headers), timeout=TIMEOUT_SECONDS) as resp:
            path = _stream_to_tempfile(resp)
    except HTTPError as exc:
        raise BookSourceFetchError(f"拉取失败（HTTP {exc.code}）: {target}") from None
    except URLError as exc:
        raise BookSourceFetchError(f"拉取失败（网络错误）: {exc.reason}") from None
    except TimeoutError:
        raise BookSourceFetchError(f"拉取失败（超时 {TIMEOUT_SECONDS:.0f}s）: {target}") from None
    try:
        data = open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    for encoding in ("utf-8", "gb18030"):  # 社区源包偶有 GBK；utf-8 优先
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BookSourceFetchError(f"响应体无法解码（utf-8/gb18030 均失败）: {target}")


def fetch_or_none(url: str) -> tuple[str | None, str]:
    """拉取并返回 (文本|None, 错误文本)——状态机用（失败不抛，落 fetch_state）。"""
    try:
        return fetch(url), ""
    except BookSourceFetchError as exc:
        return None, str(exc)
