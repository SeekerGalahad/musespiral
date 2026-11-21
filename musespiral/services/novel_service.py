"""小说模式服务（E-5，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》
§3.6「I3 小说上下文管理」）——**规则装载 + 冲突检测**的唯一持有者。

分工（与 services/compose_service 的边界）：
  - 本模块：冲突规则表装载（``~/.musespiral/novel_rules.json`` 覆盖内置缺省）与
    生成文本 × 人物卡的冲突扫描（``scan_conflicts``）；纯读 + 纯函数，零写面；
  - compose_service：前情提要的 AI 生成/手改/落盘、prompt 注入、会话挂卡列表
    （它持有 session.json 与 ``_serialized`` 串行锁；本模块不碰会话文件）。

冲突检测的定位（正典 §3.6 原文「冲突检测是提示不是拦截」）：本模块只产出
``[{block, line, character, rule, snippet, message}]`` 提示列表，**不修改任何
生成文本**，调用方（compose_service.generate / expand_section）把它塞进返回
载荷与 job.result，前端以 ⚠️ 呈现。误报可接受（正典原文），漏报不可怕——
作者看到提示自己判断。

规则表形态（``~/.musespiral/novel_rules.json``，存在即**整体替换**内置表——同
prompts.d 覆盖级联纪律）：``[{"id","speech_contains","line_pattern","message"}…]``；
文件坏/键错/正则非法 → 抛 NovelServiceError（错拼早失败，不静默退化成
「永不触发」的假绿）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.composition import (
    DEFAULT_CONFLICT_RULES,
    Block,
    detect_conflicts,
    validate_rules,
)

# 规则覆盖文件（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移——测试隔离走环境变量）
RULES_FILE = "novel_rules.json"


class NovelServiceError(ValueError):
    """小说模式业务错误（可读消息；studio 转 400 / ok:false）。"""


def _home() -> Path:
    from .. import config

    return config.home_dir()


def rules_path() -> Path:
    """规则覆盖文件路径：``~/.musespiral/novel_rules.json``。"""
    return _home() / RULES_FILE


def load_rules() -> list[dict[str, str]]:
    """装载生效规则表：覆盖文件 > 内置缺省（``core.composition.novel``）。

    - 文件缺失 → 内置表（零配置可用）；
    - 文件存在但 JSON 坏 / 非数组 / 字段缺失 / 正则非法 → NovelServiceError，
      **不静默回退**（「规则写坏 → 永不触发」是最危险的假绿：作者以为在守，
      其实没守）；
    - 校验通过的行按内置同形归一（只取四键，多余键忽略——前向兼容）。
    """
    path = rules_path()
    if not path.is_file():
        return [dict(rule) for rule in DEFAULT_CONFLICT_RULES]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise NovelServiceError(f"冲突规则文件不可读: {path.name}（{exc}）") from None
    except json.JSONDecodeError as exc:
        raise NovelServiceError(f"冲突规则文件不是合法 JSON: {path.name}（{exc}）") from None
    errors = validate_rules(data)
    if errors:
        raise NovelServiceError("冲突规则文件不合法: " + "; ".join(errors))
    return [
        {
            "id": str(rule.get("id") or "").strip(),
            "speech_contains": str(rule.get("speech_contains") or "").strip(),
            "line_pattern": str(rule.get("line_pattern") or ""),
            "message": str(rule.get("message") or ""),
        }
        for rule in data
    ]


def scan_conflicts(
    blocks: Iterable[Block],
    characters: Sequence[dict[str, Any]],
    rules: Sequence[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """生成块 × 挂卡人物 → ⚠️ 提示列表（规则可配；本函数绝不改文本）。

    ``characters`` 是**已解析的人物卡 dict 列表**（不是素材 id）——id→卡的
    解析归 compose_service（它才知道会话挂了谁）。``rules`` 缺省走
    ``load_rules()``（覆盖文件或内置表）。
    """
    effective = list(rules) if rules is not None else load_rules()
    return detect_conflicts(blocks, characters, effective)
