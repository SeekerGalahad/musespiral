"""platforms 适配器框架单测（§十四 F12）。

覆盖: registry 查询与未注册错误 / profiles 限制值与未知平台错误 /
WeChatAdapter.validate 按公众号限制（标题字节/封面比例）出结果 /
build_payload 结构含 title/html/digest / API 三件套的路线分档拒绝与
wrapper 编排。全部走 tmp_path + MUSESPIRAL_HOME 隔离，不触碰 ~/.musespiral，
网络一律 monkeypatch（无真实 AI/平台调用）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from musespiral.core.content.parser import ParsedArticle, parse_article
from musespiral.platforms import (
    PLIMITS,
    PlatformAdapter,
    WeChatAdapter,
    get,
    get_limits,
    list_platforms,
    register,
)
from musespiral.platforms import registry as _registry


def _article(metadata: dict | None = None, blocks: list | None = None) -> ParsedArticle:
    """构造测试文章（缺省只带标题元数据）。"""
    return ParsedArticle(
        metadata=dict(metadata or {"title": "测试文章"}),
        blocks=list(blocks or []),
        raw_body="正文",
    )


def _make_image(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), "#336699").save(path)


def _levels(results) -> list[str]:
    return [lvl for lvl, _ in results]


# ---- registry ----


def test_registry_has_mp_with_official_api_route() -> None:
    adapter = get("mp")
    assert isinstance(adapter, WeChatAdapter)
    assert adapter.name == "mp"
    assert adapter.route == "official_api"
    assert {"draft", "publish", "image"} <= adapter.capabilities
    assert any(a.name == "mp" for a in list_platforms())


def test_registry_unknown_platform_readable_error() -> None:
    with pytest.raises(ValueError, match="未知平台: nope（已注册: bilibili/channels/douyin/juejin/mp/xhs/zhihu）"):
        get("nope")


def test_register_then_get_custom_adapter(monkeypatch) -> None:
    """手工注册 manual_pack 适配器可被 get 查到；测试用副本注册表互不污染。"""

    class _Manual(PlatformAdapter):
        name = "_demo"
        route = "manual_pack"
        capabilities = set()

        def validate(self, article, article_path=None):
            return []

        def build_payload(self, article):
            return {}

    monkeypatch.setattr(_registry, "REGISTRY", dict(_registry.REGISTRY))
    register(_Manual())
    assert isinstance(get("_demo"), _Manual)
    assert get("_demo").route == "manual_pack"
    assert get("mp") is not None  # 既有注册不受影响


def test_non_official_api_route_raises_not_implemented() -> None:
    """manual_pack 路线的 API 三件套显式 NotImplementedError（含路线信息）。"""

    class _Manual(PlatformAdapter):
        name = "_demo"
        route = "manual_pack"
        capabilities = set()

        def validate(self, article, article_path=None):
            return []

        def build_payload(self, article):
            return {}

    a = _Manual()
    with pytest.raises(NotImplementedError, match=r"_demo.*manual_pack.*create_draft"):
        a.create_draft(_article(), "x.md")
    with pytest.raises(NotImplementedError, match=r"_demo.*manual_pack.*publish"):
        a.publish("MEDIA1")
    with pytest.raises(NotImplementedError, match=r"_demo.*manual_pack.*status"):
        a.status("PUB1")


# ---- profiles ----


def test_profiles_mp_limits_share_config_constants() -> None:
    from musespiral import config

    limits = get_limits("mp")
    assert limits["title_max_bytes"] == config.TITLE_MAX_BYTES == 64
    assert limits["digest_max_bytes"] == config.DIGEST_MAX_BYTES == 120
    assert limits["cover_ratio"] == 2.35


def test_profiles_covers_design_doc_platforms() -> None:
    assert set(PLIMITS) == {"mp", "xhs", "channels", "douyin", "zhihu", "juejin", "bilibili"}
    xhs = get_limits("xhs")
    assert xhs["title_max_chars"] == 20
    assert xhs["content_max_chars"] == 1000
    assert (xhs["images_min"], xhs["images_max"]) == (1, 18)
    assert xhs["tags_max"] == 10
    channels = get_limits("channels")
    assert (channels["title_min_chars"], channels["title_max_chars"]) == (3, 16)
    assert channels["desc_max_chars"] == 1000
    assert (channels["duration_min_minutes"], channels["duration_max_minutes"]) == (1, 60)
    douyin = get_limits("douyin")
    assert douyin["title_max_chars"] == 55
    assert (douyin["duration_min_seconds"], douyin["duration_max_seconds"]) == (15, 900)
    zhihu = get_limits("zhihu")
    assert zhihu["title_max_chars"] == 100
    assert zhihu["images_max"] is None  # 无图数上限


def test_profiles_f42_juejin_bilibili_frozen_table() -> None:
    """F42 §二-2.1 冻结表逐值断言（公开核实 2026-11-14，PLIMITS 唯一事实源）。"""
    juejin = get_limits("juejin")
    assert juejin["title_max_chars"] == 100  # 软限（编辑器容纳 ~100 字）
    assert (juejin["cover_w"], juejin["cover_h"]) == (1600, 900)  # 16:9 建议
    bilibili = get_limits("bilibili")
    assert bilibili["title_max_chars"] == 80
    assert (bilibili["cover_w"], bilibili["cover_h"]) == (1600, 1000)  # 16:10
    assert bilibili["cover_max_bytes"] == 5 * 1024 * 1024  # ≤5MB（pack 补 WARN）
    assert "cover_max_bytes" not in juejin  # 仅 bilibili 设大小上限


def test_registry_f42_juejin_bilibili_manual_pack() -> None:
    """F42 两新平台注册：route=manual_pack、capabilities 沿 zhihu 同款。"""
    for code in ("juejin", "bilibili"):
        adapter = get(code)
        assert adapter.route == "manual_pack"
        assert adapter.capabilities == get("zhihu").capabilities == {"text", "image"}
    manual_pack = {a.name for a in list_platforms() if a.route == "manual_pack"}
    assert manual_pack == {"xhs", "channels", "douyin", "zhihu", "juejin", "bilibili"}


def test_profiles_unknown_platform_readable_error() -> None:
    with pytest.raises(ValueError, match="未知平台: twitter（已知平台: bilibili/channels/douyin/juejin/mp/xhs/zhihu）"):
        get_limits("twitter")


# ---- WeChatAdapter.validate: 标题字节 ----


def test_validate_title_bytes_pass_and_fail(tmp_path: Path) -> None:
    adapter = WeChatAdapter()
    ok = adapter.validate(_article({"title": "短标题", "digest": "摘要"}), tmp_path / "a.md")
    assert ("PASS", "标题长度合规: 9 字节") in ok

    long = adapter.validate(_article({"title": "标" * 33}), tmp_path / "a.md")  # 99 字节 > 64
    fails = [msg for lvl, msg in long if lvl == "FAIL"]
    assert len(fails) == 1
    assert "标题超过 64 字节" in fails[0]
    assert "99 字节" in fails[0]


def test_validate_empty_title_fails(tmp_path: Path) -> None:
    results = WeChatAdapter().validate(_article({"title": ""}), tmp_path / "a.md")
    assert _levels(results)[0] == "FAIL"


def test_validate_digest_bytes(tmp_path: Path) -> None:
    adapter = WeChatAdapter()
    over = adapter.validate(_article({"title": "t", "digest": "摘" * 61}), tmp_path / "a.md")  # 183 字节
    assert any(lvl == "FAIL" and "摘要超过 120 字节" in msg for lvl, msg in over)

    empty = adapter.validate(_article({"title": "t"}), tmp_path / "a.md")
    assert ("WARN", "摘要为空，将自动取正文前 54 字") in empty


# ---- WeChatAdapter.validate: 封面比例 ----


def test_validate_cover_ratio_pass_and_warn(tmp_path: Path) -> None:
    adapter = WeChatAdapter()

    _make_image(tmp_path / "cover.png", 900, 383)  # 2.35:1 官方推荐
    good = adapter.validate(_article({"title": "t", "cover": "cover.png"}), tmp_path / "a.md")
    assert any(lvl == "PASS" and "2.35:1" in msg for lvl, msg in good)

    _make_image(tmp_path / "square.jpg", 600, 600)  # 1.0:1，低于下界 1.76
    bad = adapter.validate(_article({"title": "t", "cover": "square.jpg"}), tmp_path / "a.md")
    warns = [msg for lvl, msg in bad if lvl == "WARN" and "封面宽高比" in msg]
    assert len(warns) == 1
    assert "偏离推荐 2.35:1" in warns[0]


def test_validate_without_article_path_skips_ratio(tmp_path: Path) -> None:
    """article_path 缺省 → 封面比例项跳过（不崩溃、无新增级别）。"""
    _make_image(tmp_path / "cover.png", 600, 600)
    results = WeChatAdapter().validate(_article({"title": "t", "cover": "cover.png"}))
    assert all(lvl in ("PASS", "WARN") for lvl in _levels(results))
    assert not any("封面宽高比" in msg for _, msg in results)


def test_validate_cover_relative_to_article_dir(tmp_path: Path) -> None:
    """cover 相对路径以文章所在目录解析（与 run_checks/render_checks 一致）。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_image(sub / "assets" / "cover.png", 900, 383)
    results = WeChatAdapter().validate(
        _article({"title": "t", "cover": "assets/cover.png"}), sub / "a.md"
    )
    assert any(lvl == "PASS" and "2.35:1" in msg for lvl, msg in results)


# ---- WeChatAdapter.build_payload ----


def test_build_payload_structure_contains_title_html_digest() -> None:
    adapter = WeChatAdapter()
    article = _article(
        {"title": "载荷测试", "digest": "固定摘要", "author": "作者甲", "cover": "cover.png"},
        blocks=[{"type": "paragraph", "text": "第一段正文"}],
    )
    payload = adapter.build_payload(article)
    assert {"title", "html", "digest"} <= set(payload)
    assert payload["title"] == "载荷测试"
    assert payload["digest"] == "固定摘要"
    assert payload["author"] == "作者甲"
    assert payload["cover"] == "cover.png"
    assert payload["html"].startswith("<!DOCTYPE html>")
    assert "第一段正文" in payload["html"]


def test_build_payload_digest_fallback_first_54_chars() -> None:
    """digest 缺省 → 渲染 HTML 去标签后取前 54 字（与 publish 命令同口径，
    文档 head 的 <title> 文本一并计入——cli.publish 即如此）。"""
    article = _article({"title": "甲"}, blocks=[{"type": "paragraph", "text": "字" * 80}])
    payload = WeChatAdapter().build_payload(article)
    assert payload["digest"] == "甲" + "字" * 53


# ---- WeChatAdapter API 三件套（wrapper 编排，monkeypatch 全部外呼）----


class _FakeResp:
    """替身 requests.Response，只实现 publisher 用到的 .json()。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_create_draft_wraps_token_uploader_publisher(tmp_path: Path, monkeypatch) -> None:
    """编排链: token → 封面上传 → 正文图上传并替换 src → publisher.create_draft + 日志。

    真实解析形态：parse_article 把行内图片嵌进段落文本、不产 image 块——
    publish_flow 从渲染 HTML 收集 <img src>（真机② P0 修复的回归锁定）。
    """
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("musespiral.platforms.wechat.get_access_token", lambda account=None: "TOK")
    monkeypatch.setattr("musespiral.infra.wechat.publisher.requests.post", lambda *a, **k: _FakeResp({"media_id": "MEDIA9"}))

    uploaded: list[str] = []

    class _FakeUploader:
        def __init__(self, token, path) -> None:
            pass

        def upload_cover(self, src: str) -> str:
            return "THUMB1"

        def upload_content_image(self, src: str) -> str:
            uploaded.append(src)
            return "https://mmbiz.qpic.cn/fake.png"

    monkeypatch.setattr("musespiral.platforms.wechat.ImageUploader", _FakeUploader)

    article_path = tmp_path / "a.md"
    article_path.write_text(
        "---\ntitle: 适配器冒烟\ndigest: 摘要\ncover: cover.png\n---\n\n"
        "![配图](fig.png)\n\n段落。\n",
        encoding="utf-8",
    )
    parsed = parse_article(article_path)

    adapter = get("mp")
    media_id = adapter.create_draft(parsed, article_path)
    assert media_id == "MEDIA9"
    assert uploaded == ["fig.png"]  # 行内图经渲染 HTML 收集上传（真机② P0 修复回归）

    # publisher 真实路径写了日志（HTML 替换发生在内存，不落盘）
    log = tmp_path / "publish_log.jsonl"
    records = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "draft"
    assert r["title"] == "适配器冒烟"
    assert r["media_id"] == "MEDIA9"
    assert r["account"] == "default"


def test_create_draft_mmbiz_images_skipped(tmp_path: Path, monkeypatch) -> None:
    """已是 mmbiz 图床地址的行内图不再上传（重发/二创场景幂等跳过）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("musespiral.platforms.wechat.get_access_token", lambda account=None: "TOK")
    monkeypatch.setattr("musespiral.infra.wechat.publisher.requests.post", lambda *a, **k: _FakeResp({"media_id": "MEDIA7"}))

    uploaded: list[str] = []

    class _FakeUploader:
        def __init__(self, token, path) -> None:
            pass

        def upload_cover(self, src: str) -> str:
            return "THUMB1"

        def upload_content_image(self, src: str) -> str:
            uploaded.append(src)
            return "https://mmbiz.qpic.cn/fake.png"

    monkeypatch.setattr("musespiral.platforms.wechat.ImageUploader", _FakeUploader)

    article = tmp_path / "b.md"
    article.write_text(
        "---\ntitle: 行内图\ndigest: 摘要\ncover: cover.png\n---\n\n"
        "![已传](https://mmbiz.qpic.cn/already.png)\n\n段落。\n",
        encoding="utf-8",
    )
    parsed = parse_article(article)
    assert WeChatAdapter().create_draft(parsed, article) == "MEDIA7"
    assert uploaded == []  # mmbiz 前缀 → 跳过不上传


def test_create_draft_requires_cover(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="缺少封面图"):
        WeChatAdapter().create_draft(_article({"title": "t"}), tmp_path / "a.md")


def test_publish_and_status_wrap_publisher(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    monkeypatch.setattr("musespiral.platforms.wechat.get_access_token", lambda account=None: "TOK")

    def fake_post(url, **kwargs):
        if "freepublish/submit" in url:
            return _FakeResp({"errcode": 0, "publish_id": "PUB1"})
        if "freepublish/get" in url:
            return _FakeResp(
                {
                    "publish_status": 0,
                    "article_detail": {"item": [{"article_url": "https://mp.weixin.qq.com/s/pub1"}]},
                }
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("musespiral.infra.wechat.publisher.requests.post", fake_post)

    adapter = WeChatAdapter()
    publish_id = adapter.publish("MEDIA9", article="a.md", title="t")
    assert publish_id == "PUB1"

    result = adapter.status("PUB1")
    assert result["publish_status"] == 0
    assert result["article_detail"]["item"][0]["article_url"].startswith("https://mp.weixin.qq.com/")
