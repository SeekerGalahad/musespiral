"""插件加载器与服务层测试（插件系统设计 PL-1 校验要求 1/4/7-9/12-13）。

覆盖：插件区目录不存在返回空不报错 / 3 插件 1 坏 manifest 其余照常加载
（坏 JSON 在 scan 层隔离、坏契约在 service 层隔离）/ 目录名≠id 拒装 /
runtime 入口文件不存在拒装 / check_deps shutil.which 探测 / list/get/
check 编排往返。全部走 tmp_path 显式 root，不触真实 ~/.musespiral。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from musespiral.core.plugins.manifest import Plugin, parse_manifest
from musespiral.infra import plugins_loader as loader
from musespiral.services import plugins_service as svc

# ── 夹具：在 tmp 造插件目录 ────────────────────────────────────────


def _source_manifest() -> dict:
    return {
        "id": "local-file",
        "version": "1.0.0",
        "kind": "source",
        "inputs": {"path": {"type": "string", "required": True}},
        "outputs": {"media": {"type": "file/path"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
        "requires": [sys.executable, "musespiral-no-such-cmd-0f3a"],
    }


def _transcribe_manifest() -> dict:
    return {
        "id": "transcribe",
        "version": "1.2.0",
        "kind": "process",
        "inputs": {"media_path": {"type": "file/path", "required": True}},
        "outputs": {"transcript": {"type": "segments"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }


def _write_plugin(root: Path, dirname: str, manifest: dict, *, with_entry: bool = True) -> Path:
    d = root / dirname
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if with_entry and manifest.get("runtime", {}).get("type") == "python":
        (d / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {}\n", encoding="utf-8")
    return d


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


# ── read_manifest：单文件读取红绿双路径（永不抛）─────────────────


def test_read_manifest_green(plugins_root: Path) -> None:
    d = plugins_root / "transcribe"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(_transcribe_manifest()), encoding="utf-8")
    data, reason = loader.read_manifest(d / "manifest.json")
    assert reason is None and data is not None
    assert data["id"] == "transcribe"


def test_read_manifest_red_paths(plugins_root: Path) -> None:
    d = plugins_root / "broken"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{not json", encoding="utf-8")
    data, reason = loader.read_manifest(d / "manifest.json")
    assert data is None and "解析失败" in reason

    (d / "manifest.json").write_text("[]", encoding="utf-8")
    data, reason = loader.read_manifest(d / "manifest.json")
    assert data is None and "JSON 对象" in reason

    data, reason = loader.read_manifest(d / "no-such-file.json")
    assert data is None and reason


# ── 目录不存在（校验要求 7/13）────────────────────────────────────


def test_missing_dir_scan_returns_empty(plugins_root: Path) -> None:
    discovered, skipped = loader.scan_manifests(plugins_root)
    assert discovered == [] and skipped == []


def test_missing_dir_service_returns_empty(plugins_root: Path) -> None:
    out = svc.list_plugins(plugins_root)
    assert out == {"plugins": [], "skipped": []}
    assert svc.get_plugin("local-file", plugins_root) is None
    assert svc.check_plugin("local-file", plugins_root) is None


def test_empty_dir_scan_returns_empty(plugins_root: Path) -> None:
    plugins_root.mkdir(parents=True)
    discovered, skipped = loader.scan_manifests(plugins_root)
    assert discovered == [] and skipped == []


# ── 加载容错：坏 manifest 不影响其他（校验要求 8/12）──────────────


def test_three_plugins_one_broken_json(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "local-file", _source_manifest())
    _write_plugin(plugins_root, "transcribe", _transcribe_manifest())
    broken = plugins_root / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    discovered, skipped = loader.scan_manifests(plugins_root)
    assert len(discovered) == 2
    assert len(skipped) == 1 and "解析失败" in skipped[0]["reason"]

    out = svc.list_plugins(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["local-file", "transcribe"]
    # scan 层隔离的坏目录同样聚合进 service 的 skipped（两层都可见）
    assert len(out["skipped"]) == 1 and "解析失败" in out["skipped"][0]["reason"]


def test_bad_contract_manifest_isolated(plugins_root: Path) -> None:
    """合法 JSON 但契约红（kind 非法）→ service 层隔离进 skipped。"""
    _write_plugin(plugins_root, "local-file", _source_manifest())
    bad = {
        "id": "bad-kind",
        "version": "1.0.0",
        "kind": "transform",
        "inputs": {},
        "outputs": {},
        "runtime": {"type": "exec", "entry": "run.sh"},
    }
    _write_plugin(plugins_root, "bad-kind", bad, with_entry=False)

    discovered, _ = loader.scan_manifests(plugins_root)
    assert len(discovered) == 2  # scan 层只保证 JSON 可读

    out = svc.list_plugins(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["local-file"]
    assert len(out["skipped"]) == 1 and "kind" in out["skipped"][0]["reason"]


def test_missing_manifest_file_skipped(plugins_root: Path) -> None:
    (plugins_root / "no-manifest").mkdir(parents=True)
    _write_plugin(plugins_root, "local-file", _source_manifest())
    _, skipped = loader.scan_manifests(plugins_root)
    assert len(skipped) == 1 and "缺少 manifest.json" in skipped[0]["reason"]


def test_non_dict_manifest_skipped(plugins_root: Path) -> None:
    (plugins_root / "array-manifest").mkdir(parents=True)
    (plugins_root / "array-manifest" / "manifest.json").write_text("[]", encoding="utf-8")
    _, skipped = loader.scan_manifests(plugins_root)
    assert len(skipped) == 1 and "JSON 对象" in skipped[0]["reason"]


def test_hidden_and_file_entries_ignored(plugins_root: Path) -> None:
    plugins_root.mkdir(parents=True)
    (plugins_root / ".DS_Store").write_text("x", encoding="utf-8")
    (plugins_root / ".stfolder").mkdir(parents=True)
    _write_plugin(plugins_root, "local-file", _source_manifest())
    out = svc.list_plugins(plugins_root)
    assert len(out["plugins"]) == 1 and out["skipped"] == []


# ── 目录名与 runtime 入口（校验要求 1/4）──────────────────────────


def test_dirname_must_equal_flattened_id(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "wrong-name", _source_manifest())
    out = svc.list_plugins(plugins_root)
    assert out["plugins"] == []
    assert len(out["skipped"]) == 1 and "目录名与 id 不符" in out["skipped"][0]["reason"]


def test_vendor_id_dirname_flattened_green(plugins_root: Path) -> None:
    manifest = _source_manifest() | {"id": "acme/local-file"}
    _write_plugin(plugins_root, "acme_local-file", manifest)
    out = svc.list_plugins(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["acme/local-file"]
    assert out["skipped"] == []


def test_python_entry_file_missing_red(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "local-file", _source_manifest(), with_entry=False)
    out = svc.list_plugins(plugins_root)
    assert out["plugins"] == []
    assert "runtime 入口不存在" in out["skipped"][0]["reason"]


def test_entry_file_ok_python(plugins_root: Path, tmp_path: Path) -> None:
    data = {"runtime": {"type": "python", "entry": "plugin.py:run"}}
    assert loader.entry_file_ok(data, tmp_path) is False
    (tmp_path / "plugin.py").write_text("x", encoding="utf-8")
    assert loader.entry_file_ok(data, tmp_path) is True


def test_entry_file_ok_exec(tmp_path: Path) -> None:
    bare = {"runtime": {"type": "exec", "entry": sys.executable}}
    assert loader.entry_file_ok(bare, tmp_path) is True
    assert loader.entry_file_ok(
        {"runtime": {"type": "exec", "entry": "musespiral-no-such-cmd-0f3a"}}, tmp_path
    ) is False
    rel = {"runtime": {"type": "exec", "entry": "tools/run.sh"}}
    assert loader.entry_file_ok(rel, tmp_path) is False
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "run.sh").write_text("x", encoding="utf-8")
    assert loader.entry_file_ok(rel, tmp_path) is True


# ── 依赖探测（校验要求 9）─────────────────────────────────────────


def test_check_deps_probe(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "local-file", _source_manifest())
    result = svc.check_plugin("local-file", plugins_root)
    assert result is not None
    assert result["id"] == "local-file"
    by_cmd = {d["cmd"]: d for d in result["deps"]}
    assert by_cmd[sys.executable]["found"] is True
    assert by_cmd["musespiral-no-such-cmd-0f3a"]["found"] is False
    assert by_cmd["musespiral-no-such-cmd-0f3a"]["path"] is None


def test_check_deps_accepts_model_and_dict() -> None:
    plugin, errors = parse_manifest(_transcribe_manifest() | {"requires": [sys.executable]})
    assert errors == [] and plugin is not None
    for subject in (plugin, plugin.model_dump()):
        deps = loader.check_deps(subject)
        assert len(deps) == 1 and deps[0]["found"] is True


def test_check_deps_requires_missing_or_dirty() -> None:
    assert loader.check_deps({"requires": None}) == []
    assert loader.check_deps({}) == []
    assert loader.check_deps({"requires": ["", 42, "ok"]})[0]["cmd"] == "ok"


def test_check_deps_mock_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock which：探测结果完全由 shutil.which 决定，不依赖宿主机装了什么。"""
    def fake_which(cmd: str | None):
        return "/opt/bin/ffmpeg" if cmd == "ffmpeg" else None

    monkeypatch.setattr("shutil.which", fake_which)
    deps = loader.check_deps({"requires": ["ffmpeg", "some-other-tool"]})
    assert deps == [
        {"cmd": "ffmpeg", "found": True, "path": "/opt/bin/ffmpeg"},
        {"cmd": "some-other-tool", "found": False, "path": None},
    ]


# ── 服务编排 list/get/check ───────────────────────────────────────


def test_list_get_roundtrip(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "transcribe", _transcribe_manifest())
    out = svc.list_plugins(plugins_root)
    assert len(out["plugins"]) == 1
    manifest = out["plugins"][0]["manifest"]
    assert manifest["id"] == "transcribe" and manifest["kind"] == "process"
    assert manifest["outputs"]["transcript"]["type"] == "segments"

    got = svc.get_plugin("transcribe", plugins_root)
    assert got is not None and got["manifest"] == manifest
    assert svc.get_plugin("nope", plugins_root) is None


def test_list_sorted_by_id(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "zeta", _transcribe_manifest() | {"id": "zeta"})
    _write_plugin(plugins_root, "alpha", _source_manifest() | {"id": "alpha"})
    out = svc.list_plugins(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["alpha", "zeta"]


def test_ext_plugin_end_to_end(plugins_root: Path) -> None:
    """ext/<name> 端口类型经 loader+service 全链路绿（扩展命名空间贯通）。"""
    manifest = {
        "id": "embedder",
        "version": "0.1.0",
        "kind": "process",
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"vector": {"type": "ext/vector"}},
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }
    _write_plugin(plugins_root, "embedder", manifest)
    out = svc.list_plugins(plugins_root)
    assert out["plugins"][0]["manifest"]["outputs"]["vector"]["type"] == "ext/vector"
    got = svc.get_plugin("embedder", plugins_root)
    assert got is not None
    plugin = Plugin.model_validate(got["manifest"])
    assert plugin.outputs["vector"].type == "ext/vector"


# ── scan_plugins tuple 形 / refresh 重扫（正典 §十二）─────────────


def test_scan_plugins_tuple_shape(plugins_root: Path) -> None:
    _write_plugin(plugins_root, "local-file", _source_manifest())
    _write_plugin(plugins_root, "transcribe", _transcribe_manifest())
    broken = plugins_root / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    valid, skipped = svc.scan_plugins(plugins_root)
    assert isinstance(valid, list) and isinstance(skipped, list)
    assert [p["manifest"]["id"] for p in valid] == ["local-file", "transcribe"]
    assert len(skipped) == 1 and "解析失败" in skipped[0]["reason"]

    valid, skipped = svc.scan_plugins(plugins_root / "no-such-root")
    assert valid == [] and skipped == []


def test_refresh_rescans_pick_up_new_plugin(plugins_root: Path) -> None:
    """v1 无缓存：refresh 每次真扫——新放入的目录立即可见（§十二语义）。"""
    _write_plugin(plugins_root, "transcribe", _transcribe_manifest())
    out = svc.refresh(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["transcribe"]

    _write_plugin(plugins_root, "local-file", _source_manifest())
    out = svc.refresh(plugins_root)
    assert [p["manifest"]["id"] for p in out["plugins"]] == ["local-file", "transcribe"]
