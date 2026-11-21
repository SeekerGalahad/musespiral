"""F17 发布数据回填 — fetch_article_stats / sync_reads / summarize 阅读分享。

全部 monkeypatch：不发出真实网络请求（musespiral.analytics.requests.post 全程
替身），token 走 MUSESPIRAL_HOME 指向 tmp_path 的本地缓存，不触碰 ~/.musespiral
真实目录。日志写 tmp_path 临时文件。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
import requests

from musespiral import config
from musespiral.infra.wechat.analytics import AnalyticsError, fetch_article_stats, sync_reads
from musespiral.infra.storage.jsonl import append_log, summarize


def _rec(action: str, ts: str, **extra: object) -> dict[str, object]:
    """构造一条与 stats 日志同结构的记录。"""
    return {
        "ts": ts,
        "action": action,
        "article": "xx.md",
        "title": "标题",
        "media_id": "m1",
        "account": "default",
        **extra,
    }


class _FakeResp:
    """替身 requests.Response，只实现 analytics 用到的 .json()/.status_code。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._payload


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_credentials(monkeypatch, state_dir: Path) -> None:
    """MUSESPIRAL_HOME 指向临时状态目录并放入凭证 + 未过期 token 缓存（免网络取 token）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(state_dir))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / config.CREDENTIALS_FILE).write_text(
        json.dumps(
            {"accounts": {"default": {"appid": "A", "secret": "S"}}, "default": "default"}
        ),
        encoding="utf-8",
    )
    (state_dir / config.TOKEN_FILE).write_text(
        json.dumps({"access_token": "TOK", "expires_at": time.time() + 3600}),
        encoding="utf-8",
    )


def _no_network(monkeypatch) -> list:
    """把 analytics 的 requests.post 换成哨兵：被调用即失败，返回调用记录容器。"""
    calls: list = []

    def _fail(*a, **k):
        raise AssertionError("不应发起网络请求")

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", _fail)
    return calls


# ── fetch_article_stats：请求/摊平/窗口校验/错误转译 ───────


def test_fetch_request_shape_and_flatten(tmp_path: Path, monkeypatch) -> None:
    """POST getarticletotaldetail 携带 token/日期体；三种响应形态都摊平。

    F41 双形态防御（实测前不赌形态）：新契约 detail_list 嵌套（官方文档
    2026-11-14 核实）、旧 details 嵌套、扁平 list 原样透传。
    """
    seen: list[dict] = []

    def fake_post(url, params=None, json=None, timeout=None, **kw):
        seen.append({"url": url, "params": params, "json": json})
        return _FakeResp(
            {
                "list": [
                    {  # 新契约形态：按日 detail_list 嵌套（官方文档核实）
                        "ref_date": "2026-11-01",
                        "msgid": "200001_1",
                        "title": "篇甲",
                        "content_url": "https://s/a",
                        "detail_list": [
                            {"stat_date": "2026-11-01", "read_user": 100,
                             "share_user": 10, "like_user": 1},
                        ],
                    },
                    {  # 旧接口形态：按日 details 嵌套（过渡期兜底）
                        "ref_date": "2026-11-01",
                        "msgid": "200002_1",
                        "title": "篇乙",
                        "url": "https://s/old",
                        "details": [
                            {"stat_date": "2026-11-01", "int_page_read_count": 77,
                             "share_count": 7, "like_count": 0},
                        ],
                    },
                    {  # 扁平形态：无嵌套原样透传，ref_date → date
                        "ref_date": "2026-11-01",
                        "url": "https://s/b",
                        "int_page_read_count": 55,
                        "share_count": 5,
                    },
                ]
            }
        )

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", fake_post)
    rows = fetch_article_stats("TOK", "2026-11-01", "2026-11-01")

    assert len(seen) == 1
    assert seen[0]["url"] == f"{config.WECHAT_API_BASE}/datacube/getarticletotaldetail"
    assert seen[0]["params"] == {"access_token": "TOK"}
    assert seen[0]["json"] == {"begin_date": "2026-11-01", "end_date": "2026-11-01"}

    assert len(rows) == 3
    assert rows[0] == {
        "ref_date": "2026-11-01", "msgid": "200001_1", "title": "篇甲",
        "content_url": "https://s/a", "stat_date": "2026-11-01",
        "read_user": 100, "share_user": 10, "like_user": 1,
        "date": "2026-11-01",
    }
    assert rows[1] == {
        "ref_date": "2026-11-01", "msgid": "200002_1", "title": "篇乙",
        "url": "https://s/old", "stat_date": "2026-11-01",
        "int_page_read_count": 77, "share_count": 7, "like_count": 0,
        "date": "2026-11-01",
    }
    assert rows[2]["url"] == "https://s/b"
    assert rows[2]["date"] == "2026-11-01"  # ref_date 归一为 date


def test_fetch_window_max_1_day(tmp_path: Path, monkeypatch) -> None:
    """官方契约：单窗 1 天（begin_date==end_date）放行；2 天窗口发请求前即被拒。

    微信 getarticletotaldetail 官方注明「日期范围仅支持查询 1 天」（官方
    文档 2026-11-14 核实——见 analytics 模块 docstring「上游契约注」）。
    """
    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp({"list": []}),
    )
    assert fetch_article_stats("TOK", "2026-01-01", "2026-01-01") == []

    _no_network(monkeypatch)  # 超限必须请求前拦截
    with pytest.raises(AnalyticsError, match="1 天"):
        fetch_article_stats("TOK", "2026-01-01", "2026-01-02")


def test_fetch_bad_dates(tmp_path: Path, monkeypatch) -> None:
    """非法日期 / end 早于 begin → AnalyticsError（可读），不发请求。"""
    _no_network(monkeypatch)
    with pytest.raises(AnalyticsError, match="YYYY-MM-DD"):
        fetch_article_stats("TOK", "not-a-date", "2026-01-02")
    with pytest.raises(AnalyticsError, match="早于"):
        fetch_article_stats("TOK", "2026-01-05", "2026-01-01")


def test_fetch_permission_error_readable(tmp_path: Path, monkeypatch) -> None:
    """47001/48001 无权限 → 可读解释（认证服务号 + 数据分析权限）；40001 → token 提示。"""
    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 47001, "errmsg": "api unauthorized"}),
    )
    with pytest.raises(AnalyticsError) as ei:
        fetch_article_stats("TOK", "2026-01-01", "2026-01-01")
    assert "认证服务号" in str(ei.value)
    assert "数据分析权限" in str(ei.value)
    assert "47001" in str(ei.value)

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 48001, "errmsg": "api unauthorized"}),
    )
    with pytest.raises(AnalyticsError) as ei:
        fetch_article_stats("TOK", "2026-01-01", "2026-01-01")
    assert "认证服务号" in str(ei.value)
    assert "数据分析权限" in str(ei.value)
    assert "48001" in str(ei.value)

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp({"errcode": 40001, "errmsg": "invalid credential"}),
    )
    with pytest.raises(AnalyticsError, match="access_token"):
        fetch_article_stats("TOK", "2026-01-01", "2026-01-01")


def test_fetch_network_error(tmp_path: Path, monkeypatch) -> None:
    """网络异常统一转 AnalyticsError（可读）。"""
    def boom(*a, **k):
        raise requests.ConnectionError("conn refused")

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", boom)
    with pytest.raises(AnalyticsError, match="网络"):
        fetch_article_stats("TOK", "2026-01-01", "2026-01-01")


# ── sync_reads：回填/原子性/容忍度 ─────────────────────────


def test_sync_reads_backfills_and_summarize(tmp_path: Path, monkeypatch) -> None:
    """主链路：逐日 1 天窗拉取（官方契约）→ 行内回填 reads/shares/likes → summarize 汇总。"""
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("draft", "2026-11-12T08:00:00"))
    append_log(log, _rec("publish", "2026-11-01T09:00:00", publish_id="PA",
                         article_url="https://s/a"), platform="mp")
    append_log(log, _rec("publish", "2026-11-12T20:00:00", publish_id="PB",
                         article_url="https://s/b"))
    append_log(log, _rec("publish", "2026-11-02T09:00:00", publish_id="PC",
                         article_url="https://s/c"))  # API 无此 URL → 不动
    append_log(log, _rec("publish", "2026-11-03T09:00:00", publish_id="PD"))  # 无 URL → 非候选
    before = log.read_text(encoding="utf-8").splitlines()

    windows: list[dict] = []

    def fake_post(url, params=None, json=None, timeout=None, **kw):
        windows.append(dict(json))
        if json["end_date"] == "2026-11-01":  # 11-01 单日窗：首日累计
            return _FakeResp(
                {"list": [{
                    "ref_date": "2026-11-01", "msgid": "200001_1", "title": "篇甲",
                    "url": "https://s/a",
                    "details": [
                        {"stat_date": "2026-11-01", "int_page_read_count": 100,
                         "share_count": 10, "like_count": 1},
                    ],
                }]}
            )
        if json["end_date"] == "2026-11-07":  # 同 URL 后续日累计值升高
            return _FakeResp(
                {"list": [{"ref_date": "2026-11-07", "url": "https://s/a",
                           "int_page_read_count": 130, "share_count": 13, "like_count": 2}]}
            )
        if json["end_date"] == "2026-11-12":
            return _FakeResp(
                {"list": [{"ref_date": "2026-11-12", "url": "https://s/b",
                           "int_page_read_count": 55, "share_count": 5}]}
            )
        return _FakeResp({"list": []})

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", fake_post)
    st = sync_reads(log)

    # 日期范围 11-01~11-12 共 12 天 → 逐日 12 窗（begin==end）
    assert windows == [
        {"begin_date": f"2026-11-{d:02d}", "end_date": f"2026-11-{d:02d}"}
        for d in range(1, 13)
    ]
    assert st == {"candidates": 3, "matched": 2, "updated": 2, "windows": 12, "fetched": 3}

    after = log.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in after]
    by_pid = {r.get("publish_id"): r for r in rows}
    # A：同 URL 跨窗多日取最新一天（11-07 累计值）
    assert by_pid["PA"]["reads"] == 130
    assert by_pid["PA"]["shares"] == 13
    assert by_pid["PA"]["likes"] == 2
    # 红线：不删不改既有字段（title/account/platform 等原样保留）
    assert by_pid["PA"]["title"] == "标题"
    assert by_pid["PA"]["article_url"] == "https://s/a"
    assert by_pid["PA"]["platform"] == "mp"
    # B：API 无 like_count → likes 0
    assert by_pid["PB"]["reads"] == 55
    assert by_pid["PB"]["shares"] == 5
    assert by_pid["PB"]["likes"] == 0
    # 未命中 / 非候选行字节不变
    assert after[3] == before[3]  # PC 未命中
    assert after[4] == before[4]  # PD 无 URL
    assert after[0] == before[0]  # draft

    s = summarize(log)
    assert s["total_publish"] == 4
    assert s["total_reads"] == 185  # 130 + 55
    assert s["total_shares"] == 18  # 13 + 5


def test_sync_rerun_idempotent_no_rewrite(tmp_path: Path, monkeypatch) -> None:
    """重复同步：数值已最新 → 不计 updated、不重写文件（字节不变）。"""
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-05T09:00:00", article_url="https://s/a"))

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp(
            {"list": [{"ref_date": "2026-11-05", "url": "https://s/a",
                       "int_page_read_count": 42, "share_count": 4, "like_count": 1}]}
        ),
    )
    assert sync_reads(log)["updated"] == 1
    sha1 = _sha(log)
    st2 = sync_reads(log)
    assert st2["matched"] == 1
    assert st2["updated"] == 0
    assert _sha(log) == sha1


def test_sync_no_credentials_readable(tmp_path: Path, monkeypatch) -> None:
    """无凭证：有候选行时转 AnalyticsError 可读提示，且不发网络、不写文件。"""
    state = tmp_path / "state"  # 空目录：无 credentials.json / token.json
    monkeypatch.setenv("MUSESPIRAL_HOME", str(state))
    state.mkdir(parents=True, exist_ok=True)
    _no_network(monkeypatch)

    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-05T09:00:00", article_url="https://s/a"))
    sha = _sha(log)
    with pytest.raises(AnalyticsError, match="凭证"):
        sync_reads(log)
    assert _sha(log) == sha


@pytest.mark.parametrize("mode", ["mid_window", "no_permit"])
def test_sync_failure_file_bytes_unchanged(tmp_path: Path, monkeypatch, mode: str) -> None:
    """拉取失败（中途异常 / 47001 无权限）不落半写：日志字节前后一致。"""
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-01T09:00:00", article_url="https://s/a"))
    append_log(log, _rec("publish", "2026-11-12T09:00:00", article_url="https://s/b"))
    sha = _sha(log)

    def fake_post(url, params=None, json=None, timeout=None, **kw):
        if mode == "mid_window":
            if json["end_date"] == "2026-11-12":
                raise requests.ConnectionError("dropped")  # 第二窗网络失败
            return _FakeResp({"list": []})  # 第一窗成功
        return _FakeResp({"errcode": 47001, "errmsg": "api unauthorized"})

    monkeypatch.setattr("musespiral.infra.wechat.analytics.requests.post", fake_post)
    with pytest.raises(AnalyticsError):
        sync_reads(log)
    assert _sha(log) == sha
    assert not (tmp_path / "publish_log.jsonl.tmp").exists()  # 无临时文件残留


def test_sync_empty_missing_and_corrupt_only_log(tmp_path: Path, monkeypatch) -> None:
    """空日志容忍：文件缺失/空文件/全是损坏行 → 全零统计，不报错、不发网络。"""
    _no_network(monkeypatch)
    zeros = {"candidates": 0, "matched": 0, "updated": 0, "windows": 0, "fetched": 0}

    assert sync_reads(tmp_path / "nope.jsonl") == zeros

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert sync_reads(empty) == zeros

    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text("not-json{{{ \n[\"array\"]\n{\"action\": \n", encoding="utf-8")
    sha = _sha(corrupt)
    assert sync_reads(corrupt) == zeros
    assert _sha(corrupt) == sha


def test_sync_corrupt_lines_preserved(tmp_path: Path, monkeypatch) -> None:
    """成功回填时损坏行原文保留，好行正常更新。"""
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-05T09:00:00", article_url="https://s/a"))
    with log.open("a", encoding="utf-8") as f:
        f.write("not-json{{{ 损坏行\n")
        f.write('{"action": "publish", "ts": "\n')  # 截断 JSON
    before = log.read_text(encoding="utf-8").splitlines()

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp(
            {"list": [{"ref_date": "2026-11-05", "url": "https://s/a",
                       "int_page_read_count": 7, "share_count": 1, "like_count": 0}]}
        ),
    )
    st = sync_reads(log)
    assert st == {"candidates": 1, "matched": 1, "updated": 1, "windows": 1, "fetched": 1}

    after = log.read_text(encoding="utf-8").splitlines()
    assert len(after) == len(before)  # 行数不变（不删损坏行）
    assert after[1] == before[1]  # 损坏行 1 原样
    assert after[2] == before[2]  # 损坏行 2 原样
    row = json.loads(after[0])
    assert row["reads"] == 7
    assert row["shares"] == 1


def test_sync_reads_new_contract_shape(tmp_path: Path, monkeypatch) -> None:
    """F41 新契约全链：detail_list 嵌套 + content_url/read_user/share_user 字段回填。

    新接口为「人数」口径（read_user/share_user/like_user 拇指赞）；字段缺失
    一律 0 兜底不虚报（B 条无 like_user → likes 0）。
    """
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-05T09:00:00", article_url="https://s/a"))
    append_log(log, _rec("publish", "2026-11-05T10:00:00", article_url="https://s/b"))

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp(
            {"is_delay": False, "list": [
                {  # 官方文档形态：list[].detail_list[] 按日嵌套 + content_url
                    "ref_date": "2026-11-05", "msgid": "200001_1", "title": "篇甲",
                    "publish_type": 0, "content_url": "https://s/a",
                    "detail_list": [
                        {"stat_date": "2026-11-05", "read_user": 88, "share_user": 8,
                         "like_user": 3, "zaikan_user": 2},
                    ],
                },
                {  # 无 like_user → likes 0 兜底；zaikan（在看）不计入 likes
                    "ref_date": "2026-11-05", "msgid": "200002_1", "title": "篇乙",
                    "content_url": "https://s/b",
                    "detail_list": [
                        {"stat_date": "2026-11-05", "read_user": 44, "share_user": 4,
                         "zaikan_user": 9},
                    ],
                },
            ]}
        ),
    )
    st = sync_reads(log)
    assert st == {"candidates": 2, "matched": 2, "updated": 2, "windows": 1, "fetched": 2}

    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    by_url = {r.get("article_url"): r for r in rows}
    assert by_url["https://s/a"]["reads"] == 88
    assert by_url["https://s/a"]["shares"] == 8
    assert by_url["https://s/a"]["likes"] == 3
    assert by_url["https://s/b"]["reads"] == 44
    assert by_url["https://s/b"]["shares"] == 4
    assert by_url["https://s/b"]["likes"] == 0  # 缺 like_user → 0 不虚报
    assert "zaikan_user" not in by_url["https://s/b"]  # 日志只增 reads/shares/likes 三键


def test_sync_non_utf8_line_preserved_bytes(tmp_path: Path, monkeypatch) -> None:
    """非 UTF-8（GBK）字节行：整行按原始字节保留——不当候选、不回填、无 U+FFFD。

    复核发现的回归锚点：旧实现 read_text(errors="replace") + 整文件重写会把
    GBK 标题不可逆替换成 U+FFFD，且该行因替换后仍是合法 JSON 被当作候选追加
    reads/shares/likes（违反红线「损坏行原样保留」）。按字节读写后此类行
    原样穿过；同场景的 CRLF 好行回填时保留行尾字节。
    """
    state = tmp_path / "state"
    _seed_credentials(monkeypatch, state)
    log = tmp_path / "publish_log.jsonl"
    append_log(log, _rec("publish", "2026-11-05T09:00:00", article_url="https://s/a"))
    # GBK 编码的合法 JSON 发布行（中文标题）：不是合法 UTF-8 → 必须整行字节保留
    gbk_line = json.dumps(
        {**_rec("publish", "2026-11-06T09:00:00"), "title": "中文标题GBK",
         "article_url": "https://s/gbk"},
        ensure_ascii=False,
    ).encode("gbk") + b"\n"
    # CRLF 行尾的好候选行：回填重写后行尾字节保留
    crlf_line = (
        json.dumps(
            _rec("publish", "2026-11-05T10:00:00", article_url="https://s/c"),
            ensure_ascii=False,
        )
        + "\r\n"
    ).encode("utf-8")
    with log.open("ab") as f:
        f.write(gbk_line)
        f.write(crlf_line)

    monkeypatch.setattr(
        "musespiral.infra.wechat.analytics.requests.post",
        lambda *a, **k: _FakeResp(
            {"list": [
                {"ref_date": "2026-11-05", "url": "https://s/a",
                 "int_page_read_count": 7, "share_count": 1, "like_count": 0},
                {"ref_date": "2026-11-05", "url": "https://s/c",
                 "int_page_read_count": 9, "share_count": 2, "like_count": 0},
            ]}
        ),
    )
    st = sync_reads(log)
    assert st == {"candidates": 2, "matched": 2, "updated": 2, "windows": 1, "fetched": 2}

    after = log.read_bytes().splitlines(keepends=True)
    assert after[1] == gbk_line  # 非 UTF-8 行逐字节一致（无 U+FFFD、未被回填）
    assert b"\xef\xbf\xbd" not in b"".join(after)  # 全文件无 U+FFFD 替换痕迹
    gbk_obj = json.loads(after[1].decode("gbk"))
    assert gbk_obj["title"] == "中文标题GBK"
    assert "reads" not in gbk_obj  # 未被当作候选
    assert json.loads(after[0].decode("utf-8"))["reads"] == 7  # 好行正常回填
    assert after[2].endswith(b"\r\n")  # CRLF 行尾保留
    assert json.loads(after[2].decode("utf-8"))["reads"] == 9
