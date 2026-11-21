"""BookSource 规范模型（R0，L2 纯）：字段表 / 归一 / 索引投影 / 旧格式兼容。

**键名不可变（生态铁律）**：legado 社区书源按键名发布，本模块从不改名、
从不丢弃未知键——归一只做「缺键补默认 + 类型宽容」，且**规则组内部不补键**
（补键会改写原样 JSON，破坏 round-trip 逐键相等）。

字段表逐条对照前置分析 §2.1（`data/entities/BookSource.kt` 实读）与 §2.2
（`data/entities/rule/*.kt`）。

旧格式兼容（§2.2 末段）：规则组字段值可能是**再包一层的 JSON 字符串**
（`"ruleSearch": "{\"bookList\":\".x\"}"`），legado 用 INITIAL_GSON 的
jsonDeserializer 做一层解包（`json.isJsonPrimitive → fromJson(json.asString)`）——
`unwrap_rule_group` 同构实现一层解包（不递归：与 legado 完全一致）。
"""
from __future__ import annotations

import json
from typing import Any

# ── 顶层字段全表（§2.1 逐行；顺序 = Kotlin data class 声明顺）──────────

BOOKSOURCE_KEYS: tuple[str, ...] = (
    "bookSourceUrl",      # PK，站点地址（equals/hashCode 仅按此键）
    "bookSourceName",
    "bookSourceGroup",    # 逗号/分号分隔多值
    "bookSourceType",     # 0 文本 / 1 音频 / 2 图片 / 3 文件下载 / 4 视频
    "bookUrlPattern",
    "customOrder",
    "enabled",
    "enabledExplore",
    "jsLib",
    "enabledCookieJar",
    "concurrentRate",
    "header",
    "loginUrl",
    "loginUi",
    "loginCheckJs",
    "coverDecodeJs",
    "bookSourceComment",
    "variableComment",
    "lastUpdateTime",     # 导入三态合并的核心比较字段
    "respondTime",        # 默认 180000
    "weight",
    "exploreUrl",
    "exploreScreen",
    "ruleExplore",
    "searchUrl",
    "ruleSearch",
    "ruleBookInfo",
    "ruleToc",
    "ruleContent",
    "ruleReview",
    "mainJs",             # 非空 = 纯 JS 单文件源（R0 不支持执行）
    "eventListener",
    "customButton",
)

# 规则组五对象字段表（§2.2；ruleReview 单列——R0 只透传不解析）
RULE_GROUP_KEYS: dict[str, tuple[str, ...]] = {
    "ruleSearch": (
        "checkKeyWord", "bookList", "name", "author", "intro", "kind",
        "lastChapter", "updateTime", "bookUrl", "coverUrl", "wordCount",
    ),
    "ruleExplore": (
        "bookList", "name", "author", "intro", "kind",
        "lastChapter", "updateTime", "bookUrl", "coverUrl", "wordCount",
    ),
    "ruleBookInfo": (
        "init", "name", "author", "intro", "kind", "lastChapter", "updateTime",
        "coverUrl", "tocUrl", "wordCount", "canReName", "downloadUrls",
    ),
    "ruleToc": (
        "preUpdateJs", "chapterList", "chapterName", "chapterUrl", "formatJs",
        "isVolume", "isVip", "isPay", "updateTime", "nextTocUrl",
    ),
    "ruleContent": (
        "content", "subContent", "title", "nextContentUrl", "webJs",
        "sourceRegex", "replaceRegex", "imageStyle", "imageDecode",
        "payAction", "callBackJs", "contentBatch", "maxBatchSize",
    ),
}

# 顶层缺省值（Kotlin 声明缺省逐条；None 表示「可空且无默认」）
_DEFAULTS: dict[str, Any] = {
    "bookSourceUrl": "",
    "bookSourceName": "",
    "bookSourceGroup": None,
    "bookSourceType": 0,
    "bookUrlPattern": None,
    "customOrder": 0,
    "enabled": True,
    "enabledExplore": True,
    "jsLib": None,
    "enabledCookieJar": True,
    "concurrentRate": None,
    "header": None,
    "loginUrl": None,
    "loginUi": None,
    "loginCheckJs": None,
    "coverDecodeJs": None,
    "bookSourceComment": None,
    "variableComment": None,
    "lastUpdateTime": 0,
    "respondTime": 180000,
    "weight": 0,
    "exploreUrl": None,
    "exploreScreen": None,
    "ruleExplore": None,
    "searchUrl": None,
    "ruleSearch": None,
    "ruleBookInfo": None,
    "ruleToc": None,
    "ruleContent": None,
    "ruleReview": None,
    "mainJs": None,
    "eventListener": False,
    "customButton": False,
}

# 分组分隔符（legado AppPattern.splitGroupRegex = [,;，；]）
GROUP_SPLIT_CHARS = ",;，；"

# 索引投影键（§3.2 持有者定义；读侧容忍 name 别名——见 normalize_index_row）
INDEX_KEYS: tuple[str, ...] = (
    "bookSourceUrl", "bookSourceName", "group", "enabled", "sourceType",
    "lastUpdateTime", "respondTime", "weight", "imported_at",
)


# ── 归一（缺键补默认 + 类型宽容；规则组只解包不补键）───────────────────


def unwrap_rule_group(value: Any) -> Any:
    """旧格式兼容：规则组值是「JSON 字符串再包一层」时解包成对象。

    与 legado INITIAL_GSON 的 jsonDeserializer 同构——只解一层
    （`json.isJsonPrimitive → fromJson(json.asString)`）；解出来不是对象
    （坏 JSON / 解出标量）则原值返回，交给读侧宽容处理，绝不抛。
    """
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return value
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return value
    return parsed if isinstance(parsed, dict) else value


def _coerce_bool(value: Any, default: bool) -> bool:
    """布尔宽容：真布尔原样；"true"/1 等常见形态归一；其余回默认。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return default


def _coerce_int(value: Any, default: int) -> int:
    """整数宽容（GSON 会把 "123" 读成 123；脏值回默认）。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _coerce_str(value: Any, default: str | None) -> str | None:
    """字符串宽容：非 str 的标量按 JSON 形态转串（社区源偶有数字名）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return json.dumps(value, ensure_ascii=False)
    return default


def normalize(raw: Any) -> dict[str, Any] | None:
    """规范 JSON → 归一 dict（缺键补默认；未知键原样保留）。

    非 dict / bookSourceUrl 缺失或空 → None（调用方丢弃）。类型宽容只覆盖
    顶层标量键；规则组仅做旧格式解包，**不补子键、不改键名**。
    """
    if not isinstance(raw, dict):
        return None
    url = _coerce_str(raw.get("bookSourceUrl"), "")
    if not url or not url.strip():
        return None
    out = dict(raw)  # 未知键先原样带入
    out["bookSourceUrl"] = url.strip()
    for key in BOOKSOURCE_KEYS:
        if key == "bookSourceUrl":
            continue
        value = raw.get(key, _DEFAULTS[key])
        default = _DEFAULTS[key]
        if key in RULE_GROUP_KEYS or key == "ruleReview":
            out[key] = unwrap_rule_group(value)
        elif isinstance(default, bool):
            out[key] = _coerce_bool(value, default)
        elif isinstance(default, int):
            out[key] = _coerce_int(value, default)
        else:  # str 或 None
            out[key] = _coerce_str(value, default)
    return out


def require_url(source: Any) -> str:
    """校验 bookSourceUrl 必填非空（导入准入；不合规 raise ValueError）。"""
    if not isinstance(source, dict):
        raise ValueError("不是书源（元素不是 JSON 对象）")
    url = source.get("bookSourceUrl")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("不是书源（bookSourceUrl 缺失或为空）")
    return url.strip()


# ── 索引投影（轻量行：列表页不载规则大字段）──────────────────────────


def index_row(source: dict[str, Any], *, imported_at: str = "") -> dict[str, Any]:
    """规范 JSON → 索引轻量行（§3.2 index.json 行形）。

    imported_at 由调用方注入（本层不取时钟）。
    """
    return {
        "bookSourceUrl": source.get("bookSourceUrl") or "",
        "bookSourceName": source.get("bookSourceName") or "",
        "group": source.get("bookSourceGroup") or "",
        "enabled": bool(source.get("enabled", True)),
        "sourceType": _coerce_int(source.get("bookSourceType"), 0),
        "lastUpdateTime": _coerce_int(source.get("lastUpdateTime"), 0),
        "respondTime": _coerce_int(source.get("respondTime"), 180000),
        "weight": _coerce_int(source.get("weight"), 0),
        "imported_at": imported_at,
    }


def normalize_index_row(raw: Any) -> dict[str, Any] | None:
    """索引行宽容读：非 dict / url 空 → None；`name` 别名回填 bookSourceName。

    索引文件是人工可编辑的派生缓存，坏行一律丢弃而不是崩读侧。
    """
    if not isinstance(raw, dict):
        return None
    url = raw.get("bookSourceUrl")
    if not isinstance(url, str) or not url.strip():
        return None
    out = dict(raw)
    out["bookSourceUrl"] = url.strip()
    name = raw.get("bookSourceName")
    if not isinstance(name, str) or not name:
        alias = raw.get("name")
        name = alias if isinstance(alias, str) else ""
    out["bookSourceName"] = name
    out["group"] = raw.get("group") if isinstance(raw.get("group"), str) else ""
    out["enabled"] = bool(raw.get("enabled", True))
    for key, default in (("sourceType", 0), ("lastUpdateTime", 0),
                         ("respondTime", 180000), ("weight", 0)):
        out[key] = _coerce_int(raw.get(key), default)
    out["imported_at"] = raw.get("imported_at") if isinstance(raw.get("imported_at"), str) else ""
    return out


# ── 分组判定（legado splitGroupRegex = [,;，；]）────────────────────


def group_names(source: dict[str, Any]) -> list[str]:
    """分组多值拆分（去空白去空项）。"""
    raw = source.get("bookSourceGroup")
    if not isinstance(raw, str):
        return []
    parts = [raw]
    for ch in GROUP_SPLIT_CHARS:
        parts = [piece for part in parts for piece in part.split(ch)]
    return [g.strip() for g in parts if g.strip()]
