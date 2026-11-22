"""pipeline 包：prune（run 目录 LRU 回收）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的回收簇：引擎
docstring「保留策略」的落地处——LRU 保最近 N 次（agent_service.prune
判例同款——「7 天」在目录级上不可靠：一次长跑的 run 一样该保，按个数
才是稳定上限）。CLI 面= ``musespiral plrun prune``。

包门面与全量符号面见 ``musespiral.services.pipeline`` 的 ``__init__``。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .engine import pipeline_runs_root

# run 目录保留策略（引擎 docstring「保留策略」的落地处）：LRU 保最近 N 次
# （agent_service.prune 判例同款——「7 天」在目录级上不可靠：一次长跑的
# run 一样该保，按个数才是稳定上限）。默认 5，`musespiral plrun prune` 可调。
PRUNE_KEEP = 5


def prune_runs(keep: int = PRUNE_KEEP) -> dict[str, Any]:
    """LRU 清理 pipeline run 目录（保留最近 keep 个）→ {"removed", "kept"}。

    排序键：meta.json 的 finished/started（终态写全纪律保证在场）；坏
    目录（meta 读不出）按目录 mtime 参与（防永久留垃圾）。run 目录不进
    materials，prune=显式删非墓碑（agent_service 判例同款）。CLI 面=
    ``musespiral plrun prune --keep N``。
    """
    root = pipeline_runs_root()
    if not root.is_dir():
        return {"removed": 0, "kept": 0}

    def _mtime_iso(directory: Path) -> str:
        try:
            stamp = directory.stat().st_mtime
        except OSError:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp))

    entries: list[tuple[str, Path]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if not isinstance(meta, dict):
            entries.append((_mtime_iso(directory), directory))
            continue
        entries.append((str(meta.get("finished") or meta.get("started") or ""), directory))
    entries.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
    limit = keep if keep and keep > 0 else PRUNE_KEEP
    removed = 0
    for _stamp, directory in entries[limit:]:
        try:
            shutil.rmtree(directory)
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "kept": min(len(entries), limit)}
