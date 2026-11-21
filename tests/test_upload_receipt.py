"""F37 断点续传与交付回执 — CLI 面集成测试（CliRunner）。

覆盖（正典 §二 F37 / §五「F37 回执/跳过」行）：
  - pack / upload publish 前置查已交付平台（同 article+platform 有
    result=uploaded 或 action=draft 行）→ 跳过并提示「已交付」；
  - --force 重做；
  - 会话收尾回执（platform_cmds._append_uploaded 经 publish_log_service
    真实落盘，与前置检查读侧 roundtrip）；
  - 不同平台/不同文章互不误伤；demo_ 行不作为跳过依据（merge_logs 滤除）。

全程 MUSESPIRAL_HOME 隔离 + monkeypatch 掉 browser 真实路径（不起 playwright）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from musespiral.cli import cli
from musespiral.commands import platform_cmds
from musespiral.services import publish_log_service


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _article(dir_: Path, name: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 383), "#336699").save(dir_ / "cover.png")
    p = dir_ / name
    p.write_text(
        f"---\ntitle: {name}的标题\ncover: cover.png\ndigest: 摘要。\n---\n\n"
        "缓存是提升系统性能的重要手段。\n\n实测引入多级缓存后接口耗时明显下降。\n",
        encoding="utf-8",
    )
    return p


def _make_pack(dir_: Path, name: str) -> Path:
    """最小物料包（upload publish 只在 dry-run/真实路径才读 payload；前置
    检查只需目录存在——与 CLI --pack exists 校验对齐）。"""
    pack = dir_ / name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "payload.md").write_text(
        "> - 标题：t\n> - 描述：d\n> - 话题：无\n\n---\n\n正文。\n", encoding="utf-8"
    )
    return pack


def _home_log(home_env: str) -> Path:
    return Path(home_env) / "publish_log.jsonl"


# ── 回执落盘 + 读侧 roundtrip（真实 publish_log_service） ──


def test_append_uploaded_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_append_uploaded 落 result=uploaded 行；_delivered_platforms 读回命中。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    pack = _make_pack(tmp_path, "demo.xhs.pack")

    platform_cmds._append_uploaded("xhs", pack)

    rows = [json.loads(l) for l in _home_log(home).read_text(encoding="utf-8").splitlines()]
    assert rows == [{"ts": rows[0]["ts"], "platform": "xhs", "article": "demo", "result": "uploaded"}]
    assert platform_cmds._delivered_platforms("demo.md", tmp_path) == {"xhs"}
    # pack 目录名反推 stem 约定（<stem>.<platform>.pack）
    assert platform_cmds._article_from_pack(pack, "xhs") == "demo"


def test_delivered_requires_same_article_and_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同键命中、异键不误伤：换文章或换平台都不构成跳过依据。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    publish_log_service.append_uploaded(article="demo", platform="xhs")
    # 文章 stem 相同、平台不同 → 不算已交付
    assert "channels" not in platform_cmds._delivered_platforms("demo.md", tmp_path)
    # 平台相同、文章不同 → 不算已交付
    assert "xhs" not in platform_cmds._delivered_platforms("other.md", tmp_path)
    # mp 建草稿行（action=draft）按三态口径也算已交付
    with _home_log(home).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-11-14T10:00:00", "action": "draft",
                            "article": str(tmp_path / "demo.md"), "platform": "mp"}) + "\n")
    assert "mp" in platform_cmds._delivered_platforms("demo.md", tmp_path)


def test_demo_rows_are_not_delivery_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """demo_ 前缀 fixture 行被 merge_logs 滤除——不构成「已交付」跳过依据。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    _home_log(home).write_text(
        json.dumps({"ts": "2026-11-11T09:00:00", "action": "draft", "article": "demo.md",
                    "platform": "xhs", "media_id": "demo_abc", "title": "演示行"}) + "\n",
        encoding="utf-8",
    )
    assert platform_cmds._delivered_platforms("demo.md", tmp_path) == set()


# ── pack 前置跳过 + --force ──


def test_pack_skips_delivered_platform_and_force_redoes(tmp_path: Path) -> None:
    a = _article(tmp_path, "demo.md")
    publish_log_service.append_uploaded(article="demo", platform="zhihu")

    r = CliRunner().invoke(cli, ["pack", str(a), "--platform", "zhihu"])
    assert r.exit_code == 0
    assert "已交付 知乎（zhihu）：跳过物料包生成；--force 重做" in r.output
    assert not (tmp_path / "demo.zhihu.pack").exists()

    r = CliRunner().invoke(cli, ["pack", str(a), "--platform", "zhihu", "--force"])
    assert r.exit_code == 0
    assert "物料包已生成" in r.output
    assert (tmp_path / "demo.zhihu.pack").is_dir()


def test_pack_other_platform_unaffected(tmp_path: Path) -> None:
    """已交付 zhihu 不影响同篇 channels 物料包（平台维度跳过）。"""
    a = _article(tmp_path, "demo.md")
    publish_log_service.append_uploaded(article="demo", platform="zhihu")
    r = CliRunner().invoke(cli, ["pack", str(a), "--platform", "channels"])
    assert r.exit_code == 0
    assert "物料包已生成" in r.output


def test_pack_skips_when_log_at_root_top_nested_layout(tmp_path: Path) -> None:
    """嵌套布局：文章在 root 子目录、日志在 root 顶层 publish_log.jsonl——
    前置查从文章路径向上探测命中 root（L4：原 parent 作 root 漏查 root
    顶层日志，已交付被误判未交付）。"""
    sub = tmp_path / "sub"
    a = _article(sub, "demo.md")
    # root 顶层日志（非 home 侧）：uploaded 行，article 按完整路径记录
    (tmp_path / "publish_log.jsonl").write_text(
        json.dumps({"ts": "2026-11-14T09:00:00", "platform": "zhihu",
                    "article": str(a), "result": "uploaded"}) + "\n",
        encoding="utf-8",
    )
    r = CliRunner().invoke(cli, ["pack", str(a), "--platform", "zhihu"])
    assert r.exit_code == 0, r.output
    assert "已交付 知乎（zhihu）：跳过物料包生成；--force 重做" in r.output
    assert not (sub / "demo.zhihu.pack").exists(), "命中跳过后不得产包"


# ── upload publish 前置跳过 + --force + 回执接线 ──


def test_upload_publish_skips_delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = _make_pack(tmp_path, "demo.channels.pack")
    publish_log_service.append_uploaded(article="demo", platform="channels")

    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "musespiral.browser.publish_flow",
        lambda *a, **k: called.append((a[0], str(a[1]))),
    )
    r = CliRunner().invoke(cli, ["upload", "publish", "--platform", "channels", "--pack", str(pack)])
    assert r.exit_code == 0
    assert "已交付 视频号（channels/demo）：跳过上传会话；--force 重做" in r.output
    assert called == []  # 未起会话


def test_upload_publish_force_opens_session_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force 重开会话：浏览器路径被替身接管，on_delivered 真实落回执。"""
    home = tmp_path / "musespiral-home"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    pack = _make_pack(tmp_path, "demo.channels.pack")
    publish_log_service.append_uploaded(article="demo", platform="channels")

    def fake_publish_flow(platform, pack_dir, *, headless=False, on_delivered=None):
        assert on_delivered is not None  # CLI 层必须接上回执
        on_delivered(platform, Path(pack_dir))  # 模拟会话正常结束
        return None

    monkeypatch.setattr("musespiral.browser.publish_flow", fake_publish_flow)
    r = CliRunner().invoke(
        cli,
        ["upload", "publish", "--platform", "channels", "--pack", str(pack), "--force"],
        input="y\n",
    )
    assert r.exit_code == 0, r.output
    rows = [json.loads(l) for l in _home_log(home).read_text(encoding="utf-8").splitlines()]
    assert [r["result"] for r in rows] == ["uploaded", "uploaded"]  # 既有行 + 回执行
    assert rows[-1]["article"] == "demo" and rows[-1]["platform"] == "channels"
