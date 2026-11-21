"""core/materials 纯函数测试（第二十期 §五第 1 行）。

覆盖：模型校验（参数化）/构造缺省/id 稳定散列/读侧宽容归一/合并读序
末次胜出/连坐解析/引用徽章状态/分诊谓词（patch 窄门+过滤+推荐资格）。
外加 core 零 I/O 架构断言（正典 §三依赖规则：core/materials 不触文件
系统——比架构门规则 3 的库清单更严的本期加严）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from musespiral.core.materials import model, triage


def _material(**overrides):
    """合法基准素材（覆盖式改键构造参数化样本）。"""
    base = {
        "mtype": "text",
        "source_id": "kb",
        "uri": "13-source-analysis/x.md",
        "title": "样例",
        "payload": {"local_path": "13-source-analysis/x.md"},
        "fetched_at": "2026-11-15T10:00:00",
    }
    base.update(overrides)
    return model.make_material(**base)


# ── 构造与 id ────────────────────────────────────────────────────────


def test_make_material_full_shape() -> None:
    row = model.make_material(
        mtype="text", source_id="kb", uri="a.md", fetched_at="2026-11-15T10:00:00",
    )
    assert row["type"] == "text"
    assert row["origin"] == {"source_id": "kb", "uri": "a.md", "title": ""}
    assert row["license"] == "UNKNOWN"
    assert row["provenance"] == {"chain": [], "derived_from": []}
    assert row["payload"] == {}
    assert row["triage"] == {"read": False, "starred": False, "enabled": True, "tags": [],
                             "verified": False, "confidence": None}  # §1.2 四键 + N-4 两键


def test_make_material_id_stable_and_distinct() -> None:
    a = model.make_material_id("kb", "text", "a.md")
    assert a == model.make_material_id("kb", "text", "a.md")
    assert a != model.make_material_id("kb", "text", "b.md")
    assert a != model.make_material_id("radarhub", "text", "a.md")
    assert len(a) == 12
    int(a, 16)  # 12 位十六进制


@pytest.mark.parametrize("mtype", model.MATERIAL_TYPES)
def test_validate_accepts_all_types(mtype: str) -> None:
    row = _material(mtype=mtype)
    if mtype == "derived":
        row["provenance"] = {"chain": [{"from_id": "x" * 12, "op": "summarize"}], "derived_from": []}
    assert model.validate_material(row) == []


def test_validate_error_cases() -> None:
    # 空 id
    assert any("id" in e for e in model.validate_material({**_material(), "id": ""}))
    # 坏 type / 坏 license
    assert any("type" in e for e in model.validate_material({**_material(), "type": "rss"}))
    assert any("license" in e for e in model.validate_material({**_material(), "license": "MIT"}))
    # origin 非对象 / 键类型错
    assert any("origin" in e for e in model.validate_material({**_material(), "origin": None}))
    bad_origin = _material()
    bad_origin["origin"] = {"source_id": 1, "uri": "a", "title": ""}
    assert any("source_id" in e for e in model.validate_material(bad_origin))
    # 空 fetched_at
    assert any("fetched_at" in e for e in model.validate_material({**_material(), "fetched_at": ""}))
    # 坏 op / 坏 from_id
    chain_bad = _material(mtype="derived")
    chain_bad["provenance"] = {"chain": [{"from_id": "x", "op": "rewrite"}], "derived_from": []}
    errors = model.validate_material(chain_bad)
    assert any("op" in e for e in errors)
    chain_bad["provenance"] = {"chain": [{"from_id": "", "op": "summarize"}], "derived_from": []}
    assert any("from_id" in e for e in model.validate_material(chain_bad))
    # 原始素材带血统（immutable 结构性防线）
    tainted = _material()
    tainted["provenance"] = {"chain": [], "derived_from": ["x" * 12]}
    assert any("原始素材" in e for e in model.validate_material(tainted))
    # payload 非对象
    assert any("payload" in e for e in model.validate_material({**_material(), "payload": "text"}))


# ── 读侧宽容归一 ────────────────────────────────────────────────────


def test_normalize_drops_bad_rows_and_fills_defaults() -> None:
    assert model.normalize_material(None) is None
    assert model.normalize_material({"type": "text"}) is None  # 无 id
    assert model.normalize_material({"id": "  "}) is None
    row = model.normalize_material({"id": " abc "})
    assert row is not None
    assert row["id"] == "abc"
    assert row["triage"] == {"read": False, "starred": False, "enabled": True, "tags": [],
                             "verified": False, "confidence": None}  # §1.2 四键 + N-4 两键
    assert row["provenance"] == {"chain": [], "derived_from": []}
    assert row["origin"] == {}
    # triage 键值非布尔按默认（宽容，不崩读侧）
    loose = model.normalize_material({"id": "a", "triage": {"read": "yes", "enabled": 0}})
    assert loose is not None
    assert loose["triage"] == {"read": False, "starred": False, "enabled": True, "tags": [],
                               "verified": False, "confidence": None}  # N-4 补默认同口径
    # tags 脏数据宽容：非列表→[]、非 str 元素剔除（读侧不崩）
    dirty = model.normalize_material({"id": "b", "triage": {"tags": "x"}})
    assert dirty is not None and dirty["triage"]["tags"] == []
    dirty2 = model.normalize_material({"id": "c", "triage": {"tags": ["ok", 1, None]}})
    assert dirty2 is not None and dirty2["triage"]["tags"] == ["ok"]


def test_normalize_tombstone_passthrough() -> None:
    row = model.normalize_material({"id": "x", "deleted": True})
    assert row == {"id": "x", "deleted": True}


# ── 合并读序：同 id 末次胜出 ─────────────────────────────────────────


def test_merge_rows_last_write_wins() -> None:
    first = _material()
    starred = {**first, "triage": {"read": True, "starred": True, "enabled": True}}
    tomb = model.make_tombstone(first["id"])
    index = model.merge_rows([first, starred])
    assert index[first["id"]] == starred
    # 墓碑也是「末次」——胜出即已删
    index = model.merge_rows([first, starred, tomb])
    assert model.is_tombstone(index[first["id"]])
    active, deleted = model.split_active(index)
    assert active == [] and deleted == {first["id"]}


def test_split_active_partition() -> None:
    a, b = _material(uri="a.md"), _material(uri="b.md")
    tomb = model.make_tombstone(b["id"])
    active, deleted = model.split_active(model.merge_rows([a, tomb]))
    assert [m["id"] for m in active] == [a["id"]]
    assert deleted == {b["id"]}


# ── 连坐解析与引用徽章 ──────────────────────────────────────────────


def test_resolve_cascade_both_reference_paths() -> None:
    origin = _material()
    via_chain = _material(
        mtype="derived", uri="derived:1",
        provenance={"chain": [{"from_id": origin["id"], "op": "summarize"}], "derived_from": []},
    )
    via_list = _material(
        mtype="derived", uri="derived:2", provenance={"chain": [], "derived_from": [origin["id"]]},
    )
    bystander = _material(uri="other.md")
    cascade = model.resolve_cascade(origin["id"], [origin, via_chain, via_list, bystander])
    assert {m["id"] for m in cascade} == {via_chain["id"], via_list["id"]}
    # 自身不被连坐
    assert origin["id"] not in {m["id"] for m in cascade}


def test_ref_statuses_badge() -> None:
    alive = _material(uri="alive.md")
    derived = _material(
        mtype="derived", uri="d:1",
        provenance={
            "chain": [{"from_id": alive["id"], "op": "summarize"}],
            "derived_from": ["gone00000000"],
        },
    )
    statuses = model.ref_statuses(derived, {alive["id"]})
    by_id = {s["from_id"]: s for s in statuses}
    assert by_id[alive["id"]]["deleted"] is False
    assert by_id[alive["id"]]["op"] == "summarize"
    assert by_id["gone00000000"]["deleted"] is True  # 「素材已删」徽章数据面


# ── 分诊谓词（triage.py）────────────────────────────────────────────


def test_triage_patch_narrow_gate() -> None:
    base = {"read": False, "starred": False, "enabled": True, "tags": []}
    patched = triage.triage_patch(base, {"starred": True})
    assert patched == {"read": False, "starred": True, "enabled": True, "tags": []}
    assert base["starred"] is False  # 纯函数：入参不改
    with pytest.raises(ValueError, match="五键"):  # §1.2 修订三键→四键；N-4 四键→五键（+verified）
        triage.triage_patch(base, {"payload": {}})
    with pytest.raises(ValueError, match="布尔"):
        triage.triage_patch(base, {"read": 1})
    # tags 双轨：list[str] 全量替换 + 归一（§1.2 唯一契约）
    patched = triage.triage_patch(base, {"tags": ["封面 素材", "ABC"]})
    assert patched["tags"] == ["封面-素材", "abc"]
    assert patched["tags"] == triage.triage_patch(base, {"tags": ["封面-素材", "abc"]})["tags"]  # 归一幂等
    with pytest.raises(ValueError, match="字符串列表"):
        triage.triage_patch(base, {"tags": "a"})
    with pytest.raises(ValueError, match="24"):
        triage.triage_patch(base, {"tags": ["x" * 25]})
    with pytest.raises(ValueError, match="12"):
        triage.triage_patch(base, {"tags": [f"t{i}" for i in range(13)]})
    # tags 与三布尔同请求可共存（§二-F63）
    patched = triage.triage_patch(base, {"starred": True, "tags": ["a"]})
    assert patched == {"read": False, "starred": True, "enabled": True, "tags": ["a"]}


def test_filter_materials_and_defaults() -> None:
    plain = _material(uri="a.md")
    starred = _material(uri="b.md", triage={"read": True, "starred": True, "enabled": False})
    rows = [plain, starred]
    assert triage.filter_materials(rows, starred=True) == [starred]
    assert triage.filter_materials(rows, read=True) == [starred]
    assert triage.filter_materials(rows, enabled=True) == [plain]
    assert triage.filter_materials(rows) == rows  # 全 None=不过滤
    # 裸行缺 triage 按默认（read=False/starred=False/enabled=True）
    assert triage.filter_materials([{"id": "x"}, starred], starred=False) == [{"id": "x"}]


def test_matches_unknown_key_rejected() -> None:
    with pytest.raises(ValueError, match="未知分诊键"):
        triage.matches(_material(), star=True)


def test_is_recommendable() -> None:
    assert triage.is_recommendable(_material()) is True
    disabled = _material(uri="d.md", triage={"read": False, "starred": False, "enabled": False})
    assert triage.is_recommendable(disabled) is False
    assert triage.is_recommendable({"id": "x"}) is True  # 裸行默认可推荐


# ── core/materials 零 I/O 架构断言（本期加严）───────────────────────


def test_core_materials_layer_is_pure() -> None:
    """core/materials 全模块 import 白名单：stdlib 纯计算件之外零依赖。

    禁 pathlib/os/json/io/open/subprocess/config——零 I/O 不是只禁架构门
    规则 3 的四库，而是整个文件系统/时钟面（fetched_at 由调用方注入）。
    第二十一期扩两模块（tags/search）：re 属纯计算 stdlib，纳入白名单。
    """
    allowed = {"__future__", "hashlib", "typing", "dataclasses", "datetime", "re"}
    pkg = Path(model.__file__).parent
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                assert root in allowed, f"{path.name} 引入了非纯依赖: {name}"
