"""E-3 编辑器 AI 微操作测试（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》
§3.3；2026-11-19 落地批）。

四层断言（沿 test_editor_ui / test_tag_service 的既有惯例）：

  a) 服务层（真实 gateway + 注入假件）：五个动作各自路由到正确的 task 键、
     prompt 含选中文字与上下文、围栏剥离、空响应转 AIError、长度上限、
     覆盖级联（prompts.d/<task>.txt）与生效模板透传（F79.1 缓存失效口径）。
  b) API 层（真实回环服务器 + http.client）：confirm 闸门 / action 枚举外 400 /
     text 缺失 400 / 金路径 200 {ok,action,task,gen,original} / AI 未配置
     200 {ok:false} / **零落盘零 job**。
  c) TASKS 扩键 + 方言守门：五键在 TASKS 内、五键都是 chat 键（与
     image_generate 的反向护栏成对）、模板无模型/供应商方言。
  d) 前端（node 真实执行 ED 哨兵段 + 结构断言）：浮出阈值 >10 字去空白 /
     上下文段落对齐 / 写回定位三态 / `<!--ai:block-->` 包裹 / diff 红删绿增
     行 / 关闭与接线的结构锚（5 按钮、modal overlay、fetch 端点、replaceRange）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra.ai import gateway as gateway_mod
from musespiral.infra.ai.profiles import TASKS
from musespiral.services import editor_ai_service as svc

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
EDITOR_JS = STUDIO / "assets" / "editor.js"
EDITOR_CSS = STUDIO / "assets" / "editor.css"
ED_BEGIN = "/*__ED_BEGIN__*/"
ED_END = "/*__ED_END__*/"

CANARY_KEY = "sk-e3-canary-3f9a2"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """本地无 key 档案（五键未配路由 → 全走 default）。"""
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return _isolate_home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


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
    import http.client

    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
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


def _fake_chat(reply: str = "AI 产出正文", calls: list | None = None):
    """gateway 形态假件（签名=client.chat；经 chat_fn / _real_chat 注入）。"""
    calls = calls if calls is not None else []

    def fake(messages, *, task, temperature=0.6, timeout=120, profile=None):
        calls.append({"task": task, "temperature": temperature, "content": messages[0]["content"]})
        return reply

    return fake, calls


# ══ a) 服务层：五动作 → 五 task 键 ═══════════════════════════════════

_ACTIONS = ["expand", "summarize", "rewrite", "continue", "qa"]


@pytest.mark.parametrize("action", _ACTIONS)
def test_each_action_routes_to_its_own_task(action, ai_on, monkeypatch):
    """五动作 → 五 task 键一一对应（action→task 映射表是唯一事实源）。"""
    fake, calls = _fake_chat("产出")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    out = svc.run_action(action, "这是一段足够长的选中文字，用于测试动作路由。")
    assert out["action"] == action
    assert out["task"] == svc.EDITOR_ACTIONS[action]
    assert out["gen"] == "产出"
    assert len(calls) == 1
    assert calls[0]["task"] == svc.EDITOR_ACTIONS[action], "任务键必须与动作映射一致"


def test_five_actions_have_distinct_tasks(ai_on):
    """五动作不得共用任务键（否则 routing 无法按动作路由）。"""
    tasks = list(svc.EDITOR_ACTIONS.values())
    assert len(tasks) == len(set(tasks)) == 5
    assert set(svc.EDITOR_ACTIONS) == set(_ACTIONS)


def test_prompt_contains_text_and_context(ai_on, monkeypatch):
    """选中文字必进 prompt；上下文（选填）也进；两者都不得被截断丢内容。"""
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_action("rewrite", "选中的那一句话。", "前面的段落。后面的段落。")
    prompt = calls[0]["content"]
    assert "选中的那一句话。" in prompt
    assert "前面的段落。后面的段落。" in prompt
    assert "【上下文】" in prompt and "【选中文字】" in prompt


def test_prompt_without_context_has_no_context_block(ai_on, monkeypatch):
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_action("summarize", "一段要压缩的文字。")
    prompt = calls[0]["content"]
    assert "【选中文字】" in prompt
    assert "【上下文】" not in prompt


def test_code_fence_stripped_and_blank_response_rejected(ai_on, monkeypatch):
    """AI 爱包 ``` 围栏（既有 rewrite/annotate 同款处理）；空响应转 AIError。"""
    from musespiral.infra.ai import AIError

    fake, _ = _fake_chat("```markdown\n围栏内正文\n```")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    assert svc.run_action("expand", "一句话。")["gen"] == "围栏内正文"

    blank, _ = _fake_chat("   \n  ")
    monkeypatch.setattr(gateway_mod, "_real_chat", blank)
    with pytest.raises(AIError) as exc:
        svc.run_action("expand", "一句话。")
    assert "空响应" in str(exc.value)


def test_empty_and_oversize_text_rejected(ai_on):
    """空/纯空白选中、超长选中 → EditorAIError（可读拒绝，不打网络）。"""
    for bad in ("", "   \n\t "):
        with pytest.raises(svc.EditorAIError) as exc:
            svc.run_action("expand", bad)
        assert "不能为空" in str(exc.value)
    with pytest.raises(svc.EditorAIError) as exc:
        svc.run_action("expand", "字" * (svc.TEXT_MAX_CHARS + 1))
    assert "过长" in str(exc.value)


def test_unknown_action_rejected(ai_on):
    with pytest.raises(svc.EditorAIError) as exc:
        svc.run_action("summarise", "拼错的动作名。")
    assert "未知动作" in str(exc.value)


def test_context_truncated_to_budget(ai_on, monkeypatch):
    """上下文超预算 → 截断（不拒，上下文本就是辅助信息）。"""
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_action("rewrite", "选中。", "上" * (svc.CONTEXT_MAX_CHARS + 500))
    prompt = calls[0]["content"]
    assert len(prompt) < svc.CONTEXT_MAX_CHARS + 2000, "上下文应被截到预算内"


def test_prompt_override_cascade(ai_on, monkeypatch):
    """prompts.d/<task>.txt 整体覆盖内置缺省（F47 级联同纪律）。"""
    from musespiral.infra.ai.prompts import save_override

    save_override("editor_rewrite", "覆盖模板：请改写下面这段。\n{{text}}")
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_action("rewrite", "要改写的话。")
    prompt = calls[0]["content"]
    assert "覆盖模板" in prompt
    assert "要改写的话。" in prompt, "{{text}} 槽位应原位替换"
    assert svc.DEFAULT_PROMPTS["editor_rewrite"].splitlines()[0] not in prompt, "内置缺省应被整体覆盖"


def test_builtin_template_change_invalidates_cache_key(ai_on):
    """生效模板透传 gateway template 参（F79.1）：内置模板变 → key 变。"""
    from musespiral.infra.ai import cache as cache_mod

    p1, t1 = svc.build_prompt("expand", "同样一段文字。")
    orig = svc.DEFAULT_PROMPTS["editor_expand"]
    svc.DEFAULT_PROMPTS["editor_expand"] = orig + "\n补充一行新要求。"
    try:
        p2, t2 = svc.build_prompt("expand", "同样一段文字。")
    finally:
        svc.DEFAULT_PROMPTS["editor_expand"] = orig
    assert t1 != t2, "生效模板文本必须随内置缺省变化"
    assert cache_mod.template_sha(t1) != cache_mod.template_sha(t2)
    assert p1 != p2


# ══ b) API 层：契约形状 + confirm 闸门 + 零落盘 ═════════════════════


def test_api_contract_shape_and_confirm_gate(server, ai_on, monkeypatch):
    """锁面相等 + confirm 闸门 + 400/200 分档 + 响应键集。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.server import _BINARY_POST_ROUTES

    from tests.test_architecture import CONTRACT_POST_EXACT

    fake, _calls = _fake_chat("AI 改好的句子。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)

    assert "/api/editor/ai-action" in _ACTION_ROUTES
    assert "/api/editor/ai-action" in CONTRACT_POST_EXACT
    assert (set(_ACTION_ROUTES) | set(_BINARY_POST_ROUTES)) == CONTRACT_POST_EXACT

    # confirm 闸门（缺省 → 400，文案同全局）
    code, err = _request(server, "POST", "/api/editor/ai-action",
                         {"action": "expand", "text": "一段文字。"})
    assert code == 400 and "confirm:true" in err["error"]

    # action 枚举外 → 400 可读（列出五个合法值）
    code, err = _request(server, "POST", "/api/editor/ai-action",
                         {"confirm": True, "action": "translate", "text": "一段文字。"})
    assert code == 400 and "expand/summarize/rewrite/continue/qa" in err["error"]

    # text 缺失 → 400（_require_str 统一文案）
    code, err = _request(server, "POST", "/api/editor/ai-action",
                         {"confirm": True, "action": "expand"})
    assert code == 400 and "text" in err["error"]

    # context 非字符串 → 400
    code, err = _request(server, "POST", "/api/editor/ai-action",
                         {"confirm": True, "action": "expand", "text": "一段文字。", "context": 42})
    assert code == 400 and "context" in err["error"]

    # 金路径 200：{ok, original, action, task, gen}——original 回显原文
    code, payload = _request(server, "POST", "/api/editor/ai-action",
                             {"confirm": True, "action": "expand", "text": "原文一句。"})
    assert code == 200
    assert payload == {
        "ok": True, "original": "原文一句。",
        "action": "expand", "task": "editor_expand", "gen": "AI 改好的句子。",
    }


def test_api_zero_write_and_zero_job(server, studio_root, ai_on, monkeypatch):
    """同步形态：不建 job、不落任何文件（拒绝即消失不留痕）。"""
    fake, _ = _fake_chat("产出。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    before = sorted(p.name for p in studio_root.rglob("*"))
    code, _ = _request(server, "POST", "/api/editor/ai-action",
                       {"confirm": True, "action": "summarize", "text": "一段文字。"})
    assert code == 200
    after = sorted(p.name for p in studio_root.rglob("*"))
    assert after == before, "编辑器微操作不得写工作目录"
    assert not (ai_on / "jobs.jsonl").exists(), "同步形态不得建 job（job result 即留痕）"


def test_api_ai_unconfigured_returns_ok_false(server, studio_root):
    """AI 未配置 → 200 {ok:false} 可读降级（同 annotate_rewrite 面，不 4xx）。"""
    code, payload = _request(server, "POST", "/api/editor/ai-action",
                             {"confirm": True, "action": "qa", "text": "一段文字。"})
    assert code == 200 and payload["ok"] is False
    assert "AI 未配置" in payload["error"]


def test_api_error_text_is_sanitized(server, ai_on, monkeypatch):
    """AI 失败 → 200 {ok:false}；错误文本走脱敏管道（secret 打码 + 越界路径打码）。

    命中面 = security._SECRET_RE 登记的键名（access_token/appid/secret/
    api_key/token + `=`）。探针用 `api_key=`（真实 AIError 文案的形态）。
    """
    from musespiral.infra.ai import AIError

    def boom(messages, *, task, temperature=0.6, timeout=120, profile=None):
        raise AIError(f"上游失败 api_key={CANARY_KEY} 路径=/etc/passwd")

    monkeypatch.setattr(gateway_mod, "_real_chat", boom)
    code, payload = _request(server, "POST", "/api/editor/ai-action",
                             {"confirm": True, "action": "expand", "text": "一段文字。"})
    assert code == 200 and payload["ok"] is False
    assert CANARY_KEY not in json.dumps(payload, ensure_ascii=False), "密钥不得回显"
    assert "/etc/passwd" not in payload["error"], "root 外路径应打码"
    assert "***" in payload["error"]


# ══ c) TASKS 扩键 + 方言守门 ════════════════════════════════════════


def test_tasks_contains_five_editor_keys():
    """五键在 TASKS 白名单内（routing/覆盖/ai check/per_task 预算自动继承）。"""
    expected = {"editor_expand", "editor_summarize", "editor_rewrite",
                "editor_continue", "editor_qa"}
    assert expected <= set(TASKS)
    # 21→22：G-T1 Ghost Text 扩 ghost_write（2026-11-19，调研吸收 §二）——
    # 本断言是 TASKS 全量的计数锁，扩键必须在此显式登记新总数。
    assert len(TASKS) == 26, (
        "15 → 20（E-3 批 +5）→ 21（G-T4 +1 rolling_summary）→ 22（G-T1 +1 ghost_write）"
        " → 23（E-5 +1 novel_summary）→ 24（PL-5 +1 plugin_key_segment，key-segment 插件）"
        " → 25（PL-6 +1 plugin_article_writer，article-writer 插件）"
        " → 26（N-2 +1 material_distill，素材闭环·路径 C 论点提炼）"
    )


def test_tasks_pollution_regression_image_generate_still_excluded():
    """TASKS 污染判例回归（G1 2026-11-17）：扩键不得把非 chat 键带进来。

    image_generate 走 /images/generations 不经 chat()——进 TASKS 会让 ai check
    用 chat 端点探测图像任务、routing 把图像任务路由到 chat 档案。本批扩五键
    后该护栏必须仍然成立（与 test_media_gen 的同名断言互为双保险）。
    """
    assert "image_generate" not in TASKS
    # 五键都必须是 chat 键：run_action 真走 chat_policed（非 media 端点）
    assert set(svc.EDITOR_ACTIONS.values()) <= set(TASKS)


def test_editor_service_prompt_dialect_free():
    """方言守门：本批模板零模型/供应商字样（扫描面已登记本文件）。"""
    from tests.test_architecture import dialect_violations

    viol = dialect_violations("services/editor_ai_service.py", Path(svc.__file__).read_text(encoding="utf-8"))
    assert viol == [], viol


# ══ d) 前端：node 行为断言 + 结构锚 ═════════════════════════════════

_SEG = None


def _segment() -> str:
    global _SEG
    if _SEG is None:
        src = EDITOR_JS.read_text(encoding="utf-8")
        assert ED_BEGIN in src and ED_END in src, "editor.js 缺少 ED 哨兵段"
        _SEG = src.split(ED_BEGIN)[1].split(ED_END)[0]
    return _SEG


def _run_ed(script_body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行")
    script = (
        "'use strict';\n" + _segment() + "\n"
        "module.exports={edAIMenuOn:edAIMenuOn,edCtx:edCtx,edPlace:edPlace,"
        "edAccept:edAccept,edReject:edReject,"
        "edAIMark:edAIMark,edAIMarkCount:edAIMarkCount,edDiffRows:edDiffRows,"
        "edDiffStat:edDiffStat,edCount:edCount};\n"
        + script_body
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "e3_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_ed_menu_threshold_ten_chars():
    """浮出阈值：>10 字（去空白口径）；10 字不浮、11 字浮、纯空白不浮。"""
    out = _run_ed(r"""
var r={};
r['10']=edAIMenuOn('1234567890');
r['11']=edAIMenuOn('12345678901');
r.ws=edAIMenuOn('           ');
r.spaced=edAIMenuOn('  一段文字一二三四五六七八九  ');
r.short=edAIMenuOn('  一段文字  ');
r.empty=edAIMenuOn('');
console.log(JSON.stringify(r));
""")
    # spaced 去空白后 11 字（一 + 段文字一二三四五六七八九）→ 浮出
    assert out == {"10": False, "11": True, "ws": False, "spaced": True,
                   "short": False, "empty": False}


def test_ed_ctx_clips_at_paragraph_boundary():
    """上下文按段落边界截取：只取选区周边的段落内文字，不整段搬运。"""
    out = _run_ed(r"""
// 选中段落中间的 4 字：上下文应只剩同段前后文（首段尾 + 末段头都被段落空行切掉）
var t='首段\n\n甲选中文字乙\n\n末段';
var c=edCtx(t,5,9,100);
// 选中整段：同段无周边文字 → 上下文为空（不跨段搬运）
var whole=edCtx('一二三\n\n选区文字\n\n四五六',5,9,100);
var long=edCtx('甲'.repeat(2000)+'选中'+'乙'.repeat(2000),2000,2002,100);
console.log(JSON.stringify({para:c,whole:whole,max:long.length,noSel:long.indexOf('选中')<0}));
""")
    assert "选区文字" not in out["para"], "选区本身不进上下文（单独以【选中文字】传）"
    assert out["para"].startswith("甲") and out["para"].endswith("乙"), (
        f"应只剩同段前后文（首段/末段被空行切掉），实际 {out['para']!r}")
    assert "首段" not in out["para"] and "末段" not in out["para"], "跨段文字不得进上下文"
    assert out["whole"] == "", f"整段选中时同段无周边文字，实际 {out['whole']!r}"
    assert out["max"] <= 210, f"上下文应被 max 界住，实际 {out['max']}"
    assert out["noSel"] is True, "上下文函数不应把选区文本再拼回来"


def test_ed_place_three_states():
    """写回定位三态：索引命中 / 索引失效但选区仍匹配 / 两者都不符（拒写）。"""
    out = _run_ed(r"""
console.log(JSON.stringify({
index:edPlace('abcdef',1,3,'bc','zzz'),
selection:edPlace('Xabcdef',1,3,'bc','bc'),
stale:edPlace('Xabcdef',1,3,'bc','zz'),
empty:edPlace('abc',0,0,'','')
}));
""")
    assert out == {"index": "index", "selection": "selection", "stale": "stale", "empty": "stale"}


def test_ed_accept_replaces_and_marks():
    """接受：原文精确替换为 gen（包裹 <!--ai:block-->），光标落在标记后。"""
    out = _run_ed(r"""
var doc='引言。目标文字。结尾。';
var r=edAccept(doc,3,8,'目标文字。','zzz','新写的两句。');
console.log(JSON.stringify(r));
""")
    assert out["where"] == "index"
    assert out["doc"] == "引言。<!--ai:block-->\n新写的两句。\n<!--/ai:block-->结尾。"
    assert "目标文字。" not in out["doc"], "原文必须被替换掉（不是追加）"
    assert out["doc"][out["s"]:out["e"]] == "<!--ai:block-->\n新写的两句。\n<!--/ai:block-->"


def test_ed_accept_selection_fallback_and_stale_guard():
    """索引失效时按当前选区写回；两边都不符则拒写（正文一字不改）。"""
    out = _run_ed(r"""
var doc='X引言。目标文字。结尾。';
console.log(JSON.stringify({
sel:edAccept(doc,3,8,'目标文字。','目标文字。','新。'),
stale:edAccept(doc,3,8,'目标文字。','别的字。','新。')
}));
""")
    assert out["sel"]["where"] == "selection"
    assert out["sel"]["doc"] == "X引言。<!--ai:block-->\n新。\n<!--/ai:block-->结尾。"
    assert out["stale"]["where"] == "stale"
    assert out["stale"]["doc"] == "X引言。目标文字。结尾。", "拒写必须一字不改"


def test_ed_accept_empty_gen_writes_nothing():
    """空产出不写回（否则会把用户文字替换成空）。"""
    out = _run_ed(r"""
var doc='甲乙丙';
console.log(JSON.stringify(edAccept(doc,0,3,'甲乙丙','x','   ')));
""")
    assert out["where"] == "empty"
    assert out["doc"] == "甲乙丙"


def test_ed_reject_keeps_doc_untouched():
    """拒绝：正文零改动（只关闭弹窗）。"""
    out = _run_ed(r"""
var doc='一字不改的正文。';
console.log(JSON.stringify({same:edReject(doc).doc===doc,doc:edReject(doc).doc}));
""")
    assert out["same"] is True
    assert out["doc"] == "一字不改的正文。"


def test_ed_ai_mark_wraps_and_counts():
    """接受后的标记包裹：首尾标记各独占一行；空产出不包裹；计数跳过围栏。"""
    out = _run_ed(r"""
var m=edAIMark('  扩充后的正文  ');
console.log(JSON.stringify({
mark:m,
empty:edAIMark('   '),
count:edAIMarkCount('<!--ai:block-->\n正文\n<!--/ai:block-->\n<!--ai:block-->\n正文2\n<!--/ai:block-->'),
fenced:edAIMarkCount('```\n<!--ai:block-->\n```')
}));
""")
    assert out["mark"] == "<!--ai:block-->\n扩充后的正文\n<!--/ai:block-->"
    assert out["empty"] == ""
    assert out["count"] == 2
    assert out["fenced"] == 0


def test_ed_diff_rows_red_green():
    """diff 行：新增段 t=1、删除段 t=-1、相同段 t=0；单行走逐字符（红绿定位准）。"""
    out = _run_ed(r"""
var r=edDiffRows('Clean Your TV 每一步可撤销。','Clean Your TV 每一步可撤销——disable 而非 uninstall。');
var kinds=[];
for(var i=0;i<r.length;i++)kinds.push(r[i].t);
console.log(JSON.stringify({kinds:kinds,add:edDiffStat(r).add,del:edDiffStat(r).del}));
""")
    assert 1 in out["kinds"], "应有新增段"
    assert out["add"] > 0
    assert out["del"] == 0, "纯追加时无删除段"


def test_ed_diff_multiline_uses_line_granularity():
    """多行选区走逐行粒度（逐字符会把整篇碎成噪声）。"""
    out = _run_ed(r"""
var r=edDiffRows('第一行\n第二行\n第三行','第一行\n改过的第二行\n第三行');
var kinds=[];
for(var i=0;i<r.length;i++)kinds.push(r[i].t);
console.log(JSON.stringify({kinds:kinds,hasDel:kinds.indexOf(-1)>=0,hasAdd:kinds.indexOf(1)>=0}));
""")
    assert out["hasDel"] is True and out["hasAdd"] is True
    assert out["kinds"][0] == 0, "首段相同应保留为未变段"


def test_editor_js_ai_wiring_anchors():
    """结构锚：5 按钮 / modal overlay / 端点 / replaceRange+标记 / 加载态 / 拒绝路径。"""
    src = EDITOR_JS.read_text(encoding="utf-8")
    for action in ("expand", "summarize", "rewrite", "continue", "qa"):
        assert f"['{action}'" in src, action
    for token in (
        "cursorActivity",            # 选区变化触发
        "edAiSync",                  # 浮出/收纳入口
        "cursorCoords",              # 定位计算（选区坐标）
        "/api/editor/ai-action",     # 端点
        "confirm:true",              # 人审闸门
        "modal-overlay",             # diff 弹窗复用既有遮罩语义
        "ed-ai-del", "ed-ai-add",    # 红删绿增两栏
        "<del>", "<ins>",            # 语义标签（删除线 / 新增）
        "edAIMark",                  # 接受时包裹 <!--ai:block-->
        "replaceRange",              # 接受写回（保留撤销栈）
        "ed-ai-rej", "ed-ai-acc",    # 拒绝 / 接受两钮
        "edAiBusySet",               # 加载态
    ):
        assert token in src, token
    # 请求体四键（confirm/action/text/context）
    assert "action:action,text:sel,context:edCtx" in src


def test_editor_css_diff_colors():
    """样式锚：原文删除线绯红、AI 新增玉色底（红删绿增的设计稿点色）。"""
    css = EDITOR_CSS.read_text(encoding="utf-8")
    for token in (".ed-ai{", ".ed-ai-del del{", "text-decoration:line-through",
                  ".ed-ai-add ins{", "var(--crimson)", "var(--jade)",
                  ".ed-ai-dlg[hidden]", ".ed-ai-b{"):
        assert token in css, token


def test_editor_ai_no_external_references():
    """零外联硬门覆盖到新增前端面（沿 test_editor_ui 同口径）。"""
    import re

    for path in (EDITOR_JS, EDITOR_CSS):
        blob = path.read_text(encoding="utf-8")
        assert re.search(r"https?://", blob, re.IGNORECASE) is None, path
        assert "@import" not in blob, path


def test_editor_js_junction_priority_anchors():
    """三线交界结构锚（E-2×E-3 集成批，选中优先级>搜索）：

    1. 开向裁决——edRpToggle 在面板未开时先查 edAiSelActive（AI 弹窗开着或
       选区达浮出门槛）→ 不开搜索面板不夺焦点；关向不设限（面板开着恒可收）；
    2. Esc 收拾顺序——选中浮出（AI 菜单）→ 查找条 → 内联面板，全收才 Pass；
    3. 反向不设限——edAiSync 的浮出判据不含 edRpOpen（搜索面板开着选区照常
       浮出），结构面以「edAiSelActive 不引用 edRpOpen」锁定。
    """
    src = EDITOR_JS.read_text(encoding="utf-8")
    # 1) 开向裁决：门槛谓词 + 关向恒允许 + 让渡分支（顺序=先关后让）
    for token in (
        "function edAiMenuVis",
        "function edAiSelActive",
        "if(edRpOpen())return edRpHide();",
        "if(edAiSelActive())return;",
        "edAIMenuOn(edCM.getSelection())",
    ):
        assert token in src, token
    # edAiSelActive 体（弹窗开 || 选区达标）两分支齐全，且不掺 edRpOpen
    # （掺了=反向也设限，违反「搜索面板开着选区照常浮出」）
    body = src.split("function edAiSelActive(){", 1)[1].split("function ", 1)[0]
    assert "edAiDlg&&!edAiDlg.hidden" in body
    assert "edRpOpen" not in body
    # 2) Esc 收拾顺序：ghost 最先（G-T1，最瞬态）→ AI 菜单 → 查找条 → 内联面板，
    #    全收才 Pass（整链一锚锁序）
    assert ("'Esc':function(cm){if(gtMark)gtEscape(cm);"
            "else if(edAiMenuVis())edAiHide();"
            "else if(edBarOpen())edFindHide();"
            "else if(edRpOpen())edRpHide();"
            "else return CodeMirror.Pass;}") in src
