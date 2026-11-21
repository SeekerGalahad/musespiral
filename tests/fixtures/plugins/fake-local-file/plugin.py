"""假插件·本地文件源（plugins 夹具，端到端可跑）。

插件只 import core（musespiral.core.plugins.context）——这是契约优先于实现
（正典 §2.2）的落点：插件作者不需要知道引擎在哪一层。
"""
from __future__ import annotations

from pathlib import Path

from musespiral.core.plugins.context import PluginError

ALLOWED_SUFFIXES = (".md", ".txt", ".srt", ".vtt")


def run(inputs: dict, config: dict, ctx: object) -> dict:
    path = Path(str(inputs["path"]))
    ctx.log(f"local-file: 接收路径 {path}")
    if not path.is_file():
        raise PluginError(f"文件不存在: {path}")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise PluginError(f"后缀不在白名单 {ALLOWED_SUFFIXES}: {path.suffix!r}")
    ctx.progress(1, 1)
    # 返回 Path → 引擎存 path 引用（原路径不复制：媒体可能 GB 级，正典 §5.2）
    return {"media": path}
