"""N-1 小说前端（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.6 +
《musespiral-优化重构与后续开发总体方案-2026-11-19》§二 N-1）。

三条断言面（沿 rolling_ui/dual 的三层惯例）：
  a) 行为断言（node 真实执行 assets/views/compose.js 的小说区纯函数）：
     **kind 双态**——kind=novel 产出两折叠区 DOM、kind=article 返回空串且销毁
     既有 DOM（不是 hidden）；章节覆盖提示「已摘要 N 章 / 共 M 章」；冲突提示条
     逐条列 block/character/rule/snippet/message（琥珀提示，不拦截）。
  b) 结构断言（壳 + compose.js 源）：右栏两折叠区容器、四态按钮、五字段弹窗、
     类型切换 chip、端点字面量、`#cm-blocks` 前挂冲突条（生成结果区上方）。
  c) 回环断言（真实 Studio HTTP）：kind 切换后 GET session 回读、摘卡/挂卡往返
     ——前端只经既有端点，N-1 零新增（契约锁见 test_compose_novel）。

人物卡 speech_style 注入 prompt 的**后端**断言在 tests/test_compose_novel.py
（test_character_injected_into_prompt）；本文件补一条端到端回环：建卡→挂卡→
generate 的 prompt 含 speech_style（服务层真值，非端点 200 就算数）。
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from musespiral.services import compose_service, materials_service

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
COMPOSE_JS = STUDIO / "assets" / "views" / "compose.js"
COMPOSE_HTML = STUDIO / "ui.views" / "compose.html"
SHELL = STUDIO / "ui.html"

CHARACTER = {
    "name": "林远",
    "role": "研究员",
    "appearance": "瘦高 · 左眼蓝色",
    "speech_style": "短句 · 不用感叹号 · 偶尔自嘲",
    "key_events": ["第3章 初次出场"],
}
TWO_CHAPTERS = (
    "<<<block kind:heading>>># 第一章 异变\n\n"
    "<<<block kind:para>>>陈默睁开眼，左眼发烫。\n\n"
    "<<<block kind=heading>>># 第二章 借据\n\n"
    "<<<block kind:para>>>林远递来一份文件。\n"
).replace("kind=heading", "kind:heading")
CONFLICT_REPLY = "<<<block kind:para>>>林远：「这件事不能拖！」\n"


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


def _src() -> str:
    assert COMPOSE_JS.exists(), "compose.js 缺失（须跑 scripts/build_ui.py）"
    return COMPOSE_JS.read_text(encoding="utf-8")


def _shell() -> str:
    assert SHELL.exists(), "壳缺失（须跑 scripts/build_ui.py）"
    return SHELL.read_text(encoding="utf-8")


# ══ a) 行为断言（node 真实执行小说区纯函数）════════════════════════════


def _run(frag: str) -> dict:
    """抽 compose.js 的小说区段（cnL/cnH/cnPaint/cnConf）+ DOM 桩执行。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环两层仍随全量跑）")
    src = _src()
    start = src.index("var CN=")
    end = src.index("async function cnAct(")
    pure = src[start:end]
    script = (
        "'use strict';\n"
        "function esc(v){return String(v==null?'':v).replace(/[&<>\"']/g,function(c){\n"
        "return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});}\n"
        "var _el={};\n"
        "var document={getElementById:function(id){return _el[id]||null;}};\n"
        "function mkEl(){var e={textContent:'',hidden:false,style:{},html:'',value:'',\n"
        "focus:function(){},select:function(){},disabled:false};\n"
        "Object.defineProperty(e,'innerHTML',{get:function(){return this.html;},\n"
        "set:function(h){this.html=h;var re=/id=\"([^\"]+)\"/g,m;\n"
        "while((m=re.exec(h))){if(!_el[m[1]])_el[m[1]]=mkEl();}}});\n"
        "e.insertAdjacentHTML=function(pos,h){this.html+=h;var re=/id=\"([^\"]+)\"/g,m;\n"
        "while((m=re.exec(h))){if(!_el[m[1]])_el[m[1]]=mkEl();}};return e;}\n"
        "function $(id){return _el[id]||null;}\n"
        "function I(){return '<svg></svg>';}\n"
        "function seed(id){_el[id]=mkEl();return _el[id];}\n"
        + pure
        + "\nfunction el(id){return _el[id]||null;}\n"
        "module.exports={cnH:cnH,cnPaint:cnPaint,cnConf:cnConf,CN:CN,el:el,seed:seed};\n"
        + frag
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "novel_ui_check.cjs"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_kind_novel_renders_two_folds_and_article_has_none() -> None:
    """**核心双态**：kind=novel → 两折叠区（📖 前情提要 + 👥 人物卡）真实建 DOM；
    kind=article → 小说折叠区销毁（无 <details>、人物卡不残留）——**类型切换
    chips 常驻**（2026-11-20 复核 MEDIUM 修复：正典 N-1 线框「右栏顶部」未
    条件化；chips 只画在 novel 面板=单向门，article 会话回不了 novel）。"""
    out = _run(r"""
seed('cm-novel');
cnPaint({slug:'a',kind:'novel',novel_summary:'前情：陈默左眼异变。',
  novel_summary_chapters:3,novel:{chapters:4},characters:[]});
var novelHtml=el('cm-novel').innerHTML;
cnPaint({slug:'a',kind:'article',novel_summary:'旧提要',novel_summary_chapters:3,
  novel:{chapters:4},characters:[{id:'c1',name:'林远',role:'研究员'}]});
var articleHtml=el('cm-novel').innerHTML;
var st=el('cm-ns-text');
console.log(JSON.stringify({novel:novelHtml,article:articleHtml,summaryText:st?st.textContent:null}));
""")
    novel = out["novel"]
    assert "📖 前情提要" in novel and "👥 人物卡" in novel
    assert "<details" in novel      # 两折叠区=details（骨架里没有，属运行时创建）
    assert out["summaryText"] == "前情：陈默左眼异变。"  # 摘要正文落展示节点 textContent
    # article：折叠区 DOM 销毁——无 details、人物卡/提要内容不残留；
    # 但切换 chips 必须在（article 态可切回 novel）
    article = out["article"]
    assert "<details" not in article
    assert "人物卡" not in article and "前情提要" not in article
    assert "林远" not in article
    assert 'data-cn="k:novel"' in article and 'data-cn="k:article"' in article
    # is-active 跟随当前 kind（novel 态亮小说、article 态亮文章）
    novel_active = 'chip is-active" data-cn="k:novel"' in novel
    article_active = 'chip is-active" data-cn="k:article"' in article
    assert novel_active and article_active


def test_no_session_renders_nothing_not_even_chips() -> None:
    """未选会话（cnPaint(null)）→ 整区空串：chips 常驻以「有会话」为前提，
    空态不得出现无主的切换入口（点了没有 slug 可切）。"""
    out = _run(r"""
seed('cm-novel');
cnPaint(null);
console.log(JSON.stringify({html:el('cm-novel').innerHTML}));
""")
    assert out["html"] == ""


def test_chapter_coverage_hint_and_summary_text() -> None:
    """章节覆盖提示「已摘要 N 章 / 共 M 章」+ 摘要正文落到展示节点。"""
    out = _run(r"""
seed('cm-novel');
cnPaint({slug:'b',kind:'novel',novel_summary:'摘要正文在此。',
  novel_summary_chapters:2,novel:{chapters:5},characters:[]});
var html=el('cm-novel').innerHTML;
var t=el('cm-ns-text');
console.log(JSON.stringify({html:html,text:t?t.textContent:null}));
""")
    assert "2" in out["html"] and "5" in out["html"]
    assert out["text"] == "摘要正文在此。"


def test_conflict_bar_lists_fields_and_is_amber_hint_only() -> None:
    """冲突提示条：逐条列 block/character/rule/snippet/message；文案明示「仅提示」。"""
    out = _run(r"""
seed('cm-novel');seed('cm-blocks');
// 生产顺序：先 renderCompose/cnPaint（可能复位），再灌 job.result.conflicts，再 cnConf
cnPaint({slug:'c',kind:'novel',novel_summary:'',novel_summary_chapters:0,
  novel:{chapters:2},characters:[]});
CN.c=[{block:'b1',line:2,character:'林远',rule:'no_exclamation',
  snippet:'林远：「这件事不能拖！」',message:'林远的对话含感叹号，与人物卡「不用感叹号」不符'}];
cnConf();
var h=el('cm-conf-tip');
console.log(JSON.stringify({hidden:h.hidden,html:h.innerHTML}));
""")
    assert out["hidden"] is False
    html = out["html"]
    for token in ("b1", "林远", "no_exclamation", "不能拖", "感叹号", "<svg"):
        assert token in html, token
    assert "仅提示" in html          # 红线：提示不拦截
    assert "未改动" in html


def test_conflict_bar_hidden_without_conflicts() -> None:
    """零冲突 → 提示条 hidden（不占视觉噪声）；有→无的切换即时收回。"""
    out = _run(r"""
seed('cm-novel');seed('cm-blocks');
CN.c=[{block:'b1',character:'林远',rule:'r',snippet:'s',message:'m'}];
cnConf();
var withConf=el('cm-conf-tip').hidden;
CN.c=[];
cnConf();
console.log(JSON.stringify({withConf:withConf,without:el('cm-conf-tip').hidden}));
""")
    assert out["withConf"] is False and out["without"] is True


def test_characters_list_has_name_role_and_remove_button() -> None:
    """人物卡列表：姓名 · 角色 · 摘要一行（含 speech_style）+ 每卡 [×]。"""
    out = _run(r"""
seed('cm-novel');
cnPaint({slug:'d',kind:'novel',novel_summary:'',novel_summary_chapters:0,
  novel:{chapters:1},characters:[{id:'m1',name:'林远',role:'研究员',
  appearance:'瘦高',speech_style:'不用感叹号',key_events:['第3章 初次出场']}]});
console.log(JSON.stringify({html:el('cm-novel').innerHTML}));
""")
    html = out["html"]
    assert "林远" in html and "研究员" in html
    assert "不用感叹号" in html                     # speech_style 摘要行
    assert 'data-cn="rm"' in html and 'data-id="m1"' in html   # [×] 摘卡


def test_type_switch_chips_and_edit_state() -> None:
    """类型切换 chip 双态（is-active 跟随 kind）+ ✏️ 编辑态出文本框与保存。"""
    out = _run(r"""
seed('cm-novel');
cnPaint({slug:'e',kind:'novel',novel_summary:'x',novel_summary_chapters:1,
  novel:{chapters:2},characters:[]});
var novelHtml=el('cm-novel').innerHTML;
CN.e=1;
cnPaint({slug:'e',kind:'novel',novel_summary:'x',novel_summary_chapters:1,
  novel:{chapters:2},characters:[]});
var editHtml=el('cm-novel').innerHTML;
console.log(JSON.stringify({novel:novelHtml,edit:editHtml,
  area:!!el('cm-ns-area')}));
""")
    assert 'data-cn="k:novel"' in out["novel"] and 'data-cn="k:article"' in out["novel"]
    assert "is-active" in out["novel"]
    assert out["area"] is True                       # 编辑态 textarea 已建
    assert 'data-cn="save"' in out["edit"]


# ══ b) 结构断言（壳 + compose.js 源）═══════════════════════════════════


def test_rail_containers_in_shell() -> None:
    """右栏两容器：唯一稳定锚 #cm-novel（小说区 DOM 全由它内部条件创建/销毁）。"""
    shell = _shell()
    assert 'id="cm-novel"' in shell
    # 右栏位置：在 #view-compose 段内（不是别处）
    cm = shell.index('id="view-compose"')
    assert shell.index('id="cm-novel"') > cm


def test_compose_js_uses_existing_endpoints_only() -> None:
    """N-1 零新增端点：只消费 compose/session（GET）与 compose/novel-summary（POST）。"""
    src = _src()
    assert "postJSON('/api/compose/novel-summary'" in src
    # 详情读面复用 core.js 的 loadComposeDetail（不新开 GET）——视图侧只调它
    assert "loadComposeDetail(state.cslug,true)" in src
    # 六 action 字面量齐全
    for act in ("'kind'", "character_remove", "character_add", "character_create", "refresh", "save"):
        assert act in src, act


def test_conflict_tip_mounts_above_blocks() -> None:
    """回归锚：冲突条挂在 #cm-blocks **之前**（生成结果区上方），不是右栏。"""
    src = _src()
    assert "insertAdjacentHTML('beforebegin'" in src
    idx = src.index("insertAdjacentHTML('beforebegin'")
    win = src[max(0, idx - 220):idx + 120]
    assert "cm-blocks" in win, win
    assert "cm-conf-tip" in win, win


def test_five_field_character_modal() -> None:
    """新建人物弹窗五字段：name/role/appearance/speech_style/key_events。"""
    src = _src()
    # 前四字段由 [字段名, 占位文案] 数组驱动，共用一个 id 前缀模板
    for field in ("name", "role", "appearance", "speech_style"):
        assert "['" + field + "'" in src, field      # 数组字面量项
    assert "cm-chf-'+x[0]+'" in src or "cm-chf-name" in src   # id 前缀模板
    assert "cm-chf-key_events" in src


def test_kind_switch_and_rerender_wiring() -> None:
    """类型切换后立即重渲染右栏（loadComposeDetail(force) + renderCompose 同链）。"""
    src = _src()
    assert "await loadComposeDetail(state.cslug,true);renderCompose();" in src


# ══ c) 回环断言（服务层真值：建卡→挂卡→生成 prompt 注入 speech_style）════


def test_speech_style_actually_injected_into_generate_prompt(root: Path) -> None:
    """**验收红线**：不是端点回 200，而是生成 prompt 里真的含人物卡 speech_style。

    N-1 前端只负责建卡与挂卡；注入正确性归后端——本用例走服务层真实 generate
    捕获 prompt，确保前端挂上的卡确实进了 prompt（前端断言之外的端到端锁）。
    """
    compose_service.new_session("ui1", "长篇", root=root, kind="novel")
    compose_service.generate("ui1", root=root, chat_fn=lambda m, **k: TWO_CHAPTERS)
    created = materials_service.add_character(**CHARACTER)
    mid = created["material"]["id"]
    compose_service.add_character("ui1", mid, root=root)
    detail = compose_service.session_detail("ui1", root=root)
    assert detail["kind"] == "novel"
    assert [c["id"] for c in detail["characters"]] == [mid]
    captured: dict = {}

    def _gen(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return CONFLICT_REPLY

    out = compose_service.generate("ui1", root=root, chat_fn=_gen)
    prompt = captured["prompt"]
    assert "人物卡" in prompt
    assert "林远" in prompt and "研究员" in prompt
    assert "不用感叹号" in prompt         # speech_style 真值进 prompt
    assert "瘦高 · 左眼蓝色" in prompt     # appearance
    # 冲突只提示不拦截：文本原样保留
    assert out["conflicts"], "人物卡冲突未检出"
    assert any("不能拖" in b["text"] for b in out["blocks"]), "生成文本被改写"


def test_kind_toggle_roundtrip_destroys_client_state(root: Path) -> None:
    """kind 切换回读：novel → article 后详情 kind=article（前端据它销毁小说 DOM）。"""
    compose_service.new_session("ui2", "切换", root=root, kind="novel")
    assert compose_service.session_detail("ui2", root=root)["kind"] == "novel"
    compose_service.set_session_kind("ui2", "article", root=root)
    assert compose_service.session_detail("ui2", root=root)["kind"] == "article"


def test_character_remove_keeps_material(root: Path) -> None:
    """摘卡只解挂载：素材库里的卡还在（前端 [×] 语义的数据面）。"""
    compose_service.new_session("ui3", "摘卡", root=root, kind="novel")
    mid = materials_service.add_character(**CHARACTER)["material"]["id"]
    compose_service.add_character("ui3", mid, root=root)
    compose_service.remove_character("ui3", mid, root=root)
    assert compose_service.session_detail("ui3", root=root)["characters"] == []
    assert materials_service.get_character(mid) is not None
