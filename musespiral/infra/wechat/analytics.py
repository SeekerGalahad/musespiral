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
     likes 不计 zaikan_user（在看，另一口径，如实区分）。真机首跑（总账
     ④b）后按实测形态收敛。

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

    try:
        token = get_access_token(account)
    except TokenError as e:
        raise AnalyticsError(str(e)) from e

    # 先拉全量再落盘（红线：拉取失败不落半写文件）；逐日 1 天窗（官方契约）
    days = sorted({day for _, _, day in candidates})
    cur = date.fromisoformat(days[0])
    end = date.fromisoformat(days[-1])
    fetched: list[dict[str, Any]] = []
    while cur <= end:
        w_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), end)
        fetched.extend(fetch_article_stats(token, str(cur), str(w_end)))
        result["windows"] += 1
        cur = w_end + timedelta(days=1)
    result["fetched"] = len(fetched)

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

    for i, obj, _day in candidates:
        row = latest_by_url.get(str(obj.get("article_url")))
        if row is None:
            continue
        result["matched"] += 1
        new = dict(obj)
        new["reads"] = _first_int(row, "int_page_read_count", "read_user")
        new["shares"] = _first_int(row, "share_count", "share_user")
        new["likes"] = _first_int(row, "like_count", "like_user")
        if new != obj:
            result["updated"] += 1
            lines[i] = json.dumps(new, ensure_ascii=False).encode("utf-8") + line_tail(lines[i])
    if result["updated"]:
        atomic_write_bytes(path, b"".join(lines))
    return result
