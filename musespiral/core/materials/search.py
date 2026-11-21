"""跨模态词面搜索（第二十一期 §二-F64）——L2 纯函数，零 I/O。

无索引现算（零索引零落盘，零依赖红线下的诚实选择）；语义检索列远期。

语法（正典 §二-F64，任何字符串都是合法查询——无语法错误态）：
  - 按空白切词，多词 **AND**；
  - `"引号短语"` 精确子串（大小写不敏感）；
  - `foo*` 前缀匹配（按可搜文本的空白分词，任一词元以前缀命中即中）；
  - 未闭合引号按字面处理（引号字符在分词时剥除，内容当普通词）；
  - 空 q 恒真（不过滤）。

命中域 = origin.title + payload.text（text/derived/crawled 正文）+
payload.url + triage.tags + payload.meta 拍平（`key:value` 形态——
`line:embedded`、`grade:S`、`platform:bilibili` 可直接搜中）。
"""
from __future__ import annotations

import re
from typing import Any

_QUOTED = re.compile(r'"([^"]*)"')

# 词元切分：非空白连续段（前缀匹配按词元判）
_TOKEN_SPLIT = re.compile(r"\s+")


def parse_query(q: str) -> list[tuple[str, str]]:
    """解析查询词 → [(kind, text)]，kind ∈ word|phrase|prefix（已小写）。

    引号内为短语（子串语义，不切词）；带尾 `*` 的词为前缀；其余为词
    （子串语义）。未闭合引号不匹配正则，落入尾部自由文本——剥除引号
    字符后按普通词处理（字面处理，无错误态）。
    """
    terms: list[tuple[str, str]] = []

    def _add_word(word: str) -> None:
        word = word.strip('"')
        if not word:
            return
        if word.endswith("*") and len(word) > 1:
            terms.append(("prefix", word[:-1].lower()))
        else:
            terms.append(("word", word.lower()))

    pos = 0
    for match in _QUOTED.finditer(q):
        for word in _TOKEN_SPLIT.split(q[pos : match.start()]):
            _add_word(word)
        phrase = match.group(1).strip()
        if phrase:
            terms.append(("phrase", phrase.lower()))
        pos = match.end()
    for word in _TOKEN_SPLIT.split(q[pos:]):
        _add_word(word)
    return terms


def _flatten_meta(prefix: str, value: Any, out: list[str]) -> None:
    """meta 域拍平为 `key:value` 文本片（标量直出；dict/list 递归一层路径）。"""
    if isinstance(value, dict):
        for k in sorted(value, key=str):
            _flatten_meta(f"{prefix}{k}:", value[k], out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_meta(prefix, item, out)
    elif value is None:
        return
    else:
        out.append(f"{prefix}{value}")


def searchable_text(material: dict[str, Any]) -> str:
    """一条素材的可搜文本（命中域拍平，空白连接）。"""
    origin = material.get("origin") or {}
    payload = material.get("payload") or {}
    triage = material.get("triage") or {}
    parts: list[str] = [
        str(origin.get("title") or ""),
        str(payload.get("text") or ""),
        str(payload.get("url") or ""),
        str(payload.get("local_path") or ""),
    ]
    tags = triage.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if isinstance(t, str))
    meta = payload.get("meta")
    if isinstance(meta, dict):
        flat: list[str] = []
        _flatten_meta("", meta, flat)
        parts.extend(flat)
    return " ".join(p for p in parts if p)


def matches_query(material: dict[str, Any], q: str) -> bool:
    """搜索谓词：全部词项 AND 命中才真；空 q 恒真。"""
    terms = parse_query(q or "")
    if not terms:
        return True
    text = searchable_text(material).lower()
    tokens = _TOKEN_SPLIT.split(text)
    for kind, needle in terms:
        if kind == "prefix":
            if not any(tok.startswith(needle) for tok in tokens):
                return False
        else:  # word / phrase 都是子串语义（大小写不敏感）
            if needle not in text:
                return False
    return True
