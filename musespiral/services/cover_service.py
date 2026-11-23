"""F31 封面生成用例编排：文章 → 目标规格封面 PNG + frontmatter cover 写回。

编排链（§三 3.1）：读文章 → core.visual.cover 四路线生成 → 落盘文章目录
cover.png（默认/mp）或 cover.<platform>.png（其余平台）。写回只在默认
（mp 规格或未给 --platform）时发生（§二 F31 落盘与写回两行各管各的）；
非 mp 封面只落图——frontmatter cover 是文章级/mp 链路的规范指针
（check --render 封面比例、generate 嵌图都读它），被平台派生图覆写会顶替
mp 发布链路封面；平台封面由 pack/check --platform 按「文章目录探测优先级
cover.<platform>.png > cover.png/frontmatter 指向」发现。

写回纪律（§四 Q1/Q3/Q5，仅 mp/默认）：
  - 落盘前当前版入滚动版本池（F43：infra/storage/filesystem.backup_article
    落 <name>.md.bak.<时间戳> 保留 N 份——三处写回共用 helper）；cover.png
    重生成直接覆盖（幂等产物，再生成是常态）；
  - frontmatter 走 load→改 cover→dumps 整体重写（与 digest --apply 同款，
    键序可能重排、YAML 注释会丢失）；--no-write-back 只落图（mp 专用语义，
    非 mp 本就不写回，旗标对它是无害冗余）。
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import frontmatter

from ..core.visual import cover
from ..infra.storage.filesystem import backup_article
from ..platforms.profiles import PLIMITS

# 平台 → 落盘文件名：默认（mp 规格或未给 --platform）为 cover.png，其余派生
# cover.<platform>.png（pack 物料探测优先级 cover.<platform>.png > cover.png）
# 命名规则真身已上移 core/visual/cover.py（2026-11-24 W2 断环批）——本模块
# 保留同名 re-export 使既有调用方零改动（`from ...cover_service import
# cover_filename` 仍可用；下个版本可迁向 core 直取）。
from ..core.visual.cover import DEFAULT_COVER_NAME, cover_filename  # noqa: F401


def generate_cover(
    article_path: str | Path,
    *,
    route: str = "title-card",
    platform: str | None = None,
    image: str | Path | None = None,
    theme: str = "lapis",
    write_back: bool = True,
) -> Path:
    """生成封面并落盘文章目录，返回 PNG 路径。

    - route：core.visual.cover.ROUTES 四路线之一；from-image 必须给 image；
    - platform：None/mp → cover.png（900×383）+ 写回 frontmatter cover；
      xhs/channels/douyin → cover.<platform>.png 只落图不写回（§二 F31——
      平台封面由 pack/check --platform 探测优先级发现，不顶替 mp 指针）；
      zhihu 无强制封面 → ValueError（cover_size 抛出）；
    - write_back：仅对 mp/默认生效——True 时当前版先入版本池（.md.bak.<时间戳>）
      再写回 frontmatter cover；幂等可重跑；非 mp 平台恒不写回（write_back
      对其是空操作）。
    """
    article_file = Path(article_path)
    if route == "from-design":
        # F71 桥（§八-3 已批）：设计稿 cover.svg → 规格 PNG → 写回（仅 mp），
        # 编排归 visual_service（需 infra 光栅件 + C 路兜底，domain 层不承担）。
        from . import visual_service  # 函数体内导入：避免模块级循环

        return visual_service.generate_cover_from_design(
            article_file, platform=platform, theme=theme, write_back=write_back
        )

    img = cover.generate_cover_image(
        article_file, route=route, platform=platform, image=image, theme=theme
    )

    out_path = article_file.parent / cover_filename(platform)
    img.save(out_path, format="PNG")  # 幂等产物：重生成直接覆盖（§四 Q3）

    if write_back and platform in (None, "mp"):
        _write_back_cover(article_file, out_path.name)
    return out_path


def platform_choices() -> tuple[str, ...]:
    """--platform 可选平台：PLIMITS 中配置了封面规格的（mp/xhs/channels/douyin；
    zhihu 无强制封面不派生，§一 表）。"""
    return tuple(
        code
        for code, limits in PLIMITS.items()
        if isinstance(limits.get("cover_w"), int) and isinstance(limits.get("cover_h"), int)
    )


def _write_back_cover(article_file: Path, cover_name: str) -> None:
    """frontmatter cover 写回：当前版入滚动版本池 + 原子整文件重写。

    版本池语义与 Studio 保存一致（F43 三处写回共用
    infra/storage/filesystem.backup_article：<name>.md.bak.<时间戳> 滚动保留
    N 份）；重写用 mkstemp+os.replace 原子落盘（与 annotations 同款纪律），
    frontmatter 值为文章目录内相对文件名——check/pack 解析 cover 的既有
    口径即相对文章目录。
    """
    backup_article(article_file)
    post = frontmatter.load(article_file)
    post.metadata["cover"] = cover_name
    text = frontmatter.dumps(post, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(
        dir=str(article_file.parent), prefix=f".{article_file.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(article_file.stat().st_mode))
        os.replace(tmp, article_file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = [
    "DEFAULT_COVER_NAME",
    "cover_filename",
    "generate_cover",
    "platform_choices",
]
