"""素材库服务测试（第二十期 §五第 5 行）：读写/查询/分诊/删除连坐/迁移。

覆盖：add 幂等与 immutable 拒改/合并读序（同 id 末次胜出）/分诊窄门/
删除墓碑+连坐清单/详情「素材已删」徽章/查询过滤/drafted 推导面（迁移
件三）/P19 星标迁移幂等（迁移件二——重复执行零重复行）。MUSESPIRAL_HOME
monkeypatch 到 tmp 隔离（materials.jsonl 与 discover_stars.json 同隔离）。
"""
from __future__ import annotations

import json

import pytest

from musespiral.core.materials import model
from musespiral.services import materials_service as svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _material(env=None, **overrides):
    base = {
        "mtype": "text",
        "source_id": "kb",
        "uri": "13-source-analysis/a.md",
        "title": "甲",
        "payload": {"local_path": "13-source-analysis/a.md"},
        "fetched_at": "2026-11-15T10:00:00",
    }
    base.update(overrides)
    return model.make_material(**base)


def _lines(env):
    path = env / "materials" / "materials.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── 落库与幂等（immutable 红线）────────────────────────────────────


def test_add_and_list_sorted(env) -> None:
    old = _material(uri="old.md", fetched_at="2026-11-01T00:00:00")
    new = _material(uri="new.md", fetched_at="2026-11-15T00:00:00")
    svc.add_material(old)
    svc.add_material(new)
    listed = svc.list_materials()
    assert [m["origin"]["uri"] for m in listed] == ["new.md", "old.md"]  # fetched_at 降序
    assert (env / "materials" / "materials.jsonl").is_file()  # 父目录自建


def test_add_idempotent_zero_append(env) -> None:
    row = _material()
    first = svc.add_material(row)
    assert len(_lines(env)) == 1
    again = svc.add_material(dict(row))  # 等值重采（拷贝）
    assert again == first
    assert len(_lines(env)) == 1  # 零追加


def test_add_conflicting_rewrite_rejected(env) -> None:
    row = _material()
    svc.add_material(row)
    conflict = {**row, "payload": {"text": "被改写的正文"}}
    with pytest.raises(ValueError, match="immutable"):
        svc.add_material(conflict)
    assert len(_lines(env)) == 1


def test_add_invalid_row_rejected(env) -> None:
    bad = _material()
    bad["type"] = "rss"
    with pytest.raises(ValueError, match="不合法"):
        svc.add_material(bad)
    assert _lines(env) == []


def test_add_revives_tombstoned_id(env) -> None:
    row = _material()
    svc.add_material(row)
    svc.delete_material(row["id"])
    assert svc.get_material(row["id"]) is None
    svc.add_material(row)  # 删除可逆：同 id 再落即复活（末次胜出）
    assert svc.get_material(row["id"]) == row


# ── 分诊（三键窄门 + 追加合并）─────────────────────────────────────


def test_update_triage_appends_and_last_wins(env) -> None:
    row = _material()
    svc.add_material(row)
    updated = svc.update_triage(row["id"], {"starred": True, "read": True})
    assert updated["triage"] == {"read": True, "starred": True, "enabled": True, "tags": [],
                                 "verified": False, "confidence": None}  # §1.2 四键修订 + N-4 两键
    assert len(_lines(env)) == 2  # 追加式：旧行保留、新行末次胜出
    fetched = svc.get_material(row["id"])
    assert fetched["triage"]["starred"] is True
    # payload 逐字节不动（immutable）
    assert fetched["payload"] == row["payload"]
    assert fetched["fetched_at"] == row["fetched_at"]


def test_update_triage_idempotent_zero_append(env) -> None:
    row = _material()
    svc.add_material(row)
    svc.update_triage(row["id"], {"starred": True})
    assert len(_lines(env)) == 2
    svc.update_triage(row["id"], {"starred": True})  # 同值再改
    assert len(_lines(env)) == 2  # 零追加
    with pytest.raises(ValueError, match="五键"):  # §1.2 修订三键→四键；N-4 四键→五键（+verified）
        svc.update_triage(row["id"], {"payload": {}})
    with pytest.raises(ValueError, match="不存在"):
        svc.update_triage("nope00000000", {"starred": True})


# ── 删除：墓碑 + 连坐清单 ──────────────────────────────────────────


def test_delete_cascade_and_tombstone(env) -> None:
    origin = _material()
    derived = _material(
        mtype="derived", uri="derived:1",
        provenance={"chain": [{"from_id": origin["id"], "op": "summarize"}], "derived_from": []},
    )
    bystander = _material(uri="other.md")
    for m in (origin, derived, bystander):
        svc.add_material(m)
    result = svc.delete_material(origin["id"])
    assert result["deleted"] == origin["id"]
    assert [c["id"] for c in result["cascade"]] == [derived["id"]]  # 连坐声明当场返回
    assert svc.get_material(origin["id"]) is None
    assert origin["id"] in svc.deleted_ids()
    assert svc.get_material(derived["id"]) is not None  # 引用者不级联删除
    # 重复删除拒绝（墓碑在册）
    with pytest.raises(ValueError, match="已删"):
        svc.delete_material(origin["id"])
    # 不存在的 id
    with pytest.raises(ValueError, match="不存在"):
        svc.delete_material("nope00000000")


def test_get_detail_deleted_ref_badge(env) -> None:
    """引用处显「素材已删」徽章——非静默失效（正典 §1.2 delete 连坐）。"""
    origin = _material()
    derived = _material(
        mtype="derived", uri="derived:1",
        provenance={"chain": [{"from_id": origin["id"], "op": "summarize"}], "derived_from": []},
    )
    svc.add_material(origin)
    svc.add_material(derived)
    detail = svc.get_detail(derived["id"])
    assert detail is not None
    assert detail["references"] == [{"from_id": origin["id"], "op": "summarize", "deleted": False}]
    svc.delete_material(origin["id"])
    detail = svc.get_detail(derived["id"])
    assert detail["references"][0]["deleted"] is True
    assert svc.get_detail("nope00000000") is None


# ── 查询过滤（分诊谓词 + 类型/源）──────────────────────────────────


def test_query_filters(env) -> None:
    a = _material(uri="a.md")
    b = _material(uri="b.md")
    svc.add_material(a)
    svc.add_material(b)
    svc.update_triage(b["id"], {"starred": True, "enabled": False})
    radar = _material(
        mtype="radar_signal", source_id="radarhub", uri="embedded:S1",
        fetched_at="2026-11-15T12:00:00",
    )
    svc.add_material(radar)
    assert [m["origin"]["uri"] for m in svc.query(starred=True)] == ["b.md"]
    assert [m["origin"]["uri"] for m in svc.query(enabled=True)] == [radar["origin"]["uri"], "a.md"]
    assert [m["origin"]["uri"] for m in svc.query(mtype="radar_signal")] == [radar["origin"]["uri"]]
    assert {m["origin"]["uri"] for m in svc.query(source_id="kb")} == {"a.md", "b.md"}
    assert svc.query(read=True) == []


def test_bad_rows_skipped_on_read(env) -> None:
    """坏行（非法 JSON / 非对象 / 无 id）跳过不崩读侧（宽容读纪律）。"""
    path = env / "materials" / "materials.jsonl"
    path.parent.mkdir(parents=True)
    row = _material()
    path.write_text(
        "{not json\n"
        + json.dumps(["a", "list"]) + "\n"
        + json.dumps({"type": "text"}) + "\n"
        + json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert [m["id"] for m in svc.list_materials()] == [row["id"]]


# ── 迁移件三：drafted 推导（discovered.json 读侧兼容）──────────────


def test_drafted_uris_and_is_drafted(env, tmp_path) -> None:
    articles = tmp_path / "articles"
    articles.mkdir()
    kb_root = tmp_path / "kb"
    (kb_root / "13-source-analysis").mkdir(parents=True)
    (kb_root / "13-source-analysis" / "a.md").write_text("x", encoding="utf-8")
    # 相对形态 + 绝对形态两种正稿（discover_service._source_label 双落点）
    (articles / "rel.md").write_text(
        "---\ntitle: 甲\nsource: 13-source-analysis/a.md\n---\n正文\n", encoding="utf-8",
    )
    absolute = (kb_root / "13-source-analysis" / "a.md").resolve()
    (articles / "abs.md").write_text(
        f"---\ntitle: 乙\nsource: {absolute}\n---\n正文\n", encoding="utf-8",
    )
    (articles / "nosrc.md").write_text("---\ntitle: 丙\n---\n无 source\n", encoding="utf-8")
    drafted = svc.drafted_uris(articles)
    assert drafted == {"13-source-analysis/a.md", str(absolute)}
    material = _material(uri="13-source-analysis/a.md")
    assert svc.is_drafted(material, drafted) is True  # 相对命中
    assert svc.is_drafted(material, drafted, kb_root=kb_root) is True  # 绝对形态命中
    other = _material(uri="13-source-analysis/b.md")
    assert svc.is_drafted(other, drafted, kb_root=kb_root) is False
    assert svc.drafted_uris(tmp_path / "nope") == set()  # 根缺失宽容


# ── 迁移件二：P19 星标 → triage.starred（幂等）─────────────────────


def test_migrate_p19_stars_creates_and_idempotent(env) -> None:
    (env / "discover_stars.json").write_text(
        json.dumps({"13-source-analysis/a.md": "2026-11-10T09:00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    first = svc.migrate_p19_stars()
    assert first == {"migrated": 1, "skipped": 0, "total": 1}
    mid = model.make_material_id("kb", "text", "13-source-analysis/a.md")
    material = svc.get_material(mid)
    assert material is not None
    assert material["triage"]["starred"] is True
    assert material["fetched_at"] == "2026-11-10T09:00:00"  # 收藏时序保留
    assert material["payload"] == {"local_path": "13-source-analysis/a.md"}  # 引用不复制
    assert len(_lines(env)) == 1
    # 二跑：语义已体现 → 零追加（迁移幂等=重复执行零重复行）
    second = svc.migrate_p19_stars()
    assert second == {"migrated": 0, "skipped": 1, "total": 1}
    assert len(_lines(env)) == 1
    # 旧文件保留（端点切换归 M2 收编）
    assert (env / "discover_stars.json").exists()


def test_migrate_p19_stars_updates_existing(env) -> None:
    """uri 已有素材未星标 → 只追加 triage 行；再跑零追加。"""
    row = _material()
    svc.add_material(row)
    (env / "discover_stars.json").write_text(
        json.dumps({row["origin"]["uri"]: "2026-11-11T08:00:00"}), encoding="utf-8",
    )
    first = svc.migrate_p19_stars()
    assert first["migrated"] == 1 and first["skipped"] == 0
    assert len(_lines(env)) == 2
    assert svc.get_material(row["id"])["triage"]["starred"] is True
    second = svc.migrate_p19_stars()
    assert second == {"migrated": 0, "skipped": 1, "total": 1}
    assert len(_lines(env)) == 2  # 幂等：零重复行


def test_migrate_p19_stars_missing_file(env) -> None:
    assert svc.migrate_p19_stars() == {"migrated": 0, "skipped": 0, "total": 0}
    assert svc.migrate_p19_stars() == {"migrated": 0, "skipped": 0, "total": 0}


# ── 批量落库（M2 采集提交点：失败不半写/墓碑不复活/批内去重）────────


def test_add_many_batch_dedupe_and_skip(env) -> None:
    """批内等值去重 + 库内等值跳过（幂等计数）；无冲突全量追加。"""
    a, b = _material(uri="a.md"), _material(uri="b.md")
    result = svc.add_many([a, dict(a), b])
    assert result == {"added": 2, "skipped": 1}  # 批内等值 1 去重
    again = svc.add_many([a, b])
    assert again == {"added": 0, "skipped": 2}  # 二跑全等值零追加
    assert len(_lines(env)) == 2


def test_add_many_conflict_is_zero_write(env) -> None:
    """库内同 id 异内容 → 追加第一行之前抛错，库内零残留（失败不半写）。"""
    seeded = _material(uri="a.md")
    svc.add_material(seeded)
    drifted = _material(uri="a.md", title="漂移版")
    fresh = _material(uri="b.md")
    before = len(_lines(env))
    with pytest.raises(ValueError, match="零写入"):
        svc.add_many([fresh, drifted])
    assert len(_lines(env)) == before  # fresh 也不落（整批零写入）


def test_add_many_invalid_row_zero_write(env) -> None:
    bad = model.make_material(mtype="nope", source_id="kb", uri="x", fetched_at="t")
    with pytest.raises(ValueError, match="零写入"):
        svc.add_many([_material(uri="a.md"), bad])
    assert _lines(env) == []


def test_add_many_tombstone_not_resurrected(env) -> None:
    """墓碑 id 不复活（采集语义；add_material 的复活留给人工/迁移路径）。"""
    row = _material(uri="a.md")
    svc.add_material(row)
    svc.delete_material(row["id"])
    result = svc.add_many([row])
    assert result == {"added": 0, "skipped": 1}
    assert svc.get_material(row["id"]) is None


def test_add_many_intra_batch_conflict_rejected(env) -> None:
    with pytest.raises(ValueError, match="批量内"):
        svc.add_many([_material(uri="a.md"), _material(uri="a.md", title="异版")])
    assert _lines(env) == []


# ── 发现页收编后端（M2：query_scored 评分现算 + drafted 推导）────────


def _kb_tree(tmp_path):
    kb = tmp_path / "kb"
    (kb / "13-source-analysis").mkdir(parents=True)
    (kb / "13-source-analysis" / "doc.md").write_text(
        "---\ntitle: 评分文档\n"
        "category: 13-source-analysis\ncreated: 2020-01-01\n"
        "tags: [源码分析, Python, 测试, o/r]\n"
        "---\n\n## 概述\n\n正文。\n\n## 设计思维提炼\n\n- 甲\n",
        encoding="utf-8",
    )
    return kb


def test_query_scored_kb_f27_and_radar_meta(env, tmp_path) -> None:
    """kb text 现算 F27 分（含 score_breakdown）；radar_signal 取 meta 分/级。"""
    from musespiral.services import sources_service as src_svc

    kb = _kb_tree(tmp_path)
    src_svc.register_source(
        {
            "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
            "config": {"root": str(kb), "sources": []},
            "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
        }
    )
    svc.add_material(
        model.make_material(
            mtype="text", source_id="kb", uri="13-source-analysis/doc.md", title="评分文档",
            payload={"local_path": "13-source-analysis/doc.md"}, fetched_at="2026-11-15T09:00:00",
        )
    )
    svc.add_material(
        model.make_material(
            mtype="radar_signal", source_id="radarhub", uri="https://github.com/a/b",
            title="a/b", payload={"url": "https://github.com/a/b",
                                  "meta": {"line": "embedded", "grade": "S", "score": 88,
                                           "url": "https://github.com/a/b"}},
            fetched_at="2026-11-15T10:00:00",
        )
    )
    articles = tmp_path / "articles"
    articles.mkdir()
    out = svc.query_scored(articles_root=articles, kb_root=kb)
    items = out["items"]
    assert out["total"] == 2
    radar = next(m for m in items if m["type"] == "radar_signal")
    kbm = next(m for m in items if m["type"] == "text")
    assert radar["score"] == 88 and radar["grade"] == "S"  # radar 分/级取 meta
    assert "score_breakdown" not in radar
    # kb 类 F27 现算：depth(0+10 章节)+uniqueness(15)+readability(tags≥4=10)+adaptability(10)=45
    assert kbm["score"] == 45 and kbm["score_breakdown"] == {
        "depth": 10, "uniqueness": 15, "readability": 10, "adaptability": 10,
    }
    assert items[0]["id"] == radar["id"]  # 88 > 45：score 降序


def test_query_scored_drafted_and_sort_limit(env, tmp_path) -> None:
    """drafted=正稿 source 指回；无分素材沉底；limit 截断。"""
    articles = tmp_path / "articles"
    articles.mkdir()
    (articles / "draft.md").write_text(
        "---\ntitle: x\nsource: 13-source-analysis/gone.md\n---\n\n正文\n", encoding="utf-8"
    )
    svc.add_material(_material(uri="13-source-analysis/gone.md", title="已消化"))
    svc.add_material(_material(uri="13-source-analysis/missing.md", fetched_at="2026-11-15T12:00:00"))
    out = svc.query_scored(articles_root=articles, kb_root=tmp_path / "nonexistent-kb")
    items = out["items"]
    by_uri = {m["origin"]["uri"]: m for m in items}
    assert by_uri["13-source-analysis/gone.md"]["drafted"] is True  # 相对形态命中
    assert by_uri["13-source-analysis/missing.md"]["drafted"] is False
    # kb_root 不存在：kb 素材无分（None 沉底）
    assert by_uri["13-source-analysis/missing.md"]["score"] is None
    assert items[-1]["origin"]["uri"] in {"13-source-analysis/gone.md", "13-source-analysis/missing.md"}
    limited = svc.query_scored(articles_root=articles, kb_root=None, limit=1)
    # 第二十一期 F64 契约修订：total=命中总数，不随 limit 截断缩水（截断可见性）
    assert len(limited["items"]) == 1 and limited["total"] == 2


def test_query_scored_filters(env, tmp_path) -> None:
    articles = tmp_path / "articles"
    articles.mkdir()
    a = _material(uri="a.md")
    b = _material(uri="b.md")
    svc.add_material(a)
    svc.add_material(b)
    svc.update_triage(b["id"], {"starred": True})
    out = svc.query_scored(articles_root=articles, kb_root=None, starred=True)
    assert [m["id"] for m in out["items"]] == [b["id"]]
    out2 = svc.query_scored(articles_root=articles, kb_root=None, mtype="text", source_id="kb")
    assert len(out2["items"]) == 2


# ── 第二十一期 Phase A：单元组（T59-5 media_store / T61-4 域名表 /
#    T63-1 tags 规范化 / T64-1 搜索语法）─────────────────────────────

import io as _io

from musespiral.core.materials import search as mat_search
from musespiral.core.materials import tags as mat_tags
from musespiral.infra import media_store
from PIL import Image


def _png_bytes(size=(60, 30), color=(120, 40, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_t59_5_thumbnail_and_atomic_write(env, monkeypatch) -> None:
    """T59-5：thumb 长边 ≤480 / WebP（RIFF 魔数）/ 写入中途抛错无 .tmp 残留。"""
    big = _png_bytes(size=(600, 400))
    info = media_store.inspect_image(big)
    assert info["width"] == 600 and info["height"] == 400 and info["format"] == "PNG"
    mid = "aaaaaaaaaaaa"
    media_store.store_original(mid, info["ext"], big)
    thumb = media_store.make_thumbnail(mid, big)
    files = env / "materials" / "files"
    assert (files / f"{mid}.png").is_file() and thumb.is_file()
    body = thumb.read_bytes()
    assert body[:4] == b"RIFF"  # WebP 容器魔数
    with Image.open(_io.BytesIO(body)) as img:
        assert img.format == "WEBP"
        assert max(img.size) <= 480
        assert img.size == (480, 320)  # 600x400 保纵横比 → 长边恰 480
    # 中途抛错（rename 段）→ 临时文件清理，files/ 无残留
    def _boom(*_a, **_k):
        raise OSError("disk on fire")

    monkeypatch.setattr(media_store.os, "replace", _boom)
    with pytest.raises(OSError):
        media_store.store_original("bbbbbbbbbbbb", "png", big)
    leftovers = [p.name for p in files.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert not (files / "bbbbbbbbbbbb.png").exists()


def test_t59_5_sniff_and_size_gate(env) -> None:
    """魔数白名单四格式各过 + 文本/svg/超限各拒（inspect_image 提交期链）。"""
    img = Image.new("RGB", (8, 8))
    for fmt, ext in (("PNG", "png"), ("JPEG", "jpeg"), ("GIF", "gif"), ("WEBP", "webp")):
        buf = _io.BytesIO()
        img.save(buf, fmt)
        assert media_store.sniff_format(buf.getvalue()) == ext
        info = media_store.inspect_image(buf.getvalue())
        assert info["ext"] == ext and info["format"] == fmt.upper()
    with pytest.raises(ValueError, match="格式"):
        media_store.inspect_image(b"plain text pretending to be png" * 8)
    with pytest.raises(ValueError, match="格式"):
        media_store.inspect_image(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
    with pytest.raises(ValueError, match="8MiB"):
        media_store.inspect_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024 * 1024))
    assert media_store.sniff_format(b"\x00" * 16) == ""


@pytest.mark.parametrize(
    "host,platform",
    [
        ("www.bilibili.com", "bilibili"),
        ("b23.tv", "bilibili"),
        ("m.youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("vimeo.com", "other"),
        ("example.com", "other"),
    ],
)
def test_t61_4_video_platform_domain_table(host: str, platform: str) -> None:
    """T61-4：域名表参数化（后缀匹配；host 大小写归一由 urlsplit 保证）。"""
    from musespiral.services.materials_service import _video_platform

    assert _video_platform(host) == platform


def test_t61_4_url_normalization_host_lowercase(env) -> None:
    """host 归一小写 + path 原样 + query 保留（幂等锚 uri 形态，§1.4）。"""
    from musespiral.services.materials_service import _normalize_video_url

    uri, normalized = _normalize_video_url("HTTPS://WWW.BILIBILI.COM/video/BV1x?t=42")
    assert uri == "video://https://www.bilibili.com/video/BV1x?t=42"
    assert normalized == "https://www.bilibili.com/video/BV1x?t=42"
    uri2, _ = _normalize_video_url("https://www.bilibili.com/video/BV1x/")
    assert uri2 == "video://https://www.bilibili.com/video/BV1x"  # 去末尾斜杠幂等收敛
    # query 原样保留（YT t= 在 query，斜杠属 query 字符不裁剪）
    uri3, _ = _normalize_video_url("https://youtu.be/x?t=42/")
    assert uri3 == "video://https://youtu.be/x?t=42/"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("封面 素材", "封面-素材"),
        ("ABC", "abc"),
        ("a//b", "a-b"),
        ("a--b", "a-b"),
        ("-x-", "x"),
        ("已规范名", "已规范名"),  # 幂等：已规范名再过不变
        ("封面 素材/案例", "封面-素材-案例"),  # 非法字符（斜杠）转连字符
    ],
)
def test_t63_1_normalize_tag(raw: str, expected: str) -> None:
    assert mat_tags.normalize_tag(raw) == expected
    assert mat_tags.normalize_tag(mat_tags.normalize_tag(raw)) == expected  # 幂等


def test_t63_1_tag_limits() -> None:
    with pytest.raises(ValueError):
        mat_tags.normalize_tags(["x" * 25])  # 单标签 >24 字符
    with pytest.raises(ValueError):
        mat_tags.normalize_tags([f"t{i}" for i in range(13)])  # 13 个超上限
    with pytest.raises(ValueError):
        mat_tags.normalize_tags("a")  # 非 list
    with pytest.raises(ValueError):
        mat_tags.normalize_tags([1])  # 元素非 str
    assert mat_tags.normalize_tags([f"t{i}" for i in range(12)]) == [f"t{i}" for i in range(12)]
    assert mat_tags.normalize_tags(["a", "A", "a "]) == ["a"]  # 去重保序（归一后）


def _m(title: str = "", text: str = "", url: str = "", tags=None, meta=None, **kw):
    row = model.make_material(
        mtype=kw.pop("mtype", "text"),
        source_id=kw.pop("source_id", "kb"),
        uri=kw.pop("uri", "x.md"),
        title=title,
        payload={"text": text, "url": url, **({"meta": meta} if meta else {})},
        fetched_at="2026-11-16T10:00:00",
        **kw,
    )
    if tags is not None:
        row["triage"]["tags"] = tags
    return row


@pytest.mark.parametrize(
    "q,title,text,tags,expected",
    [
        ("foo bar", "foo", "", None, False),  # 双词 AND：单词命中不中
        ("foo bar", "foo bar", "", None, True),
        ("\"exact phrase\"", "an Exact PHRASE here", "", None, True),  # 短语子串+大小写
        ("pred*", "predicate logic", "", None, True),  # 前缀
        ("pred*", "prepare", "", None, False),  # 前缀不中
        ("FOO", "foo", "", None, True),  # 大小写不敏感
        ("", "anything", "whatever", None, True),  # 空 q 恒真
        ('"foo bar', "foo bar tail", "", None, True),  # 未闭合引号字面处理
        ("embedded", "", "", None, False),
    ],
)
def test_t64_1_search_syntax(q, title, text, tags, expected) -> None:
    assert mat_search.matches_query(_m(title=title, text=text, tags=tags), q) is expected


def test_t64_1_search_meta_flatten() -> None:
    radar = _m(title="信号", mtype="radar_signal", meta={"line": "embedded", "grade": "S", "score": 88})
    assert mat_search.matches_query(radar, "embedded")
    assert mat_search.matches_query(radar, "line:embedded")
    assert mat_search.matches_query(radar, "grade:S")
    assert mat_search.matches_query(radar, "信号")  # title 域
    assert not mat_search.matches_query(radar, "grade:A")
    video = _m(title="片头参考", mtype="video", url="https://www.bilibili.com/video/BV1", meta={"platform": "bilibili"})
    assert mat_search.matches_query(video, "platform:bilibili")
    assert mat_search.matches_query(video, "bilibili.com")  # payload.url 域
    tagged = _m(title="图", tags=["封面-素材"])
    assert mat_search.matches_query(tagged, "封面-素材")  # triage.tags 域
