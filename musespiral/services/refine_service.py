"""加工动作 M0（第二十期 F57）——chat(task="refine") 摘要预览 + 采纳落 derived。

飞轮关键（本期正典红线）：**AI 只起草，人工采纳才落库**——

  - preview(material_id)：素材正文 → chat(task="refine") → 预览文本。
    同步返回、**零落盘**（不进 jobs 层——job result 会持久化到 jobs.jsonl，
    预览文本即留痕，违反正典 §四-Q3「derived 拒绝即消失不留痕」；本地
    单人 Studio 是 ThreadingHTTPServer，同步 AI 调用不阻塞其他请求）；
  - adopt(material_id, text)：人工「存为素材」动作 → type=derived 素材
    落库——provenance.chain=[{from_id, op:"summarize"}]（血统只由采纳
    动作写入，immutable 红线）、license 继承源素材（含 payload.meta.
    license_note 声明原文随血统传递）、origin.source_id 继承源素材的
    信息源；
  - 拒绝=客户端不调 adopt——服务端无拒绝动作面（预览态天然零痕迹）。

P2 素材再加工循环（2026-11-20，正典=《musespiral-格式生态与素材流转能力分析-
2026-11-20》§3.3）：adopt 默认 as_iteration=True 时向 payload.meta 写
model.META_ITERATION_KEYS 三键（iteration/iteration_root/iteration_of），
表达「v2 基于 v1」的打磨链——provenance.chain 仍只指直接源（血统准确性
不破）；list_iterations/diff_iterations 提供版本列表与两版对比（diff 复用
core/version/diff.diff_result，不另写 diff 算法）。

素材正文提取（M0 口径）：
  - payload.text（crawled/derived 等自带正文）；
  - payload.local_path（kb 类引用不复制红线——正文在 KB 根下原文，读时
    解析：显式根 > sources.json kb 条目 config.root > config.knowledge_root()
    级联，与 materials_service._kb_root_for_scoring 同序）；
  - radar_signal/link 无正文面 → ValueError（可读错误，UI 引导）。

超长输入保守截断 REFINE_INPUT_MAX_CHARS（chat 输入预算的 M0 缺省；
不截断的全文摘要属后续动作面）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..core.materials import model
from ..core.version.diff import diff_result as _diff_result
from ..core.version.diff import render_diff_html as _render_diff_html
from ..infra.ai import AIError
from ..infra.ai import ai_configured
from ..infra.ai import chat as _chat
from ..infra.ai.prompts import load_override
from ..jobs.store import now_iso as _now_iso
from . import material_body, materials_service, sources_service

# F57 M0 唯一加工动作（model.PROVENANCE_OPS 首键；后续动作经该表扩）
REFINE_OP = "summarize"

# 预览输入预算（字符）：kb 长文保守截断（正典 §二-F57 无预算约束，M0
# 缺省防单次对话塞整本书；截断只影响摘要输入不影响原文）
REFINE_INPUT_MAX_CHARS = 20_000

# 任务底座模板（内置缺省；~/.musespiral/prompts.d/refine.txt 存在即整体覆盖
# ——F47 覆盖级联同纪律）。供应商无关：零模型/供应商字样（方言守门）。
DEFAULT_PROMPT = """你是资深内容编辑。请把给到的素材正文提炼成一段结构化中文摘要，供公众号写作时作为二手素材引用。

要求：
- 300 字以内，先一句话说清素材是什么，再给 3-5 条关键信息点（每条一行，以「· 」开头）
- 保留具体事实（数字、名称、版本），不添加原文没有的推断
- 结尾单独一行以「> 」开头标注这是摘要（引用时须回溯原文核实）"""

# 素材正文注入插槽（覆盖文件可含 {{material}} 控制注入位置；缺省追加在末尾）
_MATERIAL_SLOT = "{{material}}"


class RefineError(ValueError):
    """加工业务错误（可读消息；Studio 转 400/ok:false）。"""


def _material_text(material: dict[str, Any]) -> str:
    """提取素材可摘正文（**公共抽取**：`services/material_body.material_text`）。

    Phase D F76.2 表「正文抽取 → 抽公共复用同口径（本期动作）」：抽取级联
    （payload.text > payload.local_path@KB 根）与 F57 原实现逐字同源，本函数
    退化为「RefineError 域包装 + refine 输入预算截断」两层薄壳——综合（F76）
    与标签（F77）走同一函数，不各自复刻一份抽取规则。

    无正文面（radar_signal/link 等只有引用元数据）→ RefineError 可读错误。
    超长保守截断（REFINE_INPUT_MAX_CHARS）。
    """
    try:
        return material_body.material_text(material, max_chars=REFINE_INPUT_MAX_CHARS)
    except material_body.MaterialTextError as exc:
        raise RefineError(str(exc)) from None


def _build_prompt(material: dict[str, Any], body: str, template: str | None = None) -> str:
    """组装预览 prompt：源级 prompt_override > 覆盖文件（prompts.d/refine.txt）
    > 内置缺省；{{material}} 插槽存在则原位注入，否则正文追加在末尾。

    template 非空时即接管底座（N-3 源级 prompt_override 优先级最高——「这个
    源的内容特性需要特定处理」比全局覆盖更贴切）。
    """
    base = template if template else (load_override("refine") or DEFAULT_PROMPT)
    block = f"【素材正文】（title: {material.get('origin', {}).get('title') or material['id']}）\n{body}"
    if _MATERIAL_SLOT in base:
        return base.replace(_MATERIAL_SLOT, block)
    return f"{base}\n\n{block}"


# ── 预览（零落盘）────────────────────────────────────────────────────


def preview(material_id: str) -> dict[str, Any]:
    """AI 摘要预览：chat(task="refine") 同步起草，绝不落盘。

    返回 {"id", "title", "op", "text"}——text 即 AI 草稿（采纳时原样回传，
    服务端不存中间态）。素材不存在/已删、无正文面 → RefineError；
    AI 未配置/调用失败 → AIError（可读引导，同 annotate_rewrite 文案）。

    **N-3 读侧覆盖**：素材 origin.source_id 命中源级 ai_config 时，四键
    （profile/model/temperature/prompt_override）覆盖全局——只作用于本加工
    路径，文章生成（compose generate）不读源配置（裁决见《总体方案》N-3）。
    """
    material = materials_service.get_material(material_id)
    if material is None:
        raise RefineError(f"素材不存在或已删: {material_id!r}")
    body = _material_text(material)
    ai_cfg = _source_ai_cfg(material)
    profile = ai_cfg.get("profile")
    if profile is not None and not ai_configured("refine", profile=profile):
        # 源指定档案失效（被删/未配）→ 回落全局路由（读侧宽容纪律）
        ai_cfg.pop("profile", None)
        profile = None
    if not ai_configured("refine", profile=profile):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    text = _chat(
        [{"role": "user", "content": _build_prompt(material, body, ai_cfg.get("prompt_override"))}],
        task="refine",
        **sources_service.override_kwargs(ai_cfg),
    )
    return {
        "id": material["id"],
        "title": str((material.get("origin") or {}).get("title") or material["id"]),
        "op": REFINE_OP,
        "text": text,
    }


def _source_ai_cfg(material: dict[str, Any]) -> dict[str, Any]:
    """素材源级 AI 覆盖（无源/无配置 → {}）。薄壳收口本模块唯一入口。"""
    return sources_service.resolve_ai_config((material.get("origin") or {}).get("source_id"))


# ── 采纳（人工「存为素材」）─────────────────────────────────────────


def _iter_meta(material: dict[str, Any]) -> dict[str, Any]:
    """素材 payload.meta（非 dict → {}；只读视图不落盘）。"""
    payload = material.get("payload") or {}
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


def _iter_no(material: dict[str, Any]) -> int | None:
    """素材的 iteration 版本号（缺键/非 int → None；bool 不算 int）。"""
    it = _iter_meta(material).get("iteration")
    if isinstance(it, bool) or not isinstance(it, int):
        return None
    return it


def _chain_root(material: dict[str, Any]) -> str:
    """迭代链根判定（P2 契约）：素材已是 derived 且带非空 iteration_root →
    继承它；否则该素材自身即链根（原始素材 / 无迭代键的存量 derived 同判）。
    """
    if material.get("type") == "derived":
        root = _iter_meta(material).get("iteration_root")
        if isinstance(root, str) and root.strip():
            return root.strip()
    return str(material["id"])


def _chain_versions(root: str) -> list[dict[str, Any]]:
    """链上既有版本：活跃 derived 且 meta.iteration_root == root、iteration
    为合法整数；按 iteration 升序（同分 fetched_at/id 稳定排序）。

    无迭代键的存量素材（含链根本体若是原始素材）不入版本列——读侧
    「未携带三键 = 无迭代史」，零回归口径。
    """
    out: list[dict[str, Any]] = []
    for m in materials_service.list_materials():
        if m.get("type") != "derived":
            continue
        if str(_iter_meta(m).get("iteration_root") or "") != root:
            continue
        if _iter_no(m) is None:
            continue
        out.append(m)
    out.sort(key=lambda m: (_iter_no(m) or 0, str(m.get("fetched_at") or ""), m["id"]))
    return out


def adopt(
    material_id: str,
    text: str,
    *,
    title: str | None = None,
    as_iteration: bool = True,
) -> dict[str, Any]:
    """采纳预览草稿为 derived 素材（唯一写血统的动作面，F57 红线）。

      - type=derived；provenance.chain=[{from_id: 源素材 id, op:"summarize"}]
        （derived_from 保持空——M1 模型测试锚定的 F57 形态）；
      - license 继承源素材，payload.meta.license_note 声明原文随血统传递；
      - origin.source_id 继承源素材的信息源、uri=refine://<源id>/<采纳ts>/
        <正文指纹8位>（同一源多次采纳各成一行，不撞 immutable 幂等——ts
        秒级粒度下同秒两采靠正文指纹区分；同秒同文的重复采纳幂等收敛）；
      - 正文即预览 text（人工所见即所存——服务端不重跑 chat，避免采纳
        内容与预览不一致）。

    as_iteration=True（P2 缺省）：按链根判定写 META_ITERATION_KEYS 三键——
    iteration=链上既有最大版本号+1（空链即 1）、iteration_root=_chain_root
    （源头 derived 带根则继承，否则源头自身）、iteration_of=链上最新一版
    的 id（v1 为 None）。provenance.chain 不变（仍指 material_id）。
    as_iteration=False：保持 P2 之前的旧行为——meta 不写迭代键。

    素材不存在/已删、text 空 → RefineError（可读错误）。
    """
    source = materials_service.get_material(material_id)
    if source is None:
        raise RefineError(f"素材不存在或已删: {material_id!r}")
    if not isinstance(text, str) or not text.strip():
        raise RefineError("采纳正文不能为空（预览文本原样回传）")
    origin = source.get("origin") or {}
    src_payload = source.get("payload") or {}
    src_meta = src_payload.get("meta") if isinstance(src_payload.get("meta"), dict) else {}
    body = text.strip()
    ts = _now_iso()
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    payload: dict[str, Any] = {"text": body}
    note = src_meta.get("license_note")
    if isinstance(note, str) and note.strip():
        payload["meta"] = {"license_note": note.strip()}
    if as_iteration:
        root = _chain_root(source)
        versions = _chain_versions(root)
        prev = versions[-1] if versions else None
        iteration = _iter_no(prev) + 1 if prev else 1
        meta = payload.setdefault("meta", {})
        meta["iteration"] = iteration
        meta["iteration_root"] = root
        meta["iteration_of"] = prev["id"] if prev else None
        # 迭代态 uri 追加 /v<n>：同秒同文的再采纳是**新一版**（meta 不同）
        # 而非旧行为的同 id 幂等收敛——版本号入 uri 使各版各成一行；
        # as_iteration=False 保持旧 uri 形状（向后兼容硬要求）。
        version_suffix = f"/v{iteration}"
    else:
        version_suffix = ""
    row = model.make_material(
        mtype="derived",
        source_id=str(origin.get("source_id") or "refine"),
        uri=f"refine://{material_id}/{ts}/{digest}{version_suffix}",
        title=(title and title.strip()) or f"摘要 · {origin.get('title') or material_id}",
        license=str(source.get("license") or "UNKNOWN"),
        payload=payload,
        provenance={"chain": [{"from_id": material_id, "op": REFINE_OP}], "derived_from": []},
        fetched_at=ts,
    )
    saved = materials_service.add_material(row)
    return {"ok": True, "id": saved["id"], "material": saved}


# ── 迭代链读面（P2：版本列表 + 两版对比）────────────────────────────


def list_iterations(material_id: str) -> dict[str, Any]:
    """版本列表：同一链根下所有版本，按 iteration 升序。

    入参可以是链上任一素材（原始根 / 任一版本）——链根判定同 adopt。
    返回 {"root", "versions":[{id, iteration, ts, title, chars}]}；chars=
    payload.text 全长（不截断——列表是展示面不是 AI 输入预算面）。
    素材不存在/已删 → RefineError。
    """
    material = materials_service.get_material(material_id)
    if material is None:
        raise RefineError(f"素材不存在或已删: {material_id!r}")
    root = _chain_root(material)
    versions = [
        {
            "id": m["id"],
            "iteration": _iter_no(m) or 0,
            "ts": str(m.get("fetched_at") or ""),
            "title": str((m.get("origin") or {}).get("title") or m["id"]),
            "chars": len(str((m.get("payload") or {}).get("text") or "")),
        }
        for m in _chain_versions(root)
    ]
    return {"root": root, "versions": versions}


def _full_text(material: dict[str, Any]) -> str:
    """对比用全文（不截断——diff 是对照面不是 AI 输入预算面；
    无正文面 → RefineError 可读拒绝）。"""
    try:
        return material_body.material_text(material)
    except material_body.MaterialTextError as exc:
        raise RefineError(str(exc)) from None


def diff_iterations(id_a: str, id_b: str) -> dict[str, Any]:
    """两版对比：复用 core/version/diff.diff_result（不另写 diff 算法）。

    两者须同链根（任一侧不存在/已删、或链根不同 → RefineError 可读拒绝）。
    返回 {"a","b","diff"}——a/b 为 {id, iteration, title} 最小标注；diff 为
    DiffResult.to_dict()（hunks/added/removed/identical）+ 服务端渲染 html
    （render_diff_html 红删绿增，E-6 /api/article/diff 同款产物面）。
    """
    a = materials_service.get_material(id_a)
    if a is None:
        raise RefineError(f"素材不存在或已删: {id_a!r}")
    b = materials_service.get_material(id_b)
    if b is None:
        raise RefineError(f"素材不存在或已删: {id_b!r}")
    root_a, root_b = _chain_root(a), _chain_root(b)
    if root_a != root_b:
        raise RefineError(
            f"跨迭代链对比被拒绝: {id_a!r}（链根 {root_a}）与 {id_b!r}（链根 {root_b}）不同链"
        )
    no_a, no_b = _iter_no(a), _iter_no(b)
    label_a = f"v{no_a}" if no_a is not None else id_a
    label_b = f"v{no_b}" if no_b is not None else id_b
    result = _diff_result(
        _full_text(a),
        _full_text(b),
        from_label=label_a,
        to_label=label_b,
    )
    return {
        "a": {"id": a["id"], "iteration": no_a, "title": str((a.get("origin") or {}).get("title") or a["id"])},
        "b": {"id": b["id"], "iteration": no_b, "title": str((b.get("origin") or {}).get("title") or b["id"])},
        "diff": {
            **result.to_dict(),
            "html": _render_diff_html(result, from_label=label_a, to_label=label_b),
        },
    }
