"""F1 发布数据闭环 — publish_log.jsonl 追加/汇总 + publisher 成功路径写入。

全部用 tmp_path 写临时文件，不触碰 ~/.musespiral 真实目录；requests 全程 monkeypatch，
不发出真实网络请求。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from musespiral import config
from musespiral.infra.wechat.publisher import PublishError, create_draft, submit_publish
from musespiral.infra.storage.jsonl import append_log, default_log_path, summarize


def _rec(action: str, ts: str, **extra: object) -> dict[str, object]:
    """构造一条与设计 §十二 F1 同结构的日志记录。"""
    return {
        "ts": ts,
        "action": action,
        "article": "xx.md",
        "title": "标题",
        "media_id": "m1",
        "account": "default",
        **extra,
    }


def _now_ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


class _FakeResp:
    """替身 requests.Response，只实现 publisher 用到的 .json()。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


# ── stats：追加 + 汇总 ─────────────────────────────────────


def test_append_and_summarize(tmp_path: Path) -> None:
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("draft", _now_ts(), media_id="d1"))
    append_log(log, _rec("publish", _now_ts(), media_id="d1", article_url="https://mp.weixin.qq.com/s/x"))

    s = summarize(log)
    assert s["total_draft"] == 1
    assert s["total_publish"] == 1
    assert s["month_draft"] == 1
    assert s["month_publish"] == 1
    assert len(s["recent"]) == 2
    assert s["recent"][0]["action"] == "publish"  # 最新在前

    # 文件是合法 jsonl，一行一条
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "draft"


def test_summarize_month_boundary(tmp_path: Path) -> None:
    log = tmp_path / "publish_log.jsonl"
    # 45 天前必然不在本月（一个月 ≤31 天）
    old_ts = (datetime.now() - timedelta(days=45)).isoformat(timespec="seconds")
    append_log(log, _rec("draft", old_ts))
    s = summarize(log)
    assert s["total_draft"] == 1
    assert s["month_draft"] == 0


def test_summarize_skips_corrupt_lines(tmp_path: Path) -> None:
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("draft", _now_ts()))
    with log.open("a", encoding="utf-8") as f:
        f.write("not-json{{{ \n")
        f.write("\n")
        f.write('["array", "not", "object"]\n')
        f.write('{"action": "draft", "ts": "\n')  # 截断 JSON
    append_log(log, _rec("draft", _now_ts()))

    s = summarize(log)
    assert s["total_draft"] == 2  # 三类损坏行全部跳过
    assert len(s["recent"]) == 2


def test_summarize_missing_file(tmp_path: Path) -> None:
    s = summarize(tmp_path / "nope.jsonl")
    assert s == {
        "total_draft": 0,
        "total_publish": 0,
        "month_draft": 0,
        "month_publish": 0,
        "total_reads": 0,  # F17：阅读/分享键恒存在，缺字段按 0
        "total_shares": 0,
        "recent": [],
        "by_platform": {},  # F13：无记录时无平台分组
    }


def test_recent_n(tmp_path: Path) -> None:
    log = tmp_path / "publish_log.jsonl"
    for i in range(5):
        append_log(log, _rec("draft", f"2026-01-0{i + 1}T00:00:00", media_id=f"m{i}"))
    s = summarize(log, recent_n=3)
    assert [r["media_id"] for r in s["recent"]] == ["m4", "m3", "m2"]


def test_default_log_path_respects_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    assert default_log_path() == tmp_path.resolve() / config.PUBLISH_LOG


# ── publisher：成功路径写日志 ──────────────────────────────


def test_create_draft_appends_log(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"media_id": "MEDIA123"}),
    )
    media_id = create_draft(
        "tok", "标题", "<p>hi</p>", "THUMB", author="a", digest="d",
        article="xx.md", account="acc1", log_path=log,
    )
    assert media_id == "MEDIA123"
    records = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "draft"
    assert r["article"] == "xx.md"
    assert r["title"] == "标题"
    assert r["media_id"] == "MEDIA123"
    assert r["account"] == "acc1"
    assert r["ts"]


def test_create_draft_failure_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 40007, "errmsg": "invalid media id"}),
    )
    with pytest.raises(PublishError):
        create_draft("tok", "t", "<p>x</p>", "THUMB", log_path=log)
    assert not log.exists()


def test_create_draft_writes_default_path(monkeypatch, tmp_path: Path) -> None:
    # 不传 log_path 时落到 config.home_dir()/config.PUBLISH_LOG（MUSESPIRAL_HOME 指向临时目录）
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"media_id": "M"}),
    )
    create_draft("tok", "t", "<p>x</p>", "THUMB")
    log = tmp_path.resolve() / config.PUBLISH_LOG
    assert log.exists()
    assert summarize(log)["total_draft"] == 1


def test_submit_publish_appends_log(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 0, "publish_id": "PID1"}),
    )
    publish_id = submit_publish(
        "tok", "MEDIA123", article="xx.md", title="标题", account="acc1",
        article_url="https://mp.weixin.qq.com/s/abc", log_path=log,
    )
    assert publish_id == "PID1"
    r = json.loads(log.read_text(encoding="utf-8").strip())
    assert r["action"] == "publish"
    assert r["media_id"] == "MEDIA123"
    assert r["publish_id"] == "PID1"
    assert r["article_url"] == "https://mp.weixin.qq.com/s/abc"
    assert r["account"] == "acc1"


def test_submit_publish_failure_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 40007, "errmsg": "bad"}),
    )
    with pytest.raises(PublishError):
        submit_publish("tok", "MEDIA123", log_path=log)
    assert not log.exists()


# ── F13 多平台化：platform 字段 + by_platform 分组 ────────


def test_append_log_default_platform_mp(tmp_path: Path) -> None:
    """platform 缺省写 mp：不传 kwarg 时记录落 platform="mp"。"""
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("draft", _now_ts()))
    r = json.loads(log.read_text(encoding="utf-8").strip())
    assert r["platform"] == "mp"


def test_append_log_platform_kwarg(tmp_path: Path) -> None:
    """platform kwarg 写入对应平台值。"""
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("draft", _now_ts()), platform="xhs")
    r = json.loads(log.read_text(encoding="utf-8").strip())
    assert r["platform"] == "xhs"


def test_summarize_by_platform_mixed(tmp_path: Path) -> None:
    """多平台混合日志：by_platform 各组计数正确，顶层既有键仍为全平台合计。"""
    log = tmp_path / "publish_log.jsonl"
    old_ts = (datetime.now() - timedelta(days=45)).isoformat(timespec="seconds")
    append_log(log, _rec("draft", _now_ts()), platform="xhs")
    append_log(log, _rec("publish", _now_ts(), media_id="d1", publish_id="P1"), platform="xhs")
    append_log(log, _rec("draft", old_ts), platform="zhihu")

    s = summarize(log)
    bp = s["by_platform"]
    assert set(bp) == {"xhs", "zhihu"}
    assert {k: v for k, v in bp["xhs"].items() if k != "recent"} == {
        "drafts": 1, "publishes": 1, "month_drafts": 1, "month_publishes": 1,
    }
    assert {k: v for k, v in bp["zhihu"].items() if k != "recent"} == {
        "drafts": 1, "publishes": 0, "month_drafts": 0, "month_publishes": 0,
    }
    # 分组 recent（low#8 修复）：每平台最新在前
    assert [r["action"] for r in bp["xhs"]["recent"]] == ["publish", "draft"]
    assert [r["action"] for r in bp["zhihu"]["recent"]] == ["draft"]
    # 顶层既有键保留：跨平台合计（zhihu 那条在 45 天前，不计本月）
    assert s["total_draft"] == 2
    assert s["total_publish"] == 1
    assert s["month_draft"] == 1
    assert s["month_publish"] == 1


def test_summarize_legacy_rows_read_as_mp(tmp_path: Path) -> None:
    """旧格式行（无 platform 字段，第五期之前手写/旧版落盘）读作 mp 归入 mp 组。"""
    log = tmp_path / "publish_log.jsonl"
    legacy = json.dumps(
        {
            "ts": _now_ts(),
            "action": "draft",
            "article": "a.md",
            "title": "t",
            "media_id": "m",
            "account": "default",
        },
        ensure_ascii=False,
    )
    log.write_text(legacy + "\n", encoding="utf-8")
    append_log(log, _rec("publish", _now_ts(), media_id="m", publish_id="P1"), platform="xhs")

    s = summarize(log)
    assert {k: v for k, v in s["by_platform"]["mp"].items() if k != "recent"} == {
        "drafts": 1,
        "publishes": 0,
        "month_drafts": 1,
        "month_publishes": 0,
    }
    assert [r["action"] for r in s["by_platform"]["mp"]["recent"]] == ["draft"]
    assert s["by_platform"]["xhs"]["publishes"] == 1


def test_create_draft_platform_passthrough(tmp_path: Path, monkeypatch) -> None:
    """create_draft 透传 platform 到日志；不传时缺省 mp。"""
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"media_id": "M1"}),
    )
    create_draft("tok", "t", "<p>x</p>", "THUMB", log_path=log)
    create_draft("tok", "t", "<p>x</p>", "THUMB", log_path=log, platform="xhs")
    records = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [r["platform"] for r in records] == ["mp", "xhs"]


def test_submit_publish_platform_passthrough(tmp_path: Path, monkeypatch) -> None:
    """submit_publish 透传 platform 到日志；不传时缺省 mp。"""
    log = tmp_path / "publish_log.jsonl"
    monkeypatch.setattr(
        "musespiral.infra.wechat.publisher.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 0, "publish_id": "PID"}),
    )
    submit_publish("tok", "MEDIA", log_path=log)
    submit_publish("tok", "MEDIA", log_path=log, platform="channels")
    records = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [r["platform"] for r in records] == ["mp", "channels"]


def test_summarize_by_platform_recent_newest_first(tmp_path) -> None:
    """第五期遗留 low#8：分组含 per-platform recent，最新在前。"""
    from musespiral.infra.storage.jsonl import append_log, summarize

    log = tmp_path / "log.jsonl"
    append_log(log, {"ts": "2026-11-01T10:00:00", "action": "draft", "title": "旧篇", "platform": "mp"})
    append_log(log, {"ts": "2026-11-02T10:00:00", "action": "publish", "title": "新篇", "platform": "mp"})
    append_log(log, {"ts": "2026-11-02T11:00:00", "action": "draft", "title": "频道篇", "platform": "channels"})

    s = summarize(log)
    mp_recent = [r["title"] for r in s["by_platform"]["mp"]["recent"]]
    assert mp_recent[0] == "新篇"  # 最新在前
    assert [r["title"] for r in s["by_platform"]["channels"]["recent"]] == ["频道篇"]


# ── F17：total_reads / total_shares 顶层汇总 ───────────────


def test_summarize_reads_shares_totals(tmp_path: Path) -> None:
    """回填后的日志：total_reads/total_shares 为各行 reads/shares 求和。"""
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", _now_ts(), article_url="https://s/a", reads=120, shares=12))
    append_log(log, _rec("publish", _now_ts(), article_url="https://s/b", reads=65, shares=6))
    append_log(log, _rec("draft", _now_ts()))  # draft 行无阅读数据，不计入

    s = summarize(log)
    assert s["total_reads"] == 185
    assert s["total_shares"] == 18


def test_summarize_reads_legacy_rows_zero(tmp_path: Path) -> None:
    """旧日志（无 reads/shares 字段，F17 回填之前落盘）汇总为 0，键恒存在。"""
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", _now_ts(), article_url="https://s/a"))

    s = summarize(log)
    assert s["total_reads"] == 0
    assert s["total_shares"] == 0


def test_summarize_reads_tolerates_bad_values(tmp_path: Path) -> None:
    """reads 值非数值（脏数据/手改日志）按 0 计，不抛异常。"""
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", _now_ts(), reads="很多", shares=None))

    s = summarize(log)
    assert s["total_reads"] == 0
    assert s["total_shares"] == 0
