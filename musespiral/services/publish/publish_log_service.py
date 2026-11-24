"""publish_log 双位置的单一持有者（第十四期 F36/F36c/F37，正典 §三-Q1）。

双日志事实（真机②实证）：CLI publish 写 `~/.musespiral/publish_log.jsonl`
（config.home_dir()，可由 MUSESPIRAL_HOME 隔离），Studio 工作区可在 articles
root 自带一份 `publish_log.jsonl`（scan._log_path 优先读此处）。本期起
**统一读序：root 优先、home 补充、两处合并去重**——本模块是这条读序与
两类新增行（F37 uploaded 回执 / F36c manual 登记）的唯一实现，F37 回执、
F36c 登记、plan 周历、stats 统计、Studio 仪表盘/徽章读侧全部走这里
（第十四期集成对齐，禁止再散落直读 jsonl）。

行 schema（正典 §1.2，三型并存）：
  既有     {"ts", "action": "draft"|"publish", "article", "title",
            "media_id", "platform", "account"}
  型一 F37 {"ts", "platform", "article", "result": "uploaded"}
  型二 F36c{"ts", "action": "publish", "platform", "article", "title",
            "url"?, "manual": true}

契约：
  - 合并去重键 = ts + article + platform + (action 或 result)——键不同即并存；
  - 演示 fixture 行以 media_id 前缀 `demo_` 判除（周历与统计均排除）；
  - 读侧宽容：坏行（非法 JSON / 非对象 / 空行）跳过不崩溃，文件缺失视同空；
  - 写侧全部经 infra/storage/jsonl.append_log（父目录自建，行级追加），
    ts 与既有行同口径（ISO 秒级，publisher.create_draft 同款）。

依赖纪律（正典 §3.2）：只依赖 infra/storage/jsonl + config，零上层引用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import config
from ...infra.storage import jsonl
from ...infra.storage.jsonl import append_log, default_log_path, summarize_rows
from ...jobs.store import now_iso as _now_iso


def root_log_path(root: str | Path) -> Path:
    """articles root 侧发布日志路径（root 自带 publish_log.jsonl 场景）。"""
    return Path(root) / config.PUBLISH_LOG


def _row_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """合并去重键：ts + article + platform + (action 或 result)。

    action/result 均缺失的畸形行键位取 None（仍参与去重，读侧不猜语义）。
    """
    return (
        row.get("ts"),
        row.get("article"),
        row.get("platform"),
        row.get("action") or row.get("result"),
    )


def _is_demo(row: dict[str, Any]) -> bool:
    """演示 fixture 行判定：media_id 前缀 demo_（正典 §1.2）。"""
    return str(row.get("media_id") or "").startswith("demo_")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """宽容读一个 jsonl（infra/storage/jsonl.read_rows 单一权威实现的薄壳）。"""
    return jsonl.read_rows(path)


def summarize(root: str | Path | None = None, *, recent_n: int = 10) -> dict[str, Any]:
    """合并读序上的统计汇总（CLI stats / Studio 仪表盘共用，第十四期集成）。

    merge_logs（root 优先、home 补充、去重、demo 行滤除——正典 §1.2
    「周历与统计均排除」）的行集经 infra jsonl.summarize_rows 聚合，
    返回结构与 jsonl.summarize 逐键一致（同一聚合实现，口径不漂移）。
    """
    return summarize_rows(merge_logs(root), recent_n)


def merge_logs(root: str | Path | None = None) -> list[dict[str, Any]]:
    """双日志合并读（root 优先、home 补充、按 _row_key 去重、demo 行滤除）。

    返回 root 行（文件序）在前、home 补充行（文件序）在后的行列表；同键
    重复只保留 root 侧那份（root 是工作区自带日志，优先即以其为准）。
    root 为 None 时只读 home 日志（纯 CLI 场景仍可用）。
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    sources: list[Path] = []
    if root is not None:
        sources.append(root_log_path(root))
    sources.append(default_log_path())
    for path in sources:
        for row in _read_rows(path):
            if _is_demo(row):
                continue
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def append_uploaded(
    article: str,
    platform: str,
    *,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """追加 F37 浏览器交付回执行（型一）：只记交付不记发布。

    uploader_browser 会话**正常结束**点（物料已交付、人工点发前）调用；
    异常中断不得调用（未交付不算）。缺省写 home 日志（CLI 侧日志），
    log_path 可显式改指 root 侧。返回落盘的行（ts 由本函数生成）。
    """
    row = {"ts": _now_iso(), "platform": platform, "article": article, "result": "uploaded"}
    append_log(log_path or default_log_path(), row)
    return row


def append_manual(
    article: str,
    platform: str,
    title: str,
    *,
    url: str | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """追加 F36c 人工登记行（型二）：manual:true 的 publish 行。

    mp 终发是人工后台点击，publish_log 永远不会自然产生 mp 的 publish
    行——`musespiral log add` 落的就是本行，周历与 stats 的「已发布」归位唯一
    来源。url 缺省不写键（可选字段）。撤销不做（jsonl 留档纪律，误登记
    人工编辑文件删行——正典 §三-Q3）。
    """
    row: dict[str, Any] = {
        "ts": _now_iso(),
        "action": "publish",
        "platform": platform,
        "article": article,
        "title": title,
        "manual": True,
    }
    if url:
        row["url"] = url
    append_log(log_path or default_log_path(), row)
    return row
