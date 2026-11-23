"""agent 存储层——run 目录的路径门、原子读写、run.json 骨架与 LRU prune。

ARCH 语义拆分（W2-B4，正典=《musespiral-运维发布改良设计-2026-11-23》§三）：
原 ``services/agent_service.py``（1046 行）按四簇真实内聚边界拆入本包，
本模块是**唯一落盘面**——run 目录的形状门（``run_dir`` 路径穿越门）、
``_write_json``/``_read_json`` 原子读写、``_run_meta`` 骨架、``prune``
LRU 清理。执行/采纳簇只经本模块落盘，不自行拼路径。

语义逐字未动（含 docstring 判例注记），本模块只改落点。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from ... import config
from ...infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ...jobs.store import now_iso as _now_iso

# 编排运行目录（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移）
RUNS_DIR = "agent_runs"
RUN_FILE = "run.json"
STEPS_DIR = "steps"

# run_id 形状白名单（compose `_SLUG_RE` 同款纪律：首字符字母数字，其余
# [A-Za-z0-9._-]）。**路径穿越门**：`../x`、`/abs`、`..%2F` 等形态在首字符
# 与字符类两层均被拒——run_id 直接拼进 run 目录路径（`run_dir`），无此门则
# `GET /api/agent/runs?id=../sneak` 可读 home 下任意 JSON 原样回显（密钥零
# 回显红线的破口）、`adopt('../../x')` 可把外部目录伪造成 run 落库（复核
# 实测两路均通，本门为咽喉层收口）。本模块自产的 run_id
# （`<plan>-<YYYYMMDD-HHMMSS>-<rand4>`）天然在白名单内。
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# run 目录保留数（`agent runs --prune` 缺省 50，设计 §1.1 LRU by finished）
PRUNE_KEEP = 50

# 已运行态（run.json status；与 interp.RunResult 三态同枚举 + running）
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
# 读侧标记（run.json 仍是 running 但对应 job 已 failed(interrupted)——设计
# §二-F75.2 降级表末行：不自动续跑，读侧对齐 jobs 语义）
STATUS_INTERRUPTED = "interrupted"

# run.json steps[].status 的额外态（interp 只产 done/failed；running 是落盘
# 初值，仅出现在未完成 run 的 steps 列表——空）
STEP_RUNNING = "running"


class AgentServiceError(ValueError):
    """编排业务错误（可读消息；CLI 退出 1 / Studio 转 400 或 ok:false）。

    构造期（plan 名/素材/正文面/步数）与采纳期（run 不存在/status≠done/
    plan 无 adopt 面）共用——两类都是「用户可修复的可读拒绝」，不区分类型
    以免调用方双分支。
    """


def _valid_run_id(run_id: object) -> bool:
    """run_id 是否白名单形态（非 str/空/穿越形态 → False）。"""
    return isinstance(run_id, str) and bool(_RUN_ID_RE.match(run_id))


# ── 路径与时间戳 ─────────────────────────────────────────────────────


def runs_root() -> Path:
    """run 目录根：config.home_dir()/agent_runs（每次现读，随 MUSESPIRAL_HOME 漂移）。"""
    return config.home_dir() / RUNS_DIR


def run_dir(run_id: str) -> Path:
    """单个 run 目录（**形状门在咽喉层**：白名单外 run_id → AgentServiceError，
    任何未来调用方都不可能再拿穿越形态拼路径；不校验存在——调用方按需 stat）。"""
    if not _valid_run_id(run_id):
        raise AgentServiceError(
            f"非法 run_id: {run_id!r}（白名单 [A-Za-z0-9][A-Za-z0-9._-]；"
            f"含路径分隔/穿越形态一律拒）"
        )
    return runs_root() / run_id


def _make_run_id(plan_name: str) -> str:
    """`<plan>-<YYYYMMDD-HHMMSS>-<rand4>`（设计 §1.1）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{plan_name}-{stamp}-{uuid.uuid4().hex[:4]}"


def _now() -> str:
    """ISO 秒级时间戳（与 jobs.store.now_iso 同口径，run.json started/finished）。"""
    return _now_iso()


# ── run 目录落盘（单一持有者）──────────────────────────────────────


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    """tmp+replace 原子写（§四 Q4 落盘义务：全部经 atomic_write_bytes）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any] | None:
    """宽容读（坏文件/缺失 → None——读侧永不因脏数据崩溃）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run_meta(
    run_id: str,
    plan: dict[str, Any],
    material_ids: Sequence[str],
    *,
    status: str,
    started: str,
    licenses: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """run.json 骨架（设计 §1.1 键集；prompt 全文**不落盘**）。

    `inputs.licenses`=构造期定格的源许可快照（license 存证规则，见
    adopt 簇 `_license_snapshot`）——采纳时据此取最严，源事后被删不放松许可。
    """
    inputs: dict[str, Any] = {"material_ids": list(material_ids)}
    if licenses:
        inputs["licenses"] = [dict(row) for row in licenses]
    return {
        "id": run_id,
        "plan": plan["plan"],
        "status": status,
        "started": started,
        "finished": None,
        "inputs": inputs,
        "budget": {"in_chars": 0, "out_chars": 0, "steps": 0},
        "limits": dict(plan.get("budget") or {}),  # run 级预算上限（摘要块的「余量」分母）
        "steps": [],
    }


# ── prune（显式删，非墓碑）──────────────────────────────────────────


def prune(keep: int = PRUNE_KEEP) -> dict[str, Any]:
    """LRU 清理 run 目录（按 finished/started 新在前，保留 keep 个）→ `{removed, kept}`。

    run 目录不进 materials，故 prune 是**显式删**而非墓碑（设计 §四 Q3：
    「run 目录 prune=显式删非墓碑，不进 materials 无连坐义务」）。
    """
    import shutil

    root = runs_root()
    if not root.is_dir():
        return {"removed": 0, "kept": 0}
    entries: list[tuple[str, Path]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        meta = _read_json(directory / RUN_FILE)
        if meta is None:
            # 坏目录按 mtime 参与（防永久留垃圾）
            entries.append((_iso_from_mtime(directory), directory))
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


def _iso_from_mtime(directory: Path) -> str:
    """目录 mtime → ISO 秒级串（**不走 now_iso**——那取的是「现在」不是文件时间）。

    用 `strftime` 而非 `datetime.isoformat(timespec=…)`：后者是服务层时间戳
    单一权威门的扫描字样（`test_services_timestamp_single_authority`），本函数
    的语义是「读既有文件的时间」而非「造当前时间」，不该与权威混淆。
    """
    try:
        return datetime.fromtimestamp(directory.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    except OSError:
        return ""
