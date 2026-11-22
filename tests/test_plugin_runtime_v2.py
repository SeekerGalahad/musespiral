"""插件系统 v2 后半（ARCH-4 §三/§四/§五，2026-11-22）——exec runtime / 版本协商 / 发现索引。

硬要求 18-20 + 200-ish 协议面：
  18. exec echo 脚本（cat stdin→stdout）验证 JSON 往返（经引擎 run_pipeline）；
  19. exec 超时：sleep 脚本 + timeout=1s，断言可读报错 + 进程被强杀（耗时短、
      marker 未落）；
  20. 版本协商：保存后改 manifest 再加载 → 警告出现；老定义无 hash → 零警告；
  附加：exec 权限门（无 exec:subprocess 拒）、exec 进程隔离（非零退出/坏 stdout
  可读失败、stderr 透传）、发现索引 index.json 增量（首次建立/命中零读盘/
  变更只更新该条/删除清条/坏缓存降级全量重扫）、core↔infra hash 对拍。

全部在 MUSESPIRAL_HOME / tmp 插件区沙箱跑，零触真实 ~/.musespiral；exec 子进程
用本机 python3（shutil.which）裸命令或 tmp 内可执行脚本，零外部网络。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from musespiral.core.plugins import version as vcore
from musespiral.core.plugins.manifest import parse_manifest
from musespiral.core.plugins.version import HASH_KEY, manifest_hash, version_warnings
from musespiral.infra import plugins_loader as loader
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services.pipeline import exec_runtime as er
from musespiral.studio.plugin_actions import list_detail, pipeline_crud
from musespiral.studio.security import _StudioContext

# ── 沙箱 ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "pipeline_runs"


def _write_manifest(root: Path, dirname: str, manifest: dict) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return d


def _write_exec_script(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _normalized_hash(manifest: dict) -> str:
    """服务保存时算的是 parse→model_dump 归一化后的 manifest（含默认键），测试对齐。"""
    plugin, errs = parse_manifest(manifest)
    assert plugin is not None and errs == []
    return manifest_hash(plugin.model_dump(mode="json"))


# ════════════════════════════════════════════════════════════════════
# ③ exec runtime
# ════════════════════════════════════════════════════════════════════


def _exec_manifest(pid: str, entry: str, timeout: int | None = None) -> dict:
    runtime: dict = {"type": "exec", "entry": entry}
    if timeout is not None:
        runtime["timeout_s"] = timeout
    return {
        "id": pid, "version": "1.0.0", "kind": "process",
        "inputs": {"value": {"type": "string", "required": True}},
        "outputs": {"value": {"type": "string"}},
        "permissions": ["exec:subprocess"],
        "runtime": runtime,
    }


def test_exec_permission_gate_rejects_undeclared() -> None:
    """exec 插件未声明 exec:subprocess → 权限门拒（require_exec_permission）。"""
    from musespiral.core.plugins.manifest import parse_manifest

    manifest = _exec_manifest("echo-exec", "cat")
    manifest.pop("permissions")
    plugin, errs = parse_manifest(manifest)
    assert plugin is not None and errs == []
    with pytest.raises(er.ExecPermissionError, match="exec:subprocess"):
        er.require_exec_permission(plugin)
    # python runtime 不适用本门
    manifest["runtime"] = {"type": "python", "entry": "plugin.py:run"}
    py, _ = parse_manifest(manifest)
    er.require_exec_permission(py)  # 不抛


def test_exec_resolve_command_bare_and_path(plugins_root: Path) -> None:
    """entry 裸命令走 which；带路径相对 plugin_dir 解析；空 entry 可读拒。"""
    argv = er.resolve_exec_command("cat", plugins_root)
    assert argv and os.path.basename(argv[0]) == "cat"
    d = plugins_root / "x"
    d.mkdir(parents=True)
    argv2 = er.resolve_exec_command("./echo.py a", d)
    assert argv2 == [str(d / "echo.py"), "a"]
    with pytest.raises(er.ExecProtocolError):
        er.resolve_exec_command("   ", d)


def test_exec_echo_roundtrip_direct(plugins_root: Path) -> None:
    """cat stdin→stdout 原样往返：inputs dict → 同 dict（协议硬要求 18 的直测）。"""
    plugins_root.mkdir(parents=True, exist_ok=True)  # cwd 须存在
    plugin = _exec_manifest("echo-exec", "cat")
    out = er.run_exec_plugin(plugin, plugins_root, {"value": "hello-世界", "n": 3})
    assert out == {"value": "hello-世界", "n": 3}


def test_exec_echo_roundtrip_via_engine(plugins_root: Path, runs_root: Path) -> None:
    """经引擎 run_pipeline 的 exec echo：source(cat) → python sink，JSON 往返 +
    输出契约校验 + workspace 落盘全部走通（硬要求 18）。"""
    d = _write_manifest(plugins_root, "echo-exec", _exec_manifest("echo-exec", "cat"))
    assert d.is_dir()
    sink_manifest = {
        "id": "echo-sink", "version": "1.0.0", "kind": "sink",
        "inputs": {"value": {"type": "string", "required": True}},
        "outputs": {"article": {"type": "text/markdown"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }
    sd = _write_manifest(plugins_root, "echo-sink", sink_manifest)
    (sd / "plugin.py").write_text(
        "def run(inputs, config, ctx):\n    return {'article': 'echo:' + inputs['value']}\n",
        encoding="utf-8",
    )
    result = psp.run_pipeline(
        [
            {"id": "echo", "plugin": "echo-exec", "inputs": {"value": "round-trip"}},
            {"id": "sink", "plugin": "echo-sink"},
        ],
        plugins_root=plugins_root, run_root=runs_root, run_id="exec-echo-0001",
    )
    assert result["status"] == "done", result
    assert [s["step"] for s in result["steps"]] == ["echo", "sink"]
    article = json.loads(
        (runs_root / "exec-echo-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert article == "echo:round-trip"


def test_exec_timeout_kills_process_group(plugins_root: Path, runs_root: Path, tmp_path: Path) -> None:
    """sleep 脚本 + timeout=1s（硬要求 19）：可读超时报错 + 强杀整组（marker 未落 + 耗时短）。"""
    marker = tmp_path / "should-not-exist"
    manifest = _exec_manifest("slow-exec", "./slow.sh", timeout=1)
    d = _write_manifest(plugins_root, "slow-exec", manifest)
    _write_exec_script(d, "slow.sh",
        "#!/bin/sh\n(sleep 30 &\n wait)\nprintf done > '" + str(marker) + "'\n")
    started = time.monotonic()
    with pytest.raises(er.ExecTimeoutError, match="超时"):
        er.run_exec_plugin(manifest, d, {"value": "x"})
    elapsed = time.monotonic() - started
    assert elapsed < 8, f"超时未在合理时间内强杀（耗时 {elapsed:.1f}s）"
    assert not marker.exists(), "超时后孙进程仍在跑并写了 marker——进程组未被杀净"


def test_exec_timeout_via_engine_is_readable_step(plugins_root: Path, runs_root: Path, tmp_path: Path) -> None:
    """超时经引擎收敛为 failed step（不抛穿主进程），失败不收集产物。"""
    manifest = _exec_manifest("slow-exec", "./slow.sh", timeout=1)
    d = _write_manifest(plugins_root, "slow-exec", manifest)
    _write_exec_script(d, "slow.sh", "#!/bin/sh\nsleep 30\n")
    result = psp.run_pipeline(
        [{"id": "slow", "plugin": "slow-exec", "inputs": {"value": "x"}}],
        plugins_root=plugins_root, run_root=runs_root, run_id="exec-timeout-0001",
    )
    assert result["status"] == "failed" and result["failed_at"] == "slow"
    assert "超时" in result["steps"][0]["detail"]
    assert not (runs_root / "exec-timeout-0001" / "output").exists()


def test_exec_nonzero_exit_and_bad_stdout_readable(plugins_root: Path) -> None:
    """子进程非零退出 / stdout 非 JSON / stdout 非 dict 都收敛为可读失败。"""
    bad = _exec_manifest("bad-exec", "./bad.sh")
    d = _write_manifest(plugins_root, "bad-exec", bad)
    _write_exec_script(d, "bad.sh", "#!/bin/sh\necho boom >&2\nexit 7\n")
    with pytest.raises(er.ExecRuntimeError, match="退出码 7") as ei:
        er.run_exec_plugin(bad, d, {"value": "x"})
    assert "boom" in str(ei.value)  # stderr 尾行进错误文案

    nonjson = _exec_manifest("nj-exec", "./nj.sh")
    dn = _write_manifest(plugins_root, "nj-exec", nonjson)
    _write_exec_script(dn, "nj.sh", "#!/bin/sh\necho not-json\n")
    with pytest.raises(er.ExecProtocolError, match="非合法 JSON"):
        er.run_exec_plugin(nonjson, dn, {"value": "x"})

    nondict = _exec_manifest("nd-exec", "./nd.sh")
    dd = _write_manifest(plugins_root, "nd-exec", nondict)
    _write_exec_script(dd, "nd.sh", "#!/bin/sh\necho '[1,2,3]'\n")
    with pytest.raises(er.ExecProtocolError, match="须为对象"):
        er.run_exec_plugin(nondict, dd, {"value": "x"})


def test_exec_stderr_passthrough_to_log(plugins_root: Path, runs_root: Path) -> None:
    """stderr 逐行透传到运行日志（on_log）。"""
    manifest = _exec_manifest("chatty-exec", "./chatty.sh")
    d = _write_manifest(plugins_root, "chatty-exec", manifest)
    _write_exec_script(d, "chatty.sh",
        "#!/bin/sh\necho 'warn-line' >&2\nprintf '{\"value\": \"ok\"}'\n")
    logs: list[str] = []
    out = er.run_exec_plugin(manifest, d, {"value": "x"}, on_log=logs.append)
    assert out == {"value": "ok"}
    assert any("warn-line" in line for line in logs)


def test_exec_plugin_without_permission_rejected_at_plan(plugins_root: Path) -> None:
    """exec 插件无 exec:subprocess → plan 期构造期红（零 run 目录）。"""
    manifest = _exec_manifest("noperm-exec", "cat")
    manifest.pop("permissions")
    _write_manifest(plugins_root, "noperm-exec", manifest)
    with pytest.raises(psp.PipelinePlanError, match="exec:subprocess"):
        psp.plan_pipeline([{"id": "n", "plugin": "noperm-exec"}], plugins_root=plugins_root)


# ════════════════════════════════════════════════════════════════════
# ④ 版本协商
# ════════════════════════════════════════════════════════════════════


def test_hash_parity_core_infra() -> None:
    """core.manifest_hash 与 infra.manifest_content_hash 同算法（对拍防漂移）。"""
    for data in ({}, {"a": 1}, {"b": 2, "a": {"z": 1, "y": 2}}, {"id": "x", "版本": "中文"}):
        assert vcore.manifest_hash(data) == loader.manifest_content_hash(data)


def test_save_writes_version_hash_and_detail_clean(
    _isolate_home: Path, plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存落 plugin_version_hash（按 node_id）；未变更加载 → 零警告（硬要求 20 绿路径）。"""
    monkeypatch.setattr(loader, "builtin_plugins_dir", lambda: plugins_root.parent / "no-builtin")
    monkeypatch.setenv("MUSESPIRAL_HOME", str(_isolate_home))
    # 用真实 home 的 plugins 区（服务默认扫 config.home_dir()/plugins）
    home_plugins = _isolate_home / "plugins"
    src = {
        "id": "demo-source", "version": "1.0.0", "kind": "source",
        "inputs": {}, "outputs": {"x": {"type": "string"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }
    d = _write_manifest(home_plugins, "demo-source", src)
    (d / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {'x': 'v'}\n", encoding="utf-8")

    ctx = _StudioContext(root=_isolate_home)
    saved = pipeline_crud._action_pipeline_save(
        ctx, {"id": "pipe1", "nodes": [{"id": "n1", "plugin": dict(src)}], "confirm": True}
    )
    assert saved["ok"] is True
    raw = json.loads((_isolate_home / "pipelines" / "pipe1.json").read_text("utf-8"))
    assert raw["nodes"] == [{"id": "n1", "plugin": "demo-source"}]  # 引擎形状零回归
    assert raw[HASH_KEY] == {"n1": _normalized_hash(src)}

    detail = list_detail._pipeline_detail_payload(ctx, "pipe1")
    assert detail["warnings"] == []


def test_version_warning_after_manifest_change(
    _isolate_home: Path, plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改 manifest 后加载 → 可读警告出现（硬要求 20），但不阻塞（nodes 正常返回）。"""
    monkeypatch.setattr(loader, "builtin_plugins_dir", lambda: plugins_root.parent / "no-builtin")
    home_plugins = _isolate_home / "plugins"
    src = {
        "id": "demo-source", "version": "1.0.0", "kind": "source",
        "inputs": {}, "outputs": {"x": {"type": "string"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }
    d = _write_manifest(home_plugins, "demo-source", src)
    (d / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {'x': 'v'}\n", encoding="utf-8")
    ctx = _StudioContext(root=_isolate_home)
    pipeline_crud._action_pipeline_save(
        ctx, {"id": "pipe2", "nodes": [{"id": "n1", "plugin": dict(src)}], "confirm": True}
    )

    # 模拟插件升级：改 version → manifest hash 变（指纹=内容 sha256 后，
    # mtime 是否变化已不影响缓存判定——2026-11-22 复核发现 1 修订）
    upgraded = dict(src, version="2.0.0")
    (d / "manifest.json").write_text(json.dumps(upgraded, ensure_ascii=False), encoding="utf-8")
    detail = list_detail._pipeline_detail_payload(ctx, "pipe2")
    assert detail["warnings"], "改 manifest 后加载应给可读警告"
    w = detail["warnings"][0]
    assert "n1" in w and "demo-source" in w and "已升级" in w and "建议验证" in w
    assert detail["nodes"][0]["plugin"]["id"] == "demo-source"  # 不阻塞，节点正常水化


def test_version_warning_survives_mtime_preserving_upgrade(
    _isolate_home: Path, plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核发现 1 全链锚（2026-11-22）：等长改写 + os.utime 保 mtime 的升级
    也要出警告——v1 mtime:size 指纹下缓存喂旧 manifest，此场景警告静默丢失
    （磁盘 manifest 已 2.0.0、detail 仍水化 1.0.0 且 warnings==[]，实测复现）。"""
    import os as _os

    monkeypatch.setattr(loader, "builtin_plugins_dir", lambda: plugins_root.parent / "no-builtin")
    home_plugins = _isolate_home / "plugins"
    src = {
        "id": "demo-source", "version": "1.0.0", "kind": "source",
        "inputs": {}, "outputs": {"x": {"type": "string"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }
    d = _write_manifest(home_plugins, "demo-source", src)
    (d / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {'x': 'v'}\n", encoding="utf-8")
    ctx = _StudioContext(root=_isolate_home)
    pipeline_crud._action_pipeline_save(
        ctx, {"id": "pipe3", "nodes": [{"id": "n1", "plugin": dict(src)}], "confirm": True}
    )

    mp = d / "manifest.json"
    st = mp.stat()
    upgraded = dict(src, version="2.0.0")  # "1.0.0"→"2.0.0" 同字符数：size 指纹必盲
    mp.write_text(json.dumps(upgraded, ensure_ascii=False), encoding="utf-8")
    _os.utime(mp, ns=(st.st_atime_ns, st.st_mtime_ns))  # 还原时间戳：mtime 指纹也盲

    detail = list_detail._pipeline_detail_payload(ctx, "pipe3")
    assert detail["warnings"], "保 mtime 升级也必须出已升级警告"
    assert detail["nodes"][0]["plugin"]["version"] == "2.0.0", "水化 manifest 必须取磁盘真值"


def test_version_warnings_unit_paths() -> None:
    """version_warnings 纯函数各路径：老定义/卸载/升级/多节点。"""
    old = {"id": "p", "version": "1.0.0", "kind": "source", "inputs": {}, "outputs": {},
           "runtime": {"type": "python", "entry": "plugin.py:run"}}
    h = manifest_hash(old)
    nodes = [{"id": "a", "plugin": "p"}]
    # 老定义：无 saved_hashes → 零警告
    assert version_warnings(nodes, {"p": old}, None) == []
    # 未变
    assert version_warnings(nodes, {"p": old}, {"a": h}) == []
    # 升级
    w = version_warnings(nodes, {"p": dict(old, version="2.0.0")}, {"a": h})
    assert len(w) == 1 and "已升级" in w[0]
    # 卸载
    w = version_warnings(nodes, {}, {"a": h})
    assert len(w) == 1 and "已卸载" in w[0]
    # 手工编辑：saved 节点 id 不在 nodes → 跳过
    assert version_warnings(nodes, {}, {"ghost": h}) == []


# ════════════════════════════════════════════════════════════════════
# ⑤ 插件发现索引
# ════════════════════════════════════════════════════════════════════


def _plugin_manifest(pid: str) -> dict:
    return {
        "id": pid, "version": "1.0.0", "kind": "source",
        "inputs": {}, "outputs": {"x": {"type": "string"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }


def test_index_created_and_incremental_reuse(plugins_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首次扫描建 index.json（id/version/hash/timestamp/data）；重扫命中缓存零解析。

    （指纹=内容 sha256 后，命中路径仍读文件字节算指纹——省的是 read_manifest
    的解析+校验；read_manifest 不被调即证缓存生效。）"""
    _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    _write_manifest(plugins_root, "beta", _plugin_manifest("beta"))

    discovered, skipped, index = loader.scan_manifests_incremental(plugins_root)
    assert skipped == [] and len(discovered) == 2
    assert (plugins_root / "index.json").is_file()
    assert set(index["entries"]) == {"alpha", "beta"}
    e = index["entries"]["alpha"]
    assert e["id"] == "alpha" and e["version"] == "1.0.0"
    assert e["hash"] == loader.manifest_content_hash(e["data"])
    assert e["timestamp"] and e["fingerprint"]

    # 二扫命中：monkeypatch read_manifest 抛错——若真读了就炸，证明缓存生效
    def _boom(_path):
        raise AssertionError("缓存命中却重读了 manifest")

    monkeypatch.setattr(loader, "read_manifest", _boom)
    discovered2, _, index2 = loader.scan_manifests_incremental(plugins_root)
    assert len(discovered2) == 2
    assert index2["entries"]["alpha"]["timestamp"] == e["timestamp"]  # 未变条目不刷新时间戳


def test_index_fingerprint_catches_mtime_preserving_change(plugins_root: Path) -> None:
    """保 mtime 保 size 的内容改写必须被指纹识破（2026-11-22 复核发现 1 红转绿锚）。

    v1 指纹=mtime_ns:size 判例：等长内容原地改写 + os.utime 还原时间戳
    （git checkout / rsync -t / tar -p 同款形态）→ 缓存命中喂旧 manifest，
    version_warnings 比到旧 hash 上，升级警告静默丢失。指纹改为内容 sha256
    后：命中=内容逐字节一致（跳过的是解析校验，不是读取）。
    """
    _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    discovered, _, index = loader.scan_manifests_incremental(plugins_root)
    assert discovered[0]["data"]["version"] == "1.0.0"

    mp = plugins_root / "alpha" / "manifest.json"
    st = mp.stat()
    old_len = len(mp.read_bytes())
    upgraded = dict(_plugin_manifest("alpha"), version="2.0.0")
    payload = json.dumps(upgraded, ensure_ascii=False)
    assert len(payload.encode("utf-8")) == old_len  # "1.0.0"→"2.0.0" 等字符数：size 指纹必盲
    mp.write_text(payload, encoding="utf-8")
    import os as _os

    _os.utime(mp, ns=(st.st_atime_ns, st.st_mtime_ns))  # 还原时间戳：mtime/size 口径完全不变
    assert loader._manifest_fingerprint(mp) != index["entries"]["alpha"]["fingerprint"]  # 内容指纹必须识破

    discovered2, _, index2 = loader.scan_manifests_incremental(plugins_root)
    assert discovered2[0]["data"]["version"] == "2.0.0", "缓存命中不得喂旧 manifest"
    assert index2["entries"]["alpha"]["version"] == "2.0.0"
    assert index2["entries"]["alpha"]["hash"] != index["entries"]["alpha"]["hash"]


def test_index_incremental_only_changed_entry(plugins_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """改一个插件 manifest → 只重读该条、只更新该条 hash；其余复用缓存。"""
    _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    _write_manifest(plugins_root, "beta", _plugin_manifest("beta"))
    loader.scan_manifests_incremental(plugins_root)

    real_read = loader.read_manifest
    calls: list[str] = []

    def _counting(path):
        calls.append(Path(path).parent.name)
        return real_read(path)

    monkeypatch.setattr(loader, "read_manifest", _counting)
    # 升级 beta（mtime + 内容都变）
    upgraded = dict(_plugin_manifest("beta"), version="2.0.0")
    (plugins_root / "beta" / "manifest.json").write_text(json.dumps(upgraded), encoding="utf-8")
    _, _, index = loader.scan_manifests_incremental(plugins_root)
    assert calls == ["beta"], f"应只读变更的 beta，实际读了 {calls}"
    assert index["entries"]["beta"]["version"] == "2.0.0"
    assert index["entries"]["alpha"]["version"] == "1.0.0"


def test_index_removes_stale_entries(plugins_root: Path) -> None:
    """删除插件目录 → 索引清条。"""
    _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    _write_manifest(plugins_root, "beta", _plugin_manifest("beta"))
    loader.scan_manifests_incremental(plugins_root)
    shutil.rmtree(plugins_root / "beta")
    _, _, index = loader.scan_manifests_incremental(plugins_root)
    assert set(index["entries"]) == {"alpha"}


def test_index_corrupt_falls_back_full_scan(plugins_root: Path) -> None:
    """index.json 坏 JSON → 降级全量重扫，不炸（缓存是加速非真相）。"""
    _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    (plugins_root / "index.json").write_text("{broken", encoding="utf-8")
    discovered, skipped, index = loader.scan_manifests_incremental(plugins_root)
    assert len(discovered) == 1 and skipped == []
    assert set(index["entries"]) == {"alpha"}


def test_service_explicit_root_no_index_pollution(plugins_root: Path) -> None:
    """服务层显式 root（测试夹具/工具自定）**不写 index.json**——缓存只属于
    用户安装区 ~/<home>/plugins/，不往夹具/包内目录落文件污染源码树。"""
    from musespiral.services import plugins_service as svc

    ad = _write_manifest(plugins_root, "alpha", _plugin_manifest("alpha"))
    (ad / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {}\n", encoding="utf-8")
    bad = plugins_root / "broken"
    bad.mkdir()
    (bad / "manifest.json").write_text("{bad", encoding="utf-8")
    out = svc.list_plugins(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["alpha"]
    assert len(out["skipped"]) == 1
    assert not (plugins_root / "index.json").exists()  # 不污染显式 root
