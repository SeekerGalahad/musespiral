"""多步编排服务——兼容门面（W2-B4 拆分后薄转发层）。

实现已于 2026-11-23（W2-B4，正典=《musespiral-运维发布改良设计-2026-11-23》
§三，沿 ARCH-2 materials/pipeline 拆分模式）按四簇真实内聚边界拆入
``services/agent/`` 包：store（run 目录路径门/原子读写/prune）/ build（构造
期校验/覆盖级联/license 快照）/ execute（执行/job 闭包/读面）/ adopt（双
采纳唯一写面）。全量符号面见 ``musespiral.services.agent`` 的 ``__init__``。

本模块保留原 import 路径与**拆分前完全一致的符号集**（``from .agent
import *``，包 ``__all__`` 锁定 96 名，含模块级 import 名与下划线私有名——
CLI（agent_cmds）/ Studio（agent_payload）/ book_fetch/tag_service/全部测试
的调用面零改动；测试直用的私有名（``_execute``/``_make_run_id`` 等七名）
亦在集内）。

与 materials 门面不同，本域**无 setattr 注入缝**（AI 替换走 ``chat_fn=``
参数缝，gateway 打点在 ``infra.ai.gateway._real_chat`` 层）——纯转发即可。
"""
from __future__ import annotations

from .agent import *  # noqa: F401,F403 —— 门面契约：符号集=拆分前全集（__all__ 锁定 96 名）
