"""plan_service 周历编排测试（第十四期 F36，正典 §五 单元+集成）。

覆盖：三态各归位（计划/已交付/已发布各一天，已交付含 draft 与 uploaded
两口径）；空窗计数与周末占位；过期/非法 publish_at 宽容；--week 回看与
非法周序；在制品计数与文章扫描剔除。全部走 tmp（MUSESPIRAL_HOME +
DEFAULT_ARTICLES_ROOT 双隔离），不触真实 ~/musespiral-articles 与 ~/.musespiral。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from musespiral.services import plan_service


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """双隔离环境：MUSESPIRAL_HOME → tmp home、DEFAULT_ARTICLES_ROOT → tmp root。"""
    home = tmp_path / "musespiral-home"
    root = tmp_path / "arts"
    root.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setattr(plan_service, "DEFAULT_ARTICLES_ROOT", root)
    return {"home": home, "root": root}


def _article(root: Path, name: str, *, title: str | None = "标题", status: str | None = None,
             publish_at: object = None, cover: bool = True) -> Path:
    """写一篇测试文章（cover 与 title 至少一项在，构成 musespiral 文章）。"""
    fm = ["---"]
    if title is not None:
        fm.append(f"title: {title}")
    if cover:
        fm.append("cover: cover.png")
    if status is not None:
        fm.append(f"status: {status}")
    if publish_at is not None:
        fm.append(f"publish_at: {publish_at}")
    fm.append("---\n")
    path = root / name
    path.write_text("\n".join(fm) + "\n正文。\n", encoding="utf-8")
    return path


def _log_rows(env: dict[str, Path], *, root_rows=(), home_rows=()) -> None:
    for path, rows in ((env["root"] / "publish_log.jsonl", root_rows),
                       (env["home"] / "publish_log.jsonl", home_rows)):
        if rows:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )


# 今天取 2026-11-11（周三，W46：11-09 一 ~ 11-15 日），周内日期全确定
TODAY = date(2026, 11, 11)


def test_three_states_each_on_own_day(env: dict[str, Path]) -> None:
    """三态各归位：计划命中日 / 已交付（uploaded 与 draft 两口径）/ 已发布。"""
    _article(env["root"], "a.md", title="计划篇", publish_at="2026-11-09")
    _article(env["root"], "b.md", title="交付篇")
    _article(env["root"], "c.md", title="发布篇")
    _log_rows(
        env,
        root_rows=[  # 周三：action=draft（API 建草稿）→ 已交付
            {"ts": "2026-11-11T10:00:00", "action": "draft", "article": "c.md",
             "title": "草稿交付", "media_id": "M1", "platform": "mp"},
        ],
        home_rows=[
            # 周二：result=uploaded（浏览器交付）→ 已交付（无 title 回落文件名）
            {"ts": "2026-11-10T09:00:00", "platform": "xhs", "article": "b.md",
             "result": "uploaded"},
            # 周四：manual 登记 → 已发布
            {"ts": "2026-11-12T20:00:00", "action": "publish", "platform": "mp",
             "article": "c.md", "title": "终发篇", "manual": True},
        ],
    )

    text = plan_service.render_week(today=TODAY)
    lines = text.splitlines()
    assert "11-09 一 · 计划: 计划篇（a.md）" in lines
    assert "11-10 二 · 已交付: xhs b.md" in lines
    assert "11-11 三 · 已交付: mp 草稿交付" in lines
    assert "11-12 四 · 已发布: mp 终发篇" in lines
    assert "本周 计划 1 / 已交付 2 / 已发布 1" in text


def test_empty_workday_marker_and_weekend_dash(env: dict[str, Path]) -> None:
    """空工作日标空窗（N=该日起仍无事件的工作日数）；空周末日画 —。"""
    _article(env["root"], "a.md", title="唯一计划", publish_at="2026-11-09")

    lines = plan_service.render_week(today=TODAY).splitlines()
    by_day = {ln[:5]: ln for ln in lines}
    assert "（空窗——本周还差 4 篇）" in by_day["11-10"]  # 二~五共 4 个空工作日
    assert "还差 1 篇" in by_day["11-13"]  # 周五只剩自己
    assert by_day["11-14"].endswith("· —") and by_day["11-15"].endswith("· —")  # 周末
    assert "空窗" not in by_day["11-14"]  # 周末不算空窗


def test_expired_and_invalid_publish_at_tolerated(env: dict[str, Path]) -> None:
    """过期/非法 publish_at 不报错：周外的静默不显示、周内过期日如实显示。"""
    _article(env["root"], "past_out.md", title="上周计划", publish_at="2026-11-01")
    _article(env["root"], "past_in.md", title="周内过期", publish_at="2026-11-10")
    _article(env["root"], "garbage.md", title="非法值", publish_at="哪天都行")
    _article(env["root"], "bad_date.md", title="非法日期", publish_at="2026-13-99")
    _article(env["root"], "ymd_obj.md", title="裸日期对象", publish_at=date(2026, 11, 13))

    text = plan_service.render_week(today=TODAY)
    assert "上周计划" not in text  # 周外：本周视图不显示
    assert "11-10 二 · 计划: 周内过期（past_in.md）" in text.splitlines()  # 过期如实显示
    assert "11-13 五 · 计划: 裸日期对象（ymd_obj.md）" in text.splitlines()  # YAML date 对象
    assert "非法值" not in text and "非法日期" not in text
    assert "本周 计划 2" in text


def test_week_lookback_and_invalid_spec(env: dict[str, Path]) -> None:
    """--week 回看只显指定周；非法周序（格式/越界/不存在）ValueError。"""
    _log_rows(
        env,
        home_rows=[
            {"ts": "2026-11-03T09:00:00", "action": "publish", "platform": "mp",
             "article": "old.md", "title": "上周发布"},
            {"ts": "2026-11-10T09:00:00", "action": "publish", "platform": "mp",
             "article": "now.md", "title": "本周发布"},
        ],
    )

    w45 = plan_service.render_week(week="2026-W45", today=TODAY)
    assert "11-03 二 · 已发布: mp 上周发布" in w45.splitlines()
    assert "本周发布" not in w45
    assert w45.startswith("周历 2026-W45")

    assert "已发布: mp 本周发布" in plan_service.render_week(today=TODAY)

    for bad in ("2026-W54", "2026-W00", "2026w47", "W47", "2026-W"):
        with pytest.raises(ValueError):
            plan_service.render_week(week=bad, today=TODAY)


def test_wip_counts_and_scan_filters(env: dict[str, Path]) -> None:
    """在制品=draft+reviewed 计数；衍生/隐藏/非 musespiral 文章不进统计。"""
    _article(env["root"], "draft.md", title="草稿篇")  # 无 status → draft
    _article(env["root"], "reviewed.md", title="已审篇", status="reviewed")
    _article(env["root"], "published.md", title="已发篇", status="published")
    _article(env["root"], "archived.md", title="归档篇", status="archived")
    _article(env["root"], "v.xhs.md", title="变体篇")  # 平台变体：衍生文件
    _article(env["root"], "n.note.md", title="配对笔记")  # 衍生笔记
    _article(env["root"], "plain.md", title=None, cover=False)  # 非 musespiral 文章
    (env["root"] / ".musespiral").mkdir()
    _article(env["root"] / ".musespiral", "hidden.md", title="隐藏篇")

    text = plan_service.render_week(today=TODAY)
    assert "在制品（draft+reviewed）2" in text
    assert "变体篇" not in text and "配对笔记" not in text and "隐藏篇" not in text


def test_bad_frontmatter_and_missing_root_tolerated(env: dict[str, Path]) -> None:
    """坏 frontmatter 文章跳过；root 整体缺失也能渲染空周历不报错。"""
    (env["root"] / "broken.md").write_text(
        "---\ntitle: [未闭合\n---\n正文\n", encoding="utf-8"
    )
    text = plan_service.render_week(today=TODAY)
    assert text.startswith("周历 2026-W46")

    missing = plan_service.render_week(root=env["root"].parent / "no-such", today=TODAY)
    assert "本周 计划 0 / 已交付 0 / 已发布 0" in missing


def test_register_manual_publish_validates_and_defaults(env: dict[str, Path]) -> None:
    """登记编排：article 须在 root 内；title 缺省读 frontmatter；article 写相对路径。"""
    art = _article(env["root"], "c.md", title="终发标题")

    row = plan_service.register_manual_publish("c.md", "mp", url="https://s/x")
    assert row["article"] == "c.md" and row["title"] == "终发标题"
    assert row["manual"] is True and row["url"] == "https://s/x"

    row2 = plan_service.register_manual_publish(str(art), "xhs", title="覆盖")  # 绝对路径亦可
    assert row2["article"] == "c.md" and row2["title"] == "覆盖" and "url" not in row2

    with pytest.raises(ValueError, match="不存在"):
        plan_service.register_manual_publish("nope.md", "mp")
    outside = env["root"].parent / "outside.md"
    outside.write_text("---\ntitle: 外部\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不在 root 内"):
        plan_service.register_manual_publish(str(outside), "mp")
