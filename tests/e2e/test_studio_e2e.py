"""Studio E2E（R9，正典 §四-R9）：playwright chromium 驱动真实 ui.html 交互链路。

前置：playwright（pyproject optional-dependencies 的 browser 组）+ 已安装
chromium（`playwright install chromium`）。未安装 playwright 时本模块整体
skip——tests/conftest.py 在收集期已按可导入性决定是否收集 tests/e2e/，
这里的 pytest.importorskip 是显式指定文件路径时的兜底（skip 而非 error）。

AI/网络依赖一律在进程内 monkeypatch（服务线程与测试同进程）：e2e 验证的是
UI 交互链路（点击→请求→面板呈现），不是真实 adapt/publish 动作。

三用例（正典 §四-R9）：
  1. 队列渲染出卡片（demo fixture 三篇演示文章进队列，衍生文件不进）
  2. 点卡片进详情、切 tab（checks 面板检查行 / actions 面板平台按钮）
  3. 提交 mock 动作后任务面板出现记录（adapt 确认弹层→job done→任务中心）
"""
from __future__ import annotations

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
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral（jobs.jsonl / 发布日志隔离）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    """演示工作区：复制 studio_demo fixture（用副本服务——动作产物可写入，
    不污染仓库内 fixture；.note.md 与 .pack 产物随包复制齐全）。"""
    root = tmp_path / "studio-demo"
    shutil.copytree(FIXTURES / "studio_demo", root)
    return root


@pytest.fixture
def e2e_server(demo_root: Path) -> studio.ThreadingHTTPServer:
    """起真实 Studio 服务器（127.0.0.1 + 系统分配端口）。

    前后各 _reset_runner 一次：清上一用例遗留的共享 runner（jobs.jsonl 在
    MUSESPIRAL_HOME 隔离下本就干净，重置只关线程池），服务线程与测试同进程——
    monkeypatch 对动作依赖即时生效。
    """
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
    """chromium 页面，已停在发布中心（ui.html 以 #publish 为默认路由）。"""
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        pg.goto(f"{base}/#publish")
        yield pg
        context.close()
        browser.close()


def test_queue_renders_article_cards(page) -> None:
    """队列视图渲染卡片：demo 三篇演示文章进队列（配对笔记/pack 不进）。"""
    cards = page.locator("#article-list a.article-card")
    expect(cards).to_have_count(3)
    expect(cards.first.locator(".card-title")).to_contain_text("Studio 演示")
    # 统计行（评审补批 P2-2 与 fb-seg 同口径）：demo fixture 实测
    # total=3 / draft=3（三篇均 draft）/ pending=2（01+03 干净草稿）/ reviewed=0
    # / published=0（demo_ 前缀日志行判除，publish_log_service §1.2）；
    # 「有问题 1」只在筛选芯片（count-fail），不再混入统计行
    expect(page.locator("#queue-sub")).to_have_text("共 3 篇 · 草稿 3 · 待审 2 · 已审 0 · 已发布 0")


def test_open_detail_and_switch_tabs(page) -> None:
    """点卡片进详情（标题/返回链，默认编辑 Tab 分屏），切「检查」与「操作」tab 面板装载。

    Phase 3：详情默认编辑 Tab；F1 编辑器批（2026-11-19）起编辑 Tab 由 CM5
    （本地 vendor）替换 textarea——断言改为 CM 实例挂载、原 textarea 隐藏保留
    为数据面。检查 Tab 改三组 <details>（FAIL 默认展开）——展开首个分组后断言
    .check-row 可见。
    """
    page.locator("#article-list a.article-card").first.click()
    expect(page.locator("#view-article")).to_be_visible()
    expect(page.locator("#detail-title")).to_contain_text("Studio 演示")
    expect(page.locator("#panel-edit")).to_be_visible()  # 默认编辑 Tab（分屏）
    expect(page.locator(".ed-stack .CodeMirror")).to_be_visible()  # F1：CM5 已挂载
    expect(page.locator("#edit-area")).to_be_hidden()  # textarea 隐藏保留（数据面）

    page.locator('[data-tab="checks"]').click()
    expect(page.locator("#panel-checks")).to_be_visible()
    expect(page.locator("#panel-preview")).to_be_hidden()  # 切走后原面板隐藏
    expect(page.locator("#check-list summary").first).to_be_visible()  # 分组标题
    page.locator("#check-list details").first.locator("summary").click()  # 展开首个分组
    expect(page.locator("#check-list .check-row").first).to_be_visible()

    page.locator('[data-tab="actions"]').click()
    expect(page.locator("#panel-actions")).to_be_visible()
    # manual_pack 平台（xhs/zhihu/channels/douyin）渲染变体按钮
    expect(page.locator('#adapt-buttons button[data-adapt="xhs"]')).to_be_visible()


def test_mock_adapt_action_shows_in_jobs_panel(page, monkeypatch, demo_root: Path) -> None:
    """提交 mock 动作 → 确认弹层 → 任务面板出现 done 记录（R4 任务面板链路）。

    mock 掉 adapt.generate（AI/启发式改写一并绕开，零网络）：e2e 验证的是
    「点按钮→confirm→POST→job→任务中心呈现」的 UI 交互链路，非真实改写。
    """
    from musespiral.core.article import body_sha1
    import frontmatter

    def _fake_generate(article_path: Path, platform: str) -> Path:
        out = article_path.with_name(f"{article_path.stem}.{platform}.md")
        src_sha1 = body_sha1(frontmatter.load(article_path).content)
        out.write_text(
            "---\n"
            f"title: mock 变体\nplatform: {platform}\n"
            f"source: {article_path.name}\nsource_sha1: {src_sha1}\n"
            "---\n\nmock 改写正文。\n",
            encoding="utf-8",
        )
        return out

    monkeypatch.setattr(studio_actions.adapt, "generate", _fake_generate)

    page.locator("#article-list a.article-card").first.click()
    page.locator('[data-tab="actions"]').click()
    page.locator('#adapt-buttons button[data-adapt="xhs"]').click()

    # 人审闸门：确认弹层出现 → 点确认才发 POST
    expect(page.locator("#modal")).to_be_visible()
    page.locator("#modal-confirm").click()

    # 任务面板：出现 adapt 记录且到 done（mock 生成毫秒级完成；UI 侧
    # trackJob 每 800ms 轮询后刷新面板，expect 自动重试覆盖该延迟）
    page.locator("#jobs-btn").click()
    row = page.locator("#jobs-menu .job-row", has_text="adapt")
    expect(row).to_be_visible()
    expect(row.locator(".badge.done")).to_be_visible()
    # 产物落盘；重载后返回队列，变体徽章经 source 回链挂到源文章卡片
    variant = demo_root / "03_photo_essay.xhs.md"
    assert variant.is_file()
    page.reload()  # 确定性重装载（避免依赖 trackJob 异步刷新时序）
    expect(page.locator("#view-article")).to_be_visible()
    page.locator("#back-link").click()
    expect(page.locator("#article-list .tag.accent", has_text="xhs")).to_be_visible()


def test_compose_full_round_trip(page, monkeypatch, demo_root: Path, tmp_path: Path) -> None:
    """创作台全链一轮（第十八期 §2.8 前端行 e2e）：第四 Tab→建会话→素材入池+
    章节勾选→mock 再生成（job）→三态裁决→二轮（锚继承/AI 新块/按块对照）→
    采纳 confirm→正稿入队列→R2 锚滤除（编辑 Tab 实时预览不外显 <!--b:id-->）。

    mock compose_service._chat_stream（零网络；E-4 流式骨架批起 generate 恒传
    on_delta 走增量入口——假件按块回调，partial 发布链同被走过）：一轮无锚三块，
    二轮带 b1/b2 锚 + 一新块（文本与上轮块刻意不相似——不触发 difflib≥0.6
    回退继承，恒新块）。
    """
    from musespiral.services import compose_service

    source = tmp_path / "kb_note.md"  # 素材在 root 外（知识库文档常态），绝对路径入池
    source.write_text("## 一、起步\n\n起步正文。\n\n## 二、进阶\n\n进阶正文。\n", encoding="utf-8")
    calls = {"n": 0}

    def _fake_chat(messages, *, task=None, on_delta=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            reply = (
                "<<<block kind:heading>>># 缓存设计实录\n\n"
                "<<<block kind:para>>>正文段，综上所述空值缓存最稳妥。\n\n"
                "<<<block kind:para>>>结尾段。"
            )
        else:
            reply = (
                "<<<block id:b1 kind:heading>>># 缓存设计实录\n\n"
                "<<<block id:b2 kind:para>>>按人工方向反馈改写的正文。\n\n"
                "<<<block kind:para>>>完全不同的收尾：谈谈发布流水线的衔接。"
            )
        if on_delta is not None:  # 保留 \n\n 定界换行（丢了 partial 解析会并块）
            chunks = reply.split("\n\n")
            for piece in [c + "\n\n" for c in chunks[:-1]] + chunks[-1:]:
                on_delta(piece)
        return reply

    monkeypatch.setattr(compose_service, "_chat_stream", _fake_chat)

    # 第四导航 Tab → 空态引导 → 建会话（slug+标题）进三栏
    page.locator('[data-nav="compose"]').click()
    expect(page.locator("#view-compose")).to_be_visible()
    expect(page.locator("#cm-empty")).to_be_visible()
    page.locator("#cm-new-slug").fill("e2e-demo")
    page.locator("#cm-new-title").fill("缓存设计实录")
    page.locator("#cm-new-btn").click()
    expect(page.locator("#cm-grid")).to_be_visible()
    # §四前端行验收点：AI 产出不经「采纳」绝不入 draft.md（e2e 字节级断言）
    draft0 = (demo_root / "e2e-demo.compose" / "draft.md").read_bytes()

    # 素材入池（手动绝对路径）→ 章节勾选树（known_sections 数据驱动）→ 预算仪表
    page.locator("#cm-add-path").fill(str(source))
    page.locator("#cm-add-btn").click()
    tree = page.locator("#cm-sources .cm-sec input[type=checkbox]")
    expect(tree).to_have_count(2)
    tree.first.check()  # 勾选「一、起步」→ 重发 add 带 sections（同 path=更新勾选）
    expect(page.locator("#cm-sources")).to_contain_text("已选 1/2 章")
    expect(page.locator("#cm-budget")).to_contain_text("8000")

    # 再生成走 job 面板（mock chat 毫秒级完成；trackJob 轮询后重载渲染）
    page.locator("#cm-generate-btn").click()
    expect(page.locator("#cm-blocks .cm-block")).to_have_count(3)
    expect(page.locator("#cm-round-title")).to_contain_text("第 1 轮")
    assert (demo_root / "e2e-demo.compose" / "draft.md").read_bytes() == draft0  # 一轮后 draft 不动

    # 三态裁决：保留 b1 / 编辑 b2（行内改文本）/ 丢弃 b3（方向备注）
    page.locator('[data-cm-keep="b1"]').click()
    expect(page.locator('#cm-blocks [data-cm-block="b1"] .badge.pass')).to_contain_text("保留")
    page.locator('[data-cm-edit="b2"]').click()
    area = page.locator("#cm-blocks .cm-edit-area")
    expect(area).to_be_visible()
    area.fill("人工定稿正文。")
    page.locator('[data-cm-save="b2"]').click()
    expect(page.locator('#cm-blocks [data-cm-block="b2"] .badge.warn')).to_contain_text("改后采纳")
    page.locator('[data-cm-drop="b3"]').click()
    page.locator(".cm-drop-row input").fill("冗余，下轮换个角度收束")
    page.locator('[data-cm-dropok="b3"]').click()
    expect(page.locator('#cm-blocks [data-cm-block="b3"] .badge.fail')).to_contain_text("已丢弃")
    expect(page.locator("#cm-round-meta")).to_contain_text("已裁决 3/3")

    # 二轮：锚继承/AI 新块视觉区分 + N-1↔N 按块对照（新块/改写）
    page.locator("#cm-generate-btn").click()
    expect(page.locator("#cm-blocks .cm-block")).to_have_count(3)
    expect(page.locator('#cm-blocks [data-cm-block="b1"] .badge.running')).to_contain_text("锚继承")
    expect(page.locator('#cm-blocks [data-cm-block="b4"] .badge.queued')).to_contain_text("AI 新块")
    expect(page.locator("#cm-diff .cm-diffrow.add")).to_contain_text("b4")
    expect(page.locator("#cm-diff .cm-diffrow.chg")).to_contain_text("b2")

    # 二轮三块全保留 → 采纳入稿（confirm 弹层）→ 正稿入队列
    for bid in ("b1", "b2", "b4"):
        page.locator(f'[data-cm-keep="{bid}"]').click()
        expect(page.locator(f'#cm-blocks [data-cm-block="{bid}"] .badge.pass')).to_be_visible()
    assert (demo_root / "e2e-demo.compose" / "draft.md").read_bytes() == draft0  # 两轮+裁决全程不动
    page.locator("#cm-adopt-btn").click()
    expect(page.locator("#modal")).to_be_visible()
    page.locator("#modal-confirm").click()
    expect(page.locator(".toast.success")).to_contain_text("已入稿")
    assert (demo_root / "e2e-demo.md").is_file()
    assert (demo_root / "e2e-demo.compose" / "draft.md").read_bytes() != draft0  # 唯有 adopt 落盘

    page.locator('[data-nav="publish"]').click()
    expect(page.locator("#article-list a.article-card")).to_have_count(4)

    # R2 锚滤除：正稿 draft 带 <!--b:id--> 锚行，编辑 Tab 实时预览不外显锚文本
    page.locator("#article-list a.article-card", has_text="缓存设计实录").last.click()
    expect(page.locator("#edit-area")).to_be_visible()
    frame = page.frame_locator("#live-frame")
    expect(frame.locator("body")).not_to_contain_text("<!--b:")
    expect(frame.locator("body")).to_contain_text("按人工方向反馈改写的正文")


# ── P1 视图重构：六视图真实浏览器冒烟（视图层功能回归） ──────────────


def test_p1_six_views_smoke(e2e_server: studio.ThreadingHTTPServer) -> None:
    """P1 六视图（重构设计 §三）：逐 hash 进入，断言各视图核心元素真实渲染。

    漏斗带/生态带/集合轨/洞察图组全部是前端聚合（materials/articles/stats/
    jobs 载荷），demo fixture 下真实出数；素材详情抽屉走 m= 深链（detail 载荷）。
    """
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        # 发布中心：漏斗带四级 + 六档筛选 chips + 网格/列表切换
        pg.goto(f"{base}/#publish")
        expect(pg.locator("#funnel-band .fb-seg")).to_have_count(4)
        expect(pg.locator("#funnel-band .fb-seg").first).to_contain_text("草稿")
        expect(pg.locator("#queue-filters .chip")).to_have_count(6)
        pg.locator('[data-pview="list"]').click()
        expect(pg.locator("#pub-rows")).to_be_visible()
        # 源中枢：生态带四 tile + 源总表行（demo 下 kb/radar_hub 预置至少一行）
        pg.goto(f"{base}/#sources")
        expect(pg.locator("#eco-tiles .eco-tile")).to_have_count(4)
        expect(pg.locator("#eco-tiles .eco-tile").first).to_contain_text("启用源")
        pg.wait_for_selector("#src-rows tr[data-srcrow]")
        # 素材库：集合轨三段 + 密度 chips + 详情抽屉深链（无素材 fixture 下空态亦成立）
        pg.goto(f"{base}/#material")
        expect(pg.locator("#coll-rail")).to_be_visible()
        expect(pg.locator("#mat-dens .chip")).to_have_count(3)
        expect(pg.locator("#mat-drawer")).to_be_hidden()
        # 创作工坊：三栏骨架（无会话时空态；导航可达即视为骨架成立）
        pg.goto(f"{base}/#compose")
        expect(pg.locator("#cm-new-slug")).to_be_visible()
        # 洞察：创意漏斗五段 + 趋势柱图容器 + 环境健康折叠行
        pg.goto(f"{base}/#insight")
        expect(pg.locator("#idea-funnel .fb-seg")).to_have_count(5)
        expect(pg.locator("#idea-funnel .fb-seg").first).to_contain_text("采集")
        expect(pg.locator("#trend-chart")).to_be_visible()
        assert pg.locator("#health-card").get_attribute("open") is None  # 环境健康默认折叠
        # 设置：分区重组后 KB/AI/风格与底座两域仍可达
        pg.goto(f"{base}/#settings")
        expect(pg.locator("#kb-root-input")).to_be_visible()
        expect(pg.locator("#style-area")).to_be_visible()
        # 旧 hash 别名兼容：#queue → 发布中心、#dashboard → 洞察
        pg.goto(f"{base}/#queue")
        expect(pg.locator("#view-publish")).to_be_visible()
        pg.goto(f"{base}/#dashboard")
        expect(pg.locator("#view-insight")).to_be_visible()
        context.close()
        browser.close()


def test_f72_undo_stack_boundary(page) -> None:
    """F72-T3（e2e 冒烟标记，不进 CI）：撤销栈边界——排版 chips 交替点选 51 步，
    栈深恒 50（HUD 显示 50）；⌘Z 50 次后 HUD 消失（第 1 步快照已挤出即到底）；
    ⇧⌘Z 重做恢复计数。载入走缺省稿链路（demo 文章无 design.json→404→render）。"""
    page.evaluate("location.hash='#design'")
    expect(page.locator("#view-design")).to_be_visible()
    # 缺省稿自动载入（首篇文章）：舞台出 SVG 即 ready
    expect(page.locator("#dv-stage svg")).to_be_visible(timeout=15000)
    # 排版 chips 交替点选 51 次（同值点选是 no-op，必须交替产生 51 次提交）
    s_chip = page.locator('#dv-fs [data-dfs="S"]')
    m_chip = page.locator('#dv-fs [data-dfs="M"]')
    for i in range(51):
        (m_chip if i % 2 == 0 else s_chip).click()
    hud = page.locator("#dv-undo")
    expect(hud).to_have_text("撤销 50 步 · ⌘Z/⇧⌘Z")  # 栈深恒 50（第 1 快照被挤出）
    # ⌘Z 50 次：全部可回（栈内快照逐一弹出），到底后 HUD 消失
    for _ in range(50):
        page.keyboard.press("Meta+z")
    expect(hud).to_be_hidden()
    # ⇧⌘Z 重做：恢复一步后 HUD 复现
    page.keyboard.press("Shift+Meta+z")
    expect(page.locator("#dv-undo")).to_contain_text("撤销 1 步")


def test_d6_more_button_opens_ctx_menu(page, e2e_server: studio.ThreadingHTTPServer) -> None:
    """D6 右键暗示（设计终评 §三 P2-D6 方案 A）：素材行 hover 显 ⋯ 按钮，点击弹出
    与右键同源的 #ctx 菜单；点击菜单外关闭。

    回归锚（2026-11-17 集成轮证伪修复）：⋯ 左键打开菜单的同一 click 冒泡到
    document 级 click-away（ctxHide）曾闪开即关——ctxShow 头部 stopPropagation
    修复（ui.js「D6 集成修复」注）。素材经 /api/materials/register 登记制入库
    （零出网），hover 显隐是设计（.mat-more{display:none}/行 hover 才显）。
    """
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    resp = page.request.post(
        f"{base}/api/materials/register",
        data='{"url":"https://example.com/d6-e2e","title":"D6 冒烟素材","confirm":true}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200, resp.text()
    page.goto(f"{base}/#material")
    page.locator(".mat-row-sum").first.hover()
    more = page.locator(".mat-more").first
    expect(more).to_be_visible()
    assert more.get_attribute("title") == "更多操作（右键同）"
    more.click()
    expect(page.locator("#ctx")).to_be_visible()  # 修复前：闪开即关（hidden 恒真）
    items = page.locator("#ctx .cp-item")
    expect(items).to_have_count(3)
    expect(items.first).to_have_text("星标切换")
    page.locator("#view-material .view-head h1").click()  # 点菜单外任意处关闭
    expect(page.locator("#ctx")).to_be_hidden()


def test_cmd_digit_nav_global_and_article_tab_priority(page) -> None:
    """⌘1-7 分域回归（深构 §5.1/§八-4 裁决；2026-11-17 集成轮修复）：
    视图外=七视图导航（DNAV 序 sources/material/design/compose/publish/insight/
    settings）；#article 内 ⌘1-4 仍优先切 Tab（彼视图优先）、⌘5-7 照常导航。

    修复前缺陷：导航分支被 view-design 门限定在 #design 内，其余 6 视图
    ⌘1-7 全失效（#publish 下 Meta+3/7 停留原视图——/tmp/musespiral_m3_probe.py 复现）。

    ⏱ 稳定性判例（PL-3 复核修复批 2026-11-20）：本用例 6 连 expect 默认 5s
    超时对系统负载敏感——全量套件/并发浏览器时段实测 2/6 复现超时（隔离复跑
    全绿；负载判例=与前端改动无关的 view 可见性等待）。等待统一放宽到 15s，
    只影响失败路径耗时，不改断言语义。
    """
    # 视图外导航：#publish ⌘3 → #design；⌘7 → #settings
    expect(page.locator("#view-publish")).to_be_visible(timeout=15000)
    page.keyboard.press("Meta+3")
    expect(page.locator("#view-design")).to_be_visible(timeout=15000)
    page.keyboard.press("Meta+7")
    expect(page.locator("#view-settings")).to_be_visible(timeout=15000)
    # #article 内彼视图优先：⌘2 切「变体」Tab（hash 不动）；⌘5 跳 #publish
    page.evaluate("location.hash='#publish'")
    page.locator("#article-list a.article-card").first.click()
    expect(page.locator("#view-article")).to_be_visible(timeout=15000)
    page.keyboard.press("Meta+2")
    expect(page.locator("#panel-variants")).to_be_visible(timeout=15000)
    page.keyboard.press("Meta+5")
    expect(page.locator("#view-publish")).to_be_visible(timeout=15000)


def test_d3_kb_origin_renders_icon_element_not_markup(page, e2e_server: studio.ThreadingHTTPServer) -> None:
    """D-3 回归（验收报告 2026-11-17 §二）：设置「身份与存储」图标真渲染 + 数据转义不破。

    修复前缺陷：#kb-origin 赋值走 .textContent，I() 的 SVG 标记串被当纯文字
    （DOM 取证 childElementCount=0 / textContent 含 '<svg' / 窄屏同现）。
    修复后契约（ui.js renderSettings）：.innerHTML 装配 + kb.root 经 esc() 转义。

    本用例拦截 /api/settings 注入带 HTML 元字符的 root（含 <img onerror>/&/引号），
    断言：① check/warning 两态各渲染出 1 个真 svg 元素；② textContent 不含
    '<svg'（标记不再是文字）；③ 注入串以字面文本呈现且零 img 元素、onerror
    零执行（esc() 转义安全在 innerHTML 改法下仍成立）。
    """
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    hostile = '/demo/kb<img src=x onerror="window.__d3xss=1">&<b>"引号'
    state = {"root_exists": True}
    payload = {
        "kb": {"root": hostile, "root_exists": True, "origin": "kb_root.txt", "sources": []},
        "ai": {"profiles": {"profiles": {}, "default": "", "tasks": [], "routing": {}},
               "style": {}, "prompts": {}},
    }

    def _settings(route):
        body = dict(payload)
        body["kb"] = dict(body["kb"], root_exists=state["root_exists"])
        route.fulfill(json=body)

    page.route("**/api/settings", _settings)
    page.goto(f"{base}/#settings")
    expect(page.locator("#view-settings")).to_be_visible()

    # ① root_exists=true → check 图标真渲染为元素；标记不再当文字
    expect(page.locator("#kb-origin svg")).to_have_count(1)
    text = page.locator("#kb-origin").text_content()
    assert "<svg" not in text, text
    assert text.startswith("根："), text
    # ③ 注入面：hostile root 以字面文本呈现，零 img 元素，onerror 零执行
    assert hostile in text  # esc() 后回读为原字符（&amp;/&lt; 已解码回文本层）
    assert page.locator("#kb-origin img").count() == 0
    assert page.evaluate("window.__d3xss === undefined")

    # ① 续 root_exists=false → warning 图标同样真渲染
    state["root_exists"] = False
    page.goto(f"{base}/#publish")
    page.goto(f"{base}/#settings")
    expect(page.locator("#view-settings")).to_be_visible()
    expect(page.locator("#kb-origin svg")).to_have_count(1)
    assert "<svg" not in page.locator("#kb-origin").text_content()


def test_f1_usage_bar_renders_from_mock_reads(page, e2e_server: studio.ThreadingHTTPServer) -> None:
    """F1 用量条渲染冒烟（组合批集成轮 2026-11-18）：mock 两读面 → 真浏览器渲染。

    #settings 进入即 loadAiMeter：GET /api/ai/usage?days=7 + /api/ai/budget →
    #ai-meter 出 pbar 行（任务×档案 + 次数）与「日预算」行——渲染链路真实
    出数，数据面 mock（与 CLI 同数的读侧由 test_media_gen HTTP 用例锚定）。
    """
    import json as _json

    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    usage = {
        "ok": True, "days": 7, "window": "2026-11-12~2026-11-18",
        "rows": [
            {"date": "2026-11-18", "task": "refine", "profile": "cloud", "calls": 6,
             "hits": 2, "hit_rate": "33%", "chars_in": 900, "chars_out": 120},
            {"date": "2026-11-18", "task": "tag_suggest", "profile": "local", "calls": 2,
             "hits": 0, "hit_rate": "0%", "chars_in": 400, "chars_out": 60},
        ],
        "totals": {"calls": 8, "hits": 2, "chars_in": 1300, "chars_out": 180},
    }
    budget = {"ok": True, "budget": {"daily_calls": 200, "daily_out_chars": 40000, "per_task": {}}}

    page.route(
        "**/api/ai/usage*",
        lambda route: route.fulfill(status=200, content_type="application/json",
                                    body=_json.dumps(usage)),
    )
    page.route(
        "**/api/ai/budget",
        lambda route: route.fulfill(status=200, content_type="application/json",
                                    body=_json.dumps(budget)),
    )
    page.goto(f"{base}/#settings")
    expect(page.locator("#view-settings")).to_be_visible()

    bars = page.locator("#ai-meter .pbar")
    expect(bars).to_have_count(2)  # 任务×档案两行（命中不出账仍计入行）
    expect(bars.first).to_contain_text("refine · cloud")
    expect(bars.first).to_contain_text("6 次")
    expect(bars.nth(1)).to_contain_text("tag_suggest · local")
    # 预算行（两读面合成：usage 条 + budget 底行）
    expect(page.locator("#ai-meter")).to_contain_text("日预算 200 次 / 40000 字")
    # 条宽真实出数：refine(6) 是峰值行 → fill 宽 100%；tag_suggest(2) → ~33%
    widths = page.evaluate(
        "Array.from(document.querySelectorAll('#ai-meter .pbar-fill')).map(e=>e.style.width)"
    )
    assert widths[0] == "100%" and widths[1] == "33%"


def test_eight_views_reachable_smoke(e2e_server: studio.ThreadingHTTPServer) -> None:
    """八视图全可达 + 命令面板开关（M1 验收 #5 的 e2e 落点，2026-11-18 集成轮补）。

    为什么补：正典《musespiral-前端多文件化设计》§八 验收 #5 写着「playwright 真浏览器：
    八视图可达+⌘K 开关+jobs 下拉+toast 容器；e2e 10/10」，但既有套件实测只走
    publish/sources/material/compose/insight/settings 六条 hash（`test_p1_six_views_smoke`），
    #books 全无覆盖（`grep -n books tests/e2e/` 零命中）、命令面板全无所覆盖
    （`grep -n 'cp-input' tests/e2e/` 零命中）。功能面本身无缺口（独立探针实测八视图
    全可见、⌘K 开且 Esc 关、0 pageerror），但「e2e 10/10 证明了这些」属于把手工
    探针的结论记在套件名下。本用例把这句话变成真话。

    M1 双层产物的意义：壳只内含 HTML，JS/CSS 全靠 /assets/*；若静态面断了，
    本用例会是第一个红的（视图切了但什么都没渲染）。
    """
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    views = ["sources", "material", "books", "compose", "design",
             "publish", "insight", "settings"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        page_errors: list[str] = []
        pg.on("pageerror", lambda e: page_errors.append(str(e)))
        try:
            for view in views:
                pg.goto(f"{base}/#{view}")
                # 视图 section 可见（M1：八视图 HTML 内联在壳里，切换靠 hidden 属性）
                expect(pg.locator(f"#view-{view}")).to_be_visible()
                # 导航项高亮同步（renderNav）
                assert pg.locator(f'[data-nav="{view}"]').first.evaluate(
                    "el => el.classList.contains('is-active')"), f"{view} 导航未高亮"
                # 同时只应有一个 view 可见（showView 的互斥语义）
                assert pg.locator("section.view:not([hidden])").count() == 1, \
                    f"{view}: 可见视图数不为 1"

            # 文章详情深链（第九个视图 #article）
            pg.goto(f"{base}/#article?path=01_quick_start.md&tab=edit")
            expect(pg.locator("#view-article")).to_be_visible()

            # 命令面板：⌘K 开 → 输入框聚焦 → Esc 关
            pg.goto(f"{base}/#publish")
            expect(pg.locator("#cp")).to_be_hidden()
            pg.keyboard.press("Meta+k")
            expect(pg.locator("#cp")).to_be_visible()
            expect(pg.locator("#cp-input")).to_be_focused()
            pg.keyboard.press("Escape")
            expect(pg.locator("#cp")).to_be_hidden()

            assert page_errors == [], f"页面 JS 异常: {page_errors}"
        finally:
            context.close()
            browser.close()
