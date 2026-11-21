"""小说模式领域逻辑（E-5，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》
§3.6「I3 小说上下文管理」）——L2 纯函数零 I/O。

三件事（与 services/compose_service 的分工：本层只定语义，文件/网络全在上层）：

  1. **章节切分**：把块序列按 heading 切章 → 「前 N-1 章」文本（前情提要的
     AI 输入面）。N=当前正在写的那一章不参与摘要（§3.6 示例「第1-14章」）。
  2. **人物卡 schema 校验**：{name, role, appearance, speech_style, key_events}
     五字段（name 必填等宽校验；key_events=事件行列表）。
  3. **说话风格冲突检测**（§3.6「冲突检测：AI 生成后自动检查」）：规则可配
     ——每条规则=「人物卡 speech_style 含触发词」×「对话行命中正则」→ ⚠️。
     本层只产提示不拦截（正典红线：冲突是提示不是闸门；误报可接受）。

设计口径（为什么这样切）：

  - **对话行**=含引号包裹片段的文本行（「…」『…』“…”"…"）。小说里的对话
    绝大多数带引号；不带引号的心理活动不参与（宁漏勿错）。
  - **说话人是启发式**：对话行里出现人物卡 name 即归属该人物（中文小说
    的「「…！」林远说」「林远：「…」」两种主形态都覆盖）。名字不在行内
    的对话不归属——无法确证说话人时不猜（误报成本高于漏报）。
  - **规则匹配是「触发词 × 行正则」的笛卡尔积**：speech_style 含触发词才
    激活该规则（「不用感叹号」这条只对写了这四个字的卡生效），行内命中
    正则才报。规则表可由服务层用 ~/.musespiral/novel_rules.json 整体替换。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .blocks import Block

# ── 人物卡 schema（§3.6：{name, role, appearance, speech_style, key_events}）──

CHARACTER_FIELDS: tuple[str, ...] = (
    "name", "role", "appearance", "speech_style", "key_events",
)
# 单字段字符上限（人物卡进 prompt 的摘要面；超长截断在渲染层做，本层只校验类型）
CHARACTER_TEXT_MAX = 400
CHARACTER_NAME_MAX = 40
CHARACTER_MAX_EVENTS = 30

# 素材 payload.meta.sub_type 的「人物卡」标识（materials API 复用的判别键）
SUB_TYPE_CHARACTER = "character"


def validate_character(payload: Any) -> list[str]:
    """校验人物卡载荷（返回错误清单，空=合法）。

    payload 形态：{"name": str 必填非空, "role"/"appearance"/"speech_style":
    str 可缺, "key_events": list[str] 可缺}。宽容面与 materials 读侧同纪律：
    缺键按空串/空表；给错类型才是错误（不静默吞错拼字段）。
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["人物卡必须是对象"]
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 必须是非空字符串（人物名）")
    elif len(name.strip()) > CHARACTER_NAME_MAX:
        errors.append(f"name 过长（{len(name.strip())} 字符，上限 {CHARACTER_NAME_MAX}）")
    for key in ("role", "appearance", "speech_style"):
        value = payload.get(key, "")
        if not isinstance(value, str):
            errors.append(f"{key} 必须是字符串")
        elif len(value) > CHARACTER_TEXT_MAX:
            errors.append(f"{key} 过长（{len(value)} 字符，上限 {CHARACTER_TEXT_MAX}）")
    events = payload.get("key_events", [])
    if isinstance(events, str):
        errors.append("key_events 必须是字符串数组（每项一行事件）")
    elif not isinstance(events, list):
        errors.append("key_events 必须是字符串数组")
    else:
        if len(events) > CHARACTER_MAX_EVENTS:
            errors.append(f"key_events 过多（{len(events)} 条，上限 {CHARACTER_MAX_EVENTS}）")
        if any(not isinstance(e, str) for e in events):
            errors.append("key_events 每项必须是字符串")
    return errors


def normalize_character(payload: dict[str, Any]) -> dict[str, Any]:
    """归一人物卡（去空白、缺键补默认、事件去空行）——校验通过后调用。"""
    events = [str(e).strip() for e in (payload.get("key_events") or [])]
    return {
        "name": str(payload.get("name") or "").strip(),
        "role": str(payload.get("role") or "").strip(),
        "appearance": str(payload.get("appearance") or "").strip(),
        "speech_style": str(payload.get("speech_style") or "").strip(),
        "key_events": [e for e in events if e][:CHARACTER_MAX_EVENTS],
    }


def character_from_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """素材 payload.meta → 人物卡视图（非人物卡返回空 name 的骨架）。

    素材侧字段落位：meta.name/role/appearance/speech_style/key_events（与
    schema 同名，零映射——读侧不必记第二套键名）。
    """
    meta = meta or {}
    events = meta.get("key_events")
    if not isinstance(events, list):
        events = []
    return {
        "name": str(meta.get("name") or ""),
        "role": str(meta.get("role") or ""),
        "appearance": str(meta.get("appearance") or ""),
        "speech_style": str(meta.get("speech_style") or ""),
        "key_events": [str(e) for e in events if isinstance(e, str) and str(e).strip()],
    }


def render_character(card: dict[str, Any]) -> str:
    """人物卡 → prompt 注入行（§3.6「生成时注入 prompt」的单卡形态）。

    形如：``- 林远（研究员）：外貌：瘦高…；说话风格：短句·不用感叹号；
    关键事件：第3章 初次出场 / 第7章 借钱不还``。空字段整段缺席（不留空标签）。
    """
    name = str(card.get("name") or "").strip()
    if not name:
        return ""
    role = str(card.get("role") or "").strip()
    head = f"- {name}" + (f"（{role}）" if role else "")
    parts: list[str] = []
    for label, key in (("外貌", "appearance"), ("说话风格", "speech_style")):
        value = str(card.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    events = [str(e).strip() for e in (card.get("key_events") or []) if str(e).strip()]
    if events:
        parts.append("关键事件：" + " / ".join(events))
    return head + ("：" + "；".join(parts) if parts else "")


# ── 章节切分（前情提要的输入面：前 N-1 章）─────────────────────────────


@dataclass(frozen=True)
class Chapter:
    """一章：index 从 1 起；title 取首个标题文本（无标题的前置块 title=""）。"""

    index: int
    title: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)


def split_chapters(blocks: Sequence[Block]) -> list[Chapter]:
    """块序列 → 章节列表（heading 块开启新章；首个 heading 之前的块归第 1 章）。

    零 heading → 整体一章（title="")；空序列 → 空表。
    """
    chapters: list[Chapter] = []
    current: list[Block] = []
    title = ""
    started = False
    for block in blocks:
        if block.kind == "heading":
            if current or started:
                chapters.append(Chapter(len(chapters) + 1, title, tuple(current)))
            current = [block]
            title = block.text.strip().lstrip("#").strip()
            started = True
        else:
            current.append(block)
    if current or not chapters:
        chapters.append(Chapter(len(chapters) + 1, title, tuple(current)))
    return [c for c in chapters if c.blocks]


def prior_chapters_text(
    blocks: Sequence[Block],
    max_chars: int = 12000,
) -> tuple[str, int, int]:
    """前情提要输入：除最后一章外的全文 → ``(markdown, 覆盖章数, 总章数)``。

    超预算取**尾部**（越近的章节对续写越相关；与滚动摘要同判别）。单章
    （或零块）时覆盖章数=0、文本=""（无可摘要内容，调用方报可读错误）。
    """
    chapters = split_chapters(blocks)
    total = len(chapters)
    if total < 2:
        return "", 0, total
    prior = chapters[:-1]
    text = "\n\n".join(
        b.text.strip() for c in prior for b in c.blocks if b.text.strip()
    )
    if max_chars > 0 and len(text) > max_chars:
        text = "…（前文过长已截去开头）\n" + text[-max_chars:]
    return text, len(prior), total


# ── 说话风格冲突检测（§3.6：规则可配、提示不拦截）─────────────────────

# 内置规则表（~/.musespiral/novel_rules.json 存在即整体替换；每条：
#   id 稳定标识 / speech_contains 触发词（人物卡 speech_style 含即激活）/
#   line_pattern 对话行正则 / message 提示文案（{name} 占位人物名））
DEFAULT_CONFLICT_RULES: tuple[dict[str, str], ...] = (
    {
        "id": "no_exclamation",
        "speech_contains": "不用感叹号",
        "line_pattern": r"[！!]",
        "message": "{name} 的对话含感叹号，与人物卡「不用感叹号」不符",
    },
    {
        "id": "no_exclamation_alt",
        # «无感叹号»/«禁用感叹号» 同义写法（小说作者手写卡片的常见变体）
        "speech_contains": "无感叹号",
        "line_pattern": r"[！!]",
        "message": "{name} 的对话含感叹号，与人物卡「无感叹号」不符",
    },
    {
        "id": "short_sentence",
        "speech_contains": "短句",
        "line_pattern": r"[。；][^。；！？!?\n]{40,}",
        "message": "{name} 的对话句子偏长，与人物卡「短句」风格不符",
    },
)

# 对话行判定：含成对引号片段（中文四种 + ASCII 双引号；单引号不认——英文
# 撇号与强调用法误报率过高）
_DIALOGUE_RE = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")


def is_dialogue_line(line: str) -> bool:
    """该行是否含引号包裹的对话片段。"""
    return bool(_DIALOGUE_RE.search(line or ""))


def validate_rules(rules: Any) -> list[str]:
    """校验规则表（配置文件的错拼早失败——规则写坏不静默变恒不触发）。"""
    errors: list[str] = []
    if not isinstance(rules, list):
        return ["规则必须是数组"]
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"规则[{i}] 必须是对象")
            continue
        for key in ("id", "speech_contains", "line_pattern", "message"):
            value = rule.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"规则[{i}].{key} 必须是非空字符串")
        pattern = rule.get("line_pattern")
        if isinstance(pattern, str) and pattern.strip():
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"规则[{i}].line_pattern 非法正则: {exc}")
    return errors


def detect_conflicts(
    blocks: Iterable[Block],
    characters: Iterable[dict[str, Any]],
    rules: Sequence[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """扫生成文本的对话行 × 挂卡人物的说话风格规则 → ⚠️ 提示列表。

    返回项：``{block, line, character, rule, snippet, message}``（line 为该块
    内的 1 基行号，snippet 为截断行文本）。**只报不改**——提示列表交给前端
    与 job result，生成文本零改写（冲突检测是提示不是拦截）。
    """
    active = [c for c in characters if isinstance(c, dict) and str(c.get("name") or "").strip()]
    if not active:
        return []
    # 每条规则按人物卡 speech_style 预筛（触发词不在卡里的规则对该人物休眠）
    per_character: list[tuple[dict[str, Any], list[tuple[dict[str, str], re.Pattern[str]]]]] = []
    for card in active:
        style = str(card.get("speech_style") or "")
        enabled: list[tuple[dict[str, str], re.Pattern[str]]] = []
        for rule in (rules if rules is not None else DEFAULT_CONFLICT_RULES):
            trigger = str(rule.get("speech_contains") or "")
            if trigger and trigger in style:
                enabled.append((rule, re.compile(str(rule.get("line_pattern") or "$^"))))
        if enabled:
            per_character.append((card, enabled))
    if not per_character:
        return []
    out: list[dict[str, Any]] = []
    for block in blocks:
        for lineno, line in enumerate((block.text or "").splitlines(), 1):
            if not is_dialogue_line(line):
                continue
            for card, enabled in per_character:
                name = str(card.get("name") or "").strip()
                if name not in line:
                    continue  # 说话人无法确证 → 不归属（误报成本高于漏报）
                for rule, pattern in enabled:
                    if not pattern.search(line):
                        continue
                    out.append({
                        "block": block.id,
                        "line": lineno,
                        "character": name,
                        "rule": str(rule.get("id") or ""),
                        "snippet": line.strip()[:80],
                        "message": str(rule.get("message") or "").replace("{name}", name),
                    })
    return out
