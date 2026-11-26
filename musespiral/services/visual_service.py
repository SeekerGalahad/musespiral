"""F70-F74 视觉创作服务层（L3 单一持有者）——设计稿 CRUD/预设/导出编排/写回/抽数。

职责（设计 §三 模块归属 + §四 五问）：
  - **设计稿** `<slug>.design/`：design.json 唯一可变真源 + SVG 可再生（§1.1）；
    写前当前版入 F43 版本池（infra/storage/filesystem.backup_article）、
    tmp+rename 原子写（后端正典 P4）；载入时 SVG 重过白名单（F72-T5）。
  - **预设** `~/.musespiral/visual_presets.d/*.json`：纯 JSON 参数组合，零可执行
    面（用户目录只存数据不存代码，§头红线）；CLI 与 Studio 殊途同归。
  - **导出三路** A（前端 canvas，不在本层）→ B（infra/visual/raster 浏览器）
    → C（core/visual/canvas/pillow_render 兜底）；B 失败静默下降 C 并注记
    （同 cover 兜底语义）。尺寸三路恒精确（§五 专项）。
  - **cover 写回**：复用 cover_service 既有纪律（版本池先行→frontmatter 整写），
    **不复制实现**——仅 mp/默认规格写回；非 mp 只落图（F71-T1/T2）。
  - **抽数**（F73）：points=parser 要点 / radar=radar_signal meta / sources=每源
    计数——抽数在服务层不在前端（前端只传 source_ref 引用）。
  - **F72 服务层支撑**（无编辑器 UI）：spec 切换非拉伸重渲（重建 doc，非缩放
    旧稿）/ 色槽限定（任意 hex 拒收 400 + 槽清单）/ 载入白名单复验。

红线：单人本地；AI 不参与本层（建议走 chat，采纳在人工侧）；密钥零涉；
光栅不触网（raster 层 http(s) 中止 + model 层拒网络 href 两层执法）。
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..core.content.themes import load_theme
from ..core.visual import cover
from ..core.visual.canvas import (
    ELEMENT_WHITELIST,
    REGISTRY,
    SEMANTIC_COLOR_SLOTS,
    Background,
    BLEND_MODES,
    CanvasDoc,
    layout_document,
    relayout_vertical,
    render_pillow,
    render_svg,
    resolve,
    spec_size,
    validate_params,
    validate_doc,
)
from ..core.visual.canvas.model import SPECS
from ..infra.storage.filesystem import atomic_write_bytes, backup_article
from ..jobs.store import now_iso as _now_iso
# 封面命名已上移 core（2026-11-24 W2 断环批）——模块级单向依赖 core，
# 不再 import services.cover_service；写回动作仍经 cover_service（函数体内导入，
# 保持「cover→visual 编排委托」的既有单向调用方向）。
from ..core.visual.cover import cover_filename

# 设计稿目录后缀（§八-6 已批：与 .compose/ 对仗）
DESIGN_SUFFIX = ".design"
DESIGN_JSON = "design.json"
PRESETS_SUBDIR = "visual_presets.d"

# B2：kind=article（图文长页/章节分隔——新六模板中两枚；CLI Choice 与
# load/save 枚举执法都经本表单一事实源扩散）
KINDS: tuple[str, ...] = ("cover", "social", "infographic", "article")
_ROUTE_BROWSER = "browser"
_ROUTE_PILLOW = "pillow"

# kebab id（预设 id：小写字母/数字 + 单连字符分隔——F74-T6 非 kebab 拒收）
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 语义色槽缺省（themes 未配槽时回退，保证 build 恒有 hex——不引入第二色板；
# 新槽取值=lapis 内置同键值，F-B 扩槽 2026-11-18）
_SLOT_FALLBACK = {
    "primary": "#2563eb",
    "text": "#1f2937",
    "muted": "#6b7280",
    "background": "#ffffff",
    "accent": "#dc2626",
    "border": "#e5e7eb",
    "code_bg": "#f3f4f6",
    "code_text": "#1f2937",
    "quote_bg": "#eff6ff",
    "quote_border": "#2563eb",
}

# 设计稿 SVG 文件命名（kind → 固定名；social 走 social-<n> 递增）
_SVG_NAME = {"cover": "cover.svg", "infographic": "infographic.svg", "article": "article.svg"}


# ── B2 v2 层：背景/图层状态（design.json v2 的 background + elements）────


def _apply_background(doc: CanvasDoc, meta: Any, bg_raw: object) -> None:
    """把设计稿 background（dict|None）落到 doc 上并过双门：

    - Background.from_dict 全套字段校验（含 http 拒——光栅不触网在存储面复验）；
    - 模板 meta.backgrounds 允许集执法（模板只许声明过的背景种类）。
    None/缺省 → 不动 doc（保留模板 build 的槽色底——v1 语义）。"""
    if bg_raw is None:
        return
    bg = Background.from_dict(bg_raw)
    if bg.kind not in meta.backgrounds:
        raise ValueError(
            f"模板 {meta.id!r} 不允许背景种类 {bg.kind!r}"
            f"（允许: {'/'.join(meta.backgrounds)}）"
        )
    doc.background = bg


_LAYER_KEYS = ("i", "z", "opacity", "locked", "visible", "blend", "dx", "dy", "fill")
# 图层行 fill（文字元素客户端改色）：hex 形态执法（与 model._HEX_COLOR 同口径，
# 局部复写不 import 私有名——服务层自有正则）
_LAYER_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _check_layers(elements: object, *, where: str = "elements") -> list[dict[str, Any]]:
    """design.json v2 elements（图层状态表）执法：[{i,z,opacity,locked,visible,
    blend,dx?,dy?,fill?}]——i=doc 元素序（前端 data-o），z 为绘制层级，fill 为
    文字元素客户端改色（hex）；违例可读 ValueError（400 面）。返回归一化列表
    （缺省键补默认——i/z/dx/dy=0、opacity=1、locked=False、visible=True、
    blend=normal、fill 不落键）。"""
    if elements is None:
        return []
    if not isinstance(elements, list):
        raise ValueError(f"设计稿 {where} 应为数组（图层状态表）")
    out: list[dict[str, Any]] = []
    for n, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise ValueError(f"设计稿 {where}[{n}] 应为对象，得到 {type(raw).__name__}")
        unknown = [k for k in raw if k not in _LAYER_KEYS]
        if unknown:
            raise ValueError(
                f"设计稿 {where}[{n}] 未知键 {unknown}"
                f"（可选: {'/'.join(_LAYER_KEYS)}）"
            )
        row: dict[str, Any] = {}
        for key in ("i", "z", "dx", "dy"):
            v = raw.get(key, 0)
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"设计稿 {where}[{n}].{key}: 应为整数，得到 {v!r}")
            if key == "i" and v < 0:  # 元素序无负值；z/dx/dy 负值合法（层级/位移）
                raise ValueError(f"设计稿 {where}[{n}].i: 应为非负整数，得到 {v!r}")
            row[key] = v
        op = raw.get("opacity", 1.0)
        if isinstance(op, bool) or not isinstance(op, (int, float)) or not 0.0 <= op <= 1.0:
            raise ValueError(f"设计稿 {where}[{n}].opacity: 应在 0..1，得到 {op!r}")
        row["opacity"] = float(op)
        for key in ("locked", "visible"):
            v = raw.get(key, key != "locked")
            if not isinstance(v, bool):
                raise ValueError(f"设计稿 {where}[{n}].{key}: 应为布尔，得到 {v!r}")
            row[key] = v
        blend = raw.get("blend", "normal")
        if blend not in BLEND_MODES:
            raise ValueError(
                f"设计稿 {where}[{n}].blend: 非法混合模式 {blend!r}"
                f"（可选: {'/'.join(BLEND_MODES)}）"
            )
        row["blend"] = blend
        fill = raw.get("fill")
        if fill is not None:
            if not isinstance(fill, str) or not _LAYER_HEX.match(fill):
                raise ValueError(
                    f"设计稿 {where}[{n}].fill: 应为 hex 色（#rgb/#rrggbb/#rrggbbaa），"
                    f"得到 {fill!r}"
                )
            row["fill"] = fill
        out.append(row)
    return out


# ── 路径 ─────────────────────────────────────────────────────


def design_dir(article_path: str | Path) -> Path:
    """文章旁 `<stem>.design/` 目录（存在与否都返回路径）。"""
    article = Path(article_path)
    return article.parent / f"{article.stem}{DESIGN_SUFFIX}"


def design_json_path(article_path: str | Path) -> Path:
    return design_dir(article_path) / DESIGN_JSON


def presets_dir() -> Path:
    """预设目录 ~/.musespiral/visual_presets.d（随 MUSESPIRAL_HOME 复位——测试隔离即生效）。"""
    from .. import config  # 懒加载：避免 import 期固定家目录

    return config.home_dir() / PRESETS_SUBDIR


# ── 色板（themes 是全 visual 域唯一色板事实源）───────────────


def theme_colors(theme: str = "lapis") -> dict[str, str]:
    """主题 → 语义色槽 hex 表（缺槽回退 _SLOT_FALLBACK，accent 沿 quote_border）。"""
    colors = dict(load_theme(theme).get("colors") or {})
    out: dict[str, str] = {}
    for slot in SEMANTIC_COLOR_SLOTS:
        raw = colors.get(slot)
        if slot == "accent" and not raw:
            raw = colors.get("quote_border") or colors.get("primary")
        out[slot] = str(raw or _SLOT_FALLBACK[slot])
    return out


# ── 渲染（F70 调用流 2-4 步；spec 切换非拉伸重渲）────────────


def render(
    *,
    template: str,
    params: dict[str, Any] | None = None,
    spec: str,
    theme: str = "lapis",
    preset_params: dict[str, Any] | None = None,
    params_overrides: dict[str, Any] | None = None,
    article_path: str | Path | None = None,
    background: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """{template, params, spec, background?} → (SVG 字符串, 合并后最终 params)。

    - article_path 给出时补齐封面模板缺省键（title/hook 自动取材——F71 步 1
      同一口径 title_and_hook，不复制取材逻辑）；
    - schema 违例（含色槽非法）抛可读 ValueError（由 API 转 400，
      F71-T3/F72-T2）；未知模板/规格抛可读 ValueError；
    - spec 每次重建 doc（build 按 spec 比例计算坐标）——**非拉伸**，文本
      layout 在此重排（layout_document 对新 doc 全新测量，F72-T1）；
    - B2：background 给出时经 _apply_background 落 doc（Background 全字段
      校验 + 模板允许集执法）；None=模板槽色底（v1 语义不变）。
    """
    template = str(template or "").strip()
    if template not in REGISTRY:
        raise ValueError(
            f"未知模板: {template!r}（注册表: {'/'.join(sorted(REGISTRY))}）"
        )
    spec = str(spec or "").strip()
    if spec not in SPECS:
        raise ValueError(f"未知规格: {spec!r}（可选: {'/'.join(SPECS)}）")
    meta = REGISTRY[template]
    if spec not in meta.specs:
        raise ValueError(
            f"规格 {spec!r} 不适用模板 {template!r}"
            f"（适用: {'/'.join(meta.specs)}）"
        )
    merged_in = dict(params or {})
    if article_path is not None:
        _fill_article_defaults(merged_in, meta, Path(article_path))
    _meta, merged = resolve(
        REGISTRY, template, preset_params=preset_params,
        params=merged_in, overrides=params_overrides,
    )
    doc = meta.build(merged, theme_colors(theme), spec)
    _apply_background(doc, meta, background)
    layout_document(doc)  # 新 doc 全新测量（非伸缩）
    relayout_vertical(doc)  # D-1 垂直重排：多行 text 下推后续同列元素
    svg = render_svg(doc)
    return svg, merged


def _fill_article_defaults(params: dict[str, Any], meta: Any, article: Path) -> None:
    """封面/社媒模板缺省键自动取材（显式值不动；取材同 cover.title_and_hook）。"""
    if meta.kind == "article":
        # V4 修复（2026-11-26 视觉编辑审查批）：article-long 图文长页——
        # title/subtitle/paragraphs 自动取材（正文 md 前几段、剥 HTML 标签
        # /代码块/图片语法——模板槽是纯文本面，原始 markdown 会裸显示）
        from ..core.content.parser import parse_article as _pa

        _a = _pa(article)
        _body = str(_a.raw_body or "")
        # 剥图片/HTML 行内标签/代码块（视觉模板是文字排版面，不是 md 渲染面）
        _body = _body.replace("```", "")
        import re as _re

        _body = _re.sub(r"!\[[^\]]*\]\([^)]*\)", "", _body)  # 图片
        _body = _re.sub(r"</?[a-zA-Z][^>]*>", "", _body)  # HTML 标签
        _body = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", _body)  # 链接→纯文字
        _body = _re.sub(r":::video\{[^}]*\}", "", _body)  # V4 视频容器块
        _body = _re.sub(r"<!--b:[^>]*-->", "", _body)  # 块标记注释
        _body = _re.sub(r"^---$", "", _body, flags=_re.M)  # 分隔线
        _paras = [s.strip() for s in _body.split("\n\n") if s.strip() and not s.strip().startswith("#")]
        _paras = [s for s in _paras if len(s.strip()) > 4][:8]  # 取有效段
        if not params.get("title"):
            params["title"] = str(_a.metadata.get("title") or Path(article).stem)
        if not params.get("subtitle") and _paras:
            # 首段取前 60 字做副题
            params["subtitle"] = _paras[0][:60] + ("…" if len(_paras[0]) > 60 else "")
            _paras = _paras[1:]
        if not params.get("paragraphs"):
            params["paragraphs"] = "\n\n".join(_paras[:8])
        if not params.get("footer"):
            params["footer"] = "musespiral · 生成于 " + __import__("datetime").date.today().isoformat()
        return
    if meta.kind not in ("cover", "social"):
        return
    title = params.get("title")
    hook = params.get("hook")
    if title and hook:
        return
    t, h = cover.title_and_hook(article, title or None, hook or None)
    if not title:
        params["title"] = t
    if "hook" in meta.params_schema and not hook:
        params["hook"] = h


def switch_spec(
    article_path: str | Path, *, new_spec: str, theme: str = "lapis"
) -> tuple[str, dict[str, Any]]:
    """F72-T1 多尺寸切换：读 design.json params 按新 spec 重建（非拉伸重渲）。

    B2：v2 稿的 background 随切（同稿同底——切规格丢背景属回归）。"""
    design = load_design(article_path)
    if design is None:
        raise ValueError("无设计稿，先存稿（缺少 design.json）")
    return render(
        template=design["template"],
        params=design["params"],
        params_overrides=design.get("params_overrides"),
        spec=new_spec,
        theme=theme,
        preset_params=_preset_params(design.get("preset_id")),
        background=design.get("background"),
    )


# ── 设计稿 CRUD ───────────────────────────────────────────────


def load_design(article_path: str | Path) -> dict[str, Any] | None:
    """读 design.json（缺文件 → None；坏结构 → 可读 ValueError）。

    载入时若有对应 SVG 文件，**重过白名单复验**（F72-T5：手改 design 目录
    夹带 ``<script>`` 时抛可读错误——API 侧呈 200+{ok:false}，前端拿不到 svg）。

    B2 v1→v2 读兼容：v1 老稿（无 version 键）**缺省补默认值**——version=1、
    background=None（模板槽色底）、elements=[]（图层默认态）；v2 稿的
    background/elements 原样透出（elements 经 _check_layers 归一执法）。
    """
    path = design_json_path(article_path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"设计稿 design.json 读取失败: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("设计稿 design.json 应为 JSON 对象")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"设计稿 kind 非法: {kind!r}（可选: {'/'.join(KINDS)}）")
    template = raw.get("template")
    if template not in REGISTRY:
        raise ValueError(f"设计稿模板不在注册表: {template!r}")
    spec = raw.get("spec")
    if spec not in SPECS:
        raise ValueError(f"设计稿 spec 非法: {spec!r}（可选: {'/'.join(SPECS)}）")
    if not isinstance(raw.get("params"), dict):
        raise ValueError("设计稿 params 应为对象")
    overrides = raw.get("params_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("设计稿 params_overrides 应为对象或 null")
    # B2：v1 补默认 / v2 执法归一（读侧恒有键——消费端零分叉）
    if "version" not in raw or raw["version"] == 1:
        raw["version"] = 1
        raw["background"] = None
        raw["elements"] = []
    else:
        if raw.get("version") != 2:
            raise ValueError(f"设计稿 version 非法: {raw.get('version')!r}（可选: 1/2）")
        bg = raw.get("background")
        if bg is not None:
            raw["background"] = Background.from_dict(bg).to_dict()  # 全字段执法+归一
        raw["elements"] = _check_layers(raw.get("elements"))
    svg_path = _svg_path_for(article_path, kind, raw.get("svg"))
    if svg_path is not None and svg_path.exists():
        verify_svg(svg_path.read_text(encoding="utf-8"))
    return raw


def save_design(
    article_path: str | Path,
    *,
    kind: str,
    template: str,
    params: dict[str, Any] | None = None,
    params_overrides: dict[str, Any] | None = None,
    spec: str | None = None,
    preset_id: str | None = None,
    svg: str | None = None,
    theme: str = "lapis",
    background: dict[str, Any] | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """设计稿落盘（design.json v2 + 可再生 SVG）；版本池先行 + 原子写（§1.1）。

    - kind/template/spec 枚举外抛 ValueError（400 面）；params schema 违例
      同（含色槽非法 → 槽清单）；
    - spec 缺省取模板首个适用规格；params_overrides 原样存（§1.1 读序）；
    - svg 显式给出时先过白名单复验再落盘（否则按 params 重新生成）；
    - B2：v2 落盘形态 ``{version:2, background, elements:[{i,z,opacity,
      locked,visible,blend,dx,dy}], params, spec, …}``——background/elements
      提交期执法（Background 校验+模板允许集；_check_layers 归一），缺省存
      None/[]（等价 v1 语义）；读侧 load_design 对 v1 老稿补默认兼容；
    - 返回 {dir, files, updated_at}（dir=绝对路径，调用方自行转 root 相对）。
    """
    meta, spec, merged, bg_norm, layers = _validate_save_request(
        article_path, kind=kind, template=template, spec=spec, params=params,
        params_overrides=params_overrides, preset_id=preset_id,
        background=background, elements=elements,
    )
    svg_text = svg if svg is not None else _build_svg(meta, merged, spec, theme, background=bg_norm)
    if svg is not None:
        verify_svg(svg)
    out_dir = Path(article_path).parent / f"{Path(article_path).stem}{DESIGN_SUFFIX}"
    return _write_design(
        out_dir, kind=kind, meta=meta, spec=spec, params=merged,
        params_overrides=params_overrides, preset_id=preset_id, svg_text=svg_text,
        background=bg_norm, elements=layers,
    )


def _validate_save_request(
    article_path: str | Path, *, kind: str, template: str, spec: str | None,
    params: dict[str, Any] | None, params_overrides: dict[str, Any] | None,
    preset_id: str | None, background: dict[str, Any] | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> tuple[Any, str, dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """save 的提交期校验（kind/template/spec/kind 匹配 + schema + B2 背景/图层）
    → (meta, spec, merged, 归一 background, 归一 elements)。"""
    resolved_kind = str(kind or "").strip()
    if resolved_kind not in KINDS:
        raise ValueError(f"kind 非法: {kind!r}（可选: {'/'.join(KINDS)}）")
    if not Path(article_path).is_file():
        raise FileNotFoundError(f"文章不存在: {article_path}")
    meta = REGISTRY.get(str(template or "").strip())
    if meta is None:
        raise ValueError(f"未知模板: {template!r}（注册表: {'/'.join(sorted(REGISTRY))}）")
    if resolved_kind != meta.kind:
        raise ValueError(f"模板 {meta.id!r} 属 {meta.kind} 类，与 kind={kind!r} 不符")
    resolved_spec = str(spec or "").strip() or meta.specs[0]
    if resolved_spec not in SPECS:
        raise ValueError(f"未知规格: {spec!r}（可选: {'/'.join(SPECS)}）")
    if resolved_spec not in meta.specs:
        raise ValueError(f"规格 {resolved_spec!r} 不适用模板 {meta.id!r}（适用: {'/'.join(meta.specs)}）")
    preset = _load_preset_file(preset_id) if preset_id else None
    preset_params = (preset or {}).get("params") if preset else None
    # 先校验（违例零落盘——F71-T3）
    _meta, merged = resolve(
        REGISTRY, meta.id, preset_params=preset_params,
        params=dict(params or {}), overrides=dict(params_overrides or {}),
    )
    # B2：背景/图层提交期执法（模板允许集 + 全字段校验）
    if background is not None:
        bg = Background.from_dict(background)
        if bg.kind not in meta.backgrounds:
            raise ValueError(
                f"模板 {meta.id!r} 不允许背景种类 {bg.kind!r}"
                f"（允许: {'/'.join(meta.backgrounds)}）"
            )
        bg_norm: dict[str, Any] | None = bg.to_dict()
    else:
        bg_norm = None
    layers = _check_layers(elements)
    return meta, resolved_spec, merged, bg_norm, layers


def _build_svg(meta: Any, merged: dict[str, Any], spec: str, theme: str,
               *, background: dict[str, Any] | None = None) -> str:
    doc = meta.build(merged, theme_colors(theme), spec)
    _apply_background(doc, meta, background)
    layout_document(doc)
    relayout_vertical(doc)  # D-1 垂直重排
    return render_svg(doc)


def _write_design(
    out_dir: Path, *, kind: str, meta: Any, spec: str, params: dict[str, Any],
    params_overrides: dict[str, Any] | None, preset_id: str | None, svg_text: str,
    background: dict[str, Any] | None = None, elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """落盘 design.json（v2）+ SVG：版本池先行 + 原子写（§1.1 / §四 Q4）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_name = _next_svg_name(out_dir, kind)
    design = {
        "version": 2,
        "kind": kind, "template": meta.id, "params": params,
        "params_overrides": dict(params_overrides or {}), "preset_id": preset_id,
        "spec": spec, "svg": svg_name, "updated_at": _now_iso(),
        # B2 v2 键：背景层 + 图层状态表（v1 语义=background None/elements []）
        "background": background,
        "elements": elements or [],
    }
    json_path = out_dir / DESIGN_JSON
    if json_path.exists():  # 版本池先行（F43 helper，通用文件可用）
        backup_article(json_path)
    atomic_write_bytes(json_path, json.dumps(design, ensure_ascii=False, indent=2).encode("utf-8"))
    atomic_write_bytes(out_dir / svg_name, svg_text.encode("utf-8"))
    return {"dir": str(out_dir), "files": [DESIGN_JSON, svg_name], "updated_at": design["updated_at"]}


def _next_svg_name(out_dir: Path, kind: str) -> str:
    """kind → SVG 文件名；social 取首个空闲 social-<n>.svg（不覆盖既有解读稿）。"""
    fixed = _SVG_NAME.get(kind)
    if fixed:
        return fixed
    n = 1
    while (out_dir / f"social-{n}.svg").exists():
        n += 1
    return f"social-{n}.svg"


def _svg_path_for(article_path: str | Path, kind: str, recorded: Any) -> Path | None:
    """design.json 记录的 SVG 文件名 → 路径（记录名只接受安全形态）。"""
    name = recorded if isinstance(recorded, str) and recorded else _SVG_NAME.get(kind)
    if not name or "/" in name or ".." in name:
        return None
    return design_dir(article_path) / name


# ── SVG 白名单复验（F72-T5 载入门；API 透出前同门）───────────


# SVG 标签白名单 = 根 svg + 元素白名单 + tspan（text 行结构）+ B2 背景结构五件
# （defs/linearGradient/radialGradient/stop/pattern）+ clipPath（image_slot 蒙版）。
# 全部只可能由 svg.render_svg 产出——白名单扩容即「新增合法产物形态」，
# 手改夹带的其余任何标签（script/foreignObject/…）仍拒（F72-T5 语义不变）。
_SVG_TAGS: tuple[str, ...] = ELEMENT_WHITELIST + (
    "svg", "tspan", "defs", "linearGradient", "radialGradient", "stop", "pattern", "clipPath",
)

# 承载 URL 的属性面：href / xlink:href / style / fill / stroke / clip-path
# —— style 里的 url()/@import 与 fill/stroke 的 url(#id) 都是浏览器会去取的
# 引用面（光栅不触网红线在载入侧同样成立：设计稿 SVG 会被 GET
# /api/visual/design 原样透出给前端并 inline 注入 → style url() 会发外域请求）。
# B2：clip-path（image_slot 蒙版引用面 url(#dvmask)）同入执法。
_URL_ATTRS: tuple[str, ...] = ("href", "style", "fill", "stroke", "clip-path")
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
# CSS 引用形态：url( ... )（可含引号与空白）与 @import（可带 url() 或裸串）
_CSS_URL_RE = re.compile(r"url\s*\(\s*['\"]?([^'\")\s]+)", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"@import\s+([^;]+);?", re.IGNORECASE)


def _is_network_url(value: str) -> bool:
    """网络地址判定（大小写不敏感 + 协议相对地址一并算网络）。

    scheme 大小写不敏感是 RFC 3986 事实（`HTTPS://` 即 https）；`//host/path`
    是协议相对地址——继承页面协议即网络请求。
    """
    low = value.strip().lower()
    return low.startswith(("http:", "https:", "//"))


def _check_svg_url_values(raw: str, where: str) -> None:
    """属性值里的网络引用拒收（style 的 url()/@import、fill/stroke 的 url()）。"""
    text = str(raw)
    if _CSS_IMPORT_RE.search(text):
        raise ValueError(f"{where}: 禁止 @import（光栅不触网——外域样式表一律拒收）")
    for match in _CSS_URL_RE.finditer(text):
        target = match.group(1)
        if _is_network_url(target):
            raise ValueError(
                f"{where}: 禁止网络地址 {target!r}（光栅不触网——外域引用一律拒收）"
            )


def verify_svg(svg: str) -> str:
    """对原始 SVG 字符串重过白名单（标签/事件属性/网络引用四道；违规抛可读 ValueError）。

    与 core 序列化白名单同源（ELEMENT_WHITELIST + tspan 行结构 + on* 属性 +
    网络引用禁），用于「手改 design 目录夹带恶意 SVG」的载入侧执法
    （F70 API 纪律；F72-T5）。

    网络引用面覆盖**四类属性**（不只 href）：`href` / `xlink:href`（含大小写
    变体与协议相对地址）、`style`（`url(...)` 与 `@import`）、`fill`/`stroke`
    （`url(#id)` 形态同样走浏览器取数面）。缺 style 面时，`style="background:
    url(//evil/x.png)"` 可被透出给前端 inline 注入并触发外域请求——与
    「光栅不触网 / 零外联」红线冲突。
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError(f"SVG 解析失败（拒绝载入）: {exc}") from None
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        if tag not in _SVG_TAGS:
            raise ValueError(
                f"SVG 元素越界: <{tag}>（元素白名单: {'/'.join(ELEMENT_WHITELIST)}）"
            )
        for attr in el.attrib:
            low = attr.lower()
            if low.startswith("on"):
                raise ValueError(f"SVG 属性越界: {attr}（事件属性禁止）")
        for attr_name in _URL_ATTRS:
            raw = el.attrib.get(attr_name)
            if raw is None and attr_name == "href":
                raw = el.attrib.get(_XLINK_HREF)
            if raw is None:
                continue
            text = str(raw)
            if attr_name in ("href",) and _is_network_url(text):
                raise ValueError("SVG href 禁止网络地址（光栅不触网）")
            _check_svg_url_values(text, f"SVG {tag}.{attr_name}")
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("SVG 根元素必须为 <svg>（拒绝载入）")
    return svg


# ── 预设（~/.musespiral/visual_presets.d；纯 JSON 零代码）──────────


def _preset_path(preset_id: str) -> Path:
    pid = str(preset_id or "").strip()
    if not _KEBAB_RE.match(pid):
        raise ValueError(f"预设 id 非法: {pid!r}（须 kebab 形态，如 my-cover-1）")
    return presets_dir() / f"{pid}.json"


def _load_preset_file(preset_id: str) -> dict[str, Any] | None:
    path = _preset_path(preset_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _preset_params(preset_id: Any) -> dict[str, Any] | None:
    if not isinstance(preset_id, str) or not preset_id:
        return None
    preset = _load_preset_file(preset_id)
    params = (preset or {}).get("params")
    return params if isinstance(params, dict) else None


def list_presets() -> list[dict[str, Any]]:
    """全部预设（按 id 升序；坏行宽容跳过——人工可编辑文件）。"""
    d = presets_dir()
    if not d.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        row = _load_preset_file(path.stem)
        if row is None or not _KEBAB_RE.match(path.stem):
            continue
        items.append({
            "id": path.stem,
            "label": row.get("label") or path.stem,
            "template": row.get("template"),
            "kind": row.get("kind"),
        })
    return items


def save_preset(
    *, preset_id: str, template: str, params: dict[str, Any] | None = None,
    label: str | None = None, kind: str | None = None,
) -> dict[str, Any]:
    """预设保存（纯 JSON 落盘，同 id 幂等覆盖；零可执行痕迹——F74-T5）。

    校验：id kebab / template 在注册表 / kind 枚举（缺省取模板 kind）/
    params 过模板 schema（违例可读 ValueError）。
    """
    path = _preset_path(preset_id)  # 非 kebab → ValueError（F74-T6）
    meta = REGISTRY.get(str(template or "").strip())
    if meta is None:
        raise ValueError(f"未知模板: {template!r}（注册表: {'/'.join(sorted(REGISTRY))}）")
    resolved_kind = str(kind or meta.kind).strip()
    if resolved_kind not in KINDS:
        raise ValueError(f"kind 非法: {kind!r}（可选: {'/'.join(KINDS)}）")
    if resolved_kind != meta.kind:
        raise ValueError(f"模板 {meta.id!r} 属 {meta.kind} 类，与 kind={resolved_kind!r} 不符")
    merged = validate_params(meta.params_schema, dict(params or {}), where="预设 params")
    row = {
        "id": path.stem,
        "label": str(label or path.stem),
        "template": meta.id,
        "params": merged,
        "kind": resolved_kind,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, json.dumps(row, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"id": path.stem, "path": str(path), "template": meta.id, "kind": resolved_kind}


# ── 导出（B 主 / C 兜底；尺寸三路恒精确）─────────────────────


def export_file_name(article_path: str | Path, kind: str, *, platform: str | None = None,
                     n: int | None = None) -> str:
    """导出产物名——沿用既有约定族：cover.png/cover.<platform>.png（cover_service
    既有无需复制）、<stem>.social-<n>.png、<stem>.infographic.png、
    <stem>.article.png（B2 kind=article）。"""
    article = Path(article_path)
    if kind == "cover":
        return cover_filename(platform)
    if kind == "social":
        return f"{article.stem}.social-{int(n or 1)}.png"
    if kind == "article":
        return f"{article.stem}.article.png"
    return f"{article.stem}.infographic.png"


def _raster_or_pillow(doc: CanvasDoc, out: Path, *, route: str | None) -> str:
    """doc → PNG：route=browser 走 B（缺浏览器降 C 并注记）；pillow 直走 C。"""
    size = (doc.width, doc.height)
    if route != _ROUTE_PILLOW:
        try:
            from ..infra.visual.raster import raster_svg
            from ..browser.session import BrowserNotInstalled

            raster_svg(render_svg(doc), out=out, size=size)
            return "B"
        except BrowserNotInstalled:
            pass  # 静默降 C（缺浏览器是预期环境——封面必须有产出）
        except Exception:  # 光栅中途失败同样兜底（与 cover render_title_card 同语义）
            pass
    img = render_pillow(doc)
    img.save(out, format="PNG")
    return "C"


def export_design(
    article_path: str | Path, *, kind: str, route: str | None = None,
    platform: str | None = None, theme: str = "lapis",
) -> dict[str, Any]:
    """按 design.json 导出单张 PNG（B 主 C 兜底，尺寸恒精确）。

    - kind=cover：产物 cover.png（mp/默认）或 cover.<platform>.png；**仅
      mp/默认写回 frontmatter**（复用 cover_service 写回纪律，非 mp 只落图）。
    - kind=social/infographic：<stem>.social-<n>.png / <stem>.infographic.png，
      命名递增不覆盖既有（F72-T4）。
    - 返回 {png, size, route}（png=绝对路径，调用方按根边界转相对）。
    """
    kind = str(kind or "").strip()
    if kind not in KINDS:
        raise ValueError(f"kind 非法: {kind!r}（可选: {'/'.join(KINDS)}）")
    if route is not None and route not in (_ROUTE_BROWSER, _ROUTE_PILLOW):
        raise ValueError(f"route 非法: {route!r}（可选: browser/pillow）")
    article = Path(article_path)
    design = load_design(article)
    if design is None:
        raise ValueError("无设计稿，先存稿（缺少 design.json）")
    meta = REGISTRY[design["template"]]
    doc = meta.build(design["params"], theme_colors(theme), design["spec"])
    _apply_background(doc, meta, design.get("background"))  # B2：随稿背景
    layout_document(doc)
    relayout_vertical(doc)  # D-1 垂直重排

    out_dir = article.parent
    name = export_file_name(article, kind, platform=platform, n=_next_social_n(out_dir, article, kind))
    actual = _raster_or_pillow(doc, out_dir / name, route=route)

    if kind == "cover" and platform in (None, "mp"):
        # 写回纪律内联：先入版本池再写 frontmatter（cover_service 单一实现）
        from . import cover_service

        cover_service._write_back_cover(article, name)  # noqa: SLF001 - 复用既有纪律
    return {"png": str(out_dir / name), "size": [doc.width, doc.height], "route": actual}


def _next_social_n(out_dir: Path, article: Path, kind: str) -> int | None:
    """social 产物命名递增：扫描既有 <stem>.social-<n>.png，取 max+1（不覆盖）。"""
    if kind != "social":
        return None
    n = 1
    while (out_dir / f"{article.stem}.social-{n}.png").exists():
        n += 1
    return n


def export_social_batch(
    article_path: str | Path, *, specs: list[str] | None = None,
    route: str | None = None, theme: str = "lapis",
) -> list[dict[str, Any]]:
    """F72-T4 社媒图多规格批量导出：同 params 逐 spec 重建（非拉伸）→ 递增命名。"""
    article = Path(article_path)
    design = load_design(article)
    if design is None:
        raise ValueError("无设计稿，先存稿（缺少 design.json）")
    meta = REGISTRY[design["template"]]
    chosen = specs or list(meta.specs)
    for spec in chosen:
        if spec not in SPECS or spec not in meta.specs:
            raise ValueError(f"规格 {spec!r} 不适用模板 {meta.id!r}（适用: {'/'.join(meta.specs)}）")
    results: list[dict[str, Any]] = []
    for spec in chosen:
        doc = meta.build(design["params"], theme_colors(theme), spec)
        _apply_background(doc, meta, design.get("background"))  # B2：随稿背景
        layout_document(doc)
        relayout_vertical(doc)  # D-1 垂直重排
        n = _next_social_n(article.parent, article, "social")
        name = export_file_name(article, "social", n=n)
        actual = _raster_or_pillow(doc, article.parent / name, route=route)
        results.append({"png": str(article.parent / name), "size": [doc.width, doc.height], "route": actual})
    return results


def generate_cover_from_design(
    article_path: str | Path, *, platform: str | None = None, theme: str = "lapis",
    route: str | None = None, write_back: bool = True,
) -> Path:
    """cover.ROUTES `from-design` 落点（§八-3 已批的桥）：设计稿 cover.svg → 规格
    PNG → 写回（仅 mp）。cover_service.generate_cover 的 from-design 分支委托本函数。"""
    article = Path(article_path)
    design = load_design(article)
    if design is None or design.get("kind") != "cover":
        raise FileNotFoundError(
            f"无封面设计稿: {design_dir(article)}（先 musespiral design save --kind cover）"
        )
    meta = REGISTRY[design["template"]]
    if platform in (None, "mp"):
        spec = design["spec"] if design["spec"] in meta.specs else "mp"
    else:
        spec = _platform_spec(platform)
        if spec not in meta.specs:
            raise ValueError(
                f"平台规格 {spec!r} 不适用模板 {meta.id!r}（适用: {'/'.join(meta.specs)}）"
            )
    doc = meta.build(design["params"], theme_colors(theme), spec)
    _apply_background(doc, meta, design.get("background"))  # B2：随稿背景
    layout_document(doc)
    relayout_vertical(doc)  # D-1 垂直重排
    out = article.parent / cover_filename(platform)
    _raster_or_pillow(doc, out, route=route)
    if write_back and platform in (None, "mp"):
        from . import cover_service

        cover_service._write_back_cover(article, out.name)  # noqa: SLF001
    return out


def _platform_spec(platform: str) -> str:
    """平台 → 设计稿规格键（mp→mp；xhs→xhs-3x4；其余按 cover_size 推导方图近似）。"""
    if platform == "mp":
        return "mp"
    if platform == "xhs":
        return "xhs-3x4"
    w, h = cover.cover_size(platform)
    return "square" if abs(w - h) <= 1 else "wide16x9"


# ── F73 抽数（三数据源；抽数在服务层不在前端）─────────────────


def extract_points(article_path: str | Path) -> dict[str, Any]:
    """文章要点抽取：各级小节标题 → (标题, 小节内内容块数)——条形数据面。

    无小节（或全空）→ points=[]（模板层产合法空态，F73-T4）。
    """
    from ..core.content.parser import parse_article

    parsed = parse_article(article_path)
    points: list[dict[str, Any]] = []
    warnings: list[str] = []
    current: str | None = None
    count = 0

    def _flush() -> None:
        if current is None:
            return
        if count > 0:
            points.append({"label": current, "value": float(count)})
        else:
            warnings.append(f"小节「{current}」无内容块，已跳过")

    for block in parsed.blocks:
        if block.get("type") == "heading":
            _flush()
            current = str(block.get("text") or "").strip()
            count = 0
        elif current is not None:
            count += 1
    _flush()
    return {"points": points, "warnings": warnings}


def extract_radar(materials: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """radar_signal 素材 → 雷达线计数条（meta.line 分组；grade 缺失行跳过+warning）。"""
    if materials is None:
        from . import materials_service

        materials = materials_service.list_materials()
    counts: dict[str, int] = {}
    warnings: list[str] = []
    for m in materials:
        if m.get("type") != "radar_signal":
            continue
        meta = (m.get("payload") or {}).get("meta") or {}
        grade = meta.get("grade")
        line = meta.get("line") or (m.get("origin") or {}).get("source_id") or "unknown"
        if not grade:
            warnings.append(f"素材 {m.get('id')!r} 缺 grade，已跳过")
            continue
        counts[str(line)] = counts.get(str(line), 0) + 1
    points = [{"label": k, "value": float(v)} for k, v in sorted(counts.items())]
    return {"points": points, "warnings": warnings}


def extract_sources(materials: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """sources 生态 → 每源素材计数条（origin.source_id 分组，label 取源 label）。"""
    from . import sources_service

    if materials is None:
        from . import materials_service

        materials = materials_service.list_materials()
    labels = {s["id"]: str(s.get("label") or s["id"]) for s in sources_service.list_sources()}
    counts: dict[str, int] = {}
    for m in materials:
        sid = (m.get("origin") or {}).get("source_id")
        if isinstance(sid, str) and sid:
            counts[sid] = counts.get(sid, 0) + 1
    points = [
        {"label": labels.get(sid, sid), "value": float(cnt)}
        for sid, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {"points": points, "warnings": []}


def build_infographic(
    article_path: str | Path, *, source_ref: dict[str, Any], theme: str = "lapis"
) -> dict[str, Any]:
    """source_ref → 信息图渲染（F73：前端只传引用，数值全由服务层取）。"""
    kind = (source_ref or {}).get("kind")
    if kind not in ("points", "radar", "sources"):
        raise ValueError(f"source_ref.kind 非法: {kind!r}（可选: points/radar/sources）")
    if kind == "points":
        data = extract_points(article_path)
    elif kind == "radar":
        data = extract_radar()
    else:
        data = extract_sources()
    from ..core.content.parser import parse_article

    title = str(parse_article(article_path).metadata.get("title") or Path(article_path).stem)
    params = {"title": title, "points": data["points"], "footer": ""}
    svg, _merged = render(
        template="points-three", params=params, spec="wide16x9", theme=theme
    )
    return {"svg": svg, "params": params, "warnings": data["warnings"]}


# ── M4 AI 参数建议（chat task=visual_suggest；只产预览态，人工采纳）─────

_SUGGEST_PROMPT = (
    "你是视觉版式顾问。基于下列设计稿上下文，给出一组**参数建议**（只给参数，"
    "不要产出 SVG 或成品稿）：\n"
    "模板: {template}（kind={kind}）\n"
    "当前 params: {params}\n"
    "可用色槽: {slots}\n"
    "请只输出一个 JSON 对象，键限定为模板槽位（如 title/hook/font_size/align/"
    "background_slot——font_size 取 S/M/L、align 取 left/center/right、"
    "background_slot 取色槽名）。"
)


def suggest_params(
    *, template: str, kind: str, params: dict[str, Any] | None = None,
    chat_fn: Any | None = None,
) -> dict[str, Any]:
    """M4 AI 参数建议（chat task=visual_suggest）：**只产参数建议 JSON，不产成品稿**。

    - 风格注入第七任务（F44 六任务 + visual_suggest）：system prompt 追加 style 块
      （两资产全无 → 空串不注入，同六任务形态）；
    - AIError/坏 JSON → 降级返回 {ok:False, reason}（不阻断，人工仍可手改）；
    - 返回 {ok, params, raw?}——params 已过模板 schema（非法建议丢弃并记 ignored）。
    """
    from ..infra.ai.habits_inject import load_style_samples, style_block
    from ..core.habits import load_habits

    meta = REGISTRY.get(str(template or "").strip())
    if meta is None:
        raise ValueError(f"未知模板: {template!r}（注册表: {'/'.join(sorted(REGISTRY))}）")
    system = "你是资深视觉版式顾问。"
    style = style_block(load_habits(), load_style_samples())  # 第七注入任务
    if style:
        system = f"{system}\n\n{style}"
    prompt = _SUGGEST_PROMPT.format(
        template=meta.id, kind=kind, params=json.dumps(params or {}, ensure_ascii=False),
        slots="/".join(SEMANTIC_COLOR_SLOTS),
    )
    if chat_fn is None:
        from ..infra.ai import chat as chat_fn
    from ..infra.ai import AIError

    try:
        raw = chat_fn(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            task="visual_suggest", temperature=0.4,
        )
    except AIError as exc:
        return {"ok": False, "reason": f"AI 未配置或调用失败: {exc}", "params": {}}
    try:
        data = _parse_suggest_json(raw)
        validated = validate_params(meta.params_schema, data, where="visual_suggest")
    except ValueError as exc:
        return {"ok": False, "reason": str(exc), "params": {}, "raw": raw}
    return {"ok": True, "params": validated, "raw": raw}


def _parse_suggest_json(raw: str) -> dict[str, Any]:
    """解析建议 JSON（容忍 ``` 围栏与前后杂文字）；非对象 → ValueError。"""
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("建议响应不含 JSON 对象（AI 输出不可解析，人工手改即可）")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"建议 JSON 解析失败: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError("建议 JSON 应为对象（槽位→参数值）")
    return obj


__all__ = [
    "DESIGN_JSON",
    "DESIGN_SUFFIX",
    "KINDS",
    "PRESETS_SUBDIR",
    "build_infographic",
    "design_dir",
    "design_json_path",
    "export_design",
    "export_file_name",
    "export_social_batch",
    "extract_points",
    "extract_radar",
    "extract_sources",
    "generate_cover_from_design",
    "list_presets",
    "load_design",
    "presets_dir",
    "render",
    "save_design",
    "save_preset",
    "suggest_params",
    "switch_spec",
    "theme_colors",
    "verify_svg",
]
