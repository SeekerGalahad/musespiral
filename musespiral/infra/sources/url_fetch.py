"""公众号文章抓取源（Phase C 迁入 infra/sources/，正典 §八）（R6，重构正典 §四-R6 + §六-V2 验证结论）。

fetch_article(url_or_html, *, is_html=False) -> frontmatter.Post：
  - 在线路径（is_html=False）：requests GET（普通 Chrome UA、15s 超时）。
    V2 实测（2026-11-12）：真实文章匿名 GET 可行，js_content/og:title/正文
    均在原始 HTML 无需 JS；页面内嵌 verify 检测代码，保持一次性低频抓取。
  - SSRF 防线（§十七 F21 复核修复）：在线请求不自动跟随重定向——3xx 逐跳
    手动跟随，每一跳目标重跑 assert_article_url 微信文章域白名单（与
    Studio 提交期同一道门）。requests 默认跟随且无后置校验，初始 URL 过门
    后链上 30x 仍可把请求导向内网/回环——白名单必须逐跳成立。
  - 离线路径（is_html=True）：url_or_html 直接是 HTML 文本——CLI 的 --html
    本地文件由命令层读出后传入；测试与冒烟走此路，不联网。

失效链接边界（V2 实测）：失效文章返回约 32KB 占位页，页面无
id="js_content" —— 响应/输入无 js_content 即抛可读 FetchError（文章失效
或需在微信内打开）。

解析用标准库 html.parser（不引第三方；事件驱动、无递归建树——超深嵌套/
超长文本天然不爆栈，未知节点降级纯文本）：
  - 标题取 <meta property="og:title">（V2 实测在原始 HTML）
  - div#js_content 逐节转 Markdown：section/p→段落、h1-h6→# 标题行、
    strong/b→**加粗**（任务书「strong/h 系→#」的落地：strong 是行内强调
    标记、h 系是标题行）、img 取 data-src 或 src（微信懒加载正文图在
    data-src）、blockquote→> 前缀、br→段内换行
  - script/style/noscript 整体跳过（verify 检测代码不进产物）

产物 frontmatter：title / source_url（离线为空）/ status: draft /
cover: ""（留空，命令层提示人工补充）。
"""
from __future__ import annotations

import re

import frontmatter
import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# 普通 Chrome UA（V2 结论：普通 UA 直连即可，无需伪装特殊客户端）
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TIMEOUT = 15  # 秒（V2/正典 §四-R6：15s 超时）

# 微信文章域白名单（§十七 F21：Studio 是服务端发请求方，任意域 = 内网/本机
# 探测面，白名单把面收窄到微信文章域——本节最重要的一条安全决策）
ARTICLE_HOST = "mp.weixin.qq.com"

# 3xx 逐跳手动跟随（不交给 requests 自动跟随——默认跟随无后置校验，
# 白名单可被 30x 指向内网绕过；requests 语义 = 有 Location 才算重定向）
_REDIRECT_STATI = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5

# 块级标签：起止处冲刷缓冲成段落（h1-h6/blockquote 另有语义，见 _HEADING_TAGS）
_BLOCK_TAGS = frozenset(
    {"p", "section", "div", "blockquote", "ul", "ol", "li", "table",
     "thead", "tbody", "tr", "td", "th", "pre", "figure", "figcaption"}
)
_HEADING_TAGS = frozenset(f"h{i}" for i in range(1, 7))

# 无闭合标签：不进栈（img/br 在 handle_starttag 内即时处理）
_VOID_TAGS = frozenset(
    {"img", "br", "hr", "meta", "link", "input", "source", "wbr",
     "area", "base", "col", "embed", "param", "track"}
)

# 整体跳过其内容（正文之外的脚本/样式，防 verify 检测代码混进产物）
_SKIP_TAGS = frozenset({"script", "style", "noscript"})

_WS_RE = re.compile(r"[ \t\r\f\v]+")


class FetchError(Exception):
    """抓取/解析失败（可读信息：失效链接 / 网络错误 / 缺标题三类来源）。"""


class _WxToMarkdown(HTMLParser):
    """事件驱动转换器：og:title 收集 + div#js_content 正文逐节转 Markdown。

    用标签栈（迭代非递归）跟踪嵌套：js_content 的入栈下标被弹出即正文结束。
    未闭合标签靠「弹到匹配为止」恢复；游离结束标签直接忽略——畸形 HTML
    不崩溃、文本尽量保留（未知节点降级纯文本）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[str] = []
        self.found_js_content = False
        self._stack: list[str] = []
        self._js_at: int | None = None  # js_content div 在栈中的下标
        self._buf: list[str] = []  # 当前段落文本片段（含 ** 标记与换行）
        self._quote = 0  # blockquote 嵌套层数
        self._bold = 0  # strong/b 嵌套层数
        self._skip_tag: str | None = None  # script/style 跳过态

    # ── 事件处理 ────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_tag:
            return
        d = dict(attrs)
        if tag == "meta":
            key = (d.get("property") or d.get("name") or "").strip().lower()
            if key == "og:title" and d.get("content"):
                self.title = d["content"].strip()
            return
        if tag in _SKIP_TAGS:
            self._skip_tag = tag
            return
        if tag == "img":
            if self._js_at is not None:
                self._flush()
                url = d.get("data-src") or d.get("src") or ""
                if url.startswith("data:"):
                    # 懒加载占位图（微信常见 1x1 gif data: URI）不进正文——
                    # 降级为可见占位标记，人工补图（轮三复核 low 修复）
                    self._emit("（图片占位：原文此处有图，占位符已跳过）")
                elif url:
                    self._emit(f"![]({url})")
            return
        if tag == "br":
            if self._js_at is not None:
                self._buf.append("\n")
            return
        if tag in ("strong", "b"):
            if self._js_at is not None:
                self._bold += 1
                self._buf.append("**")
            return
        if self._js_at is not None:
            if tag == "blockquote":
                self._quote += 1
                self._flush()
            elif tag in _BLOCK_TAGS:
                self._flush()
        if tag not in _VOID_TAGS:
            self._stack.append(tag)
            if self._js_at is None and tag == "div" and d.get("id") == "js_content":
                self._js_at = len(self._stack)
                self.found_js_content = True

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_tag = None
            return
        if tag in ("strong", "b"):
            if self._bold > 0:
                self._bold -= 1
                if self._js_at is not None:
                    self._buf.append("**")
            return
        if tag in _VOID_TAGS or tag not in self._stack:
            return  # 游离结束标签忽略
        while self._stack:  # 弹到匹配为止（恢复未闭合的中间标签）
            popped = self._stack.pop()
            self._on_block_close(popped)
            if popped == tag:
                break
        if self._js_at is not None and len(self._stack) < self._js_at:
            self._js_at = None  # js_content 结束：正文收尾
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_tag or self._js_at is None:
            return
        self._buf.append(data)

    # ── 产块 ────────────────────────────────────────────────
    def _on_block_close(self, tag: str) -> None:
        if tag in _HEADING_TAGS:
            self._flush(kind="heading", level=int(tag[1]))
        elif tag == "blockquote":
            self._quote = max(0, self._quote - 1)
            self._flush()
        elif tag in _BLOCK_TAGS:
            self._flush()

    def _flush(self, kind: str = "para", level: int = 0) -> None:
        """缓冲 → Markdown 块；空白冲刷丢弃，引用内段落加 > 前缀。"""
        raw = "".join(self._buf)
        self._buf = []
        lines = [ln for ln in (_WS_RE.sub(" ", s).strip() for s in raw.split("\n")) if ln]
        if not lines:
            return
        if kind == "heading":
            self._emit("#" * min(level, 6) + " " + " ".join(lines))
            return
        text = "\n".join(lines)
        if self._quote:
            text = "\n".join("> " + ln if ln else ">" for ln in text.split("\n"))
        self._emit(text)

    def _emit(self, block: str) -> None:
        if self._quote and not block.startswith(">"):
            block = "> " + block
        self.blocks.append(block)

    def finish(self) -> None:
        """feed/close 后收尾：冲刷残留在缓冲的正文（未闭合标签场景）。"""
        self._flush()


def assert_article_url(url: str) -> str:
    """微信文章 URL 白名单门：http(s) / 无 userinfo / hostname 精确等于
    mp.weixin.qq.com（hostname 取 urlparse 的小写规范化值，大小写与
    userinfo 混淆、后缀伪装域均无效）。

    Studio 提交期（actions._assert_fetch_url 翻译为 400）与在线抓取的
    重定向逐跳共用本门——同一道门两处执法，杜绝「提交期校验、网络层
    跟随重定向绕过」。通过返回原 url；不满足抛 ValueError（可读信息）。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        raise ValueError(f"URL 非法: {url}") from None
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http(s) URL: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"URL 不允许携带用户凭证（userinfo）: {url}")
    if parsed.hostname != ARTICLE_HOST:
        raise ValueError(f"仅支持抓取微信公众号文章域（{ARTICLE_HOST}）的 URL: {url}")
    return url


def _get_article_html(url: str) -> str:
    """在线 GET 文章页 HTML：3xx 不自动跟随，逐跳手动跟随且每一跳目标
    重跑 assert_article_url 白名单（网络错误/非 200 统一转 FetchError）。

    Location 经 urljoin 解析（相对路径落回同域，合法 http→https 跳转可用）；
    重定向目标不过门即拒——请求根本不发出，内网/回环既不可达也不可探测。
    """
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        try:
            resp = requests.get(
                current,
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise FetchError(f"抓取失败（网络错误）: {e}") from e
        if resp.status_code in _REDIRECT_STATI and "Location" in resp.headers:
            try:
                current = assert_article_url(urljoin(current, resp.headers["Location"]))
            except ValueError as e:
                raise FetchError(f"重定向目标被拒绝: {e}") from None
            continue
        if resp.status_code != 200:
            raise FetchError(f"抓取失败（HTTP {resp.status_code}）: {current}")
        return resp.text
    raise FetchError(f"重定向次数超过 {_MAX_REDIRECTS} 次: {url}")


def fetch_article(url_or_html: str, *, is_html: bool = False) -> frontmatter.Post:
    """抓取公众号文章并转为 frontmatter.Post 草稿。

    is_html=False：url_or_html 是文章 URL（在线抓取；重定向逐跳白名单 +
    网络错误/非 200 统一转 FetchError，见 _get_article_html）。is_html=True：
    url_or_html 是 HTML 文本本身（离线，不联网）。页面无 div#js_content →
    FetchError（文章失效或需在微信内打开，V2 边界）。
    """
    if is_html:
        html_text, source_url = url_or_html, ""
    else:
        source_url = url_or_html
        html_text = _get_article_html(url_or_html)

    parser = _WxToMarkdown()
    parser.feed(html_text)
    parser.close()
    parser.finish()
    if not parser.found_js_content:
        raise FetchError(f"文章失效或需在微信内打开（页面无正文容器 js_content）: {url_or_html}")
    title = parser.title
    if not title:
        raise FetchError("文章页缺少 og:title 标题，无法生成草稿")
    body = "\n\n".join(parser.blocks).strip()
    if not body:
        raise FetchError("文章正文为空（js_content 内无可读内容）")
    return frontmatter.Post(body, title=title, source_url=source_url, status="draft", cover="")
