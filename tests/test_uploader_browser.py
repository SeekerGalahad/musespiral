"""F16 浏览器上传器 — dry-run / cookie 存取 / 选择器校验 / 红线结构断言。

全部 tmp_path + MUSESPIRAL_HOME 隔离，不起浏览器不联网：playwright 未安装是本
环境常态，BrowserNotInstalled 用 sys.modules 注入 None 确定性复现（装了
playwright 的机器结论相同）；真机路径（扫码/填表/高亮）留用户（§十六）。

F37 追加：平台集对拍（flows vs registry manual_pack，registry 单一事实源）
与 publish_flow 会话收尾回执注入点（on_delivered 正常结束调用/异常不调用）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from musespiral import browser
from musespiral.platforms import registry
from musespiral.services.pack_service import build_pack
from musespiral.browser import cookies
from musespiral.browser.flows import PLATFORMS, _FLOWS, dry_run, get_flow, publish_flow
from musespiral.browser.flows.base import (
    REQUIRED_SELECTOR_KEYS,
    load_selectors,
    read_pack,
)

# ── 物料包 fixture（与 pack._payload_md 头部格式一致；部分平台不在
#    build_pack 平台表内，手写同格式 payload） ─────────────────────


def _make_pack(
    pack_dir: Path,
    *,
    title: str = "缓存优化三步走",
    desc: str = "三步搞定缓存优化",
    tags: tuple[str, ...] = ("性能", "缓存"),
    body: str = "这是正文第一段。\n\n这是正文第二段。",
    cover: bool = True,
) -> Path:
    pack_dir.mkdir(parents=True, exist_ok=True)
    topics = " ".join(f"#{t}" for t in tags) if tags else "无"
    header = (
        "> 【逐字段对照】platform: demo（上传步骤见 README.md，各字段取材如下）\n"
        ">\n"
        f"> - 标题：{title}\n"
        f"> - 描述：{desc}\n"
        f"> - 话题：{topics}\n"
        "> - 正文：对照说明以下的全部内容（内容来源：测试）\n"
        "\n---\n"
    )
    (pack_dir / "payload.md").write_text(header + body + "\n", encoding="utf-8")
    (pack_dir / "README.md").write_text("# 上传指引\n", encoding="utf-8")
    (pack_dir / "checks.txt").write_text("ok 标题字数: 7/16\n", encoding="utf-8")
    if cover:
        cdir = pack_dir / "cover"
        cdir.mkdir()
        (cdir / "cover.png").write_bytes(b"fake-cover-bytes")
    return pack_dir


# ── dry-run：四平台步骤清单与选择器校验 ─────────────────────


def test_dry_run_four_platforms_full_steps(tmp_path: Path) -> None:
    for platform in PLATFORMS:
        pack = _make_pack(tmp_path / f"demo.{platform}.pack")
        steps = dry_run(platform, pack)
        assert isinstance(steps, list) and all(isinstance(s, str) for s in steps)
        text = "\n".join(steps)

        selectors = load_selectors(platform)
        assert steps[0].startswith(selectors["name"])  # 平台名抬头
        assert selectors["entry_url"] in text  # 入口 URL
        assert "缓存优化三步走" in text  # 标题填写值
        assert "性能" in text and "缓存" in text  # 话题填写值
        assert "cover.png" in text  # 封面文件
        for key in ("title_input", "desc_input", "topic_input", "cover_input"):
            assert selectors[key] in text  # 逐字段选择器名
        assert selectors["publish_button"] in text  # 待人工点击项
        assert "【人工】" in text and "永不点击" in text  # 红线①文案
        assert "选择器校验通过" in text  # YAML 完整性校验反馈


def test_dry_run_desc_source_per_platform(tmp_path: Path) -> None:
    """desc_source 口径：channels 填头部描述行，其余平台正文即描述/文案。"""
    pack = _make_pack(tmp_path / "demo.pack")
    channels_text = "\n".join(dry_run("channels", pack))
    assert "三步搞定缓存优化" in channels_text  # 头部「描述」行
    assert "这是正文第一段" not in channels_text
    for platform in ("xhs", "douyin", "zhihu", "juejin", "bilibili"):
        text = "\n".join(dry_run(platform, pack))
        assert "这是正文第一段" in text, platform  # 正文进描述/文案框
        assert "三步搞定缓存优化" not in text, platform  # 头部描述行不进文案框


def test_dry_run_reads_real_build_pack(tmp_path: Path) -> None:
    """read_pack 与 build_pack 产出格式对拍（防头部格式漂移）。"""
    article = tmp_path / "demo.md"
    article.write_text(
        "---\ntitle: 一篇足够长的测试标题\ncover: cover.png\ntags: [AI, 写作]\n"
        "digest: 这是摘要。\n---\n\n这是文章正文第一段。\n\n这是文章正文第二段。\n",
        encoding="utf-8",
    )
    (tmp_path / "cover.png").write_bytes(b"fake-cover-bytes")
    pack_dir = build_pack(article, "channels")

    fields = read_pack(pack_dir)
    assert fields.title == "一篇足够长的测试标题"
    assert fields.desc == "这是摘要。"
    assert fields.tags == ["AI", "写作"]
    assert fields.body.startswith("这是文章正文第一段。")
    assert [c.name for c in fields.covers] == ["cover.png"]

    text = "\n".join(dry_run("channels", pack_dir))
    assert "一篇足够长的测试标题" in text


def test_dry_run_missing_payload_readable_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pack"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="payload.md"):
        dry_run("channels", empty)


def test_dry_run_unknown_platform_rejected(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path / "demo.pack")
    with pytest.raises(ValueError, match="不支持的平台"):
        dry_run("myspace", pack)


# ── 选择器 YAML：内置齐全 + 缺键/缺文件/坏结构报错 ───────────


def test_builtin_selectors_complete_for_all_platforms() -> None:
    for platform in PLATFORMS:
        selectors = load_selectors(platform)
        for key in REQUIRED_SELECTOR_KEYS:
            assert selectors[key].strip(), f"{platform}.{key} 为空"


def test_load_selectors_missing_key_reports(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text(
        "entry_url: https://example.com\npublish_button: 'button'\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="缺键") as ei:
        load_selectors("broken", selectors_dir=tmp_path)
    for key in ("title_input", "desc_input", "topic_input", "cover_input"):
        assert key in str(ei.value)  # 逐个点名缺失键


def test_load_selectors_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        load_selectors("nosuch", selectors_dir=tmp_path)
    (tmp_path / "bad.yaml").write_text("entry_url: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_selectors("bad", selectors_dir=tmp_path)
    (tmp_path / "flat.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="顶层应为映射"):
        load_selectors("flat", selectors_dir=tmp_path)


# ── BrowserNotInstalled：提示可读（安装 extra + chromium + 镜像） ──


def test_browser_not_installed_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules 注入 None → import 确定性抛 ImportError（无论环境装没装 playwright）
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(browser.BrowserNotInstalled) as ei:
        with browser.BrowserSession("channels"):
            pass
    message = str(ei.value)
    assert 'pip install "musespiral[browser]"' in message
    assert "playwright install chromium" in message
    assert "PLAYWRIGHT_DOWNLOAD_HOST" in message  # 境内镜像提示


# ── cookies：存取 roundtrip + tmp_path 隔离（红线②：只落家目录） ──


def test_cookies_roundtrip_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    state = {
        "cookies": [
            {"name": "sid", "value": "abc", "domain": ".example.com", "path": "/"}
        ],
        "origins": [],
    }

    target = cookies.save("xhs", state)

    assert target == tmp_path / "cookies" / "xhs.json"  # 仓外家目录
    assert cookies.has("xhs") and not cookies.has("douyin")
    assert cookies.load("xhs") == state
    assert cookies.list() == ["xhs"]
    assert not list((tmp_path / "cookies").glob("*.tmp"))  # 原子写无残留


def test_cookies_load_missing_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    with pytest.raises(cookies.CookieMissing, match="upload login"):
        cookies.load("channels")


# ── publish_flow：cookie 缺失先于浏览器快败（无 playwright 也可报） ──


def test_publish_flow_cookie_check_before_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 cookie
    pack = _make_pack(tmp_path / "demo.pack")
    # 本环境未装 playwright：若 cookie 校验晚于 BrowserSession，会先抛
    # BrowserNotInstalled——此处断言抛的是 CookieMissing，证明快败顺序
    with pytest.raises(cookies.CookieMissing, match="upload login"):
        publish_flow("channels", pack)


# ── 红线① 结构断言：发布按钮永不点击 ─────────────────────


def test_redline_no_click_on_publish_button() -> None:
    """红线①：flows/ 与 session.py 运行路径不得对发布按钮（乃至任何元素）点击。

    可维护性约定（改代码前先读，与 flows/base.py、session.py 的模块 docstring
    同源）：
    - ① AST 层：flows/*.py 与 session.py 中不得出现任何 `.click()` 调用。
      发布按钮只经 selectors['publish_button'] 定位后 highlight()；这是比
      「不点发布按钮」更强的禁令，堵死变量中转等 AST 难以静态判定的链路。
      若未来确需非发布元素的点击（如展开话题弹层），必须：选择器键显式命名
      （不得叫 publish_button）+ 先修改本测试再改代码，留下评审痕迹。
    - ② 源码行层：包内任何包含 publish_button 的行不得同时含 `.click(`，
      兜底覆盖 ① 扫描范围之外的文件（如 __init__.py / cookies.py）。
    """
    pkg = Path(browser.__file__).resolve().parent

    # ① AST：运行路径（flows/ + session.py）零 .click() 调用
    runtime_files = [pkg / "session.py", *sorted((pkg / "flows").glob("*.py"))]
    for py in runtime_files:
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "click"
            ):
                pytest.fail(
                    f"{py.name}:{node.lineno} 运行路径出现 .click() 调用"
                    "（红线①：发布按钮永不代点；约定见本测试 docstring）"
                )

    # ② 行级：publish_button 与 .click( 不得同现一行
    for py in sorted(pkg.rglob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "publish_button" in line and ".click(" in line:
                pytest.fail(f"{py.name}:{lineno} publish_button 与 .click( 同行（红线①）")


def test_flows_registry_covers_platforms() -> None:
    for platform in PLATFORMS:
        flow_cls = get_flow(platform)
        assert flow_cls.platform == platform
        assert flow_cls is not None
    with pytest.raises(ValueError, match="不支持的平台"):
        get_flow("myspace")


# ── F37 平台集对拍：flows == registry manual_pack（单一事实源） ──


def test_flows_platform_set_matches_registry_manual_pack() -> None:
    """对拍（正典 §二 F37 / §五）：flows 平台集 == registry manual_pack 平台集。

    registry 是单一事实源——漂移以 registry 为准修正 flows（本期判例：
    kuaishou 未在 registry 登记退场、zhihu 已登记补位）。browser 属 infra
    层不能 import platforms，故由测试侧对拍看守。
    """
    manual_pack = {a.name for a in registry.list_platforms() if a.route == "manual_pack"}
    assert manual_pack, "registry manual_pack 为空——对拍退化成恒真，先修注册表"
    assert set(PLATFORMS) == manual_pack
    assert set(_FLOWS) == manual_pack
    assert "kuaishou" not in manual_pack  # 漂移判例锚定（防回潮）


# ── F37 会话收尾回执注入点：正常结束调用 / 异常中断不调用 ──


class _Kb:
    def press(self, key: str) -> None:  # 话题回车确认
        pass


class _El:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    def fill(self, value: str) -> None:
        if self._fail:
            raise RuntimeError("fill 失败（模拟会话中断）")

    def set_input_files(self, files: list[str]) -> None:
        pass

    def highlight(self) -> None:
        pass


class _FakePage:
    def __init__(self, fail: bool) -> None:
        self.keyboard = _Kb()
        self._fail = fail

    def goto(self, url: str) -> None:
        pass

    def locator(self, selector: str) -> _El:
        return _El(self._fail)


class _FakeSession:
    """BrowserSession 替身（不起 playwright）：类属性 fail 控制填表是否中断。"""

    fail = False

    def __init__(self, platform: str, *, headless: bool = False) -> None:
        self.platform = platform
        self.page = _FakePage(type(self).fail)

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_publish_flow_receipt_callback_on_normal_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话正常结束（填表完成+人工点发后返回）→ on_delivered 恰好调用一次。"""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(cookies, "load", lambda p: {})
    import musespiral.browser.flows as flows_module

    monkeypatch.setattr(flows_module, "BrowserSession", _FakeSession)
    pack = _make_pack(tmp_path / "demo.channels.pack")
    got: list[tuple[str, Path]] = []
    publish_flow("channels", pack, on_delivered=lambda p, d: got.append((p, d)))
    assert got == [("channels", Path(pack))]


def test_publish_flow_receipt_callback_not_called_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """异常中断（填表中途失败）→ 不写回执（未交付不算，正典 §二 F37）。"""
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(cookies, "load", lambda p: {})
    import musespiral.browser.flows as flows_module

    failing = type("FailingSession", (_FakeSession,), {"fail": True})
    monkeypatch.setattr(flows_module, "BrowserSession", failing)
    pack = _make_pack(tmp_path / "demo.channels.pack")
    got: list[tuple[str, Path]] = []
    with pytest.raises(RuntimeError, match="会话中断"):
        publish_flow("channels", pack, on_delivered=lambda p, d: got.append((p, d)))
    assert got == []
