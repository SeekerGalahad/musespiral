"""manifest 解析与契约校验（插件系统设计 §3.2/§4.2）——L2 纯函数，零 I/O。

manifest.json 是插件的唯一自描述面（正典 §四「目录即插件」）。本模块
负责它的**契约半边**：pydantic 结构校验 + 格式/枚举/交叉校验。文件
存在性、可执行性这类 I/O 半边归 infra/plugins_loader（架构门规则 1：
infra 是最低层不得 import core，故 entry 存在性由 services 编排时
调 infra 核验，本层只校验格式）。

parse_manifest 红绿双出口：(Plugin | None, errors)——坏 manifest 返回
(None, 错误清单)，绝不让单个坏插件炸掉整批加载（校验要求 8 的数据面；
隔离在 services/plugins_service 逐个调用本函数达成）。

extra="forbid" 是有意的：manifest 拼错键（permissins）静默忽略是契约
系统的隐性坑——宁可在安装时红，不在运行时哑。v1 键集=§4.2.1 键集规范
（2026-11-21 补的规范性枚举表）的必需六键 id/version/kind/inputs/outputs/
runtime + 可选六键 name/description/requires/permissions/config_schema/
uses_ai，恰好此 12 键（其中 uses_ai 的**语义**出自 §7.3，键位归 §4.2.1）。

控制流 kind（ARCH-4 §一，2026-11-22）：``kind`` 枚举增 ``control``——
switch 类插件（输出 ``route`` 供引擎选分支）的契约标记。规则收在
parse_manifest 一处：kind=control ⇒ outputs 必须含 ``route``（string 型）
——引擎按该端口取路由值选 branch，缺声明即安装期红而非运行时哑。
loop 节点不经插件（引擎原生，见 services/pipeline/plan），无 control
manifest 形态。

端口类型表（校验要求 3）= 契约 10 型（types.CONTRACT_TYPES）∪ ext/ 前缀
∪ 标量基元 SCALAR_PORT_TYPES。标量基元必须收录：正典 §4.2 官方示例自身
就用 string/number/string[]（language/hotwords/duration_s）——它们是
辅助端口不是管道契约面；契约 10 型保持恰好 10 个不动。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import types as contract_types
from .types import types_compatible

# ── 权限表（正典 §7.1，安装时审批与运行时限权的声明词表）──────────
PERMISSIONS: tuple[str, ...] = (
    "fs:read",           # 读输入文件（workspace 与 inputs 里的路径）
    "fs:write:tmp",      # 写 ctx.tmpdir()
    "fs:write:output",   # 写 ctx.artifacts_dir
    "net:none",          # 零外联
    "net:http",          # 允许 HTTP 出网（安装时需明确批准）
    "exec:subprocess",   # 可启动子进程（安装时需批准）
)

# 端口可用的标量基元类型（见模块 docstring「端口类型表」；scalars 是
# 运行级参数/辅助输出，不进契约 10 型，但同样按「完全相等」参与连接）
SCALAR_PORT_TYPES: tuple[str, ...] = (
    "string", "number", "boolean", "string[]", "number[]", "boolean[]",
)

# id 格式：<name> 或 <vendor>/<name>（正典 §4.2），段内小写字母数字下划线连字符
_ID_RE = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)?$")
# 语义化版本（MAJOR.MINOR.PATCH，可带 -预发布/+构建元数据）
_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
# python 入口格式：<path>.py:<func>（path 段禁冒号禁空白；func 是标识符）。
# 文件存在性归 infra（本层纯）。
_PY_ENTRY_RE = re.compile(r"^[^:\s]+\.py:[A-Za-z_][A-Za-z0-9_]*$")


def is_known_port_type(port_type: str) -> bool:
    """端口类型合法性（校验要求 3）：契约 10 型 / ext/ 前缀 / 标量基元。"""
    return (
        port_type in contract_types.CONTRACT_TYPES
        or contract_types.is_ext_type(port_type)
        or port_type in SCALAR_PORT_TYPES
    )


def expected_dirname(plugin_id: str) -> str:
    """插件目录名 = id 斜杠转下划线（校验要求 1；loader/services 据此
    核对「目录名必须等于 id」）。纯字符串运算。"""
    return plugin_id.replace("/", "_")


class I18nText(BaseModel):
    """name/description 的 i18n 对象（正典 §4.2「关键设计点」：中文优先、
    英文兜底，不是简单字符串）。zh 必填——中文是本平台主语言。"""
    model_config = ConfigDict(extra="forbid")

    zh: str
    en: str | None = None


class Port(BaseModel):
    """具名端口（正典 §4.2：inputs/outputs 是**对象**不是数组——具名端口
    支持多输入多输出，数组无法表达「这个输出叫什么」）。

    required 缺省 True：未声明的端口视为必需（契约从严）；§4.2 示例里
    required 总是显式写出，缺省值只是宽容读。outputs 端口不使用 required
    （产出即存在），共用同一模型免双轨。
    """
    model_config = ConfigDict(extra="forbid")

    type: str
    required: bool = True
    default: Any = None


class Runtime(BaseModel):
    """运行时声明（正典 §4.3 裁决：v1 只做 python 与 exec；http 需服务
    发现/健康检查/重试，留接口不实现——声明为 http 直接校验红）。

    entry 格式校验在本模块（纯），文件存在性在 infra/plugins_loader。
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["python", "exec"]
    entry: str = Field(min_length=1)
    timeout_s: int | None = None


class Plugin(BaseModel):
    """解析后的插件清单（§4.2.1 键集规范：必需六键 id/version/kind/inputs/
    outputs/runtime + 可选六键 name/description/requires/permissions/
    config_schema/uses_ai；多余键 forbid）。"""
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    kind: Literal["source", "process", "sink", "control"]
    name: I18nText | None = None
    description: I18nText | None = None
    inputs: dict[str, Port]  # §4.2 必需键——无默认，缺键即红（可显式传 {} 表无端口）
    outputs: dict[str, Port]  # 同上
    runtime: Runtime
    requires: list[str] = []
    permissions: list[str] = []
    config_schema: dict[str, Any] = {}
    uses_ai: bool = False


def _id_errors(plugin_id: str) -> list[str]:
    if not _ID_RE.match(plugin_id):
        return [f"id 格式非法（须为 <name> 或 <vendor>/<name>，小写字母数字下划线连字符）: {plugin_id!r}"]
    return []


def _version_errors(version: str) -> list[str]:
    if not _SEMVER_RE.match(version):
        return [f"version 须为语义化版本 MAJOR.MINOR.PATCH: {version!r}"]
    return []


def _port_type_errors(field: str, ports: dict[str, Port]) -> list[str]:
    return [
        f"{field}.{name}.type 不在已知端口类型表内（契约 10 型/ext/标量基元）: {port.type!r}"
        for name, port in ports.items()
        if not is_known_port_type(port.type)
    ]


def _runtime_entry_errors(plugin: Plugin) -> list[str]:
    """entry 格式校验（校验要求 4 的纯半边）：python 须 file.py:func 形态；
    exec 只要求非空（可执行路径/命令的存在性核验在 infra）。"""
    if plugin.runtime.type == "python" and not _PY_ENTRY_RE.match(plugin.runtime.entry):
        return [f"runtime.entry 须为 <file>.py:<func> 形态（python runtime）: {plugin.runtime.entry!r}"]
    return []


def _permission_errors(perms: list[str]) -> list[str]:
    return [
        f"permissions 不在已知权限表内 {PERMISSIONS}: {p!r}"
        for p in perms
        if p not in PERMISSIONS
    ]


def _control_kind_errors(plugin: Plugin) -> list[str]:
    """control kind 契约规则（ARCH-4）：必须声明 route 输出（string 型）。

    引擎按 ``outputs.route`` 的值选 branch（services/pipeline/engine），
    缺声明会在运行期才哑——收到安装期红。非 control kind 不适用本规则。
    """
    if plugin.kind != "control":
        return []
    port = plugin.outputs.get("route")
    if port is None:
        return ["kind=control 的插件必须声明输出端口 route（string，引擎据此选分支）"]
    if port.type != "string":
        return [f"kind=control 的 route 输出端口须为 string 型，实为 {port.type!r}"]
    return []


def parse_manifest(data: Any) -> tuple[Plugin | None, list[str]]:
    """解析并校验一份 manifest dict → (Plugin | None, errors)。

    红路径：非 dict / pydantic 结构错 / id-version-kind-端口类型-entry
    格式-权限任一不过 → (None, 错误清单)。绿路径：(Plugin, [])。
    不抛异常——调用方（loader/services）靠它隔离单个坏插件。
    """
    if not isinstance(data, dict):
        return None, ["manifest 必须是 JSON 对象"]
    try:
        plugin = Plugin.model_validate(data)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<root>"
            errors.append(f"{loc}: {err['msg']}")
        return None, errors
    errors = (
        _id_errors(plugin.id)
        + _version_errors(plugin.version)
        + _port_type_errors("inputs", plugin.inputs)
        + _port_type_errors("outputs", plugin.outputs)
        + _runtime_entry_errors(plugin)
        + _permission_errors(plugin.permissions)
        + _control_kind_errors(plugin)
    )
    if errors:
        return None, errors
    return plugin, []


def _as_plugin(raw: Any, node_label: str, errors: list[str]) -> Plugin | None:
    """节点携带的 plugin 宽容归一：Plugin 直用；dict 走 parse_manifest；
    其他 → 记错返 None（错误挂到具体节点，不让一个坏节点哑掉整条校验）。"""
    if isinstance(raw, Plugin):
        return raw
    if isinstance(raw, dict):
        plugin, errs = parse_manifest(raw)
        if errs:
            errors.append(f"节点[{node_label}] plugin manifest 非法: {'；'.join(errs)}")
            return None
        return plugin
    errors.append(f"节点[{node_label}] 缺少 plugin（Plugin 模型或 manifest dict）")
    return None


def validate_pipeline_contract(nodes: list[dict[str, Any]]) -> list[str]:
    """流水线静态契约校验（正典 §3.2/§4.2/§5.1）→ 错误清单，空=可保存。

    nodes 形状（v1 线性流水线的节点 dict）：每项
      {"id": 节点 id (str), "plugin": Plugin 模型 | manifest dict}
    plugin 也接受 None/缺省之外的任意值——宽容收错不抛异常（与
    parse_manifest 同纪律，保存前的静态校验必须总能给出红清单）。

    四条规则：
      1. 节点非空、id 非空且唯一（workspace 按 node_id 存取，§5.2——
         重复 id 会让产物互相覆盖）；
      2. kind 位置：source 只能开头、sink 只能结尾（§4.2「三态决定
         它在流水线里的位置」）；control（ARCH-4 switch 类）无位置约束
         ——路由节点可出现在任意中间位置，其分支子链的位置规则由
         plan_pipeline 按链递归校验（本函数吃展平链）；
      3. 供给（正典 §3.2「outputs ∩ inputs 非空」的逐端口收紧 + 传递
         化）：每个 process/sink 节点的**每个必需输入端口**，须有任一
         更早节点存在兼容类型（types_compatible）的输出端口。为什么比
         §3.2 的相邻交集更强：正典 §9.1 首链里 frame-grab 的 file/path
         来自两步之前的 local-file（工作区按 node_id 取数，§5.2），
         相邻交集判定会误红正典首链；而「必需端口无供给」与「相邻不
         接」是同一错误类别——保存前标红而非运行时才炸（§3.2 立意）。
      4. 兼容判定只经 types_compatible（ext/<name> 只与自身兼容）。
    """
    errors: list[str] = []
    if not nodes:
        return ["流水线至少需要一个节点"]
    plugins: list[Plugin | None] = []
    node_ids: list[str | None] = []
    seen_ids: set[str] = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"节点[{i}] 必须是对象（{{id, plugin}}）")
            plugins.append(None)
            node_ids.append(None)
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"节点[{i}] 缺少非空 id")
            node_ids.append(None)
        elif node_id in seen_ids:
            errors.append(f"节点 id 重复: {node_id!r}（workspace 按 node_id 存取，重复会互相覆盖）")
            node_ids.append(node_id)
        else:
            seen_ids.add(node_id)
            node_ids.append(node_id)
        plugins.append(_as_plugin(node.get("plugin"), str(node_id if node_id is not None else i), errors))

    n = len(nodes)
    for i, plugin in enumerate(plugins):
        if plugin is None:
            continue
        node_id = node_ids[i] or f"#{i}"
        # 报错同时带节点 id 与插件 id——前端画布上用户看到的是插件名（§8.2）
        label = node_id if plugin.id == node_id else f"{node_id}（{plugin.id}）"
        if plugin.kind == "source" and i != 0:
            errors.append(f"节点[{i}] {label} 是 source，只能位于流水线开头")
        if plugin.kind == "sink" and i != n - 1:
            errors.append(f"节点[{i}] {label} 是 sink，只能位于流水线结尾")
        if i == 0:
            continue  # 首节点的必需输入是运行级参数，不由流水线供给
        for port_name, port in plugin.inputs.items():
            if not port.required:
                continue
            fed = any(
                types_compatible(out.type, port.type)
                for earlier in plugins[:i]
                if earlier is not None
                for out in earlier.outputs.values()
            )
            if not fed:
                errors.append(
                    f"节点[{i}] {label} 必需输入端口 {port_name!r}（type={port.type!r}）"
                    "未被任何更早节点的输出供给"
                )
    return errors
