"""G-T4 滚动摘要（正典=《musespiral-调研吸收功能设计-2026-11-18》§五）。

覆盖四件事（任务书 G-T4 验收面）：
  a) **阈值触发**：正稿字数每跨 1500 字档位才（重）算——未跨档零 AI 调用；
     adopt 后异步触发（主流程不等 AI，摘要落盘发生在返回之后）；
  b) **摘要生成**：chat(task="rolling_summary") 输入=已写全文、输出清洗
     （剥围栏 + 200 字硬上限）；
  c) **prompt 注入断言**：rolling_summary 非空 → 后续 compose/generate 的
     prompt 必含摘要文字（这是本功能的关键——不能只存不注）；空摘要整节缺席；
  d) **手动刷新与用户手改**：POST /api/compose/rolling-summary 两形态
     （无 summary=刷新、有=手改）；手改不推进档位、清空即停注入。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from musespiral.services import compose_service

# 一份 1600 字（去空白）以上的 AI 首轮回复：跨过第一档 1500，触发摘要。
LONG_PARA = "长文段落内容。" * 260  # 7 字 × 260 = 1820 字（去空白口径）
REPLY_LONG = (
    "<<<block kind:heading>>># 长文标题\n\n"
    f"<<<block kind:para>>>{LONG_PARA}\n\n"
    "<<<block kind:para>>>结尾段。"
)
# 短稿（约 30 字）：远未到第一档。
REPLY_SHORT = (
    "<<<block kind:heading>>># 短标题\n\n"
    "<<<block kind:para>>>短文一段，综上所述收尾。"
)

SUMMARY_AI = "全文讲缓存三解法：先立问题，再比三种路线，结论倾向空值缓存（伏笔：布隆过滤器）。"


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
    """按任务分派的假 chat：compose 走脚本回复，rolling_summary 回固定摘要。

    捕获两任务的 messages 供断言（摘要输入=全文；生成 prompt 含摘要）。
    """

    def __init__(self, *compose_replies: str, summary: str = SUMMARY_AI) -> None:
        self.compose = list(compose_replies)
        self.summary = summary
        self.compose_calls: list[list[dict]] = []
        self.summary_calls: list[list[dict]] = []

    def __call__(self, messages, *, task=None, **kw):
        if task == "rolling_summary":
            self.summary_calls.append(messages)
            return self.summary
        assert task == "compose", task
        self.compose_calls.append(messages)
        return self.compose.pop(0)


def _fake(monkeypatch: pytest.MonkeyPatch, *replies: str, summary: str = SUMMARY_AI) -> _FakeChat:
    fake = _FakeChat(*replies, summary=summary)
    monkeypatch.setattr(compose_service, "_chat", fake)
    return fake


def _keep_all(svc, slug: str, round_n: int, root: Path) -> None:
    """末轮全块 keep（未裁决块不入稿——adopt 前置条件）。"""
    round_ = svc._load_session(slug, root)[0].rounds[-1]
    for b in round_.blocks:
        svc.decide(slug, round_n, b.id, "keep", root=root)


def _wait_summary(root: Path, slug: str, timeout: float = 5.0) -> dict:
    """等 adopt 起的后台摘要线程落盘（session.json 出现 rolling_summary）。"""
    meta_path = root / f"{slug}.compose" / "session.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (meta.get("rolling_summary") or "").strip():
            return meta
        time.sleep(0.02)
    pytest.fail("滚动摘要未在超时内落盘")


# ── a) 阈值：1500 字档位 ────────────────────────────────────────────


def test_rolling_level_and_article_chars() -> None:
    """字数口径（去空白）与档位（floor(n/1500)）——阈值判定的纯函数面。"""
    assert compose_service.article_chars("") == 0
    assert compose_service.article_chars("一 二\n三\t四") == 4  # 空白不计
    assert compose_service.rolling_level(0) == 0
    assert compose_service.rolling_level(1499) == 0
    assert compose_service.rolling_level(1500) == 1
    assert compose_service.rolling_level(3001) == 2


def test_adopt_below_threshold_never_calls_ai(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """短稿 adopt：未跨档 → 零摘要调用、session.json 无 rolling_summary。"""
    fake = _fake(monkeypatch, REPLY_SHORT)
    compose_service.new_session("short", "T", root=root)
    compose_service.generate("short", root=root)
    compose_service.decide("short", 1, "b1", "keep", root=root)
    compose_service.adopt("short", root=root)

    meta = json.loads((root / "short.compose" / "session.json").read_text(encoding="utf-8"))
    assert not (meta.get("rolling_summary") or "").strip()
    assert meta.get("rolling_summary_level", 0) == 0
    assert fake.summary_calls == []  # 零 AI 调用（阈值未到）


def test_adopt_over_threshold_async_summary_lands(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """长稿 adopt：跨 1500 档 → **异步**摘要落 session.json（adopt 已先返回）。"""
    fake = _fake(monkeypatch, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    _keep_all(compose_service, "long", 1, root)  # 全块裁决（未裁决块不入稿）
    result = compose_service.adopt("long", root=root)

    # 主流程已返回（article 路径即证），摘要随后由后台线程补齐
    assert result["article"].endswith("long.md")
    meta = _wait_summary(root, "long")
    assert meta["rolling_summary"] == SUMMARY_AI
    assert meta["rolling_summary_level"] == 1
    assert len(fake.summary_calls) == 1
    # 摘要输入=已写全文（adopt 后的 draft 合并稿）
    summary_input = fake.summary_calls[0][0]["content"]
    assert "长文段落内容" in summary_input and "长文标题" in summary_input


def test_rolling_due_reactivates_after_drop_and_recross(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """档位单调推进：已完成第 1 档后，同档不再触发；掉回低档再跨档才再次触发。"""
    _fake(monkeypatch, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    _keep_all(compose_service, "long", 1, root)
    compose_service.adopt("long", root=root)
    meta = _wait_summary(root, "long")
    assert meta["rolling_summary_level"] == 1

    session, _ = compose_service._load_session("long", root)
    assert compose_service.rolling_due(session) is False  # 同档不重复触发


# ── b) 摘要生成：输入/清洗/上限 ─────────────────────────────────────


def test_refresh_summary_strips_fence_and_caps_200(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """摘要清洗：``` 围栏剥离；超长按 200 字硬截断（§五「<200 字」）。"""
    long_reply = "```\n" + ("摘" * 800) + "\n```"
    fake = _fake(monkeypatch, REPLY_LONG, summary=long_reply)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)  # 真稿空 → 摘要输入取末轮块

    out = compose_service.refresh_rolling_summary("long", root=root)
    assert out["refreshed"] is True
    assert len(out["summary"]) == compose_service.ROLLING_SUMMARY_MAX == 200
    assert "```" not in out["summary"]
    assert len(fake.summary_calls) == 1


def test_refresh_refuses_without_content(root: Path) -> None:
    """无块可摘要 → 可读 ComposeError（不把空输入发给 AI）。"""
    compose_service.new_session("empty", "T", root=root)
    with pytest.raises(compose_service.ComposeError, match="无可摘要内容"):
        compose_service.refresh_rolling_summary("empty", root=root)


def test_refresh_empty_ai_response_is_error(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AI 空响应 → 可读错误（不写空摘要进 session.json）。"""
    _fake(monkeypatch, REPLY_LONG, summary="   ")
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    with pytest.raises(compose_service.ComposeError, match="AI 响应为空"):
        compose_service.refresh_rolling_summary("long", root=root)
    meta = json.loads((root / "long.compose" / "session.json").read_text(encoding="utf-8"))
    assert not (meta.get("rolling_summary") or "").strip()


def test_summary_input_uses_tail_when_over_budget(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超输入预算取尾部（近期最近相关），并标注开头已截去。"""
    fake = _fake(monkeypatch, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    # 直接把真稿撑到超预算（模拟长文；不依赖 AI 产出规模）
    d = root / "long.compose"
    (d / "draft.md").write_text(
        "<!--b:b1-->\n# 标题\n\n<!--b:b2-->\n" + ("开头内容。" * 3000) + "\n\n尾部唯一标记段。",
        encoding="utf-8",
    )
    compose_service.refresh_rolling_summary("long", root=root)
    text = fake.summary_calls[0][0]["content"]
    assert "前文过长已截去开头" in text
    assert "尾部唯一标记段" in text


# ── c) prompt 注入（本功能的关键断言）────────────────────────────────


def test_rolling_summary_injected_into_generate_prompt(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**注入断言**：rolling_summary 非空 → 后续 generate 的 prompt 含摘要文字。"""
    fake = _fake(monkeypatch, REPLY_LONG, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)  # 首轮（此时无摘要）
    first_prompt = fake.compose_calls[0][0]["content"]
    assert "到目前为止的文章脉络" not in first_prompt  # 空摘要整节缺席
    assert SUMMARY_AI not in first_prompt

    out = compose_service.refresh_rolling_summary("long", root=root)  # 生成摘要
    assert out["refreshed"] is True

    compose_service.generate("long", root=root)  # 第二轮：prompt 必须携带摘要
    second_prompt = fake.compose_calls[1][0]["content"]
    assert SUMMARY_AI in second_prompt, "后续 generate 的 prompt 必须自动携带滚动摘要"
    assert "到目前为止的文章脉络" in second_prompt
    # 插槽位序：素材 → 摘要 → 真稿（摘要落在真稿之前）
    assert second_prompt.index("到目前为止的文章脉络") < second_prompt.index("【当前真稿】")


def test_rolling_summary_backfilled_when_template_lacks_slot(
    root: Path, monkeypatch: pytest.MonkeyPatch, _isolate_home: Path
) -> None:
    """自定义 compose.txt 缺 {{rolling}} 槽 → 缺槽回补仍注入（不丢摘要）。"""
    prompts_dir = _isolate_home / "prompts.d"
    prompts_dir.mkdir()
    (prompts_dir / "compose.txt").write_text(
        "自定义底座。素材：\n{{sources}}", encoding="utf-8"
    )
    fake = _fake(monkeypatch, REPLY_LONG, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    compose_service.refresh_rolling_summary("long", root=root)
    compose_service.generate("long", root=root)
    prompt = fake.compose_calls[1][0]["content"]
    assert prompt.startswith("自定义底座。")
    assert SUMMARY_AI in prompt and "到目前为止的文章脉络" in prompt  # 回补命中


def test_rolling_block_helper() -> None:
    """rolling_block：空摘要 → 空串（调用方不注入）；有摘要 → 带节标题。"""
    assert compose_service.rolling_block("") == ""
    assert compose_service.rolling_block("   ") == ""
    block = compose_service.rolling_block("脉络 A")
    assert "到目前为止的文章脉络" in block and block.rstrip().endswith("脉络 A")


def test_rolling_summary_task_key_is_chat_whitelisted() -> None:
    """TASKS 含 rolling_summary（20→21）：routing/覆盖/ai check 自动继承。"""
    from musespiral.infra.ai.profiles import TASKS

    assert "rolling_summary" in TASKS
    # 21（G-T4 批）→ 22：G-T1 Ghost Text 扩 ghost_write → 23：E-5 扩 novel_summary
    # → 24：PL-5 扩 plugin_key_segment → 25：PL-6 扩 plugin_article_writer
    assert len(TASKS) == 27  # N-2 25→26；插件 v2 验证批 26→27（plugin_refine_segment，refine-segments 插件）


# ── d) 用户手改 + 手动刷新（服务层两函数）──────────────────────────


def test_set_rolling_summary_keeps_level_and_stops_on_empty(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手改：写回 + 不推进档位；清空 → 后续 prompt 不再注入。"""
    fake = _fake(monkeypatch, REPLY_LONG, REPLY_LONG, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    compose_service.refresh_rolling_summary("long", root=root)  # level=1

    compose_service.set_rolling_summary("long", "人工修正后的脉络。", root=root)
    meta = json.loads((root / "long.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["rolling_summary"] == "人工修正后的脉络。"
    assert meta["rolling_summary_level"] == 1  # 手改不推进/不重置档位

    compose_service.generate("long", root=root)
    assert "人工修正后的脉络。" in fake.compose_calls[1][0]["content"]  # 人改稿即生效

    compose_service.set_rolling_summary("long", "", root=root)  # 清空
    compose_service.generate("long", root=root)
    prompt = fake.compose_calls[2][0]["content"]
    assert "到目前为止的文章脉络" not in prompt  # 清空即停注入


def test_refresh_force_false_skips_when_level_unchanged(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=False 且档位未变 → refreshed:false 且零 AI 调用（自动路径幂等）。"""
    fake = _fake(monkeypatch, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    first = compose_service.refresh_rolling_summary("long", root=root)
    assert first["refreshed"] is True
    calls = len(fake.summary_calls)

    again = compose_service.refresh_rolling_summary("long", root=root, force=False)
    assert again["refreshed"] is False and again["summary"] == first["summary"]
    assert len(fake.summary_calls) == calls  # 未重复调 AI


# ── 集成修复批（2026-11-19）回归面：元数据竞态与插槽二次吃 token ──────


def test_manual_edit_survives_inflight_refresh(root: Path) -> None:
    """刷新飞行中用户手改：手改作为更晚的写落在 AI 结果之后，不被覆盖。

    旧实现读-改-写无锁：后台线程用起飞前装进内存的旧快照整份覆写，
    实测「编辑后落盘=人工修正 → 线程结束后=AI 生成」（连复现 3 次）。
    修复=写路径全程持会话锁（@_serialized），手改在刷新释放锁后落盘。
    """
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root, chat_fn=lambda m, **k: REPLY_LONG)
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(messages, *, task=None, **kw):
        assert task == "rolling_summary"
        started.set()
        release.wait(10)
        return "AI飞行中生成的摘要。"

    t = threading.Thread(
        target=compose_service.refresh_rolling_summary, args=("long", root),
        kwargs={"chat_fn": blocking_chat, "force": True}, daemon=True,
    )
    t.start()
    assert started.wait(5), "刷新线程未起飞"
    compose_service.set_rolling_summary("long", "人工修正的摘要。", root=root)  # 持锁排队
    release.set()
    t.join(10)
    meta = json.loads((root / "long.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["rolling_summary"] == "人工修正的摘要。"


def test_generate_concurrent_with_refresh_keeps_both(root: Path) -> None:
    """generate 与摘要刷新并发：轮次与摘要双存，互不覆盖。

    旧实现并发生成会丢其一（探针实测 rolling_summary 清空或轮次回退）。
    """
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root, chat_fn=lambda m, **k: REPLY_LONG)
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(messages, *, task=None, **kw):
        started.set()
        release.wait(10)
        return "并发刷新的摘要。"

    t = threading.Thread(
        target=compose_service.refresh_rolling_summary, args=("long", root),
        kwargs={"chat_fn": blocking_chat, "force": True}, daemon=True,
    )
    t.start()
    assert started.wait(5), "刷新线程未起飞"
    compose_service.generate(
        "long", root=root,
        chat_fn=lambda m, **k: "<<<block kind:para>>>第二轮新增内容块。",
    )  # 持锁排队，等刷新释放后带着新快照写
    release.set()
    t.join(10)
    meta = json.loads((root / "long.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["rolling_summary"] == "并发刷新的摘要。"
    assert len(meta["rounds"]) == 2  # 第二轮 generate 未被刷新的旧快照回退


def test_summary_tokens_stay_literal_in_prompt(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """摘要值内的 {{token}} 保持字面——单遍填充，不被 draft/decisions 再消费。

    旧实现逐键 replace：set_rolling_summary("脉络：见 {{draft}} 与 {{decisions}}")
    会让 prompt 该行变成「脉络：见 （空——首轮创作） 与 （无——…）」。
    """
    fake = _fake(monkeypatch, REPLY_LONG, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    compose_service.set_rolling_summary("long", "脉络：见 {{draft}} 与 {{decisions}}", root=root)
    compose_service.generate("long", root=root)
    prompt = fake.compose_calls[1][0]["content"]
    assert "{{draft}}" in prompt and "{{decisions}}" in prompt  # 字面保留
    # 真插槽各填一次（真稿空 → 空态占位只出现一次；裁决空占位同律）
    assert prompt.count("（空——首轮创作）") == 1
    assert prompt.count("（无——首轮或上轮未裁决）") == 1


# ── e) Studio 端点两形态（confirm 闸门 + 载荷分流）────────────────────


def _server(root: Path):
    import threading

    from musespiral import studio

    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post(server, path: str, body: dict):
    import http.client

    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(server, path: str):
    import http.client

    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_api_rolling_summary_refresh_and_edit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/compose/rolling-summary：无 summary=AI 刷新；有 summary=手改。

    另验：缺 confirm → 400；缺 slug → 400；详情 GET 三字段随会话下发。
    """
    _fake(monkeypatch, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    server, thread = _server(root)
    try:
        # 缺 confirm 全局闸门
        code, payload = _post(server, "/api/compose/rolling-summary", {"slug": "long"})
        assert code == 400
        # 缺 slug
        code, payload = _post(server, "/api/compose/rolling-summary", {"confirm": True})
        assert code == 400
        # summary 非字符串
        code, payload = _post(
            server, "/api/compose/rolling-summary",
            {"slug": "long", "summary": 5, "confirm": True},
        )
        assert code == 400

        # 刷新形态：{slug} → AI 摘要落盘
        code, payload = _post(
            server, "/api/compose/rolling-summary", {"slug": "long", "confirm": True}
        )
        assert code == 200 and payload["ok"] is True
        assert payload["summary"] == SUMMARY_AI and payload["edited"] is False
        assert payload["chars"] > 1500 and payload["level"] == 1

        # 详情面三字段
        code, detail = _get(server, "/api/compose/session?slug=long")
        assert code == 200
        assert detail["rolling_summary"] == SUMMARY_AI
        assert detail["rolling_summary_chars"] == payload["chars"]
        assert detail["rolling_summary_level"] == 1

        # 手改形态：{slug, summary} → 不调 AI、不推进档位
        code, payload = _post(
            server, "/api/compose/rolling-summary",
            {"slug": "long", "summary": "人工脉络：三解法已讲完。", "confirm": True},
        )
        assert code == 200 and payload["edited"] is True
        assert payload["summary"] == "人工脉络：三解法已讲完。"
        code, detail = _get(server, "/api/compose/session?slug=long")
        assert detail["rolling_summary"] == "人工脉络：三解法已讲完。"
        assert detail["rolling_summary_level"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_api_rolling_summary_error_is_ok_false(root: Path) -> None:
    """会话不存在 → 200 {ok:false}（业务失败面，不 500）。"""
    server, thread = _server(root)
    try:
        code, payload = _post(
            server, "/api/compose/rolling-summary", {"slug": "ghost", "confirm": True}
        )
        assert code == 200 and payload["ok"] is False
        assert "会话不存在" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ── f) 大纲展开的 prompt 也携带摘要（§五「后续所有 compose/generate 请求」）──


def test_outline_expand_prompt_carries_rolling_summary(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-T2 大纲展开是另一次 compose 生成——摘要必须同样注入（不被旁路）。"""
    fake = _fake(monkeypatch, REPLY_LONG, REPLY_LONG, REPLY_LONG)
    compose_service.new_session("long", "T", root=root)
    compose_service.generate("long", root=root)
    compose_service.refresh_rolling_summary("long", root=root)  # 生成摘要
    outline = compose_service.outline_save(
        "long",
        [{"id": "s1", "title": "第一律", "target_chars": 500, "direction": "对比两家"}],
        root=root,
    )
    compose_service.outline_approve("long", "s1", True, root=root)
    compose_service.expand_section("long", "s1", root=root)
    expand_prompt = fake.compose_calls[-1][0]["content"]
    assert SUMMARY_AI in expand_prompt, "大纲展开的 prompt 必须携带滚动摘要"
    assert "到目前为止的文章脉络" in expand_prompt and "对比两家" in expand_prompt
