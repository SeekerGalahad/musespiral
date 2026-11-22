"""FEAT-P1B 写作统计（services/writing_stats.py + GET /api/writing_stats）。

数据源=各 ``*.compose`` 的 session.json rounds 块记录 + ``rounds/<n>.md`` mtime。
覆盖：
  - daily_stats：三日 session 造数 → 每日增量正确、跨日边界（近 30 天窗口含首尾）
  - 增量口径：同会话逐轮取 max(0, chars_n - chars_{n-1})，继承块不虚报
  - streak：current（今天未写不断连胜）/ longest（历史最长）
  - weekly_goal：周一→今天累加 + pct
  - 空数据降级：无 root / 无会话 / 轮次缺 date 证据 → empty=True 全零不抛错
  - 端点契约：/api/writing_stats 200 + 结构 + target 非法 400
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from musespiral.services import writing_stats


def _mk_session(root: Path, slug: str, rounds: list[tuple[int, str, datetime]]) -> Path:
    """造一个会话：rounds 的 (n, text, mtime)；session.json 按真实形状落盘。"""
    d = root / f"{slug}.compose"
    (d / "rounds").mkdir(parents=True)
    payload = []
    for n, text, when in rounds:
        payload.append({"n": n, "blocks": [{"id": f"b{n}", "kind": "para", "text": text}]})
        p = d / "rounds" / f"{n}.md"
        p.write_text(text, encoding="utf-8")
        os.utime(p, (when.timestamp(), when.timestamp()))
    (d / "session.json").write_text(
        json.dumps({"slug": slug, "title": slug, "sources": [], "status": "composing",
                    "rounds": payload}, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


TODAY = date(2026, 11, 25)  # 周三（weekday=2）——周目标窗口=11-23(周一)→11-25


def test_daily_stats_three_sessions_across_days(root: Path) -> None:
    """三日造数：11-23 / 11-24 / 11-25 各一篇 → 对应日 chars_added 正确、其余为 0。"""
    _mk_session(root, "a", [(1, "一二三", datetime(2026, 11, 23, 9, 0))])          # 3 字
    _mk_session(root, "b", [(1, "一二三四五", datetime(2026, 11, 24, 10, 0))])     # 5 字
    _mk_session(root, "c", [(1, "一二三四五六七", datetime(2026, 11, 25, 11, 0))]) # 7 字
    daily = writing_stats.daily_stats(root, today=TODAY)
    assert len(daily) == 30  # 近 30 天含今天
    assert daily[-1]["date"] == "2026-11-25"
    by = {x["date"]: x for x in daily}
    assert by["2026-11-23"]["chars_added"] == 3
    assert by["2026-11-24"]["chars_added"] == 5
    assert by["2026-11-25"]["chars_added"] == 7
    assert by["2026-11-22"]["chars_added"] == 0
    assert by["2026-11-25"]["sessions"] == 1


def test_window_boundary_excludes_older_than_30_days(root: Path) -> None:
    """跨日边界：窗口最早一天（今天-29）计入，再早一天不计入。"""
    _mk_session(root, "edge", [
        (1, "旧", datetime(2026, 10, 27, 8, 0)),   # 今天-29 → 计入
        (2, "旧旧", datetime(2026, 10, 26, 8, 0)), # 今天-30 → 排除
    ])
    daily = writing_stats.daily_stats(root, today=TODAY)
    assert daily[0]["date"] == "2026-10-27"
    by = {x["date"]: x for x in daily}
    assert "2026-10-26" not in by
    # 轮 2 比轮 1 长（+1 字），但落在窗口外的「增量」不应计入任何窗口日
    assert sum(x["chars_added"] for x in daily) == 1


def test_rounds_increment_not_double_counted(root: Path) -> None:
    """同会话逐轮增量：第 2 轮继承第 1 轮正文（更长）→ 只计增量 2 字（不累加 8）。"""
    _mk_session(root, "s", [
        (1, "一二三四五五", datetime(2026, 11, 25, 9, 0)),        # 6 字
        (2, "一二三四五五六六六", datetime(2026, 11, 25, 10, 0)),  # 9 字 → 增量 3
    ])
    daily = writing_stats.daily_stats(root, today=TODAY)
    assert daily[-1]["chars_added"] == 9  # 首轮 6 + 增量 3（同会话同一天合计）


def test_round_shrink_contributes_zero(root: Path) -> None:
    """后续轮变短（删减）→ 增量夹到 0，不产生负数日。"""
    _mk_session(root, "shrink", [
        (1, "一二三四五六七八", datetime(2026, 11, 24, 9, 0)),   # 8 字
        (2, "一二", datetime(2026, 11, 25, 9, 0)),               # 2 字 → 0
    ])
    daily = writing_stats.daily_stats(root, today=TODAY)
    by = {x["date"]: x for x in daily}
    assert by["2026-11-24"]["chars_added"] == 8
    assert by["2026-11-25"]["chars_added"] == 0


def test_streak_current_and_longest(root: Path) -> None:
    """连续天数：今天未写不断连胜（从昨天回溯）；longest 取历史最长段。"""
    _mk_session(root, "st", [
        (1, "一", datetime(2026, 11, 21, 9, 0)),
        (2, "一二", datetime(2026, 11, 22, 9, 0)),
        (3, "一二三", datetime(2026, 11, 23, 9, 0)),
        (4, "一二三四", datetime(2026, 11, 24, 9, 0)),
        # 今天 11-25 无记录
        (5, "一二三四五六七八", datetime(2026, 11, 5, 9, 0)),  # 孤立更早一天
    ])
    s = writing_stats.streak(root, today=TODAY)
    assert s["current"] == 4  # 11-21..24 连续，今天未写不算断
    assert s["longest"] == 4


def test_streak_today_active_extends(root: Path) -> None:
    """今天也写 → current 含今天。"""
    _mk_session(root, "t", [
        (1, "一", datetime(2026, 11, 24, 9, 0)),
        (2, "一二", datetime(2026, 11, 25, 9, 0)),
    ])
    assert writing_stats.streak(root, today=TODAY)["current"] == 2


def test_weekly_goal_monday_to_today(root: Path) -> None:
    """周目标：仅计本周一→今天（11-23..11-25）；上周不进，pct 真实百分比。"""
    _mk_session(root, "w", [
        (1, "一", datetime(2026, 11, 22, 9, 0)),          # 上周日 → 不计
        (2, "一二三", datetime(2026, 11, 23, 9, 0)),       # 本周一 +3
        (3, "一二三四五", datetime(2026, 11, 25, 9, 0)),   # 本周三 +5
    ])
    w = writing_stats.weekly_goal(root, target=300, today=TODAY)
    # 注意：同会话轮 1(1字)→轮 2(3字) 增量 2、轮 3(5字) 增量 2；轮1 落在上周日不计入本周
    assert w["written"] == 4  # 11-23 的 +2 与 11-25 的 +2（同会话轮增量）
    assert w["target"] == 300
    assert w["pct"] == round(4 / 300 * 100)


def test_weekly_goal_over_target_pct_can_exceed_100(root: Path) -> None:
    _mk_session(root, "big", [(1, "一二三四五", datetime(2026, 11, 25, 9, 0))])
    w = writing_stats.weekly_goal(root, target=2, today=TODAY)
    assert w["written"] == 5 and w["pct"] == 250


def test_empty_data_degrades_gracefully(tmp_path: Path) -> None:
    """空数据降级：目录不存在 / 无会话 / 会话无轮次 → empty=True 全零、不抛错。"""
    missing = tmp_path / "nope"
    dash = writing_stats.dashboard(missing, today=TODAY)
    assert dash["empty"] is True
    assert all(x["chars_added"] == 0 for x in dash["daily"])
    assert dash["streak"] == {"current": 0, "longest": 0}
    assert dash["weekly"]["written"] == 0

    empty_root = tmp_path / "articles"
    empty_root.mkdir()
    _mk_session(empty_root, "noround", [])  # 有会话无轮次
    assert writing_stats.dashboard(empty_root, today=TODAY)["empty"] is True


def test_corrupt_session_json_does_not_raise(root: Path) -> None:
    """坏 session.json 静默跳过（宽容读），不塌读面。"""
    d = root / "bad.compose"
    (d / "rounds").mkdir(parents=True)
    (d / "session.json").write_text("{ not json", encoding="utf-8")
    dash = writing_stats.dashboard(root, today=TODAY)
    assert dash["empty"] is True


def test_round_without_md_is_skipped(root: Path) -> None:
    """轮次缺 rounds/<n>.md（无日期证据）→ 跳过该轮，不臆造日期。"""
    d = root / "nomd.compose"
    (d / "rounds").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "rounds": [{"n": 1, "blocks": [{"id": "b1", "text": "一二三"}]}]}), encoding="utf-8")
    assert writing_stats.dashboard(root, today=TODAY)["empty"] is True


# ── Studio 端点契约 ─────────────────────────────────────────────

@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def test_writing_stats_endpoint(tmp_path: Path, isolated_home) -> None:
    """/api/writing_stats 200 + dashboard 四键；target 非法 400。"""
    import http.client
    import threading

    from musespiral import studio

    art = tmp_path / "articles"
    art.mkdir()
    _mk_session(art, "e2e", [(1, "一二三", datetime(2026, 11, 25, 9, 0))])
    httpd = studio.make_server(art, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def _get(path: str) -> tuple[int, dict]:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        return resp.status, body

    try:
        code, payload = _get("/api/writing_stats")
        assert code == 200
        assert {"daily", "streak", "weekly", "empty"} <= set(payload)
        assert isinstance(payload["daily"], list) and len(payload["daily"]) == 30
        code, _ = _get("/api/writing_stats?target=abc")
        assert code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


# ── 前端结构锚（写作节奏卡 + 空数据降级文案）──────────────────

def test_insight_view_structural_anchors() -> None:
    """洞察页写作节奏卡：骨架容器 + renderWriting/writingBars + 空数据引导文案
    + 徽章/目标条 + 数据面走 /api/writing_stats（懒加载视图，壳零增量）。"""
    root = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
    html = (root / "ui.views" / "insight.html").read_text(encoding="utf-8")
    js = (root / "assets" / "views" / "insight.js").read_text(encoding="utf-8")
    assert 'id="writing-card"' in html
    assert 'id="writing-chart"' in html and 'id="writing-goal"' in html
    assert 'id="writing-streak"' in html and "role=\"status\"" in html
    assert "function writingBars(" in js and "function renderWriting(){" in js
    # 空数据降级：无 w.empty / 无 daily → 引导文案而非报错
    assert "还没有写作记录" in js
    assert "近 30 天暂无字数增量" in js
    # 连续徽章 + 周目标进度条
    assert "🔥 " in js and "streak-badge" in js and "wr-goal" in js
    # 数据面契约：loadWriting 归 core 段（load* 工具纪律），视图消费 state.writing
    core = (root / "assets" / "core.js").read_text(encoding="utf-8")
    assert "/api/writing_stats" in core and "async function loadWriting(" in core
    assert "renderWriting();" in js  # 接入 renderInsight 渲染链
    # 否向锚：写作卡不直连端点于视图（数据面归 core loadWriting）；壳不内联卡骨架
    # （懒加载视图，壳零增量）——防「全塞壳/视图直连」的等价实现通过。
    assert "/api/writing_stats" not in js
    shell = (root / "ui.html").read_text(encoding="utf-8")
    assert "writing-card" not in shell
