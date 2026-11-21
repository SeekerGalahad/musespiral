"""E-6 版本 diff 视图（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.7）。

断言面（任务书验收面逐条对应）：
  a) diff 算法（L2 纯）：增行 / 删行 / 改行（一删一增）的行数与 hunk 行号正确；
     两版相同 → identical 且无 hunk；空文件侧（全新增/全删除）正确；
  b) HTML 红删绿增：删除行包 `<del class="dl-del">`（绯红）、新增行
     `<ins class="dl-add">`（竹绿）、上下文行不包；文本一律 html.escape
     （正文里写 <script> 只以文本形态出现）；替换块配对行做**行内字符级
     精化**（改一个字只高亮那个字——批4 复核④，共同字符为普通文本、
     无共同字符/纯删纯增/超 1500 字符行退整行包裹）；
  c) 服务层：list_versions 首项为当前版（id="current"）、池内新→旧、
     旧无时间戳 .md.bak 尾部（id="legacy"）；diff_versions 双版读取与
     同版拒绝；restore_version 恢复 + 当前版先入池（恢复可再恢复）+
     current 拒绝恢复；
  d) 端点层（真实回环服务器）：GET 双态（无 v1/v2 只回清单 / 有则附 diff）、
     400 面（缺 path / 单侧版本 / 同版 / 未知版本 / context 非法）、
     POST confirm 闸门（缺 confirm → 400）与金路径恢复、版本不在池 → 200 {ok:false}；
  e) 契约锁：GET /api/article/diff 与 POST /api/article/restore 在
     CONTRACT_*_EXACT 且在真实路由表里；
  f) 前端：versions.js 纯函数（node 真实执行）、结构锚（tab 按钮 + panel +
     装载钩子）、零外联、/assets/versions.js 回环逐字节一致。
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.version.diff import (
    CTX,
    DEL,
    INS,
    diff_result,
    render_diff_html,
    summarize,
    unified_diff_lines,
)
from musespiral.services import version_service

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
VER_JS = STUDIO / "assets" / "versions.js"
TMPL = STUDIO / "ui.tmpl.html"
UI_JS = STUDIO / "ui.js"
CORE_JS = STUDIO / "assets" / "core.js"
SHELL = STUDIO / "ui.html"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


def _article(root: Path, name: str = "post.md", body: str = "第一段。\n") -> Path:
    path = root / name
    path.write_text(f"---\ntitle: 测试文章\n---\n\n{body}", encoding="utf-8")
    return path


def _craft_pool(art: Path, versions: list[str], prefix: str = "20261119-08010") -> list[str]:
    """按版本池命名约定造确定性时间戳版本（旧→新），返回时间戳列表。"""
    stamps: list[str] = []
    for i, body in enumerate(versions):
        ts = f"{prefix}{i}"
        stamps.append(ts)
        art.with_name(f"{art.name}.bak.{ts}").write_text(body, encoding="utf-8")
    return stamps


def _craft_full_pool(art: Path, bodies: list[str], prefix: str = "20261119-09010") -> list[str]:
    """同 `_craft_pool`，但写入**整文件**（frontmatter + 给定正文）——这样与
    当前版对比时只有正文差异，增删行数才是「改一行 = 1/1」。"""
    stamps: list[str] = []
    for i, body in enumerate(bodies):
        ts = f"{prefix}{i}"
        stamps.append(ts)
        art.with_name(f"{art.name}.bak.{ts}").write_text(
            f"---\ntitle: 测试文章\n---\n\n{body}", encoding="utf-8"
        )
    return stamps


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data)


@pytest.fixture
def server(root: Path):
    from musespiral.studio.actions import _reset_runner

    _reset_runner()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _reset_runner()


# ══ a) diff 算法（L2 纯）══════════════════════════════════════════


def test_added_line_only() -> None:
    """纯新增一行：added=1 / removed=0，该行 kind=ins 且带新侧行号。"""
    r = diff_result("甲\n乙\n", "甲\n乙\n丙\n")
    assert (r.added, r.removed, r.identical) == (1, 0, False)
    ins = [row for h in r.hunks for row in h.rows if row.kind == INS]
    assert len(ins) == 1
    assert ins[0].text == "丙"
    assert ins[0].old_no is None and ins[0].new_no == 3


def test_removed_line_only() -> None:
    """纯删除一行：removed=1 / added=0，该行 kind=del 且带旧侧行号。"""
    r = diff_result("甲\n乙\n丙\n", "甲\n乙\n")
    assert (r.added, r.removed) == (0, 1)
    dels = [row for h in r.hunks for row in h.rows if row.kind == DEL]
    assert [(d.text, d.old_no, d.new_no) for d in dels] == [("丙", 3, None)]


def test_modified_line_is_one_del_plus_one_ins() -> None:
    """改一行 = 一删一增（unified diff 口径）：两侧行号各自正确、上下文行双号并列。"""
    r = diff_result("甲\n乙\n丙\n", "甲\n乙改\n丙\n")
    assert (r.added, r.removed) == (1, 1)
    rows = [row for h in r.hunks for row in h.rows]
    assert [(x.kind, x.text, x.old_no, x.new_no) for x in rows] == [
        (CTX, "甲", 1, 1),
        (DEL, "乙", 2, None),
        (INS, "乙改", None, 2),
        (CTX, "丙", 3, 3),
    ]


def test_identical_text_has_no_hunks() -> None:
    """两版相同 → identical 且 hunks 为空（不渲染空骨架）。"""
    r = diff_result("同样的内容\n第二行\n", "同样的内容\n第二行\n")
    assert r.identical is True
    assert r.hunks == ()
    assert (r.added, r.removed) == (0, 0)
    assert unified_diff_lines("a\n", "a\n") == []


def test_empty_old_side_counts_all_as_added() -> None:
    """旧版为空 → 全部算新增（hunk 头 `@@ -0,0 +1,N @@`，行号从新侧 1 起）。"""
    r = diff_result("", "一\n二\n")
    assert (r.added, r.removed) == (2, 0)
    rows = [row for h in r.hunks for row in h.rows]
    assert [(x.kind, x.new_no) for x in rows] == [(INS, 1), (INS, 2)]


def test_context_window_respects_n() -> None:
    """上下文行数受 n 控制：n=0 只留改动行，n=1 前后各一行。"""
    old = "".join(f"行{i}\n" for i in range(1, 11))
    new = old.replace("行5\n", "行5改\n")
    tight = diff_result(old, new, context=0)
    loose = diff_result(old, new, context=1)
    assert len([r for h in tight.hunks for r in h.rows]) == 2  # 仅 -/+ 两行
    assert len([r for h in loose.hunks for r in h.rows]) == 4  # 前后各一 + -/+
    assert tight.added == loose.added == 1


def test_summarize_skips_frontmatter_fence_and_truncates() -> None:
    """摘要取首个非空非 `---` 行；超宽截断加省略号；空文件给可读占位。"""
    assert summarize("---\ntitle: X\n---\n\n正文首行。\n") == "title: X"
    assert summarize("---\n---\n\n正文首行。\n") == "正文首行。"
    assert summarize("") == "（空文件）"
    assert summarize("长" * 100).endswith("…")


# ══ b) HTML 红删绿增 ═════════════════════════════════════════════


def _html_for(old: str, new: str) -> str:
    return render_diff_html(diff_result(old, new, "v1", "v2"), "v1", "v2")


def test_html_marks_del_and_ins_with_semantic_classes() -> None:
    """删除行 `<del class="dl-del">`（绯红类）／新增行 `<ins class="dl-add">`（竹绿类）。"""
    html = _html_for("甲\n乙\n", "甲\n丙\n")
    assert '<del class="dl-del">乙</del>' in html
    assert '<ins class="dl-add">丙</ins>' in html
    assert 'class="ver-row del"' in html and 'class="ver-row ins"' in html
    # 上下文行不包 del/ins（甲只以 span 出现）
    assert '<span class="ver-tx">甲</span>' in html


def test_html_escapes_untrusted_body_text() -> None:
    """正文是不可信输入：`<script>` 之类只以转义文本形态出现（绝不生成标签）。

    行内字符级精化会把转义片段按共同字符拆开（`<` `>` 常与对侧共同），
    故不再断言 `&lt;script&gt;` 的连续形态——改断「产物里只可能出现自家
    白名单标签」这一更强的安全性质。
    """
    html = _html_for("旧 <script>alert(1)</script>\n", "新 <b>x</b>\n")
    tags = set(re.findall(r"</?([a-zA-Z]+)[ >]", html))
    assert tags <= {"div", "span", "del", "ins", "p"}, tags
    assert "<script>" not in html and "<b>" not in html
    assert "&lt;" in html and "&gt;" in html  # 尖角只以转义形态出现


def test_html_inline_char_refinement_for_single_char_change() -> None:
    """行内字符级精化（批4 复核④验收）：改一个字只高亮那个字。

    del/ins 行配对后按字符 SequenceMatcher 拆段——未变字符为普通文本、
    实改字符才进 del/ins 标签；中文散文段落=单行，单字改动不再整段标红。
    """
    html = _html_for("基本原理，是理解整段的上下文。\n第二段保持不变。\n",
                     "基本原裏，是理解整段的上下文。\n第二段保持不变。\n")
    assert '<del class="dl-del">理</del>' in html
    assert '<ins class="dl-add">裏</ins>' in html
    # 未变字符不进高亮标签（整段红/绿的旧形态不再出现）
    assert '<del class="dl-del">基本原理' not in html
    assert '<ins class="dl-add">基本原裏' not in html
    assert "基本原" in html and "，是理解整段的上下文。" in html  # 共同文本仍在行内


def test_html_inline_refinement_fallbacks() -> None:
    """精化的三个退路：无共同字符（整行包裹同形）/ 纯删纯增（不配对）/ 超长行。"""
    from musespiral.core.version.diff import INLINE_REFINE_MAX_CHARS

    # 无共同字符：乙→丙 精化体与整行包裹逐字节同形（走外层整行路径）
    html = _html_for("甲\n乙\n", "甲\n丙\n")
    assert '<del class="dl-del">乙</del>' in html
    assert '<ins class="dl-add">丙</ins>' in html
    # 纯删/纯增：无配对行，整行包裹
    html2 = _html_for("甲\n乙\n", "甲\n")
    assert '<del class="dl-del">乙</del>' in html2
    # 超长行（>1500 字符）不精化——退整行包裹（长行逐字是噪声）
    long_a = "长" * (INLINE_REFINE_MAX_CHARS + 1)
    long_b = "长" * INLINE_REFINE_MAX_CHARS + "改"
    html3 = _html_for(f"{long_a}\n", f"{long_b}\n")
    assert f'<del class="dl-del">{long_a}</del>' in html3
    assert f'<ins class="dl-add">{long_b}</ins>' in html3


def test_html_identical_message() -> None:
    """两版相同 → 一句可读提示，不渲染 hunk 骨架。"""
    html = _html_for("同一段\n", "同一段\n")
    assert "ver-diff" not in html
    assert "两版内容相同" in html


def test_html_line_numbers_on_both_sides() -> None:
    """行号双槽：del 行只在旧侧有号、ins 行只在新侧有号（另一侧为空槽）。"""
    html = _html_for("甲\n乙\n", "甲\n丙\n")
    assert '<div class="ver-row del"><span class="ver-no">2</span><span class="ver-no"></span>' in html
    assert '<div class="ver-row ins"><span class="ver-no"></span><span class="ver-no">2</span>' in html


# ══ c) 服务层 ════════════════════════════════════════════════════


def test_list_versions_current_first_then_pool_new_to_old(root: Path) -> None:
    """清单首项=当前版（id=current），池内新→旧；旧 .md.bak 恒排尾部（id=legacy）。"""
    art = _article(root, body="当前正文\n")
    stamps = _craft_pool(art, ["旧一\n", "旧二\n"])
    art.with_name("post.md.bak").write_text("旧单版本\n", encoding="utf-8")

    items = version_service.list_versions(art)
    assert [i["id"] for i in items] == ["current", stamps[1], stamps[0], "legacy"]
    assert items[0]["current"] is True and items[0]["label"] == "当前版"
    assert items[1]["label"].startswith("2026-11-19")
    assert items[-1]["label"] == "旧单版本" and items[-1]["ts"] is None
    assert all(i["available"] is True for i in items)
    assert items[0]["summary"] == "title: 测试文章"


def test_diff_versions_reads_both_sides(root: Path) -> None:
    """`v1` 只读池内版、`v2=current` 读磁盘：内容与标签各自正确。

    池内版按整文件造（frontmatter 与当前版一致，只有正文一行不同）——这样
    增删行数才是「改一行」的 1/1，不被 frontmatter 差异污染。
    """
    art = _article(root, body="当前版正文\n")
    art.with_name("post.md.bak.20261119-080100").write_text(
        "---\ntitle: 测试文章\n---\n\n池内版正文\n", encoding="utf-8"
    )
    out = version_service.diff_versions(art, "20261119-080100", version_service.CURRENT_ID)
    assert out["v1"]["label"].startswith("2026-11-19")
    assert out["v2"]["label"] == "当前版"
    assert (out["added"], out["removed"]) == (1, 1)
    # 行内字符级精化：共同片段「版正文」作普通文本，实改两字才进红绿标签
    assert '<del class="dl-del">池内</del>' in out["html"]
    assert '<ins class="dl-add">当前</ins>' in out["html"]
    assert "版正文" in out["html"]


def test_diff_versions_rejects_same_and_unknown(root: Path) -> None:
    """同版对比与未知版本 id 都是可读拒绝（不静默返回空 diff）。"""
    art = _article(root)
    with pytest.raises(version_service.VersionError):
        version_service.diff_versions(art, "current", "current")
    with pytest.raises(version_service.VersionError) as err:
        version_service.diff_versions(art, "19990101-000000", "current")
    assert "版本不存在" in str(err.value)


def test_restore_version_pools_current_first(root: Path) -> None:
    """恢复：被选版本覆盖当前版，且**恢复前的当前版进了池**（恢复可再恢复）。"""
    art = _article(root, body="将被覆盖的正文\n")
    stamps = _craft_pool(art, ["要恢复的旧正文\n"])
    info = version_service.restore_version(art, stamps[0])
    assert info["id"] == stamps[0]
    assert "要恢复的旧正文" in art.read_text(encoding="utf-8")
    # 池内应有「恢复前当前版」的整文件字节（摘要只取首行，正文级验要看内容）
    pool = list(art.parent.glob("post.md.bak.*"))
    contents = [p.read_text(encoding="utf-8") for p in pool]
    assert any("将被覆盖的正文" in c for c in contents), contents


def test_restore_version_refuses_current(root: Path) -> None:
    """`current` 不在可恢复集合（选当前版恢复是空操作，报可读拒绝）。"""
    art = _article(root)
    with pytest.raises(version_service.VersionError):
        version_service.restore_version(art, "current")


def test_read_version_reports_non_utf8_pool_entry(root: Path) -> None:
    """池内坏字节（非 UTF-8）→ 可读错误，不吐 U+FFFD 伪文本。"""
    art = _article(root)
    bad = art.with_name("post.md.bak.20261119-080100")
    bad.write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(version_service.VersionError) as err:
        version_service.read_version(art, "20261119-080100")
    assert "UTF-8" in str(err.value)


# ══ d) 端点层（真实回环）═════════════════════════════════════════


def test_get_diff_two_modes(server, root: Path) -> None:
    """GET 双态：无 v1/v2 只回清单（diff=null）；有则附 hunks + html。"""
    art = _article(root, body="新正文\n")
    stamps = _craft_full_pool(art, ["旧正文\n"])
    code, d = _request(server, "GET", "/api/article/diff?path=post.md")
    assert code == 200 and d["diff"] is None
    assert [v["id"] for v in d["versions"]][0] == "current"

    code, d = _request(
        server, "GET", f"/api/article/diff?path=post.md&v1={stamps[0]}&v2=current"
    )
    assert code == 200
    assert d["diff"]["added"] == 1 and d["diff"]["removed"] == 1
    # 行内字符级精化（批4 复核④）：「新」字进绿、「正文」共同片段为普通文本
    assert '<ins class="dl-add">新</ins>' in d["diff"]["html"]
    assert "正文" in d["diff"]["html"]
    assert '<del class="dl-del">旧</del>' in d["diff"]["html"]
    assert len(d["diff"]["hunks"]) == 1


def test_get_diff_accepts_slug_alias(server, root: Path) -> None:
    """`slug=` 与 `path=` 同义（设计 §3.7 原文用 slug；两者都走 root 边界校验）。"""
    _article(root)
    code, d = _request(server, "GET", "/api/article/diff?slug=post.md")
    assert code == 200 and d["article"] == "post.md"


def test_get_diff_400_surface(server, root: Path) -> None:
    """400 面：缺 path / 只给一侧 / 同版 / 未知版本 / context 非整数或越界。"""
    _article(root)
    for qs in (
        "",
        "?path=post.md&v1=current",
        "?path=post.md&v1=current&v2=current",
        "?path=post.md&v1=nope&v2=current",
        "?path=post.md&v1=current&v2=nope",
        "?path=post.md&v1=a&v2=b&context=abc",
        "?path=post.md&v1=a&v2=b&context=99",
    ):
        code, d = _request(server, "GET", "/api/article/diff" + qs)
        assert code == 400, (qs, code, d)
        assert d["ok"] is False


def test_get_diff_404_for_missing_article(server, root: Path) -> None:
    """不存在的文章 → 404（与 /api/article 族同口径）。"""
    code, _d = _request(server, "GET", "/api/article/diff?path=nope.md")
    assert code == 404


def test_post_restore_requires_confirm(server, root: Path) -> None:
    """confirm 闸门：缺 confirm / confirm=false → 400，且文件未被改写。"""
    art = _article(root, body="当前正文\n")
    stamps = _craft_pool(art, ["旧正文\n"])
    for body in ({"article": "post.md", "version": stamps[0]},
                 {"article": "post.md", "version": stamps[0], "confirm": False}):
        code, d = _request(server, "POST", "/api/article/restore", body)
        assert code == 400 and d["ok"] is False
    assert "当前正文" in art.read_text(encoding="utf-8")


def test_post_restore_golden_path_and_errors(server, root: Path) -> None:
    """金路径：confirm:true → 200 ok 且正文被覆盖；未知版本 → 200 {ok:false}。"""
    art = _article(root, body="当前正文\n")
    stamps = _craft_pool(art, ["旧正文\n"])
    code, d = _request(
        server, "POST", "/api/article/restore",
        {"article": "post.md", "version": stamps[0], "confirm": True},
    )
    assert code == 200 and d["ok"] is True
    assert d["version"] == stamps[0] and d["article"] == "post.md"
    assert "旧正文" in art.read_text(encoding="utf-8")

    code, d = _request(
        server, "POST", "/api/article/restore",
        {"article": "post.md", "version": "19990101-000000", "confirm": True},
    )
    assert code == 200 and d["ok"] is False and "版本不存在" in d["error"]


# ══ e) 契约锁 ════════════════════════════════════════════════════


def test_contract_locks_include_diff_and_restore() -> None:
    """GET /api/article/diff 与 POST /api/article/restore 在契约集且路由表实装。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES
    from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

    assert "/api/article/diff" in CONTRACT_GET_EXACT
    assert "/api/article/diff" in set(_GET_ROUTES)
    assert "/api/article/restore" in CONTRACT_POST_EXACT
    assert "/api/article/restore" in _ACTION_ROUTES


# ══ f) 前端 ══════════════════════════════════════════════════════


def _run_node(frag: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用——JS 行为断言需真实执行（结构/回环仍随全量跑）")
    src = VER_JS.read_text(encoding="utf-8")
    begin, end = "/*__VER_BEGIN__*/", "/*__VER_END__*/"
    pure = src.split(begin, 1)[1].split(end, 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.js"
        path.write_text(pure + "\n" + frag, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 行为断言失败:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_versions_js_pure_functions() -> None:
    """node 真实执行：path 提取 / 下拉项 / 缺省对 / 恢复标签。"""
    out = _run_node(r"""
const versions=[
  {id:'current',label:'当前版',summary:'当前首行',current:true},
  {id:'20261119-090000',label:'2026-11-19 09:00:00',summary:'池内首行',current:false},
  {id:'legacy',label:'旧单版本',summary:'老版首行',current:false,ts:null}];
console.log(JSON.stringify({
  p1: verPath('#article?path=a%2Fb.md&tab=versions'),
  p2: verPath('#material?path=x'),
  p3: verPath('#article'),
  opts: verOptions(versions),
  empty: verOptions([]),
  pair: verDefaultPairs(versions),
  pairEmpty: verDefaultPairs([]),
  label: verRestoreLabel(versions[2]),
  esc: verEsc('<a>&"'),
}));
""")
    assert out["p1"] == "a/b.md"
    assert out["p2"] == "" and out["p3"] == ""
    assert '<option value="current">当前版</option>' in out["opts"]
    assert '<option value="20261119-090000">2026-11-19 09:00:00 · 池内首行</option>' in out["opts"]
    assert "（版本池为空）" in out["empty"]
    # 旧版=池内最旧（legacy），新版=当前版
    assert out["pair"] == {"a": "legacy", "b": "current"}
    assert out["pairEmpty"] == {"a": "", "b": ""}
    assert out["label"] == "恢复到 旧单版本（覆盖当前版）"
    assert out["esc"] == "&lt;a&gt;&amp;&quot;"


def test_versions_js_structure_and_loader() -> None:
    """结构锚：tmpl 有 versions tab 与 panel-versions；ui.js 装载循环含 versions.js。"""
    tmpl = TMPL.read_text(encoding="utf-8")
    assert 'data-tab="versions"' in tmpl
    assert 'data-panel="versions"' in tmpl
    for anchor in ("ver-a", "ver-b", "ver-diff-btn", "ver-stage", "ver-note"):
        assert f'id="{anchor}"' in tmpl, anchor
    ui = UI_JS.read_text(encoding="utf-8")
    assert "'assets/versions.js'" in ui
    assert "'versions'" in ui  # tab 白名单承认該 tab（否则深链回落 edit）
    core = CORE_JS.read_text(encoding="utf-8")
    assert "assets/versions.js" in core  # 构建产物里的装载面


def test_versions_js_no_external_refs() -> None:
    """零外联：只用相对 /api 路径，无 http(s)/协议相对引用。"""
    src = VER_JS.read_text(encoding="utf-8")
    assert "http://" not in src and "https://" not in src
    assert "'//" not in src
    assert "/api/article/diff" in src and "/api/article/restore" in src


def test_serves_versions_assets(server) -> None:
    """/assets/versions.js：200 + application/javascript + 与源逐字节一致。"""
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/assets/versions.js")
    resp = conn.getresponse()
    body = resp.read()
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    assert resp.status == 200
    assert ctype.startswith("application/javascript")
    assert body == VER_JS.read_bytes()
