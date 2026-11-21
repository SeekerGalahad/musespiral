"""选题雷达聚合核心（Phase D F78，L2 纯）——三源候选聚合+已写过排除+确定性排序。

设计（musespiral-D智能体设计-2026-11-16.md §二 F78.3）：

  输入 = 三条供给腿的**已装配数据**（I/O 全部归 services/topic_radar_service，
  本模块零 I/O 零时钟零全局态——同输入两次输出逐字节一致）：

    radar_items      radar_signal 活跃素材（service 按 enabled 取，含
                     meta.grade/score 与 meta.url）
    kb_items         KB 扫描项（service 复用 discovery/scanner+scorer 现算，
                     mtime 窗口 30 天内的新鲜资产）
    star_items       星标素材（triage.starred=true 的人工意向）
    published_titles 已发标题清单（publish_log+正稿 frontmatter，90 天窗，
                     service 装配；None 视为空清单=不排除）

  聚合输出 = 候选行列表 [{kind: radar|kb|star, title, refs:{material_id?,
  path?, url?}, score, reasons[], excluded?}]——excluded 是**候选行上的
  可选键**（§1.4 快照形状：candidates 数组含被剔行，无顶层 excluded 集合），
  被剔行携带 {matched_title, similarity} 供排雷回看。

  已写过排除：候选标题与任一已发标题的 difflib 相似度 ≥EXCLUDE_SIMILARITY
  （0.6，**含等号**——阈值沿 composition/protocol.py 判例口径，独立常量）
  即剔除；对全部已发标题取最高分命中（并列取清单序在前者——确定性）。

  排序全确定性：保留行 score 降序 → kind 字典序（kb < radar < star）→
  title 字典序；被剔行不参与候选序位，按同一键排序后整体追加在尾部。

star 腿无内在评分，取固定基准分 STAR_SCORE（低于 KB 深度分与雷达信号常见
档——星标是意向信号不是热度信号，排序靠前但不压制有据可查的两腿）。
"""
from __future__ import annotations

import difflib

# 已写过排除的相似度下限（设计 §二 F78.3：沿 protocol.py 0.6 判例口径，
# 独立常量；**边界含等号：=0.6 剔除**）
EXCLUDE_SIMILARITY = 0.6

# star 腿固定基准分（本模块单点定义；消费方不得另设）
STAR_SCORE = 40

# kind 字典序（排序并列时的第二键；测试锚定 kb<radar<star）
KIND_ORDER = ("kb", "radar", "star")


def _refs(*, material_id: str | None = None, path: str | None = None, url: str | None = None) -> dict[str, str]:
    """refs 对象（键序恒 material_id→path→url，缺键省略——快照逐字节确定性）。"""
    out: dict[str, str] = {}
    if material_id:
        out["material_id"] = material_id
    if path:
        out["path"] = path
    if url:
        out["url"] = url
    return out


def _match_published(title: str, published: list[str]) -> tuple[str, float] | None:
    """最高相似度命中（≥threshold 才算；并列取清单序在前——确定性）。"""
    best: tuple[str, float] | None = None
    stripped = title.strip()
    for pub in published:
        score = difflib.SequenceMatcher(None, stripped, str(pub).strip()).ratio()
        if best is None or score > best[1]:
            best = (str(pub), score)
    return best


def _mark_excluded(rows: list[dict], published: list[str], threshold: float) -> None:
    """已写过排除（原地）：相似度 ≥threshold 的行打 excluded 键（含等号）。"""
    for row in rows:
        hit = _match_published(str(row.get("title") or ""), published)
        if hit is not None and hit[1] >= threshold:  # 含等号
            row["excluded"] = {"matched_title": hit[0], "similarity": hit[1]}


def _sort_rows(rows: list[dict]) -> None:
    """确定性排序（原地）：score 降序 → kind 字典序 → title 字典序。"""
    rows.sort(key=lambda r: (-int(r.get("score") or 0), str(r.get("kind") or ""), str(r.get("title") or "")))


def candidates(
    radar_items: list[dict],
    kb_items: list[dict],
    star_items: list[dict],
    published_titles: list[str] | None = None,
    *,
    threshold: float = EXCLUDE_SIMILARITY,
) -> list[dict]:
    """三源聚合 → 候选行列表（保留行在前按确定性序，被剔行追加尾部）。

    每腿输入行形状（service 装配，宽容缺键）：
      radar_items  {material_id, title, score, grade?, url?}
      kb_items     {path, title, score}
      star_items   {material_id, title, url?}

    输出行：{kind, title, refs, score, reasons[]}，被剔行另带
    excluded={matched_title, similarity}。同输入两次调用输出逐字节一致
    （json.dumps 可复现——确定性验收线）。
    """
    rows: list[dict] = []
    # 构造序钉死 radar→kb→star（同 (score,kind,title) 重复行时稳定序的兜底）
    for item in radar_items or []:
        title = str(item.get("title") or "")
        reasons = ["radar-hub 采集信号"]
        grade = str(item.get("grade") or "")
        if grade:
            reasons.append(f"评级 {grade}")
        rows.append({
            "kind": "radar",
            "title": title,
            "refs": _refs(material_id=str(item.get("material_id") or "") or None,
                          url=str(item.get("url") or "") or None),
            "score": int(item.get("score") or 0),
            "reasons": reasons,
        })
    for item in kb_items or []:
        rows.append({
            "kind": "kb",
            "title": str(item.get("title") or ""),
            "refs": _refs(path=str(item.get("path") or "") or None),
            "score": int(item.get("score") or 0),
            "reasons": ["知识库新鲜资产（30 天内更新）"],
        })
    for item in star_items or []:
        rows.append({
            "kind": "star",
            "title": str(item.get("title") or ""),
            "refs": _refs(material_id=str(item.get("material_id") or "") or None,
                          url=str(item.get("url") or "") or None),
            "score": STAR_SCORE,
            "reasons": ["人工星标"],
        })
    _mark_excluded(rows, list(published_titles or []), float(threshold))
    kept = [r for r in rows if "excluded" not in r]
    excluded = [r for r in rows if "excluded" in r]
    _sort_rows(kept)
    _sort_rows(excluded)
    return kept + excluded
