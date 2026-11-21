"""musespiral 测试全局配置（R9 测试体系升级，正典 §四-R9）。

- pytest_configure 注册 e2e / perf marker（未注册的 marker 在 --strict-markers
  语义下告警，且 `-m e2e` / `-m "not perf"` 等筛选依赖注册名）；
- e2e 收集期检测 playwright 可导入性：不可导入时整个 tests/e2e/ 目录
  collect_ignore——本仓 venv 默认不带 playwright（optional-dependencies
  的 browser 组），没有它 e2e 用例既无法跑也无法诚实收集，收集期静默排除
  保证全量回归「collection 干净」；用例文件内另有 pytest.importorskip
  兜底（显式指定 e2e 文件路径时以 skip 呈现而非 error）。

F45 方言守门（第十七期）不在本文件：conftest 内的测试函数不进 pytest
默认目录扫描，守门与自证单测落在 tests/test_architecture.py（被收集）
与 tests/test_dialect_gate.py。
"""
from __future__ import annotations

import pytest


# ── R1 前端构建（会话级，先构建再断言）──


@pytest.fixture(scope="session", autouse=True)
def _build_studio_ui() -> None:
    """R1（设置与创作台设计 §三）：ui.html 改由三源文件合成——

    musespiral/studio/{ui.tmpl.html, ui.css, ui.js} --scripts/build_ui.py--> ui.html。
    每轮 pytest 会话先跑一次构建，保证被测对象（test_studio_ui.py 的结构断言、
    test_studio.py 的 GET / 服务）读到的是三源最新合成产物；构建确定性与
    体积门由 tests/test_build_ui.py 专测。
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "build_ui.py"
    spec = importlib.util.spec_from_file_location("musespiral_build_ui", script)
    assert spec is not None and spec.loader is not None, f"加载失败: {script}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: 需 playwright 真实浏览器的端到端用例（默认跳过——装 playwright 才启用；"
        "运行：pytest tests/e2e/ -m e2e）",
    )
    config.addinivalue_line(
        "markers",
        "perf: 性能基线用例（默认随全量跑；阈值宽松防 CI 抖动，超限才红）",
    )
    config.addinivalue_line(
        "markers",
        "browser: 需真实浏览器（playwright）的用例（缺浏览器 skip 非 fail——"
        "Phase C 视觉线 §5.4 套件规则，如 F70-T6 B vs C 对拍）",
    )


collect_ignore: list[str] = []
try:  # e2e 前置：playwright 可导入才收集 tests/e2e/
    import playwright  # noqa: F401
except ImportError:
    collect_ignore.append("e2e")


# ── 架构分层检查（正典 §二 规则 1-4，自动化防回归）──


def test_architecture_domain_no_external_io():
    """规则 3：Domain 层（core/ + platforms/）不得 import requests/sqlite3/subprocess。

    urllib.parse 白名单：仅做 URL 字符串解析（urlparse），无网络 I/O。
    """
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "musespiral"
    violations = []
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        rel = f.relative_to(root)
        parts = rel.parts
        # 只检查 Domain 层（core/ + platforms/，排除已迁到 infra 的子目录）
        is_domain = parts[0] in ("core", "platforms")
        if not is_domain:
            continue
        text = f.read_text(encoding="utf-8")
        for bad in ("import requests", "import sqlite3", "import subprocess",
                     "from requests", "from sqlite3", "from subprocess"):
            if bad in text:
                violations.append(f"{rel}: {bad}")
    assert not violations, f"Domain 层 I/O 违规:\n" + "\n".join(violations)


def test_architecture_infra_no_upper_import():
    """规则 4：Infrastructure 层不得 import 上层模块（commands/studio/services）。"""
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "musespiral"
    violations = []
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        rel = f.relative_to(root)
        parts = rel.parts
        is_infra = parts[0] in ("infra", "jobs", "browser")
        if not is_infra:
            continue
        text = f.read_text(encoding="utf-8")
        for bad in ("from ..commands", "from ..studio", "from ..services",
                     "from ...commands", "from ...studio", "from ...services"):
            if bad in text:
                violations.append(f"{rel}: {bad}")
    assert not violations, f"Infra 层反向依赖违规:\n" + "\n".join(violations)


def test_architecture_services_no_studio():
    """规则：services/ 不得 import studio/。"""
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "musespiral" / "services"
    for f in root.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        assert "from ..studio" not in text and "from .studio" not in text, \
            f"services/{f.name} imports studio (违规)"
