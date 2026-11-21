"""四平台发布流注册表 + login / publish_flow / dry_run 编排（F16）。

真实路径（login/publish_flow）需要 playwright；dry_run 不起浏览器。CLI 接线
（musespiral upload 组）归集成阶段。

F37 平台集单一事实源 = platforms/registry 的 manual_pack 路线
（channels/douyin/xhs/zhihu + F42 juejin/bilibili）。本包属 infra 层，架构
规则 4 禁止向上 import platforms，故这里保留一份字面平台集、由
tests/test_uploader_browser.py 的对拍测试看守与 registry 的一致（漂移以
registry 为准修正本包）。juejin/bilibili 两流为 F42 骨架（待真机④校准）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .. import cookies
from ..session import BrowserSession
from .base import (
    REQUIRED_SELECTOR_KEYS,
    BrowserFlow,
    load_selectors,
    read_pack,
)
from .bilibili import BilibiliFlow
from .channels import ChannelsFlow
from .douyin import DouyinFlow
from .juejin import JuejinFlow
from .xhs import XhsFlow
from .zhihu import ZhihuFlow

PLATFORMS: tuple[str, ...] = ("channels", "douyin", "xhs", "zhihu", "juejin", "bilibili")

_FLOWS: dict[str, type[BrowserFlow]] = {
    "channels": ChannelsFlow,
    "douyin": DouyinFlow,
    "xhs": XhsFlow,
    "zhihu": ZhihuFlow,
    "juejin": JuejinFlow,
    "bilibili": BilibiliFlow,
}


def get_flow(platform: str) -> type[BrowserFlow]:
    try:
        return _FLOWS[platform]
    except KeyError:
        raise ValueError(
            f"不支持的平台: {platform}（可用: {'/'.join(PLATFORMS)}）"
        ) from None


def login(platform: str) -> Path:
    """开有头浏览器 → 打开平台入口 → 等扫码登录 → 退出时保存 storage_state。"""
    selectors = load_selectors(platform)
    get_flow(platform)  # 平台合法性快败（未知平台不起浏览器）
    with BrowserSession(platform, headless=False) as session:
        session.page.goto(selectors["entry_url"])
        print(
            f"请在打开的浏览器中完成{selectors.get('name', platform)}登录"
            "（扫码/账密），完成后回到终端按回车保存登录态…"
        )
        input()
    return cookies.path(platform)


def publish_flow(
    platform: str,
    pack_dir: str | Path,
    *,
    headless: bool = False,
    on_delivered: Callable[[str, Path], None] | None = None,
) -> None:
    """真实发布路径：cookie 快验 → 打开创作者平台 → 填表 → 人工点发布。

    红线①：发布按钮永不代点（notify_manual_publish 仅定位高亮 + 提示）。
    cookie 缺失在起浏览器之前快败（CookieMissing，提示先 login）。

    F37 回执注入点：on_delivered(platform, pack_dir) 在会话**正常结束**
    （with 块完整退出=物料已交付、人工点发后返回前）调用一次；块内任何
    异常都会先于该调用抛出——异常中断不写回执。回执实现（publish_log_
    service.append_uploaded）由调用方（CLI 层）注入：本包属 infra 层，
    架构规则 4 禁止 import services，故以回调承载「会话收尾追加」。
    """
    flow_cls = get_flow(platform)
    cookies.load(platform)  # 缺 cookie 时抛 CookieMissing（可读提示先 login）
    selectors = load_selectors(platform)
    with BrowserSession(platform, headless=headless) as session:
        flow = flow_cls(session.page, selectors)
        flow.open_creator()
        flow.fill_form(Path(pack_dir))
        flow.notify_manual_publish()
    if on_delivered is not None:
        on_delivered(platform, Path(pack_dir))


def dry_run(
    platform: str, pack_dir: str | Path, *, selectors_dir: Path | None = None
) -> list[str]:
    """dry-run：不起浏览器。选择器 YAML 完整性校验 + 逐字段填写值步骤清单。

    缺选择器键/缺 payload.md 在此处快败（可读错误）；返回清单文本行。
    """
    flow_cls = get_flow(platform)
    selectors = load_selectors(platform, selectors_dir=selectors_dir)
    fields = read_pack(pack_dir)
    header = (
        f"{selectors.get('name', platform)}（{platform}）dry-run 步骤清单 "
        f"— 不起浏览器；pack: {Path(pack_dir)}"
    )
    tail = (
        f"选择器校验通过: {platform}.yaml"
        f"（必需键齐全: {','.join(REQUIRED_SELECTOR_KEYS)}）"
    )
    return [header, *flow_cls.dry_run_steps(selectors, fields), tail]
