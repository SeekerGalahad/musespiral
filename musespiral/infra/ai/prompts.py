"""任务底座模板覆盖件（第十八期 F47 第三层面板：~/.musespiral/prompts.d/<task>.txt）。

覆盖级联约定（创作台设计 §2.3）：代码内置缺省 → 覆盖文件存在即整体覆盖；
插槽 {{style}}/{{sources}}/{{draft}}/{{decisions}} 由消费方（compose 编排，
Group B 落地）填充。本模块只管覆盖文件的键域/读写与方言守门，不组装提示词
（infra 不 import 上层；方言表放 config 顶层——表值本身是供应商名，不能进
方言扫描范围内的任何文件）。

方言守门扩面（F45→F47）：覆盖文件与源码组装点同受「prompt 零模型/供应商名」
约束——save_override 保存时校验拒写；tests/test_architecture.py 的守门测试
对 prompts.d 目录扫描（双保险：手工落盘的违规文件在 CI 拦截）。
"""
from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = "prompts.d"

# 任务底座模板可用插槽（创作台 §2.3；展示与编辑提示用，不做强校验——
# 模板是自由文本，缺槽走消费方缺省）。{{rolling}} 为 G-T4 滚动摘要槽
# （设计 §五；空摘要整节缺席，缺槽回补仍注入）；{{novel}}/{{characters}}
# 为 E-5 小说模式两槽（评审 §3.6：前情提要 + 人物卡，空则整节缺席）。
PROMPT_SLOTS: tuple[str, ...] = (
    "{{style}}", "{{sources}}", "{{novel}}", "{{characters}}", "{{rolling}}",
    "{{draft}}", "{{decisions}}",
)

# 任务键域：小写字母开头 + 小写字母/数字/下划线（与 profiles.TASKS 全集一致
# 的形态；防路径穿越——任务键直接拼进文件名）
_TASK_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def prompt_tasks() -> tuple[str, ...]:
    """可覆盖任务键 = profiles.TASKS（routing 白名单同一事实源）+ compose。

    compose（创作台 §2.3 chat task）由 Group B 并入 profiles.TASKS 后自然去重。
    """
    from .profiles import TASKS

    tasks = list(TASKS)
    if "compose" not in tasks:
        tasks.append("compose")
    return tuple(tasks)


def prompts_dir() -> Path:
    """覆盖目录 ~/.musespiral/prompts.d（随 MUSESPIRAL_HOME 解析，测试隔离走环境变量）。"""
    from ... import config  # 懒加载：infra 不在 import 期拉 config（profiles 同款）

    return config.home_dir() / PROMPTS_DIR


def override_path(task: str) -> Path:
    """任务键 → 覆盖文件路径；键域外（绝对/穿越/大写形态）raise ValueError。"""
    if not _TASK_RE.match(task):
        raise ValueError(f"任务键非法: {task!r}（须 [a-z][a-z0-9_]*）")
    return prompts_dir() / f"{task}.txt"


def load_override(task: str) -> str:
    """读单个覆盖文件；键域外/文件缺失/读失败 → 空串（调用方按「未覆盖」处理）。"""
    try:
        return override_path(task).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def list_overrides() -> dict[str, str]:
    """全部覆盖文件 {task: content}；目录缺失/坏文件宽容跳过。"""
    out: dict[str, str] = {}
    try:
        entries = sorted(prompts_dir().glob("*.txt"))
    except OSError:
        return out
    for path in entries:
        if not _TASK_RE.match(path.stem):
            continue
        try:
            out[path.stem] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def dialect_violations(text: str) -> list[str]:
    """文本命中的方言禁用字样（config.DIALECT_FORBIDDEN 单一事实源；大小写不敏感）。"""
    from ... import config

    low = text.lower()
    return [tok for tok in config.DIALECT_FORBIDDEN if tok in low]


def save_override(task: str, content: str) -> Path:
    """写覆盖文件；含模型/供应商方言字样拒写（ValueError，调用方转 400/CLI 报错）。"""
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串")
    violations = dialect_violations(content)
    if violations:
        raise ValueError(
            "提示词含模型/供应商字样（供应商无关红线，见 F45 方言守门）: " + "/".join(violations)
        )
    path = override_path(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
