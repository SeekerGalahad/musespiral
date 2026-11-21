"""Studio 文章扫描与状态探测（Phase A 自 routing.py 拆出，行为零变更）。

  - 队列候选发现 _iter_article_files：衍生文件/物料包/隐藏目录/越界符号链接
    已剔除；衍生后缀契约 _DERIVED_SUFFIXES 与变体平台码集 _PLATFORM_CODES
  - 产物标记探测：配对笔记 / 平台变体（R3 回链契约 + R9 请求级缓存
    _VariantScanCache，把逐文章探测的重复 YAML 解析降为每目录一次）/ 物料包 /
    发布日志记录，聚合为 _article_flags（§15.1 徽章数据面）
  - 文章状态 _front_status（R3 契约字段 status，缺省读作 draft）
  - 发布日志定位 _log_path（root 自带 publish_log.jsonl 优先，否则回落
    stats.default_log_path()，可由 MUSESPIRAL_HOME 隔离）——写侧/回填目标用；
    读侧（_has_publish_record 徽章）第十四期起走 publish_log_service
    双日志合并读序 + demo 行滤除（正典 §1.2/§三-Q1）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter

from .. import config
from ..infra.storage import jsonl as stats
from ..core import adaptation as adapt
from ..core.article import body_sha1
from ..platforms import list_platforms
from ..services import publish_log_service
from .security import _rel


# 已注册平台代码 ∪ adapt 变体平台代码 —— 变体文件名 <stem>.<code>.md 的识别集
_PLATFORM_CODES: frozenset[str] = frozenset(
    p.name for p in list_platforms()
) | frozenset(adapt.PLATFORM_SPECS)

# 衍生文件名后缀（配对笔记 + 平台变体 + R7 批注改写预览）：不进队列，
# 只做源文章的 flags（.annotated.md 是 AI 按批注生成的预览件，人对照后
# 自行替换，不应作为可发布文章参与队列/筛选）
_DERIVED_SUFFIXES: tuple[str, ...] = (".note.md", ".annotated.md") + tuple(
    f".{code}.md" for code in sorted(_PLATFORM_CODES)
)

# 创作台会话目录后缀（第十八期 §2.1：<slug>.compose/ 自包含
# session.json/draft.md/rounds）——会话内部的 md 不是可发布文章，
# 不进队列；adopt 产出的正稿 <slug>.md 才入队列。
_COMPOSE_DIR_SUFFIX = ".compose"


# ---- 文章扫描与产物标记 ----


def _iter_article_files(root: Path) -> list[Path]:
    """root 下候选文章文件（衍生文件/物料包/隐藏目录/越界符号链接已剔除）。"""
    files: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue  # 隐藏文件/目录（含 .musespiral 运行目录）
        if any(part.endswith(".pack") for part in parts[:-1]):
            continue  # 物料包内的 README/payload 不是文章
        if any(part.endswith(_COMPOSE_DIR_SUFFIX) for part in parts[:-1]):
            continue  # 创作台会话目录（draft.md/rounds/N.md 非队列文章）
        if path.name.endswith(_DERIVED_SUFFIXES):
            continue  # 配对笔记 / 平台变体：作为源文章 flags，不进队列
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            continue  # 符号链接环等无法解析：跳过
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue  # 符号链接指向 root 外：绝不扫描/解析
        if resolved in seen:
            continue  # 指向 root 内其它文章的符号链接：按解析后的同一文件去重
        seen.add(resolved)
        files.append(resolved)
    return files


def _paired_note(path: Path, root: Path) -> Path | None:
    """配对笔记探测：同目录 `<stem>.note.md`，且解析后必须仍在 root 内。"""
    candidate = path.with_name(f"{path.stem}.note.md")
    if not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_relative_to(root) else None


def _paired_review(path: Path, root: Path) -> Path | None:
    """CLI `musespiral review` 产物 `<stem>.review.html`（存在且 root 内才回放）。"""
    candidate = path.with_name(f"{path.stem}.review.html")
    if not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_relative_to(root) else None


def _variant_platform(name: str) -> str | None:
    """文件名尾部 `.<平台码>.md` → 平台码；非变体命名返回 None。"""
    for code in _PLATFORM_CODES:
        if name.endswith(f".{code}.md"):
            return code
    return None


class _VariantScanCache:
    """variants 探测的请求级缓存（R9 性能优化，轮次二遗留 low#5）。

    单次 /api/articles 请求内由 _articles_payload 创建一个实例并传给
    _article_flags/_variants，把逐文章探测的重复磁盘与 YAML 解析降为每目录一次：
      - 目录索引：每目录一次 iterdir，筛出变体候选（<平台码>.md 命名的常规
        文件）并各做一次 frontmatter.load 取回链字段——同目录 N 篇文章共享；
      - 源正文哈希：每源文件一次 body_sha1；队列已解析过的正文经 seed_body
        直接种入，免二次读盘。
    匹配规则本身不变（与无缓存逐文章探测逐字对拍，test_flags_variants_source_
    backlink 锚定）；只假设请求进行中目录内容不变——单请求视角与原实现等价。
    """

    def __init__(self) -> None:
        # 目录 → [(entry, code, source, sha1)] 变体候选索引（entry 为该目录内
        # 变体命名的常规文件，source/sha1 为其回链字段，坏产物读作空字段）
        self._index: dict[Path, list[tuple[Path, str, str, str]]] = {}
        # 源文件 → 正文哈希（解析失败记 None：文件名匹配与 stem 兜底仍可用）
        self._hash: dict[Path, str | None] = {}

    def variant_index(self, directory: Path) -> list[tuple[Path, str, str, str]]:
        """目录的变体候选索引（请求内首次访问时建，之后复用）。"""
        if directory not in self._index:
            try:
                entries = sorted(directory.iterdir())
            except OSError:
                entries = []
            index: list[tuple[Path, str, str, str]] = []
            for entry in entries:
                code = _variant_platform(entry.name)
                if code is None or not entry.is_file():
                    continue
                try:
                    meta = frontmatter.load(entry).metadata
                except Exception:
                    meta = {}  # 坏产物文件不拖垮队列（按无字段走 stem 兜底）
                index.append(
                    (
                        entry,
                        code,
                        str(meta.get("source") or "").strip(),
                        str(meta.get("source_sha1") or "").strip(),
                    )
                )
            self._index[directory] = index
        return self._index[directory]

    def source_hash(self, path: Path) -> str | None:
        """源正文哈希（缓存；无 seed 时现场读盘解析，失败记 None）。"""
        if path not in self._hash:
            try:
                self._hash[path] = body_sha1(frontmatter.load(path).content)
            except Exception:
                self._hash[path] = None
        return self._hash[path]

    def seed_body(self, path: Path, body: str) -> None:
        """种入已解析的源正文（_articles_payload 持有 parse_article 结果，
        raw_body 与 frontmatter.load().content 同一内容——免二次读盘）。"""
        self._hash[path] = body_sha1(body)


def _variants(path: Path, cache: _VariantScanCache | None = None) -> list[str]:
    """源文章的平台变体平台码清单（R3 回链契约，正典 §四-R3）。

    优先读同目录候选产物 `*.<平台码>.md` 的 frontmatter 回链字段
    （adapt.generate 自 R3 起写入）：
      - source == 源文件名 → 命中（产物名与源名脱钩，自定义 out_path 也能挂上）；
      - source_sha1 == body_sha1(源正文) → 命中（源改名后内容不变仍回链）。
    产物无回链字段（旧数据）时降级为 stem 命名约定兜底（<stem>.<平台码>.md）；
    有字段但两路都不匹配则不认领——产物显式声明了归属，不让文件名抢挂。
    哈希算法与写入端共用 core.article.body_sha1（探测端不得另行实现）。

    R9：cache 由 _articles_payload 传入（单请求内目录索引/源哈希复用）；
    独立调用不传 cache 时自建局部缓存，行为与逐文章探测一致。
    """
    scan = _VariantScanCache() if cache is None else cache
    src_hash = scan.source_hash(path)
    codes: set[str] = set()
    for entry, code, source, sha1 in scan.variant_index(path.parent):
        if source or sha1:
            if source == path.name or (src_hash is not None and sha1 == src_hash):
                codes.add(code)
        elif entry.stem == f"{path.stem}.{code}":  # 无回链字段的旧产物：stem 约定兜底
            codes.add(code)
    return sorted(codes)


def _has_pack(path: Path) -> bool:
    """是否有 <stem>.*.pack/ 物料包目录（逐目录迭代，避免 stem 特殊字符进 glob）。"""
    prefix, suffix = f"{path.stem}.", ".pack"
    try:
        return any(
            entry.is_dir() and entry.name.startswith(prefix) and entry.name.endswith(suffix)
            for entry in path.parent.iterdir()
        )
    except OSError:
        return False


def _log_path(root: Path) -> Path:
    """发布日志：root 自带 publish_log.jsonl 优先（演示 fixture/工作区日志），
    否则回落 stats.default_log_path()（~/.musespiral，可由 MUSESPIRAL_HOME 隔离）。
    root 内日志若是越界符号链接，视同没有工作区日志（边界不放松）。"""
    root_log = root / config.PUBLISH_LOG
    if root_log.is_file():
        try:
            resolved = root_log.resolve()
        except (OSError, ValueError):
            resolved = None
        if resolved is not None and resolved.is_relative_to(root):
            return resolved
    return stats.default_log_path()


def _has_publish_record(root: Path, article_path: Path) -> bool:
    """合并发布日志（root∪home，demo 行滤除）里该文章是否有 draft/publish 记录。

    第十四期集成：读侧走 publish_log_service.merge_logs——统一读序 +
    demo_ fixture 行判除（正典 §1.2/§三-Q1；原单文件 _log_path 读会把演示
    fixture 误点「已发草稿」徽章、并漏另一侧日志的真实记录）。

    article 字段按三种口径匹配（CLI 常写绝对路径/原样入参、Studio 写绝对路径、
    演示 fixture 可写 root 相对路径）：解析后绝对值相等 / root 相对路径相等。
    """
    target = article_path.resolve()
    rel = _rel(root, article_path)
    for record in publish_log_service.merge_logs(root):
        if record.get("action") not in ("draft", "publish"):
            continue
        recorded = str(record.get("article") or "")
        if not recorded:
            continue
        if recorded == rel or recorded == str(article_path) or recorded == str(target):
            return True
        try:
            if Path(recorded).expanduser().resolve() == target:
                return True
        except (OSError, ValueError):
            continue
    return False


def _article_flags(
    root: Path,
    path: Path,
    cache: _VariantScanCache | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """队列卡片产物标记（§15.1 徽章数据面）。

    R9：cache 由 _articles_payload 按请求传入（variants 探测目录索引/源哈希
    复用）；body 为队列已解析的源正文（seed 进 cache，免 _variants 二次读盘）。
    独立调用两者皆可省，行为与逐文章探测一致。
    """
    scan = _VariantScanCache() if cache is None else cache
    if body is not None:
        scan.seed_body(path, body)
    return {
        "review": _paired_review(path, root) is not None,
        "note": _paired_note(path, root) is not None,
        "variants": _variants(path, scan),
        "pack": _has_pack(path),
        "published": _has_publish_record(root, path),
    }


# ---- 文章状态（R3 契约字段 status: draft|reviewed|published|archived）----


def _front_status(metadata: dict[str, Any]) -> str:
    """frontmatter.status 读取（与 core.article.read_status 同口径，免二次读盘）：
    缺失/空读作 draft 不回写；未知历史值原样透出（读取侧不猜，UI 层负责降级显示）。"""
    value = str(metadata.get("status") or "").strip()
    return value or "draft"
