"""插件系统服务层（插件系统设计 §十二 /api/plugins 族，PL-1）——L3 编排。

编排 infra/plugins_loader（I/O 半边：扫描/入口存在性/依赖探测）与
core/plugins（契约半边：parse_manifest / expected_dirname），对表现层
暴露 list/get/check/refresh 入口（PL-3 前端「manifest 即 UI」的数据面；
scan_plugins 是同份扫描的 tuple 形编程接口）。
每次调用重扫插件区——插件是用户手工放置的目录，无安装登记表，重扫即
是事实真相（设计 §十二 /api/plugins/refresh 语义即如此）。用户区走
**增量发现索引**（ARCH-4 §五，2026-11-22，loader.scan_manifests_incremental）：
~/.musespiral/plugins/index.json 缓存每目录的 hash/version/timestamp + 原始
manifest，未变目录跳过读盘与 JSON 解析（加速启动列表加载），变更目录只更新
该条；内置区（包内 plugins_builtin/）始终全量扫——不往包目录写运行时缓存。

三个入口都容忍脏数据：坏插件只进 skipped 清单（reason 带错误明细），
不影响其他插件（校验要求 8）。流水线静态契约校验在
core.plugins.manifest.validate_pipeline_contract（PL-2 接线）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.plugins.manifest import expected_dirname, parse_manifest
from ..infra import plugins_loader as loader


def _scan_one_root(
    root: Path, *, incremental: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """单 root 扫描 + 逐个契约校验三道门（list_plugins 的原主体，抽出以便
    内置区与用户区合并扫描）。

    三道门全过才算已装：parse_manifest 红→skipped；目录名≠id（斜杠转
    下划线核对，校验要求 1）→skipped；runtime 入口文件不存在
    （infra.entry_file_ok，校验要求 4）→skipped。

    incremental=True（用户区/显式 root）走发现索引缓存——未变目录跳过读盘；
    内置区 False 全量扫（不往包目录写运行时缓存）。两路的 discovered/skipped
    形状一致，调用方无感。
    """
    if incremental:
        discovered, skipped, _index = loader.scan_manifests_incremental(root)
    else:
        discovered, skipped = loader.scan_manifests(root)
    plugins: list[dict[str, Any]] = []
    for disc in discovered:
        directory, data = disc["dir"], disc["data"]
        plugin, errors = parse_manifest(data)
        if plugin is None:
            skipped.append({"dir": directory, "reason": "；".join(errors)})
            continue
        if Path(directory).name != expected_dirname(plugin.id):
            skipped.append({
                "dir": directory,
                "reason": f"目录名与 id 不符（应为 {expected_dirname(plugin.id)}）",
            })
            continue
        if not loader.entry_file_ok(data, directory):
            skipped.append({"dir": directory, "reason": f"runtime 入口不存在: {plugin.runtime.entry}"})
            continue
        plugins.append({"dir": directory, "manifest": plugin.model_dump(mode="json")})
    return plugins, skipped


def list_plugins(root: Path | None = None) -> dict[str, Any]:
    """扫描+逐个契约校验 → {"plugins": [{"dir", "manifest"}], "skipped": [...]}。

    root 语义两态（首插件族起，官方插件住包内 plugins_builtin/）：
      - root=None（生产路径）：**内置区 + 用户区合并**——官方插件随包分发
        免安装（不必放 ~/.musespiral/plugins/）；用户区同 id **覆盖**内置（用户
        换掉官方实现的逃生门）；skipped 两区聚合。
      - root 显式（测试/工具自定 root）：只扫该 root，内置区不混入——
        断言单一 root 内容的调用方语义不变；**不写索引**（缓存只属于用户安装区，
        不往测试夹具/包内目录落 index.json 污染源码树）。
    plugins 按插件 id 排序（稳定输出，前端清单不跳）；manifest 是
    model_dump(mode="json")——前端据此渲染 I/O 表/权限/配置表单（§8.2）。
    """
    if root is not None:
        plugins, skipped = _scan_one_root(Path(root))
    else:
        builtin, builtin_skipped = _scan_one_root(loader.builtin_plugins_dir())
        user, user_skipped = _scan_one_root(loader.plugins_dir(), incremental=True)
        by_id: dict[str, dict[str, Any]] = {}
        for item in (*builtin, *user):
            by_id[item["manifest"]["id"]] = item  # 用户区后扫，同 id 覆盖内置
        plugins = sorted(by_id.values(), key=lambda item: item["manifest"]["id"])
        skipped = [*builtin_skipped, *user_skipped]
    return {"plugins": plugins, "skipped": skipped}


def scan_plugins(root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """扫描+契约校验 → (valid, skipped) 二元组形。

    名义入口放在本层而非 infra 是架构门裁决（后端架构正典 §2.2 规则 4：
    infra 不得 import 上层模块）：(valid, skipped) 里的 valid 需要
    core/plugins.manifest 的契约校验，infra 只能交付原始 dict。tuple 形
    供编程方消费，dict 形（list_plugins）供 API/前端消费，同一份扫描。
    """
    out = list_plugins(root)
    return out["plugins"], out["skipped"]


def refresh(root: Path | None = None) -> dict[str, Any]:
    """重扫插件目录（正典 §十二 POST /api/plugins/refresh）。

    v1 无缓存——插件是用户手工放置的目录，无安装登记表，每次调用即
    重扫（refresh ≡ list_plugins）。独立成函数是端点契约的锚：未来若
    引入缓存/登记表，只有这一处需要改语义，调用方（API 层）不动。
    """
    return list_plugins(root)


def get_plugin(plugin_id: str, root: Path | None = None) -> dict[str, Any] | None:
    """单插件详情（GET /api/plugins/{id} 形）→ {"dir", "manifest"} | None。

    None 表示未装/坏 manifest/目录名不符——详情页对四态统一按「不存在」
    处理（坏插件的 reason 已在 list_plugins 的 skipped 里可见）。
    """
    for item in list_plugins(root)["plugins"]:
        if item["manifest"]["id"] == plugin_id:
            return item
    return None


def check_plugin(plugin_id: str, root: Path | None = None) -> dict[str, Any] | None:
    """依赖检查（GET /api/plugins/{id}/check 形）→ {"id", "deps", "python_deps",
    "status"} | None。

    deps 每项 {"cmd", "found", "path"}（infra.check_deps，shutil.which 探
    requires 命令面）；python_deps 每项 {"cmd", "found", "path", "status"}——
    插件入口模块顶层 ``PYTHON_DEPS`` 声明的 pip 依赖（faster-whisper 这类
    可选包，不是命令，which 探不到），infra.check_python_deps 静态读取+
    find_spec 探测，任一缺席该项 status="MISSING_DEP"；顶层 status 为
    deps+python_deps 全体的聚合（"ok" | "MISSING_DEP"）。模型权重等非
    命令非包依赖由插件在运行时自报（transcribe 的模型就位探测），v1 探
    测面只覆盖命令+包（正典 §十三）。
    """
    found = get_plugin(plugin_id, root)
    if found is None:
        return None
    manifest = found["manifest"]
    deps = loader.check_deps(manifest)
    python_deps = loader.check_python_deps(manifest, found["dir"])
    missing = any(not d["found"] for d in (*deps, *python_deps))
    return {
        "id": manifest["id"],
        "deps": deps,
        "python_deps": python_deps,
        "status": "MISSING_DEP" if missing else "ok",
    }
