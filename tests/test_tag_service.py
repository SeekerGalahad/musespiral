"""自动标签建议服务测试（Phase D §五 F77 行，6 用例）。

覆盖：F77-1 预览零落盘（materials.jsonl 字节不变 + 不建 job + 缓存条目
无 prompt 正文）；F77-2 TAG: 协议解析容错（空行/无前缀/超长/超 5/互撞
先出现者胜）；F77-3 冲突三档（撞既有不重复写/互撞先胜/并集超限既有优先
截断+被弃清单）；F77-4 triage 窄门（payload 越键 400 回归 + 采纳只动
triage）；F77-5 缓存复跑（mock 计数=1，命中行零出账）；F77-6 API 契约
（锁面相等/confirm 闸门/缺 id 400/业务错 200 ok:false/响应形状）。
MUSESPIRAL_HOME monkeypatch 到 tmp 隔离；AI 假件经 chat_fn/gateway._real_chat
注入（gateway 设计注入点，不 mock HTTP）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.infra.ai import usage as usage_mod
from musespiral.infra.ai import gateway as gateway_mod
from musespiral.services import materials_service as ms
from musespiral.services import tag_service as svc

_TAG_REPLY = "TAG: python\nTAG: 数据工程"


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(env: Path) -> Path:
    """配置一个本地无 key 档案（tag_suggest 未配路由→走 default）。"""
    (env / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return env


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _material(env: Path, *, seq: int = 1, title: str = "粘贴素材", tags: list[str] | None = None) -> dict:
    row = model.make_material(
        mtype="crawled", source_id="manual", uri=f"https://x/{seq}", title=title,
        payload={"text": "手动粘贴的正文内容，讲数据管道与标签组织。"},
        fetched_at="2026-11-16T09:00:00",
    )
    ms.add_material(row)
    if tags:
        ms.update_triage(row["id"], {"tags": tags})
        row = ms.get_material(row["id"])
    return row


def _materials_bytes(env: Path) -> bytes:
    return (env / "materials" / "materials.jsonl").read_bytes()


def _fake_chat(reply: str = _TAG_REPLY, calls: list | None = None):
    """gateway 形态假件（签名=client.chat；经 chat_fn 注入点进治理层）。"""
    calls = calls if calls is not None else []

    def fake(messages, *, task, temperature=0.6, timeout=120, profile=None):
        calls.append({"task": task, "content": messages[0]["content"]})
        return reply

    return fake, calls


def _request(server, method: str, path: str, body: dict | None = None):
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


# ── F77-1 预览零落盘（字节断言）────────────────────────────────────


def test_f77_1_preview_zero_disk_byte_identity(env, ai_on) -> None:
    row = _material(env, tags=["python"])
    fake, calls = _fake_chat()
    before = _materials_bytes(env)
    out = svc.preview(row["id"], chat_fn=fake)
    # 任务键与 prompt 注入（正文节选 + 既有标签词表）
    assert calls[0]["task"] == "tag_suggest"
    assert "手动粘贴的正文内容" in calls[0]["content"]
    assert "【既有标签词表】" in calls[0]["content"]
    # 响应形状（F77.4）：tags 已服务端归一化+过滤；existing 供标冲突档
    assert out == {"id": row["id"], "title": "粘贴素材", "tags": ["python", "数据工程"], "existing": ["python"]}
    # 零落盘：素材库逐字节不变 + 不建 job（预览即焚判例）
    assert _materials_bytes(env) == before
    assert not (env / "jobs.jsonl").exists()
    # 治理层派生态合法写入（cache/usage 非素材库留痕）；缓存条目只有输出正文
    entries = list((env / "cache" / "ai").glob("*.json"))
    assert len(entries) == 1
    entry = json.loads(entries[0].read_text(encoding="utf-8"))
    assert entry["text"] == _TAG_REPLY
    assert "手动粘贴的正文内容" not in json.dumps(entry, ensure_ascii=False)  # prompt 正文不落缓存


# ── F77-2 协议解析容错 ───────────────────────────────────────────────


def test_f77_2_parse_suggestions_protocol_tolerance() -> None:
    text = (
        "好的，以下是建议：\n"
        "\n"
        "TAG: Machine Learning\n"           # 归一化 machine-learning
        "TAG: 大数据\n"
        f"TAG: {'字' * 30}\n"                # 规范化后 30 字符 >24 → 丢弃
        "无前缀行直接忽略\n"
        "TAG: 大数据\n"                       # 互撞：先出现者胜
        "TAG: Python\n"
        "TAG: etl\n"
        "TAG: extra5\n"
        "TAG: extra6\n"                       # 第 6 个 → 截断
    )
    assert svc.parse_suggestions(text) == ["machine-learning", "大数据", "python", "etl", "extra5"]
    assert svc.parse_suggestions("") == []
    assert svc.parse_suggestions(None) == []
    assert svc.parse_suggestions("TAG:   \n---\n===\n") == []  # 空前缀/无前缀全忽略


# ── F77-3 冲突三档 ───────────────────────────────────────────────────


def test_f77_3_conflict_three_tiers(env, ai_on) -> None:
    # ① 建议撞既有：preview 下发 existing 供标「已有」；adopt 并集去重不报错
    row = _material(env, seq=1, tags=["python"])
    fake, _calls = _fake_chat("TAG: Python\nTAG: 新领域")
    out = svc.preview(row["id"], chat_fn=fake)
    assert out["tags"] == ["python", "新领域"]
    assert set(out["tags"]) & set(out["existing"]) == {"python"}  # 冲突档① 标记依据
    res = svc.adopt(row["id"], out["tags"])
    assert res["tags"] == ["python", "新领域"]  # 撞既有不重复写入
    assert res["dropped"] == []
    # ② 建议互撞：归一化后同值先出现者胜（采纳集内部互撞同口径）
    res2 = svc.adopt(row["id"], ["ETL", "etl"])
    assert res2["tags"] == ["python", "新领域", "etl"]
    # ③ 并集超限：既有优先+建议按序补足，保序截断至 12，被弃清单随结果返回
    row3 = _material(env, seq=2, tags=[f"t{i:02d}" for i in range(10)])
    res3 = svc.adopt(row3["id"], ["a01", "b02", "c03", "d04"])
    assert res3["tags"] == [f"t{i:02d}" for i in range(10)] + ["a01", "b02"]
    assert res3["dropped"] == ["c03", "d04"]
    stored = ms.get_material(row3["id"])
    assert stored["triage"]["tags"] == res3["tags"]


# ── F77-4 triage 窄门（payload 越键 400 回归）───────────────────────


def test_f77_4_narrow_gate_payload_overkey_rejected(env, ai_on, server) -> None:
    row = _material(env, seq=1)
    # 服务层窄门回归：changes 越键（payload）被 triage_patch 拒绝
    with pytest.raises(ValueError, match="只允许改"):
        ms.update_triage(row["id"], {"tags": ["x"], "payload": {"text": "篡改"}})
    # 采纳路径只动 triage：payload/provenance 逐字节不动（immutable 另一面）
    before = ms.get_material(row["id"])
    svc.adopt(row["id"], ["python"])
    after = ms.get_material(row["id"])
    assert after["payload"] == before["payload"]
    assert after["provenance"] == before["provenance"]
    assert after["triage"]["tags"] == ["python"]
    # HTTP 窄门回归：POST /api/materials/triage 越键 → 400（窄门不宽）
    code, payload = _request(server, "POST", "/api/materials/triage", {
        "confirm": True, "id": row["id"], "tags": ["y"], "payload": {"text": "越键"},
    })
    assert code == 400 and "只允许改" in payload["error"]
    assert ms.get_material(row["id"])["triage"]["tags"] == ["python"]  # 越键请求零写入


# ── F77-5 缓存复跑（mock 计数=1）────────────────────────────────────


def test_f77_5_cache_rerun_counts_one_call(env, ai_on) -> None:
    row = _material(env, seq=1)
    fake, calls = _fake_chat()
    out1 = svc.preview(row["id"], chat_fn=fake)
    out2 = svc.preview(row["id"], chat_fn=fake)  # 同素材重建议=缓存命中零网络
    assert len(calls) == 1
    assert out1["tags"] == out2["tags"]
    # 台账两行：首跑出账（cache_hit=false）、复跑命中只记账不出账
    rows = [r for r in usage_mod.read_all() if r.get("task") == "tag_suggest"]
    assert [r["cache_hit"] for r in rows] == [False, True]


# ── F77-6 API 契约 ───────────────────────────────────────────────────


def test_f77_6_api_contract_tags_suggest(server, studio_root, env, ai_on, monkeypatch) -> None:
    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.plugin_actions import POST_DYNAMIC_SURFACE
    from musespiral.studio.server import _BINARY_POST_ROUTES

    from tests.test_architecture import CONTRACT_POST_EXACT

    row = _material(env, seq=1)
    fake, _calls = _fake_chat()
    monkeypatch.setattr(gateway_mod, "_real_chat", fake)  # API 层唯一假件缝（服务线程同进程）

    # 双锁同步：路由入表 + 入 POST 锁 + 面锁仍逐条相等（本组路由登记后；
    # AI-1 批起 POST 面含动态段占位 runs/<run_id>/adopt|discard）
    assert "/api/action/tags_suggest" in _ACTION_ROUTES
    assert "/api/action/tags_suggest" in CONTRACT_POST_EXACT
    assert (
        set(_ACTION_ROUTES) | set(_BINARY_POST_ROUTES) | set(POST_DYNAMIC_SURFACE)
    ) == CONTRACT_POST_EXACT

    # confirm 全局闸门 / 缺 id → 400
    assert _request(server, "POST", "/api/action/tags_suggest", {"id": row["id"]})[0] == 400
    assert _request(server, "POST", "/api/action/tags_suggest", {"confirm": True})[0] == 400
    # 业务校验 200 {ok:false}：素材不存在（RefineError 族可读）
    code, miss = _request(server, "POST", "/api/action/tags_suggest", {"confirm": True, "id": "nope00000000"})
    assert code == 200 and miss["ok"] is False and "不存在" in miss["error"]
    # 金路径：200 {ok:true, id, title, tags, existing}；建议面不外带 AI 原文
    before = _materials_bytes(env)
    code, payload = _request(server, "POST", "/api/action/tags_suggest", {"confirm": True, "id": row["id"]})
    assert code == 200
    assert payload == {"ok": True, "id": row["id"], "title": "粘贴素材", "tags": ["python", "数据工程"], "existing": []}
    assert "text" not in payload
    # 同步无 job + 预览零落盘（materials.jsonl 字节不变）
    assert _materials_bytes(env) == before
    assert not (env / "jobs.jsonl").exists()


# ── 复核修复批（2026-11-17）：内置模板变更使缓存失效（template 参口径）──


def test_builtin_prompt_change_invalidates_cache(env, ai_on, monkeypatch) -> None:
    """F79-1 口径修正回归锚：tag_suggest 无覆盖时 template_sha 此前恒为
    sha1('')——改内置 DEFAULT_PROMPT 旧缓存被误命中。修复后 preview 把
    `load_override or DEFAULT_PROMPT` 透传 gateway template 参，内置模板
    一变 → key 变 → 重新触网（同 prompt 正文也因模板不同而不同语义）。"""
    row = _material(env, seq=2)
    fake, calls = _fake_chat()
    svc.preview(row["id"], chat_fn=fake)
    svc.preview(row["id"], chat_fn=fake)  # 同内置模板 → 命中
    assert len(calls) == 1
    monkeypatch.setattr(svc, "DEFAULT_PROMPT", "新版内置模板：输出 TAG: 行。")
    svc.preview(row["id"], chat_fn=fake)  # 内置模板变 → miss 重调
    assert len(calls) == 2, "内置模板变更必须使旧缓存失效（template 参口径）"


def test_batch_prompt_override_cascade(env, ai_on) -> None:
    """tag_batch 走 apply_prompt_overrides：prompts.d/tag_suggest.txt 对批量
    步同样生效（此前 plans 字面量使覆盖零作用——复核实测回归锚）。"""
    from musespiral.infra.ai.prompts import save_override

    row = _material(env, seq=3)
    save_override("tag_suggest", "覆盖后的批量标签模板：输出 TAG: 行。")
    fake, calls = _fake_chat()
    svc.batch([row["id"]], chat_fn=fake)
    assert calls, "batch 应至少调用一次 chat"
    assert any("覆盖后的批量标签模板" in c["content"] for c in calls), \
        "覆盖文本未进批量步 prompt"


# ── 入口 B 前端契约（Phase C/D，2026-11-17）──────────────────────────


def test_e2_frontend_contract_zero_write_and_shapes(env, ai_on, server, studio_root) -> None:
    """素材详情「建议标签」前端所依赖的三条服务端契约（前端 E2 的验收基础）：

    ① AI 零落盘：预览前后 materials.jsonl **逐字节不变**（前端点「获取建议」
       走同一端点，故用同一字节门看守）；
    ② 响应形状：`tags`（≤5，已归一）+ `existing`（既有标签）——前端据此标
       「已有」禁用档与算并集；**当前响应不含 cache_hit**（tag_service.preview
       只回四键），故前端「（缓存）」徽只在服务端真回该键时才亮——本测试把
       这一事实钉住，防止误以为前端能凭空显示缓存态；
    ③ 采纳写面：/api/materials/triage 的 tags 是**全量替换**——前端必须带
       既有标签，超 12 由窄门 ValueError→400 拒绝（不是服务端截断），故前端
       负责事前截断+告知。
    """
    row = _material(env, tags=["python", "数据工程"])
    fake, _calls = _fake_chat()
    before = _materials_bytes(env)

    # ② 形状：仅 chat_fn 注入（同 F77-1 口径，不经 HTTP 便于断言 cache_hit 缺席）
    out = svc.preview(row["id"], chat_fn=fake)
    assert set(out) == {"id", "title", "tags", "existing"}
    assert len(out["tags"]) <= 5
    assert out["existing"] == ["python", "数据工程"]
    assert "cache_hit" not in out, "preview 不回 cache_hit —— 前端缓存徽必须为之缺席"

    # ① 零落盘（字节门）
    assert _materials_bytes(env) == before
    assert not (env / "jobs.jsonl").exists()

    # ③ 全量替换语义 + 超 12 被拒（文档化前端为何要自己截断）
    import pytest as _pytest
    many = [f"t{i}" for i in range(1, 14)]
    with _pytest.raises(ValueError):
        ms.update_triage(row["id"], {"tags": many})
    # 12 个以内可写；写入后既有标签若未携带即被抹（全量替换的字面含义）
    ms.update_triage(row["id"], {"tags": ["only"]})
    after = ms.get_material(row["id"])["triage"]["tags"]
    assert after == ["only"], "tags 是全量替换——漏带既有标签即被抹掉"
