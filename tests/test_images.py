"""R5 图床抽象单测：local 恒等 / wechat 迁移行为 / github Contents API / resolve 次序。

全部 tmp_path + MUSESPIRAL_HOME 隔离、monkeypatch 全程 mock requests——本轮所有
真实网络路径都必须 mock（正典轮次三验收线），零真实请求。CLI imagehost 命令
用 CliRunner 冒烟（命令未注册进 cli.py——注册归集成阶段，直接调命令组对象，
与 test_jobs 同法）。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.imagehost_cmds import imagehost_cmd
from musespiral.infra import images
from musespiral.infra.images import (
    CAP_CONTENT,
    CAP_COVER,
    GitHubHost,
    ImageHost,
    LocalHost,
    imagehost_path,
    load_host_config,
    resolve_host,
)
from musespiral.infra.wechat.uploader import WeChatHost
from musespiral.infra.wechat import uploader as wechat_mod
from musespiral.infra.wechat.uploader import ImageUploader, UploadError


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def img(tmp_path: Path) -> Path:
    p = tmp_path / "pic.png"
    p.write_bytes(b"fake-png-bytes-for-tests")
    return p


class _Resp:
    """替身 requests.Response：github/wechat 用到的 .json()/.status_code/.text。"""

    def __init__(self, payload: dict | None = None, status: int = 200, text: str | None = None) -> None:
        self._payload = payload or {}
        self.status_code = status
        self.text = text if text is not None else json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


def _write_github_config(**overrides) -> None:
    """写一份完整的 github 图床配置（token=TEST 供泄漏断言）。"""
    gh = {"owner": "wagic", "repo": "pics", "token": "ghp_TESTSECRET1234"}
    gh.update(overrides)
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text(
        json.dumps({"default": "github", "hosts": {"github": gh}}, ensure_ascii=False),
        encoding="utf-8",
    )


# ── local：恒等返回原路径 ─────────────────────────────────────


def test_local_identity(img: Path) -> None:
    host = LocalHost()
    assert host.name == "local"
    assert host.capabilities == frozenset({CAP_CONTENT})
    assert not host.supports(CAP_COVER)

    url, meta = host.upload(img)
    assert url == str(img)  # 恒等：原路径原样返回
    assert meta == {"host": "local", "path": str(img)}

    rel = "assets/a.png"
    url2, _ = host.upload(rel)
    assert url2 == rel  # 字符串入参亦恒等（无路径解析、无校验、零网络）


# ── wechat：ImageUploader 迁移行为保持（mock requests.post） ──


def _patch_wechat_post(monkeypatch: pytest.MonkeyPatch, handler) -> list:
    calls: list = []
    monkeypatch.setattr(wechat_mod.requests, "post", lambda *a, **k: handler(calls, a, k))
    return calls


def test_wechat_content_image_upload_and_cache(tmp_path: Path, img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    article = tmp_path / "a.md"
    article.write_text("# t\n", encoding="utf-8")

    def handler(calls, args, kwargs):
        calls.append((args, kwargs))
        return _Resp({"url": "https://mmbiz.qpic.cn/abc123"})

    calls = _patch_wechat_post(monkeypatch, handler)
    up = ImageUploader("TOK", article)

    url = up.upload_content_image(img.name)  # 相对文章目录解析（迁移前同口径）
    assert url == "https://mmbiz.qpic.cn/abc123"
    assert len(calls) == 1
    assert calls[0][0][0].endswith("/cgi-bin/media/uploadimg")
    assert calls[0][1]["params"]["access_token"] == "TOK"

    # 同内容二次上传走 meta.json 缓存，不再发请求
    url2 = up.upload_content_image(img.name)
    assert url2 == url and len(calls) == 1
    meta_file = tmp_path / "a.musespiral" / "meta.json"
    assert meta_file.is_file()  # 缓存落盘位置不变（<stem>.musespiral/meta.json）


def test_wechat_cover_upload(tmp_path: Path, img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    article = tmp_path / "a.md"
    article.write_text("# t\n", encoding="utf-8")

    def handler(calls, args, kwargs):
        calls.append((args, kwargs))
        return _Resp({"media_id": "MEDIA_1"})

    calls = _patch_wechat_post(monkeypatch, handler)
    up = ImageUploader("TOK", article)

    assert up.upload_cover(img.name) == "MEDIA_1"
    assert calls[0][0][0].endswith("/cgi-bin/material/add_material")
    assert calls[0][1]["params"]["type"] == "image"


def test_wechat_upload_failure_readable(tmp_path: Path, img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    article = tmp_path / "a.md"
    article.write_text("# t\n", encoding="utf-8")
    _patch_wechat_post(monkeypatch, lambda calls, a, k: _Resp({"errcode": 40001, "errmsg": "invalid credential"}))

    with pytest.raises(UploadError, match="正文图片上传失败"):
        ImageUploader("BAD", article).upload_content_image(img.name)


def test_wechat_host_adapter(tmp_path: Path, img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """WeChatHost：能力声明 content+cover；upload 委托正文图；cover 返回 media_id。"""
    article = tmp_path / "a.md"
    article.write_text("# t\n", encoding="utf-8")

    def handler(calls, args, kwargs):
        endpoint = args[0]
        if "uploadimg" in endpoint:
            return _Resp({"url": "https://mmbiz.qpic.cn/x"})
        return _Resp({"media_id": "MEDIA_9"})

    _patch_wechat_post(monkeypatch, handler)
    host = WeChatHost("TOK", article)
    assert isinstance(host, ImageHost)
    assert host.name == "wechat"
    assert host.supports(CAP_CONTENT) and host.supports(CAP_COVER)

    url, meta = host.upload(img)
    assert url == "https://mmbiz.qpic.cn/x"
    assert meta["host"] == "wechat"
    assert host.upload_cover(img.name) == "MEDIA_9"


def test_wechat_missing_image_readable(tmp_path: Path) -> None:
    article = tmp_path / "a.md"
    article.write_text("# t\n", encoding="utf-8")
    with pytest.raises(UploadError, match="图片不存在"):
        ImageUploader("TOK", article).upload_content_image("nope.png")


# ── github：Contents API 全 mock（成功/422 幂等/401/token 不泄漏） ──


def _patch_github(monkeypatch: pytest.MonkeyPatch, put_handler, get_handler=None) -> list:
    calls: list = []
    monkeypatch.setattr(images.github.requests, "put", lambda *a, **k: put_handler(calls, a, k))
    if get_handler is not None:
        monkeypatch.setattr(images.github.requests, "get", lambda *a, **k: get_handler(calls, a, k))
    return calls


def _remote_path(p: Path) -> str:
    digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return f"musespiral/{digest}-{p.name}"


def test_github_upload_success_default_url(img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功路径：PUT 直连 api.github.com；默认下载 URL 为 raw 直连（V3：不强制镜像）。"""
    def put(calls, args, kwargs):
        calls.append(("PUT", args, kwargs))
        return _Resp({"content": {"sha": "S1"}, "commit": {"sha": "C1"}}, status=201)

    calls = _patch_github(monkeypatch, put)
    host = GitHubHost("wagic", "pics", "ghp_TESTSECRET1234")
    url, meta = host.upload(img)

    remote = _remote_path(img)
    assert url == f"https://raw.githubusercontent.com/wagic/pics/main/{remote}"
    assert meta == {"host": "github", "path": remote, "sha": "S1", "commit": "C1", "created": True}

    method, args, kwargs = calls[0]
    assert args[0] == f"https://api.github.com/repos/wagic/pics/contents/{remote}"
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_TESTSECRET1234"
    assert "ghp_TESTSECRET1234" not in args[0]  # token 只走头，绝不进 URL
    body = kwargs["json"]
    assert body["content"] == base64.b64encode(img.read_bytes()).decode("ascii")
    assert body["branch"] == "main"  # 缺省分支 main
    assert img.name in body["message"]  # commit message 带文件名


def test_github_upload_branch_and_cdn(img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """branch 进 PUT 体；cdn 模板含 {path} 占位时接管下载 URL（如 ghfast 加速）。"""
    cdn = "https://ghfast.top/https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

    def put(calls, args, kwargs):
        return _Resp({"content": {"sha": "S"}, "commit": {"sha": "C"}}, status=200)

    _patch_github(monkeypatch, put)
    host = GitHubHost("wagic", "pics", "T", branch="assets", cdn=cdn)
    url, meta = host.upload(img)

    remote = _remote_path(img)
    assert url == f"https://ghfast.top/https://raw.githubusercontent.com/wagic/pics/assets/{remote}"
    assert meta["created"] is False  # 200 = 更新既有文件，201 = 新建


def test_github_upload_422_conflict_updates_idempotent(img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件已存在（422 sha 冲突）→ GET 拉 sha → 带 sha 覆盖更新 → 返回同一 URL。"""
    def put(calls, args, kwargs):
        calls.append(("PUT", args, kwargs))
        if len(calls) == 1:
            return _Resp({"message": '"sha" wasn\'t supplied'}, status=422)
        return _Resp({"content": {"sha": "S2"}, "commit": {"sha": "C2"}}, status=200)

    def get(calls, args, kwargs):
        calls.append(("GET", args, kwargs))
        assert args[0].endswith(f"/contents/{_remote_path(img)}")
        assert kwargs["params"] == {"ref": "main"}
        return _Resp({"sha": "REMOTE_SHA"}, status=200)

    calls = _patch_github(monkeypatch, put, get)
    host = GitHubHost("wagic", "pics", "T")
    url, meta = host.upload(img)

    puts = [c for c in calls if c[0] == "PUT"]
    gets = [c for c in calls if c[0] == "GET"]
    assert len(puts) == 2 and len(gets) == 1
    assert puts[1][2]["json"]["sha"] == "REMOTE_SHA"  # 第二次 PUT 带远端 sha
    assert url == f"https://raw.githubusercontent.com/wagic/pics/main/{_remote_path(img)}"
    assert meta["created"] is False


def test_github_upload_401_readable_no_token_leak(img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """401：可读错误（含状态码与 token 权限提示），token 原值绝不进错误文本。"""
    def put(calls, args, kwargs):
        return _Resp({"message": "Bad credentials"}, status=401, text="Bad credentials")

    _patch_github(monkeypatch, put)
    host = GitHubHost("wagic", "pics", "ghp_TESTSECRET1234")
    with pytest.raises(UploadError) as ei:
        host.upload(img)
    msg = str(ei.value)
    assert "401" in msg and "token" in msg  # 可读：状态码 + 归因提示
    assert "ghp_TESTSECRET1234" not in msg  # 红线：token 不泄漏


def test_github_error_body_masked_and_truncated(img: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4xx 响应体入错误前打码（token= 形态与裸 token 原值）并截断。"""
    body = "detail: token=ghp_TESTSECRET1234 raw ghp_TESTSECRET1234 " + "x" * 500

    def put(calls, args, kwargs):
        return _Resp({"message": "fail"}, status=422, text=body)

    def get(calls, args, kwargs):
        return _Resp({"message": "fail"}, status=404, text=body)

    _patch_github(monkeypatch, put, get)
    host = GitHubHost("wagic", "pics", "ghp_TESTSECRET1234")
    with pytest.raises(UploadError) as ei:
        host.upload(img)  # 422 → 拉 sha 也 404 → 报获取失败（第二次错误路径）
    msg = str(ei.value)
    assert "ghp_TESTSECRET1234" not in msg
    assert "token=***" in msg
    assert len(msg) < 400  # 截断生效（错误文本不携带 500 字符长体）
    assert "404" in msg  # 可读：定位到「获取文件 sha」一步


def test_github_missing_image_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_github(monkeypatch, lambda calls, a, k: _Resp({}, status=500))
    host = GitHubHost("wagic", "pics", "T")
    with pytest.raises(UploadError, match="图片不存在"):
        host.upload(Path("/nonexistent/zz.png"))


def test_github_capabilities() -> None:
    host = GitHubHost("o", "r", "t")
    assert host.name == "github"
    assert host.supports(CAP_CONTENT) and not host.supports(CAP_COVER)


# ── resolve_host：frontmatter 覆盖 > 配置 default > local ─────


def test_resolve_default_local_without_config() -> None:
    assert isinstance(resolve_host(), LocalHost)
    assert isinstance(resolve_host({}), LocalHost)
    assert isinstance(resolve_host({"title": "无 imagehost 键"}), LocalHost)


def test_resolve_config_default_github() -> None:
    _write_github_config()
    host = resolve_host()
    assert isinstance(host, GitHubHost)
    assert (host.owner, host.repo) == ("wagic", "pics")


def test_resolve_frontmatter_overrides_config_default() -> None:
    _write_github_config()  # default=github，但文章指名 local
    assert isinstance(resolve_host({"imagehost": "local"}), LocalHost)


def test_resolve_frontmatter_github_without_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置无 default（或 default=local）时，frontmatter imagehost: github 仍可启用。"""
    gh = {"owner": "o", "repo": "r", "token": "t"}
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text(json.dumps({"hosts": {"github": gh}}), encoding="utf-8")
    assert isinstance(resolve_host({"imagehost": "github"}), GitHubHost)
    assert isinstance(resolve_host(), LocalHost)  # 无 default → local


def test_resolve_frontmatter_empty_string_ignored() -> None:
    _write_github_config()
    assert isinstance(resolve_host({"imagehost": "  "}), GitHubHost)  # 空白不视为覆盖


def test_resolve_github_incomplete_config_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text(
        json.dumps({"default": "github", "hosts": {"github": {"owner": "o"}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="github 图床未配置完整.*repo.*token"):
        resolve_host()


def test_resolve_unknown_and_wechat_names() -> None:
    with pytest.raises(ValueError, match="未知图床: qiniu"):
        resolve_host({"imagehost": "qiniu"})
    with pytest.raises(ValueError, match="publish 流程内建"):
        resolve_host({"imagehost": "wechat"})


def test_resolve_corrupt_config_readable() -> None:
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="图床配置损坏"):
        resolve_host()


# ── imagehost CLI：list / config（CliRunner，未注册——直接调命令组） ──


def test_cli_config_writes_json_no_token_echo() -> None:
    result = CliRunner().invoke(
        imagehost_cmd,
        ["config", "--github-owner", "wagic", "--github-repo", "pics", "--github-token", "ghp_TESTSECRET1234"],
    )
    assert result.exit_code == 0, result.output
    assert "ghp_TESTSECRET1234" not in result.output  # token 不回显
    assert "token" in result.output  # 但明说不回显了什么

    cfg = load_host_config()
    assert cfg["hosts"]["github"] == {
        "owner": "wagic",
        "repo": "pics",
        "token": "ghp_TESTSECRET1234",
    }
    assert "default" not in cfg  # 只写 host，不动 default（显式 --default 才写）


def test_cli_config_partial_github_rejected() -> None:
    result = CliRunner().invoke(imagehost_cmd, ["config", "--github-owner", "wagic"])
    assert result.exit_code != 0
    assert "缺 repo,token" in result.output


def test_cli_config_default_github_requires_complete() -> None:
    result = CliRunner().invoke(imagehost_cmd, ["config", "--default", "github"])
    assert result.exit_code != 0
    assert "缺 owner,repo,token" in result.output

    # 同条命令给齐三项 → default 写入成功
    result = CliRunner().invoke(
        imagehost_cmd,
        [
            "config",
            "--github-owner", "wagic",
            "--github-repo", "pics",
            "--github-token", "ghp_TESTSECRET1234",
            "--default", "github",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_host_config()["default"] == "github"


def test_cli_config_merges_existing_fields() -> None:
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text(
        json.dumps({"default": "local", "hosts": {"github": {
            "owner": "old", "repo": "old", "token": "old", "branch": "assets", "cdn": "https://ghfast.top/x/{path}",
        }}}),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        imagehost_cmd,
        ["config", "--github-owner", "new", "--github-repo", "new", "--github-token", "newtok"],
    )
    assert result.exit_code == 0, result.output
    gh = load_host_config()["hosts"]["github"]
    assert (gh["owner"], gh["repo"], gh["token"]) == ("new", "new", "newtok")
    assert gh["branch"] == "assets"  # 未给的键保留既有值
    assert gh["cdn"].startswith("https://ghfast.top")
    assert load_host_config()["default"] == "local"  # default 不被隐式改写


def test_cli_list_masks_token() -> None:
    imagehost_path().parent.mkdir(parents=True, exist_ok=True)
    imagehost_path().write_text(
        json.dumps({"default": "github", "hosts": {"github": {
            "owner": "wagic", "repo": "pics", "token": "ghp_TESTSECRET1234", "branch": "assets",
        }}}),
        encoding="utf-8",
    )
    result = CliRunner().invoke(imagehost_cmd, ["list"])
    assert result.exit_code == 0, result.output
    assert "默认图床: github" in result.output
    assert "owner=wagic" in result.output and "branch=assets" in result.output
    assert "ghp_…1234" in result.output  # 掩码形态：头 4 + … + 尾 4
    assert "ghp_TESTSECRET1234" not in result.output  # 全值绝不出现


def test_cli_list_without_config() -> None:
    result = CliRunner().invoke(imagehost_cmd, ["list"])
    assert result.exit_code == 0, result.output
    assert "默认图床: local（缺省）" in result.output
    assert "未配置" in result.output
    assert "local: 恒等图床" in result.output


def test_cli_config_noop_rejected() -> None:
    result = CliRunner().invoke(imagehost_cmd, ["config"])
    assert result.exit_code != 0
    assert "至少给一项" in result.output
