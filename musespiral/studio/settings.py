"""Studio 设置面（第十八期 F46/F47）：GET /api/settings + POST kb/ai 两域写。

- GET /api/settings：只读复用既有数据面——kb 域（config.load_kb_sources 级联：
  kb_sources.json → kb_root.txt → 内置默认源表）；ai 域（infra.ai.profiles.
  list_profiles——key 恒为 has_key 布尔，明文/掩码不进响应；风格资产
  style_samples+habits 条数；prompts.d 覆盖表与插槽清单）。
- POST /api/settings/kb（confirm 已由 server 动作闸门统一强制）：root 与每条
  relpath 先过 config.validate_kb_root/validate_kb_source（存在性+越界）再落盘
  ——配置动作本身即放宽扫描边界，校验先于写，违规一律 400 可读错误。
- POST /api/settings/ai：action 判别——save_profile（api_key write-only：
  空串/缺省=保留旧值，永不回显）/set_default/set_route/save_style/
  save_prompt（方言守门保存时校验，违规 400 且不落盘）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..infra.ai import profiles as ai_profiles
from ..infra.ai import prompts as ai_prompts
from ..infra.ai.habits_inject import STYLE_SAMPLES_FILE, load_style_samples
from .security import _HttpError, _StudioContext, _http_400_on

# 档案名上限（JSON 键，防误贴长串）
_PROFILE_NAME_MAX = 64


def _need_str(data: dict[str, Any], key: str) -> str:
    """设置动作必填字符串字段（缺失/非字符串/空白 → 400）。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _HttpError(400, f"缺少必填字段 {key}（非空字符串）")
    return value.strip()


# ---- GET /api/settings（只读，两域 + 源连接域 M3）----


def _sources_domain() -> dict[str, Any]:
    """源连接域（M3「源连接」区块数据面）：源清单 + 每源素材计数。

    radar-hub 预置一条（正典 §1.1/F53）：注册表缺席时读侧合成默认条目
    （零写入——首次「验证/启停/刷新」动作才经 register_source 固化为
    权威 sources.json，与 kb 合成→权威切换同纪律）。
    """
    from ..infra.sources import registry as sources_registry  # 函数体内（导入期解耦）
    from ..services import materials_service, sources_service  # 同上

    rows = [dict(s) for s in sources_service.list_sources()]
    if not any(s.get("id") == "radarhub" for s in rows):
        rows.append(sources_registry.default_radarhub_source())
    counts: dict[str, int] = {}
    for material in materials_service.list_materials():
        sid = (material.get("origin") or {}).get("source_id")
        if isinstance(sid, str) and sid:
            counts[sid] = counts.get(sid, 0) + 1
    return {"list": rows, "counts": counts, "total_materials": sum(counts.values())}





def _settings_payload(ctx: _StudioContext) -> dict[str, Any]:
    kb = config.load_kb_sources()
    root_path = Path(kb["root"]) if kb["root"] else None
    root_ok = root_path is not None and root_path.is_dir()
    # 每源附 exists 校验态（根在且目录在 → True；根缺失时缺省——客户端显示空态）
    sources = [
        {**s, "exists": (root_path / s["relpath"]).is_dir() if root_ok else None}
        for s in kb["sources"]
    ]
    kb_payload = {
        "root": kb["root"],
        "root_exists": root_ok,
        "sources": sources,
        "origin": kb["origin"],
    }
    from ..core.habits import load_habits  # 函数体内导入：domain 层按需拉入

    st = ai_profiles.list_profiles()
    profiles_payload = {
        "default": st.get("default"),
        "routing": st.get("routing") or {},
        "tasks": st.get("tasks") or [],
        # key 只报 has_key 布尔（掩码串也不出——设置面无需掩码形态）
        "profiles": {
            name: {
                "base_url": p.get("base_url", ""),
                "model": p.get("model", ""),
                "has_key": bool(p.get("has_key")),
                "configured": bool(p.get("configured")),
            }
            for name, p in (st.get("profiles") or {}).items()
        },
    }
    return {
        "kb": kb_payload,
        "sources": _sources_domain(),
        "ai": {
            "profiles": profiles_payload,
            "style": {
                "content": load_style_samples(),
                "habits_count": len(load_habits()),
            },
            "prompts": {
                "tasks": list(ai_prompts.prompt_tasks()),
                "overrides": ai_prompts.list_overrides(),
                "slots": list(ai_prompts.PROMPT_SLOTS),
            },
        },
    }


# ---- POST /api/settings/kb ----


def _action_settings_kb(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """KB 域写入（root/sources 均可选字段，缺省保持现值；全量保存语义）。"""
    current = config.load_kb_sources()
    root_str: str | None = current["root"]
    root_raw = data.get("root")
    if root_raw is not None:
        if not isinstance(root_raw, str) or not root_raw.strip():
            raise _HttpError(400, "root 必须是非空绝对路径字符串")
        try:
            root_str = str(config.validate_kb_root(root_raw))
        except ValueError as err:
            raise _HttpError(400, str(err)) from None
    if not root_str:
        raise _HttpError(400, "KB 根未配置——先提供 root（musespiral config kb set-root 对等）")
    root = Path(root_str)
    sources_raw = data.get("sources")
    if sources_raw is None:
        sources = current["sources"]
    else:
        if not isinstance(sources_raw, list):
            raise _HttpError(400, "sources 必须是数组 [{key,label,relpath}]")
        sources = []
        seen: set[str] = set()
        for i, item in enumerate(sources_raw):
            if not isinstance(item, dict):
                raise _HttpError(400, f"sources[{i}] 必须是对象 {{key,label,relpath}}")
            key = _need_str(item, "key")
            if key in seen:
                raise _HttpError(400, f"sources key 重复: {key}")
            seen.add(key)
            label = _need_str(item, "label")
            relpath = _need_str(item, "relpath")
            try:
                config.validate_kb_source(root, relpath)
            except ValueError as err:
                raise _HttpError(400, f"[{key}] {err}") from None
            sources.append({"key": key, "label": label, "relpath": relpath})
    config.save_kb_sources(root_str, sources)
    return {"ok": True, "root": root_str, "sources": sources}


# ---- POST /api/settings/ai ----


def _action_settings_ai(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """AI 域写入（action 判别；key write-only，响应只回名与布尔态）。"""
    action = data.get("action")
    if action == "save_profile":
        return _save_profile(data)
    if action == "set_default":
        name = _need_str(data, "name")
        with _http_400_on(ai_profiles.AIError):
            ai_profiles.set_default_profile(name)
        return {"ok": True, "default": name}
    if action == "set_route":
        task = _need_str(data, "task")
        profile = _need_str(data, "profile")
        with _http_400_on(ai_profiles.AIError):
            ai_profiles.set_route(task, profile)
        return {"ok": True, "routing": {task: profile}}
    if action == "save_style":
        return _save_style(data)
    if action == "save_prompt":
        return _save_prompt(data)
    raise _HttpError(
        400, "action 必须是 save_profile/set_default/set_route/save_style/save_prompt 之一"
    )


def _save_profile(data: dict[str, Any]) -> dict[str, Any]:
    name = _need_str(data, "name")
    if len(name) > _PROFILE_NAME_MAX:
        raise _HttpError(400, f"档案名过长（≤{_PROFILE_NAME_MAX} 字符）")
    base_url = _need_str(data, "base_url")
    model = _need_str(data, "model")
    # write-only key：空串/缺省=保留旧值（本地端点可无 key；换 key 才传新值）
    api_key_raw = data.get("api_key")
    if api_key_raw is not None and not isinstance(api_key_raw, str):
        raise _HttpError(400, "api_key 必须是字符串（空串=保留旧值）")
    existing = ai_profiles.load_profile(name)
    api_key = api_key_raw if api_key_raw else str(existing.get("api_key") or "")
    ai_profiles.save_ai_profile(name, base_url, api_key, model, set_default=False)
    return {"ok": True, "name": name, "has_key": bool(api_key)}


def _save_style(data: dict[str, Any]) -> dict[str, Any]:
    content = data.get("content")
    if not isinstance(content, str):
        raise _HttpError(400, "content 必须是字符串")
    path = config.home_dir() / STYLE_SAMPLES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "saved": STYLE_SAMPLES_FILE}


def _save_prompt(data: dict[str, Any]) -> dict[str, Any]:
    task = _need_str(data, "task")
    content = data.get("content")
    if not isinstance(content, str):
        raise _HttpError(400, "content 必须是字符串")
    if task not in ai_prompts.prompt_tasks():
        raise _HttpError(400, f"未知任务: {task}（可用: {'/'.join(ai_prompts.prompt_tasks())}）")
    with _http_400_on(ValueError):
        path = ai_prompts.save_override(task, content)
    return {"ok": True, "task": task, "saved": path.name}
