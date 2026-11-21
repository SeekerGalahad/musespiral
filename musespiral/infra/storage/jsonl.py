"""publish_log 的 JSONL 行级读写（Phase C 自顶层 stats.py 迁入，行为零变更）。

正典 §2.3 infra/storage/jsonl.py：『JSONL 行级原子读写（jobs/publish_log
共用）』——本模块收编发布日志的追加/回填/汇总全部 I/O；行级原子写共用件
在 infra/storage/filesystem.py（jobs store 同源）。

The log (~/.musespiral/publish_log.jsonl) records every successful draft creation
and publish submission, closing the "feedback" loop of the platform pipeline.
`summarize` reads it back for `musespiral stats`: cumulative / this-month counts
plus the most recent entries, and (F13) per-platform group counts — every
record carries a `platform` field (default "mp"; legacy rows without it are
read back as "mp").

Pure local file access — no WeChat data API calls here. F17 发布数据回填
(infra/wechat/analytics.sync_reads) 负责调数据分析接口把 reads/shares/likes
回填进行内，`summarize` 只读回这些字段汇总 total_reads/total_shares（缺
字段按 0）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ... import config


def _to_int(v: Any) -> int:
    """宽容取整：None/缺字段/非数值一律 0（summarize 对脏数据不崩溃）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def default_log_path() -> Path:
    """默认发布日志路径：config.home_dir()/config.PUBLISH_LOG。"""
    return config.home_dir() / config.PUBLISH_LOG


def append_log(
    log_path: str | Path,
    record: dict[str, Any],
    *,
    platform: str = "mp",
) -> None:
    """追加一条发布记录到 jsonl 日志（父目录不存在则创建）。

    F13 多平台化：platform 缺省 "mp"（微信公众号），注入记录的 platform
    字段；record 自带 platform 时以 record 为准（显式构造的值不被覆盖）。
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"platform": platform, **record}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def set_publish_url(log_path: str | Path, publish_id: str, article_url: str) -> bool:
    """把日志中 publish_id 对应的发表记录补上 article_url（就地重写该行）。

    submit_publish 成功即落日志，而文章链接要等 check_publish_status 轮询
    才能拿到 —— CLI 在取得链接后回调本函数补写。找不到匹配记录（日志缺
    失/损坏）返回 False，宽容处理不抛错。
    """
    path = Path(log_path)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("publish_id") == publish_id:
            obj["article_url"] = article_url
            lines[i] = json.dumps(obj, ensure_ascii=False)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def read_rows(log_path: str | Path) -> list[dict[str, Any]]:
    """宽容读一个 jsonl 文件 → 行对象列表（单一权威实现）。

    坏行（非法 JSON / 非对象 / 空行）跳过、文件缺失返回空、errors="replace"
    兼容混入的非 UTF-8 字节——jobs/store、publish_log、materials、
    discovered 等全部 JSONL 读侧共用本口径（行为零变更收编，原四处逐字
    相同的私有副本删除）。
    """
    path = Path(log_path)
    if not path.is_file():
        return []
    try:
        # errors="replace"：混入非 UTF-8 字节时按坏行跳过（studio /api/stats 不 500）
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def summarize(log_path: str | Path, recent_n: int = 10) -> dict[str, Any]:
    """汇总发布日志（路径读侧：宽容读文件 → summarize_rows 聚合）。

    返回结构见 summarize_rows；损坏行（非法 JSON 或非对象）跳过不崩溃；
    日志文件不存在时返回全零汇总。第十四期起聚合本体在 summarize_rows，
    供 services.publish_log_service 对合并去重后的行集复用（同一聚合口径）。
    """
    return summarize_rows(read_rows(log_path), recent_n)


def summarize_rows(records: list[dict[str, Any]], recent_n: int = 10) -> dict[str, Any]:
    """聚合已读入的发布日志行（纯函数，不触文件系统）。

    返回：
      total_draft / total_publish — 累计草稿 / 发表数
      month_draft / month_publish — 本自然月（按 ts 前 "YYYY-MM" 匹配）草稿 / 发表数
      total_reads / total_shares — F17 发布数据回填后的阅读 / 分享数合计
       （analytics.sync_reads 回填的 reads/shares 字段求和；旧日志行缺
        字段或值非数值按 0 计，兼容回填前的日志）
      recent — 最近 recent_n 条记录，最新在前
      by_platform — F13 按平台分组：{platform: {drafts, publishes,
        month_drafts, month_publishes, recent（该平台最近 recent_n 条，最新在前）}}；
        旧日志行缺 platform 字段（或为空值）读作 "mp" 归入 mp 组（向后兼容）。
        日志无记录时为 {}
    """
    month_prefix = datetime.now().strftime("%Y-%m")
    total_draft = sum(1 for r in records if r.get("action") == "draft")
    total_publish = sum(1 for r in records if r.get("action") == "publish")
    month_draft = sum(
        1
        for r in records
        if r.get("action") == "draft" and str(r.get("ts", "")).startswith(month_prefix)
    )
    month_publish = sum(
        1
        for r in records
        if r.get("action") == "publish" and str(r.get("ts", "")).startswith(month_prefix)
    )
    # F17：阅读/分享合计（sync_reads 回填的行内字段；缺字段按 0 兼容旧日志）
    total_reads = sum(_to_int(r.get("reads")) for r in records)
    total_shares = sum(_to_int(r.get("shares")) for r in records)
    by_platform: dict[str, dict[str, Any]] = {}
    for r in records:
        # 旧日志行无 platform 字段（或空值）读作 "mp"，与顶层计数口径一致
        p = str(r.get("platform") or "mp")
        action = r.get("action")
        if action not in ("draft", "publish"):
            continue
        in_month = str(r.get("ts", "")).startswith(month_prefix)
        grp = by_platform.setdefault(
            p,
            {"drafts": 0, "publishes": 0, "month_drafts": 0, "month_publishes": 0, "recent": []},
        )
        if action == "draft":
            grp["drafts"] += 1
            if in_month:
                grp["month_drafts"] += 1
        else:
            grp["publishes"] += 1
            if in_month:
                grp["month_publishes"] += 1
        grp["recent"].append(r)
    # 分组 recent：每平台最近 recent_n 条，最新在前（第五期遗留 low 修复）
    for grp in by_platform.values():
        grp["recent"] = list(reversed(grp["recent"]))[:recent_n]
    return {
        "total_draft": total_draft,
        "total_publish": total_publish,
        "month_draft": month_draft,
        "month_publish": month_publish,
        "total_reads": total_reads,
        "total_shares": total_shares,
        "recent": list(reversed(records))[:recent_n],
        "by_platform": by_platform,
    }
