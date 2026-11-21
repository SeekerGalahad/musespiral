"""治理网关（§五 F79-2 命中零网络零预算 / F79-3 超限 / F79-5 无敏感 / F79-6 clear
/ F79-7 真机冒烟）。

五段序是本节的全部被测对象：① 查缓存 → ② 预算 → ③ chat → ④ 记账 → ⑤ 写缓存。
顺序错了这里就红（例如把预算放到缓存前面 → 「命中仍成功」用例挂）。
"""
from __future__ import annotations

import json

import pytest

from musespiral.infra.ai import budget, cache, gateway, usage
from musespiral.infra.ai.gateway import BudgetExceeded, chat_policed


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    (home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {
                "local": {"base_url": "http://127.0.0.1:3425/v1", "api_key": "k-local", "model": "m-local"},
                "cloud": {"base_url": "https://cloud/v1", "api_key": "k-cloud", "model": "m-cloud"},
            },
            "routing": {"agent_extract": "cloud"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return home


class Spy:
    """记录每次调用、返回预设文本的假 chat（依赖注入点，不 monkeypatch 全局）。"""

    def __init__(self, text="响应正文", exc=None):
        self.text = text
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, messages, *, task, temperature, timeout, profile):
        self.calls.append({"task": task, "temperature": temperature, "profile": profile,
                           "messages": messages})
        if self.exc is not None:
            raise self.exc
        return self.text


MSGS = [{"role": "user", "content": "请给标签"}]


# ── F79-2：命中零网络零预算 ────────────────────────────────────────


def test_second_call_hits_cache_and_skips_chat(env):
    spy = Spy("TAG: 甲")
    r1 = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    r2 = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r1["cache_hit"] is False and r2["cache_hit"] is True
    assert len(spy.calls) == 1, "命中不得再调 chat"
    assert r1["text"] == r2["text"] == "TAG: 甲"


def test_usage_two_rows_miss_then_hit(env):
    spy = Spy("out")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    rows = usage.read_all()
    assert [r["cache_hit"] for r in rows] == [False, True]
    assert rows[0]["chars_out"] == len("out")
    assert rows[1]["chars_out"] == len("out")  # 命中行也记计量（只是不出账）


def test_hit_succeeds_even_at_budget_limit(env):
    """**bucket 在 budget 之后**的直接证明：daily_calls=1 时命中仍成功。"""
    spy = Spy("out")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    budget.save({"daily_calls": 1, "daily_out_chars": 200_000, "per_task": {}})
    r = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r["cache_hit"] is True, "命中路径不得被预算拦（cache 在 budget 前）"
    assert len(spy.calls) == 1


def test_cache_false_always_calls_chat(env):
    """cache=False（compose/rewrite/annotate 类）恒不命中、恒不写缓存。"""
    spy = Spy("生成正文")
    r1 = chat_policed(MSGS, task="compose", cache=False, chat_fn=spy, now="2026-11-17T10:00:00")
    r2 = chat_policed(MSGS, task="compose", cache=False, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r1["cache_hit"] is False and r2["cache_hit"] is False
    assert len(spy.calls) == 2
    assert cache.stats()["entries"] == 0, "非 cacheable 调用点不得写缓存"


def test_prompt_change_invalidates_cache(env):
    spy = Spy("v")
    chat_policed([{"role": "user", "content": "A"}], task="tag_suggest",
                 cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    r = chat_policed([{"role": "user", "content": "B"}], task="tag_suggest",
                     cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r["cache_hit"] is False and len(spy.calls) == 2


def test_profile_route_change_invalidates_cache(env):
    """换档案=新 key（旧模型产出不会被新模型误读）。"""
    spy = Spy("v")
    chat_policed(MSGS, task="agent_extract", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    r = chat_policed(MSGS, task="agent_extract", cache=True, profile="local",
                     chat_fn=spy, now="2026-11-17T10:01:00")
    assert r["cache_hit"] is False


def test_template_override_change_invalidates_cache(env):
    """prompts.d 覆盖文件一改，该 task 全部缓存失效（template_sha 进 key）。"""
    from musespiral.infra.ai import prompts

    spy = Spy("v")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    prompts.save_override("tag_suggest", "新的任务底座模板")
    r = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r["cache_hit"] is False and len(spy.calls) == 2


# ── F79-3：预算超限 ────────────────────────────────────────────────


def test_second_miss_raises_budget_exceeded_with_readable_message(env):
    budget.save({"daily_calls": 1, "daily_out_chars": 200_000, "per_task": {}})
    spy = Spy("a")
    chat_policed(MSGS, task="tag_suggest", cache=False, chat_fn=spy, now="2026-11-17T10:00:00")
    with pytest.raises(BudgetExceeded) as ei:
        chat_policed(MSGS, task="tag_suggest", cache=False, chat_fn=spy, now="2026-11-17T10:01:00")
    msg = str(ei.value)
    assert "tag_suggest" in msg and "今日已用 1" in msg and "上限 1" in msg
    assert "musespiral ai budget" in msg
    assert len(spy.calls) == 1, "超限时不得调 chat"


def test_budget_exceeded_is_ai_error_subclass():
    """AIError 族（既有脱敏面复用；非崩溃）。"""
    from musespiral.infra.ai import AIError

    assert issubclass(BudgetExceeded, AIError)


def test_budget_exceeded_carries_duck_type_flag():
    """跨层协议位：core/agent/interp 靠这个属性（不 isinstance）识别限额态。"""
    assert getattr(BudgetExceeded, "budget_exceeded", False) is True


def test_per_task_override_gates_synthesize_only(env):
    """F79-3：per_task.agent_synthesize.daily_calls=1 → 第二次 synthesize 拒，
    tag_suggest 不受影响。"""
    budget.save({"daily_calls": 200, "daily_out_chars": 200_000,
                 "per_task": {"agent_synthesize": {"daily_calls": 1}}})
    spy = Spy("a")
    chat_policed(MSGS, task="agent_synthesize", cache=False, chat_fn=spy, now="2026-11-17T10:00:00")
    with pytest.raises(BudgetExceeded):
        chat_policed(MSGS, task="agent_synthesize", cache=False, chat_fn=spy, now="2026-11-17T10:01:00")
    # 同日 tag_suggest 不受影响
    r = chat_policed(MSGS, task="tag_suggest", cache=False, chat_fn=spy, now="2026-11-17T10:02:00")
    assert r["cache_hit"] is False


def test_daily_out_chars_limit_enforced(env):
    budget.save({"daily_calls": 200, "daily_out_chars": 5, "per_task": {}})
    spy = Spy("0123456789")  # 10 字符 > 5
    chat_policed(MSGS, task="t", cache=False, chat_fn=spy, now="2026-11-17T10:00:00")
    with pytest.raises(BudgetExceeded):
        chat_policed(MSGS, task="t", cache=False, chat_fn=spy, now="2026-11-17T10:01:00")


# ── 返回形状 ───────────────────────────────────────────────────────


def test_return_shape(env):
    spy = Spy("abc")
    r = chat_policed([{"role": "user", "content": "12"}], task="t", chat_fn=spy,
                     now="2026-11-17T10:00:00")
    assert set(r) == {"text", "cache_hit", "chars_in", "chars_out"}
    assert r["chars_in"] == 2 and r["chars_out"] == 3


def test_chars_in_sums_all_message_contents(env):
    spy = Spy("o")
    r = chat_policed(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}],
        task="t", chat_fn=spy, now="2026-11-17T10:00:00",
    )
    assert r["chars_in"] == 3 + 4


def test_non_str_return_raises_ai_error(env):
    from musespiral.infra.ai import AIError

    def bad(messages, *, task, temperature, timeout, profile):
        return None

    with pytest.raises(AIError, match="非字符串返回"):
        chat_policed(MSGS, task="t", chat_fn=bad, now="2026-11-17T10:00:00")


def test_chat_fn_exception_propagates_and_no_usage_row(env):
    """chat 失败 → 异常透传（AIError 族），且**不记出账行**（没花到钱）。"""
    spy = Spy(exc=RuntimeError("network down"))
    with pytest.raises(RuntimeError, match="network down"):
        chat_policed(MSGS, task="t", chat_fn=spy, now="2026-11-17T10:00:00")
    assert usage.read_all() == [], "失败调用不得记出账"


def test_run_id_flows_into_usage(env):
    spy = Spy("o")
    chat_policed(MSGS, task="t", chat_fn=spy, run_id="run-42", now="2026-11-17T10:00:00")
    assert usage.read_all()[0]["run_id"] == "run-42"


def test_run_id_absent_when_not_given(env):
    spy = Spy("o")
    chat_policed(MSGS, task="t", chat_fn=spy, now="2026-11-17T10:00:00")
    assert "run_id" not in usage.read_all()[0]


def test_profile_resolved_from_routing(env):
    """档案名按 task 路由解析（agent_extract → cloud）。"""
    spy = Spy("o")
    chat_policed(MSGS, task="agent_extract", chat_fn=spy, now="2026-11-17T10:00:00")
    rows = usage.read_all()
    assert rows[0]["profile"] == "cloud"


# ── F79-5：治理文件无敏感（canary 全链路）───────────────────────────


def test_canary_key_never_in_cache_or_usage(env):
    """注入 canary api_key 后，cache/*.json 与 ai_usage.jsonl 零命中。"""
    canary = "sk-CANARY-DO-NOT-PERSIST-777"
    home = env
    (home / "ai.json").write_text(
        json.dumps({"default": "local",
                    "profiles": {"local": {"base_url": "http://x/v1", "api_key": canary, "model": "m"}}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    spy = Spy("输出正文 CANARY-OUT")
    chat_policed([{"role": "user", "content": "CANARY-PROMPT"}], task="tag_suggest",
                 cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    chat_policed([{"role": "user", "content": "CANARY-PROMPT"}], task="tag_suggest",
                 cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    blobs = []
    for p in cache.cache_dir().glob("*.json") if cache.cache_dir().is_dir() else []:
        blobs.append(p.read_text(encoding="utf-8"))
    blobs.append(usage.usage_path().read_text(encoding="utf-8"))
    joined = "\n".join(blobs)
    assert canary not in joined, "缓存/台账不得含 api_key"
    assert "CANARY-PROMPT" not in joined, "缓存/台账不得含 prompt 正文"


def test_cache_entry_schema_has_no_prompt_key(env):
    spy = Spy("o")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    files = list(cache.cache_dir().glob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(entry) == set(cache.ENTRY_KEYS)
    for forbidden in ("prompt", "messages", "api_key"):
        assert forbidden not in entry


# ── F79-6：--clear 零副作用（重建=重调 chat，产出相等）─────────────


def test_clear_then_recompute_yields_same_text(env):
    spy = Spy("稳定输出")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:00:00")
    assert cache.clear() == 1
    r = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy, now="2026-11-17T10:01:00")
    assert r["cache_hit"] is False
    assert r["text"] == "稳定输出"
    assert len(spy.calls) == 2, "清空后应重调 chat（mock 计数 +1）"


# ── 分层纪律自证（D2/D3 的行为侧对拍）────────────────────────────


def test_gateway_module_does_not_import_services_or_core():
    """运行时反证（架构门 D2 的 AST 版在 test_architecture；此处断言模块本身干净）。"""
    import musespiral.infra.ai.gateway as gw

    src = gw.__file__
    text = open(src, encoding="utf-8").read()
    for bad in ("from ...services", "from ...core", "from ...studio",
                "import musespiral.services", "import musespiral.core"):
        assert bad not in text, f"gateway 不得 import 上层: {bad}"


# ── F79-7：真机冒烟（可 skip 不阻塞合入，§八-7）─────────────────────

# 本地网关（契约口径：profiles.magpie @127.0.0.1:3425）
_GATEWAY = ("127.0.0.1", 3425)
# 网关可路由的模型（实测 /v1/models 首个稳定项；404 消息建议的形态一致）
_GATEWAY_MODEL = "group/group"


def _gateway_reachable() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        try:
            s.connect(_GATEWAY)
        except OSError:
            return False
    return True


@pytest.mark.e2e
def test_smoke_real_profile_optional(env):
    """M4 真机：本地网关实调一次 tag_suggest。网关/模型不可用即 skip（不失败）。

    契约的闸门语义：**环境缺失 → skip**（绝不记成产品缺陷）；网关在且模型
    可路由时才真正断言产出与台账。首版此用例曾用 fixture 的占位模型名
    （`m-local`），结果「网关在、模型名假」也走 skip——那会把真机验证悄悄
    变成永不执行的空壳。改为网关可达即用真实可路由模型实调。
    """
    if not _gateway_reachable():
        pytest.skip("本地网关 127.0.0.1:3425 不可达——真机冒烟跳过（§八-7）")

    (env / "ai.json").write_text(
        json.dumps({
            "default": "magpie",
            "profiles": {"magpie": {
                "base_url": f"http://{_GATEWAY[0]}:{_GATEWAY[1]}/v1",
                "api_key": "", "model": _GATEWAY_MODEL,
            }},
            "routing": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        r = chat_policed(
            [{"role": "user", "content": "只回复两个字：可以"}],
            task="tag_suggest", cache=False, timeout=90, now="2026-11-17T10:00:00",
        )
    except Exception as exc:  # noqa: BLE001 - 真机依赖项，失败不阻塞
        pytest.skip(f"真机调用失败（不阻塞合入）: {str(exc)[:160]}")
    assert isinstance(r["text"], str) and r["text"].strip()
    assert r["cache_hit"] is False
    rows = usage.read_all()
    assert rows, "真机调用应留下至少一行台账"
    assert rows[-1]["task"] == "tag_suggest" and rows[-1]["cache_hit"] is False


# ── jobs KINDS 扩键锚（M1 出口判据：diff 含 KINDS 两行枚举）───────────


def test_jobs_kinds_include_agent_run_and_topic_radar():
    """Phase D 两个新 job kind 已入白名单（设计 §1.1/§四-Q2 与契约 §3.2 同步项）。

    M1 只扩枚举不建服务（agent_service/topic_radar_service 归 M2/M3）；此处
    锚定枚举就位，防止后续实现阶段忘了注册（`JobStore.create` 会以「未知
    kind」拒掉提交）。
    """
    from musespiral.jobs.store import KINDS

    assert "agent_run" in KINDS
    assert "topic_radar" in KINDS
    # 存量 12 项仍在（只增不减）
    for kind in ("adapt", "pack", "publish", "batch_adapt", "status",
                 "annotate_rewrite", "fetch", "sync_stats", "compose_generate",
                 "ingest", "clip", "visual_export"):
        assert kind in KINDS


# ── 复核修复批（2026-11-17）：template 参=生效模板（F79.1 口径修正）──────


def test_template_param_participates_in_cache_key(env):
    """显式 template 参与五元组：同 prompt 不同 template → 不同 key（miss）；
    同 template 二次 → 命中（gateway 不再只哈希覆盖文件——内置模板变更也能
    使旧缓存失效）。"""
    spy = Spy("TAG: a")
    r1 = chat_policed(MSGS, task="tag_suggest", cache=True, template="内置模板甲",
                      chat_fn=spy, now="2026-11-17T10:00:00")
    r2 = chat_policed(MSGS, task="tag_suggest", cache=True, template="内置模板甲",
                      chat_fn=spy, now="2026-11-17T10:01:00")
    assert r1["cache_hit"] is False and r2["cache_hit"] is True
    assert len(spy.calls) == 1
    r3 = chat_policed(MSGS, task="tag_suggest", cache=True, template="内置模板乙",
                      chat_fn=spy, now="2026-11-17T10:02:00")
    assert r3["cache_hit"] is False, "内置模板（经 template 参）变更必须使缓存失效"
    assert len(spy.calls) == 2


def test_template_none_falls_back_to_override_only(env):
    """缺省 None 保持历史口径：template_sha 只吃覆盖文件（既有消费方回归锚）。"""
    from musespiral.infra.ai.prompts import save_override

    spy = Spy("TAG: a")
    chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy,
                 now="2026-11-17T10:00:00")
    save_override("tag_suggest", "覆盖模板")
    r2 = chat_policed(MSGS, task="tag_suggest", cache=True, chat_fn=spy,
                      now="2026-11-17T10:01:00")
    assert r2["cache_hit"] is False, "覆盖文件变更仍使 key 变（None 回退口径不变）"
