"""Studio 本地工作台 API 契约与安全红线单测（设计 §15.2 / §15.3 / §15.4）。

覆盖:
  契约 —— 起真实端口（port=0 取实际端口）用 http.client 逐端点验 JSON 形状：
    /api/articles（含检查计数与 run_checks 一致性）、/api/article（frontmatter 级
    + 渲染级报告）、/api/article/html、/api/article/review、/api/platforms、/api/stats
  产物标记 —— 配对笔记 / 平台变体 / 物料包 / review / 发布日志 的 flags 探测
  安全红线 —— ../ 与绝对路径与 %00 穿越全部拒、动作白名单外 404、方法不符 405、
    动作必须 confirm:true、publish 无凭证/无封面返回可读 ok:false、错误文本脱敏
  动作三件套 —— adapt（无 AI 走 xhs 启发式）/ pack（limits 注入）/ publish（仅草稿）

全部走 tmp_path + MUSESPIRAL_HOME 隔离，不触碰 ~/.musespiral、不发真实网络请求
（publish 无凭证在 token 层即中断；adapt xhs 走本地启发式；脱敏用例
 monkeypatch requests.post 抛异常）。服务器只绑 127.0.0.1 回环地址。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

from musespiral import config, studio
from musespiral.core.content.parser import parse_article
from musespiral.core.quality.checks import run_checks
from musespiral.core.quality.render_checks import render_checks
from musespiral.studio import actions as studio_actions

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_UI_HTML = Path(studio.__file__).parent / "ui.html"
# M1 前端多文件化（2026-11-18）：JS/CSS 结构标记在 assets/core.js|core.css（壳只留 HTML）
_CORE_JS = Path(studio.__file__).parent / "assets" / "core.js"
_CORE_CSS = Path(studio.__file__).parent / "assets" / "core.css"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    """工作目录：复制 fixtures 样例文章与封面/配图。"""
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    shutil.copy(FIXTURES / "cover.png", root / "cover.png")
    (root / "assets").mkdir()
    shutil.copy(FIXTURES / "assets" / "fig1.png", root / "assets" / "fig1.png")
    return root


@pytest.fixture
def server(studio_root: Path, _isolate_home) -> studio.ThreadingHTTPServer:
    """起真实 Studio 服务器（127.0.0.1 + 系统分配端口），测试结束优雅关闭。"""
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(
    server: studio.ThreadingHTTPServer, method: str, path: str, body: dict | None = None
) -> tuple[int, str, bytes]:
    """发一次 HTTP 请求，返回 (状态码, Content-Type, body)；4xx/5xx 不抛异常。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    ctype = resp.getheader("Content-Type", "")
    conn.close()
    return resp.status, ctype, data


def _get_json(server: studio.ThreadingHTTPServer, path: str) -> tuple[int, object]:
    code, _, body = _request(server, "GET", path)
    return code, json.loads(body)


def _post_json(server: studio.ThreadingHTTPServer, path: str, body: dict | None) -> tuple[int, dict]:
    code, _, raw = _request(server, "POST", path, body)
    return code, json.loads(raw)


def _expected_counts(root: Path, rel: str) -> dict[str, int]:
    """用 run_checks + render_checks 真值算 fail/warn/pass 计数（完整审核口径）。"""
    article = parse_article(root / rel)
    results = run_checks(article, root / rel)
    results += render_checks(studio._render(article), article, root / rel)
    counts = {"fail": 0, "warn": 0, "pass": 0}
    for level, _ in results:
        counts[level.lower()] += 1
    return counts


# ---- GET 端点契约 ----


def test_binds_loopback_and_root_page(server: studio.ThreadingHTTPServer) -> None:
    """服务器只绑 127.0.0.1；GET / 返回 ui.html（前端未落盘时占位断言 404 JSON）。"""
    assert server.server_address[0] == "127.0.0.1"
    code, ctype, body = _request(server, "GET", "/")
    if _UI_HTML.is_file():
        assert code == 200
        assert ctype.startswith("text/html")
        assert body.strip()  # 三视图标识由 tests/test_studio_ui.py 校验（并行组）
    else:
        # 集成阶段前 ui.html 尚未落盘：占位断言，防止静默 HTML 500
        assert code == 404
        assert json.loads(body)["error"]


def test_articles_contract_matches_full_checks(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """/api/articles：卡片字段齐全，检查计数与 run_checks + render_checks 真值一致。"""
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    articles = payload["articles"]
    assert [a["path"] for a in articles] == ["sample_article.md"]

    item = articles[0]
    assert item["title"] == "示例文章：渲染级验收冒烟"
    assert isinstance(item["chars"], int) and item["chars"] > 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"])
    # Phase 2 队列卡片封面缩略图数据面：cover 投影进列表条目（与详情 meta.cover 同源；
    # 无 cover 的文章为空串，前端走 accent 色块回退）
    assert item["cover"] == "cover.png"
    assert item["checks"] == _expected_counts(studio_root, "sample_article.md")
    assert set(item["checks"]) == {"fail", "warn", "pass"}
    assert item["flags"] == {
        "review": False,
        "note": False,
        "variants": [],
        "pack": False,
        "published": False,
    }


def test_article_detail_contract(server: studio.ThreadingHTTPServer, studio_root: Path) -> None:
    """/api/article：frontmatter 级 run_checks 在前、渲染级 render_checks 追加在后。"""
    code, payload = _get_json(server, "/api/article?path=sample_article.md")
    assert code == 200
    assert payload["title"] == "示例文章：渲染级验收冒烟"
    assert payload["meta"]["cover"] == "cover.png"
    assert payload["renderable"] is True
    assert payload["note"] is None

    article = parse_article(studio_root / "sample_article.md")
    expected = [
        {"level": level, "message": message}
        for level, message in run_checks(article, studio_root / "sample_article.md")
    ]
    checks = payload["checks"]
    assert checks[: len(expected)] == expected
    assert len(checks) > len(expected)  # 渲染级检查已追加（§15.1 检查页 check+render_checks）
    assert all(set(c) == {"level", "message"} for c in checks)
    assert all(c["level"] in {"PASS", "WARN", "FAIL"} for c in checks)


def test_article_html_endpoint(server: studio.ThreadingHTTPServer) -> None:
    """/api/article/html：渲染后完整 HTML（text/html）。"""
    code, ctype, body = _request(server, "GET", "/api/article/html?path=sample_article.md")
    assert code == 200
    assert ctype.startswith("text/html")
    text = body.decode("utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "示例文章：渲染级验收冒烟" in text


def test_review_endpoint_requires_note(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """/api/article/review：无 note → 404；有配对笔记 → 对照 HTML（text/html）。"""
    code, payload = _get_json(server, "/api/article/review?path=sample_article.md")
    assert code == 404
    assert payload["error"]

    shutil.copy(FIXTURES / "source_note.md", studio_root / "source_note.md")
    code, ctype, body = _request(
        server, "GET", "/api/article/review?path=sample_article.md&note=source_note.md"
    )
    assert code == 200
    assert ctype.startswith("text/html")
    assert "对照审阅" in body.decode("utf-8")


def test_platforms_contract(server: studio.ThreadingHTTPServer) -> None:
    """/api/platforms：registry + profiles 能力矩阵，字段齐全。"""
    code, payload = _get_json(server, "/api/platforms")
    assert code == 200
    assert isinstance(payload, list)
    assert {p["code"] for p in payload} == {
        "mp", "xhs", "channels", "douyin", "zhihu", "juejin", "bilibili",
    }
    for item in payload:
        assert set(item) == {"code", "name", "route", "capabilities", "limits"}
        assert isinstance(item["capabilities"], list)  # JSON 安全的集合展平
        assert isinstance(item["limits"], dict)
    mp = next(p for p in payload if p["code"] == "mp")
    assert mp["name"] == "微信公众号"
    assert mp["route"] == "official_api"
    assert {"draft", "publish", "image"} <= set(mp["capabilities"])
    assert mp["limits"]["title_max_bytes"] == config.TITLE_MAX_BYTES


def test_stats_contract(server: studio.ThreadingHTTPServer, studio_root: Path) -> None:
    """/api/stats：stats.summarize 原样结构（含 by_platform/recent）。"""
    records = [
        {
            "ts": "2026-11-01T10:00:00",
            "action": "draft",
            "article": "sample_article.md",
            "title": "示例文章",
            "media_id": "m1",
            "platform": "mp",
        },
        {
            "ts": "2026-11-02T11:00:00",
            "action": "publish",
            "article": "sample_article.md",
            "title": "示例文章",
            "media_id": "m1",
            "platform": "xhs",
        },
    ]
    log = studio_root / config.PUBLISH_LOG
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )

    code, payload = _get_json(server, "/api/stats")
    assert code == 200
    assert payload["total_draft"] == 1
    assert payload["total_publish"] == 1
    assert set(payload["by_platform"]) == {"mp", "xhs"}
    assert payload["by_platform"]["mp"]["drafts"] == 1
    assert payload["by_platform"]["xhs"]["publishes"] == 1
    assert len(payload["recent"]) == 2  # 最新在前
    assert payload["recent"][0]["action"] == "publish"


def test_stats_recent_not_truncated_for_trend(server, studio_root: Path) -> None:
    """3-2 热修：单平台 15 条（各一天）时 by_platform.recent 全量 15 条——
    summarize 默认 recent_n=10 会截断，仪表盘 30 天趋势图逐事件计数即少柱。"""
    records = [
        {
            "ts": f"2026-10-{day:02d}T10:00:00",
            "action": "draft",
            "article": f"day{day}.md",
            "title": f"第{day}篇",
            "platform": "mp",
        }
        for day in range(1, 16)
    ]
    log = studio_root / config.PUBLISH_LOG
    log.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    code, payload = _get_json(server, "/api/stats")
    assert code == 200
    # 修复前：len == 10（默认截断）→ 趋势图 30 天柱少 5 根；修复后全量
    assert len(payload["by_platform"]["mp"]["recent"]) == 15
    assert payload["by_platform"]["mp"]["drafts"] == 15


def test_stats_dashboard_merges_home_and_excludes_demo(server, studio_root: Path) -> None:
    """第十四期 §1.2 契约看板侧：root∪home 合并计数、demo_ fixture 行判除。

    root 放 demo 行 + 真实 draft，home（MUSESPIRAL_HOME 隔离目录）放真实 publish
    ——仪表盘计数/分组/recent 均只含真实行（原 _log_path 单处二选一会漏
    home 行、且把演示 fixture 计入看板）。
    """
    from musespiral.infra.storage.jsonl import default_log_path

    (studio_root / config.PUBLISH_LOG).write_text(
        json.dumps(
            {
                "ts": "2026-11-11T10:30:00", "action": "draft", "article": "03_photo_essay.md",
                "title": "Studio 演示行", "media_id": "demo_draft_media_id_001",
                "platform": "mp",
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"ts": "2026-11-12T09:00:00", "action": "draft", "article": "a.md",
             "title": "root 真实草稿", "platform": "mp"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    home_log = default_log_path()
    home_log.parent.mkdir(parents=True, exist_ok=True)
    home_log.write_text(
        json.dumps(
            {"ts": "2026-11-13T09:00:00", "action": "publish", "article": "a.md",
             "title": "home 真实发表", "platform": "xhs"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    code, payload = _get_json(server, "/api/stats")
    assert code == 200
    assert payload["total_draft"] == 1 and payload["total_publish"] == 1  # demo 不计
    assert set(payload["by_platform"]) == {"mp", "xhs"}  # home 行并入
    assert [r["title"] for r in payload["recent"]] == ["home 真实发表", "root 真实草稿"]


def test_flags_demo_only_log_does_not_light_publish_badge(
    server, studio_root: Path
) -> None:
    """徽章侧：demo-only 日志行不点「已发草稿」徽章（真实行仍点亮，见上例）。"""
    (studio_root / config.PUBLISH_LOG).write_text(
        json.dumps(
            {
                "ts": "2026-11-11T10:30:00", "action": "draft", "article": "sample_article.md",
                "title": "Studio 演示行", "media_id": "demo_draft_media_id_001",
                "platform": "mp",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    flags = next(a["flags"] for a in payload["articles"] if a["path"] == "sample_article.md")
    assert flags["published"] is False  # demo 行不算发布记录（§1.2 判除）


def test_flags_detect_artifacts(server: studio.ThreadingHTTPServer, studio_root: Path) -> None:
    """产物标记：配对笔记/平台变体/物料包/review HTML/发布记录；衍生文件不进队列。"""
    (studio_root / "sample_article.note.md").write_text(
        "---\ntitle: 原笔记\n---\n\n原笔记段落。\n", encoding="utf-8"
    )
    (studio_root / "sample_article.xhs.md").write_text(
        "---\ntitle: 小红书变体\nplatform: xhs\n---\n\n变体正文。\n", encoding="utf-8"
    )
    pack_dir = studio_root / "sample_article.xhs.pack"
    pack_dir.mkdir()
    (pack_dir / "README.md").write_text("# 上传指引\n", encoding="utf-8")
    (studio_root / "sample_article.review.html").write_text("<html>review</html>", encoding="utf-8")
    log = studio_root / config.PUBLISH_LOG
    log.write_text(
        json.dumps(
            {
                "ts": "2026-11-01T10:00:00",
                "action": "draft",
                "article": "sample_article.md",
                "title": "示例文章",
                "platform": "mp",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    # 配对笔记 / 平台变体 / 物料包内 README 都不进队列
    assert [a["path"] for a in payload["articles"]] == ["sample_article.md"]
    flags = payload["articles"][0]["flags"]
    assert flags["review"] is True
    assert flags["note"] is True
    assert flags["variants"] == ["xhs"]
    assert flags["pack"] is True
    assert flags["published"] is True  # draft 记录也算「已发草稿」（§15.1 徽章口径）

    # 详情页拿到配对笔记 rel，可直接渲染对照
    code, detail = _get_json(server, "/api/article?path=sample_article.md")
    assert detail["note"] == "sample_article.note.md"
    code, ctype, _ = _request(
        server, "GET", "/api/article/review?path=sample_article.md&note=sample_article.note.md"
    )
    assert code == 200
    assert ctype.startswith("text/html")


def test_flags_variants_source_backlink(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """R3 回链契约对拍：variants 探测 source 字段优先、stem 约定兜底。

    三路实证（正典 §四-R3「优先读产物 source 字段，stem 约定降级为兜底」）：
      - 产物名与源 stem 脱钩（自定义 out_path）但 source=源文件名 → 命中；
      - 有回链字段的产物 source 指向别处 → 不认领（不让 stem 同名抢挂）；
      - 源改名后 source 文件名失配，经 source_sha1 == body_sha1(源正文) 仍命中，
        源正文再变更（sha1 漂移）即断链。
    """
    import frontmatter

    from musespiral.core.article import body_sha1

    # 路一：自定义产物名 + source 文件名回链；路二：stem 同名但 source 指他处
    (studio_root / "my_variant.xhs.md").write_text(
        "---\ntitle: 小红书变体\nplatform: xhs\nsource: sample_article.md\n---\n\n变体。\n",
        encoding="utf-8",
    )
    (studio_root / "sample_article.zhihu.md").write_text(
        "---\ntitle: 知乎变体\nplatform: zhihu\nsource: other.md\n---\n\n变体。\n",
        encoding="utf-8",
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    flags = {a["path"]: a["flags"] for a in payload["articles"]}["sample_article.md"]
    assert flags["variants"] == ["xhs"]  # custom 名命中；stem 同名但 source 指他处不计入

    # 路三：源改名 → source 失配，经 source_sha1 内容回链命中；正文漂移即断链
    moved = studio_root / "moved.md"
    (studio_root / "sample_article.md").rename(moved)
    (studio_root / "old_name.douyin.md").write_text(
        "---\ntitle: 抖音变体\nplatform: douyin\n"
        f"source: sample_article.md\nsource_sha1: {body_sha1(frontmatter.load(moved).content)}\n"
        "---\n\n变体。\n",
        encoding="utf-8",
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    moved_flags = {a["path"]: a["flags"] for a in payload["articles"]}["moved.md"]
    assert moved_flags["variants"] == ["douyin"]  # 改名后经 sha1 回链仍命中

    moved.write_text(
        moved.read_text(encoding="utf-8") + "\n\n正文已变更，产物哈希漂移。\n", encoding="utf-8"
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    moved_flags = {a["path"]: a["flags"] for a in payload["articles"]}["moved.md"]
    assert moved_flags["variants"] == []  # sha1 漂移 + 文件名/stem 均失配 → 断链


def test_articles_root_param_scoped_and_guarded(
    server: studio.ThreadingHTTPServer, studio_root: Path, tmp_path: Path
) -> None:
    """?root= 接受 root 相对路径与 root 内绝对路径（前端透传），越界一律 400。"""
    sub = studio_root / "sub"
    sub.mkdir()
    shutil.copy(FIXTURES / "cover.png", sub / "cover.png")
    (sub / "child.md").write_text(
        "---\ntitle: 子目录文章\ncover: cover.png\n---\n\n正文。\n", encoding="utf-8"
    )

    code, payload = _get_json(server, "/api/articles?root=sub")
    assert code == 200
    assert [a["path"] for a in payload["articles"]] == ["sub/child.md"]

    # 绝对路径形态（Story 前端 ?root=<绝对目录> 透传）：等于服务器 root 仍放行
    code, payload = _get_json(server, "/api/articles?root=" + quote(str(studio_root), safe=""))
    assert code == 200
    assert {a["path"] for a in payload["articles"]} == {"sample_article.md", "sub/child.md"}

    for bad_root in ("../", quote(str(tmp_path), safe="")):
        code, payload = _get_json(server, "/api/articles?root=" + bad_root)
        assert code == 400, bad_root
        assert payload["error"]


# ---- 安全红线：路径穿越 ----


@pytest.mark.parametrize(
    "bad_rel",
    ["../outside.md", "..%2Foutside.md", "sample_article%00.md"],
)
def test_traversal_rejected_on_get_endpoints(
    server: studio.ThreadingHTTPServer, tmp_path: Path, bad_rel: str
) -> None:
    """../ 与百分号编码的 NUL 穿越 → 400（每个 <rel> 参数逐一验证）。"""
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: 越界\ncover: cover.png\n---\n\n越界正文\n", encoding="utf-8")

    for path in (
        f"/api/article?path={bad_rel}",
        f"/api/article/html?path={bad_rel}",
        f"/api/article/review?path=sample_article.md&note={bad_rel}",
    ):
        code, _, body = _request(server, "GET", path)
        assert code == 400, (path, body)
        assert json.loads(body)["error"]


def test_absolute_path_rejected(
    server: studio.ThreadingHTTPServer, tmp_path: Path
) -> None:
    """绝对路径（即便真实存在且内容合法）→ 400。"""
    outside = tmp_path / "abs.md"
    outside.write_text("---\ntitle: 越界\ncover: cover.png\n---\n\n越界正文\n", encoding="utf-8")
    quoted = quote(str(outside), safe="")
    code, _, body = _request(server, "GET", f"/api/article?path={quoted}")
    assert code == 400
    assert json.loads(body)["error"]


def test_traversal_rejected_on_action_endpoints(
    server: studio.ThreadingHTTPServer, tmp_path: Path
) -> None:
    """动作接口同一防护：越界/绝对路径 400（不会触发动作执行）。"""
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: 越界\ncover: cover.png\n---\n\n越界正文\n", encoding="utf-8")
    bad_values = ["../outside.md", str(outside)]  # JSON body 内路径不经 URL 编码

    for bad in bad_values:
        for route, extra in (
            ("/api/action/adapt", {"platform": "xhs"}),
            ("/api/action/pack", {"platform": "xhs"}),
            ("/api/action/publish", {}),
            ("/api/action/batch_adapt", {"platforms": ["xhs"]}),
            ("/api/action/status", {"status": "reviewed"}),
        ):
            body = {"article": bad, "confirm": True, **extra}
            code, payload = _post_json(server, route, body)
            assert code == 400, (route, bad, payload)
            assert payload["ok"] is False

    # variant 同样受防护（pack 的 variant 参数）
    code, payload = _post_json(
        server,
        "/api/action/pack",
        {"article": "sample_article.md", "platform": "xhs", "variant": "../outside.md", "confirm": True},
    )
    assert code == 400
    assert payload["ok"] is False


def test_missing_file_inside_root_404(server: studio.ThreadingHTTPServer) -> None:
    """root 内但文件不存在 → 404（区别于穿越 400）。"""
    for path in ("/api/article?path=missing.md", "/api/article/html?path=missing.md"):
        code, _, body = _request(server, "GET", path)
        assert code == 404
        assert json.loads(body)["error"]


# ---- 安全红线：方法/路由/confirm ----


def test_method_not_allowed_405(server: studio.ThreadingHTTPServer) -> None:
    """GET/POST 以外方法 405；动作接口 GET、GET 接口 POST 同样 405。"""
    cases = [
        ("POST", "/api/articles"),
        ("POST", "/api/stats"),
        ("GET", "/api/action/adapt"),
        ("PUT", "/api/articles"),
        ("DELETE", "/api/article?path=sample_article.md"),
        ("PATCH", "/api/platforms"),
        ("OPTIONS", "/api/stats"),
        ("HEAD", "/api/platforms"),
        ("TRACE", "/api/stats"),  # stdlib 隐含 501 → 统一转 405
    ]
    for method, path in cases:
        code, _, _ = _request(server, method, path)
        assert code == 405, (method, path)


def test_unknown_routes_404_json(server: studio.ThreadingHTTPServer) -> None:
    """动作白名单外（submit 等）与未知路由 → 404 JSON，不存在任何发表接口。"""
    for path in ("/api/action/submit", "/api/action/publish-all", "/api/unknown", "/nope"):
        code, _, body = _request(server, "POST", path)
        assert code == 404, path
        assert json.loads(body)["error"]
    code, _, body = _request(server, "GET", "/api/action/publish")
    assert code == 405  # GET 动作路由：方法是错的
    code, _, body = _request(server, "GET", "/api/action/submit")
    assert code == 404  # 白名单外路由即路由不存在


@pytest.mark.parametrize("action", ["adapt", "pack", "publish", "batch_adapt", "status"])
@pytest.mark.parametrize("confirm", ["missing", "false", "string"])
def test_action_requires_confirm_true(
    server: studio.ThreadingHTTPServer, action: str, confirm: str
) -> None:
    """POST 动作缺 confirm / confirm 非 true → 400（人审闸门，五动作全覆盖）。"""
    base: dict = {"article": "sample_article.md"}
    if action in ("adapt", "pack"):
        base["platform"] = "xhs"
    if action == "batch_adapt":
        base["platforms"] = ["xhs"]
    if action == "status":
        base["status"] = "reviewed"
    if confirm == "false":
        base["confirm"] = False
    elif confirm == "string":
        base["confirm"] = "true"

    code, payload = _post_json(server, f"/api/action/{action}", base)
    assert code == 400
    assert payload["ok"] is False
    assert "confirm" in payload["error"]


def test_action_invalid_json_body_400(server: studio.ThreadingHTTPServer) -> None:
    """请求体非 JSON 对象 → 400（不进入动作执行）。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/api/action/adapt",
        body=b"not-json",
        headers={"Content-Type": "application/json", "Content-Length": "8"},
    )
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    assert resp.status == 400
    assert json.loads(body)["ok"] is False


# ---- 动作三件套（R4 起经任务层：提交返回 {ok,job_id}，轮询至终态）----


def _wait_job(
    server: studio.ThreadingHTTPServer, job_id: str, timeout: float = 10.0
) -> dict:
    """轮询 GET /api/jobs/<id> 至 done/failed（动作 fn 均为本地快速路径）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, job = _get_json(server, f"/api/jobs/{job_id}")
        assert code == 200
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


def _submit(
    server: studio.ThreadingHTTPServer, route: str, body: dict
) -> str:
    """提交动作并断言 200 {ok:true, job_id}，返回 job_id。"""
    code, payload = _post_json(server, route, body)
    assert code == 200, payload
    assert payload["ok"] is True
    assert payload["job_id"]
    return payload["job_id"]


def test_adapt_action_heuristic_without_ai(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """adapt：提交即回 {ok,job_id}；xhs 启发式出稿入 job.result；二次提交 job 失败（拒绝覆盖）。"""
    job_id = _submit(
        server,
        "/api/action/adapt",
        {"article": "sample_article.md", "platform": "xhs", "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["result"]["output"] == "sample_article.xhs.md"
    assert isinstance(job["result"]["warnings"], list)
    assert (studio_root / "sample_article.xhs.md").is_file()

    job_id = _submit(
        server,
        "/api/action/adapt",
        {"article": "sample_article.md", "platform": "xhs", "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "已存在" in job["error"]  # 红线：存在即拒绝覆盖（可读错误，已脱敏）


def test_adapt_action_channels_needs_ai(
    server: studio.ThreadingHTTPServer
) -> None:
    """adapt：channels 无 AI 时 AdaptNeedsAI → job failed，error 含 AI（不炸 worker）。"""
    job_id = _submit(
        server,
        "/api/action/adapt",
        {"article": "sample_article.md", "platform": "channels", "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "AI" in job["error"]


def test_pack_action_ok_and_limited(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """pack：生成四件套物料包（dir 入 job.result），limits 注入 profiles（标题超限标 FAIL）。"""
    job_id = _submit(
        server,
        "/api/action/pack",
        {"article": "sample_article.md", "platform": "xhs", "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["result"]["dir"] == "sample_article.xhs.pack"
    pack_dir = studio_root / "sample_article.xhs.pack"
    assert (pack_dir / "README.md").is_file()
    assert (pack_dir / "payload.md").is_file()
    assert (pack_dir / "checks.txt").is_file()
    assert (pack_dir / "cover" / "cover.png").is_file()
    checks = (pack_dir / "checks.txt").read_text(encoding="utf-8")
    assert "标题字数" in checks  # xhs title_max_chars 已注入


def test_pack_action_with_variant(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """pack：variant 显式传入时 payload 取其内容；variant 缺失 → 404（提交期同步）。"""
    variant = studio_root / "sample_article.zhihu.md"
    variant.write_text("---\ntitle: 知乎变体\n---\n\nVARIANT-MARKER 正文\n", encoding="utf-8")
    job_id = _submit(
        server,
        "/api/action/pack",
        {
            "article": "sample_article.md",
            "platform": "zhihu",
            "variant": "sample_article.zhihu.md",
            "confirm": True,
        },
    )
    assert _wait_job(server, job_id)["status"] == "done"
    assert "VARIANT-MARKER" in (studio_root / "sample_article.zhihu.pack" / "payload.md").read_text(
        encoding="utf-8"
    )

    code, payload = _post_json(
        server,
        "/api/action/pack",
        {"article": "sample_article.md", "platform": "zhihu", "variant": "nope.md", "confirm": True},
    )
    assert code == 404  # variant 路径不存在 → 404（不静默回退原文）
    assert payload["ok"] is False


# ---- 安全红线：publish 仅草稿 + 错误可读脱敏 ----


def test_publish_without_credentials_returns_readable_error(
    server: studio.ThreadingHTTPServer
) -> None:
    """publish 无凭证：提交期检查门通过 → job failed（token 层中断，绝无真实网络请求）。"""
    job_id = _submit(
        server, "/api/action/publish", {"article": "sample_article.md", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "凭证" in job["error"]
    assert "media_id" not in job.get("result", {})


def test_publish_without_cover_returns_readable_error(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """publish 无封面 → {ok:false,error}（frontmatter.cover 必填）。"""
    (studio_root / "no_cover.md").write_text("---\ntitle: 无封面\n---\n\n正文。\n", encoding="utf-8")
    code, payload = _post_json(
        server, "/api/action/publish", {"article": "no_cover.md", "confirm": True}
    )
    assert code == 200
    assert payload["ok"] is False
    assert "封面" in payload["error"]


def test_publish_error_text_sanitized(
    server: studio.ThreadingHTTPServer, studio_root: Path, monkeypatch
) -> None:
    """错误文本脱敏：job.error 里的 access_token/appid/secret 值打码 + 截断（凭证不进任何响应）。"""
    home = config.home_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / config.CREDENTIALS_FILE).write_text(
        json.dumps(
            {"accounts": {"default": {"appid": "APPID-LEAK-123", "secret": "SECRET-LEAK-456"}},
             "default": "default"}
        ),
        encoding="utf-8",
    )

    leaky = (
        "HTTPSConnectionPool(host='api.weixin.qq.com', port=443): Max retries exceeded with url: "
        "/cgi-bin/stable_token?access_token=TOKEN-LEAK-789 appid=APPID-LEAK-123 "
        "secret=SECRET-LEAK-456 " + "x" * 500
    )

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise requests.ConnectionError(leaky)

    monkeypatch.setattr("musespiral.infra.wechat.token.requests.post", _boom)

    job_id = _submit(
        server, "/api/action/publish", {"article": "sample_article.md", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    error = job["error"]
    for secret_value in ("TOKEN-LEAK-789", "APPID-LEAK-123", "SECRET-LEAK-456"):
        assert secret_value not in error
    assert "access_token=***" in error
    assert len(error) <= 201  # 截断上限（200 + 省略号）

    # 列表端点同样不泄漏（job.error 已在写入侧脱敏）
    code, payload = _get_json(server, "/api/jobs")
    assert code == 200
    assert "TOKEN-LEAK-789" not in json.dumps(payload, ensure_ascii=False)


# ---- 独立复核修复回归（2026-11-12）----


def test_title_injection_escaped_in_preview_html(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """frontmatter.title 里的 </title><script> 必须被转义，预览 HTML 无可执行脚本。"""
    (studio_root / "xss.md").write_text(
        '---\ntitle: "</title><script>alert(1)</script>"\ncover: cover.png\n---\n\n正文。\n',
        encoding="utf-8",
    )
    code, _, body = _request(server, "GET", "/api/article/html?path=xss.md")
    assert code == 200
    text = body.decode("utf-8")
    assert "<script>" not in text
    assert "&lt;/title&gt;" in text  # 原样落在 <title> 文本里，未逃逸成标签


def test_symlink_outside_root_skipped_and_not_leaked(
    server: studio.ThreadingHTTPServer, studio_root: Path, tmp_path: Path
) -> None:
    """指向 root 外的符号链接：扫描跳过、配对视同无、请求 400 且不回显外部绝对路径。"""
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\ntitle: root 外文章\ncover: cover.png\n---\n\n外部正文\n", encoding="utf-8"
    )
    (studio_root / "link.md").symlink_to(outside)

    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    assert [a["path"] for a in payload["articles"]] == ["sample_article.md"]

    (studio_root / "sample_article.note.md").symlink_to(outside)
    code, _, body = _request(server, "GET", "/api/article?path=sample_article.md")
    assert code == 200
    assert json.loads(body)["note"] is None
    assert str(outside) not in body.decode("utf-8")

    code, _, body = _request(server, "GET", "/api/article?path=link.md")
    assert code == 400
    assert str(outside) not in body.decode("utf-8")


def test_asset_route_serves_preview_images(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """预览 HTML 的相对图片重写到 /asset；图片白名单内回放，越界 400、非图片 404。"""
    code, _, body = _request(server, "GET", "/api/article/html?path=sample_article.md")
    text = body.decode("utf-8")
    assert 'src="/asset?path=cover.png"' in text
    assert "/asset?path=assets%2Ffig1.png" in text

    code, ctype, image = _request(server, "GET", "/asset?path=assets%2Ffig1.png")
    assert code == 200
    assert ctype.startswith("image/png")
    assert image

    for bad, expected in (("../outside.png", 400), ("sample_article.md", 404)):
        code, _, _ = _request(server, "GET", f"/asset?path={bad}")
        assert code == expected, bad
    # SVG 可执行脚本，同源回放会绕过 confirm 弹层 → 白名单外
    (studio_root / "evil.svg").write_text("<svg onload=\"alert(1)\"/>", encoding="utf-8")
    assert _request(server, "GET", "/asset?path=evil.svg")[0] == 404
    assert _request(server, "POST", "/asset", {})[0] == 405


def test_publish_blocks_render_level_fail(
    server: studio.ThreadingHTTPServer, studio_root: Path, monkeypatch
) -> None:
    """只有渲染级 FAIL（外链残留）的文章：publish 与 CLI 同门，拒绝建草稿。"""
    (studio_root / "render_fail.md").write_text(
        "---\ntitle: 渲染级 FAIL\ncover: cover.png\ndigest: 外链残留演示\n---\n\n"
        "[外链](HTTPS://example.com/render-fail)\n",
        encoding="utf-8",
    )
    calls: list[int] = []
    monkeypatch.setattr(studio_actions, "publish_flow", lambda *a, **k: calls.append(1) or "MID")

    code, payload = _post_json(
        server, "/api/action/publish", {"article": "render_fail.md", "confirm": True}
    )
    assert code == 200
    assert payload["ok"] is False
    assert "审核未通过" in payload["error"]
    assert calls == []  # 检查门在 publish_flow 之前拦下


def test_action_assets_must_stay_inside_root(
    server: studio.ThreadingHTTPServer, studio_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """frontmatter.cover 越界（../）：pack 拒绝复制、publish 拒绝读盘上传。"""
    outside_png = tmp_path / "outside_secret.png"
    shutil.copy(FIXTURES / "cover.png", outside_png)
    (studio_root / "leaky.md").write_text(
        "---\ntitle: 越界封面\ncover: ../outside_secret.png\n---\n\n![本地](cover.png)\n",
        encoding="utf-8",
    )

    code, payload = _post_json(
        server, "/api/action/pack", {"article": "leaky.md", "platform": "xhs", "confirm": True}
    )
    assert code == 400
    assert payload["ok"] is False
    assert "越界" in payload["error"]
    assert not (studio_root / "leaky.xhs.pack").exists()

    calls: list[int] = []
    monkeypatch.setattr(studio_actions, "publish_flow", lambda *a, **k: calls.append(1) or "MID")
    code, payload = _post_json(
        server, "/api/action/publish", {"article": "leaky.md", "confirm": True}
    )
    assert code == 400
    assert payload["ok"] is False
    assert calls == []


def test_non_utf8_publish_log_keeps_endpoints_alive(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """发布日志混入非 UTF-8 字节：坏行跳过，/api/articles 与 /api/stats 不 500。"""
    good = {
        "ts": "2026-11-01T10:00:00",
        "action": "draft",
        "article": "sample_article.md",
        "title": "示例文章",
        "platform": "mp",
    }
    log = studio_root / config.PUBLISH_LOG
    log.write_bytes(
        b"\xff\xfe\x00broken\n" + json.dumps(good, ensure_ascii=False).encode("utf-8") + b"\n"
    )

    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    assert payload["articles"][0]["flags"]["published"] is True
    code, stats_payload = _get_json(server, "/api/stats")
    assert code == 200
    assert stats_payload["total_draft"] == 1


def test_review_artifact_replayed_for_pairing(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """CLI review 产物 <stem>.review.html：详情 note 指向它、对照 tab 直接回放。"""
    artifact = studio_root / "sample_article.review.html"
    artifact.write_text(
        "<!DOCTYPE html><html><body>REVIEW-ARTIFACT</body></html>", encoding="utf-8"
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    assert payload["articles"][0]["flags"]["review"] is True

    code, detail = _get_json(server, "/api/article?path=sample_article.md")
    assert detail["note"] == "sample_article.review.html"
    code, ctype, body = _request(
        server, "GET", "/api/article/review?path=sample_article.md&note=sample_article.review.html"
    )
    assert code == 200
    assert ctype.startswith("text/html")
    assert "REVIEW-ARTIFACT" in body.decode("utf-8")

    # 非本篇派生名的 HTML 不会被原样回放（仍走转义的实时 diff）
    (studio_root / "evil.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    code, _, body = _request(
        server, "GET", "/api/article/review?path=sample_article.md&note=evil.html"
    )
    assert code == 200
    assert "<script>alert(1)</script>" not in body.decode("utf-8")


# ---- §16 F19：错误路径打码 + /api/meta 契约 ----


def test_sanitize_error_masks_paths_against_root(
    studio_root: Path, tmp_path: Path
) -> None:
    """_sanitize_error 路径打码：root 外绝对路径与 ~/ 路径 → <path>，root 内 → 相对形式。"""
    root = studio_root.resolve()
    outside = tmp_path / "outside_secret.md"
    err = RuntimeError(
        f"读取 {outside} 失败；另见 ~/.musespiral/credentials.json；"
        f"工作文件 {root / 'sample_article.md'} 正常"
    )
    text = studio._sanitize_error(err, root)
    assert str(outside) not in text  # root 外绝对路径不回显
    assert "~/.musespiral" not in text  # 家目录形态同样打码
    assert text.count("<path>") == 2
    assert "sample_article.md" in text  # root 内保留相对形式（定位信息不丢）
    assert str(root) not in text  # 绝对前缀不出现


def test_action_error_response_masks_paths(
    server: studio.ThreadingHTTPServer, studio_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """动作错误同一路径打码（R4 起经 job.error）：穿越 _sanitize_error(ctx.root) 全链路生效。"""
    outside = tmp_path / "stage_secret.png"
    leaky = RuntimeError(
        f"上传失败: {outside} 与 ~/secret/stage.png；源 {studio_root.resolve() / 'sample_article.md'}"
    )

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise leaky

    monkeypatch.setattr(studio_actions, "publish_flow", _boom)
    job_id = _submit(
        server, "/api/action/publish", {"article": "sample_article.md", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    error = job["error"]
    assert str(outside) not in error
    assert "~/" not in error
    assert "<path>" in error
    assert "sample_article.md" in error  # root 内 → 相对形式保留


def test_meta_endpoint_contract(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """GET /api/meta：{root,version,ai_configured} 契约；AI 只回布尔不回显配置内容。"""
    import musespiral
    from musespiral.infra import ai

    code, _, raw = _request(server, "GET", "/api/meta")
    assert code == 200
    payload = json.loads(raw)
    assert set(payload) == {"root", "version", "ai_configured"}
    assert payload["root"] == str(studio_root.resolve())  # 绝对路径（root chip 数据面）
    assert payload["version"] == musespiral.__version__
    assert payload["ai_configured"] is False  # MUSESPIRAL_HOME 隔离，无 ai.json

    # 配置 AI 后翻转为 True（每次请求现读），且配置内容绝不进响应
    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-meta-test-key", "meta-model-x")
    code, _, raw = _request(server, "GET", "/api/meta")
    assert code == 200
    payload = json.loads(raw)
    assert payload["ai_configured"] is True
    text = raw.decode("utf-8")
    for secret in ("sk-meta-test-key", "meta-model-x", "127.0.0.1:9"):
        assert secret not in text

    assert _request(server, "POST", "/api/meta", body={})[0] == 405  # 仅 GET


def test_screenshot_script_delivered_executable() -> None:
    """视觉冒烟脚本已交付且可执行（内容守门：demo 服务 + 三视图；不进 pytest 默认集）。"""
    script = Path(__file__).resolve().parent.parent / "scripts" / "musespiral_studio_screenshot.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK), "screenshot 脚本必须可执行"
    text = script.read_text(encoding="utf-8")
    assert "make_server" in text
    assert "studio_demo" in text
    assert '"$BASE/#$1"' in text  # hash 路由拼 URL
    for view in ('shot "queue"', 'shot "article?path=', 'shot "dashboard"'):
        assert view in text, view


def test_sanitize_error_masks_paths_with_spaces() -> None:
    """第七期复核 low：含空格的 root 外绝对路径整体打码，不在空格处截断泄漏尾段。"""
    from pathlib import Path

    from musespiral.studio import _sanitize_error

    root = Path("/home/user/researchEverything/demo")
    out = _sanitize_error(ValueError("上传失败: /home/user/My Secret Dir/stage.png"), root)
    assert "Secret" not in out
    assert "stage.png" not in out
    assert "<path>" in out


# ---- R4 任务层：/api/jobs 契约 + 批量 adapt + 状态动作 + 状态字段 ----


def test_jobs_endpoints_contract(server: studio.ThreadingHTTPServer) -> None:
    """GET /api/jobs 列表（新在前）与 /api/jobs/<id> 单条；未知 id 404；方法约束 405。"""
    code, payload = _get_json(server, "/api/jobs")
    assert code == 200
    assert payload == {"jobs": []}  # MUSESPIRAL_HOME 隔离，初始为空

    code, _, _ = _request(server, "GET", "/api/jobs/00000000")
    assert code == 404
    code, _, body = _request(server, "GET", "/api/jobs/")
    assert code == 404  # 空 id 段不匹配（未定义 /api/jobs/ 路由）
    assert json.loads(body)["error"]
    assert _request(server, "POST", "/api/jobs", body={"confirm": True})[0] == 405
    assert _request(server, "POST", "/api/jobs/00000000", body={"confirm": True})[0] == 405


def test_jobs_list_newest_first_and_single_poll(
    server: studio.ThreadingHTTPServer
) -> None:
    """/api/jobs 列表倒序；/api/jobs/<id> 单条含完整生命周期字段。"""
    ids = [
        _submit(server, "/api/action/adapt",
                {"article": "sample_article.md", "platform": p, "confirm": True})
        for p in ("xhs", "zhihu")
    ]
    for job_id in ids:
        _wait_job(server, job_id)

    code, payload = _get_json(server, "/api/jobs")
    assert code == 200
    listed = [job["id"] for job in payload["jobs"]]
    assert listed == ids[::-1]  # 最近 50 条，新在前
    job = payload["jobs"][0]
    for key in ("id", "kind", "params", "status", "created", "started", "finished", "result"):
        assert key in job, key

    code, single = _get_json(server, f"/api/jobs/{ids[0]}")
    assert code == 200 and single["id"] == ids[0]


def test_batch_adapt_job_runs_platforms_sequentially(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """batch_adapt：一 job 顺序生成 N 个产物，phase 记 n/N，outputs 依序入 result。"""
    job_id = _submit(
        server,
        "/api/action/batch_adapt",
        {"article": "sample_article.md", "platforms": ["xhs", "zhihu"], "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["kind"] == "batch_adapt"
    assert job["result"]["outputs"] == ["sample_article.xhs.md", "sample_article.zhihu.md"]
    assert job["phase"] == "2/2"  # 顺序执行的末段进度
    assert (studio_root / "sample_article.xhs.md").is_file()
    assert (studio_root / "sample_article.zhihu.md").is_file()


def test_batch_adapt_partial_failure_resend_skips_existing(
    server: studio.ThreadingHTTPServer, studio_root: Path, monkeypatch
) -> None:
    """batch_adapt 部分失败：已产文件随 JobFailure 入 result；重发跳过已存在产物。

    复核修复：失败 job 此前 result=None（已产文件只在 phase 里间接可见），
    且 UI「重发」=同参新建 job 会被 adapt.generate 的拒绝覆盖挡死
    （FileExistsError 掩盖原始失败原因，形成死循环）。
    """
    real_generate = studio_actions.adapt.generate

    def fail_on_zhihu(article_path, platform, out_path=None):
        if platform == "zhihu":
            raise RuntimeError("模拟失败：第 2 平台中断")
        return real_generate(article_path, platform, out_path)

    monkeypatch.setattr(studio_actions.adapt, "generate", fail_on_zhihu)
    job_id = _submit(
        server,
        "/api/action/batch_adapt",
        {"article": "sample_article.md", "platforms": ["xhs", "zhihu"], "confirm": True},
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "模拟失败" in job["error"]
    assert job["result"]["outputs"] == ["sample_article.xhs.md"]  # 中断前已产文件可见
    assert (studio_root / "sample_article.xhs.md").is_file()

    # 重发（同参新建 job）：已存在的 xhs 跳过不覆盖，只补 zhihu 缺口
    monkeypatch.setattr(studio_actions.adapt, "generate", real_generate)
    job_id2 = _submit(
        server,
        "/api/action/batch_adapt",
        {"article": "sample_article.md", "platforms": ["xhs", "zhihu"], "confirm": True},
    )
    job2 = _wait_job(server, job_id2)
    assert job2["status"] == "done"
    assert job2["result"]["outputs"] == ["sample_article.zhihu.md"]  # 只补缺口
    assert job2["result"]["skipped"] == ["sample_article.xhs.md"]
    assert (studio_root / "sample_article.zhihu.md").is_file()


def test_batch_adapt_validates_platforms_sync(
    server: studio.ThreadingHTTPServer
) -> None:
    """batch_adapt 参数校验同步：platforms 非数组/空/含非字符串 → 400，不建 job。"""
    for bad in (None, "xhs", [], ["xhs", 3]):
        code, payload = _post_json(
            server,
            "/api/action/batch_adapt",
            {"article": "sample_article.md", "platforms": bad, "confirm": True},
        )
        assert code == 400, bad
        assert payload["ok"] is False
    code, payload = _get_json(server, "/api/jobs")
    assert payload == {"jobs": []}  # 全被提交期拦下，无 job 落盘


def test_status_action_marks_reviewed(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """POST /api/action/status（瞬时同步）：白名单外 400；draft→reviewed 落 frontmatter。"""
    code, payload = _post_json(
        server,
        "/api/action/status",
        {"article": "sample_article.md", "status": "bogus", "confirm": True},
    )
    assert code == 400
    assert payload["ok"] is False

    code, payload = _post_json(
        server,
        "/api/action/status",
        {"article": "sample_article.md", "status": "reviewed", "confirm": True},
    )
    assert code == 200
    assert payload == {"ok": True, "status": "reviewed", "article": "sample_article.md"}
    text = (studio_root / "sample_article.md").read_text(encoding="utf-8")
    assert "status: reviewed" in text  # R3 状态机写入 frontmatter

    # 非法迁移（reviewed→reviewed 幂等合法；reviewed→archived 非法）由 core.article 把关
    code, payload = _post_json(
        server,
        "/api/action/status",
        {"article": "sample_article.md", "status": "archived", "confirm": True},
    )
    assert code == 200
    assert payload["ok"] is False
    assert "非法状态迁移" in payload["error"]

    # published 不开放人工置位（§三-2：只由 publish 成功自动回写；与 CLI
    # --set 同门）——此刻文章正是 reviewed，若白名单放行即可绕过人审闸门
    code, payload = _post_json(
        server,
        "/api/action/status",
        {"article": "sample_article.md", "status": "published", "confirm": True},
    )
    assert code == 400
    assert payload["ok"] is False
    assert "publish 成功自动置位" in payload["error"]
    assert "status: reviewed" in (studio_root / "sample_article.md").read_text(encoding="utf-8")


def test_articles_and_detail_carry_status_field(
    server: studio.ThreadingHTTPServer, studio_root: Path
) -> None:
    """/api/articles 卡片与 /api/article 详情带 status；缺字段读作 draft（R3 契约）。"""
    (studio_root / "reviewed_article.md").write_text(
        "---\ntitle: 已审文章\ncover: cover.png\nstatus: reviewed\n---\n\n正文。\n",
        encoding="utf-8",
    )
    code, payload = _get_json(server, "/api/articles")
    assert code == 200
    by_path = {a["path"]: a for a in payload["articles"]}
    assert by_path["sample_article.md"]["status"] == "draft"  # 缺 status 读作 draft
    assert by_path["reviewed_article.md"]["status"] == "reviewed"

    code, detail = _get_json(server, "/api/article?path=reviewed_article.md")
    assert code == 200
    assert detail["status"] == "reviewed"


# ---- R4 前端结构：任务面板 / 状态徽章 / 重发 / 标记已审 ----


def test_ui_has_job_panel_status_badges_and_resend() -> None:
    """壳+core.js+core.css：顶栏铃铛+角标+下拉、失败重发按钮、状态徽章、标记已审与 job 轮询。"""
    src = (_UI_HTML.read_text(encoding="utf-8") + "\n" + _CORE_JS.read_text(encoding="utf-8")
           + "\n" + _CORE_CSS.read_text(encoding="utf-8"))
    for token in (
        "jobs-btn",       # 顶栏铃铛
        "jobs-badge",     # 进行中角标
        "jobs-menu",      # 任务下拉面板
        "data-resend",    # 失败行「重发」按钮
        "重发",
        "waitForJob",     # 动作按钮轮询 job 至终态
        "mark-reviewed-btn",  # 详情「标记已审」
        "标记已审",
        "statusBadge",    # 队列卡片状态徽章（draft/reviewed/published 着色）
        "STATUS_LABELS",
        "badge.published",
        "badge.reviewed",
        "badge.draft",
    ):
        assert token in src, token
    # 新端点走模板字面量（单引号字面量集保持 §15.2 契约测试不变，集成阶段统一收编）
    assert "`/api/jobs/${id}`" in src
    assert "`/api/action/status`" in src
    assert "`/api/action/batch_adapt`" in src


def test_jobs_endpoint_recovers_orphans_on_first_access(
    server: studio.ThreadingHTTPServer
) -> None:
    """孤儿恢复挂在首次 /api/jobs 访问：遗留 running 行呈现为 failed(interrupted)。

    server.py 不感知任务层（R2 边界），恢复由 actions._runner() 惰性触发——
    本用例验证该接线：预置 running 行 → GET /api/jobs → 行已标 failed 可重发。
    """
    from musespiral.jobs.store import JobStore

    # 共享 runner 在进程内只做一次孤儿恢复（生产语义）；本用例重置后重走惰性路径
    studio_actions._reset_runner()
    store = JobStore()
    orphan = store.create("adapt", {"article": "sample_article.md", "platform": "xhs"})
    store.update(orphan["id"], status="running", started="2026-11-12T00:00:00")

    code, payload = _get_json(server, "/api/jobs")
    assert code == 200
    rows = {job["id"]: job for job in payload["jobs"]}
    assert rows[orphan["id"]]["status"] == "failed"
    assert "interrupted" in rows[orphan["id"]]["error"]
    # 重发=新建同参 job（旧行不复活）：提交同参 adapt → 新 job_id 落 jobs.jsonl
    job_id = _submit(
        server,
        "/api/action/adapt",
        {"article": "sample_article.md", "platform": "xhs", "confirm": True},
    )
    assert job_id != orphan["id"]
    _wait_job(server, job_id)
    code, payload = _get_json(server, "/api/jobs")
    ids = [job["id"] for job in payload["jobs"]]
    assert orphan["id"] in ids and job_id in ids  # 旧行保留供追溯，新行是重发


# ---- R7 编辑环节：body/save 往返 + 批注 sidecar + 按批注 AI 改写 ----


def _head_bytes(raw: bytes) -> bytes:
    """测试侧独立真值：frontmatter 头字节（首行 --- 到闭合 --- 行，含）。"""
    lines = raw.decode("utf-8").splitlines(keepends=True)
    assert lines[0].strip() == "---"
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[: i + 1]).encode("utf-8")
    raise AssertionError("frontmatter 未闭合")


def test_article_body_and_save_roundtrip(server, studio_root: Path) -> None:
    """GET body 只含正文；save 仅替换正文——frontmatter 字节恒等，旧版入版本池。"""
    code, payload = _get_json(server, "/api/article/body?path=sample_article.md")
    assert code == 200
    assert "示例文章" not in payload["content"]  # frontmatter 不含（title 只在头部）
    assert "微信内链" in payload["content"]

    orig_bytes = (studio_root / "sample_article.md").read_bytes()
    orig_head = _head_bytes(orig_bytes)
    new_body = "\n## 新正文\n\n保存后的段落。\n"
    code, payload = _post_json(
        server,
        "/api/article/save",
        {"article": "sample_article.md", "content": new_body, "confirm": True},
    )
    assert code == 200 and payload["ok"] is True

    saved = (studio_root / "sample_article.md").read_bytes()
    assert saved.startswith(orig_head)  # frontmatter 字节不动
    assert saved == orig_head + new_body.encode("utf-8")  # 正文整体替换
    # F43 版本池：旧版整文件字节入 <name>.md.bak.<时间戳>（滚动保留）
    pool = sorted(studio_root.glob("sample_article.md.bak.*"))
    assert len(pool) == 1 and pool[0].read_bytes() == orig_bytes

    code, payload = _get_json(server, "/api/article/body?path=sample_article.md")
    assert code == 200 and payload["content"] == new_body  # 读回往返一致

    # 缺 confirm / content 非字符串 → 400（动作管线人审闸门）
    for bad_body in (
        {"article": "sample_article.md", "content": "x"},
        {"article": "sample_article.md", "content": "x", "confirm": False},
        {"article": "sample_article.md", "confirm": True},
    ):
        code, payload = _post_json(server, "/api/article/save", bad_body)
        assert code == 400, bad_body
        assert payload["ok"] is False


def test_annotations_crud_and_sidecar_compat(server, studio_root: Path) -> None:
    """批注 CRUD：缺 sidecar=空列表、POST 生成短 id、DELETE 按 id（未命中 ok:false）。"""
    code, payload = _get_json(server, "/api/annotations?path=sample_article.md")
    assert code == 200 and payload == {"annotations": []}  # 缺文件 = 空

    code, payload = _post_json(
        server,
        "/api/annotations/add",
        {"path": "sample_article.md", "line_start": 3, "comment": "语气改口语化", "confirm": True},
    )
    assert code == 200 and payload["ok"] is True
    first = payload["annotation"]
    assert set(first) == {"id", "line_start", "line_end", "comment", "created"}
    assert first["line_end"] == 3  # line_end 缺省 = line_start
    assert len(first["id"]) == 8  # 短 id
    sidecar = studio_root / "sample_article.annotations.json"
    assert sidecar.is_file()

    code, payload = _post_json(
        server,
        "/api/annotations/add",
        {"path": "sample_article.md", "line_start": 5, "line_end": 7, "comment": "补充论据", "confirm": True},
    )
    second = payload["annotation"]
    code, payload = _get_json(server, "/api/annotations?path=sample_article.md")
    assert [a["id"] for a in payload["annotations"]] == [first["id"], second["id"]]

    code, payload = _post_json(
        server,
        "/api/annotations/delete",
        {"path": "sample_article.md", "id": "nope", "confirm": True},
    )
    assert code == 200 and payload["ok"] is False  # 未命中可读错误，不静默

    code, payload = _post_json(
        server,
        "/api/annotations/delete",
        {"path": "sample_article.md", "id": first["id"], "confirm": True},
    )
    assert code == 200 and payload["ok"] is True
    code, payload = _get_json(server, "/api/annotations?path=sample_article.md")
    assert [a["id"] for a in payload["annotations"]] == [second["id"]]

    # 参数校验（同步 400）：comment 缺失 / 行号非整数
    for bad in (
        {"path": "sample_article.md", "line_start": 3, "confirm": True},
        {"path": "sample_article.md", "line_start": "3", "comment": "x", "confirm": True},
        {"path": "sample_article.md", "line_start": 0, "comment": "x", "confirm": True},
    ):
        code, payload = _post_json(server, "/api/annotations/add", bad)
        assert code == 400, bad

    # 坏 JSON sidecar 读作空（编辑不因坏 sidecar 中断）
    sidecar.write_text("{not-json", encoding="utf-8")
    code, payload = _get_json(server, "/api/annotations?path=sample_article.md")
    assert code == 200 and payload == {"annotations": []}


def test_annotations_concurrent_add_keeps_valid_json(server, studio_root: Path) -> None:
    """并发写纪律回归锁：ThreadingHTTPServer 下 12 个并发 add 全部 ok 后，
    sidecar 必须仍是合法 JSON 且零丢失、无 .tmp 残留（复核实证：无锁
    write_text 交错 → json.loads 'Extra data' 数据破坏）。"""
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(
                _post_json,
                server,
                "/api/annotations/add",
                {
                    "path": "sample_article.md",
                    "line_start": i + 1,
                    "comment": f"并发批注{i}",
                    "confirm": True,
                },
            )
            for i in range(12)
        ]
        results = [f.result() for f in futures]
    assert all(code == 200 and payload["ok"] is True for code, payload in results)

    sidecar = studio_root / "sample_article.annotations.json"
    items = json.loads(sidecar.read_text(encoding="utf-8"))  # 合法 JSON（坏则此处抛错）
    assert len(items) == 12  # 零丢失
    assert len({a["id"] for a in items}) == 12  # id 无重复
    assert not list(studio_root.glob(".*annotations*.tmp"))  # 原子写无残留


def test_annotations_corrupt_sidecar_refuses_overwrite(server, studio_root: Path) -> None:
    """坏 sidecar 写保护（复核实证链路：并发写坏 → 下次 add 以空表覆写清空）：
    add/delete 对坏文件拒绝落盘（ok:false 可读错误、字节不动），GET 容错读空
    契约保持；annotate_rewrite 提交期也按「文件损坏」如实报错。"""
    for i in (1, 2):
        code, payload = _post_json(
            server,
            "/api/annotations/add",
            {"path": "sample_article.md", "line_start": i, "comment": f"批注{i}", "confirm": True},
        )
        assert code == 200 and payload["ok"] is True
    sidecar = studio_root / "sample_article.annotations.json"
    corrupt = json.dumps(
        [
            {
                "id": "deadbeef",
                "line_start": 1,
                "line_end": 1,
                "comment": "旧批注",
                "created": "2026-11-12T00:00:00",
            }
        ],
        ensure_ascii=False,
    )[:-3]  # 合法 JSON 尾部截断，模拟半写
    sidecar.write_text(corrupt, encoding="utf-8")

    code, payload = _post_json(
        server,
        "/api/annotations/add",
        {"path": "sample_article.md", "line_start": 9, "comment": "新批注", "confirm": True},
    )
    assert code == 200 and payload["ok"] is False
    assert "拒绝覆写" in payload["error"] and "sample_article.annotations.json" in payload["error"]
    assert sidecar.read_text(encoding="utf-8") == corrupt  # 字节不动，未被空表覆写

    code, payload = _post_json(
        server,
        "/api/annotations/delete",
        {"path": "sample_article.md", "id": "deadbeef", "confirm": True},
    )
    assert code == 200 and payload["ok"] is False and "拒绝覆写" in payload["error"]
    assert sidecar.read_text(encoding="utf-8") == corrupt

    # GET 容错契约不变（坏 sidecar 读作空，编辑不中断）
    code, payload = _get_json(server, "/api/annotations?path=sample_article.md")
    assert code == 200 and payload == {"annotations": []}

    # AI 改写提交期对坏 sidecar 如实报「损坏」而非误导性的「没有批注」
    code, payload = _post_json(
        server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True}
    )
    assert code == 200 and payload["ok"] is False and "损坏" in payload["error"]


def test_annotate_rewrite_mock_ai(server, studio_root: Path, monkeypatch) -> None:
    """annotate_rewrite：mock AI 下产物路径正确、原文字节不动、预览件不进队列。"""
    from musespiral.infra import ai

    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-anno-test", "anno-model")
    for comment in ("改口语化", "补一句结尾"):
        _post_json(
            server,
            "/api/annotations/add",
            {"path": "sample_article.md", "line_start": 1, "comment": comment, "confirm": True},
        )
    orig_bytes = (studio_root / "sample_article.md").read_bytes()

    calls: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "AI-ANNOTATED-MARKER 改写后的正文。"}}]}

    def _fake_post(url, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json")})
        return _Resp()

    from musespiral.infra.ai import adapt_mock as _am
    monkeypatch.setattr("musespiral.infra.ai.client._post_chat_stream", lambda u,h,b,to: _am(_fake_post, u, h, b, to))

    job_id = _submit(server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True})
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["kind"] == "annotate_rewrite"
    assert job["result"] == {"output": "sample_article.annotated.md", "annotations_used": 2}

    preview = studio_root / "sample_article.annotated.md"
    assert preview.is_file()
    text = preview.read_text(encoding="utf-8")
    assert "AI-ANNOTATED-MARKER" in text
    # frontmatter 内容沿袭原文（dumps 重序列化，非字节恒等——字节红线只约束原文自身）
    assert "title: 示例文章：渲染级验收冒烟" in text
    assert "ai_model: anno-model" in text
    assert (studio_root / "sample_article.md").read_bytes() == orig_bytes  # 原文一字节未动

    # AI 请求：批注意见与原文正文都进入 prompt
    sent = calls[0]["json"]["messages"][-1]["content"]
    assert "改口语化" in sent and "微信内链" in sent
    # 预览件不进文章队列（衍生后缀，不作为可发布文章参与筛选）
    code, payload = _get_json(server, "/api/articles")
    assert "sample_article.annotated.md" not in [a["path"] for a in payload["articles"]]

    # 预览文件已存在 → 重发 job 失败（拒绝覆盖），原文依旧未动
    job_id = _submit(server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True})
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "已存在" in job["error"]
    assert (studio_root / "sample_article.md").read_bytes() == orig_bytes


def test_annotate_rewrite_sync_gates(server, studio_root: Path) -> None:
    """提交期同步可读错误：无批注 / AI 未配置 → {ok:false} 且不建 job。"""
    code, payload = _post_json(
        server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True}
    )
    assert code == 200 and payload["ok"] is False
    assert "批注" in payload["error"]

    _post_json(
        server,
        "/api/annotations/add",
        {"path": "sample_article.md", "line_start": 1, "comment": "x", "confirm": True},
    )
    code, payload = _post_json(
        server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True}
    )
    assert code == 200 and payload["ok"] is False
    assert "AI" in payload["error"] and "musespiral ai config" in payload["error"]

    code, payload = _get_json(server, "/api/jobs")
    assert payload == {"jobs": []}  # 两类失败都在提交期拦下，无 job 落盘


def test_r7_endpoints_traversal_guarded(server, studio_root: Path, tmp_path: Path) -> None:
    """R7 新端点同一 <rel> 穿越防护：body/annotations 读侧 + save/add/delete/AI 写侧。"""
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: 越界\ncover: cover.png\n---\n\n越界正文\n", encoding="utf-8")
    bad_values = ["../outside.md", str(outside)]  # JSON body 内路径不经 URL 编码

    for bad in ("../outside.md", "..%2Foutside.md"):
        for path in (
            f"/api/article/body?path={bad}",
            f"/api/annotations?path={bad}",
        ):
            code, _, body = _request(server, "GET", path)
            assert code == 400, (path, body)
            assert json.loads(body)["error"]

    for bad in bad_values:
        for route, extra in (
            ("/api/article/save", {"content": "x"}),
            ("/api/annotations/add", {"line_start": 1, "comment": "x"}),
            ("/api/annotations/delete", {"id": "abcd1234"}),
            ("/api/action/annotate_rewrite", {}),
        ):
            # 先备好批注/AI 之外的唯一必填差异字段：路径字段名不同（article vs path）
            body = {"confirm": True, **extra}
            body["path" if "annotations" in route else "article"] = bad
            code, payload = _post_json(server, route, body)
            assert code == 400, (route, bad, payload)
            assert payload["ok"] is False


def test_ui_has_edit_tab_and_annotation_tools() -> None:
    """壳+core.js+views/*.js：编辑 tab 结构（textarea/行号列/批注侧栏/AI 按钮/保存）。

    M3（2026-11-19）按视图拆分后，编辑 tab 渲染与批注接线随 article 视图进
    assets/views/article.js，断言面取 core+views 合并面。
    """
    src = _UI_HTML.read_text(encoding="utf-8") + "\n" + _CORE_JS.read_text(encoding="utf-8")
    _vdir = _CORE_JS.parent / "views"
    if _vdir.is_dir():
        src += "\n" + "\n".join(p.read_text(encoding="utf-8") for p in sorted(_vdir.glob("*.js")))
    for token in (
        'data-tab="edit"',        # 编辑 tab
        'data-panel="edit"',
        "edit-area",              # 正文 textarea
        "edit-gutter",            # 行号列（批注模式点击目标）
        "annot-list",             # 批注侧栏
        "edit-save-btn",          # 保存正文
        "edit-annotate-toggle",   # 批注模式开关
        "edit-annotate-ai",       # 按批注 AI 修改
        "按批注 AI 修改",
        "editConfirm",            # 编辑环节确认弹层（askConfirm 薄封装）
        "annotPost",              # 批注增删共用 POST
    ):
        assert token in src, token
    # 新端点走模板字面量（单引号契约集不变，集成阶段统一收编）
    for token in (
        "`/api/article/body?path=${",
        "`/api/article/save`",
        "`/api/annotations?path=${",
        "`/api/annotations/add`",
        "`/api/annotations/delete`",
        "`/api/action/annotate_rewrite`",
    ):
        assert token in src, token


def test_annotate_rewrite_kind_whitelisted(server, studio_root: Path, monkeypatch) -> None:
    """kind 白名单锚定（主 agent 裁决的单行扩展）：annotate_rewrite 可创建 job。"""
    from musespiral.infra import ai
    from musespiral.jobs import KINDS

    assert "annotate_rewrite" in KINDS  # core/jobs/store.py KINDS 扩展锚点

    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-kind-test", "kind-model")
    _post_json(
        server,
        "/api/annotations/add",
        {"path": "sample_article.md", "line_start": 1, "comment": "x", "confirm": True},
    )

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "改写稿。"}}]}

    monkeypatch.setattr("musespiral.infra.ai.client._post_chat_stream", lambda u,h,b,to: __import__("io").BytesIO(__import__("json").dumps({"choices":[{"message":{"content":"AI-ANNOTATED-MARKER"}}]}).encode()))
    job_id = _submit(server, "/api/action/annotate_rewrite", {"article": "sample_article.md", "confirm": True})
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    code, listed = _get_json(server, "/api/jobs")
    assert [j["kind"] for j in listed["jobs"] if j["id"] == job_id] == ["annotate_rewrite"]


# ---- §十七 F20/F21：环境健康面板 + Studio 抓取入口 ----


def test_health_endpoint_contract(server, studio_root: Path) -> None:
    """/api/health：全键契约 + 布尔与名称 only（任何 secret 值不进响应）。"""
    import musespiral
    from musespiral.infra import ai
    from musespiral.infra.images import save_host_config
    from musespiral.browser import cookies as _cookies

    # 四类凭证/配置全部就绪（值是哨兵泄漏串：响应里出现即失败）
    config.save_credentials("default", "wx-appid-LEAK", "wx-secret-LEAK")
    ai.save_ai_config("http://127.0.0.1:9/v1", "sk-health-LEAK", "health-model")
    save_host_config(
        {"default": "github", "hosts": {"github": {"owner": "o", "repo": "r", "token": "ghp-LEAK"}}}
    )
    _cookies.save("channels", {"origins": []})
    themes_dir = config.home_dir() / "themes"
    themes_dir.mkdir(parents=True)
    (themes_dir / "mytheme.yaml").write_text("extends: lapis\n", encoding="utf-8")

    code, ctype, raw = _request(server, "GET", "/api/health")
    assert code == 200
    assert ctype.startswith("application/json")
    payload = json.loads(raw)

    assert set(payload) == {
        "version", "root", "disk_free_gb",
        "credentials", "ai", "imagehost", "cookies", "jobs", "themes",
    }
    assert payload["version"] == musespiral.__version__
    assert payload["root"] == str(studio_root.resolve())
    assert isinstance(payload["disk_free_gb"], (int, float)) and payload["disk_free_gb"] >= 0
    assert payload["credentials"] == {"configured": True, "accounts": 1}  # 仅数量
    assert payload["ai"] == {  # F25：补 default/profiles（名称 only），configured 口径=base_url+model
        "configured": True,
        "model": "health-model",
        "default": "default",
        "profiles": ["default"],
    }
    assert payload["imagehost"] == {"default": "github", "hosts": ["github"]}
    assert payload["cookies"] == {
        "channels": True, "douyin": False, "xhs": False, "kuaishou": False,
    }
    assert payload["jobs"] == {"queued": 0, "running": 0}  # 空闲无积压
    assert payload["themes"] == {"user_count": 1}

    # 红线：响应文本不含 secret 字样与任何配置写入的值
    text = raw.decode("utf-8")
    lowered = text.lower()
    for marker in ("appid", "secret", "token", "api_key"):
        assert marker not in lowered, marker
    for leak in ("wx-appid-LEAK", "wx-secret-LEAK", "sk-health-LEAK", "ghp-LEAK"):
        assert leak not in text, leak


class _FakeResp:
    """url_fetch 在线路径的 mock 响应（test_sources 同款；不真实联网）。"""

    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


def _wx_html(title: str) -> str:
    return (
        '<html><head><meta property="og:title" content="' + title + '"></head>'
        "<body><div id=\"js_content\"><p>抓来的正文段落。</p></div></body></html>"
    )


def test_fetch_whitelist_rejects(server: studio.ThreadingHTTPServer) -> None:
    """SSRF 白名单五种以上绕过形态：全部提交期 400，不建 job；缺 confirm 同拒。"""
    bad_urls = [
        "https://github.com/foo/bar",            # 非 mp 域
        "http://127.0.0.1/s",                    # 回环 IP 形态
        "http://192.168.1.10/s",                 # 内网 IP 形态
        "http://[::1]/s",                        # IPv6 回环形态
        "http://localhost/s",                    # localhost 形态
        "https://mp.weixin.qq.com@evil.com/s",   # userinfo 混淆（真实主机 evil.com）
        "https://user:pass@mp.weixin.qq.com/s",  # 带凭证形态（主机正确也拒）
        "https://mp.weixin.qq.com.evil.com/s",   # 后缀伪装域
        "ftp://mp.weixin.qq.com/s",              # 非 http(s) scheme
        "file:///etc/passwd",                    # 本地文件 scheme
    ]
    for url in bad_urls:
        code, payload = _post_json(server, "/api/action/fetch", {"url": url, "confirm": True})
        assert code == 400, (url, payload)
        assert payload["ok"] is False
        assert payload["error"]

    # 缺 confirm → 400（server 层人审闸门，先于动作执行）
    code, payload = _post_json(server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/x"})
    assert code == 400
    assert "confirm" in payload["error"]

    # 全部在提交期拦下：无 job 落盘
    code, listed = _get_json(server, "/api/jobs")
    assert listed == {"jobs": []}


def test_fetch_job_success_writes_root(server, studio_root: Path, monkeypatch) -> None:
    """mock 在线路径：job done、产物入 root、frontmatter status: draft 入队可见。"""
    import frontmatter
    from musespiral.jobs import KINDS

    assert "fetch" in KINDS  # core/jobs/store.py KINDS 扩展锚点（§十七 F21 预授权）

    captured: list[str] = []

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        captured.append(url)
        assert allow_redirects is False  # SSRF 防线：不自动跟随重定向
        return _FakeResp(_wx_html("抓取标题测试"))

    monkeypatch.setattr(studio_actions.url_fetch.requests, "get", fake_get)

    url = "https://mp.weixin.qq.com/s/abc123"
    job_id = _submit(server, "/api/action/fetch", {"url": url, "confirm": True})
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["kind"] == "fetch"
    assert job["params"] == {"url": url}
    assert job["result"] == {"output": "抓取标题测试.md"}
    assert captured == [url]  # 在线路径真实发起（网络层 mock）且只发一次

    dest = studio_root / "抓取标题测试.md"
    assert dest.is_file()
    post = frontmatter.load(dest)
    assert post.metadata["title"] == "抓取标题测试"
    assert post.metadata["source_url"] == url
    assert post.metadata["status"] == "draft"
    assert "抓来的正文段落" in post.content

    # 入队可见：标题 frontmatter 齐备 → /api/articles 可见（status 徽章 draft）
    code, payload = _get_json(server, "/api/articles")
    row = next(a for a in payload["articles"] if a["path"] == "抓取标题测试.md")
    assert row["status"] == "draft"


def test_fetch_slug_conflict_suffix(server, studio_root: Path, monkeypatch) -> None:
    """slug 冲突：同名产物已存在 → 依次 -2/-3 后缀，绝不覆盖既有文件。"""
    monkeypatch.setattr(
        studio_actions.url_fetch.requests, "get", lambda *a, **k: _FakeResp(_wx_html("同名标题"))
    )
    for expected in ("同名标题.md", "同名标题-2.md", "同名标题-3.md"):
        job_id = _submit(
            server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/x", "confirm": True}
        )
        job = _wait_job(server, job_id)
        assert job["status"] == "done"
        assert job["result"] == {"output": expected}
    assert sorted(p.name for p in studio_root.glob("同名标题*.md")) == [
        "同名标题-2.md", "同名标题-3.md", "同名标题.md",
    ]


def test_fetch_slug_traversal_guarded(server, studio_root: Path, monkeypatch) -> None:
    """穿越防护：标题含 ../ 与路径分隔符 → 净化为单段文件名，恒在 root 内。"""
    monkeypatch.setattr(
        studio_actions.url_fetch.requests,
        "get",
        lambda *a, **k: _FakeResp(_wx_html("../../etc/passwd")),
    )
    job_id = _submit(
        server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/y", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["result"] == {"output": "etcpasswd.md"}  # 点与斜杠全部剔除
    assert (studio_root / "etcpasswd.md").is_file()
    assert not (studio_root / "etc").exists()
    assert not (studio_root.parent / "etc").exists()

    # 路径分隔符（正反斜杠）与空白/标点一并净化
    monkeypatch.setattr(
        studio_actions.url_fetch.requests,
        "get",
        lambda *a, **k: _FakeResp(_wx_html("a/b\\c d!e")),
    )
    job_id = _submit(
        server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/z", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "done"
    assert job["result"] == {"output": "abcde.md"}


def test_fetch_job_dead_link_readable_error(server, monkeypatch) -> None:
    """失效链接：job failed 且错误可读（无 js_content → FetchError 文案入 job.error）。"""
    monkeypatch.setattr(
        studio_actions.url_fetch.requests,
        "get",
        lambda *a, **k: _FakeResp("<html><body>占位页</body></html>"),
    )
    job_id = _submit(
        server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/dead", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "文章失效或需在微信内打开" in job["error"]


def test_fetch_redirect_to_internal_blocked_in_job(server, monkeypatch) -> None:
    """SSRF 复核 high 回归：提交期 URL 合法但链上 30x 指向内网 —— 重定向
    逐跳白名单在网络层拒绝（job failed 可读错误），内网目标零接触。

    提交期白名单只校验初始 URL；requests 默认跟随重定向会把请求导向
    127.0.0.1/169.254.169.254 并把响应体解析落盘。修复后 302 Location
    先过 url_fetch.assert_article_url 再发下一跳，此处 mock 断言第二跳
    根本没有发生。"""
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        calls.append(url)
        assert allow_redirects is False
        return _FakeResp("", status_code=302, headers={"Location": "http://127.0.0.1:18792/internal"})

    monkeypatch.setattr(studio_actions.url_fetch.requests, "get", fake_get)

    job_id = _submit(
        server, "/api/action/fetch", {"url": "https://mp.weixin.qq.com/s/redirect", "confirm": True}
    )
    job = _wait_job(server, job_id)
    assert job["status"] == "failed"
    assert "重定向目标被拒绝" in job["error"]
    assert "127.0.0.1" in job["error"]
    # 内网目标从未被请求：仅初始一跳发出
    assert calls == ["https://mp.weixin.qq.com/s/redirect"]
