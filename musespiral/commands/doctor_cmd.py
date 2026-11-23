"""doctor 预检命令：musespiral doctor [--json]。

W1-B1（2026-11-23，正典=《musespiral-运维发布改良设计-2026-11-23》§二 W1-B1）：
新环境装完不知道缺什么——INSTALL.md 是文档不是诊断。五项预检一次出绿黄红：

  credentials  ~/.musespiral/credentials.json 可读且有 appid 字段
  ai           ai.json 可解析 + default/路由目标档案有 base_url（**零出网**：
               只查配置形态；真探活是既有 `ai check` 的事，预检不重复）
  ffmpeg       shutil.which('ffmpeg') 非 None（视频转写/朗读依赖）
  kb_root      kb_root.txt / kb_sources.json 指向存在目录（选题/评分依赖）
  disk         home 所在盘剩余水位（<10GB 红 / <20GB 黄 / 否则绿）

红色**只 reserved 给磁盘水位**——「可配置未配」不是故障，一律黄。退出码：
全绿 0 / 有黄 1 / 有红 2（脚本消费口径，J3 不用管道短路）。--json 输出
{checks:[{name,status,detail}], exit_code} 机读形态。

版本无关：纯读本地配置与 PATH，零网络（测试以 monkeypatch 锚定不 import
urllib/requests——见 tests/test_doctor.py 的出网断言）。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import click

# 三项档位（终端人读表与 --json 同源）
GREEN, YELLOW, RED = "green", "yellow", "red"
_ICON = {GREEN: "✓", YELLOW: "⚠", RED: "✗"}

# 磁盘水位阈值（GB；与 /api/health 的 disk_free_gb 同口径——2**30 字节）
_DISK_RED_GB = 10
_DISK_YELLOW_GB = 20


def _check_credentials() -> tuple[str, str]:
    """凭证：credentials.json 可读且有 appid（缺=黄，建议配置）。"""
    from .. import config

    path = Path(config.home_dir()) / config.CREDENTIALS_FILE
    if not path.is_file():
        return YELLOW, "凭证未配置（musespiral config set appid <appid> 后填 secret）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return YELLOW, f"credentials.json 不可读（{err}）——检查文件格式或重建"
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or not accounts:
        return YELLOW, "credentials.json 无账号（musespiral config set appid <appid>）"
    appid = ""
    for acc in accounts.values():
        if isinstance(acc, dict) and str(acc.get("appid") or "").strip():
            appid = str(acc["appid"])
            break
    if not appid:
        return YELLOW, "credentials.json 缺 appid 字段（musespiral config set appid <appid>）"
    return GREEN, f"凭证就绪（{len(accounts)} 个账号）"


def _check_ai() -> tuple[str, str]:
    """AI 配置：ai.json 可解析 + 生效档案有 base_url（零出网，只查配置形态）。

    生效档案口径与运行期同源——default 档案（AI 未配任务路由时全部走 default）；
    另检 routing 指向的档案，任一命中 base_url 即绿。api_key 可空（本地 Ollama）。
    """
    from ..infra.ai.profiles import AIError, ai_configured, list_profiles, load_ai_config

    try:
        cfg = load_ai_config()
        state = list_profiles()
    except (AIError, OSError, ValueError) as err:  # 坏文件在 profiles 内已宽容，此处兜底
        return (
            YELLOW,
            f"ai.json 不可读（{err}）——musespiral ai config --base-url ... --model ... 重建",
        )
    if not state.get("profiles"):
        return YELLOW, "AI 未配置（musespiral ai config --base-url ... --model ...）"
    if cfg.get("base_url"):
        return GREEN, f"AI 就绪（默认 {state.get('default') or 'default'} · {cfg.get('base_url')}）"
    # default 无 base_url：看路由目标档案是否有可达 base_url
    routing = state.get("routing") or {}
    for task, name in routing.items():
        if ai_configured(task):
            return GREEN, f"AI 就绪（{task} → {name}）"
    return YELLOW, "AI 档案缺 base_url（musespiral ai config --base-url ... --model ...）"


def _check_ffmpeg() -> tuple[str, str]:
    """ffmpeg 在 PATH（视频转写/朗读依赖；缺=黄）。"""
    found = shutil.which("ffmpeg")
    if found is None:
        return YELLOW, "ffmpeg 不在 PATH（视频转写/朗读需 ffmpeg；brew install ffmpeg）"
    return GREEN, f"ffmpeg 就绪（{found}）"


def _check_kb_root() -> tuple[str, str]:
    """KB 根指向存在目录（kb_sources.json 优先，回退 kb_root.txt；缺=黄）。"""
    from .. import config

    try:
        root = config.knowledge_root()
    except (OSError, ValueError) as err:
        return YELLOW, f"KB 根路径非法（{err}）——musespiral config kb set-root <绝对路径>"
    if root is None:
        return YELLOW, "KB 根未配置（musespiral config kb set-root <绝对路径>）"
    if not Path(root).is_dir():
        return YELLOW, f"KB 根目录不存在（{root}）——musespiral config kb set-root <绝对路径>"
    return GREEN, f"KB 根就绪（{root}）"


def _check_disk() -> tuple[str, str]:
    """home 所在盘剩余水位（<10GB 红 / <20GB 黄 / 否则绿）。"""
    try:
        usage = shutil.disk_usage(str(Path.home()))
    except OSError as err:
        return RED, f"磁盘水位不可读（{err}）"
    free_gb = usage.free / 2**30
    if free_gb < _DISK_RED_GB:
        return RED, f"磁盘剩余 {free_gb:.1f}GB（<{_DISK_RED_GB}GB 红线——克隆/缓存需清理）"
    if free_gb < _DISK_YELLOW_GB:
        return YELLOW, f"磁盘剩余 {free_gb:.1f}GB（<{_DISK_YELLOW_GB}GB，注意清理）"
    return GREEN, f"磁盘剩余 {free_gb:.1f}GB"


# 预检项清单（name → 探测函数；顺序即终端表序）
_CHECKS: tuple[tuple[str, Any], ...] = (
    ("credentials", _check_credentials),
    ("ai", _check_ai),
    ("ffmpeg", _check_ffmpeg),
    ("kb_root", _check_kb_root),
    ("disk", _check_disk),
)


def _exit_code(checks: list[dict[str, str]]) -> int:
    """全绿 0 / 有黄 1 / 有红 2（红优先）。"""
    statuses = {c["status"] for c in checks}
    if RED in statuses:
        return 2
    if YELLOW in statuses:
        return 1
    return 0


@click.command()
@click.option("--json", "as_json", is_flag=True, help="机读 JSON 输出（{checks:[...], exit_code}）")
def doctor(as_json: bool) -> None:
    """环境预检（凭证/AI/ffmpeg/KB 根/磁盘五项出绿黄红；退出码 0 绿/1 黄/2 红）。"""
    checks: list[dict[str, str]] = []
    for name, fn in _CHECKS:
        try:
            status, detail = fn()
        except Exception as err:  # 预检本身不得炸——单项异常归黄（诊断面宽容）
            status, detail = YELLOW, f"预检异常：{err}"
        checks.append({"name": name, "status": status, "detail": detail})
    code = _exit_code(checks)
    if as_json:
        click.echo(json.dumps({"checks": checks, "exit_code": code}, ensure_ascii=False))
        sys.exit(code)
    for c in checks:
        click.echo(f"  {_ICON[c['status']]} {c['name']:12} {c['detail']}")
    summary = {GREEN: "全部就绪", YELLOW: "有可配置项待补", RED: "有故障需处理"}[
        RED if code == 2 else YELLOW if code == 1 else GREEN
    ]
    click.echo(f"\n{summary}（退出码 {code}）")
    sys.exit(code)
