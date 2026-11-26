"""Studio 编辑器 E2E（F1 一期全落，正典=《musespiral-专业创作者视角深度评审与改进
-2026-11-18》§3.1；2026-11-19 落地批）：playwright chromium 驱动真实 CM5 链路。

前置与隔离同 test_studio_e2e（demo fixture 副本 + 真实 Studio 服务器 + MUSESPIRAL_HOME
隔离）；本文件聚焦编辑器替换后的交互面：CM 挂载/工具栏插入/快捷键/专注模式切换/
大纲解析与跳转/字数与阅读时长/搜索替换/文章切换重同步。

降级契约：editor.js/vendor 装载失败时 textarea 原功能保留（行为面测试在
test_editor_ui.py 的 node 层；真机层这里只测成功路径——失败路径的结构保证是
mountEdit 的 else 分支不抛错，由 node/结构层覆盖）。
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")  # e2e 前置：无 playwright 时 skip 而非 error

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from musespiral import studio  # noqa: E402
from musespiral.studio import actions as studio_actions  # noqa: E402

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    root = tmp_path / "studio-demo"
    shutil.copytree(FIXTURES / "studio_demo", root)
    return root


@pytest.fixture
def e2e_server(demo_root: Path) -> studio.ThreadingHTTPServer:
    studio_actions._reset_runner()
    httpd = studio.make_server(demo_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        studio_actions._reset_runner()


@pytest.fixture
def page(e2e_server: studio.ThreadingHTTPServer):
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        pg.goto(f"{base}/#publish")
        yield pg
        context.close()
        browser.close()


@pytest.fixture
def editor(page):
    """进第一篇文章的编辑 tab 并等 CM5 挂载完成（vendor 本地链载 ~百 ms 级）。"""
    page.locator("#article-list a.article-card").first.click()
    page.wait_for_selector(".ed-stack .CodeMirror", timeout=10000)
    return page


def test_cm_mounts_and_replaces_textarea(editor) -> None:
    """CM5 挂载：实例替换 textarea（隐藏），工具栏 17 钮 + 大纲侧栏 + 状态栏扩展就位。

    B3（2026-11-26）插入媒体批：工具栏 +🎬（ED_BTNS media 项）——原 16 钮基线
    随实现更新（锚的是「按钮数与 ED_BTNS 一致」，不是某个数字本身）。"""
    page = editor
    expect(page.locator(".ed-stack .CodeMirror")).to_be_visible()
    expect(page.locator("#edit-area")).to_be_hidden()  # fromTextArea 隐藏原 textarea
    expect(page.locator("#edit-gutter")).to_be_hidden()  # 旧行号列退役（.ed-cm）
    expect(page.locator("#edit-hl")).to_be_hidden()  # 旧 overlay 退役
    assert page.locator("#ed-bar .ed-tb").count() == 17, "工具栏应 17 钮（含插入媒体/专注/打字机）"
    expect(page.locator("#ed-outline")).to_be_visible()
    for sid in ("ed-sect", "ed-paras", "ed-aib", "ed-read", "ed-goal",
                "ed-sync", "ed-focus-btn", "ed-tw", "ed-sprint"):
        assert page.locator(f"#{sid}").count() == 1, sid
    # 正文已同步进 CM，且 textarea（数据面）与 CM 一致
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert len(val) > 50
    assert page.evaluate(
        "() => document.getElementById('edit-area').value === window.__mdpubEditor.cm.getValue()")


def test_toolbar_insertion_bold_and_heading(editor) -> None:
    """工具栏插入：B 包裹/解包切换、H2 前缀切换（replaceSelection 语义的按钮面）。"""
    page = editor
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('# 标题\\n\\n正文段落');
      cm.setSelection({line: 2, ch: 0}, {line: 2, ch: 4});
    }""")
    page.locator('[data-edb="bold"]').click()
    assert page.evaluate("() => window.__mdpubEditor.cm.getLine(2)") == "**正文段落**"
    page.locator('[data-edb="bold"]').click()  # 再点解包
    assert page.evaluate("() => window.__mdpubEditor.cm.getLine(2)") == "正文段落"
    page.locator('[data-edb="h2"]').click()
    assert page.evaluate("() => window.__mdpubEditor.cm.getLine(2)") == "## 正文段落"
    # 代码块（无选区）：插入围栏并落位首行内容行
    page.evaluate("() => window.__mdpubEditor.cm.setCursor({line:4,ch:0})")
    page.locator('[data-edb="code"]').click()
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert "```" in val


def test_shortcuts_bold_link_and_search(editor) -> None:
    """快捷键：⌘B 包裹、⌘K 链接（且不撞全局命令面板）、⌘F 搜索条。"""
    page = editor
    page.locator(".ed-stack .CodeMirror").click()  # 先聚焦（点击会清选区）
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('hello world');
      cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 5});
    }""")
    page.keyboard.press("Meta+b")
    assert page.evaluate("() => window.__mdpubEditor.cm.getLine(0)") == "**hello** world"
    page.keyboard.press("Meta+k")
    assert page.evaluate("() => window.__mdpubEditor.cm.getLine(0)") == "**[hello](url)** world"
    # ⌘K 在 CM 内是链接，不触发全局命令面板
    assert page.evaluate("() => document.getElementById('cp').hidden")
    # ⌘F 打开搜索条：计数 + 回车跳选
    page.keyboard.press("Meta+f")
    expect(page.locator("#ed-find")).to_be_visible()
    page.fill("#ed-find-q", "world")
    page.keyboard.press("Enter")
    sel = page.evaluate("() => window.__mdpubEditor.cm.getSelection()")
    assert sel == "world"


def test_focus_mode_f11_toggle(editor) -> None:
    """专注模式：F11 切换 editor-focus（隐藏大纲/预览/工具栏）+ 段落聚焦淡出类。"""
    page = editor
    page.locator(".ed-stack .CodeMirror").click()
    page.keyboard.press("F11")
    panel_cls = page.get_attribute("#panel-edit", "class") or ""
    assert "editor-focus" in panel_cls and "ed-para" in panel_cls
    assert not page.locator("#ed-outline").is_visible()
    assert not page.locator(".pv-stage").is_visible()
    assert not page.locator("#ed-bar").is_visible()
    # 当前行挂 ed-cur（段落聚焦锚）
    wrap = page.evaluate(
        "() => window.__mdpubEditor.cm.lineInfo(window.__mdpubEditor.cm.getCursor().line).wrapClass")
    assert wrap == "ed-cur"
    page.keyboard.press("F11")  # 再按退出
    panel_cls = page.get_attribute("#panel-edit", "class") or ""
    assert "editor-focus" not in panel_cls and "ed-para" not in panel_cls


def test_outline_parse_and_click_jump(editor) -> None:
    """大纲解析：H1/H2/H3 结构+分节字数；点击跳转对应行。"""
    page = editor
    page.evaluate("""() => {
      window.__mdpubEditor.cm.setValue('# 一级\\n甲甲甲\\n## 二级\\n乙乙乙乙\\n### 三级\\n丙丙');
    }""")
    page.wait_for_timeout(400)  # change 去抖 200ms 后刷新大纲
    items = page.locator(".ed-ol-item")
    expect(items).to_have_count(3)
    assert page.locator(".ed-ol-item.lv1 .ed-ol-t").text_content() == "一级"
    chars = page.evaluate(
        "() => Array.from(document.querySelectorAll('.ed-ol-item .ed-ol-c')).map(e=>+e.textContent)")
    # H1=甲3+「##二级」4+乙4+「###三级」5+丙2=18 / H2=乙4+「###三级」5+丙2=11 / H3=丙2
    assert chars == [18, 11, 2], chars
    page.locator(".ed-ol-item").nth(2).click()  # 点 ### 三级（line 4）
    assert page.evaluate("() => window.__mdpubEditor.cm.getCursor().line") == 4


def test_wordcount_and_reading_time(editor) -> None:
    """字数与阅读时长：状态栏总字数与 ⏱ 分钟随正文更新（400 字/分）。"""
    page = editor
    page.evaluate("() => window.__mdpubEditor.cm.setValue('a'.repeat(800))")
    page.wait_for_timeout(400)
    expect(page.locator("#ed-chars")).to_have_text("800")
    expect(page.locator("#ed-read")).to_have_text("2 分钟")


def test_article_switch_resync_without_dirty(editor) -> None:
    """文章切换重同步：换第二篇正文跟随、无重复 UI、不误标 dirty。"""
    page = editor
    v1 = page.evaluate("() => window.__mdpubEditor.cm.getValue().slice(0, 10)")
    # 回发布中心 → 进第二篇（hash 导航）
    page.evaluate("() => { location.hash = '#publish'; }")
    page.wait_for_timeout(400)
    page.locator("#article-list a.article-card").nth(1).click()
    page.wait_for_selector(".ed-stack .CodeMirror", timeout=10000)
    page.wait_for_timeout(800)  # mountEdit 载入 + resync
    v2 = page.evaluate("() => window.__mdpubEditor.cm.getValue().slice(0, 10)")
    assert v1 != v2, "第二篇文章正文应不同"
    assert page.locator("#ed-bar").count() == 1  # 不重复建工具栏
    assert page.locator(".ed-stack .CodeMirror").count() == 1
    assert page.evaluate(
        "() => document.getElementById('edit-area').value === window.__mdpubEditor.cm.getValue()")
    expect(page.locator("#ed-saved")).to_contain_text("已保存")  # 程序性置入不误标 dirty


# ══ E-2 内联研究面板（§3.2）：⌘⇧S/📚 → 搜索 → 插入 → Esc 光标回归 ══


def _seed_materials():
    """MUSESPIRAL_HOME 已被 _isolate_home 指向 tmp——素材按请求现读，任意时机落库即可。"""
    from musespiral.core.materials import model
    from musespiral.services import materials_service

    materials_service.add_material(model.make_material(
        mtype="text", source_id="kb", uri="docs/wan.md", title="Wan 架构拆解",
        license="CC-BY", payload={"text": "性能追平商业产品，社区生态比性能更重要"},
        fetched_at="2026-11-19T00:00:00"))
    materials_service.add_material(model.make_material(
        mtype="image", source_id="media_gen", uri="gen://demo",
        title="AI 对比图", license="AI-GENERATED",
        payload={"local_path": "materials/files/bb.png",
                 "meta": {"width": 1280, "height": 720}},
        fetched_at="2026-11-19T01:00:00"))


def test_inline_panel_shortcut_opens_with_focus(editor) -> None:
    """⌘⇧S（CM 聚焦态走 Shift-Cmd-S extraKeys）开面板：面板可见、焦点在搜索框。"""
    page = editor
    page.evaluate("() => { window.__mdpubEditor.cm.focus(); }")
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_visible()
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.id") == "ed-rp-q"
    # 再按一次（焦点在搜索框 → 文档级兜底）收起并回编辑器
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_hidden()
    assert page.evaluate("() => window.__mdpubEditor.cm.hasFocus()")


def test_inline_panel_toolbar_button_toggles(editor) -> None:
    """工具栏 📚（原「素材」钮改内联面板入口）开合面板。"""
    page = editor
    assert page.locator('[data-edb="mat"]').text_content() == "📚"
    page.locator('[data-edb="mat"]').click()
    expect(page.locator("#ed-rp")).to_be_visible()
    page.locator('[data-rp="close"]').click()
    expect(page.locator("#ed-rp")).to_be_hidden()
    assert page.evaluate("() => window.__mdpubEditor.cm.hasFocus()")  # Esc 同语义：✕ 也回编辑器


def test_inline_panel_search_insert_quote_escape(editor) -> None:
    """E-2 主链路（搜索/插入/Esc 光标回归）：⌘⇧S 开面板 → 输入命中素材
    （debounce 300ms 后 fetch）→ 插入引用格式（面板保持、光标落插入文本后）
    → Esc 收起、焦点与光标回编辑器原位置。"""
    _seed_materials()
    page = editor
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('第一段\\n\\n');
      cm.setCursor({line: 2, ch: 0});
      cm.focus();
    }""")
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_visible()
    page.fill("#ed-rp-q", "Wan")
    expect(page.locator(".ed-rp-row")).to_have_count(1, timeout=5000)
    assert page.locator(".ed-rp-tt").text_content() == "Wan 架构拆解"
    assert "性能追平商业产品" in page.locator(".ed-rp-x").text_content()
    assert page.locator(".ed-rp-meta").text_content().endswith("字")
    # 插入引用：光标处落 `> 来自[素材名]：摘录`；面板保持、焦点回搜索框
    page.locator(".ed-rp-row [data-rpins]").click()
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    # 种子文本无换行 → 摘录原样；压空白惯例只在含换行/连续空白时插入单空格
    assert "> 来自[Wan 架构拆解]：性能追平商业产品，社区生态比性能更重要\n" in val
    expect(page.locator("#ed-rp")).to_be_visible()  # 面板保持（可继续搜下一个）
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.id") == "ed-rp-q"
    cur1 = page.evaluate(
        "() => { const c = window.__mdpubEditor.cm.getCursor(); return {line: c.line, ch: c.ch}; }")
    assert cur1 == {"line": 3, "ch": 0}  # replaceSelection('end')：光标落插入文本后
    page.keyboard.press("Escape")
    expect(page.locator("#ed-rp")).to_be_hidden()
    assert page.evaluate("() => window.__mdpubEditor.cm.hasFocus()")
    cur = page.evaluate(
        "() => { const c = window.__mdpubEditor.cm.getCursor(); return {line: c.line, ch: c.ch}; }")
    assert cur == {"line": 3, "ch": 0}


def test_inline_panel_image_insert_and_empty_state(editor) -> None:
    """图片素材插入 `![](path)`（/api/materials/file 内容寻址 URL）；无匹配词显空态。"""
    _seed_materials()
    page = editor
    page.evaluate("() => { window.__mdpubEditor.cm.focus(); }")
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_visible()
    page.fill("#ed-rp-q", "对比图")
    expect(page.locator(".ed-rp-row")).to_have_count(1, timeout=5000)
    assert page.locator(".ed-rp-ico").text_content() == "🖼️"
    assert page.locator(".ed-rp-meta").text_content() == "1280×720"
    page.locator(".ed-rp-row [data-rpins]").click()
    from musespiral.core.materials import model as _m  # id 由 (source_id,type,uri) 稳定散列
    img_id = _m.make_material_id("media_gen", "image", "gen://demo")
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert f"![](/api/materials/file?id={img_id}&variant=full)" in val
    # 无匹配词 → 空态占位
    page.fill("#ed-rp-q", "绝不存在的词xyz")
    expect(page.locator(".ed-rp-empty")).to_contain_text("无匹配素材", timeout=5000)


# ── E-3 AI 微操作（正典 §3.3）：选中浮出 → POST → diff 弹窗 → 接受/拒绝 ──


def _seed_ai_profile(home: Path) -> None:
    """本地无 key 档案（端点不走真实网络——chat 已被假件顶替）。"""
    home.mkdir(parents=True, exist_ok=True)
    (home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )


def test_ai_menu_appears_on_selection_and_accept_writes_marked_block(editor, monkeypatch, tmp_path) -> None:
    """全链：选中 >10 字 → 浮出 5 钮 → 点「扩写」→ diff 弹窗红删绿增 →
    [接受] 原文被替换为 `<!--ai:block-->` 包裹的产出。"""
    from musespiral.infra.ai import gateway as gateway_mod

    _seed_ai_profile(tmp_path / "musespiral-home")

    def fake(messages, *, task, temperature=0.6, timeout=120, profile=None):
        assert task == "editor_expand"
        return "扩写后的整句话，补了细节。"

    monkeypatch.setattr(gateway_mod, "_real_chat", fake)

    page = editor
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('前言。\\n\\n原始那一句话需要被扩写。\\n\\n后记。');
      cm.setSelection({line: 2, ch: 0}, {line: 2, ch: 12});
      cm.focus();
    }""")
    # 短选区（≤10 字去空白）不浮出
    page.evaluate("() => window.__mdpubEditor.cm.setSelection({line:0,ch:0},{line:0,ch:3})")
    expect(page.locator("#ed-aimenu")).to_be_hidden()
    # 长选区浮出，5 钮齐备
    page.evaluate("() => window.__mdpubEditor.cm.setSelection({line:2,ch:0},{line:2,ch:12})")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    assert page.locator("#ed-aimenu .ed-ai-b").count() == 5
    # 点「扩写」→ 弹窗；红删绿增加载完成后渲染
    page.locator('[data-edai="expand"]').click()
    expect(page.locator("#ed-ai-dlg")).to_be_visible()
    expect(page.locator("#ed-ai-new")).to_contain_text("扩写后的整句话", timeout=5000)
    assert page.locator("#ed-ai-old del").count() >= 1, "原文侧应有删除段（红删）"
    # 接受 → 原文被替换 + 标记包裹
    page.locator("#ed-ai-acc").click()
    expect(page.locator("#ed-ai-dlg")).to_be_hidden()
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert "<!--ai:block-->\n扩写后的整句话，补了细节。\n<!--/ai:block-->" in val, val
    assert "原始那一句话需要被扩写。" not in val
    expect(page.locator("#ed-aib")).to_have_text("0"), "AI 块计数按 <!--b:--> 锚计，微操作标记另算"


def test_ai_reject_keeps_text_untouched(editor, monkeypatch, tmp_path) -> None:
    """[拒绝] 只关闭弹窗：正文零改动（AI 只起草，写好才落笔）。"""
    from musespiral.infra.ai import gateway as gateway_mod

    _seed_ai_profile(tmp_path / "musespiral-home")
    monkeypatch.setattr(
        gateway_mod, "_real_chat",
        lambda messages, *, task, temperature=0.6, timeout=120, profile=None: "不该出现的产出")

    page = editor
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('一字不改的正文，选它一下再拒绝。');
      cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 12});
      cm.focus();
    }""")
    before = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    page.locator('[data-edai="rewrite"]').click()
    expect(page.locator("#ed-ai-new")).to_contain_text("不该出现的产出", timeout=5000)
    page.locator("#ed-ai-rej").click()
    expect(page.locator("#ed-ai-dlg")).to_be_hidden()
    after = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert after == before, "拒绝必须一字不改"


def test_ai_error_renders_and_disables_accept(editor, tmp_path) -> None:
    """AI 未配置：弹窗显示可读错误且「接受」置灰（拒绝仍可关闭）。"""
    page = editor  # 不写 ai.json → /api/meta 的 ai_configured=false，端点回 ok:false
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('未配置环境下也点一下扩写看看。');
      cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 14});
      cm.focus();
    }""")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    page.locator('[data-edai="expand"]').click()
    expect(page.locator("#ed-ai-dlg")).to_be_visible()
    expect(page.locator("#ed-ai-err")).to_contain_text("AI 未配置", timeout=5000)
    assert page.locator("#ed-ai-acc").is_disabled()
    page.locator("#ed-ai-rej").click()
    expect(page.locator("#ed-ai-dlg")).to_be_hidden()


# ══ 三线交界（E-2×E-3 集成批）：⌘⇧S(搜索) × 选中浮出(AI) 不冲突 ══


def test_junction_selection_priority_over_search(editor) -> None:
    """选中优先级>搜索 的四步行为面：

    1) 选中 >10 字（AI 菜单浮出）按 ⌘⇧S：面板不开、焦点不被搜索框夺走、菜单仍在；
    2) 收起选区后 ⌘⇧S 照常开面板（搜索入口未被劫持成死键）；
    3) 面板开着时再选中达标文字：AI 菜单照常浮出（反向不设限——搜索不压制选中）；
    4) Esc 收拾顺序：先收 AI 菜单（选区原样保留）→ 再收内联面板。
    """
    page = editor
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('一句足够长可以被选中的正文内容。');
      cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 12});
      cm.focus();
    }""")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    # 1) 选中态按 ⌘⇧S：搜索面板不开、焦点仍在编辑器、AI 菜单不受影响
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_hidden()
    assert page.evaluate("() => window.__mdpubEditor.cm.hasFocus()"), \
        "选中态下 ⌘⇧S 不得把焦点夺给搜索框"
    expect(page.locator("#ed-aimenu")).to_be_visible()
    # 工具栏 📚 同一裁决（显式鼠标路径同gate，不开面板）
    page.locator('[data-edb="mat"]').click()
    expect(page.locator("#ed-rp")).to_be_hidden()
    # 2) 收起选区（点击落点=collapse）后 ⌘⇧S 照常开面板
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setCursor({line: 0, ch: 12});
      cm.focus();
    }""")
    expect(page.locator("#ed-aimenu")).to_be_hidden()
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_visible()
    # 3) 面板开着时选中达标文字：AI 菜单照常浮出（反向不设限）
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setSelection({line: 0, ch: 0}, {line: 0, ch: 12});
      cm.focus();
    }""")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    expect(page.locator("#ed-rp")).to_be_visible()
    # 4) Esc 先收 AI 菜单（选区保留），再收内联面板
    page.keyboard.press("Escape")
    expect(page.locator("#ed-aimenu")).to_be_hidden()
    assert page.evaluate("() => window.__mdpubEditor.cm.getSelection()") == "一句足够长可以被选中的正"
    expect(page.locator("#ed-rp")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#ed-rp")).to_be_hidden()


# ══ E-2→E-3→E-4 全链冒烟（三线交界后的成品链）══════════════════


def test_smoke_full_chain_editor_search_ai_skeleton(editor, monkeypatch, tmp_path) -> None:
    """全链冒烟：编辑器 → ⌘⇧S 搜素材 → 插入引用 → 选中文字 → AI 扩写 →
    diff 红删绿增 → 接受写回（<!--ai:block--> 包裹）→ 洞察页 ⚡极速启动 →
    生成期流式逐块可见（F2）→ 开始写作落文章编辑器·骨架已就位（F1）。

    AI 双消费方（editor_expand 走 chat_policed / compose 走 client.chat_stream）
    统一在 client._post_chat_stream 接缝点打桩（文档注明的测试 mock 面），按请求体
    「【选中文字】」标记分流：编辑器五动作回整段 JSON；compose 回**逐块 SSE
    生成器**（0.7s/块）——E-4 流式链（chat_stream 增量→job partial 节流发布→
    前端轮询重绘）以真实后端路径过流。快照在首载后落盘，洞察段前整页刷新
    一次走真实 boot 装载路径（loadRadar/qSnapshot 都只在 boot 跑——这是产品
    装载契约，非测试偷懒）。
    """
    import io as _io
    import time as _time

    from musespiral.infra.ai import client as ai_client

    _seed_materials()
    _seed_ai_profile(tmp_path / "musespiral-home")
    snap_dir = tmp_path / "musespiral-home" / "topic_radar"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "latest.json").write_text(json.dumps({
        "generated_at": "2026-11-19T00:00:00",
        "inputs": {"radar_n": 1, "kb_n": 0, "down_sources": []},
        "candidates": [{"title": "三线交界冒烟选题", "kind": "kb", "score": 70,
                        "reasons": ["冒烟"], "refs": {}}],
        "ai_enhanced_n": 0,
    }, ensure_ascii=False), encoding="utf-8")

    editor_gen = "扩写后的句子，比原来更丰满，补足了细节与衔接。"

    def _sse(content: str) -> bytes:
        return ("data: " + json.dumps(
            {"choices": [{"delta": {"content": content}}]}) + "\n").encode("utf-8")

    def compose_stream():
        # 逐块 SSE + 0.7s 间隔：拉长生成窗，让 600ms 轮询必落在运行期——
        # 「等待期弹窗内骨架逐块长出」（F2）的行为面证据由此可等待可断言
        for piece in ("<<<block kind:heading>>>冒烟骨架：三段式\n\n",
                      "<<<block kind:para>>>第一段要点。\n\n",
                      "<<<block kind:para>>>第二段要点。"):
            yield _sse(piece)
            _time.sleep(0.7)
        yield b"data: [DONE]\n"

    def fake_post(url, headers, body, timeout):
        req = json.loads(body)
        content = "".join(m.get("content", "") for m in req.get("messages", []))
        if "【选中文字】" in content:
            return _io.BytesIO(json.dumps(
                {"choices": [{"message": {"content": editor_gen}}]}).encode("utf-8"))
        return compose_stream()

    monkeypatch.setattr(ai_client, "_post_chat_stream", fake_post)

    page = editor
    # ── 段 1：编辑器 → ⌘⇧S 搜 → 插入引用（E-2）──
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      cm.setValue('开头段落。\\n\\n这里有一句足够长的正文等待被扩写。\\n\\n结尾。');
      cm.setCursor({line: 0, ch: 5});
      cm.focus();
    }""")
    page.keyboard.press("Meta+Shift+s")
    expect(page.locator("#ed-rp")).to_be_visible()
    page.fill("#ed-rp-q", "Wan")
    expect(page.locator(".ed-rp-row")).to_have_count(1, timeout=5000)
    page.locator(".ed-rp-row [data-rpins]").click()
    assert "> 来自[Wan 架构拆解]：" in page.evaluate(
        "() => window.__mdpubEditor.cm.getValue()")
    # ── 段 2：选中文字 → AI 扩写 → diff → 接受（E-3）──
    # （引用插入会在目标句前多出空行——按内容定位行，不硬编码行号）
    page.evaluate("""() => {
      const cm = window.__mdpubEditor.cm;
      const line = cm.getValue().split('\\n').indexOf('这里有一句足够长的正文等待被扩写。');
      cm.setSelection({line: line, ch: 0}, {line: line, ch: 14});
      cm.focus();
    }""")
    expect(page.locator("#ed-aimenu")).to_be_visible()
    page.locator('[data-edai="expand"]').click()
    expect(page.locator("#ed-ai-dlg")).to_be_visible()
    expect(page.locator("#ed-ai-new")).to_contain_text("扩写后的句子", timeout=5000)
    assert page.locator("#ed-ai-old del").count() >= 1, "diff 原文侧应有红删段"
    assert page.locator("#ed-ai-new ins").count() >= 1, "diff 产出侧应有绿增段"
    assert page.locator("#ed-ai-stat").text_content().strip() != ""
    page.locator("#ed-ai-acc").click()
    expect(page.locator("#ed-ai-dlg")).to_be_hidden()
    val = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert f"<!--ai:block-->\n{editor_gen}\n<!--/ai:block-->" in val, val
    # ── 段 3：洞察页 ⚡极速启动 → 生成期流式可见（F2）→ 开始写作落编辑器（F1）──
    page.reload()
    page.wait_for_selector("#view-publish, #view-article, #view-compose", timeout=10000)
    page.evaluate("() => { location.hash = '#insight'; }")
    expect(page.locator("#view-insight")).to_be_visible()
    expect(page.locator(".radar-row")).to_have_count(1, timeout=10000)
    launch = page.locator(".qs-launch")
    expect(launch).to_have_count(1, timeout=10000)
    launch.click()
    expect(page.locator("#qs-overlay")).to_be_visible()
    assert page.locator("#qs-overlay [data-qs-mode]").count() == 4, "骨架模式四 chips"
    page.locator("#qs-go").click()
    # F2 生成期流式：job 行 partial 节流发布 → 600ms 轮询在等待期即重绘
    # 骨架（标签「生成中 · N 块」只在运行期出现，终态被「骨架就绪」覆盖——
    # 能等到它即为「不是干等」的行为面证据）
    page.wait_for_function(
        "() => /生成中 · \\d+ 块/.test((document.getElementById('qs-prog-label')||{}).textContent||'')",
        timeout=15000)
    ready = page.locator("#qs-ready")
    expect(ready).to_be_enabled(timeout=30000)
    assert page.locator(".qs-block").count() >= 3, "骨架应揭示 ≥3 块（heading+2 para）"
    # F1 开始写作：keep-all 快速裁决 → 采纳入稿 → 文章编辑器深链（正典 §3.4
    # 「跳转到编辑器（骨架已就位）」——不再是 compose 会话裁决面）
    ready.click()
    page.wait_for_function(
        "() => location.hash.indexOf('#article?path=') === 0", timeout=15000)
    expect(page.locator("#view-article")).to_be_visible()
    expect(page.locator("#view-compose")).to_be_hidden()
    page.wait_for_selector(".ed-stack .CodeMirror", timeout=10000)
    expect(page.locator("#ed-rp")).to_be_hidden()  # 交界契约面在编辑器落地后仍守
    doc = page.evaluate("() => window.__mdpubEditor.cm.getValue()")
    assert "冒烟骨架：三段式" in doc and "第一段要点" in doc, doc
    assert "第二段要点" in doc, doc
