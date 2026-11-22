"""写作统计（纯读；FEAT-P1B 写作节奏仪表盘数据面）。

数据来源与口径（**真实、不虚报**）：
  - ``<root>/*.compose/session.json`` 的 ``rounds`` 块记录 = 每轮 AI 产出的正文；
  - 轮次归属日期取 ``rounds/<n>.md`` 的 mtime（生成即落盘，mtime≈产出时刻）；
  - 单会话按轮次升序求「候选稿字数增量」：``delta_n = max(0, chars_n - chars_{n-1})``
    ——同批继承块不重复计数（rounds 里大量块是上一轮的锚继承，直接累加会虚报）。

轮次文件缺失（无日期证据）的轮次跳过，不按创建日臆造。无会话 / 无轮次 → 全零，
不抛错（前端空态引导由此判定）。本模块纯读零写。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

SESSION_SUFFIX = ".compose"
ROUNDS_DIR = "rounds"
META_FILE = "session.json"
DEFAULT_DAYS = 30
DEFAULT_WEEKLY_TARGET = 5000

_WS_RE = re.compile(r"\s+")


def _chars(text: str) -> int:
    """与 compose.summary.article_chars 同口径（去空白字符数）——全库字数一致。"""
    return len(_WS_RE.sub("", text or ""))


def _today(value: date | str | None = None) -> date:
    """归一到 date；None = 今天（测试可注入固定日，保证跨日边界可断言）。"""
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _session_dirs(root: str | Path | None) -> list[Path]:
    from .discover_service import DEFAULT_ARTICLES_ROOT  # 单一权威（与 compose 同根约定）

    base = (Path(root).expanduser() if root is not None else DEFAULT_ARTICLES_ROOT)
    if not base.is_dir():
        return []
    return sorted(d for d in base.glob(f"*{SESSION_SUFFIX}") if d.is_dir())


def _load_rounds(d: Path) -> list[dict]:
    """宽容读 session.json 的 rounds（损坏/缺失 → []，不塌读面）。"""
    try:
        meta = json.loads((d / META_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rounds = meta.get("rounds") if isinstance(meta, dict) else None
    return [r for r in (rounds or []) if isinstance(r, dict)]


def _round_chars(r: dict) -> int:
    blocks = r.get("blocks") or []
    return sum(_chars(str(b.get("text") or "")) for b in blocks if isinstance(b, dict))


def _session_timeline(d: Path, slug: str) -> list[tuple[date, str, int]]:
    """单会话增量时间线：[(日期, slug, 字数增量)]；无证据的轮次跳过。"""
    rounds = sorted(_load_rounds(d), key=lambda r: int(r.get("n") or 0))
    out: list[tuple[date, str, int]] = []
    prev = 0
    for r in rounds:
        try:
            n = int(r.get("n") or 0)
        except (TypeError, ValueError):
            continue
        path = d / ROUNDS_DIR / f"{n}.md"
        if n <= 0 or not path.is_file():
            continue
        chars = _round_chars(r)
        delta = chars - prev
        prev = chars
        if delta <= 0:
            continue
        try:
            day = datetime.fromtimestamp(path.stat().st_mtime).date()
        except OSError:
            continue
        out.append((day, slug, delta))
    return out


def _entries(root: str | Path | None) -> list[tuple[date, str, int]]:
    entries: list[tuple[date, str, int]] = []
    for d in _session_dirs(root):
        entries.extend(_session_timeline(d, d.name[: -len(SESSION_SUFFIX)]))
    return entries


def daily_stats(
    root: str | Path | None = None, days: int = DEFAULT_DAYS, *, today: date | str | None = None
) -> list[dict]:
    """近 N 天（含今天）每日增量：[{date, chars_added, sessions}]，按日期升序。"""
    entries = _entries(root)
    end = _today(today)
    start = end - timedelta(days=max(days, 1) - 1)
    sums: dict[date, int] = {}
    slugs: dict[date, set[str]] = {}
    for day, slug, delta in entries:
        if not (start <= day <= end):
            continue
        sums[day] = sums.get(day, 0) + delta
        slugs.setdefault(day, set()).add(slug)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "chars_added": sums.get(start + timedelta(days=i), 0),
            "sessions": len(slugs.get(start + timedelta(days=i), ())),
        }
        for i in range((end - start).days + 1)
    ]


def _active_days(entries: list[tuple[date, str, int]]) -> set[date]:
    return {day for day, _slug, delta in entries if delta > 0}


def streak(
    root: str | Path | None = None, *, today: date | str | None = None
) -> dict:
    """连续写作天数 {current, longest}。

    current＝自今天回溯的连续活跃日；今天尚无记录不算断（从昨天起算，常见
    「今天还没写但连胜未破」语义）。longest＝历史最长连续段。
    """
    active = _active_days(_entries(root))
    end = _today(today)
    cur = 0
    day = end if end in active else end - timedelta(days=1)
    while day in active:
        cur += 1
        day -= timedelta(days=1)
    longest = 0
    run = 0
    prev: date | None = None
    for day in sorted(active):
        run = run + 1 if prev is not None and day == prev + timedelta(days=1) else 1
        longest = max(longest, run)
        prev = day
    return {"current": cur, "longest": longest}


def weekly_goal(
    root: str | Path | None = None,
    target: int = DEFAULT_WEEKLY_TARGET,
    *,
    today: date | str | None = None,
) -> dict:
    """本周（周一→今天）字数 vs 目标：{written, target, pct}。

    pct 为真实百分比（可 >100），进度条宽度由前端 clamp；target<=0 时 pct=0。
    """
    end = _today(today)
    start = end - timedelta(days=end.weekday())
    written = sum(delta for day, _slug, delta in _entries(root) if start <= day <= end)
    target = max(int(target), 0)
    pct = 0 if target <= 0 else round(written / target * 100)
    return {"written": written, "target": target, "pct": pct}


def dashboard(
    root: str | Path | None = None,
    *,
    days: int = DEFAULT_DAYS,
    target: int = DEFAULT_WEEKLY_TARGET,
    today: date | str | None = None,
) -> dict:
    """读面聚合（GET /api/writing_stats 单请求）：daily + streak + weekly + empty。"""
    daily = daily_stats(root, days, today=today)
    return {
        "daily": daily,
        "streak": streak(root, today=today),
        "weekly": weekly_goal(root, target, today=today),
        "empty": not any(x["chars_added"] for x in daily),
    }
