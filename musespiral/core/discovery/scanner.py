"""知识库文档扫描器（F26）——frontmatter 手工解析 + 行数 + 章节提取。

L2 Domain 纯逻辑：只依赖 pathlib/re（§3.2 零内部依赖）。frontmatter 手工
解析（--- 块内 title/tags/created/category 扁平键，tags 为行内数组）——
不引第三方库，知识库 1850+ 篇实测均为该形态。

元数据口径（供 scorer/suggester/skeleton 消费）：

- path        文档绝对路径（str，draft 回链用）
- title       frontmatter.title，缺省回退正文首个 ``# `` 标题，再回退文件 stem
- tags        行内数组解析结果（list[str]；非数组值按单元素容忍）
- created     原始字符串（"YYYY-MM-DD"；缺省 ""，日期合法性归 scorer）
- category    frontmatter.category，缺省按路径目录名推断（13-source-analysis
              等知识库分类目录即类别）
- line_count  正文行数（frontmatter 块不计——「内容深度」量的是正文）
- headings    [(级别, 标题)]，# ~ ###### 全量（级别为 1-6 整数）
- sections    二级标题列表（grep "^## " 口径，F26 章节列表）
- pattern_titles  「设计思维提炼/设计模式/亮点」小节内的加粗条目标题
                  （与 scripts/gen_pattern_ledger.py 的 TITLE_RE/SECTION_RE/
                  normalize 同口径——模式家族成员判定以此为准）
- family_repos    模式家族族卡的「复现 N 仓」计数（非族卡为 None）
"""
from __future__ import annotations

import re
from pathlib import Path

# frontmatter 界定符行（--- / ...，容许行尾空白——与 python-frontmatter 同口径）
_FM_DELIM_RE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")
# 扁平键行：key: value（缩进键是嵌套结构，不属于本层，跳过）
_FM_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:[ \t]*(.*)$")
# 标题行（# ~ ######），标题主体去首尾空白
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
# 「设计思维提炼/设计模式/亮点」类小节（与 gen_pattern_ledger.SECTION_RE 同源兼容）
_PATTERN_SECTION_RE = re.compile(r"设计思维提炼|设计模式|亮点")
# 小节内加粗条目标题（与 gen_pattern_ledger.TITLE_RE 同口径：`1. **标题**` / `- **标题**`）
_PATTERN_TITLE_RE = re.compile(r"^\s*(?:\d+\.|-)[ \t]+\*\*(.{2,50}?)\*\*", re.M)
# 族卡头部的「复现 N 仓」计数行
_FAMILY_REPOS_RE = re.compile(r"^>\s*复现\s*(\d+)\s*仓", re.M)


def normalize_title(title: str) -> str:
    """条目标题归一（与 gen_pattern_ledger.normalize 同口径）。"""
    return title.strip().rstrip("：:—-")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """把全文拆为 (扁平键值表, 正文)。

    首行非界定符、或有开头无闭合，均按「无 frontmatter」处理（与
    python-frontmatter / core.article._fm_close_index 同口径）——全文皆正文。
    值去引号（成对单/双引号）；tags 的数组解析归 parse_tags。
    """
    lines = text.splitlines()
    if not lines or not _FM_DELIM_RE.match(lines[0]):
        return {}, text
    close = None
    for i in range(1, len(lines)):
        if _FM_DELIM_RE.match(lines[i]):
            close = i
            break
    if close is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:close]:
        m = _FM_KEY_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key] = value
    body = "\n".join(lines[close + 1 :])
    return meta, body


def parse_tags(value: str) -> list[str]:
    """解析行内数组 tags：``[a, b, c]`` → ["a", "b", "c"]。

    非数组值（历史脏数据）按单元素容忍；空数组/空串返回 []。
    """
    v = value.strip()
    if not v:
        return []
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
        items = [t.strip().strip("'\"") for t in v.split(",")]
        return [t for t in items if t]
    return [v.strip("'\"")] if v.strip("'\"") else []


def extract_headings(body: str) -> list[tuple[int, str]]:
    """提取标题树 [(级别, 标题)]（# ~ ######，正文内代码块不特殊处理——
    知识库文档章节头不出现在围栏代码块内的假设与 grep "^## " 口径一致）。"""
    out: list[tuple[int, str]] = []
    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def extract_pattern_titles(body: str) -> list[str]:
    """提取「设计思维提炼/设计模式/亮点」小节内的加粗条目标题（归一后）。

    小节切片到下一个二级标题为止（防跨节误收），与 gen_pattern_ledger.
    extract_patterns 同口径；无该类小节返回 []。
    """
    titles: list[str] = []
    lines = body.splitlines()
    in_section = False
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            if level <= 2:
                in_section = bool(_PATTERN_SECTION_RE.search(m.group(2)))
                continue
        if not in_section:
            continue
        for t in _PATTERN_TITLE_RE.findall(line):
            t = normalize_title(t)
            if len(t) >= 2:
                titles.append(t)
    return titles


def _fallback_title(body: str, stem: str) -> str:
    """title 回退链：正文首个一级标题 → 文件 stem。"""
    for level, title in extract_headings(body):
        if level == 1:
            return title
    return stem


def _infer_category(path: Path) -> str:
    """category 回退：取路径中的知识库分类目录名（NN-xxx 形态），无则 ""。"""
    for part in reversed(path.parts):
        if re.fullmatch(r"\d{2}-[a-z-]+", part):
            return part
    return ""


def extract_hook(body: str, max_chars: int = 80) -> str:
    """从概述类章节提取一句话钩子（F29 骨架首行引言）。

    优先级：①「一句话价值[:：]」行；② 首个 ``> `` 引用行；③ 章节首段
    首行。章节优先「项目概述/概述/族定义」，无则取首个二级章节。取不到
    返回 ""（骨架侧留待补占位）。超长截断（不猜下文）。
    """
    lines = body.splitlines()
    start = 0
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            if re.search(r"项目概述|概述|族定义", m.group(2)):
                start = i + 1
                break
    else:  # 无概述类章节：退到首个二级章节（无则从头）
        for i, line in enumerate(lines):
            m = _HEADING_RE.match(line)
            if m and len(m.group(1)) == 2:
                start = i + 1
                break

    value_re = re.compile(r"一句话价值[：:](.+)")
    first_para: str | None = None
    for line in lines[start:]:
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 2:  # 走到下一二级章节仍未命中：停
            break
        s = line.strip()
        if not s:
            continue
        vm = value_re.search(s)
        if vm:
            return vm.group(1).strip()[:max_chars]
        if s.startswith(">") and not s.startswith("> 分析日期"):
            return s.lstrip("> ").strip()[:max_chars]
        if first_para is None:
            first_para = s
    return (first_para or "")[:max_chars]


def scan_file(path: str | Path) -> dict:
    """扫描单篇 .md 文档，返回上述口径的元数据 dict。

    文件不存在/不可读抛 OSError（调用方决定容忍面）；编码坏字节按
    errors="replace" 容忍（1850 篇真实语料混入坏行时不中断整批扫描）。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    headings = extract_headings(body)
    m = _FAMILY_REPOS_RE.search(body)
    return {
        "path": str(p.resolve()),
        "title": fm.get("title") or _fallback_title(body, p.stem),
        "tags": parse_tags(fm.get("tags", "")),
        "created": fm.get("created", ""),
        "category": fm.get("category") or _infer_category(p.resolve()),
        "line_count": len(body.splitlines()),
        "headings": headings,
        "sections": [t for lvl, t in headings if lvl == 2],
        "pattern_titles": extract_pattern_titles(body),
        "family_repos": int(m.group(1)) if m else None,
    }


def scan_dir(source: str | Path, *, recursive: bool = True) -> list[dict]:
    """扫描目录下全部 .md（recursive=False 只扫顶层），按路径名排序（确定性）。

    单篇损坏（OSError）跳过不中断整批——扫描面是知识库大盘，坏一篇不塌。
    """
    root = Path(source)
    it = root.rglob("*.md") if recursive else root.glob("*.md")
    metas: list[dict] = []
    for p in sorted(it):
        try:
            metas.append(scan_file(p))
        except OSError:
            continue
    return metas
