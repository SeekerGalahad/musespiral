"""素材库服务——兼容门面（ARCH-2 拆分后薄转发层）。

实现已于 2026-11-22（ARCH-2，正典=《musespiral-极限架构与功能扩展总规划
-2026-11-21》§ARCH-2）按七簇真实职责边界拆入 ``services/materials/`` 包：
crud / triage / search / upload / spark / feedback / iterations（全量符号面
见 ``musespiral.services.materials`` 的 ``__init__``）。本模块保留原 import
路径与**拆分前完全一致的符号集**（``from .materials import *`` +
``__all__`` 全集 85 名，含下划线私有名）——CLI（tags_cmds）/ Studio
（handlers/payload/settings/compose_actions）/ 全部测试的调用面零改动。

唯一的运行期差异补丁：``_chat_policed``（distill 的 AI 注入缝）在拆分前
是本模块的全局量，对它做属性替换（测试 monkeypatch、运行期换入口）即
全局生效；拆分后消费方在 feedback 子模块（``_distill_article``），故本
模块以自定义模块类的 ``__setattr__`` 把替换**同步转发**到该消费子模块
——替换语义与拆分前逐点一致（compose_service 对 _chat/_chat_stream 同款）。
"""
from __future__ import annotations

from .materials import *  # noqa: F401,F403 —— 门面契约：符号集=拆分前全集（__all__ 锁定）

# 拆分前模块级带注解赋值（_VIDEO_DOMAINS / _doc_extract_mod）会在模块字典
# 产生 __annotations__ 键；薄门面无注解赋值，显式对齐 dir() 契约。
__annotations__ = {}

import sys as _sys
import types as _types


class _MaterialsServiceFacade(_types.ModuleType):
    """兼容门面模块类：AI 注入缝名的替换转发（拆分前 setattr 即全局生效）。"""

    _SEAM_NAMES = ("_chat_policed",)
    _SEAM_CONSUMERS = (
        "musespiral.services.materials.feedback",
    )

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in self._SEAM_NAMES:
            import sys

            for mod_name in self._SEAM_CONSUMERS:
                mod = sys.modules.get(mod_name)
                if mod is not None and hasattr(mod, name):
                    setattr(mod, name, value)


_sys.modules[__name__].__class__ = _MaterialsServiceFacade
del _sys, _types, _MaterialsServiceFacade
