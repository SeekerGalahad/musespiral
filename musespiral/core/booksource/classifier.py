"""legado 配置包类型识别（R0.5 F-X1，L2 纯）——§二 F-X1 ① 六类判定。

aoaostar 站 `sources/` 目录混放 legado 六类配置（README 原文：书源/订阅源/
净化规则/阅读排版/主题/在线朗读引擎），§一 实弹扫描 19 包 9060 源里
**309 条缺 url 的包全是非书源类型**——这不是「脏数据」，是「类型未识别」。

六类（kind）与特征键（按 §一 实测 19 包 + legado 实体字段表）：

    bookSource  任一条目含 `bookSourceUrl`（BookSource.kt PK；脏条目跳过
                交由 importer ② 逐条判定，不 here 拦）
    theme       `themeName` / `accentColor`（Theme.kt：39c60133 等主题包实测）
    tts         `contentType` 值含 audio / `id`+`url`+`name` 三键齐（HttpTTS
                实体形态，19db2f5e/856adf0e 等朗读包实测；pit/vol/spd 是
                朗读参数，出现在 url/name 文本里）
    replace     `replaceSummary` / `replaceRule` / `pattern`+`replacement`
                （ReplaceRule 净化规则，3f067eb2 实测后两键）
    layout      `normalRule` / `bodyIndent` / `ruleArticles` / `singleUrl`
                （RssSource 阅读排版，9955b83c/c5791307 实测后两键）
    unknown     以上全不中——`features` 列出识别到的键特征供可读提示

判定顺序即优先级（先 bookSource 后其他——非书源包不会带 bookSourceUrl 键；
theme/tts/replace/layout 互斥，实测键集合无交集）。
"""
from __future__ import annotations

from typing import Any, NamedTuple

# 六类 kind 枚举（§二 F-X1 ①；顺序即判定优先级）
KIND_BOOKSOURCE = "bookSource"
KIND_THEME = "theme"
KIND_TTS = "tts"
KIND_REPLACE = "replace"
KIND_LAYOUT = "layout"
KIND_UNKNOWN = "unknown"
KINDS: tuple[str, ...] = (
    KIND_BOOKSOURCE, KIND_THEME, KIND_TTS, KIND_REPLACE, KIND_LAYOUT, KIND_UNKNOWN,
)


class Classification(NamedTuple):
    """classify_payload 结果：kind 六类之一 + 识别到的键特征（unknown 提示用）。"""

    kind: str
    features: tuple[str, ...]


def classify_payload(data: Any) -> Classification:
    """解析后的 JSON（数组/对象）→ 六类 Classification。

    数组逐元素聚合键特征（单对象即单元素）；条目非 dict 不产生特征。
    """
    items = data if isinstance(data, list) else [data]
    keys: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            keys.update(item.keys())
    # 特征值检查用条目引用
    entries = [item for item in items if isinstance(item, dict)]

    if isinstance(data, list) and not data:
        # 空数组 = 零条书源包（0 有效 0 跳过），不给「无法识别」
        return Classification(KIND_BOOKSOURCE, ())
    if "bookSourceUrl" in keys:
        return Classification(KIND_BOOKSOURCE, ("bookSourceUrl",))
    if "themeName" in keys or "accentColor" in keys:
        feats = sorted(k for k in ("themeName", "accentColor", "primaryColor", "backgroundColor") if k in keys)
        return Classification(KIND_THEME, tuple(feats))
    if _looks_tts(entries, keys):
        return Classification(KIND_TTS, ("contentType", "id", "url", "name"))
    if "replaceSummary" in keys or "replaceRule" in keys or {"pattern", "replacement"} <= keys:
        feats = tuple(sorted(k for k in keys if k in ("replaceSummary", "replaceRule", "pattern", "replacement")))
        return Classification(KIND_REPLACE, feats)
    if "normalRule" in keys or "bodyIndent" in keys or {"ruleArticles", "ruleTitle"} <= keys or "singleUrl" in keys:
        feats = tuple(sorted(k for k in keys if k in ("normalRule", "bodyIndent", "ruleArticles", "singleUrl")))
        return Classification(KIND_LAYOUT, feats)
    return Classification(KIND_UNKNOWN, tuple(sorted(keys)[:12]))


def _looks_tts(entries: list[dict[str, Any]], keys: set[str]) -> bool:
    """TTS 特征：contentType 值含 audio，或 HttpTTS 实体形态（id+url+name 三键齐）。"""
    for item in entries:
        ct = item.get("contentType")
        if isinstance(ct, str) and "audio" in ct.lower():
            return True
    return {"id", "url", "name"} <= keys


def typed_message(kind: str, features: tuple[str, ...]) -> str:
    """非书源包的可读边界说明（§二 F-X1 用户可见段——给理解不给冷漠拒绝）。"""
    names = {
        KIND_THEME: "主题配置包（legado 主题）",
        KIND_TTS: "在线朗读引擎/TTS 配置包",
        KIND_REPLACE: "净化规则包（替换规则）",
        KIND_LAYOUT: "阅读排版/订阅源配置包",
    }
    label = names.get(kind, "无法识别的 legado 配置包")
    hint = "" if kind in names else f"（识别到键特征: {', '.join(features) or '无'}）"
    return f"这是{label}{hint}，musespiral 目前只支持书源；可在「阅读」APP 导入"
