"""微信文章 URL 抓取草稿用例编排：URL → 在线抓取 → root/<slug>.md。

从 studio/actions._action_fetch 的 job 执行体提取（Phase A 下沉）。SSRF
域名白名单门本体在 core.sources.url_fetch.assert_article_url（与在线抓取
的重定向逐跳共用同一道门），本编排入口先过同一道门再抓取——任何调用方
都无法绕过。

与原实现的差异：JobFailure/脱敏/{ok:...} 包装不再出现——FetchError/
ValueError/OSError 原样上抛，路径返回绝对 Path。slug 净化（仅
[\\w\\u4e00-\\u9fff-]、截断 40 字符）与 O_EXCL 独占创建 + 冲突递增后缀
的纪律原样保留；标题全符号的兜底名服务层无 job_id，用 "fetch"（冲突由
后缀循环消解，产物名恒为 root 内单段文件名）。
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from ..infra.sources import url_fetch

# slug 净化：仅 [\w\u4e00-\u9fff-]——路径分隔符与点都被剔除，产物名恒为
# root 内单段文件名，杜绝文件名穿越
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff-]")
_SLUG_MAX = 40


def _fetch_slug(title: str) -> str:
    """标题 → 产物文件名 slug：仅 [\\w\\u4e00-\\u9fff-]，截断 40 字符。

    路径分隔符/点/空白全部被剔除——产物名恒为单段文件名（杜绝穿越）；
    标题全为符号时净化结果为空，由调用方兜底 "fetch"。
    """
    return _SLUG_RE.sub("", title)[:_SLUG_MAX]


def fetch_to_draft(url: str, root: str | Path) -> Path:
    """抓取微信文章 URL 并落草稿到 root/<slug>.md，返回草稿路径。

    白名单外 URL（非微信文章域/userinfo/伪装后缀等）抛 ValueError；
    在线抓取失败（网络/失效链接/缺标题/正文空）抛 url_fetch.FetchError；
    写盘 OSError 原样上抛。frontmatter status: draft 由 fetch_article
    落位（入队可见）。
    """
    url_fetch.assert_article_url(url)
    post = url_fetch.fetch_article(url)
    slug = _fetch_slug(str(post.metadata.get("title") or "")) or "fetch"
    # O_EXCL 原子创建（open 'x'）：并发调用下独占创建失败即递增后缀重试，
    # 不互相覆盖同名草稿
    n = 1
    while True:
        dest = Path(root) / (f"{slug}.md" if n == 1 else f"{slug}-{n}.md")
        try:
            with open(dest, "x", encoding="utf-8") as fh:
                fh.write(frontmatter.dumps(post, allow_unicode=True, sort_keys=False))
            return dest
        except FileExistsError:
            n += 1
            continue
