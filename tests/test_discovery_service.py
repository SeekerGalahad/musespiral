"""discover_service.py 集成测试——扫描→评分→建议→骨架→落盘→discovered.json 全链。

MUSESPIRAL_HOME monkeypatch 到 tmp（discovered.json 隔离）；知识库用 tmp_path
搭最小仿真树（含 模式家族词典.json）；输出目录一律显式传入（不触真实
~/musespiral-articles）。created 用 date.today() 动态生成——断言不随真实日期
漂移（新鲜度维恒命中）。
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from musespiral.services import discover_service as svc

GOOD_DOC = """---
title: 源码分析-FastDB
category: 13-source-analysis
created: {created}
tags: [源码分析, Python, 高性能-数据库, demo/fastdb]
---

# 源码分析：FastDB (demo/fastdb)

## 一、项目概述

一句话价值：快。

{body}

## 三、核心模块分析

### 3.1 引擎：列式压缩

引擎正文。

## 四、端到端数据流

流程正文。

## 五、设计思维提炼

### 5.1 关键权衡

1. **邮箱模型替代锁**
"""

WEAK_DOC = """---
title: 随笔
category: 09-cross-cutting
created: 2020-01-01
tags: [随笔]
---

# 随笔

短文。
"""


def _make_kb(tmp_path):
    """仿真知识库根：13-source-analysis 下两篇 + 模式家族词典.json。"""
    src = tmp_path / "ai-knowledge-base" / "13-source-analysis"
    src.mkdir(parents=True)
    good_body = "\n".join(f"第 {i} 行" for i in range(200))  # 撑 ≥200 行深度档
    doc = GOOD_DOC.format(body=good_body, created=date.today().isoformat())
    (src / "源码分析-FastDB.md").write_text(doc, encoding="utf-8")
    (src / "随笔.md").write_text(WEAK_DOC, encoding="utf-8")
    (src / "模式家族词典.json").write_text(
        json.dumps({"version": 1, "families": {"邮箱模型": {"aliases": ["邮箱模型替代锁"]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return src


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))
    src = _make_kb(tmp_path)
    return {
        "src": src,
        "doc": (src / "源码分析-FastDB.md").resolve(),
        "out": tmp_path / "articles",
        "kb": tmp_path / "ai-knowledge-base",
    }


# ── scan_candidates ───────────────────────────────────────────────────


def test_scan_contract_shape_and_sort(env):
    cands = svc.scan_candidates(source=env["src"], limit=10, min_score=0)
    assert [c["title"] for c in cands] == ["源码分析-FastDB", "随笔"]  # 评分降序
    for c in cands:
        assert set(c) == {
            "path", "title", "score", "score_breakdown", "series", "series_no",
            "reason", "skeleton_hint", "drafted",
        }
        assert set(c["score_breakdown"]) == {"depth", "uniqueness", "readability", "adaptability"}
        assert c["score"] == sum(c["score_breakdown"].values())
        assert c["drafted"] is False


def test_scan_good_doc_full_marks_with_family_dict(env):
    cands = svc.scan_candidates(source=env["src"], limit=1, min_score=0)
    fastdb = cands[0]
    # 200+行(20)+四问章节(10)+端到端(10) | owner/repo(15)+家族成员(15) |
    # tags4(10)+30天内(10) | 源码分析(10) = 100
    assert fastdb["score"] == 100
    assert fastdb["score_breakdown"] == {
        "depth": 40, "uniqueness": 30, "readability": 20, "adaptability": 10,
    }
    assert fastdb["series"] == "开源项目深度解读"
    assert fastdb["series_no"] == 1
    assert "邮箱模型替代锁" in fastdb["reason"] or "demo/fastdb" in fastdb["reason"]
    assert fastdb["skeleton_hint"].startswith("为什么 FastDB 这么")
    assert fastdb["drafted"] is False


def test_scan_min_score_and_limit(env):
    only_high = svc.scan_candidates(source=env["src"], min_score=100)
    assert [c["title"] for c in only_high] == ["源码分析-FastDB"]
    assert svc.scan_candidates(source=env["src"], limit=0, min_score=0) == []


def test_scan_missing_dict_file_no_crash(env):
    (env["src"] / "模式家族词典.json").unlink()
    cands = svc.scan_candidates(source=env["src"], min_score=0)
    fastdb = next(c for c in cands if c["title"] == "源码分析-FastDB")
    # 词典缺失 → families=None 不报错，家族维按 0 分（85 = 100-15）
    assert fastdb["score"] == 85
    assert fastdb["score_breakdown"]["uniqueness"] == 15


def test_scan_source_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        svc.scan_candidates(source=tmp_path / "nope")


# ── draft_from_kb：全链 + O_EXCL + discovered.json ────────────────────


def test_draft_full_chain(env):
    doc = env["doc"]
    out = svc.draft_from_kb(doc, output_dir=env["out"])
    assert out.exists() and out.parent == env["out"] and out.suffix == ".md"

    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f"source: {doc}" in text  # 溯源（tmp 不在 cwd 下 → 绝对路径）
    assert "series: 开源项目深度解读" in text
    assert "<!-- 源文档 §" in text  # 章节来源注释
    assert "## 参考资料" in text

    # discovered.json：jsonl 落一条记录，字段齐
    records = [json.loads(ln) for ln in svc.discovered_path().read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "discover_draft"
    assert r["path"] == str(doc)
    assert r["output"] == str(out)
    assert r["series"] == "开源项目深度解读"
    assert r["title"] == "源码分析-FastDB"


def test_draft_rejects_existing_file(env):
    doc = env["doc"]
    svc.draft_from_kb(doc, output_dir=env["out"])
    with pytest.raises(FileExistsError):
        svc.draft_from_kb(doc, output_dir=env["out"])  # O_EXCL：同名骨架拒绝
    # 第二次失败不追加记录
    records = svc._load_discovered()
    assert len(records) == 1


def test_drafted_filtered_after_draft(env):
    doc = env["doc"]
    svc.draft_from_kb(doc, output_dir=env["out"])

    # 默认过滤：FastDB 消失（drafted），只剩随笔
    titles = [c["title"] for c in svc.scan_candidates(source=env["src"], min_score=0)]
    assert "源码分析-FastDB" not in titles
    assert "随笔" in titles

    # include_drafted=True：保留并打标
    cands = svc.scan_candidates(source=env["src"], min_score=0, include_drafted=True)
    fastdb = next(c for c in cands if c["title"] == "源码分析-FastDB")
    assert fastdb["drafted"] is True
    # series_no 从 discovered 计数续号
    assert fastdb["series_no"] == 2


def test_draft_series_override(env):
    doc = env["doc"]
    out = svc.draft_from_kb(doc, series="我的私享系列", output_dir=env["out"])
    assert "series: 我的私享系列" in out.read_text(encoding="utf-8")


def test_draft_rejects_non_markdown(env):
    p = env["src"] / "notes.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        svc.draft_from_kb(p, output_dir=env["out"])


def test_draft_missing_doc(env):
    with pytest.raises(FileNotFoundError):
        svc.draft_from_kb(env["src"] / "不存在.md", output_dir=env["out"])


# ── 词典解析（load_family_titles）─────────────────────────────────────


def test_load_family_titles_relative_to_kb_root(env):
    titles = svc.load_family_titles(env["src"])
    assert titles is not None
    assert "邮箱模型" in titles and "邮箱模型替代锁" in titles  # 族名+别名都进集合


def test_load_family_titles_none_outside_kb(tmp_path):
    assert svc.load_family_titles(tmp_path) is None  # 非知识库目录
    empty = tmp_path / "ai-knowledge-base" / "13-source-analysis"
    empty.mkdir(parents=True)
    assert svc.load_family_titles(empty) is None  # 无词典文件


def test_kb_root_registered_config_takes_priority(tmp_path, monkeypatch):
    # 双轨统一点：显式登记（kb_root.txt）优先——登记根不必叫 ai-knowledge-base
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True)
    kb = tmp_path / "my-kb"  # 非约定目录名：仅登记可发现
    src = kb / "13-source-analysis"
    src.mkdir(parents=True)
    (src / "模式家族词典.json").write_text(
        json.dumps({"families": {"登记族": {}}}, ensure_ascii=False), encoding="utf-8"
    )
    (home / "kb_root.txt").write_text(str(kb), encoding="utf-8")
    assert svc.load_family_titles(src) == {"登记族"}  # 走登记根；目录名回退本例找不到


def test_kb_root_registered_overrides_nested_walk_up(tmp_path, monkeypatch):
    # 登记根与向上回退都命中时，登记根权威（嵌套同名目录不劫持词典）
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir(parents=True)
    outer = tmp_path / "ai-knowledge-base"
    outer_dict = outer / "13-source-analysis"
    outer_dict.mkdir(parents=True)
    (outer_dict / "模式家族词典.json").write_text(
        json.dumps({"families": {"外层族": {}}}, ensure_ascii=False), encoding="utf-8"
    )
    inner = outer_dict / "ai-knowledge-base" / "13-source-analysis"
    inner.mkdir(parents=True)
    (inner / "模式家族词典.json").write_text(
        json.dumps({"families": {"内层族": {}}}, ensure_ascii=False), encoding="utf-8"
    )
    (home / "kb_root.txt").write_text(str(outer), encoding="utf-8")
    # 回退会命中更近的内层同名目录，登记根优先 → 外层词典胜出
    assert svc.load_family_titles(inner) == {"外层族"}
