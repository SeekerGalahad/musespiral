"""services 层冒烟测试（Phase A：从 studio/actions 提取编排；mock AI/网络）。

- 每个 service 至少一条用例（正典 §六 services 集成测试策略：mock infra
  接口，不发真网络请求、不触真实 ~/.musespiral）；
- 与 test_studio.py 共用 fixtures/sample_article.md（该样例过完整检查门），
  断言落在服务契约上（返回路径/异常语义/产物落盘），不断言 Studio 载荷。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import frontmatter
import pytest

from musespiral.infra import ai
from musespiral.core.article import read_status, set_status
from musespiral.services import (
    BatchAdaptError,
    annotate_rewrite,
    build_pack_for,
    fetch_to_draft,
    generate_batch,
    generate_variant,
    publish_draft,
    run_publish_gate,
    submit_and_track,
)
from musespiral.services import fetch_service as _fetch_service
from musespiral.services import publish_service as _publish_service

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral（AI 配置/发布日志同源隔离）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def article_root(tmp_path: Path) -> Path:
    """工作目录：复制 fixtures 样例文章与封面/配图（与 test_studio 同款）。"""
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    shutil.copy(FIXTURES / "cover.png", root / "cover.png")
    (root / "assets").mkdir()
    shutil.copy(FIXTURES / "assets" / "fig1.png", root / "assets" / "fig1.png")
    return root


# ---- publish_service ----


def test_run_publish_gate_full_results(article_root: Path) -> None:
    """检查门：frontmatter 级 + 渲染级全量结果，样例文章无 FAIL（纯本地）。"""
    results = run_publish_gate(article_root / "sample_article.md")
    assert isinstance(results, list) and results
    assert all(level in ("PASS", "WARN", "FAIL") for level, _ in results)
    assert not [level for level, _ in results if level == "FAIL"]


def test_publish_draft_orchestrates_and_marks_reviewed(
    article_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """建草稿编排（C1）：门通过 → mock publish_flow → draft 自动置 reviewed。"""
    # 样例文章无 status 字段，读作 draft——publish 语义=建草稿，成功后
    # draft→reviewed（原置 published 与状态机冲突恒 WARN，第十四期改）
    seen: dict[str, object] = {}

    def _fake_flow(article, path, html, *, account=None, log_path=None, echo=None):
        seen.update(account=account, has_html="<html" in html or "正文" in html or bool(html))
        return "MID-SMOKE-1"

    monkeypatch.setattr(_publish_service, "publish_flow", _fake_flow)
    media_id = publish_draft(article_root / "sample_article.md", account="myacct")
    assert media_id == "MID-SMOKE-1"
    assert seen["account"] == "myacct" and seen["has_html"] is True
    assert read_status(article_root / "sample_article.md") == "reviewed"


def test_publish_draft_auto_reviewed_tolerant_of_other_states(
    article_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1 置位宽容：已 reviewed 保持；已 published 不回落（其余迁移归人工）。"""
    monkeypatch.setattr(_publish_service, "publish_flow", lambda *a, **k: "MID-2")

    reviewed = article_root / "sample_article.md"
    set_status(reviewed, "reviewed")
    publish_draft(reviewed)
    assert read_status(reviewed) == "reviewed"

    published = article_root / "published_article.md"
    shutil.copy(FIXTURES / "sample_article.md", published)
    set_status(published, "reviewed")
    set_status(published, "published")
    publish_draft(published)
    assert read_status(published) == "published"


def test_publish_draft_refuses_on_gate_failure(
    article_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门失败（无封面 FAIL）→ 拒绝建草稿，publish_flow 不被触达。"""
    (article_root / "no_cover.md").write_text(
        "---\ntitle: 无封面\n---\n\n正文。\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        _publish_service, "publish_flow", lambda *a, **k: pytest.fail("门失败不得触达上传")
    )
    with pytest.raises(ValueError, match="审核未通过"):
        publish_draft(article_root / "no_cover.md")


def test_submit_and_track_returns_url_and_logs(
    article_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交发表编排：mock token/submit/轮询 → 返回文章链接并回写发布日志。"""
    url = "https://mp.weixin.qq.com/s/smoke-track"
    monkeypatch.setattr(_publish_service, "get_access_token", lambda account=None: "TOK")

    def _fake_submit(token: str, draft_id: str, **kw: object) -> str:
        # 与真实 publisher.submit_publish 同款：成功即落一条发表日志（含 publish_id）
        from musespiral.infra.storage.jsonl import append_log, default_log_path

        append_log(
            default_log_path(),
            {"action": "publish", "publish_id": "PID-1", "article_url": ""},
            platform="mp",
        )
        return "PID-1"

    monkeypatch.setattr(_publish_service, "submit_publish", _fake_submit)
    monkeypatch.setattr(
        _publish_service,
        "check_publish_status",
        lambda token, publish_id, max_tries=10: {
            "article_detail": {"item": [{"article_url": url}]}
        },
    )
    assert submit_and_track("MID-9", article_root / "sample_article.md") == url
    from musespiral.infra.storage.jsonl import default_log_path

    log = default_log_path()
    assert log.is_file() and url in log.read_text(encoding="utf-8")


# ---- adapt_service ----


def test_generate_variant_and_batch_idempotent(article_root: Path) -> None:
    """单平台（启发式，无 AI）+ 批量幂等续跑：已存在默认产物跳过不覆盖。"""
    out = generate_variant(article_root / "sample_article.md", "xhs")
    assert out == article_root / "sample_article.xhs.md" and out.is_file()

    outputs = generate_batch(article_root / "sample_article.md", ["xhs", "zhihu"])
    assert outputs == [article_root / "sample_article.zhihu.md"]
    assert (article_root / "sample_article.zhihu.md").is_file()


def test_generate_batch_partial_failure_keeps_outputs(
    article_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中途失败 → BatchAdaptError 携带已产出/已跳过清单（重发只补缺口）。"""
    from musespiral.services import adapt_service

    real_generate = adapt_service.adapt.generate  # 先存真实实现，防 mock 自递归

    def _fail_zhihu(src: Path, code: str, out_path=None):
        if code == "zhihu":
            raise RuntimeError("boom")
        return real_generate(src, code, out_path)

    monkeypatch.setattr(adapt_service.adapt, "generate", _fail_zhihu)
    with pytest.raises(BatchAdaptError) as excinfo:
        generate_batch(article_root / "sample_article.md", ["xhs", "zhihu"])
    assert excinfo.value.outputs == [article_root / "sample_article.xhs.md"]
    assert excinfo.value.skipped == []


# ---- pack_service ----


def test_build_pack_for_four_piece(article_root: Path) -> None:
    """物料包四件套：README/payload/checks + cover（limits 由 PLIMITS 注入）。"""
    pack_dir = build_pack_for(article_root / "sample_article.md", "xhs")
    assert pack_dir == article_root / "sample_article.xhs.pack"
    assert (pack_dir / "README.md").is_file()
    assert (pack_dir / "payload.md").is_file()
    assert (pack_dir / "checks.txt").is_file()
    assert (pack_dir / "cover" / "cover.png").is_file()
    assert "标题字数" in (pack_dir / "checks.txt").read_text(encoding="utf-8")  # xhs 限制已注入


# ---- annotate_service ----


def test_annotate_rewrite_writes_preview(article_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """按批注改写：mock AI 返回正文 → 预览件落盘带 ai_model，原文不动，重发拒绝覆盖。"""
    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-anno-test", "anno-model")
    sent: list[str] = []
    monkeypatch.setattr(
        "musespiral.services.annotate_service.chat",
        lambda messages, **kw: sent.append(messages[-1]["content"]) or "改写后的正文。",
    )
    annotations = [
        {"line_start": 2, "line_end": 2, "comment": "改口语化"},
        {"line_start": 5, "line_end": 7, "comment": "补一句结尾"},
    ]
    orig_bytes = (article_root / "sample_article.md").read_bytes()

    out = annotate_rewrite(article_root / "sample_article.md", annotations)
    assert out == article_root / "sample_article.annotated.md" and out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "改写后的正文。" in text
    assert "title: 示例文章：渲染级验收冒烟" in text  # frontmatter 沿袭原文
    assert "ai_model: anno-model" in text
    assert (article_root / "sample_article.md").read_bytes() == orig_bytes  # 原文一字节未动
    assert "第 2 行：改口语化" in sent[0] and "第 5-7 行：补一句结尾" in sent[0]  # 行号进 prompt
    with pytest.raises(FileExistsError, match="已存在"):
        annotate_rewrite(article_root / "sample_article.md", annotations)


def test_annotate_rewrite_empty_annotations(article_root: Path) -> None:
    """空批注 → 可读 ValueError，不触 AI。"""
    with pytest.raises(ValueError, match="没有批注可应用"):
        annotate_rewrite(article_root / "sample_article.md", [])


# ---- fetch_service ----


def test_fetch_to_draft_writes_slug_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """抓取编排：mock fetch_article → root/<slug>.md 草稿；同名冲突递增后缀。"""
    monkeypatch.setattr(
        _fetch_service.url_fetch,
        "fetch_article",
        lambda url: frontmatter.Post("抓来的正文", title="标题：你好 世界", status="draft"),
    )
    out = fetch_to_draft("https://mp.weixin.qq.com/s/abc123", tmp_path)
    assert out == tmp_path / "标题你好世界.md"  # 冒号/空白被 slug 净化剔除
    post = frontmatter.load(out)
    assert post.content == "抓来的正文" and post.metadata["status"] == "draft"

    out2 = fetch_to_draft("https://mp.weixin.qq.com/s/abc123", tmp_path)
    assert out2 == tmp_path / "标题你好世界-2.md"  # O_EXCL 冲突递增后缀


def test_fetch_to_draft_ssrf_gate(tmp_path: Path) -> None:
    """白名单外 URL（github）在抓取前被拦（ValueError，不建任何文件）。"""
    with pytest.raises(ValueError):
        fetch_to_draft("https://github.com/wagic/researchEverything", tmp_path)
    assert list(tmp_path.iterdir()) == []
