"""POST /api/action/cover 契约测试（P3 封面工坊；契约 §3.2-1 壳层接线）。

覆盖：confirm 全局闸门/必填与 404/枚举校验 400（route·platform·write_back）/
同步生成（Pillow 兜底直绘确定性路径——monkeypatch sys.modules 复现无浏览器
环境，与 test_cover.py 同款约定）/mp 默认写回 frontmatter cover + 平台封面
与 write_back:false 不写回/业务失败 200 {ok:false}（from-image 缺源图）。
真实回环服务器 + tmp_path 与 MUSESPIRAL_HOME 全隔离。
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

from musespiral import studio


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    # 无浏览器环境确定性走 Pillow 兜底直绘（title-card 路线）
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def article(studio_root: Path) -> Path:
    path = studio_root / "demo.md"
    path.write_text(
        "---\n"
        "title: 缓存优化三步走\n"
        "digest: 摘要。\n"
        "---\n"
        "\n"
        "首句钩子在这里。\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _fm_cover(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("cover:"):
            return line.split(":", 1)[1].strip()
    return ""


def test_cover_requires_confirm(server) -> None:
    """confirm 全局闸门：缺省 → 400（人审闸门先于一切）。"""
    code, payload = _request(server, "POST", "/api/action/cover", {"article": "demo.md"})
    assert code == 400
    assert "confirm" in payload["error"]


def test_cover_accepts_legacy_article_alias(server) -> None:
    """W1 参数规范：`article` 旧名仍可用（别名至少保留一个版本）。

    读面 GET 用 path、写面 POST 用 article 是历史两套命名（实测 6 处）；
    `_require_str(..., alias=...)` 让两键兼容且新代码走规范名 path。
    本测试锁「别名不破」——若将来移除 alias，此测试必须同步改并记 BREAKING。
    """
    code, payload = _request(
        server, "POST", "/api/action/cover", {"confirm": True, "article": "03_photo_essay.md"}
    )
    # 关键断言=别名被识别：绝不落到「缺少必填字段」分支。
    # 200=成功、400=服务层业务拒绝、404=文件不在该 fixture root 里——都可接受，
    # 因为三者的共同前提是「参数校验已通过」。
    assert "缺少必填字段" not in (payload.get("error") or ""), payload


def test_cover_requires_article(server) -> None:
    code, payload = _request(server, "POST", "/api/action/cover", {"confirm": True})
    # 2026-11-24 W1 参数规范批：规范名 path（article 为兼容别名）——断言只锁
    # 「400 + 可读错误提到必填字段」，不锚死具体字段名（避免实现字符串锚腐化）
    assert code == 400 and "缺少必填字段" in payload["error"]


def test_cover_article_not_found(server) -> None:
    code, _ = _request(
        server, "POST", "/api/action/cover", {"confirm": True, "article": "nope.md"}
    )
    assert code == 404


@pytest.mark.parametrize(
    "extra",
    [
        {"route": "bogus"},
        {"platform": "zhihu"},  # zhihu 无封面规格，不在 platform_choices 枚举
        {"platform": "nope"},
        {"write_back": "yes"},
        {"theme": "  "},
    ],
)
def test_cover_enum_and_type_gates(server, article: Path, extra: dict) -> None:
    """提交期校验（400 契约）：route/platform 枚举、write_back/theme 类型。"""
    code, _ = _request(
        server, "POST", "/api/action/cover", {"confirm": True, "article": "demo.md", **extra}
    )
    assert code == 400


def test_cover_generates_png_and_writes_back(server, article: Path, studio_root: Path) -> None:
    """默认（mp）：cover.png 落文章目录 + frontmatter cover 写回。"""
    code, payload = _request(
        server, "POST", "/api/action/cover", {"confirm": True, "article": "demo.md"}
    )
    assert code == 200 and payload["ok"] is True
    assert payload["cover"] == "cover.png"  # root 相对 posix
    png = studio_root / "cover.png"
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert _fm_cover(article) == "cover.png"


def test_cover_platform_variant_no_write_back(server, article: Path, studio_root: Path) -> None:
    """platform=xhs：cover.xhs.png 只落图，frontmatter 不写回（F31 纪律）。"""
    code, payload = _request(
        server,
        "POST",
        "/api/action/cover",
        {"confirm": True, "article": "demo.md", "platform": "xhs"},
    )
    assert code == 200 and payload["cover"] == "cover.xhs.png"
    assert (studio_root / "cover.xhs.png").is_file()
    assert _fm_cover(article) == ""


def test_cover_write_back_false(server, article: Path, studio_root: Path) -> None:
    """write_back=false：PNG 落盘、frontmatter 不动。"""
    code, payload = _request(
        server,
        "POST",
        "/api/action/cover",
        {"confirm": True, "article": "demo.md", "write_back": False},
    )
    assert code == 200 and payload["cover"] == "cover.png"
    assert (studio_root / "cover.png").is_file()
    assert _fm_cover(article) == ""


def test_cover_business_failure_readable(server, article: Path) -> None:
    """生成失败 → 200 {ok:false} 可读错误（from-image 缺源图，不 500）。"""
    code, payload = _request(
        server,
        "POST",
        "/api/action/cover",
        {"confirm": True, "article": "demo.md", "route": "from-image"},
    )
    assert code == 200 and payload["ok"] is False
    assert "源图" in payload["error"]
