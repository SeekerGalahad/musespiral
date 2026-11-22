"""F17/F41 发布数据回填 — 微信图文统计 → publish_log 阅读数据闭环。

fetch_article_stats 调 /datacube/getarticletotaldetail（发表内容发表详细
数据——圈选日期内发表内容的详细数据，需认证服务号 + 数据分析权限）。该
接口是官方对已停维护 /datacube/getarticletotal 的替代（第十六期裁决③
F41 直接替换，不留双轨），把微信 list[].detail_list[]（新契约按日嵌套，
官方文档 2026-11-14 核实）、旧 list[].details[] 嵌套与扁平 list 三种响应
形态都摊平成 [{url/date/read 指标/...}]；sync_reads 据此对 publish_log 中
action=publish 且带 article_url 的记录回填行内 reads/shares/likes，
stats.summarize 顶层汇总 total_reads/total_shares。

红线（设计 §十六 F17）：不删不改既有日志字段（只增改 reads/shares/likes
三个键）；网络/接口错误不半写文件（先拉全量再落盘）；损坏行（含非 UTF-8
字节行）按原始字节整行保留——sync_reads 按字节读写，绝不经 errors=replace
的 U+FFFD 不可逆替换。

上游契约注（F41 迁移，官方文档 2026-11-14 核实：
developers.weixin.qq.com「数据统计·发表内容发表详细数据」）：
  1. 窗口：getarticletotaldetail 官方注明「日期范围仅支持查询 1 天」
     （begin_date==end_date）——设计 §一 要点 2 猜测的「支持日期范围」
     与实测契约不符，sync_reads 保守逐日切窗即契约本身；另注 end_date
     最大为昨日、每篇仅统计发表日起 30 天内数据、数据存储起始 2025-11-01。
  2. 字段：新接口为「人数」口径（read_user/share_user/like_user 拇指赞），
     URL 键为 content_url——回填按「旧字段名优先、新字段名兜底」双读
     （int_page_read_count/read_user 等），任一缺失一律 0 兜底不虚报；
     likes 不计 zaikan_user（在看，另一口径，如实区分——FEAT-3 起在看单列
     进 stats.zaikan，不混入 likes）。真机首跑（总账④b）后按实测形态收敛。

FEAT-3（2026-11-22）数据回流自动化：
  - 行内新增 stats 子对象（reads/shares/likes/zaikan/date，含在看），与
    F17 顶层 reads/shares/likes 并存（旧读侧零改动）；
  - backfill_published_stats 在 submit 成功后即时回填该篇 stats——best-effort
    不抛错，失败仅返回 ok=False + 可读 reason，绝不阻塞已完成的发表。

Phase C 自顶层 musespiral/analytics.py 迁入（正典 §八 Phase C），行为零变更——
相对导入层级调整（config 三层、token 同包）+ 发布日志路径改经
同层 infra/storage/jsonl（正典 §2.3）。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from ... import config
from ..storage.filesystem import atomic_write_bytes, line_tail
from ..storage.jsonl import default_log_path
from .token import TokenError, get_access_token

# getarticletotaldetail 时间窗口：官方契约「日期范围仅支持查询 1 天」
# （begin_date==end_date；另注 end_date 最大为昨日、每篇仅统计发表日起
# 30 天内数据、数据存储起始 2025-11-01——官方文档 2026-11-14 核实，见
# 模块 docstring「上游契约注」）。设计 §一 要点 2 的「官方支持日期范围」
# 与实测契约不符，保守逐日切窗即契约本身；sync_reads 据此逐日切窗。
_MAX_WINDOW_DAYS = 1
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PERMIT_HINT = "图文统计接口需认证服务号并开通数据分析权限"
_TOKEN_ERRCODES = (40001, 40014, 42001)
_PERMIT_ERRCODES = (47001, 48001)


class AnalyticsError(RuntimeError):
    """数据分析接口调用失败（无凭证/无权限/窗口超限/网络错误），消息面向用户可读。"""


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _explain_errcode(errcode: Any, data: dict[str, Any]) -> str:
    errmsg = data.get("errmsg", "")
    if errcode in _PERMIT_ERRCODES:
        return (
            f"数据分析接口无权限（errcode={errcode}, errmsg={errmsg}）："
            f"{_PERMIT_HINT}——请确认公众号为已认证服务号且已开通数据分析权限"
        )
    if errcode in _TOKEN_ERRCODES:
        return (
            f"access_token 无效或已过期（errcode={errcode}, errmsg={errmsg}）："
            f"请核对凭证（musespiral config set）后重试"
        )
    return f"数据分析接口错误 errcode={errcode} errmsg={errmsg}"


def _flatten(items: list[Any]) -> list[dict[str, Any]]:
    """微信按日嵌套结构摊平为一行一天；无嵌套则原样透传（F41 双形态防御）。

    兼容三种形态（实测前不赌形态，真机首跑后如实收敛）：
      - 新契约 list[].detail_list[]（官方文档 2026-11-14 核实的
        getarticletotaldetail 按日嵌套键）；
      - 旧 list[].details[]（getarticletotal 历史键，mock/真机过渡期兜底）；
      - 扁平 list[]（无嵌套直接透传）。
    ref_date/stat_date 统一为 date 键（设计 F17 返回结构约定），其余字段
    （url/content_url/int_page_read_count/read_user/...）原样保留。
    """
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        details: list[Any] | None = None
        for key in ("detail_list", "details"):
            v = item.get(key)
            if isinstance(v, list) and v:
                details = v
                break
        if details is not None:
            base = {k: v for k, v in item.items() if k not in ("detail_list", "details")}
            for d in details:
                if not isinstance(d, dict):
                    continue
                rows.append({
                    **base,
                    **d,
                    "date": d.get("stat_date") or d.get("ref_date") or item.get("ref_date") or "",
                })
        else:
            rows.append({**item, "date": item.get("ref_date") or item.get("date") or ""})
    return rows


def fetch_article_stats(token: str, begin_date: str, end_date: str) -> list[dict[str, Any]]:
    """POST /datacube/getarticletotaldetail 拉取图文统计（单次窗口 = 1 天，官方契约）。

    返回摊平后的 [{url/date/read 指标/...}]。官方契约 begin_date==end_date
    （「日期范围仅支持查询 1 天」，见模块 docstring 上游契约注）；跨 ≥2 天
    的窗口在发请求前即被拒。日期非法 / 网络失败 / 业务错误码（47001/48001
    无权限等）统一转 AnalyticsError，消息可读。
    """
    try:
        begin = date.fromisoformat(begin_date)
        end = date.fromisoformat(end_date)
    except ValueError as e:
        raise AnalyticsError(
            f"日期格式应为 YYYY-MM-DD: begin={begin_date!r} end={end_date!r}"
        ) from e
    if end < begin:
        raise AnalyticsError(f"end_date 早于 begin_date: {begin_date} > {end_date}")
    span = (end - begin).days + 1
    if span > _MAX_WINDOW_DAYS:
        raise AnalyticsError(
            f"时间窗口超限: {begin_date}~{end_date} 共 {span} 天，"
            f"getarticletotaldetail 单次仅支持 1 天（begin_date==end_date，官方契约）"
        )
    try:
        resp = requests.post(
            f"{config.WECHAT_API_BASE}/datacube/getarticletotaldetail",
            params={"access_token": token},
            json={"begin_date": begin_date, "end_date": end_date},
            timeout=30,
        )
    except requests.RequestException as e:
        raise AnalyticsError(f"请求数据分析接口失败（网络错误）: {e}") from e
    try:
        data = resp.json()
    except ValueError as e:
        raise AnalyticsError(
            f"数据分析接口返回非 JSON（HTTP {resp.status_code}）"
        ) from e
    errcode = data.get("errcode")
    if errcode:
        raise AnalyticsError(_explain_errcode(errcode, data))
    return _flatten(data.get("list") or [])





def _first_int(row: dict[str, Any], *keys: str) -> int:
    """按序取第一个存在的字段转 int；全缺/不可解析 → 0（恒 0 兜底不虚报）。

    F41 双读：旧字段名（int_page_read_count/share_count/like_count）优先、
    新字段名（read_user/share_user/like_user）兜底——真机形态收敛前不赌
    哪一侧出现。字段存在但值非数值时同样 0（_to_int 语义）。
    """
    for k in keys:
        if k in row:
            return _to_int(row[k])
    return 0


def _latest_stats_by_url(
    begin: str, end: str, account: str | None
) -> tuple[dict[str, dict[str, Any]], int, int]:
    """在 [begin, end] 逐日 1 天窗拉取统计，返回每个 url 最新一天的原始行。

    返回 (latest_by_url, windows, fetched)。先拉全量再返回——任何失败抛
    AnalyticsError，调用方据此保证不落半写文件（红线，见 sync_reads docstring）。
    """
    try:
        token = get_access_token(account)
    except TokenError as e:
        raise AnalyticsError(str(e)) from e

    cur = date.fromisoformat(begin)
    last = date.fromisoformat(end)
    fetched: list[dict[str, Any]] = []
    windows = 0
    while cur <= last:
        w_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), last)
        fetched.extend(fetch_article_stats(token, str(cur), str(w_end)))
        windows += 1
        cur = w_end + timedelta(days=1)

    # getarticletotaldetail 是按日累计口径（stat_date 数据 = [发表日-统计日]
    # 期间新产生数据总和，官方文档）：同一 url 取最新一天为当前值
    latest_by_url: dict[str, dict[str, Any]] = {}
    for row in fetched:
        url = str(row.get("url") or row.get("content_url") or "")  # F41 双读：旧 url / 新 content_url
        if not url:
            continue
        kept = latest_by_url.get(url)
        if kept is None or str(row.get("date") or "") >= str(kept.get("date") or ""):
            latest_by_url[url] = row
    return latest_by_url, windows, len(fetched)


def _stats_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """把摊平后的接口行转成日志 stats 子对象（FEAT-3，含在看；0 兜底不虚报）。"""
    date_val = str(row.get("date") or "")
    return {
        "reads": _first_int(row, "int_page_read_count", "read_user"),
        "shares": _first_int(row, "share_count", "share_user"),
        "likes": _first_int(row, "like_count", "like_user"),
        "zaikan": _first_int(row, "zaikan_user"),
        "date": date_val,
    }


def sync_reads(log_path: str | Path | None = None, account: str | None = None) -> dict[str, int]:
    """把微信图文统计回填进 publish_log 的 reads/shares/likes（F17 反馈环）。

    流程：按字节读入日志 → 定位 action=publish 且带 article_url、ts 日期
    可解析的候选行（非 UTF-8 字节行不可解码，整行按原始字节保留、不当候选）
    → 取 token（无凭证转 AnalyticsError 可读提示）→ 候选日期范围逐日切 1 天
    窗拉取（官方契约；先拉全量再落盘，任何失败不写文件）→ 同一 article_url
    取最新一天的累计值回填。落盘为原子重写：读全文件字节 → 内存改匹配行 →
    临时文件写回 → os.replace；未匹配行/损坏行/非 UTF-8 行按原始字节原样
    保留，只增改 reads/shares/likes 三个键，其余字段不动。

    日志缺失 / 无候选行（空日志、全是损坏行）→ 返回全零统计不报错、
    不发起任何 API 调用。返回 {candidates, matched, updated, windows,
    fetched}：候选行数 / URL 命中数 / 实际改写字段行数 / API 窗口数（逐日
    1 窗）/ 摊平后取回条目数。
    """
    path = Path(log_path) if log_path is not None else default_log_path()
    result = {"candidates": 0, "matched": 0, "updated": 0, "windows": 0, "fetched": 0}
    if not path.exists():
        return result
    # 按字节读取（红线：绝不 errors=replace——U+FFFD 替换不可逆，会损坏
    # 非 UTF-8 字节行且使其伪装成可回填候选）
    lines = path.read_bytes().splitlines(keepends=True)

    # 第一遍：定位候选行（不可解码/损坏行/非对象/缺 article_url/日期不可解析一律跳过）
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for i, line in enumerate(lines):
        try:
            stripped = line.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue  # 非 UTF-8 字节行：整行原始字节保留，不当候选
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("action") != "publish" or not obj.get("article_url"):
            continue
        day = str(obj.get("ts", ""))[:10]
        if not _DATE_RE.match(day):
            continue
        candidates.append((i, obj, day))

    if not candidates:
        return result
    result["candidates"] = len(candidates)

    # 先拉全量再落盘（红线：拉取失败不落半写文件）；逐日 1 天窗（官方契约）
    days = sorted({day for _, _, day in candidates})
    latest_by_url, windows, fetched_n = _latest_stats_by_url(days[0], days[-1], account)
    result["windows"] = windows
    result["fetched"] = fetched_n

    for i, obj, _day in candidates:
        row = latest_by_url.get(str(obj.get("article_url")))
        if row is None:
            continue
        result["matched"] += 1
        new = dict(obj)
        new["reads"] = _first_int(row, "int_page_read_count", "read_user")
        new["shares"] = _first_int(row, "share_count", "share_user")
        new["likes"] = _first_int(row, "like_count", "like_user")
        # FEAT-3：行内 stats 子对象（含在看 zaikan + 统计日期），与顶层三键并存。
        # 顶层 reads/shares/likes 保持不变——旧读侧（summarize/CLI/仪表盘）零改动。
        new["stats"] = _stats_from_row(row)
        if new != obj:
            result["updated"] += 1
            lines[i] = json.dumps(new, ensure_ascii=False).encode("utf-8") + line_tail(lines[i])
    if result["updated"]:
        atomic_write_bytes(path, b"".join(lines))
    return result


def backfill_published_stats(
    article_url: str,
    *,
    log_path: str | Path | None = None,
    account: str | None = None,
    published_day: str | None = None,
    echo=None,
) -> dict[str, Any]:
    """发表后即时回填该篇文章的 stats（FEAT-3 数据回流，best-effort）。

    给已发表文章的 article_url，从发表日（含）到昨天逐日 1 天窗拉取微信统计
    （官方契约：每篇仅统计发表日起 30 天内、end_date 最大昨日、数据存储起始
    2025-11-01），取最新一天的累计值，对 publish_log 中 article_url 匹配的
    action=publish 行回填行内 stats（含在看）与顶层 reads/shares/likes。

    设计取舍：**best-effort 不抛错**——发表是不可撤回动作，统计回填失败不应
    阻塞/回滚已完成的发表；任何 AnalyticsError/异常都收敛为 {ok: False,
    reason: 可读消息}。返回 {ok, url, matched, reads, shares, likes, zaikan,
    date, reason}；未匹配/无数据 ok=False reason 说明，不改写任何文件。
    echo 可传 click.echo：拉取前输出一行进度提示（CLI 路径），缺省静默。

    时间窗：published_day 缺省取今天；下限取 max(published_day, 2025-11-01)，
    上限取 min(今天, published_day+29)（30 天口径），再与「最大昨日」取交集。
    """
    out: dict[str, Any] = {
        "ok": False, "url": article_url, "matched": 0,
        "reads": 0, "shares": 0, "likes": 0, "zaikan": 0, "date": "", "reason": "",
    }
    if not article_url:
        out["reason"] = "无文章链接，跳过数据回填"
        return out
    say = echo or (lambda *a, **k: None)
    say("   正在拉取文章数据（阅读/在看/分享）...")
    today = date.today()
    try:
        pub = date.fromisoformat(published_day) if published_day else today
    except ValueError:
        pub = today

    path = Path(log_path) if log_path is not None else default_log_path()
    if not path.exists():
        out["reason"] = "发布日志不存在"
        return out
    try:
        lines = path.read_bytes().splitlines(keepends=True)
        candidates: list[tuple[int, dict[str, Any], str]] = []
        for i, line in enumerate(lines):
            try:
                obj = json.loads(line.decode("utf-8").strip())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(obj, dict) or obj.get("action") != "publish":
                continue
            if str(obj.get("article_url")) != article_url:
                continue
            day = str(obj.get("ts", ""))[:10]
            if not _DATE_RE.match(day):
                day = str(pub)
            candidates.append((i, obj, day))
        if not candidates:
            out["reason"] = "发布日志无该文章的发表记录"
            return out
        # 用真实发表日期（日志 ts）作窗口起点，published_day 仅兜底
        days = [day for _, _, day in candidates]
        win_first = max(date.fromisoformat(min(days)), date(2025, 11, 1))
        # 发表当日即试拉一次（数据次日才出，多半空）——「发布后自动调 API」动作
        # 确实发生；末端与「最大昨日」取交集，低于 30 天上限则按实际日历天收口。
        win_last = min(today - timedelta(days=1), win_first + timedelta(days=29))
        if win_last < win_first:
            win_last = win_first  # 至少打一窗，空数据由下方 row is None 收敛
        latest_by_url, _, _ = _latest_stats_by_url(str(win_first), str(win_last), account)
        row = latest_by_url.get(article_url)
        if row is None:
            out["reason"] = "微信统计接口尚无该文章数据（可能未过 30 天窗口或次日才更新）"
            return out
        stats = _stats_from_row(row)
        changed = 0
        for i, obj, _ in candidates:
            new = dict(obj)
            new["stats"] = stats
            new["reads"] = stats["reads"]
            new["shares"] = stats["shares"]
            new["likes"] = stats["likes"]
            if new != obj:
                changed += 1
                lines[i] = json.dumps(new, ensure_ascii=False).encode("utf-8") + line_tail(lines[i])
        if changed:
            atomic_write_bytes(path, b"".join(lines))
        out.update({"ok": True, "matched": changed, **stats})
        return out
    except (AnalyticsError, OSError, ValueError) as e:
        out["reason"] = f"数据回填失败: {e}"
        return out
