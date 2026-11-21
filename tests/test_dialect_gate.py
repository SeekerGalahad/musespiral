"""F45 方言守门自证——守门断言必须真拦截（喂违规样本走同一扫描代码路径）。

test_architecture.scan_prompt_dialect / dialect_violations 即架构门
test_architecture_prompt_dialect_free 的全部判定逻辑；本文件用内嵌违规
字样的临时文件证明它非空判（守住「守门形同虚设」回归）。
"""
from __future__ import annotations

from test_architecture import DIALECT_FORBIDDEN, dialect_violations, scan_prompt_dialect


def _pkg_tree(tmp_path, files: dict[str, str]):
    """在 tmp_path/musespiral/ 下造一棵最小包树（路径→内容）。"""
    pkg = tmp_path / "musespiral"
    for rel, text in files.items():
        p = pkg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return pkg


# ── 单元级：判定函数对违规样本非空判 ────────────────────────


def test_predicate_flags_each_forbidden_token():
    """六个禁用字样逐一命中（大写/混合大小写同样拦——大小写不敏感）。"""
    for i, tok in enumerate(DIALECT_FORBIDDEN):
        v = dialect_violations(f"fake/{i}.py", f'PROMPT = "你是 {tok.upper()}"\n')
        assert any(tok in x for x in v), f"{tok} 未被拦截"


def test_predicate_clean_text_passes():
    assert dialect_violations("ok.py", '_PROMPT = "你是公众号资深编辑。"\n') == []


# ── 全量级：scan_prompt_dialect 对临时违规文件真拦截 ──────────


def test_scan_catches_violating_prompt_file(tmp_path):
    """违规样本进扫描树（core/quality/）→ 同一扫描函数必须抓到并报出路径。"""
    pkg = _pkg_tree(tmp_path, {"core/quality/title.py": '_PROMPT = "请模仿 qwen 的口吻"\n'})
    v = scan_prompt_dialect(pkg)
    assert any("qwen" in x and "core/quality/title.py" in x for x in v), v


def test_scan_catches_violation_in_every_registered_point(tmp_path):
    """每个登记的组装点（目录递归 + 点名文件 + 新增文件）都被覆盖。"""
    files = {
        "core/adaptation/generator.py": "# 由 gpt 生成\n",
        "core/quality/digest.py": "x = 'claude'\n",
        "infra/ai/client.py": "docstring mentions gemini\n",
        "core/discovery/suggester.py": "s = 'glm-4'\n",
        "services/rewrite_service.py": "t = 'deepseek-chat'\n",
        "services/annotate_service.py": "u = 'Qwen3'\n",
        "infra/ai/style_inject.py": "v = 'gpt-4o'\n",  # 未来新增文件（Group A 落地后）同样在扫描面
    }
    pkg = _pkg_tree(tmp_path, files)
    v = scan_prompt_dialect(pkg)
    for rel in files:
        assert any(rel in x for x in v), f"{rel} 未被扫描覆盖: {v}"


def test_scan_clean_tree_passes(tmp_path):
    """干净树零违规（对照组：守门不放空枪也不误伤）。"""
    pkg = _pkg_tree(tmp_path, {
        "core/adaptation/generator.py": "PROMPT = '改写'\n",
        "infra/ai/check.py": "CHECK_PROMPT = '回复：OK'\n",
    })
    assert scan_prompt_dialect(pkg) == []


# ── F47 守门扩面：prompts.d 覆盖文件（隔离 MUSESPIRAL_HOME 自证）──


def test_runtime_save_override_rejects_dialect(tmp_path, monkeypatch):
    """运行时保存校验真拦截：save_override 对违规内容拒写（API 面的第一道门）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))
    from musespiral.infra.ai import prompts as ai_prompts

    try:
        ai_prompts.save_override("rewrite", "请模仿 glm-4 的口吻\n")
    except ValueError as err:
        assert "glm" in str(err)
    else:
        raise AssertionError("save_override 未拒绝违规内容")
    assert not (tmp_path / "musespiral-home" / "prompts.d" / "rewrite.txt").exists()


def test_override_dir_scan_catches_manual_drop(tmp_path):
    """目录扫描真拦截：手工落盘（绕过 API）的违规覆盖文件被 scan_prompt_overrides 抓到。"""
    from test_architecture import scan_prompt_overrides

    home = tmp_path / "musespiral-home"
    d = home / "prompts.d"
    d.mkdir(parents=True)
    (d / "rewrite.txt").write_text("你是 claude 3.5\n", encoding="utf-8")
    (d / "digest.txt").write_text("摘要任务底座。\n", encoding="utf-8")
    v = scan_prompt_overrides(home)
    assert len(v) == 1 and "rewrite.txt" in v[0] and "claude" in v[0], v
    # 对照：干净目录零违规；目录缺失也零违规（不误伤未配置环境）
    (d / "rewrite.txt").unlink()
    assert scan_prompt_overrides(home) == []
    assert scan_prompt_overrides(tmp_path / "no-such-home") == []


def test_dialect_table_single_source():
    """单一事实源：运行时校验表（config）与测试守门表同值（防两处漂移）。"""
    from musespiral.config import DIALECT_FORBIDDEN as runtime

    from test_architecture import DIALECT_FORBIDDEN as gate

    assert tuple(runtime) == tuple(gate)
