"""内置计划构造器（Phase D F75.6/F76/F77，L2 纯函数零 I/O）。

计划是**代码构造器**产出的声明式数据，不是用户可编辑的文件格式——设计
§八-6：格式不作稳定承诺，未裁决前不引导用户依赖 JSON/YAML 计划文件。
因此本模块只提供两个具名构造器，签名即契约：

- `build_synthesis(material_ids)` → `synthesis_v1`（F76 跨素材综合）
- `build_tag_batch(material_ids)` → `tag_batch`（F77 批量标签建议）

**为什么构造器自己算 run 预算**：run 级预算的目的是「防单 run 失控烧钱」
（§三-D2 ③），而计划是唯一知道自己各步上界的东西。因此构造器按
`Σ步上界` 声明预算——恰够跑完本计划，不多给额度。给定 N 步时该值恒
不超过 §八-4 的缺省（40k-in/20k-out），不构成对默认值的突破。

**步数上限的单一权威在 `plan.validate_plan`**（`budgets.RUN_STEPS=8`）：
构造器只校验**自己的输入边界**（综合 2-8 篇），不重复实现步数策略。
由此 `build_synthesis(8 篇)` 产出 9 步、`build_tag_batch(9+ 篇)` 产出
9+ 步，都会在 validate 处以可读消息被拒（「步数上限 8，实际 9 步」）——
调用方（M2 服务层）据此分批。这条接缝是刻意的：两处各说各的上限会
漂移，一处裁决不会有歧义。

**方言约束**：本模块的提示词模板是新增 prompt 组装点，落 F45 方言守门
扫描域（`tests/test_architecture.py` DIALECT_SCAN_DIRS 含 `core/agent`）——
零模型/供应商字样。
"""
from __future__ import annotations

from typing import Any, Sequence

from . import budgets
from .plan import MAX_STEPS

# 计划名（run.json 的 plan 字段 / CLI 的 plan 参数取值集）
PLAN_SYNTHESIS = "synthesis_v1"
PLAN_TAG_BATCH = "tag_batch"
BUILTIN_PLANS: tuple[str, ...] = (PLAN_SYNTHESIS, PLAN_TAG_BATCH)

# 综合计划的素材数量区间（§二 F76.1：勾选 N（2-8）篇有正文面素材）
SYNTHESIS_MIN_MATERIALS = 2
SYNTHESIS_MAX_MATERIALS = 8

# **可执行**素材上限（= 步数上限 - synthesize 步）。设计 §二 F76.1 的「2-8 篇」
# 与 §三-D2/§八-4 的「run 8 步」在 N=8 处冲突：8 篇展平为 9 步，超步数上限；
# 且 N=8 的 out 上界 22000 > RUN_OUT_CHARS(20000)。**R3.5 书籍提炼批
# （2026-11-18）RUN_STEPS 8→16 后 N=8 已可执行**（9 步 ≤ 16；out 上界由
# 构造器自报 run 预算承载，不再与缺省 20000 相撞）；常量保留作语义注记。
SYNTHESIS_MAX_RUNNABLE_MATERIALS = budgets.RUN_STEPS - 1

# 单步预算（extract：正文节选 ≤2000 沿 PER_SOURCE_BUDGET 语义 + 模板余量）
EXTRACT_BUDGET_IN = 2400
EXTRACT_MAX_OUT = 2000
# synthesize：输入 = 指令 + N 条 carry（每条 ≤ tail:1200 正文 + 截断标注 + 换行），
# 因此**随 N 推导**而非固定常量——写死 8000 会让 N>5 的计划自截断（诚实性：
# budget_in 必须覆盖 plan 自己声明的全部输入，见 `_synthesis_budget_in`）
SYNTHESIS_MAX_OUT = 6000
# 标签步：注入正文节选 ≤4000 + 既有标签词表余量
TAG_BUDGET_IN = 5000
TAG_MAX_OUT = 400
# 尾部携带规格（§二 F76.2 / §三-D2：缺省 1200 字符）
CARRY_SPEC = f"tail:{budgets.CARRY_TAIL_DEFAULT}"

# ── 书籍提炼计划（R3.5，设计=《musespiral-书籍内容提炼管线设计-2026-11-18.md》）──
#
# 计划名。**不进 BUILTIN_PLANS**：该元组是 `agent_service._resolve_plan` 的
# 可选清单（CLI/API `agent run` 按名取计划、入参恒为 material_ids），而书籍
# 计划的输入是章节文本（book_fetch_service 组装 ctx），不经 agent run 提交
# ——列入会让错误信息谎称「可跑」。
PLAN_BOOK_DIGEST = "book_digest"
# 输入边界：1-15 章（15+1 综述 = 16 步恰为 RUN_STEPS 上限）
BOOK_DIGEST_MIN_CHAPTERS = 1
BOOK_DIGEST_MAX_CHAPTERS = budgets.RUN_STEPS - 1
# 综述步追加门槛（设计 §2.1：勾选 ≥5 章时追加一步）
BOOK_SYNTHESIS_MIN_CHAPTERS = 5
# 单章步预算：设计 §四「输入≤8k 字清洗文本」+ 指令与章节头余量
CHAPTER_DIGEST_BUDGET_IN = 8200
CHAPTER_DIGEST_MAX_OUT = 2000
# 综述步输出上界（全书综述一条素材）
BOOK_SYNTHESIS_MAX_OUT = 3000
# 综述 carry 规格：tail:2000 恒取**整张**提炼卡（max_out=2000 钉住卡长，
# 正常不截断）——设计说「输入=各章『定位』段」，DSL 只有 tail 文法，整卡
# 尾携是「定位段」的超集：综述指令再约束以各章「## 定位」为纲（见模板）。
BOOK_SYNTHESIS_CARRY = f"tail:{CHAPTER_DIGEST_MAX_OUT}"

# 单条 carry 在 prompt 里的最大占用：换行分隔 + (截断标注 + tail:N)
_CARRY_SLOT_CHARS = 1 + len(budgets.TRUNCATED_MARK) + budgets.CARRY_TAIL_DEFAULT

# ── 提示词模板（内置缺省；prompts.d/<task>.txt 经 F47 级联覆盖任务底座）──

EXTRACT_INSTRUCTION = """请阅读下面这一篇素材，产出一份结构化笔记，供后续多源综合分析使用。

要求（每项一行，不加寒暄与总结语）：
- 一句话定位：这篇素材在说什么
- 关键事实：数字、名称、版本，每条一行
- 独特观点：这篇素材独有、其他素材不易替代的判断
- 相关度：与共同主题的关系（高/中/低 + 一句理由）

只写原文有依据的内容，不补充原文没有的推断。"""

SYNTHESIZE_INSTRUCTION = """下面是同一主题下多篇素材的结构化笔记（各取尾部节选）。请产出一份 Markdown 综合稿。

固定四节结构（节标题逐字一致，顺序不变）：
## 共同点
## 分歧
## 时间线
## 选题角度

要求：
- 只使用笔记中出现的事实，不引入外部信息
- 共同点与分歧都要能追溯到具体素材
- 时间线按时间先后排列；无日期信息则写「时间信息不足」
- 选题角度给 2-3 条，每条一句话
- 输出末尾单独加一行标注你对综合稿忠实反映各笔记的把握，格式固定为「CONFIDENCE: 0.8」（0-1 小数；不确定时给低值）——该行不算综合稿正文"""

TAG_INSTRUCTION = """请为下面这篇素材建议最多 5 个标签。

输出协议：每行一个标签，行首固定加「TAG: 」前缀，不加编号、不加解释。
要求：
- 标签简短、可复用（同一主题下多篇素材能共用）
- 优先复用已给词表中的标签
- 只输出标签本身，不输出理由或空行以外的内容"""

# 书籍提炼两模板（R3.5；方言守门扫描域 core/agent 自动纳管）
CHAPTER_DIGEST_INSTRUCTION = """请阅读下面这一章节的全文，产出一张结构化提炼卡，供内容创作者后续取材使用。

固定四节结构（节标题逐字一致，顺序不变）：
## 定位
## 要点
## 引用
## 选题

要求：
- 定位：一句话说明这一章讲什么
- 要点：3-6 条关键事实或论点，每条一行
- 引用：1-3 段值得原文引用的段落，逐字摘录完整原句
- 选题：对内容创作者，这一章能支撑什么选题角度，1-3 条每条一句

只写本章原文有依据的内容，不补充原文没有的推断。"""

BOOK_SYNTHESIS_INSTRUCTION = """下面是同一本书多个章节的提炼卡（每张卡含「## 定位」等四节）。请以各章「## 定位」为纲要，产出一份全书综述。

固定三节结构（节标题逐字一致，顺序不变）：
## 全书主线
## 章节脉络
## 创作选题建议

要求：
- 只使用提炼卡中出现的内容，不引入外部信息
- 章节脉络按章节先后一行一章，概括各章定位
- 创作选题建议给 2-3 条，每条一句话"""


def _check_material_ids(material_ids: Sequence[str], *, name: str) -> list[str]:
    """素材 id 列表的基本形状校验（非空/全 str/无重复——纯结构，不查库）。"""
    ids = list(material_ids)
    if not ids:
        raise ValueError(f"{name} 需要至少 1 个素材 id")
    for i, mid in enumerate(ids):
        if not isinstance(mid, str) or not mid.strip():
            raise ValueError(f"{name}: material_ids[{i}] 必须是非空字符串，实际: {mid!r}")
    if len(set(ids)) != len(ids):
        dupes = sorted({m for m in ids if ids.count(m) > 1})
        raise ValueError(
            f"{name}: material_ids 含重复 id: " + "、".join(repr(i) for i in dupes)
        )
    return [mid.strip() for mid in ids]


def _synthesis_budget_in(n: int) -> int:
    """synthesize 步输入上界 = 指令 + N 条 carry 最大占用 + 连接换行（推导值）。

    **为什么推导而不写死**：`budget_in` 是「本步输入上限」，若小于计划自己
    声明的 carry 总量，`interp` 会把自己的 prompt 截掉一截——计划自相矛盾。
    推导保证 `budget_in >= 实际最大输入`，使 `max_out`/`budget_in` 成为可以
    用于 run 级预算求和的真上界（见 `budgets.clip` 的「标注占限内」同款理由）。
    """
    carry = n * _CARRY_SLOT_CHARS
    # 指令与首个 carry 之间的分隔换行 + 兜底余量（模板可被 prompts.d 覆盖加长）
    return len(SYNTHESIZE_INSTRUCTION) + carry + n + 200


def build_synthesis(material_ids: Sequence[str]) -> dict[str, Any]:
    """构造 `synthesis_v1`：N 个 extract 步展平 + 1 个 synthesize 步。

    输入边界（§二 F76.1）：`2 <= N <= 8`，越界 → ValueError（可读，含实际
    篇数与区间）。**注意 N=8 构造出的计划不可执行**——8 篇展平为 9 步，超
    `budgets.RUN_STEPS=8` 的步数上限（设计 F76.1「2-8 篇」与 F75.2「≤8 步」
    的冲突，见 `SYNTHESIS_MAX_RUNNABLE_MATERIALS` 注释）；`validate_plan`
    会在解释前以「步数上限 8，实际 9 步」可读拒绝。可跑区间 2-7。

    形态（§二 F75.2/F76.2）：
      extract_i  task=agent_extract  cache=True  budget_in=2400  max_out=2000
                 inputs={"instruction": EXTRACT_INSTRUCTION,
                         "material": "{{materials[i-1]}}"}   out=f"notes_{i}"
      synthesize task=agent_synthesize cache=False max_out=6000
                 budget_in=推导值（覆盖 N 条 carry 最大占用）  carry={notes_i: tail:1200}
                 out="synthesis"
    """
    ids = _check_material_ids(material_ids, name="build_synthesis")
    n = len(ids)
    if not (SYNTHESIS_MIN_MATERIALS <= n <= SYNTHESIS_MAX_MATERIALS):
        raise ValueError(
            f"综合计划需要 {SYNTHESIS_MIN_MATERIALS}-{SYNTHESIS_MAX_MATERIALS} 篇素材，"
            f"实际 {n} 篇"
        )

    steps: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        steps.append(
            {
                "id": f"extract_{i}",
                "task": "agent_extract",
                "inputs": {
                    "instruction": EXTRACT_INSTRUCTION,
                    # materials 下标从 0 起；extract_i 对应第 i 篇
                    "material": f"{{{{materials[{i - 1}]}}}}",
                },
                "out": f"notes_{i}",
                "budget_in": EXTRACT_BUDGET_IN,
                "max_out": EXTRACT_MAX_OUT,
                "cache": True,
                "retries": 1,
            }
        )
    synth_budget_in = _synthesis_budget_in(n)
    steps.append(
        {
            "id": "synthesize",
            "task": "agent_synthesize",
            "inputs": {"instruction": SYNTHESIZE_INSTRUCTION},
            "carry": {f"notes_{i}": CARRY_SPEC for i in range(1, n + 1)},
            "out": "synthesis",
            "budget_in": synth_budget_in,
            "max_out": SYNTHESIS_MAX_OUT,
            # 生成类恒不缓存（§八-3 裁决）：同 prompt 复用=创作语义回退
            "cache": False,
            "retries": 0,
        }
    )
    return {
        "plan": PLAN_SYNTHESIS,
        "steps": steps,
        "budget": budgets.run_budget(
            steps=n + 1,
            in_chars=n * EXTRACT_BUDGET_IN + synth_budget_in,
            out_chars=n * EXTRACT_MAX_OUT + SYNTHESIS_MAX_OUT,
        ),
    }


def build_tag_batch(material_ids: Sequence[str]) -> dict[str, Any]:
    """构造 `tag_batch`：每素材一个 `tag_suggest` 步（cache=True）。

    输入边界：至少 1 个 id；上限由 `validate_plan` 的步数上限（8）裁决
    ——`musespiral tags batch --limit 20` 须由调用方分批（每批 ≤8），构造器
    不静默切片（静默截断会让「我提交了 20 个」与「只跑了 8 个」不可区分）。

    形态（§二 F77.1）：步 out=AI 原文，**解析在消费侧**（tag_service 逐行
    认 `TAG: ` 前缀），计划本身不做任何解析。
    """
    ids = _check_material_ids(material_ids, name="build_tag_batch")
    n = len(ids)
    steps: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        steps.append(
            {
                "id": f"tag_{i}",
                "task": "tag_suggest",
                "inputs": {
                    "instruction": TAG_INSTRUCTION,
                    "material": f"{{{{materials[{i - 1}]}}}}",
                },
                "out": f"tags_{i}",
                "budget_in": TAG_BUDGET_IN,
                "max_out": TAG_MAX_OUT,
                "cache": True,
                "retries": 0,
            }
        )
    return {
        "plan": PLAN_TAG_BATCH,
        "steps": steps,
        "budget": budgets.run_budget(
            steps=n,
            in_chars=n * TAG_BUDGET_IN,
            out_chars=n * TAG_MAX_OUT,
        ),
    }


def _check_chapters(chapters: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """章节列表的形状校验（每项须含非空 url/title，纯结构不查重）。"""
    out = list(chapters)
    if not out:
        raise ValueError("build_book_digest 需要至少 1 个章节")
    for i, ch in enumerate(out):
        if not isinstance(ch, dict):
            raise ValueError(f"chapters[{i}] 必须是对象（含 url/title），实际: {type(ch).__name__}")
        for key in ("url", "title"):
            value = ch.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"chapters[{i}].{key} 必须是非空字符串，实际: {value!r}")
    return out


def build_book_digest(chapters: Sequence[dict[str, Any]], *, synthesis: bool | None = None) -> dict[str, Any]:
    """构造 `book_digest`：N 个逐章 digest 步 + （≥5 章）1 个全书综述步。

    设计 §2.3「编排形态选 plan 复用」——与 synthesis_v1 同构（N 独立单元
    逐个处理 + 1 综合），零新编排代码，只写一个 plan 构造器：

      digest_i     task=chapter_digest  cache=True（重跑同章零成本——「反复
                   换 prompt 打磨提炼」免费）  budget_in=8200  max_out=2000
                   inputs={"instruction": CHAPTER_DIGEST_INSTRUCTION,
                           "chapter": "{{materials[i-1]}}"}  out=f"digest_{i}"
      book_synthesis（仅 len(chapters) ≥ 5 时追加）task=book_digest  cache=False
                   （生成类恒不缓存 §八-3）  carry={digest_i: tail:2000}（整卡）
                   out="synthesis"  max_out=3000

    `synthesis` 参数：None=按 ≥5 章门槛自动；True/False 显式覆盖（服务层
    「残留章全部降级后再对已提炼章补综述」的续跑场景用 False 关、终局
    综述用 True 开——此时门槛由调用方自证）。

    输入边界：1 ≤ N ≤ 15（15+1=16 步恰为 RUN_STEPS 上限；越界可读拒绝，
    「提交 100 章」与「只跑 15 章」不可静默混淆）。**不在 agent_service
    的 BUILTIN_PLANS 里**：本计划消费章节文本而非素材 id，提交方是
    book_fetch_service（见 PLAN_BOOK_DIGEST 注）。
    """
    items = _check_chapters(chapters)
    n = len(items)
    if not (BOOK_DIGEST_MIN_CHAPTERS <= n <= BOOK_DIGEST_MAX_CHAPTERS):
        raise ValueError(
            f"书籍提炼计划需要 {BOOK_DIGEST_MIN_CHAPTERS}-{BOOK_DIGEST_MAX_CHAPTERS} 章，"
            f"实际 {n} 章（更多章请分批取文）"
        )
    want_synthesis = (n >= BOOK_SYNTHESIS_MIN_CHAPTERS) if synthesis is None else bool(synthesis)

    steps: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        steps.append(
            {
                "id": f"digest_{i}",
                "task": "chapter_digest",
                "inputs": {
                    "instruction": CHAPTER_DIGEST_INSTRUCTION,
                    "chapter": f"{{{{materials[{i - 1}]}}}}",
                },
                "out": f"digest_{i}",
                "budget_in": CHAPTER_DIGEST_BUDGET_IN,
                "max_out": CHAPTER_DIGEST_MAX_OUT,
                "cache": True,
                "retries": 1,
            }
        )
    in_chars = n * CHAPTER_DIGEST_BUDGET_IN
    out_chars = n * CHAPTER_DIGEST_MAX_OUT
    if want_synthesis:
        # 综述步输入上界 = 指令 + N 条 carry 整卡最大占用 + 换行余量（推导
        # 值，同 _synthesis_budget_in 的「预算必须覆盖计划自己声明的输入」纪律）
        carry = n * (1 + len(budgets.TRUNCATED_MARK) + CHAPTER_DIGEST_MAX_OUT)
        synth_budget_in = len(BOOK_SYNTHESIS_INSTRUCTION) + carry + n + 200
        steps.append(
            {
                "id": "book_synthesis",
                "task": "book_digest",
                "inputs": {"instruction": BOOK_SYNTHESIS_INSTRUCTION},
                "carry": {f"digest_{i}": BOOK_SYNTHESIS_CARRY for i in range(1, n + 1)},
                "out": "synthesis",
                "budget_in": synth_budget_in,
                "max_out": BOOK_SYNTHESIS_MAX_OUT,
                "cache": False,
                "retries": 0,
            }
        )
        in_chars += synth_budget_in
        out_chars += BOOK_SYNTHESIS_MAX_OUT
    return {
        "plan": PLAN_BOOK_DIGEST,
        "steps": steps,
        "budget": budgets.run_budget(
            steps=len(steps),
            in_chars=in_chars,
            out_chars=out_chars,
        ),
    }


def list_builtin_plans() -> list[dict[str, Any]]:
    """内置计划清单（`musespiral agent plans` 的数据面：名/步数/预算/一句话用途）。

    步数按「典型输入」给出说明而非固定值（综合随素材数展平）；这里给
    下界形态，避免 CLI 谎报固定步数。
    """
    return [
        {
            "plan": PLAN_SYNTHESIS,
            "purpose": "跨素材综合：N 篇素材各产结构化笔记，再合成四节综合稿",
            "steps": f"{SYNTHESIS_MIN_MATERIALS + 1}-{SYNTHESIS_MAX_MATERIALS + 1}",
            "budget": f"in ≤{SYNTHESIS_MAX_MATERIALS * EXTRACT_BUDGET_IN + _synthesis_budget_in(SYNTHESIS_MAX_MATERIALS)} 字"
            f" / out ≤{SYNTHESIS_MAX_MATERIALS * EXTRACT_MAX_OUT + SYNTHESIS_MAX_OUT} 字",
        },
        {
            "plan": PLAN_TAG_BATCH,
            "purpose": "批量标签建议：每素材一步产 TAG: 行，采纳后写 triage.tags",
            "steps": f"1-{MAX_STEPS}",
            "budget": f"in ≤{MAX_STEPS * TAG_BUDGET_IN} 字 / out ≤{MAX_STEPS * TAG_MAX_OUT} 字",
        },
    ]
