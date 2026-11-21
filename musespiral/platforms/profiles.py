"""平台能力档案 —— 各平台硬性限制常量表（§十四 F12）。

吸收 chinese-social-mcp 的限制映射思路；供适配器 validate 与 pack
checks 消费。字段口径约定:
  - *_bytes:   UTF-8 字节数（微信公众号 API 的限制口径）
  - *_chars:   字符数（中文内容平台普遍口径）
  - cover_w/cover_h: 封面像素规格（第十三期 §一 封面规格表——F31 派生与
    F33 预检的唯一事实源，不得在别处另写一份；zhihu 无强制封面不设键）
  - 时长一律 *_seconds / *_minutes，不混用单位

mp 的既有口径与 config 常量同源（TITLE_MAX_BYTES/DIGEST_MAX_BYTES），
避免两处限制值漂移。
"""
from __future__ import annotations

from .. import config

PLIMITS: dict[str, dict] = {
    # 微信公众号: 标题 64 字节 / 摘要 120 字节 / 封面 900×383（2.35:1 官方推荐）
    "mp": {
        "title_max_bytes": config.TITLE_MAX_BYTES,
        "digest_max_bytes": config.DIGEST_MAX_BYTES,
        "cover_ratio": 2.35,
        "cover_ratio_tolerance": 0.25,  # 允许 ±25% 偏差（与 render_checks 同口径）
        "cover_w": 900,  # §一 封面规格表（第十三期 F31/F33B 唯一事实源）
        "cover_h": 383,
    },
    # 小红书: 标题 ≤20 字 / 正文 ≤1000 字 / 图片 1-18 张 / 话题 ≤10 个 / 封面 3:4
    "xhs": {
        "title_max_chars": 20,
        "content_max_chars": 1000,
        "images_min": 1,
        "images_max": 18,
        "tags_max": 10,
        "cover_w": 1242,
        "cover_h": 1656,
    },
    # 视频号: 标题 3-16 字 / 描述 ≤1000 字 / 时长 1-60 分钟 / 封面 1:1
    # （channels 封面规格为公开规范通用值，真机批次④后如有出入以实测回写 §一 表）
    "channels": {
        "title_min_chars": 3,
        "title_max_chars": 16,
        "desc_max_chars": 1000,
        "duration_min_minutes": 1,
        "duration_max_minutes": 60,
        "cover_w": 1080,
        "cover_h": 1080,
    },
    # 抖音: 标题 ≤55 字 / 描述 ≤1000 字 / 时长 15s-15min / 封面 3:4
    # （douyin 封面规格为公开规范，真机批次④后如有出入以实测回写 §一 表）
    "douyin": {
        "title_max_chars": 55,
        "desc_max_chars": 1000,
        "duration_min_seconds": 15,
        "duration_max_seconds": 15 * 60,
        "cover_w": 1080,
        "cover_h": 1440,
    },
    # 知乎: 标题 ≤100 字，无图数上限，无强制封面（不派生，§一 表）
    "zhihu": {
        "title_max_chars": 100,
        "images_max": None,
    },
    # 掘金: 标题无官方硬限，编辑器容纳 ~100 字（软限）；正文 <250 字不被
    # 推荐（quality 维不机器化，pack README 提示）；封面 16:9 建议 1600×900
    # （平台自动抓正文首图兜底）（F42 §二-2.1 表，公开核实 2026-11-14）
    "juejin": {
        "title_max_chars": 100,
        "cover_w": 1600,
        "cover_h": 900,
    },
    # B站专栏: 标题 ≤80 字（建议 20-30，建议值只在 pack README 提示）；
    # 封面 16:10 = 1600×1000、≤5MB（pack_checks 消费 cover_max_bytes 出
    # WARN）；APP 首页按 3:2 裁切（主体居中提示在 pack README）（F42 §二-2.1 表）
    "bilibili": {
        "title_max_chars": 80,
        "cover_w": 1600,
        "cover_h": 1000,
        "cover_max_bytes": 5 * 1024 * 1024,
    },
}


def get_limits(code: str) -> dict:
    """取平台限制表；未知平台把 KeyError 转成可读 ValueError。"""
    try:
        return PLIMITS[code]
    except KeyError:
        known = "/".join(sorted(PLIMITS))
        raise ValueError(f"未知平台: {code}（已知平台: {known}）") from None
