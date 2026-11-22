"""materials 包：spark（灵感速记，N-2 ①）。

原 materials_service.py 按 ARCH-2 拆出的灵感簇：一句话极简创建 add_spark
（type=text 载体 + meta.sub_type=spark——类型枚举不动的用户裁决，E-5 人物
卡同款纪律；幂等锚 spark://sha1:<全文散列>，先查后建零追加）与 is_spark
判别面。
"""
from __future__ import annotations

import hashlib
from typing import Any

from ...core.materials import model
from ...jobs.store import now_iso as _now_iso
from .crud import add_material, get_material

# 灵感速记虚拟源（设计 §五「origin=manual」）——与 register_video 的
# VIDEO_SOURCE_ID 同值同域：人工录入族共用一个不入 sources.json 的虚拟
# 源（正典 §1.4 迁移表 upload/manual 同款口径，不新增注册表条目）。
SPARK_SOURCE_ID = "manual"


def is_spark(material: dict[str, Any]) -> bool:
    """该素材是否灵感速记（meta.sub_type=spark；is_character 同款判别面）。"""
    meta = (material.get("payload") or {}).get("meta") or {}
    return isinstance(meta, dict) and meta.get("sub_type") == "spark"


def add_spark(
    text: str,
    *,
    title: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """灵感速记（N-2 ①）：一句话极简创建 → ``{material, dedup}``。

    只需 text，其余自动填：type=text 载体 + meta.sub_type=spark（裁决：
    不新增 MATERIAL_TYPES——读侧未命中者按普通 text 零回归）、origin.
    source_id=manual（不入册虚拟源）、triage 缺省 read=False。title 缺省
    取全文首行压平前 24 字（仅作清单展示——唯一真值是 payload.text）。

    幂等锚 uri = ``spark://sha1:<全文散列>``：同一句话重发同 id——先查
    既有活跃行直接返回 dedup=true（register_video 同款先查后建，重复
    提交/手滑双击天然收敛零追加）。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("缺少必填字段 text（灵感内容不能为空）")
    text = text.strip()
    uri = f"spark://sha1:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"
    mid = model.make_material_id(SPARK_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}
    material = model.make_material(
        mtype="text",
        source_id=SPARK_SOURCE_ID,
        uri=uri,
        title=title.strip() or text.replace("\n", " ")[:24],
        payload={"text": text, "meta": {"sub_type": "spark"}},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}
