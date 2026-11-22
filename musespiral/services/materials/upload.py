"""materials 包：upload（图片/文档上传全链 + 视频登记 + 素材文件服务）。

原 materials_service.py 按 ARCH-2 拆出的上传簇（第二十一期 Phase A 多模态
+ 2026-11-20 格式生态线 DOC-1）：

  - add_uploaded_image（F59 图片上传：体检→落盘→缩略→索引，幂等锚
    upload://sha256:<内容散列>）；
  - add_uploaded_document（DOC-1：后缀门→doc-extract 插件 run()→text 素材
    ——插件是提取逻辑唯一实现，宿主只做字节→临时文件→run()→落库编排）；
  - add_uploaded（分流：文档后缀走提取，其余按图片）；
  - register_video（F61 登记制：URL 校验→规范化幂等锚 video://→落库，
    零出网）；
  - read_material_file（F60 文件服务三道门：路径边界/后缀白名单/魔数复核
    ——服务层统一 ValueError=400 / FileNotFoundError=404）。
"""
from __future__ import annotations

import hashlib
import ipaddress
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ... import config
from ...core.materials import model
from ...jobs.store import now_iso as _now_iso
from .crud import add_material, get_material

# 上传素材的虚拟源与视频登记的人工源（正典 §1.4 迁移表：均不入 sources.json）
UPLOAD_SOURCE_ID = "upload"
VIDEO_SOURCE_ID = "manual"
# 视频平台域名表（F61：域名后缀匹配，其余 other）
_VIDEO_DOMAINS: tuple[tuple[str, str], ...] = (
    ("bilibili.com", "bilibili"), ("b23.tv", "bilibili"),
    ("youtube.com", "youtube"), ("youtu.be", "youtube"),
)
# 选填文本字段上限（F61：title/note/duration_text 各 ≤200 字符）
_VIDEO_TEXT_MAX = 200


def add_uploaded_image(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """图片上传全链（F59）：体检→落盘→缩略→索引（幂等，同 sha256 同 id 零追加）。

    - 校验链全在 infra.media_store（8MiB 门/魔数嗅探/Pillow verify/尺寸
      真值）；license 枚举外 → ValueError（400）；
    - 幂等锚 uri = upload://sha256:<内容散列>（正典 §1.4）——同图重传同
      id：既有活跃行直接命中返回（dedup=true，jsonl 零追加；文件仓内容
      寻址重写同字节，无 immutable 冲突）；
    - 响应 {material, dedup, thumb_ready}（缩略同步生成，无异步补偿路径）。
    """
    from ...infra import media_store  # 函数体内导入（导入期解耦）

    if license not in model.LICENSES:
        raise ValueError(f"license 必须是 {model.LICENSES} 之一: {license!r}")
    info = media_store.inspect_image(data)
    sha256 = info["sha256"]
    uri = f"upload://sha256:{sha256}"
    mid = model.make_material_id(UPLOAD_SOURCE_ID, "image", uri)
    existing = get_material(mid)
    if existing is not None:
        media_store.store_original(mid, info["ext"], data)  # 内容寻址：重写同字节（文件缺失自愈）
        media_store.make_thumbnail(mid, data)
        return {"material": existing, "dedup": True, "thumb_ready": True}
    material = model.make_material(
        mtype="image",
        source_id=UPLOAD_SOURCE_ID,
        uri=uri,
        title=name.strip() or f"{sha256[:8]}.{info['ext']}",
        license=license,
        payload={
            "local_path": media_store.local_rel_path(mid, info["ext"]),
            "meta": {
                "width": info["width"],
                "height": info["height"],
                "bytes": info["bytes"],
                "format": info["format"],
                "sha256": sha256,
                "license_note": note.strip(),
            },
        },
        fetched_at=fetched_at or _now_iso(),
    )
    media_store.store_original(mid, info["ext"], data)
    media_store.make_thumbnail(mid, data)
    row = add_material(material)
    return {"material": row, "dedup": False, "thumb_ready": True}


# ── 文档上传（2026-11-20 格式生态线 DOC-1）──────────────────────────
# doc-extract 插件目录名带连字符（doc-extract），不能常规 import——importlib
# 按文件位置加载（与 pipeline_service_plugins._load_python_entry 同款），模块级
# 缓存避免每次上传重复 exec。插件 run() 是提取逻辑的**唯一实现**，这里只做
# 「字节→临时文件→run()→material 落库」的宿主编排，不复制任何解析逻辑。
# parents[2]：本文件在 services/materials/ 下比拆分前深一层，仓库根（含
# plugins_builtin/）是上两级——与拆分前 services/materials_service.py 的
# parents[1] 同指 musespiral/ 包根。
_DOC_EXTRACT_PLUGIN = (
    Path(__file__).resolve().parents[2] / "plugins_builtin" / "doc-extract" / "plugin.py"
)
_doc_extract_mod: Any = None


def _load_doc_extract() -> Any:
    """加载内置 doc-extract 插件 run()（importlib 位置加载 + 模块级缓存）。"""
    global _doc_extract_mod
    if _doc_extract_mod is None:
        import importlib.util  # 函数调用期导入（仅文档上传路径需要）

        spec = importlib.util.spec_from_file_location("musespiral_builtin_doc_extract", _DOC_EXTRACT_PLUGIN)
        if spec is None or spec.loader is None:  # pragma: no cover - importlib 形态防御
            raise ValueError(f"doc-extract 插件无法加载: {_DOC_EXTRACT_PLUGIN}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _doc_extract_mod = module
    return _doc_extract_mod


class _DocExtractCtx:
    """doc-extract.run() 的极简宿主上下文（log/progress/cancelled 三面）。"""

    cancelled = False

    def log(self, msg: str) -> None:  # noqa: D401 - 接口形状
        pass

    def progress(self, done: int, total: int) -> None:
        pass


def add_uploaded_document(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """文档上传全链（DOC-1）：后缀门→doc-extract.run()→text 素材落库（幂等）。

    - 后缀白名单复用 doc-extract.ALLOWED_SUFFIXES（大小写不敏感）；非文档
      后缀（图片等）→ ValueError（400 可读），由 server 按文件名分流；
    - 提取逻辑**完全复用** doc-extract 插件 run()——扫描版 PDF 可读拒绝、
      空结果拒绝、可选依赖缺席指引等纪律全在插件内，这里不重复实现；
    - 幂等锚 uri = upload://sha256:<内容散列>（与图片同 scheme，mtype=text
      使 id 不同）——同文档重传同 id 零追加；原文落 files/ 保血统；
    - 产出 type=text / payload.text=提取正文 → refine 可直接消费（下游联通）。
    """
    from ...core.plugins.context import PluginError

    plugin = _load_doc_extract()
    raw_name = name.strip()
    suffix = Path(raw_name).suffix.lower()
    if suffix not in plugin.ALLOWED_SUFFIXES:
        raise ValueError(
            f"不是可提取的文档格式（{'/'.join(plugin.ALLOWED_SUFFIXES)}）: {raw_name or suffix!r}"
        )
    if license not in model.LICENSES:
        raise ValueError(f"license 必须是 {model.LICENSES} 之一: {license!r}")

    sha256 = hashlib.sha256(data).hexdigest()
    uri = f"upload://sha256:{sha256}"
    mid = model.make_material_id(UPLOAD_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}

    # 字节→临时文件（doc-extract.run 接路径入参；原始字节不落库，仅短暂驻留）
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp_path = Path(fh.name)
        try:
            out = plugin.run(
                {"doc_path": str(tmp_path), "title": Path(raw_name).stem},
                config_overrides or {},
                _DocExtractCtx(),
            )
        except PluginError as err:
            # 插件可读失败（扫描版/空结果/缺依赖）→ ValueError→server 400 原文呈现
            raise ValueError(str(err)) from None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    text = out["text"]
    pages = out.get("pages")
    from ...infra import media_store  # 函数体内导入（导入期解耦）

    media_store.store_original(mid, suffix.lstrip("."), data)
    meta: dict[str, Any] = {
        "bytes": len(data), "sha256": sha256, "suffix": suffix,
        "filename": raw_name, "license_note": note.strip(),
    }
    if pages is not None:
        meta["pages"] = pages
    material = model.make_material(
        mtype="text",
        source_id=UPLOAD_SOURCE_ID,
        uri=uri,
        title=Path(raw_name).stem or f"{sha256[:8]}{suffix}",
        license=license,
        payload={
            "text": text,
            "local_path": media_store.local_rel_path(mid, suffix.lstrip(".")),
            "meta": meta,
        },
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}


def add_uploaded(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """上传分流（DOC-1）：文档后缀走 doc-extract 提取，其余按图片全链。

    后缀知识收口本层——server 只管字节与 confirm 闸门，不重复解析文件名。
    文档后缀判定复用 doc-extract.ALLOWED_SUFFIXES（唯一事实源）。
    """
    suffix = Path(name.strip()).suffix.lower()
    if suffix in _load_doc_extract().ALLOWED_SUFFIXES:
        return add_uploaded_document(
            data, name=name, license=license, note=note, fetched_at=fetched_at
        )
    return add_uploaded_image(
        data, name=name, license=license, note=note, fetched_at=fetched_at
    )


# ── 视频登记（F61 登记制，零出网）──────────────────────────────────


def _normalize_video_url(url: str) -> tuple[str, str]:
    """视频 URL 校验+规范化（F61 提交期，全 400 语义）→ (uri, 规范化URL)。

    校验：scheme ∈ {http,https}；无 userinfo（user:pass@ 拒）；host 非回环/
    内网（localhost/私网/保留段——防协议注入，不做域名白名单：登记不是
    抓取，任意视频站可登记）。规范化：host 小写 + 去末尾斜杠 + query 原样
    保留（B 站 BV 号在 path、YT t= 在 query，不裁剪；fragment 丢弃）。
    uri = "video://" + 规范化URL（幂等锚，正典 §1.4）。"""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme 必须是 http/https: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不得携带 user:pass@ 用户信息")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("URL 主机不得是回环地址: localhost")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass  # 非 IP 字面量（域名）：登记零出网，域名解析不在此门
    else:
        if not addr.is_global:
            raise ValueError(f"URL 主机不得是回环/内网/保留地址: {host}")
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    path = parsed.path.rstrip("/")
    normalized = f"{parsed.scheme}://{netloc}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return f"video://{normalized}", normalized


def _video_platform(host: str) -> str:
    """域名表映射（F61）：bilibili.com/b23.tv→bilibili、youtube.com/youtu.be
    →youtube、其余→other（host 后缀匹配，大小写归一由 urlsplit 保证）。"""
    for domain, platform in _VIDEO_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return platform
    return "other"


def register_video(
    url: str,
    *,
    title: str = "",
    note: str = "",
    duration_text: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """视频登记（F61 登记制）：URL 校验→规范化幂等锚→落库。**零出网**——
    纯本地登记，无任何 HTTP 请求（不下载、不解码、无预览帧）。

    同 URL（规范化后）再登记 → dedup=true 返回既有行（零追加）；选填
    字段各 ≤200 字符；title 缺省取 URL 尾段。响应 {material, dedup}。"""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("缺少必填字段 url（http/https 视频 URL）")
    for field, value in (("title", title), ("note", note), ("duration_text", duration_text)):
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是字符串")
        if len(value) > _VIDEO_TEXT_MAX:
            raise ValueError(f"{field} 最长 {_VIDEO_TEXT_MAX} 字符: {len(value)}")
    uri, normalized = _normalize_video_url(url)
    mid = model.make_material_id(VIDEO_SOURCE_ID, "video", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}
    parsed = urlsplit(normalized)
    segments = [seg for seg in parsed.path.split("/") if seg]
    meta: dict[str, Any] = {"platform": _video_platform(parsed.hostname or "")}
    if duration_text.strip():
        meta["duration_text"] = duration_text.strip()
    if note.strip():
        meta["note"] = note.strip()
    material = model.make_material(
        mtype="video",
        source_id=VIDEO_SOURCE_ID,
        uri=uri,
        title=title.strip() or (segments[-1] if segments else parsed.hostname or normalized),
        payload={"url": url.strip(), "meta": meta},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}


# ── 素材文件服务（第二十一期 F60：三道门——服务层统一 ValueError=400 /
#    FileNotFoundError=404，表现层只做状态码映射）──────────────────────

_FILE_SUFFIX_WHITELIST = ("png", "jpg", "jpeg", "gif", "webp")  # svg 恒拒（assets.py 判例同源）


def read_material_file(material_id: str, variant: str = "thumb") -> tuple[bytes, str]:
    """按 id 服务素材文件字节（F60：thumb/full 两态）——服务端钉死三道门：

    1. 路径边界：payload.local_path 必须解析落在 ~/.musespiral/materials/files/
       内（../、绝对路径、symlink 逃逸全拒——_resolve_inside 同款纪律）；
    2. 后缀白名单：png/jpg/jpeg/gif/webp（svg 恒拒——同源 SVG 执行脚本
       可绕 confirm 直发动作，assets.py:22 判例原文）；
    3. 魔数复核：读文件头复核真实格式与后缀一致（防入库后改扩展名）。

    返回 (bytes, content_type)；Content-Type 按魔数真值。404 面（素材/
    文件不存在或已删）抛 FileNotFoundError，400 面抛 ValueError。"""
    if variant not in ("thumb", "full"):
        raise ValueError(f"variant 必须是 thumb/full 之一: {variant!r}")
    from ...infra import media_store  # 函数体内导入（导入期解耦）

    material = get_material(material_id)
    if material is None:
        raise FileNotFoundError(f"素材不存在或已删: {material_id!r}")
    if material.get("type") != "image":
        raise ValueError(f"素材无文件面（仅 image 类型可服务文件）: type={material.get('type')!r}")
    files = media_store.files_dir().resolve()
    if variant == "thumb":
        target = files / f"{material_id}.thumb.webp"
    else:
        rel = str((material.get("payload") or {}).get("local_path") or "")
        if not rel:
            raise ValueError("素材缺 payload.local_path（无文件可服务）")
        if "\x00" in rel or Path(rel).is_absolute():
            raise ValueError(f"local_path 非法（须为素材仓内相对路径）: {rel!r}")
        try:
            target = (config.home_dir() / rel).resolve()
        except (OSError, ValueError):
            raise ValueError(f"local_path 非法: {rel!r}") from None
        if not target.is_relative_to(files):
            raise ValueError("local_path 越界（必须落在素材文件仓 materials/files/ 内）")
        suffix = target.suffix.lower().lstrip(".")
        if suffix not in _FILE_SUFFIX_WHITELIST:
            raise ValueError(
                f"后缀不在白名单（{'/'.join(_FILE_SUFFIX_WHITELIST)}；svg 恒拒）: .{suffix}"
            )
    if not target.is_file():
        raise FileNotFoundError(f"素材文件缺失: {target.name}")
    try:
        body = target.read_bytes()
    except OSError:
        raise FileNotFoundError(f"素材文件读取失败: {target.name}") from None
    sniffed = media_store.sniff_format(body[:64])
    if not sniffed:
        raise ValueError(f"文件魔数无法识别（非图片字节）: {target.name}")
    if variant == "thumb":
        if sniffed != "webp":
            raise ValueError(f"缩略图魔数不符（应为 WebP，判定为 {sniffed}）: {target.name}")
        return body, "image/webp"
    suffix = target.suffix.lower().lstrip(".")
    expected = {"jpg", "jpeg"} if sniffed == "jpeg" else {sniffed}
    if suffix not in expected:
        raise ValueError(f"文件魔数与后缀不符（魔数判定为 {sniffed}，后缀 .{suffix}）: {target.name}")
    return body, media_store.ALLOWED_FORMATS[sniffed]
