"""materials 包：iterations（打磨版本链读面，P2）。

ARCH-2（2026-11-22）自 ``services/refine_service.py`` 迁入的迭代链族——
它操作的是素材（读 materials 库），落点归素材域；refine_service 经本模块
re-export 保持调用面零改动。链写面（adopt 写 META_ITERATION_KEYS 三键）
仍在 refine_service.adopt——「AI 只起草，人工采纳才落库」的红线不动。

  - _iter_meta/_iter_no/_chain_root/_chain_versions：版本链判定四助手
    （adopt 与读面共用——迁入后 adopt 自 refine_service 反向导入）；
  - list_iterations：版本列表（同链根全部版本，iteration 升序）；
  - diff_iterations：两版对比（复用 core/version/diff.diff_result，不另写
    diff 算法；跨链拒绝；html 服务端渲染，E-6 /api/article/diff 同款产物面）。

RefineError 仍以 refine_service 为单一事实源（错误类型不动）——本模块
函数体内导入同一类对象，pytest.raises(RefineError) 判别语义不变。
"""
from __future__ import annotations

from typing import Any

from ...core.version.diff import diff_result as _diff_result
from ...core.version.diff import render_diff_html as _render_diff_html
from .. import material_body
from .crud import get_material, list_materials


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
    for m in list_materials():
        if m.get("type") != "derived":
            continue
        if str(_iter_meta(m).get("iteration_root") or "") != root:
            continue
        if _iter_no(m) is None:
            continue
        out.append(m)
    out.sort(key=lambda m: (_iter_no(m) or 0, str(m.get("fetched_at") or ""), m["id"]))
    return out


def list_iterations(material_id: str) -> dict[str, Any]:
    """版本列表：同一链根下所有版本，按 iteration 升序。

    入参可以是链上任一素材（原始根 / 任一版本）——链根判定同 adopt。
    返回 {"root", "versions":[{id, iteration, ts, title, chars}]}；chars=
    payload.text 全长（不截断——列表是展示面不是 AI 输入预算面）。
    素材不存在/已删 → RefineError。
    """
    from ..refine_service import RefineError  # 函数体内导入（错误类型单一事实源）

    material = get_material(material_id)
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
    from ..refine_service import RefineError  # 函数体内导入（错误类型单一事实源）

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
    from ..refine_service import RefineError  # 函数体内导入（错误类型单一事实源）

    a = get_material(id_a)
    if a is None:
        raise RefineError(f"素材不存在或已删: {id_a!r}")
    b = get_material(id_b)
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
