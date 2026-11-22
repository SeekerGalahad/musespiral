"""标题 A/B 三风格候选（P1-B，契约 §3.26）单元与接线测试——AI 全程注入假件。

覆盖任务书硬性断言：
  - 三个标题不重复且风格差异化（问题式/数字式/情感式三轴互异）；
  - AI 路径按序映射三风格、失败/未配置静默降级启发式（降级仍互异）；
  - apply 三选一写回（版本池先行）；封面 spec 本地确定性零 AI。
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest

from musespiral.services import title_ab

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _write_article(tmp_path: Path, body: str = "# 缓存实战指南\n\n缓存是提升性能的手段。\n\n## 原理\n\n## 实践\n\n## 陷阱\n",
                   meta: str = "title: 缓存实战\n") -> Path:
    p = tmp_path / "ab_sample.md"
    p.write_text(f"---\n{meta}---\n\n{body}", encoding="utf-8")
    return p


# ── 服务层：启发式（无 AI）三风格互异 ───────────────────────


def test_heuristic_three_styles_distinct(tmp_path) -> None:
    """无 AI 配置 → 启发式三风格兜底：标题互不重复、风格轴齐备、元数据齐全。"""
    p = _write_article(tmp_path)
    out = title_ab.generate_variants(p)
    assert out["source"] == "heuristic"
    vs = out["variants"]
    assert len(vs) == 3
    assert [v["style"] for v in vs] == ["问题式", "数字式", "情感式"]
    titles = [v["title"] for v in vs]
    assert len(set(titles)) == 3, f"标题必须互不重复: {titles}"
    for v in vs:
        assert v["rationale"]
        spec = v["cover_spec"]
        assert spec["size"] == "900x383" and spec["headline"]
        assert spec["style"] == v["style"] and spec["palette"]
    # 三风格模板确实差异化（不是同一模板的三个变体）
    assert vs[0]["title"].endswith("？") or "？" in vs[0]["title"]
    assert any(ch.isdigit() for ch in vs[1]["title"])
    assert vs[2]["title"] != vs[0]["title"]


def test_heuristic_uses_h1_and_section_count(tmp_path) -> None:
    """无 frontmatter title 时母题取 H1；数字式取实际小节数。"""
    p = _write_article(tmp_path, meta="cover: c.png\n")  # 无 title
    out = title_ab.generate_variants(p)
    digital = next(v for v in out["variants"] if v["style"] == "数字式")
    assert "3 个要点" in digital["title"]


def test_ai_three_styles_mapped_in_order(tmp_path, monkeypatch) -> None:
    """AI 返回 3 条 → 按序映射问题式/数字式/情感式，source=ai。"""
    p = _write_article(tmp_path)
    monkeypatch.setattr(
        title_ab, "_ai_configured", lambda: True
    )
    payload = json.dumps(["为啥缓存总失效？", "3 招搞定缓存", "终于搞懂缓存了"], ensure_ascii=False)
    monkeypatch.setattr(title_ab, "_ai_titles", lambda t, c, chat_fn=None: json.loads(payload))
    out = title_ab.generate_variants(p, chat_fn=lambda m, **k: payload)
    assert out["source"] == "ai"
    assert [v["title"] for v in out["variants"]] == json.loads(payload)
    assert [v["style"] for v in out["variants"]] == ["问题式", "数字式", "情感式"]


def test_ai_failure_degrades_to_heuristic(tmp_path, monkeypatch) -> None:
    """AI 已配置但调用失败 → 静默降级启发式（source=heuristic，三风格仍互异）。"""
    from musespiral.infra.ai import AIError

    p = _write_article(tmp_path)
    monkeypatch.setattr(title_ab, "_ai_configured", lambda: True)

    def boom(messages, **kwargs):
        raise AIError("连接失败")

    out = title_ab.generate_variants(p, chat_fn=boom)
    assert out["source"] == "heuristic"
    titles = [v["title"] for v in out["variants"]]
    assert len(set(titles)) == 3


def test_ai_partial_fill_keeps_three(tmp_path, monkeypatch) -> None:
    """AI 只回了 1 条可用 → 缺口由启发式按风格轴补齐（仍 3 条、互异）。"""
    p = _write_article(tmp_path)
    monkeypatch.setattr(title_ab, "_ai_configured", lambda: True)
    payload = json.dumps(["为啥缓存总失效？", "", "   "], ensure_ascii=False)
    out = title_ab.generate_variants(p, chat_fn=lambda m, **k: payload)
    assert out["source"] == "ai"
    assert len(out["variants"]) == 3
    assert len({v["title"] for v in out["variants"]}) == 3


def test_empty_article_raises(tmp_path) -> None:
    p = tmp_path / "empty.md"
    p.write_text("---\ncover: c.png\n---\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="全空"):
        title_ab.generate_variants(p)


def test_count_clamped(tmp_path) -> None:
    p = _write_article(tmp_path)
    assert len(title_ab.generate_variants(p, count=2)["variants"]) == 2
    assert len(title_ab.generate_variants(p, count=99)["variants"]) == 3  # 风格轴只有 3


# ── apply：三选一写回（版本池先行）─────────────────────────


def test_apply_variant_writes_back_with_pool(tmp_path) -> None:
    p = _write_article(tmp_path)
    before = p.read_text(encoding="utf-8")
    applied = title_ab.apply_variant(p, "为啥缓存总失效？")
    assert applied == "为啥缓存总失效？"
    import frontmatter

    assert frontmatter.load(p).metadata["title"] == "为啥缓存总失效？"
    pool = sorted(p.parent.glob(f"{p.name}.bak.*"))
    assert len(pool) == 1, "写回前必须先入版本池"
    assert pool[0].read_text(encoding="utf-8") == before


def test_apply_variant_rejects_empty(tmp_path) -> None:
    p = _write_article(tmp_path)
    with pytest.raises(ValueError, match="标题不能为空"):
        title_ab.apply_variant(p, "   ")


# ── Studio 接线（真实回环服务器）───────────────────────────


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    shutil.copy(FIXTURES / "cover.png", root / "cover.png")
    (root / "assets").mkdir()
    shutil.copy(FIXTURES / "assets" / "fig1.png", root / "assets" / "fig1.png")
    return root


@pytest.fixture
def server(studio_root: Path):
    from musespiral import studio

    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _post(server, path: str, body: dict) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    return resp.status, payload


def test_studio_title_ab_generate_heuristic(server, studio_root: Path) -> None:
    """generate（无 AI）：同步零落盘零 job，三风格候选齐全。"""
    code, payload = _post(server, "/api/action/title_ab",
                          {"article": "sample_article.md", "confirm": True})
    assert code == 200 and payload["ok"] is True
    assert payload["source"] == "heuristic"
    vs = payload["variants"]
    assert len(vs) == 3
    assert {v["style"] for v in vs} == {"问题式", "数字式", "情感式"}
    assert len({v["title"] for v in vs}) == 3
    # 零落盘零 job：无预览/衍生文件（同步动作，无 job_id 字段）
    assert "job_id" not in payload


def test_studio_title_ab_apply(server, studio_root: Path) -> None:
    """apply：三选一写回 frontmatter.title（版本池先行）。"""
    import frontmatter

    before = (studio_root / "sample_article.md").read_text(encoding="utf-8")
    code, payload = _post(server, "/api/action/title_ab",
                          {"article": "sample_article.md", "mode": "apply",
                           "title": "三招搞定缓存", "confirm": True})
    assert code == 200 and payload["ok"] is True
    assert payload["applied"] == "三招搞定缓存"
    assert frontmatter.load(studio_root / "sample_article.md").metadata["title"] == "三招搞定缓存"
    pool = sorted(studio_root.glob("sample_article.md.bak.*"))
    assert len(pool) == 1 and pool[0].read_text(encoding="utf-8") == before


def test_studio_title_ab_gates(server, studio_root: Path) -> None:
    """confirm 闸门 400；mode 枚举外 400；apply 空标题 400；文件不存在 404。"""
    code, payload = _post(server, "/api/action/title_ab", {"article": "sample_article.md"})
    assert code == 400 and "confirm" in payload.get("error", "")

    code, payload = _post(server, "/api/action/title_ab",
                          {"article": "sample_article.md", "mode": "nope", "confirm": True})
    assert code == 400 and "mode" in payload["error"]

    code, payload = _post(server, "/api/action/title_ab",
                          {"article": "sample_article.md", "mode": "apply",
                           "title": "  ", "confirm": True})
    assert code == 400 and "title" in payload["error"]

    code, payload = _post(server, "/api/action/title_ab",
                          {"article": "ghost.md", "confirm": True})
    assert code == 404
