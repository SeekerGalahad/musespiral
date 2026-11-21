"""选题雷达服务（Phase D F78，L3）——快照唯一持有者 + 三源装配 + AI 增强。

分层（设计 §二 F78.3/§四 Q1）：聚合是 core/discovery/topic_radar.py 的 L2
纯函数；本层只做 **I/O 装配与快照持有**：

- 三源装配：radar 腿=materials 已入库 radar_signal（enabled，meta.grade/
  score——**不直连 radar-hub**，F78 红线）；kb 腿=KB 根扫描（scanner+scorer
  现算，mtime 30 天窗）；star 腿=triage.starred 素材。hub 宕机=down_sources
  注记（sources.json health 快照只读，不发网络探测）+KB/star 腿照跑。
- 已发清单（90 天窗）：publish_log 双日志合并行 + 正稿 frontmatter 标题
  （status=published）——排除的对照面。
- 快照目录 ~/.musespiral/topic_radar/（§1.4）：latest.json（读面恒指此）+
  <YYYYMMDD-HHMMSS>.json 历史（保留 HISTORY_KEEP=20 份，超限删最旧——
  显式删非追加态，本模块唯一持有者）。
- AI 增强（可选，默认开）：top5 保留候选逐项走 gateway
  （chat_policed(task="discover_suggest", cache=True)——§八-3 雷达增强恒
  缓存）；单项失败（AIError 族含 BudgetExceeded）跳过不中断，只计
  ai_enhanced_n。形态复用 suggester.ai_enhance（逐项吞错+计数）。
- jobs（kind=topic_radar）：result={snapshot_path, candidates_n,
  ai_enhanced_n}——**计数无正文**（候选表正文只在快照文件，refine 判例
  延伸）。

确定性：候选行逐字节确定（纯函数保证）；快照只增不改、候选**不回写**
discovered/materials（雷达自身零写面——后续动作走既有端点）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..core.content.parser import parse_article
from ..core.discovery import scanner, scorer, topic_radar
from ..infra.ai import AIError, ai_configured
from ..infra.ai.gateway import chat_policed
from ..infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ..jobs.store import now_iso
from . import materials_service, publish_log_service, sources_service

# 快照目录与文件（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移）
SNAPSHOT_DIR = "topic_radar"
LATEST_FILE = "latest.json"

# 历史快照保留份数（§1.4：保留 20 份超限删最旧，latest.json 不计入）
HISTORY_KEEP = 20

# KB 腿新鲜度窗口（天）：文档 mtime 距快照时刻 ≤30 天才算新鲜资产（F78.3）
KB_WINDOW_DAYS = 30

# 已发清单窗口（天）：publish_log 行与正稿 mtime 距快照时刻 ≤90 天（F78.3）
PUBLISH_WINDOW_DAYS = 90

# AI 增强条数上限（top5；契约 candidates[].ai_angle?）
AI_ENHANCE_TOP_N = 5

ChatFn = Callable[..., dict[str, Any]]


# ── 路径与读面 ───────────────────────────────────────────────────────


def snapshot_root() -> Path:
    """快照目录根：config.home_dir()/topic_radar（每次现读）。"""
    return config.home_dir() / SNAPSHOT_DIR


def latest_path() -> Path:
    """latest.json 路径（读面恒指此）。"""
    return snapshot_root() / LATEST_FILE


def latest() -> dict[str, Any] | None:
    """读最近一次快照；无快照/坏文件 → None（读面空态，不 404 不 500）。"""
    path = latest_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ── 三源装配（I/O 面）───────────────────────────────────────────────


def _radar_items() -> list[dict[str, Any]]:
    """radar 腿：已入库 radar_signal 活跃素材（enabled；不直连 hub）。

    分值/评级取 payload.meta（采集时落的 grade/score，F54/F55 四键契约），
    url 同源 meta.url。按 material id 排序（确定性——同库两次装配同序）。
    """
    items: list[dict[str, Any]] = []
    for m in materials_service.query(mtype="radar_signal", enabled=True):
        meta = (m.get("payload") or {}).get("meta") or {}
        try:
            score = int(meta.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        items.append({
            "material_id": str(m.get("id") or ""),
            "title": str((m.get("origin") or {}).get("title") or m.get("id") or ""),
            "score": score,
            "grade": str(meta.get("grade") or ""),
            "url": str(meta.get("url") or ""),
        })
    items.sort(key=lambda it: it["material_id"])
    return items


def _kb_items(now: datetime) -> list[dict[str, Any]]:
    """kb 腿：KB 根扫描（scanner+scorer 现算），mtime ≤30 天的新鲜 .md。

    根未配置/不存在 → 空腿（KB 腿缺失不是错误，照常出快照）；单篇
    损坏/OSError 跳过不中断（scanner.scan_dir 同纪律）。按路径排序。
    """
    root = sources_service.kb_root()
    if root is None or not root.is_dir():
        return []
    families = None
    try:
        from . import discover_service  # 函数体内导入（层内解耦，tag_service 同款）

        families = discover_service.load_family_titles(root)
    except Exception:
        families = None  # 词典缺失/损坏 → 该维 0 分（F27 契约）
    cutoff = now.timestamp() - KB_WINDOW_DAYS * 86400
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        try:
            if path.stat().st_mtime < cutoff:
                continue  # 30 天窗外的旧文档不进雷达
            meta = scanner.scan_file(path)
        except OSError:
            continue
        scored = scorer.score_doc(meta, families=families)
        items.append({"path": str(path), "title": str(meta.get("title") or ""), "score": int(scored["score"])})
    return items


def _star_items() -> list[dict[str, Any]]:
    """star 腿：triage.starred 活跃素材（人工意向；enabled）。按 id 排序。"""
    items: list[dict[str, Any]] = []
    for m in materials_service.query(starred=True, enabled=True):
        meta = (m.get("payload") or {}).get("meta") or {}
        items.append({
            "material_id": str(m.get("id") or ""),
            "title": str((m.get("origin") or {}).get("title") or m.get("id") or ""),
            "url": str(meta.get("url") or (m.get("payload") or {}).get("url") or ""),
        })
    items.sort(key=lambda it: it["material_id"])
    return items


def _down_sources() -> list[str]:
    """宕机源注记（灰灯语义）：radar_hub 类源 health.status=down 的 id 集合。

    只读 sources.json 的**既有健康快照**（探测/刷新时回写）——本函数不发
    任何网络请求（F78 红线：不直连 hub）。排序去重（确定性）。
    """
    out: set[str] = set()
    for source in sources_service.list_sources():
        if source.get("type") != "radar_hub":
            continue
        if (source.get("health") or {}).get("status") == "down":
            sid = str(source.get("id") or "")
            if sid:
                out.add(sid)
    return sorted(out)


def _published_titles(root: Path, now: datetime) -> list[str]:
    """已发标题清单（90 天窗）：publish_log 行 + 正稿 frontmatter 标题。

    - publish_log（双日志合并、demo 行滤除——publish_log_service 既有纪律）：
      ts 落窗内的行取 title（人工登记行）或 article（文件名）；
    - 正稿：文章根 *.md（不递归）frontmatter status=published 且 mtime 落窗
      内 → frontmatter title；解析失败跳过。
    去重保序（发布日志行序在前、正稿标题在后——确定性）。
    """
    cutoff = now.timestamp() - PUBLISH_WINDOW_DAYS * 86400
    out: list[str] = []

    def _in_window(ts: str) -> bool:
        try:
            stamp = datetime.fromisoformat(str(ts))
        except ValueError:
            return False
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone()
        return cutoff <= stamp.timestamp() <= now.timestamp() + 1

    for row in publish_log_service.merge_logs(root):
        if not _in_window(str(row.get("ts") or "")):
            continue
        title = str(row.get("title") or row.get("article") or "").strip()
        if title and title not in out:
            out.append(title)
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                article = parse_article(path)
            except Exception:
                continue  # 无法解析的 md 不是文章（payload._articles_payload 同纪律）
            if str(article.metadata.get("status") or "") != "published":
                continue
            title = str(article.metadata.get("title") or "").strip()
            if title and title not in out:
                out.append(title)
    return out


# ── AI 增强（可选；复用 suggester.ai_enhance 形态走 gateway）──────────

# 增强模板（prompt 组装点——落 F45 方言守门扫描表，tests/test_architecture.py
# DIALECT_SCAN_FILES 本文件条目；title/reasons 为逐候选内容插槽）
AI_ENHANCE_TEMPLATE = (
    "基于以下选题候选的标题与理由，给出一个具体的写作角度（一句话）。\n"
    "标题：{title}\n"
    "理由：{reasons}\n"
    "输出一行角度文本，不加编号、不加引号。"
)


def _ai_enhance(rows: list[dict[str, Any]], *, use_ai: bool, chat_fn: ChatFn | None = None) -> int:
    """top5 保留候选逐项增强（写 ai_angle），返回成功数。

    - use_ai=False（CLI --no-ai）或 AI 未配置 → 0（静默，非错误）；
    - 单项失败（AIError 族——含 BudgetExceeded）跳过不中断；
    - 响应取首个非空行为角度文本（空响应视为该单项失败）。
    """
    if not use_ai or not ai_configured("discover_suggest"):
        return 0
    kept = [r for r in rows if not r.get("excluded")][:AI_ENHANCE_TOP_N]
    enhanced = 0
    for row in kept:
        prompt = AI_ENHANCE_TEMPLATE.format(
            title=row.get("title"),
            reasons="；".join(str(x) for x in (row.get("reasons") or [])),
        )
        try:
            res = chat_policed(
                [{"role": "user", "content": prompt}],
                task="discover_suggest",
                cache=True,  # §八-3：雷达增强恒缓存——同候选重跑零网络
                template=AI_ENHANCE_TEMPLATE,  # F79.1：生效模板进 template_sha
                chat_fn=chat_fn,
            )
        except AIError:
            continue
        lines = [ln.strip() for ln in str(res.get("text") or "").splitlines() if ln.strip()]
        if not lines:
            continue
        row["ai_angle"] = lines[0]
        enhanced += 1
    return enhanced


# ── 快照生成与持有（唯一写面）───────────────────────────────────────


def _stamp_slug(stamp: str) -> str:
    """iso 时间戳 → 历史文件名干 <YYYYMMDD-HHMMSS>（解析失败回退当前时刻）。"""
    try:
        return datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y%m%d-%H%M%S")
    except ValueError:
        return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")


def _trim_history() -> int:
    """历史快照裁剪：>HISTORY_KEEP 时按文件名（即时刻序）删最旧，返回删除数。"""
    root = snapshot_root()
    history = sorted(p for p in root.glob("*.json") if p.name != LATEST_FILE)
    removed = 0
    while len(history) > HISTORY_KEEP:
        oldest = history.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass  # 删不掉不炸快照主流程（下轮裁剪重试）
        removed += 1
    return removed


def run_snapshot(
    *,
    articles_root: str | Path | None = None,
    use_ai: bool = True,
    chat_fn: ChatFn | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """计算并持久化一份快照（latest.json 覆盖 + 历史追加），返回快照 dict。

    now：iso 时间戳注入点（缺省 jobs.store.now_iso 同口径现取；测试传定值
    断言历史文件名与字节确定性）。候选**不回写** discovered/materials
    （雷达自身零写面，F78 红线）。
    """
    stamp = now if now is not None else now_iso()
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    root = Path(articles_root).expanduser() if articles_root is not None else _default_articles_root()
    radar = _radar_items()
    kb = _kb_items(moment)
    stars = _star_items()
    published = _published_titles(root, moment)
    rows = topic_radar.candidates(radar, kb, stars, published)
    ai_enhanced_n = _ai_enhance(rows, use_ai=use_ai, chat_fn=chat_fn)
    snapshot: dict[str, Any] = {
        "generated_at": stamp,
        "inputs": {"radar_n": len(radar), "kb_n": len(kb), "down_sources": _down_sources()},
        "candidates": rows,
        "ai_enhanced_n": ai_enhanced_n,
    }
    directory = snapshot_root()
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    _atomic_write(directory / f"{_stamp_slug(stamp)}.json", payload)
    _atomic_write(latest_path(), payload)
    _trim_history()
    return snapshot


def _default_articles_root() -> Path:
    """文章根缺省（discover_service.DEFAULT_ARTICLES_ROOT 同款约定——薄壳
    避免导入期耦合 discover_service 全量）。"""
    return Path.home() / "musespiral-articles"


# ── jobs（kind=topic_radar；result 计数无正文）──────────────────────


def job_fn(
    *,
    articles_root: str | Path | None = None,
    use_ai: bool = True,
    chat_fn: ChatFn | None = None,
) -> Callable[[str], dict[str, Any]]:
    """把一次快照重算包成 jobs 闭包 → result={snapshot_path, candidates_n,
    ai_enhanced_n}（计数无正文；候选表正文只在快照文件）。"""

    def _run(_job_id: str) -> dict[str, Any]:
        snapshot = run_snapshot(
            articles_root=articles_root, use_ai=use_ai, chat_fn=chat_fn
        )
        return {
            "snapshot_path": str(latest_path()),
            "candidates_n": len(snapshot.get("candidates") or []),
            "ai_enhanced_n": int(snapshot.get("ai_enhanced_n") or 0),
        }

    return _run


def submit_to_runner(
    runner: Any,
    *,
    articles_root: str | Path | None = None,
    use_ai: bool = True,
    chat_fn: ChatFn | None = None,
) -> str:
    """提交一次快照重算 job（kind=topic_radar），返回 job_id。"""
    return runner.submit(
        "topic_radar",
        {"use_ai": bool(use_ai)},
        job_fn(articles_root=articles_root, use_ai=use_ai, chat_fn=chat_fn),
    )


__all__ = [
    "AI_ENHANCE_TOP_N",
    "HISTORY_KEEP",
    "KB_WINDOW_DAYS",
    "LATEST_FILE",
    "PUBLISH_WINDOW_DAYS",
    "SNAPSHOT_DIR",
    "job_fn",
    "latest",
    "latest_path",
    "run_snapshot",
    "snapshot_root",
    "submit_to_runner",
]
