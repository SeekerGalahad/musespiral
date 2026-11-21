"""创作台 POST 动作（第十八期 F48-F52；M2 后端接线——ui 不做）。

六动作（confirm:true 由 server.py 动作层统一强制）：
  /api/compose/new      建会话（同步瞬时）
  /api/compose/add      素材入池（同步瞬时；章节勾选复用 scanner）
  /api/compose/remove_source 素材撤池（F-C 2026-11-18，同步瞬时）
  /api/compose/generate 按反馈再生成（长动作 → job compose_generate，
                         流式落 rounds/N.md，产物在 job.result）
  /api/compose/decide   逐块三态裁决（同步瞬时；feedback.jsonl 追加）
  /api/compose/adopt    采纳入稿（同步瞬时：合并+版本池+habits+正稿入队列）
  /api/compose/outline  G-T2 大纲模式（generate/update/approve/expand 四动作）
  /api/compose/rolling-summary  G-T4 滚动摘要（刷新 / 用户手改两形态）
  /api/compose/novel-summary    E-5 小说上下文（前情提要刷新/手改 + 人物卡
                                建卡/挂载/摘下；action 分流，见函数 docstring）

薄壳纪律同 actions.py：参数解析 + 提交期同步校验（400/{ok:false} 契约）
与结果格式化；编排全部在 services/compose_service（函数体内导入——导入
期解耦，R7/F21 同款先例）。业务失败转 _error_payload（可读不 500）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
)


def _service():
    from ..services import compose_service  # 函数体内导入（导入期解耦）

    return compose_service


def _action_compose_new(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """建会话：slug 白名单/重名拒绝在服务层（ComposeError → ok:false）。

    `kind?`（E-5 §3.6）：article 缺省 / novel——建会话时直接起小说模式
    （也可建后经 `compose/novel-summary` 的 `action=kind` 切换）。
    """
    slug = _require_str(data, "slug")
    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise _HttpError(400, "title 必须是字符串或 null")
    kind = data.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise _HttpError(400, "kind 必须是字符串或 null")
    try:
        d = _service().new_session(slug, title or slug, root=ctx.root, **({"kind": kind} if kind else {}))
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "slug": slug, "dir": _rel(ctx.root, d)}


def _action_compose_add(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """素材入池：path（绝对路径或相对路径，相对=KB 根优先，其次工作目录）
    / material_id（素材库直引）/ article_slug（文章根 <slug>.md）三选一
    （N-2「引用自己」2026-11-21 扩展——恰给一个，多给/不给 → 400）。"""
    slug = _require_str(data, "slug")
    sections = data.get("sections")
    if sections is not None and (
        not isinstance(sections, list) or not all(isinstance(s, str) and s.strip() for s in sections)
    ):
        raise _HttpError(400, "sections 必须是字符串数组或 null")
    material_id = data.get("material_id")
    article_slug = data.get("article_slug")
    given = [
        key
        for key, value in (
            ("path", data.get("path")),
            ("material_id", material_id),
            ("article_slug", article_slug),
        )
        if isinstance(value, str) and value.strip()
    ]
    if len(given) != 1:
        raise _HttpError(400, "素材来源三选一：path / material_id / article_slug（恰好给一个）")
    sections_arg = [s.strip() for s in sections] if sections else None
    try:
        if "material_id" in given:
            info = _service().add_source(
                slug, None, None, root=ctx.root, material_id=material_id.strip()
            )
        elif "article_slug" in given:
            info = _service().add_source(
                slug, None, None, root=ctx.root, article_slug=article_slug.strip()
            )
        else:
            src = _resolve_source(ctx, data["path"].strip())
            info = _service().add_source(slug, src, sections_arg, root=ctx.root)
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    # known_sections 透出：M3 创作台章节勾选树的数据面（详情载荷只回已选章节）
    out = {
        "ok": True,
        "slug": slug,
        "path": info["path"],
        "sections": info["sections"],
        "known_sections": info["known_sections"],
    }
    if info.get("material_id"):
        out["material_id"] = info["material_id"]  # 素材直引条回 id（前端定位详情）
    return out


def _resolve_source(ctx: _StudioContext, raw: str) -> Path:
    """素材路径解析：绝对路径直取；相对路径按 KB 根（已登记）→ 工作目录
    回退解析。素材天然多为知识库文档（工作目录之外），不做 root 内穿越
    校验——存在性/后缀校验在服务层。"""
    from .. import config

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    kb = config.knowledge_root()
    base = kb if kb is not None and kb.is_dir() else ctx.root
    return (base / candidate).resolve()


def _action_compose_remove_source(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """素材撤池（F-C 2026-11-18，同步瞬时）：与 add 同口径路径解析（相对=
    KB 根优先，其次工作目录）；不要求素材文件仍存在。sections 透出=前端
    成功 toast（「已选 N 章」）与池行恢复数据面；confirm 连坐文案取会话
    详情 sources.sections（素材勾选章节）+ 轮次数——'source_ref' 块归因在
    生产代码无赋值处（blocks.py ORIGIN_SOURCE 仅预留），原「草稿中 N 块」
    计数恒 0 为死代码已删（2026-11-18 集成修复判例）。"""
    slug = _require_str(data, "slug")
    path_raw = _require_str(data, "path")
    # 素材库直引条（N-2）：material://<id> 虚拟定位符原样透传（不经文件
    # 路径解析——store.remove_source 同分支按池内 path 直匹）
    raw = path_raw.strip()
    src = raw if raw.startswith("material://") else _resolve_source(ctx, path_raw)
    try:
        info = _service().remove_source(slug, src, root=ctx.root)
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    return {
        "ok": True,
        "slug": slug,
        "path": info["path"],
        "sections": info["sections"],
        "sources_left": info["sources_left"],
    }


def _action_compose_generate(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """按反馈再生成（长动作 → job compose_generate；结果在 job.result）。

    job 提交走 actions._submit 统一件（异常→JobFailure 已脱敏）。

    E-4 流式骨架批（2026-11-19，正典 §3.4「不是干等」）：闭包给
    generate 传 on_delta 增量回调——SSE delta 到达即累积，按「新块出现
    或 ≥0.7s」节流把 ``partial``（[{kind,text}] 展示面）与 phase
    （"生成中 N 块"）合并进 job 行（JobStore 行级原子更新，模块锁串行）；
    前端 600ms 轮询 /api/jobs/<id> 在生成期即见骨架逐块长出。非 SSE 端点
    无增量可回调（chat_stream JSON 兜底）——partial 不发布，前端降级为
    完成后错峰揭示，两端语义自洽。终态 result 仍只含 {round, blocks}
    （partial 字段留存于行内，无既有消费方读取，零干扰）。
    """
    import time

    from .actions import _runner, _submit

    svc = _service()
    slug = _require_str(data, "slug")

    def _body(job_id: str) -> dict[str, Any]:
        buf: list[str] = []
        last_n = -1
        last_ts = 0.0

        def _on_delta(delta: str) -> None:
            nonlocal last_n, last_ts
            buf.append(delta)
            partial = svc.blocks_so_far("".join(buf))
            now = time.monotonic()
            if len(partial) == last_n and now - last_ts < 0.7:
                return  # 节流：块数没变且距上次发布 <0.7s（对齐前端轮询节拍）
            last_n, last_ts = len(partial), now
            _runner().store.update(
                job_id,
                phase=f"生成中 {len(partial)} 块",
                partial=partial,
            )

        result = svc.generate(
            slug, root=ctx.root, job_id=job_id, on_delta=_on_delta)
        # raw 落盘路径不进 job.result（绝对路径不透出；UI 经会话详情读取）
        # E-5（N-1）：conflicts 随 job.result 回传——前端 cnGenerate 读
        # j.result.conflicts 渲染⚠️提示条（只提示不拦截）。snippet 是截断行
        # 文本、无绝对路径，与 round/blocks 同安全面。
        return {"round": result["round"], "blocks": result["blocks"],
                "conflicts": result.get("conflicts") or []}

    return _submit(ctx, "compose_generate", {"slug": slug}, _body)


def _action_compose_decide(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """逐块三态裁决（同步瞬时）。"""
    slug = _require_str(data, "slug")
    block_id = _require_str(data, "block")
    action = _require_str(data, "action")
    round_raw = data.get("round")
    if not isinstance(round_raw, int) or round_raw < 1:
        raise _HttpError(400, "round 必须是正整数（轮次号）")
    text = data.get("text")
    direction = data.get("direction")
    for key, value in (("text", text), ("direction", direction)):
        if value is not None and not isinstance(value, str):
            raise _HttpError(400, f"{key} 必须是字符串或 null")
    try:
        record = _service().decide(
            slug, round_raw, block_id, action, text=text, direction=direction, root=ctx.root
        )
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "slug": slug, "round": round_raw, "decision": record}


def _action_compose_adopt(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """采纳入稿（同步瞬时：合并+版本池+habits+正稿入队列）。"""
    slug = _require_str(data, "slug")
    try:
        result = _service().adopt(slug, root=ctx.root)
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    result["article"] = _rel(ctx.root, Path(result["article"]))
    result["ok"] = True
    return result


def _action_compose_outline(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """G-T2 大纲模式统一端点（§三）：action=generate|update|approve|expand。

    - generate：AI 生成大纲（同步瞬时；chat 走 compose 任务键）
    - update：单节改 title/direction/target_chars（section + 可选字段）
    - approve：逐节裁决（section + approved:bool）——未裁决节不可展开
    - expand：展开已裁决节（section；产块进正常协议，prompt 含 direction+已展开节摘要）
    """
    slug = _require_str(data, "slug")
    action = _require_str(data, "action")
    if action not in ("generate", "update", "approve", "expand"):
        raise _HttpError(400, "action 必须是 generate|update|approve|expand")
    svc = _service()
    try:
        if action == "generate":
            return {"ok": True, "outline": svc.outline_generate(slug, root=ctx.root)}
        section = _require_str(data, "section")
        if action == "update":
            changes = {}
            for key in ("title", "direction", "target_chars"):
                if key in data:
                    changes[key] = data[key]
            return {"ok": True, "outline": svc.outline_update_section(
                slug, section, root=ctx.root, **changes)}
        if action == "approve":
            approved = data.get("approved", True)
            if not isinstance(approved, bool):
                raise _HttpError(400, "approved 必须是布尔值")
            return {"ok": True, "outline": svc.outline_approve(
                slug, section, approved, root=ctx.root)}
        result = svc.expand_section(slug, section, root=ctx.root)
        result["ok"] = True
        return result
    except _HttpError:
        raise
    except Exception as exc:
        return _error_payload(exc, ctx.root)


def _action_compose_dual(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """G-T3 双模型对比出稿统一端点（§四；同步瞬时、零 job）。

    同 `compose/outline` 的多形态纪律——一个路由、按 `action` 分流（契约锁只
    增一条 POST：G-T3 = compose/dual）：

      - `generate`（缺省）：`{slug, profile_a, profile_b}` → 读会话素材 → 同一
        prompt 两个档案并行 chat(task=compose) → `{ok, text_a, text_b, usage_a,
        usage_b}`。仅返回两版原文供前端双栏对照，不落 rounds/N.md。
      - `pick`：`{slug, text, pick:a|b}` → 选定稿替换末轮块进正常三态裁决流。
      - `keep`：`{slug, text_a, text_b, profile_a?, profile_b?}` → 各存独立草稿。

    400（表单校验）：action 枚举外 / 缺 slug，或 generate 缺 profile_a/profile_b
    或 profile 不是 ai.json 已配置档案（`load_profile` 无 base_url/model → 400，
    不允许静默按路由默认档案跑——对比前提是两个档案确实不同且都已配置）。
    200 {ok:false}：会话不存在/已采纳、AI 未配置或调用失败、空响应。
    """
    from ..infra.ai import profiles as ai_profiles

    action = data.get("action", "generate")
    if action not in ("generate", "pick", "keep"):
        raise _HttpError(400, "action 必须是 generate|pick|keep")
    slug = _require_str(data, "slug")
    svc = _service()
    try:
        if action == "generate":
            profile_a = _require_str(data, "profile_a")
            profile_b = _require_str(data, "profile_b")
            for label, name in (("profile_a", profile_a), ("profile_b", profile_b)):
                cfg = ai_profiles.load_profile(name)
                if not (cfg.get("base_url") and cfg.get("model")):
                    raise _HttpError(
                        400,
                        f"{label} 不是已配置的 AI 档案: {name!r}（先在设置面配置该档案）",
                    )
            return svc.dual_compose(slug, profile_a, profile_b, root=ctx.root)
        if action == "pick":
            text = _require_str(data, "text")
            pick = data.get("pick", "a")
            if pick not in ("a", "b"):
                raise _HttpError(400, "pick 必须是 a 或 b")
            return svc.apply_dual_pick(slug, text, root=ctx.root, pick=pick)
        # keep
        text_a = _require_str(data, "text_a")
        text_b = _require_str(data, "text_b")
        profile_a = data.get("profile_a")
        profile_b = data.get("profile_b")
        for key, value in (("profile_a", profile_a), ("profile_b", profile_b)):
            if value is not None and not isinstance(value, str):
                raise _HttpError(400, f"{key} 必须是字符串")
        result = svc.keep_both_drafts(
            slug, text_a, text_b, root=ctx.root,
            profile_a=profile_a or "", profile_b=profile_b or "",
        )
        result["drafts"] = [_rel(ctx.root, Path(p)) for p in result["drafts"]]
        return result
    except _HttpError:
        raise
    except Exception as exc:
        return _error_payload(exc, ctx.root)


def _action_compose_rolling_summary(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """G-T4 滚动摘要手动刷新/用户编辑（§五；同步瞬时，零 job）。

    两形态（同一端点，按载荷分流）：
      - `{slug}` → 刷新：读已写全文 → chat(task=rolling_summary) → <200 字
        脉络 → 落 session.json rolling_summary（右栏「🔄 重新生成」）；
      - `{slug, summary:"…"}` → 用户手改（§五「用户可修正 AI 的理解」）：
        不调 AI、不推进档位——手改即定稿，下次跨档才自动重算。

    同步理由同 refine preview / editor_ai（本地单人 Studio 是
    ThreadingHTTPServer，同步 AI 调用不阻塞其他请求）；adopt 后的自动
    触发走服务层后台线程，与此端点互不影响。
    """
    slug = _require_str(data, "slug")
    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise _HttpError(400, "summary 必须是字符串")
    svc = _service()
    try:
        if summary is not None:
            result = svc.set_rolling_summary(slug, summary, root=ctx.root)
            return {"ok": True, "slug": slug, **result, "edited": True}
        result = svc.refresh_rolling_summary(slug, root=ctx.root)
        return {"ok": True, "slug": slug, **result, "edited": False}
    except Exception as exc:
        return _error_payload(exc, ctx.root)


def _action_compose_novel_summary(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """E-5 小说上下文统一端点（评审 §3.6「I3 小说上下文管理」；同步瞬时，零 job）。

    **一个路由按 action 分流**（同 `compose/outline` 的多形态纪律——任务书
    契约锁 POST 只 +1）：

    | action | 载荷 | 语义 |
    |---|---|---|
    | 缺省（无 action 且无 summary） | `{slug}` | **刷新前情提要**：读前 N-1 章 → `chat(task="novel_summary")` → <300 字 → 落 session.json `novel_summary`；末章=当前章不进摘要 |
    | `edit`（或给 `summary`） | `{slug, summary:"…"}` | **用户手改**：不调 AI、不推进已摘要章数；空串=清空（后续 prompt 不再注入） |
    | `kind` | `{slug, kind:"article"\\|"novel"}` | **会话种类切换**（novel 才显示/注入小说上下文） |
    | `character_add` | `{slug, id:<素材id>}` | **挂卡**（幂等；素材须 meta.sub_type=character） |
    | `character_remove` | `{slug, id:<素材id>}` | **摘卡**（只解挂载，不删素材） |
    | `character_create` | `{name, role?, appearance?, speech_style?, key_events?}` | **建卡**（落素材库 sub_type=character；同名同内容 dedup，异内容拒绝） |

    **与滚动摘要并存**：本端点只写 novel 域字段，绝不触碰 `rolling_summary`
    （§3.6 两摘要各记各的：rolling=近期脉络、novel=此前各章情节账本）。
    """
    from ..services import materials_service

    slug = data.get("slug")
    action = data.get("action")
    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise _HttpError(400, "summary 必须是字符串")
    if action is not None and not isinstance(action, str):
        raise _HttpError(400, "action 必须是字符串")
    if action not in (None, "refresh", "edit", "kind", "character_add",
                      "character_remove", "character_create"):
        raise _HttpError(
            400,
            "action 必须是 refresh/edit/kind/character_add/character_remove/character_create 之一",
        )
    svc = _service()
    try:
        # 建卡不依赖会话 slug（卡的家在素材库，先建卡后挂载）
        if action == "character_create":
            name = _require_str(data, "name")
            for key in ("role", "appearance", "speech_style"):
                value = data.get(key)
                if value is not None and not isinstance(value, str):
                    raise _HttpError(400, f"{key} 必须是字符串")
            events = data.get("key_events")
            if events is not None and (
                not isinstance(events, list) or not all(isinstance(e, str) for e in events)
            ):
                raise _HttpError(400, "key_events 必须是字符串数组")
            created = materials_service.add_character(
                name,
                role=data.get("role") or "",
                appearance=data.get("appearance") or "",
                speech_style=data.get("speech_style") or "",
                key_events=list(events or []),
            )
            return {
                "ok": True,
                "action": action,
                "material": created["material"],
                "dedup": created["dedup"],
                "character": materials_service.get_character(created["material"]["id"]),
            }
        slug = _require_str(data, "slug")
        if action == "kind":
            kind = _require_str(data, "kind")
            return {"ok": True, "action": action,
                    **svc.set_session_kind(slug, kind, root=ctx.root)}
        if action in ("character_add", "character_remove"):
            material_id = _require_str(data, "id")
            if action == "character_add":
                result = svc.add_character(slug, material_id, root=ctx.root)
            else:
                result = svc.remove_character(slug, material_id, root=ctx.root)
            return {"ok": True, "action": action, **result}
        # 前情提要两形态（同 rolling-summary 的载荷分流纪律）
        if action == "edit" or summary is not None:
            result = svc.set_novel_summary(slug, summary or "", root=ctx.root)
            return {"ok": True, "slug": slug, "action": "edit", **result, "edited": True}
        result = svc.refresh_novel_summary(slug, root=ctx.root)
        return {"ok": True, "slug": slug, "action": "refresh", **result, "edited": False}
    except _HttpError:
        raise
    except Exception as exc:
        return _error_payload(exc, ctx.root)
