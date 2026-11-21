"""E1 一键金路径：check→title→cover→adapt→pack 五步混合管线（jobs kind=pipeline）。

混合管线（LLM 步=title+adapt；2026-11-18 集成轮勘正——原「其余四步确定性」
与 core/adaptation/generator 实现不符）：title 一步 ai.chat(task=title)
（title_service→core.quality.title，未配置/失败自带启发式降级照常写回）；
adapt 配置态四平台各一次 ai.chat(task=adapt)，未配置时 xhs/zhihu 启发式降级、
channels/douyin 抛 AdaptNeedsAI（可读引导 musespiral ai config——无 AI 环境
管线必停于 adapt）；check/cover/pack 三步确定性本地调用。本服务
**零业务规则新增**——每步只是既有 service 入口的串行编排（非子进程；
agent plan 留给纯 LLM 链）：

  check → core.quality.checks.run_checks（musespiral check 同一审核清单）
  title → title_service.improve_title（候选→写回 frontmatter，版本池备份）
  cover → cover_service.generate_cover（title-card 默认路线，mp 规格写回）
  adapt → adapt_service.generate_batch（四平台；同名变体已存在跳过不覆盖）
  pack  → pack_service.build_pack_for（逐平台，variant=adapt 产物）

步级纪律：每步记录 ``{step, status, detail, artifacts}``（status ∈
ok|skipped|failed）；**失败即停**——失败步之后的步骤不执行，已完成步与
失败步（含原因）保留在 ``result.steps``。job result 延续「计数无正文」
惯例：steps_done / failed_step? / artifacts[]（+ 紧凑 steps 状态行，供
CLI 与前端步级呈现，不含任何正文文本）。

幂等/安全：
  - cover 已存在跳过重生成选项（``skip_existing_cover``；缺省 False=重生成
    直接覆盖，与 cover_service「再生成是常态」幂等语义一致）；
  - adapt 沿 generate_batch 既有语义：同名变体已存在跳过（不覆盖、可续跑）；
  - pack 同目录冲突→可读错误（FileExistsError 语义透传，提示 --skip pack）。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..core.content.parser import parse_article
from ..core.quality.checks import has_failures, run_checks
from ..jobs.runner import JobFailure
from . import adapt_service, cover_service, pack_service
from .title_service import improve_title

# 五步固定序（契约 §3.7：check→title→cover→adapt→pack）
STEP_NAMES: tuple[str, ...] = ("check", "title", "cover", "adapt", "pack")
# adapt/pack 的「四平台」（core.adaptation PLATFORM_SPECS 的 F14 经典四平台）
ADAPT_PLATFORMS: tuple[str, ...] = ("xhs", "channels", "douyin", "zhihu")
# cover 步默认路线（契约 §3.7：title-card 默认）
COVER_ROUTE = "title-card"


class PipelineServiceError(ValueError):
    """提交期校验失败（构造期即抛——零 job；studio 端点转 200 {ok:false}）。"""


class _StepFailure(RuntimeError):
    """步内失败：message 已可读；artifacts 携带该步已产出的部分产物。"""

    def __init__(self, message: str, artifacts: Sequence[Path] = ()) -> None:
        super().__init__(message)
        self.artifacts = list(artifacts)


def _display(path: Path, root: Path | None) -> str:
    """展示路径：root 内相对化（studio 语境），否则原样字符串。"""
    if root is not None:
        try:
            return str(path.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass  # root 外路径不强制相对化（如临时目录产物）
    return str(path)


def _normalize_skip(skip: Iterable[str] | None) -> list[str]:
    """skip 归一：去空白、保序去重；枚举外名字 → PipelineServiceError。"""
    out: list[str] = []
    for raw in skip or ():
        name = str(raw).strip()
        if name and name not in out:
            out.append(name)
    unknown = [n for n in out if n not in STEP_NAMES]
    if unknown:
        raise PipelineServiceError(
            f"未知步骤名: {'/'.join(unknown)}（可选: {'/'.join(STEP_NAMES)}）"
        )
    return out


def _resolve_article(article_path: str | Path, root: Path | None) -> Path:
    """文章路径解析：相对路径按 root 拼、存在性校验（构造期，零 job）。"""
    path = Path(article_path)
    if root is not None and not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        raise PipelineServiceError(f"文章不存在: {article_path}")
    return path


# ── 五步步体（每步返回 (status, detail, artifacts)；失败抛 _StepFailure）───
# status ∈ ok|skipped：skipped = 步已执行但零动作（如 cover 已存在跳过重生成
# 的幂等续跑分支），与 --skip 旗标的整步跳过（未执行）是两种语义。


def _step_check(article: Path) -> tuple[str, str, list[Path]]:
    """check：musespiral check 同一审核清单（格式级，不接 --render）。"""
    results = run_checks(parse_article(article), article)
    fails = [msg for level, msg in results if level == "FAIL"]
    if fails:
        raise _StepFailure("；".join(fails))
    passes = sum(1 for level, _ in results if level == "PASS")
    warns = sum(1 for level, _ in results if level == "WARN")
    return "ok", f"审核通过：{passes} PASS / {warns} WARN / 0 FAIL", []


def _step_title(article: Path) -> tuple[str, str, list[Path]]:
    """title：LLM 步（自带降级）——第一条候选写回 frontmatter。"""
    picked = improve_title(article)
    return "ok", f"标题已写回「{picked}」（旧版入版本池，musespiral restore 可恢复）", []


def _step_cover(article: Path, *, skip_existing: bool) -> tuple[str, str, list[Path]]:
    """cover：title-card 默认路线（mp 规格落 cover.png + 写回 frontmatter）。"""
    out = article.parent / cover_service.DEFAULT_COVER_NAME
    if skip_existing and out.is_file():
        return "skipped", f"{out.name} 已存在，跳过重生成（幂等续跑）", [out]
    png = cover_service.generate_cover(article, route=COVER_ROUTE)
    return "ok", "title-card 封面已生成并写回 frontmatter cover", [png]


def _step_adapt(
    article: Path, on_progress: Callable[[int, int], None] | None = None
) -> tuple[str, str, list[Path]]:
    """adapt：LLM 依赖步（配置态四平台各一次 chat；未配置 xhs/zhihu 启发式、
    channels/douyin 抛 AdaptNeedsAI）——generate_batch 幂等续跑，同名变体跳过不覆盖。"""
    skipped: list[Path] = []
    try:
        outputs = adapt_service.generate_batch(
            article, list(ADAPT_PLATFORMS), on_progress=on_progress, skipped=skipped
        )
    except adapt_service.BatchAdaptError as exc:
        raise _StepFailure(
            f"变体生成失败：{exc}（已生成 {len(exc.outputs)}、跳过 {len(exc.skipped)}"
            "——重发续跑只补缺口）",
            artifacts=[*exc.outputs, *exc.skipped],
        ) from None
    variants = [
        article.with_name(f"{article.stem}.{code}.md") for code in ADAPT_PLATFORMS
    ]
    return (
        "ok",
        f"四平台变体就绪：新生成 {len(outputs)}、已存在跳过 {len(skipped)}",
        variants,
    )


def _step_pack(article: Path) -> tuple[str, str, list[Path]]:
    """pack：逐平台物料包（variant=adapt 产物；同目录冲突=可读错误）。"""
    packs: list[Path] = []
    for code in ADAPT_PLATFORMS:
        variant = article.with_name(f"{article.stem}.{code}.md")
        try:
            packs.append(
                pack_service.build_pack_for(
                    article, code, variant=variant if variant.is_file() else None
                )
            )
        except FileExistsError as exc:
            raise _StepFailure(
                f"物料包目录冲突：{exc}（重跑请 --skip pack 或先清理该目录）",
                artifacts=packs,
            ) from None
    return "ok", "四平台物料包已生成（README/payload/checks/cover 四件套）", packs


# ── 管线主体 ───────────────────────────────────────────────────────


def run_pipeline(
    article_path: str | Path,
    *,
    skip: Iterable[str] = (),
    root: str | Path | None = None,
    skip_existing_cover: bool = False,
    on_phase: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """串行执行五步金路径，返回步级记录与产物清单（失败即停，不抛步内异常）。

    返回结构（job result 同形，「计数无正文」）::

        {"article", "status": done|failed, "steps_done", "steps", "artifacts",
         "failed_step"?}
         - steps: [{"step", "status": ok|skipped|failed, "detail", "artifacts"}]
           （失败时只含已完成步 + 失败步，含原因）
    """
    article = Path(article_path)
    root_path = Path(root) if root is not None else None
    skip_names = _normalize_skip(skip)
    total = len(STEP_NAMES)

    steps: list[dict[str, Any]] = []
    artifacts: list[str] = []
    failed_step: str | None = None

    for index, name in enumerate(STEP_NAMES, 1):
        if name in skip_names:
            steps.append(
                {"step": name, "status": "skipped", "detail": "--skip 指定跳过", "artifacts": []}
            )
            if on_phase is not None:
                on_phase(f"{index}/{total} {name}:skipped")
            continue
        if on_phase is not None:
            on_phase(f"{index}/{total} {name}")

        step_status, detail, produced = "ok", "", []
        try:
            if name == "check":
                step_status, detail, produced = _step_check(article)
            elif name == "title":
                step_status, detail, produced = _step_title(article)
            elif name == "cover":
                step_status, detail, produced = _step_cover(
                    article, skip_existing=skip_existing_cover
                )
            elif name == "adapt":
                progress = (
                    (lambda i, n: on_phase(f"{index}/{total} adapt {i}/{n}"))
                    if on_phase is not None
                    else None
                )
                step_status, detail, produced = _step_adapt(article, on_progress=progress)
            else:  # pack
                step_status, detail, produced = _step_pack(article)
        except _StepFailure as exc:
            step_artifacts = [_display(p, root_path) for p in exc.artifacts]
            steps.append(
                {"step": name, "status": "failed", "detail": str(exc), "artifacts": step_artifacts}
            )
            artifacts.extend(step_artifacts)
            failed_step = name
            break
        except Exception as exc:  # 防御面：未知异常也转可读失败（失败即停）
            message = str(exc).strip() or exc.__class__.__name__
            steps.append(
                {"step": name, "status": "failed", "detail": message, "artifacts": []}
            )
            failed_step = name
            break
        step_artifacts = [_display(p, root_path) for p in produced]
        steps.append(
            {"step": name, "status": step_status, "detail": detail, "artifacts": step_artifacts}
        )
        artifacts.extend(step_artifacts)

    result: dict[str, Any] = {
        "article": _display(article, root_path),
        "status": "failed" if failed_step else "done",
        "steps_done": sum(1 for s in steps if s["status"] in ("ok", "skipped")),
        "steps": steps,
        "artifacts": artifacts,
    }
    if failed_step:
        result["failed_step"] = failed_step
    return result


# ── jobs 接驳（kind=pipeline；对齐 agent/topic_radar submit 先例）────────


def job_fn(
    article_path: str | Path,
    *,
    root: str | Path | None = None,
    skip: Iterable[str] = (),
    skip_existing_cover: bool = False,
    runner: Any = None,
) -> Callable[[str], dict[str, Any]]:
    """把一次金路径包成 jobs 闭包（runner 调 ``fn(job_id)``）。

    失败即停映射为 job failed：JobFailure 携带步级 result（runner 会把
    ``exc.result`` 入库——batch_adapt 部分产物同款机制），error 为
    「金路径在 <step> 步失败：<原因>」可读文案。
    """

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(text: str) -> None:
            if runner is not None:
                runner.set_phase(job_id, text)

        result = run_pipeline(
            article_path,
            skip=skip,
            root=root,
            skip_existing_cover=skip_existing_cover,
            on_phase=_phase,
        )
        if result["status"] == "failed":
            failed = str(result.get("failed_step") or "")
            detail = next(
                (s["detail"] for s in result["steps"] if s["step"] == failed), ""
            )
            raise JobFailure(
                f"金路径在 {failed} 步失败：{detail}", result=result
            )
        return result

    return _run


def submit_to_runner(
    runner: Any,
    article_path: str | Path,
    *,
    root: str | Path | None = None,
    skip: Iterable[str] | None = None,
    skip_existing_cover: bool = False,
) -> str:
    """提交一次金路径 job（kind=pipeline），返回 job_id。

    构造期校验（文章存在、skip 枚举合法）在 ``runner.submit`` **之前**完成
    ——校验不通过抛 PipelineServiceError，零 job（agent_service 先例）。
    调用面锚定：``submit_to_runner(runner, article, root=ctx.root, skip=skip)``
    （studio pipeline_actions 与 test_media_gen 接缝测试同形）。
    """
    article = _resolve_article(article_path, Path(root) if root is not None else None)
    skip_list = _normalize_skip(skip)
    fn = job_fn(
        article,
        root=root,
        skip=skip_list,
        skip_existing_cover=skip_existing_cover,
        runner=runner,
    )
    return runner.submit(
        "pipeline",
        {"article": str(article_path), "skip": skip_list},
        fn,
    )


__all__ = [
    "ADAPT_PLATFORMS",
    "COVER_ROUTE",
    "PipelineServiceError",
    "STEP_NAMES",
    "job_fn",
    "run_pipeline",
    "submit_to_runner",
]
