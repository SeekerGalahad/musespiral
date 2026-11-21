"""E1 一键金路径（pipeline_service + pipeline run CLI）单元测试。

覆盖任务书五条红线：
1. 夹具文章全步跑通五步产物齐（cover 1 + 四平台变体 4 + 物料包 4 = 9 件）；
2. 构造 check FAIL 失败即停（title 未执行断言——无版本池备份/frontmatter 未动）；
3. --skip 旗标（skip 步记录 skipped、不执行）；
4. jobs KINDS 白名单含 pipeline（15 种，存量 14 只增不减）；
5. CLI 快照只增（test_cli_snapshot 另册；此处跑 CLI 金路径与非法 skip 零 job）。

AI 全程 monkeypatch `_post_chat_stream`（test_adapt 同款接缝，不真实联网）；
cover 走 Pillow 兜底（sys.modules 注入 None 复现无浏览器环境，test_cover 同款）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import frontmatter
import pytest
from click.testing import CliRunner
from PIL import Image

from musespiral.cli import cli
from musespiral.infra.ai import client as ai_mod
from musespiral.jobs.runner import JobRunner
from musespiral.jobs.store import KINDS, JobStore
from musespiral.services import pipeline_service
from musespiral.services.title_service import improve_title

ADAPT_PLATFORMS = pipeline_service.ADAPT_PLATFORMS  # xhs/channels/douyin/zhihu


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral（jobs/ai.json/版本池全隔离）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.modules 注入 None → import 确定性抛 ImportError（cards/test_cover 同款）。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)


def _setup_ai(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """配置假档案 + 按 prompt 关键词分发回包（title/adapt 四平台全覆盖）。

    ai.json 落 MUSESPIRAL_HOME（config.home_dir() 现读，与生产同路径解析）。
    """
    import os

    ai_dir = Path(os.environ["MUSESPIRAL_HOME"])
    ai_dir.mkdir(parents=True, exist_ok=True)
    (ai_dir / "ai.json").write_text(
        json.dumps(
            {
                "default": "fake",
                "profiles": {
                    "fake": {"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}
                },
                "routing": {},
            }
        ),
        encoding="utf-8",
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        prompt = " ".join(
            str(m.get("content", "")) for m in (json or {}).get("messages", [])
        )
        if "备选标题" in prompt:  # title 步（core/quality/title._PROMPT）
            # 注意：json 形参遮蔽同名模块——这里直接给字面 JSON 数组串
            content = '["标题候选甲", "标题候选乙", "标题候选丙"]'
        elif "小红书" in prompt:
            content = "缓存优化真的别乱来 ✅\n\n先压测再优化。\n\n话题：#缓存 #性能"
        elif "视频号" in prompt:
            content = (
                "【开场钩子】缓存优化三步走。\n【要点1】先压测。\n"
                "【要点2】过期时间是关键。\n【收尾引导】关注我。\n"
                "封面标题：缓存三步走\n描述：缓存优化实录 #缓存"
            )
        elif "抖音" in prompt:
            content = (
                "【开场钩子】缓存优化。\n【要点1】先压测。\n【收尾引导】关注。\n"
                "封面标题：缓存三步走\n描述：缓存 #缓存"
            )
        else:  # zhihu
            content = "缓存优化实录的知乎版正文，讲清三步走的方法论与实践。"
        return _FakeResp(content)

    monkeypatch.setattr(
        ai_mod, "_post_chat_stream", lambda u, h, b, to: ai_mod.adapt_mock(fake_post, u, h, b, to)
    )


def _png(path: Path, size: tuple[int, int] = (900, 383), color: str = "white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _write_article(tmp_path: Path, *, title: str = "旧标题", name: str = "article.md") -> Path:
    article = tmp_path / name
    article.write_text(
        "---\n"
        f"title: {title}\n"
        "cover: cover.png\n"
        "digest: 这是摘要。\n"
        "tags: [性能, 缓存]\n"
        "---\n\n"
        "# 缓存优化实录\n\n"
        "缓存是提升系统性能的第一道防线。\n\n"
        "## 先压测再优化\n\n不做压测就上缓存，容易优化没人用的路径。\n\n"
        "## 过期时间是关键\n\nTTL 从 5 分钟调到 30 分钟后命中率翻倍。\n",
        encoding="utf-8",
    )
    return article


def _golden_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """全步可通的文章：check 可过（title/cover/digest 齐）+ AI 假档案 + 无浏览器。"""
    _setup_ai(monkeypatch, tmp_path)
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    _png(tmp_path / "cover.png")
    return article


def _wait_terminal(runner: JobRunner, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get(job_id)
        if job is not None and job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.01)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


# ── 红线 1：全步跑通五步产物齐 ───────────────────────────────────────


def test_golden_path_five_steps_full_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)

    result = pipeline_service.run_pipeline(article, root=tmp_path)

    assert result["status"] == "done" and result["steps_done"] == 5
    assert "failed_step" not in result
    assert [s["step"] for s in result["steps"]] == list(pipeline_service.STEP_NAMES)
    assert all(s["status"] == "ok" for s in result["steps"])
    # 产物清单：cover 1 + 四平台变体 4 + 物料包 4 = 9 件，逐一存在
    assert len(result["artifacts"]) == 9
    for rel in result["artifacts"]:
        assert (tmp_path / rel).exists(), f"产物缺失: {rel}"
    assert "cover.png" in result["artifacts"]
    for code in ADAPT_PLATFORMS:
        assert f"article.{code}.md" in result["artifacts"]
        assert f"article.{code}.pack" in result["artifacts"]
    # title 步已写回 AI 首条候选（旧版入版本池）
    fm = frontmatter.load(article)
    assert fm.metadata["title"] == "标题候选甲"
    assert list(tmp_path.glob("article.md.bak.*")), "title/cover 写回应入版本池"
    # check 步 detail 是计数摘要（计数无正文），不含全量报告
    check_detail = result["steps"][0]["detail"]
    assert "PASS" in check_detail and "FAIL" in check_detail


def test_title_step_degrades_without_ai(tmp_path: Path) -> None:
    """title 是 LLM 步但自带降级：无 ai.json → 启发式首条（H1 原文）照常写回。"""
    article = _write_article(tmp_path)

    picked = improve_title(article)

    assert picked == "缓存优化实录"  # 启发式·H1（AI 缺席时首条）
    assert frontmatter.load(article).metadata["title"] == "缓存优化实录"
    assert list(tmp_path.glob("article.md.bak.*")), "写回前旧版应入版本池"


def test_adapt_step_llm_dependency_unconfigured_fails_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adapt 是 LLM 依赖步（契约 2026-11-18 勘正锚）：无 AI 环境 xhs/zhihu 走
    启发式、channels/douyin 抛 AdaptNeedsAI——管线必停于 adapt（可读引导
    musespiral ai config），pack 不执行；「其余四步确定性」表述以此为准不再回潮。"""
    _no_browser(monkeypatch)
    article = _write_article(tmp_path)
    _png(tmp_path / "cover.png")

    result = pipeline_service.run_pipeline(article, root=tmp_path)

    assert result["status"] == "failed" and result["failed_step"] == "adapt"
    assert result["steps_done"] == 3  # check/title/cover 完成；pack 未执行
    adapt = result["steps"][3]
    assert adapt["status"] == "failed"
    assert "启发式降级" in adapt["detail"] and "musespiral ai config" in adapt["detail"]
    # 失败即停的部分产物面：xhs 启发式变体保留，pack 目录未建
    assert "article.xhs.md" in result["artifacts"]
    assert not (tmp_path / "article.xhs.pack").exists()


# ── 红线 2：check FAIL 失败即停（title 未执行断言）────────────────────


def test_check_fail_stops_before_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)
    article.write_text(
        article.read_text(encoding="utf-8").replace("title: 旧标题", "title: "),
        encoding="utf-8",
    )  # 构造 check FAIL：标题为空

    result = pipeline_service.run_pipeline(article)

    assert result["status"] == "failed" and result["failed_step"] == "check"
    assert [s["step"] for s in result["steps"]] == ["check"]  # 失败即停：只含失败步
    assert result["steps"][0]["status"] == "failed"
    assert "title" in result["steps"][0]["detail"]  # FAIL 项含原因（frontmatter.title）
    # title 未执行：frontmatter 未动、零版本池备份、无 cover/变体/物料包产物
    assert frontmatter.load(article).metadata.get("title") in (None, "")
    assert not list(tmp_path.glob("article.md.bak.*"))
    assert result["artifacts"] == []


# ── 红线 3：--skip 旗标 ─────────────────────────────────────────────


def test_skip_flag_skips_named_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)

    result = pipeline_service.run_pipeline(article, skip=["title", "cover"])

    statuses = {s["step"]: s["status"] for s in result["steps"]}
    assert statuses["title"] == "skipped" and statuses["cover"] == "skipped"
    assert statuses["check"] == "ok" and statuses["adapt"] == "ok" and statuses["pack"] == "ok"
    assert result["status"] == "done" and result["steps_done"] == 5
    # skip title：frontmatter 未改、零版本池；skip cover：cover 不在产物清单
    assert frontmatter.load(article).metadata["title"] == "旧标题"
    assert not list(tmp_path.glob("article.md.bak.*"))
    assert "cover.png" not in result["artifacts"]


def test_unknown_skip_name_rejected_zero_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造期校验在 runner.submit 之前——枚举外步骤名 → 可读错误 + 零 job。"""
    article = _golden_fixture(tmp_path, monkeypatch)
    runner = JobRunner()
    try:
        with pytest.raises(pipeline_service.PipelineServiceError, match="未知步骤名"):
            pipeline_service.submit_to_runner(runner, article, skip=["bogus"])
        with pytest.raises(pipeline_service.PipelineServiceError, match="文章不存在"):
            pipeline_service.submit_to_runner(runner, tmp_path / "nope.md")
        assert JobStore().list(limit=None) == []  # 零 job
    finally:
        runner.shutdown(wait=True)


# ── 红线 4：jobs KINDS 白名单（只增不减；15→17：R3 书源接驳批 2026-11-18
# +book_search/+book_fetch；17→18：G-T5 TTS 朗读批 2026-11-19 +tts_generate；
# 18→19：PL-2 插件流水线批 2026-11-20 +plugin_pipeline）
# ────────────────────────────────────────────────────────────────────


def test_jobs_kinds_include_pipeline() -> None:
    assert {"pipeline", "plugin_pipeline"} <= KINDS and len(KINDS) == 19
    for kind in (
        "adapt", "pack", "publish", "batch_adapt", "status", "annotate_rewrite",
        "fetch", "sync_stats", "compose_generate", "ingest", "clip",
        "visual_export", "agent_run", "topic_radar", "book_search", "book_fetch",
        "tts_generate",
    ):
        assert kind in KINDS
    store = JobStore()  # 写入侧校验面（MUSESPIRAL_HOME 已隔离）
    assert store.create("pipeline", {"article": "a.md"})["kind"] == "pipeline"
    with pytest.raises(ValueError, match="未知任务类型"):
        store.create("bogus", {})


# ── jobs 接驳：submit_to_runner（kind=pipeline；失败保留已完成步）────────


def test_submit_golden_path_via_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)
    runner = JobRunner()
    try:
        # 相对路径 + root 解析（studio pipeline_actions 调用面同形）
        job_id = pipeline_service.submit_to_runner(runner, "article.md", root=tmp_path)
        job = _wait_terminal(runner, job_id)
    finally:
        runner.shutdown(wait=True)
    assert job["kind"] == "pipeline" and job["status"] == "done"
    assert job["result"]["steps_done"] == 5
    assert "failed_step" not in job["result"]
    assert len(job["result"]["artifacts"]) == 9
    assert job.get("phase"), "步级进度应写入 job.phase"


def test_job_failure_preserves_steps_and_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败即停的 job 面映射：status=failed + error 可读 + result.steps 保留失败步。"""
    article = _golden_fixture(tmp_path, monkeypatch)
    article.write_text(
        article.read_text(encoding="utf-8").replace("title: 旧标题", "title: "),
        encoding="utf-8",
    )
    runner = JobRunner()
    try:
        job_id = pipeline_service.submit_to_runner(runner, article)
        job = _wait_terminal(runner, job_id)
    finally:
        runner.shutdown(wait=True)
    assert job["status"] == "failed"
    assert "check 步失败" in str(job["error"])
    assert job["result"]["failed_step"] == "check"
    assert [s["step"] for s in job["result"]["steps"]] == ["check"]
    assert job["result"]["steps"][0]["status"] == "failed"


# ── 幂等/安全：cover 跳过重生成 / adapt 已存在跳过 / pack 冲突可读错误 ────


def test_keep_cover_skips_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)
    before = (tmp_path / "cover.png").read_bytes()

    result = pipeline_service.run_pipeline(article, root=tmp_path, skip_existing_cover=True)

    cover_step = next(s for s in result["steps"] if s["step"] == "cover")
    assert cover_step["status"] == "skipped"
    assert (tmp_path / "cover.png").read_bytes() == before  # 未重生成（幂等续跑）
    assert "cover.png" in cover_step["artifacts"]  # 既有封面仍计入产物清单


def test_rerun_pack_conflict_readable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """二跑：adapt 同名变体跳过不覆盖（generate_batch 既有语义）；
    pack 同目录冲突 → 失败即停 + 可读错误。"""
    article = _golden_fixture(tmp_path, monkeypatch)
    first = pipeline_service.run_pipeline(article)
    assert first["status"] == "done"

    second = pipeline_service.run_pipeline(article)

    adapt_step = next(s for s in second["steps"] if s["step"] == "adapt")
    assert adapt_step["status"] == "ok" and "已存在跳过 4" in adapt_step["detail"]
    assert second["status"] == "failed" and second["failed_step"] == "pack"
    pack_step = next(s for s in second["steps"] if s["step"] == "pack")
    assert pack_step["status"] == "failed"
    assert "物料包目录冲突" in pack_step["detail"] and "拒绝覆盖" in pack_step["detail"]
    assert "--skip pack" in pack_step["detail"]  # 可恢复指引


# ── 红线 5：CLI 金路径（pipeline run 提交 job + 步级进度）──────────────


def test_cli_pipeline_run_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _golden_fixture(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, ["pipeline", "run", str(article)])

    assert result.exit_code == 0, result.output
    assert "job_id:" in result.output
    assert "[1/5] check" in result.output and "[5/5] pack" in result.output
    assert "金路径完成：5/5 步，产物 9 件" in result.output
    rows = JobStore().list(limit=None)
    assert rows and rows[0]["kind"] == "pipeline" and rows[0]["status"] == "done"


def test_cli_pipeline_unknown_skip_zero_job(tmp_path: Path) -> None:
    article = _write_article(tmp_path)

    result = CliRunner().invoke(cli, ["pipeline", "run", str(article), "--skip", "bogus"])

    assert result.exit_code == 1
    assert "未知步骤名" in result.output
    assert JobStore().list(limit=None) == []
