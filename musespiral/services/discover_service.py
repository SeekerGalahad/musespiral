"""知识库选题引擎编排服务（第十二期 F26-F29，正典 §三-3.1）。

编排 core/discovery 四模块：扫描（scanner）→ 评分（scorer，词典缺失传
None 不报错）→ 建议（suggester）→ 骨架（skeleton）。本层只做状态与路径：

- 已推荐记录 ~/.musespiral/discovered.json（JSONL，归 infra/storage/jsonl
  单一持有者——正典 §四-Q1；路径随 MUSESPIRAL_HOME 解析）
- 模式家族词典 模式家族词典.json 相对知识库根解析（缺文件 → families=None）
- 骨架写文章根（open 'x' O_EXCL 拒绝覆盖——正典 §四-Q3/Q5）

CLI（commands/discover_cmds.py）与 Studio API 都只调本层（判例 6：编排
下沉服务层；判例 2：从第一天起就在 services 层，不走 top-level）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..core.discovery import scanner, scorer, skeleton, suggester
from ..infra.storage import jsonl
from ..jobs.store import now_iso

# F26 默认扫描源（相对仓库根；CLI/Studio 传入绝对路径或相对 cwd 均可）
DEFAULT_SOURCE = "docs/ai-knowledge-base/13-source-analysis"
# 知识库根目录名（词典相对它解析）
_KB_ROOT_NAME = "ai-knowledge-base"
# 词典文件名（位于 KB 根下 13-source-analysis/）
FAMILY_DICT_REL = Path("13-source-analysis") / "模式家族词典.json"
# 已推荐记录文件名（config.home_dir() 下）
DISCOVERED_FILE = "discovered.json"
# 骨架默认文章根（Studio launchd root 同款约定）
DEFAULT_ARTICLES_ROOT = Path.home() / "musespiral-articles"


# ── 已推荐记录（discovered.json，JSONL）──────────────────────────────


def discovered_path() -> Path:
    """已推荐记录路径：随 MUSESPIRAL_HOME 解析（测试 monkeypatch env 即隔离）。"""
    return config.home_dir() / DISCOVERED_FILE


def _load_discovered(path: Path | None = None) -> list[dict]:
    """宽容读回全部记录（infra/storage/jsonl.read_rows 单一权威实现的薄壳）。"""
    return jsonl.read_rows(path or discovered_path())


def _drafted_paths(records: list[dict]) -> set[str]:
    """已生成过骨架的源文档绝对路径集合（drafted 判定面）。"""
    return {r["path"] for r in records if r.get("path")}


def _series_counts(records: list[dict]) -> dict[str, int]:
    """各系列已荐数（series_no 编号基准——批内建议从既有计数续号）。"""
    counts: dict[str, int] = {}
    for r in records:
        s = r.get("series")
        if s:
            counts[s] = counts.get(s, 0) + 1
    return counts


# ── 模式家族词典 ──────────────────────────────────────────────────────


def _kb_root(source: Path) -> Path | None:
    """知识库根解析（单一语义：显式登记优先，向上回退）。

    1) config.knowledge_root() 已登记且 source 在其内（含相等）→ 登记根
       （权威——与 Studio /api/discover 穿越防护同根同源，双轨统一点）
    2) 未登记或 source 不在其内 → 从 source 向上找目录名
       ai-knowledge-base（零配置回退：CLI 对任意目录可扫描，词典能找到就用）
    """
    root = config.knowledge_root()
    if root is not None and (root == source or root in source.parents):
        return root
    for p in [source, *source.parents]:
        if p.name == _KB_ROOT_NAME and p.is_dir():
            return p
        if p == p.parent:  # 走到文件系统根
            return None
    return None


def load_family_titles(source: str | Path) -> set[str] | None:
    """构建模式家族成员标题集合（族名 + 别名，scanner.normalize_title 归一）。

    词典相对 KB 根解析（KB根/13-source-analysis/模式家族词典.json）；
    source 不在知识库内或词典缺失/损坏 → None（scorer 该维按 0 分计，
    不报错——契约口径）。
    """
    src = Path(source).resolve()
    if not src.is_dir():
        return None
    root = _kb_root(src)
    if root is None:
        return None
    dict_path = root / FAMILY_DICT_REL
    if not dict_path.is_file():
        return None
    try:
        data = json.loads(dict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    families = data.get("families") if isinstance(data, dict) else None
    if not isinstance(families, dict):
        return None
    titles: set[str] = set()
    for name, info in families.items():
        titles.add(scanner.normalize_title(str(name)))
        for alias in (info or {}).get("aliases") or []:
            titles.add(scanner.normalize_title(str(alias)))
    return titles or None


# ── 公开服务面（两组共同契约）─────────────────────────────────────────


def scan_candidates(
    source: str | Path | None = None,
    limit: int = 20,
    min_score: int = 60,
    include_drafted: bool = False,
) -> list[dict]:
    """扫描知识库源目录，返回评分降序候选列表（F26 输出形状）。

    每项：path/title/score/score_breakdown{depth,uniqueness,readability,
    adaptability}/series/series_no/reason/skeleton_hint/drafted。
    drafted（discovered.json 命中=已生成过骨架）默认整项过滤（include_
    drafted=True 时保留并置 True）。
    """
    src = Path(source) if source is not None else Path(DEFAULT_SOURCE)
    if not src.is_dir():
        raise FileNotFoundError(f"扫描源目录不存在: {src}")
    families = load_family_titles(src)
    records = _load_discovered()
    drafted = _drafted_paths(records)

    items: list[dict] = []
    for meta in scanner.scan_dir(src):
        if not include_drafted and meta["path"] in drafted:
            continue
        scored = scorer.score_doc(meta, families=families)
        if scored["score"] < min_score:
            continue
        items.append({"meta": meta, **scored})
    items.sort(key=lambda it: (-it["score"], it["meta"]["path"]))
    items = items[: max(0, int(limit))]

    suggestions = suggester.suggest_batch(items, existing_counts=_series_counts(records))
    return [
        {
            "path": it["meta"]["path"],
            "title": it["meta"]["title"],
            "score": it["score"],
            "score_breakdown": it["score_breakdown"],
            "series": sug["series"],
            "series_no": sug["series_no"],
            "reason": sug["reason"],
            "skeleton_hint": sug["skeleton_hint"],
            "drafted": it["meta"]["path"] in drafted,
        }
        for it, sug in zip(items, suggestions)
    ]


def draft_from_kb(
    path: str | Path,
    series: str | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """从一篇知识库文档生成文章骨架，写入文章根并返回骨架路径。

    - 骨架 open('x') O_EXCL 创建：目标已存在即 FileExistsError（不覆盖
      ——正典 §四-Q3/Q5，多入口并发只有一方成功）
    - 成功后 discovered.json 追加一条记录（jsonl 模块单一持有者）
    - 知识库文档只读——本函数不向知识库内写任何文件
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"知识库文档不存在: {src}")
    if src.suffix.lower() != ".md":
        raise ValueError(f"只接受 Markdown 文档: {src}")

    meta = scanner.scan_file(src)
    scored = scorer.score_doc(meta, families=load_family_titles(src.parent))
    suggestion = suggester.suggest_batch(
        [{"meta": meta, **scored}], existing_counts=_series_counts(_load_discovered())
    )[0]
    if series:
        suggestion["series"] = series

    _, body = scanner.split_frontmatter(src.read_text(encoding="utf-8", errors="replace"))
    source_label = _source_label(src)
    skeleton_text = skeleton.build_skeleton(meta, suggestion, source_label, hook=scanner.extract_hook(body))

    out_dir = Path(output_dir).expanduser() if output_dir is not None else DEFAULT_ARTICLES_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / (skeleton.slugify(suggestion["skeleton_hint"]) + ".md")
    with open(dst, "x", encoding="utf-8") as f:  # O_EXCL：已存在即拒绝
        f.write(skeleton_text)

    jsonl.append_log(
        discovered_path(),
        {
            "ts": now_iso(),
            "action": "discover_draft",
            "path": str(src),
            "title": meta["title"],
            "series": suggestion["series"],
            "score": scored["score"],
            "output": str(dst),
        },
        platform="discover",
    )
    return dst


def _source_label(src: Path) -> str:
    """frontmatter.source 溯源标签：cwd 内取相对路径（F26 示例形态），否则绝对路径。"""
    try:
        return str(src.relative_to(Path.cwd()))
    except ValueError:
        return str(src)
