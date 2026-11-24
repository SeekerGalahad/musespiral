"""bookengine 管理面（2026-11-23 用户反馈批，L3）——引擎三态/体验模式/全链自检。

背景（用户实测提问）：书籍视图灰灯只写「未就绪」，本地无任何引擎管理入口——
用户无法判断「这是坏了还是没建」，也拿不到下一步该做什么。本模块把
「零件状态 → 人可读三态 + 指引」收成单一持有者，供 CLI 与 Studio 复用。

三态语义（**「未建设」不是故障**）：

  absent   引擎未建设（R1-R2 分期未开工）——书源导入/订阅/管理全可用，
           只缺「搜索→取文」；指引=体验 mock 或等待 R1-R2
  mock     体验模式（引擎 mock 环境开关 开）——假数据全流程可跑
  ok       真实引擎在线可搜（附带 version/sources_loaded）

红线：本模块只读探测与开关（demo 写用户配置），不启停进程、不碰引擎数据。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import config
from ..infra import bookengine
from ..infra import engine_select

# mock 开关的用户级持久化位置（CLI demo on/off 写这里，Studio 启动时读进 env）
DEMO_FILE = "bookengine_demo"


def _demo_path() -> Path:
    return config.home_dir() / DEMO_FILE


def demo_enabled() -> bool:
    """体验模式是否开（读用户配置文件；env 面由 infra 统一裁决，见 bookengine）。

    架构门（test_mock_switch_stays_inside_infra）：mock 语义只在 infra/bookengine，
    service 层不得引用 引擎 mock 语义函数——本函数只看用户配置文件，两者在 infra 内合并。
    """
    try:
        return _demo_path().read_text(encoding="utf-8").strip() == "on"
    except OSError:
        return False


def set_demo(mode: str) -> str:
    """开/关体验模式（写用户配置；返回人读结果）。"""
    path = _demo_path()
    if mode == "status":
        return f"体验模式：{'开' if demo_enabled() else '关'}"
    if mode == "on":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("on\n", encoding="utf-8")
        return (
            "✅ 体验模式已开（引擎 mock 假件）\n"
            "  书籍搜索将用确定性假数据跑全流程（搜索→选书→选章→入素材库）。\n"
            "  需重启 Studio 生效：~/.musespiral/local/ctl.sh restart\n"
            "  关闭：musespiral bookengine demo off"
        )
    try:
        path.unlink()
    except OSError:
        pass
    return "✅ 体验模式已关（恢复真实引擎探测）。需重启 Studio 生效：ctl.sh restart"


def status(*, client: bookengine.BookEngineClient | None = None) -> dict[str, Any]:
    """引擎三态（headline + detail + next_steps）。"""
    # W18b：统一走引擎选择门面（JVM→Playwright→缺席）；注入 client 保留测试契约。
    payload = client.health() if client is not None else engine_select.health()
    if payload.get("ok") is True:
        version = str(payload.get("version") or "")
        if version == "mock" or payload.get("mock"):
            return {
                "state": "mock",
                "headline": "🟡 bookengine：体验模式（mock 假数据）",
                "detail": [
                    f"地址：{bookengine.BASE_URL}",
                    "搜索/取文用确定性假件——可走全流程，但内容非真实书籍。",
                ],
                "next_steps": [
                    "关闭体验模式：musespiral bookengine demo off",
                    "真实引擎等待 R1-R2 建设（JVM 微服务）",
                ],
            }
        loaded = payload.get("sources_loaded") or 0
        if payload.get("kind") == "playwright":
            return {
                "state": "ok",
                "headline": "🟢 Playwright 书源引擎：可搜（本地浏览器抓取）",
                "detail": [
                    "无需 JVM：书源 browserRule（CSS 选择器）驱动真实网页抓取。",
                    f"带 browserRule 的启用书源：{loaded} 个",
                    "抓取走本机 Chromium（headless），零外部服务。",
                ],
                "next_steps": [
                    "去「书籍」视图搜索书名 → 选章入素材库",
                    "书源规则写法：core/booksource/model.py 的 browserRule 子对象",
                ],
            }
        return {
            "state": "ok",
            "headline": f"🟢 bookengine：可搜（{loaded} 源已加载）",
            "detail": [f"地址：{bookengine.BASE_URL}", f"版本：{version or '未知'}"],
            "next_steps": ["去「书籍」视图搜索书名/作者"],
        }
    return {
        "state": "absent",
        "headline": "⚪ bookengine：未建设（R1-R2 分期未开工）",
        "detail": [
            f"地址：{bookengine.BASE_URL}（无进程监听）",
            "**这不是故障**：书源导入 / 订阅管理 / 书源库全部可用；",
            "唯一不可用的是「搜索书籍 → 选章入素材库」（需要 JVM 引擎抓取）。",
        ],
        "next_steps": [
            "想先体验完整流程：musespiral bookengine demo on && ctl.sh restart",
            "想用真实书籍：给书源加 browserRule（CSS 规则）即自动启用 Playwright 引擎，\n            或等 JVM 引擎建设（R1-R2）",
            "书源管理照常：booksource import / subs / list 全可用",
        ],
    }


def check_endpoints(*, client: bookengine.BookEngineClient | None = None) -> tuple[bool, list[str]]:
    """四端点逐项自检（health/search/info/toc）→ (全ok?, 人读行)。"""
    cli = client if client is not None else bookengine.BookEngineClient()
    lines: list[str] = []
    ok_all = True
    try:
        h = cli.health()
        ok = h.get("ok") is True
        lines.append(f"{'✓' if ok else '✗'} /health  {h.get('version') or h.get('error') or ''}")
        ok_all = ok_all and ok
        if not ok:
            lines.append("  （引擎缺席——后端跳过其余端点探测）")
            return False, lines
    except Exception as err:  # noqa: BLE001 - 自检面不得抛
        return False, [f"✗ /health 异常: {err}"]

    for name, call in (
        ("/search", lambda: cli.search("测试", sources="all")),
        ("/book/info", lambda: cli.book_info("https://m.example.com", "https://m.example.com/b")),
        ("/book/toc", lambda: cli.toc("https://m.example.com", "https://m.example.com/b")),
    ):
        try:
            r = call()
            good = isinstance(r, dict) and r.get("ok") is not False
            lines.append(f"{'✓' if good else '✗'} {name}")
            ok_all = ok_all and good
        except Exception as err:  # noqa: BLE001
            lines.append(f"✗ {name} 异常: {err}")
            ok_all = False
    return ok_all, lines
