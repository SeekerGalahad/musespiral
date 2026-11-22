"""exec runtime 执行件（ARCH-4 §三，2026-11-22）——外部可执行程序插件。

非 Python 插件走子进程：JSON 走 stdin/stdout（进程读 JSON 输入、写 JSON 输出），
timeout 强杀整个进程组（start_new_session + killpg，孙进程不留），stderr 逐行
透传到运行日志。契约校验（输出键 ⊆ manifest.outputs）、workspace 落盘、步级
记账复用 engine 的公共后半段——本模块只负责「进程 → dict 输出」这一段。

安全纪律（本批硬要求 9/10，对应正典 §7.1 权限表）：
  - 权限门：runtime.type=exec 的插件必须显式声明 ``permissions`` 含
    ``exec:subprocess``，否则 **拒绝执行**（ExecPermissionError）。声明在
    manifest，由用户安装时知情批准；引擎不放行未声明的子进程启动。
  - 进程隔离：shell=False（entry 经 shlex.split 拆词，不经 shell 解释，无命令
    注入）；子进程崩溃/非零退出不能掀翻主进程，收敛为可读节点失败。
  - 超时强杀：subprocess.run 的 timeout 只杀直接子进程，这里用 Popen +
    start_new_session=True + os.killpg(SIGKILL) 杀整组，再 communicate 回收，
    杜绝 sleep 脚本派生子进程的僵尸残留。

协议（刻意最小，ponytail）：stdin = inputs 的 JSON（不包 config 信封——
echo/cat 类插件原样回吐即完成往返，硬要求 18）；stdout = outputs 的 JSON dict。
需要 config 的 exec 插件在脚本里读环境变量或改 envelope 是后续升级点。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable

# exec 插件必须声明的权限词（正典 §7.1，与 core.plugins.manifest.PERMISSIONS 同值；
# 此处重复字面量避免 services→core 之外的耦合，测试对拍两表一致）
EXEC_PERMISSION = "exec:subprocess"

# timeout_s 缺省（manifest 未声明时）——60s，防无 timeout 的插件永久挂住流水线
DEFAULT_EXEC_TIMEOUT_S = 60


class ExecRuntimeError(RuntimeError):
    """exec 插件执行期可读失败基类（引擎捕获 → failed step）。"""


class ExecPermissionError(ExecRuntimeError):
    """未声明 exec:subprocess 权限——拒绝执行。"""


class ExecTimeoutError(ExecRuntimeError):
    """超时强杀——子进程（含其进程组）已被 SIGKILL。"""


class ExecProtocolError(ExecRuntimeError):
    """I/O 协议错：entry 空/stdout 非 JSON/输出非 dict。"""


def require_exec_permission(plugin: Any) -> None:
    """权限门：exec runtime 插件须声明 exec:subprocess，否则抛 ExecPermissionError。

    plugin 鸭子类型（Plugin 模型取 .permissions/.runtime，dict 取键）——plan 期
    与 run 期共用同一判定（plan 期给构造期可读错，run 期为防御二道门）。
    """
    if isinstance(plugin, dict):
        permissions = plugin.get("permissions") or []
        runtime = plugin.get("runtime") or {}
        rtype = runtime.get("type") if isinstance(runtime, dict) else None
    else:
        permissions = getattr(plugin, "permissions", None) or []
        runtime = getattr(plugin, "runtime", None)
        rtype = getattr(runtime, "type", None) if runtime is not None else None
    if rtype != "exec":
        return  # 非 exec 不适用本门
    if EXEC_PERMISSION not in permissions:
        pid = plugin.get("id") if isinstance(plugin, dict) else getattr(plugin, "id", "?")
        raise ExecPermissionError(
            f"exec 插件 {pid!r} 未声明权限 {EXEC_PERMISSION!r}——拒绝启动子进程"
            "（请在 manifest.permissions 显式声明）"
        )


def resolve_exec_command(entry: str, plugin_dir: Path) -> list[str]:
    """entry 字串 → argv 列表（shlex 拆词；首词路径相对 plugin_dir 解析）。

    不经 shell（shell=False）——拆词只做空白/引号分词，不做 $VAR/; 展开，无
    注入面。首词含路径分隔符按路径（相对 plugin_dir），裸命令走 shutil.which；
    找不到也返回原词（Popen 抛 FileNotFoundError，引擎转可读失败）。
    """
    try:
        tokens = shlex.split(entry)
    except ValueError as exc:
        raise ExecProtocolError(f"exec entry 分词失败: {entry!r}: {exc}") from None
    if not tokens:
        raise ExecProtocolError("exec entry 为空——无法启动子进程")
    head = tokens[0]
    if "/" in head or "\\" in head:
        p = Path(head)
        tokens[0] = str(p if p.is_absolute() else plugin_dir / p)
    else:
        found = shutil.which(head)
        if found:
            tokens[0] = found
    return tokens


def run_exec_plugin(
    plugin: Any,
    plugin_dir: Path,
    inputs: dict[str, Any],
    *,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """执行 exec 插件 → outputs dict（JSON 往返）。

    流程：权限门 → entry 解析 → stdin=inputs JSON → Popen(start_new_session) →
    communicate(timeout) → stdout JSON 解析成 dict → stderr 逐行透传 on_log。
    超时杀整组并回收；非零退出/非法 stdout/非 dict 一律可读 ExecRuntimeError。
    不返回 Path/mem ref——exec 插件输出须可 JSON 序列化（协议约束）。
    """
    require_exec_permission(plugin)
    if isinstance(plugin, dict):
        runtime = plugin.get("runtime") or {}
        entry = runtime.get("entry") if isinstance(runtime, dict) else None
        timeout = runtime.get("timeout_s") if isinstance(runtime, dict) else None
    else:
        runtime = getattr(plugin, "runtime", None)
        entry = getattr(runtime, "entry", None) if runtime is not None else None
        timeout = getattr(runtime, "timeout_s", None) if runtime is not None else None
    if not isinstance(entry, str) or not entry.strip():
        raise ExecProtocolError("exec 插件缺 runtime.entry——无法启动子进程")
    argv = resolve_exec_command(entry, Path(plugin_dir))
    timeout_s = timeout if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_EXEC_TIMEOUT_S

    payload = json.dumps(inputs, ensure_ascii=False)
    proc = subprocess.Popen(  # noqa: S603 —— argv 来自已校验 manifest，shell=False 无注入
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(plugin_dir),
        text=True,
        start_new_session=True,  # 独立进程组——超时可 killpg 整组
    )
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # 强杀整组（含孙进程），再 communicate 回收 zombie
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover - 竞态防御
            proc.kill()
        proc.communicate()
        if on_log is not None:
            on_log(f"exec 超时 {timeout_s}s，已强杀进程组: {' '.join(argv)}")
        raise ExecTimeoutError(
            f"exec 插件子进程超时（{timeout_s}s，已强杀进程组）: {' '.join(argv)}"
        ) from None
    finally:
        # 兜底：communicate 正常返回后进程已退出；异常路径上面已回收。此处只处理
        # 极端情况（如 KeyboardInterrupt）——确保不留活进程。
        if proc.poll() is None:  # pragma: no cover - 正常路径不会走到
            proc.kill()

    if stderr and on_log is not None:
        for line in stderr.splitlines():
            on_log(f"[exec stderr] {line}")

    if proc.returncode != 0:
        tail = stderr.strip().splitlines()
        hint = f"；stderr: {tail[-1]}" if tail else ""
        raise ExecRuntimeError(
            f"exec 子进程退出码 {proc.returncode}: {' '.join(argv)}{hint}"
        )
    try:
        outputs = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ExecProtocolError(
            f"exec stdout 非合法 JSON: {exc}（前 120 字: {stdout[:120]!r}）"
        ) from None
    if not isinstance(outputs, dict):
        raise ExecProtocolError(
            f"exec stdout JSON 须为对象（键=manifest.outputs），实为 {type(outputs).__name__}"
        )
    return outputs
