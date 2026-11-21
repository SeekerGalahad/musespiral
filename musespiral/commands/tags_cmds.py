"""素材标签建议命令组：tags（suggest / batch）——Phase D F77。

AI 只建议、人工采纳才写 triage.tags（A 档 §1.2 tags.py 唯一契约）：
  - `musespiral tags suggest <material_id>`：同步调服务层（task=tag_suggest，
    cache=true——同素材重看建议零网络）→ 打印建议清单（≤5 行带编号，
    建议撞既有标「已有」）→ 输入要采纳的编号（如 `1 3 4`）；
    **直接回车=全拒=零落盘**（预览即焚，refine 判例同款）；
  - `musespiral tags batch [--limit 20]`：选无 tags 素材 → 每批 ≤16 个素材走
    一次 tag_batch 计划（interp 步数上限裁决，服务层 batch）→ 逐素材
    打印建议+confirm（y=采纳全部建议 / n=拒绝 / s=跳过）→ 逐条经 triage
    窄门写入；**Ctrl-C 中止=已确认项保留、剩余跳过**。
"""
from __future__ import annotations

import click

from ..core.agent import budgets
from ..services import materials_service, tag_service

# tag_batch 计划的步数上限（=interp 预算 RUN_STEPS；分批大小同源不另立）
_BATCH_CHUNK = budgets.RUN_STEPS


def _fmt_tags(tags: list[str]) -> str:
    return "、".join(tags) if tags else "（无）"


def _parse_indices(raw: str, upper: int) -> list[int]:
    """把 `1 3 4` 形态的输入解析为有效下标（去重保序；越界/非数字忽略）。"""
    picked: list[int] = []
    for token in raw.split():
        if not token.isdigit():
            continue
        idx = int(token)
        if 1 <= idx <= upper and idx - 1 not in picked:
            picked.append(idx - 1)
    return picked


def _echo_suggestions(item: dict) -> None:
    """打印单条建议（编号 + 「已有」冲突标记——冲突档① 的 CLI 呈现）。"""
    existing = set(item.get("existing") or [])
    click.echo(f"《{item.get('title') or item['id']}》建议标签：")
    if not item.get("tags"):
        click.echo("  （无建议——AI 未给出有效标签）")
        return
    for i, tag in enumerate(item["tags"], start=1):
        mark = "  [已有]" if tag in existing else ""
        click.echo(f"  {i}. {tag}{mark}")
    click.echo(f"  既有标签: {_fmt_tags(item.get('existing') or [])}")


@click.group()
def tags() -> None:
    """素材标签建议（Phase D F77）：AI 建议，人工采纳才写 triage.tags。"""


@tags.command("suggest")
@click.argument("material_id")
def tags_suggest(material_id: str) -> None:
    """单篇建议：AI 出 ≤5 个标签，勾选编号采纳（直接回车=全拒零落盘）。"""
    try:
        item = tag_service.preview(material_id)
    except Exception as err:  # TagError/AIError 族可读降级（未配置引导原样透出）
        raise click.ClickException(str(err)) from None
    _echo_suggestions(item)
    if not item.get("tags"):
        return
    raw = click.prompt(
        "输入要采纳的编号（空格分隔；直接回车=全拒）", default="", show_default=False
    ).strip()
    if not raw:
        click.echo("已全部拒绝（预览即焚，零落盘）")
        return
    picked = [item["tags"][i] for i in _parse_indices(raw, len(item["tags"]))]
    if not picked:
        click.echo("无有效编号，视同全拒（零落盘）")
        return
    try:
        result = tag_service.adopt(material_id, picked)
    except Exception as err:
        raise click.ClickException(str(err)) from None
    if result["dropped"]:
        click.echo(f"超 12 上限被弃（既有优先）: {_fmt_tags(result['dropped'])}")
    click.echo(f"已写入 triage.tags: {_fmt_tags(result['tags'])}")


@tags.command("batch")
@click.option("--limit", default=20, show_default=True, help="最多处理的素材个数")
def tags_batch(limit: int) -> None:
    """批量建议：无标签素材逐个确认（y=采纳/n=拒绝/s=跳过；Ctrl-C 保留已确认）。"""
    candidates = [
        m for m in materials_service.list_materials()
        if not (m.get("triage") or {}).get("tags")
    ][:limit]
    if not candidates:
        click.echo("没有待建议素材（无标签素材为零）")
        return
    total = len(candidates)
    click.echo(f"待建议素材 {total} 个（每批 ≤{_BATCH_CHUNK}，共 {(total + _BATCH_CHUNK - 1) // _BATCH_CHUNK} 批）")
    confirmed = 0
    try:
        for start in range(0, total, _BATCH_CHUNK):
            chunk = candidates[start : start + _BATCH_CHUNK]
            run = tag_service.batch([m["id"] for m in chunk])
            if run["status"] != "done":
                click.echo(f"批次未完成（{run['status']}）: {run.get('error') or '预算触停，未跑素材按无建议处理'}")
            for item in run["items"]:
                _echo_suggestions(item)
                if not item.get("tags"):
                    continue
                action = click.prompt(
                    "采纳? [y=全部采纳/n=拒绝/s=跳过]", default="y", show_default=True
                ).strip().lower()
                if action == "y":
                    try:
                        result = tag_service.adopt(item["id"], item["tags"])
                    except Exception as err:
                        click.echo(f"  写入失败（跳过）: {err}")
                        continue
                    if result["dropped"]:
                        click.echo(f"  超 12 上限被弃（既有优先）: {_fmt_tags(result['dropped'])}")
                    click.echo(f"  已写入: {_fmt_tags(result['tags'])}")
                    confirmed += 1
                elif action == "n":
                    click.echo("  已拒绝（零落盘）")
                # s（以及其他输入）=跳过，不写不问
    except KeyboardInterrupt:
        click.echo(f"\n已中止：已确认 {confirmed} 项保留，其余跳过")
        return
    click.echo(f"完成：采纳 {confirmed}/{total}")
