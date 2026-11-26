"""core/localbook L2 纯层单测（B1 本地书籍线，2026-11-26）。

覆盖：编码探测（GB18030 / UTF8-BOM / Big5 / 空文件 / 全失败）、章节正
则族各形态（中文章/节/回/卷、Chapter N、CHAPTER、散文句不误判）、无结构
回退（min_chapters 与 split 规则）、目录清洗（序号/空白/去重/空章/重编号）、
正文截断（cap 与 CHAPTER_CAP 同值）、自定义规则加载（好/坏 JSON 跳过计数）。

正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》§1.2/§1.3。
这些测试**不触磁盘、不联网**（L2 纯度本身即被测对象的一部分：所有输入都是
字符串/字节，所有输出都是 dict）。
"""
from __future__ import annotations

import pytest

from musespiral.core.localbook import (
    BUILTIN_RULES,
    CHAP_RE,
    RuleError,
    clean_toc,
    detect_bytes,
    get_rule,
    load_rules_from_texts,
    parse_chapters,
    parse_rule,
    parse_with_rule,
    slice_chapter,
)
from musespiral.core.localbook import reader as lb_reader
from musespiral.core.localbook.charset import readability_score
from musespiral.core.localbook.chapters import (
    DEFAULT_CHUNK_CHARS,
    compile_rule_regex,
    split_fixed as split_fixed_fn,
)
from musespiral.core.localbook.toc import clean_title

CN_TEXT = (
    "卷首语：这是一本测试书。\n"
    "\n"
    "第一章 天穹裂痕\n"
    "雨是三更时分来的。先是远处有闷雷走动，像有什么东西在天穹上探路。\n"
    "我放下手中的读数板，望向那面巨大的观测窗。\n"
    "\n"
    "第二章 观测站\n"
    "记录员走进来，手里拿着一份读数表。\n"
    "\n"
    "第三章 第三象限\n"
    "裂痕又扩大了。\n"
)
TW_TEXT = (
    "第一章 天穹裂痕\n"
    "雨是三更時分來的。先是遠處有悶雷走動，像有什麼東西在天穹上探路。\n"
    "第二章 觀測站\n"
    "記錄員走進來，手裡拿著一份讀數表。\n"
    "第三章 第三象限\n"
    "裂痕又擴大了。\n"
)


# ── 1. 编码探测 ────────────────────────────────────────────────────


def test_detect_utf8() -> None:
    info = detect_bytes(CN_TEXT.encode("utf-8"))
    assert info["charset"] == "utf-8"
    assert info["confidence"] >= 0.9
    assert info["bom"] is False


def test_detect_utf8_bom() -> None:
    """UTF-8 BOM → utf-8-sig（decode 自带剥 BOM，调用方零二次处理）。"""
    raw = ("\ufeff" + CN_TEXT).encode("utf-8")
    info = detect_bytes(raw)
    assert info["charset"] == "utf-8-sig" and info["bom"] is True
    assert info["confidence"] >= 0.99
    # 解码后 BOM 不再出现在正文
    assert not raw.decode(info["charset"]).startswith("\ufeff")


def test_detect_gb18030() -> None:
    """GB18030：utf-8 严格试解必失败 → 落到双字节候选，必须判对而不是 Big5。"""
    raw = CN_TEXT.encode("gb18030")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # 顺序前提的实证（不是先验）
    info = detect_bytes(raw)
    assert info["charset"] == "gb18030"
    assert 0.5 <= info["confidence"] <= 0.85  # 双字节族封顶 0.85（不谎称确定）
    assert raw.decode(info["charset"]).startswith("卷首语")


def test_detect_big5() -> None:
    """Big5：GB18030 也能「解得出」（乱码+私用区），靠可读性打分才能判对。"""
    raw = TW_TEXT.encode("big5")
    # 判例实证：GB18030 对同一字节串不抛错但产出乱码（若此断言失败，说明
    # Python 的解码器行为变了——那时打分的必要性也要重新评估）
    garbled = raw.decode("gb18030", errors="replace")
    assert garbled != TW_TEXT
    info = detect_bytes(raw)
    assert info["charset"] == "big5"
    assert raw.decode(info["charset"]) == TW_TEXT


def test_detect_empty_and_undecodable() -> None:
    empty = detect_bytes(b"")
    assert empty == {"charset": "utf-8", "confidence": 1.0, "bom": False, "sample_bytes": 0}
    # 全部候选都解不了（0xFF 在 GB18030/Big5 里都是非法首字节形态）
    info = detect_bytes(b"\xff\xfe\xff\xff\xff\xff")
    # 前两字节是 UTF-16LE BOM → 走 BOM 分支；这里测的是真·无 BOM 的坏字节
    info2 = detect_bytes(b"\xff\xff\xff\xff")
    assert info2["charset"] == "utf-8" and info2["confidence"] <= 0.4
    assert "charset" in info


def test_readability_score_ranks_text_over_garbage() -> None:
    """真实文本 vs 错解乱码的打分差（Big5→GB18030 错解的实测样本）。"""
    real = readability_score(CN_TEXT)
    garbage = readability_score(TW_TEXT.encode("big5").decode("gb18030", errors="replace"))
    assert real > 0.9
    assert garbage < real - 0.3, (real, garbage)


# ── 2. 章节族各形态 ────────────────────────────────────────────────


def test_parse_chapters_cn_standard() -> None:
    chapters = parse_chapters(CN_TEXT)
    assert [c["n"] for c in chapters] == [1, 2, 3]
    assert chapters[0]["title"] == "第一章 天穹裂痕"
    assert chapters[0]["start"] < chapters[1]["start"] < chapters[2]["start"]
    # 末章 end = 全文长度（含尾部空白，正文渲染侧 strip）
    assert chapters[-1]["end"] == len(CN_TEXT)
    # 每章切片确含该章正文、不含下一章标题
    first = CN_TEXT[chapters[0]["start"]:chapters[0]["end"]]
    assert "雨是三更时分来的" in first and "第二章" not in first


def test_parse_chapters_variants() -> None:
    """各形态：节/回/卷/集/部/篇、中文数字、两/零、Chapter N、CHAPTER 罗马。"""
    text = (
        "第1节 起始\n正文一\n第二节 继续\n正文二\n"
        "第三回 对仗回目\n正文三\n第四卷 风起\n正文四\n"
        "第五集 收束\n正文五\n第六部 终章\n正文六\n"
        "第七篇 附录\n正文七\n"
        "Chapter 8 The Return\nbody eight\n"
        "CHAPTER IX FINALE\nbody nine\n"
        "第两百零一章 大结局\n正文十\n"
    )
    chapters = parse_chapters(text)
    titles = [c["title"] for c in chapters]
    assert len(chapters) == 10, titles
    assert titles[0] == "第1节 起始" and titles[-1] == "第两百零一章 大结局"
    assert "Chapter 8 The Return" in titles and "CHAPTER IX FINALE" in titles


def test_parse_chapters_rejects_prose_and_long_lines() -> None:
    """散文句/超长行不当章（启发式两层：句读结尾 + 长度上限）。"""
    text = (
        "第一章 真章\n正文一\n"
        "第二章讲的是他如何离开这座城市。\n"
        "第三章 真章三\n正文三\n"
        "第四章 这一行故意写得非常非常长" + "很" * 80 + "\n"
        "第五章 真章五\n正文五\n"
    )
    chapters = parse_chapters(text)
    titles = [c["title"] for c in chapters]
    assert titles == ["第一章 真章", "第三章 真章三", "第五章 真章五"], titles


def test_parse_chapters_few_hits_returns_empty() -> None:
    """命中 <3 章 → []（上层回退定长切块并如实标注「未识别章节结构」）。"""
    only_two = "第一章 一\n正文\n第二章 二\n正文\n"
    assert parse_chapters(only_two) == []
    # min_chapters 参数可放宽（自定义规则可调，但内置缺省=3）
    assert len(parse_chapters(only_two, min_chapters=2)) == 2


def test_parse_chapters_offsets_are_char_offsets() -> None:
    """偏移是**字符**偏移（不是字节）：含多字节汉字时 start 直接可用作切片。"""
    text = (
        "前言一段中文。\n第一章 甲\n中文正文。\n"
        "第二章 乙\n中文正文。\n第三章 丙\n中文正文。\n"
    )
    chapters = parse_chapters(text)
    # D2 修复（2026-11-26）：start 语义改为正文起点（跳过标题行）——标题行
    # 起点为 len(前言)+0，正文起点 = 标题行「第一章 甲」+ 行尾
    assert chapters[0]["start"] == len("前言一段中文。\n第一章 甲\n")
    assert text[chapters[0]["start"]:].startswith("中文正文")  # D2：正文起点（标题行已剥离）


def test_chap_re_exports_four_patterns() -> None:
    assert len(CHAP_RE) == 4
    assert all(isinstance(p, str) for p in CHAP_RE)


# ── 3. 无结构回退 ──────────────────────────────────────────────────


def test_split_fixed_default_chunk() -> None:
    text = "字" * (DEFAULT_CHUNK_CHARS * 2 + 500)
    blocks = split_fixed_fn(text)
    assert DEFAULT_CHUNK_CHARS == 5000
    assert len(blocks) == 3
    assert blocks[0]["start"] == 0 and blocks[0]["end"] == 5000
    assert blocks[1]["start"] == 5000
    assert blocks[-1]["end"] == len(text)  # 残块仍成一块（内容不丢）
    assert blocks[0]["title"].startswith("第 1 ")


def test_split_fixed_empty() -> None:
    assert split_fixed_fn("") == []


def test_parse_with_rule_falls_back_and_reports_hits() -> None:
    """规则命中不足 → structure=fixed 且 hits 是**实测原始命中数**（可归因）。"""
    two_chapters = "第一章 一\n正文\n第二章 二\n正文\n"
    chapters, structure, hits = parse_with_rule(two_chapters, get_rule("cn-novel-standard"))
    assert structure == "fixed" and hits == 2
    assert chapters and chapters[0]["title"].startswith("第 1 ")
    # split 规则：hits 恒 0（与「正则命中不足」可区分——设计 §1.3 的如实口径）
    blocks, structure2, hits2 = parse_with_rule(two_chapters, get_rule("plain-split"))
    assert structure2 == "fixed" and hits2 == 0 and blocks
    # 够数的正常书
    chapters3, structure3, hits3 = parse_with_rule(CN_TEXT, get_rule("cn-novel-standard"))
    assert structure3 == "chapters" and hits3 == 3 and len(chapters3) == 3


def test_cn_classic_rule_requires_duizhang_title() -> None:
    """古典规则要求「回 + 空白 + 回目」（无回目的纯章号不算回目）。"""
    classic_ok = (
        "第一回 甄士隐梦幻识通灵\n正文\n第二回 贾夫人仙逝扬州城\n正文\n第三回 托内兄如海荐西宾\n正文\n"
    )
    chapters, structure, _hits = parse_with_rule(classic_ok, get_rule("cn-classic"))
    assert structure == "chapters" and len(chapters) == 3
    # 「第一章 XXX」（不是回）在古典规则下不命中 → 回退
    _, structure2, hits2 = parse_with_rule(CN_TEXT, get_rule("cn-classic"))
    assert structure2 == "fixed" and hits2 == 0


# ── 4. 目录清洗 ────────────────────────────────────────────────────


def test_clean_toc_shape_and_renumber() -> None:
    chapters = [
        {"n": 1, "title": "第一章 甲", "start": 0, "end": 10},
        {"n": 2, "title": "第二章 乙", "start": 10, "end": 20},
    ]
    toc = clean_toc(chapters, "abcdef123456")
    assert toc == [
        {"title": "第一章 甲", "index": 1, "url": "localbook:abcdef123456#1"},
        {"title": "第二章 乙", "index": 2, "url": "localbook:abcdef123456#2"},
    ]


def test_clean_toc_drops_empty_and_renumbers() -> None:
    """空标题剔除后 index 必须**重新连续编号**（阅读面板 data-n 从 1 数）。"""
    chapters = [
        {"n": 1, "title": "第一章 甲", "start": 0, "end": 10},
        {"n": 2, "title": "   \u3000 ", "start": 10, "end": 20},  # 全空白 → 剔除
        {"n": 3, "title": "第三章 丙", "start": 20, "end": 30},
    ]
    toc = clean_toc(chapters, "x" * 12 if False else "abcdef123456")
    assert [t["index"] for t in toc] == [1, 2]
    # url 仍用**原始** n（第 3 章定位到真章 3，不是目录位次 2）
    assert toc[1]["url"].endswith("#3")


def test_clean_toc_consecutive_dupes_only() -> None:
    """只去**连续**重复标题（分卷首尾重名）；非相邻重名是真实内容必须保留。"""
    chapters = [
        {"n": 1, "title": "序", "start": 0, "end": 5},
        {"n": 2, "title": "序", "start": 5, "end": 10},  # 连续重复 → 丢
        {"n": 3, "title": "第一章 甲", "start": 10, "end": 15},
        {"n": 4, "title": "序", "start": 15, "end": 20},  # 非相邻 → 保留
    ]
    toc = clean_toc(chapters, "abcdef123456")
    assert [t["title"] for t in toc] == ["序", "第一章 甲", "序"]


def test_clean_toc_skips_bad_rows() -> None:
    chapters = [
        "不是 dict",
        {"n": "1", "title": "章号是字符串"},
        {"n": 0, "title": "章号为 0"},
        {"n": True, "title": "章号是 bool"},
        {"n": 5, "title": "合法"},
    ]
    toc = clean_toc(chapters, "abcdef123456")
    assert [t["title"] for t in toc] == ["合法"]


def test_clean_title_strips_seq_whitespace() -> None:
    assert clean_title("第 一 章  天穹") == "第一章 天穹"
    assert clean_title("第  12  回 风起") == "第12回 风起"  # 序号内空白压掉，标题分隔保留
    assert clean_title("\x00第一章 带控制字符\x07") == "第一章 带控制字符"
    assert clean_title(None) == ""


# ── 5. 正文截断（cap 与 CHAPTER_CAP 同值）──────────────────────────


def test_slice_chapter_basic() -> None:
    text = "abc\n\n第一章 甲\n中文正文。\n"
    start = text.index("第一章")
    out = slice_chapter(text, start, len(text))
    assert out["content"].startswith("第一章 甲")
    assert out["chars"] == len(out["content"])
    assert out["truncated"] is False
    assert out["orig_chars"] == len(text) - start


def test_slice_chapter_cap_truncates() -> None:
    text = "字" * 300_000
    out = slice_chapter(text, 0, len(text))
    assert out["truncated"] is True
    assert out["chars"] == lb_reader.CHAPTER_CAP == 200_000
    assert out["orig_chars"] == 300_000  # 原始长度如实上报（可核验面）
    # cap 可下调（测试用窄口；生产恒 200k）
    small = slice_chapter(text, 0, len(text), cap=10)
    assert small["chars"] == 10 and small["truncated"] is True


def test_slice_chapter_clamps_bad_offsets() -> None:
    """脏偏移（负/反向/越界）夹紧而不抛——手改 meta.json 不该产生 500。"""
    text = "0123456789"
    assert slice_chapter(text, -5, 5)["content"] == "01234"
    assert slice_chapter(text, 8, 3)["content"] == ""  # 反向 → 空章
    assert slice_chapter(text, 5, 999)["content"] == "56789"


def test_chapter_cap_matches_studio_constant() -> None:
    """cap 与 studio/book_actions.CHAPTER_CAP 必须同值（阅读面板同一截断口径）。"""
    from musespiral.studio.book_actions import CHAPTER_CAP as STUDIO_CAP

    assert lb_reader.CHAPTER_CAP == STUDIO_CAP == 200_000


# ── 6. 规则加载（内置三档 + 自定义 + 坏 JSON 跳过计数）────────────


def test_builtin_rules_shape() -> None:
    assert [r["id"] for r in BUILTIN_RULES] == ["cn-novel-standard", "cn-classic", "plain-split"]
    assert all(r["builtin"] for r in BUILTIN_RULES)
    assert get_rule("plain-split")["mode"] == "split"
    # 未知/缺省 id → 内置第一档（读路径永不抛）
    assert get_rule("no-such-rule")["id"] == "cn-novel-standard"
    assert get_rule(None)["id"] == "cn-novel-standard"


def test_parse_rule_validates() -> None:
    rule = parse_rule({"id": "my-rule", "name": "自定义", "chapter_regex": r"^第\d+话"})
    assert rule["id"] == "my-rule" and rule["mode"] == "regex" and rule["min_chapters"] == 3
    with pytest.raises(RuleError):
        parse_rule({"id": "Bad_ID"})
    with pytest.raises(RuleError):
        parse_rule({"chapter_regex": "x"})
    with pytest.raises(RuleError):
        parse_rule({"id": "ok-id", "chapter_regex": "([unclosed"})  # 编译失败即拒
    with pytest.raises(RuleError):
        parse_rule("not a dict")


def test_load_rules_skips_bad_json_and_counts() -> None:
    """坏 JSON 跳过并计数（设计 §1.3）——一个社区规则写错不该让书架不可用。"""
    rules, bad = load_rules_from_texts(
        [
            ("good.json", '{"id":"my-rule","chapter_regex":"^第\\\\d+话.{0,40}$"}'),
            ("broken.json", "{not json"),
            ("bad-schema.json", '{"id":"UPPER"}'),
            ("also-bad.json", '{"id":"compile-fail","chapter_regex":"([x"}'),
            ("good2.json", '{"id":"cn-classic","chapter_regex":"^第[0-9]+回 [^ ]+","name":"覆盖"}'),
        ]
    )
    assert bad == 3
    assert [r["id"] for r in rules] == ["my-rule", "cn-classic"]


def test_custom_rule_overrides_builtin_and_parses() -> None:
    """自定义规则可覆盖内置同 id，且真的改变切分结果（不是装饰品）。"""
    text = "第一话 甲\n正文一\n第二话 乙\n正文二\n第三话 丙\n正文三\n"
    custom, _bad = load_rules_from_texts(
        [("hua.json", '{"id":"cn-novel-standard","chapter_regex":"^第[0-9一二三]+话","min_chapters":3}')]
    )
    rule = get_rule("cn-novel-standard", custom)
    assert rule["chapter_regex"] == "^第[0-9一二三]+话"
    chapters, structure, hits = parse_with_rule(text, rule)
    assert structure == "chapters" and hits == 3 and len(chapters) == 3
    # 内置规则对「第X话」不命中 → 回退（同一文本两规则两种结果=覆盖生效的实证）
    _, structure_builtin, _ = parse_with_rule(text, get_rule("cn-novel-standard"))
    assert structure_builtin == "fixed"


def test_compile_rule_regex_empty() -> None:
    assert compile_rule_regex("") == ()
    assert compile_rule_regex(None) == ()
    assert len(compile_rule_regex(r"^第\d+章")) == 1


def test_chapter_body_excludes_title_line_d2() -> None:
    """D2 修复锚：章正文剥离标题行（rd 渲染自带章名，正文不带首行重复）。"""
    from musespiral.core.localbook.chapters import parse_chapters
    from musespiral.core.localbook.reader import slice_chapter

    text = "书名\n\n第一章 起风\n\n正文甲段落。\n\n第二章 落雨\n\n正文乙段落。\n\n第三章 天晴\n\n正文丙段落。\n"
    chs = parse_chapters(text)
    assert len(chs) == 3
    body = slice_chapter(text, chs[0]["start"], chs[0]["end"])["content"]
    assert "第一章" not in body and "起风" not in body.strip()[:3]
    assert body.strip().startswith("正文甲")
    # 末章只剩标题（body_start 回退分支）
    text2 = "书名\n\n第一章 甲\n\n内容一。\n\n第二章 乙\n\n内容二。\n\n第三章 丙\n"
    chs2 = parse_chapters(text2)
    assert slice_chapter(text2, chs2[2]["start"], chs2[2]["end"])["content"].strip() == ""
