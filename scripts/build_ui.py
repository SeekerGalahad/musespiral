#!/usr/bin/env python3
r"""Studio 前端构建入口（R1 三源合成 + F58 视图拆源 + M1 双层 + M3 按视图拆 JS
+ ARCH-1 壳 HTML 外移 + ② CSS 按视图拆源 + UIX-3 material 视图三拆）。

源文件（musespiral/studio/，开发态正典，改前端改这些）：
  - ui.tmpl.html   HTML 骨架；占位注释各占整行：
      <!--BUILD:CSS-->（M1 起合成 <link rel="stylesheet" href="assets/core.css">）
      <!--BUILD:JS1-->（M1 起合成 <script defer src="assets/core.js"></script>）
      <!--BUILD:JS2-->（M1 起整行移除）
      <!--BUILD:VIEW:<name>>——首屏视图（publish）：整行替换为 ui.views/<name>.html
        的逐字节内容（首屏 HTML 留壳，加载不触发懒加载）；
        其余视图：替换为**空占位** <section …></section>（开标签逐字节取自
        ui.views/<name>.html 的 <section> 开标签，属性全保真），真骨架由
        assets/views/<name>.html.js 在进入视图时 innerHTML 注入（ARCH-1）。
  - ui.views/      按视图拆源的 HTML 片段（F58）与 CSS 片段（ARCH-1 ②）：
        十一视图 HTML 各一文件（首屏 publish 同在此目录，构建时内联进壳）；
        十懒视图 CSS 各一文件（publish 首屏样式留 ui.css 不拆），HTML 零压缩、
        CSS 保守压缩同 ui.css
  - ui.css         core 样式（全局 token/暗色覆写/reset/keyframes/壳框架/通用组件
        btn·badge·card·modal·toast 等/首屏 publish 视图样式）→ 压缩后落
        assets/core.css；视图专属样式在 ui.views/<view>.css（②拆分）
  - ui.js          全部脚本（开发态只读一份，扁平全局零 import）。M3 起用注释级
      标记 // @view:<name> ... // @endview 圈出视图块（core 段 + 各视图段）；
      /*__MDPUB_UI_SPLIT__*/ 仍分隔 core 主段与图标定义段。

产物（入库，musespiral/studio/ 下）——M3 拆分 + ARCH-1 HTML/CSS 外移：
  - ui.html            壳：tmpl 骨架 + **首屏视图 HTML 内联** + 其余视图空占位
      section + 首屏引用（link/defer script，均 assets/ 相对路径）。仍不内联
      CSS（M2 范围）。
  - assets/core.js     ui.js 去视图块后的 core 段（路由/状态/工具/命令面板/toast/
      jobs + 图标定义）保守压缩；首屏加载。core 内 ensureView(view) 按视图懒注入
      ——ARCH-1 起三步：先 assets/views/<view>.css（<link> 样式先于骨架，免闪）
      再 html.js（innerHTML 填充占位 + __onViewHtml 补装钩子：mountChrome 重跑/
      article 编辑器全局重抓取/绑定型独立产物 boot 重调）最后 <view>.js。
  - assets/views/<view>.js  十一视图各自的 render* + 专属 helper + bind<View>()，
      顶层 function 声明注入即挂全局；core 预置 no-op 占位符供早于注入的调用。
  - assets/views/<view>.html.js  非首屏视图的 HTML 骨架注入器（ARCH-1）：单语句
      document.getElementById('view-<view>').innerHTML=`<骨架内层>`——内容为
      ui.views/<view>.html <section> 开闭标签之间的逐字节内容（模板串转义仅
      \ ` ${ 三类）；publish 无此产物（HTML 已在壳）。
  - assets/views/<view>.css  非首屏视图的专属样式（ARCH-1 ②）：ui.views/<view>.css
      保守压缩（compress_bytes，allow_line_comments=False）；publish 无此产物
      （首屏样式在 core.css）。
构建是纯函数：产物字节由源文件唯一决定（无时间戳/环境量），连跑两次字节一致；
tests/conftest.py 每轮 pytest 会话先跑 build() 再断言。
零外联自检（render 内机器断言）：壳中所有 <script src>/<link href> 必须以
assets/ 开头—— http(s)/协议相对引用在构建期即拒（防被改成 CDN）。

F58 保守压缩（compress_bytes）——只做三类变换，字符串字面量零触碰：
  1. 注释剥离：JS 行注释 // 与 JS/CSS 块注释 /*…*/；
  2. 行尾空白剥离；3. 空行折叠（注释剥空/纯空白行整行删除，语句间换行保留）。
  安全机制（逐行字节扫描 + 跨行携带引号态）：
  - 追踪 ' / " / ` 引号态；处于字符串内（含跨行模板串）的行一字节不动——
    其尾空白/空行都是字符串内容；
  - 行内字符串未闭合 → 可疑：整行原样保留并携带状态，宁可少压不碰串；
  - 行中 // 仅当处于代码态、前置非空白字符 ∈ ; } { 或行首仅空白时才按注释剥
    （排除除法/正则歧义）；反斜杠转义对（正则 \/ 等）整体跳过，转义后的 /
    永不当作注释起点；行首 // 在合法 JS 里不可能是空正则，恒为注释；
  - CSS 关闭 // 剥离（url(https://…) 裸 // 不是注释，剥了即毁）；
  - 压缩绝不改引号内字节——对拍门：压缩前后 test_studio_ui 全量绿 +
    test_build_ui 字符串字面量序列保全断言。
源文件内联/产物规则：各源文件末尾换行（含尾部空行）在合成时剥除；JS/CSS
产物文件以单个换行结尾（文本文件卫生，git diff 干净）；HTML 模板与视图零触碰
（html.js 里的 HTML 骨架同样逐字节保真，仅做模板串安全转义）。

用法（modules/musespiral 下）：python3 scripts/build_ui.py
"""
from __future__ import annotations

import re
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
TMPL = STUDIO_DIR / "ui.tmpl.html"
VIEWS_DIR = STUDIO_DIR / "ui.views"
CSS = STUDIO_DIR / "ui.css"
JS = STUDIO_DIR / "ui.js"
OUT = STUDIO_DIR / "ui.html"
ASSETS_DIR = STUDIO_DIR / "assets"
CORE_JS = ASSETS_DIR / "core.js"
CORE_CSS = ASSETS_DIR / "core.css"

MARKER_CSS = b"<!--BUILD:CSS-->"
MARKER_JS1 = b"<!--BUILD:JS1-->"
MARKER_JS2 = b"<!--BUILD:JS2-->"
JS_SPLIT = b"\n/*__MDPUB_UI_SPLIT__*/\n"
_VIEW_MARKER_RE = re.compile(rb"<!--BUILD:VIEW:([a-z0-9-]+)-->")

# ARCH-1 首屏视图：HTML 内联进壳（首屏加载不触发懒加载）；其余视图 HTML 外移到
# assets/views/<view>.html.js，壳内留空占位 section（开标签取自源文件，属性保真）。
FIRST_SCREEN_VIEW = "publish"

# <section …> 开标签（懒视图占位与 html.js 内层切分的共同形状约束）
_SECTION_OPEN_RE = re.compile(rb"<section\b[^>]*>")

# 壳的首屏引用（M1：均 assets/ 相对路径——零外联红线的产物形态）
SHELL_CSS_LINK = b'<link rel="stylesheet" href="assets/core.css">\n'
SHELL_JS_TAG = b'<script defer src="assets/core.js"></script>\n'

# 壳内 <script src> / <link href> 引用提取（零外联自检用；源内无其他该形态标签）。
# 2026-11-18 集成接线轮修正：原正则只认「双引号 + 小写属性名」，实测 12 样本
# 对拍里 `<script src='https://…'>`（单引号）、`src=https://…`（无引号）、
# `<script SRC="https://…">`（大写）三种违规形态在构建期全部 PASS——构建期门
# 只是产物面门的一个子集，「双保险」名不副实。现改为：标签级扫描 + 属性级
# 提取，引号三态（双引号/单引号/无引号）与大小写一律覆盖。
_TAG_OPEN_RE = re.compile(rb"<(?:script|link)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ATTR_REF_RE = re.compile(
    rb"(?:^|[\s/])(?:src|href)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
    re.IGNORECASE,
)


def _tag_refs(shell: bytes) -> list[bytes]:
    """提取壳内全部 <script>/<link> 的 src/href 值（引号三态 + 大小写无关）。

    同一标签上的多个 src/href 全部取出（非只取首个/末个）；属性值去引号后
    原样返回，由调用方判定前缀——绝对路径 / 协议相对 // / http(s) / file: /
    data: 都会落进「不以 assets/ 开头」的拒绝分支。
    """
    refs: list[bytes] = []
    for tag in _TAG_OPEN_RE.finditer(shell):
        for attr in _ATTR_REF_RE.finditer(tag.group(1)):
            refs.append(attr.group(1) or attr.group(2) or attr.group(3) or b"")
    return refs

_QUOTE_CHARS = (0x27, 0x22, 0x60)  # ' " `
_SAFE_BEFORE_LINE_COMMENT = b";}{"  # 行中 // 前一个非空白字符白名单
_WS = (0x20, 0x09)


def _find_close(raw: bytes, start: int, quote: int) -> int | None:
    """从 start 起找未转义的闭合引号；返回其下标或 None（行内未闭合）。"""
    j = start
    n = len(raw)
    while j < n:
        c = raw[j]
        if c == 0x5C:  # 反斜杠转义对整体跳过
            j += 2
            continue
        if c == quote:
            return j
        j += 1
    return None


def _process_line(raw: bytes, in_quote: int | None, in_block: bool,
                  allow_line_comments: bool):
    r"""压缩单行。返回 (产物行|None, 行末引号态, 行末块注释态, 行尾是否代码态)。

    产物行 None=整行被跨行块注释吞没；行尾代码态=True 表示整行已完整扫到
    行末且未处于/未进入可疑字符串（调用方可安全 rstrip/判空折叠）。
    可疑情形（行内引号未闭合等）→ 返回 raw 原样：' " 的未闭合按正则误报
    处理重置状态（JS 单双引号串不能跨行；行尾恰为 \ 的续行串除外，显式
    携带）；` 未闭合 → 携带（模板串合法跨行，其间行=字符串内容零触碰）。
    """
    out = bytearray()
    i = 0
    n = len(raw)

    if in_block:
        end = raw.find(b"*/")
        if end == -1:
            return None, None, True, False
        i = end + 2  # 注释段丢弃

    if in_quote is not None:  # 上一行携带的字符串在本行延续
        close = _find_close(raw, i, in_quote)
        if close is None:  # 整行仍是字符串内容 → 原样，继续携带
            return raw, in_quote, False, False
        out += raw[i:close + 1]  # 字符串内容（含闭合引号）原样
        i = close + 1
        in_quote = None

    while i < n:
        c = raw[i]
        if c == 0x5C:  # 转义对整体跳过：正则 \/ 里的 / 永不当注释起点
            out += raw[i:i + 2]
            i += 2
            continue
        if c in _QUOTE_CHARS:
            close = _find_close(raw, i + 1, c)
            if close is None:  # 行内未闭合 → 可疑：整行原样
                if c == 0x60 or raw.endswith(b"\\"):  # 模板串跨行 / \ 续行串
                    return raw, c, False, False  # 携带态（后续行按串内容处理）
                return raw, None, False, False  # 正则引号误报：重置状态
            out += raw[i:close + 1]  # 整个字符串字面量原样
            i = close + 1
            continue
        if c == 0x2F and i + 1 < n and raw[i + 1] == 0x2A:  # /* 块注释
            end = raw.find(b"*/", i + 2)
            if end == -1:  # 跨行块注释：余下全丢，态带入下一行
                return bytes(out), None, True, True
            i = end + 2
            continue
        if (c == 0x2F and i + 1 < n and raw[i + 1] == 0x2F
                and allow_line_comments):  # // 行注释（仅 JS）
            k = i - 1
            while k >= 0 and raw[k] in _WS:
                k -= 1
            if k >= 0 and raw[k] not in _SAFE_BEFORE_LINE_COMMENT:
                return raw, None, False, False  # 除法/正则歧义 → 可疑，整行原样
            return bytes(out), None, False, True  # 余下全丢（行注释）
        out += raw[i:i + 1]
        i += 1
    return bytes(out), None, False, True


def compress_bytes(data: bytes, *, allow_line_comments: bool) -> bytes:
    r"""F58 保守压缩：注释剥离 + 行尾空白 + 空行折叠（字符串零触碰）。

    输入须以 \n 结尾；输出恒以单个 \n 结尾。逐行扫描、跨行携带引号态与
    块注释态；任何可疑行整行原样保留——压缩率让位于字节安全。
    行尾代码态的行才做 rstrip 与空行折叠（含原本就空白的行——语句间
    至少保留一个换行，ASI 语义不变）。
    allow_line_comments=False 用于 CSS（// 不是 CSS 注释，url() 裸 // 须保留）。
    """
    if not data.endswith(b"\n"):
        raise ValueError("compress_bytes 输入必须以换行结尾")
    if data == b"\n":
        return data
    in_quote: int | None = None
    in_block = False
    out_lines: list[bytes] = []
    for raw in data.split(b"\n")[:-1]:
        emitted, in_quote, in_block, ends_code = _process_line(
            raw, in_quote, in_block, allow_line_comments)
        if emitted is None:  # 整行在跨行块注释内
            continue
        if ends_code:
            stripped = emitted.rstrip()
            if not stripped:  # 注释剥空/纯空白 → 空行折叠（整行删除）
                continue
            out_lines.append(stripped)
        else:
            out_lines.append(raw)  # 原样（字符串行/可疑行：尾空白是内容）
    return b"\n".join(out_lines) + b"\n"


def _inline(blob: bytes, *, allow_line_comments: bool) -> bytes:
    """源文件字节 → 去尾换行 → 保守压缩 → 去尾换行的内联内容。"""
    body = blob.rstrip(b"\n")
    if not body:
        return b""
    return compress_bytes(body + b"\n", allow_line_comments=allow_line_comments)[:-1]


def _split_section(blob: bytes, name: str) -> tuple[bytes, bytes]:
    """视图源 → (开标签, 内层)。ARCH-1 懒视图占位/注入器的形状门。

    要求：去尾换行后以 `<section id="view-<name>"` 开头、以 `</section>` 结尾
    （与 test_views_directory_layout 同口径）——不满足即拒绝构建（防骨架
    形状漂移导致占位/注入器静默错位）。返回的开标签逐字节保真（占位属性 =
    源属性）；内层 = 开标签之后到 `</section>` 之前的逐字节内容。
    """
    body = blob.rstrip(b"\n")
    if not body.startswith(f'<section id="view-{name}"'.encode()):
        raise ValueError(
            f"ui.views/{name}.html 须以 <section id=\"view-{name}\" 开头（懒视图外移形状门）")
    if not body.endswith(b"</section>"):
        raise ValueError(f"ui.views/{name}.html 须以 </section> 结尾（懒视图外移形状门）")
    m = _SECTION_OPEN_RE.match(body)
    if m is None:
        raise ValueError(f"ui.views/{name}.html 开标签不完整（缺 >）")
    inner = body[m.end():-len(b"</section>")]
    return m.group(0), inner


def _expand_views(tmpl: bytes, views: dict[str, bytes]) -> bytes:
    """<!--BUILD:VIEW:name--> 整行替换（ARCH-1 两形态）：

    - 首屏视图（publish）：整行替换为视图文件逐字节内容（原 F58 语义）；
    - 其余视图：替换为空占位 `<section …></section>`（开标签取自源文件），
      真骨架由 assets/views/<view>.html.js 进入视图时注入；
    - 空文件 = 整行消失（两形态同此兜底语义）。
    """
    out = tmpl
    seen: set[bytes] = set()
    for match in _VIEW_MARKER_RE.finditer(tmpl):
        marker = match.group(0)
        if marker in seen:
            raise ValueError(f"视图占位符 {marker.decode()} 重复出现")
        seen.add(marker)
        name = match.group(1).decode()
        if out.count(marker + b"\n") != 1:
            raise ValueError(f"视图占位符 {marker.decode()} 须独占整行")
        content = views[name].rstrip(b"\n")
        if not content:
            replacement = b""
        elif name == FIRST_SCREEN_VIEW:
            replacement = content + b"\n"
        else:
            opening, _ = _split_section(views[name], name)
            replacement = opening + b"</section>\n"
        out = out.replace(marker + b"\n", replacement)
    return out


def _split_js(js: bytes) -> tuple[bytes, bytes]:
    """校验并切分 ui.js 的两段（JS_SPLIT 唯一性是源纪律，M1 不变）。

    M3 起 head 段=扁平 core（含视图块标记），tail 段=ICONS 图标定义（core 用）。
    """
    if js.count(JS_SPLIT) != 1:
        raise ValueError("ui.js 缺少唯一的 /*__MDPUB_UI_SPLIT__*/ 分隔行（独立一行，两侧各一个换行）")
    head, _, tail = js.partition(JS_SPLIT)
    return head, tail


# M3 视图块标记（注释级，不影响源码执行）：// @view:<name> ... // @endview。
# build 期据此把同一份 ui.js 源拆成 core.js + views/<name>.js；视图函数用顶层
# function 声明，注入后即挂到全局（core 已预置 no-op 占位符供早于注入的调用）。
# UIX-3 视图子拆分（2026-11-22 素材三拆）：@view 块内可用「// @<view>-<part>」独占
# 行圈出子段——块首到首标记 = <view>-core，各标记到下一标记/块尾 = <view>-<part>；
# 产物 = assets/views/<view>-<part>.js，**不再产出** <view>.js（ensureView 经
# VJS[view] 首载 -core，其余 part 由视图内 matPart() 按需拉取——进抽屉/开面板才载）。
# 登记表锁子段名与顺序：乱序/缺失/多余/错视图/未登记视图出现子标记，一律拒绝构建
# （防静默漏拆；与 EXPECTED_VIEWS 同一纪律）。
EXPECTED_SUBVIEWS: dict[str, tuple[str, ...]] = {"material": ("core", "drawer", "media")}
_SUBVIEW_BEGIN_RE = re.compile(rb"^// @([a-z0-9-]+)$")
_VIEW_BEGIN_RE = re.compile(rb"^// @view:([a-z0-9-]+)$", re.MULTILINE)
_VIEW_END = b"// @endview"
# 预期十一视图（M3 九视图 + PL-3 批 2026-11-20 增 plugins/plugin-run——插件系统
# 设计 §八四视图按评审 §2.3 拆两 JS：清单/详情/新建=plugins、画布/运行/segments=
# plugin-run；缺失/多余即拒绝构建，防静默漏拆）
EXPECTED_VIEWS = ("sources", "material", "books", "compose", "design",
                  "publish", "insight", "article", "settings",
                  "plugins", "plugin-run")


def split_ui_js(js: bytes) -> tuple[bytes, dict[str, bytes]]:
    """ui.js 源 → (core 字节, {view: view 字节})。

    core = 两个标记之间以外的全部内容（head 段去视图块 + JS_SPLIT + tail 段）；
    view = 每个 @view/@endview 块的逐字节内容。标记行本身不进任何产物。
    """
    if not js.endswith(b"\n"):
        raise ValueError("ui.js 必须以换行结尾（文本文件卫生，git diff 干净）")
    _split_js(js)  # 仍校验 JS_SPLIT 唯一性（源纪律）

    core_parts: list[bytes] = []
    views: dict[str, bytes] = {}
    all_lines = js.split(b"\n")
    i = 0
    while i < len(all_lines):
        line = all_lines[i]
        m = _VIEW_BEGIN_RE.match(line)
        if not m:
            core_parts.append(line)
            i += 1
            continue
        name = m.group(1).decode()
        # 找匹配的 // @endview（独占整行）
        j = i + 1
        block: list[bytes] = []
        while j < len(all_lines) and all_lines[j] != _VIEW_END:
            block.append(all_lines[j])
            j += 1
        if j >= len(all_lines):
            raise ValueError(f"@view:{name} 缺少配对的 // @endview")
        if name in views:
            raise ValueError(f"@view:{name} 重复出现")
        if name not in EXPECTED_VIEWS:
            raise ValueError(f"未登记的视图块 @view:{name}（预期 {EXPECTED_VIEWS}）")
        views[name] = (b"\n".join(block) + b"\n") if block and block[-1] != b"" else b"\n"
        i = j + 1

    if tuple(sorted(views)) != tuple(sorted(EXPECTED_VIEWS)):
        raise ValueError(
            f"ui.js 视图块 {sorted(views)} 与预期 {sorted(EXPECTED_VIEWS)} 不一致（视图块缺一不可）")

    # core = 全文去掉视图块；JS_SPLIT 标记作为注释行保留（compress 会剥掉）
    core = b"\n".join(core_parts)
    return core, views


def render_core_js(js: bytes) -> bytes:
    """ui.js 源 → assets/core.js（去视图块的 core 部分保守压缩；尾换行）。"""
    core, _ = split_ui_js(js)
    return _inline(core, allow_line_comments=True) + b"\n"


def render_view_js(js: bytes, name: str) -> bytes:
    """ui.js 源 → 单个 assets/views/<name>.js（视图块保守压缩；尾换行）。

    UIX-3 起仅适用**未子拆分**视图；material 等登记于 EXPECTED_SUBVIEWS 的视图
    无单文件形态（用 render_view_parts 取各子段产物）。
    """
    parts = render_view_parts(js, name)
    if set(parts) != {""}:
        raise ValueError(
            f"视图 {name} 已子拆分（子段 {sorted(parts)}），无单文件形态——用 render_view_parts")
    return parts[""]


def _norm_part(buf: list[bytes]) -> bytes:
    """子段行 → 字节：剥尾部空行，非空以单个换行结尾；全空段 → b"\\n"。

    与 split_ui_js 对整块的既有归一同语义（块尾空行不进产物，文本文件卫生）。
    """
    while buf and buf[-1] == b"":
        buf.pop()
    return (b"\n".join(buf) + b"\n") if buf else b"\n"


def split_view_parts(name: str, blob: bytes) -> dict[str, bytes]:
    """@view 块字节 → {产物名后缀: 子段字节}（UIX-3 子拆分）。

    无子标记 → {"": blob}（产物仍为 assets/views/<name>.js，其余十视图不变）；
    有子标记 → {"-core": 块首到首标记, "-<part>": 各标记到下一标记/块尾}，标记行
    本身不进任何产物。纪律（构建期硬校验，违者拒构建）：
      - 子段名必须以「<view>-」开头（books 块内写 @material-* 即拒）；
      - 视图未登记于 EXPECTED_SUBVIEWS 而出现子标记 → 拒；
      - 子段顺序与集合必须与登记完全一致（缺段/多段/乱序 → 拒）。
    """
    if not blob.endswith(b"\n"):
        raise ValueError(f"@view:{name} 块须以换行结尾")
    all_lines = blob.split(b"\n")
    expected = EXPECTED_SUBVIEWS.get(name)
    if not any(_SUBVIEW_BEGIN_RE.match(l) for l in all_lines):
        # 无子标记：保持 M3 既有整块归一语义（字节不变）
        block = all_lines[:-1]  # 去掉 blob 末尾换行产生的空尾行
        legacy = (b"\n".join(block) + b"\n") if block and block[-1] != b"" else b"\n"
        return {"": legacy}
    parts: dict[str, bytes] = {}
    cur = "-core"
    buf: list[bytes] = []
    seen: list[str] = []
    for line in all_lines[:-1]:
        m = _SUBVIEW_BEGIN_RE.match(line)
        if m is None:
            buf.append(line)
            continue
        part = m.group(1).decode()
        if not part.startswith(name + "-"):
            raise ValueError(f"@view:{name} 内子标记 // @{part} 与视图名不符")
        if expected is None:
            raise ValueError(
                f"视图 {name} 未登记子拆分，出现子标记 // @{part}（EXPECTED_SUBVIEWS 无此项）")
        suffix = part[len(name) + 1:]
        # expected[0]="core" 为隐式首段（块首到首标记），标记名只登记其后各段
        if len(seen) >= len(expected) - 1 or suffix != expected[len(seen) + 1]:
            raise ValueError(
                f"@view:{name} 子段顺序/集合与登记 {list(expected)} 不符：得到 @{part}（已见 {seen}）")
        seen.append(suffix)
        parts[cur] = _norm_part(buf)
        buf = []
        cur = "-" + suffix
    parts[cur] = _norm_part(buf)
    if seen != list(expected[1:]):
        raise ValueError(f"@view:{name} 子段 {seen} 与登记 {list(expected)} 不一致（标记须按序齐备）")
    return parts


def render_view_parts(js: bytes, name: str) -> dict[str, bytes]:
    """ui.js 源 → {产物后缀: 压缩字节}（UIX-3；未子拆分视图 → {"": 产物}）。

    各子段独立保守压缩（compress_bytes 复用，字符串零触碰）；产物为
    assets/views/<name><suffix>.js，尾换行。
    """
    _, views = split_ui_js(js)
    if name not in views:
        raise ValueError(f"未知视图: {name}")
    return {suffix: (_inline(blob, allow_line_comments=True) + b"\n")
            for suffix, blob in split_view_parts(name, views[name]).items()}


def render_view_html_js(views: dict[str, bytes], name: str) -> bytes:
    r"""ui.views/<name>.html → 单个 assets/views/<name>.html.js（ARCH-1 注入器）。

    单语句 document.getElementById('view-<name>').innerHTML=`<内层>`——内层为
    源 <section> 开闭标签之间的逐字节内容；模板串安全转义仅 \ ` ${ 三类（现源
    零命中，转义是防未来引入）。HTML 零压缩零触碰（F58 纪律延伸）。首屏视图无
    此产物（HTML 已在壳）。
    """
    if name == FIRST_SCREEN_VIEW:
        raise ValueError(f"首屏视图 {name} 的 HTML 留壳，无 html.js 产物")
    _, inner = _split_section(views[name], name)
    lit = (inner.replace(b"\\", b"\\\\").replace(b"`", b"\\`")
               .replace(b"${", b"\\${"))
    return (b"document.getElementById('view-" + name.encode()
            + b"').innerHTML=`" + lit + b"`;\n")


def render_core_css(css: bytes) -> bytes:
    """ui.css（core 样式源）→ assets/core.css（保守压缩；文件以换行结尾）。"""
    if not css.endswith(b"\n"):
        raise ValueError("ui.css 必须以换行结尾（文本文件卫生，git diff 干净）")
    return _inline(css, allow_line_comments=False) + b"\n"


def render_view_css(css: bytes, name: str) -> bytes:
    """ui.views/<name>.css（视图专属样式源）→ assets/views/<name>.css（②拆分）。

    保守压缩（同 core.css：注释剥离+尾空白+空行折叠，// 恒不剥）；文件以换行
    结尾。空源文件 → 1 字节换行产物（ensureView 无条件注入 <link>，缺文件即 404
    ——装载契约要求每懒视图一份产物）。
    """
    if not css.endswith(b"\n"):
        raise ValueError(f"ui.views/{name}.css 必须以换行结尾（文本文件卫生）")
    return _inline(css, allow_line_comments=False) + b"\n"


def load_view_css() -> dict[str, bytes]:
    """读 ui.views/*.css → {视图名: 字节}；未登记名即报错（防漂移）。"""
    css_views: dict[str, bytes] = {}
    for path in sorted(VIEWS_DIR.glob("*.css")):
        name = path.stem
        if name not in EXPECTED_VIEWS:
            raise ValueError(f"ui.views/{path.name} 未登记视图（预期 {EXPECTED_VIEWS}）")
        if name == FIRST_SCREEN_VIEW:
            raise ValueError(
                f"首屏视图 {name} 的样式留 ui.css（core.css），不得有 ui.views/{name}.css")
        css_views[name] = path.read_bytes()
    missing = sorted(set(EXPECTED_VIEWS) - {FIRST_SCREEN_VIEW} - set(css_views))
    if missing:
        raise ValueError(f"懒视图缺 ui.views/<view>.css 源: {missing}"
                         "（ensureView 无条件注入 <link>，缺源即缺产物 404）")
    return css_views


def render(tmpl: bytes, views: dict[str, bytes], css: bytes, js: bytes) -> bytes:
    """源字节 → 壳字节（纯函数，无副作用）。

    css/js 参与源卫生校验（尾换行/JS_SPLIT 唯一性）但不进壳——内容各自
    落 assets/core.js|css（见 render_core_js/render_core_css）。
    """
    for name, blob in (("ui.tmpl.html", tmpl), ("ui.css", css), ("ui.js", js)):
        if not blob.endswith(b"\n"):
            raise ValueError(f"{name} 必须以换行结尾（文本文件卫生，git diff 干净）")
    for name, blob in views.items():
        if blob and not blob.endswith(b"\n"):
            raise ValueError(f"ui.views/{name} 非空时必须以换行结尾")
    for marker in (MARKER_CSS, MARKER_JS1, MARKER_JS2):
        if tmpl.count(marker) != 1:
            raise ValueError(f"ui.tmpl.html 占位符 {marker.decode()} 须恰好出现 1 次")
    marker_names = {m.group(1).decode() for m in _VIEW_MARKER_RE.finditer(tmpl)}
    if marker_names != set(views):
        raise ValueError(
            f"视图占位符 {sorted(marker_names)} 与 ui.views/ 文件 {sorted(views)} 不一致")
    # ARCH-1 装载契约门：非空懒视图（非首屏）必须有对应 JS 视图块——ensureView
    # 会去拉 assets/views/<view>.js，占位符登记了 JS 侧不存在的视图即 404。
    # （空文件=标记整行消失，无占位无装载，不在门内——builder 语义回归用例）
    lazy_names = {n for n in marker_names - {FIRST_SCREEN_VIEW}
                  if views[n].strip()}
    missing_js = lazy_names - set(EXPECTED_VIEWS)
    if missing_js:
        raise ValueError(
            f"懒视图 {sorted(missing_js)} 无对应 ui.js @view 块（ensureView 装载必 404）")
    split_ui_js(js)  # 源纪律校验（两段切分 + 视图块完整性，见 split_ui_js）
    out = _expand_views(tmpl, views)
    out = (
        out.replace(MARKER_CSS + b"\n", SHELL_CSS_LINK)
            .replace(MARKER_JS1 + b"\n", SHELL_JS_TAG)
            .replace(MARKER_JS2 + b"\n", b"")
    )
    if MARKER_CSS in out or MARKER_JS1 in out or MARKER_JS2 in out:
        raise AssertionError("占位符未全部消费（占位符不在整行位置）")
    if _VIEW_MARKER_RE.search(out):
        raise AssertionError("视图占位符未全部消费")
    # 零外联自检（M1 硬门的构建期形态）：壳内所有 script src / link href
    # 必须是 assets/ 相对路径——http(s)://、协议相对 //、绝对路径、file:/data:
    # 在此即拒。引号三态（双/单/无）与属性名大小写全覆盖（见 _tag_refs）。
    for url in _tag_refs(out):
        if not url.startswith(b"assets/"):
            raise AssertionError(
                f"壳内引用须为 assets/ 相对路径（零外联红线）: {url.decode(errors='replace')}")
    return out


def load_views() -> dict[str, bytes]:
    """读 ui.views/ 目录 → {视图名: 字件字节}；出现未登记文件即报错。"""
    views: dict[str, bytes] = {}
    for path in sorted(VIEWS_DIR.glob("*.html")):
        views[path.stem] = path.read_bytes()
    return views


def _write_if_changed(path: Path, data: bytes) -> bool:
    """内容不变不写（保 mtime 防 git 抖动）；返回是否落盘。"""
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
        return True
    return False


def build() -> Path:
    """读源 → 合成 → 写壳 + assets/（内容不变不写，保 mtime 防 git 抖动）。

    M3：ui.js 一份源同时拆出 core.js + assets/views/<view>.js（九视图），
    压缩器 compress_bytes 复用；视图块缺失/多余即拒绝构建（split_ui_js 硬校验）。
    ARCH-1 ②：ui.views/<view>.css 十懒视图源 → assets/views/<view>.css（publish
    无产物——首屏样式在 core.css）。
    """
    tmpl = TMPL.read_bytes()
    views = load_views()  # HTML 片段（ui.views/*.html）
    css = CSS.read_bytes()
    js = JS.read_bytes()
    css_views = load_view_css()  # CSS 片段（ui.views/*.css，十懒视图）
    shell = render(tmpl, views, css, js)
    core_js = render_core_js(js)
    core_css = render_core_css(css)
    _, js_views = split_ui_js(js)
    _write_if_changed(OUT, shell)
    ASSETS_DIR.mkdir(exist_ok=True)
    _write_if_changed(CORE_JS, core_js)
    _write_if_changed(CORE_CSS, core_css)
    views_js_dir = ASSETS_DIR / "views"
    views_js_dir.mkdir(exist_ok=True)
    for name, blob in js_views.items():
        parts = render_view_parts(js, name)
        for suffix, product in parts.items():
            _write_if_changed(views_js_dir / f"{name}{suffix}.js", product)
        if set(parts) != {""}:
            # UIX-3：子拆分视图退役单文件产物（防陈旧 material.js 残留被误引）
            stale = views_js_dir / f"{name}.js"
            if stale.exists():
                stale.unlink()
    # ARCH-1：非首屏视图的 HTML 骨架注入器（与 <view>.js 同路径；publish 除外）
    for name in sorted(views):
        if name == FIRST_SCREEN_VIEW:
            continue
        _write_if_changed(views_js_dir / f"{name}.html.js",
                          render_view_html_js(views, name))
    # ARCH-1 ②：非首屏视图的专属样式（与 <view>.js/.html.js 同路径；publish 除外）
    for name in sorted(css_views):
        _write_if_changed(views_js_dir / f"{name}.css",
                          render_view_css(css_views[name], name))
    return OUT


if __name__ == "__main__":
    path = build()
    shell_n = OUT.stat().st_size
    js_n = CORE_JS.stat().st_size
    css_n = CORE_CSS.stat().st_size
    views_css = sorted((ASSETS_DIR / "views").glob("*.css"))
    css_n_views = sum(p.stat().st_size for p in views_css)
    views_js = sorted((ASSETS_DIR / "views").glob("*.js"))
    html_js = sorted((ASSETS_DIR / "views").glob("*.html.js"))
    views_n = sum(p.stat().st_size for p in views_js if p not in html_js)
    html_n = sum(p.stat().st_size for p in html_js)
    print(f"built {path} (shell {shell_n} bytes) "
          f"+ {CORE_JS.relative_to(STUDIO_DIR)} ({js_n} bytes) "
          f"+ {CORE_CSS.relative_to(STUDIO_DIR)} ({css_n} bytes) "
          f"+ assets/views/*.js ({views_n} bytes / {len(views_js) - len(html_js)} files) "
          f"+ assets/views/*.html.js ({html_n} bytes / {len(html_js)} files) "
          f"+ assets/views/*.css ({css_n_views} bytes / {len(views_css)} files) "
          f"= total {shell_n + js_n + css_n + views_n + html_n + css_n_views} bytes")
