"""契约类型系统（插件系统设计 §三）——L2 纯函数，零 I/O。

v1 契约类型恰好 10 个（正典 §3.1，少而稳定——加类型要改契约成本高，
future 类型一律走 ext/<name> 命名空间由插件自带 schema）：

  file/path, url/http, media/av, segments, ranges,
  images, clips, text/markdown, article, material

兼容判定（正典 §三 校验要求 6）：**完全相等才兼容**。ext/<name> 只与
自身兼容——它不是任何内置类型的子类型，内置类型也不接收它；这条规则
未来可能放宽（如引入 any/层级），所以收拢在 types_compatible 一个函数
里，全系统只此一处判定，禁止散落 `==`。
"""
from __future__ import annotations

# v1 契约类型表（正典 §3.1，恰好 10 个——顺序即设计文档表格顺序）
CONTRACT_TYPES: tuple[str, ...] = (
    "file/path", "url/http", "media/av", "segments", "ranges",
    "images", "clips", "text/markdown", "article", "material",
)

# 外部扩展类型命名空间前缀（正典 §3.1 设计原则：ext/<name> 由插件自带 schema）
EXT_PREFIX = "ext/"


def is_ext_type(port_type: str) -> bool:
    """ext/<name> 扩展类型判定：带 ext/ 前缀且名字非空（裸 "ext/" 不是
    合法类型名）。宽容非 str 输入（恒 False 不抛）——调用方常直接把
    manifest 里读来的值递进来。"""
    if not isinstance(port_type, str):
        return False
    return port_type.startswith(EXT_PREFIX) and len(port_type) > len(EXT_PREFIX)


def types_compatible(out_type: str, in_type: str) -> bool:
    """契约兼容判定（正典 §三 校验要求 6，全系统唯一权威入口）。

    规则：完全相等才兼容。ext/<name> 只与自身兼容（相等规则的推论，
    单列成文是因为它是「扩展类型不被任何内置类型吸收」的红线声明）。
    非 str（脏 manifest）一律不兼容——校验层报错而非这里抛异常。
    """
    if not isinstance(out_type, str) or not isinstance(in_type, str):
        return False
    return out_type == in_type
