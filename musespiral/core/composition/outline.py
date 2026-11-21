"""大纲模式数据面（G-T2，正典=《musespiral-调研吸收功能设计-2026-11-18》§三）。

长文先出大纲 → 逐节裁决 → 逐节展开，防一次成稿在第二节跑偏。本节只定义
大纲的纯数据结构与校验/序列化（L2 Domain 零 I/O——落地/AI 全在 services/
compose_service，编排在 studio/compose_actions）。

落盘形态：``<slug>.compose/outline.json``
  {"sections":[{"id","title","target_chars","direction","status"}], "current_expanding":null}

节状态机：pending → approved（用户裁决）=可展开；expanded =已展开进块协议。
铁律：**未裁决（pending）不可展开**——裁决 gating 在数据面即表达，服务层
expand_section 强制执行。
"""
from __future__ import annotations

from dataclasses import dataclass

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_EXPANDED = "expanded"
SECTION_STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_APPROVED, STATUS_EXPANDED)


@dataclass
class OutlineSection:
    """大纲中的一节：id 稳定、title 标题、target_chars 目标字数、direction 方向备注、status 状态。"""

    id: str
    title: str
    target_chars: int = 0
    direction: str = ""
    status: str = STATUS_PENDING
    summary: str = ""  # 展开后的脉络摘要（供后续节的展开 prompt 携带）

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "target_chars": self.target_chars,
            "direction": self.direction,
            "status": self.status,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OutlineSection":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            target_chars=int(data.get("target_chars") or 0),
            direction=str(data.get("direction") or ""),
            status=str(data.get("status") or STATUS_PENDING),
            summary=str(data.get("summary") or ""),
        )


def validate_sections(raw: object) -> list[OutlineSection]:
    """校验并归一 sections 载荷；非法抛 ValueError（可读消息）。

    - raw 必须是非空 list，每项为 dict；
    - 每节 id 必填且与同大纲内其他 id 不重复（稳定标识，展开定位用）；
    - title 必填（空白不算）；
    - target_chars 为非负整数（0=未指定字数）；
    - status 必须为 SECTION_STATUSES 之一。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("大纲 sections 必须是非空数组")
    out: list[OutlineSection] = []
    seen: set[str] = set()
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 节必须是对象")
        sec = OutlineSection.from_dict(item)
        if not sec.id:
            raise ValueError(f"第 {i} 节缺 id")
        if sec.id in seen:
            raise ValueError(f"大纲 id 重复: {sec.id}")
        seen.add(sec.id)
        if not sec.title.strip():
            raise ValueError(f"第 {i} 节缺 title")
        if sec.target_chars < 0:
            raise ValueError(f"第 {i} 节 target_chars 不能为负")
        if sec.status not in SECTION_STATUSES:
            raise ValueError(f"第 {i} 节 status 非法: {sec.status}")
        out.append(sec)
    return out


def to_json(sections: list[OutlineSection], *, current_expanding: str | None = None) -> dict:
    """大纲 → outline.json 形态（含 current_expanding 光标题字段）。"""
    return {
        "sections": [s.to_dict() for s in sections],
        "current_expanding": current_expanding,
    }


def from_json(data: dict) -> tuple[list[OutlineSection], str | None]:
    """outline.json → (sections, current_expanding)；坏结构按空大纲宽容。"""
    if not isinstance(data, dict):
        return [], None
    try:
        sections = validate_sections(data.get("sections"))
    except ValueError:
        sections = []
    return sections, data.get("current_expanding")
