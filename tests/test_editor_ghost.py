"""G-T1 Ghost Text 续写测试（正典=《musespiral-调研吸收功能设计-2026-11-18》§二；
2026-11-19 落地批）。

四层断言（沿 test_editor_ai / test_compose_rolling_ui 的既有惯例）：

  a) 服务层（真实 gateway + 注入假件）：run_ghost 路由 ghost_write 键、
     temperature 0.8、prompt 含上下文与 title/tags、围栏剥离、空响应转
     AIError、200 字硬帽、覆盖级联（prompts.d/ghost_write.txt）。
  b) API 层（真实回环服务器 + http.client）：契约锁成员 / confirm 闸门 /
     context-title-tags 校验 400 / 金路径 200 {ok,task,ghost} / AI 未配置
     200 {ok:false} / 零落盘零 job。
  c) TASKS 扩键：ghost_write 在白名单（chat 键）、22 键计数锁。
  d) 前端（node 真实执行 editor.js GT 哨兵段 + 最小 CM 桩）：行尾谓词 /
     上下文 ±1 行窗口 / frontmatter 元信息 / cooldown 纯判定 / 800ms 防抖
     触发（到点才发请求 → 渲染 cm-ghost-text 书签）/ Tab 采纳（落稿+清+
     cooldown 阻断）/ Esc 忽略（保持态）/ 打字自动消失 / mid-line 不触发 /
     过期响应丢弃（序号守卫）/ 状态栏 hint 显隐 / 结构锚与回环字节一致。

机制注记（测试与实现一致的依据）：显示走 setBookmark 内联 widget——CM5
markText 只能样式化已在文档中的文字（collapsed mark 零宽不可见），而设计稿
「不可编辑 / 打字自动消失 / Tab=replaceSelection 落稿」要求 ghost 落稿前
不进缓冲；交互语义与设计稿逐条对应，见契约 §3.14 机制注记。
"""
from __future__ import annotations

import http.client
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
GT_BEGIN = "/*__GT_BEGIN__*/"
GT_END = "/*__GT_END__*/"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """本地无 key 档案（ghost_write 未配路由 → 走 default）。"""
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


def _fake_chat(reply: str = "AI 续写的一段话。", calls: list | None = None):
    calls = calls if calls is not None else []

    def fake(messages, *, task, temperature=0.6, timeout=120, profile=None):
        calls.append({"task": task, "temperature": temperature, "content": messages[0]["content"]})
        return reply

    return fake, calls


# ══ a) 服务层：ghost_write 任务 ══════════════════════════════════════


def test_ghost_routes_to_ghost_write_task_at_temp_08(ai_on, monkeypatch):
    """ghost_write 独立任务键 + 温度 0.8（§2.4：创意预判偏高）。"""
    fake, calls = _fake_chat("顺着写下去的一段。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    out = svc.run_ghost("第一段。当前写到一半。")
    assert out == {"task": "ghost_write", "ghost": "顺着写下去的一段。"}
    assert calls[0]["task"] == "ghost_write"
    assert calls[0]["temperature"] == svc.GHOST_TEMPERATURE == 0.8


def test_ghost_prompt_carries_context_title_tags(ai_on, monkeypatch):
    """上下文必进 prompt；title/tags 选填进（§2.3：frontmatter 元信息随行）。"""
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_ghost("前一行。当前行。后一行。", title="AI 视频三日谈", tags=["ai", "写作"])
    prompt = calls[0]["content"]
    assert "前一行。当前行。后一行。" in prompt
    assert "AI 视频三日谈" in prompt
    assert "ai、写作" in prompt
    assert svc.DEFAULT_PROMPTS["ghost_write"].splitlines()[0] in prompt, "内置缺省模板在用"


def test_ghost_prompt_without_meta_has_no_residue(ai_on, monkeypatch):
    """无 title/tags → 槽位替换为空串，不得残留 {{title}}/{{tags}} 字面量。"""
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_ghost("一段上下文。")
    prompt = calls[0]["content"]
    assert "{{title}}" not in prompt and "{{tags}}" not in prompt
    assert "一段上下文。" in prompt


def test_ghost_fence_stripped_and_blank_rejected(ai_on, monkeypatch):
    from musespiral.infra.ai import AIError

    fake, _ = _fake_chat("```\n围栏内续写\n```")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    assert svc.run_ghost("上下文。")["ghost"] == "围栏内续写"

    blank, _ = _fake_chat("  \n ")
    monkeypatch.setattr(gateway_mod, "_real_chat", blank)
    with pytest.raises(AIError) as exc:
        svc.run_ghost("上下文。")
    assert "空响应" in str(exc.value)


def test_ghost_output_hard_capped_at_200(ai_on, monkeypatch):
    """产出硬帽 200 字（设计 max_out=200 的服务端字符口径，G-T4 同机制）。"""
    fake, _ = _fake_chat("写" * 500)
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    out = svc.run_ghost("上下文。")
    assert len(out["ghost"]) == svc.GHOST_MAX_CHARS == 200


def test_ghost_blank_context_rejected(ai_on):
    with pytest.raises(svc.EditorAIError) as exc:
        svc.run_ghost("   \n\t ")
    assert "context" in str(exc.value)


def test_ghost_oversize_context_clamped(ai_on, monkeypatch):
    """超长上下文截断到预算（辅助信息不拒收，run_action 同判例）。"""
    fake, calls = _fake_chat("续写。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_ghost("上" * (svc.CONTEXT_MAX_CHARS + 999))
    assert len(calls[0]["content"]) < svc.CONTEXT_MAX_CHARS + 2000


def test_ghost_override_cascade(ai_on, monkeypatch):
    """prompts.d/ghost_write.txt 整体覆盖内置缺省（F47 级联同纪律）。"""
    from musespiral.infra.ai.prompts import save_override

    save_override("ghost_write", "覆盖模板：请接着写。\n{{context}}")
    fake, calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    svc.run_ghost("要承接的句子。")
    prompt = calls[0]["content"]
    assert "覆盖模板" in prompt
    assert "要承接的句子。" in prompt, "{{context}} 槽位应原位替换"
    assert svc.DEFAULT_PROMPTS["ghost_write"].splitlines()[0] not in prompt


# ══ b) API 层：契约形状 + confirm 闸门 + 零落盘 ═════════════════════


def test_api_contract_shape_and_confirm_gate(server, ai_on, monkeypatch):
    """契约锁成员 + confirm 闸门 + 400 分档 + 金路径 200 {ok,task,ghost}。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from tests.test_architecture import CONTRACT_POST_EXACT

    fake, _calls = _fake_chat("AI 续写的一段话。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)

    assert "/api/editor/ghost-write" in _ACTION_ROUTES
    assert "/api/editor/ghost-write" in CONTRACT_POST_EXACT

    # confirm 闸门（缺省 → 400，文案同全局）
    code, err = _request(server, "POST", "/api/editor/ghost-write",
                         {"context": "一段上下文。"})
    assert code == 400 and "confirm:true" in err["error"]

    # context 缺失 → 400（_require_str 统一文案）
    code, err = _request(server, "POST", "/api/editor/ghost-write", {"confirm": True})
    assert code == 400 and "context" in err["error"]

    # title / tags 类型不对 → 400
    code, err = _request(server, "POST", "/api/editor/ghost-write",
                         {"confirm": True, "context": "上下文。", "title": 42})
    assert code == 400 and "title" in err["error"]
    code, err = _request(server, "POST", "/api/editor/ghost-write",
                         {"confirm": True, "context": "上下文。", "tags": 42})
    assert code == 400 and "tags" in err["error"]

    # 金路径 200：{ok, task, ghost}；tags 接受字符串数组
    code, payload = _request(server, "POST", "/api/editor/ghost-write",
                             {"confirm": True, "context": "当前写到一半的段落。",
                              "title": "标题", "tags": ["ai", "写作"]})
    assert code == 200
    assert payload == {"ok": True, "task": "ghost_write", "ghost": "AI 续写的一段话。"}

    # tags 也可为字符串（前端兼容形态）
    code, payload = _request(server, "POST", "/api/editor/ghost-write",
                             {"confirm": True, "context": "上下文。", "tags": "ai"})
    assert code == 200 and payload["ok"] is True


def test_api_ai_unconfigured_returns_ok_false(server, studio_root):
    """AI 未配置 → 200 {ok:false} 可读降级（同 ai-action 面，不 4xx）。"""
    code, payload = _request(server, "POST", "/api/editor/ghost-write",
                             {"confirm": True, "context": "一段上下文。"})
    assert code == 200 and payload["ok"] is False
    assert "AI 未配置" in payload["error"]


def test_api_zero_write_and_zero_job(server, studio_root, ai_on, monkeypatch):
    """同步零落盘零 job（拒绝即消失不留痕判例）。"""
    fake, _ = _fake_chat("续写。")
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)
    before = sorted(p.name for p in studio_root.rglob("*"))
    code, _ = _request(server, "POST", "/api/editor/ghost-write",
                       {"confirm": True, "context": "一段上下文。"})
    assert code == 200
    after = sorted(p.name for p in studio_root.rglob("*"))
    assert after == before, "ghost 续写不得写工作目录"
    assert not (ai_on / "jobs.jsonl").exists(), "同步形态不得建 job"


# ══ c) TASKS 扩键 ═══════════════════════════════════════════════════


def test_ghost_write_task_key_is_chat_whitelisted():
    """ghost_write 在 TASKS 白名单（routing/覆盖/ai check/per_task 预算自动继承）。"""
    assert "ghost_write" in TASKS
    # 21→22 计数锁（扩键必须在此显式登记新总数；与 test_editor_ai 的锁互为双保险）
    assert len(TASKS) == 27  # PL-6 24→25；N-2 25→26；插件 v2 验证批 26→27（plugin_refine_segment）
    assert "image_generate" not in TASKS, "chat 白名单不得被非 chat 键污染（G1 判例）"


# ══ d) 前端：GT 哨兵段 node 行为断言 + 结构锚 ═══════════════════════

_SEG = None


def _segment() -> str:
    global _SEG
    if _SEG is None:
        src = EDITOR_JS.read_text(encoding="utf-8")
        assert GT_BEGIN in src and GT_END in src, "editor.js 缺少 GT 哨兵段"
        _SEG = src.split(GT_BEGIN)[1].split(GT_END)[0]
    return _SEG


_PRELUDE = r"""'use strict';
var _els={};
var document={
getElementById:function(id){if(_els[id]==null)_els[id]={hidden:true,className:'',textContent:''};return _els[id];},
createElement:function(tag){var e={tag:tag,className:'',textContent:'',attrs:{},
setAttribute:function(k,v){e.attrs[k]=v;}};
Object.defineProperty(e,'hidden',{value:false,writable:true});return e;}};
function mkCM(lines,line,ch){
var cm={cur:{line:line,ch:ch},focus:true,repl:[],bm:null};
cm.lines=lines.slice();
cm.getCursor=function(){return cm.cur;};
cm.getLine=function(n){return(n<0||n>=cm.lines.length)?'':cm.lines[n];};
cm.getValue=function(){return cm.lines.join('\n');};
cm.hasFocus=function(){return cm.focus;};
cm.setBookmark=function(pos,opt){cm.bm={pos:pos,cleared:false,
widget:opt.widget,insertLeft:opt.insertLeft,
clear:function(){cm.bm.cleared=true;}};return cm.bm;};
cm.replaceSelection=function(t,mode,origin){cm.repl.push({t:t,mode:mode,origin:origin});};
cm.focus=function(){cm.focus=true;};
return cm;}
function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}
"""


def _run_gt(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行")
    script = (
        _PRELUDE + _segment() + "\n"
        # 断言面只读窗口：真函数别名（frag 内裸调用可用；gtShown 避让哨兵段
        # 内部 var gtText 字符串——别名不得与其重名）
        "function gtActive(){return!!gtMark;}\n"
        "function gtShown(){return gtText;}\n"
        "function gtCooling(){return Date.now()-gtCdAt<GT_CD;}\n"
        "function gtHintEl(){return _els['ed-ghost-hint']||null;}\n"
        "module.exports={gtCursor:gtCursor,gtTyping:gtTyping,gtAccept:gtAccept,"
        "gtEscape:gtEscape,gtReset:gtReset,gtShow:gtShow,gtActive:gtActive,"
        "gtShown:gtShown,"
        "gtCtx:edGhostCtx,gtAtEnd:edGhostAtEnd,gtMeta:edGhostMeta,gtCooled:edGhostCooled,"
        "gtCooling:gtCooling,gtHintEl:gtHintEl};\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "gt_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _async_frag(body: str) -> str:
    """异步行为断言骨架：失败 process.exit(1)（returncode 断言在 _run_gt）。"""
    return ("(async function(){\n" + body +
            "\n})().catch(function(e){console.error(e&&(e.stack||e.message)||e);"
            "process.exit(1);});\n")


def test_gt_at_end_predicate():
    """触发谓词：行尾/空行真，行中假（§二「光标在行尾或空行才触发」）。"""
    out = _run_gt(r"""
console.log(JSON.stringify({
mid:edGhostAtEnd('abcde',2),
end:edGhostAtEnd('abcde',5),
beyond:edGhostAtEnd('abcde',9),
empty:edGhostAtEnd('',0),
blank:edGhostAtEnd('   ',3)
}));
""")
    assert out == {"mid": False, "end": True, "beyond": True,
                   "empty": True, "blank": True}


def test_gt_ctx_is_three_line_window():
    """请求上下文=当前行 ±1 行（getLine 口径）；文档边界处窗口收缩不越界。"""
    out = _run_gt(r"""
var doc='L0\nL1\nL2\nL3\nL4';
console.log(JSON.stringify({
mid:edGhostCtx(doc,2,1),
first:edGhostCtx(doc,0,1),
last:edGhostCtx(doc,4,1)
}));
""")
    assert out["mid"] == "L1\nL2\nL3"
    assert out["first"] == "L0\nL1"
    assert out["last"] == "L3\nL4"


def test_gt_meta_parses_frontmatter():
    """frontmatter title/tags 提取：[] 列表与顿号/逗号串两形态；无 frontmatter → 空态。"""
    out = _run_gt(r"""
var withFm='---\ntitle: AI 视频三日谈\ntags: [ai, 写作, 观测]\n---\n\n正文。';
var plain='---\ntitle: 标题二\ntags: ai、写作\n---\n正文。';
var none='直接正文，无 frontmatter。';
console.log(JSON.stringify({a:edGhostMeta(withFm),b:edGhostMeta(plain),c:edGhostMeta(none)}));
""")
    assert out["a"] == {"title": "AI 视频三日谈", "tags": ["ai", "写作", "观测"]}
    assert out["b"] == {"title": "标题二", "tags": ["ai", "写作"]}
    assert out["c"] == {"title": "", "tags": []}


def test_gt_cooled_boundary():
    """cooldown 纯判定：未满 3s 假、满 3s 真（gtAccept 时钟为准）。"""
    out = _run_gt(r"""
console.log(JSON.stringify({
fresh:edGhostCooled(10000,9000,3000),
edge:edGhostCooled(10000,7000,3000),
past:edGhostCooled(10000,6999,3000)
}));
""")
    assert out == {"fresh": False, "edge": True, "past": True}


def test_gt_debounce_fires_only_after_800ms_then_renders():
    """防抖触发：cursorActivity 即刻不发请求；防抖到点发一次（confirm+context
    +frontmatter 元信息）→ 渲染 cm-ghost-text 书签（文本不进缓冲）。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=20;
var calls=[];
gtSend=function(p){calls.push(p);return Promise.resolve({ok:true,ghost:'这是 AI 的续写。'});};
var doc='---\ntitle: 三日谈\ntags: [ai]\n---\n\n第一段。\n光标停在这行的末尾\n尾段。';
var cm=mkCM(doc.split('\n'),6,9);  // 行 6=「光标停在这行的末尾」（行 4 是 frontmatter 后空行）
gtCursor(cm);
var atOnce=calls.length;
await sleep(5);
var beforeDue=calls.length;
await sleep(80);
var sent=calls.length===1?calls[0]:null;
console.log(JSON.stringify({atOnce:atOnce,beforeDue:beforeDue,
sent:sent,active:gtActive(),cls:cm.bm?cm.bm.widget.className:null,
inBuffer:cm.lines.join('\n').indexOf('这是 AI 的续写')>=0,
hintHidden:gtHintEl().hidden}));
"""))
    assert out["atOnce"] == 0 and out["beforeDue"] == 0, "防抖期内不得发请求"
    assert out["sent"] == {"confirm": True,
                           "context": "第一段。\n光标停在这行的末尾\n尾段。",
                           "title": "三日谈", "tags": ["ai"]}
    assert out["active"] is True
    assert out["cls"] == "cm-ghost-text", "书签 widget 必须带 cm-ghost-text 类"
    assert out["inBuffer"] is False, "ghost 未采纳前不得进文档缓冲（⌘S/字数统计不受污染）"
    assert out["hintHidden"] is False, "状态栏 hint 应显示「Tab 采纳 · Esc 忽略」"


def test_gt_tab_accept_replaces_clears_and_cools_down():
    """Tab 采纳：replaceSelection 落稿（正常色）+ 书签清 + cooldown 3s 内
    不再发新请求（防 Tab 滚雪球）；无 ghost 时 Tab 返回 null 让位智能缩进。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=10;
var calls=[];
gtSend=function(p){calls.push(p);return Promise.resolve({ok:true,ghost:'续写文本'});};
var cm=mkCM(['第一行','停在行尾','第三行'],1,4);
// 无 ghost 时 gtAccept 返回 null（extraKeys Tab 落回 edSmartTab）
var noGhostAccept=gtAccept(cm);
gtCursor(cm);
await sleep(50);
var shown=gtActive();
var acc=gtAccept(cm);
gtCursor(cm);   // 采纳后光标仍在行尾 → 防抖重排 → 到点被 cooldown 拦下
await sleep(60);
console.log(JSON.stringify({noGhostAccept:noGhostAccept,shown:shown,
acc:acc,repl:cm.repl,active:gtActive(),reqs:calls.length,
cooling:gtCooling()}));
"""))
    assert out["noGhostAccept"] is None
    assert out["shown"] is True
    assert out["acc"] == "续写文本"
    assert out["repl"] == [{"t": "续写文本", "mode": "end", "origin": "+ghost"}]
    assert out["active"] is False, "采纳后书签清（文本已是正常色正文）"
    assert out["reqs"] == 1, "cooldown 3s 内第二次防抖到点不得再发请求"
    assert out["cooling"] is True

    # gtCdAt 是哨兵段内部变量——经一次真值只读暴露（module.exports 未导出时跳过）
    src = _segment()
    assert "gtCdAt=Date.now();" in src, "采纳必须记录 cooldown 时间戳"


def test_gt_esc_ignores_and_holds_until_new_typing():
    """Esc：清屏 + 保持态（不自动请求）；用户再输入（gtTyping）解除保持态。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=10;
var calls=[];
gtSend=function(p){calls.push(p);return Promise.resolve({ok:true,ghost:'预判'});};
var cm=mkCM(['一行文字','停在行尾'],1,4);
gtShow(cm,'一段预判',{line:1,ch:4});
var shownBefore=gtActive();
gtEscape(cm);
var afterEsc=gtActive();
var hintBackHidden=gtHintEl().hidden;
gtCursor(cm);           // 保持态：行尾也不排防抖
await sleep(40);
var heldReqs=calls.length;
gtTyping();              // 用户敲了一个字 → 解除保持态
gtCursor(cm);
await sleep(60);
console.log(JSON.stringify({shownBefore:shownBefore,afterEsc:afterEsc,
hintBackHidden:hintBackHidden,heldReqs:heldReqs,
releasedReqs:calls.length,active:gtActive()}));
"""))
    assert out["shownBefore"] is True and out["afterEsc"] is False
    assert out["hintBackHidden"] is True
    assert out["heldReqs"] == 0, "Esc 保持态下不得自动请求"
    assert out["releasedReqs"] == 1 and out["active"] is True, "再输入解除保持态"


def test_gt_typing_clears_visible_ghost():
    """打字自动消失：文档变更 → 在屏 ghost 立即退场（hint 同步收回）。"""
    out = _run_gt(r"""
var cm=mkCM(['停笔行'],0,3);
gtShow(cm,'灰色预判',{line:0,ch:3});
var shown=gtActive();
gtTyping();
console.log(JSON.stringify({shown:shown,after:gtActive(),
hintHidden:gtHintEl().hidden,bookmarkCleared:cm.bm?cm.bm.cleared:null}));
""")
    assert out == {"shown": True, "after": False, "hintHidden": True,
                   "bookmarkCleared": True}


def test_gt_midline_and_blank_context_never_request():
    """行中光标不触发；全空白上下文（空文首行）不空转；失焦不发。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=10;
var calls=[];
gtSend=function(p){calls.push(p);return Promise.resolve({ok:true,ghost:'x'});};
var mid=mkCM(['abcdef'],0,2);
gtCursor(mid); await sleep(50);
var midReqs=calls.length;
var blank=mkCM([''],0,0);
gtCursor(blank); await sleep(50);
var blankReqs=calls.length;
var unfocused=mkCM(['停在行尾'],0,4);
unfocused.focus=false;
gtCursor(unfocused); await sleep(50);
var blurReqs=calls.length;
console.log(JSON.stringify({midReqs:midReqs,blankReqs:blankReqs,blurReqs:blurReqs}));
"""))
    assert out == {"midReqs": 0, "blankReqs": 0, "blurReqs": 0}


def test_gt_stale_response_dropped_by_seq_guard():
    """过期响应丢弃：请求飞行中离开视图（gtReset 序号前推）→ 迟到响应不渲染。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=5;
gtSend=function(p){return new Promise(function(res){
setTimeout(function(){res({ok:true,ghost:'迟到的续写'});},40);});};
var cm=mkCM(['停在行尾'],0,4);
gtCursor(cm);
await sleep(20);      // 请求已发出、仍在飞行
gtReset();            // 离开文章视图（edSuspend 接线）
await sleep(80);      // 迟到响应到达
console.log(JSON.stringify({active:gtActive(),text:gtShown()}));
"""))
    assert out == {"active": False, "text": ""}


def test_gt_arrival_retracts_when_cursor_left_line_end():
    """响应到达时若光标已不在行尾（用户点了别处再停）→ 不渲染。"""
    out = _run_gt(_async_frag(r"""
GT_DEB=5;
gtSend=function(p){return new Promise(function(res){
setTimeout(function(){res({ok:true,ghost:'迟到但光标已走'});},30);});};
var cm=mkCM(['停在行尾'],0,4);
gtCursor(cm);
cm.cur={line:0,ch:1};   // 响应到达前光标移到行中
await sleep(60);
console.log(JSON.stringify({active:gtActive()}));
"""))
    assert out == {"active": False}


# ══ e) 结构锚 + 回环 ════════════════════════════════════════════════


def test_editor_js_ghost_wiring_anchors():
    """结构锚：端点字面量 / 时序常量 / Tab·Esc·change·cursorActivity·suspend
    接线 / 状态栏 hint / 竞态守卫。"""
    src = EDITOR_JS.read_text(encoding="utf-8")
    for token in (
        "GT_DEB=800",                      # §二 防抖 800ms
        "GT_CD=3000",                      # §二 采纳后 cooldown 3s
        "edGhostAtEnd",                    # 行尾/空行谓词
        "edGhostCtx",                      # 当前行 ±1 行上下文
        "edGhostMeta",                     # frontmatter title/tags
        "'/api/editor/ghost-write'",       # 端点（零外联相对路径）
        "confirm:true,context:context",    # 请求体闸门+上下文
        "cm-ghost-text",                   # 渲染类（CSS 半透明灰斜体）
        "setBookmark",                     # 书签内联 widget（文本不进缓冲）
        "gtAccept(cm)",                    # extraKeys Tab 先问 ghost
        "if(gtMark)gtEscape(cm)",          # Esc 收拾顺序 ghost 最先
        "gtTyping();",                     # change → 打字自动消失
        "gtCursor(edCM);",                 # cursorActivity → 防抖接线
        "gtReset();",                      # edSuspend → 全量复位
        "seq!==gtSeq",                     # 竞态守卫（过期响应丢弃）
        "edGhostCooled(Date.now(),gtCdAt,GT_CD)",  # cooldown 闸
        'id="ed-ghost-hint"',              # 状态栏 hint
        "⌨ Tab 采纳 · Esc 忽略",
    ):
        assert token in src, token
    # Tab 分支：ghost 真值短路、否则落回智能缩进
    assert "'Tab':function(cm){if(gtAccept(cm))return;edSmartTab(cm);}" in src


def test_editor_css_ghost_anchors():
    """样式锚：.cm-ghost-text 半透明墨灰斜体（约束 g 原式）+ hint 显隐。"""
    css = EDITOR_CSS.read_text(encoding="utf-8")
    assert ".cm-ghost-text{color:var(--ink-3);opacity:.5;font-style:italic" in css
    assert ".ed-ghost-hint{font-size:11px" in css
    assert ".ed-ghost-hint[hidden]{display:none}" in css


def test_editor_ghost_asset_served_byte_identical(tmp_path: Path):
    """/assets/editor.js 回环：200 + application/javascript + 与源逐字节一致。"""
    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/assets/editor.js")
        resp = conn.getresponse()
        body = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        assert resp.status == 200
        assert ctype.startswith("application/javascript"), ctype
        assert body == EDITOR_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_editor_ghost_no_external_references():
    """零外联硬门覆盖 GT 段（沿 test_editor_ui 同口径；fetch 相对路径合法）。"""
    import re

    for path in (EDITOR_JS, EDITOR_CSS):
        blob = path.read_text(encoding="utf-8")
        assert re.search(r"https?://", blob, re.IGNORECASE) is None, path
        assert "@import" not in blob, path
