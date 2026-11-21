"""F2 主题引擎测试 — extends 深合并 / 循环与链深检测 / 用户目录覆盖 / 骨架生成。

全部通过 MUSESPIRAL_HOME 指向 tmp_path，绝不读写真实 ~/.musespiral。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from musespiral.core.content.themes import list_all, load_theme, new_skeleton

_MERGE_SECTIONS = ("colors", "typography", "components", "colors_dark")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """autouse：所有测试的 musespiral 状态目录都指向临时目录，隔离真实 ~/.musespiral。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def user_home(_isolated_home: Path) -> Path:
    """返回（已隔离的）musespiral 状态目录路径。"""
    return _isolated_home


def write_user_theme(home: Path, name: str, data: dict) -> Path:
    """在用户主题目录写入一个主题 YAML。"""
    d = home / "themes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ── 内置主题加载 ──────────────────────────────────────────


@pytest.mark.parametrize("name", ["lapis", "default", "phycat", "ember", "mint"])
def test_load_builtin_theme(name: str) -> None:
    """五套内置主题均可加载且四段结构齐全。"""
    t = load_theme(name)
    for section in _MERGE_SECTIONS:
        assert section in t and isinstance(t[section], dict), f"{name} 缺段 {section}"


def test_new_builtins_align_lapis_components() -> None:
    """ember/mint 的组件键与 lapis 完全对齐（渲染引擎按键取样式）。"""
    lapis = load_theme("lapis")
    for name in ("ember", "mint"):
        assert set(load_theme(name)["components"]) == set(lapis["components"])


def test_load_theme_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_theme("no-such-theme")


def test_load_theme_rejects_path_like_name() -> None:
    with pytest.raises(ValueError):
        load_theme("../etc/passwd")


# ── list_all ──────────────────────────────────────────────


def test_list_all_builtin_labels() -> None:
    names = dict(list_all())
    assert names["lapis"] == "内置"
    assert names["ember"] == "内置"
    assert names["mint"] == "内置"


def test_list_all_user_overrides_builtin_label(user_home: Path) -> None:
    write_user_theme(user_home, "lapis", {"colors": {"primary": "#000000"}})
    write_user_theme(user_home, "mine", {"extends": "lapis"})
    names = dict(list_all())
    assert names["lapis"] == "用户"  # 同名覆盖内置
    assert names["mine"] == "用户"
    assert names["ember"] == "内置"


# ── 用户目录优先于内置 ────────────────────────────────────


def test_user_theme_overrides_builtin(user_home: Path) -> None:
    """同名用户主题整体覆盖内置（无 extends 时不合并）。"""
    write_user_theme(user_home, "lapis", {
        "name": "lapis",
        "colors": {"primary": "#123456", "background": "#ffffff"},
    })
    t = load_theme("lapis")
    assert t["colors"]["primary"] == "#123456"
    assert "typography" not in t  # 无 extends 即不继承内置段


def test_user_extends_builtin_partial_override(user_home: Path) -> None:
    write_user_theme(user_home, "myblue", {"extends": "lapis", "colors": {"primary": "#0099ff"}})
    t = load_theme("myblue")
    assert t["colors"]["primary"] == "#0099ff"  # 覆盖项生效
    lapis = load_theme("lapis")
    assert t["typography"] == lapis["typography"]  # 未覆盖段原样继承
    assert t["components"] == lapis["components"]
    assert t["colors_dark"] == lapis["colors_dark"]
    assert t["colors"]["muted"] == lapis["colors"]["muted"]  # 同段未覆盖键保留


# ── extends 深合并 ────────────────────────────────────────


def test_extends_deep_merge_keeps_sibling_keys(user_home: Path) -> None:
    """覆盖 components.heading.h2 不丢 h1/h3，也不丢其他三段。"""
    write_user_theme(user_home, "child", {
        "extends": "lapis",
        "components": {"heading": {"h2": "color: red;"}},
    })
    t = load_theme("child")
    lapis = load_theme("lapis")
    assert t["components"]["heading"]["h2"] == "color: red;"
    assert t["components"]["heading"]["h1"] == lapis["components"]["heading"]["h1"]
    assert t["components"]["heading"]["h3"] == lapis["components"]["heading"]["h3"]
    for section in _MERGE_SECTIONS:
        assert section in t, f"深合并丢了段 {section}"


def test_extends_chain_depth_two_ok(user_home: Path) -> None:
    """用户→用户→内置的两级 extends 链合法且逐级覆盖。"""
    write_user_theme(user_home, "mid", {"extends": "lapis", "colors": {"primary": "#111111"}})
    write_user_theme(user_home, "leaf", {"extends": "mid", "colors": {"background": "#222222"}})
    t = load_theme("leaf")
    assert t["colors"]["primary"] == "#111111"  # 继承 mid 的覆盖
    assert t["colors"]["background"] == "#222222"  # 自己的覆盖
    assert "typography" in t  # 经 mid 继承自 lapis


def test_extends_chain_depth_over_limit(user_home: Path) -> None:
    write_user_theme(user_home, "a", {"extends": "b"})
    write_user_theme(user_home, "b", {"extends": "c"})
    write_user_theme(user_home, "c", {"extends": "lapis"})
    with pytest.raises(ValueError, match="链深"):
        load_theme("a")


def test_extends_cycle_raises(user_home: Path) -> None:
    write_user_theme(user_home, "x", {"extends": "y"})
    write_user_theme(user_home, "y", {"extends": "x"})
    with pytest.raises(ValueError, match="循环"):
        load_theme("x")


def test_extends_self_cycle_raises(user_home: Path) -> None:
    write_user_theme(user_home, "selfish", {"extends": "selfish"})
    with pytest.raises(ValueError, match="循环"):
        load_theme("selfish")


def test_extends_missing_parent_raises(user_home: Path) -> None:
    write_user_theme(user_home, "orphan", {"extends": "ghost"})
    with pytest.raises(FileNotFoundError):
        load_theme("orphan")


# ── 骨架生成 ──────────────────────────────────────────────


def test_new_skeleton_loadable(user_home: Path) -> None:
    """生成的骨架含 extends，且能被 load_theme 正常加载（深合并补齐四段）。"""
    out_dir = user_home / "themes"
    p = new_skeleton("mytheme", "lapis", out_dir)
    assert p == out_dir / "mytheme.yaml" and p.is_file()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["extends"] == "lapis"
    t = load_theme("mytheme")
    for section in _MERGE_SECTIONS:
        assert section in t
    assert t["colors"]["primary"] == raw["colors"]["primary"]
    assert t["typography"] == load_theme("lapis")["typography"]


def test_new_skeleton_refuses_overwrite(user_home: Path) -> None:
    out_dir = user_home / "themes"
    new_skeleton("dup", "ember", out_dir)
    with pytest.raises(FileExistsError):
        new_skeleton("dup", "ember", out_dir)


def test_new_skeleton_invalid_base(user_home: Path) -> None:
    with pytest.raises(FileNotFoundError):
        new_skeleton("okname", "ghost", user_home / "themes")


def test_new_skeleton_invalid_name(user_home: Path) -> None:
    with pytest.raises(ValueError):
        new_skeleton("../evil", "lapis", user_home / "themes")
