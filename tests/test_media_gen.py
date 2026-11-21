"""G1 文生图（media_gen 唯一出口 + preview/adopt/verify）+ F1 AI 治理读面测试。

红线对齐（G1 工作流契约，调研 §四/§七 A-1~A-5）：
  - 白名单拒绝（host ∉ 允许集 / 非 https，零网络请求即拒）；
  - preview 零落盘（materials.jsonl 字节断言 + 服务端临时文件存在）；
  - 采纳形状全对（AI-GENERATED / meta.ai_generated / chain generate /
    enabled=false / AI 虚拟源）+ PNG tEXt 四键（GB 45438-2025）；
  - 密钥 canary 零回显（错误文本/CLI 输出/落库行/预览 sidecar 全零命中）；
  - 双形态（b64_json 优先 + URL 下载兜底）；
  - seed+params 透传（model/prompt/response_format 安全键强制）；
  - GET /api/ai/usage 与 CLI `musespiral ai usage` 同数（summarize 单一权威）；
  - 全程 mock 网络（FakeOpener 注入 / 服务层接缝 monkeypatch）——零真实请求。
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from musespiral import studio
from musespiral.commands.ai_cmds import ai
from musespiral.commands.media_cmds import media
from musespiral.core.materials import model as material_model
from musespiral.infra import media_gen
from musespiral.infra.ai import usage as usage_mod
from musespiral.infra.media_gen import ALLOWED_HOSTS, MediaGenError
from musespiral.services import media_gen_service

CANARY = "sk-CANARY-a1b2c3d4e5f6g7h8"


# ── 隔离与替身 ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _png_bytes(color=(200, 30, 40), size=(10, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    """替身 HTTPResponse：.status/.read()，上下文管理器形态。"""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class FakeOpener:
    """替身 urllib.request：按 URL 前缀路由；全程记录请求（测试断言面）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.routes: list[tuple[str, object]] = []  # (url 片段, handler(req)→(status, body))

    def route(self, fragment: str, handler) -> None:
        self.routes.append((fragment, handler))

    def urlopen(self, req, timeout: int = 0):
        self.calls.append(
            {
                "method": req.method,
                "url": req.full_url,
                "body": req.data,
                "headers": {k.lower(): v for k, v in req.header_items()},
                "timeout": timeout,
            }
        )
        for fragment, handler in self.routes:
            if fragment in req.full_url:
                status, body = handler(req)
                return _Resp(status, body)
        raise AssertionError(f"测试替身未覆盖该 URL（未发生真实网络）: {req.full_url}")


def _write_media_profile(
    *, base_url: str = "https://api.siliconflow.cn/v1",
    api_key: str = CANARY, model: str = "FLUX.1-schnell", name: str = "siliconflow",
) -> None:
    """写一份 media 域档案（独立命名空间 media_profiles/media_default——
    评审修正 2026-11-17：不进 chat TASKS/routing；canary key 供零回显断言）。"""
    from musespiral import config

    home = config.home_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / "ai.json").write_text(
        json.dumps(
            {
                "media_default": name,
                "media_profiles": {
                    name: {"base_url": base_url, "api_key": api_key, "model": model}
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _b64_response(png: bytes, model: str = "FLUX.1-schnell") -> bytes:
    return json.dumps(
        {"data": [{"b64_json": base64.b64encode(png).decode("ascii")}], "model": model}
    ).encode("utf-8")


def _ok_opener(png: bytes) -> FakeOpener:
    op = FakeOpener()
    op.route("api.siliconflow.cn/v1/images/generations", lambda req: (200, _b64_response(png)))
    return op


# ── 白名单（提交期零网络即拒）──────────────────────────────


def test_whitelist_rejects_foreign_host() -> None:
    """media 档案手滑填 chat 域端点（deepseek）→ 白名单拒（零网络）。"""
    _write_media_profile(base_url="https://api.deepseek.com/v1", name="chatmix")
    with pytest.raises(MediaGenError, match="白名单"):
        media_gen.generate_image(prompt="一只橘猫", opener=FakeOpener())


def test_whitelist_rejects_http_scheme() -> None:
    """非 https 端点 → 拒（零出网纪律：明文出网被禁）。"""
    _write_media_profile(base_url="http://api.siliconflow.cn/v1")
    with pytest.raises(MediaGenError, match="https"):
        media_gen.generate_image(prompt="一只橘猫", opener=FakeOpener())


def test_whitelist_hosts_are_provider_domains() -> None:
    """白名单=预设表派生（SiliconFlow 先行 + DashScope 档案位，单一事实源）。"""
    assert ALLOWED_HOSTS == frozenset({"api.siliconflow.cn", "dashscope.aliyuncs.com"})
    assert media_gen.PROVIDERS["siliconflow"] == "https://api.siliconflow.cn/v1"
    assert "dashscope" in media_gen.PROVIDERS  # 档案位（调研 §五 裁决 2）


def test_unconfigured_readable_error() -> None:
    """未配置 → 可读引导（指向 musespiral media config）。"""
    with pytest.raises(MediaGenError, match="musespiral media config"):
        media_gen.generate_image(prompt="一只橘猫", opener=FakeOpener())


# ── 双形态 + 参数透传（A-1/A-3）───────────────────────────


def test_b64_json_primary_form() -> None:
    """显式请求 b64_json 且响应 b64_json → 直接解码（第一形态）。"""
    _write_media_profile()
    png = _png_bytes()
    op = _ok_opener(png)
    result = media_gen.generate_image(prompt=" 一只橘猫 ", opener=op)
    assert result["format"] == "png"
    assert result["data"] == png
    assert result["provider"] == "siliconflow"
    assert result["model"] == "FLUX.1-schnell"
    (call,) = op.calls
    assert call["url"] == "https://api.siliconflow.cn/v1/images/generations"
    body = json.loads(call["body"])
    assert body["response_format"] == "b64_json"  # A-1 显式请求
    assert body["prompt"] == "一只橘猫"  # strip 归一
    assert body["size"] == "1024x1024"
    assert "seed" not in body  # 未给不携带


def test_url_fallback_download() -> None:
    """URL-only 模型 → 下载兜底（第二形态）：SSRF 门放行的 https CDN 可下。"""
    _write_media_profile()
    png = _png_bytes(color=(1, 2, 3))
    op = FakeOpener()
    op.route(
        "images/generations",
        lambda req: (200, json.dumps({"data": [{"url": "https://cdn.example.com/files/x.png"}]}).encode()),
    )
    op.route("cdn.example.com/files/x.png", lambda req: (200, png))
    result = media_gen.generate_image(prompt="夜景", opener=op)
    assert result["data"] == png and result["format"] == "png"
    assert [c["method"] for c in op.calls] == ["POST", "GET"]  # 先生成后下载


def test_url_fallback_rejects_private_target() -> None:
    """下载兜底过 SSRF 门：内网/回环 URL 拒（fetcher.assert_public_url 同道门）。"""
    _write_media_profile()
    op = FakeOpener()
    op.route(
        "images/generations",
        lambda req: (200, json.dumps({"data": [{"url": "https://127.0.0.1:8443/evil.png"}]}).encode()),
    )
    with pytest.raises(MediaGenError, match="SSRF"):
        media_gen.generate_image(prompt="夜景", opener=op)


def test_seed_and_params_passthrough() -> None:
    """seed 可选 + params 透传（A-3）；安全键 model/prompt/response_format 强制。"""
    _write_media_profile()
    op = FakeOpener()
    op.route("images/generations", lambda req: (200, _b64_response(_png_bytes())))
    media_gen.generate_image(
        prompt="橘猫", size="768x768", seed=42,
        params={"guidance": 3.5, "negative_prompt": "模糊", "model": "evil", "response_format": "url"},
        opener=op,
    )
    body = json.loads(op.calls[0]["body"])
    assert body["seed"] == 42
    assert body["guidance"] == 3.5
    assert body["negative_prompt"] == "模糊"
    assert body["size"] == "768x768"
    assert body["model"] == "FLUX.1-schnell"  # 透传不覆盖安全键
    assert body["response_format"] == "b64_json"  # 双形态契约根基


def test_api_key_only_in_authorization_header() -> None:
    """api_key 只进 Authorization 头：不出现在 URL/请求体。"""
    _write_media_profile()
    op = _ok_opener(_png_bytes())
    media_gen.generate_image(prompt="x", opener=op)
    (call,) = op.calls
    assert call["headers"]["authorization"] == f"Bearer {CANARY}"
    assert CANARY not in call["url"]
    assert CANARY not in call["body"].decode("utf-8")


def test_both_forms_missing_rejected() -> None:
    """响应既无 b64_json 也无 url → 可读拒绝。"""
    _write_media_profile()
    op = FakeOpener()
    op.route("images/generations", lambda req: (200, b'{"data":[{}]}'))
    with pytest.raises(MediaGenError, match="双形态"):
        media_gen.generate_image(prompt="x", opener=op)


def test_non_image_payload_rejected() -> None:
    """返回非图片字节（文本伪图）→ 魔数复核拒。"""
    _write_media_profile()
    op = FakeOpener()
    op.route(
        "images/generations",
        lambda req: (200, _b64_response(b"<svg onload=alert(1)>not-an-image")),
    )
    with pytest.raises(MediaGenError, match="不是受支持的图片格式"):
        media_gen.generate_image(prompt="x", opener=op)


# ── preview 零落盘 / adopt 落库形状 + tEXt ─────────────────


def _materials_path() -> Path:
    from musespiral import config

    return config.home_dir() / "materials" / "materials.jsonl"


def test_preview_zero_material_writes() -> None:
    """红线：preview 后 materials.jsonl 字节不变 + 服务端临时文件存在。"""
    _write_media_profile()
    before = _materials_path().read_bytes() if _materials_path().exists() else None
    result = media_gen_service.preview("极简主义封面，留白", size="1024x1024", opener=_ok_opener(_png_bytes()))
    assert result["preview_id"] and len(result["preview_id"]) == 16
    assert result["url"] == f"/api/media/preview?id={result['preview_id']}"
    assert result["width"] == 10 and result["height"] == 8 and result["format"] == "png"
    after = _materials_path().read_bytes() if _materials_path().exists() else None
    assert after == before  # 零落 materials（红线）
    directory = media_gen_service.preview_dir()
    assert (directory / f"{result['preview_id']}.png").is_file()  # A-2 临时文件
    sidecar = json.loads((directory / f"{result['preview_id']}.json").read_text(encoding="utf-8"))
    assert sidecar["prompt"] == "极简主义封面，留白"
    assert sidecar["model"] == "FLUX.1-schnell"
    assert CANARY not in json.dumps(sidecar)  # sidecar 零密钥


def test_adopt_full_shape_with_text_chunk() -> None:
    """采纳形状全对：四枚举位 + chain generate + enabled=false + tEXt 四键。"""
    _write_media_profile()
    png = _png_bytes()
    pre = media_gen_service.preview("赛博朋克城市夜景", opener=_ok_opener(png))
    result = media_gen_service.adopt(pre["preview_id"], title="城市之夜")
    mat = result["material"]
    assert result["ok"] is True and result["dedup"] is False
    assert mat["type"] == "image"
    assert mat["license"] == "AI-GENERATED"  # 裁决 1：第四枚举
    assert mat["origin"]["source_id"] == "ai_gen"
    assert mat["origin"]["uri"].startswith("generate://sha256:")
    assert mat["provenance"] == {"chain": [{"from_id": "ai", "op": "generate"}], "derived_from": []}
    assert mat["triage"]["enabled"] is False  # 裁决 4：默认不进发现页
    meta = mat["payload"]["meta"]
    assert meta["ai_generated"] is True
    assert meta["prompt"] == "赛博朋克城市夜景"
    assert meta["provider"] == "siliconflow" and meta["model"] == "FLUX.1-schnell"
    assert meta["generated_at"] and meta["sha256"] and meta["format"] == "PNG"
    # tEXt 四键（GB 45438-2025 隐式标识，A-5）
    from musespiral import config

    stored = config.home_dir() / mat["payload"]["local_path"]
    assert stored.is_file() and stored.suffix == ".png"
    with Image.open(stored) as img:
        assert img.size == (10, 8)
        for key in ("prompt", "provider", "model", "generated_at"):
            assert img.info.get(key), f"tEXt 缺键: {key}"
        assert img.info["provider"] == "siliconflow"
    # 缩略 + 搬正（预览临时文件随采纳消失）
    assert (stored.parent / f"{mat['id']}.thumb.webp").is_file()
    assert not (media_gen_service.preview_dir() / f"{pre['preview_id']}.png").exists()
    assert not (media_gen_service.preview_dir() / f"{pre['preview_id']}.json").exists()
    assert mat["id"] in _materials_path().read_text(encoding="utf-8")


def test_adopt_transcodes_non_png_to_png_with_text() -> None:
    """非 PNG 生成物统一转码 PNG——每张采纳图都带 tEXt（合规不因上游格式缺席）。"""
    _write_media_profile(model="Qwen-Image")
    jpeg_buf = io.BytesIO()
    Image.new("RGB", (6, 6), (9, 9, 9)).save(jpeg_buf, "JPEG")
    op = FakeOpener()
    op.route(
        "images/generations",
        lambda req: (
            200,
            json.dumps({"data": [{"b64_json": base64.b64encode(jpeg_buf.getvalue()).decode()}]}).encode(),
        ),
    )
    pre = media_gen_service.preview("jpeg 模型输出", opener=op)
    assert pre["format"] == "jpeg"
    result = media_gen_service.adopt(pre["preview_id"])
    mat = result["material"]
    assert mat["payload"]["local_path"].endswith(".png")
    from musespiral import config

    with Image.open(config.home_dir() / mat["payload"]["local_path"]) as img:
        assert img.format == "PNG" and img.info.get("prompt") == "jpeg 模型输出"


def test_adopt_dedup_same_bytes() -> None:
    """同图两次独立预览 → 同 sha256 幂等锚：第二次 dedup=true 零追加。"""
    _write_media_profile()
    png = _png_bytes()
    p1 = media_gen_service.preview("同图", opener=_ok_opener(png))
    p2 = media_gen_service.preview("同图", opener=_ok_opener(png))
    r1 = media_gen_service.adopt(p1["preview_id"])
    rows_after_first = len(_materials_path().read_text(encoding="utf-8").splitlines())
    r2 = media_gen_service.adopt(p2["preview_id"])
    rows_after_second = len(_materials_path().read_text(encoding="utf-8").splitlines())
    assert r1["id"] == r2["id"]
    assert r1["dedup"] is False and r2["dedup"] is True
    assert rows_after_second == rows_after_first  # 幂等零追加


def test_adopt_missing_preview_rejected() -> None:
    """过期/不存在预览 → 可读 400 语义错误。"""
    with pytest.raises(media_gen_service.MediaGenServiceError, match="预览不存在"):
        media_gen_service.adopt("0123456789abcdef")
    with pytest.raises(media_gen_service.MediaGenServiceError, match="形状非法"):
        media_gen_service.adopt("../escape")


def test_preview_gc_stale_files() -> None:
    """陈旧预览（>24h）由下一次 preview 顺带 GC（拒绝即消失的落地形态）。"""
    import os
    import time as _time

    _write_media_profile()
    old = media_gen_service.preview_dir()
    old.mkdir(parents=True, exist_ok=True)
    stale = old / "deadbeefdeadbeef.png"
    stale.write_bytes(_png_bytes())
    stale_sidecar = old / "deadbeefdeadbeef.json"
    stale_sidecar.write_text("{}", encoding="utf-8")
    stamp = _time.time() - 25 * 3600
    os.utime(stale, (stamp, stamp))
    os.utime(stale_sidecar, (stamp, stamp))
    media_gen_service.preview("新一张", opener=_ok_opener(_png_bytes()))
    assert not stale.exists() and not stale_sidecar.exists()


# ── verify 三态（A-4）──────────────────────────────────────


def test_verify_unconfigured_third_state() -> None:
    """未配置是第三态（不是故障）。"""
    result = media_gen_service.verify(opener=FakeOpener())
    assert result["health"]["status"] == "unconfigured"
    assert media_gen_service.read_health()["status"] == "unconfigured"  # 回写


def test_verify_ok_and_down() -> None:
    """/models 探测：200 → ok；500 → down（返回值不抛错）。"""
    _write_media_profile()
    op = FakeOpener()
    op.route("api.siliconflow.cn/v1/models", lambda req: (200, b'{"data":[]}'))
    health = media_gen_service.verify(opener=op)["health"]
    assert health["status"] == "ok" and health["profile"] == "siliconflow"
    op_down = FakeOpener()
    op_down.route("api.siliconflow.cn/v1/models", lambda req: (401, b'{"error":"bad key"}'))
    down = media_gen_service.verify(opener=op_down)["health"]
    assert down["status"] == "down" and "401" in down["detail"]


def test_verify_whitelist_violation_is_down_not_crash() -> None:
    """白名单外档案探活 → down 带理由（零网络请求）。"""
    _write_media_profile(base_url="https://api.deepseek.com/v1", name="chatmix")
    health = media_gen_service.verify(opener=FakeOpener())["health"]
    assert health["status"] == "down" and "白名单" in health["detail"]


# ── model 值域（license 第四值 + generate 链豁免）──────────


def test_license_enum_and_generate_chain_validation() -> None:
    assert "AI-GENERATED" in material_model.LICENSES
    assert "generate" in material_model.PROVENANCE_OPS
    row = material_model.make_material(
        mtype="image", source_id="ai_gen", uri="generate://sha256:x",
        license="AI-GENERATED",
        provenance={"chain": [{"from_id": "ai", "op": "generate"}], "derived_from": []},
        fetched_at="2026-11-17T00:00:00",
    )
    assert material_model.validate_material(row) == []
    bad = dict(row)
    bad["license"] = "GENERATIVE"  # 枚举外
    assert material_model.validate_material(bad)
    worse = material_model.make_material(
        mtype="image", source_id="s", uri="https://x", fetched_at="2026-11-17T00:00:00",
        provenance={"chain": [{"from_id": "s", "op": "summarize"}], "derived_from": []},
    )
    errors = material_model.validate_material(worse)
    assert any("provenance 必须为空" in e for e in errors)  # 非 generate 链照旧拒绝


# ── Studio HTTP 面（端到端；服务层接缝 monkeypatch 零真网）──


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    fixtures = Path(__file__).resolve().parent / "fixtures"
    shutil.copy(fixtures / "sample_article.md", root / "sample_article.md")
    return root


@pytest.fixture
def server(studio_root: Path) -> studio.ThreadingHTTPServer:
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server: studio.ThreadingHTTPServer, method: str, path: str, body=None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers, payload = {}, None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type", ""), data


def _fake_generate(png: bytes):
    """服务层接缝替身：返回 media_gen.generate_image 同形结果（零真网）。"""
    return lambda **kwargs: {"data": png, "format": "png", "provider": "siliconflow", "model": "FLUX.1-schnell"}


def test_http_generate_preview_and_adopt_flow(server, monkeypatch) -> None:
    """generate_image（confirm 门+400 面）→ GET preview 字节流 → adopt 落库。"""
    png = _png_bytes()
    monkeypatch.setattr(media_gen_service, "_generate_image", _fake_generate(png))
    # 缺 confirm → 400（全局闸门）
    code, _, raw = _request(server, "POST", "/api/action/generate_image", {"prompt": "x"})
    assert code == 400
    # 缺 prompt → 400
    code, _, raw = _request(server, "POST", "/api/action/generate_image", {"confirm": True})
    assert code == 400
    # 正常预览
    code, _, raw = _request(server, "POST", "/api/action/generate_image",
                            {"confirm": True, "prompt": "青黛山影", "size": "768x768", "seed": 7})
    assert code == 200
    pre = json.loads(raw)
    assert pre["ok"] is True and pre["url"].startswith("/api/media/preview?id=")
    # 预览字节流（Content-Type 按魔数）
    code, ctype, body = _request(server, "GET", pre["url"])
    assert code == 200 and ctype == "image/png" and body == png
    # 采纳 → 落库；预览 404（搬正后消失）
    code, _, raw = _request(server, "POST", "/api/action/adopt_generated",
                            {"confirm": True, "preview_id": pre["preview_id"]})
    assert code == 200
    adopted = json.loads(raw)
    assert adopted["ok"] is True and adopted["material"]["license"] == "AI-GENERATED"
    code, _, _ = _request(server, "GET", pre["url"])
    assert code == 404
    # 形状非法 preview_id → 400
    code, _, _ = _request(server, "POST", "/api/action/adopt_generated",
                          {"confirm": True, "preview_id": "../etc"})
    assert code == 400


def test_http_generate_degrades_on_media_error(server, monkeypatch) -> None:
    """MediaGenError → 200 {ok:false} 降级面（同 refine AIError 面，脱敏）。"""

    def _boom(**kwargs):
        raise MediaGenError("媒体生成未配置。请先运行: musespiral media config --api-key <KEY>")

    monkeypatch.setattr(media_gen_service, "_generate_image", _boom)
    code, _, raw = _request(server, "POST", "/api/action/generate_image",
                            {"confirm": True, "prompt": "x"})
    assert code == 200
    payload = json.loads(raw)
    assert payload["ok"] is False and "media config" in payload["error"]


def test_http_media_verify_endpoint(server, monkeypatch) -> None:
    """/api/action/media_verify：三态回写（mock 探活）。"""
    _write_media_profile()
    op = FakeOpener()
    op.route("api.siliconflow.cn/v1/models", lambda req: (200, b"{}"))
    monkeypatch.setattr(media_gen_service, "_verify_connection",
                        lambda **kwargs: media_gen.verify_connection(opener=op))
    code, _, raw = _request(server, "POST", "/api/action/media_verify", {"confirm": True})
    assert code == 200
    payload = json.loads(raw)
    assert payload["ok"] is True and payload["health"]["status"] == "ok"
    assert payload["last"]["status"] == "ok"  # 回写快照随响应


def test_http_ai_usage_endpoint_matches_cli(server) -> None:
    """F1：GET /api/ai/usage 与 CLI `musespiral ai usage` 同数（summarize 单一权威）。"""
    from datetime import date

    today = date.today().isoformat()  # 端点与 CLI 都按真实今天开窗，同数才成立
    rows = [
        dict(ts=f"{today}T09:00:00", task="refine", profile="cloud", cache_hit=False, chars_in=900, chars_out=120),
        dict(ts=f"{today}T09:05:00", task="refine", profile="cloud", cache_hit=True, chars_in=900, chars_out=120),
        dict(ts=f"{today}T10:00:00", task="tag_suggest", profile="local", cache_hit=False, chars_in=400, chars_out=60),
        dict(ts="2020-01-01T00:00:00", task="refine", profile="cloud", cache_hit=False, chars_in=1, chars_out=1),  # 窗口外
    ]
    for r in rows:
        usage_mod.append(**r)
    expected = usage_mod.summarize(days=7, today=today)
    # 端点（days 缺省 7）
    code, _, raw = _request(server, "GET", "/api/ai/usage")
    assert code == 200
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["rows"] == expected["rows"] and payload["totals"] == expected["totals"]
    # 窗口与聚合口径：命中不出账、任务×档案分行
    assert payload["totals"] == {"calls": 2, "hits": 1, "chars_in": 1300, "chars_out": 180}
    assert {(r["task"], r["profile"]) for r in payload["rows"]} == {("refine", "cloud"), ("tag_suggest", "local")}
    # CLI 同数（musespiral ai usage 同一 summarize）
    result = CliRunner().invoke(ai, ["usage", "--days", "7"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "调用 2" in result.output and "命中 1" in result.output and "out 180" in result.output
    # days 越界 → 400（与 CLI IntRange 同限）
    code, _, _ = _request(server, "GET", "/api/ai/usage?days=0")
    assert code == 400
    code, _, _ = _request(server, "GET", "/api/ai/usage?days=abc")
    assert code == 400


def test_http_ai_budget_endpoint(server) -> None:
    """F1：GET /api/ai/budget 三层预算读（全局两键+per_task；缺省兜底）。"""
    from musespiral.infra.ai import budget as budget_mod

    code, _, raw = _request(server, "GET", "/api/ai/budget")
    assert code == 200
    payload = json.loads(raw)
    assert payload == {
        "ok": True,
        "budget": {
            "daily_calls": budget_mod.DEFAULT_DAILY_CALLS,
            "daily_out_chars": budget_mod.DEFAULT_DAILY_OUT_CHARS,
            "per_task": {},
        },
    }
    budget_mod.save({"daily_calls": 88, "per_task": {"compose": {"daily_calls": 9}}})
    _, _, raw = _request(server, "GET", "/api/ai/budget")
    payload = json.loads(raw)
    assert payload["budget"]["daily_calls"] == 88
    assert payload["budget"]["per_task"]["compose"] == {"daily_calls": 9}


# ── CLI（media config/list/verify；canary 零回显）──────────


def test_cli_media_config_routes_and_masks_key() -> None:
    runner = CliRunner()
    result = runner.invoke(
        media, ["config", "--api-key", CANARY, "--model", "FLUX.1-schnell"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert CANARY not in result.output  # CLI 回显零密钥
    from musespiral import config
    from musespiral.infra.ai.profiles import load_media_config, media_configured

    assert media_configured() is True
    cfg = load_media_config()
    assert cfg["base_url"] == "https://api.siliconflow.cn/v1"
    assert cfg["model"] == "FLUX.1-schnell"
    assert cfg["api_key"] == CANARY  # 存储面（ai.json）持有是设计内
    assert not Path(result.output and config.home_dir() / "media_health.json").exists()


def test_cli_media_config_isolated_from_chat_namespace() -> None:
    """评审修正核心断言：media config 落 media_profiles 独立命名空间——
    chat 域 default/profiles/routing 零动，image_generate 不进 TASKS。"""
    from musespiral.infra.ai import profiles as profiles_mod

    profiles_mod.save_ai_profile("cloud", "https://chat.example/v1", "chat-key", "chat-model")
    runner = CliRunner()
    result = runner.invoke(
        media, ["config", "--api-key", CANARY], catch_exceptions=False
    )
    assert result.exit_code == 0
    # chat 域原样（default 仍指向 cloud，未被 media config 劫持）
    assert profiles_mod.load_ai_config() == {
        "base_url": "https://chat.example/v1", "api_key": "chat-key", "model": "chat-model"
    }
    # media 域独立生效
    assert profiles_mod.load_media_config()["model"] == "FLUX.1-schnell"
    # TASKS 无 media 键（ai check 不会用 chat 端点探测图像任务）
    assert "image_generate" not in profiles_mod.TASKS
    # 反向：chat 写回也不擦 media 域（双命名空间互不擦写）
    profiles_mod.set_route("title", "cloud")
    assert profiles_mod.load_media_config()["api_key"] == CANARY


def test_cli_media_list_masks_key() -> None:
    _write_media_profile()
    result = CliRunner().invoke(media, ["list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert CANARY not in result.output
    assert "siliconflow" in result.output
    assert "…" in result.output or "..." in result.output  # key 掩码形态


def test_cli_media_verify_exit_code(monkeypatch) -> None:
    _write_media_profile()
    op = FakeOpener()
    op.route("api.siliconflow.cn/v1/models", lambda req: (200, b"{}"))
    real = media_gen.verify_connection  # 先存原函数再替换，防自递归
    monkeypatch.setattr(media_gen, "verify_connection", lambda **kwargs: real(opener=op))
    result = CliRunner().invoke(media, ["verify"], catch_exceptions=False)
    assert result.exit_code == 0 and "ok" in result.output


def test_canary_never_leaks_through_errors_or_disk() -> None:
    """canary 红线终检：失败错误文本/回写文件/降级响应全零命中。"""
    _write_media_profile()
    leaks: list[str] = []
    # 网络失败（500）→ 错误文本零 canary
    op = FakeOpener()
    op.route("images/generations", lambda req: (500, b'{"error":"upstream boom"}'))
    with pytest.raises(MediaGenError) as exc:
        media_gen.generate_image(prompt="x", opener=op)
    leaks.append(str(exc.value))
    # 白名单拒错误
    _write_media_profile(base_url="https://api.deepseek.com/v1", name="bad")
    with pytest.raises(MediaGenError) as exc2:
        media_gen.generate_image(prompt="x", opener=FakeOpener())
    leaks.append(str(exc2.value))
    # 磁盘面：预览 sidecar / 素材库行 / 健康快照
    _write_media_profile()
    pre = media_gen_service.preview("终检", opener=_ok_opener(_png_bytes()))
    media_gen_service.adopt(pre["preview_id"])
    from musespiral import config

    for p in config.home_dir().rglob("*"):
        if p.is_file() and p.name != "ai.json":  # ai.json 是存储面（设计内持有）
            leaks.append(p.read_text(encoding="utf-8", errors="ignore"))
    assert all(CANARY not in text for text in leaks)


# ── E1 pipeline 触发端点（POST /api/action/pipeline）────────


def test_http_pipeline_endpoint_shape_gate(server) -> None:
    """形状面：缺 confirm → 400（全局闸门）；article 缺失/不存在 → 400/404；
    skip 非字符串数组 → 400。"""
    code, _, _ = _request(server, "POST", "/api/action/pipeline", {"article": "sample_article.md"})
    assert code == 400  # 缺 confirm
    code, _, _ = _request(server, "POST", "/api/action/pipeline", {"confirm": True})
    assert code == 400  # 缺 article
    code, _, _ = _request(server, "POST", "/api/action/pipeline", {"confirm": True, "article": "nope.md"})
    assert code == 404  # 文件不存在
    code, _, _ = _request(
        server, "POST", "/api/action/pipeline",
        {"confirm": True, "article": "sample_article.md", "skip": "title"},
    )
    assert code == 400  # skip 必须是数组


def test_http_pipeline_endpoint_degrades_without_service(server) -> None:
    """pipeline_service 未落地（并行产出）→ 200 {ok:false} 可读降级，不 4xx/5xx。"""
    try:
        import musespiral.services.pipeline_service  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pipeline_service 已落地——降级分支由集成轮真链路测试覆盖")
    code, _, raw = _request(
        server, "POST", "/api/action/pipeline",
        {"confirm": True, "article": "sample_article.md"},
    )
    assert code == 200
    payload = json.loads(raw)
    assert payload["ok"] is False and "pipeline_service" in payload["error"]


def test_http_pipeline_endpoint_submits_job(server, studio_root, monkeypatch) -> None:
    """接缝全链：伪 pipeline_service 注入 → 端点经 runner.submit 建
    kind=pipeline job 并回 job_id（agent/topic_radar submit 先例同形）。"""
    import sys
    import types

    from musespiral import services as services_pkg
    from musespiral.jobs.store import jobs_path

    calls: dict = {}

    def _submit_to_runner(runner, article, *, root=None, skip=None):
        calls["article"], calls["root"], calls["skip"] = article, root, skip
        return runner.submit(
            "pipeline",
            {"article": article, "skip": skip or []},
            lambda job_id: {"steps": [{"step": "check", "status": "ok"}]},
        )

    fake = types.ModuleType("musespiral.services.pipeline_service")
    fake.submit_to_runner = _submit_to_runner
    # 双保险注入：sys.modules 命中 + 父包属性命中（from-import 两解析路径）
    monkeypatch.setitem(sys.modules, "musespiral.services.pipeline_service", fake)
    monkeypatch.setattr(services_pkg, "pipeline_service", fake, raising=False)
    code, _, raw = _request(
        server, "POST", "/api/action/pipeline",
        {"confirm": True, "article": "sample_article.md", "skip": ["cover"]},
    )
    assert code == 200
    payload = json.loads(raw)
    assert payload["ok"] is True and payload["job_id"]
    assert calls["article"] == "sample_article.md"
    assert calls["skip"] == ["cover"]
    assert calls["root"] == studio_root.resolve()  # ctx.root 透传给服务层
    # job 落盘形状：kind=pipeline（KINDS 14→15 的消费证）
    rows = [json.loads(line) for line in jobs_path().read_text(encoding="utf-8").splitlines()]
    row = next(r for r in rows if r["id"] == payload["job_id"])
    assert row["kind"] == "pipeline"


# ── 集成轮 Studio 冒烟（make_server 夹具 + mock；2026-11-18 组合批收口）──


def test_smoke_generate_adopt_lands_full_material_shape(server, monkeypatch) -> None:
    """冒烟①：HTTP 面生成→采纳——materials.jsonl 落库形状全对（裁决 1/4）。

    服务层形状已有 test_adopt_full_shape_with_text_chunk 锚定；本用例证
    「经 /api/action/* 端点走完整链路后落库行同形」——HTTP 壳不丢字段、
    素材文件真落盘且带 tEXt 四键、GET /api/materials 读面可见。
    """
    from musespiral import config

    png = _png_bytes()
    monkeypatch.setattr(media_gen_service, "_generate_image", _fake_generate(png))
    code, _, raw = _request(server, "POST", "/api/action/generate_image",
                            {"confirm": True, "prompt": "青黛山影", "size": "768x768"})
    assert code == 200
    pre = json.loads(raw)
    assert pre["ok"] is True and pre["provider"] == "siliconflow"

    code, _, raw = _request(server, "POST", "/api/action/adopt_generated",
                            {"confirm": True, "preview_id": pre["preview_id"], "title": "山影题字"})
    assert code == 200
    adopted = json.loads(raw)
    assert adopted["ok"] is True and adopted["dedup"] is False

    # 落库形状：materials.jsonl 行与采纳响应同 id 同形（HTTP 面真落库证）
    rows = [json.loads(x) for x in _materials_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    row = next(r for r in rows if r["id"] == adopted["id"])
    assert row["type"] == "image" and row["license"] == "AI-GENERATED"
    assert row["origin"]["source_id"] == "ai_gen"
    assert row["origin"]["uri"].startswith("generate://sha256:")
    assert row["provenance"]["chain"] == [{"from_id": "ai", "op": "generate"}]
    assert row["triage"]["enabled"] is False  # 裁决 4：默认不进发现页推荐
    meta = row["payload"]["meta"]
    assert meta["ai_generated"] is True and meta["prompt"] == "青黛山影"
    assert meta["provider"] == "siliconflow" and meta["model"] == "FLUX.1-schnell"

    # 素材文件真落盘：内容寻址 + PNG tEXt 四键（GB 45438-2025）
    stored = config.home_dir() / row["payload"]["local_path"]
    assert stored.is_file() and stored.suffix == ".png"
    with Image.open(stored) as img:
        for key in ("prompt", "provider", "model", "generated_at"):
            assert img.info.get(key), f"tEXt 缺键: {key}"

    # 读面可见：GET /api/materials 能查到该行（发现页收编的读侧证）
    code, _, raw = _request(server, "GET", "/api/materials?limit=200")
    assert code == 200
    listing = json.loads(raw)
    items = listing.get("items") or listing.get("materials") or []
    assert any(m.get("id") == adopted["id"] for m in items)


class _FakeChatResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _setup_pipeline_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """假 AI 档案 + 按 prompt 关键词分发回包（test_pipeline_service 同款接缝）。"""
    from musespiral.infra.ai import client as ai_mod

    home = Path(os.environ["MUSESPIRAL_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "ai.json").write_text(
        json.dumps({
            "default": "fake",
            "profiles": {"fake": {"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}},
            "routing": {},
        }),
        encoding="utf-8",
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        prompt = " ".join(str(m.get("content", "")) for m in (json or {}).get("messages", []))
        if "备选标题" in prompt:  # title 步（core/quality/title._PROMPT）
            content = '["标题候选甲", "标题候选乙", "标题候选丙"]'
        elif "小红书" in prompt:
            content = "缓存优化真的别乱来 ✅\n\n先压测再优化。\n\n话题：#缓存 #性能"
        elif "视频号" in prompt:
            content = ("【开场钩子】缓存优化三步走。\n【要点1】先压测。\n"
                       "【要点2】过期时间是关键。\n【收尾引导】关注我。\n"
                       "封面标题：缓存三步走\n描述：缓存优化实录 #缓存")
        elif "抖音" in prompt:
            content = ("【开场钩子】缓存优化。\n【要点1】先压测。\n【收尾引导】关注。\n"
                       "封面标题：缓存三步走\n描述：缓存 #缓存")
        else:  # zhihu
            content = "缓存优化实录的知乎版正文，讲清三步走的方法论与实践。"
        return _FakeChatResp(content)

    monkeypatch.setattr(
        ai_mod, "_post_chat_stream",
        lambda u, h, b, to: ai_mod.adapt_mock(fake_post, u, h, b, to),
    )


def _write_golden_article(root: Path) -> str:
    """夹具文章（test_pipeline_service._write_article 同形状：check 0 FAIL 可通）。"""
    Image.new("RGB", (900, 383), "white").save(root / "cover.png")  # check 前置：封面存在
    (root / "article.md").write_text(
        "---\n"
        "title: 旧标题\n"
        "cover: cover.png\n"
        "digest: 这是摘要。\n"
        "tags: [性能, 缓存]\n"
        "---\n\n"
        "# 缓存优化实录\n\n"
        "缓存是提升系统性能的第一道防线。\n\n"
        "## 先压测再优化\n\n不做压测就上缓存，容易优化没人用的路径。\n\n"
        "## 过期时间是关键\n\nTTL 从 5 分钟调到 30 分钟后命中率翻倍。\n",
        encoding="utf-8",
    )
    return "article.md"


def test_smoke_pipeline_fixture_article_runs_through(server, studio_root, monkeypatch) -> None:
    """冒烟②：E1 触发端点 → 真 pipeline_service → job 后台执行 → done。

    全链走 make_server 真服务（非伪模块注入）：POST /api/action/pipeline
    → 共享 runner 后台线程 → GET /api/jobs/<id> 轮询（与前端 waitForJob
    同面）→ done 后 result.steps 五步全 ok、九件产物逐一存在、title 步
    经 mock AI 写回首条候选。
    """
    import frontmatter
    from musespiral.studio.actions import _reset_runner

    _reset_runner()  # 共享 runner 干净起止：mock 线程池不外泄到其它用例
    try:
        _setup_pipeline_ai(monkeypatch)
        # 无浏览器环境 → cover title-card 走 Pillow 兜底（test_cover 同款）
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        article = _write_golden_article(studio_root)

        code, _, raw = _request(server, "POST", "/api/action/pipeline",
                                {"confirm": True, "article": article})
        assert code == 200
        job_id = json.loads(raw)["job_id"]
        assert job_id

        deadline = time.time() + 30.0
        job = None
        while time.time() < deadline:
            code, _, raw = _request(server, "GET", f"/api/jobs/{job_id}")
            assert code == 200
            job = json.loads(raw)
            if job.get("status") in ("done", "failed"):
                break
            time.sleep(0.05)
        assert job is not None and job["status"] == "done", f"job 未跑通: {job}"
        assert job["kind"] == "pipeline"

        result = job["result"]
        assert [s["step"] for s in result["steps"]] == [
            "check", "title", "cover", "adapt", "pack",
        ]
        assert all(s["status"] == "ok" for s in result["steps"])
        assert result["steps_done"] == 5 and "failed_step" not in result
        assert len(result["artifacts"]) == 9  # cover 1 + 变体 4 + 物料包 4
        for rel in result["artifacts"]:
            assert (studio_root / rel).exists(), f"产物缺失: {rel}"

        # title 步经 mock AI 写回首条候选（旧版入版本池）
        fm = frontmatter.load(studio_root / article)
        assert fm.metadata["title"] == "标题候选甲"
        assert list(studio_root.glob("article.md.bak.*")), "写回应入版本池"
    finally:
        _reset_runner()
