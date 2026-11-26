"""R4 任务存储 —— ~/.musespiral/jobs.jsonl（追加写 + 行级更新，字节级保留非候选行）。

Phase C 自 musespiral/core/jobs/ 提升为顶层 jobs/ 包（正典 §八 Phase C：从
core 提升为独立 infra 模块），行为零变更——相对导入层级 from ... import
config → from .. import config；行级原子写共用件改经同层
infra/storage/filesystem（正典 §2.3 infra/storage/，与 publish_log 共用）。

行结构（正典 §四-R4 契约）：
  {id: 短uuid, kind: adapt|pack|publish|batch_adapt|status,
   params: dict, status: queued|running|done|failed, phase: 进度标记（可选）,
   created/started/finished: ISO 秒级时间戳, error: 脱敏文本, result: 任意 JSON}

写路径纪律（复用 F17 analytics.sync_reads 的字节级经验）：
  - 建行走 append（O(1)，不重写全文件）；
  - 行级更新按字节读入 → 只改目标行 → 临时文件 + os.replace 原子重写；
    非候选行（非 UTF-8 字节行、损坏 JSON 行、CRLF 行尾）按原始字节整行
    保留，绝不经 errors="replace" 的 U+FFFD 不可逆替换；
  - 全部操作走模块级 threading.Lock（单写者串行，正典 §八 并发缓解）；
  - 路径每次现读 config.home_dir()——MUSESPIRAL_HOME 测试隔离即时生效。

kind 含 "status"：R3 状态动作契约上是合法 kind，但 Studio 侧按正典 §三-3
保持同步执行（瞬时动作不经任务层）；该值留给未来异步化时复用。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..infra.storage.filesystem import atomic_write_bytes as _atomic_write, line_tail as _line_tail

JOBS_FILE = "jobs.jsonl"

# 合法任务类型 / 任务状态（写入侧校验；读取侧容忍历史行）
# sync_stats：第十四期 F39 Studio 回采入口（stats --sync 既有链路进 job 层）
# compose_generate：第十八期 F48 创作台按反馈再生成（流式落 rounds/N.md）
# ingest：第二十期 F54 采集任务（源刷新→materials，services/ingest_service）
# clip：B 采集扩展 §1.3（F66/F67 剪藏与手动爬取的网络长动作进 job 保 UI 响应）
# visual_export：Phase C 视觉创作（F71/F72/F73 导出 PNG——B 路浏览器光栅秒级；
#   无浏览器降 C 路属 result.route 注记非 error）
# agent_run：Phase D F75（多步编排长动作——run 目录存证、jobs result 只放计数
#   无正文，设计 §1.1/§四-Q2）
# topic_radar：Phase D F78（三源选题聚合 + 可选 AI 增强；快照写 latest.json）
# pipeline：E1 金路径（2026-11-17 组合批）——check→title→cover→adapt→pack
#   混合管线（LLM 步=title+adapt：title 未配置走启发式；adapt 未配置时
#   xhs/zhihu 启发式降级、channels/douyin 硬失败可读引导，check/cover/pack
#   确定性——2026-11-18 勘正，原「其余确定性」与 generator 实现不符）；
#   串行调既有 service 函数，
#   每步 {step,status,detail,artifacts} 入 result.steps、失败即停（§3.7）
# book_search / book_fetch：R3 书源接驳（2026-11-18，架构设计 §四 R3 行）
#   ——book_search=引擎代理聚合搜索（长任务，submit 前探活缺席零 job）；
#   book_fetch=选章取文入素材库+R3.5 提炼管线（AI 失败章降级原文入库，
#   部分成功是合法终态——result 明列 M 完成/N 失败；正文不进 result，
#   原文在 ~/.musespiral/book_cache/ 章节缓存）
# tts_generate：G-T5 TTS 朗读（2026-11-19，调研吸收 §六）——正稿→朗读脚本
#   →edge-tts（微软免费端点，零 API key）合成 MP3 落文章目录；网络长动作
#   进 job 保 UI 响应，result={output,voice,rate,bytes,chars}
# plugin_pipeline：插件流水线（插件系统设计 §5.3，PL-2，18→19）——整条
#   用户可编排流水线只占 1 个 kind（内部节点不是独立 job，节点生命周期
#   归流水线管理）；steps 步级记账 {step,status,detail,artifacts}、失败
#   即停、正文在 ~/.musespiral/pipeline_runs/<run_id>/（result 计数无正文）
# quality_loop：P1-A 质量循环（2026-11-23，契约 §3.26）——check→AI 修复
#   →再 check 多轮长动作（每轮一次 chat_policed(task=rewrite)）；修复稿
#   <stem>.quality_fix.md 预览（原文零触碰）；result={rounds,fixes_applied,
#   final_result,output?}（19→20）
# localbook_fetch：B1 本地书籍选章素材化（2026-11-26，契约 §3.27）——逐章
#   现读现切（引用型，正文不落库）→ 每章一条 text 素材（payload 只放摘录，
#   全文留在用户磁盘）；幂等同章 skip；result={chapters_total,added_n,
#   skipped_n,skipped,failed,materials} 计数无正文（20→21）
KINDS: frozenset[str] = frozenset({"adapt", "pack", "publish", "batch_adapt", "status", "annotate_rewrite", "fetch", "sync_stats", "compose_generate", "ingest", "clip", "visual_export", "agent_run", "topic_radar", "pipeline", "book_search", "book_fetch", "tts_generate", "plugin_pipeline", "quality_loop", "localbook_fetch"})
STATUSES: frozenset[str] = frozenset({"queued", "running", "done", "failed"})

# 模块级锁：jobs.jsonl 的读改写全串行（跨 runner/CLI 实例共享——同进程内
# Studio 线程池与 CLI 各自的 JobStore 都走这一把锁）
_LOCK = threading.Lock()

# 秘密值打码（与 studio.security._SECRET_RE 同源规则；core 层不依赖 studio，
# 正则保持两处同步——改动须双侧对拍）
_SECRET_RE = re.compile(r"(?i)\b(access_token|appid|secret|api_key|token)=([^&\s'\"),]+)")
_MAX_ERROR_CHARS = 200


def jobs_path() -> Path:
    """任务日志路径：config.home_dir()/jobs.jsonl（每次现读，随 MUSESPIRAL_HOME 漂移）。"""
    return config.home_dir() / JOBS_FILE


def now_iso() -> str:
    """ISO 秒级时间戳（与 publish_log 的 ts 同口径）。"""
    return datetime.now().isoformat(timespec="seconds")


def sanitize_error(text: str) -> str:
    """错误文本脱敏：敏感值打码 + 截断（路径打码按 root 由调用方层完成，core 无 root）。"""
    text = (text or "").strip()
    text = _SECRET_RE.sub(r"\1=***", text)
    if len(text) > _MAX_ERROR_CHARS:
        text = text[:_MAX_ERROR_CHARS] + "…"
    return text


class JobStore:
    """jobs.jsonl 读写门面：损坏行容忍（跳过不崩），全部操作持模块级锁。"""

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any] | None:
        """单行解析；非 UTF-8 / 损坏 JSON / 非对象 → None（容忍，不抛错）。"""
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not text:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    # ---- 写 ----

    def create(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """追加一条 queued 行，返回新 job（id 为 8 位短 uuid，避让既有 id）。"""
        if kind not in KINDS:
            raise ValueError(f"未知任务类型: {kind}（可选: {'/'.join(sorted(KINDS))}）")
        with _LOCK:
            path = jobs_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: set[str] = set()
            if path.exists():
                for line in path.read_bytes().splitlines():
                    obj = self._parse(line)
                    if obj is not None and isinstance(obj.get("id"), str):
                        existing.add(obj["id"])
            job_id = uuid.uuid4().hex[:8]
            while job_id in existing:
                job_id = uuid.uuid4().hex[:8]
            job: dict[str, Any] = {
                "id": job_id,
                "kind": kind,
                "params": params,
                "status": "queued",
                "created": now_iso(),
            }
            with path.open("ab") as f:
                f.write(json.dumps(job, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
            return job

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        """行级合并更新（status/phase/started/finished/error/result…）。

        按字节读入全文件，仅重写 id 命中的行（保留该行原行尾字节），其余行
        （含损坏行/非 UTF-8 行）原样字节保留后原子重写；id 不存在返回 None。
        """
        with _LOCK:
            path = jobs_path()
            if not path.exists():
                return None
            lines = path.read_bytes().splitlines(keepends=True)
            updated: dict[str, Any] | None = None
            for i, line in enumerate(lines):
                obj = self._parse(line)
                if obj is None or obj.get("id") != job_id:
                    continue
                updated = {**obj, **fields}
                lines[i] = json.dumps(updated, ensure_ascii=False, default=str).encode(
                    "utf-8"
                ) + _line_tail(line)
                _atomic_write(path, b"".join(lines))
                break
            return updated

    def clean(self) -> int:
        """删除 done/failed 行（字节级保留其余行），返回删除行数。"""
        with _LOCK:
            path = jobs_path()
            if not path.exists():
                return 0
            kept: list[bytes] = []
            removed = 0
            for line in path.read_bytes().splitlines(keepends=True):
                obj = self._parse(line)
                if obj is not None and obj.get("status") in ("done", "failed"):
                    removed += 1
                else:
                    kept.append(line)
            if removed:
                _atomic_write(path, b"".join(kept))
            return removed

    # ---- 读 ----

    def get(self, job_id: str) -> dict[str, Any] | None:
        """按 id 取单条（自尾向头找——新 job 在文件尾）；不存在返回 None。"""
        with _LOCK:
            path = jobs_path()
            if not path.exists():
                return None
            for line in reversed(path.read_bytes().splitlines()):
                obj = self._parse(line)
                if obj is not None and obj.get("id") == job_id:
                    return obj
            return None

    def list(self, limit: int | None = 50) -> list[dict[str, Any]]:
        """任务列表（新在前；None=全部）。损坏行跳过不计入。"""
        with _LOCK:
            path = jobs_path()
            if not path.exists():
                return []
            out: list[dict[str, Any]] = []
            for line in reversed(path.read_bytes().splitlines()):
                obj = self._parse(line)
                if obj is None:
                    continue
                out.append(obj)
                if limit is not None and len(out) >= limit:
                    break
            return out
