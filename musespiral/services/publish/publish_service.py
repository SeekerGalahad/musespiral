"""发布用例编排：check 门 → render → upload → 建草稿 + 提交发表。

从 studio/actions._action_publish（检查门/建草稿/mark_published）与
commands/publish_cmds.submit（提交发表+轮询）提取的编排逻辑（Phase A
下沉）。与原实现的差异仅是去掉 Studio 层包装：JobFailure/_sanitize_error
/{ok:...} 载荷不再出现——异常原样上抛，由表现层脱敏与格式化。

契约（正典 §三 services/publish_service）：本模块不做渲染引擎（委托
renderer.Renderer）、不直接触微信 API（委托 platforms.wechat.publish_flow
与 publisher.*）。
"""
from __future__ import annotations

from pathlib import Path

from ... import config
from ...core.quality.checks import CheckResult, has_failures, run_checks
from ...core.quality.render_checks import render_checks
from ...core.article import DRAFT, REVIEWED, read_status, set_status
from ...core.content.parser import ParsedArticle, parse_article
from ...platforms.wechat import publish_flow
from ...infra.wechat.publisher import check_publish_status, submit_publish
from ...core.content.renderer import Renderer
from ...infra.storage.jsonl import default_log_path, set_publish_url
from ...infra.wechat.token import get_access_token


def render_article(article: ParsedArticle) -> str:
    """按 frontmatter > 默认值渲染（与 WeChatAdapter._build_renderer 同口径）。

    渲染口径的单一权威定义（原 studio/payload._render 与本模块两处逐字
    相同的私有副本收编于此；payload 侧保留薄壳委托，行为零变更）。
    """
    theme = str(article.metadata.get("theme") or config.DEFAULT_THEME)
    code_theme = str(article.metadata.get("code_theme") or config.DEFAULT_CODE_THEME)
    renderer = Renderer(
        theme_name=theme, code_theme=code_theme, dark=bool(article.metadata.get("dark", False))
    )
    return renderer.render(article)


def _gate(article_path: Path) -> tuple[ParsedArticle, str, list[CheckResult]]:
    """检查门三步：解析 → frontmatter 级检查 → 渲染 + 渲染级检查。

    渲染 HTML 同时供上传链路复用（渲染一次，检查与上传共用——与原
    _action_publish 相同口径）；解析/渲染异常原样上抛。
    """
    article = parse_article(article_path)
    results: list[CheckResult] = list(run_checks(article, article_path))
    html = render_article(article)
    results += render_checks(html, article, article_path)
    return article, html, results


def run_publish_gate(article_path: str | Path) -> list[CheckResult]:
    """完整发布检查门（纯本地，不触网）：返回全部 CheckResult。

    是否拦截由调用方按 has_failures 裁决；渲染失败原样上抛（与
    check --render 同口径的异常语义，格式化归表现层）。
    """
    _, _, results = _gate(Path(article_path))
    return results


def auto_mark_reviewed(path: str | Path) -> None:
    """建草稿成功后的自动置位：draft→reviewed（C1，第十四期顺手批）。

    publish 语义=建草稿非发表——发表是后续人工/submit 的事；原实现置
    published 与状态机冲突（draft→published 非法迁移，真机②实测恒 WARN）。
    现改为 draft→reviewed（合法自动迁移：人审闸门保持，建完草稿即「已审
    待发」）。非 draft 状态（已 reviewed/published/archived）不动——状态机
    其余迁移归人工。
    """
    if read_status(path) == DRAFT:
        set_status(path, REVIEWED)


def publish_draft(
    article_path: str | Path,
    *,
    account: str | None = None,
    log_path: str | Path | None = None,
) -> str:
    """审核门 → 渲染 → 上传图片/封面 → 创建公众号草稿，返回 media_id。

    有 FAIL 即拒绝建草稿（ValueError，消息含全部 FAIL 项——人审前的机器
    闸门不因入口不同而放松）；成功后自动置位 draft→reviewed（C1：publish
    语义=建草稿，原置 published 与状态机冲突恒 WARN；置位失败降级为告警
    不阻塞草稿成功——与 CLI publish / Studio 同一口径；服务层无回显通道，
    告警不上抛）。log_path 缺省走 stats 默认日志。
    """
    path = Path(article_path)
    article, html, results = _gate(path)
    if has_failures(results):
        failures = [message for level, message in results if level == "FAIL"]
        raise ValueError(
            f"审核未通过（{len(failures)} 项 FAIL），已拒绝创建草稿: " + "；".join(failures)
        )
    media_id = publish_flow(article, path, html, account=account, log_path=log_path)
    try:
        auto_mark_reviewed(path)
    except (ValueError, OSError):
        pass  # 草稿已创建，置位失败不影响发布结果
    return media_id


def submit_and_track(draft_id: str, article_path: str | Path) -> str:
    """提交草稿发表并轮询至完成，返回文章链接（无链接时为空串）。

    freepublish submit（publish_id 落日志）→ get 轮询至终态 → 文章链接
    回写发布日志（set_publish_url）。发表是不可撤回动作，二次确认等交互
    归表现层，本函数不做确认。
    """
    path = Path(article_path)
    title = str(parse_article(path).metadata.get("title", ""))
    token = get_access_token()
    publish_id = submit_publish(token, draft_id, article=str(path), title=title)
    result = check_publish_status(token, publish_id)
    article_url = str(
        result.get("article_detail", {}).get("item", [{}])[0].get("article_url", "")
    )
    if article_url:
        log_path = default_log_path()
        set_publish_url(log_path, publish_id, article_url)
        # FEAT-3：发表后自动拉一次微信统计回填该篇 stats（含在看）——best-effort，
        # 失败/无数据内部收敛为 ok=False，绝不阻塞已完成的发表（红线见 analytics）。
        from ...infra.wechat.analytics import backfill_published_stats

        backfill_published_stats(article_url, log_path=log_path)
    return article_url
