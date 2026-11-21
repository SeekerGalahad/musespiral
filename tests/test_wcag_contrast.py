"""A5 WCAG 机械复核（沉淀篇 §三-A5 待验证项 → 档A 落地，2026-11-15）。

对纸墨四状态色的**文本态**跑 WCAG AA（4.5:1）对比度断言：绛（crimson）/
墨（ink-1）/竹绿（jade）/琥珀（amber）。相对亮度为纯函数计算
（WCAG 2.x 定义：sRGB → 线性化 → 0.2126/0.7152/0.0722 加权），不依赖
浏览器或第三方库。

改色记档（本测试即门：改回旧值会红）：
  - 原 --color-success:#27ae60 文本态 2.73:1、--color-warning:#e67e22 文本态
    2.71:1（米白 #faf9f7 底），均不达 AA → 暗化为 #1a7038（5.84:1）与
    #9c5700（5.28:1）；暗朱 #922b21（7.71:1）与墨 #1a1a1a（16.54:1）达标不动。
  - 断言双底：①纸面 --color-bg；②各色自己的 12% badge 浅底（.badge.pass/
    .warn 的 color-mix 背景）——徽章是四色文本态的实际载体，单测纸面会漏。
  - 暗色模式四值（#2ecc71/#f39c12/#f43f5e/#e0ddd8 对 #1e1e1e）实测全部
    ≥4.5:1，不在本门断言范围（A5 原文只点名四色文本态的纸墨面）。

附带记档：复核时发现 :root 的 --t8/--t4 为自引用死 token（--t8:var(--t8)，
全部消费点实际渲染透明）——已修复为 8%/4% 墨色浅轨（dc-bar 总分条轨道/
job-prog/kbd/ghost hover 本意恢复），断言见 test_studio_ui.py 四维条测试。
"""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.css"


# ── 纯函数：相对亮度与对比度（WCAG 2.x 定义）──────────────


def _hex_rgb(color: str) -> tuple[int, int, int]:
    h = color.strip().lstrip("#")
    assert len(h) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", h), f"非 6 位 hex: {color}"
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def relative_luminance(color: str) -> float:
    """相对亮度 L = 0.2126R + 0.7152G + 0.0722B（通道经 sRGB 线性化）。"""
    channels = []
    for c in _hex_rgb(color):
        s = c / 255
        channels.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """对比度 = (L亮+0.05)/(L暗+0.05)。"""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _badge_tint(color: str, pct: float = 0.12, base: str = "#ffffff") -> str:
    """.badge.* 的 color-mix(状态色 12%, transparent) 叠在卡片白底上的等效底色。"""
    f, b = _hex_rgb(color), _hex_rgb(base)
    mixed = [round(pct * f[i] + (1 - pct) * b[i]) for i in range(3)]
    return "#%02x%02x%02x" % tuple(mixed)


def _root_tokens() -> dict[str, str]:
    """从 ui.css :root 抽 token 现值（测真实产物，不二次硬编码）。"""
    css = CSS.read_text(encoding="utf-8")
    root = re.search(r":root\{[^}]*\}", css)
    assert root is not None, "缺少 :root token 块"
    return dict(re.findall(r"(--(?:crimson|ink-1|ink-2|jade|amber|paper-0|paper-1)):(#[0-9a-fA-F]{6})",
                           root.group(0)))


def _dark_tokens(css: str, selector: str) -> dict[str, str]:
    """暗色覆写块（html[data-theme="dark"] 与 prefers-color-scheme 自动块）抽 token。"""
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m is not None, f"缺少暗色覆写块: {selector}"
    return dict(re.findall(r"(--(?:ink-2|paper-1)):(#[0-9a-fA-F]{6})", m.group(1)))


# ── A5 四色文本态：AA 4.5:1 机械门 ─────────────────────────


def test_four_status_colors_meet_aa_on_paper() -> None:
    """绛/墨/竹绿/琥珀 四色文本态对纸面 --paper-0 ≥ 4.5:1。"""
    t = _root_tokens()
    bg = t["--paper-0"]
    for token, name in (
        ("--crimson", "绛（危险）"),
        ("--ink-1", "墨"),
        ("--jade", "竹绿"),
        ("--amber", "琥珀"),
    ):
        ratio = contrast_ratio(t[token], bg)
        assert ratio >= 4.5, f"{name} {t[token]} 文本态对纸面 {bg} = {ratio:.2f}:1 不达 AA"


def test_four_status_colors_meet_aa_on_badge_tint() -> None:
    """四色文本态对各自 12% badge 浅底（.badge.pass/.warn 实际载体）≥ 4.5:1。"""
    t = _root_tokens()
    for token in ("--crimson", "--ink-1", "--jade", "--amber"):
        tint = _badge_tint(t[token])
        ratio = contrast_ratio(t[token], tint)
        assert ratio >= 4.5, f"{t[token]} 对 badge 浅底 {tint} = {ratio:.2f}:1 不达 AA"


def test_darkened_tokens_are_the_fixed_values() -> None:
    """改色记档锚定（P1 token v2 迁移）：jade/amber 现值=复核后暗化值；
    crimson=§1.2 绛值；ink-1 墨值不变。

    旧值 #27ae60/#e67e22 文本态 2.73/2.71:1 不达 AA（见模块 docstring 记档）。
    """
    t = _root_tokens()
    assert t["--jade"].lower() == "#1a7038"
    assert t["--amber"].lower() == "#9c5700"
    assert t["--crimson"].lower() == "#8e2434"
    assert t["--ink-1"].lower() == "#1a1a1a"


def test_pure_function_reference_values() -> None:
    """纯函数自证：黑/白 = 21:1；同色 = 1:1（公式实现锚定）。"""
    assert abs(contrast_ratio("#000000", "#ffffff") - 21) < 0.01
    assert abs(contrast_ratio("#abcdef", "#abcdef") - 1) < 1e-9


# ── 评审补批 P0-2（2026-11-17，《界面功能交互整体评审》§三 唯一 WCAG 失败项）──


def test_statusbar_text_meets_aa_on_opaque_paper() -> None:
    """状态栏文本态 ≥4.5:1：底改实心 var(--paper-1)（原 85% 半透明白），fg 保持 --ink-2。

    双主题断言（测 ui.css 真实 token，非硬编码）：
      - 浅色 :root → ink-2 #6b6b6b 对 paper-1 #ffffff = 5.33:1
      - 深色 html[data-theme=dark] → #a09a92 对 #2a2827 = 5.26:1
      - 自动暗色 prefers-color-scheme 块（html:not([data-theme="light"])）同值
    记档：评审原文「3.92:1」按本文件同式复算不可复现（ink-2 对 85% 白的等效
    实底 ≈5.28:1；3.92 既不等于 --ink-3 #9a948c（白底 3.005 / 等效实底 2.979），
    也不对应任何声明 token——复核穷举灰阶后确认 ≈#808080 才是 3.92，故原数
    为旧产物实测口径，不与 token 对应）；不论原数如何，本门改为机械断言：
    实心底 + ink-2 双主题 ≥4.5，改回半透明底或换 ink-3 即红。
    """
    css = CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.statusbar\{[^}]*\}", css)
    assert rule is not None, "缺少 .statusbar 规则"
    # 底必须是不透明 var(--paper-1)（半透明 color-mix 会让对比度随下层内容漂移）
    assert "background:var(--paper-1)" in rule.group(0), rule.group(0)
    assert "transparent" not in rule.group(0).split("background:")[1].split(";")[0]
    # fg 为 --ink-2（.statusbar color 继承给 .status-item）
    assert "color:var(--ink-2)" in rule.group(0)

    light = _root_tokens()
    assert contrast_ratio(light["--ink-2"], light["--paper-1"]) >= 4.5
    dark = _dark_tokens(css, 'html[data-theme="dark"]')
    assert contrast_ratio(dark["--ink-2"], dark["--paper-1"]) >= 4.5
    auto = _dark_tokens(css, 'html:not([data-theme="light"])')
    assert auto == dark, "自动暗色块与显式暗色块 token 漂移"
    assert contrast_ratio(auto["--ink-2"], auto["--paper-1"]) >= 4.5


# ── Phase C/D 入口 C 选题雷达（2026-11-17）：新增可见文字色的 AA 门 ──


def test_radar_row_text_colors_meet_aa_both_themes() -> None:
    """选题雷达卡的新增可见文字色双主题 ≥4.5:1。

    雷达卡没有新配色——只复用既有 token，本门把「复用」变成机械断言：
      - kind 徽三色：radar=--viz-1(青黛 → --indigo) / kb=--viz-5(石板) / star=--jade
      - 标题 --ink-1 / reasons·正文 --ink-2 / 降级注记 --amber
      - 排除组装饰线 --ink-3：**仅装饰线不是文字色**（文本 3.0:1 不达 AA），
        本门断言其确实没被用作正文色（ui.css 里 --ink-3 只出现在 border-left）
    底：浅色 --paper-1 卡片面；深色 html[data-theme="dark"] 的 --paper-1。
    """
    css = CSS.read_text(encoding="utf-8")
    light, dark = _root_tokens(), _dark_tokens(css, 'html[data-theme="dark"]')

    # 浅色：卡片面 paper-1（#ffffff）
    for token in ("--ink-1", "--ink-2", "--amber", "--jade"):
        ratio = contrast_ratio(light[token], light["--paper-1"])
        assert ratio >= 4.5, f"{token} 对 paper-1 = {ratio:.2f}:1 不达 AA"
    # kind 徽三色：--viz-1 是 var(--indigo) 别名（#2c5f8a）/ --viz-5 石板静态值
    root = re.search(r":root\{[^}]*\}", css)
    assert root is not None
    assert "--viz-1:var(--indigo)" in root.group(0)
    assert "--viz-5:#5d6d7e" in root.group(0)
    for token, expect in (("--viz-1=--indigo", "#2c5f8a"), ("--viz-5", "#5d6d7e")):
        ratio = contrast_ratio(expect, light["--paper-1"])
        assert ratio >= 4.5, f"{token} {expect} 对 paper-1 = {ratio:.2f}:1 不达 AA"

    # 深色：卡片面 paper-1（#2a2827）——深色块内逐 token 取值（_dark_tokens 只抽 ink-2/paper-1）
    dblock = re.search(r'html\[data-theme="dark"\]\{([^}]*)\}', css)
    assert dblock is not None
    dt = dict(re.findall(r"(--(?:ink-1|ink-2|ink-3|amber|jade|indigo|viz-5)):(#[0-9a-fA-F]{6})",
                         dblock.group(1)))
    for token in ("--ink-1", "--ink-2", "--amber", "--jade", "--indigo", "--viz-5"):
        assert token in dt, f"深色块缺少 {token}"
        ratio = contrast_ratio(dt[token], dark["--paper-1"])
        assert ratio >= 4.5, f"深色 {token} {dt[token]} 对 paper-1 = {ratio:.2f}:1 不达 AA"

    # --ink-3 只作装饰线：雷达块内不得作为 color
    radar = css[css.index("/* 入口 C 选题雷达"):]
    assert "--ink-3" in radar
    for rule in re.findall(r"#radar-ex\{[^}]*\}|\.radar-row[^{]*\{[^}]*\}", radar):
        assert "color:var(--ink-3)" not in rule, f"--ink-3 被用作文字色: {rule}"


def test_tag_suggest_chip_colors_meet_aa() -> None:
    """入口 B 建议标签 chips 的两种可见文字态双主题 ≥4.5:1。

    未选 chip：`.chip-tag` 的 --indigo 文字对卡片面（青黛 5.92:1 @白底 / 6.53 @暗底）。
    选中 chip：`.syn-chip.on` 青黛底 + `--paper-1` 文字——**白字于青黛 = 6.75:1**（反向同值）。
    「已有」禁用档 --ink-3 删除线：WCAG 1.4.3 明确豁免非活动控件文本，故不入本门
    （仍在本测试里锁住「它确实只是禁用态、不会被当作正文」）。
    """
    css = CSS.read_text(encoding="utf-8")
    light = _root_tokens()
    dblock = re.search(r'html\[data-theme="dark"\]\{([^}]*)\}', css)
    assert dblock is not None
    dt = dict(re.findall(r"(--(?:indigo|paper-1)):(#[0-9a-fA-F]{6})", dblock.group(1)))

    # 未选 chip：青黛字对纸面
    assert contrast_ratio("#2c5f8a", light["--paper-1"]) >= 4.5
    # 选中 chip：纸面字于青黛底（浅色主题的 --paper-1 是 #ffffff）
    assert ".syn-chip.on{background:var(--indigo);color:var(--paper-1)}" in css
    assert contrast_ratio(light["--paper-1"], "#2c5f8a") >= 4.5
    # 暗色主题：青黛 #7fb3d8 上的文字用暗色 --paper-1（深底反白不适用，取暗纸）
    assert contrast_ratio(dt["--paper-1"], dt["--indigo"]) >= 4.5
    # 禁用档确为 --ink-3（非正文色）
    assert ".syn-chip.off{" in css
    off = re.search(r"\.syn-chip\.off\{([^}]*)\}", css)
    assert off is not None and "color:var(--ink-3)" in off.group(1)
    assert "text-decoration:line-through" in off.group(1)


# ── Phase C/D 入口 A AI 综合（2026-11-17 三入口合并批）：新增可见文字/图形色的门 ──


def test_syn_panel_text_colors_meet_aa_both_themes() -> None:
    """入口 A（多选工具条 + 综合面板）新增可见文字色双主题 ≥4.5:1。

    - `.sel-bar` 工具条正文 --ink-2 对卡片面 --paper-1；
    - `.syn-step code` 字符数（caption 字级主要信息）——三入口合并修：原实现误用
      --ink-3（3:1，入口 C 批判例：新增 caption 正文禁用），已升 --ink-2，
      本门机械锁死不得回退；
    - 预算条（ui.js inline）--ink-3 是设计 §3.3 明文 micro 辅助档（与 fb-rate/
      coll-cap 等 10+ 处仓库惯例同档）——不入 AA 门，但锁「ink-3 必伴随 fs-micro」。
    """
    css = CSS.read_text(encoding="utf-8")
    light = _root_tokens()
    light_all = dict(re.findall(r"(--(?:ink-2|ink-3|jade|vermilion)):(#[0-9a-fA-F]{6})",
                                re.search(r":root\{[^}]*\}", css).group(0)))
    dark_css = re.search(r'html\[data-theme="dark"\]\{([^}]*)\}', css)
    assert dark_css is not None
    dark_all = dict(re.findall(r"(--(?:ink-2|jade|vermilion|paper-1)):(#[0-9a-fA-F]{6})",
                               dark_css.group(1)))

    # .sel-bar 与 .syn-step code 的文字色声明必须是 --ink-2（机械锁）
    selbar = re.search(r"\.sel-bar\{([^}]*)\}", css)
    assert selbar is not None and "color:var(--ink-2)" in selbar.group(1)
    code = re.search(r"\.syn-step code\{([^}]*)\}", css)
    assert code is not None and "color:var(--ink-2)" in code.group(1), \
        ".syn-step code 不得回退 --ink-3（caption 正文 3:1 不达 AA）"

    # ink-2 双主题 ≥4.5（浅色 root + 深色块各自取值）
    assert contrast_ratio(light_all["--ink-2"], light["--paper-1"]) >= 4.5
    assert contrast_ratio(dark_all["--ink-2"], dark_all["--paper-1"]) >= 4.5

    # 预算条 inline（ui.js）：--ink-3 只许出现在 fs-micro 档行内
    js = (CSS.parent / "ui.js").read_text(encoding="utf-8")
    for line in js.splitlines():
        if "color:var(--ink-3)" in line:
            assert "font-size:var(--fs-micro)" in line, \
                f"ui.js 中 ink-3 文字未伴随 fs-micro（非豁免档）: {line.strip()[:80]}"


def test_syn_step_dots_meet_non_text_contrast() -> None:
    """入口 A 三态进度点（图形组件）双主题 ≥3:1（WCAG 1.4.11 非文字对比）。

    done=竹绿实心 / running=朱砂脉冲点都是 8px 图形指示器（非文字）——1.4.11
    要求图形组件对相邻色 ≥3:1，此处对卡片面 --paper-1 断言。
    """
    css = CSS.read_text(encoding="utf-8")
    root = re.search(r":root\{[^}]*\}", css)
    assert root is not None
    light = dict(re.findall(r"(--(?:jade|vermilion)):(#[0-9a-fA-F]{6})", root.group(0)))
    dark_css = re.search(r'html\[data-theme="dark"\]\{([^}]*)\}', css)
    assert dark_css is not None
    dark = dict(re.findall(r"(--(?:jade|vermilion|paper-1)):(#[0-9a-fA-F]{6})",
                           dark_css.group(1)))
    # 点色声明机械锁（换 token 即红）
    assert re.search(r"\.syn-step\.done i\{background:var\(--jade\)", css)
    assert re.search(r"\.syn-step\.running i\{background:var\(--vermilion\)", css)
    # 三态点声明归位后（#syn-panel{grid-column:3}）步级规则族仍在
    assert "#syn-panel{grid-column:3}" in css

    for theme, tokens, paper in (("浅色", light, "#ffffff"), ("深色", dark, dark["--paper-1"])):
        for token in ("--jade", "--vermilion"):
            ratio = contrast_ratio(tokens[token], paper)
            assert ratio >= 3.0, f"{theme} {token} {tokens[token]} 对纸面 = {ratio:.2f}:1 不达 1.4.11"


def test_syn_doc_indigo_marks_ai_and_adopt_btn_vermilion() -> None:
    """纪律一（设计 §2.2/§八项4）机械锁：青黛专表 AI、朱砂给人工动作。

    - 综合稿容器 .syn-doc：青黛左边线（AI 视觉语言，与雷达 AI 角度行 em 同族）
      + 节标题 h1-h4 青黛（运行态实测修复前 h2=rgb(26,26,26)=--ink-1、
      #syn-body 内 indigo 元素数 0——mock done 态 Playwright 遍历复现）；
    - 青黛文字色双主题 AA 已由既有断言覆盖（浅 #2c5f8a 5.92:1 / 深 #7fb3d8）；
    - 「采纳所选」（人工动作）必须 btn-primary（朱砂）——纪律一反侧。
    """
    css = CSS.read_text(encoding="utf-8")
    doc = re.search(r"\.syn-doc\{([^}]*)\}", css)
    assert doc is not None, "缺少 .syn-doc 容器（青黛专表 AI 纪律）"
    assert "border-left:2px solid var(--indigo)" in doc.group(1)
    for lvl in (1, 2, 3, 4):
        assert re.search(r"\.syn-doc h1,\.syn-doc h2,\.syn-doc h3,\.syn-doc h4"
                         r"\{color:var\(--indigo\)\}", css), "综合稿节标题未走青黛"
    js = (CSS.parent / "ui.js").read_text(encoding="utf-8")
    m = re.search(r"<button class=\"(btn[^\"]*)\" data-tsg=\"ok\"", js)
    assert m is not None and "btn-primary" in m.group(1), \
        "「采纳所选」是人工动作按钮，必须朱砂（btn-primary）——纪律一反侧"
    # 综合稿外包容器存在（done 分支）
    assert "'<div class=\"syn-doc\">'+mdToHtml(S.text||'','')" in js
