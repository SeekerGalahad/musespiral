"""插件加载器（插件系统设计 §四/§六，PL-1）——L1 基础设施。

`~/.musespiral/plugins/`（随 MUSESPIRAL_HOME）的扫描与探测面：

  <plugins>/
    <plugin-id>/            # 目录名必须等于 id（斜杠转下划线，校验要求 1）
      manifest.json         # 唯一必需文件
      plugin.py             # python runtime 入口
      requirements.txt      # 可选

本层只做 I/O：扫目录、读 JSON、入口文件存在性、shutil.which 依赖探测。
**契约校验归 core/plugins.manifest，由 services 编排调用**——架构门
规则 1（infra 是最低层不得 import core），本模块零 core import；这是
为什么 scan_manifests 返回原始 dict 而不是 Plugin 模型。

容错纪律（校验要求 7/8）：插件区目录不存在 → 空列表不报错（装零插件
是合法状态）；单个 manifest 坏 → 记入 skipped 清单继续扫，绝不因一个
坏插件炸掉整批（用户手工放置目录，脏数据常态化）。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

from .. import config

MANIFEST_NAME = "manifest.json"

# 插件入口模块可选声明的 python 包依赖（模块级 ``PYTHON_DEPS = [...]``，
# 元素是 **import 名**（faster_whisper）不是 pip 名——探测对象是 find_spec）。
PYTHON_DEPS_ATTR = "PYTHON_DEPS"

# 官方内置插件区目录名（包内 plugins_builtin/，首插件族 2026-11-20 起）
BUILTIN_DIRNAME = "plugins_builtin"


def plugins_dir() -> Path:
    """插件安装区根：~/.musespiral/plugins/（随 MUSESPIRAL_HOME，测试 monkeypatch 即隔离）。"""
    return config.home_dir() / "plugins"


def builtin_plugins_dir() -> Path:
    """官方内置插件区：包内 musespiral/plugins_builtin/（随包分发，免安装）。

    与用户区 plugins_dir() 并列；两区的合并语义（生产路径 root=None 两区
    同扫、用户区同 id 覆盖内置）归 services/plugins_service——本层只给
    路径（infra 不 import core，也不做合并编排）。
    """
    return Path(__file__).resolve().parents[1] / BUILTIN_DIRNAME


def read_manifest(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    """读取单份 manifest.json → (data, None) | (None, reason)。

    永不抛：文件读不出 / JSON 解析失败 / 非 JSON 对象全部收敛为
    (None, reason)——「一个坏文件不炸整个扫描」（校验要求 8）的容错
    纪律收拢在这一处，scan_manifests 与未来的单文件读取方（安装/
    更新流）共用同一条错误路径。契约合法性仍归 core/plugins.manifest。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"manifest 读取/解析失败: {exc}"
    if not isinstance(data, dict):
        return None, "manifest 必须是 JSON 对象"
    return data, None


def scan_manifests(root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """扫描插件区 → (discovered, skipped)。

    discovered 每项 {"dir": 目录绝对路径 str, "data": manifest 原始 dict}
    ——只保证「读到了 JSON 对象」，契约合法性（id 格式/端口类型/runtime
    entry）归 core/plugins.manifest.parse_manifest，由 services 逐个调用。

    skipped 每项 {"dir", "reason"}：缺 manifest / 读取解析失败 / 非
    JSON 对象（read_manifest 的 reason 原样透传）。目录名与 id 的核对
    需要契约语义（expected_dirname），也归 services（本层不 import core）。

    root 缺省 plugins_dir()；不存在 → ([], [])（校验要求 7，不报错）。
    隐藏目录（. 开头）与散文件忽略——不是插件目录，不是错误。
    """
    root = Path(root) if root is not None else plugins_dir()
    if not root.is_dir():
        return [], []
    discovered: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        mpath = entry / MANIFEST_NAME
        if not mpath.is_file():
            skipped.append({"dir": str(entry), "reason": f"缺少 {MANIFEST_NAME}"})
            continue
        data, reason = read_manifest(mpath)
        if data is None:
            skipped.append({"dir": str(entry), "reason": reason})
            continue
        discovered.append({"dir": str(entry), "data": data})
    return discovered, skipped


def entry_file_ok(data: dict[str, Any], plugin_dir: str | Path) -> bool:
    """runtime 入口存在性核验（校验要求 4 的 I/O 半边；格式半边在
    core/plugins.manifest 纯校验）。

    python：entry "file.py:func" → plugin_dir/<file.py> 必须存在；
    exec：entry 含路径分隔符 → 按路径核验（绝对路径直查，相对路径相对
    plugin_dir）；裸命令（ffmpeg 等）→ shutil.which 可寻即算就位。
    entry 缺失/非 str/未知 runtime.type → False（契约红由 core 报，
    这里保守判否）。
    """
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        return False
    etype = runtime.get("type")
    entry = runtime.get("entry")
    if not isinstance(entry, str) or not entry:
        return False
    base = Path(plugin_dir)
    if etype == "python":
        file_part = entry.split(":", 1)[0]
        return (base / file_part).is_file()
    if etype == "exec":
        if "/" in entry or "\\" in entry:
            path = Path(entry)
            target = path if path.is_absolute() else base / path
            return target.exists()
        return shutil.which(entry) is not None
    return False


def check_deps(plugin: Any) -> list[dict[str, Any]]:
    """依赖探测（校验要求 9）：requires 里的命令逐个 shutil.which。

    plugin 鸭子类型接受 core 的 Plugin 模型或 manifest dict（本层不
    import core，取 requires 走属性/键双路）。每项
    {"cmd", "found", "path"}；requires 缺失/非列表按空处理不抛。
    """
    if isinstance(plugin, dict):
        requires = plugin.get("requires")
    else:
        requires = getattr(plugin, "requires", None)
    if not isinstance(requires, list):
        requires = []
    out: list[dict[str, Any]] = []
    for cmd in requires:
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        found = shutil.which(cmd)
        out.append({"cmd": cmd, "found": found is not None, "path": found})
    return out


def declared_python_deps(plugin: Any, plugin_dir: str | Path) -> list[str]:
    """读入口模块顶层声明的 python 包依赖（``PYTHON_DEPS = ["<import 名>", ...]``）。

    **ast 静态提取，零执行**：check 是用户点「重查依赖」就会走的面，若
    importlib 加载入口模块，第三方插件的模块级副作用（连网/写盘）会在
    检查期跑起来——静态解析只读不跑。只认「模块级裸列表赋值、元素全是
    str 字面量」这一种形态；语法坏/形态不符/文件缺失一律 []（依赖面
    收窄不炸，check_plugin 上层语义=无 python 依赖）。
    """
    runtime = plugin.get("runtime") if isinstance(plugin, dict) else getattr(plugin, "runtime", None)
    entry = runtime.get("entry") if isinstance(runtime, dict) else getattr(runtime, "entry", None)
    etype = runtime.get("type") if isinstance(runtime, dict) else getattr(runtime, "type", None)
    if etype != "python" or not isinstance(entry, str) or ":" not in entry:
        return []
    file_part = entry.split(":", 1)[0]
    path = Path(plugin_dir) / file_part
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    for node in tree.body:  # 只看模块级（函数内声明不算）
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == PYTHON_DEPS_ATTR):
            continue
        value = node.value
        if isinstance(value, (ast.Tuple, ast.List)) and all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in value.elts
        ):
            return [elt.value for elt in value.elts]
    return []


def check_python_deps(plugin: Any, plugin_dir: str | Path) -> list[dict[str, Any]]:
    """python 包依赖探测（faster-whisper 这类**可选 pip 依赖**——不在
    requires 命令面，shutil.which 探不到）：逐包 importlib.util.find_spec。

    每项 {"cmd": import 名, "found", "path": 模块来源路径或 None,
    "status": "ok" | "MISSING_DEP"}；find_spec 抛异常按未找到处理
    （探测绝不崩）。与 check_deps 的命令探测并列，聚合归
    services.check_plugin。
    """
    out: list[dict[str, Any]] = []
    for pkg in declared_python_deps(plugin, plugin_dir):
        found_path: str | None = None
        found = False
        try:
            spec = importlib.util.find_spec(pkg)
        except (ValueError, ModuleNotFoundError, ImportError):
            spec = None
        if spec is not None:
            found = True
            found_path = str(spec.origin) if spec.origin else "<namespace>"
        out.append({
            "cmd": pkg,
            "found": found,
            "path": found_path,
            "status": "ok" if found else "MISSING_DEP",
        })
    return out
