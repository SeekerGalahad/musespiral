"""Material 素材模型（第二十期 §1.2）——L2 纯函数，零 I/O。

行 schema（正典 §1.2，追加式 JSONL 每行一份）：

  {"id": "sha1前12", "type": "text|image|video|link|radar_signal|crawled|derived",
   "origin": {"source_id","uri","title"},
   "license": "CC-BY|ALL-RIGHTS|UNKNOWN",   # 结构化枚举；声明原文存 payload.meta.license_note
   "fetched_at": "ISO",
   "provenance": {"chain":[{"from_id","op"}], "derived_from":[]},  # 原始恒空
   "payload": {"text?","url?","local_path?","meta?"},
   "triage": {"read":false,"starred":false,"enabled":true,"tags":[],
              "verified":false,"confidence":null}}   # N-4 扩后两键：人工核验布尔 + AI 自评 0-1（可空）

不变式（正典红线）：
  - immutable 原始：payload 落库后不改写——人工动作只改 triage 键
    （三布尔 + tags 列表 + verified 人工核验布尔，N-4 扩；
    verified 走专用端点 /api/materials/verify），
    derived 血统只由采纳动作写 provenance.chain；
  - 同 id 末次胜出：读序按文件序覆盖（merge_rows），与 publish_log 同
    纪律；删除以 tombstone 行（{"id", "deleted": true}）表达，不重写文件；
  - id 由 (source_id, type, uri) 稳定散列生成——同键重采天然同 id，
    追加式落盘幂等（不产生新行）。

时间戳（fetched_at）由调用方注入：本层不取当前时间，保证同输入恒同
输出、可完全单测。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

# 素材类型枚举（正典 §1.2；radar_signal=radar-hub 订阅产物，derived=AI 加工采纳）
MATERIAL_TYPES: tuple[str, ...] = (
    "text", "image", "video", "link", "radar_signal", "crawled", "derived",
)
# license 结构化枚举（裁决 5）：声明原文不入枚举，存 payload.meta.license_note
# G1（2026-11-17 裁决 1）增第四值 AI-GENERATED：AI 生成物与抓取物的许可语义
# 不同，混用 UNKNOWN 丢信息——落库时 meta.ai_generated=true 双保险
LICENSES: tuple[str, ...] = ("CC-BY", "ALL-RIGHTS", "UNKNOWN", "AI-GENERATED")
# 原始素材类型集（provenance 必须为空；derived 的 provenance 只由采纳动作写入）
ORIGINAL_TYPES: tuple[str, ...] = tuple(t for t in MATERIAL_TYPES if t != "derived")
# provenance 动作枚举（F57 M0 先立 summarize；后续动作经此表扩）
# synthesize：Phase D F76 跨素材综合（chain 每项仍 {from_id, op}，多源=多项
#   ——设计 §1.3 扩键；血统只由采纳动作写入，immutable 红线不破）
# generate：G1 文生图采纳（2026-11-17 调研 §4.1——血统链扩展位，D 档同族；
#   AI 生成物无上游素材，from_id 为虚拟标记，见 validate_material 豁免条）
# book_fetch：R0 书源功能（2026-11-18 架构设计 §3.6）——取文入素材库的
#   血统动作（from_id=bookSourceUrl）；R0 只定常量，写入归 R3
# plugin_pipeline：PL-6 material-sink（2026-11-20，正典=《musespiral-插件系统架构
#   设计-2026-11-19.md》§9.1）——插件流水线产物入素材库的血统动作
#   （from_id=<pipeline run_id> 虚拟标记非素材 id；run 目录归 pipeline_runs
#   的 LRU 保留策略，不进素材库——引用状态徽章恒「已删/不在库」与 generate
#   的虚拟标记 "ai" 同语义，见 ref_statuses 既有口径）
# published_from：N-2 文章反哺（2026-11-21，正典=《musespiral-素材闭环与专业编辑器
#   设计-2026-11-18.md》§3.2/§五）——已产出文章 → 素材的血统动作
#   （from_id=文章 slug 真实可寻址 id：articles_root/<slug>.md；与
#   generate/book_fetch 的虚拟标记不同，slug 在文章根可解析回原文件）
# distill：N-2 路径 C 论点提炼（同上正典）——AI 抽文章核心论点 → 多条
#   spark（from_id=源文章 slug；每条 spark 一条单链，与 single_op 豁免同族）
# adopt：B1 本地书籍选章（2026-11-26，正典=《musespiral-三线扩展设计-本地TXT
#   书籍-视觉编辑2-多媒体-2026-11-26》§1.4）——本地 TXT 章节 → 素材的血统动作
#   （from_id=`localbook:<bookId>:<n>` 虚拟标记：指向用户磁盘文件里的字符偏移，
#   不是素材 id；正文不进素材库=引用型红线的落点）
PROVENANCE_OPS: tuple[str, ...] = (
    "summarize", "synthesize", "generate", "book_fetch", "plugin_pipeline",
    "published_from", "distill", "adopt",
)

# payload.meta.book schema 键（R0 定五键；R3.5 提炼批 2026-11-18 扩两键：
# original_chars=清洗后原文字符数（提炼卡可核验性的对照面）、distilled=是否
# 已提炼（false=AI 失败/未开提炼的降级原始文本，前端「未提炼」徽数据源））
META_BOOK_KEYS: tuple[str, ...] = (
    "name", "author", "source_name", "chapter_index", "chapter_title",
    "original_chars", "distilled",
)

# payload.meta.sub_type 枚举（E-5 小说模式 2026-11-19，正典=《musespiral-专业创作者
# 视角深度评审与改进-2026-11-18》§3.6 「人物卡 = type=character 新素材类型」）：
# **不新增 MATERIAL_TYPES 取值**——人物卡复用 type=text 载体、以 meta.sub_type
# 判别（正典原文给了两条路：sub_type=character 或 meta.character=true，本实现
# 取前者）。为什么不动 MATERIAL_TYPES：类型枚举是素材库全部读侧（评分/分诊/
# 集合/文件服务/CLI tags）的既有分支面，扩一个只服务小说模式的取值会把「无
# 正文档 / 无文件面 / 无评分」这些空分支散到各处；sub_type 是 payload.meta 内的
# 语义标注，读侧未命中者天然按普通 text 对待（零回归）。
# video_article：PL-6 material-sink（2026-11-20，正典=插件系统架构设计 §9.1
# + 后续工作流评审 §3.3「引用要标出处 ✅ sub_type=video_article + anchor_map」）
# ——视频转图文稿素材（type=text 载体 + meta 带 anchor_map/organize_mode/
# source_video，引用时可回跳视频原处），同一条「sub_type 判别不扩类型枚举」纪律。
# spark：N-2 灵感速记（2026-11-21，正典=《musespiral-素材闭环与专业编辑器设计-
# 2026-11-18.md》§五 P-1 +《总体方案》N-2——用户裁决沿用 E-5 人物卡纪律：
# **不新增 MATERIAL_TYPES 取值**，spark = type=text 载体 + meta.sub_type='spark'
# 与 'character' 并列；读侧未命中者天然按普通 text 对待（零回归））
META_SUB_TYPES: tuple[str, ...] = ("character", "video_article", "spark")

# payload.meta 迭代链三键（P2 素材再加工循环 2026-11-20，正典=《musespiral-格式
# 生态与素材流转能力分析-2026-11-20》§3.3）：derived 素材的打磨版本元信息——
# iteration=第几版（整数，v1=1）、iteration_root=迭代链根素材 id（同一根的
# 所有版本共享）、iteration_of=上一版素材 id（v1 为 null）。沿 META_SUB_TYPES
# 同一条「meta 内语义标注不扩类型/血统枚举」纪律：provenance.chain 仍只指
# 直接源（血统准确性不破），读侧未携带三键的存量素材按「无迭代史」对待
# （零回归）；写入只经 refine_service.adopt（as_iteration=True，血统/迭代
# 元信息同由采纳动作落库的既有红线）。
META_ITERATION_KEYS: tuple[str, ...] = ("iteration", "iteration_root", "iteration_of")


def make_material_id(source_id: str, mtype: str, uri: str) -> str:
    """稳定素材 id：sha1(source_id|type|uri) 前 12 位十六进制。

    同一源内同类型同 uri 的素材恒同 id——追加式 JSONL 语义下，重采/重迁移
    天然合并到既有行（末次胜出），不产生重复行（迁移幂等的数据面基础）。
    """
    key = f"{source_id}|{mtype}|{uri}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def make_tombstone(material_id: str) -> dict[str, Any]:
    """删除墓碑行：追加式文件不重写，删除=追加 {"id", "deleted": true}。

    连坐解析依赖墓碑 id 保留在文件里——「素材已删」徽章要知道哪些 id
    曾存在（get/引用解析见 services.materials_service）。
    """
    return {"id": material_id, "deleted": True}


def is_tombstone(row: Any) -> bool:
    """墓碑行判定（宽容：非 dict 恒 False）。"""
    return isinstance(row, dict) and row.get("deleted") is True


def _clean_str(value: Any) -> str:
    """宽容字符串化：None/非 str → ""，str 去 strip。"""
    return value.strip() if isinstance(value, str) else ""


def make_material(
    *,
    mtype: str,
    source_id: str,
    uri: str,
    title: str = "",
    license: str = "UNKNOWN",
    payload: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    triage: dict[str, Any] | None = None,
    material_id: str | None = None,
    fetched_at: str = "",
) -> dict[str, Any]:
    """构造一条完整素材行（缺省补默认；不校验——校验独立走 validate_material）。

    material_id 缺省由 (source_id, mtype, uri) 稳定散列生成；显式传入用于
    测试与特殊归档场景。fetched_at 由调用方注入（本层不取时钟）。

    triage 缺省六键（N-4 扩 verified/confidence，2026-11-21）：三布尔 +
    tags + verified（人工核验，恒 False 起步）+ confidence（AI 自评，
    None 起步——调用方携带 AI 置信度时显式传 triage 覆盖）。
    """
    return {
        "id": material_id or make_material_id(source_id, mtype, uri),
        "type": mtype,
        "origin": {"source_id": source_id, "uri": uri, "title": title},
        "license": license or "UNKNOWN",
        "fetched_at": fetched_at,
        "provenance": provenance if provenance is not None else {"chain": [], "derived_from": []},
        "payload": payload if payload is not None else {},
        "triage": triage if triage is not None else {
            "read": False, "starred": False, "enabled": True, "tags": [],
            "verified": False, "confidence": None,
        },
    }


def normalize_material(row: Any) -> dict[str, Any] | None:
    """读侧宽容归一：坏行（非 dict / id 非非空 str）→ None 丢弃。

    合法骨架行补默认（license/provenance/payload/triage 缺键不崩读侧）；
    墓碑行原样放行（不补三键——它不是完整素材）。与 discovered/publish_log
    的宽容读同纪律：人工可编辑文件，读侧永不因脏数据崩溃。
    """
    if not isinstance(row, dict):
        return None
    mid = row.get("id")
    if not isinstance(mid, str) or not mid.strip():
        return None
    if is_tombstone(row):
        return {"id": mid.strip(), "deleted": True}
    out = dict(row)
    out["id"] = mid.strip()
    out.setdefault("type", "text")
    out.setdefault("license", "UNKNOWN")
    out.setdefault("fetched_at", "")
    origin = out.get("origin")
    out["origin"] = dict(origin) if isinstance(origin, dict) else {}
    provenance = out.get("provenance")
    out["provenance"] = dict(provenance) if isinstance(provenance, dict) else {}
    out["provenance"].setdefault("chain", [])
    out["provenance"].setdefault("derived_from", [])
    payload = out.get("payload")
    out["payload"] = dict(payload) if isinstance(payload, dict) else {}
    triage = out.get("triage")
    triage = dict(triage) if isinstance(triage, dict) else {}
    for key, default in (("read", False), ("starred", False), ("enabled", True), ("verified", False)):
        value = triage.get(key, default)
        triage[key] = value if isinstance(value, bool) else default
    # N-4 confidence（AI 自评 0-1，可空）：宽容归一——缺失/脏数据（非数值、
    # 布尔、越界）一律折 None（读侧永不因脏数据崩溃；与「人工核验 verified」
    # 正交，confidence 是机器字段不走 triage_patch 窄门）
    conf = triage.get("confidence", None)
    if (
        not isinstance(conf, (int, float))
        or isinstance(conf, bool)
        or not (0.0 <= float(conf) <= 1.0)
    ):
        conf = None
    triage["confidence"] = float(conf) if conf is not None else None
    tags = triage.get("tags", [])
    if not isinstance(tags, list):
        tags = []  # 宽容读：脏数据（非列表）按无标签，不崩读侧
    else:
        tags = [t for t in tags if isinstance(t, str)]
    triage["tags"] = tags
    out["triage"] = triage
    return out


def validate_material(row: dict[str, Any]) -> list[str]:
    """完整素材行校验（返回错误清单，空列表=合法）。

    追加落库前（add_material）调用；墓碑行不走本校验（结构由 make_tombstone
    钉死）。校验项：
      - id 非空 str；
      - type ∈ MATERIAL_TYPES；
      - license ∈ LICENSES；
      - origin.source_id / origin.uri / origin.title 为 str（允许空串——
        爬取物 title 可缺；source_id/uri 的非空约束归服务层注册面）；
      - fetched_at 非空 str（ISO——格式由调用方生成口径保证，此处不猜解析）；
      - provenance.chain 每项 {from_id, op} 两键非空 str 且 op ∈ PROVENANCE_OPS；
        derived_from 为 str 列表；derived 类 provenance 可非空，原始类必须全空
        （immutable 红线的结构性防线：原始素材不得自带血统）；
      - payload 为 dict。
    """
    errors: list[str] = []
    mid = row.get("id")
    if not isinstance(mid, str) or not mid.strip():
        errors.append("id 必须是非空字符串")
    mtype = row.get("type")
    if mtype not in MATERIAL_TYPES:
        errors.append(f"type 必须是 {MATERIAL_TYPES} 之一: {mtype!r}")
    if row.get("license") not in LICENSES:
        errors.append(f"license 必须是 {LICENSES} 之一: {row.get('license')!r}")
    origin = row.get("origin")
    if not isinstance(origin, dict):
        errors.append("origin 必须是对象")
    else:
        for key in ("source_id", "uri", "title"):
            if not isinstance(origin.get(key), str):
                errors.append(f"origin.{key} 必须是字符串")
    fetched = row.get("fetched_at")
    if not isinstance(fetched, str) or not fetched.strip():
        errors.append("fetched_at 必须是非空 ISO 字符串")
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance 必须是对象")
    else:
        chain = provenance.get("chain", [])
        if not isinstance(chain, list):
            errors.append("provenance.chain 必须是列表")
        else:
            for i, link in enumerate(chain):
                if not isinstance(link, dict):
                    errors.append(f"provenance.chain[{i}] 必须是对象")
                    continue
                if not isinstance(link.get("from_id"), str) or not link.get("from_id"):
                    errors.append(f"provenance.chain[{i}].from_id 必须是非空字符串")
                if link.get("op") not in PROVENANCE_OPS:
                    errors.append(f"provenance.chain[{i}].op 必须是 {PROVENANCE_OPS} 之一: {link.get('op')!r}")
        derived_from = provenance.get("derived_from", [])
        if not isinstance(derived_from, list) or any(not isinstance(x, str) for x in derived_from):
            errors.append("provenance.derived_from 必须是字符串列表")
    if mtype in ORIGINAL_TYPES and (chain or derived_from):
        # 豁免条一（G1 2026-11-17）：AI 生成物采纳——image/video 等原始
        # 类型携带**单条 generate 链**（无上游素材，from_id 为虚拟标记
        # 非素材 id）。血统仍只由采纳动作写入（adopt_generated）。
        # 豁免条二（R3 书源取文 2026-11-18）：书源章节素材——text 类型
        # 携带**单条 book_fetch 链**（from_id=bookSourceUrl 虚拟标记，
        # 架构设计 §3.6；血统只由 book_fetch_service 写入）。immutable
        # 红线不变：其余任何 chain/derived_from 组合照旧拒绝。
        # 豁免条三（E-5 人物卡 2026-11-19）：人物卡是**人工创作资产**，不是
        # 采集物——允许空链（人工手写）与单条 generate 链（AI 起草后人工定稿，
        # 与豁免条一同族）。判别只看 sub_type，其余 text 素材照旧。
        # 豁免条四（PL-6 material-sink 2026-11-20，正典=插件系统架构设计 §9.1
        # + 评审 §3.3「素材要能追溯来源」）：插件流水线产物——**单条
        # plugin_pipeline 链**（from_id=<run_id> 虚拟标记非素材 id，run 目录
        # 归 pipeline_runs 保留策略不入素材库），immutable 红线不变：落库
        # 由流水线 sink 写入即定稿，其余任何 chain/derived_from 组合照旧拒绝。
        # 豁免条五（N-2 文章反哺 2026-11-21，正典=《musespiral-素材闭环与专业编辑器
        # 设计-2026-11-18.md》§3.2）：反哺素材——**单条 published_from /
        # distill 链**（from_id=文章 slug，articles_root/<slug>.md 真实可寻址
        # ——设计 §3.1「origin.uri 与创作工坊 slug 可互链」）。本条不新增
        # 豁免分支：两个 op 直接加入下方既有 single_op 白名单（同
        # generate/book_fetch/plugin_pipeline 同族单链形态，血统只由
        # from-article 动作写入），immutable 红线不变：其余任何 chain/
        # derived_from 组合照旧拒绝。
        # 豁免条六（B1 本地书籍 2026-11-26，正典=《musespiral-三线扩展设计-本地
        # TXT书籍-视觉编辑2-多媒体-2026-11-26》§1.4）：本地书选章素材——**单条
        # adopt 链**（from_id=`localbook:<id>:<n>` 虚拟标记，指向用户磁盘文件
        # 的章偏移而非素材 id；血统只由 localbook_service 写入）。同族单链形态，
        # 直接加入 single_op 白名单，immutable 红线不变：其余组合照旧拒绝。
        sub_type = ""
        payload = row.get("payload")
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                sub_type = str(meta.get("sub_type") or "")
        single_op = (
            not derived_from
            and len(chain) == 1
            and isinstance(chain[0], dict)
            and chain[0].get("op")
            in (
                "generate",
                "book_fetch",
                "plugin_pipeline",
                "published_from",
                "distill",
                "adopt",
            )
        )
        # 人物卡人工手写（无血统）合法——写入不是 derive，不写 chain
        handwritten_character = sub_type == "character" and not derived_from
        if not single_op and not handwritten_character:
            errors.append(f"原始素材（type={mtype}）provenance 必须为空（血统只由采纳动作写入）")
    if not isinstance(row.get("payload"), dict):
        errors.append("payload 必须是对象")
    # triage 校验（N-4 同步：verified 布尔 / confidence 可空数值 0-1）。
    # triage 键集本身不在此校验（人工可变面归 triage_patch 窄门）；这里只
    # 守两新键的值域——make_material 缺省即合法，显式携带时形状不对即拒
    # （append 前最后一道；bool 不是 int 的豁免同 confidence 归一口径）。
    tri = row.get("triage")
    if isinstance(tri, dict):
        if "verified" in tri and not isinstance(tri["verified"], bool):
            errors.append(f"triage.verified 必须是布尔值: {tri['verified']!r}")
        conf = tri.get("confidence", None)
        if conf is not None and (
            not isinstance(conf, (int, float)) or isinstance(conf, bool)
            or not (0.0 <= float(conf) <= 1.0)
        ):
            errors.append(f"triage.confidence 必须是 0-1 数值或 null: {conf!r}")
    return errors


def merge_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """合并读序：同 id 末次胜出（文件序覆盖），返回 {id: 行}。

    与 publish_log 读序同纪律（正典 §1.2「jsonl 追加式更新=同 id 新行
    末次胜出」）；墓碑行同样参与覆盖（末次是墓碑=该 id 已删）。
    """
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        index[row["id"]] = row
    return index


def split_active(index: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """索引切分：活跃素材列表 + 已删（墓碑）id 集。

    已删 id 集是连坐徽章的数据面——引用解析要知道「哪些 id 曾存在但
    已删」，而非静默当不存在（正典 §1.2 delete 连坐红线）。
    """
    active = [row for row in index.values() if not is_tombstone(row)]
    deleted = {mid for mid, row in index.items() if is_tombstone(row)}
    return active, deleted


def _row_refs(row: dict[str, Any]) -> list[str]:
    """一条素材引用的全部素材 id（chain.from_id + derived_from，去重保序）。"""
    provenance = row.get("provenance") or {}
    out: list[str] = []
    for link in provenance.get("chain") or []:
        if isinstance(link, dict) and isinstance(link.get("from_id"), str):
            if link["from_id"] not in out:
                out.append(link["from_id"])
    for x in provenance.get("derived_from") or []:
        if isinstance(x, str) and x not in out:
            out.append(x)
    return out


def resolve_cascade(material_id: str, materials: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """连坐解析：materials 中引用 material_id 的素材（排除其自身）。

    删除素材时由 delete_material 调用，返回受影响引用者清单（两步
    confirm 的连坐声明数据面）；传入集通常为当前活跃集。
    """
    return [row for row in materials if row.get("id") != material_id and material_id in _row_refs(row)]


def ref_statuses(material: dict[str, Any], known_ids: set[str]) -> list[dict[str, Any]]:
    """素材血统引用的就地状态（「素材已删」徽章数据面）。

    对 provenance 每个引用附 deleted 布尔：from_id 不在 known_ids（活跃
    集）即已删/不存在——详情视图据此显示徽章（非静默失效，正典 §1.2）。
    """
    provenance = material.get("provenance") or {}
    statuses: list[dict[str, Any]] = []
    for link in provenance.get("chain") or []:
        if not isinstance(link, dict):
            continue
        from_id = link.get("from_id")
        statuses.append({
            "from_id": from_id,
            "op": link.get("op"),
            "deleted": not (isinstance(from_id, str) and from_id in known_ids),
        })
    for from_id in provenance.get("derived_from") or []:
        statuses.append({
            "from_id": from_id,
            "op": None,
            "deleted": not (isinstance(from_id, str) and from_id in known_ids),
        })
    return statuses
