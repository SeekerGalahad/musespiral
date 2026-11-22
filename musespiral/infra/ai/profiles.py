"""AI 档案状态（Phase B 自 musespiral/ai.py 拆分）——ai.json 的纯文件读写，零网络。

多提供商（§十九 F25）：ai.json v2 档案制——
  {"default": "cloud",
   "profiles": {"cloud": {base_url, api_key, model}, "local": {…}},
   "routing": {"check_facts": "local", …},     # 任务级覆盖，可选
   "media_default": "siliconflow",             # ← G1 media 域（独立命名空间）
   "media_profiles": {"siliconflow": {…}}}     #    与 chat 域同文件不互擦
旧 v1 单配置（顶层 base_url/api_key/model）读作名为 "default" 的档案，不回写。
本地端点（Ollama）api_key 可为空——configured 判定 = base_url+model 非空。

media 域（G1，2026-11-17 评审修正）**不进 chat TASKS**：media 生成走
/images/generations 不经 chat()，混入 TASKS 会被 ai check 用 chat 端点
误探测、被 routing 误路由——故独立 media_profiles/media_default 键，
生效档案=media_default 指向者（media 无任务路由）。

key 永不回显（list_profiles/list_media_profiles 掩码）、不进日志。

注：AIError 定义于本模块（而非 client.py）——profiles 的 set_default_profile/
set_route 抛出它，client 又须从本模块 import load_ai_config，放在 client 会
构成环；依赖方向恒为 client → profiles。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import config

_AI_FILE = "ai.json"

# 任务键白名单（routing 允许的路由目标；= 各消费方传入 chat(task=…) 的取值集）
TASKS: tuple[str, ...] = (
    "rewrite",
    "adapt",
    "digest",
    "title",  # 第十三期 F32：标题多候选优选（core/quality/title.py）
    "check_facts",
    "annotate_rewrite",
    "discover_suggest",  # 第十二期 F28：知识库选题 AI 增强（commands/discover_cmds.py）
    "compose",  # 第十八期 F48：创作台分块生成（services/compose_service.py）
    "refine",  # 第二十期 F57：素材 AI 摘要（services/refine_service.py，M4）
    "visual_suggest",  # Phase C F70-F74（§八-4）：视觉参数建议 JSON（只产预览态）
    # ── Phase D F75-F79（设计 §1.5）：智能体层三键，9 键 → 12 键 ─────────
    # 扩键即自动继承全部任务面机制（零新代码，§三 依赖规则 4）：
    #   ai.json routing 按步路由 / prompts.d/<task>.txt 覆盖级联
    #   （prompt_tasks() 派生自本表）/ musespiral ai check 逐任务探测 /
    #   F45 方言守门自动扫新模板（tests/test_architecture.py 扫描域含 core/agent）。
    "agent_extract",  # F76：单素材 → 结构化笔记步（plans_builtin.build_synthesis）
    "agent_synthesize",  # F76：多笔记 → 四节综合稿步（生成类，恒不缓存 §八-3）
    "tag_suggest",  # F77：素材 → TAG: 行标签建议（cache=true）
    # ── R3.5 书籍提炼管线（2026-11-18，设计=《musespiral-书籍内容提炼管线设计-
    # 2026-11-18.md》§2.1/§2.3）：13 键 → 15 键——chapter_digest=逐章提炼卡
    # 步（cache=true，重跑同章零成本）；book_digest=全书综述步（生成类恒
    # 不缓存）。扩键即自动继承任务面全套机制（同 Phase D 批注）：ai.json
    # routing 按步路由 / prompts.d/<task>.txt 覆盖级联 / ai check 逐任务探测 /
    # F45 方言守门（core/agent 扫描域自动纳管新模板）/ F79 per_task 预算
    # （`per_task.chapter_digest.daily_calls` 等键直接生效）。
    "chapter_digest",
    "book_digest",
    # ── E-3 编辑器 AI 微操作（2026-11-19，设计=《musespiral-专业创作者视角深度
    # 评审与改进-2026-11-18》§3.3）：15 键 → 20 键——选中文本五动作各一任务
    # 键（服务=services/editor_ai_service.py，走 chat_policed）。扩键即自动
    # 继承任务面全套机制（同 Phase D / R3.5 批注）：ai.json routing 按动作
    # 路由 / prompts.d/<task>.txt 覆盖级联 / ai check 逐任务探测 / F45 方言
    # 守门（本批把该服务文件登记进 DIALECT_SCAN_FILES）/ F79 per_task 预算。
    # 五键都是 **chat 键**（真走 chat() 端点）——与 media 域的
    # image_generate 相反：那种不走 /chat/completions 的键不得进本表。
    "editor_expand",  # 选中几句 → 扩写
    "editor_summarize",  # 选中一段 → 摘要
    "editor_rewrite",  # 换个说法
    "editor_continue",  # 续写两句
    "editor_qa",  # 问 AI 关于这段
    # ── G-T4 滚动摘要（2026-11-19，设计=《musespiral-调研吸收功能设计-2026-11-18》
    # §五）：20 键 → 21 键——正稿字数每跨 1500 字阈值，把全文压成 <200 字
    # 脉络摘要存 session.json，后续 compose/generate 的 prompt 自动携带。
    # 是 **chat 键**（服务=services/compose_service.refresh_rolling_summary，
    # 走 chat(task="rolling_summary")）。扩键即自动继承任务面全套机制（同
    # Phase D / E-3 批注）：ai.json routing 按任务路由 / prompts.d/
    # rolling_summary.txt 覆盖级联 / ai check 逐任务探测 / F45 方言守门
    # （该组装点在 services/compose_service.py，已在扫描面）。
    "rolling_summary",
    # ── G-T1 Ghost Text 续写（2026-11-19，设计=《musespiral-调研吸收功能设计-
    # 2026-11-18》§二）：21 键 → 22 键——编辑器内 AI 灰色预判续写（800ms 防抖
    # 触发，Tab 采纳 / Esc 忽略）。是 **chat 键**（服务=
    # services/editor_ai_service.run_ghost，走 chat_policed，F79 usage 自动
    # 计入）。扩键即自动继承任务面全套机制（同 Phase D / E-3 / G-T4 批注）：
    # ai.json routing 按任务路由 / prompts.d/ghost_write.txt 覆盖级联 /
    # ai check 逐任务探测 / F45 方言守门（模板在 editor_ai_service.py，该文件
    # 已在 DIALECT_SCAN_FILES）/ F79 per_task 预算
    # （`per_task.ghost_write.daily_calls` 等键直接生效）。
    "ghost_write",
    # ── E-5 小说模式前情提要（2026-11-19，正典=《musespiral-专业创作者视角深度评审
    # 与改进-2026-11-18》§3.6）：22 键 → 23 键——AI 摘要「前 N-1 章」成 <300
    # 字前情提要存 session.json novel_summary，后续 compose/generate 的 prompt
    # 自动携带（与 rolling_summary 同机制、不同插槽 {{novel}}/{{rolling}} 并存）。
    # 是 **chat 键**（服务=services/compose_service.refresh_novel_summary，走
    # chat(task="novel_summary")）。扩键即自动继承任务面全套机制（同 Phase D /
    # E-3 / G-T4 / G-T1 批注）：ai.json routing 按任务路由 / prompts.d/
    # novel_summary.txt 覆盖级联 / ai check 逐任务探测 / F45 方言守门（该组装
    # 点在 services/compose_service.py，已在扫描面）/ F79 per_task 预算。
    "novel_summary",
    # ── PL-6 首插件族 article-writer（2026-11-20，正典=《musespiral-插件系统架构
    # 设计-2026-11-19.md》§9.1/§9.3 + 后续工作流评审 §三.2）：24 键 → 25 键
    # ——视频转文字链的成稿步（服务面=plugins_builtin/article-writer/
    # plugin.py，走 chat_policed 返结构化段落 JSON）。**一插件一任务键**
    # （PL-4+5 纪律延续，不共用泛化键）：routing 可按插件独立路由到不同
    # 档案、prompts.d/plugin_article_writer.txt 可独立覆盖级联。是
    # **chat 键**（真走 chat() 端点经治理层）。扩键即自动继承任务面全套
    # 机制（同 Phase D / PL-5 批注）：ai.json routing 按任务路由 /
    # prompts.d/<task>.txt 覆盖级联 / ai check 逐任务探测 / F45 方言守门
    # （plugins_builtin 目录已在 DIALECT_SCAN_DIRS，新插件提示词自动纳管）/
    # F79 per_task 预算（`per_task.plugin_article_writer.daily_calls` 等键
    # 直接生效）。AI 未配置或调用失败时插件降级模板化拼装（双路径设计，
    # 降级只支持 chronological——两条路径都保留时间戳锚）。
    "plugin_article_writer",
    # ── PL-5 首插件族 key-segment（2026-11-20，正典=《musespiral-插件系统架构
    # 设计-2026-11-19.md》§9.1/§7.3 + 后续工作流评审 §四）：23 键 → 24 键
    # ——视频转文字链的关键内容切片步（服务面=plugins_builtin/key-segment/
    # plugin.py，走 chat_policed 一次调用返 JSON ranges）。**一插件一任务键**
    # （不是全体插件共用 "plugin" 键）：routing 可按插件独立路由到不同档案、
    # prompts.d/plugin_key_segment.txt 可独立覆盖级联——后续新 AI 插件照此
    # 各扩各键。是 **chat 键**（真走 chat() 端点经治理层）。扩键即自动继承
    # 任务面全套机制（同 Phase D / R3.5 批注）：ai.json routing 按任务路由 /
    # prompts.d/<task>.txt 覆盖级联 / ai check 逐任务探测 / F45 方言守门
    # （plugins_builtin 目录已登记进 DIALECT_SCAN_DIRS）/ F79 per_task 预算
    # （`per_task.plugin_key_segment.daily_calls` 等键直接生效）。AI 未配置
    # 或调用失败时插件降级规则切片（双路径设计——不给用户报错卡死）。
    "plugin_key_segment",
    # ── N-2 素材闭环·路径 C 论点提炼（2026-11-21，正典=《musespiral-素材闭环与
    # 专业编辑器设计-2026-11-18.md》§3.2/§五 P-1）：25 键 → 26 键——文章
    # 反哺的 AI 抽论点步（服务面=services/materials_service._distill_article，
    # 走 chat_policed）。扩键即自动继承任务面全套机制（同 Phase D / R3.5 /
    # PL-5 批注）：ai.json routing 按任务路由 / prompts.d/material_distill.
    # txt 覆盖级联 / ai check 逐任务探测 / F45 方言守门（services/
    # materials_service.py 本批登记进 DIALECT_SCAN_FILES——内置缺省模板
    # 在该文件）/ F79 per_task 预算（`per_task.material_distill.daily_calls`
    # 等键直接生效）。cache=False（生成类恒不缓存——同一篇文章两次提炼
    # 产出本就允许不同）。AI 未配置 → AIError 可读引导（ok:false 降级面）。
    "material_distill",
    # ── 插件 v2 DSL 验证批（2026-11-23，ARCH-4 控制流实战示例）：26 键 → 27 键
    # ——refine-segments 插件（plugins_builtin/refine-segments/plugin.py）的
    # 逐段精炼步：loop 节点 body 工人，口语转写一段一段经 chat_policed 精炼
    # （AI 未配置/失败 → 原段透传不炸流水线）。**一插件一任务键**（PL-5/PL-6
    # 纪律延续）：routing 可按插件独立路由、prompts.d/plugin_refine_segment.txt
    # 可独立覆盖级联。是 **chat 键**（真走 chat() 端点经治理层）。扩键即自动
    # 继承任务面全套机制（同 Phase D / PL-5 批注）：ai.json routing 按任务
    # 路由 / prompts.d/<task>.txt 覆盖级联 / ai check 逐任务探测 / F45 方言
    # 守门（plugins_builtin 目录已在 DIALECT_SCAN_DIRS，插件提示词自动纳管）/
    # F79 per_task 预算（`per_task.plugin_refine_segment.daily_calls` 等键直接
    # 生效）。cache=True（确定性校对变换，同段重跑零成本）。
    "plugin_refine_segment",
    # G1 评审修正（2026-11-17，组合批评审点 1）：media 生成（image_generate）
    # **不进 TASKS**——media 走 /images/generations 不经 chat()，进 TASKS 会导致
    # ai check 用 chat 端点探测图像任务（check.run_check 逐 TASKS 键发 chat 请求）
    # 与 routing 把图像任务路由到 chat 档案的概念污染。media 档案落独立命名空间
    # media_profiles/media_default（见本文件 media 节），方言守门不适用（无模板）。
)


class AIError(RuntimeError):
    pass


def _ai_path() -> Path:
    return config.home_dir() / _AI_FILE


# media 域在 ai.json 里的顶层键（chat 域写回时须原样保留，防双命名空间互擦）
_MEDIA_PROFILES_KEY = "media_profiles"
_MEDIA_DEFAULT_KEY = "media_default"


def _read_document() -> dict[str, Any]:
    """整文件读 + v1→v2 归一（**双命名空间写回的共享底座**）。

    与 _load_state 的分工：_load_state 只取 chat 域三键（读侧，行为零变更）；
    本函数供**写路径**用——返回含 media 域键的完整文档，chat 域写回时
    media_profiles/media_default 原样保留（反之亦然）。v1 扁平 chat 配置
    在此迁为 profiles.default（与 _load_state 读侧同一迁移语义，首次写回
    即固化 v2 形态），坏文件/缺文件 → 空 v2 骨架。
    """
    try:
        raw = json.loads(_ai_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("profiles"), dict):
        flat = {
            k: raw.get(k)
            for k in ("base_url", "api_key", "model")
            if raw.get(k) is not None
        }
        raw = {k: v for k, v in raw.items() if k not in ("base_url", "api_key", "model")}
        if flat.get("base_url") or flat.get("model"):
            raw["profiles"] = {"default": flat}
            raw["default"] = raw.get("default") if raw.get("default") in raw["profiles"] else "default"
    raw.setdefault("profiles", {})
    raw.setdefault("routing", {})
    raw.setdefault("default", None)
    return raw


def _write_document(doc: dict[str, Any]) -> None:
    config.home_dir().mkdir(parents=True, exist_ok=True)
    _ai_path().write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- 档案状态（v2 + v1 兼容） ------------------------------------------


def _load_state() -> dict[str, Any]:
    """读 ai.json 并归一为 {default, profiles, routing}；缺文件/坏文件/v1 全兼容。"""
    p = _ai_path()
    if not p.exists():
        return {"default": None, "profiles": {}, "routing": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"default": None, "profiles": {}, "routing": {}}
    if not isinstance(raw, dict):
        return {"default": None, "profiles": {}, "routing": {}}
    if "profiles" in raw and isinstance(raw.get("profiles"), dict):
        profiles = {k: v for k, v in raw["profiles"].items() if isinstance(v, dict)}
        default = raw.get("default")
        if default not in profiles and profiles:
            default = next(iter(profiles))
        routing = {
            t: n for t, n in (raw.get("routing") or {}).items()
            if t in TASKS and n in profiles
        }
        return {"default": default, "profiles": profiles, "routing": routing}
    # v1 扁平单配置 → 视作名为 "default" 的档案（不回写文件）
    if raw.get("base_url") or raw.get("model"):
        return {"default": "default", "profiles": {"default": raw}, "routing": {}}
    return {"default": None, "profiles": {}, "routing": {}}


def resolve_profile_name(task: str | None = None) -> str | None:
    """取生效档案名：routing[task] → default → None（ai check 展示「任务×档案」用）。

    与 load_ai_config 同一解析口径（routing 命中已删档案回退 default），
    供需要展示档案名而非档案内容的消费方使用。
    """
    state = _load_state()
    if not state["profiles"]:
        return None
    name = state["routing"].get(task) if task else None
    if name not in state["profiles"]:
        name = state["default"]
    return name


def load_profile(name: str) -> dict[str, Any]:
    """按名取档案原文；不存在返回 {}（不抛错——ai check 的未配置分型走通用口径）。"""
    state = _load_state()
    return dict(state["profiles"].get(name) or {})


def load_ai_config(task: str | None = None) -> dict[str, Any]:
    """取生效档案：routing[task]（需 task 在白名单）→ default → {}。

    v1 兼容：旧文件自动归一为 default 档案，旧调用 load_ai_config() 等价
    于取 default。路由指向已删除的档案时回退 default（misconfig 在
    list_profiles 里可见，不在此抛错阻断读取）。
    """
    name = resolve_profile_name(task)
    if name is None:
        return {}
    return load_profile(name)


def ai_configured(task: str | None = None, *, profile: str | None = None) -> bool:
    """configured 判定（F25 起 api_key 可空——本地 Ollama 场景）。

    profile 给出时校验该档案（N-3 源级 AI 配置的 profile 覆盖：调用方要先
    确认「源指定档案可用」，否则判定应走全局回落——由调用方自己 catch）。
    """
    cfg = load_profile(profile) if profile else load_ai_config(task)
    return bool(cfg.get("base_url") and cfg.get("model"))


def save_ai_profile(
    name: str, base_url: str, api_key: str, model: str, *, set_default: bool = True
) -> None:
    """写入/覆盖一个 chat 档案；首个 v2 写入会把 v1 旧配置迁为 "default" 档案保留。

    写回经 _read_document 整文档合并——media 域键（media_profiles/
    media_default）原样保留，chat 写不擦 media（反之 media 写不擦 chat）。
    """
    doc = _read_document()
    profiles = doc["profiles"]
    profiles[name] = {"base_url": base_url, "api_key": api_key, "model": model}
    doc["default"] = name if set_default else (doc.get("default") or name)
    _write_document(doc)


def set_default_profile(name: str) -> None:
    doc = _read_document()
    if name not in doc["profiles"]:
        known = "/".join(sorted(doc["profiles"])) or "（空）"
        raise AIError(f"档案不存在: {name}（可用: {known}）")
    doc["default"] = name
    _write_document(doc)


def set_route(task: str, profile: str) -> None:
    if task not in TASKS:
        raise AIError(f"未知任务: {task}（可用: {'/'.join(TASKS)}）")
    doc = _read_document()
    if profile not in doc["profiles"]:
        known = "/".join(sorted(doc["profiles"])) or "（空）"
        raise AIError(f"档案不存在: {profile}（可用: {known}）")
    # 写侧净化与 _load_state 读侧同口径：失效任务键（如已移出的 media 键）/
    # 指向已删档案的陈旧路由随写回落（读侧本就不生效，落盘同步清）
    doc["routing"] = {
        t: n for t, n in doc["routing"].items()
        if t in TASKS and n in doc["profiles"]
    } | {task: profile}
    _write_document(doc)


def _mask_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """档案掩码形态（chat/media 两域共用）：key 永不回显，只给掩码+布尔。"""
    key = str(profile.get("api_key", ""))
    masked = key[:6] + "..." + key[-4:] if len(key) > 10 else ("(空)" if not key else "(已设置)")
    return {
        "base_url": profile.get("base_url", ""),
        "model": profile.get("model", ""),
        "api_key": masked,
        "has_key": bool(key),  # F47 设置面 key 态布尔（key 明文永不回显）
        "configured": bool(profile.get("base_url") and profile.get("model")),
    }


def list_profiles() -> dict[str, Any]:
    """档案清单（key 掩码）——CLI/health 展示与 misconfig 可见化的数据面。

    只列 chat 域（media 域独立清单=list_media_profiles——两域档案语义
    不同不混排，防「把 chat 档案当生成档案」的误读）。
    """
    state = _load_state()
    return {
        "default": state["default"],
        "profiles": {k: _mask_profile(v) for k, v in state["profiles"].items()},
        "routing": dict(state["routing"]),
        "tasks": list(TASKS),
    }


# ── media 域档案（G1 2026-11-17 评审修正：独立命名空间，不进 chat TASKS）──
#
# ai.json 同文件双命名空间：
#   {"default": …, "profiles": {…}, "routing": {…},          ← chat 域（本节以上）
#    "media_default": "siliconflow",
#    "media_profiles": {"siliconflow": {base_url, api_key, model}}}
#
# 为什么独立而非 profiles 加 kind：TASKS 是 chat() 的任务白名单（ai check
# 逐键 chat 探测、routing 按键路由）——media 生成走 /images/generations
# 不经 chat()，混入 chat 命名空间必然误探测/误路由（组合批评审点 1）。
# media 域无任务路由（单一生成语义，G2 视频再议 media_routing）——生效
# 档案 = media_default 指向的档案。档案三元组与 chat 域同构，「供应商
# 可转换」裁决直接适用。


def _load_media_state() -> dict[str, Any]:
    """读 media 域状态 → {default, profiles}（缺文件/坏文件宽容空态）。"""
    doc = _read_document()
    raw_profiles = doc.get(_MEDIA_PROFILES_KEY)
    profiles = (
        {k: v for k, v in raw_profiles.items() if isinstance(v, dict)}
        if isinstance(raw_profiles, dict) else {}
    )
    default = doc.get(_MEDIA_DEFAULT_KEY)
    if default not in profiles:
        default = next(iter(profiles), None)
    return {"default": default, "profiles": profiles}


def resolve_media_profile_name() -> str | None:
    """生效 media 档案名（media_default；无档案 → None）。"""
    state = _load_media_state()
    return state["default"]


def load_media_profile(name: str) -> dict[str, Any]:
    """按名取 media 档案原文；不存在返回 {}。"""
    return dict(_load_media_state()["profiles"].get(name) or {})


def load_media_config() -> dict[str, Any]:
    """生效 media 档案（media_default 指向；未配置 → {}）。"""
    name = resolve_media_profile_name()
    if name is None:
        return {}
    return load_media_profile(name)


def media_configured() -> bool:
    """media 域 configured 判定（与 chat 域同口径：base_url+model 非空）。"""
    cfg = load_media_config()
    return bool(cfg.get("base_url") and cfg.get("model"))


def save_media_profile(
    name: str, base_url: str, api_key: str, model: str, *, set_default: bool = True
) -> None:
    """写入/覆盖一个 media 档案（整文档合并——chat 域键原样保留）。

    `musespiral media config` 的落地件：set_default=True 使刚配置的档案立即
    生效（media 域独立 default，不动 chat 域 default——无劫持面）。
    """
    doc = _read_document()
    raw_profiles = doc.get(_MEDIA_PROFILES_KEY)
    profiles = (
        {k: v for k, v in raw_profiles.items() if isinstance(v, dict)}
        if isinstance(raw_profiles, dict) else {}
    )
    profiles[name] = {"base_url": base_url, "api_key": api_key, "model": model}
    doc[_MEDIA_PROFILES_KEY] = profiles
    if set_default or doc.get(_MEDIA_DEFAULT_KEY) not in profiles:
        doc[_MEDIA_DEFAULT_KEY] = name
    _write_document(doc)


def set_media_default(name: str) -> None:
    """切换生效 media 档案；不存在抛 AIError（可读列出可用档案）。"""
    doc = _read_document()
    raw_profiles = doc.get(_MEDIA_PROFILES_KEY)
    profiles = (
        {k: v for k, v in raw_profiles.items() if isinstance(v, dict)}
        if isinstance(raw_profiles, dict) else {}
    )
    if name not in profiles:
        known = "/".join(sorted(profiles)) or "（空）"
        raise AIError(f"media 档案不存在: {name}（可用: {known}）")
    doc[_MEDIA_DEFAULT_KEY] = name
    _write_document(doc)


def list_media_profiles() -> dict[str, Any]:
    """media 档案清单（key 掩码，_mask_profile 同款零回显）。"""
    state = _load_media_state()
    return {
        "default": state["default"],
        "profiles": {k: _mask_profile(v) for k, v in state["profiles"].items()},
    }


# 兼容旧入口（ai_cmds 旧签名等）：三参保存=写 "default" 档案
def save_ai_config(base_url: str, api_key: str, model: str) -> None:
    save_ai_profile("default", base_url, api_key, model, set_default=True)
