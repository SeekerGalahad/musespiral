"""章节规则 profile（L2 纯，零 I/O）——内置三档 + 自定义规则解析。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.3「文本处理插件（开源亮点）」：规则是 **JSON 而非代码**——新增规则=加一个
JSON，不改代码；`~/.musespiral/localbook-rules/*.json` 由用户/社区贡献。

内置三档（同节表格）：
  - `cn-novel-standard`：中文网文（第X章/节/回…，min_chapters=3）；
  - `cn-classic`：古典回目（第X回 + 对仗回目，min_chapters=3）；
  - `plain-split`：无结构 → 定长切块（chapter_regex 为空，min_chapters 无意义）。

**本模块只解析 JSON 文本，不读目录**（L2 零 I/O 纪律）：目录遍历与坏文件计数
落在 `services/localbook_service`——那里拿得到 home_dir 与错误计数语义，
而 core 保持可纯单测（`load_rules_from_texts({name: text})` 直接喂字符串）。

规则 schema（设计 §1.3 样例 + 本实现的显式键）::

    {"id":"cn-novel-standard","name":"中文小说标准",
     "encoding":["UTF-8","GB18030"],
     "chapter_regex":"^\\\\s*(第[...]+[章节回卷集部篇].{0,40})$",
     "toc_clean":"strip_index,dedupe","min_chapters":3,
     "mode":"regex"|"split","chunk_chars":5000}

`encoding` 与 `toc_clean` 是**声明式元信息**（给 UI 展示与将来扩展），本期
不参与切分决策——编码由 `charset.detect_bytes` 实测决定、目录清洗恒为
strip+dedupe（见 `toc.clean_toc`）。这一点必须写清：声明与行为的差距不藏着。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

# 规则 id 白名单指纹：kebab-case（与源 id 同纪律——要能进文件路径与 URL 参数）
_RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

DEFAULT_RULE_ID = "cn-novel-standard"

# 内置三档（dict 顺序=UI 展示顺序；id 即 profile 名）
BUILTIN_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "cn-novel-standard",
        "name": "中文小说标准",
        "builtin": True,
        "encoding": ["UTF-8", "GB18030"],
        # 同 chapters.CN_PATTERN 的逐字形态（内置与自定义走同一条编译/匹配路径，
        # 不做两套实现——否则「换规则」实测不出差异）
        "chapter_regex": r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*[章节回卷集部篇](\s*.{0,40})?$",
        "toc_clean": "strip_index,dedupe",
        "min_chapters": 3,
        "mode": "regex",
    },
    {
        "id": "cn-classic",
        "name": "古典回目",
        "builtin": True,
        "encoding": ["UTF-8", "GB18030", "Big5"],
        # 回目对仗：第X回 后必须跟空白 + 标题（散文句不会有这个形态）
        "chapter_regex": r"^第\s*[0-9一二三四五六七八九十百千万两零〇]+\s*[回卷][ 　\t]+\S.{0,40}$",
        "toc_clean": "strip_index,dedupe",
        "min_chapters": 3,
        "mode": "regex",
    },
    {
        "id": "plain-split",
        "name": "无结构 · 定长切块",
        "builtin": True,
        "encoding": [],
        # 空正则=不做章节识别，直接走 split_fixed（+ 上游如实标注未识别）
        "chapter_regex": "",
        "toc_clean": "strip_index,dedupe",
        "min_chapters": 0,
        "mode": "split",
        "chunk_chars": 5000,
    },
)


class RuleError(ValueError):
    """规则 JSON 不可用（缺必填键/类型不对/正则编译失败）——坏规则跳过并计数。"""


def _clean_encoding(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise RuleError("encoding 必须是字符串或字符串数组")
    out = [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]
    return out


def parse_rule(raw: Any) -> dict[str, Any]:
    """单条规则归一 + 校验 → 规范 dict（id/name/chapter_regex/min_chapters/mode…）。

    校验口径（错即 RuleError，由调用方决定跳过还是抛）：
      - `id` 必填且 kebab-case（要进 URL 参数与文件名）；
      - `chapter_regex` 必须在 `mode=regex` 下**编译得通**（编译失败的规则等于
        坏 JSON，早失败胜于运行期每本书都炸）；
      - `min_chapters` 缺省 3、`chunk_chars` 缺省 5000（split 模式用）——非法
        数值回落缺省而不是报错（数值不是安全面，宽容读）。
    """
    if not isinstance(raw, dict):
        raise RuleError("规则必须是 JSON 对象")
    rid = raw.get("id")
    if not isinstance(rid, str) or not _RULE_ID_RE.match(rid.strip()):
        raise RuleError(f"规则 id 非法（须 kebab-case）: {rid!r}")
    rid = rid.strip()
    pattern = raw.get("chapter_regex") or ""
    if not isinstance(pattern, str):
        raise RuleError(f"chapter_regex 必须是字符串: {pattern!r}")
    mode = raw.get("mode") or ("split" if not pattern.strip() else "regex")
    if mode not in ("regex", "split"):
        raise RuleError(f"mode 必须是 regex 或 split: {mode!r}")
    if mode == "regex" and pattern.strip():
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise RuleError(f"chapter_regex 编译失败: {exc}") from None
    min_chapters = raw.get("min_chapters", 3)
    if isinstance(min_chapters, bool) or not isinstance(min_chapters, int):
        min_chapters = 3
    chunk_chars = raw.get("chunk_chars", 5000)
    if isinstance(chunk_chars, bool) or not isinstance(chunk_chars, int) or chunk_chars < 200:
        chunk_chars = 5000
    name = raw.get("name")
    return {
        "id": rid,
        "name": str(name).strip() if isinstance(name, str) and name.strip() else rid,
        "builtin": bool(raw.get("builtin", False)),
        "encoding": _clean_encoding(raw.get("encoding")),
        "chapter_regex": pattern,
        "toc_clean": str(raw.get("toc_clean") or "strip_index,dedupe"),
        "min_chapters": max(0, min_chapters),
        "mode": mode,
        "chunk_chars": chunk_chars,
    }


def load_rules_from_texts(texts: Iterable[tuple[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """`[(来源名, JSON 文本)]` → `(合法规则表, 坏条目数)`。

    **坏 JSON 跳过并计数**（设计 §1.3「坏 JSON 跳过并计数」）而不是整体失败：
    一个社区规则写错不该让整个书架不可用；返回的计数由服务层写进 meta
    （`rules_bad`），前端可如实显示「有 N 条规则读不了」。
    """
    ok: list[dict[str, Any]] = []
    bad = 0
    for _source, text in texts:
        try:
            parsed = parse_rule(json.loads(text))
        except (json.JSONDecodeError, RuleError, TypeError, ValueError):
            bad += 1
            continue
        ok.append(parsed)
    return ok, bad


def all_rules(custom: Iterable[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """内置 + 自定义合并（**自定义可覆盖内置同 id**——用户改规则的本意）。

    顺序：内置三档恒在前（顺序固定），自定义追加在后（同 id 的覆盖原位）
    ——UI 里内置永远是前三张卡，不因用户加了规则而漂移。
    """
    by_id: dict[str, dict[str, Any]] = {r["id"]: dict(r) for r in BUILTIN_RULES}
    order: list[str] = [r["id"] for r in BUILTIN_RULES]
    for rule in custom:
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = dict(rule)
    return [by_id[rid] for rid in order]


def get_rule(rule_id: str | None, custom: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """按 id 取规则；缺省/未知 id → 内置 `cn-novel-standard`（永不抛——读路径）。"""
    rules = all_rules(custom)
    wanted = (rule_id or DEFAULT_RULE_ID).strip() or DEFAULT_RULE_ID
    for rule in rules:
        if rule.get("id") == wanted:
            return rule
    return dict(BUILTIN_RULES[0])


__all__ = [
    "BUILTIN_RULES",
    "DEFAULT_RULE_ID",
    "RuleError",
    "all_rules",
    "get_rule",
    "load_rules_from_texts",
    "parse_rule",
]
