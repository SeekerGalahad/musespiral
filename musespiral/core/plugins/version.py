"""插件版本协商（ARCH-4 §四，2026-11-22）——L2 纯函数，零 I/O。

流水线保存时按节点记录当时插件 manifest 的内容 hash（manifest_content_hash
的 sha256[:12]），加载时与当前已安装插件的 hash 比对：不一致给**可读警告**
（「此插件已升级，建议验证」），不阻塞加载。设计取舍（正典 §ARCH-4）：流水线
的 outputs 契约可能因插件升级而改变，用户应被提醒「这条流水线当时是在旧版
插件上存的」——但升级未必破坏兼容，故警告而非拒绝（向后兼容优先）。

hash 计算的实现在 infra/plugins_loader.manifest_content_hash（需 json/sha256
但属纯逻辑；架构门规则 1 infra 不 import core，故 core 不反向 import infra
——本模块自带同样的规范化 hash 函数，两处算法必须逐字一致，测试对拍锚定）。

saved hash 缺席（老流水线/手工编辑）→ 视为未知，不报警（零迁移：升级前存的
流水线没有该键，不能被误判为「已变更」）。

存储形状：流水线 json 顶层 ``plugin_version_hash`` = {node_id: hash}（与
nodes 分离，保持 nodes 的引擎形状零回归，见 studio pipeline_crud）。加载时把
该 dict 与 nodes（node_id→plugin id）、当前清单（plugin id→manifest）三者联表
比对。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_KEY = "plugin_version_hash"
HASH_LEN = 12


def manifest_hash(manifest: dict[str, Any]) -> str:
    """manifest dict → 内容 hash（sha256[:12]），键序无关、字节稳定。

    与 infra.plugins_loader.manifest_content_hash 同算法（sort_keys + 紧凑
    JSON + ensure_ascii=False），测试对拍两处确保不漂移。
    """
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_LEN]


def _plugin_id(raw: Any) -> str:
    """节点 plugin 字段 → 插件 id（引擎形状 str 或 UI 形状 manifest dict 通吃）。"""
    if isinstance(raw, dict):
        return str(raw.get("id") or raw.get("plugin") or "")
    return str(raw or "")


def version_warnings(
    nodes: list[dict[str, Any]],
    current_by_id: dict[str, dict[str, Any]],
    saved_hashes: dict[str, str] | None,
) -> list[str]:
    """比对「保存时各节点 hash」与「当前已装插件 hash」→ 可读警告列表。

    nodes 每项 {"id": 节点 id, "plugin": 插件 id（str 或 manifest dict）}；
    current_by_id = {插件 id: 当前 manifest dict}；saved_hashes =
    流水线 json 的顶层 plugin_version_hash = {node_id: hash}。

    判定（对 saved_hashes 逐节点）：
      - saved_hashes 为 None/空（老流水线）→ 零警告，零迁移；
      - 节点在 nodes 中找不到（手工编辑）→ 跳过；
      - 当前插件找不到（已卸载）→ 警告「插件已卸载」；
      - 当前 hash ≠ 保存 hash → 警告「插件已升级，建议验证该节点」。
    同一插件多节点各报一条（带节点 id，用户能定位）。当前 manifest 无法算
    hash（非 dict）按「已卸载」同档处理。
    """
    if not saved_hashes or not isinstance(saved_hashes, dict):
        return []  # 老流水线无该键 → 零迁移
    node_ids = {str(n.get("id") or _plugin_id(n.get("plugin"))) for n in nodes if isinstance(n, dict)}
    warnings: list[str] = []
    for nid, saved_hash in saved_hashes.items():
        if nid not in node_ids or not isinstance(saved_hash, str) or not saved_hash:
            continue
        node = next(
            (n for n in nodes
             if isinstance(n, dict) and str(n.get("id") or _plugin_id(n.get("plugin"))) == nid),
            None,
        )
        pid = _plugin_id(node.get("plugin")) if node else nid
        current = current_by_id.get(pid)
        if not isinstance(current, dict) or not current:
            warnings.append(f"节点 {nid!r} 的插件 {pid!r} 当前不可用（已卸载/未安装）——建议核对流水线")
            continue
        now = manifest_hash(current)
        if now != saved_hash:
            warnings.append(
                f"节点 {nid!r} 的插件 {pid!r} 已升级（保存时 {saved_hash}，当前 {now}）"
                "——建议验证该节点仍兼容"
            )
    return warnings
