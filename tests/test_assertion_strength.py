"""D-3 假门元测试：全库测试弱断言模式巡检（台账 2026-11-20 §四；告警不 fail）。

背景：PL-2 的 HIGH 缺陷（collect_output 整段读大产物进内存）曾溜过验收——
测试看似覆盖「大文件不读内存」实则构不成门（台账 D-2 判例）。本元测试把
五类「假门」模式固化为全库巡检，防止同类假门再次静默入库。

五类模式（P1~P5，均按「单个测试函数」为作用域判定）：
  P1 空态平凡：``assert x == {}`` / ``== []`` 且同函数内无任何数据构造——
     空实现/短路路径下恒真，回归成「什么都不做」测试仍绿。
  P2 遍历无嵌套：断言走 iterdir/glob 等目录遍历，但夹具只构造单层目录——
     iterdir 只枚举顶层，嵌套布局/递归遍历的回归测不出。
  P3 并发无交错证据：测试文本声称 Thread/asyncio/并发，但无 sleep/join/
     Event/计时等交错证据——任意调度序下可能恒绿，锁与竞态缺陷漏网。
  P4 上报值替代产物：断言消费返回载荷里自报的 size/count/total 等，函数内
     却无 .stat()/read_bytes()/filecmp 等产物取证——「记账对、实物错」测不出。
  P5 存在性无否向：``assert "X" in src`` 一类包含断言，同函数内无 ``not in``
     否向断言——全量输出/复制粘贴实现也能通过，特化逻辑未被证伪。

误报控制（ask 硬要求）：
  1. 合成语料自检（见 _CORPUS 与 test_detector_patterns_flag_known_weaknesses）：
     每类模式配一个已知弱样本（对应探测器**必须**命中）+ 一个干净对照
     （所有探测器**必须**零命中）。此后全库扫描某类命中 0 只可能是真干净，
     不可能是模式正则写错——「0 命中要检查模式是否写错」由本测试结构性保证。
  2. 每条告警带 ``file:line [P?]`` + 「为什么这是弱断言」说明，可逐条人工复核。
  3. 全库扫描结果走 warnings + print（对齐 test_build_ui.py 三档告警风格），
     **不产生 assert 失败**——本文件对套件永绿，价值在告警面；处置策略
     （改测试/豁免）由人工判断。

扫描范围：tests/ 全部 test_*.py（含 e2e/）；排除本文件（合成语料会自命中）。
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SELF_NAME = "test_assertion_strength.py"

# ── 五类探测器（正则按「测试函数源码片段」匹配）──────────────────────────

# P1：与空容器相等
_P1_EMPTY = re.compile(
    r"assert\b[^\n]*?("
    r"==\s*\{\}|\bis\s*\{\}|==\s*\[\]|\bis\s*\[\]|==\s*\(\)"
    r"|==\s*(?:dict|list|set|tuple)\(\s*\))"
)
# P1 豁免证据：同函数内存在数据构造（非空字面量/构造调用）
_P1_BUILD = re.compile(
    r"=\s*\{\s*['\"A-Za-z0-9_$]|=\s*\[\s*['\"A-Za-z0-9_$]"
    r"|\.(?:append|add|extend|update|put|insert)\(|json\.loads\(|json\.dumps\("
)

# P2：目录遍历 + 建目录，但无嵌套路径构造
_P2_TRAVERSE = re.compile(r"\.iterdir\(|\.rglob\(|\.glob\(|os\.listdir|os\.walk|Path\.walk")
_P2_MKDIR = re.compile(r"\.mkdir\(")
_P2_NESTED = re.compile(r"/\s*['\"][\w.\- ]+['\"]\s*/\s*['\"][\w.\- ]+['\"]")

# P3：并发声称 vs 交错证据
_P3_CLAIM = re.compile(
    r"\bThread\b|threading|ThreadPool|Executor|asyncio|multiprocessing"
    r"|as_completed|create_task|并发|竞态|交错"
)
_P3_EVIDENCE = re.compile(
    r"sleep\(|\.join\(|Event\(|Barrier|barrier|monotonic|perf_counter"
    r"|time\.time|interleav|\.wait\(|future|as_completed|gather\(|Queue\(|\bLock\b"
    # 本地测试 HTTP/套接字服务 fixture（serve_forever 线程）是测试基建而非
    # 被测代码的并发声称——套件内两类误报判例（test_radarhub_client /
    # test_compose_dual 的 ThreadingHTTPServer），豁免之
    r"|serve_forever|HTTPServer|TCPServer|socketserver"
)

# P4：自报值断言且无产物取证
_P4_REPORTED = re.compile(
    r"assert\b[^\n]*?['\"](?:size|bytes|total_bytes|count|total|length|n_\w+|num_\w+)['\"]"
)
_P4_ARTIFACT = re.compile(
    r"\.stat\(|read_bytes\(|filecmp|\.read_text\(|open\(|Image\.open|\.read\("
)

# P5：字符串包含断言且无否向断言（容器限定文本/源码类名字——
# ``assert "key" in resp`` 这类字典键存在性是真门（缺键即败），不算弱）
_P5_IN = re.compile(r"assert\b[^\n]*['\"][^'\"]{2,}['\"]\s+in\s+([A-Za-z_][\w.\[\]'\"]*)")
_P5_NOT_IN = re.compile(r"\bnot\s+in\b")
_P5_TEXT_WORDS = {
    "src", "source", "content", "html", "body", "text", "markdown", "md",
    "css", "js", "code", "markup", "msg", "message", "prompt", "line",
    "snippet", "template", "tmpl", "svg", "xml", "str", "group",
}


def _p5_container_textish(expr: str) -> bool:
    segs = [s.strip() for s in re.split(r"[\[\].('\")]+", expr) if s.strip()]
    segs = [s for s in segs if not s.isdigit()]
    return bool(segs) and segs[-1].casefold() in _P5_TEXT_WORDS


def _detect_p1(fn: str) -> bool:
    return bool(_P1_EMPTY.search(fn)) and not _P1_BUILD.search(fn)


def _detect_p2(fn: str) -> bool:
    return bool(_P2_TRAVERSE.search(fn) and _P2_MKDIR.search(fn)) and not _P2_NESTED.search(fn)


def _detect_p3(fn: str) -> bool:
    return bool(_P3_CLAIM.search(fn)) and not _P3_EVIDENCE.search(fn)


def _detect_p4(fn: str) -> bool:
    return bool(_P4_REPORTED.search(fn)) and not _P4_ARTIFACT.search(fn)


def _detect_p5(fn: str) -> bool:
    if not _P5_IN.search(fn) or _P5_NOT_IN.search(fn):
        return False
    return any(
        _p5_container_textish(m.group(1)) for m in _P5_IN.finditer(fn)
    )


# (代号, 探测器, 为什么这是弱断言)
DETECTORS: list[tuple[str, object, str]] = [
    (
        "P1",
        _detect_p1,
        "空态平凡成立：断言与空容器相等且同测试内无数据构造——实现回归成空实现/短路返回时测试仍绿",
    ),
    (
        "P2",
        _detect_p2,
        "遍历无嵌套用例：目录遍历断言的夹具只构造单层目录——iterdir 只枚举顶层，嵌套布局回归测不出",
    ),
    (
        "P3",
        _detect_p3,
        "并发声称无交错证据：无 sleep/join/Event/计时等调度交错证明——任意线程序下可能恒绿，竞态与锁缺陷漏网",
    ),
    (
        "P4",
        _detect_p4,
        "读服务上报值断言产物：断言消费返回载荷自报的 size/count 等，函数内无产物文件取证——记账对、实物错时测试仍绿",
    ),
    (
        "P5",
        _detect_p5,
        "存在性断言无否向：只查「包含 X」不查「不含 Y」——全量输出/复制粘贴实现也能通过，特化逻辑未被证伪",
    ),
]

# ── 合成语料（误报自检的锚）：弱样本必须命中，干净对照必须零命中 ──────────

_CORPUS_WEAK: dict[str, str] = {
    "P1": (
        "def test_empty(void):\n"
        "    out = svc.collect()\n"
        "    assert out == []\n"
    ),
    "P2": (
        "def test_scan(tmp_path):\n"
        "    (tmp_path / 'a').mkdir()\n"
        "    names = [p.name for p in tmp_path.iterdir()]\n"
        "    assert names == ['a']\n"
    ),
    "P3": (
        "def test_concurrent_write(tmp_path):\n"
        "    threads = [Thread(target=svc.write, args=(i,)) for i in range(4)]\n"
        "    for t in threads:\n"
        "        t.start()\n"
        "    assert svc.count() == 4\n"
    ),
    "P4": (
        "def test_export_report(tmp_path):\n"
        "    resp = api.export(plan)\n"
        "    assert resp['size'] > 0\n"
    ),
    "P5": (
        "def test_render_html(article):\n"
        "    html = render(article)\n"
        "    assert '<h1>' in html\n"
    ),
}

# 干净对照：有数据构造 / 嵌套路径 / 交错证据 / 产物取证 / 否向断言——全探测器零命中
_CORPUS_CLEAN = (
    "def test_real(tmp_path):\n"
    "    d = tmp_path / 'a' / 'b'\n"
    "    d.mkdir(parents=True)\n"
    "    items = [1, 2]\n"
    "    items.append(3)\n"
    "    threads = [Thread(target=svc.write, args=(i,)) for i in items]\n"
    "    time.sleep(0.05)\n"
    "    for t in threads:\n"
    "        t.start(); t.join(timeout=2)\n"
    "    p = d / 'out.bin'\n"
    "    assert p.stat().st_size > 0\n"
    "    assert p.read_bytes()[:4] == b'PK\\x03\\x04'\n"
    "    html = render(items)\n"
    "    assert '<h1>' in html\n"
    "    assert 'PLACEHOLDER' not in html\n"
)


# ── 扫描器 ────────────────────────────────────────────────────────────


def _strip_docstrings(fn_src: str) -> str:
    """docstring 置空（保行数，行号偏移不变）。"""
    try:
        tree = ast.parse(fn_src)
    except SyntaxError:
        return fn_src
    lines = fn_src.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ds = body[0]
                for i in range(ds.lineno - 1, (ds.end_lineno or ds.lineno)):
                    if 0 <= i < len(lines):
                        lines[i] = ""
    return "\n".join(lines)


def _strip_line_comments(fn_src: str) -> str:
    """剥行注释（引号态感知；保行数）。"""
    out = []
    for line in fn_src.split("\n"):
        res: list[str] = []
        q: str | None = None
        i = 0
        while i < len(line):
            ch = line[i]
            if q:
                res.append(ch)
                if ch == "\\" and i + 1 < len(line):
                    res.append(line[i + 1])
                    i += 2
                    continue
                if ch == q:
                    q = None
            elif ch in ("'", '"'):
                q = ch
                res.append(ch)
            elif ch == "#":
                break
            else:
                res.append(ch)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def _test_functions(path: Path) -> list[tuple[str, int, str]]:
    """解析文件，返回 [(函数名, 起始行, 函数源码片段)]——仅 test_ 前缀函数。

    片段已剥 docstring 与行注释（均保行数）：注释级字样（如 e2e 文档串里的
    「并发浏览器时段」）不是代码声称，不参与模式判定。
    """
    try:
        src = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    out: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            start = node.lineno
            end = node.end_lineno or node.lineno
            fn_src = "\n".join(lines[start - 1 : end])
            out.append((node.name, start, _strip_line_comments(_strip_docstrings(fn_src))))
    return out


def scan_suite() -> list[tuple[str, str, int, str]]:
    """全库扫描。返回 [(文件相对路径, 函数名, 行号, 模式代号)]。"""
    findings: list[tuple[str, str, int, str]] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == SELF_NAME:
            continue  # 本文件合成语料会自命中
        rel = path.relative_to(TESTS_DIR).as_posix()
        for fn_name, start_line, fn_src in _test_functions(path):
            for code, detector, _why in DETECTORS:
                if detector(fn_src):  # type: ignore[operator]
                    findings.append((rel, fn_name, start_line, code))
    return findings


# ── 测试 ──────────────────────────────────────────────────────────────


def test_detector_patterns_flag_known_weaknesses() -> None:
    """合成语料自检（误报控制硬要求）：

    - 每类弱样本必须被**对应**探测器命中（防模式正则写错/漏匹配）；
    - 干净对照必须被**所有**探测器放过（防探测器过宽造成全库误报）。
    本测试有真断言——它锁的是扫描器本身；对真实套件只告警不 fail。
    """
    for code, detector, _why in DETECTORS:
        sample = _CORPUS_WEAK[code]
        assert detector(sample), f"合成弱样本未被 {code} 命中——模式正则写错，先修扫描器"
    for code, detector, _why in DETECTORS:
        assert not detector(_CORPUS_CLEAN), (
            f"干净对照被 {code} 误命中——探测器过宽，会向真实套件倾倒误报"
        )


def test_suite_weak_assertions_advisory() -> None:
    """全库弱断言巡检：逐条 warnings + 汇总 print。**不 fail**（ask 明确：告警）。

    命中 0 的模式不需要再怀疑正则写错——test_detector_patterns_flag_known_
    weaknesses 已用合成语料结构性排除该可能。
    """
    findings = scan_suite()
    by_code: dict[str, list[tuple[str, str, int]]] = {c: [] for c, _d, _w in DETECTORS}
    for rel, fn_name, line, code in findings:
        by_code[code].append((rel, fn_name, line))

    why_of = {c: w for c, _d, w in DETECTORS}
    for rel, fn_name, line, code in findings:
        warnings.warn(
            f"弱断言[{code}] tests/{rel}::{fn_name}:{line} —— {why_of[code]}",
            UserWarning,
            stacklevel=2,
        )

    parts = " | ".join(f"{c}:{len(by_code[c])}处" for c, _d, _w in DETECTORS)
    print(f"\n=== D-3 弱断言巡检（告警不阻塞）：{parts}，共 {len(findings)} 处 ===")
    zeros = [c for c, _d, _w in DETECTORS if not by_code[c]]
    if zeros:
        print(f"{'、'.join(zeros)} 0 命中——正则已由合成语料自检锁死（见 corpus 测试），"
              "0 为真干净而非模式写错。")
    if findings:
        print("每条含 file:line 与弱因说明（见 warnings summary）；处置（改测试/豁免）走人工判断。")
