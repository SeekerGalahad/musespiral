"""compose_service 编排与 CLI headless 全链（第十八期 §2.8 会话/编排/合并/归因）。

覆盖：生命周期 new→adopt / 素材预算截断进 prompt / 轮次窗口 K / prompt
插槽命中与模板覆盖级联 / decisions（含 discard 方向）进下轮 prompt /
adopt 产正稿入队列 + habits learn 触发 + source/source_extra 归因 / F43
版本池 / 会话目录不污染文章队列（scan 排除）/ CLI 六命令全链（mock chat，
不起服务器不发网络）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.core.habits import load_habits
from musespiral.services import compose_service

# AI 首轮回复（协议定界：三块——标题/正文（书面词收尾，供 habits 差分）/结尾）
REPLY_R1 = (
    "<<<block kind:heading>>># 缓存设计实录\n\n"
    "<<<block kind:para>>>三种缓存解法的对比与取舍，综上所述空值缓存最稳妥。\n\n"
    "<<<block kind:para>>>结尾段，推荐从布隆过滤器起步。"
)
# AI 二轮回复：锚继承 b2（改写）+ 新块（吸收 discard 方向的改写）
REPLY_R2 = (
    "<<<block id:b2 kind:para>>>三种缓存解法的对比与取舍，总的来说空值缓存最稳妥。\n\n"
    "<<<block kind:para>>>按方向备注改写后的新结尾，先讲实践再给结论。"
)

SOURCE_MD = """---
title: 素材：缓存三解法
---

## 一、概述

概述正文段落，讲缓存问题的背景与动机。

## 二、架构

架构正文段落，讲三种解法的结构差异。

## 三、取舍

取舍正文段落，讲各自适用场景。
"""


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


@pytest.fixture
def source(tmp_path: Path) -> Path:
    p = tmp_path / "kb" / "cache.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text(SOURCE_MD, encoding="utf-8")
    return p


class _FakeChat:
    """按序回放的假 chat（捕获 messages 供 prompt 断言）。"""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, *, task=None, **kw):
        assert task == "compose"  # TASKS+compose：任务路由白名单键
        self.calls.append(messages)
        return self.replies.pop(0)


def _fake(monkeypatch: pytest.MonkeyPatch, *replies: str) -> _FakeChat:
    fake = _FakeChat(*replies)
    monkeypatch.setattr(compose_service, "_chat", fake)
    return fake


# ── 生命周期与落盘形态（§2.1）────────────────────────────────────────


def test_new_session_layout_and_guards(root: Path) -> None:
    d = compose_service.new_session("demo", "示例标题", root=root)
    assert d == root / "demo.compose"
    assert (d / "session.json").is_file() and (d / "draft.md").is_file()
    meta = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert meta["status"] == "composing" and meta["title"] == "示例标题"
    with pytest.raises(compose_service.ComposeError, match="已存在"):
        compose_service.new_session("demo", "再建", root=root)
    with pytest.raises(compose_service.ComposeError, match="非法 slug"):
        compose_service.new_session("../evil", "穿越", root=root)


def test_add_source_section_validation_and_listing(root: Path, source: Path) -> None:
    compose_service.new_session("demo", "T", root=root)
    known = compose_service.list_source_sections(source)
    assert known == ["一、概述", "二、架构", "三、取舍"]  # 复用 discovery scanner
    info = compose_service.add_source("demo", source, ["一、概述", "三、取舍"], root=root)
    assert info["sections"] == ["一、概述", "三、取舍"]
    detail = compose_service.session_detail("demo", root=root)
    assert detail["sources"][0]["sections"] == ["一、概述", "三、取舍"]
    with pytest.raises(compose_service.ComposeError, match="章节不存在"):
        compose_service.add_source("demo", source, ["不存在的章"], root=root)
    # 重复添加同路径=更新勾选（不重复占位）
    compose_service.add_source("demo", source, ["二、架构"], root=root)
    assert len(compose_service.session_detail("demo", root=root)["sources"]) == 1
    with pytest.raises(FileNotFoundError):
        compose_service.list_source_sections(root / "nope.md")


# ── remove_source：素材撤池（F-C 2026-11-18）────────────────────────


def test_remove_source_deletes_entry_and_returns_sections(root: Path, source: Path) -> None:
    """删条目+session.json 原子重写+返回被移除源勾选章节（连坐文案数据面）。"""
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, ["一、概述"], root=root)
    other = source.with_name("other.md")
    other.write_text("## 协作素材\n\n协作者观点。", encoding="utf-8")
    compose_service.add_source("demo", other, ["协作素材"], root=root)

    result = compose_service.remove_source("demo", source, root=root)
    assert result == {
        "path": str(source.resolve()),
        "sections": ["一、概述"],
        "sources_left": 1,
    }
    detail = compose_service.session_detail("demo", root=root)
    assert [s["path"] for s in detail["sources"]] == [str(other)]  # 仅剩 other
    # session.json 是唯一落盘面（无 .tmp 残留——原子写 tmp+os.replace）
    meta_path = root / "demo.compose" / "session.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert [s["path"] for s in meta["sources"]] == [str(other)]
    assert not list(meta_path.parent.glob("*.tmp"))
    # 撤池后可重新加入（池行恢复语义；add 追加在尾）
    compose_service.add_source("demo", source, ["三、取舍"], root=root)
    detail = compose_service.session_detail("demo", root=root)
    assert detail["sources"][-1]["sections"] == ["三、取舍"]


def test_remove_source_relative_path_resolves_and_missing_source_readable(
    root: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径同口径解析（与 add 一致）；源不在池→可读 ComposeError 含池内清单。"""
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, None, root=root)
    # 相对路径撤池：monkeypatch cwd 到素材父目录（compose_service 内 Path(path).expanduser()）
    monkeypatch.chdir(source.parent)
    result = compose_service.remove_source("demo", "cache.md", root=root)
    assert result["path"] == str(source.resolve()) and result["sources_left"] == 0
    with pytest.raises(compose_service.ComposeError) as ei:
        compose_service.remove_source("demo", str(source), root=root)
    msg = str(ei.value)
    assert "素材不在池中" in msg and "cache.md" in msg  # 可读：点名素材名
    with pytest.raises(compose_service.ComposeError, match="会话不存在"):
        compose_service.remove_source("ghost", str(source), root=root)


def test_remove_source_refused_when_sealed(root: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """封版会话撤池拒绝（同 add_source 归档快照语义）：adopt 后池已封存。"""
    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, ["一、概述"], root=root)
    compose_service.generate("demo", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.adopt("demo", root=root)
    with pytest.raises(compose_service.ComposeError, match="素材池已封存"):
        compose_service.remove_source("demo", source, root=root)


# ── generate：prompt 管线 + 轮次落盘（§2.3）─────────────────────────


def test_generate_round1_pipeline_and_files(root: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, ["一、概述"], root=root)

    result = compose_service.generate("demo", root=root)
    assert result["round"] == 1
    ids = [b["id"] for b in result["blocks"]]
    kinds = [b["kind"] for b in result["blocks"]]
    assert ids == ["b1", "b2", "b3"] and kinds == ["heading", "para", "para"]
    d = root / "demo.compose"
    assert (d / "rounds" / "1.md").read_text(encoding="utf-8") == REPLY_R1  # 原始产出存证
    assert json.loads((d / "rounds" / "1.decisions.json").read_text(encoding="utf-8")) == []

    prompt = fake.calls[0][0]["content"]
    assert "<<<block" in prompt and "输出协议" in prompt  # 任务底座模板含协议指令
    assert "概述正文段落" in prompt and "架构正文段落" not in prompt  # 勾选章节进、未勾不进
    assert "（空——首轮创作）" in prompt  # 首轮空稿占位
    assert "（无——首轮或上轮未裁决）" in prompt


def test_generate_on_delta_takes_stream_path(root: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """on_delta 增量路径（E-4 流式骨架批）：走 _chat_stream 而非 _chat，
    回调原样接到增量入口；终态 result 与非流式完全同形（round/blocks/raw）。

    chat_fn 注入优先级更高（既有测试面不回归）；on_delta 与 chat_fn 都不给
    时行为与本批之前逐字节一致——那两条由既有 generate 测试群锚定。
    """
    seen: list[str] = []

    def fake_stream(messages, *, task=None, temperature=0.6, on_delta=None, **kw):
        assert task == "compose"
        pieces = REPLY_R1.split("\n\n")
        assert on_delta is not None
        for piece in pieces:
            on_delta(piece)
        return REPLY_R1

    def bomb(messages, **kw):  # on_delta 路径不得回落 _chat
        raise AssertionError("on_delta 路径不得走 _chat")

    monkeypatch.setattr(compose_service, "_chat_stream", fake_stream)
    monkeypatch.setattr(compose_service, "_chat", bomb)
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, ["一、概述"], root=root)

    result = compose_service.generate("demo", root=root, on_delta=seen.append)
    assert seen == REPLY_R1.split("\n\n"), "服务层应把 on_delta 原样接到增量入口"
    assert result["round"] == 1
    assert [b["id"] for b in result["blocks"]] == ["b1", "b2", "b3"]
    assert (root / "demo.compose" / "rounds" / "1.md").read_text(encoding="utf-8") == REPLY_R1


def test_blocks_so_far_display_view() -> None:
    """blocks_so_far（E-4）：部分文本尾块随增量收编（「尾块打字」观感）、
    前块定格稳定、空文本空列表；display 面 [{kind,text}] 无 id 无归并。"""
    assert compose_service.blocks_so_far("") == []
    p1 = "<<<block kind:heading>>># 缓存设计实录\n\n"
    got1 = compose_service.blocks_so_far(p1)
    assert got1 == [{"kind": "heading", "text": "# 缓存设计实录"}]
    # 尾块未闭合（增量中途）：照样收编，且随增量继续到达而变长
    p2 = p1 + "<<<block kind:para>>>三种缓存解法"
    got2 = compose_service.blocks_so_far(p2)
    assert got2[0] == got1[0], "已完整的前块必须定格"
    assert got2[1] == {"kind": "para", "text": "三种缓存解法"}
    got3 = compose_service.blocks_so_far(REPLY_R1)
    assert [b["kind"] for b in got3] == ["heading", "para", "para"]
    assert all("id" not in b for b in got3)


def test_generate_prompt_style_injection_seventh_task(
    root: Path, source: Path, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P17 style 块注入 compose（第七注入任务）：habits+范文现读现拼。"""
    (_isolate_home / "habits.json").write_text(
        json.dumps([{"rule": "客套删除", "example": "不要开场客套"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (_isolate_home / "style_samples.md").write_text("范文首句在此，语气要稳。\n\n第二段范文。", encoding="utf-8")
    fake = _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.generate("demo", root=root)
    prompt = fake.calls[0][0]["content"]
    assert "<!--style-->" in prompt and "写作风格要求" in prompt  # {{style}} 插槽命中
    assert "客套删除" in prompt and "范文首句在此" in prompt


def test_generate_budget_truncation_marks_excerpt(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_source = tmp_path / "long.md"
    long_source.write_text("## 一、长文\n\n" + "长" * 5000, encoding="utf-8")
    fake = _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", long_source, ["一、长文"], root=root)
    compose_service.generate("demo", root=root)
    prompt = fake.calls[0][0]["content"]
    assert "素材已节选" in prompt  # 超限截断+标注
    assert 1990 <= prompt.count("长") < 2100  # 截到每素材预算位（含章节标题里的「长」）


def test_generate_template_override_cascade_and_missing_slot_backfill(
    root: Path, source: Path, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompts.d/compose.txt 覆盖级联：自定义模板生效；缺槽回补不丢上下文。"""
    prompts_dir = _isolate_home / "prompts.d"
    prompts_dir.mkdir()
    (prompts_dir / "compose.txt").write_text(
        "自定义底座。素材如下：\n{{sources}}", encoding="utf-8"
    )
    fake = _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, None, root=root)
    compose_service.generate("demo", root=root)
    prompt = fake.calls[0][0]["content"]
    assert prompt.startswith("自定义底座。")  # 覆盖生效
    assert "概述正文段落" in prompt  # {{sources}} 命中
    assert "【当前真稿】" in prompt  # 模板缺 {{draft}} 槽 → 固定顺序回补
    assert "【上一轮人工裁决】" in prompt


def test_decisions_and_direction_enter_next_prompt_and_k_window(
    root: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """decide 三态落盘 + discard 方向/adopt_edited 定稿进下轮 prompt；窗口 K=2。"""
    fake = _fake(monkeypatch, REPLY_R1, REPLY_R2, REPLY_R2, REPLY_R2)
    compose_service.new_session("demo", "T", root=root)
    compose_service.generate("demo", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.decide("demo", 1, "b2", "adopt_edited", text="人工定稿：三种解法对比。", root=root)
    compose_service.decide("demo", 1, "b3", "discard", direction="太啰嗦，压缩为一句", root=root)

    compose_service.generate("demo", root=root)  # 第 2 轮
    prompt2 = fake.calls[1][0]["content"]
    assert "人工定稿：三种解法对比。" in prompt2  # adopt_edited 定稿进 prompt
    assert "太啰嗦，压缩为一句" in prompt2  # discard 方向进 prompt
    assert "第 1 轮" in prompt2  # 块级 diff 摘要（首轮基线=draft）

    # 摘要在 generate 时对既有轮取最近 K=2 窗：第 4 轮的 prompt 只含第 2/3 轮
    compose_service.generate("demo", root=root)  # 第 3 轮（prompt 含第 1/2 轮摘要）
    compose_service.generate("demo", root=root)  # 第 4 轮（prompt 含第 2/3 轮摘要）
    prompt4 = fake.calls[3][0]["content"]
    assert "第 2 轮" in prompt4 and "第 3 轮" in prompt4
    assert "第 1 轮：" not in prompt4  # 窗口外整轮退出


def test_decide_guards_and_feedback_log(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.generate("demo", root=root)
    with pytest.raises(compose_service.ComposeError, match="轮次不存在"):
        compose_service.decide("demo", 9, "b1", "keep", root=root)
    with pytest.raises(compose_service.ComposeError, match="块不存在"):
        compose_service.decide("demo", 1, "zz", "keep", root=root)
    with pytest.raises(ValueError, match="direction"):
        compose_service.decide("demo", 1, "b1", "discard", direction="", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.decide("demo", 1, "b1", "discard", direction="改主意", root=root)  # 末次胜出
    decisions = json.loads(
        (root / "demo.compose" / "rounds" / "1.decisions.json").read_text(encoding="utf-8")
    )
    assert [x["action"] for x in decisions] == ["discard"]  # 同块不重复占位
    lines = (root / "demo.compose" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # 裁决流逐条追加（审计）
    first = json.loads(lines[0])
    assert first["session"] == "demo" and first["round"] == 1 and first["block"] == "b1"


def test_generate_second_round_anchor_inheritance(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """二轮输出锚 b2 → 继承 id；新块避开会话既有 b1-b3。"""
    _fake(monkeypatch, REPLY_R1, REPLY_R2)
    compose_service.new_session("demo", "T", root=root)
    compose_service.generate("demo", root=root)
    result = compose_service.generate("demo", root=root)
    assert result["round"] == 2
    ids = [b["id"] for b in result["blocks"]]
    assert ids[0] == "b2"  # 锚继承
    assert ids[1] == "b4"  # 新块避让 b1-b3


# ── adopt：合并+版本池+habits+归因+入队列（§2.4/§2.6）────────────────


def test_adopt_full_closure(root: Path, source: Path, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "缓存三解法", root=root)
    other = source.with_name("other.md")
    other.write_text("## 协作素材\n\n协作者观点。", encoding="utf-8")
    compose_service.add_source("demo", source, ["一、概述"], root=root)
    compose_service.add_source("demo", other, ["协作素材"], root=root)
    # §四前端行验收点：AI 产出不经「采纳」绝不入 draft.md——generate/decide
    # 全程 draft.md 字节不变（行为红线，非纯函数断言）
    draft_before = (root / "demo.compose" / "draft.md").read_bytes()
    compose_service.generate("demo", root=root)
    assert (root / "demo.compose" / "draft.md").read_bytes() == draft_before  # generate 后不动
    with pytest.raises(compose_service.ComposeError, match="尚无任何裁决"):
        compose_service.adopt("demo", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.decide(
        "demo", 1, "b2", "adopt_edited", text="人工定稿段：解法对比要更口语，总的来说这样更顺。", root=root
    )
    compose_service.decide("demo", 1, "b3", "discard", direction="结尾太套话", root=root)
    assert (root / "demo.compose" / "draft.md").read_bytes() == draft_before  # 裁决后仍不动

    result = compose_service.adopt("demo", root=root)
    assert result["merged_blocks"] == 2 and result["habits_learned"] >= 1

    # draft.md：合并稿带锚（keep=ai 原文；adopt_edited=human 定稿；discard 弃）
    draft = (root / "demo.compose" / "draft.md").read_text(encoding="utf-8")
    assert (root / "demo.compose" / "draft.md").read_bytes() != draft_before  # 唯有 adopt 落盘
    assert "<!--b:b1-->" in draft and "<!--b:b2-->" in draft
    assert "人工定稿段" in draft and "推荐从布隆过滤器起步" not in draft

    # 正稿入队列 + 归因（§2.6：首素材=主源，其余=协作者）
    final = root / "demo.md"
    assert final.is_file()
    import frontmatter

    post = frontmatter.load(final)
    assert post.metadata["title"] == "缓存三解法"
    assert post.metadata["status"] == "draft"
    assert post.metadata["source"] == str(source)
    assert post.metadata["source_extra"] == [str(other)]
    assert "人工定稿段" in post.content

    # habits 闭环：adopt_edited 差分 → 训练对落 habits.json
    habits = load_habits(_isolate_home / "habits.json")
    assert habits and any("口语" in h.example or "总的来说" in h.example for h in habits)

    # 会话终态
    assert compose_service.session_detail("demo", root=root)["status"] == "adopted"
    with pytest.raises(compose_service.ComposeError, match="已采纳"):
        compose_service.adopt("demo", root=root)


def test_adopt_backs_up_prior_draft_to_version_pool(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """人工预置真稿 → 首轮生成（基线=draft）→ adopt：旧稿入 F43 版本池。"""
    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    d = root / "demo.compose"
    (d / "draft.md").write_text(
        "<!--b:h1-->\n# 人工预置标题\n\n<!--b:h2-->\n人工预置正文段。", encoding="utf-8"
    )
    compose_service.generate("demo", root=root)  # 首轮 previous=draft
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.adopt("demo", root=root)
    pools = sorted(p.name for p in d.glob("draft.md.bak.*"))
    assert pools and pools[0].startswith("draft.md.bak.")  # F43 版本池
    assert "# 人工预置标题" in (d / pools[0]).read_text(encoding="utf-8")


def test_adopt_refuses_when_final_exists(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    (root / "demo.md").write_text("---\ntitle: 占位\n---\n\n已有正稿。", encoding="utf-8")
    compose_service.generate("demo", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    with pytest.raises(compose_service.ComposeError, match="正稿已存在"):
        compose_service.adopt("demo", root=root)


def test_status_and_list(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, REPLY_R1)
    assert compose_service.status(root=root) == {"sessions": []}
    compose_service.new_session("demo", "标题一", root=root)
    compose_service.generate("demo", root=root)
    sessions = compose_service.list_sessions(root=root)
    assert sessions[0]["slug"] == "demo" and sessions[0]["rounds"] == 1
    detail = compose_service.status("demo", root=root)
    assert detail["rounds"][0]["n"] == 1 and len(detail["rounds"][0]["blocks"]) == 3


def test_session_dir_excluded_from_article_queue(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """会话目录不污染文章队列；adopt 正稿才入队列（scan._iter_article_files）。"""
    from musespiral.studio.scan import _iter_article_files

    _fake(monkeypatch, REPLY_R1)
    compose_service.new_session("demo", "T", root=root)
    compose_service.generate("demo", root=root)
    compose_service.decide("demo", 1, "b1", "keep", root=root)
    compose_service.adopt("demo", root=root)
    names = [p.name for p in _iter_article_files(root)]
    assert names == ["demo.md"]  # draft.md/rounds/1.md 均不进队列


# ── CLI headless 全链（§四 验收：new→add→generate(mock)→decide→再generate→adopt）──


def test_cli_full_chain_headless(
    root: Path, source: Path, _isolate_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from musespiral.commands.compose_cmds import compose as compose_group

    fake = _fake(monkeypatch, REPLY_R1, REPLY_R2)
    r = CliRunner()

    def run(*args: str) -> None:
        result = r.invoke(compose_group, list(args))
        assert result.exit_code == 0, result.output

    run("new", "demo", "--title", "缓存三解法", "--root", str(root))
    run("add", "demo", str(source), "--section", "一、概述", "--root", str(root))
    run("add", "demo", str(source), "--list-sections")
    run("generate", "demo", "--root", str(root))
    run("decide", "demo", "1", "b1", "keep", "--root", str(root))
    run(
        "decide", "demo", "1", "b2", "adopt_edited",
        "--text", "人工定稿：解法对比更口语，总的来说这样更顺。", "--root", str(root),
    )
    run("decide", "demo", "1", "b3", "discard", "--direction", "太啰嗦，压缩", "--root", str(root))
    run("generate", "demo", "--root", str(root))  # 按反馈再生成（第 2 轮：锚继承 b2 + 新块 b4）
    run("decide", "demo", "2", "b2", "keep", "--root", str(root))  # 本轮裁决（人工最后一步）
    run("decide", "demo", "2", "b4", "keep", "--root", str(root))
    run("adopt", "demo", "--root", str(root))
    run("status", "demo", "--root", str(root))
    run("status", "--root", str(root))

    assert len(fake.calls) == 2  # 两轮 generate 均走 chat(task="compose")
    assert (root / "demo.md").is_file()  # 正稿入队列
    assert (root / "demo.compose" / "rounds" / "2.md").is_file()
    habits = load_habits(_isolate_home / "habits.json")
    assert habits  # habits 训练对已产出（飞轮点火）
    meta = json.loads((root / "demo.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["status"] == "adopted" and len(meta["rounds"]) == 2


def test_cli_error_paths(root: Path) -> None:
    from musespiral.commands.compose_cmds import compose as compose_group

    r = CliRunner()
    result = r.invoke(compose_group, ["status", "ghost", "--root", str(root)])
    assert result.exit_code == 1 and "会话不存在" in result.output
    result = r.invoke(compose_group, ["new", "bad/slug", "--root", str(root)])
    assert result.exit_code == 1 and "非法 slug" in result.output
    result = r.invoke(compose_group, ["decide", "x", "1", "b1", "keep", "--root", str(root)])
    assert result.exit_code == 1
