"""排期周历编排（第十四期 F36/F36c，正典 §二/§三）。

数据源三路合流（正典 §3.3）：
  publish_at(frontmatter) ─ 计划（写入靠人，本模块只读侧消费）
  publish_log(root+home) ── 已交付（action=draft 或 result=uploaded）/
                            已发布（action=publish，含 manual 登记）
  文章状态机 status ─────── 尾行「在制品」（draft+reviewed 计数）

三态口径（两组共同约束）：
  计划   = frontmatter publish_at 命中日
  已交付 = action=='draft'（API 建草稿）或 result=='uploaded'（浏览器交付）
  已发布 = action=='publish'（含 manual:true 人工登记）

读侧全部宽容：publish_at 缺失/非法/过期一律容忍（过期=如实显示不报错），
坏 frontmatter 文章跳过不崩，日志坏行由 publish_log_service 兆过滤。

依赖纪律（正典 §3.2）：publish_log_service + core/content/parser +
core/adaptation 与 platforms 的平台码集（衍生文件剔除用）——不 import
studio；本模块只读（`musespiral plan` 只读不改）。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..core.adaptation import PLATFORM_SPECS
from ..core.content.parser import parse_article
from ..platforms import registry
from . import publish_log_service
from .discover_service import DEFAULT_ARTICLES_ROOT

# 周序入参形态：2026-W47（正典 §二 F36）
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
# ts 里的日期前缀（YYYY-MM-DD）；不匹配的行无处落日，跳过（宽容）
_TS_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# publish_at 字符串形态的日期前缀
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")



def known_platform_codes() -> frozenset[str]:
    """可登记/可交付的平台码全集：registry 注册平台 ∪ adapt 变体平台。

    `log add --platform` 的校验集与衍生文件剔除集共用同一事实源，防两处
    各维护一份清单漂移。
    """
    return frozenset(p.name for p in registry.list_platforms()) | frozenset(PLATFORM_SPECS)


# 衍生文件名后缀（与 studio 队列剔除口径同源：配对笔记/批注预览/平台变体）
_DERIVED_SUFFIXES: tuple[str, ...] = (".note.md", ".annotated.md") + tuple(
    f".{code}.md" for code in sorted(known_platform_codes())
)


def _resolve_root(root: str | Path | None) -> Path:
    """articles root：显式传入优先，缺省 DEFAULT_ARTICLES_ROOT（测试可 patch）。"""
    return Path(root).expanduser() if root is not None else DEFAULT_ARTICLES_ROOT


def _iter_articles(root: Path) -> list[Path]:
    """root 下候选文章（隐藏/物料包/衍生文件/越界符号链接已剔除）。

    与 Studio 队列同口径的剔除规则在本层独立实现（services 不 import
    studio）；root 不存在返回空。musespiral frontmatter 判定（title/cover 至少
    一项）由调用侧 parse 后裁决——本函数只管文件名与边界。
    """
    if not root.is_dir():
        return []
    files: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue  # 隐藏文件/目录（含 .musespiral 运行目录）
        if any(part.endswith(".pack") for part in parts[:-1]):
            continue  # 物料包内的 README/payload 不是文章
        if path.name.endswith(_DERIVED_SUFFIXES):
            continue  # 配对笔记 / 批注预览 / 平台变体
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue  # 越界符号链接等
        if resolved in seen:
            continue
        seen.add(resolved)
        files.append(resolved)
    return files


def _publish_at_date(metadata: dict[str, Any]) -> date | None:
    """frontmatter publish_at → date；缺失/非法返回 None（读侧宽松）。

    YAML 会把裸日期解析成 date/datetime 对象，人工手写则可能是字符串——
    两种形态都收（字符串取 YYYY-MM-DD 前缀），其余一律当缺失。
    """
    raw = metadata.get("publish_at")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        m = _DATE_RE.match(raw.strip())
        if m:
            try:
                return date.fromisoformat(m.group(1))
            except ValueError:
                return None
    return None


def _row_date(row: dict[str, Any]) -> date | None:
    """日志行 ts → date；无法定位日期的行返回 None（跳过不崩）。"""
    m = _TS_DATE_RE.match(str(row.get("ts") or ""))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _row_title(row: dict[str, Any]) -> str:
    """行展示标题：title 缺失（型一 uploaded 行无 title）回落文件名。"""
    title = str(row.get("title") or "").strip()
    if title:
        return title
    return Path(str(row.get("article") or "?")).name


def _week_days(spec: str | None, today: date) -> tuple[int, int, list[date]]:
    """周序 → (year, week, 周一~周日七日)。spec None 取 today 所在周。

    非法 spec（格式错/周序越界）抛 ValueError，由表现层转可读错误。
    """
    if spec is None:
        year, week = today.isocalendar()[:2]
    else:
        m = _WEEK_RE.match(spec.strip())
        if not m:
            raise ValueError(f"周序格式应为 YYYY-Www（如 2026-W47）: {spec!r}")
        year, week = int(m.group(1)), int(m.group(2))
        if not 1 <= week <= 53:
            raise ValueError(f"周序越界: {spec!r}")
    try:
        days = [date.fromisocalendar(year, week, i) for i in range(1, 8)]
    except ValueError as e:  # 如 2026-W53 不存在 / 平年 W00 之外的残余形态
        raise ValueError(f"非法周序: {spec!r}（{e}）") from e
    return year, week, days


def render_week(
    root: str | Path | None = None,
    *,
    week: str | None = None,
    today: date | None = None,
) -> str:
    """渲染文字周历（周一~周日逐日 + 尾行汇总）；`musespiral plan` 的数据面。

    week=None 取 today 所在周（today 缺省 date.today()，测试可注入）；
    --week 回看传 "2026-W47" 形态。只读不改：双日志经 publish_log_service
    合并（root 优先+去重+demo 过滤），全文章 frontmatter 收集 publish_at。
    """
    today = today or date.today()
    year, week, days = _week_days(week, today)
    arts_root = _resolve_root(root)
    in_week = {d: i for i, d in enumerate(days)}

    # 计划事件 + 在制品（同趟扫描，坏 frontmatter 文章跳过不崩）
    plans: dict[date, list[tuple[str, str]]] = {}
    wip = 0
    for path in _iter_articles(arts_root):
        try:
            meta = parse_article(path).metadata
        except Exception:
            continue  # 坏 frontmatter / 读失败：宽容跳过
        if not (meta.get("title") or meta.get("cover")):
            continue  # 非 musespiral 文章（与 Studio 队列同口径）
        if str(meta.get("status") or "draft") in ("draft", "reviewed"):
            wip += 1
        pad = _publish_at_date(meta)
        if pad is not None and pad in in_week:
            plans.setdefault(pad, []).append(
                (str(meta.get("title") or path.stem), path.name)
            )

    # 日志事件：已交付（draft|uploaded）/ 已发布（publish 含 manual）
    delivered: dict[date, list[tuple[str, str, str]]] = {}
    published: dict[date, list[tuple[str, str, str]]] = {}
    n_delivered = n_published = 0
    for row in publish_log_service.merge_logs(arts_root):
        d = _row_date(row)
        if d is None or d not in in_week:
            continue
        platform = str(row.get("platform") or "mp")
        title = _row_title(row)
        if row.get("action") == "publish":
            published.setdefault(d, []).append((platform, title, str(row.get("ts") or "")))
            n_published += 1
        elif row.get("result") == "uploaded" or row.get("action") == "draft":
            delivered.setdefault(d, []).append((platform, title, str(row.get("ts") or "")))
            n_delivered += 1

    # 逐日渲染：计划（文件名序）→ 已交付（ts 序）→ 已发布（ts 序）；
    # 空工作日标空窗（N=该日至周五仍无事件的工作日数），空周末日画 —
    lines = [
        f"周历 {year}-W{week:02d}（{days[0].strftime('%m-%d')} ~ {days[-1].strftime('%m-%d')}）"
    ]
    empty_workdays_left = [0] * 7  # 自后向前累计：下标 i = 第 i 天起（含）的空工作日数
    running = 0
    for i in range(6, -1, -1):
        if i <= 4:  # 周一~周五（0~4）为工作日
            has_event = days[i] in plans or days[i] in delivered or days[i] in published
            running = 0 if has_event else running + 1
        empty_workdays_left[i] = running

    for i, d in enumerate(days):
        head = f"{d.strftime('%m-%d')} {_WEEKDAYS[i]} ·"
        events: list[str] = []
        for title, filename in sorted(plans.get(d, []), key=lambda t: t[1]):
            events.append(f"计划: {title}（{filename}）")
        for platform, title, _ in sorted(delivered.get(d, []), key=lambda e: e[2]):
            events.append(f"已交付: {platform} {title}")
        for platform, title, _ in sorted(published.get(d, []), key=lambda e: e[2]):
            events.append(f"已发布: {platform} {title}")
        if events:
            lines.extend(f"{head} {e}" for e in events)
        elif i <= 4:
            lines.append(f"{head} （空窗——本周还差 {empty_workdays_left[i]} 篇）")
        else:
            lines.append(f"{head} —")

    lines.append(
        f"本周 计划 {sum(len(v) for v in plans.values())} / "
        f"已交付 {n_delivered} / 已发布 {n_published}；在制品（draft+reviewed）{wip}"
    )
    return "\n".join(lines)


def _article_label(arts_root: Path, raw: str) -> str:
    """日志行 article 字段 → root 相对 posix（CLI/Studio 写绝对、手动登记写
    相对——三种口径归一；越界/不可解析原样保留，宽容不炸）。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(arts_root.resolve()).as_posix()
    except (OSError, ValueError):
        return p.as_posix()


def plan_items(
    root: str | Path | None = None,
    *,
    days: int = 30,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """排期条目清单（P4 排期周历数据面；契约 §3.2-2，与 render_week 同源三态）。

    items 逐条：{article(root 相对 posix), title, status ∈ planned|delivered|
    published, publish_at("YYYY-MM-DD"|None), date(事件日 "YYYY-MM-DD")}，
    按 date 升序（同日按 article 序）。三态口径与本模块头注一致：

      planned   = frontmatter publish_at 命中日（写入靠人，本模块只读消费）
      delivered = 日志 action=='draft' 或 result=='uploaded'
      published = 日志 action=='publish'（含 manual:true 人工登记）

    窗口语义（契约 §3.2-2 只预定 range=30|90 未定窗口，此为实施选择）：
    保留 |date - today| < days 的条目——对称看待未来计划与近期交付/发布
    （range=30 → 今天前后各 29 天；range=90 → 季度量级）。读侧宽容：坏
    frontmatter 文章、无日期日志行跳过，不抛错（demo 行由 merge_logs 滤除）。
    """
    today = today or date.today()
    base = today.toordinal()
    lo, hi = date.fromordinal(base - (days - 1)), date.fromordinal(base + (days - 1))
    arts_root = _resolve_root(root)
    items: list[dict[str, Any]] = []

    # 计划事件（与 render_week 同一趟文章扫描口径）
    for path in _iter_articles(arts_root):
        try:
            meta = parse_article(path).metadata
        except Exception:
            continue  # 坏 frontmatter / 读失败：宽容跳过
        if not (meta.get("title") or meta.get("cover")):
            continue  # 非 musespiral 文章（与 Studio 队列同口径）
        pad = _publish_at_date(meta)
        if pad is None or not (lo <= pad <= hi):
            continue
        items.append(
            {
                "article": _article_label(arts_root, str(path)),
                "title": str(meta.get("title") or path.stem),
                "status": "planned",
                "publish_at": pad.isoformat(),
                "date": pad.isoformat(),
            }
        )

    # 日志事件：已交付（draft|uploaded）/ 已发布（publish 含 manual）
    for row in publish_log_service.merge_logs(arts_root):
        d = _row_date(row)
        if d is None or not (lo <= d <= hi):
            continue
        if row.get("action") == "publish":
            status = "published"
        elif row.get("result") == "uploaded" or row.get("action") == "draft":
            status = "delivered"
        else:
            continue
        items.append(
            {
                "article": _article_label(arts_root, str(row.get("article") or "")),
                "title": _row_title(row),
                "status": status,
                "publish_at": None,
                "date": d.isoformat(),
            }
        )

    items.sort(key=lambda it: (it["date"], it["article"]))
    return items


def register_manual_publish(
    article: str,
    platform: str,
    *,
    title: str | None = None,
    url: str | None = None,
    root: str | Path | None = None,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """F36c 人工发布登记的编排：article 校验 → title 兜底 → 追加 manual 行。

    article 按 root 相对路径或 root 内绝对路径解析，**须存在于 root**
    （ValueError 可读错误）；落日志的 article 字段统一写 root 相对 POSIX
    路径（与真实日志/Studio 匹配口径一致）。title 缺省读 frontmatter
    title，再缺回落文件名 stem。返回落盘行。mp 人工后台点完发表后跑
    这条，周历与 stats 的「已发布」即归位。
    """
    arts_root = _resolve_root(root)
    raw = Path(article).expanduser()
    path = raw if raw.is_absolute() else arts_root / raw
    if not path.is_file():
        raise ValueError(f"文章不存在于 root: {article}（root={arts_root}）")
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(arts_root.resolve())
    except ValueError:
        raise ValueError(f"文章不在 root 内: {article}（root={arts_root}）") from None
    if title is None:
        try:
            title = str(parse_article(resolved).metadata.get("title") or "").strip()
        except Exception:
            title = ""
        title = title or resolved.stem
    return publish_log_service.append_manual(
        rel.as_posix(), platform, title, url=url, log_path=log_path
    )
