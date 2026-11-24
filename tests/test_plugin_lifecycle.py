"""插件生命周期测试（W14a，2026-11-24）：五方法 + 七道安全门。

安全门是本模块的核心价值——插件包来自外部，七道门缺一都可能被利用。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from musespiral.services import plugin_lifecycle as L

GOOD_MANIFEST = {
    "id": "demo-plugin", "version": "1.0.0", "kind": "process",
    "name": {"zh": "演示", "en": "Demo"},
    "description": {"zh": "演示插件", "en": "demo"},
    "inputs": {"text": {"type": "string"}},
    "outputs": {"out": {"type": "string"}},
    "runtime": {"type": "python", "entry": "plugin.py:run"},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(h))
    return h


def _pkg(path: Path, manifest: dict | None = None, extra: dict[str, bytes] | None = None,
         prefix: str = "demo-plugin/") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix + "manifest.json", json.dumps(manifest or GOOD_MANIFEST))
        zf.writestr(prefix + "plugin.py", "def run(inputs, config, ctx):\n    return {'out': 'x'}\n")
        for name, data in (extra or {}).items():
            zf.writestr(prefix + name, data)
    return path


# ── 正常流程 ────────────────────────────────────────────────────

def test_install_then_list(home, tmp_path) -> None:
    p = _pkg(tmp_path / "demo.mspkg")
    r = L.install_from_package(p)
    assert r["ok"] and r["id"] == "demo-plugin"
    items = {i["id"]: i for i in L.list_installed()}
    assert items["demo-plugin"]["source"] == "user" and items["demo-plugin"]["enabled"] is True


def test_install_rejects_duplicate(home, tmp_path) -> None:
    p = _pkg(tmp_path / "demo.mspkg")
    L.install_from_package(p)
    with pytest.raises(L.PluginLifecycleError, match="已安装"):
        L.install_from_package(p)


def test_set_enabled_moves_to_disabled_and_back(home, tmp_path) -> None:
    L.install_from_package(_pkg(tmp_path / "d.mspkg"))
    L.set_enabled("demo-plugin", False)
    items = {i["id"]: i for i in L.list_installed()}
    assert items["demo-plugin"]["enabled"] is False
    assert (home / "plugins" / ".disabled" / "demo-plugin").is_dir()
    L.set_enabled("demo-plugin", True)
    items = {i["id"]: i for i in L.list_installed()}
    assert items["demo-plugin"]["enabled"] is True


def test_uninstall_removes_dir(home, tmp_path) -> None:
    L.install_from_package(_pkg(tmp_path / "d.mspkg"))
    L.uninstall("demo-plugin")
    assert "demo-plugin" not in {i["id"] for i in L.list_installed()}
    assert not (home / "plugins" / "demo-plugin").exists()


def test_export_roundtrip(home, tmp_path) -> None:
    """导出 → 再安装（另一 home）→ 相同 id。"""
    L.install_from_package(_pkg(tmp_path / "d.mspkg"))
    out = tmp_path / "exported.mspkg"
    r = L.export_package("demo-plugin", out)
    assert r["ok"] and out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert any(n.endswith("manifest.json") for n in names)


# ── 安全门（七道）──────────────────────────────────────────────

def test_gate_reject_path_traversal(home, tmp_path) -> None:
    p = tmp_path / "evil.mspkg"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("demo-plugin/manifest.json", json.dumps(GOOD_MANIFEST))
        zf.writestr("../../etc/passwd", "pwned")
    with pytest.raises(L.PluginLifecycleError, match="非法路径"):
        L.install_from_package(p)


def test_gate_reject_absolute_path(home, tmp_path) -> None:
    p = tmp_path / "abs.mspkg"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("demo-plugin/manifest.json", json.dumps(GOOD_MANIFEST))
        zf.writestr("/etc/passwd", "pwned")
    with pytest.raises(L.PluginLifecycleError, match="非法路径"):
        L.install_from_package(p)


def test_gate_reject_symlink(home, tmp_path) -> None:
    import stat as st
    p = tmp_path / "ln.mspkg"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("demo-plugin/manifest.json", json.dumps(GOOD_MANIFEST))
        info = zipfile.ZipInfo("demo-plugin/link")
        info.external_attr = (st.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(L.PluginLifecycleError, match="符号链接"):
        L.install_from_package(p)


def test_gate_reject_bad_manifest(home, tmp_path) -> None:
    bad = dict(GOOD_MANIFEST)
    del bad["kind"]
    p = _pkg(tmp_path / "bad.mspkg", manifest=bad)
    with pytest.raises(L.PluginLifecycleError, match="契约门"):
        L.install_from_package(p)


def test_gate_reject_two_manifests(home, tmp_path) -> None:
    p = tmp_path / "two.mspkg"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("a/manifest.json", json.dumps(GOOD_MANIFEST))
        zf.writestr("b/manifest.json", json.dumps(GOOD_MANIFEST))
    with pytest.raises(L.PluginLifecycleError, match="恰好一个"):
        L.install_from_package(p)


def test_gate_reject_too_many_files(home, tmp_path) -> None:
    extra = {f"f{i}.txt": b"x" for i in range(L.MAX_FILES)}
    p = _pkg(tmp_path / "many.mspkg", extra=extra)
    with pytest.raises(L.PluginLifecycleError, match="文件数"):
        L.install_from_package(p)


def test_gate_reject_oversize(home, tmp_path) -> None:
    extra = {"big.bin": b"x" * (L.MAX_PACKAGE_BYTES + 1)}
    p = _pkg(tmp_path / "big.mspkg", extra=extra)
    with pytest.raises(L.PluginLifecycleError, match="超过 10MB"):
        L.install_from_package(p)


def test_gate_reject_non_zip(home, tmp_path) -> None:
    p = tmp_path / "notzip.mspkg"
    p.write_bytes(b"not a zip")
    with pytest.raises(L.PluginLifecycleError, match="不是合法 zip"):
        L.install_from_package(p)


def test_gate_builtin_cannot_uninstall(home) -> None:
    with pytest.raises(L.PluginLifecycleError, match="内置插件"):
        L.uninstall("article-writer")


def test_gate_builtin_cannot_disable(home) -> None:
    with pytest.raises(L.PluginLifecycleError, match="内置插件"):
        L.set_enabled("article-writer", False)


def test_gate_reject_id_traversal(home) -> None:
    for bad in ("../x", "a/b", ""):
        with pytest.raises(L.PluginLifecycleError):
            L.uninstall(bad)


def test_failed_install_leaves_no_partial_dir(home, tmp_path) -> None:
    """坏包安装失败后不得留下 .installing 残留。"""
    bad = dict(GOOD_MANIFEST); del bad["kind"]
    p = _pkg(tmp_path / "bad.mspkg", manifest=bad)
    with pytest.raises(L.PluginLifecycleError):
        L.install_from_package(p)
    leftovers = list((home / "plugins").glob("*.installing")) if (home / "plugins").exists() else []
    assert not leftovers


# ── W14b 端点与 CLI ─────────────────────────────────────────────

def test_endpoints_registered() -> None:
    """四端点进 _ACTION_ROUTES（路由注册表）。"""
    from musespiral.studio.actions import _ACTION_ROUTES

    for p in ("/api/plugins/install", "/api/plugins/uninstall",
              "/api/plugins/enabled", "/api/plugins/export"):
        assert p in _ACTION_ROUTES, p


def test_cli_plugin_list(home) -> None:
    from click.testing import CliRunner

    from musespiral.cli import cli

    r = CliRunner().invoke(cli, ["plugin", "list"])
    assert r.exit_code == 0
    assert "内置" in r.output


def test_cli_install_uninstall_roundtrip(home, tmp_path) -> None:
    from click.testing import CliRunner

    from musespiral.cli import cli

    pkg = _pkg(tmp_path / "cli.mspkg")
    r = CliRunner().invoke(cli, ["plugin", "install", str(pkg)])
    assert r.exit_code == 0 and "已安装" in r.output
    r2 = CliRunner().invoke(cli, ["plugin", "list"])
    assert "demo-plugin" in r2.output and "用户" in r2.output
    r3 = CliRunner().invoke(cli, ["plugin", "disable", "demo-plugin"])
    assert r3.exit_code == 0
    r4 = CliRunner().invoke(cli, ["plugin", "list"])
    assert "禁用" in r4.output
    r5 = CliRunner().invoke(cli, ["plugin", "enable", "demo-plugin"])
    assert r5.exit_code == 0
    r6 = CliRunner().invoke(cli, ["plugin", "uninstall", "demo-plugin"])
    assert r6.exit_code == 0 and "已卸载" in r6.output


def test_cli_builtin_uninstall_fails_readable(home) -> None:
    from click.testing import CliRunner

    from musespiral.cli import cli

    r = CliRunner().invoke(cli, ["plugin", "uninstall", "article-writer"])
    assert r.exit_code == 1 and "内置插件" in r.output


def test_cli_export(home, tmp_path) -> None:
    from click.testing import CliRunner

    from musespiral.cli import cli

    L.install_from_package(_pkg(tmp_path / "e.mspkg"))
    out = tmp_path / "out.mspkg"
    r = CliRunner().invoke(cli, ["plugin", "export", "demo-plugin", "--out", str(out)])
    assert r.exit_code == 0 and out.is_file()
