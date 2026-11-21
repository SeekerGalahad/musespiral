"""BrowserFlow 抽象 + 选择器加载 + 物料包取材 + dry-run 步骤（F16）。

「填表」交给机器、「点发布」留给人：open_creator → fill_form（读 pack 目录
payload.md/cover/）→ notify_manual_publish（定位发布按钮高亮 + 打印人工确认
提示）。运行路径纪律（红线①）：本目录与 session.py 禁用一切 .click()——
publish_button 选择器只用于定位后高亮提示，代码永不点击；结构测试
tests/test_browser.py 据此扫描，改动前先读其 docstring。

选择器外置可更新：每平台 selectors/<platform>.yaml（matrix 教训：CSS 选择器
易碎，别硬编码）——真机失效直接改 YAML，不改代码。
"""
from __future__ import annotations

import re
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml

# 选择器 YAML 必需键：入口 URL / 标题/描述/话题输入框 / 封面上传 input /
# 发布按钮（仅定位后高亮提示，代码永不点击）。缺键在 dry-run 与真实路径
# 前置校验中快败（load_selectors）。
REQUIRED_SELECTOR_KEYS: tuple[str, ...] = (
    "entry_url",
    "title_input",
    "desc_input",
    "topic_input",
    "cover_input",
    "publish_button",
)

_SELECTORS_DIR = Path(__file__).resolve().parent.parent / "selectors"

# payload.md 头部对照行格式（契约源：pack._payload_md 的产出）
_HEADER_RE = re.compile(r"^> - (标题|描述|话题)：(.*)$", re.MULTILINE)
_SPLIT_RE = re.compile(r"^---\s*$", re.MULTILINE)


def load_selectors(
    platform: str, *, selectors_dir: Path | None = None
) -> dict[str, str]:
    """读取并校验平台选择器 YAML；缺文件/非映射/缺键都是可读 ValueError。"""
    base = Path(selectors_dir) if selectors_dir else _SELECTORS_DIR
    path = base / f"{platform}.yaml"
    if not path.is_file():
        known = "/".join(sorted(p.stem for p in base.glob("*.yaml"))) or "（无）"
        raise ValueError(f"平台选择器文件不存在: {path}（可用: {known}）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"选择器文件不是合法 YAML: {path} ({e})") from e
    if not isinstance(data, dict):
        raise ValueError(f"选择器文件结构错误（顶层应为映射）: {path}")
    missing = [k for k in REQUIRED_SELECTOR_KEYS if not str(data.get(k, "")).strip()]
    if missing:
        raise ValueError(
            f"选择器文件缺键: {','.join(missing)}（{path}）——"
            "选择器外置可更新，直接补全 YAML 即可"
        )
    return {str(k): str(v) for k, v in data.items()}


@dataclass
class PackFields:
    """fill_form / dry-run 共用的物料包取材（payload.md 头部 + 正文 + cover/）。"""

    title: str = ""
    desc: str = ""
    tags: list[str] = field(default_factory=list)
    body: str = ""
    covers: list[Path] = field(default_factory=list)


def read_pack(pack_dir: str | Path) -> PackFields:
    """解析物料包：payload.md 头部「标题/描述/话题」+ --- 以下正文 + cover/ 图片清单。"""
    d = Path(pack_dir)
    payload = d / "payload.md"
    if not payload.is_file():
        raise FileNotFoundError(
            f"物料包缺少 payload.md: {d}（请先 musespiral pack 生成物料包）"
        )
    text = payload.read_text(encoding="utf-8")
    parts = _SPLIT_RE.split(text, maxsplit=1)
    header_text = parts[0]
    body = parts[1].strip() if len(parts) == 2 else ""
    header = dict(_HEADER_RE.findall(header_text))
    raw_tags = header.get("话题", "").strip()
    tags = [] if raw_tags in ("", "无") else [
        t.lstrip("#") for t in raw_tags.split() if t.strip()
    ]
    cover_dir = d / "cover"
    covers = (
        sorted(p for p in cover_dir.iterdir() if p.is_file())
        if cover_dir.is_dir()
        else []
    )
    return PackFields(
        title=header.get("标题", "").strip(),
        desc=header.get("描述", "").strip(),
        tags=tags,
        body=body,
        covers=covers,
    )


def preview_text(text: str, limit: int = 40) -> str:
    """步骤清单里的字段值预览：压平换行、超长截断。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


class BrowserFlow(ABC):
    """平台发布流抽象：open_creator → fill_form → notify_manual_publish。

    page 为 Playwright Page（typing 作 Any：playwright 是 browser extra，
    本模块不静态依赖）。子类以 platform / desc_source 声明平台差异，
    选择器全部来自 selectors YAML。
    """

    platform: ClassVar[str] = ""
    # 描述框取材：desc=payload 头部「描述」行 / body=正文（平台无独立描述框时）
    desc_source: ClassVar[str] = "desc"

    def __init__(self, page: Any, selectors: dict[str, str]) -> None:
        self.page = page
        self.selectors = selectors

    # ── 三个公开步骤（真实路径；不起浏览器的清单见 dry_run_steps） ──

    def open_creator(self) -> None:
        self.page.goto(self.selectors["entry_url"])

    def fill_form(self, pack_dir: str | Path) -> None:
        fields = read_pack(pack_dir)
        self.fill_title(fields.title)
        self.fill_desc(fields)
        self.fill_topics(fields.tags)
        self.upload_covers(fields.covers)

    def notify_manual_publish(self) -> None:
        """定位发布按钮并高亮，打印人工确认提示——永不点击（红线①）。"""
        selector = self.selectors["publish_button"]
        try:
            self.page.locator(selector).highlight()  # 仅高亮提示
        except Exception:
            pass  # 高亮失败不影响提示（选择器或待真机校准）
        name = self.selectors.get("name", self.platform)
        print(
            f"[{name}] 表单已填写完成。请人工检查标题/正文/封面/话题与 checks.txt，"
            f"确认无误后手动点击「发布」（按钮选择器: {selector}，本工具永不代点）。"
        )
        input("点击发布后按回车关闭浏览器…")

    # ── 填字段钩子（默认读 selectors 键直接填；子类按平台差异覆写） ──

    def fill_title(self, title: str) -> None:
        if title:
            self.page.locator(self.selectors["title_input"]).fill(title)

    def fill_desc(self, fields: PackFields) -> None:
        value = fields.desc if self.desc_source == "desc" else fields.body
        if value:
            self.page.locator(self.selectors["desc_input"]).fill(value)

    def fill_topics(self, tags: list[str]) -> None:
        box = self.page.locator(self.selectors["topic_input"])
        for tag in tags:
            box.fill(f"#{tag}")
            self.page.keyboard.press("Enter")  # 话题逐个输入回车确认，不点弹层

    def upload_covers(self, covers: list[Path]) -> None:
        if covers:
            self.page.locator(self.selectors["cover_input"]).set_input_files(
                [str(c) for c in covers]
            )

    # ── dry-run 步骤清单（类方法，不起浏览器，page 无关） ──

    @classmethod
    def dry_run_steps(
        cls, selectors: dict[str, str], fields: PackFields
    ) -> list[str]:
        desc_value = fields.desc if cls.desc_source == "desc" else fields.body
        topics = " ".join(f"#{t}" for t in fields.tags) if fields.tags else "（无话题）"
        cover = (
            "、".join(c.name for c in fields.covers) if fields.covers else "（pack 无 cover/，跳过）"
        )
        return [
            f"[1] 打开创作者平台入口: {selectors['entry_url']}",
            f"[2] 填写标题: {fields.title or '（payload 无标题，人工补）'} → {selectors['title_input']}",
            f"[3] 填写描述: {preview_text(desc_value) or '（无）'} → {selectors['desc_input']}",
            f"[4] 填写话题: {topics} → {selectors['topic_input']}",
            f"[5] 上传封面: {cover} → {selectors['cover_input']}",
            f"[6] 核对 checks.txt 后【人工】点击「发布」 → {selectors['publish_button']}"
            "（代码永不点击，仅定位高亮提示）",
        ]
