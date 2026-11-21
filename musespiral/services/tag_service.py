"""自动标签建议 M0（Phase D F77）——preview/adopt 两动作，镜像 refine_service 形态。

飞轮红线同款（正典 §二-F77）：**AI 只建议，人工采纳才写 `triage.tags`**——

  - preview(material_id)：素材正文 → chat_policed(task="tag_suggest",
    cache=True) → 「TAG: 」行解析 → 服务端归一化+过滤 → ≤5 建议。
    同步返回、**素材库零落盘**（不建 job——job result 持久化即预览留痕，
    违反「拒绝即消失不留痕」refine 判例；缓存/台账是治理层派生状态可整体
    删除重建，不在留痕红线内——F79 红线的正面对象）；
  - adopt(material_id, tags)：人工勾选的建议集 → A 档 `core/materials/tags.py`
    归一化（唯一契约，本模块只消费不另立）→ 与既有 tags 求并集（既有优先）
    → 超 12 保序截断（被弃清单随结果返回）→ **经 triage 窄门写入**
    （materials_service.update_triage，全量替换语义；payload/provenance 恒
    不动——tags 是 triage 第四键）；
  - 拒绝=客户端不调 adopt；CLI 直接回车=全拒零落盘（预览态天然零痕迹）。

  - batch(material_ids)：`tag_batch` 计划经 interp.run_plan 一次跑 N 步
    （每素材一个 tag_suggest 步，cache=True；步 out=AI 原文，**解析在消费
    侧**——本模块对槽位文本逐行认前缀）。步数上限由 plan 校验裁决
    （budgets.RUN_STEPS=8），`tags batch --limit` 由 CLI 分批（每批 ≤8）。

协议解析容错（F77.5）：空行跳过 / 无前缀行忽略 / 规范化失败或规范化后
超 24 字符丢弃 / >5 取前 5 / 同值互撞先出现者胜（归一化后判定）。

冲突三档（F77.2，全在归一化之后判定）：
  ① 建议撞既有：并集天然去重——不重复写入、不报错（CLI/API 据下发的
     existing 自行标「已有」）；
  ② 建议互撞：解析期去重保序，先出现者胜；
  ③ 并集超限：既有优先+建议按序补足，保序截断至 12，被弃清单随 adopt
     结果返回（CLI 打印）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..core.agent.interp import run_plan
from ..core.agent.plans_builtin import build_tag_batch
from ..core.materials import tags as _tags
from ..infra.ai import TASKS, AIError, ai_configured
from ..infra.ai.gateway import chat_policed
from ..infra.ai.prompts import load_override
from . import materials_service, sources_service
from .refine_service import RefineError, _material_text

# 输出协议前缀（行首「TAG: 」；解析宽容带/不带尾随空格两种形态）
TAG_PREFIX = "TAG:"

# 建议条数上限（§二-F77：AI 建议 ≤5 个标签）
SUGGEST_MAX = 5

# 预览输入预算（字符）：素材正文节选 ≤4000（§二-F77.5；比 refine 的
# 20000 更紧——标签建议只需主题面，不需全文）
SUGGEST_INPUT_MAX_CHARS = 4000

# 既有标签词表 Top N（§二-F77.5：与 GET /api/materials/tags 同源
# tag_counts()，促复用防标签爆炸）
VOCAB_TOP_N = 200

# 任务底座模板（内置缺省；~/.musespiral/prompts.d/tag_suggest.txt 存在即整体
# 覆盖——F47 覆盖级联同纪律）。供应商无关：零模型/供应商字样（方言守门）。
DEFAULT_PROMPT = """你是素材库的标签编辑。请为下面这篇素材建议最多 5 个可复用标签。

输出协议（严格遵守）：每行一个标签，行首固定加「TAG: 」前缀，不加编号、不加解释、不写空行以外的内容。
要求：
- 标签简短（不超过 24 字符）、可复用（同一主题下多篇素材能共用）
- 优先复用【既有标签词表】中的标签，避免近义新造
- 只依据素材内容判断主题，不添加原文没有的主题"""

# 素材正文注入插槽（覆盖文件可含 {{material}} 控制注入位置；缺省追加在末尾）
_MATERIAL_SLOT = "{{material}}"

# chat_fn 注入点形态（透传 chat_policed；测试脚本化假件用）
ChatFn = Callable[..., str]


class TagError(RefineError):
    """标签建议业务错误（RefineError 族——素材缺失/无正文面共用可读口径）。"""


# ── prompt 组装（词表 + 正文节选）───────────────────────────────────


def _tag_vocab() -> list[str]:
    """既有标签词表（tag_counts 同源 Top N；F63 频次表只消费不另立口径）。"""
    return [row["name"] for row in materials_service.tag_counts()[:VOCAB_TOP_N]]


def _material_block(material: dict[str, Any]) -> str:
    """组装建议材料块：正文节选（≤4000）+ 既有标签词表。

    正文提取沿 refine_service._material_text 口径（payload.text >
    payload.local_path@KB 根；radar_signal/link 无正文面 → RefineError
    族可读错误）。"""
    body = _material_text(material)
    if len(body) > SUGGEST_INPUT_MAX_CHARS:
        body = body[:SUGGEST_INPUT_MAX_CHARS]
    title = str((material.get("origin") or {}).get("title") or material["id"])
    lines = [f"【素材正文】（title: {title}）", body]
    vocab = _tag_vocab()
    if vocab:
        lines.append(f"【既有标签词表】（优先复用）：" + "、".join(vocab))
    return "\n".join(lines)


def _build_prompt(block: str, template: str | None = None) -> tuple[str, str]:
    """组装预览 prompt → `(prompt, 生效模板)`：源级 prompt_override > 覆盖
    文件（prompts.d/tag_suggest.txt）> 内置缺省；{{material}} 插槽存在则原位
    注入，否则材料块追加在末尾。生效模板随 prompt 一起返回，供 gateway
    `template` 参（F79.1：template_sha=sha1(生效模板)——内置缺省变更也会使
    缓存失效）。"""
    base = template if template else (load_override("tag_suggest") or DEFAULT_PROMPT)
    if _MATERIAL_SLOT in base:
        return base.replace(_MATERIAL_SLOT, block), base
    return f"{base}\n\n{block}", base


# ── 输出协议解析（纯函数，消费侧）──────────────────────────────────


def parse_suggestions(text: Any) -> list[str]:
    """AI 原文 → 规范化建议清单（≤5；容错规则见模块 docstring）。

    归一化走 A 档 `tags.normalize_tag`（小写/非法字符转连字符/幂等），
    与采纳写面同一契约——建议即预览承诺（回车采纳不做二次变形）。"""
    if not isinstance(text, str):
        return []
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith(TAG_PREFIX):
            continue  # 空行跳过；无前缀行忽略
        raw = stripped[len(TAG_PREFIX):].strip()
        if not raw:
            continue
        try:
            tag = _tags.normalize_tag(raw)
        except ValueError:
            continue  # 规范化后为空（全非法字符）→ 丢弃
        if len(tag) > _tags.MAX_TAG_CHARS:
            continue  # 规范化后超 24 字符 → 丢弃（建议解析期容错，非冲突档）
        if tag not in out:
            out.append(tag)  # 同值互撞：先出现者胜
        if len(out) >= SUGGEST_MAX:
            break  # >5 取前 5
    return out


# ── 预览（零落盘）────────────────────────────────────────────────────


def _existing_tags(material: dict[str, Any]) -> list[str]:
    """素材既有 tags（宽容读：脏数据按无标签）。"""
    tags = (material.get("triage") or {}).get("tags")
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str) and t]


def preview(material_id: str, *, chat_fn: ChatFn | None = None) -> dict[str, Any]:
    """AI 标签建议预览：chat_policed(task="tag_suggest", cache=True) 同步
    起草，素材库零落盘（cache=True——同素材重建议=缓存命中零网络，§八-3）。

    返回 {"id", "title", "tags": ≤5 建议, "existing": 既有标签}——tags 已
    服务端归一化+过滤（≤24 字符/去重保序）；existing 供 CLI/UI 标冲突档。
    素材不存在/已删、无正文面 → TagError（RefineError 族可读）；AI 未配置/
    调用失败 → AIError。
    """
    material = materials_service.get_material(material_id)
    if material is None:
        raise TagError(f"素材不存在或已删: {material_id!r}")
    try:
        block = _material_block(material)
    except RefineError as err:
        raise TagError(str(err)) from None
    ai_cfg = sources_service.resolve_ai_config((material.get("origin") or {}).get("source_id"))
    profile = ai_cfg.get("profile")
    if profile is not None and not ai_configured("tag_suggest", profile=profile):
        ai_cfg.pop("profile", None)
        profile = None
    if not ai_configured("tag_suggest", profile=profile):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    prompt, effective_template = _build_prompt(block, ai_cfg.get("prompt_override"))
    res = chat_policed(
        [{"role": "user", "content": prompt}],
        task="tag_suggest",
        cache=True,
        template=effective_template,
        chat_fn=chat_fn,
        **sources_service.override_kwargs(ai_cfg),
    )
    return {
        "id": material["id"],
        "title": str((material.get("origin") or {}).get("title") or material["id"]),
        "tags": parse_suggestions(res.get("text")),
        "existing": _existing_tags(material),
    }


# ── 批量（tag_batch 计划，消费 interp）──────────────────────────────


def batch(material_ids: list[str], *, chat_fn: ChatFn | None = None) -> dict[str, Any]:
    """批量标签建议：`tag_batch` 计划（每素材一个 tag_suggest 步）经
    interp.run_plan 一次跑完，槽位文本逐个解析为建议。

    素材数上限由 plan 校验裁决（步数上限 =interp 预算 RUN_STEPS）——调用方
    分批。素材缺失 →
    TagError（构建期可读拒绝，零步执行）。返回 {"status", "error",
    "budget", "items"}；items 每项 {"id", "title", "tags", "existing"}，
    budget_stopped/failed 态下未产出槽位按「无建议」呈现（不吞 run 级
    状态——CLI 据此提示）。
    """
    ids = [mid.strip() for mid in material_ids]
    materials: list[dict[str, Any]] = []
    for mid in ids:
        material = materials_service.get_material(mid)
        if material is None:
            raise TagError(f"素材不存在或已删: {mid!r}")
        materials.append(material)
    # N-3：批量只在全部素材同源时吃源级覆盖（多源批次无「一个源配置」语义）
    ai_cfg = sources_service.ai_overrides(materials)
    profile = ai_cfg.get("profile")
    if profile is not None and not ai_configured("tag_suggest", profile=profile):
        ai_cfg.pop("profile", None)
    if not ai_configured("tag_suggest", profile=ai_cfg.get("profile")):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    blocks: list[str] = []
    for material in materials:
        try:
            blocks.append(_material_block(material))
        except RefineError as err:
            raise TagError(f"{material['id']}: {err}") from None
    # F47 覆盖级联 + 生效模板透传（agent_service.apply_prompt_overrides 同款
    # 纪律：interp 是 L2 纯函数，级联在服务层组装期完成；gateway 的
    # template 参按 F79.1 口径吃真实生效文本）。N-3：源级 prompt_override
    # 优先级高于全局覆盖——批量同源时整批底座被源级文本接管（{{material}}
    # 插槽若在源级文本里，interp 的槽位装配会原位替换）。
    from .agent_service import apply_prompt_overrides, step_templates

    plan = apply_prompt_overrides(build_tag_batch(ids))
    if ai_cfg.get("prompt_override"):
        plan = {
            **plan,
            "steps": [
                {**step, "inputs": {**(step.get("inputs") or {}), "instruction": ai_cfg["prompt_override"]}}
                if isinstance(step.get("inputs"), dict) else step
                for step in plan["steps"]
            ],
        }
    templates = step_templates(plan)
    overrides = sources_service.override_kwargs(ai_cfg)

    def _gateway_step(prompt: str, *, task: str, cache: bool) -> dict[str, Any]:
        return chat_policed(
            [{"role": "user", "content": prompt}],
            task=task,
            cache=cache,
            template=templates.get(task),
            chat_fn=chat_fn,
            **overrides,
        )

    result = run_plan(plan, {"materials": blocks, "params": {}}, _gateway_step, tasks=TASKS)
    items = [
        {
            "id": material["id"],
            "title": str((material.get("origin") or {}).get("title") or material["id"]),
            "tags": parse_suggestions(result["slots"].get(f"tags_{i}", "")),
            "existing": _existing_tags(material),
        }
        for i, material in enumerate(materials, start=1)
    ]
    return {
        "status": result["status"],
        "error": result["error"],
        "budget": result["budget"],
        "items": items,
    }


# ── 采纳（人工勾选 → triage 窄门）───────────────────────────────────


def adopt(material_id: str, tags: list[str]) -> dict[str, Any]:
    """采纳建议集写入 triage.tags（唯一写面；经 A 档窄门，消费不另立）。

      - 采纳集逐个走 `tags.normalize_tag`（A 档唯一契约；规范化失败 →
        TagError 可读拒绝）；
      - 与既有 tags 求并集：既有优先+建议按序补足，保序去重（建议撞既有
        天然不重复写入——冲突档①）；
      - 超 12 上限保序截断至 `tags.MAX_TAGS`，被弃清单随结果返回（冲突
        档③，CLI 打印）；
      - 写入走 materials_service.update_triage（triage_patch 窄门——越键
        /超限在窄门仍会被拒；payload/provenance 恒不动）。

    返回 {"ok", "id", "tags": 回写后完整 tags, "added": 新增, "dropped": 被弃}。
    素材不存在/已删、采纳集非字符串列表 → TagError。
    """
    material = materials_service.get_material(material_id)
    if material is None:
        raise TagError(f"素材不存在或已删: {material_id!r}")
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise TagError("采纳标签集必须是字符串列表（预览下发的 tags 原样勾选回传）")
    chosen: list[str] = []
    for raw in tags:
        if not raw.strip():
            continue  # 空串宽容跳过（预览列表不会被切出空串；防御手拼输入）
        try:
            tag = _tags.normalize_tag(raw)
        except ValueError as err:
            raise TagError(f"标签归一化失败: {err}") from None
        if tag not in chosen:
            chosen.append(tag)
    existing: list[str] = []
    for raw in _existing_tags(material):
        try:
            tag = _tags.normalize_tag(raw)
        except ValueError:
            continue  # 存量脏数据宽容跳过（窄门写面本不该产生）
        if tag not in existing:
            existing.append(tag)
    union = existing + [t for t in chosen if t not in existing]  # 既有优先
    kept = union[: _tags.MAX_TAGS]
    dropped = union[_tags.MAX_TAGS:]
    row = materials_service.update_triage(material_id, {"tags": kept})
    final = [t for t in (row.get("triage") or {}).get("tags") or [] if isinstance(t, str)]
    added = [t for t in final if t not in existing]
    return {"ok": True, "id": material_id, "tags": final, "added": added, "dropped": dropped}
