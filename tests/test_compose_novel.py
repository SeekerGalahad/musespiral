"""E-5 小说模式（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.6）。

覆盖任务书六验收面：
  a) **前情提要生成**：读「前 N-1 章」（末章=正在写的章不进摘要）→
     `chat(task="novel_summary")` → <300 字硬上限；手改不推进章数、清空即停注入；
  b) **摘要注入 prompt**：novel_summary 非空 → 后续 generate 的 prompt 必含其
     文字（这是本功能的关键——不能只存不注）；与 rolling_summary **并存**且
     互不覆盖（两插槽各记各的）；
  c) **人物卡**：素材 meta.sub_type=character 建卡/幂等/同名异内容拒绝；会话
     挂卡 → 生成 prompt 注入 name/appearance/speech_style/key_events 摘要；
  d) **冲突检测**：人物卡 speech_style 含「不用感叹号」+ 生成对话含「！」→
     ⚠️ 提示列表（**只提示不拦截**：生成文本零改写、blocks 原样返回）；规则
     可配（~/.musespiral/novel_rules.json 覆盖；坏文件降级零提示不炸生成）；
  e) **端点**：POST /api/compose/novel-summary 六 action（refresh/edit/kind/
     character_add/character_remove/character_create）+ confirm 闸门 + 400 分档；
  f) **契约锁**：端点进 CONTRACT_POST_EXACT；TASKS 扩 novel_summary；prompts
     七槽。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral.core.composition import Block
from musespiral.core.composition import novel as novel_mod
from musespiral.services import compose_service, materials_service

# 两章稿件：第一章=前情（进摘要），第二章=当前章（不进摘要）
REPLY_TWO_CHAPTERS = (
    "<<<block kind:heading>>># 第一章 异变\n\n"
    "<<<block kind:para>>>陈默睁开眼，左眼发烫。\n\n"
    "<<<block kind:heading>>># 第二章 借据\n\n"
    "<<<block kind:para>>>林远递来一份文件。\n"
)

# 生成稿里带一句违规则对话（林远说感叹号）——冲突检测的靶子
REPLY_CONFLICT = (
    "<<<block kind:heading>>># 第一章 异变\n\n"
    "<<<block kind:para>>>陈默睁开眼。\n\n"
    "<<<block kind:heading>>># 第二章 借据\n\n"
    "<<<block kind:para>>>林远：「这件事不能拖！」\n"
)

NOVEL_AI = "陈默左眼异变；林远在第2章递来文件（伏笔：文件内容未交代）。"

CHARACTER = {
    "name": "林远",
    "role": "研究员",
    "appearance": "瘦高 · 左眼蓝色",
    "speech_style": "短句 · 不用感叹号 · 偶尔自嘲",
    "key_events": ["第3章 初次出场", "第7章 借钱不还"],
}


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


class _FakeChat:
    """按任务分派的假 chat：compose 走脚本回复，novel_summary 回固定提要。"""

    def __init__(self, *compose_replies: str, summary: str = NOVEL_AI) -> None:
        self.compose = list(compose_replies)
        self.summary = summary
        self.compose_calls: list[list[dict]] = []
        self.summary_calls: list[list[dict]] = []

    def __call__(self, messages, *, task=None, **kw):
        if task == "novel_summary":
            self.summary_calls.append(messages)
            return self.summary
        assert task == "compose", task
        self.compose_calls.append(messages)
        return self.compose.pop(0)


def _fake(monkeypatch: pytest.MonkeyPatch, *replies: str, summary: str = NOVEL_AI) -> _FakeChat:
    fake = _FakeChat(*replies, summary=summary)
    monkeypatch.setattr(compose_service, "_chat", fake)
    return fake


def _novel_session(root: Path, slug: str = "novel1", chapters: str = REPLY_TWO_CHAPTERS) -> None:
    compose_service.new_session(slug, "长篇", root=root, kind="novel")
    compose_service.generate(slug, root=root, chat_fn=lambda m, **k: chapters)


def _attach_character(slug: str, root: Path, **overrides) -> str:
    payload = {**CHARACTER, **overrides}
    created = materials_service.add_character(
        payload["name"],
        role=payload["role"],
        appearance=payload["appearance"],
        speech_style=payload["speech_style"],
        key_events=payload["key_events"],
    )
    mid = created["material"]["id"]
    compose_service.add_character(slug, mid, root=root)
    return mid


# ══ a) 章节切分 + 前情提要生成 ═════════════════════════════════════════


def test_split_chapters_by_heading() -> None:
    """章节切分：heading 开启新章；首个 heading 之前的块归第 1 章。"""
    blocks = [
        Block("b1", "para", "序言段。"),
        Block("b2", "heading", "# 第一章 异变"),
        Block("b3", "para", "正文一。"),
        Block("b4", "heading", "# 第二章 借据"),
        Block("b5", "para", "正文二。"),
    ]
    chapters = novel_mod.split_chapters(blocks)
    assert [c.index for c in chapters] == [1, 2, 3]
    assert chapters[0].title == "" and chapters[0].blocks[0].text == "序言段。"
    assert chapters[1].title == "第一章 异变"
    assert chapters[2].title == "第二章 借据"


def test_prior_chapters_excludes_last_chapter() -> None:
    """**前 N-1 章**：末章（当前正在写）不进摘要输入；单章 → 无可摘要。"""
    blocks = [
        Block("b1", "heading", "# 第一章"),
        Block("b2", "para", "第一章内容。"),
        Block("b3", "heading", "# 第二章"),
        Block("b4", "para", "第二章内容。"),
    ]
    text, covered, total = novel_mod.prior_chapters_text(blocks)
    assert "第一章内容" in text and "第二章内容" not in text
    assert (covered, total) == (1, 2)
    # 单章 → 空（调用方报可读错误）
    single = [Block("b1", "heading", "# 唯一章"), Block("b2", "para", "内容。")]
    assert novel_mod.prior_chapters_text(single) == ("", 0, 1)


def test_refresh_novel_summary_caps_300_and_records_chapters(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前情提要：输出硬截断 300 字；章数落盘；围栏剥离。"""
    _fake(monkeypatch, REPLY_TWO_CHAPTERS)
    _novel_session(root)
    reply = "```\n" + ("提" * 800) + "\n```"
    out = compose_service.refresh_novel_summary(
        "novel1", root=root, chat_fn=lambda m, **k: reply
    )
    assert out["refreshed"] is True
    assert len(out["summary"]) == compose_service.NOVEL_SUMMARY_MAX == 300
    assert "```" not in out["summary"]
    assert out["chapters"] == 2 and out["covered_chapters"] == 1
    meta = json.loads((root / "novel1.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["novel_summary"] == out["summary"]
    assert meta["novel_summary_chapters"] == 1


def test_refresh_novel_summary_uses_task_key(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刷新走 chat(task="novel_summary")（routing/覆盖/预算/per_task 自动继承）。"""
    fake = _fake(monkeypatch, REPLY_TWO_CHAPTERS)
    _novel_session(root, "task")
    compose_service.refresh_novel_summary("task", root=root)
    assert len(fake.summary_calls) == 1
    assert "陈默睁开眼" in fake.summary_calls[0][0]["content"]


def test_novel_summary_input_is_prior_chapters_only(root: Path) -> None:
    """chat_fn 注入路径下，摘要输入必须是前 N-1 章正文（末章缺席）。"""
    _novel_session(root)
    seen: list[str] = []

    def _chat(messages, *, task=None, **kw):
        assert task == "novel_summary"
        seen.append(messages[0]["content"])
        return NOVEL_AI

    compose_service.refresh_novel_summary("novel1", root=root, chat_fn=_chat)
    assert len(seen) == 1
    assert "陈默睁开眼" in seen[0]  # 第一章在内
    assert "林远递来一份文件" not in seen[0]  # 第二章（当前章）在外


def test_refresh_novel_summary_single_chapter_is_error(root: Path) -> None:
    """只有一章（无前情）→ 可读 ComposeError，不调 AI。"""
    compose_service.new_session("short", "短篇", root=root, kind="novel")
    compose_service.generate(
        "short", root=root,
        chat_fn=lambda m, **k: "<<<block kind:heading>>># 唯一章\n\n<<<block kind:para>>>内容。",
    )
    with pytest.raises(compose_service.ComposeError, match="尚无前情可摘要"):
        compose_service.refresh_novel_summary("short", root=root)


def test_set_novel_summary_manual_edit_and_clear(root: Path) -> None:
    """手改：写回但不推进章数；清空 → 后续 prompt 不再注入前情。"""
    _novel_session(root)
    compose_service.refresh_novel_summary(
        "novel1", root=root, chat_fn=lambda m, **k: NOVEL_AI
    )  # chapters=1
    compose_service.set_novel_summary("novel1", "人工修正后的前情。", root=root)
    meta = json.loads((root / "novel1.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["novel_summary"] == "人工修正后的前情。"
    assert meta["novel_summary_chapters"] == 1  # 手改不动章数
    compose_service.set_novel_summary("novel1", "", root=root)
    prompt = compose_service.build_prompt(compose_service._load_session("novel1", root)[0])
    assert "人工修正后的前情。" not in prompt and "前情提要" not in prompt


def test_dual_summaries_coexist_and_both_inject(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**两摘要并存**：rolling 与 novel 互不覆盖，prompt 同带两节（§3.6 明确要求）。"""
    _novel_session(root, "dual")
    compose_service.set_rolling_summary("dual", "滚动脉络：近期推进到这里。", root=root)
    compose_service.set_novel_summary("dual", "前情：第一章已交代世界观。", root=root)
    meta = json.loads((root / "dual.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["rolling_summary"] and meta["novel_summary"]
    prompt = compose_service.build_prompt(compose_service._load_session("dual", root)[0])
    assert "滚动脉络：近期推进到这里。" in prompt and "前情：第一章已交代世界观。" in prompt
    # 插槽序：novel 在 rolling 之前，两者都在真稿之前
    assert prompt.index("前情提要") < prompt.index("到目前为止的文章脉络") < prompt.index("【当前真稿】")
    # 清 novel 不动 rolling；清 rolling 不动 novel
    compose_service.set_novel_summary("dual", "", root=root)
    meta = json.loads((root / "dual.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["rolling_summary"] and not meta["novel_summary"]


# ══ b) 摘要注入 prompt（本功能的关键断言）═════════════════════════════


def test_novel_summary_injected_into_generate_prompt(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """novel_summary 非空 → 后续 generate 的 prompt 必含摘要文字；空则整节缺席。"""
    _novel_session(root, "inj")
    first = compose_service.build_prompt(compose_service._load_session("inj", root)[0])
    assert "前情提要" not in first  # 空摘要整节缺席
    compose_service.set_novel_summary("inj", "前情：左眼异变尚未解释。", root=root)
    second = compose_service.build_prompt(compose_service._load_session("inj", root)[0])
    assert "前情：左眼异变尚未解释。" in second and "前情提要" in second


def test_novel_summary_backfilled_when_template_lacks_slot(
    root: Path, _isolate_home: Path
) -> None:
    """自定义 compose.txt 缺 {{novel}} → 缺槽回补仍注入（不丢前情）。"""
    prompts_dir = _isolate_home / "prompts.d"
    prompts_dir.mkdir()
    (prompts_dir / "compose.txt").write_text("自定义底座。素材：\n{{sources}}", encoding="utf-8")
    _novel_session(root, "bk")
    compose_service.set_novel_summary("bk", "前情：回补测试。", root=root)
    prompt = compose_service.build_prompt(compose_service._load_session("bk", root)[0])
    assert prompt.startswith("自定义底座。")
    assert "前情：回补测试。" in prompt and "前情提要" in prompt


def test_novel_summary_task_key_is_chat_whitelisted() -> None:
    """TASKS 含 novel_summary（22→23）：routing/覆盖/ai check 自动继承。"""
    from musespiral.infra.ai.profiles import TASKS

    assert "novel_summary" in TASKS and len(TASKS) == 26  # PL-6 批 24→25；N-2 批 25→26（material_distill）


def test_prompt_slots_include_novel_and_characters() -> None:
    """PROMPT_SLOTS 七槽（增 {{novel}}/{{characters}}）：设置面编辑器可见。"""
    from musespiral.infra.ai.prompts import PROMPT_SLOTS

    assert "{{novel}}" in PROMPT_SLOTS and "{{characters}}" in PROMPT_SLOTS
    assert len(PROMPT_SLOTS) == 7


# ══ c) 人物卡：素材面 + 注入 ═══════════════════════════════════════════


def test_character_material_sub_type_and_idempotent() -> None:
    """建卡：type=text + meta.sub_type=character；同名同内容幂等；同名异内容拒绝。"""
    created = materials_service.add_character(
        CHARACTER["name"],
        role=CHARACTER["role"],
        appearance=CHARACTER["appearance"],
        speech_style=CHARACTER["speech_style"],
        key_events=CHARACTER["key_events"],
    )
    row = created["material"]
    assert row["type"] == "text"  # 不新增 MATERIAL_TYPES 取值（见 model.META_SUB_TYPES）
    assert row["payload"]["meta"]["sub_type"] == "character"
    assert row["payload"]["meta"]["name"] == "林远"
    assert row["origin"]["uri"] == "character://林远"
    # 幂等
    again = materials_service.add_character(
        CHARACTER["name"], role=CHARACTER["role"], appearance=CHARACTER["appearance"],
        speech_style=CHARACTER["speech_style"], key_events=CHARACTER["key_events"],
    )
    assert again["dedup"] is True and again["material"]["id"] == row["id"]
    # 同名异内容 → 拒绝（人物卡是快照，不改写）
    with pytest.raises(ValueError, match="内容不同"):
        materials_service.add_character(CHARACTER["name"], role="换了")


def test_character_validation_errors() -> None:
    """建卡校验：name 必填非空、key_events 必须字符串数组。"""
    with pytest.raises(ValueError, match="name 必须是非空字符串"):
        materials_service.add_character("   ")
    with pytest.raises(ValueError, match="key_events"):
        materials_service.add_character("林远", key_events="不是数组")  # type: ignore[arg-type]


def test_list_and_get_characters() -> None:
    """卡池读面：list_characters 只回人物卡；get_character 非卡 → None。"""
    materials_service.add_character("林远", role="研究员")
    materials_service.add_character("陈默", role="主角")
    cards = materials_service.list_characters()
    assert {c["name"] for c in cards} == {"林远", "陈默"}
    assert materials_service.get_character("nope") is None


def test_character_injected_into_prompt(root: Path) -> None:
    """会话挂卡 → 生成 prompt 注入 name/role/appearance/speech_style/key_events。"""
    _novel_session(root, "withcards")
    _attach_character("withcards", root)
    prompt = compose_service.build_prompt(compose_service._load_session("withcards", root)[0])
    assert "人物卡" in prompt
    assert "林远" in prompt and "研究员" in prompt
    assert "瘦高 · 左眼蓝色" in prompt  # appearance
    assert "短句 · 不用感叹号 · 偶尔自嘲" in prompt  # speech_style
    assert "第3章 初次出场" in prompt and "第7章 借钱不还" in prompt  # key_events


def test_character_attach_idempotent_and_remove(root: Path) -> None:
    """挂卡幂等（重复挂不重复入列）；摘卡不动素材本身。"""
    _novel_session(root, "att")
    mid = _attach_character("att", root)
    compose_service.add_character("att", mid, root=root)  # 再挂一次
    detail = compose_service.session_detail("att", root=root)
    assert [c["id"] for c in detail["characters"]] == [mid]
    compose_service.remove_character("att", mid, root=root)
    assert compose_service.session_detail("att", root=root)["characters"] == []
    assert materials_service.get_character(mid) is not None  # 素材未删
    with pytest.raises(compose_service.ComposeError, match="未挂"):
        compose_service.remove_character("att", mid, root=root)


def test_attach_non_character_material_rejected(root: Path) -> None:
    """非人物卡素材不可挂（meta.sub_type 缺省 → ComposeError）。"""
    from musespiral.core.materials import model

    _novel_session(root, "bad")
    plain = materials_service.add_material(
        model.make_material(
            mtype="text", source_id="kb", uri="doc.md", title="普通文档",
            payload={"text": "正文"}, fetched_at="2026-11-19T10:00:00",
        )
    )
    with pytest.raises(compose_service.ComposeError, match="不是人物卡"):
        compose_service.add_character("bad", plain["id"], root=root)


# ══ d) 冲突检测（⚠️ 提示，不拦截）═════════════════════════════════════


def test_conflict_detected_for_exclamation_style(root: Path) -> None:
    """**核心验收**：卡写「不用感叹号」+ 生成对话含「！」→ ⚠️ 提示列表。"""
    compose_service.new_session("cf", "冲突", root=root, kind="novel")
    _attach_character("cf", root)
    out = compose_service.generate(
        "cf", root=root, chat_fn=lambda m, **k: REPLY_CONFLICT
    )
    conflicts = out["conflicts"]
    assert len(conflicts) == 1
    hit = conflicts[0]
    assert hit["character"] == "林远"
    assert hit["rule"] == "no_exclamation"
    assert "感叹号" in hit["message"]
    assert "！」" in hit["snippet"] or "不能拖" in hit["snippet"]
    # **不拦截**：块照常产出、文本零改写
    assert any("不能拖" in b["text"] for b in out["blocks"])
    assert "！" in next(b["text"] for b in out["blocks"] if "不能拖" in b["text"])


def test_no_conflict_when_style_absent_or_text_clean(root: Path) -> None:
    """无感叹号的卡/干净文本 → 零提示（规则按 speech_style 触发词激活）。"""
    compose_service.new_session("clean", "干净", root=root, kind="novel")
    _attach_character("clean", root, speech_style="短句")
    out = compose_service.generate("clean", root=root, chat_fn=lambda m, **k: REPLY_CONFLICT)
    assert out["conflicts"] == []
    # 未挂卡 → 零提示（无卡无规则可对照）
    compose_service.new_session("nocards", "无卡", root=root, kind="novel")
    out2 = compose_service.generate("nocards", root=root, chat_fn=lambda m, **k: REPLY_CONFLICT)
    assert out2["conflicts"] == []


def test_conflict_requires_speaker_attribution() -> None:
    """说话人归属：行内不含卡上姓名的对话不报（误报成本高于漏报）。"""
    cards = [{"name": "林远", "speech_style": "不用感叹号"}]
    unrelated = [Block("b1", "para", "路人：「快看！」")]
    assert novel_mod.detect_conflicts(unrelated, cards) == []
    attributed = [Block("b2", "para", "林远：「快看！」")]
    assert len(novel_mod.detect_conflicts(attributed, cards)) == 1


def test_conflict_rules_configurable_and_bad_file_degrades(
    root: Path, _isolate_home: Path
) -> None:
    """规则可配：novel_rules.json 覆盖内置表；坏文件 → 降级零提示（不炸生成）。"""
    compose_service.new_session("rules", "规则", root=root, kind="novel")
    _attach_character("rules", root, speech_style="爱用问号")
    rules_path = _isolate_home / "novel_rules.json"
    rules_path.write_text(
        json.dumps([{
            "id": "too_many_question",
            "speech_contains": "爱用问号",
            "line_pattern": r"[？?]{2,}",
            "message": "{name} 连用问号，与人物卡不符",
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    reply = "<<<block kind:para>>>林远：「你确定？？」\n"
    out = compose_service.generate("rules", root=root, chat_fn=lambda m, **k: reply)
    assert len(out["conflicts"]) == 1 and out["conflicts"][0]["rule"] == "too_many_question"
    # 坏文件：JSON 非法 → 零提示降级（生成主流程不因检测器配置失败）
    rules_path.write_text("{not json", encoding="utf-8")
    out2 = compose_service.generate("rules", root=root, chat_fn=lambda m, **k: reply)
    assert out2["conflicts"] == [] and out2["round"] == 2


def test_novel_service_rejects_bad_rule_files(_isolate_home: Path) -> None:
    """规则文件错拼早失败：结构错/正则非法 → NovelServiceError 可读。"""
    from musespiral.services import novel_service

    path = _isolate_home / "novel_rules.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(novel_service.NovelServiceError, match="必须是数组"):
        novel_service.load_rules()
    path.write_text(json.dumps([{
        "id": "x", "speech_contains": "y", "line_pattern": "([", "message": "m",
    }]), encoding="utf-8")
    with pytest.raises(novel_service.NovelServiceError, match="非法正则"):
        novel_service.load_rules()
    path.unlink()
    assert novel_service.load_rules() == [dict(r) for r in novel_mod.DEFAULT_CONFLICT_RULES]


def test_outline_expand_reports_conflicts(root: Path) -> None:
    """大纲展开同链：产块同样过冲突检测（§3.6 生成后自动检查不旁路）。"""
    compose_service.new_session("ohn", "大纲小说", root=root, kind="novel")
    _attach_character("ohn", root)
    calls: list[str] = []

    def _chat(messages, *, task=None, **kw):
        calls.append(messages[0]["content"])
        if len(calls) == 1:  # outline_generate
            return "第一节 | 400 | 开篇"
        return "<<<block kind:para>>>林远：「走！」\n"

    compose_service.outline_generate("ohn", root=root, chat_fn=_chat)
    compose_service.outline_approve("ohn", "s1", True, root=root)
    out = compose_service.expand_section("ohn", "s1", root=root, chat_fn=_chat)
    assert len(out["conflicts"]) == 1 and out["conflicts"][0]["character"] == "林远"
    # 展开 prompt 也带前情/人物卡（长文不跑偏）
    assert "人物卡" in calls[-1] and "林远" in calls[-1]


# ══ e) Studio 端点 ═════════════════════════════════════════════════════


def _server(root: Path):
    from musespiral import studio

    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post(server, path: str, body: dict):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(server, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_api_novel_summary_forms(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """六 action 金路径：refresh / edit / kind / character_create / add / remove。"""
    _fake(monkeypatch, REPLY_TWO_CHAPTERS)
    _novel_session(root, "api")
    server, thread = _server(root)
    try:
        # 缺 confirm 全局闸门
        assert _post(server, "/api/compose/novel-summary", {"slug": "api"})[0] == 400
        # 缺 slug（refresh 形态）
        assert _post(server, "/api/compose/novel-summary", {"confirm": True})[0] == 400
        # summary 非字符串
        code, _ = _post(server, "/api/compose/novel-summary",
                        {"slug": "api", "summary": 5, "confirm": True})
        assert code == 400
        # action 枚举外
        code, _ = _post(server, "/api/compose/novel-summary",
                        {"slug": "api", "action": "explode", "confirm": True})
        assert code == 400

        # refresh 金路径
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "confirm": True})
        assert code == 200 and payload["ok"] is True
        assert payload["summary"] == NOVEL_AI and payload["action"] == "refresh"

        # edit 形态
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "summary": "人工前情。", "confirm": True})
        assert code == 200 and payload["edited"] is True and payload["summary"] == "人工前情。"

        # kind 切换
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "action": "kind", "kind": "article", "confirm": True})
        assert code == 200 and payload["kind"] == "article"
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "action": "kind", "kind": "novel", "confirm": True})
        assert code == 200 and payload["kind"] == "novel"

        # character_create 金路径
        code, payload = _post(server, "/api/compose/novel-summary", {
            **CHARACTER, "action": "character_create", "confirm": True,
        })
        assert code == 200 and payload["ok"] is True and payload["dedup"] is False
        cid = payload["material"]["id"]
        assert payload["character"]["speech_style"] == CHARACTER["speech_style"]

        # character_add / remove
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "action": "character_add", "id": cid, "confirm": True})
        assert code == 200 and [c["id"] for c in payload["characters"]] == [cid]
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "api", "action": "character_remove", "id": cid, "confirm": True})
        assert code == 200 and payload["characters"] == []

        # 详情面：kind/novel/characters/novel_summary 全下发
        code, detail = _get(server, "/api/compose/session?slug=api")
        assert code == 200
        assert detail["kind"] == "novel"
        assert detail["novel_summary"] == "人工前情。"
        assert detail["novel"]["chapters"] == 2
        assert detail["characters"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_api_novel_summary_error_is_ok_false(root: Path) -> None:
    """会话不存在 → 200 {ok:false}（业务失败面，不 500）。"""
    server, thread = _server(root)
    try:
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"slug": "ghost", "confirm": True})
        assert code == 200 and payload["ok"] is False
        assert "会话不存在" in payload["error"]
        # character_create 不依赖会话（卡的家在素材库）
        code, payload = _post(server, "/api/compose/novel-summary",
                              {"name": "无名氏", "action": "character_create", "confirm": True})
        assert code == 200 and payload["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_compose_new_accepts_kind(root: Path) -> None:
    """建会话可直起 novel（kind 参数透传服务层；枚举外 → ok:false）。"""
    server, thread = _server(root)
    try:
        code, payload = _post(server, "/api/compose/new",
                              {"slug": "n1", "title": "小说", "kind": "novel", "confirm": True})
        assert code == 200 and payload["ok"] is True
        code, detail = _get(server, "/api/compose/session?slug=n1")
        assert detail["kind"] == "novel"
        code, payload = _post(server, "/api/compose/new",
                              {"slug": "n2", "kind": "poem", "confirm": True})
        assert code == 200 and payload["ok"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generate_job_result_carries_conflicts(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N-1 接缝回归锁（2026-11-20 集成轮浏览器冒烟缺陷）：job.result 必带
    conflicts——前端 cmGenerate 读 ``j.result.conflicts`` 渲染⚠️提示条，
    闭包漏传则服务层 conflicts 再对、提示条也永不出现。

    链：novel 会话 + 挂卡（speech_style 含「不用感叹号」）→ HTTP generate
    （chat 假件返回含「！」的林远对话行）→ job 终态 result.conflicts 非空
    且逐字段可回溯（character/rule/snippet）。"""
    import time as _time

    reply = "<<<block kind:para>>>林远：「这件事不能拖！」\n"
    _fake(monkeypatch, reply)
    # Studio 走 on_delta 流式路径（E-4）——消费的是 _chat_stream 而非 _chat
    # （门面 __setattr__ 会把两名字同步转发到 generate 子模块）。
    monkeypatch.setattr(compose_service, "_chat_stream",
                        lambda messages, **kw: reply)
    compose_service.new_session("conf1", "冲突链", root=root, kind="novel")
    _attach_character("conf1", root)  # CHARACTER.speech_style 含「不用感叹号」
    server, thread = _server(root)
    try:
        code, payload = _post(server, "/api/compose/generate",
                              {"slug": "conf1", "confirm": True})
        assert code == 200 and payload["ok"] and payload["job_id"]
        job = None
        for _ in range(200):  # ≤10s 轮询至终态（job 面无长等待——假件秒回）
            code, job = _get(server, f"/api/jobs/{payload['job_id']}")
            if job.get("status") in ("done", "failed"):
                break
            _time.sleep(0.05)
        assert job and job["status"] == "done", job
        conflicts = job["result"].get("conflicts")
        assert conflicts, "job.result 缺 conflicts——前端冲突提示条无数据面"
        first = conflicts[0]
        assert first["character"] == "林远" and first["rule"] == "no_exclamation"
        assert "！" in first["snippet"] and "感叹号" in first["message"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ══ f) 契约锁 ══════════════════════════════════════════════════════════


def test_contract_lock_membership() -> None:
    """契约锁：novel-summary 在 POST 面；GET 面零新增（详情复用 compose/session）。
    面计数随 §3.19 PL-3 接驳批 POST 62→65 / GET 42→44；N-2 批 POST 66→69（对拍锁语义不变）。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    assert "/api/compose/novel-summary" in CONTRACT_POST_EXACT
    assert "/api/compose/novel-summary" in _ACTION_ROUTES
    assert len(CONTRACT_POST_EXACT) == 69  # §3.19 插件三动作 + PL-6 批 +3；N-2 素材闭环批 +3
    assert len(CONTRACT_GET_EXACT) == 46  # §3.20 P2 迭代链两读面入锁（44→46；POST 零新增）


def test_kind_field_backward_compatible_with_legacy_session(root: Path) -> None:
    """存量 session.json 无 kind/novel 四键 → 读侧归一 article（零迁移）。"""
    compose_service.new_session("legacy", "老会话", root=root)
    meta_path = root / "legacy.compose" / "session.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key in ("kind", "novel_summary", "novel_summary_chapters", "characters"):
        meta.pop(key, None)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    session, _ = compose_service._load_session("legacy", root)
    assert session.kind == "article"
    assert session.novel_summary == "" and session.characters == []
    detail = compose_service.session_detail("legacy", root=root)
    assert detail["kind"] == "article" and detail["characters"] == []
    # 列表清单同口径
    assert compose_service.list_sessions(root=root)[0]["kind"] == "article"


def test_sealed_session_rejects_novel_writes(root: Path) -> None:
    """封版会话拒绝 novel 域写（与其余写面同口径）。"""
    _novel_session(root, "sealed")
    for b in compose_service._load_session("sealed", root)[0].rounds[-1].blocks:
        compose_service.decide("sealed", 1, b.id, "keep", root=root)
    compose_service.adopt("sealed", root=root)
    with pytest.raises(compose_service.ComposeError, match="已采纳"):
        compose_service.set_session_kind("sealed", "novel", root=root)
    with pytest.raises(compose_service.ComposeError, match="会话已采纳"):
        compose_service.refresh_novel_summary("sealed", root=root, chat_fn=lambda m, **k: "x")
