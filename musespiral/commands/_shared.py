"""跨命令组共享小助手：_build_renderer / _PLATFORM_NAMES / run_batch。

R1 拆分裁决（任务书二选一）：取「提为 commands/_shared.py」而非「归入使用最多的
模块」——_build_renderer 被 core_cmds.check 与 publish_cmds.publish 各调用一次
（1:1 平局，「使用最多」规则无法裁决），_PLATFORM_NAMES 跨 platform_cmds（2 处）
与 publish_cmds.stats（1 处）；集中于此使命令组模块之间不产生横向 import，
八个命令文件只向 ../ 领域层取依赖。run_batch 为 F34 五命令共用的批量执行器。
"""
from __future__ import annotations

import time

import click

from .. import config
from ..core.content.parser import ParsedArticle
from ..core.content.renderer import Renderer


def _build_renderer(
    parsed: ParsedArticle,
    theme: str | None = None,
    code_theme: str | None = None,
    dark: bool = False,
) -> Renderer:
    """按 frontmatter > 默认值（可被 CLI 参数覆盖）构建渲染器。"""
    t = theme or parsed.metadata.get("theme") or config.DEFAULT_THEME
    ct = code_theme or parsed.metadata.get("code_theme") or config.DEFAULT_CODE_THEME
    use_dark = dark or bool(parsed.metadata.get("dark", False))
    return Renderer(theme_name=t, code_theme=ct, dark=use_dark)


# 平台代码 → 展示名（能力矩阵表与 stats 分组共用；仅展示用途）
_PLATFORM_NAMES = {
    "mp": "微信公众号",
    "xhs": "小红书",
    "channels": "视频号",
    "douyin": "抖音",
    "zhihu": "知乎",
    "juejin": "掘金",
    "bilibili": "B站专栏",
}


def run_batch(command: str, articles: tuple[str, ...], run_one) -> int:
    """F34 批量执行器：返回进程退出码（调用方 sys.exit(run_batch(...))）。

    - 单文件：直接执行（输出与旧版单值 ARTICLE 逐字节一致，零批量痕迹）；
    - 多文件：逐篇执行不短路（一篇 FAIL 继续下一篇，各篇输出随跑随出），
      收尾追加汇总表（逐篇 命令/结果/耗时）；任一篇非 0 → 聚合退出码 1。
    """
    if len(articles) == 1:
        return run_one(articles[0])
    rows: list[tuple[str, int, float]] = []
    aggregate = 0
    for art in articles:
        t0 = time.perf_counter()
        rc = run_one(art)
        rows.append((art, rc, time.perf_counter() - t0))
        if rc != 0:
            aggregate = 1
    click.echo(f"\n{'=' * 50}\n批量汇总（{command} × {len(rows)} 篇）\n{'=' * 50}")
    for art, rc, dt in rows:
        status = "PASS" if rc == 0 else "FAIL"
        click.echo(f"  {command}  {status}  {art}  {dt:.2f}s")
    return aggregate
