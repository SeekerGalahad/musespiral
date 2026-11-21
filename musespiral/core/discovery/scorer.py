"""可发布性评分器（F27）——纯规则、纯函数、零 I/O。

评分表（正典 §二-F27，满分 100）：

  内容深度 depth（40）：
    正文行数 ≥200=20 / ≥150=15 / ≥100=10（档位互斥）；含「设计思维提炼」
    章节 +10；含「端到端数据流」章节 +10
  知识独特性 uniqueness（30）：
    tags 含 owner/repo（值含 "/"）=15；模式家族成员（条目标题命中词典）=15
  可读性 readability（20）：
    tags ≥4 项 =10；created 距今 ≤30 天 =10（新鲜度）
  适配潜力 adaptability（10）：
    源码分析类=10 / 学习路径类=5 / 其他=0

总分与四维分项同源返回（score_breakdown）——Studio 分解条与 CLI --json
消费同一份数据。matched 额外透出命中明细（owner/repo tag、命中的家族
标题），供 suggester 生成 reason 复用，不重复计算。
"""
from __future__ import annotations

from datetime import date, datetime

# 新鲜度窗口（天）——F27 可读性维
FRESH_DAYS = 30
# tags 数量门槛（可读性维）
TAGS_FLOOR = 4

MAX_DEPTH = 40
MAX_UNIQUENESS = 30
MAX_READABILITY = 20
MAX_ADAPTABILITY = 10

# 深度维章节关键词（按 F27 表原文：含「设计思维提炼」章节 / 含「端到端数据流」章节）
_DEPTH_SECTIONS = ("设计思维提炼", "端到端数据流")
# 适配维类别分（F27 表）
_ADAPT_SCORES = {"13-source-analysis": 10, "11-learning-path": 5}


def _depth_lines(line_count: int) -> int:
    """行数档位（互斥取最高档）。"""
    if line_count >= 200:
        return 20
    if line_count >= 150:
        return 15
    if line_count >= 100:
        return 10
    return 0


def _parse_created(raw: str) -> date | None:
    """created 字符串 → date；空/非法格式返回 None（不猜）。"""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _match_owner_repo(tags: list[str]) -> str | None:
    """首个 owner/repo 形态 tag（值含 "/"），无则 None。"""
    for t in tags:
        if "/" in t:
            return t
    return None


def _match_families(pattern_titles: list[str], families: set[str] | None) -> list[str]:
    """文档条目标题 ∩ 词典成员标题集合（保持文档内出现顺序，去重）。"""
    if not families:
        return []
    seen: list[str] = []
    for t in pattern_titles:
        if t in families and t not in seen:
            seen.append(t)
    return seen


def score_doc(
    meta: dict,
    families: set[str] | None = None,
    *,
    today: date | None = None,
) -> dict:
    """按 F27 表给单篇元数据评分。

    - families：模式家族成员标题集合（service 载入 模式家族词典.json 构建；
      缺文件传 None 不报错——该维按 0 分计）
    - today：新鲜度基准日（缺省 date.today()；测试注入固定日打边界）

    返回 {"score", "score_breakdown": {depth, uniqueness, readability,
    adaptability}, "matched": {"owner_repo", "families"}}——总分恒等于
    四维之和（构造即同源，无第二处计算）。
    """
    sections = meta.get("sections") or []
    tags = meta.get("tags") or []
    base = today or date.today()

    # depth：行数档位 + 两章节
    depth = _depth_lines(int(meta.get("line_count") or 0))
    for keyword in _DEPTH_SECTIONS:
        if any(keyword in s for s in sections):
            depth += 10
    depth = min(depth, MAX_DEPTH)

    # uniqueness：owner/repo tag + 家族成员
    owner_repo = _match_owner_repo(tags)
    fam_hits = _match_families(meta.get("pattern_titles") or [], families)
    uniqueness = (15 if owner_repo else 0) + (15 if fam_hits else 0)

    # readability：tags 数量 + 新鲜度（未来日期不算新鲜——差值须落在
    # [0, FRESH_DAYS] 闭区间，负差值（created 晚于基准日）视为数据错误）
    readability = 10 if len(tags) >= TAGS_FLOOR else 0
    created = _parse_created(str(meta.get("created") or ""))
    if created is not None and 0 <= (base - created).days <= FRESH_DAYS:
        readability += 10
    readability = min(readability, MAX_READABILITY)

    # adaptability：类别
    adaptability = _ADAPT_SCORES.get(str(meta.get("category") or ""), 0)

    breakdown = {
        "depth": depth,
        "uniqueness": uniqueness,
        "readability": readability,
        "adaptability": adaptability,
    }
    return {
        "score": sum(breakdown.values()),
        "score_breakdown": breakdown,
        "matched": {"owner_repo": owner_repo, "families": fam_hits},
    }
