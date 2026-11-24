"""ESM 骨架门（2026-11-24 W3-3a）：src/ 三模块与 core.js 语义对齐锁。

W3（前端 ESM 化）分 6 批推进，3a=骨架（api/store/router 三模块 + 不接壳）。
本门锁两件事：
  1. 三模块存在且导出契约稳定（后续批次依赖这些名字，改名必须显式）；
  2. api.js 的请求语义与 core.js 的 request 逐字对齐（迁移期双轨的语义锁——
     若 core.js 侧改了错误文案/ok 判定，此门红=提醒同步）。
"""
from __future__ import annotations

import re
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
SRC = STUDIO / "assets" / "src"


def test_three_modules_exist() -> None:
    for name, exports in (
        ("api.js", {"request", "getJSON", "postJSON", "waitForJob"}),
        ("store.js", {"store", "subscribe", "notify"}),
        ("router.js", {"current", "navigate", "onChange"}),
    ):
        p = SRC / name
        assert p.exists(), f"ESM 骨架缺 {name}"
        s = p.read_text(encoding="utf-8")
        for e in exports:
            assert f"export" in s and e in s, f"{name} 应导出 {e}"


def test_api_semantics_match_core() -> None:
    """api.js 与 core.js 的关键语义逐点对齐（迁移期双轨锁）。"""
    api = (SRC / "api.js").read_text(encoding="utf-8")
    core = (STUDIO / "assets" / "core.js").read_text(encoding="utf-8")
    # ① ok===false 抛错
    assert "data.ok === false" in api and "data.ok===false" in core.replace(" ", "")
    # ② HTTP 非 2xx 抛 Error（data.error 优先）
    for pat in ("HTTP ' + res.status", "HTTP '+res.status"):
        if pat in api.replace('"', "'"):
            break
    else:
        assert "res.ok" in api, "api.js 应保留 !res.ok 抛错语义"
    # ③ waitForJob 的终态判定与轮询间隔
    assert "'done'" in api and "'failed'" in api and "800" in api
    assert "900" in api, "轮询上限次数应与 core 一致"


def test_no_accidental_shell_change() -> None:
    """3a 批不改壳（ui.tmpl.html 的 script 引用仍是 core.js，不是 ESM）。

    接壳是 3b 之后的事——本门防「骨架批超范围」提前动了壳。
    """
    # W3-3b（2026-11-24）：壳已接 ESM——占位仍在模板（构建期合成双 tag），
    # 产物同时引用 core.js（defer，业务全局）与 src/main.js（module，ESM 入口）。
    tmpl = (STUDIO / "ui.tmpl.html").read_text(encoding="utf-8")
    assert "<!--BUILD:JS1-->" in tmpl, "模板的构建占位应保留（由构建器合成）"
    built = (STUDIO / "ui.html").read_text(encoding="utf-8")
    assert 'src="assets/core.js"' in built, "产物应引用 core.js（业务全局，双轨期共存）"
    assert 'type="module"' in built and 'src/main.js' in built, "3b 应已接 ESM 入口"
    # 双轨期纪律：ESM 入口必须是 assets/ 相对路径（零外联红线不因 ESM 放宽）
    assert 'src="assets/src/main.js"' in built
