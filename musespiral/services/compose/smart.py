"""compose 包：smart（FEAT-2 智能组稿——主题 → 素材 → 会话 + 三段式大纲）。

正典=docs/项目方案/musespiral-极限架构与功能扩展总规划-2026-11-21.md §FEAT-2。
依赖 ARCH-5（search_service 语义搜索）+ FEAT-1（可搜索素材库）。

三步组稿的**服务端落点**：
  1. suggest_materials(topic)：hybrid 搜索取 top-N（相似度 + 来源 + 摘要），
     复用 search_service 的降级承诺——embedding 缺位时关键词+标签两路照常出结果；
  2. smart_start(topic, material_ids)：建会话 → 逐条素材直引入池（add_source
     material_id=…，素材已删时早失败而非静默丢）→ 可选 AI 三段式大纲；
  3. 大纲每节带 points（要点）+ material_ids（引用素材 id）——取自真实素材
     正文（material_text 抽取），不是空泛套话。

大纲生成的降级：AI 未配置/调用失败/产出不可解析 → 确定性兜底（按素材标题+
首句拼三段），保证端点主职责（建会话+挂素材）永远成功。谁生效由返回的
``outline_source``（ai|fallback）诚实标注——AI 环境态不是请求错误。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ...core.composition import STATUS_PENDING
from ...core.materials import search as mat_search
from ...infra.ai import chat as _chat
from .. import search_service
from ..material_body import MaterialTextError, material_text
from .store import ComposeError, add_source, new_session

# 大纲正文注入 prompt 的单素材节选长度（防长正文撑爆请求；与 INDEX_TEXT_MAX 同量级）
MATERIAL_EXCERPT_MAX = 800
# 兜底/AI 大纲固定三段（正典「三段式」）
SECTION_TITLES = ("开篇 · 缘起与背景", "主体 · 核心与演进", "收尾 · 总结与展望")

SMART_OUTLINE_TEMPLATE = """你是资深公众号作者。基于【素材】为【主题】规划一篇三段式大纲（正好 3 节：开篇/主体/收尾）。

每节一行，严格格式：
标题 | 要点1；要点2 | 目标字数 | 素材序号
其中「素材序号」为下面素材列表里的序号（逗号分隔，如 1,3）；要点必须来自素材的真实内容，禁止空泛套话。只输出 3 行，不要解释。

【主题】
{{topic}}

【素材】
{{materials}}"""


def suggest_materials(topic: str, limit: int = 10) -> list[dict[str, Any]]:
    """主题 → 语义相关素材 top-N（FEAT-2 步骤 2 的后端数据面）。

    hybrid 三路合并（关键词+标签+语义 RRF）；语义缺位自动降级为词面两路——
    继承 search_service 的降级承诺，绝不因 embedding 缺位抛错。每条带
    similarity/score/source_id/title + id（详情与入池用 id）+ preview（首
    100 字摘要预览——向导列表「摘要预览」数据面，共享搜索端点不回传正文，
    本包装层就地补）。
    """
    q = (topic or "").strip()
    if not q:
        raise ComposeError("主题描述不能为空")
    items = search_service.hybrid_search(q, limit=limit)
    if not items:
        return []
    from .. import materials_service

    by_id = {m["id"]: m for m in materials_service.list_materials()}
    for item in items:
        material = by_id.get(str(item.get("id")))
        text = mat_search.searchable_text(material) if material else ""
        item["preview"] = " ".join(text.split())[:100]
    return items


def _default_slug(topic: str) -> str:
    """稳定 ASCII slug：smart-<时间戳>-<主题哈希6>（slug 白名单 [A-Za-z0-9._-]）。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:6]
    return f"smart-{stamp}-{digest}"


def _material_briefs(material_ids: list[str]) -> list[dict[str, str]]:
    """素材 id → {id,title,excerpt}（prompt + 兜底大纲共用的单一摘录口径）。"""
    from .. import materials_service

    briefs: list[dict[str, str]] = []
    for mid in material_ids:
        material = materials_service.get_material(mid)
        if material is None:
            raise ComposeError(f"素材不存在或已删: {mid!r}")
        origin = material.get("origin") or {}
        try:
            excerpt = material_text(material, max_chars=MATERIAL_EXCERPT_MAX)
        except MaterialTextError:
            excerpt = str(origin.get("uri") or "")  # 无正文素材仅留引用（仍是真实内容）
        briefs.append(
            {
                "id": mid,
                "title": str(origin.get("title") or origin.get("uri") or mid),
                "excerpt": excerpt,
            }
        )
    return briefs


def _parse_ai_outline(content: str, briefs: list[dict[str, str]]) -> list[dict[str, Any]]:
    """解析「标题 | 要点 | 字数 | 素材序号」行 → 三段式 section dict 列表。

    素材序号映射到真实 id（只认 1..N，越界忽略）；要点按中文/英文分号切。
    少于 3 段或标题全空 → 返回 []（调用方走确定性兜底）。
    """
    sections: list[dict[str, Any]] = []
    for line in (content or "").splitlines():
        line = re.sub(r"^(?:[-*•]\s+|\d{1,2}\s*[.、)]\s*)", "", line.strip()).strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0]
        if not title:
            continue
        points = (
            [p.strip() for p in re.split(r"[；;]", parts[1]) if p.strip()] if len(parts) > 1 else []
        )
        m = re.search(r"\d+", parts[2]) if len(parts) > 2 else None
        target = int(m.group()) if m else 0
        ids: list[str] = []
        if len(parts) > 3:
            for token in re.findall(r"\d+", parts[3]):
                idx = int(token) - 1
                if 0 <= idx < len(briefs) and briefs[idx]["id"] not in ids:
                    ids.append(briefs[idx]["id"])
        sections.append(
            {
                "id": f"s{len(sections) + 1}",
                "title": title,
                "target_chars": target,
                "direction": "；".join(points),  # 逐节展开 prompt 复用现有插槽
                "status": STATUS_PENDING,
                "points": points,
                "material_ids": ids,
            }
        )
        if len(sections) == 3:
            break
    return sections if len(sections) == 3 else []


def _fallback_outline(briefs: list[dict[str, str]]) -> list[dict[str, Any]]:
    """AI 缺位时的确定性三段式：每段挂真实素材 + 首句要点（非空泛兜底）。"""
    out: list[dict[str, Any]] = []
    n = len(briefs)
    for i in range(3):
        # 三段分摊素材：开篇取首条、收尾取末条、主体取中间（不足则轮转复用）
        picked = [briefs[0], briefs[1], briefs[2]][i] if n >= 3 else briefs[i % n]
        if i == 1 and n > 2:
            picked = briefs[1]
        point = re.split(r"[。\n]", picked["excerpt"].strip(), maxsplit=1)[0].strip()
        point = (point or picked["title"])[:120]
        out.append(
            {
                "id": f"s{i + 1}",
                "title": SECTION_TITLES[i],
                "target_chars": 0,
                "direction": point,
                "status": STATUS_PENDING,
                "points": [point],
                "material_ids": [picked["id"]],
            }
        )
    return out


def _build_outline(
    topic: str, briefs: list[dict[str, str]], *, chat_fn: Callable | None = None
) -> tuple[list[dict[str, Any]], str]:
    """AI 三段式大纲（含素材要点/id），失败诚实降级到确定性兜底。"""
    material_block = "\n\n".join(
        f"[{i}] {b['id']} {b['title']}\n{b['excerpt']}" for i, b in enumerate(briefs, 1)
    )
    prompt = SMART_OUTLINE_TEMPLATE.replace("{{topic}}", topic).replace(
        "{{materials}}", material_block
    )
    call = chat_fn if chat_fn is not None else _chat
    try:
        content = call([{"role": "user", "content": prompt}], task="compose", temperature=0.5)
        sections = _parse_ai_outline(content, briefs) if isinstance(content, str) else []
        if sections:
            return sections, "ai"
    except Exception:
        pass  # AI 环境态不阻塞建会话——兜底大纲保证主职责完成
    return _fallback_outline(briefs), "fallback"


def smart_start(
    topic: str,
    material_ids: list[str],
    root: str | Path | None = None,
    *,
    generate_outline: bool = True,
    slug: str | None = None,
    chat_fn: Callable | None = None,
) -> dict[str, Any]:
    """智能组稿落点：建会话 → 素材全入池 → 可选 AI 三段式大纲。

    - topic 必填；material_ids 非空字符串数组（重复 id 自动去重保序）；
    - slug 缺省按主题哈希生成（可选显式指定，重名由 new_session 拒绝）；
    - generate_outline=False 跳过大纲（outline.sections=[]、outline_source=null）；
    - 大纲每节含 points（要点）+ material_ids（引用素材 id），基于素材真实正文；
    - 返回 slug + 入池数 + 大纲（供前端确认后跳转）。
    """
    topic = (topic or "").strip()
    if not topic:
        raise ComposeError("主题描述不能为空")
    if not isinstance(material_ids, list) or not material_ids:
        raise ComposeError("material_ids 必须是非空数组")
    ids: list[str] = []
    for mid in material_ids:
        if not isinstance(mid, str) or not mid.strip():
            raise ComposeError("material_ids 必须是非空字符串数组")
        if mid.strip() not in ids:
            ids.append(mid.strip())

    target_slug = (slug or _default_slug(topic)).strip()
    # 素材存在性 + 正文摘录先于建会话（缺 id 早失败，不留半成品会话）
    briefs = _material_briefs(ids)
    new_session(target_slug, topic, root=root)  # 重名/非法 slug → ComposeError

    attached: list[str] = []
    for mid in ids:
        add_source(target_slug, None, None, root=root, material_id=mid)
        attached.append(mid)

    outline: dict[str, Any] = {"sections": [], "current_expanding": None}
    outline_source: str | None = None
    if generate_outline:
        from .outline import outline_save

        sections, outline_source = _build_outline(topic, briefs, chat_fn=chat_fn)
        outline = outline_save(target_slug, sections, root=root)

    return {
        "slug": target_slug,
        "title": topic,
        "material_ids": attached,
        "attached": len(attached),
        "outline": outline,
        "outline_source": outline_source,
    }


__all__ = ["suggest_materials", "smart_start"]
