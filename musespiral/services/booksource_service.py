"""书源域单一持有者（R0，L3）——订阅/导入/刷新/索引/引擎状态的全部编排。

三件套（§3.2）：
    ~/.musespiral/booksource_subs.json        订阅表
    ~/.musespiral/booksources/index.json      轻量投影（列表页不载规则大字段）
    ~/.musespiral/booksources/<sha1前16>.json 规范 JSON 原样（键名不可变）

三条编排纪律（本模块是唯一落点）：

  1. **预览零落库**：`import_preview` 只读不写（字节级断言见 tests）；
     落库只经 `import_apply`；
  2. **订阅刷新状态机**（前置分析 §3.3 RuleUpdate.cacheSource 同构）：
     间隔闸门 → **先落 last_fetch 时间戳（乐观）** → 拉取 → 按 url 三态合并
     → silent_update=true 直接落库（继承本地分组）/ false 进待确认缓存；
     拉取失败 fetch_state=failed + 可读错误，**乐观时间戳不回滚**
     （legado 同语义：无论后续成败都算拉取过）；
  3. **引擎缺席降级**：`engine_status()` 捕获 BookEngineUnavailable 返回
     `absent` 而非抛——订阅/导入/管理/书库全功能在无引擎时可用。

importKeep 偏好（keep_name/keep_group/keep_enable）由调用方按用户配置传入
（默认全 false = 全用导入值；customOrder 恒保留本地——core.merge 的硬语义）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from ..core.booksource import classifier, importer, merge, model
from ..infra import bookengine, booksource_fetch, booksource_store
from ..jobs.store import now_iso as _now_iso

# 订阅表/索引的读改写串行（Studio 线程 + CLI 同进程；文件级无锁跨进程亦安全
# ——atomic_write_bytes 的 os.replace 是原子替换）
_LOCK = threading.RLock()

# 待确认缓存（silent_update=false 的订阅刷新产物；进程内，键=订阅 url）
_PENDING: dict[str, list[dict[str, Any]]] = {}


# ── 索引/列表 ─────────────────────────────────────────────────────


def list_sources(*, group: str = "", q: str = "") -> list[dict[str, Any]]:
    """索引列表（可选分组/关键词过滤；只返回轻量行，不载规则大字段）。

    group 精确命中分组多值之一（legado hasGroup 语义）；q 对
    bookSourceName/bookSourceUrl 做大小写不敏感子串匹配。
    """
    rows = [r for r in (model.normalize_index_row(x) for x in booksource_store.load_index()) if r]
    if group.strip():
        want = group.strip()
        rows = [r for r in rows if want in _split_groups(r.get("group") or "")]
    if q.strip():
        needle = q.strip().lower()
        rows = [
            r for r in rows
            if needle in str(r.get("bookSourceName") or "").lower()
            or needle in str(r.get("bookSourceUrl") or "").lower()
        ]
    return rows


def _split_groups(raw: str) -> list[str]:
    return model.group_names({"bookSourceGroup": raw})


def get_source(url: str) -> dict[str, Any] | None:
    """单源详情（含规则组——编辑/查看用）；不存在 → None。"""
    if not isinstance(url, str) or not url.strip():
        return None
    return booksource_store.load_source(url.strip())


def _local_projection(url: str) -> dict[str, Any] | None:
    """本地源视图（**三态比较/local 摘要用**）：源文件优先，缺失退索引行。

    规范 JSON 是权威；索引只是投影。源文件缺失（脏索引）→ 退回索引行
    （仍能拿到 lastUpdateTime，避免误判 isNew 覆盖用户数据）。

    **合并偏好（importKeep/分组继承/customOrder 保留）不走本函数**——
    索引投影没有 bookSourceGroup/customOrder/enabledExplore 规范键，
    用投影行合并会把导入值洗成 None/0/True（复核轮 2026-11-18 收口；
    合并只认 `_local_full` 的全字段源）。
    """
    source = booksource_store.load_source(url)
    if source is not None:
        return source
    for row in booksource_store.load_index():
        normalized = model.normalize_index_row(row)
        if normalized and normalized["bookSourceUrl"] == url:
            return normalized
    return None


def _local_full(url: str) -> dict[str, Any] | None:
    """全字段本地源（规范 JSON 原样；缺失 → None）——合并偏好的唯一取数面。

    脏索引（源文件被删而索引残留）时返回 None：本地 customOrder/enabledExplore/
    规范分组已不可恢复，导入值原样落盘比洗成 None/0/True 更忠实。
    """
    if not isinstance(url, str) or not url.strip():
        return None
    return booksource_store.load_source(url.strip())


def _upsert_index(source: dict[str, Any], *, imported_at: str) -> None:
    """索引 upsert（同 url 覆盖，其余保留；原子写）。调用方持 _LOCK。"""
    rows = [r for r in (model.normalize_index_row(x) for x in booksource_store.load_index()) if r]
    url = source["bookSourceUrl"]
    rows = [r for r in rows if r["bookSourceUrl"] != url]
    rows.append(model.index_row(source, imported_at=imported_at))
    rows.sort(key=lambda r: (r.get("bookSourceName") or "", r["bookSourceUrl"]))
    booksource_store.save_index(rows)


# ── 导入（预览零落库 / apply 落库）─────────────────────────────────


def _candidates(
    text: str, fetcher: Callable[[str], str] | None
) -> dict[str, Any]:
    """导入解析（四分支+六类分类）并做本地三态标注——**只读**。

    返回 {kind, message, items, skipped}：非书源包 items/skipped 皆空，
    kind/message 给类型化边界说明（F-X1：主题/TTS/净化/排版可读提示）；
    bookSource 包 skipped=[{index, reason}] 脏源清单（逐条跳过不整批拒绝）。
    """
    result = importer.import_source(text, fetcher)
    if result["kind"] != classifier.KIND_BOOKSOURCE:
        return {
            "kind": result["kind"],
            "message": result["message"],
            "items": [],
            "skipped": [],
        }
    items: list[dict[str, Any]] = []
    for source in result["valid"]:
        url = source["bookSourceUrl"]
        local = _local_projection(url)
        status = merge.resolve_status(
            None if local is None else local.get("lastUpdateTime"),
            source.get("lastUpdateTime"),
        )
        items.append({
            "status": status,
            "selected": merge.default_selected(status),
            "source": source,
            "local": None if local is None else {
                "bookSourceName": local.get("bookSourceName") or "",
                "bookSourceGroup": local.get("bookSourceGroup") or "",
                "lastUpdateTime": local.get("lastUpdateTime") or 0,
            },
        })
    return {
        "kind": classifier.KIND_BOOKSOURCE,
        "message": "",
        "items": items,
        "skipped": result["skipped"],
    }


def _preview_full(
    text: str,
    *,
    fetcher: Callable[[str], str] | None = None,
    keep_name: bool = False,
    keep_group: bool = False,
    keep_enable: bool = False,
) -> dict[str, Any]:
    """全字段预览（**内部用**）：items[].source 为完整规范源（含规则大字段）。

    仅供 `import_apply` 取落库源——落库必须写全字段，不能吃 `import_preview`
    的轻量投影。任何对外的预览出口都走 `import_preview`。
    """
    fetch = fetcher if fetcher is not None else booksource_fetch.fetch
    parsed = _candidates(text, fetch)
    zero_counts = {s: 0 for s in merge.STATUSES}
    if parsed["kind"] != classifier.KIND_BOOKSOURCE:
        return {
            "ok": True,
            "kind": parsed["kind"],
            "message": parsed["message"],
            "items": [],
            "skipped": [],
            "counts": {**zero_counts, "dupUrls": 0},
        }
    items = parsed["items"]
    for item in items:
        # importKeep 只在全字段本地源上应用（_local_full——脏索引回退行缺
        # 规范键，拿投影行合并会洗值；local 摘要仍来自 _candidates 的投影）
        full = _local_full(item["source"]["bookSourceUrl"])
        if full is not None:
            item["source"] = merge.apply_keep(
                item["source"],
                full,
                keep_name=keep_name,
                keep_group=keep_group,
                keep_enable=keep_enable,
            )
    counts = {s: sum(1 for i in items if i["status"] == s) for s in merge.STATUSES}
    urls = [i["source"]["bookSourceUrl"] for i in items]
    counts["dupUrls"] = len(urls) - len(set(urls))
    return {
        "ok": True,
        "kind": classifier.KIND_BOOKSOURCE,
        "message": "",
        "items": items,
        "skipped": parsed["skipped"],
        "counts": counts,
    }


# ponytail: 设计 §3.3 承诺「轻量行 {url,name,group,status,selected}，不传规则大字段」。
# 保留嵌套 source 键（3 字段）而非拍平成 5 字段——全仓消费者都按 item["source"][...] 取，
# 拍平要改 CLI/JS/测试十余处且无收益；当消费者需要新增投影字段时在此加键即可。
_LIGHT_SOURCE_KEYS = ("bookSourceUrl", "bookSourceName", "bookSourceGroup")


def import_preview(
    text: str,
    *,
    fetcher: Callable[[str], str] | None = None,
    keep_name: bool = False,
    keep_group: bool = False,
    keep_enable: bool = False,
) -> dict[str, Any]:
    """导入预览（三态清单；**零落库**；**轻量投影**）。

    importKeep 在预览里就体现（用户看到的就是将要落库的形态）。
    返回 {ok, kind, message, items:[{status, selected, source:{url,name,group}, local}],
    skipped:[{index, reason}], counts:{isNew,isUpdate,latest,dupUrls}}。

    设计 §3.3 投影承诺：items[].source 只含 3 个展示字段（url/name/group），
    **不载规则大字段**（exploreUrl/ruleSearch/header…）——3906 行实测响应从
    20.8MB 降至约 1MB。投影发生在 apply_keep 之后，故 keep_name/keep_group
    的偏好仍反映在投影的 name/group 上。落库源走内部 `_preview_full`（全字段）。

    F-X1：非书源包 items/skipped 空清单 + kind/message 类型化说明（ok 仍为
    True——解析本身成功，给出的是可理解边界而非冷漠拒绝）。
    F-X3：counts.dupUrls = 包内同 URL 重复条数（total - 去重后条数）。
    """
    preview = _preview_full(
        text, fetcher=fetcher,
        keep_name=keep_name, keep_group=keep_group, keep_enable=keep_enable,
    )
    if preview["kind"] == classifier.KIND_BOOKSOURCE:
        for item in preview["items"]:
            source = item["source"]
            item["source"] = {k: source.get(k) or "" for k in _LIGHT_SOURCE_KEYS}
    return preview


def import_apply(
    text: str,
    selections: list[dict[str, Any]] | None = None,
    *,
    fetcher: Callable[[str], str] | None = None,
    keep_name: bool = False,
    keep_group: bool = False,
    keep_enable: bool = False,
) -> dict[str, Any]:
    """按选择落库（唯一写面）。

    selections：`[{"bookSourceUrl": ..., "selected": bool}]`；缺省=None 表示
    「按默认三态」（isNew/isUpdate 全落、latest 跳过）——UI 未显式选择时的
    安全默认。selections 里出现未知 url 忽略（前端可能基于旧预览）。

    F-X1：非书源包返回 {ok, kind, message, imported:[]} 类型化说明（零落库）。
    F-X3：返回增 {stored, deduped_from}——stored=去重后实际落库数（同 URL
    保留最后一条，与旧行为一致）、deduped_from=落库前的选择条数；同包重复
    URL 只写一次盘，消除「预览 3906 落库 2583」的预期落差。
    返回 {ok, kind, message, imported:[url...], skipped:[url...], stored, deduped_from}。
    """
    # 落库走全字段路径，不吃 import_preview 的轻量投影（投影缺规则字段，落库会丢数据）
    preview = _preview_full(
        text,
        fetcher=fetcher,
        keep_name=keep_name,
        keep_group=keep_group,
        keep_enable=keep_enable,
    )
    if preview["kind"] != classifier.KIND_BOOKSOURCE:
        return {
            "ok": True,
            "kind": preview["kind"],
            "message": preview["message"],
            "imported": [],
            "skipped": [],
            "stored": 0,
            "deduped_from": 0,
        }
    wanted: dict[str, bool] | None = None
    if selections is not None:
        if not isinstance(selections, list):
            raise ValueError("selections 必须是列表")
        wanted = {}
        for row in selections:
            if not isinstance(row, dict):
                raise ValueError("selections 每项必须是对象")
            url = row.get("bookSourceUrl") or row.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("selections 每项需要 bookSourceUrl")
            wanted[url.strip()] = row.get("selected") is not False

    skipped: list[str] = []
    occurrences = 0  # 落库前的选择条数（F-X3 deduped_from）
    by_url: dict[str, dict[str, Any]] = {}  # 同 URL 后见覆盖（与旧落库语义一致）
    for item in preview["items"]:
        source = item["source"]
        url = source["bookSourceUrl"]
        take = item["selected"] if wanted is None else wanted.get(url, False)
        if not take:
            skipped.append(url)
            continue
        occurrences += 1
        by_url[url] = source
    imported: list[str] = []
    stamp = _now_iso()
    with _LOCK:
        for url, source in by_url.items():
            booksource_store.save_source(source)
            _upsert_index(source, imported_at=stamp)
            imported.append(url)
    return {
        "ok": True,
        "kind": classifier.KIND_BOOKSOURCE,
        "message": "",
        "imported": imported,
        "skipped": skipped,
        "stored": len(imported),
        "deduped_from": occurrences,
    }


# ── 订阅 CRUD ────────────────────────────────────────────────────


def list_subs() -> list[dict[str, Any]]:
    """订阅清单（读盘直出）。"""
    return booksource_store.load_subs()


def add_sub(
    url: str,
    *,
    name: str = "",
    update_interval_h: int = 0,
    silent_update: bool = True,
    auto_update: bool = True,
) -> dict[str, Any]:
    """新增/更新订阅（同 url upsert：保留既有 last_fetch/fetch_state）。"""
    booksource_fetch.validate_url(url)  # https 门在注册期就执法（不留到刷新期）
    if not isinstance(update_interval_h, int) or isinstance(update_interval_h, bool) or update_interval_h < 0:
        raise ValueError("update_interval_h 必须是 ≥0 的整数（小时；0=不限）")
    with _LOCK:
        subs = booksource_store.load_subs()
        existing = next((s for s in subs if s["url"] == url.strip()), None)
        row = existing or {
            "id": url.strip(), "url": url.strip(), "name": "", "type": 0,
            "auto_update": True, "update_interval_h": 0, "silent_update": True,
            "last_fetch": None, "fetch_state": "pending", "error": "",
        }
        row = dict(row)
        if name.strip():
            row["name"] = name.strip()
        elif not row.get("name"):
            row["name"] = url.strip()
        row["auto_update"] = bool(auto_update)
        row["update_interval_h"] = update_interval_h
        row["silent_update"] = bool(silent_update)
        if existing is None:
            subs.append(row)
        else:
            subs = [row if s["url"] == row["url"] else s for s in subs]
        booksource_store.save_subs(subs)
    return row


def del_sub(url: str) -> dict[str, Any]:
    """删除订阅（不存在 → ValueError 可读错误）。"""
    target = url.strip()
    with _LOCK:
        subs = booksource_store.load_subs()
        rest = [s for s in subs if s["url"] != target]
        if len(rest) == len(subs):
            raise ValueError(f"订阅不存在: {target}")
        booksource_store.save_subs(rest)
    return {"ok": True, "url": target}


# ── 订阅刷新状态机（前置分析 §3.3 同构）────────────────────────────


def _interval_due(sub: dict[str, Any], now_ts: float) -> bool:
    """间隔闸门：last_fetch + interval_h*3600 > now → 未到刷新期（不刷）。"""
    interval = sub.get("update_interval_h") or 0
    if not interval:
        return True  # 0=不限，恒到期
    last = sub.get("last_fetch")
    if not isinstance(last, (int, float)):
        return True
    return last + interval * 3600 <= now_ts


def refresh_subscription(
    url: str,
    *,
    force: bool = False,
    fetcher: Callable[[str], str] | None = None,
    now: float | None = None,
    keep_name: bool = False,
    keep_group: bool = False,
    keep_enable: bool = False,
) -> dict[str, Any]:
    """刷新一条订阅（状态机全流程；返回刷新结果）。

    返回 {ok, url, action, ...}：
      action=skipped（间隔未到；**零落盘**）
      action=updated（silent 直接落库；imported/skipped 二清单）
      action=pending（非 silent；进待确认缓存，返回待确认清单）
      action=failed（拉取/解析失败；fetch_state=failed + error）

    乐观时间戳：过了闸门就先落 last_fetch（无论后续成败），失败不回滚。
    force=True 旁路间隔闸门（Studio 手动刷新的显式语义）。
    """
    target = url.strip()
    fetch = fetcher if fetcher is not None else booksource_fetch.fetch
    now_ts = float(now if now is not None else _now_ts())
    with _LOCK:
        subs = booksource_store.load_subs()
        sub = next((s for s in subs if s["url"] == target), None)
        if sub is None:
            raise ValueError(f"订阅不存在: {target}")
        if not force and not _interval_due(sub, now_ts):
            return {"ok": True, "url": target, "action": "skipped", "reason": "间隔未到"}
        # 乐观时间戳：先落（legado cacheSource 第 2 步）
        sub["last_fetch"] = now_ts
        booksource_store.save_subs(subs)

    # 网络拉取在锁外（慢 I/O 不占锁；并发刷新同 url 的语义由乐观戳保证不重复刷）
    try:
        text = fetch(target)
        parsed = _candidates(text, fetch)
    except Exception as err:
        _mark_sub(target, fetch_state="failed", error=str(err))
        return {"ok": False, "url": target, "action": "failed", "error": str(err)}
    items = parsed["items"]
    if parsed["kind"] != classifier.KIND_BOOKSOURCE or not items:
        _mark_sub(target, fetch_state="ok", error="")
        return {
            "ok": True, "url": target, "action": "skipped",
            "reason": parsed["message"] or "订阅无书源",
        }

    changed = [i for i in items if i["status"] != merge.STATUS_LATEST]
    if not sub.get("silent_update", True):
        _PENDING[target] = [i["source"] for i in changed]
        _mark_sub(target, fetch_state="pending", error="")
        return {
            "ok": True, "url": target, "action": "pending",
            "items": [{"status": i["status"], "source": i["source"]} for i in changed],
        }

    imported: list[str] = []
    stamp = _now_iso()
    with _LOCK:
        for item in changed:
            source = item["source"]
            local = _local_full(source["bookSourceUrl"])
            if local is not None:
                # 静默更新继承本地分组（legado：list.bookSourceGroup = local.bookSourceGroup）
                # ——同 importKeep 只认全字段源（脏索引回退行缺规范键不参与合并）
                group = local.get("bookSourceGroup")
                if isinstance(group, str) or group is None:
                    source["bookSourceGroup"] = group
                source = merge.apply_keep(
                    source, local,
                    keep_name=keep_name, keep_group=keep_group, keep_enable=keep_enable,
                )
            booksource_store.save_source(source)
            _upsert_index(source, imported_at=stamp)
            imported.append(source["bookSourceUrl"])
    _mark_sub(target, fetch_state="ok", error="")
    return {"ok": True, "url": target, "action": "updated", "imported": imported}


def _mark_sub(url: str, *, fetch_state: str, error: str) -> None:
    """回写订阅状态（fetch_state/error；不动 last_fetch——乐观戳已落）。"""
    with _LOCK:
        subs = booksource_store.load_subs()
        hit = False
        for sub in subs:
            if sub["url"] == url:
                sub["fetch_state"] = fetch_state
                sub["error"] = error
                hit = True
        if hit:
            booksource_store.save_subs(subs)


def _now_ts() -> float:
    """秒级时间戳（测试可 monkeypatch：状态机闸门纯时间比较）。"""
    import time

    return time.time()


def pending(url: str) -> list[dict[str, Any]]:
    """待确认清单（silent_update=false 的刷新产物；取走即清）。"""
    return list(_PENDING.pop(url.strip(), []))


def refresh_all(*, force: bool = False, fetcher: Callable[[str], str] | None = None) -> list[dict[str, Any]]:
    """全部订阅逐个刷新（串行；单条失败不影响后续）。"""
    out: list[dict[str, Any]] = []
    for sub in list_subs():
        if not force and not sub.get("auto_update", True):
            out.append({"ok": True, "url": sub["url"], "action": "skipped", "reason": "auto_update 关闭"})
            continue
        try:
            out.append(refresh_subscription(sub["url"], force=force, fetcher=fetcher))
        except Exception as err:  # 单条异常不阻塞整批（可读错误进结果）
            out.append({"ok": False, "url": sub["url"], "action": "failed", "error": str(err)})
    return out


# ── 引擎状态（灰灯数据源）─────────────────────────────────────────


def engine_status(*, client: bookengine.BookEngineClient | None = None) -> dict[str, Any]:
    """引擎状态：{ok, status: ok|absent, version?, base_url}。

    引擎缺席是**正常返回值**不是异常（§3.5 降级语义）：status=absent →
    前端灰灯「引擎未就绪——bookengine 服务未运行（R1-R2 建设中）」。
    """
    cli = client if client is not None else bookengine.BookEngineClient()
    payload = cli.health()
    if payload.get("ok") is True:
        return {
            "ok": True,
            "status": "ok",
            "base_url": bookengine.BASE_URL,
            "version": payload.get("version") or "",
            "sources_loaded": payload.get("sources_loaded") or 0,
        }
    return {
        "ok": True,
        "status": "absent",
        "base_url": bookengine.BASE_URL,
        "error": payload.get("error") or "bookengine 未运行",
    }
