"""Theme engine — user theme directory + extends inheritance.

Loading order: ~/.musespiral/themes/<name>.yaml (user) overrides the built-in
musespiral/renderer/themes/<name>.yaml. A theme may declare `extends: <base>` to
inherit from another theme; the four style sections (colors / typography /
components / colors_dark) are deep-merged, child keys win.

extends 链深 ≤2，且解析时记录访问路径，循环继承直接报错。

The renderer keeps its own `_load_theme` until the integration stage swaps
it for `load_theme` here.
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ... import config

# 内置主题 YAML 数据目录（Phase C 自 musespiral/renderer/themes/ 随包迁入 themes_data/）
_BUILTIN_DIR = resources.files("musespiral.core.content").joinpath("themes_data")
_MERGE_SECTIONS = ("colors", "typography", "components", "colors_dark")
_MAX_EXTENDS_DEPTH = 2  # A extends B extends C 为上限（两级 extends）
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_SKELETON_TEMPLATE = """\
# musespiral 用户主题骨架 — extends 继承基座 {base}
# 只写要覆盖的键，colors/typography/components/colors_dark 会与基座深合并；
# 编辑后用 `musespiral preview` 查看效果。
name: {name}
description: 基于 {base} 的自定义主题
extends: {base}
colors:
  primary: "#e11d48"
  quote_border: "#e11d48"
colors_dark:
  primary: "#fb7185"
"""


def _user_theme_dir() -> Path:
    """用户主题目录 ~/.musespiral/themes/（受 MUSESPIRAL_HOME 覆盖）。"""
    return config.home_dir() / "themes"


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"主题名只能包含字母/数字/下划线/连字符: {name}")


def _find_theme_path(name: str) -> Any:
    """按「用户目录优先于内置目录」定位主题文件，找不到返回 None。

    返回值可能是 pathlib.Path（用户目录）或 Traversable（内置包资源），
    两者均支持 is_file()/read_text()。
    """
    user_path = _user_theme_dir() / f"{name}.yaml"
    if user_path.is_file():
        return user_path
    builtin = _BUILTIN_DIR / f"{name}.yaml"
    if builtin.is_file():
        return builtin
    return None


def _read_yaml(path: Path | Any, name: str) -> dict[str, Any]:
    """读取单个主题 YAML，格式错误统一报 ValueError。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"主题文件不是合法 YAML: {path} ({e})") from e
    if not isinstance(data, dict):
        raise ValueError(f"主题文件结构错误（顶层应为映射）: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深合并：override 的键胜出，嵌套映射递归合并，base 的其余键保留。"""
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_theme(name: str) -> dict[str, Any]:
    """按名加载主题（用户目录优先），解析 extends 并深合并后返回。

    - extends 逐级加载父主题，colors/typography/components/colors_dark
      四段深合并，子键覆盖父键；
    - 链深超过两级或出现循环继承时抛 ValueError；
    - 主题不存在抛 FileNotFoundError。
    """
    return _load_chain(name, seen=[], depth=0)


def _load_chain(name: str, seen: list[str], depth: int) -> dict[str, Any]:
    _validate_name(name)
    if name in seen:
        raise ValueError(f"主题 extends 出现循环: {' -> '.join(seen + [name])}")
    path = _find_theme_path(name)
    if path is None:
        available = ", ".join(n for n, _ in list_all())
        raise FileNotFoundError(f"主题不存在: {name}（可用: {available}）")
    data = _read_yaml(path, name)
    parent = data.pop("extends", None)
    if parent is None:
        return data
    if depth + 1 > _MAX_EXTENDS_DEPTH:
        raise ValueError(
            f"主题 extends 链深超过 {_MAX_EXTENDS_DEPTH}: {' -> '.join(seen + [name])} -> {parent}"
        )
    base = _load_chain(str(parent), seen=seen + [name], depth=depth + 1)
    return _deep_merge(base, data)


def list_all() -> list[tuple[str, str]]:
    """枚举全部主题：[(名字, "内置"|"用户")]，用户目录同名覆盖内置，按名排序。"""
    found: dict[str, str] = {}
    for p in _BUILTIN_DIR.glob("*.yaml"):
        found[p.stem] = "内置"
    user_dir = _user_theme_dir()
    if user_dir.is_dir():
        for p in user_dir.glob("*.yaml"):
            found[p.stem] = "用户"
    return sorted(found.items())


def new_skeleton(name: str, base: str, out_dir: str | Path) -> Path:
    """生成含 extends 的用户主题骨架 YAML，返回文件路径。

    已存在同名文件抛 FileExistsError；base 必须是可解析的现有主题。
    """
    _validate_name(name)
    target = Path(out_dir) / f"{name}.yaml"
    if target.exists():
        raise FileExistsError(f"主题已存在: {target}")
    load_theme(base)  # 校验基座存在且 extends 合法
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_SKELETON_TEMPLATE.format(name=name, base=base), encoding="utf-8")
    return target
