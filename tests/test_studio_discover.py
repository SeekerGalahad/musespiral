"""Studio 知识库选题引擎 API 契约与安全红线（第十二期 F26/F30）。

覆盖（沿 test_studio.py 惯例：真实回环服务器 + http.client，tmp_path 与
MUSESPIRAL_HOME 全隔离，不触 ~/.musespiral、零网络）：
  - KB 根未配置 → GET /api/discover 返回空 candidates（不 500）
  - source 穿越防护：../、绝对路径、%2e%2e%2f 一律 400（校验先于服务调用，
    服务缺席亦自证）
  - 动作闸门：discover_draft 必须 confirm:true；path 越界/绝对路径 400；
    方法不符 405
  - 契约形状（依赖 services/discover_service，importorskip——并行小组落地后
    集成阶段合验跑绿）：candidates 含 F26 输出形状（score_breakdown 四维/
    drafted 等）；draft 落盘文章根 + O_EXCL 拒绝重复
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from musespiral import config, studio

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    """工作目录（文章根）：复制样例文章，骨架产物应落在这里。"""
    root = tmp_path / "articles"
    root.mkdir()
    shutil.copy(FIXTURES / "sample_article.md", root / "sample_article.md")
    return root


@pytest.fixture
def server(studio_root: Path, _isolate_home) -> studio.ThreadingHTTPServer:
    """起真实 Studio 服务器（127.0.0.1 + 系统分配端口），测试结束优雅关闭。"""
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(
    server: studio.ThreadingHTTPServer, method: str, path: str, body: dict | None = None
) -> tuple[int, object]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _write_kb_root(tmp_path: Path, kb: Path) -> Path:
    """登记知识库根（~/.musespiral/kb_root.txt，随隔离的 MUSESPIRAL_HOME 解析）。"""
    home = Path(config.home_dir())
    home.mkdir(parents=True, exist_ok=True)
    (home / config.KB_ROOT_FILE).write_text(str(kb), encoding="utf-8")
    return kb


# ---- KB 根未配置 / 穿越防护 / 动作闸门（不依赖 discover_service，自证绿）----


def test_discover_empty_candidates_when_kb_unconfigured(server) -> None:
    """KB 根未配置 → 200 + 空 candidates（不 500，前端走空状态引导）。"""
    assert config.knowledge_root() is None
    code, payload = _request(server, "GET", "/api/discover")
    assert code == 200
    assert payload == {"candidates": []}


def test_discover_kb_root_missing_dir_returns_empty(server, tmp_path) -> None:
    """KB 根登记了但目录不存在 → 同样空 candidates（不 500）。"""
    _write_kb_root(tmp_path, tmp_path / "no-such-kb")
    code, payload = _request(server, "GET", "/api/discover")
    assert code == 200
    assert payload == {"candidates": []}


def test_discover_default_source_is_registered_root_not_cwd(server, tmp_path, monkeypatch) -> None:
    """无 source 参数：缺省=登记根下 13-source-analysis（CWD 无关——不落
    service 的 CWD 相对默认源）；目录缺失 → 空态不 500。"""
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    src = kb / "13-source-analysis"
    src.mkdir(parents=True)
    (src / "源码分析-Demo.md").write_text(
        "---\ntitle: 源码分析-Demo\ncategory: 13-source-analysis\n"
        "created: 2026-01-01\ntags: [源码分析, Python, 数据库, demo/demo]\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)  # CWD 与仓库根无关的任意目录
    code, payload = _request(server, "GET", "/api/discover?min_score=0")
    assert code == 200
    assert [c["title"] for c in payload["candidates"]] == ["源码分析-Demo"]
    (src / "源码分析-Demo.md").unlink()
    src.rmdir()
    code, payload = _request(server, "GET", "/api/discover")
    assert code == 200
    assert payload == {"candidates": []}


@pytest.mark.parametrize(
    "source",
    ["../outside", "../../etc", "/etc", "%2e%2e%2foutside", "sub/../../escape"],
)
def test_discover_source_traversal_rejected(server, tmp_path, source: str) -> None:
    """source 前缀必须落在知识库根内：穿越/绝对路径一律 400（校验先于服务调用）。"""
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    (kb / "13-source-analysis").mkdir(parents=True)
    code, payload = _request(server, "GET", f"/api/discover?source={source}")
    assert code == 400
    assert payload["ok"] is False


def test_discover_limit_must_be_integer(server, tmp_path) -> None:
    """limit 非整数 → 400（提交期同步校验，不建扫描）。"""
    _write_kb_root(tmp_path, tmp_path / "kb")
    (tmp_path / "kb").mkdir(exist_ok=True)
    code, _ = _request(server, "GET", "/api/discover?limit=abc")
    assert code == 400


def test_discover_min_score_must_be_integer(server, tmp_path) -> None:
    """min_score 非整数 → 400（提交期同步校验，不建扫描）。"""
    _write_kb_root(tmp_path, tmp_path / "kb")
    (tmp_path / "kb").mkdir(exist_ok=True)
    code, _ = _request(server, "GET", "/api/discover?min_score=abc")
    assert code == 400


def test_discover_min_score_gates_family_cards(server, tmp_path) -> None:
    """交界合验：族卡实测封顶 ~35 分 < 服务默认阈值 60 → 「模式家族」chip 恒空。

    缺省策略（payload 裁决）：未显式给 min_score 时，模式家族源缺省 0（族卡
    放行且 series 走「跨仓模式拆解」分叉）；显式值优先（60 仍滤空）；越界值
    钳制 [0,100]（250→100 滤空，不 500）。
    """
    _service()
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    fam = kb / "13-source-analysis" / "模式家族"
    fam.mkdir(parents=True)
    (fam / "注册表驱动工厂.md").write_text(
        "---\n"
        "title: 模式家族-注册表驱动工厂\n"
        "category: 13-source-analysis\n"
        "created: 2026-01-01\n"
        "tags: [模式家族]\n"
        "---\n"
        "# 模式家族：注册表驱动工厂\n"
        "短族卡（低于默认阈值的真实形态）。\n",
        encoding="utf-8",
    )
    base = "/api/discover?source=" + quote("13-source-analysis/模式家族")
    code, payload = _request(server, "GET", base)
    assert code == 200, "族卡源未显式给阈值 → 缺省 0 放行"
    titles = [c["title"] for c in payload["candidates"]]
    assert titles == ["模式家族-注册表驱动工厂"]
    assert payload["candidates"][0]["series"] == "跨仓模式拆解"
    code, payload = _request(server, "GET", base + "&min_score=60")
    assert code == 200 and payload["candidates"] == [], "显式 60 优先于缺省策略，滤掉族卡"
    code, payload = _request(server, "GET", base + "&min_score=250")
    assert code == 200 and payload["candidates"] == [], "钳到 100 后仍滤空（不 500）"


def test_discover_draft_requires_confirm_true(server, tmp_path) -> None:
    """动作接口必须 confirm:true（缺省/假值一律 400，不触知识库）。"""
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    (kb / "13-source-analysis").mkdir(parents=True)
    for body in ({"path": "13-source-analysis/x.md"}, {"path": "13-source-analysis/x.md", "confirm": False}):
        code, payload = _request(server, "POST", "/api/action/discover_draft", body)
        assert code == 400, body
        assert payload["ok"] is False


def test_discover_draft_kb_unconfigured_rejected(server) -> None:
    """KB 根未配置时生成骨架 → 400 可读错误（不 500）。"""
    code, payload = _request(
        server, "POST", "/api/action/discover_draft", {"path": "13-source-analysis/x.md", "confirm": True}
    )
    assert code == 400
    assert "知识库根未配置" in payload["error"]


@pytest.mark.parametrize("path", ["../escape.md", "/etc/passwd", "sub/../../x.md"])
def test_discover_draft_path_traversal_rejected(server, tmp_path, path: str) -> None:
    """path 参数过穿越防护：知识库根外一律 400。"""
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    (kb / "13-source-analysis").mkdir(parents=True)
    code, payload = _request(server, "POST", "/api/action/discover_draft", {"path": path, "confirm": True})
    assert code == 400
    assert payload["ok"] is False


def test_discover_draft_missing_doc_404(server, tmp_path) -> None:
    """根内但文件不存在 → 404。"""
    _write_kb_root(tmp_path, tmp_path / "kb")
    (tmp_path / "kb" / "13-source-analysis").mkdir(parents=True)
    code, _ = _request(
        server,
        "POST",
        "/api/action/discover_draft",
        {"path": "13-source-analysis/nope.md", "confirm": True},
    )
    assert code == 404


def test_discover_endpoints_method_gating(server) -> None:
    """方法表：/api/discover 仅 GET（POST→405）；discover_draft 仅 POST（GET→405）。"""
    assert _request(server, "POST", "/api/discover", {})[0] == 405
    assert _request(server, "GET", "/api/action/discover_draft")[0] == 405


# ---- 契约形状（依赖 discover_service——并行小组落地后集成阶段合验跑绿）----


def _make_kb_doc(kb: Path) -> Path:
    """造一篇高分工夫样本（行数≥200 + 四问章节 + 端到端章节 + owner/repo tag）。"""
    d = kb / "13-source-analysis"
    d.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"## 小节 {i}\n\n内容行 {i}。" for i in range(60))
    doc = d / "源码分析-demo-owner_demo-repo.md"
    doc.write_text(
        "---\n"
        "title: 源码分析-demo-owner/demo-repo\n"
        "category: 13-source-analysis\n"
        "created: 2026-11-01\n"
        "tags: [源码分析, Python, 数据库, demo-owner/demo-repo]\n"
        "---\n"
        "# 源码分析：demo-repo\n"
        "## 一、项目概述\n概述内容。\n"
        "## 二、架构总览\n架构内容。\n"
        "## 三、核心模块分析\n模块内容。\n"
        "## 四、端到端数据流\n数据流内容。\n"
        "## 五、设计思维提炼\n四问内容。\n"
        "## 六、可借鉴的工程实践\n实践内容。\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return doc


def _service():
    pytest.importorskip("musespiral.services.discover_service", reason="Group A 服务未落地（集成阶段合验）")
    from musespiral.services import discover_service

    return discover_service


def test_discover_contract_shape(server, tmp_path) -> None:
    """GET /api/discover 返回 F26 输出形状：score_breakdown 四维 + drafted 等齐全。

    path 必须是 KB 根相对（服务 scanner 给绝对路径，payload 层归一——绝对路径
    会被 draft 的穿越防护拒绝，「生成骨架」回贴该值才走得通）。
    """
    _service()
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    _make_kb_doc(kb)
    code, payload = _request(server, "GET", "/api/discover?source=13-source-analysis")
    assert code == 200
    candidates = payload["candidates"]
    assert isinstance(candidates, list) and candidates, "样本文档应产出候选"
    for key in (
        "path",
        "title",
        "score",
        "score_breakdown",
        "series",
        "series_no",
        "reason",
        "skeleton_hint",
        "drafted",
    ):
        assert key in candidates[0], key
    breakdown = candidates[0]["score_breakdown"]
    for key in ("depth", "uniqueness", "readability", "adaptability"):
        assert key in breakdown, key
    assert isinstance(candidates[0]["drafted"], bool)
    assert not candidates[0]["path"].startswith("/"), "候选 path 应为 KB 根相对路径"
    assert candidates[0]["path"].startswith("13-source-analysis/")


def test_discover_draft_writes_into_articles_root(server, tmp_path, studio_root: Path) -> None:
    """POST discover_draft：骨架写文章根（队列可见），重复提交被 O_EXCL 拒绝。

    path 取 GET /api/discover 返回值回贴（UI「生成骨架」按钮的真实数据流）。
    """
    _service()
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    _make_kb_doc(kb)
    code, payload = _request(server, "GET", "/api/discover?source=13-source-analysis")
    assert code == 200 and payload["candidates"]
    body = {"path": payload["candidates"][0]["path"], "confirm": True}
    code, payload = _request(server, "POST", "/api/action/discover_draft", body)
    assert code == 200, payload
    assert payload["ok"] is True
    output = studio_root / payload["output"]
    assert output.is_file(), "骨架必须落在 Studio 文章根内"
    assert "source" in output.read_text(encoding="utf-8"), "骨架应含 source 溯源字段"
    # O_EXCL：同一篇重复生成 → ok:false 可读错误（不 500）
    code2, payload2 = _request(server, "POST", "/api/action/discover_draft", body)
    assert code2 == 200
    assert payload2["ok"] is False
    assert "骨架已存在（拒绝覆盖）" in payload2["error"]
    assert "Errno" not in payload2["error"]  # 不透传原始 errno 文本


# ---- 档A M0：收藏星标 /api/discover/star（M4 起读写 materials triage.starred）----


def test_discover_star_roundtrip_persistence(server, tmp_path) -> None:
    """星标持久化契约（M4 迁移件二后）：POST 收藏 → materials.jsonl 落一条
    kb 引用素材（triage.starred=true，引用不复制）+ GET 集合可见（带 ts，
    即素材 fetched_at）；POST 取消 → triage 追加 starred=false 行。旧文件
    discover_stars.json 不再写入（迁移后只读退役）。"""
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    (kb / "13-source-analysis").mkdir(parents=True)
    rel = "13-source-analysis/demo.md"
    code, payload = _request(
        server, "POST", "/api/discover/star", {"path": rel, "starred": True, "confirm": True}
    )
    assert code == 200, payload
    assert payload["ok"] is True and payload["starred"] is True and payload["ts"]
    ts = payload["ts"]
    materials_file = Path(config.home_dir()) / "materials" / "materials.jsonl"
    assert materials_file.is_file(), "收藏必须持久化到 materials.jsonl"
    rows = [json.loads(x) for x in materials_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(
        (m.get("origin") or {}).get("uri") == rel and (m.get("triage") or {}).get("starred") is True
        for m in rows
    )
    assert not (Path(config.home_dir()) / "discover_stars.json").exists(), "旧星标文件停写"
    code, payload = _request(server, "GET", "/api/discover/star")
    assert code == 200
    assert rel in payload["starred"] and payload["starred"][rel] == ts
    # 取消收藏：triage 追加 starred=false 行（GET 集合移除）
    code, payload = _request(
        server, "POST", "/api/discover/star", {"path": rel, "starred": False, "confirm": True}
    )
    assert code == 200 and payload["ts"] == ""
    code, payload = _request(server, "GET", "/api/discover/star")
    assert rel not in payload["starred"]


def test_discover_star_recent_from_discovered_log(server, tmp_path) -> None:
    """「最近」tab 数据面：GET recent 取 discovered.json 倒序（path 归一 KB 相对）。"""
    import json as _json

    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    doc = kb / "13-source-analysis" / "demo.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# demo\n", encoding="utf-8")
    home = Path(config.home_dir())
    home.mkdir(parents=True, exist_ok=True)
    (home / "discovered.json").write_text(
        _json.dumps(
            {"ts": "2026-11-15T10:00:00", "action": "discover_draft",
             "path": str(doc), "title": "Demo", "score": 82, "output": "x.md"},
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    code, payload = _request(server, "GET", "/api/discover/star")
    assert code == 200
    assert payload["recent"] and payload["recent"][0]["path"] == "13-source-analysis/demo.md"
    assert payload["recent"][0]["title"] == "Demo"


def test_discover_star_gates_and_bounds(server, tmp_path) -> None:
    """星标动作闸门：confirm 缺省 400；starred 非布尔 400；路径越界/绝对 400；
    KB 根未配置 400；GET 未配置时 200 空集（不 500）。"""
    code, payload = _request(
        server, "POST", "/api/discover/star", {"path": "a.md", "starred": True}
    )
    assert code == 400 and "confirm" in payload["error"]
    kb = _write_kb_root(tmp_path, tmp_path / "kb")
    for bad_path in ("../escape.md", "/abs/a.md"):
        code, payload = _request(
            server, "POST", "/api/discover/star",
            {"path": bad_path, "starred": True, "confirm": True},
        )
        assert code == 400, bad_path
    code, payload = _request(
        server, "POST", "/api/discover/star",
        {"path": "a.md", "starred": "yes", "confirm": True},
    )
    assert code == 400 and "starred" in payload["error"]
    # KB 根取消登记后：GET 空集不 500；POST 可读 400
    (Path(config.home_dir()) / config.KB_ROOT_FILE).unlink()
    code, payload = _request(server, "GET", "/api/discover/star")
    assert code == 200 and payload["starred"] == {} and payload["recent"] == []
    code, payload = _request(
        server, "POST", "/api/discover/star", {"path": "a.md", "starred": True, "confirm": True}
    )
    assert code == 400 and "知识库根" in payload["error"]
