"""物料包平台限制校验（F15，Phase C 自 pack.py 拆出；F33B 扩物料级三项）。

checks.txt 逐项校验的纯逻辑：limits 键归一（platforms.profiles 风格 →
校验键）+ 逐行 ok/FAIL 判定。pack.build_pack 只做编排（取材/写盘），本
模块承载「限制怎么校验」这一可独立测试的领域规则。

limits 为 dict 依赖注入（集成阶段接 platforms.profiles.get_limits，本
模块不 import platforms）：支持键 title_max / title_min / content_max /
tags_max，缺哪键跳过哪项；limits 为 None 或不含任何已知键时 checks.txt
输出「未接入平台限制表」（由 _checks_lines 返回）。

F33B 物料级扩展（§二 F33B）：_material_checks_lines 消费 images_min/
images_max（图数越界 FAIL，xhs 1-18 张机器核对，承接缺口 1-8）与封面
规格键 cover_w/cover_h（pack 物料 cover/ 内 cover.<platform>.png 或
cover.png 尺寸不符 FAIL；探测优先级 cover.<platform>.png > cover.png >
唯一非标准名文件——frontmatter cover 文件名自由，非标准名也要进核对面）。
封面缺失或 PIL 读不出尺寸跳过（与 render_checks 同口径——存在性/可读性
不是本项的判据，规格才是）。

F42 追加封面大小键 cover_max_bytes（bilibili ≤5MB）：超限出 WARN 不
FAIL——体积超限可人工压缩后再传，属告警不属否决；缺键平台跳过。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# checks.txt 逐项校验的（注入键, 指标名, 通过判据）；limits 缺哪键跳过哪项
_CHECK_KEYS: tuple[tuple[str, str, str], ...] = (
    ("title_max", "标题字数", "≤"),
    ("title_min", "标题最短字数", "≥"),
    ("content_max", "正文字数", "≤"),
    ("tags_max", "标签数", "≤"),
)

# platforms.profiles 键名 → 校验键归一（CLI 接线注入的是 get_limits 原键；
# channels/douyin 的"正文"即描述/口播稿，desc_max_chars 映射到 content_max；
# images_min/images_max 原名即归一名，供 _material_checks_lines 图数核对）
_LIMIT_ALIASES: dict[str, str] = {
    "title_max": "title_max",
    "title_min": "title_min",
    "content_max": "content_max",
    "tags_max": "tags_max",
    "title_max_chars": "title_max",
    "title_min_chars": "title_min",
    "content_max_chars": "content_max",
    "desc_max_chars": "content_max",
    "tags_max_chars": "tags_max",
    "images_min": "images_min",
    "images_max": "images_max",
}


def _normalize_limits(limits: dict[str, int] | None) -> dict[str, int] | None:
    """把 profiles 风格键（*_chars 后缀/desc_*）归一到 _CHECK_KEYS 键。

    未知键忽略；归一后无任何已知键时返回 None（checks.txt 提示未接入）。
    """
    if limits is None:
        return None
    out: dict[str, int] = {}
    for k, v in limits.items():
        mapped = _LIMIT_ALIASES.get(k)
        if mapped is None or not isinstance(v, (int, float)):
            continue
        out[mapped] = int(v)
    return out or None


def _checks_lines(
    title: str,
    content: str,
    tags: list[str],
    limits: dict[str, int] | None,
) -> list[str]:
    """checks.txt 逐行校验：通过打 ok、超限/低于下限标 FAIL、标题缺失标必填 FAIL；
    未注入限制表时提示跳过。"""
    values = {
        "title_max": len(title),
        "title_min": len(title),
        "content_max": len(content.strip()),
        "tags_max": len(tags),
    }
    lines: list[str] = []
    title_missing_reported = False
    for key, label, op in _CHECK_KEYS:
        if limits is None or key not in limits:
            continue
        value, limit = values[key], limits[key]
        if key.startswith("title") and value == 0:
            # 标题缺失是必填项错误（平台如视频号标题 3-16 字），不再是「0 字合规」；只报一次
            if not title_missing_reported:
                lines.append(f"FAIL {label}: 0/{limit}（未设置，frontmatter.title 必填）")
                title_missing_reported = True
            continue
        violated = value > limit if op == "≤" else value < limit
        if violated:
            reason = "超限" if op == "≤" else "低于下限"
            lines.append(f"FAIL {label}: {value}/{limit}（{reason}）")
        else:
            lines.append(f"ok {label}: {value}/{limit}")
    if not lines:
        lines.append("未接入平台限制表")
    return lines


# ── F33B 物料级校验：图数（images_min/max）+ 封面尺寸（cover_w/h）──


def count_pack_images(pack_dir: str | Path) -> int:
    """物料包内将上传给平台的图片总数：images-cards/ 全部 PNG（xhs 图文卡，
    F22 整组带入）+ cover/ 内全部文件（封面）。笔记类平台（xhs）图数上限
    核对的是「上传图总数」，封面也占一坑——README 指引同口径。"""
    pack = Path(pack_dir)
    cards_dir = pack / "images-cards"
    n = len(list(cards_dir.glob("*.png"))) if cards_dir.is_dir() else 0
    cover_dir = pack / "cover"
    if cover_dir.is_dir():
        n += len([p for p in cover_dir.iterdir() if p.is_file()])
    return n


def _find_pack_cover(pack_dir: str | Path, platform: str) -> Path | None:
    """物料包封面探测：cover/cover.<platform>.png 优先，退 cover.png
    （与 F31 落盘命名/探测优先级对齐）；两者皆无但 cover/ 内恰有一个文件
    （build_pack 复制的 frontmatter 封面，文件名自由——banner.jpg 等）→
    该文件即封面，照样进尺寸核对（check 将 cover 列为必填项但文件名自由，
    非标准名不能零核对放行）；目录为空或多文件且无标准名 → None（跳过）。"""
    cover_dir = Path(pack_dir) / "cover"
    if not cover_dir.is_dir():
        return None
    for name in (f"cover.{platform}.png", "cover.png"):
        candidate = cover_dir / name
        if candidate.is_file():
            return candidate
    files = [p for p in cover_dir.iterdir() if p.is_file()]
    return files[0] if len(files) == 1 else None


def _material_checks_lines(
    pack_dir: str | Path, platform: str, limits: dict | None
) -> list[str]:
    """物料级校验行（§二 F33B）：图数越界 FAIL + 封面尺寸不符 FAIL。

    limits 为注入的 profiles 原始 dict（非归一键）：images_min/images_max/
    cover_w/cover_h 缺哪键跳过哪项，全缺返回 []。封面 PIL 读不出尺寸跳过
    （与 render_checks 同口径）。
    """
    if not limits:
        return []
    lines: list[str] = []

    image_min = limits.get("images_min")
    image_max = limits.get("images_max")
    if isinstance(image_min, int) or isinstance(image_max, int):
        count = count_pack_images(pack_dir)
        if isinstance(image_min, int) and isinstance(image_max, int):
            bound = f"{image_min}-{image_max}"
        elif isinstance(image_min, int):
            bound = f"≥{image_min}"
        else:
            bound = f"≤{image_max}"
        if isinstance(image_min, int) and count < image_min:
            lines.append(f"FAIL 图片数: {count}/{bound}（低于下限）")
        elif isinstance(image_max, int) and count > image_max:
            lines.append(f"FAIL 图片数: {count}/{bound}（超限）")
        else:
            lines.append(f"ok 图片数: {count}/{bound}")

    expected_w, expected_h = limits.get("cover_w"), limits.get("cover_h")
    cover_max_bytes = limits.get("cover_max_bytes")
    has_dims = isinstance(expected_w, int) and isinstance(expected_h, int)
    has_size_cap = isinstance(cover_max_bytes, int)
    if has_dims or has_size_cap:
        cover_path = _find_pack_cover(pack_dir, platform)
    else:
        cover_path = None

    if has_dims and cover_path is not None:
        width = height = 0
        try:
            with Image.open(cover_path) as im:
                width, height = im.size
        except Exception:
            width = height = 0  # 读不出尺寸：跳过（render_checks 同口径）
        if width and height:
            if (width, height) != (expected_w, expected_h):
                lines.append(
                    f"FAIL 封面尺寸: {width}×{height} ≠ {expected_w}×{expected_h}"
                    f"（{platform} 规格见 platform list / §一 表）"
                )
            else:
                lines.append(f"ok 封面尺寸: {width}×{height}")

    # 封面大小（F42 bilibili ≤5MB）：超限 WARN 不 FAIL——尺寸合规但体积超限
    # 的图仍可人工压缩后上传，故只告警提醒（缺 cover_max_bytes 键的平台跳过）
    if has_size_cap and cover_path is not None:
        size = cover_path.stat().st_size
        mb, cap_mb = size / 1048576, cover_max_bytes / 1048576
        if size > cover_max_bytes:
            lines.append(
                f"WARN 封面大小: {mb:.1f}MB > {cap_mb:.0f}MB"
                f"（{platform} 上传上限，需压缩后再传）"
            )
        else:
            lines.append(f"ok 封面大小: {mb:.1f}MB ≤ {cap_mb:.0f}MB")
    return lines
