"""性能基线（R9 测试体系升级，正典 §四-R9）。

  - 100 篇 fixture 目录（frontmatter 最小 + 短正文循环生成于 tmp_path）
  - GET /api/articles 响应 < 2.0s（真实 HTTP，起 127.0.0.1 临时端口服务器）
  - variants 产物探测 100 篇 < 1.0s（_VariantScanCache 请求形态：与
    /api/articles 单次请求内的调用形状一致；R9 优化前后对拍见正典 §九）

阈值宽松防 CI 抖动（正典原文如此）；默认随全量跑——@pytest.mark.perf 仅作
`-m "not perf"` 筛选用。全部走 tmp_path + MUSESPIRAL_HOME 隔离，不触 ~/.musespiral。
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.studio.scan import (
    _VariantScanCache,
    _iter_article_files,
    _variants,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_N = 100  # 正典锚定的规模：100 篇


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral（发布日志回落路径隔离）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def hundred_root(tmp_path: Path) -> Path:
    """100 篇最小 frontmatter 文章 + 短正文（循环生成），另配 20 个变体：

    - 10 个 stem 命名无回链字段（旧产物，stem 约定兜底路径）；
    - 10 个自定义名 + source 回链字段（R3 回链路径）。
    变体让 variants 探测有真实工作量，而非空目录扫描。
    """
    root = tmp_path / "articles"
    root.mkdir()
    # resolve：macOS 下 tmp_path 在 /var/folders（/private/var 符号链接内侧），
    # _iter_article_files 按解析后绝对路径做边界判定，root 须与其同口径
    root = root.resolve()
    body = "\n\n".join(f"第 {i} 段：性能基线短正文，模拟真实文章的段落长度与标点。" for i in range(8))
    for i in range(_N):
        (root / f"perf_{i:03d}.md").write_text(
            f"---\ntitle: 性能文章{i:03d}\ncover: cover.png\n---\n\n{body}\n",
            encoding="utf-8",
        )
    shutil.copy(FIXTURES / "cover.png", root / "cover.png")
    for i in range(0, 20, 2):  # 10 个 stem 命名（无回链字段 → stem 兜底）
        (root / f"perf_{i:03d}.xhs.md").write_text(
            f"---\ntitle: 变体{i}\nplatform: xhs\n---\n\n变体正文。\n", encoding="utf-8"
        )
    for i in range(1, 21, 2):  # 10 个自定义名 + source 回链（产物名与源名脱钩）
        (root / f"custom_out_{i}.zhihu.md").write_text(
            f"---\ntitle: 变体{i}\nplatform: zhihu\nsource: perf_{i:03d}.md\n---\n\n变体。\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def server(hundred_root: Path) -> studio.ThreadingHTTPServer:
    """起真实 Studio 服务器（127.0.0.1 + 系统分配端口），结束优雅关闭。"""
    httpd = studio.make_server(hundred_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.mark.perf
def test_articles_endpoint_100_under_2s(server: studio.ThreadingHTTPServer) -> None:
    """100 篇（含 20 变体）队列载荷的完整 HTTP 响应 < 2.0s，且内容齐全。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    t0 = time.perf_counter()
    conn.request("GET", "/api/articles")
    resp = conn.getresponse()
    data = resp.read()
    elapsed = time.perf_counter() - t0
    conn.close()

    assert resp.status == 200
    articles = json.loads(data)["articles"]
    assert len(articles) == _N  # 变体/封面 png 不进队列
    assert sum(1 for a in articles if a["flags"]["variants"]) == 20
    assert elapsed < 2.0, f"/api/articles 100 篇耗时 {elapsed:.3f}s 超基线 2.0s"


@pytest.mark.perf
def test_variants_probe_100_under_1s(hundred_root: Path) -> None:
    """variants 探测 100 篇 < 1.0s（请求形态：单 _VariantScanCache 复用）。"""
    files = _iter_article_files(hundred_root)
    assert len(files) == _N
    scan = _VariantScanCache()
    t0 = time.perf_counter()
    hits = sum(len(_variants(path, scan)) for path in files)
    elapsed = time.perf_counter() - t0

    assert hits == 20  # 10 stem 兜底 + 10 source 回链，语义与优化前一致
    assert elapsed < 1.0, f"variants 探测 100 篇耗时 {elapsed:.3f}s 超基线 1.0s"
