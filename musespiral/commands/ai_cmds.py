"""AI 辅助命令组：ai（config/show/learn/check-facts/check + Phase D M4 cache/usage/budget）+ rewrite。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ..infra.ai.profiles import TASKS  # route --task 选项与白名单单一事实源


# ── ai（AI 辅助改写）───────────────────────────────────────
@click.group()
def ai() -> None:
    """AI 辅助改写（OpenAI 兼容接口，仅生成本地文件，不自动发布）。"""


@ai.command("config")
@click.option("--profile", "profile_name", default="default", help="档案名（多提供商各一份；缺省 default）")
@click.option("--base-url", required=True, help="例如 https://api.deepseek.com/v1 或本地 http://127.0.0.1:11434/v1")
@click.option("--api-key", default="", help="API Key（本地 Ollama 可留空）")
@click.option("--model", required=True, help="模型名，例如 deepseek-chat / qwen3:8b")
@click.option("--no-set-default", is_flag=True, help="只写档案不切默认")
def ai_config(profile_name: str, base_url: str, api_key: str, model: str, no_set_default: bool) -> None:
    """配置 AI 档案（多提供商，保存于 ~/.musespiral/ai.json；首个档案自动成为默认）。"""
    from ..infra.ai import save_ai_profile

    save_ai_profile(profile_name, base_url, api_key, model, set_default=not no_set_default)
    key_note = "" if not api_key else "（key 已存，不回显）"
    click.echo(f"✅ 档案「{profile_name}」已保存: {base_url} / {model} {key_note}")


@ai.command("use")
@click.argument("profile_name")
def ai_use(profile_name: str) -> None:
    """切换默认 AI 档案。"""
    from ..infra.ai import AIError, set_default_profile

    try:
        set_default_profile(profile_name)
    except AIError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 默认档案 → {profile_name}")


@ai.command("list")
def ai_list() -> None:
    """列出全部档案（key 掩码）、默认项与任务路由。"""
    from ..infra.ai import list_profiles

    st = list_profiles()
    if not st["profiles"]:
        click.echo("尚无档案。运行: musespiral ai config --profile <名> --base-url ... --model ...")
        return
    for name, prof in st["profiles"].items():
        mark = " ←默认" if name == st["default"] else ""
        ok = "✅" if prof["configured"] else "⚠️ "
        click.echo(f"  {ok} {name}{mark}: {prof['base_url']} / {prof['model']} / key {prof['api_key']}")
    if st["routing"]:
        click.echo("  任务路由:")
        for task, prof in st["routing"].items():
            click.echo(f"    {task} → {prof}")
    else:
        click.echo("  任务路由: （未配置，全部走默认档案）")


@ai.command("route")
@click.option(
    "--task",
    required=True,
    type=click.Choice(TASKS),  # 单一事实源：infra/ai/profiles.TASKS（防两处枚举漂移）
    help="任务键",
)
@click.option("--profile", "profile_name", required=True, help="目标档案名")
def ai_route(task: str, profile_name: str) -> None:
    """设置任务级路由（该任务固定用某档案，如 check_facts 走本地 Ollama）。"""
    from ..infra.ai import AIError, set_route

    try:
        set_route(task, profile_name)
    except AIError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ {task} → {profile_name}")


@ai.command("show")
def ai_show() -> None:
    """查看当前 AI 配置（隐藏 key）。"""
    from ..infra.ai import load_ai_config

    cfg = load_ai_config()
    if not cfg:
        click.echo("AI 未配置。运行: musespiral ai config --base-url ... --model ...（或 --profile 建多档案）")
        return
    key = cfg.get("api_key", "")
    masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "(已设置)"
    click.echo(f"  base_url: {cfg.get('base_url')}")
    click.echo(f"  model:    {cfg.get('model')}")
    click.echo(f"  api_key:  {masked}")


@ai.command("check")
@click.option("--task", "task", type=click.Choice(TASKS), default=None,
              help="只检查该任务（缺省=全部任务逐一检查）")
@click.option("--profile", "profile_name", default=None,
              help="强制用指定档案（覆盖任务路由；不改写 ai.json）")
def ai_check(task: str | None, profile_name: str | None) -> None:
    """逐任务连通检查：最小真实请求「回复：OK」级，汇总 任务×档案×通/不通（F45）。"""
    from ..infra.ai.check import run_check
    from ..infra.ai.profiles import list_profiles

    st = list_profiles()
    if not st["profiles"]:
        click.echo("❌ AI 未配置（无任何档案）。运行: musespiral ai config --profile <名> --base-url ... --model ...", err=True)
        sys.exit(1)
    if profile_name is not None and profile_name not in st["profiles"]:
        known = "/".join(sorted(st["profiles"])) or "（空）"
        click.echo(f"❌ 档案不存在: {profile_name}（可用: {known}）", err=True)
        sys.exit(1)

    click.echo("AI 连通性检查（逐任务最小真实请求「回复：OK」级，走既有 chat 流式/超时；仅 stdout 不落盘）")
    results = run_check([task] if task else None, profile=profile_name)
    w_task = max(len(r.task) for r in results)
    w_prof = max(len(r.profile) for r in results)
    for r in results:
        mark, secs = ("✅", f"{r.elapsed:.1f}s") if r.ok else ("❌", "")
        cell = f"「{r.detail}」" if r.ok else r.detail
        tail = f" {secs} {cell}".rstrip()
        click.echo(f"  {mark} {r.task:<{w_task}}  {r.profile:<{w_prof}}  {r.kind}{tail}")
    ok_n = sum(1 for r in results if r.ok)
    total = len(results)
    if ok_n == total:
        click.echo(f"汇总: {ok_n}/{total} 通")
        return
    fails: dict[str, int] = {}
    for r in results:
        if not r.ok:
            fails[r.kind] = fails.get(r.kind, 0) + 1
    breakdown = "、".join(f"{k}×{n}" for k, n in sorted(fails.items()))
    click.echo(f"汇总: {ok_n}/{total} 通，不通 {total - ok_n}（分型: {breakdown}）", err=True)
    sys.exit(1)


# ── ai cache / usage / budget（Phase D F79.2，M4 运维命令）──────────
# 三命令全是 L1 数据面的薄壳呈现：cache=条目概览/分布/全清；usage=台账
# 日汇总（读侧现算不回写）；budget=三层数值 + 点路径 set（tmp+replace 原子写）。


@ai.command("cache")
@click.option("--stats", "show_stats", is_flag=True, help="附按 task 分布表")
@click.option("--clear", is_flag=True, help="全清缓存并打印清除条目数（派生状态非资产，零副作用）")
def ai_cache(show_stats: bool, clear: bool) -> None:
    """AI 输出缓存概览（~/.musespiral/cache/ai；条目只存输出与元数据，无 prompt 正文）。"""
    from ..infra.ai import cache as cache_mod

    if clear:
        removed = cache_mod.clear()
        click.echo(f"🧹 已清除 {removed} 个缓存条目（重建=重调 AI，零副作用）。")
        return
    s = cache_mod.stats()
    click.echo(f"缓存条目: {s['entries']}/{s['max_entries']}  总字符: {s['chars']}")
    click.echo(f"最旧条目: {s['oldest'] or '（空）'}")
    click.echo(f"目录: {s['dir']}")
    if show_stats and s["by_task"]:
        click.echo("按任务分布:")
        for task, count in s["by_task"].items():
            click.echo(f"  {task or '（未知）'}: {count}")


@ai.command("usage")
@click.option("--days", default=7, show_default=True, type=click.IntRange(min=1, max=90),
              help="汇总窗口（自然日，含今天）")
def ai_usage(days: int) -> None:
    """AI 用量日汇总：行=任务×档案，列=调用/命中/命中率/字符（读侧现算不回写）。"""
    from ..infra.ai import usage as usage_mod

    s = usage_mod.summarize(days=days)
    window = s["window"]
    click.echo(f"AI 用量（{window[0]} ~ {window[-1]}，{s['days']} 日；calls 只计未命中出账行）:")
    if not s["rows"]:
        click.echo("（无记录）")
    else:
        click.echo(f"{'日期':<10}  {'任务':<16}  {'档案':<8}  {'调用':>4}  {'命中':>4}  {'命中率':>6}  {'in':>8}  {'out':>8}")
        for r in s["rows"]:
            click.echo(
                f"{r['date']:<10}  {r['task']:<16}  {r['profile']:<8}  "
                f"{r['calls']:>4}  {r['hits']:>4}  {r['hit_rate']:>6.0%}  "
                f"{r['chars_in']:>8}  {r['chars_out']:>8}"
            )
    t = s["totals"]
    click.echo(f"合计: 调用 {t['calls']} / 命中 {t['hits']} / in {t['chars_in']} / out {t['chars_out']}")


@ai.command("budget")
@click.option("--set", "sets", multiple=True, metavar="KEY=VALUE",
              help="点路径设置（可重复）：daily_calls=150 / daily_out_chars=300000 / per_task.<task>.daily_calls=10")
def ai_budget(sets: tuple[str, ...]) -> None:
    """AI 日预算（全局 + per_task 覆盖，--set 点路径调整；与 ai.json 分离）。"""
    from ..infra.ai import budget as budget_mod

    if sets:
        for expr in sets:
            try:
                key, value, _data = budget_mod.apply_set(expr)
            except ValueError as e:
                click.echo(f"❌ {e}", err=True)
                sys.exit(1)
            click.echo(f"✅ {key} = {value}")
        click.echo("")
    data = budget_mod.load()
    click.echo(f"全局: daily_calls={data['daily_calls']}  daily_out_chars={data['daily_out_chars']}")
    per_task = data["per_task"]
    if per_task:
        click.echo("per_task 覆盖（该任务用自己的计数比自己的上限）:")
        for task, row in sorted(per_task.items()):
            joined = "、".join(f"{k}={v}" for k, v in sorted(row.items()))
            click.echo(f"  {task}: {joined}")
    else:
        click.echo("per_task 覆盖: （无）")


@ai.command("learn")
@click.option("--draft", "draft_path", required=True, type=click.Path(exists=True, dir_okay=False), help="AI 底稿路径")
@click.option("--final", "final_path", required=True, type=click.Path(exists=True, dir_okay=False), help="用户终稿路径")
def ai_learn(draft_path: str, final_path: str) -> None:
    """从底稿↔终稿 diff 学习改稿习惯（追加到 ~/.musespiral/habits.json）。"""
    from ..core.habits import extract_habits, save_habits

    habits = extract_habits(
        Path(draft_path).read_text(encoding="utf-8"),
        Path(final_path).read_text(encoding="utf-8"),
    )
    if not habits:
        click.echo("本次未学到新习惯（底稿与终稿没有命中规则的变化）")
        return
    target = save_habits(habits)
    click.echo(f"✅ 学到 {len(habits)} 条改稿习惯，已追加到 {target}:")
    for h in habits:
        click.echo(f"  - {h.rule}: {h.example}")
    click.echo("   musespiral rewrite 时会自动注入这些习惯")


@ai.command("check-facts")
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--note", required=True, type=click.Path(exists=True, dir_okay=False), help="原笔记路径")
def ai_check_facts(article: str, note: str) -> None:
    """AI 事实自检：列出文章中无原笔记依据的增量事实（只标记不否决）。"""
    from ..core.content.parser import parse_article
    from ..core.quality.banned_words import load_words, scan
    from ..core.quality.factcheck import FactcheckNotConfigured, FactcheckUnavailable, check_facts

    # 违禁词命中（F33A 遗留末句）：本地静态词表，与 check 清单同一实现；
    # 报告内同列续编呈现（只标记不否决，AI 不可用时也照常给出）
    banned = scan(parse_article(article).raw_body, load_words())

    try:
        flags = check_facts(note, article)
    except FactcheckNotConfigured as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except FactcheckUnavailable as e:
        click.echo(f"⚠️  {e}", err=True)
        flags = None
    if flags:
        click.echo(f"发现 {len(flags)} 条增量事实（原笔记中无依据，请人工核对）:")
        for i, f in enumerate(flags, 1):
            where = f"（{f['where']}）" if f["where"] else ""
            click.echo(f"  {i}. {f['claim']}{where}")
    elif flags is not None:
        click.echo("✅ 未发现增量事实（文章断言均可在原笔记找到依据）")
    if banned:
        base = len(flags or [])
        click.echo(f"违禁词命中 {len(banned)} 处（同列呈现，只标记不否决）:")
        for j, (word, lineno) in enumerate(banned, 1):
            click.echo(f"  {base + j}. {word}×L{lineno}")


@click.command()
@click.argument("note", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", "-o", required=True, type=click.Path(), help="输出的文章路径")
@click.option("--words", default=800, help="目标字数")
@click.option("--theme", default="lapis", help="排版主题")
@click.option("--cover", default="./assets/cover.png", help="封面图路径")
def rewrite(note: str, out: str, words: int, theme: str, cover: str) -> None:
    """用 AI 把笔记改写成文章（产出本地文件，需人工审核后才发布）。"""
    from ..infra.ai import AIError
    from ..services.rewrite_service import rewrite as ai_rewrite

    try:
        dst = ai_rewrite(note, out, words=words, theme=theme, cover=cover)
    except (AIError, FileExistsError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已生成 AI 改写稿: {dst}")
    click.echo("   ⚠️  必须人工审核后再发布: musespiral check → musespiral preview → musespiral publish")
