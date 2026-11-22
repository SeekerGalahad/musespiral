"""materials 包：feedback（文章反哺三路径 + 内部源刷新，N-2 ②/④）。

原 materials_service.py 按 ARCH-2 拆出的反哺簇（正典=《musespiral-素材闭环
与专业编辑器设计-2026-11-18.md》§三/§五 P-1）：

  - add_from_article：full/excerpt/distill 三路径统一入口（幂等锚
    article://<slug> 系；immutable 红线下改稿不覆盖既有条，首次采集为准）；
  - _distill_article：路径 C——AI 抽 3-5 条核心论点 → 逐条 spark 落库
    （license=AI-GENERATED + meta.ai_generated，G1 生成物口径；AI 自评
    confidence 随 triage 落行，N-4）；
  - refresh_internal_articles：sources.json type=internal 的刷新面——扫
    文章目录逐篇产 text 素材（幂等按 slug；墓碑不复活，add_many 同语义）。

AI 注入缝 _chat_policed 与拆分前 materials_service 模块级同款——兼容门面
对它的属性替换（测试 monkeypatch）经 __setattr__ 同步转发到本模块。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core.discovery import scanner
from ...core.materials import confidence as confidence_mod  # N-4 自评解析单一权威
from ...core.materials import model
from ...infra.ai.gateway import chat_policed as _chat_policed  # distill 注入缝（refine 的 _chat 同款）
from ...jobs.store import now_iso as _now_iso
from .crud import _append, _read_index, add_material, get_material

# 文章反哺虚拟源（full/excerpt/distill 三路径产物 origin.source_id）——
# 指回文章走 provenance.chain（from_id=slug，articles_root/<slug>.md 真实
# 可寻址），本 id 只标记「来源族=自家文章」，不入 sources.json。
ARTICLE_SOURCE_ID = "article"

# distill 的 AI 任务键（TASKS 白名单同批扩键；模板覆盖级联 prompts.d/
# material_distill.txt 自动生效——profiles.TASKS 扩键即继承任务面全套机制）
DISTILL_TASK = "material_distill"
# 论点条数上界（正典 §3.2 路径 C「AI 抽 3-5 条核心论点」：解析时钳制 ≤5；
# 少于 3 条不硬拒——AI 偶尔少给时采可用值，0 条才报错）
DISTILL_MAX_POINTS = 5

DEFAULT_DISTILL_PROMPT = (
    "你是编辑助手。阅读下面这篇文章，提炼 3-5 条核心论点，每条一行、"
    "一句独立成立的话（不带编号、不带列表符号、不换行内再分段）。"
    "只输出论点本身，不要任何前言或解释。\n\n"
    "输出末尾单独加一行标注你对自己准确提炼原文的把握，格式固定为"
    "「CONFIDENCE: 0.8」（0-1 小数；不确定时给低值）——该行不算论点。\n\n"
    "【标题】{{title}}\n\n【正文】\n{{body}}\n"
)


def _article_file(slug: str, articles_root: str | Path) -> Path:
    """文章路径解析（articles_root/<slug>.md；不存在 → ValueError 可读）。"""
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("缺少必填字段 slug（文章标识）")
    slug = slug.strip()
    if "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ValueError(f"slug 必须是文章根内的文件名主干: {slug!r}")
    path = Path(articles_root).expanduser() / f"{slug}.md"
    if not path.is_file():
        raise ValueError(f"文章不存在（articles_root 下无 <slug>.md）: {slug}")
    return path


def _article_text(path: Path) -> tuple[str, str]:
    """读文章 → (frontmatter 表, 正文)（scanner.split_frontmatter 单一口径）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = scanner.split_frontmatter(text)
    return fm, body


def add_from_article(
    slug: str,
    mode: str,
    *,
    articles_root: str | Path,
    start_line: int | None = None,
    end_line: int | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """文章反哺（N-2 ②）三路径统一入口。

    - mode='full'（路径 A 整篇归档）：正文全文（frontmatter 剥离——
      origin.title/meta 已携带元信息，素材正文不带 YAML 噪声）→ type=text
      素材，provenance.chain=[{from_id: slug, op: published_from}]；
    - mode='excerpt'（路径 B 段落摘取）：正文行区间 [start_line, end_line]
      （1-based 双闭，对剥除 frontmatter 后的正文行计——行号随 frontmatter
      增删不漂移），同 published_from 链；
    - mode='distill'（路径 C 论点提炼）：AI 抽 3-5 条核心论点 → 多条 spark
      （每条 provenance.chain=[{from_id: slug, op: distill}]，license=
      AI-GENERATED + meta.ai_generated——G1 生成物口径）。

    幂等锚：uri = ``article://<slug>``（full）/ ``article://<slug>#L{s}-L{e}``
    （excerpt）/ ``article://<slug>#distill-<i>``（distill 逐条槽位）——
    先查既有活跃行（register_video 同款）：命中返回 dedup=true（immutable
    红线下文章改稿不覆盖既有条，首次采集为准）。

    返回：full/excerpt → ``{mode, material, dedup}``；distill →
    ``{mode, materials, skipped, points}``（materials=本次新增，skipped=
    既有槽位数）。mode 枚举外/区间非法/AI 未配置 → ValueError/AIError
    （studio 转 400 / ok:false 可读面）。
    """
    path = _article_file(slug, articles_root)
    slug = slug.strip()
    fm, body = _article_text(path)
    title = str(fm.get("title") or "").strip() or slug
    if mode == "distill":
        return _distill_article(slug, title, body, fetched_at=fetched_at)
    if mode not in ("full", "excerpt"):
        raise ValueError(f"mode 必须是 full/excerpt/distill 之一: {mode!r}")
    if mode == "excerpt":
        body_lines = body.splitlines()
        if not isinstance(start_line, int) or isinstance(start_line, bool):
            raise ValueError("excerpt 需要整数行区间 start_line")
        if not isinstance(end_line, int) or isinstance(end_line, bool):
            raise ValueError("excerpt 需要整数行区间 end_line")
        if not (1 <= start_line <= end_line <= len(body_lines)):
            raise ValueError(
                f"行区间非法（1 ≤ start ≤ end ≤ {len(body_lines)}）: " f"{start_line}-{end_line}"
            )
        text = "\n".join(body_lines[start_line - 1 : end_line])
        uri = f"article://{slug}#L{start_line}-L{end_line}"
        meta: dict[str, Any] = {
            "article_slug": slug,
            "mode": "excerpt",
            "line_start": start_line,
            "line_end": end_line,
            "fm_title": title,
        }
    else:
        text = body
        uri = f"article://{slug}"
        meta = {"article_slug": slug, "mode": "full", "fm_title": title}
    mid = model.make_material_id(ARTICLE_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"mode": mode, "material": existing, "dedup": True}
    material = model.make_material(
        mtype="text",
        source_id=ARTICLE_SOURCE_ID,
        uri=uri,
        title=title,
        license="ALL-RIGHTS",  # 自产文章：权利自有（区别于抓取物 UNKNOWN）
        payload={"text": text, "meta": meta},
        provenance={"chain": [{"from_id": slug, "op": "published_from"}], "derived_from": []},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"mode": mode, "material": row, "dedup": False}


def _parse_points(reply: str) -> list[str]:
    """AI 回复 → 论点行清单（剥代码围栏/列表符号/编号；空行丢弃）。

    每行一条（模板已约束「每条一行」）；行首的列表符号/编号统一剥除
    （`-`/`*`/`•`/`·`/`1.`/`1、`/`1）` 等形态），钳制 ≤5 条（正典路径 C
    「3-5 条」上界；少于 3 条不硬拒——AI 偶尔少给时采可用值，0 条才报错）。
    """
    from ..rewrite_service import _strip_code_fence  # 函数体内导入（同层解耦）

    bullet = re.compile(r"^(?:[-*•·]+|\d{1,2}[.、)）])\s*")
    points: list[str] = []
    for line in _strip_code_fence(str(reply or "")).splitlines():
        cleaned = bullet.sub("", line.strip()).strip()
        if cleaned:
            points.append(cleaned)
    return points[:DISTILL_MAX_POINTS]


def _distill_article(slug: str, title: str, body: str, *, fetched_at: str | None) -> dict[str, Any]:
    """路径 C：AI 提炼论点 → 逐条 spark 落库（dedup 按槽位 first-capture）。"""
    from ...infra.ai import AIError, ai_configured  # 函数体内导入（导入期解耦）

    if not ai_configured(DISTILL_TASK):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    from ...infra.ai.prompts import load_override  # 函数体内导入（导入期解耦）

    template = load_override(DISTILL_TASK) or DEFAULT_DISTILL_PROMPT
    prompt = template.replace("{{title}}", title).replace("{{body}}", body)
    res = _chat_policed([{"role": "user", "content": prompt}], task=DISTILL_TASK, cache=False)
    # N-4：先剥置信度行再解析论点（容错契约——AI 不带该行时 conf=None 不阻
    # 断面，sparks 照常落库；行混进论点会污染 _parse_points 输出，故先剥）
    reply, conf = confidence_mod.parse_ai_confidence(str(res.get("text") or ""))
    points = _parse_points(reply)
    if not points:
        raise AIError("AI 未返回可用论点（空响应）")
    added: list[dict[str, Any]] = []
    skipped = 0
    for i, point in enumerate(points, 1):
        uri = f"article://{slug}#distill-{i}"
        mid = model.make_material_id(ARTICLE_SOURCE_ID, "text", uri)
        if get_material(mid) is not None:
            skipped += 1  # 槽位已有（重跑提炼不覆盖既有条——immutable 红线）
            continue
        material = model.make_material(
            mtype="text",
            source_id=ARTICLE_SOURCE_ID,
            uri=uri,
            title=point[:24],
            license="AI-GENERATED",
            payload={
                "text": point,
                "meta": {
                    "sub_type": "spark",
                    "ai_generated": True,
                    "article_slug": slug,
                    "distill_slot": i,
                    "fm_title": title,
                },
            },
            provenance={"chain": [{"from_id": slug, "op": "distill"}], "derived_from": []},
            triage={"confidence": conf},  # AI 自评（None=未自评，归一后六键补齐）
            fetched_at=fetched_at or _now_iso(),
        )
        added.append(add_material(material))
    return {"mode": "distill", "materials": added, "skipped": skipped, "points": points}


# ── 内部源刷新（N-2 ④，设计 §3.1：sources.json type=internal）────────


def refresh_internal_articles(
    source_id: str,
    scan_root: str | Path,
    *,
    include_drafts: bool = True,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """内部源刷新：扫文章目录 → 逐篇产 type=text 素材（幂等，按 slug 去重）。

    - 幂等口径：uri=文章 slug → make_material_id 稳定散列；库内已有活跃
      行 → 跳过（immutable 红线下改稿不覆盖既有条，首次采集为准；连跑
      两次零新增）；墓碑 id 不复活（删除优先，add_many 同语义）；
    - 每篇 payload.meta 携带 frontmatter（title/tags/series——设计 §3.1
      「frontmatter 成为素材 meta」）+ article_slug；license=ALL-RIGHTS
      （自产）；provenance 恒空（内部源素材是原始采集物，指回原文走
      origin.uri=slug，与创作工坊 slug 互链——设计 §3.1）；
    - include_drafts=False 时只收 publish_log 里的 publish 行文章
      （merge_logs 按 scan_root 优先双日志合并读）。

    返回 ``{source_id, added, skipped, total}``；scan_root 非目录 →
    ValueError（studio 400 可读）。
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("缺少必填字段 source_id（内部源 id）")
    root = Path(scan_root).expanduser() if isinstance(scan_root, str) else Path(scan_root)
    if not root.is_dir():
        raise ValueError(f"内部源 scan_root 不是目录: {root}")
    wanted: list[Path] = sorted(root.glob("*.md"))
    if not include_drafts:
        from ..publish_log_service import merge_logs  # 函数体内导入（同层解耦）

        published = {
            str(Path(str(row.get("article") or "")).stem)
            for row in merge_logs(root)
            if row.get("action") == "publish" or row.get("result") == "published"
        }
        wanted = [p for p in wanted if p.stem in published]
    sid = source_id.strip()
    index, deleted = _read_index()
    added = skipped = 0
    added_ids: list[str] = []
    for path in wanted:
        uri = path.stem  # slug（articles_root 平铺约定，drafted_uris 同口径）
        mid = model.make_material_id(sid, "text", uri)
        row = index.get(mid)
        if row is not None and not model.is_tombstone(row):
            skipped += 1  # 已采集（改稿不覆盖既有条——immutable 红线）
            continue
        if mid in deleted:
            skipped += 1  # 墓碑：删除优先，不复活
            continue
        try:
            meta = scanner.scan_file(path)
        except OSError:
            skipped += 1
            continue
        fm, body = _article_text(path)
        payload_meta: dict[str, Any] = {"article_slug": uri}
        for key in ("title", "series"):
            value = str(fm.get(key) or "").strip()
            if value:
                payload_meta[key] = value
        tags = meta.get("tags") or []
        if tags:
            payload_meta["tags"] = list(tags)
        material = model.make_material(
            mtype="text",
            source_id=sid,
            uri=uri,
            title=str(meta.get("title") or uri),
            license="ALL-RIGHTS",
            payload={"text": body, "meta": payload_meta},
            fetched_at=fetched_at or _now_iso(),
        )
        errors = model.validate_material(material)
        if errors:
            raise ValueError(f"内部源素材行不合法（{uri}）: " + "; ".join(errors))
        _append(material)
        added_ids.append(material["id"])
        added += 1
    if added_ids:
        # ARCH-5 增量索引钩子：本簇直走 _append（绕过 add_many 的墓碑/冲突
        # 预检——上方循环已做同语义判定），索引通知在这里补——批量一次 embed
        from .. import search_service  # 函数体内导入（导入期解耦）

        search_service.on_materials_added(added_ids)
    return {"source_id": sid, "added": added, "skipped": skipped, "total": len(wanted)}
