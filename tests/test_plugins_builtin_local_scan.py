"""local-scan 插件测试（本地媒体线 2026-11-20 批）。

覆盖（本批测试硬要求 12-15）：
  a) manifest 契约：parse_manifest 绿、kind=source、inputs/outputs 形状、
     生产路径内置区自动发现（不破坏既有 skipped==[] 断言）；
  b) 入口三道门（安全边界核心）：空 roots 拒绝、`/` 根拒绝、不存在目录
     拒绝、pattern 出白名单拒绝（pattern 只能收窄不能扩大）；
  c) 排除与限额：node_modules/.git 排除（tmp 夹具 + **本机真实 repository/**
     造夹具双验证）、50001 条目触发硬限额停止并在清单说明；
  d) 隐私边界：root 命中 Library/Containers / Documents → ctx.log 隐私警告；
     **排除按绝对分量匹配，root 选择不可绕过**（复核判例 2026-11-20：
     root=~/Library 曾无提示带出微信私人媒体——root=Library/Library/Containers/
     微信容器三种位形全拦住，合法同级 Library/Music 不误伤）；
  e) 三种边界：空目录 / 不存在目录 / 无权限目录（静默计数不崩）；
  f) ffprobe 双态：真 ffprobe 探垃圾字节文件 → None 不炸；真 lavfi 合成
     mp4 → 真时长；_probe_many 并发保序；
  g) 输出契约：run() 恰好 files/count/manifest 三键（v1 file/path 单值
     不改契约），表格含路径且按大小降序。

符号链接不跟随、min_size 过滤、大小降序、探针脚本冒烟同批覆盖。
全部临时夹具自建自清，真机 repository/ 夹具 try/finally 必清。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from musespiral.core.plugins.context import PluginContext, PluginError
from musespiral.core.plugins.manifest import parse_manifest
from musespiral.infra import plugins_loader as loader
from musespiral.services import plugins_service as svc

BUILTIN_DIR = loader.builtin_plugins_dir()
PLUGIN_DIR = BUILTIN_DIR / "local-scan"
REPO_ROOT = Path(__file__).resolve().parents[3]  # tests→musespiral→modules→仓库根

# 本机真实媒体大目录（验收硬要求 11/12 的真机扫描面；缺席则跳过真机用例）
REPO_SCAN_ROOT = REPO_ROOT / "repository"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_builtin_local_scan", PLUGIN_DIR / "plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ls_mod() -> ModuleType:
    return _load_module()


class _FakeCtx(PluginContext):
    """最小 PluginContext（测试面）：logs 收集、无取消。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        self.logs.append(f"progress {done}/{total}")

    def tmpdir(self) -> Path:
        d = self._root / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def artifacts_dir(self) -> Path:
        d = self._root / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def ai(self) -> Any:
        raise PluginError("测试上下文不注入 AI 客户端")


# ── a) manifest 契约与内置发现 ─────────────────────────────────────


def test_manifest_parses_and_contract() -> None:
    data = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    plugin, errors = parse_manifest(data)
    assert errors == [] and plugin is not None
    assert plugin.kind == "source"
    assert plugin.inputs["roots"].required is True
    assert plugin.inputs["max_depth"].default == 3
    assert plugin.inputs["min_size_mb"].default == 1
    assert plugin.outputs["files"].type == "text/markdown"
    assert plugin.outputs["count"].type == "number"
    assert plugin.outputs["manifest"].type == "text/markdown"
    assert plugin.permissions == ["fs:read"]
    assert "ffprobe" in plugin.requires


def test_builtin_discovered_in_production_path() -> None:
    out = svc.list_plugins()  # 用户区不存在（沙箱 home）→ 只有内置区
    ids = {p["manifest"]["id"] for p in out["plugins"]}
    assert "local-scan" in ids and "local-file" in ids
    assert out["skipped"] == []  # 新插件 manifest 坏会让既有断言红，这里双保险


def test_suffix_whitelist_matches_local_file(ls_mod: ModuleType) -> None:
    """与 local-file 的白名单逐项一致（清单点选后进 local-file 不二次被拒）。"""
    lf_spec = importlib.util.spec_from_file_location(
        "test_builtin_local_file_const", BUILTIN_DIR / "local-file" / "plugin.py"
    )
    assert lf_spec is not None and lf_spec.loader is not None
    lf_mod = importlib.util.module_from_spec(lf_spec)
    lf_spec.loader.exec_module(lf_mod)
    assert set(ls_mod.ALLOWED_SUFFIXES) == set(lf_mod.ALLOWED_SUFFIXES)


# ── b) 入口三道门（roots / pattern） ───────────────────────────────


def test_gates_roots_reject(ls_mod: ModuleType, tmp_path: Path) -> None:
    ctx = _FakeCtx(tmp_path / "ctx")
    with pytest.raises(PluginError, match="缺少输入 roots"):
        ls_mod.run({}, {}, ctx)
    with pytest.raises(PluginError, match="缺少输入 roots"):
        ls_mod.run({"roots": "   "}, {}, ctx)
    with pytest.raises(PluginError, match="拒绝扫描根目录"):
        ls_mod.run({"roots": "/"}, {}, ctx)
    with pytest.raises(PluginError, match="不存在或不是目录"):
        ls_mod.run({"roots": str(tmp_path / "no-such-dir")}, {}, ctx)
    with pytest.raises(PluginError, match="含空项"):
        ls_mod.run({"roots": f"{tmp_path}, "}, {}, ctx)


def test_gates_pattern_only_narrows(ls_mod: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="只能收窄"):
        ls_mod._parse_pattern("mp4,txt")
    with pytest.raises(PluginError, match="只能收窄"):
        ls_mod._parse_pattern("exe")
    # 三种宽容写法归一：* 前缀 / 点前缀 / 大小写
    assert ls_mod._parse_pattern("*.MP4, .Mov") == (".mp4", ".mov")
    assert ls_mod._parse_pattern(None) == ls_mod.ALLOWED_SUFFIXES


# ── c) 排除与限额 ──────────────────────────────────────────────────


def _make_fixture(base: Path) -> Path:
    """标准夹具：1 个可达 mp4（3MB）+ 排除目录内各 1 个 mp4 + 符号链接环。"""
    (base / "videos").mkdir(parents=True)
    (base / "videos" / "real.mp4").write_bytes(b"x" * (3 * 1024 * 1024))
    for frag in ("node_modules", ".git", "Library/Caches", "Library/Application Support"):
        d = base / frag
        d.mkdir(parents=True, exist_ok=True)
        (d / "excluded.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
    return base


def test_excluded_dirs_and_symlink_not_followed(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 9.9)  # 隔离 ffprobe
    base = _make_fixture(tmp_path / "scanme")
    link = tmp_path / "scanme" / "loop"  # 符号链接指向自己上级 → 不跟随则不环
    link.symlink_to(tmp_path, target_is_directory=True)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(base)}, {}, ctx)
    assert out["count"] == 1
    assert "real.mp4" in out["files"] and "excluded.mp4" not in out["files"]
    assert "loop" not in out["files"]  # 符号链接不跟随


def test_exclusion_in_real_repository(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """硬要求 12：在本机真实 repository/ 下造 node_modules/xxx.mp4 断言不被扫到。

    夹具建在 repository/musespiral-scan-fixture/（gitignored 克隆区），try/finally
    必清；同一扫描面同时做「真仓 .git 目录不被带出」的存量断言。
    """
    if not REPO_SCAN_ROOT.is_dir():
        pytest.skip("本机无 repository/ 克隆区")
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 1.0)
    fixture = REPO_SCAN_ROOT / "musespiral-scan-fixture"
    try:
        _make_fixture(fixture)
        ctx = _FakeCtx(tmp_path / "ctx")
        out = ls_mod.run({"roots": str(fixture)}, {}, ctx)
        assert out["count"] == 1 and "real.mp4" in out["files"]
        assert "node_modules" not in out["files"]
    finally:
        shutil.rmtree(fixture, ignore_errors=True)
    # 存量断言：真仓遍布 .git（category/repo 两级布局），任取一个克隆仓扫描，
    # 其下的 .git 恰在 root 之下的相对层 → 排除生效、零带出
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: None)
    ctx2 = _FakeCtx(tmp_path / "ctx2")
    git_dir = next(REPO_SCAN_ROOT.glob("*/*/.git"), None) or next(REPO_SCAN_ROOT.glob("*/.git"), None)
    if git_dir is None:
        pytest.skip("repository/ 下无可断言的克隆仓（.git）")
    out2 = ls_mod.run({"roots": str(git_dir.parent)}, {}, ctx2)
    assert out2["count"] == 0  # .git 排除生效


def test_entry_limit_stops_and_explains(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """硬要求 13：造 50001 个合法条目（min_size=0）→ 恰扫满 50000 即停，
    manifest/日志都带「超限停止、结果不完整」说明。"""
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 0.1)
    big = tmp_path / "big"
    big.mkdir()
    for i in range(ls_mod.MAX_ENTRIES + 1):
        (big / f"f{i}.mp4").write_bytes(b"")
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(big), "min_size_mb": 0}, {}, ctx)
    assert out["count"] == ls_mod.MAX_ENTRIES  # 第 50001 个条目处停止
    assert any("上限" in line and "停止" in line for line in ctx.logs)
    assert "上限" in out["manifest"] and "不完整" in out["manifest"]


# ── d) 隐私边界 ────────────────────────────────────────────────────


def test_privacy_warning_on_sensitive_roots(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 2.0)
    wechat = tmp_path / "Library" / "Containers" / "com.tencent.xinWeChat"
    wechat.mkdir(parents=True)
    (wechat / "msg.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(wechat)}, {}, ctx)
    assert any("隐私" in line for line in ctx.logs)  # 硬要求 14
    # 显式点名不豁免隐私红线（2026-11-20 复核判例）：root=微信容器目录 →
    # 整树跳过（绝对分量匹配命中 ("Library","Containers")）+ 可读说明
    assert out["count"] == 0
    assert any("整树跳过" in line for line in ctx.logs)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "keep.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
    ctx2 = _FakeCtx(tmp_path / "ctx2")
    out2 = ls_mod.run({"roots": str(docs)}, {}, ctx2)
    assert any("隐私" in line for line in ctx2.logs)
    assert out2["count"] == 1  # Documents 只警告不排除（不在排除表内）
    # 普通目录不警告
    plain = tmp_path / "plain"
    plain.mkdir()
    ctx3 = _FakeCtx(tmp_path / "ctx3")
    ls_mod.run({"roots": str(plain)}, {}, ctx3)
    assert not any("隐私" in line for line in ctx3.logs)


def test_privacy_not_bypassed_by_root_choice(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核判例回归（2026-11-20）：root=…/Library 时 Containers 的相对分量
    只剩单分量，双片段永不匹配 → 微信私人媒体无提示带出。修复后按绝对
    分量匹配：三种 root 位形全部拦住，且合法同级（Library/Music）不受误伤。"""
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 1.0)
    base = tmp_path / "priv"
    wechat = base / "Library" / "Containers" / "com.tencent.xinWeChat"
    wechat.mkdir(parents=True)
    (wechat / "msg.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
    music = base / "Library" / "Music"
    music.mkdir(parents=True)
    (music / "song.mp4").write_bytes(b"x" * (2 * 1024 * 1024))  # 合法媒体：必须仍被扫出

    for root, expect in ((base, 1), (base / "Library", 1)):  # Music 可达、Containers 不可达
        ctx = _FakeCtx(tmp_path / "ctx")
        out = ls_mod.run({"roots": str(root)}, {}, ctx)
        assert out["count"] == expect, root
        assert all("xinWeChat" not in ln for ln in out["files"])  # 私人媒体零带出
        assert "song.mp4" in out["files"]  # 合法同级不误伤（排除只命中双片段）
        assert all("msg.mp4" not in ln for ln in out["files"])

    ctx_wechat = _FakeCtx(tmp_path / "ctx-w")
    out_w = ls_mod.run({"roots": str(wechat)}, {}, ctx_wechat)
    assert out_w["count"] == 0  # root 正好是容器目录 → 整树跳过
    assert any("整树跳过" in ln for ln in ctx_wechat.logs)

    ctx_c = _FakeCtx(tmp_path / "ctx-c")
    out_c = ls_mod.run({"roots": str(base / "Library" / "Containers")}, {}, ctx_c)
    assert out_c["count"] == 0  # root=Containers 本身同样拦住


# ── e) 三种边界 ────────────────────────────────────────────────────


def test_edge_empty_dir(ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: None)
    empty = tmp_path / "empty"
    empty.mkdir()
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(empty)}, {}, ctx)
    assert out["count"] == 0
    assert "| # |" in out["files"]  # 表头仍在（空表）


def test_edge_nonexistent_dir(ls_mod: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="不存在"):
        ls_mod.run({"roots": str(tmp_path / "ghost")}, {}, _FakeCtx(tmp_path / "ctx"))


def test_edge_no_permission_dir(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无权限目录：root 本身 scandir 被拒 → 静默计数不崩（osexchange 下
    is_dir 仍 True——stat 查父目录权限）。"""
    if os.geteuid() == 0:
        pytest.skip("root 用户无权限拒绝可言")
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: None)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
    locked.chmod(0o000)
    ctx = _FakeCtx(tmp_path / "ctx")
    try:
        out = ls_mod.run({"roots": str(locked)}, {}, ctx)
        assert out["count"] == 0
        assert "权限拒绝" in out["manifest"] or "跳过" in out["manifest"]
    finally:
        locked.chmod(0o755)  # 恢复以便 tmp 清理


# ── f) ffprobe 双态与并发保序 ──────────────────────────────────────


def test_probe_degraded_garbage_bytes(ls_mod: ModuleType, tmp_path: Path) -> None:
    """真 ffprobe 探垃圾字节 mp4 → 非零退出 → duration None，不炸。"""
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(os.urandom(2 * 1024 * 1024))
    assert ls_mod._probe_one(garbage) is None


def test_probe_many_order_preserved(ls_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    files = [Path(f"/fake/{i}.mp4") for i in range(9)]  # 9 个 > MAX_WORKERS=4
    monkeypatch.setattr(ls_mod, "_probe_one", lambda p: float(p.stem) / 10)
    ctx = _FakeCtx(Path("/tmp/ctx-probe-many"))
    got = ls_mod._probe_many(files, ctx)
    assert got == [i / 10 for i in range(9)]  # 与输入同序


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="无 ffmpeg（真时长用例）")
def test_probe_real_lavfi_media(ls_mod: ModuleType, tmp_path: Path) -> None:
    """真 lavfi 合成 1s mp4 → 扫描发现 + 真时长（表内非「未知」）+ 格式列 mp4。"""
    real = tmp_path / "real-media"
    real.mkdir()
    out_mp4 = real / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=128x72:rate=10",
         "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", "-y", str(out_mp4)],
        check=True, capture_output=True, timeout=60,
    )
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(real), "min_size_mb": 0}, {}, ctx)
    assert out["count"] == 1
    assert "clip.mp4" in out["files"] and "| mp4 |" in out["files"]
    assert "未知" not in out["files"]  # 真 ffprobe 探到了时长


# ── g) 输出契约与排序 ──────────────────────────────────────────────


def test_run_output_contract_and_ordering(
    ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ls_mod, "_probe_one", lambda p: 10.0 if p.stem == "b" else 3.0)
    base = tmp_path / "order"
    base.mkdir()
    (base / "a.mp4").write_bytes(b"x" * (3 * 1024 * 1024))
    (base / "b.mp4").write_bytes(b"x" * (9 * 1024 * 1024))
    (base / "small.mp4").write_bytes(b"x" * (512 * 1024))  # < min_size 1MB → 滤掉
    (base / "notes.txt").write_bytes(b"x" * (9 * 1024 * 1024))  # 后缀白名单外 → 滤掉
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(base)}, {}, ctx)
    assert set(out.keys()) == {"files", "count", "manifest"}  # v1 恰好三端口
    assert out["count"] == 2
    lines = [ln for ln in out["files"].splitlines() if ln.startswith("| ")]
    assert lines[0].startswith("| # |")
    assert "b.mp4" in lines[1] and "a.mp4" in lines[2]  # 9MB 在 3MB 前（大小降序）
    assert "small.mp4" not in out["files"] and "notes.txt" not in out["files"]
    assert "命中 2 个文件" in out["manifest"] and "ffprobe" in out["manifest"]


def test_min_size_filter_zero_means_all(ls_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ls_mod, "_probe_one", lambda _m: 0.5)
    base = tmp_path / "tiny"
    base.mkdir()
    (base / "t.mp4").write_bytes(b"x" * 100)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ls_mod.run({"roots": str(base), "min_size_mb": 0}, {}, ctx)
    assert out["count"] == 1
    out2 = ls_mod.run({"roots": str(base)}, {}, ctx)  # 默认 1MB → 滤掉
    assert out2["count"] == 0


# ── h) 探针脚本冒烟（子进程真跑，路径固定 modules/musespiral/scripts/scan_probe.py）──


def test_scan_probe_script_smoke(tmp_path: Path) -> None:
    probe = REPO_ROOT / "modules" / "musespiral" / "scripts" / "scan_probe.py"
    assert probe.is_file()
    base = tmp_path / "probe-fixture"
    _make_fixture(base)
    proc = subprocess.run(
        [sys.executable, "-I", str(probe), str(base), "--max-depth", "3", "--min-size-mb", "1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["count"] == 1
    assert data["files"][0]["path"].endswith("real.mp4")
    # 夹具 mp4 是垃圾字节 → 真 ffprobe 失败 → 时长留空（probed_ok=0 不炸）
    assert data["probed_ok"] == 0
    assert data["files"][0]["duration_s"] is None
