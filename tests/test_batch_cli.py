"""F34 多文章批量 CLI 集成测试（CliRunner）。

覆盖：五命令（check/generate/adapt/pack/digest）多文件汇总表 + 聚合退出码 +
逐篇不短路；单文件输出与旧版（ARTICLE 单值）逐字节一致；pack 多文件 +
--variant 显式报错；零参数用法报错。旧版输出基线取自 2026-11-14 改造前
实测捕获（capture-old，musespiral@f6a1d6d6 之前的工作树）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from musespiral.cli import cli


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _article(dir_: Path, name: str, *, title: str = "测试标题甲", cover: bool = True) -> Path:
    """写一篇与 F34 基线捕获同构的文章（title 5 字 / digest 21 字节 / 无图）。"""
    dir_.mkdir(parents=True, exist_ok=True)
    if cover:
        Image.new("RGB", (900, 383), "#336699").save(dir_ / "cover.png")
    p = dir_ / name
    p.write_text(
        f"---\ntitle: {title}\ncover: cover.png\ndigest: 这是基线摘要。\ntags: [AI, 写作]\n---\n\n"
        "缓存是提升系统性能的重要手段。\n\n"
        "实测引入多级缓存后接口耗时明显下降。\n",
        encoding="utf-8",
    )
    return p


# ── 单文件输出与旧版逐字节一致（F34 向后兼容门）──

# 旧版 `musespiral check a.md` 实测输出（改造前捕获，含末尾换行）
_OLD_CHECK_OUT = (
    "✅ [PASS] 标题长度合规: 15 字节\n"
    "✅ [PASS] 封面图存在: cover.png (0.0MB)\n"
    "✅ [PASS] 摘要长度合规: 21 字节\n"
    "⚠️  [WARN] 正文无图片，纯文字文章吸引力较低\n"
    "==================================================\n"
    "审核结果: 0 失败, 1 警告, 3 通过\n"
    "==================================================\n"
)

# 旧版 `musespiral digest a.md` 实测输出
_OLD_DIGEST_OUT = (
    "共 2 条备选摘要:\n"
    "  1. 缓存是提升系统性能的重要手段。\n"
    "  2. 实测引入多级缓存后接口耗时明显下降。\n"
    "\n"
    "选用某条写回: musespiral digest <article> --apply 1|2|3\n"
)


def test_single_file_check_byte_identical(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    r = CliRunner().invoke(cli, ["check", str(a)])
    assert r.exit_code == 0
    assert r.output == _OLD_CHECK_OUT
    assert "批量汇总" not in r.output


def test_single_file_digest_byte_identical(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    r = CliRunner().invoke(cli, ["digest", str(a)])
    assert r.exit_code == 0
    assert r.output == _OLD_DIGEST_OUT


def test_single_file_generate_and_adapt_and_pack(tmp_path) -> None:
    """单文件 generate/adapt/pack：输出逐字节断言（C5 升级，对齐 check/digest
    强度——路径随 tmp 变化，用 f-string 插值后全等比较），零批量痕迹。"""
    a = _article(tmp_path, "a.md")
    runner = CliRunner()

    r = runner.invoke(cli, ["generate", str(a)])
    assert r.exit_code == 0
    assert r.output == (
        f"✅ 已生成: {a.parent}/a.musespiral/rendered.html\n"
        f"   主题: lapis | 代码主题: monokai\n"
        f"   下一步: musespiral preview {a}  或  musespiral check {a}\n"
    )

    r = runner.invoke(cli, ["adapt", str(a), "--platform", "xhs"])
    assert r.exit_code == 0
    assert r.output == (
        f"✅ 已生成小红书变体: {a.parent}/a.xhs.md\n"
        f"   ⚠️ 仅产出本地文件，人审后才可进入 pack / publish\n"
    )

    r = runner.invoke(cli, ["pack", str(a), "--platform", "channels"])
    assert r.exit_code == 0
    assert r.output == (
        f"✅ 物料包已生成: {a.parent}/a.channels.pack\n"
        f"   按 README.md 的入口与字段对应表粘贴发布；checks.txt 全部 ok 后再点发布\n"
    )


# ── 多文件：汇总表 + 聚合退出码 + 不短路 ──


def test_check_multi_one_fails_no_short_circuit(tmp_path) -> None:
    """b 篇故意 FAIL（无封面）→ 两篇都执行（两份报告齐）、汇总表两行、exit 1。"""
    a = _article(tmp_path / "a", "a.md")
    b = _article(tmp_path / "b", "b.md", cover=False)  # 目录内无 cover.png → 封面项 FAIL
    r = CliRunner().invoke(cli, ["check", str(a), str(b)])
    assert r.exit_code == 1, "任一 FAIL → 聚合 exit 1"
    assert r.output.count("审核结果") == 2, "逐篇执行不短路"
    assert "封面图不存在" in r.output, "b 篇的报告确实产出"
    assert "批量汇总（check × 2 篇）" in r.output
    lines = [ln for ln in r.output.splitlines() if ln.startswith("  check  ")]
    assert len(lines) == 2
    assert any(" PASS " in ln for ln in lines) and any(" FAIL " in ln for ln in lines)
    assert all(ln.rstrip().endswith("s") for ln in lines), "逐行带耗时"


def test_check_multi_all_pass(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(cli, ["check", str(a), str(b)])
    assert r.exit_code == 0
    assert "批量汇总（check × 2 篇）" in r.output


def test_generate_multi(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(cli, ["generate", str(a), str(b)])
    assert r.exit_code == 0
    assert r.output.count("✅ 已生成") == 2
    assert "批量汇总（generate × 2 篇）" in r.output
    assert (tmp_path / "a.musespiral" / "rendered.html").exists()
    assert (tmp_path / "b.musespiral" / "rendered.html").exists()


def test_digest_multi_and_apply(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    runner = CliRunner()
    r = runner.invoke(cli, ["digest", str(a), str(b)])
    assert r.exit_code == 0
    assert r.output.count("共 2 条备选摘要") == 2
    assert "批量汇总（digest × 2 篇）" in r.output

    a2, b2 = _article(tmp_path / "d", "a.md"), _article(tmp_path / "d", "b.md")
    r2 = runner.invoke(cli, ["digest", str(a2), str(b2), "--apply", "2"])
    assert r2.exit_code == 0, r2.output
    assert r2.output.count("已写回第 2 条为 digest") == 2


def test_adapt_multi(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(cli, ["adapt", str(a), str(b), "--platform", "xhs"])
    assert r.exit_code == 0, r.output
    assert r.output.count("已生成小红书变体") == 2
    assert "批量汇总（adapt × 2 篇）" in r.output


def test_pack_multi(tmp_path) -> None:
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(cli, ["pack", str(a), str(b), "--platform", "channels"])
    assert r.exit_code == 0, r.output
    assert r.output.count("物料包已生成") == 2
    assert "批量汇总（pack × 2 篇）" in r.output


def test_pack_multi_with_variant_rejected(tmp_path) -> None:
    """--variant 单值仅单文件合法：多文件 + variant → 显式用法报错（exit 2）。"""
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    v = tmp_path / "v.md"
    v.write_text("---\ntitle: v\n---\n正文", encoding="utf-8")
    r = CliRunner().invoke(cli, ["pack", str(a), str(b), "--platform", "channels", "--variant", str(v)])
    assert r.exit_code == 2
    assert "--variant 仅支持单文件调用" in r.output


def test_pack_single_with_variant_still_works(tmp_path) -> None:
    """单文件 + --variant 仍走旧语义（显式路径缺失时拒绝静默回退）。"""
    a = _article(tmp_path, "a.md")
    missing = tmp_path / "nope.md"
    r = CliRunner().invoke(cli, ["pack", str(a), "--platform", "channels", "--variant", str(missing)])
    assert r.exit_code == 1
    assert "❌" in r.output


# ── C2（第十四期顺手批）：--out/--outdir 多文件+单值前置显式报错 ──


def test_adapt_multi_with_out_rejected(tmp_path) -> None:
    """C2：adapt --out 单值对多文件无法逐篇对应 → 用法报错（与 --variant 同语义）。"""
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(
        cli, ["adapt", str(a), str(b), "--platform", "xhs", "--out", str(tmp_path / "v.md")]
    )
    assert r.exit_code == 2
    assert "--out 仅支持单文件调用" in r.output


def test_pack_multi_with_outdir_rejected(tmp_path) -> None:
    """C2：pack --outdir 单值对多文件无法逐篇对应 → 用法报错。"""
    a = _article(tmp_path, "a.md")
    b = _article(tmp_path, "b.md")
    r = CliRunner().invoke(
        cli,
        ["pack", str(a), str(b), "--platform", "channels", "--outdir", str(tmp_path / "p")],
    )
    assert r.exit_code == 2
    assert "--outdir 仅支持单文件调用" in r.output


def test_single_file_with_out_and_outdir_still_legal(tmp_path) -> None:
    """守门只拦多文件：单文件 + --out/--outdir 语义不变（out 落盘成功）。"""
    a = _article(tmp_path, "a.md")
    out = tmp_path / "custom.md"
    r = CliRunner().invoke(cli, ["adapt", str(a), "--platform", "xhs", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.is_file()


@pytest.mark.parametrize(
    "cmd", [["check"], ["generate"], ["digest"], ["adapt", "--platform", "xhs"], ["pack", "--platform", "channels"]]
)
def test_zero_articles_usage_error(tmp_path, cmd) -> None:
    """nargs=-1 required：零参数 → 用法报错（≥1 篇硬门）。"""
    r = CliRunner().invoke(cli, cmd)
    assert r.exit_code == 2
    assert "Missing argument" in r.output


def test_summary_shows_command_and_duration_format(tmp_path) -> None:
    """汇总表行格式：命令 / 结果 / 路径 / 耗时。"""
    a = _article(tmp_path / "a", "a.md")
    b = _article(tmp_path / "b", "b.md", cover=False)
    r = CliRunner().invoke(cli, ["check", str(a), str(b)])
    assert any(
        ln.startswith(f"  check  FAIL  {b}") and ln.rstrip().endswith("s")
        for ln in r.output.splitlines()
    )
