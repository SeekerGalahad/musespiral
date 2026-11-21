"""假插件·成稿（夹具）：ranges × segments → 带 `[hh:mm:ss](#t=秒)` 锚的 markdown。

时间戳锚是「视频转文章」的核心增量（正典 §9.3）：夹具输出的锚格式与
正典示例一致，用来验证锚这条数据能从 transcribe 一路活到成稿。
"""


def _stamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def run(inputs: dict, config: dict, ctx: object) -> dict:
    segments = inputs["segments"]
    ranges = inputs["ranges"]
    title = str(config.get("title") or "转写稿")
    by_start = {float(s.get("start", 0.0)): s for s in segments}

    lines = [f"# {title}", ""]
    for r in ranges:
        start = float(r.get("start", 0.0))
        end = float(r.get("end", 0.0))
        seg = by_start.get(start)
        text = str(seg.get("text", "")) if seg else ""
        label = str(r.get("label", "")) or "段落"
        anchor = f"[{_stamp(start)}](#t={int(start)})"
        lines.append(f"- {anchor} **{label}**（{_stamp(start)}–{_stamp(end)}）：{text}")
    if not ranges:
        lines.append("_零关键段：本次转写没有命中任何关键内容。_")
    article = "\n".join(lines) + "\n"

    out = ctx.artifacts_dir / "article.md"
    out.write_text(article, encoding="utf-8")
    ctx.log(f"article-writer: {len(ranges)} 段关键内容 → {out.name}")
    return {"article": article}
