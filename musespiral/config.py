"""Configuration management — credentials, paths, defaults.

State lives outside the repo: ~/.musespiral/  (override with MUSESPIRAL_HOME env var).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home_dir() -> Path:
    """Return the musespiral state directory (outside repo)."""
    env = os.environ.get("MUSESPIRAL_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".musespiral"


def _ensure_dir() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
PUBLISH_LOG = "publish_log.jsonl"
DEFAULT_THEME = "lapis"
DEFAULT_CODE_THEME = "monokai"

# 知识库根登记文件（第十二期 F26/F30 选题引擎）：~/.musespiral/kb_root.txt 一行
# 绝对路径，指向知识库目录（如 <repo>/docs/ai-knowledge-base）。Studio 侧
# /api/discover 的穿越防护与 discover_service 的默认扫描边界都以它为准。
KB_ROOT_FILE = "kb_root.txt"

# 第十八期 F46：kb_sources.json 成为 root+sources 的单一持有者
# （{"root": "<绝对路径>", "sources": [{"key","label","relpath"}, …]}）；
# kb_root.txt 降为读侧兼容（新文件缺席或 root 为空时回退读根，零迁移）。
KB_SOURCES_FILE = "kb_sources.json"

# 内置默认源表（F46：kb_sources.json 缺席时的源清单缺省——chips/CLI 零配置
# 即有可用分档；与拆分前发现页硬编码四 chips 同集：源码分析/学习路径/模式家族）
_KB_DEFAULT_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("13-source-analysis", "源码分析", "13-source-analysis"),
    ("11-learning-path", "学习路径", "11-learning-path"),
    ("pattern-family", "模式家族", "13-source-analysis/模式家族"),
)

# F45/F47 方言守门禁用字样（小写；大小写不敏感匹配）——产品级单一事实源：
# 运行时（prompts.d 覆盖保存校验，infra/ai/prompts.save_override）与测试守门
# （tests/test_architecture.py 的 scan_prompt_dialect）共用同一表防两处漂移。
# 表值本身是模型/供应商名，故必须放在方言扫描范围（core/adaptation、
# core/quality、infra/ai 与点名 services 文件）之外的 config 顶层。
DIALECT_FORBIDDEN: tuple[str, ...] = ("gpt", "glm", "qwen", "claude", "deepseek", "gemini")


def kb_default_sources() -> list[dict[str, str]]:
    """内置默认源表（每次新 list，防调用方污染常量）。"""
    return [
        {"key": k, "label": label, "relpath": rel} for k, label, rel in _KB_DEFAULT_SOURCES
    ]


def _root_from_legacy() -> Path | None:
    """旧 kb_root.txt 读根（F26 原语义逐字节保留：一行路径，缺失/空/坏 → None）。"""
    path = home_dir() / KB_ROOT_FILE
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        # resolve：登记路径经符号链接（如 /tmp → /private/tmp）也须与
        # security._resolve_inside 的 resolve 后子路径可比
        return Path(raw).expanduser().resolve()
    except (OSError, ValueError):
        return None


def kb_sources_path() -> Path:
    """kb_sources.json 路径（随 MUSESPIRAL_HOME 解析）。"""
    return home_dir() / KB_SOURCES_FILE


def _read_kb_sources_file() -> dict[str, Any] | None:
    """读 kb_sources.json；缺失/坏 JSON/非对象 → None（文件可人工编辑，宽容）。"""
    try:
        raw = json.loads(kb_sources_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _normalize_kb_sources(raw: Any) -> list[dict[str, str]]:
    """源表形状归一：[{key,label,relpath}] 三值均非空 str 才收，坏行丢弃。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key, label, rel = item.get("key"), item.get("label"), item.get("relpath")
        if all(isinstance(v, str) and v.strip() for v in (key, label, rel)):
            out.append({"key": key.strip(), "label": label.strip(), "relpath": rel.strip()})
    return out


def load_kb_sources() -> dict[str, Any]:
    """读 KB 源设置全口径（F46 读侧级联）。

    - kb_sources.json 存在 → 权威：sources 以文件为准（空表=用户清空）；
      root 为空时回退 kb_root.txt 读根（只写了源清单未 set-root 的中间态）。
    - 文件缺席 → kb_root.txt（root）+ 内置默认源表。
    origin 标注生效来源（kb_sources.json / kb_root.txt / builtin）供 UI/CLI 展示。
    """
    raw = _read_kb_sources_file()
    if raw is not None:
        root_raw = str(raw.get("root") or "").strip()
        if root_raw:
            try:
                root: str | None = str(Path(root_raw).expanduser().resolve())
            except (OSError, ValueError):
                root = None
        else:
            legacy = _root_from_legacy()
            root = str(legacy) if legacy is not None else None
        return {"root": root, "sources": _normalize_kb_sources(raw.get("sources")), "origin": KB_SOURCES_FILE}
    legacy = _root_from_legacy()
    return {
        "root": str(legacy) if legacy is not None else None,
        "sources": kb_default_sources(),
        "origin": KB_ROOT_FILE if legacy is not None else "builtin",
    }


def save_kb_sources(root: str | None, sources: list[dict[str, str]]) -> Path:
    """写 kb_sources.json（root=None 落 null——读侧回退 kb_root.txt）。"""
    data = {"root": root, "sources": _normalize_kb_sources(sources)}
    _ensure_dir()
    path = kb_sources_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate_kb_root(raw: str) -> Path:
    """KB 根写入校验：绝对路径 + 存在目录；违规 raise ValueError（API/CLI 共用）。"""
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        raise ValueError("root 必须是绝对路径（禁止相对路径——随 CWD 漂移）")
    try:
        resolved = p.resolve()
    except (OSError, ValueError):
        raise ValueError(f"root 路径非法: {raw}") from None
    if not resolved.is_dir():
        raise ValueError(f"root 目录不存在: {raw}")
    return resolved


def validate_kb_source(root: Path, relpath: str) -> Path:
    """源 relpath 写入校验（存在性+根内越界拒绝）；违规 raise ValueError。"""
    raw = relpath.strip()
    if not raw or "\x00" in raw:
        raise ValueError(f"relpath 非法: {relpath!r}")
    if Path(raw).is_absolute():
        raise ValueError(f"relpath 必须是 KB 根内相对路径（禁止绝对路径）: {raw}")
    if ".." in Path(raw).parts:
        raise ValueError(f"relpath 含上跳段（禁止 ..）: {raw}")
    try:
        resolved = (root / raw).resolve()
    except (OSError, ValueError):
        raise ValueError(f"relpath 路径非法: {raw}") from None
    if not resolved.is_relative_to(root):
        raise ValueError(f"relpath 越界（必须仍在 KB 根内）: {raw}")
    if not resolved.is_dir():
        raise ValueError(f"relpath 目录不存在: {raw}")
    return resolved


def knowledge_root() -> Path | None:
    """知识库根目录（选题引擎扫描边界）；未配置/为空返回 None（不抛错）。

    F46 起优先取 kb_sources.json 的 root；该文件缺席或 root 字段为空时回退
    旧 kb_root.txt（读侧兼容——既有登记零迁移继续生效）。随 MUSESPIRAL_HOME 解析
    （测试隔离走环境变量），Studio 据此判定「KB 根未配置 → 空候选不 500」
    并对 source 前缀做 root 内穿越校验。
    """
    raw = _read_kb_sources_file()
    if raw is not None:
        root_raw = str(raw.get("root") or "").strip()
        if root_raw:
            try:
                return Path(root_raw).expanduser().resolve()
            except (OSError, ValueError):
                return None
    return _root_from_legacy()

# WeChat API constraints
TITLE_MAX_BYTES = 64
DIGEST_MAX_BYTES = 120
IMAGE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
WECHAT_API_BASE = "https://api.weixin.qq.com"


def load_credentials() -> dict[str, str]:
    """Load WeChat appid/secret from ~/.musespiral/credentials.json."""
    path = _ensure_dir() / CREDENTIALS_FILE
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("accounts", {}).get(data.get("default", "default"), {})


def save_credentials(account: str, appid: str, secret: str, *, set_default: bool = True) -> None:
    """Save WeChat credentials for an account."""
    path = _ensure_dir() / CREDENTIALS_FILE
    data: dict[str, Any] = {"accounts": {}, "default": account}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("accounts", {})[account] = {"appid": appid, "secret": secret}
    if set_default:
        data["default"] = account
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_accounts() -> list[str]:
    path = home_dir() / CREDENTIALS_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("accounts", {}).keys())
