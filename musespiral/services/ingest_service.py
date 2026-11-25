"""采集编排（第二十期 §1.3/F54 + B 采集扩展 rss/clip/crawl/tick）——源刷新
→ materials；jobs kind=ingest/clip。

源刷新（每源一行采集任务，任务层两态重试沿用）：
  - kb 源：scan 确定性管道（core/discovery/scanner.scan_dir）扫子源目录
    → type=text 素材（引用不复制：payload.local_path 指回 KB 文档，正文
    不入库）；
  - radar_hub 源：订阅表达式求值（F55）→ type=radar_signal 素材，payload
    .meta 携带 {line, grade, score, url} 四键（+license_note 声明原文）；
  - rss 源（B 扩展 F65）：infra/fetcher 五段门拉取 → infra/sources/rss
    （feedparser）解析 → type=link 素材（meta 四键 feed_title/published/
    url(+guid)）；drift 策略=skip+drifted 计数（首见冻结，不 raise）。

B 扩展新增面（F66/F67/F68/F69）：
  - clip_url：手动剪藏预览（fetcher manual 模式+readability 提取）——
    零落盘（确认闸绝对：adopt 才入库）；
  - crawl_manual：手动爬取首切片——fetch+extract → crawler pending 区
    （去重=URL sha256+前 500 字内容 sha，已存在标 dup 不入库）；
  - adopt_clip：人工确认落库（crawled/link 两模式，pending 翻 adopted）；
  - tick：调度单趟（due 判定=now−last_refresh ≥ max(interval, ceil(
    crawl_delay/60))；dry-run 零网络零落盘）。

radar 数据面（四端点纪律的落地口径）：雷达线清单与产物清单走客户端四端点
（/api/radars + /api/radars/<id>/outputs——回环钉死、只读、宕机 down 不抛）；
信号条目本体读产物清单指向的本机 curated/scored.json（清单 file.path 是
radar-hub 报告的本机绝对路径；hub 侧 /api/file 不在钉死四端点内，不接）。
只读、只认文件名 scored.json，不写任何外部路径。

纪律：
  - 串行队列：模块级锁贯穿单源刷新全程（本地单人=串行，无调度器；jobs
    runner 双 worker 并发提交时在此排队）；
  - 失败不半写：素材行全部构造+校验+冲突预检通过后才落盘（materials_
    service.add_many 单一提交点）；任何一行失败，库内零残留；
  - 采集只新增：既有同 id 素材（含墓碑）一律跳过不改写——immutable 原始
    红线的采集侧延伸（人工删除优先于重采复活）；
  - 宕机=灰灯：radar-hub 不可达 / rss 拉取拒 → 源健康标 down、本源零
    素材、返回 status=down（正常返回值不是失败——不阻塞其他源）。
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.materials import model
from ..core.web.canonical import canonical_url
from ..infra import fetcher
from ..infra.sources import clipper, crawler, radarhub, rss as rss_feed
from ..jobs.store import now_iso as _now_iso
from ..services import materials_service, sources_service

# 串行队列锁：单源刷新全程持锁（refresh_all 逐源顺序调用，天然串行）
_REFRESH_LOCK = threading.Lock()

# curated 评分台账文件名（radar-hub 各线 data/curated/ 下；只认这个名字）
SCORED_FILENAME = "scored.json"


# 采集侧内容指纹剔除键：fetched_at 随每次重采前进（采集时钟）；triage 是
# 用户在采集之后落的分诊态（星标/已读/禁用）。两者都不是内容面——重采时
# 不剔除则等值比较永不成立，稳定撞 add_many 的 immutable 零写入 raise
# （2026-11-16 真 radar-hub 复测抓出：+37min 重采全批 ValueError；既有
# 幂等测试背靠背同秒走运相等掩盖了该缺陷）。其余键（type/origin/license/
# payload/provenance）任何差异仍构成内容漂移，交 add_many fail-fast。
_VOLATILE_KEYS = ("fetched_at", "triage")


def _content_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    """行内容指纹（immutable 比较的采集侧口径）：剔除采集时钟与用户分诊态。"""
    return {k: v for k, v in row.items() if k not in _VOLATILE_KEYS}


def _split_stale(
    rows: list[dict[str, Any]], existing: dict[str, dict[str, Any]],
    *, drift_policy: str | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """把候选行分成（待提交, 等值重采跳过数, 漂移计数）。

    等值＝内容指纹相同（仅 fetched_at/triage 漂移）→ 跳过不改写（采集只
    新增，模块头契约）；指纹不同或新 id → 原样交给 add_many（真漂移由其
    immutable 红线 raise，零写入——test_kb_refresh_conflict_is_zero_write
    钉死的裁决）。墓碑 id 不在活跃面，自然流入待提交，由 add_many 不复活
    保护跳过。

    drift_policy="drift"（B 扩展 F69，rss/clip 流）：同 id 指纹不同 →
    skip+drifted 计数（首见冻结——不 raise 不覆盖不半写，旧行逐字节
    不变）；缺省 None（kb/radar 流）保持 fail-fast 现状零变更。
    """
    fresh: list[dict[str, Any]] = []
    stale = 0
    drifted = 0
    for row in rows:
        e = existing.get(row["id"])
        if e is not None and not model.is_tombstone(e):
            if _content_fingerprint(e) == _content_fingerprint(row):
                stale += 1
                continue
            if drift_policy == "drift":
                drifted += 1  # 首见冻结：旧行不动，新行不入库
                continue
        fresh.append(row)
    return fresh, stale, drifted


# ── 单源刷新（F54「刷新此源（单步）」；jobs kind=ingest 的执行体）────


def refresh_source(source_id: str, *, articles_root: str | Path | None = None) -> dict[str, Any]:
    """刷新一条源（串行持锁）：按源类型分派，返回可读结果字典。

    - 源不存在/类型不支持/表达式非法 → ValueError（任务层转 failed，
      行内暗朱点悬停原文，F54）；
    - 源禁用 → {"status": "disabled"}（禁用源不进采集循环，零动作零落盘）；
    - radar-hub 宕机 → {"status": "down"}（灰灯语义，非失败）。
    """
    with _REFRESH_LOCK:
        source = sources_service.get_source(source_id)
        if source is None:
            raise ValueError(f"源不存在: {source_id!r}")
        if source.get("enabled") is not True:
            return {"source_id": source_id, "status": "disabled", "added": 0, "skipped": 0}
        stype = source.get("type")
        if stype == "kb":
            return _refresh_kb(source)
        if stype == "radar_hub":
            return _refresh_radar(source, articles_root=articles_root)
        if stype == "rss":
            return _refresh_rss(source)
        if stype == "dir":
            return _refresh_dir(source)
        raise ValueError(f"源类型 {stype!r} 暂不支持刷新（支持 kb / dir / radar_hub / rss）")


def refresh_all(*, articles_root: str | Path | None = None) -> list[dict[str, Any]]:
    """刷新全部源（F54「刷新全部」=顺序单步循环；失败不阻塞后续源）。

    每源结果一条（ok/down/disabled/failed + 计数/错误文本）；单源失败
    记入该源 result 的 status=failed，循环继续——与「宕机不阻塞其他源」
    同语义放大到失败面。
    """
    results: list[dict[str, Any]] = []
    for source in sources_service.list_sources():
        try:
            results.append(refresh_source(source["id"], articles_root=articles_root))
        except Exception as exc:  # 单源失败不阻塞（可读错误随源记录）
            results.append({"source_id": source["id"], "status": "failed", "error": str(exc)})
    return results


# ── kb 源刷新（scan 管道 → text 素材，引用不复制）──────────────────


def _kb_scan_dirs(source: dict[str, Any]) -> tuple[Path, list[Path]]:
    """kb 源扫描面解析：(根, 待扫目录列表)。

    子源表（F46 config.sources[].relpath）逐个解析为根内目录；空表回退扫
    根本身。根未配置/缺失 → ValueError（可读错误，不猜路径）。
    """
    cfg = source.get("config") or {}
    raw_root = cfg.get("root")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ValueError(f"kb 源 {source['id']!r} 未配置 config.root（先在设置面登记知识库根）")
    root = Path(raw_root).expanduser()
    if not root.is_dir():
        raise ValueError(f"kb 源 {source['id']!r} 的根目录不存在: {raw_root}")
    rels = [s.get("relpath") for s in (cfg.get("sources") or []) if isinstance(s, dict)]
    dirs = [root / str(rel) for rel in rels if isinstance(rel, str) and rel.strip()] or [root]
    for d in dirs:
        if not d.is_dir():
            raise ValueError(f"kb 源 {source['id']!r} 的子源目录不存在: {d}")
    return root, dirs


def _refresh_kb(source: dict[str, Any]) -> dict[str, Any]:
    """kb 刷新：scanner 扫子源目录 → type=text 素材批量落库。"""
    from ..core.discovery import scanner  # 函数体内导入（导入期零扫描器依赖）

    root, dirs = _kb_scan_dirs(source)
    fetched_at = _now_iso()
    rows: list[dict[str, Any]] = []
    for d in dirs:
        for meta in scanner.scan_dir(d):
            try:
                uri = Path(meta["path"]).relative_to(root).as_posix()
            except ValueError:
                raise ValueError(
                    f"扫描产物越出 KB 根（子源配置疑似回退）: {meta['path']} 不在 {root} 内"
                ) from None
            rows.append(
                model.make_material(
                    mtype="text",
                    source_id=source["id"],
                    uri=uri,
                    title=str(meta.get("title") or ""),
                    payload={"local_path": uri},  # 引用不复制：正文不入库
                    fetched_at=fetched_at,
                )
            )
    # 同一文档可能被子源重叠覆盖扫到两次——add_many 批内等值去重收敛；
    # 等值重采（仅 fetched_at/triage 前进）预滤跳过不改写，真漂移仍 fail-fast
    existing = {m["id"]: m for m in materials_service.list_materials()}
    fresh, stale, _drifted = _split_stale(rows, existing)
    result = materials_service.add_many(fresh)
    return {
        "source_id": source["id"],
        "status": "ok",
        "scanned": len(rows),
        "added": result["added"],
        "skipped": result["skipped"] + stale,
    }


def _refresh_dir(source: dict[str, Any]) -> dict[str, Any]:
    """dir 刷新：扫本地目录（.md）→ type=text 素材批量落库（引用不复制）。

    与 kb 的差别只在「扫描面」：kb 有子源层级（config.sources[].relpath 相对
    根解析），dir 是单个独立目录，故 uri 用**绝对路径**——同一文件重扫 id
    稳定（id=sha1(source_id|type|uri)），且天然不与 kb 的相对路径冲突。

    config.path 必填且必须存在（可读报错，不猜路径）；单篇损坏由 scanner
    跳过不塌（scan_dir 既有语义）。
    """
    from ..core.discovery import scanner  # 函数体内导入（导入期零扫描器依赖）

    cfg = source.get("config") or {}
    raw_path = cfg.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"dir 源 {source['id']!r} 未配置 config.path（设置面登记本地目录）")
    root = Path(raw_path).expanduser()
    if not root.is_dir():
        raise ValueError(f"dir 源 {source['id']!r} 的目录不存在: {raw_path}")

    fetched_at = _now_iso()
    rows: list[dict[str, Any]] = []
    for meta in scanner.scan_dir(root):
        abs_path = str(Path(meta["path"]).resolve())
        rows.append(
            model.make_material(
                mtype="text",
                source_id=source["id"],
                uri=abs_path,
                title=str(meta.get("title") or ""),
                payload={"local_path": abs_path},  # 引用不复制：正文不入库
                fetched_at=fetched_at,
            )
        )
    existing = {m["id"]: m for m in materials_service.list_materials()}
    fresh, stale, _drifted = _split_stale(rows, existing)
    result = materials_service.add_many(fresh)
    return {
        "source_id": source["id"],
        "status": "ok",
        "scanned": len(rows),
        "added": result["added"],
        "skipped": result["skipped"] + stale,
    }


# ── radar_hub 源刷新（订阅求值 → radar_signal 素材）─────────────────


def _load_scored_items(path: Path) -> list[dict[str, Any]]:
    """读 curated/scored.json 条目（本机只读；坏文件 ValueError 可读错误）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"radar 评分台账不可读: {path}: {exc}") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"radar 评分台账缺 items 数组: {path}")
    return [i for i in items if isinstance(i, dict)]


def _signal_row(source_id: str, radar_id: str, item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    """一条 radar 信号 → radar_signal 素材行（meta 四键 + license_note）。"""
    url = str(item.get("url") or "")
    raw_license = str(item.get("license") or "").strip()
    try:
        score = int(item.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return model.make_material(
        mtype="radar_signal",
        source_id=source_id,
        uri=url,
        title=str(item.get("repo") or url),
        license="CC-BY" if raw_license.upper().startswith("CC-BY") else "UNKNOWN",
        payload={
            "url": url,
            "meta": {
                "line": radar_id,
                "grade": str(item.get("grade") or ""),
                "score": score,
                "url": url,
                "license_note": raw_license,  # SPDX 原文（枚举外的声明留档）
            },
        },
        fetched_at=fetched_at,
    )


def _refresh_radar(source: dict[str, Any], *, articles_root: str | Path | None) -> dict[str, Any]:
    """radar_hub 刷新：四端点客户端拉线清单/产物清单 → 订阅求值 → 落库。

    宕机：radars() 返 down → 健康标 down、返回 status=down（灰灯非失败，
    零素材零落库）。可达：健康标 ok 后求值——订阅表达式非法（解析期）/
    条件键未知（求值期）均 ValueError（可读错误，不静默）。
    """
    base = (source.get("config") or {}).get("base")
    client = radarhub.RadarHubClient(str(base))
    radars = client.radars()
    if radarhub.is_down(radars):
        sources_service.mark_health(source["id"], "down")
        return {
            "source_id": source["id"],
            "status": "down",
            "added": 0,
            "skipped": 0,
            "detail": str(radars.get("error") or ""),
        }
    sources_service.mark_health(source["id"], "ok")

    parsed = [sources_service.parse_subscription(expr) for expr in source.get("subscriptions") or []]
    from ..services import discover_service  # 缺省文章根与其上正稿推导同源

    articles = Path(articles_root) if articles_root is not None else discover_service.DEFAULT_ARTICLES_ROOT
    drafted = materials_service.drafted_uris(articles)

    fetched_at = _now_iso()
    batch: dict[str, dict[str, Any]] = {}  # id → row（跨线同仓取首线）
    deduped = invalid = 0
    lines_scanned: list[str] = []
    for conditions in parsed:
        for radar in radars.get("radars") or []:
            radar_id = str(radar.get("id") or "")
            if not radar_id or not sources_service.line_matches(conditions, radar_id):
                continue
            if radar_id not in lines_scanned:
                lines_scanned.append(radar_id)
            outputs = client.outputs(radar_id)
            if radarhub.is_down(outputs):
                raise ValueError(f"雷达线产物清单拉取失败: {radar_id}: {outputs.get('error')}")
            for out in outputs.get("outputs") or []:
                for f in out.get("files") or []:
                    if str(f.get("name") or "") != SCORED_FILENAME:
                        continue
                    raw_path = str(f.get("path") or "")
                    if not raw_path.strip():
                        continue  # 清单行缺本机路径：无台账可读，跳过该文件
                    for item in _load_scored_items(Path(str(f.get("path") or ""))):
                        url = str(item.get("url") or "")
                        repo = str(item.get("repo") or "")
                        if not url or not repo:
                            invalid += 1  # 无 url/repo 的行成不了信号（计数不静默）
                            continue
                        if not sources_service.item_matches(conditions, item, drafted=drafted):
                            continue
                        row = _signal_row(source["id"], radar_id, item, fetched_at)
                        if row["id"] in batch:
                            deduped += 1  # 跨线/跨订阅同仓：首线胜出
                            continue
                        batch[row["id"]] = row
    # 等值重采（仅 fetched_at/triage 前进）预滤跳过不改写，真漂移仍 fail-fast
    existing = {m["id"]: m for m in materials_service.list_materials()}
    fresh, stale, _drifted = _split_stale(list(batch.values()), existing)
    result = materials_service.add_many(fresh)
    return {
        "source_id": source["id"],
        "status": "ok",
        "lines": lines_scanned,
        "added": result["added"],
        "skipped": result["skipped"] + stale,
        "deduped": deduped,
        "invalid": invalid,
    }


# ── rss 源刷新（B 扩展 F65：fetcher 五段门 → feedparser → link 素材）──

# 剪藏/爬取素材的伪源 id（Material.origin.source_id；稳定 id 的第一元）
CLIP_SOURCE_ID = "clip"


def _rss_row(source: dict[str, Any], item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    """一条 feed 条目 → type=link 素材行（meta 四键 + license 推断）。

    uri 取 guid 优先（feed 条目编辑天然稳定 id）；缺 guid 回退规范化 link
    （canonical 幂等保证重采同 id）。license 无 CC 标记 → ALL-RIGHTS 基线
    （T65-1 口径：网络内容默认有主）；声明原文入 meta.license_note。
    """
    canon = canonical_url(item["url"]) if item.get("url") else ""
    uri = item.get("guid") or canon
    meta: dict[str, Any] = {
        "feed_title": item.get("feed_title") or "",
        "published": item.get("published") or "",
        "url": canon,
    }
    if item.get("guid"):
        meta["guid"] = item["guid"]
    if item.get("author"):
        meta["author"] = item["author"]
    if item.get("license_note"):
        meta["license_note"] = item["license_note"]
    return model.make_material(
        mtype="link",
        source_id=source["id"],
        uri=uri,
        title=item.get("title") or "",
        license=item.get("license") or "ALL-RIGHTS",
        payload={"url": canon, "meta": meta},
        fetched_at=fetched_at,
    )


def _refresh_rss(source: dict[str, Any]) -> dict[str, Any]:
    """rss 刷新：fetcher auto 门拉取 → feedparser 解析 → link 素材落库。

    拒/网络错/HTTP≠200/解析失败 → status=down + 可读 error + 健康回写
    （零素材零半写）；成功 → mark_refresh(ok)（last_refresh/last_ok/
    crawl_delay 同写）。漂移策略=skip+drifted（首见冻结，F69）。
    """
    sid = source["id"]
    cfg = source.get("config") or {}
    url = str(cfg.get("url") or "")
    try:
        max_items = int(cfg.get("max_items") or 50)
    except (TypeError, ValueError):
        max_items = 50

    def _down(error: str) -> dict[str, Any]:
        sources_service.mark_refresh(sid, status="down")
        return {"source_id": sid, "status": "down", "added": 0, "skipped": 0,
                "drifted": 0, "error": error}

    try:
        fetched = fetcher.fetch(url, mode="auto")
    except fetcher.FetchError as exc:
        return _down(str(exc))
    try:
        parsed = rss_feed.parse_feed(fetched.body, max_items=max_items)
    except rss_feed.FeedParseError as exc:
        return _down(str(exc))
    fetched_at = _now_iso()
    feed_title = parsed.get("feed_title") or ""
    rows = [_rss_row(source, {**item, "feed_title": feed_title}, fetched_at)
            for item in parsed["items"]]
    existing = {m["id"]: m for m in materials_service.list_materials()}
    fresh, stale, drifted = _split_stale(rows, existing, drift_policy="drift")
    result = materials_service.add_many(fresh)
    robots = fetched.robots or {}
    crawl_delay = robots.get("crawl_delay")
    sources_service.mark_refresh(sid, status="ok", crawl_delay=crawl_delay)
    return {
        "source_id": sid,
        "status": "ok",
        "added": result["added"],
        "skipped": result["skipped"] + stale,
        "drifted": drifted,
        "rejected": len(parsed.get("rejected") or []),
    }


# ── 手动剪藏（F66）与爬虫首切片（F67：确认闸绝对，preview 零落盘）──


def _clip_preview(url: str, *, fetched: fetcher.FetchResult) -> dict[str, Any]:
    """fetch+extract 的公共预览面（mode 裁决=置信度 ≥0.5 且正文非空）。"""
    ext = clipper.extract(fetched.body)
    canon = canonical_url(fetched.final_url)
    mode = "crawled" if ext["confidence"] >= 0.5 and ext["text"].strip() else "link"
    license_note = ext.get("license_note") or ""
    license_name = (
        "CC-BY" if "creativecommons.org/licenses/by" in license_note.lower() else "ALL-RIGHTS"
    )
    preview: dict[str, Any] = {
        "mode": mode,
        "url": canon,
        "title": ext["title"],
        "text": ext["text"] if mode == "crawled" else "",
        "confidence": ext["confidence"],
        "extractor": clipper.EXTRACTOR,
        "word_count": ext["word_count"],
        "license": license_name,
        "robots": fetched.robots or {},
    }
    if license_note:
        preview["license_note"] = license_note
    return preview


def clip_url(url: str) -> dict[str, Any]:
    """手动剪藏预览（manual 模式：robots 徽章不阻断；SSRF 门照拒）——零落盘。

    采纳（adopt_clip）是独立的人工确认动作——预览即纯读，拒绝即消失。
    """
    fetched = fetcher.fetch(url, mode="manual")
    return _clip_preview(url, fetched=fetched)


def _already_clipped(canon: str) -> bool:
    """同规范化 URL 已在素材库（crawled/link 活跃行）——dup 判定。"""
    for m in materials_service.list_materials():
        if m.get("type") in ("crawled", "link") and (m.get("payload") or {}).get("url") == canon:
            return True
    return False


def crawl_manual(url: str) -> dict[str, Any]:
    """手动爬取首切片（F67 §〇：无自动抓取——本函数只由人工触发）：
    fetch+extract → 去重（URL sha256+前 500 字内容 sha）→ pending 区。

    dup：同 URL 已在素材库，或同内容 pending 未决 → 标 dup 不入库；
    否则落 pending 行待人工采纳（确认闸绝对——采纳走 adopt_clip）。
    """
    fetched = fetcher.fetch(url, mode="manual")
    preview = _clip_preview(url, fetched=fetched)
    canon = preview["url"]
    url_sha = crawler.url_sha256(canon)
    c_sha = crawler.content_sha(preview["text"])
    prior = crawler.find_by_url(canon)
    dup = _already_clipped(canon) or (
        prior is not None
        and prior.get("status") == "pending"
        and prior.get("content_sha") == c_sha
    )
    status = "dup" if dup else "pending"
    row = crawler.register_pending(
        url=canon,
        title=preview["title"],
        text=preview["text"],
        confidence=preview["confidence"],
        word_count=preview["word_count"],
        license_name=preview["license"],
        license_note=preview.get("license_note") or "",
        robots=preview["robots"],
        status=status,
    )
    return {**preview, "status": status, "pending_id": row["id"]}


def adopt_clip(
    *,
    url: str,
    title: str,
    text: str = "",
    mode: str = "crawled",
    confidence: float = 0.0,
    word_count: int = 0,
    license_name: str | None = None,
    license_note: str = "",
    pending_id: str | None = None,
) -> dict[str, Any]:
    """人工确认落库（F66 adopt / F67 确认闸第二步）：crawled 或 link 素材。

    preview 结果可经人工编辑后回传（title/text/license 均以人工值落库）；
    同 id 等值重采幂等（add_many 跳过计数），异内容冲突 ValueError（壳层
    转 {ok:false}）。pending 行翻 adopted（给定时或按 URL 命中）。
    """
    canon = canonical_url(url)
    mtype = "crawled" if mode == "crawled" else "link"
    license_note = license_note or ""
    license_value = license_name or (
        "CC-BY" if "creativecommons.org/licenses/by" in license_note.lower() else "ALL-RIGHTS"
    )
    meta: dict[str, Any] = {
        "extractor": clipper.EXTRACTOR,
        "confidence": confidence,
        "word_count": word_count,
    }
    if license_note:
        meta["license_note"] = license_note
    payload: dict[str, Any] = {"url": canon, "meta": meta}
    if mtype == "crawled":
        payload["text"] = text
    row = model.make_material(
        mtype=mtype,
        source_id=CLIP_SOURCE_ID,
        uri=canon,
        title=title,
        license=license_value,
        payload=payload,
        fetched_at=_now_iso(),
    )
    result = materials_service.add_many([row])
    target = pending_id or (crawler.find_by_url(canon) or {}).get("id")
    if target:
        crawler.mark_status(str(target), "adopted")
    return {"material": row, "mode": mode, "added": result["added"], "skipped": result["skipped"]}


# ── 调度单趟（F68：due 判定 + tick；dry-run 零网络零落盘）──────────


def _effective_floor_minutes(source: dict[str, Any]) -> int:
    """due 下限分钟：max(interval_minutes, ceil(crawl_delay 秒/60))。"""
    cfg = source.get("config") or {}
    try:
        interval = int(cfg.get("interval_minutes") or 360)
    except (TypeError, ValueError):
        interval = 360
    delay = (source.get("health") or {}).get("crawl_delay")
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay > 0:
        return max(interval, math.ceil(delay / 60))
    return interval


def is_due(source: dict[str, Any], *, now: datetime | None = None) -> bool:
    """due 判定：now−last_refresh ≥ 有效下限（含相等）；从未刷新 → due。

    last_refresh 缺失/不可解析 → due（保守：宁可早刷不可漏刷）。
    """
    last = (source.get("health") or {}).get("last_refresh")
    if not isinstance(last, str) or not last.strip():
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    now_dt = now or datetime.now()
    elapsed_minutes = (now_dt - last_dt).total_seconds() / 60.0
    return elapsed_minutes >= _effective_floor_minutes(source)


def tick(
    *,
    source_id: str | None = None,
    due_only: bool = True,
    dry_run: bool = False,
    articles_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """调度单趟（F68）：逐 rss 源判 due——dry-run 只出 [due]/[skip] 行
    （零网络零落盘），实跑对 due 源走 refresh_source（串行锁贯穿）。

    只管 rss 源（kb/radar 无轮询语义）；未到期源 health.last_refresh
    逐字节不动（due-only 缺省即安全态）。
    """
    out: list[dict[str, Any]] = []
    for source in sources_service.list_sources():
        if source.get("type") != "rss":
            continue
        if source_id is not None and source["id"] != source_id:
            continue
        if source.get("enabled") is not True:
            out.append({"source_id": source["id"], "action": "skip", "reason": "disabled"})
            continue
        if not due_only:
            out.append(refresh_source(source["id"], articles_root=articles_root))
            continue
        if is_due(source):
            if dry_run:
                out.append({
                    "source_id": source["id"],
                    "action": "due",
                    "interval_minutes": _effective_floor_minutes(source),
                    "last_refresh": (source.get("health") or {}).get("last_refresh"),
                })
            else:
                out.append(refresh_source(source["id"], articles_root=articles_root))
        else:
            out.append({
                "source_id": source["id"],
                "action": "skip",
                "reason": "未到",
                "interval_minutes": _effective_floor_minutes(source),
                "last_refresh": (source.get("health") or {}).get("last_refresh"),
            })
    return out
