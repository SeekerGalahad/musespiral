"""创作台编排服务——兼容门面（R-1 拆分后薄转发层）。

实现已于 2026-11-19（R-1）按六簇真实职责边界拆入 ``services/compose/`` 包：
store / prompt / summary / generate / outline / characters（正典文档与全量
符号面见 ``musespiral.services.compose`` 的 ``__init__``）。本模块保留原
import 路径与**拆分前完全一致的符号集**（``from .compose import *`` +
``__all__`` 全集，含下划线私有名）——CLI（compose_cmds）/ Studio
（compose_actions/actions）/ 全部测试的调用面零改动。

唯一的运行期差异补丁：``_chat`` / ``_chat_stream`` 两个 AI 入口在拆分前
是本模块的全局量，对它们做属性替换（测试 monkeypatch、运行期换入口）即
全局生效；拆分后消费方在 generate/summary/outline 三个子模块，故本模块
以自定义模块类的 ``__setattr__`` 把这两个名字的替换**同步转发**到全部
消费子模块——替换语义与拆分前逐点一致。
"""
from __future__ import annotations

from .compose import *  # noqa: F401,F403 —— 门面契约：符号集=拆分前全集（__all__ 锁定）

# 拆分前模块级带注解赋值（如 _META_LOCKS: dict[...]）会在模块字典产生
# __annotations__ 键；薄门面无注解赋值，显式对齐 dir() 契约。
__annotations__ = {}

import sys as _sys
import types as _types


class _ComposeServiceFacade(_types.ModuleType):
    """兼容门面模块类：AI 入口名的替换转发（拆分前 setattr 即全局生效）。"""

    _SEAM_NAMES = ("_chat", "_chat_stream")
    _SEAM_CONSUMERS = (
        "musespiral.services.compose.generate",
        "musespiral.services.compose.summary",
        "musespiral.services.compose.outline",
    )

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in self._SEAM_NAMES:
            import sys

            for mod_name in self._SEAM_CONSUMERS:
                mod = sys.modules.get(mod_name)
                if mod is not None and hasattr(mod, name):
                    setattr(mod, name, value)


_sys.modules[__name__].__class__ = _ComposeServiceFacade
del _sys, _types, _ComposeServiceFacade
