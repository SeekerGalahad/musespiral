"""书源实弹修复全链测试（R0.5 F-X1/X2/X3/X4）——真实样本夹具驱动。

夹具 = tests/fixtures/booksource_samples/（生成脚本一次性从克隆仓
repository/musespiral/legado-aoaostar/sources 拷贝/构造，gitignored 源不入库）：

  71e56d4f.json            22 源干净书源包（XIU2 精品，§一 实测）
  3bb7b751_dirty_subset.json  3bb7b751 前 50 条 + index 25 插 1 条 {}（脏源）
  39c60133.json            主题包（themeName/accentColor，1 对象）
  856adf0e_tts_subset.json 朗读引擎包前 5 条（id/url/name/contentType）
  3f067eb2.json            净化规则包（pattern/replacement，20 条）
  9955b83c.json            阅读排版包（singleUrl/ruleArticles，1 条）

合成 JSON 只证明 happy path 的教训（设计 §一）——本文件全部用真实样本
断言六类型识别 / 脏源跳过 / 干净导入 / 类型化提示 / dupUrls / --file 全链。
MUSESPIRAL_HOME monkeypatch 隔离，零真实联网（导入走本地文件文本）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.cli import cli
from musespiral.core.booksource import classifier, importer
from musespiral.infra import booksource_store
from musespiral.services import booksource_service as svc

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "booksource_samples"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """MUSESPIRAL_HOME 隔离 + 待确认缓存清空。"""
    h = tmp_path / "musespiral-home"
    h.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(h))
    svc._PENDING.clear()
    yield h
    svc._PENDING.clear()


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load(name: str) -> list | dict:
    return json.loads(_read(name))


# ── X1：六类型识别（真实样本）─────────────────────────────────────


def test_classify_real_booksource_package() -> None:
    """71e56d4f 22 源包 → bookSource；脏子集（含 1 条 {}）仍是 bookSource。"""
    verdict = classifier.classify_payload(_load("71e56d4f.json"))
    assert verdict.kind == "bookSource" and verdict.features == ("bookSourceUrl",)
    dirty = classifier.classify_payload(_load("3bb7b751_dirty_subset.json"))
    assert dirty.kind == "bookSource", "含 1 条脏源的包仍按 bookSource（F-X1 两级判定）"


def test_classify_real_non_booksource_packages() -> None:
    """主题/TTS/净化/排版四类真实包逐一识别（§一：非书源是类型未识别）。"""
    theme = classifier.classify_payload(_load("39c60133.json"))
    assert theme.kind == "theme"
    assert {"themeName", "accentColor"} <= set(theme.features)
    tts = classifier.classify_payload(_load("856adf0e_tts_subset.json"))
    assert tts.kind == "tts"
    replace = classifier.classify_payload(_load("3f067eb2.json"))
    assert replace.kind == "replace"
    layout = classifier.classify_payload(_load("9955b83c.json"))
    assert layout.kind == "layout"


def test_classify_unknown_lists_key_features() -> None:
    """unknown 列出识别到的键特征（{} 形态无特征→空 features）。"""
    verdict = classifier.classify_payload([{"totally": "unrelated", "other": 1}])
    assert verdict.kind == "unknown"
    assert "other" in verdict.features and "totally" in verdict.features
    assert classifier.classify_payload({}).kind == "unknown"


# ── X1：脏源跳过 + skipped 清单（真实脏包）───────────────────────


def test_dirty_subset_skips_one_and_imports_fifty(home) -> None:
    """3bb7b751 前 50 条 + {}：50 有效 + skipped=[{index:25, reason}]，不整批拒绝。"""
    result = importer.import_source(_read("3bb7b751_dirty_subset.json"))
    assert result["kind"] == "bookSource"
    assert len(result["valid"]) == 50
    assert result["skipped"] == [{"index": 25, "reason": "缺 bookSourceUrl"}]


def test_dirty_subset_preview_and_apply(home) -> None:
    """预览 skipped 可见 + apply 落 50 条（预览零落库红线照旧）。"""
    text = _read("3bb7b751_dirty_subset.json")
    preview = svc.import_preview(text)
    assert preview["kind"] == "bookSource"
    assert preview["skipped"] == [{"index": 25, "reason": "缺 bookSourceUrl"}]
    assert preview["counts"]["isNew"] == 50
    applied = svc.import_apply(text)
    assert applied["stored"] == 50
    assert applied["deduped_from"] == 50
    assert len(svc.list_sources()) == 50


def test_booksource_all_invalid_rejected_as_whole(home) -> None:
    """仅当 bookSource 类型且全部条目无效才整体拒绝（§二 F-X1 ③）。

    可达形态：bookSourceUrl 键在但值空（classify 认得 bookSource 包、
    逐条校验全挂）；键全缺的包在 classify 层判 unknown（类型化提示，
    不进入整包拒绝分支——另测）。
    """
    with pytest.raises(importer.BookSourceImportError, match="全部无效"):
        importer.import_source(json.dumps([{"bookSourceUrl": " "}, {"bookSourceUrl": ""}]))
    # 键全缺 → unknown 类型化提示（不是「书源」，也不是异常）
    result = importer.import_source(json.dumps([{}, {"bookSourceName": "无地址"}]))
    assert result["kind"] == "unknown" and result["message"]


# ── 22 源干净导入（§五 验收 #6 全链）─────────────────────────────


def test_clean_22_sources_import_chain(home) -> None:
    """71e56d4f（22 源精品包）preview → apply → list 全链。"""
    text = _read("71e56d4f.json")
    preview = svc.import_preview(text)
    assert preview["counts"]["isNew"] == 22
    assert preview["counts"]["dupUrls"] == 0
    assert preview["skipped"] == []
    applied = svc.import_apply(text)
    assert applied["stored"] == 22
    rows = svc.list_sources()
    assert len(rows) == 22
    assert any(r["bookSourceName"] == "起点中文" for r in rows)


# ── 非书源包可读提示（§二 F-X1 用户可见段）───────────────────────


def test_theme_package_typed_message_preview_and_apply(home) -> None:
    """39c60133 主题包：ok=True + kind=theme + 可读提示；apply 零落库。"""
    text = _read("39c60133.json")
    preview = svc.import_preview(text)
    assert preview["ok"] is True and preview["kind"] == "theme"
    assert "主题" in preview["message"] and "阅读" in preview["message"]
    assert preview["items"] == [] and preview["counts"]["isNew"] == 0
    applied = svc.import_apply(text)
    assert applied["kind"] == "theme" and applied["message"] == preview["message"]
    assert applied["imported"] == [] and applied["stored"] == 0
    assert svc.list_sources() == []


def test_tts_replace_layout_typed_messages(home) -> None:
    """TTS/净化/排版真实包各给类型化提示。"""
    assert svc.import_preview(_read("856adf0e_tts_subset.json"))["kind"] == "tts"
    assert svc.import_preview(_read("3f067eb2.json"))["kind"] == "replace"
    assert svc.import_preview(_read("9955b83c.json"))["kind"] == "layout"


# ── X3：dupUrls 计数 + apply 去重透明（真实条目）─────────────────


def test_dup_urls_counted_and_apply_dedupes(home) -> None:
    """真实 22 源 + 重复 1 条：dupUrls=1；apply stored=22 / deduped_from=23。"""
    sources = _load("71e56d4f.json")
    sources.append(sources[0])  # 同 URL 重复（真实条目构造重复包）
    text = json.dumps(sources, ensure_ascii=False)
    preview = svc.import_preview(text)
    assert preview["counts"]["dupUrls"] == 1
    applied = svc.import_apply(text)
    assert applied["stored"] == 22, "同 URL 只落一次盘"
    assert applied["deduped_from"] == 23, "落库前选择条数透明"
    assert len(svc.list_sources()) == 22
    stored = booksource_store.load_source(sources[0]["bookSourceUrl"])
    assert stored is not None


# ── X4：CLI --file / stdin / 过滤（真实文件入口）─────────────────


def test_cli_import_from_file_preview(home) -> None:
    """--file <PATH> 读真实 22 源包预览（零落库）。"""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["booksource", "import", "--file", str(FIXTURES / "71e56d4f.json")]
    )
    assert result.exit_code == 0, result.output
    assert "新增 22" in result.output
    assert "起点中文" in result.output
    assert not list(home.rglob("*.json")), "预览零落库"


def test_cli_import_from_stdin_apply(home) -> None:
    """--file - 读 stdin + --apply 落库。"""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["booksource", "import", "--file", "-", "--apply"],
        input=_read("71e56d4f.json"),
    )
    assert result.exit_code == 0, result.output
    assert "已导入 22 个书源" in result.output
    assert "落库前 22 条" in result.output
    assert len(svc.list_sources()) == 22


def test_cli_import_filter_group_status(home) -> None:
    """--filter/--group/--status 三轴过滤（防刷屏）+ 过滤落库。"""
    runner = CliRunner()
    path = str(FIXTURES / "71e56d4f.json")

    out = runner.invoke(cli, ["booksource", "import", "--file", path, "--filter", "起点"])
    assert out.exit_code == 0, out.output
    assert "过滤后 1 / 全部 22 条" in out.output
    assert "起点中文" in out.output and "酷我小说" not in out.output

    out = runner.invoke(cli, ["booksource", "import", "--file", path, "--group", "正版"])
    assert out.exit_code == 0, out.output
    assert "过滤后 1 / 全部 22 条" in out.output

    out = runner.invoke(cli, ["booksource", "import", "--file", path, "--status", "new"])
    assert out.exit_code == 0, out.output
    assert "过滤后 22 / 全部 22 条" in out.output, "空库全为新"

    out = runner.invoke(cli, ["booksource", "import", "--file", path, "--status", "latest"])
    assert out.exit_code == 0, out.output
    assert "过滤后 0 / 全部 22 条" in out.output

    # 过滤落库：只落匹配的 1 条
    out = runner.invoke(
        cli, ["booksource", "import", "--file", path, "--filter", "起点", "--apply"]
    )
    assert out.exit_code == 0, out.output
    assert "已导入 1 个书源" in out.output
    assert len(svc.list_sources()) == 1


def test_cli_import_theme_file_gives_typed_hint(home) -> None:
    """CLI 导入主题包文件 → 可读边界提示（exit 0，不冷漠拒绝）。"""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["booksource", "import", "--file", str(FIXTURES / "39c60133.json")]
    )
    assert result.exit_code == 0, result.output
    assert "主题" in result.output and "阅读」APP" in result.output


def test_cli_import_missing_input_and_bad_file(home) -> None:
    """无内容 / 文件不存在 → 可读错误 exit 1。"""
    runner = CliRunner()
    out = runner.invoke(cli, ["booksource", "import"])
    assert out.exit_code == 1 and "缺少导入内容" in out.output
    out = runner.invoke(cli, ["booksource", "import", "--file", "/no/such/pkg.json"])
    assert out.exit_code == 1 and "文件不存在" in out.output
