"""官方插件·素材落库（首插件族 #6，kind=sink——首个 sink 插件，正典 §9.1，PL-6）。

把 article-writer 的图文稿落进既有素材库（materials_service 单一持有者），
飞轮闭环：一次性的视频 → 可检索、可引用、可追溯的素材（评审 §3.3）。三条
既有纪律在这里收口：

**preview 态（正典 §7.3：插件产出的素材必须以 preview 态落库）**：结构化
表达 = ``triage.enabled=false``（不进推荐——查询默认过滤，triage.py 既有
语义），人工「采纳」= triage 面 ``enabled=true``。config.triage 初值可设
read/starred/tags，**改不了 enabled**——红线钉死在代码里而非约定里。

**不新增 MATERIAL_TYPES（沿 E-5 人物卡同一条纪律）**：``type=text`` +
``payload.meta.sub_type="video_article"``——类型枚举是素材库全部读侧的
分支面，扩取值会把空分支散到评分/文件服务/CLI 各处；sub_type 是
payload.meta 内的语义标注，读侧未命中者天然按普通 text 对待（零回归）。
license 走 AI-GENERATED + meta.ai_generated=true（G1 既有 AI 产物双保险）。

**血统可溯（评审 §3.3「素材要能追溯来源」）**：``provenance.chain =
[{from_id: <run_id>, op: "plugin_pipeline"}]``——run_id 经 ctx.run_id
（引擎按 run 目录注入）取得，from_id 是**虚拟标记非素材 id**（run 目录归
pipeline_runs 的 LRU 保留策略，不进素材库），与 generate 的 "ai"、
book_fetch 的 bookSourceUrl 同族；core.model 豁免条四放行此单链形态。

图片随稿持久化：稿件+帧图复制进素材仓 durable 区
（``~/.musespiral/materials/pipeline/<run_id>/``），``payload.meta.images`` 记
durable 副本路径——run workspace 归 pipeline_runs LRU（PRUNE_KEEP=5）管
辖，只存 run 内路径会让素材在数次流水线后变成死引用（2026-11-20 复核
判例）。正文相对引用 ``![](images/…)`` 在 durable 目录内自洽；正式采纳
时走图片上传面从 durable 副本转正式图片素材。同 run 重落幂等（同 id
同稿 → 返回既有行零追加；durable 副本同字节重写自愈）；同 id 异稿 →
ValueError（immutable：payload 落库后不改写）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from musespiral import config as _config
from musespiral.core.materials import model as _mat_model
from musespiral.core.materials.tags import normalize_tags as _normalize_tags
from musespiral.core.plugins.context import PluginError
from musespiral.jobs.store import now_iso as _now_iso
from musespiral.services import materials_service as _materials

# 素材归属（source_id 走线 precedent：upload/manual/novel——pipeline 产物独立源）
SOURCE_ID = "plugin_pipeline"
SUB_TYPE = "video_article"


def _initial_triage(cfg: Any, ctx: Any) -> dict[str, Any]:
    """preview 基线 + config.triage 初值合并（enabled 红线钉死 False）。"""
    triage: dict[str, Any] = {"read": False, "starred": False, "enabled": False, "tags": []}
    if cfg is None:
        return triage
    if not isinstance(cfg, dict):
        raise PluginError(f"config.triage 须为对象: {cfg!r}")
    for key, value in cfg.items():
        if key == "enabled":
            ctx.log("material-sink: config.triage.enabled 被忽略（preview 红线——采纳走 triage 面）")
        elif key in ("read", "starred"):
            if not isinstance(value, bool):
                raise PluginError(f"config.triage.{key} 须为布尔: {value!r}")
            triage[key] = value
        elif key == "tags":
            # tags 面唯一契约（core/materials/tags.py，正典 §1.2）：规范化
            # （小写/非法字符转连字符/去重保序）+ 限额（≤12 个、单标签 ≤24
            # 字符）只此一处定义——绕过它原样落库会把脏标签灌进
            # tag_counts，且该素材此后 AI 标签建议面（tags_suggest 的
            # adopt）永久抛错（2026-11-20 复核判例）
            try:
                triage["tags"] = _normalize_tags(value)
            except ValueError as exc:
                raise PluginError(f"config.triage.tags 被拒: {exc}") from exc
        else:
            raise PluginError(
                f"config.triage 只接受 read/starred/tags（enabled 恒 false——preview 红线）: {key!r}"
            )
    return triage


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    article = inputs.get("article")
    if not isinstance(article, dict):
        raise PluginError("缺少输入 article（article-writer 的 article 输出）")
    md_path = Path(str(article.get("path") or "").strip())
    if not md_path.is_file():
        raise PluginError(f"article.path 不存在: {md_path}")
    run_id = str(getattr(ctx, "run_id", "") or "").strip()
    if not run_id:
        # 血统缺失比伪造 run_id 更诚实——无运行概念的宿主（纯单测假 ctx）拒绝落库
        raise PluginError("ctx.run_id 为空：素材落库必须携带流水线 run_id（血统链 from_id）")

    text = md_path.read_text(encoding="utf-8")
    slug = Path(md_path).stem or "article"
    uri = f"pipeline://{run_id}/{slug}.md"
    mid = _mat_model.make_material_id(SOURCE_ID, "text", uri)

    # 幂等/immutable 先判（同 id 同稿零 IO 返回；同 id 异稿拒——durable 副本
    # 不被失败的异稿尝试覆写）
    existing = _materials.get_material(mid)
    if existing is not None:
        if (existing.get("payload") or {}).get("text") == text:
            ctx.log(f"material-sink: 素材已存在（幂等返回）: {mid}")
            return {"material": mid}
        raise PluginError(
            f"素材 id 已存在且内容不同（immutable 红线——payload 落库后不改写）: {mid}"
        )

    # ── 产物持久化（复核 2026-11-20 判例）：稿件+帧图复制进素材仓 durable
    # 区（~/.musespiral/materials/pipeline/<run_id>/）。run workspace 归
    # pipeline_runs 的 LRU 保留策略（PRUNE_KEEP=5）——只存 run 内绝对路径
    # 会让素材在几次流水线后变成永久死引用（帧图/article.md 双 404，且
    # 全仓无 meta.images 消费者、采纳搬运面未实现）。durable 区随素材库
    # 存活，正文相对引用 ![](images/…) 在副本目录内自洽；正式采纳时走
    # 图片上传面从 durable 副本转正式图片素材（设计不变，搬移路径落地）。
    durable_root = _config.home_dir() / "materials" / "pipeline" / run_id
    try:
        durable_root.mkdir(parents=True, exist_ok=True)
        durable_md = durable_root / md_path.name
        shutil.copyfile(md_path, durable_md)
    except OSError as exc:
        raise PluginError(f"素材产物持久化失败（durable 区不可写）: {exc}") from exc

    images_meta: list[dict[str, Any]] = []
    for img in inputs.get("images") or []:
        if not isinstance(img, dict):
            continue
        try:
            ts = float(img.get("ts"))
        except (TypeError, ValueError):
            continue
        src = Path(str(img.get("path") or "").strip())
        if not src.is_file():
            ctx.log(f"material-sink: 帧文件缺失，引用清单跳过: {src}")
            continue
        dst = durable_root / "images" / src.name
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():  # 同名帧（同 ts 重抽）零重写
                shutil.copyfile(src, dst)
        except OSError as exc:
            ctx.log(f"material-sink: 帧图持久化失败跳过: {src}（{exc}）")
            continue
        images_meta.append({"path": str(dst), "ts": ts})

    try:
        duration = float(article.get("video_duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    anchor_map = article.get("anchor_map")
    if not isinstance(anchor_map, dict):
        anchor_map = {}

    material = _mat_model.make_material(
        mtype="text",
        source_id=SOURCE_ID,
        uri=uri,
        title=str(article.get("title") or slug),
        license="AI-GENERATED",
        payload={
            "text": text,
            "meta": {
                "sub_type": SUB_TYPE,
                "ai_generated": True,
                "organize_mode": str(article.get("organize_mode") or "chronological"),
                "source_video": str(article.get("source_video") or ""),
                "video_duration": duration,
                "anchor_map": anchor_map,
                "images": images_meta,  # durable 副本路径（run LRU 剪除后仍可回跳）
                "article_path": str(durable_md),
            },
        },
        provenance={
            "chain": [{"from_id": run_id, "op": "plugin_pipeline"}],
            "derived_from": [],
        },
        triage=_initial_triage(config.get("triage"), ctx),
        fetched_at=_now_iso(),
    )

    try:
        row = _materials.add_material(material)
    except ValueError as exc:  # 校验失败（枚举/结构）→ 可读插件失败
        raise PluginError(f"素材落库被拒: {exc}") from exc
    ctx.log(
        f"material-sink: 落库 {row['id']}（sub_type={SUB_TYPE}, preview 态 "
        f"enabled=false, provenance←run {run_id}）"
    )
    return {"material": row["id"]}


__all__ = ["SOURCE_ID", "SUB_TYPE", "run"]
