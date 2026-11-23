"""架构门（正典 §7.3 / 规则 5）：分层规则的持久化 CI 检查。

正典 docs/项目方案/musespiral-后端架构正典-2026-11-14.md §2.2：
  规则1：Presentation → Service → Domain → Infrastructure（只允许向下）
  规则3：Domain 层不得 import requests/urllib.request/sqlite3/subprocess
  规则4：Infrastructure 层不得 import 任何上层模块
  规则5：每条规则用自写测试在 CI 中强制执行（本文件）

实现：AST 扫描 musespiral/ 全部 .py 的 import（绝对 + 相对导入解析为绝对包名），
不依赖源码文本形态（注释/字符串里的假阳性天然免疫）。
"""
from __future__ import annotations

import ast
import bisect
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MUSESPIRAL_ROOT = Path(__file__).resolve().parent.parent / "musespiral"
# 仓库根（跨视图错位门的检测器住在仓库级 scripts/；musespiral 随仓部署）
REPO_ROOT = MUSESPIRAL_ROOT.parents[2]

# 分层映射（正典 §7.3 LAYERS；preview.py 归 Presentation）
LAYER_PREFIXES: dict[str, tuple[str, ...]] = {
    "presentation": ("musespiral.commands", "musespiral.studio", "musespiral.preview"),
    "service": ("musespiral.services",),
    "domain": ("musespiral.core", "musespiral.platforms"),
    "infra": ("musespiral.infra", "musespiral.jobs", "musespiral.browser"),
}
# 层秩：数值越大越上层；只允许 import 秩更小（向下）的层
LAYER_RANK = {"presentation": 3, "service": 2, "domain": 1, "infra": 0}

# Domain 层禁止的 I/O 库（规则3）
FORBIDDEN_IN_DOMAIN = ("requests", "urllib.request", "sqlite3", "subprocess")

# Infrastructure 允许 import 的内部模块前缀（规则4：config 全局常量 + 同层）
INFRA_ALLOWED_INTERNAL = ("musespiral.config", "musespiral.infra", "musespiral.jobs", "musespiral.browser")

# 顶层目标态（正典 §2.3 骨架）：musespiral/ 下只允许入口/配置两文件 + 子包
# preview.py：正典 §2.3 顶层 L4 表现层成员（commands/ + studio/ + preview.py）
TOPLEVEL_ALLOWED_FILES = {"__init__.py", "cli.py", "config.py", "preview.py"}
TOPLEVEL_ALLOWED_DIRS = {
    "commands", "studio", "services", "core", "platforms", "infra", "jobs", "browser",
    # 官方内置插件区（首插件族 2026-11-20，正典 §四「目录即插件」：随包分发
    # 免安装；数据目录只有 manifest.json/plugin.py，无 .py 顶层散件风险）
    "plugins_builtin",
}


@dataclass
class ImportEdge:
    importer: str  # 绝对模块名（如 musespiral.studio.actions）
    target: str  # 解析后的绝对包/模块名（如 musespiral.infra.ai）
    lineno: int
    tags: set[str] = field(default_factory=set)


def layer_of(module: str) -> str | None:
    for layer, prefixes in LAYER_PREFIXES.items():
        if module == "musespiral.preview" and layer == "presentation":
            return layer
        if any(module == p or module.startswith(p + ".") for p in prefixes):
            return layer
    return None


def _resolve_relative(module_file_parts: list[str], level: int, target: str | None) -> str:
    """把相对导入解析为绝对包名（from .X import / from ..Y import …）。"""
    base = module_file_parts[: len(module_file_parts) - level]
    if target:
        base = base + target.split(".")
    return ".".join(base)


def scan_imports() -> list[ImportEdge]:
    """AST 扫描 musespiral/ 全部 .py，产出解析后的 import 边。"""
    edges: list[ImportEdge] = []
    for path in sorted(MUSESPIRAL_ROOT.rglob("*.py")):
        parts = list(path.with_suffix("").parts)
        # parts 形如 (…, 'musespiral', 'musespiral', 'studio', 'actions')
        module = ".".join(parts[parts.index("musespiral", 1) + 1:]) or "musespiral"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:  # pragma: no cover - 语法错误本身就该炸门
            raise AssertionError(f"{path} 语法错误: {e}") from e
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges.append(ImportEdge(module, alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    target = node.module or ""
                    if target.startswith("musespiral"):
                        edges.append(ImportEdge(module, target, node.lineno))
                else:
                    target = _resolve_relative(parts[parts.index("musespiral", 1) + 1:], node.level, node.module)
                    if target.startswith("musespiral"):
                        edges.append(ImportEdge(module, target, node.lineno))
    return edges


def test_rule1_layers_only_import_downward() -> None:
    """规则1：presentation>service>domain>infra，只允许向下 import。"""
    violations = []
    for edge in scan_imports():
        src_lay, dst_lay = layer_of(edge.importer), layer_of(edge.target)
        if src_lay is None or dst_lay is None or src_lay == dst_lay:
            continue
        if LAYER_RANK[dst_lay] >= LAYER_RANK[src_lay]:
            violations.append(
                f"{edge.importer}:{edge.lineno} ({src_lay}) -> {edge.target} ({dst_lay})"
            )
    assert not violations, "跨层向上 import 违规:\n" + "\n".join(violations)


def test_rule3_domain_has_no_io_libs() -> None:
    """规则3：Domain（core/platforms）零 requests/urllib.request/sqlite3/subprocess。"""
    violations = []
    for edge in scan_imports():
        if layer_of(edge.importer) != "domain":
            continue
        if edge.target in FORBIDDEN_IN_DOMAIN:
            violations.append(f"{edge.importer}:{edge.lineno} -> {edge.target}")
    assert not violations, "Domain 层 I/O 库违规:\n" + "\n".join(violations)


def test_rule4_infra_imports_nothing_upward() -> None:
    """规则4：infra/jobs/browser 只允许 import config 与同层（infra/jobs/browser）。

    比正典 §7.3 的字面清单（commands/studio/services）更严：任何 musespiral.*
    上层/顶层模块（core/platforms/cli/studio/…）一律禁止——防止再度出现
    infra 反向依赖顶层散件（如 Phase C 修复前的 from ...stats import）。
    """
    violations = []
    for edge in scan_imports():
        if layer_of(edge.importer) != "infra":
            continue
        if not edge.target.startswith("musespiral."):
            continue
        if any(
            edge.target == p or edge.target.startswith(p + ".")
            for p in INFRA_ALLOWED_INTERNAL
        ):
            continue
        violations.append(f"{edge.importer}:{edge.lineno} -> {edge.target}")
    assert not violations, "Infra 层上探违规:\n" + "\n".join(violations)


def test_toplevel_matches_canonical_skeleton() -> None:
    """顶层零散件防回归：musespiral/ 顶层只剩 cli/config/__init__ + 正典子包。

    Phase C 修复轮曾检出 9 个目标清单外顶层项（aihabits/digest/factcheck/
    pack/poster/review/stats/theme_learn/cards）——本断言锁死归位成果。
    """
    strays = []
    for entry in MUSESPIRAL_ROOT.iterdir():
        if entry.name.startswith("_") or entry.name in {"__pycache__"}:
            if entry.name not in TOPLEVEL_ALLOWED_FILES and entry.is_file() and not entry.name.startswith("__"):
                strays.append(entry.name)
            continue
        if entry.is_file():
            if entry.name not in TOPLEVEL_ALLOWED_FILES and entry.suffix == ".py":
                strays.append(entry.name)
        elif entry.is_dir():
            if entry.name not in TOPLEVEL_ALLOWED_DIRS:
                strays.append(entry.name + "/")
    assert not strays, "顶层清单外项:\n" + "\n".join(strays)


def test_no_legacy_shim_paths_return() -> None:
    """垫片防回归：Phase C 删除的旧路径不得复活。"""
    gone = [
        "token.py", "publisher.py", "analytics.py", "uploader.py", "notes.py",
        "notessql.py", "checklist.py", "adapt.py", "ai.py", "parser.py",
        "themes.py", "pack.py", "stats.py", "aihabits.py", "digest.py",
        "factcheck.py", "review.py", "poster.py", "theme_learn.py", "cards",
        "renderer", "uploader_browser", "cards",
        "core/images", "core/sources", "core/jobs",
    ]
    revived = [g for g in gone if (MUSESPIRAL_ROOT / g).exists()]
    assert not revived, "已删除的旧路径复活:\n" + "\n".join(revived)


# ── F45 方言守门（第十七期 §二：prompt 组装点零模型/供应商名）──
# 供应商无关红线：提示词组装处出现特定模型/供应商字样（gpt/glm/qwen/
# claude/deepseek/gemini——含注释与 docstring：离进提示词只差一次复制）
# 即违规。扫描函数独立成模块级，test_dialect_gate.py 用违规样本自证
# 「断言真拦截」（同一代码路径，非另写一份逻辑）。
# 本守门必须落在被默认收集的测试文件里（conftest.py 中的测试函数不进
# pytest 目录扫描——曾放 conftest 致 `pytest -q` 恒不执行，集成复核修正）。

# 禁用字样（小写；大小写不敏感匹配）。第十八期 F47 起单一事实源迁至产品侧
# musespiral.config.DIALECT_FORBIDDEN（运行时 prompts.d 覆盖保存校验与测试守门
# 共表防漂移）；此处 re-import 保住既有名字（test_dialect_gate 消费路径不变）。
from musespiral.config import DIALECT_FORBIDDEN  # noqa: E402

# prompt 组装点（AI 消息/提示词构建的落点；新增 AI 消费方时同步登记）
DIALECT_SCAN_DIRS: tuple[str, ...] = (
    "core/adaptation",       # adapt（generator + specs 提示词）
    "core/quality",          # digest / factcheck / title / review 提示词
    "infra/ai",              # chat 入口 + habits/style 注入 + ai check
    "core/agent",            # Phase D F75-F79（设计 §三 依赖规则 4）：三套计划
                             # 模板（EXTRACT/SYNTHESIZE/TAG_INSTRUCTION）——
                             # TASKS 扩键即自动纳管，本目录登记是机器兜底
    "plugins_builtin",       # PL-5（2026-11-20）：key-segment 的 AI 切片提示词
                             # 组装点在插件目录（目录递归——后续官方 AI 插件
                             # 的提示词自动纳管，无需逐文件登记）
)
DIALECT_SCAN_FILES: tuple[str, ...] = (
    "core/discovery/suggester.py",   # discover_suggest 提示词
    "services/rewrite_service.py",   # rewrite
    "services/annotate_service.py",  # annotate_rewrite
    "services/compose_service.py",   # compose（第十八期 F48：任务底座模板+插槽管线）
    "services/visual_service.py",    # Phase C F70-F74（§八-4）：visual_suggest 参数建议
    "services/tag_service.py",       # Phase D F77（§二-F77.5）：tag_suggest 建议模板
    "services/topic_radar_service.py",  # Phase D F78（§二-F78.3）：雷达 AI 增强
                                     # 模板 AI_ENHANCE_TEMPLATE（复核修复批补登记
                                     # ——此前漏列，该组装点曾不受守门扫描）
    "services/editor_ai_service.py",  # E-3（2026-11-19，§3.3）：编辑器五动作
                                     # 模板 DEFAULT_PROMPTS（随 TASKS 扩五键同批
                                     # 登记——扩键自动纳管的前提是本文件在扫描面）
    "services/materials_service.py",  # N-2（2026-11-21，素材闭环 §3.2 路径 C）：
                                     # distill 模板 DEFAULT_DISTILL_PROMPT（随
                                     # TASKS 扩 material_distill 键同批登记）
    "services/quality_loop.py",       # P1-A（2026-11-23，契约 §3.26）：质量循环
                                     # 修复模板 _PROMPT（复用 task=rewrite 通道）
    "services/title_ab.py",           # P1-B（2026-11-23，契约 §3.26）：标题 A/B
                                     # 三风格模板 _PROMPT（task=title 通道）
)


def dialect_violations(rel_path: str, text: str) -> list[str]:
    """单个 prompt 组装点文件的方言违规清单（守门测试与自证单测共用）。"""
    low = text.lower()
    return [
        f"{rel_path}: 禁用字样 {tok!r}（模型/供应商名不得出现在 prompt 组装点）"
        for tok in DIALECT_FORBIDDEN if tok in low
    ]


def scan_prompt_dialect(pkg_root) -> list[str]:
    """扫描全部 prompt 组装点 .py（目录递归 + 点名文件，含新增文件）。"""
    import pathlib
    targets: list[pathlib.Path] = []
    for d in DIALECT_SCAN_DIRS:
        targets.extend(sorted((pkg_root / d).rglob("*.py")))
    for f in DIALECT_SCAN_FILES:
        p = pkg_root / f
        if p.exists():
            targets.append(p)
    violations: list[str] = []
    for f in targets:
        if "__pycache__" in f.parts:
            continue
        violations.extend(
            dialect_violations(str(f.relative_to(pkg_root)), f.read_text(encoding="utf-8"))
        )
    return violations


def test_architecture_prompt_dialect_free() -> None:
    """F45 守门：prompt 组装点零模型/供应商方言（供应商可切换且输出一致的正典前提）。"""
    violations = scan_prompt_dialect(MUSESPIRAL_ROOT)
    assert not violations, "提示词方言违规:\n" + "\n".join(violations)


# ── F47 守门扩面：prompts.d 覆盖文件同受方言守门 ──────────
# 覆盖文件会整体替换代码内置任务底座模板进提示词（创作台 §2.3 级联），
# 与源码组装点同一红线。保存时校验拦 API 面（settings._save_prompt →
# save_override 拒写）；本扫描拦手工落盘面（用户直接编辑文件绕过 API）。
# 真实 ~/.musespiral/prompts.d 在本守门的巡视范围（CI 门语义）；hermetic 自证
# 在 test_dialect_gate.py 用隔离 MUSESPIRAL_HOME 喂违规样本走同一扫描函数。


def scan_prompt_overrides(home_dir) -> list[str]:
    """扫描 <home>/prompts.d/*.txt 的方言违规（缺失目录=零违规）。"""
    import pathlib

    d = pathlib.Path(home_dir) / "prompts.d"
    if not d.is_dir():
        return []
    violations: list[str] = []
    for f in sorted(d.glob("*.txt")):
        if "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        low = text.lower()
        for tok in DIALECT_FORBIDDEN:
            if tok in low:
                violations.append(
                    f"prompts.d/{f.name}: 禁用字样 {tok!r}（覆盖文件不得含模型/供应商名）"
                )
    return violations


def test_prompt_overrides_dialect_free() -> None:
    """F47 守门扩面：prompts.d 覆盖文件零方言（运行时保存校验的手工绕过兜底）。"""
    from musespiral import config

    violations = scan_prompt_overrides(config.home_dir())
    assert not violations, "prompts.d 覆盖文件方言违规:\n" + "\n".join(violations)


# ── 后端重构轮（2026-11-16）：去重成果回归锁 + 契约端点全集对齐 ──
# 依据=docs/项目方案/mdpub-API契约-2026-11-16.md §3.1/§3.2 与后端架构正典
# §四 P2/P3；锁死「单一权威实现」不因新代码回涨出第二份副本。


def test_services_timestamp_single_authority() -> None:
    """服务层时间戳单一权威：services/ 不得定义 `_now_iso` 或内联
    `isoformat(timespec="seconds")`（权威=jobs.store.now_iso，六处私有
    副本已于重构轮收编；monkeypatch 锚点经 `import as _now_iso` 保留）。"""
    violations = []
    for f in (MUSESPIRAL_ROOT / "services").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        if "def _now_iso" in text:
            violations.append(
                f"{f.name}: 定义了 _now_iso（应 from ..jobs.store import now_iso as _now_iso）"
            )
        if 'isoformat(timespec="seconds")' in text:
            violations.append(f"{f.name}: 内联 isoformat(timespec=…)（应走 jobs.store.now_iso）")
    assert not violations, "服务层时间戳副本回涨:\n" + "\n".join(violations)


def test_services_lenient_jsonl_read_single_authority() -> None:
    """服务层宽容 JSONL 读单一权威：services/ 不得出现行级 `json.loads(line)`
    循环（权威=infra/storage/jsonl.read_rows；jobs/store._parse 是字节级保留
    的另一套纪律，不在本断言范围）。"""
    violations = []
    for f in (MUSESPIRAL_ROOT / "services").glob("*.py"):
        if "json.loads(line)" in f.read_text(encoding="utf-8"):
            violations.append(f"{f.name}: 自实现行级宽容读（应走 infra.storage.jsonl.read_rows）")
    assert not violations, "服务层宽容读副本回涨:\n" + "\n".join(violations)


def test_shared_render_helper_layered_correctly() -> None:
    """渲染口径单一权威在服务层：publish_service.render_article 公开存在，
    studio/payload._render 为薄壳委托（Presentation→Service 合法向下依赖，
    rule1 的具名锚点——两处逐字副本已于重构轮收编）。"""
    import inspect

    from musespiral.services.publish_service import render_article

    assert callable(render_article)
    import musespiral.studio.payload as payload_mod

    src = inspect.getsource(payload_mod._render)
    assert "publish_service.render_article" in src, "payload._render 应委托服务层权威实现"


CONTRACT_GET_EXACT = {
    # §3.1 既有 20 条（另 /api/jobs/<id> 动态段 + GET / 在 server.py 特判）
    "/api/articles", "/api/article", "/api/article/html", "/api/article/review",
    "/api/article/body", "/api/annotations", "/api/platforms", "/api/stats",
    "/api/meta", "/api/health", "/api/jobs", "/api/discover", "/api/discover/star",
    "/api/materials", "/api/materials/detail", "/api/settings", "/api/sources",
    "/api/compose", "/api/compose/session", "/asset",
    # §3.23 新增（FEAT-P1B 2026-11-23：写作节奏读面——纯读，rounds 历史现算）
    "/api/writing_stats",
    # §3.2-2 新增（P4 排期周历）
    "/api/plan",
    # §3.3 新增（B 采集扩展 F68/F69：采集面状态只读端点）
    "/api/ingest/status",
    # §3.4 新增（A 多模态 F60/F62/F63：素材文件服务/标签频次/手工集合清单）
    "/api/materials/file", "/api/materials/tags", "/api/materials/collections",
    # §3.5 新增（Phase C 视觉创作 F71/F74：设计稿详情/模板注册表/预设清单）
    "/api/visual/design", "/api/visual/templates", "/api/visual/presets",
    # §3.6 新增（Phase D F75.4 执行面读端：编排运行清单/详情双模式）
    "/api/agent/runs",
    # §3.6 新增（Phase D F78.4 选题雷达快照读面：只读 latest；空态 snapshot:null）
    "/api/topic_radar",
    # §3.7 新增（G1 批 2026-11-17：F1 AI 治理两读面 + G1 预览字节流——
    # usage 与 CLI ai usage 同数同口径；media/preview 是 A-2 服务端临时文件
    # 的消费面，前端 <img> 直指不经 b64）
    "/api/ai/usage", "/api/ai/budget", "/api/media/preview",
    # §3.9 新增（R0 书源功能 2026-11-18：四读面——索引/单源详情/订阅清单/引擎状态）
    "/api/booksource", "/api/booksource/one", "/api/booksource/subs",
    "/api/booksource/engine",
    # §3.10 新增（R3 书源接驳+R3.5 提炼 2026-11-18：三读面——详情/目录/章节原文）
    "/api/book/info", "/api/book/toc", "/api/book/original",
    # §3.16 新增（G-T5 TTS 朗读 2026-11-19，正典=《musespiral-调研吸收功能设计-
    # 2026-11-18》§六）：朗读产物 MP3 字节流（前端内嵌 <audio src> 直指；
    # 仅 .mp3 + 魔数复核）。GET 33→34。
    "/api/tts/file",
    # §3.17 新增（E-6 版本 diff 2026-11-19，正典=《musespiral-专业创作者视角深度
    # 评审与改进-2026-11-18》§3.7）：版本池清单 + 任意两版对比（纯读——
    # 无 v1/v2 只回清单，有则附 hunks 与红删绿增 HTML）。GET 41→42。
    "/api/article/diff",
    # §3.19 新增（PL-3 插件系统接驳批 2026-11-20 复核修复，插件系统设计
    # §十二 as-built）：插件清单 + 流水线清单两精确读面。动态读面三族
    # （/api/plugins/<id>/check、/api/pipelines/<id>、/api/pipelines/runs/
    # <run_id>）由 _RouteSet 前缀承载，不入精确锁（/api/jobs/<id> 先例）。
    # GET 42→44。
    "/api/plugins",
    "/api/pipelines",
    # §3.20 新增（P2 素材再加工循环 2026-11-20，正典=《musespiral-格式生态与
    # 素材流转能力分析-2026-11-20》§3.3）：derived 打磨版本列表 + 同链两版
    # 对比（纯读；diff 复用 E-6 core/version/diff 引擎；POST 零新增——迭代
    # 元信息由既有 /api/action/refine adopt 写入）。GET 44→46。
    "/api/materials/iterations",
    "/api/materials/iterations/diff",
    # §3.22 新增（N-5 多平台并排预览 2026-11-21，正典=《mdpub-优化重构与后续
    # 开发总体方案-2026-11-19》§二 N-5）：物料包清单 + 单平台 payload/原文/
    # diff（纯读双态；diff 复用 E-6 引擎；残包 200 available:false 不 400；
    # POST 零新增）。GET 46→47。
    "/api/article/pack",
    # ARCH-5 新增（2026-11-22 语义基础设施，正典=《musespiral-极限架构与
    # 功能扩展总规划-2026-11-21》§ARCH-5）：统一搜索读面——q +
    # mode=keyword|semantic|hybrid（语义缺位自动降级为关键词+标签；
    # FEAT-1 的后端数据面，UI 归 FEAT-1 批）。GET 47→48。
    "/api/materials/search",
    # W1-B1 新增（2026-11-23，正典=《musespiral-运维发布改良设计-2026-11-23》
    # §二 W1-B1）：轻量探针面——恰五键（ok/version/uptime_s/jobs_pending/
    # materials_total），供 ctl.sh smoke 与外部探针。与 F20 的 /api/health 九键
    # 面板并存（该面板前端四处消费、形状不动——「前端零字节」验收）。GET 49→50。
    "/api/healthz",
}
CONTRACT_POST_EXACT = {
    # §3.1 既有 27 条
    "/api/action/adapt", "/api/action/pack", "/api/action/publish", "/api/action/batch_adapt",
    "/api/action/status", "/api/article/save", "/api/annotations/add", "/api/annotations/delete",
    "/api/action/annotate_rewrite", "/api/action/fetch", "/api/action/discover_draft",
    "/api/discover/star", "/api/action/sync_stats", "/api/settings/kb", "/api/settings/ai",
    "/api/materials/triage", "/api/materials/delete", "/api/sources/verify",
    "/api/sources/enabled", "/api/sources", "/api/action/ingest", "/api/action/refine",
    "/api/compose/new", "/api/compose/add", "/api/compose/generate", "/api/compose/decide",
    "/api/compose/adopt",
    # §3.8 新增（F-C 素材池撤回 2026-11-18：撤池同步瞬时）
    "/api/compose/remove_source",
    # §3.2-1 新增（P3 封面工坊）
    "/api/action/cover",
    # §3.3 新增（B 采集扩展 F65/F66/F67：/api/ingest/* 三动作端点）
    "/api/ingest/refresh", "/api/ingest/clip", "/api/ingest/crawl",
    # §3.4 新增（A 多模态 F61/F62：视频登记 + 手工集合五动作合并端点；
    # F59 upload 为二进制旁路路由，在 server._BINARY_POST_ROUTES 承载——
    # 不占 JSON 动作分派表，但同受本 POST 面锁约束）
    "/api/materials/register", "/api/materials/collections", "/api/materials/upload",
    # §3.5 新增（Phase C 视觉创作 F71-F74：render/save/export/preset 四动作；
    # 凡 POST 一律 confirm:true——契约 §1.2）
    "/api/visual/render", "/api/visual/save", "/api/visual/export", "/api/visual/preset",
    # Phase D 智能体层新增（§3.6）：F77 标签建议（甲组）+ F75.4 执行面 / F76.4 采纳面（乙组）
    "/api/action/tags_suggest",
    "/api/agent/run", "/api/agent/adopt",
    # §3.7 新增（G1 文生图 2026-11-17，调研 §四/§七）：生成（同步零落盘）/
    # 采纳（搬正+tEXt+落库）/连接验证（health 三态）
    "/api/action/generate_image", "/api/action/adopt_generated", "/api/action/media_verify",
    # §3.7 新增（E1 金路径 2026-11-17 组合批，评审点 3）：一键管线触发 →
    # job（kind=pipeline，KINDS 14→15）；调 pipeline_service.submit_to_runner
    "/api/action/pipeline",
    # §3.9 新增（R0 书源功能 2026-11-18：五动作——预览零落库/落库/刷新/订阅增删）
    "/api/booksource/import", "/api/booksource/import_apply",
    "/api/booksource/refresh",
    "/api/booksource/subs/add", "/api/booksource/subs/del",
    # §3.10 新增（R3 书源接驳+R3.5 提炼 2026-11-18：两动作——聚合搜索 job
    # book_search / 选章入素材库 job book_fetch；GET+3/POST+2 同批入锁）
    "/api/book/search", "/api/materials/from-chapters",
    # 集成接线轮（2026-11-18）：长任务协作式取消（R3.5 正典 §2.2 取消语义
    # 落点——已完成章保留入库/未完成章放弃；全域 job 可用，book_fetch 设
    # 检查点=取文逐章/提炼逐 pass）。POST 53→54。
    "/api/jobs/cancel",
    # §3.11 新增（E-3 编辑器 AI 微操作 2026-11-19，正典=《musespiral-专业创作者
    # 视角深度评审与改进-2026-11-18》§3.3）：选中文本五动作（expand/
    # summarize/rewrite/continue/qa）合并为一个端点，同步零落盘零 job。
    # POST 54→55。
    "/api/editor/ai-action",
    # §3.12 新增（G-T2 大纲模式 2026-11-18，正典=《musespiral-调研吸收功能设计-
    # 2026-11-18》§三）：大纲 generate|update|approve|expand 统一端点。POST 55→56。
    "/api/compose/outline",
    # §3.13 新增（G-T4 滚动摘要 2026-11-19，正典=同篇 §五）：刷新 / 用户手改
    # 两形态同一端点（载荷分流：有 summary=手改，无=AI 刷新）。POST 56→57。
    "/api/compose/rolling-summary",
    # §3.14 新增（G-T1 Ghost Text 2026-11-19，正典=《musespiral-调研吸收功能设计-
    # 2026-11-18》§二）：编辑器行内 AI 预判续写（前端 800ms 防抖触发，同步
    # 零落盘零 job）。POST 57→58。
    "/api/editor/ghost-write",
    # §3.15 新增（G-T3 双模型对比 2026-11-19，正典=同篇 §四）：同 prompt 双档案
    # 并行对比出稿——generate|pick|keep 三形态同一端点。POST 58→59。
    "/api/compose/dual",
    # §3.16 新增（G-T5 TTS 朗读 2026-11-19，正典=同篇 §六）：正稿 → 朗读脚本
    # → edge-tts（微软免费端点，外部服务非 LLM，零 API key）→ MP3 落文章目录。
    # 网络长动作 → job tts_generate（KINDS 17→18）。POST 59→60。
    "/api/action/tts_generate",
    # §3.17 新增（E-6 版本恢复 2026-11-19，正典=《musespiral-专业创作者视角深度评审
    # 与改进-2026-11-18》§3.7）：池内任一版覆盖当前版——confirm 闸门强制；
    # 当前版先入池（撤销可撤销）。POST 60→61。
    "/api/article/restore",
    # §3.18 新增（E-5 小说模式 2026-11-19，正典=同篇 §3.6「I3 小说上下文管理」）：
    # 前情提要刷新/手改 + 会话种类切换 + 人物卡建卡/挂载/摘下合并为一个端点
    # （action 分流，同 compose/outline 的多形态纪律——契约锁 POST 只 +1）。
    # POST 61→62。
    "/api/compose/novel-summary",
    # §3.19 新增（PL-3 插件系统接驳批 2026-11-20 复核修复，插件系统设计 §十二
    # as-built）：插件重扫（v1 refresh≡全量重扫）/ 流水线保存（UI 形状
    # {id,plugin:manifest} 归一引擎形状落盘；保存前 validate_pipeline_contract
    # ——非法顺序 400）/ 流水线运行（plan_pipeline 校验先行 → job
    # plugin_pipeline → {job_id, run_id}）。设计 §十二 的 /{id} 形动态 POST
    # 改精确路径 + body id（server.py 分派零改动，/api/jobs/<id> 先例反向）。
    # POST 62→65。
    "/api/plugins/refresh",
    "/api/pipelines",
    "/api/pipelines/run",
    # §3.19 E-4 转写编辑接驳（PL-6 批 2026-11-20，台账 §四「PL-6 = E-3+E-4」）：
    # 编辑版保存——body run_id（PL-3「动态段 POST 改精确路径」先例），落
    # segments.edited.json / ranges.edited.json（key-segment/frame-grab 的
    # 既有消费位置）；GET segments 与 GET audio 为动态读面，由 _RouteSet
    # 前缀承载不入精确锁（/api/jobs/<id> 先例）。POST 65→66。
    "/api/pipelines/runs/segments",
    # §3.21 新增（N-2 素材闭环 2026-11-21，正典=《musespiral-素材闭环与专业编辑器
    # 设计-2026-11-18.md》§五 P-1 +《总体方案》N-2）：灵感速记（text 必填
    # 其余自动填，type=text + meta.sub_type=spark——裁决不新增
    # MATERIAL_TYPES）/ 文章反哺三路径（full/excerpt/distill 一端点 mode
    # 分流）/ 内部源刷新（扫 config.scan_root 产素材，幂等按 slug 去重）。
    # 三条同步瞬时（扫本地目录与 AI 单次提炼不走 job）。POST 66→69。
    "/api/materials/spark",
    "/api/materials/from-article",
    "/api/sources/internal/refresh",
    # §3.19 修订 3 新增（AI-1 三态裁决 2026-11-21，正典=《mdpub-AI产物裁决机制
    # 设计-2026-11-21》§二/§五）：裁决动作 adopt/discard——**动态段 POST**
    # （run_id 在 URL 段内，server._handle 裁决分流先于 GET 面判定），
    # 占位形路径入锁由 POST_DYNAMIC_SURFACE 承载（<run_id> 运行期替换，
    # GET_DYNAMIC_PREFIXES 承载动态读面同一思路）。本批不含 regenerate
    # （归 AI-3）。POST 69→71。
    "/api/pipelines/runs/<run_id>/adopt",
    "/api/pipelines/runs/<run_id>/discard",
    # §3.19 修订 4 新增（AI-3 节点重生成 2026-11-21，裁决三件套最后一件；
    # 同上动态段 POST——body {step:'ks'|'aw', feedback?}，长动作走 job
    # kind=plugin_pipeline）。POST 71→72。
    "/api/pipelines/runs/<run_id>/regenerate",
    # §3.23 新增（N-4 置信度+血统 2026-11-21，正典=《mdpub-优化重构与后续开发
    # 总体方案-2026-11-19.md》§二 N-4）：人工核验标记/取消同端点（body
    # {id, verified?}——缺省 true 标记、false 取消）写 triage.verified，与
    # AI 自评 confidence 正交不混写；同步瞬时（triage 窄门追加，无 job）。
    # AI 自评面零新增端点（refine preview/adopt 与 agent adopt 既有端点形状
    # 扩展）；GET 面零新增（低置信筛选走既有 /api/materials confidence=low
    # 查询参数）。POST 72→73。
    "/api/materials/verify",
    # ARCH-5 新增（2026-11-22 语义基础设施，正典=《musespiral-极限架构与
    # 功能扩展总规划-2026-11-21》§ARCH-5）：素材向量索引全量重建（confirm
    # 闸门；幂等重嵌 + 清理残留行；embedding 缺位 200 ok:false 安装引导）。
    # POST 73→74。
    "/api/materials/reindex",
    # FEAT-2 新增（2026-11-22 智能组稿，正典=《musespiral-极限架构与功能扩展总规划
    # -2026-11-21》§FEAT-2）：主题描述→语义搜素材→建会话+素材入池+AI 三段式大纲
    # 统一端点（两形态载荷分流：无 material_ids=搜候选 / 有=建会话；confirm 闸门）。
    # POST 74→75。
    "/api/compose/smart-start",
    # §3.26 新增（P1 AI 辅助创作增强批 2026-11-23）：质量循环 check→AI 修复
    # →再 check 多轮长动作 → job quality_loop（KINDS 19→20；修复稿
    # .quality_fix.md 预览零覆盖原文）+ 标题 A/B 三风格（mode 分流：generate
    # 同步零落盘零 job / apply 三选一写回版本池先行；不接微信 A/B API）。
    # POST 75→77。
    "/api/action/quality_loop",
    "/api/action/title_ab",
}


def test_route_surface_matches_api_contract() -> None:
    """API 契约面对齐锁：GET/POST 路由表与契约 §3.1–§3.4 全集逐条相等。

    任何端点增删（含废弃下线）须先改契约文档再改本断言与路由表——
    「后端改动须先改本文再改代码」（契约 §一 效力条）。POST 面 = JSON
    动作表（_ACTION_ROUTES）∪ 二进制旁路表（_BINARY_POST_ROUTES，F59
    upload——confirm 走 query 特判，契约 §3.2 修订条）∪ 动态段 POST 占位面
    （POST_DYNAMIC_SURFACE，AI-1 三态裁决 adopt/discard + AI-3 regenerate
    ——run_id 在 URL 段内，占位形入锁同 GET_DYNAMIC_PREFIXES 承载动态读面
    的思路）。"""
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.plugin_actions import POST_DYNAMIC_SURFACE
    from musespiral.studio.routing import _GET_ROUTES
    from musespiral.studio.server import _BINARY_POST_ROUTES

    actual_get = set(_GET_ROUTES)
    assert actual_get == CONTRACT_GET_EXACT, (
        f"GET 面漂移 缺失={sorted(CONTRACT_GET_EXACT - actual_get)} "
        f"多余={sorted(actual_get - CONTRACT_GET_EXACT)}"
    )
    actual_post = (
        set(_ACTION_ROUTES) | set(_BINARY_POST_ROUTES) | set(POST_DYNAMIC_SURFACE)
    )
    assert actual_post == CONTRACT_POST_EXACT, (
        f"POST 面漂移 缺失={sorted(CONTRACT_POST_EXACT - actual_post)} "
        f"多余={sorted(actual_post - CONTRACT_POST_EXACT)}"
    )


def test_frontend_api_literals_covered_by_backend_surface() -> None:
    """复核 medium-3 对拍锁：前端消费面 ⊆ 后端服务面。

    缺陷现场（PL-3 复核 2026-11-20）：前端字面量锁（test_studio_ui
    CONTRACT_PATHS）与后端路由锁（CONTRACT_GET/POST_EXACT）各自为真、
    无一致性断言——「前端永远调 404」与「后端固定 42/62 条」同时全绿，
    2668 绿不构成验收证据。本锁从 ui.js 源重提全部 /api/ 字面量（单引号
    + 模板串静态前缀），逐条要求被 GET 精确面 / POST 精确面 / 动态前缀族
    之一接住——此后「前端新调一个端点而后端没接」必红。
    """
    import re as _re

    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import GET_DYNAMIC_PREFIXES, _GET_ROUTES
    from musespiral.studio.server import _BINARY_POST_ROUTES

    src = (MUSESPIRAL_ROOT / "studio" / "ui.js").read_text(encoding="utf-8")
    raw = set(_re.findall(r"'(/api/[^']+)'", src))
    raw |= set(_re.findall(r"`(/api/[^`$]*)\$\{", src))
    # 归一：去 query 串（test_studio_ui CONTRACT_PATHS 同款）；模板前缀保留尾 /，
    # 以「去尾斜杠后命中精确面或动态前缀族」判定接住。
    literals = {lit.split("?", 1)[0] for lit in raw}
    assert literals, "ui.js /api/ 字面量提取为空——提取器失效同样要红"

    def _covered(lit: str) -> bool:
        base = lit.rstrip("/")
        return (
            lit in _GET_ROUTES
            or lit in _ACTION_ROUTES
            or lit in _BINARY_POST_ROUTES  # F59 二进制旁路（upload）
            or base in _GET_ROUTES
            or base in _ACTION_ROUTES
            or base in _BINARY_POST_ROUTES
            or any(lit.startswith(p) and len(lit) > len(p) for p in GET_DYNAMIC_PREFIXES)
        )

    uncovered = sorted(lit for lit in literals if not _covered(lit))
    assert not uncovered, (
        f"前端消费端点无后端路由接住: {uncovered}"
        "（新端点须先改契约文档 §3.x 再接路由——见 test_route_surface_matches_api_contract）"
    )


def test_contract_paths_frontend_literals_have_backend_routes() -> None:
    """契约锁对拍门（台账 D-1）：前端字面量集合逐条必须被后端路由接住。

    缺陷现场：`tests/test_studio_ui.CONTRACT_PATHS`（前端字面量契约）与
    本文件 `CONTRACT_GET/POST_EXACT`（后端路由锁）**各自为真、互不校验**
    ——把一条前端根本不存在的端点写进 CONTRACT_PATHS，两边同时全绿，而线上
    是「前端永远调 404」。上一道门（test_frontend_api_literals_…) 是**反方向**
    的：它从 ui.js 重提字面量要求被后端接住，管的是「新调的没接」。本门补的
    是「契约表里登记了、后端没这条」——登记面本身不能再是自证的。

    判定：CONTRACT_PATHS 每条必须被后端三张路由表之一直接接住——
    `_GET_ROUTES`（__contains__ 自带动态段，`/api/jobs/<id>` 由前缀族算命中）、
    `_ACTION_ROUTES`（JSON 动作表）、`_BINARY_POST_ROUTES`（二进制旁路）。
    **不做去尾斜杠归一**：后端分派按原始 path 精确匹配，带尾斜杠的
    `/api/articles/` 在线上真的 404——门松一档就漏一档。
    """
    import ast as _ast

    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES
    from musespiral.studio.server import _BINARY_POST_ROUTES

    # 从 test_studio_ui 源码 AST 取常量：不 import 该模块（它依赖构建产物与
    # 会话级 fixture，测试模块互相 import 会把两套假设绑死）。
    contract_file = Path(__file__).resolve().parent / "test_studio_ui.py"
    tree = _ast.parse(contract_file.read_text(encoding="utf-8"))
    paths = None
    for node in tree.body:
        if isinstance(node, _ast.Assign) and any(
            getattr(t, "id", None) == "CONTRACT_PATHS" for t in node.targets
        ):
            paths = _ast.literal_eval(node.value)
            break
    assert paths, f"{contract_file.name} 未找到 CONTRACT_PATHS 字面量（提取器失效同样要红）"

    orphans = sorted(
        p for p in paths
        if p not in _GET_ROUTES and p not in _ACTION_ROUTES and p not in _BINARY_POST_ROUTES
    )
    assert not orphans, (
        f"CONTRACT_PATHS 中的路径后端无路由接住（前端将永远 404）: {orphans}"
    )


# ── 跨视图错位门（台账 A 类专项，2026-11-20）────────────────────────
# 背景：build_ui.py 把 ui.js 按 `// @view:<name>` 拆成 core.js + views/<name>.js，
# 视图脚本懒注入；core 侧曾经用 no-op 占位符兜底，于是「函数随定义搬进视图块、
# 消费点在别的块」不报错、只静默失效（台账 A-1~A-5：直入目标视图才发现）。
# 2026-11-20 A 类专项把占位符整族退役，改 VAPI 契约 + 工具函数归 core。
# 本门把整类问题**永久堵死**：新增视图/新搬函数时自动检查，不靠人记得跑检测器。

_UI_JS = MUSESPIRAL_ROOT / "studio" / "ui.js"


def _load_cross_view_detector():
    """加载仓库级检测器 scripts/audit_cross_view_deps.py（跨视图词法判据的单一权威）。

    判定逻辑住在检测器里而非测试内：CLI 审计、本门、单测消费同一份实现，
    修一处三处同步——门与工具口径分叉过一次（analyze 只追 `F(` 直呼），
    这里显式钉成同源。
    """
    import importlib.util

    script = REPO_ROOT / "scripts" / "audit_cross_view_deps.py"
    if not script.is_file():
        # 检测器是 monorepo 级治理脚本（仓库根 scripts/，不随 musespiral
        # 独立仓分发）——独立仓 CI 检出（SeekerGalahad/musespiral 单仓根）
        # 天然缺席，五条 V1 门在本地 monorepo 照常严格执行。
        pytest.skip(f"跨视图检测器缺席（monorepo 级治理脚本，独立仓不随包分发）: {script}")
    spec = importlib.util.spec_from_file_location("musespiral_audit_cross_view_deps", script)
    assert spec is not None and spec.loader is not None, f"检测器加载失败: {script}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_v1_no_cross_view_function_misplacement() -> None:
    """规则 V1（跨视图错位门）：@view:X 定义的函数不得被 Y≠X 或 core 引用。

    红灯条件（三条同时成立）：
      ① 该名字在某个 `// @view:<name>` 块内以**顶层** function 声明 / var 函数
         表达式定义；
      ② 存在该名字的引用点（`F(` 直呼与裸标识符引用都算——`setTimeout(F,…)`、
         `arr.map(F)`、`el.onclick=F` 在视图未注入时同样是 undefined/占位 no-op）
         落在别的视图块或 core 段；
      ③ core 段没有该名字的**真实现**（没有定义，或只有 `var F=function(){}`
         no-op 占位符）——core 有真身时全局解析恒落 core，视图内同名只是遮蔽。

    豁免（不算引用，避免噪声红把人逼得关掉门）：定义处本身、`{name: …}` 对象键、
    属性访问 `x.name`、以及落在同名形参/局部 var·let·const/嵌套 function 作用域
    内的遮蔽引用。

    修复方向由检测器 verdict 给：utils（无状态）移回 core；view-owned（读视图
    状态）改调用方经 VAPI 或补 core 真实现。
    """
    det = _load_cross_view_detector()
    violations = det.strict_misplacements(_UI_JS.read_text(encoding="utf-8"))
    assert not violations, (
        "跨视图函数错位（视图未注入时静默失效/直接 ReferenceError）:\n"
        + det.format_misplacements(violations)
        + "\n处置：无视图状态的工具函数移回 core 段；视图专属函数改由该视图内调用，"
        "或 core 侧提供真实现（不要再加 no-op 占位符——那是本门要堵死的形态）"
    )


def test_rule_v1_gate_red_green() -> None:
    """V1 红绿自证（门若不红，等于没有门）。

    用检测器自身的判据函数打合成样本：真错位必须报出来，安全形态一条都不许报。
    这是「门口径」的回归锚——判据被改松（例如把裸引用排除、把遮蔽豁免写宽）
    时本测先红，而不是等到线上再发现失效。
    """
    det = _load_cross_view_detector()

    def _hit(src: str) -> list[str]:
        return [it["fn"] for it in det.strict_misplacements(src)]

    # 红 ①：视图函数被 core 直呼（A-1 原形——直入目标视图时占位 no-op 吞掉）
    assert _hit(
        "var state={q:1};\n"
        "// @view:a\n"
        "function loadDiscover(){return state.q;}\n"
        "// @endview\n"
        "function coreCall(){return loadDiscover();}\n"
    ) == ["loadDiscover"]

    # 红 ②：A 视图函数被 B 视图直呼（A-3 原形：源表调 insight 的 dkey）
    assert _hit(
        "// @view:a\n"
        "function dkey(dt){return dt;}\n"
        "// @endview\n"
        "// @view:b\n"
        "function renderTable(){return dkey(new Date());}\n"
        "// @endview\n"
    ) == ["dkey"]

    # 红 ③：裸标识符引用（不经 `(`）——回调注册，视图未注入时同样断
    assert _hit(
        "// @view:a\n"
        "function repaint(){return 1;}\n"
        "// @endview\n"
        "setTimeout(repaint,10);\n"
    ) == ["repaint"]

    # 绿 ①：同块内自用（定义块 = 引用块）
    assert _hit(
        "// @view:a\n"
        "function inner(){return 1;}\n"
        "function outer(){return inner();}\n"
        "// @endview\n"
    ) == []

    # 绿 ②：core 有真实现 → 全局解析恒落 core，视图同名只是遮蔽
    assert _hit(
        "function dkey(dt){return dt;}\n"
        "// @view:insight\n"
        "function dkeyShim(){return dkey(0);}\n"
        "// @endview\n"
    ) == []

    # 绿 ③：形参同名遮蔽（ui.js:2055 `function imBtns(on){…b.disabled=on;}` 原形
    # ——design 块里有 `var on=…`，参数同名不是跨块调用）
    assert _hit(
        "// @view:design\n"
        "var on=function(id,fn){return id;};\n"
        "// @endview\n"
        "// @view:books\n"
        "function imBtns(on){return on;}\n"
        "// @endview\n"
    ) == []

    # 绿 ④：体内局部 var 同名遮蔽（rr 原形——design 块 `var rr=job.result` 遮蔽 insight 的 rr）
    assert _hit(
        "// @view:insight\n"
        "function rr(c){return c;}\n"
        "// @endview\n"
        "// @view:design\n"
        "function dvExportGo(job){var rr=job.result||{};return rr.ok;}\n"
        "// @endview\n"
    ) == []

    # 红 ④（2026-11-20 复核加固）：core 顶层函数读「仅视图块顶层声明」的变量。
    # 函数名归户只追函数名，VS 不在 homes 表 → 旧判据恒绿；但视图未注入时
    # coreFn() 运行时 ReferenceError（VS 全局不存在）。本判据必须报出来。
    _vs_src = (
        "// @view:a\n"
        "var VS=1;\n"
        "// @endview\n"
        "function coreFn(){return VS;}\n"
        "coreFn();\n"
    )
    assert _hit(_vs_src) == ["VS"], _vs_src
    assert det.strict_misplacements(_vs_src)[0]["kind"] == "var-ref"

    # 绿 ⑥：core 有自己的同名顶层声明 → 全局解析落到 core，非视图专有的变量
    assert _hit(
        "// @view:a\n"
        "var SHARED=1;\n"
        "// @endview\n"
        "var SHARED=0;\n"
        "function coreFn(){return SHARED;}\n"
    ) == []

    # 绿 ⑤：对象字面量键 / 属性访问不是引用
    assert _hit(
        "// @view:a\n"
        "function on(x){return x;}\n"
        "// @endview\n"
        "// @view:b\n"
        "function f(el){var o={on:1};return o.on+el.on;}\n"
        "// @endview\n"
    ) == []


# ── 检测器单测（脚本级；台账 A 类专项第一步的交付物）─────────────────
# 三道合成场景缝合成一个「真过一遍文件」的回路：把样本落到 tmp 目录再走
# strict_misplacements，避免只在字符串上打转——一旦实现改成读文件形态，
# 本组测试仍成立。合成样本同时覆盖 verdict（utils / view-owned）与错位判定，
# 因为「该不该搬、搬去哪」正是检测器给人看的结论。

def test_detector_synthetic_ok_utils_view_owned(tmp_path) -> None:
    """检测器三场景各判对：ok（同块自用）/ utils（无状态跨块）/ view-owned（读状态跨块）。"""
    det = _load_cross_view_detector()
    src = (
        "var state={q:1};\n"
        "var pureUtil=function(){}; // M3 占位：a 视图注入后由顶层同名函数覆盖\n"
        "var readsState=function(){}; // M3 占位：b 视图注入后由顶层同名函数覆盖\n"
        "// @view:a\n"
        "function pureUtil(x){return (x||0)+1;}\n"      # 无状态，被 core 调 → utils
        "function inAonly(){return pureUtil(2);}\n"    # 全在自家块 → ok
        "// @endview\n"
        "// @view:b\n"
        "function readsState(){return state.q;}\n"      # 读 state，被 core 调 → view-owned
        "// @endview\n"
        "var z=pureUtil(3);\n"
        "var w=readsState();\n"
    )
    probe = tmp_path / "ui.js"
    probe.write_text(src, encoding="utf-8")
    result = det.analyze(probe.read_text(encoding="utf-8"))
    got = {it["fn"]: it for it in result["items"]}

    # ok：调用点全部落在定义块内
    assert got["inAonly"]["verdict"] == "ok"
    assert got["inAonly"]["consumers"] == []
    # utils：无状态 + 跨块 → 应移回 core
    assert got["pureUtil"]["verdict"] == "utils"
    assert got["pureUtil"]["consumers"] == ["core"]
    assert got["pureUtil"]["hasPlaceholder"] is True
    # view-owned：读模块级状态 + 跨块 → 需改调用方或补 core 真实现
    assert got["readsState"]["verdict"] == "view-owned"
    assert "state" in got["readsState"]["stateReads"]
    assert got["readsState"]["consumers"] == ["core"]

    # 交叉验证：同一份样本过严格门口径，两条跨块项都要红（两套判据同一事实）
    assert sorted(it["fn"] for it in det.strict_misplacements(src)) == ["pureUtil", "readsState"]

    # 2026-11-20 复核加固：状态名自动发现（替代硬编码 STATE_VARS 九名）。
    # 新视图的顶层状态对象 NV 不在旧硬编码表里——读它的跨块函数仍须判 view-owned，
    # 否则 analyze 会误报 utils，其「移回 core」建议制造 core 读视图状态的 ReferenceError。
    src_new_state = (
        "var newUtil=function(){};\n"
        "// @view:newview\n"
        "var NV={x:1};\n"
        "function readsNV(){return NV.x+1;}\n"     # 读新视图状态，被 core 调 → view-owned
        "// @endview\n"
        "var r=readsNV();\n"
    )
    probe2 = tmp_path / "ui_new_state.js"
    probe2.write_text(src_new_state, encoding="utf-8")
    got2 = {it["fn"]: it for it in det.analyze(probe2.read_text(encoding="utf-8"))["items"]}
    assert got2["readsNV"]["verdict"] == "view-owned", got2["readsNV"]
    assert "NV" in got2["readsNV"]["stateReads"], got2["readsNV"]


def test_detector_selftest_cli_green() -> None:
    """检测器自带 --selftest 必须绿（CLI 审计与人读摘要的口径锚）。"""
    import subprocess
    import sys

    script = REPO_ROOT / "scripts" / "audit_cross_view_deps.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"--selftest 红:\n{proc.stdout}\n{proc.stderr}"


def test_detector_reports_current_tree_clean() -> None:
    """当前 ui.js 树必须零非 ok DepItem（A 类专项的验收条件：修完只剩 ok）。

    这条是**回归锚**：将来某个函数再被搬到别的视图块，检测器先红——比 V1 门
    更早给出「谁、从哪搬到哪」的可读信息（V1 是硬门，这里是审计面）。
    """
    det = _load_cross_view_detector()
    result = det.analyze(_UI_JS.read_text(encoding="utf-8"))
    offenders = [it for it in result["items"] if it["verdict"] != "ok"]
    assert not offenders, "检测器仍有跨视图 DepItem（台账 A 类未清）:\n" + "\n".join(
        f"  [{it['verdict']}] {it['fn']} {it['home']} → {','.join(it['consumers'])}"
        for it in offenders
    )
    # 视图块解析必须真读到东西（解析器失效同样要红，而不是「零发现=绿」）
    assert result["summary"]["views"], "视图块解析为空——@view 标记消失或解析器失效"
    assert result["summary"]["view_defined_functions"] > 0


# ── B 采集扩展（第二十一期 §三）：三条新架构规则 ──────────────────
# 依据=docs/项目方案/musespiral-B采集扩展设计-2026-11-16.md §三 依赖规则（按
# 实施布局适配：core 子包仅 core/web 新增；网络咽喉=infra/fetcher）。


def test_rule_b_core_web_no_io_libs() -> None:
    """规则 B1：core/web（URL 规范化）零网络/I/O 库（requests/urllib.request/
    socket/feedparser/readability）——纯字符串计算面（T67-6 面一）。"""
    violations = []
    web_dir = MUSESPIRAL_ROOT / "core" / "web"
    if not web_dir.is_dir():  # pragma: no cover - 目录随本批新增
        raise AssertionError("core/web 目录缺失（B 扩展 canonical 落点）")
    for path in web_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for bad in ("import requests", "import socket", "urllib.request", "import feedparser",
                    "from readability"):
            if bad in text:
                violations.append(f"core/web/{path.name}: {bad}")
    assert not violations, "core/web I/O 库违规:\n" + "\n".join(violations)


def test_rule_b_services_studio_no_direct_network() -> None:
    """规则 B2：一切新增出网必经 infra/fetcher——services/ 与 studio/ 零
    requests import / 零 requests. 调用点（现状全绿基线，url_fetch 在
    infra 属存量豁免；T67-6 面三静态扫描）。"""
    violations = []
    for sub in ("services", "studio"):
        for path in (MUSESPIRAL_ROOT / sub).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for bad in ("import requests", "from requests", "requests.get", "requests.post",
                        "requests.request"):
                if bad in text:
                    violations.append(f"{sub}/{path.relative_to(MUSESPIRAL_ROOT / sub)}: {bad}")
    assert not violations, "services/studio 直连出网违规（必经 infra/fetcher）:\n" + "\n".join(violations)


def test_rule_b_infra_fetcher_not_import_upward() -> None:
    """规则 B3：infra/fetcher 与 infra/sources 新增采集件不上探（规则 4 的
    具名回归锚：fetcher/rss/clipper/crawler 零 core/services/studio import
    ——canonical 归 core/web，由 services 层消费，infra 不得反向引）。"""
    violations = []
    for edge in scan_imports():
        if not edge.importer.startswith("musespiral.infra."):
            continue
        if edge.importer.split(".")[-1] in {"fetcher", "rss", "clipper", "crawler"} or edge.importer in (
            "musespiral.infra.fetcher", "musespiral.infra.sources.rss", "musespiral.infra.sources.clipper",
            "musespiral.infra.sources.crawler",
        ):
            if edge.target.startswith(("musespiral.core", "musespiral.services", "musespiral.studio")):
                violations.append(f"{edge.importer}:{edge.lineno} -> {edge.target}")
    assert not violations, "infra 采集件上探违规:\n" + "\n".join(violations)


# ── A 多模态素材（第二十一期 §五 横切面）：三条新架构规则 ──────────
# 依据=docs/项目方案/musespiral-A多模态素材设计-2026-11-16.md §三 依赖规则
# （core/materials 两新模块零 I/O；media_store 不 import 上层；TRIAGE
# 窄门四键形状锚——TASKS 白名单不变由既有方言守门断言覆盖）。


def test_rule_a_core_materials_new_modules_pure() -> None:
    """规则 A1：core/materials/tags.py 与 search.py 零 I/O——无 open 调用、
    无 requests/urllib.request import（纯函数，正典 §三；架构门规则 3 的
    本期具名加严锚）。"""
    for name in ("tags.py", "search.py"):
        path = MUSESPIRAL_ROOT / "core" / "materials" / name
        if not path.is_file():  # pragma: no cover - 文件随本批新增
            raise AssertionError(f"core/materials/{name} 缺失（A 多模态 canonical 落点）")
        text = path.read_text(encoding="utf-8")
        for bad in ("import requests", "urllib.request", "open(", "Path("):
            if bad in text:
                raise AssertionError(f"core/materials/{name}: 出现 I/O 形态 {bad!r}（应为纯函数）")


def test_rule_a_media_store_not_upward() -> None:
    """规则 A2：infra/media_store 不 import services/studio/core（规则 4 的
    具名回归锚——素材文件仓是 L1，Pillow 调用归此，不得反向引用上层）。"""
    violations = [
        f"{edge.importer}:{edge.lineno} -> {edge.target}"
        for edge in scan_imports()
        if edge.importer == "musespiral.infra.media_store"
        and edge.target.startswith(("musespiral.core", "musespiral.services", "musespiral.studio"))
    ]
    assert not violations, "media_store 上探违规:\n" + "\n".join(violations)


def test_rule_a_triage_four_keys_anchor() -> None:
    """规则 A3：TRIAGE_KEYS 形状锚（第二十一期 §1.2 修订——三布尔 + tags；
    N-4 扩 verified 人工核验布尔（confidence 是 AI 自评字段不入窄门）；
    tags/verified 之外的窄门不放宽由 triage 单测回归锚死）。"""
    from musespiral.core.materials.triage import TRIAGE_KEYS

    assert TRIAGE_KEYS == ("read", "starred", "enabled", "tags", "verified")


# ── Phase C 视觉创作线（F70-F74）：两条新架构门（设计 §三 依赖规则）────
# 依据=docs/项目方案/musespiral-C视觉创作设计-2026-11-16.md §三：
#   core/visual/canvas 禁 import 网络/文件模块（Pillow 白名单例外）；
#   /api/visual 族路由面契约锁 + confirm 语义（凡 POST 必须 confirm:true）。

# 网络 I/O 模块黑名单（AST 级 import 判定——文本匹配会被注释/字符串里的
# 同名字样误伤，且无法覆盖 `from x import y` 的模块名解析）
_CANVAS_FORBIDDEN_MODULES = frozenset({
    "requests", "urllib", "urllib.request", "http", "http.client", "httpx",
    "socket", "ssl", "subprocess", "ftplib", "smtplib", "telnetlib",
    "aiohttp", "websockets", "xmlrpc",
})
# 文件 I/O 判定：AST 层面命中下列调用/形态即算「读写了文件」。
# 字体加载（ImageFont.truetype(path)）与 PIL 图片加载属 Pillow 白名单例外
# （设计 §三「Pillow 允许」——layout.py 测量字体、pillow_render.py 加载图层
# 是 C 路兜底的必要面），但**不得静默放过**：例外清单是显式的、可核对的。
_CANVAS_FILE_IO_CALLS = frozenset({"open", "io.open", "os.open", "pathlib.Path.open"})
# 路径方法（须挂在路径对象上——`str.replace()` 不是文件 I/O，故此处只认
# 「接收者是路径型」的方法名；Path 与 str 的方法名重叠者另行处理）
_CANVAS_FILE_IO_PATH_METHODS = frozenset({
    "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "mkdir",
    "readdir", "iterdir", "glob", "rglob", "touch", "rename", "stat", "symlink_to",
})
# 第三方库代读盘的形态（Pillow 字体加载：`ImageFont.truetype(path, px)` 读
# 字体文件）。字面量匹配对此完全失效——原规则只找 `open(`/`Path(`，layout.py
# 经 ImageFont.truetype 读盘却零命中，「零文件 I/O」是假的。此处显式列出，
# 使 Pillow 白名单例外成为**可核对的清单**而非「没被搜到就算干净」。
_CANVAS_FILE_IO_VIA_LIB = frozenset({"truetype", "load_default", "fromstring"})
# 免检文件：Pillow 白名单例外（字体测量 + 图片图层加载）
_CANVAS_FILE_IO_EXEMPT = {"pillow_render.py", "layout.py"}


def _canvas_file_io_violations(path: Path) -> list[str]:
    """单个 canvas 模块的文件 I/O 违规清单（AST 判定，非文本匹配）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        # open(...) / Path.open(...) / os.open(...)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _CANVAS_FILE_IO_CALLS:
                out.append(f"{path.name}:{node.lineno} 文件 I/O 调用 {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in _CANVAS_FILE_IO_CALLS:
                out.append(f"{path.name}:{node.lineno} 文件 I/O 调用 .{func.attr}()")
        # path.read_text() / path.write_bytes() / path.unlink() …
        if isinstance(node, ast.Attribute) and node.attr in _CANVAS_FILE_IO_PATH_METHODS:
            out.append(f"{path.name}:{node.lineno} 文件 I/O 方法 .{node.attr}()")
        # Path("...") 构造（路径对象即文件系统面）
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "Path":
                out.append(f"{path.name}:{node.lineno} 文件系统面 Path()")
            elif isinstance(func, ast.Attribute) and func.attr == "Path":
                out.append(f"{path.name}:{node.lineno} 文件系统面 pathlib.Path()")
            # 第三方库代读盘（ImageFont.truetype(path, px) 读字体文件）
            elif isinstance(func, ast.Attribute) and func.attr in _CANVAS_FILE_IO_VIA_LIB:
                out.append(
                    f"{path.name}:{node.lineno} 第三方库代读盘 .{func.attr}()"
                )
    return out


def test_rule_c_canvas_pure_layer_no_io() -> None:
    """规则 C1：core/visual/canvas 六模块零网络 I/O；文件 I/O 仅 Pillow 白名单例外。

    纯度纪律（设计 §三）：禁网络模块 import 于全部六模块（AST 判定，模块名
    精确匹配）；文件 I/O 判定升级为 **AST 级**（调用/属性形态），不再依赖
    `open(`/`Path(` 的字面量匹配——字面量匹配对 `ImageFont.truetype(path, px)`
    这类**经第三方 API 读盘**的形态完全失效（layout.py 即此形态），也不覆盖
    `path.read_text()` / `path.unlink()` 等调用面。

    Pillow 白名单例外是**显式清单**（layout.py 字体测量 + pillow_render.py
    图层加载），非「文本里没出现 open( 就算干净」。
    """
    canvas_dir = MUSESPIRAL_ROOT / "core" / "visual" / "canvas"
    assert canvas_dir.is_dir(), "core/visual/canvas 目录缺失（Phase C 视觉纯层落点）"
    violations: list[str] = []
    for path in sorted(canvas_dir.glob("*.py")):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for mod in mods:
                root = mod.split(".")[0]
                if mod in _CANVAS_FORBIDDEN_MODULES or root in _CANVAS_FORBIDDEN_MODULES:
                    violations.append(
                        f"core/visual/canvas/{path.name}:{node.lineno} 网络 I/O 模块 {mod!r}"
                    )
        if path.name in _CANVAS_FILE_IO_EXEMPT:
            continue
        violations.extend(
            f"core/visual/canvas/{v}" for v in _canvas_file_io_violations(path)
        )
    assert not violations, "core/visual/canvas 纯度违规（零 I/O 纯层）:\n" + "\n".join(violations)


def test_rule_c_visual_api_family_confirm_and_surface() -> None:
    """规则 C2：/api/visual 族路由面契约锁 + confirm 闸门**行为**验证。

    ① 路由面：POST 四条（render/save/export/preset）在 _ACTION_ROUTES、GET 三条
    （design/templates/presets）在 _GET_ROUTES（增删即红——契约同步义务机器兜底）；
    ② confirm 语义：四条都不走二进制旁路（旁路只有 upload 一条），且
    **起真回环服务器逐条打**——缺 confirm / 假值 confirm 必须 400。
       上一版只断言「不在 _BINARY_POST_ROUTES」，那不证明 confirm 闸门真的
       对这四条生效（闸门在 server 分派层，不在路由表层）。
    """
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES
    from musespiral.studio.server import _BINARY_POST_ROUTES

    expected_post = {
        "/api/visual/render", "/api/visual/save", "/api/visual/export", "/api/visual/preset",
    }
    expected_get = {"/api/visual/design", "/api/visual/templates", "/api/visual/presets"}
    actual_visual_post = {p for p in _ACTION_ROUTES if p.startswith("/api/visual/")}
    actual_visual_get = {p for p in _GET_ROUTES if p.startswith("/api/visual/")}
    assert actual_visual_post == expected_post, (
        f"/api/visual POST 面漂移 缺失={sorted(expected_post - actual_visual_post)} "
        f"多余={sorted(actual_visual_post - expected_post)}"
    )
    assert actual_visual_get == expected_get, (
        f"/api/visual GET 面漂移 缺失={sorted(expected_get - actual_visual_get)} "
        f"多余={sorted(actual_visual_get - expected_get)}"
    )
    # confirm 闸门：四条 POST 都不得走二进制旁路（旁路只有 upload 一条）
    bypass = {p for p in _BINARY_POST_ROUTES if p.startswith("/api/visual/")}
    assert not bypass, f"/api/visual 族不得有二进制旁路（confirm 必须走 JSON body 全局闸门）: {sorted(bypass)}"

    # ── confirm 闸门**行为**验证（真回环服务器逐条打）──
    import http.client
    import json
    import threading
    from pathlib import Path as _Path

    from musespiral import studio

    tmp_root = _Path(tempfile.mkdtemp())
    (tmp_root / "demo.md").write_text(
        "---\ntitle: T\n---\n\n首段。\n", encoding="utf-8"
    )
    httpd = studio.make_server(tmp_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        for path in sorted(expected_post):
            for bad_body in ({}, {"confirm": False}, {"confirm": "true"}):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                conn.request(
                    "POST", path,
                    body=json.dumps(bad_body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                payload = json.loads(resp.read() or b"{}")
                conn.close()
                assert resp.status == 400, (
                    f"{path} 缺 confirm 必须 400（实测 {resp.status}）"
                )
                assert "confirm" in str(payload.get("error", "")), (
                    f"{path} 400 文案须含 confirm（实测 {payload}）"
                )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


# ── Phase D 智能体层（F75-F79 设计 §三 依赖规则）：四条新架构门 ──────────
# 依据=docs/项目方案/musespiral-D智能体设计-2026-11-16.md §三「依赖规则（架构门
# 可机器校验，追加四条）」。四条均为具名回归锚：README/docstring 的纪律若被
# 后续改动绕开，这里立即红。

_FORBIDDEN_DOMAIN_ROOTS = frozenset(m.split(".")[0] for m in FORBIDDEN_IN_DOMAIN)


def resolve_imports(path: Path, pkg_root: Path = MUSESPIRAL_ROOT) -> list[tuple[str, int]]:
    """单文件全部 import 解析为 (绝对包名, 行号)——相对导入补回 `musespiral.` 前缀。

    为什么需要这个独立助手：相对导入的 **level 语义是「从当前模块所在包
    往上层数」**。`musespiral/core/agent/x.py` 的包内点路径是 `core.agent.x`，
    故 `from ...infra import y`（level=3）→ base 空 → `musespiral.infra.y`。
    之前的门（与既有 `_resolve_relative`）都漏了 `musespiral.` 前缀，导致
    「`from ...infra` 是否上探」判定形同虚设（red 用例实测未拦截）。
    """
    rel = path.relative_to(pkg_root)
    file_parts = list(rel.with_suffix("").parts)
    out: list[tuple[str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod_parts = node.module.split(".") if node.module else []
            if node.level > 0:
                base = file_parts[: len(file_parts) - node.level]
                dotted = ".".join(["musespiral"] + base + mod_parts)
            else:
                dotted = ".".join(mod_parts)
            out.append((dotted, node.lineno))
            # `from . import x` / `from X import a, b`：**被导入的名字本身也是
            # 模块路径的延伸**（`from . import infra` 就是把同包的 infra 拉进来）。
            # 漏掉这条会让「同包上探」整类写法静默放行（red 对拍实测）。
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = dotted + "." + alias.name if dotted else alias.name
                out.append((full, node.lineno))
    return out


def test_rule_d1_core_agent_pure_no_infra_or_io() -> None:
    """规则 D1：core/agent/** 零网络/I/O 库、零 infra/services import。

    AST 判定（不用文本匹配——注释/docstring 里的同名字样不误伤）：
      ① 禁 requests / urllib / urllib.request / sqlite3 / subprocess 等模块；
      ② 禁任何 `musespiral.infra.*` / `musespiral.services.*` import（含 `from ...infra`
         这类相对导入——`resolve_imports` 已补前缀，函数体内懒加载同样命中）。

    为什么严格到「懒加载也算」：`plan`/`interp` 是 L2 纯函数，TASKS 白名单
    与 chat_fn 都是**注入**进来的（设计 §三-D1 依赖注入）；一旦某个 core/agent
    模块 import infra，它就获得了「自己决定策略/自己触网」的能力，D1 的
    「同 plan+同 ctx+同 chat_fn ⇒ 同结果」立刻不再成立。
    """
    agent_dir = MUSESPIRAL_ROOT / "core" / "agent"
    assert agent_dir.is_dir(), "core/agent 目录缺失（Phase D 引擎落点）"
    violations: list[str] = []
    for path in sorted(agent_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(MUSESPIRAL_ROOT)
        for mod, lineno in resolve_imports(path):
            root = mod.split(".")[0]
            if mod in FORBIDDEN_IN_DOMAIN or root in _FORBIDDEN_DOMAIN_ROOTS:
                violations.append(f"{rel}:{lineno} I/O 库 {mod!r}")
            if mod.startswith(("musespiral.infra", "musespiral.services", "musespiral.studio")):
                violations.append(f"{rel}:{lineno} 上探 {mod!r}")
    assert not violations, "core/agent 纯净性违规（D1 纯函数 + 依赖注入）:\n" + "\n".join(violations)


def test_rule_d2_gateway_imports_same_layer_only() -> None:
    """规则 D2：infra/ai/gateway.py 只 import 同层（+ config）——不 import 上层。

    比通用规则 4 更进一步：gateway 是「chat() 之上唯一组合层」，它若知道
    业务概念（core 的 materials、services 的会话）就会长出第二套编排逻辑。
    """
    gateway = MUSESPIRAL_ROOT / "infra" / "ai" / "gateway.py"
    assert gateway.is_file(), "infra/ai/gateway.py 缺失（F79 组合层落点）"
    allowed = (
        "musespiral.infra.ai",        # 同层：client/profiles/prompts/cache/usage/budget
        "musespiral.infra.storage",   # 同层：atomic 写共用件
        "musespiral.config",          # 全局常量（规则 4 明文允许）
    )
    violations: list[str] = []
    for mod, lineno in resolve_imports(gateway):
        if not mod.startswith("musespiral"):
            continue  # 标准库/第三方
        if any(mod == a or mod.startswith(a + ".") for a in allowed):
            continue
        violations.append(f"gateway.py:{lineno} -> {mod}")
    assert not violations, "gateway 层外 import 违规（只许同层 + config）:\n" + "\n".join(violations)


# chat() 自身允许的网络实现点（唯一豁免：它是唯一 LLM HTTP 出口）
_URLLIB_ALLOWED_FILES = {"client.py"}


def test_rule_d3_agent_and_gateway_no_urllib_bypass() -> None:
    """规则 D3：agent 域与 gateway 无 urllib/requests 旁路——LLM 出口仍唯 chat()。

    全仓 LLM HTTP 出口必须唯一（chat() 内部经 urllib 是**存量豁免**，规则 3
    的既有形状）；本期新增的两个域一个都不得自己起网络通道，否则「查缓存→
    预算→调用→记账→写缓存」的五段序就有绕过点。`client.py` 是唯一豁免文件
    （`urllib.parse`/`urllib.error` 在该文件属纯字符串解析与原错误包装）。
    """
    violations: list[str] = []
    for target in (MUSESPIRAL_ROOT / "core" / "agent", MUSESPIRAL_ROOT / "infra" / "ai"):
        for path in sorted(target.rglob("*.py")):
            if "__pycache__" in path.parts or path.name in _URLLIB_ALLOWED_FILES:
                continue
            rel = path.relative_to(MUSESPIRAL_ROOT)
            for mod, lineno in resolve_imports(path):
                root = mod.split(".")[0]
                if root in ("urllib", "requests", "http", "httpx", "aiohttp", "websockets"):
                    violations.append(f"{rel}:{lineno} 网络旁路 {mod!r}")
    assert not violations, "agent/gateway 域网络旁路违规（LLM 出口须唯 chat）:\n" + "\n".join(violations)


def test_rule_d4_new_agent_templates_registered_in_dialect_gate() -> None:
    """规则 D4：新任务模板的红绿对拍（方言守门真覆盖 core/agent）。

    红：往临时 core/agent 树写一个带禁用字样的模板 → `scan_prompt_dialect`
    必须报出该文件（证明登记非空转）；
    绿：真实 core/agent 树零违规（证明现网模板干净）。
    同时断言全部 agent 任务键已在 TASKS（否则「新模板自动纳管」的前提不成立）
    ——R3.5 批（2026-11-18）扩 chapter_digest/book_digest 两键（模板=提炼卡
    四节/全书综述，落在 plans_builtin）。
    """
    import pathlib as _pathlib

    from musespiral.infra.ai.profiles import TASKS

    for task in (
        "agent_extract", "agent_synthesize", "tag_suggest",
        "chapter_digest", "book_digest",
    ):
        assert task in TASKS, f"TASKS 白名单缺新任务 {task}（prompt_tasks 派生失效）"

    # 绿：真实树干净
    real = scan_prompt_dialect(MUSESPIRAL_ROOT)
    assert not real, "core/agent 真实树方言违规:\n" + "\n".join(real)

    # 红：喂违规样本走同一扫描函数（同一代码路径，非另写判定）
    tmp = _pathlib.Path(tempfile.mkdtemp())
    (tmp / "core" / "agent").mkdir(parents=True)
    (tmp / "core" / "agent" / "plans_builtin.py").write_text(
        '_PROMPT = "请模仿 gpt 的口吻"\n', encoding="utf-8"
    )
    red = scan_prompt_dialect(tmp)
    assert any("core/agent/plans_builtin.py" in v and "gpt" in v for v in red), red


def test_rule_d1_gate_actually_catches_violations(tmp_path) -> None:
    """D1 守门红绿对拍：喂违规样本必须真拦截（防守门放空枪）。

    与 `test_rule_d4` 的「红」同一纪律。**关键在于走同一解析助手**
    （`resolve_imports`）——这正是本对拍抓出的真实缺陷：首版 D1/D2/D3 各自
    内联解析相对导入且漏了 `musespiral.` 前缀，导致 `from ...infra import x` 这种
    最典型的上探写法**零命中**（对拍一跑就露）。故本用例直接调用生产助手，
    不做第二份逻辑。
    """
    import pathlib as _pl

    pkg = _pl.Path(tmp_path) / "musespiral"
    (pkg / "core" / "agent").mkdir(parents=True)

    def _scan(pkg_root):
        out: list[str] = []
        for path in sorted((pkg_root / "core" / "agent").rglob("*.py")):
            rel = path.relative_to(pkg_root)
            for mod, lineno in resolve_imports(path, pkg_root):
                root = mod.split(".")[0]
                if mod in FORBIDDEN_IN_DOMAIN or root in _FORBIDDEN_DOMAIN_ROOTS:
                    out.append(f"{rel}:{lineno} IO {mod}")
                if mod.startswith(("musespiral.infra", "musespiral.services", "musespiral.studio")):
                    out.append(f"{rel}:{lineno} UP {mod}")
        return out

    # ① 相对导入上探 infra（level=3：core/agent/x.py → musespiral.infra）
    (pkg / "core" / "agent" / "up.py").write_text(
        "from ...infra.ai import client\n", encoding="utf-8"
    )
    v = _scan(pkg)
    assert any("UP" in x and "musespiral.infra" in x for x in v), f"相对上探 infra 未被拦截: {v}"

    # ② 绝对导入上探 services
    (pkg / "core" / "agent" / "up.py").write_text(
        "import musespiral.services.agent_service\n", encoding="utf-8"
    )
    v2 = _scan(pkg)
    assert any("UP" in x and "services" in x for x in v2), f"绝对上探 services 未被拦截: {v2}"

    # ③ 网络库（含 urllib.request 与 requests 两种写法）
    (pkg / "core" / "agent" / "up.py").write_text("import urllib.request\n", encoding="utf-8")
    v3 = _scan(pkg)
    assert any("IO" in x and "urllib" in x for x in v3), f"urllib 未被拦截: {v3}"
    (pkg / "core" / "agent" / "up.py").write_text("import requests\n", encoding="utf-8")
    v4 = _scan(pkg)
    assert any("IO" in x and "requests" in x for x in v4), f"requests 未被拦截: {v4}"

    # ④ 干净树零违规（不误伤：同层相对导入 + 标准库）
    (pkg / "core" / "agent" / "up.py").unlink()
    (pkg / "core" / "agent" / "ok.py").write_text(
        "from . import budgets\nimport json\nfrom typing import Any\n", encoding="utf-8"
    )
    assert _scan(pkg) == []


def test_resolve_imports_regression_keeps_mdpub_prefix() -> None:
    """`resolve_imports` 本身的回归锚：相对导入必须解析出完整 `musespiral.` 路径。

    这是 D1/D2/D3 三门共用的判定内核——它错，三门同时失效（且都是静默失效：
    「零违规」看起来像绿灯）。用真实文件而非人造树，锁住三个典型 level。
    """
    import pathlib as _pl

    pkg = _pl.Path(tempfile.mkdtemp()) / "musespiral"
    (pkg / "core" / "agent").mkdir(parents=True)

    # level=1 → 同包；level=2 → musespiral.core；level=3 → musespiral
    (pkg / "core" / "agent" / "x.py").write_text(
        "from . import budgets\n"
        "from .. import something\n"
        "from ...infra.ai import client\n"
        "import musespiral.services.nope\n",
        encoding="utf-8",
    )
    resolved = dict((line, mod) for mod, line in resolve_imports(pkg / "core" / "agent" / "x.py", pkg))
    assert resolved[1] == "musespiral.core.agent.budgets"
    assert resolved[2] == "musespiral.core.something"
    assert resolved[3] == "musespiral.infra.ai.client"
    assert resolved[4] == "musespiral.services.nope"


def test_rule_d2_and_d3_red_green() -> None:
    """D2/D3 共享 `resolve_imports` 内核 → 用真实违规文件证明两者都能拦。

    与 D1 对拍同一纪律：门禁若「零违规」是解析漏判的结果，最危险的失效形态
    就是**绿灯假安全**。此处对 gateway 复制品与 agent 复制品注入违规，逐门验。
    """
    import pathlib as _pl
    import shutil

    tmp = _pl.Path(tempfile.mkdtemp())
    src_pkg = MUSESPIRAL_ROOT
    shutil.copytree(src_pkg / "infra" / "ai", tmp / "musespiral" / "infra" / "ai")
    shutil.copytree(src_pkg / "core" / "agent", tmp / "musespiral" / "core" / "agent")
    (tmp / "musespiral" / "config.py").write_text("", encoding="utf-8")

    # ── D2：给 gateway 加一条上探 core 的 import → 必须被发现 ──
    gw = tmp / "musespiral" / "infra" / "ai" / "gateway.py"
    gw.write_text(
        gw.read_text(encoding="utf-8") + "\nfrom ...core.materials import model\n",
        encoding="utf-8",
    )
    allowed = ("musespiral.infra.ai", "musespiral.infra.storage", "musespiral.config")
    up_edges = [
        mod for mod, _l in resolve_imports(gw, tmp / "musespiral")
        if mod.startswith("musespiral") and not any(mod == a or mod.startswith(a + ".") for a in allowed)
    ]
    assert "musespiral.core.materials.model" in up_edges, f"D2 未捕获上探: {up_edges}"

    # ── D3：给 agent 模块加 urllib 旁路 → 必须被发现 ──
    probe = tmp / "musespiral" / "core" / "agent" / "probe.py"
    probe.write_text("import urllib.request\n", encoding="utf-8")
    net_edges = [
        mod for mod, _l in resolve_imports(probe, tmp / "musespiral")
        if mod.split(".")[0] in ("urllib", "requests", "http", "httpx", "aiohttp", "websockets")
    ]
    assert net_edges == ["urllib.request"], f"D3 未捕获旁路: {net_edges}"


# ── Phase D 复核修复批：D5 写面白名单门（设计 §二-F75.5 的机器化）────────
# 设计原文「架构测试断言 services/agent_service 无 add_material/写 draft 调用
# ——写面仅采纳分发函数」此前只有人工读码，本门补上：素材/会话两写面 API 只许
# 出现在 adopt_* 函数体内；run 执行路径（_execute/job_fn/create_run/…）出现
# 即红。判定用 AST（注释/字符串里的同名样不误伤）。


def _agent_write_surface_violations(source: str) -> list[str]:
    """agent_service 源码 → 写面白名单违规清单（供真文件门与红绿自证共用）。

    白名单：`add_material` / `new_session` 调用与 `DRAFT_FILE` 引用只许出现在
    名以 `adopt` 开头的函数体内；模块层/其余函数出现即违规。
    """
    import ast as _ast

    tree = _ast.parse(source)
    parent: dict[int, _ast.AST] = {}
    for node in _ast.walk(tree):
        for child in _ast.iter_child_nodes(node):
            parent[id(child)] = node

    def _enclosing_func(node: _ast.AST) -> str:
        cur = node
        while id(cur) in parent:
            cur = parent[id(cur)]
            if isinstance(cur, _ast.FunctionDef):
                return cur.name
        return "<module>"

    _WRITE_CALLS = frozenset({"add_material", "new_session"})
    _WRITE_REFS = frozenset({"DRAFT_FILE"})
    violations: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name = None
            if isinstance(node.func, _ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, _ast.Name):
                name = node.func.id
            if name in _WRITE_CALLS:
                fn = _enclosing_func(node)
                if not fn.startswith("adopt"):
                    violations.append(
                        f"第 {node.lineno} 行: {name}() 出现在 {fn}（写面仅 adopt_* 允许）"
                    )
        elif isinstance(node, _ast.Attribute):
            if node.attr in _WRITE_REFS:
                fn = _enclosing_func(node)
                if not fn.startswith("adopt"):
                    violations.append(
                        f"第 {node.lineno} 行: {node.attr} 引用出现在 {fn}（仅 adopt_* 允许）"
                    )
    return violations


def test_rule_d5_agent_service_write_surface_only_in_adopt() -> None:
    """规则 D5：run 永不直写 materials/正稿——写面 API 只在 adopt_* 体内。"""
    src = (MUSESPIRAL_ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")
    violations = _agent_write_surface_violations(src)
    assert not violations, "agent_service 写面越界:\n" + "\n".join(violations)


def test_rule_d5_gate_catches_violations() -> None:
    """D5 红绿自证：同一判定函数对违规样本必须报、对合规样本必须静默。"""
    bad = (
        "def _execute(run_id):\n"
        "    materials_service.add_material(row)\n"
        "    compose_service.new_session(slug, title)\n"
        "    d = compose_service.DRAFT_FILE\n"
    )
    violations = _agent_write_surface_violations(bad)
    assert len(violations) == 3, f"D5 未捕获写面越界: {violations}"
    ok = (
        "def adopt_material(run_id):\n"
        "    materials_service.add_material(row)\n"
        "\n"
        "def adopt_compose(run_id):\n"
        "    compose_service.new_session(slug, title)\n"
        "    atomic_write_bytes(directory / compose_service.DRAFT_FILE, b'x')\n"
    )
    assert _agent_write_surface_violations(ok) == []


# ── 插件系统 PL-1（2026-11-20）：P1 契约层纯净门 ──────────────────────
# core/plugins 是 L2 纯函数层（types/manifest 零 I/O；「目录即插件」的 I/O
# 半边归 infra/plugins_loader，由 services 编排两侧）。通用规则 1 只拦
# 「向上」（domain→services），而 domain→infra 是**向下**放行——不立专属
# 门的话，core/plugins import infra 拿走文件 I/O 能力属静默合规。与 D1
# 同纪律：AST 判定（注释/docstring 同名样不误伤）+ 红绿自证。

_PLUGIN_FORBIDDEN_UP = ("musespiral.infra", "musespiral.services", "musespiral.studio")


def _core_plugins_violations(pkg_root: Path) -> list[str]:
    """core/plugins 树 → 纯净性违规清单（真文件门与红绿自证共用同一判定）。

    两条：① I/O 库（同规则 3 的 FORBIDDEN_IN_DOMAIN）；② infra/services/
    studio 上探——②对 services/studio 是规则 1 的冗余确认，对 infra 是
    **本门独有**（domain→infra 向下放行，通用门不看守）。
    """
    violations: list[str] = []
    for path in sorted((pkg_root / "core" / "plugins").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(pkg_root)
        for mod, lineno in resolve_imports(path, pkg_root):
            root = mod.split(".")[0]
            if mod in FORBIDDEN_IN_DOMAIN or root in _FORBIDDEN_DOMAIN_ROOTS:
                violations.append(f"{rel}:{lineno} IO {mod}")
            if mod.startswith(_PLUGIN_FORBIDDEN_UP):
                violations.append(f"{rel}:{lineno} UP {mod}")
    return violations


def test_rule_p1_core_plugins_pure_no_infra_or_io() -> None:
    """规则 P1：core/plugins/** 零 I/O 库、零 infra/services/studio import。

    为什么严于通用规则 1：契约层一旦 import infra（哪怕只取一个路径工具），
    parse_manifest 的「红绿双出口、永不抛、可完全单测」就失去前提——I/O
    失败会以异常形态漏进契约判定（manifest.parse_manifest docstring 的
    纪律声明）。entry 存在性核验必须留在 services 编排层调用 infra 完成。
    """
    plugins_dir = MUSESPIRAL_ROOT / "core" / "plugins"
    assert plugins_dir.is_dir(), "core/plugins 目录缺失（PL-1 契约层落点）"
    violations = _core_plugins_violations(MUSESPIRAL_ROOT)
    assert not violations, "core/plugins 纯净性违规（P1：契约层零 I/O + 不碰 infra/services）:\n" + "\n".join(violations)


def test_rule_p1_gate_red_green(tmp_path) -> None:
    """P1 红绿自证：违规样本必须真拦截（防守门放空枪）。

    与 D1 对拍同纪律，走同一判定助手。重点红例是 ①`from ...infra import`
    ——通用规则 1 对 domain→infra **放行**，只有本门能拦，这正是立门的
    理由；④绿例确认同层相对导入与标准库不误伤。
    """
    import pathlib as _pl

    pkg = _pl.Path(tmp_path) / "musespiral"
    (pkg / "core" / "plugins").mkdir(parents=True)
    probe = pkg / "core" / "plugins" / "probe.py"

    # ① 相对导入碰 infra（level=3：core/plugins/x.py → musespiral.infra）——通用门放行、本门必拦
    probe.write_text("from ...infra import plugins_loader\n", encoding="utf-8")
    v = _core_plugins_violations(pkg)
    assert any("UP" in x and "musespiral.infra" in x for x in v), f"相对导入 infra 未被拦截: {v}"

    # ② 绝对导入上探 services
    probe.write_text("import musespiral.services.plugins_service\n", encoding="utf-8")
    v2 = _core_plugins_violations(pkg)
    assert any("UP" in x and "services" in x for x in v2), f"绝对上探 services 未被拦截: {v2}"

    # ③ I/O 库（subprocess——若契约层想自己跑插件就先红在这里）
    probe.write_text("import subprocess\n", encoding="utf-8")
    v3 = _core_plugins_violations(pkg)
    assert any("IO" in x and "subprocess" in x for x in v3), f"subprocess 未被拦截: {v3}"

    # ④ 干净树零违规（不误伤：同包相对导入 + 标准库 + 第三方 pydantic）
    probe.write_text(
        "from . import types\n"
        "from .types import types_compatible\n"
        "import json, re\n"
        "from pydantic import BaseModel\n",
        encoding="utf-8",
    )
    assert _core_plugins_violations(pkg) == []
