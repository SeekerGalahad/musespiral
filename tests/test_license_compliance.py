"""许可证合规门：禁止 AGPL/GPL 依赖（商业开源红线）。

**为什么需要这个机器门（2026-11-21 判例）**：
`ebooklib` 是 AGPL-3.0-or-later，其第 13 条网络条款要求「任何以网络提供服务
的衍生作品必须开源全部源码」——这会直接堵死 musespiral 的商业开源路径（SaaS 化 /
闭源商业版 / 私有部署销售三种变现全被禁止）。它一度混进依赖树而无人察觉，
直到用户主动询问「是否有影响商业开源的事情」才被查出来。

**人工审查靠不住，必须机器拦**：任何人（包括 AI 与未来的贡献者）加依赖时，
都不会主动去读 LICENSE。这个测试用元数据扫描兜底。

判定口径：
  - **禁止**：AGPL-3.0 / GPL-2.0 / GPL-3.0（强传染）
  - **允许**：MIT / Apache-2.0 / BSD / ISC / MPL-2.0 / **LGPL**（弱传染，
    动态链接下不传染——Python import 被广泛认为等同动态链接，
    Qt/FFmpeg 都是 LGPL 且被大量商业产品使用）
  - **允许**：无 license 元数据的包（保守起见只警告不失败——很多包只是
    没把 license 写进 metadata，实际是 MIT）

**豁免机制**：`ALLOWLIST` 里的包名跳过检查。仅用于「已确认合规但元数据缺失」
的包；**不允许**用它放行真正的 copyleft 依赖。
"""
from __future__ import annotations

import importlib.metadata as md

import pytest

# ── 判定配置 ──────────────────────────────────────────────────────

# 强传染许可证（禁止）——出现在包名/classifier/license 字段即失败
_FORBIDDEN_MARKERS: tuple[str, ...] = (
    "AGPL",
    "GNU AFFERO",
    "GPL-2.0", "GPL-3.0", "GPLV2", "GPLV3",
    "GNU GENERAL PUBLIC LICENSE",
)

# 弱传染（允许）：LGPL 必须先于 GPL 检查排除，否则 "LGPL" 会命中 "GPL"
_ALLOWED_WEAK: tuple[str, ...] = ("LGPL", "LESSER GENERAL PUBLIC")

# 元数据缺失但已人工确认合规的包（谨慎使用，且需注明理由）
_ALLOWLIST: dict[str, str] = {}


def _license_text(dist: md.Distribution) -> str:
    """把某 distribution 的全部许可证线索拼成一个大写字符串供匹配。"""
    parts: list[str] = []
    lic = dist.metadata.get("License")
    if lic:
        parts.append(str(lic))
    for c in dist.metadata.get_all("Classifier") or []:
        parts.append(str(c))
    # 有些包用 License-Expression（PEP 639）
    expr = dist.metadata.get("License-Expression")
    if expr:
        parts.append(str(expr))
    return " ".join(parts).upper()


def _verdict(text: str) -> str:
    """→ 'forbidden' | 'allowed-weak' | 'clean'。"""
    if any(m in text for m in _ALLOWED_WEAK):
        return "allowed-weak"  # LGPL：见效但不传染
    if any(m in text for m in _FORBIDDEN_MARKERS):
        return "forbidden"
    return "clean"


def test_no_copyleft_dependencies() -> None:
    """**商业开源红线**：依赖树里不得出现 AGPL/GPL（LGPL 允许）。

    2026-11-21 判例：ebooklib（AGPL-3.0）曾混入依赖树，阻塞商业开源。
    修复方式=EPUB 改纯标准库解析（zipfile + ElementTree），彻底移除该依赖。
    """
    violations: list[str] = []
    for dist in md.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name in _ALLOWLIST:
            continue
        text = _license_text(dist)
        if _verdict(text) == "forbidden":
            violations.append(f"{name} {dist.version}：{text[:90]}")

    assert not violations, (
        "发现 copyleft 依赖（AGPL/GPL 会传染，禁止用于商业开源项目）：\n  "
        + "\n  ".join(violations)
        + "\n\n处置：① 换宽松许可的替代实现；② 若确需该能力，自己实现"
        + "（如 ebooklib → 纯标准库解析 EPUB）；③ 不要用 ALLOWLIST 放行——"
        + "那只是把风险藏起来。"
    )


def test_ebooklib_specifically_absent() -> None:
    """具名回归锚：ebooklib 必须不在依赖树里（它曾是唯一的 AGPL 污染源）。"""
    names = {(d.metadata.get("Name") or "").strip().lower() for d in md.distributions()}
    assert "ebooklib" not in names, (
        "ebooklib（AGPL-3.0）又在依赖树里了。EPUB 提取已改为纯标准库实现"
        "（musespiral/plugins_builtin/doc-extract/plugin.py::_extract_epub），"
        "不需要这个依赖——请检查是谁引入的。"
    )


def test_lgpl_is_not_flagged() -> None:
    """反向验证：LGPL 不应被判 forbidden（否则会误伤 edge-tts 等合规依赖）。"""
    assert _verdict("GNU LESSER GENERAL PUBLIC LICENSE VERSION 2.1") == "allowed-weak"
    assert _verdict("License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)") == "allowed-weak"


def test_agpl_is_flagged() -> None:
    """正向验证：AGPL 必须被判 forbidden。"""
    assert _verdict("GNU AFFERO GENERAL PUBLIC LICENSE VERSION 3") == "forbidden"
    assert _verdict("License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)") == "forbidden"


def test_permissive_is_clean() -> None:
    """宽松许可证不应被判 forbidden。"""
    for text in ("MIT LICENSE", "APACHE SOFTWARE LICENSE", "BSD-3-CLAUSE", "ISC"):
        assert _verdict(text) == "clean", text
