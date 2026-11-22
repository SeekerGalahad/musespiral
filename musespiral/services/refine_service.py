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

AI-3 重生成（2026-11-21，正典=《mdpub-AI产物裁决机制设计-2026-11-21》§2.3/
§三.3——裁决三件套 adopt/regenerate/discard 的中间件）：regenerate
(material_id, feedback=None) = 带 feedback 再 preview 一次 + adopt——
feedback 拼进 prompt 尾部（「用户改进要求：{feedback}」），旧版不动、新版
追加进同一条 REFINE-ITER 版本链；preview 相应增 feedback 参数（regenerate
透传用）。零新落库路径：版本链三键仍由 adopt 自算。

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

from .. import config
from ..core.materials import model
from ..core.materials.confidence import parse_ai_confidence  # N-4 末行自评解析
from ..core.version.diff import diff_result as _diff_result  # noqa: F401（ARCH-2 迁移后保留拆分前模块面）
from ..core.version.diff import render_diff_html as _render_diff_html  # noqa: F401（同上）
from ..infra.ai import AIError
from ..infra.ai import ai_configured
from ..infra.ai import chat as _chat
from ..infra.ai.prompts import load_override
from ..jobs.store import now_iso as _now_iso
from . import material_body, materials_service, sources_service
# ARCH-2（2026-11-22）：迭代链族迁入 services/materials/iterations（它操作
# 素材库，落点归素材域）。本模块 re-import 保住拆分前符号面——payload 与
# 测试的 refine_service.list_iterations/diff_iterations 调用面零改动；
# _chain_* 四助手是 adopt 写链的判定核，消费照旧。RefineError 的单一事实
# 源仍是本模块（iterations 函数体内导入同一类对象）。
from .materials.iterations import (  # noqa: F401（list/diff/_full_text 为门面转出）
    _chain_root,
    _chain_versions,
    _full_text,
    _iter_meta,
    _iter_no,
    diff_iterations,
    list_iterations,
)

# F57 M0 唯一加工动作（model.PROVENANCE_OPS 首键；后续动作经该表扩）
REFINE_OP = "summarize"

# 预览输入预算（字符）：kb 长文保守截断（正典 §二-F57 无预算约束，M0
# 缺省防单次对话塞整本书；截断只影响摘要输入不影响原文）
REFINE_INPUT_MAX_CHARS = 20_000

# 任务底座模板（内置缺省；~/.musespiral/prompts.d/refine.txt 存在即整体覆盖
# ——F47 覆盖级联同纪律）。供应商无关：零模型/供应商字样（方言守门）。
# N-4（2026-11-21）：末行 CONFIDENCE 自评——preview 解析剥行后回传，
# 采纳写 triage.confidence；AI 不带该行时 confidence=None 不阻断面。
DEFAULT_PROMPT = """你是资深内容编辑。请把给到的素材正文提炼成一段结构化中文摘要，供公众号写作时作为二手素材引用。

要求：
- 300 字以内，先一句话说清素材是什么，再给 3-5 条关键信息点（每条一行，以「· 」开头）
- 保留具体事实（数字、名称、版本），不添加原文没有的推断
- 结尾单独一行以「> 」开头标注这是摘要（引用时须回溯原文核实）
- 最后单独加一行标注你对该摘要忠实概括原文的把握，格式固定为「CONFIDENCE: 0.8」（0-1 小数；不确定时给低值）——该行不算摘要正文"""

# 素材正文注入插槽（覆盖文件可含 {{material}} 控制注入位置；缺省追加在末尾）
_MATERIAL_SLOT = "{{material}}"

# FEAT-1 ③（2026-11-22）：refine prompt 尾部附带的相关历史素材摘要。
# top-3（正典 §FEAT-1 第 6 条）；每条摘要截断预算防 prompt 膨胀（语义相关
# 是「提示线索」不是「第二份正文」，仅给 AI 一个方向）。开关=config.semantic_context。
RELATED_CONTEXT_TOP = 3
RELATED_SUMMARY_CHARS = 160


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


def _related_context(material_id: str) -> str:
    """FEAT-1 ③：组装「相关历史素材」prompt 块（无相关/开关关闭/embedding 缺位 → ""）。

    纯增强面——search_service.related 内部已吞 embedding 缺位/空索引，本函数
    再包一层容错（任何异常都不让相关推荐毁掉 AI 加工主路径）；摘要取相关素材
    payload.text 首段（无正文面则退回标题），每条截 RELATED_SUMMARY_CHARS。
    """
    if not config.semantic_context_enabled():
        return ""
    from . import search_service  # 函数体内导入（导入期解耦）

    try:
        related = search_service.related(material_id, limit=RELATED_CONTEXT_TOP)
    except Exception:
        return ""  # 相关推荐是可失败的增强面——永不阻断 refine
    lines: list[str] = []
    for item in related:
        mid = item.get("id")
        full = materials_service.get_material(mid) if mid else None
        if full is None:
            continue
        try:
            summary = material_body.material_text(full, max_chars=RELATED_SUMMARY_CHARS)
        except material_body.MaterialTextError:
            summary = str(item.get("title") or mid or "")
        if summary.strip():
            lines.append(f"· [{item.get('title') or mid}] {summary.strip()}")
    if not lines:
        return ""
    return "\n\n相关历史素材（语义相关，仅供提炼参考，非本素材正文）：\n" + "\n".join(lines)


def _build_prompt(
    material: dict[str, Any],
    body: str,
    template: str | None = None,
    feedback: str | None = None,
    related_context: str = "",
) -> str:
    """组装预览 prompt：源级 prompt_override > 覆盖文件（prompts.d/refine.txt）
    > 内置缺省；{{material}} 插槽存在则原位注入，否则正文追加在末尾。

    template 非空时即接管底座（N-3 源级 prompt_override 优先级最高——「这个
    源的内容特性需要特定处理」比全局覆盖更贴切）。

    feedback 非空（去空白后）时拼在**最终 prompt 尾部**（形如
    「用户改进要求：{feedback}」）——AI-3 重生成的自然语言改进要求，插槽/
    覆盖文件/源级 override 均不影响其落位（指令永远在素材正文之后）。

    related_context 非空（FEAT-1 ③）时拼在**素材正文之后、feedback 之前**——
    相关历史素材是提炼线索（在正文之后避免被误当主素材），用户改进要求永远最后。
    """
    base = template if template else (load_override("refine") or DEFAULT_PROMPT)
    block = f"【素材正文】（title: {material.get('origin', {}).get('title') or material['id']}）\n{body}"
    if _MATERIAL_SLOT in base:
        prompt = base.replace(_MATERIAL_SLOT, block)
    else:
        prompt = f"{base}\n\n{block}"
    if related_context:
        prompt = f"{prompt}\n\n{related_context}"
    if feedback and feedback.strip():
        prompt = f"{prompt}\n\n用户改进要求：{feedback.strip()}"
    return prompt


# ── 预览（零落盘）────────────────────────────────────────────────────


def preview(material_id: str, *, feedback: str | None = None) -> dict[str, Any]:
    """AI 摘要预览：chat(task="refine") 同步起草，绝不落盘。

    返回 {"id", "title", "op", "text", "confidence"}——text 即 AI 草稿**原样**
    （末行含 CONFIDENCE 自评标记——**preview 不剥行**：标记行随可编辑预览
    一并呈现，剥离动作归 adopt（采纳时解析落 triage.confidence 并从正文
    剔除）；人工所见即所存语义不破——用户删掉该行 = 放弃自评，宽容不阻断）；
    confidence=AI 自评 0-1（N-4；AI 未带该行/值非法 → None——**容错不阻断
    面**）。素材不存在/已删、无正文面 → RefineError；AI 未配置/调用失败 →
    AIError（可读引导，同 annotate_rewrite 文案）。

    **N-3 读侧覆盖**：素材 origin.source_id 命中源级 ai_config 时，四键
    （profile/model/temperature/prompt_override）覆盖全局——只作用于本加工
    路径，文章生成（compose generate）不读源配置（裁决见《总体方案》N-3）。

    feedback（AI-3，可选）：自然语言改进要求（如「更简洁」「保留原话更多」），
    拼在 prompt 尾部——regenerate 透传用，手动 preview 亦可带。
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
        [{"role": "user", "content": _build_prompt(
            material, body, ai_cfg.get("prompt_override"), feedback=feedback,
            related_context=_related_context(material_id),
        )}],
        task="refine",
        **sources_service.override_kwargs(ai_cfg),
    )
    # N-4：解析末行 CONFIDENCE 自评（只读不剥——剥行归 adopt；无标记 → None）
    _, confidence = parse_ai_confidence(text)
    return {
        "id": material["id"],
        "title": str((material.get("origin") or {}).get("title") or material["id"]),
        "op": REFINE_OP,
        "text": text,
        "confidence": confidence,
    }


def _source_ai_cfg(material: dict[str, Any]) -> dict[str, Any]:
    """素材源级 AI 覆盖（无源/无配置 → {}）。薄壳收口本模块唯一入口。"""
    return sources_service.resolve_ai_config((material.get("origin") or {}).get("source_id"))


# ── 采纳（人工「存为素材」）─────────────────────────────────────────
# （迭代链判定四助手 _iter_meta/_iter_no/_chain_root/_chain_versions 已随
#   ARCH-2 迁入 services/materials/iterations，本模块头部 re-import 消费。）


def adopt(
    material_id: str,
    text: str,
    *,
    title: str | None = None,
    as_iteration: bool = True,
    confidence: float | None = None,
) -> dict[str, Any]:
    """采纳预览草稿为 derived 素材（唯一写血统的动作面，F57 红线）。

      - type=derived；provenance.chain=[{from_id: 源素材 id, op:"summarize"}]
        （derived_from 保持空——M1 模型测试锚定的 F57 形态）；
      - license 继承源素材，payload.meta.license_note 声明原文随血统传递；
      - origin.source_id 继承源素材的信息源、uri=refine://<源id>/<采纳ts>/
        <正文指纹8位>（同一源多次采纳各成一行，不撞 immutable 幂等——ts
        秒级粒度下同秒两采靠正文指纹区分；同秒同文的重复采纳幂等收敛）；
      - 正文即预览 text，**采纳时解析并剥除末行 CONFIDENCE 自评标记**（N-4：
        preview 原样带标记行，本动作剥离落 triage.confidence 后存正文——
        入库正文不含自评行；用户编辑后标记行消失 → 自评 None，宽容不阻断）；
      - confidence（N-4）：显式入参优先（API 直调面），缺省取正文标记行
        解析值；两者皆无 → None。服务端不重算——AI 只起草红线不变。

    as_iteration=True（P2 缺省）：按链根判定写 META_ITERATION_KEYS 三键——
    iteration=链上既有最大版本号+1（空链即 1）、iteration_root=_chain_root
    （源头 derived 带根则继承，否则源头自身）、iteration_of=链上最新一版
    的 id（v1 为 None）。provenance.chain 不变（仍指 material_id）。
    as_iteration=False：保持 P2 之前的旧行为——meta 不写迭代键。

    素材不存在/已删、text 空、confidence 非 0-1 数值 → RefineError（可读错误）。
    """
    source = materials_service.get_material(material_id)
    if source is None:
        raise RefineError(f"素材不存在或已删: {material_id!r}")
    if not isinstance(text, str) or not text.strip():
        raise RefineError("采纳正文不能为空（预览文本原样回传）")
    # N-4：剥末行自评标记（正文即所存的剥离点；剥离后空正文仍拒绝）
    body, conf_from_text = parse_ai_confidence(text)
    body = body.strip()
    if not body:
        raise RefineError("采纳正文不能为空（剥离自评行后无正文）")
    if confidence is None:
        confidence = conf_from_text
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not (0.0 <= float(confidence) <= 1.0)
    ):
        raise RefineError(f"confidence 必须是 0-1 数值或省略: {confidence!r}")
    origin = source.get("origin") or {}
    src_payload = source.get("payload") or {}
    src_meta = src_payload.get("meta") if isinstance(src_payload.get("meta"), dict) else {}
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
        triage={"confidence": float(confidence) if confidence is not None else None},
        fetched_at=ts,
    )
    saved = materials_service.add_material(row)
    return {"ok": True, "id": saved["id"], "material": saved}


def regenerate(material_id: str, *, feedback: str | None = None) -> dict[str, Any]:
    """重生成（AI-3，裁决三件套最后一件）：AI 重新起草一版并直接采纳落库——
    **旧版不动作**（追加式进链，随时 diff_iterations 对比）。

    实现即「带 feedback 再 preview 一次 + adopt」（正典 §三.3——preview/adopt
    是既有能力，regenerate 不另起草/落库路径）：

      - preview(material_id, feedback=feedback)：feedback 非空时拼进 prompt
        尾部（「用户改进要求：{feedback}」——这是重生成相对手动改的价值）；
      - adopt(material_id, draft)：版本链三键（iteration/iteration_root/
        iteration_of）由 adopt 自算——material_id 是链上任一成员（原始源或
        某版 derived）时新行都落在同一条 REFINE-ITER 链上（iteration=链上
        最大版本号+1，provenance.chain 指本次传入的直接源）；confidence
        （N-4）随 preview 自评透传。

    错误面同 preview/adopt 原样透传：素材不存在/已删 → RefineError；AI 未
    配置/调用失败 → AIError（此时零落库——旧版原样保留）。

    返回即 adopt 返回：{"ok", "id", "material"}。
    """
    draft = preview(material_id, feedback=feedback)
    return adopt(material_id, draft["text"], confidence=draft.get("confidence"))


# ── 迭代链读面（P2：版本列表 + 两版对比）────────────────────────────
# （list_iterations/_full_text/diff_iterations 已随 ARCH-2 迁入
#   services/materials/iterations——它操作素材库，落点归素材域；本模块
#   头部 re-import 保符号面，调用语义零变化。）
