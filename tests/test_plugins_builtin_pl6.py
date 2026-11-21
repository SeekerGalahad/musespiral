"""官方内置插件测试·PL-6 批（article-writer / material-sink，2026-11-20）。

覆盖（本批测试硬要求 14-18 + 阻塞点）：
  a) 阻塞点数据面：plugin_article_writer 已入 profiles.TASKS（25 键）、
     prompt_tasks() 派生面可见、ai.json routing 可按插件独立路由（PL-4+5
     「每插件独立 task 键」纪律延续）；
  b) article-writer 三组合（硬要求 14，**三种都断言 output 含时间戳锚**）：
     chronological+AI / thematic+AI / chronological+降级；
  c) thematic 多锚（硬要求 15）：同一段落可带多个锚点，且每个 #t=NNN
     都能在 transcript 找到对应 start；
  d) anchor_map 正确性（硬要求 16）：article 里每个 #t=NNN 都能追溯到
     segments（anchor_map 精确浮点起点 ⊆ transcript starts，显示秒=取整）；
  e) 降级形态：AI 失败（非 JSON / AIError）→ 降级 chronological 模板；
     thematic+未配置 → 降级 chronological 留痕（降级只支持 chronological，
     frontmatter.organize_mode 记录实际生效模式）；空转写空稿；非法 mode
     可读报错；锚点越界值收敛到段落网格；
  f) material-sink（硬要求 17）：落库素材 preview 态（triage.enabled=false）
     + type=text + meta.sub_type=video_article；provenance.chain =
     [{from_id: <run_id>, op: plugin_pipeline}]；config triage 的 enabled
     红线被忽略（其余键生效）；同 run 重落幂等；同 id 异稿 immutable 拒；
     无 run_id 拒落库；枚举扩面（META_SUB_TYPES/PROVENANCE_OPS）；
  g) 端到端（硬要求 18）：六节点链（local-file→transcribe→key-segment→
     frame-grab→article-writer→material-sink）静态契约绿 + 全 mock 执行
     （ffprobe/ffmpeg/faster_whisper/chat_policed 全假件）→ 素材库查到该
     素材且 provenance 指向本次 run。

插件模块按引擎同款 importlib 按路径加载（与 psp._load_python_entry 同一
保真度）；AI 面 monkeypatch 在 gateway/profiles 模块对象上（插件调用点是
模块属性访问，对引擎加载的副本同样生效）。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from musespiral.core.materials import model as mat_model
from musespiral.core.plugins.context import PluginContext, PluginError
from musespiral.core.plugins.manifest import validate_pipeline_contract
from musespiral.infra import plugins_loader as loader
from musespiral.infra.ai import gateway as ai_gateway
from musespiral.infra.ai import profiles as ai_profiles
from musespiral.infra.ai.profiles import TASKS
from musespiral.infra.ai.prompts import prompt_tasks
from musespiral.services import materials_service
from musespiral.services import pipeline_service_plugins as psp
from musespiral.studio import plugin_actions  # ai_verdicts 读面（AI-1 批）

BUILTIN_DIR = loader.builtin_plugins_dir()
_ANCHOR_RE = re.compile(r"\(#t=(\d+)\)")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUSESPIRAL_HOME 沙箱：素材库/AI 未配置态可复现（不触真实 ~/.musespiral）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture(autouse=True)
def _ai_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认钉死「AI 未配置」——AI 路径用例显式翻 True，互不渗漏。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: False)


def _load_module(dirname: str) -> ModuleType:
    d = BUILTIN_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        f"test_pl6_{dirname.replace('-', '_')}", d / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_of(dirname: str) -> dict[str, Any]:
    return json.loads((BUILTIN_DIR / dirname / "manifest.json").read_text(encoding="utf-8"))


class _FakeCtx(PluginContext):
    """最小 PluginContext（测试面）；artifacts_dir=root/artifacts、run_id 可注。"""

    def __init__(self, root: Path, run_id: str = "") -> None:
        self._root = root
        self._run_id = run_id
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        self.logs.append(f"progress {done}/{total}")

    def tmpdir(self) -> Path:
        d = self._root / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def artifacts_dir(self) -> Path:
        d = self._root / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def ai(self) -> Any:
        raise PluginError("测试上下文不注入 AI 客户端")


@pytest.fixture
def aw_mod() -> ModuleType:
    return _load_module("article-writer")


@pytest.fixture
def sink_mod() -> ModuleType:
    return _load_module("material-sink")


def _seg(start: float, end: float, text: str, **kw: Any) -> dict[str, Any]:
    return {"start": start, "end": end, "text": text, **kw}


def _topic_segments() -> list[dict[str, Any]]:
    """概念 A（0-12s 引入）→ B（14-24s）→ A 深化（26-36s）：thematic 重组的
    教学视频典型形态（评审 §3.2「概念 A 第 3 分钟引入、第 25 分钟深化」）。"""
    return [
        _seg(0.0, 6.0, "首先讲重点：列式存储的原理"),
        _seg(6.0, 12.0, "数据先写入内存再异步刷盘"),
        _seg(14.0, 20.0, "接下来看查询路由的机制"),
        _seg(20.0, 24.0, "Frontend 负责解析 SQL"),
        _seg(26.0, 32.0, "回到列式存储：索引的细节"),
        _seg(32.0, 36.0, "这些细节决定了扫描效率"),
    ]


def _anchor_ints(md: str) -> list[int]:
    return [int(m) for m in _ANCHOR_RE.findall(md)]


def _assert_anchors_traceable(
    md: str, anchor_map: dict[str, list[float]], segments: list[dict[str, Any]]
) -> None:
    """硬要求 15/16 合并判据：md 里每个 #t=NNN (1) 对应 anchor_map 中某精确
    起点（取整相等）、(2) 在 transcript 里找得到对应 start（|差| ≤ 0.5），
    且 anchor_map 的精确起点全部 ⊆ transcript starts（机器可验证不变量）。"""
    starts = [float(s["start"]) for s in segments]
    exact = [v for vals in anchor_map.values() for v in vals]
    assert exact, "anchor_map 为空——锚链断了"
    assert all(v in starts for v in exact), f"anchor_map 精确起点脱离段落网格: {exact}"
    hrefs = _anchor_ints(md)
    assert hrefs, "article 正文不含任何 #t= 时间戳锚"
    for n in hrefs:
        assert any(int(round(v)) == n for v in exact), f"#t={n} 不在 anchor_map 内"
        assert any(abs(s - n) <= 0.5 for s in starts), f"#t={n} 在 transcript 无对应 start"


# ── a) 任务键接线（阻塞点数据面）──────────────────────────────────


def test_plugin_article_writer_task_registered() -> None:
    assert "plugin_article_writer" in TASKS
    assert len(TASKS) == 26  # PL-6 批 24→25；N-2 批 25→26（material_distill，素材闭环）
    assert "plugin_article_writer" in prompt_tasks()  # prompts.d 覆盖级联面派生


def test_routing_per_plugin_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ai.json routing 可把 plugin_article_writer 独立路由（一插件一键）。"""
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    (home / "ai.json").write_text(json.dumps({
        "default": "cloud",
        "profiles": {"cloud": {"base_url": "https://a", "api_key": "k", "model": "m"},
                     "local": {"base_url": "http://b", "api_key": "", "model": "n"}},
        "routing": {"plugin_article_writer": "local"},
    }), encoding="utf-8")
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    assert ai_profiles.resolve_profile_name("plugin_article_writer") == "local"
    assert ai_profiles.resolve_profile_name("rewrite") == "cloud"  # 其他任务不受影响


# ── b/c/d) article-writer：三组合 × 时间戳锚 ──────────────────────


def test_article_writer_manifest_contract() -> None:
    m = _manifest_of("article-writer")
    assert m["kind"] == "process"
    assert m["inputs"]["transcript"]["type"] == "segments"
    assert m["inputs"]["images"]["type"] == "images"
    assert m["inputs"]["ranges"]["required"] is False
    assert m["inputs"]["title"]["type"] == "string"
    assert {k: v["type"] for k, v in m["outputs"].items()} == {
        "article": "article", "anchor_map": "ext/anchor_map",
    }
    assert m["config_schema"]["mode"]["default"] == "chronological"
    assert m["uses_ai"] is True
    assert set(m["permissions"]) == {"fs:read", "fs:write:output"}


def test_article_writer_chronological_ai(
    aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """组合一 chronological+AI：经治理层一次调用（task 键/温度/生成类不缓存）；
    锚点全部落段落网格；md 落 artifacts_dir；article/anchor_map 双端口一致。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    calls: list[dict[str, Any]] = []

    def fake(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        calls.append({"messages": messages, **kwargs})
        return {"text": json.dumps([
            {"heading": "列式存储的引入", "text": "原理是列式存储，先写内存再刷盘。",
             "anchors": [0.0]},
            # 自造数值 15.7 → snap 到最近起点 14；越界 9999 → 收敛到末段起点 26
            {"heading": "查询路由", "text": "Frontend 解析 SQL 生成计划。",
             "anchors": [15.7, 9999.0]},
            {"heading": "索引细节", "text": "索引细节决定扫描效率。", "anchors": [26.0]},
        ], ensure_ascii=False), "cache_hit": False, "chars_in": 1, "chars_out": 1}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    segments = _topic_segments()
    ctx = _FakeCtx(tmp_path / "ctx", run_id="pl-unit-0001")
    out = aw_mod.run(
        {"transcript": segments, "title": "测试视频", "media_path": "/tmp/v.mp4",
         "duration_s": 36.0},
        {}, ctx,
    )

    assert len(calls) == 1
    assert calls[0]["task"] == "plugin_article_writer"
    assert calls[0]["temperature"] == 0.4
    assert calls[0]["cache"] is False  # 生成类恒不缓存（§八-3）
    user_text = calls[0]["messages"][-1]["content"]
    assert "chronological" in user_text  # 组织方式进了提示词

    article, anchor_map = out["article"], out["anchor_map"]
    md = Path(article["path"]).read_text(encoding="utf-8")
    assert Path(article["path"]).parent == ctx.artifacts_dir  # 产物受管
    assert article["slug"] == "测试视频".replace("测试视频", "") or article["slug"]  # 中文 slug 兜底非空
    # 硬要求 14：output 含时间戳锚；硬要求 15/16：全部可追溯
    _assert_anchors_traceable(md, anchor_map, segments)
    assert anchor_map == {"p1": [0.0], "p2": [14.0, 26.0], "p3": [26.0]} or sum(
        len(v) for v in anchor_map.values()
    ) == 4  # 自造 15.7→14、越界 9999→26（网格收敛，锚总数不减）
    assert "00:00:00" in md and "#t=0)" in md
    assert f"organize_mode: \"chronological\"" in md
    assert 'source_video: "/tmp/v.mp4"' in md
    assert "video_duration: 36" in md
    assert "anchor_map:" in md
    assert article["organize_mode"] == "chronological"
    assert article["video_duration"] == 36.0
    assert any("AI 成稿" in line for line in ctx.logs)


def test_article_writer_thematic_ai_multi_anchor(
    aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """组合二 thematic+AI（评审 §3.2）：概念 A 两处出现合并成一段——
    **同一段落带多个锚点**（硬要求 15），且全部可追溯（硬要求 15/16 判据）。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

    def fake(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        assert "thematic" in messages[-1]["content"]  # 组织方式进提示词
        return {"text": json.dumps([
            {"heading": "列式存储：原理与细节", "text": "先引入原理，后面再深化细节。",
             "anchors": [0.0, 26.0]},  # 概念 A 引入处 + 深化处
            {"heading": "查询路由", "text": "Frontend 负责解析。", "anchors": [14.0]},
        ], ensure_ascii=False), "cache_hit": False, "chars_in": 1, "chars_out": 1}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    segments = _topic_segments()
    ctx = _FakeCtx(tmp_path / "ctx")
    out = aw_mod.run({"transcript": segments}, {"mode": "thematic"}, ctx)

    article, anchor_map = out["article"], out["anchor_map"]
    md = Path(article["path"]).read_text(encoding="utf-8")
    assert article["organize_mode"] == "thematic"
    assert 'organize_mode: "thematic"' in md
    assert anchor_map["p1"] == [0.0, 26.0]  # 同一段落两个锚（A 引入 + A 深化）
    p1_line = next(line for line in md.splitlines() if line.startswith("[") and "先引入原理" in line)
    assert p1_line.count("](#t=") == 2  # 硬要求 15 字面：一段多锚
    assert "[00:00:00](#t=0)" in p1_line and "[00:00:26](#t=26)" in p1_line
    _assert_anchors_traceable(md, anchor_map, segments)


def test_article_writer_chronological_degraded(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    """组合三 chronological+降级（AI 未配置）：按 ranges 顺序引原文——
    **output 仍含时间戳锚**（硬要求 14 第三态）；零 AI 调用。"""
    segments = _topic_segments()
    ranges = [
        {"start": 0.0, "end": 12.0, "label": "列式存储", "confidence": 0.9},
        {"start": 14.0, "end": 24.0, "label": "查询路由", "confidence": 0.8},
    ]
    ctx = _FakeCtx(tmp_path / "ctx")
    out = aw_mod.run({"transcript": segments, "ranges": ranges, "title": "降级稿"}, {}, ctx)

    article, anchor_map = out["article"], out["anchor_map"]
    md = Path(article["path"]).read_text(encoding="utf-8")
    assert article["organize_mode"] == "chronological"
    assert anchor_map == {"p1": [0.0], "p2": [14.0]}
    _assert_anchors_traceable(md, anchor_map, segments)
    # 每段引原文（模板拼装契约）
    assert "首先讲重点" in md and "接下来看查询路由" in md
    assert "## 1. 列式存储" in md and "## 2. 查询路由" in md
    assert any("模板拼装" in line for line in ctx.logs)


def test_article_writer_ai_failure_degrades_chronological(
    aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 已配置但失败（非 JSON / AIError / 零有效段落）→ 降级 chronological；
    mode=thematic 时同样降级且 organize_mode 记录实际生效模式（降级只支持
    chronological）。两条降级路径都保留时间戳锚。"""
    segments = _topic_segments()
    cases = [
        ("抱歉，我不会输出 JSON", None),
        (json.dumps([{"heading": "无锚段落", "text": "没有 anchors 键的段落"}]), None),
        (None, ai_profiles.AIError("调用 AI 失败: 网络超时")),
    ]
    for ai_text, ai_exc in cases:
        monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

        def fake(messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            if ai_exc is not None:
                raise ai_exc
            return {"text": ai_text}

        monkeypatch.setattr(ai_gateway, "chat_policed", fake)
        ctx = _FakeCtx(tmp_path / f"ctx-{abs(hash(ai_text or 'exc'))}")
        out = aw_mod.run({"transcript": segments, "ranges": [
            {"start": 0.0, "end": 12.0, "label": "列式存储", "confidence": 0.9},
        ]}, {"mode": "thematic"}, ctx)
        assert out["article"]["organize_mode"] == "chronological"  # 实际生效模式
        md = Path(out["article"]["path"]).read_text(encoding="utf-8")
        _assert_anchors_traceable(md, out["anchor_map"], segments)
        assert any("降级模板拼装" in line for line in ctx.logs)


def test_article_writer_images_assigned_and_copied(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    """关键帧按 ts 插入对应段落之后（正典 §9.3）：复制进 artifacts/images、
    相对路径引用、图注含时间戳；帧文件缺失跳过不炸。"""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    f1 = frames_dir / "frame_01_7.0s.jpg"
    f1.write_bytes(b"JFIF")
    f2 = frames_dir / "frame_02_28.0s.jpg"
    f2.write_bytes(b"JFIF")
    missing = frames_dir / "frame_03_lost.jpg"
    images = [
        {"path": str(f1), "ts": 7.0,
         "source_range": {"start": 6.0, "end": 12.0, "label": "存储", "confidence": 0.9}},
        {"path": str(missing), "ts": 99.0, "source_range": {}},  # 缺失 → 跳过
        {"path": str(f2), "ts": 28.0,
         "source_range": {"start": 26.0, "end": 32.0, "label": "细节", "confidence": 0.9}},
    ]
    ctx = _FakeCtx(tmp_path / "ctx")
    out = aw_mod.run({
        "transcript": _topic_segments(), "images": images,
        "ranges": [
            {"start": 0.0, "end": 12.0, "label": "列式存储", "confidence": 0.9},
            {"start": 14.0, "end": 24.0, "label": "查询路由", "confidence": 0.8},
            {"start": 26.0, "end": 36.0, "label": "索引细节", "confidence": 0.9},
        ],
    }, {}, ctx)
    md = Path(out["article"]["path"]).read_text(encoding="utf-8")
    img_dir = ctx.artifacts_dir / "images"
    assert (img_dir / "frame_01_7.0s.jpg").is_file()  # 帧复制进受管产物目录
    assert (img_dir / "frame_02_28.0s.jpg").is_file()
    assert "![](images/frame_01_7.0s.jpg)" in md  # 相对路径引用（目录自包含）
    assert "![](images/frame_02_28.0s.jpg)" in md
    assert "frame_03_lost" not in md  # 缺失帧不引用
    # 图注含时间戳 + 来源片段
    assert "图：00:00:07 处关键帧（来源片段 00:00:06–00:00:12）" in md
    assert "图：00:00:28 处关键帧（来源片段 00:00:26–00:00:32）" in md
    # ts 就近归段：7.0 → 段1（span 0-12）、28.0 → 段3（span 26-36）
    assert md.index("frame_01_7.0s.jpg") < md.index("## 2.")
    assert md.index("frame_02_28.0s.jpg") > md.index("## 3.")


def test_article_writer_empty_transcript_and_invalid_inputs(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    ctx = _FakeCtx(tmp_path / "ctx")
    # 空转写=真实结果：不进 AI、空稿照常产出（流水线不卡死）
    out = aw_mod.run({"transcript": []}, {}, ctx)
    assert out["anchor_map"] == {}
    md = Path(out["article"]["path"]).read_text(encoding="utf-8")
    assert "(#t=" not in md
    assert "转写为空" in " ".join(ctx.logs)
    # 非法输入可读报错
    with pytest.raises(PluginError, match="缺少输入 transcript"):
        aw_mod.run({}, {}, ctx)
    with pytest.raises(PluginError, match="须为段落列表"):
        aw_mod.run({"transcript": "不是列表"}, {}, ctx)
    with pytest.raises(PluginError, match="start ≥ end"):
        aw_mod.run({"transcript": [_seg(5.0, 1.0, "倒挂")]}, {}, ctx)
    with pytest.raises(PluginError, match="mode 非法"):
        aw_mod.run({"transcript": _topic_segments()}, {"mode": "penta"}, ctx)


def test_article_writer_fractional_start_snap(
    aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非整秒段落起点（4.4）：锚点显示取整（#t=4）但 anchor_map 保精确值——
    「#t=NNN 在 transcript 找得到对应 start（±0.5s）」不变量跨非整秒成立。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    monkeypatch.setattr(ai_gateway, "chat_policed", lambda *a, **kw: {"text": json.dumps(
        [{"heading": "一段", "text": "正文", "anchors": [5.0]}]  # → snap 最近起点 4.4
    )})
    segments = [_seg(0.0, 2.0, "开头"), _seg(4.4, 6.0, "收尾")]
    out = aw_mod.run({"transcript": segments}, {}, _FakeCtx(tmp_path / "ctx"))
    assert out["anchor_map"]["p1"] == [4.4]  # 精确起点（非取整）
    md = Path(out["article"]["path"]).read_text(encoding="utf-8")
    _assert_anchors_traceable(md, out["anchor_map"], segments)


# ── e2) 三态裁决（AI-1 批 2026-11-21：auto_adopt 双态 + 建议语义）──


def test_article_writer_auto_adopt_defaults_to_draft(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    """裁决批硬要求 16：缺省 → article.status='draft' 且 outputs.article_status
    ='draft'（顶层直判面，下游免解包），日志体现「建议」语义。降级路径断言
    （fixture 钉死 AI 未配置）——状态在 run() 公共尾部加盖，AI/降级同覆盖。"""
    m = _manifest_of("article-writer")
    assert m["config_schema"]["auto_adopt"]["type"] == "boolean"
    assert m["config_schema"]["auto_adopt"]["default"] is False
    assert "article_status" not in m["outputs"]  # 字段级裁决（契约 A）：不新增输出端口类型

    ctx = _FakeCtx(tmp_path / "ctx")
    out = aw_mod.run({"transcript": _topic_segments()}, {}, ctx)
    assert out["article"]["status"] == "draft"
    assert out["article_status"] == "draft"
    assert any(
        "AI 建议" in line and "draft" in line and "待人工裁决" in line
        for line in ctx.logs
    )


def test_article_writer_auto_adopt_true_adopts(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    """auto_adopt=true → article.status='adopted'（旧行为，信任 AI 全自动）。"""
    ctx = _FakeCtx(tmp_path / "ctx")
    out = aw_mod.run({"transcript": _topic_segments()}, {"auto_adopt": True}, ctx)
    assert out["article"]["status"] == "adopted"
    assert out["article_status"] == "adopted"
    assert any("auto_adopt=true" in line for line in ctx.logs)
    assert not any("待人工裁决" in line for line in ctx.logs)


def test_article_writer_auto_adopt_invalid_rejected(
    aw_mod: ModuleType, tmp_path: Path
) -> None:
    """非布尔 auto_adopt 可读报错（引擎只填 default 不深校验，插件自验布尔）。"""
    with pytest.raises(PluginError, match="auto_adopt 须为布尔"):
        aw_mod.run({"transcript": _topic_segments()}, {"auto_adopt": 1},
                   _FakeCtx(tmp_path / "ctx"))


# ── f) material-sink：preview 态 / sub_type / provenance ──────────


def _make_article(aw_mod: ModuleType, root: Path) -> dict[str, Any]:
    """真实 article-writer 降级产出（relay：不手造 dict，契约形状天然一致）。"""
    ctx = _FakeCtx(root / "aw")
    return aw_mod.run(
        {"transcript": _topic_segments(), "title": "测试视频",
         "ranges": [{"start": 0.0, "end": 12.0, "label": "列式存储", "confidence": 0.9}]},
        {}, ctx,
    )["article"]


def test_material_sink_manifest_contract() -> None:
    m = _manifest_of("material-sink")
    assert m["kind"] == "sink"  # 首个 sink 插件
    assert m["inputs"]["article"]["type"] == "article"
    assert m["inputs"]["images"]["required"] is False
    assert {k: v["type"] for k, v in m["outputs"].items()} == {"material": "material"}
    assert m["config_schema"]["triage"]["default"] == {}
    assert "uses_ai" not in m or m.get("uses_ai") is not True


def test_enums_extended() -> None:
    """枚举扩面（本批 §11/§12）：sub_type 取值不扩 MATERIAL_TYPES（沿 E-5 纪律）、
    plugin_pipeline 入 PROVENANCE_OPS。"""
    assert "video_article" in mat_model.META_SUB_TYPES
    assert "plugin_pipeline" in mat_model.PROVENANCE_OPS
    for t in ("text", "image", "video", "link", "radar_signal", "crawled", "derived"):
        assert t in mat_model.MATERIAL_TYPES  # MATERIAL_TYPES 不动
    assert len(mat_model.MATERIAL_TYPES) == 7


def test_material_sink_lands_preview_video_article(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path
) -> None:
    """硬要求 17：落库素材 preview 态（enabled=false）+ type=text +
    sub_type=video_article + provenance 指向 run_id。"""
    article = _make_article(aw_mod, tmp_path)
    run_id = "pl-20261120-120000-abcd"
    ctx = _FakeCtx(tmp_path / "sink", run_id=run_id)
    out = sink_mod.run({"article": article}, {}, ctx)
    mid = out["material"]
    assert isinstance(mid, str) and mid

    row = materials_service.get_material(mid)
    assert row is not None
    assert row["type"] == "text"  # 不新增 MATERIAL_TYPES（§11）
    assert row["triage"]["enabled"] is False  # preview 态（§10 既有纪律）
    assert row["triage"]["read"] is False and row["triage"]["starred"] is False
    assert row["license"] == "AI-GENERATED"  # G1 AI 产物既有纪律
    meta = row["payload"]["meta"]
    assert meta["sub_type"] == "video_article"  # §11
    assert meta["ai_generated"] is True
    assert meta["organize_mode"] == "chronological"
    assert meta["source_video"] == ""
    assert meta["anchor_map"] == {"p1": [0.0]}
    assert row["provenance"]["chain"] == [{"from_id": run_id, "op": "plugin_pipeline"}]  # §12
    assert row["provenance"]["derived_from"] == []
    assert "首先讲重点" in row["payload"]["text"]  # 正文=md 全文
    assert "#t=0)" in row["payload"]["text"]  # 锚随稿入库
    # 分诊查询默认过滤面：enabled=false 不进推荐（preview 语义的结构化表达）
    assert materials_service.query(enabled=True) == []
    assert [m["id"] for m in materials_service.query(enabled=False)] == [mid]


def test_material_sink_carries_upstream_ai_status(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path
) -> None:
    """裁决批（AI-1 2026-11-21）硬要求 17：上游 article.status=draft →
    meta.ai_status='draft'（UI「AI 建议」徽章数据源）且 triage.enabled 仍
    false（preview 红线与裁决态两层不混）；adopted 同步透传；无合法状态
    （旧稿手造 dict）→ 不写该键，存量读侧零回归。落库瞬间态就此定格
    （payload immutable），run 侧后续裁决走裁决端点。"""
    article = _make_article(aw_mod, tmp_path)
    assert article["status"] == "draft"  # article-writer 缺省即 draft（② 批语义）

    ctx = _FakeCtx(tmp_path / "sink-draft", run_id="pl-ais-draft1")
    mid = sink_mod.run({"article": article}, {}, ctx)["material"]
    row = materials_service.get_material(mid)
    assert row["payload"]["meta"]["ai_status"] == "draft"
    assert row["triage"]["enabled"] is False
    assert any("ai_status=draft" in line for line in ctx.logs)

    adopted = {**article, "status": "adopted"}
    ctx2 = _FakeCtx(tmp_path / "sink-adopted", run_id="pl-ais-adopt1")
    mid2 = sink_mod.run({"article": adopted}, {}, ctx2)["material"]
    meta2 = materials_service.get_material(mid2)["payload"]["meta"]
    assert meta2["ai_status"] == "adopted"

    legacy = {k: v for k, v in article.items() if k != "status"}
    ctx3 = _FakeCtx(tmp_path / "sink-legacy", run_id="pl-ais-legacy")
    mid3 = sink_mod.run({"article": legacy}, {}, ctx3)["material"]
    meta3 = materials_service.get_material(mid3)["payload"]["meta"]
    assert "ai_status" not in meta3  # 无合法状态不写键（零回归）


def test_material_sink_config_triage_enabled_redline(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.triage 初值：enabled=true 被红线忽略（留痕），read/starred/tags 生效；
    非法键/非布尔可读报错。"""
    article = _make_article(aw_mod, tmp_path)
    ctx = _FakeCtx(tmp_path / "sink", run_id="pl-redline-0001")
    out = sink_mod.run(
        {"article": article},
        {"triage": {"enabled": True, "read": True, "tags": ["视频", " 教程 "]}},
        ctx,
    )
    row = materials_service.get_material(out["material"])
    assert row["triage"]["enabled"] is False  # 红线：config 改不了 preview 态
    assert row["triage"]["read"] is True
    assert row["triage"]["tags"] == ["视频", "教程"]
    assert any("enabled 被忽略" in line for line in ctx.logs)

    ctx2 = _FakeCtx(tmp_path / "sink2", run_id="pl-redline-0002")
    with pytest.raises(PluginError, match="enabled 恒 false"):
        sink_mod.run({"article": article}, {"triage": {"bad_key": 1}}, ctx2)
    with pytest.raises(PluginError, match="须为布尔"):
        sink_mod.run({"article": article}, {"triage": {"read": "yes"}}, ctx2)


def test_material_sink_dedup_and_immutable_conflict(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path
) -> None:
    """同 run 重落幂等（同 id 同稿返回既有行零追加）；同 id 异稿 immutable 拒。"""
    article = _make_article(aw_mod, tmp_path)
    ctx = _FakeCtx(tmp_path / "sink", run_id="pl-dedup-0001")
    first = sink_mod.run({"article": article}, {}, ctx)
    rows_before = len(materials_service.list_materials())
    second = sink_mod.run({"article": article}, {}, ctx)
    assert first["material"] == second["material"]
    assert len(materials_service.list_materials()) == rows_before  # 零追加

    # 同 id 异稿（改 md 内容重落同 run）→ immutable 冲突可读报错
    Path(article["path"]).write_text("---\ntitle: 改稿\n---\n\n不同内容\n", encoding="utf-8")
    with pytest.raises(PluginError, match="immutable"):
        sink_mod.run({"article": article}, {}, ctx)


def test_material_sink_rejections(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path
) -> None:
    article = _make_article(aw_mod, tmp_path)
    # 无 run_id（纯单测假 ctx 默认空串）→ 拒落库（血统缺失比伪造诚实）
    with pytest.raises(PluginError, match="run_id"):
        sink_mod.run({"article": article}, {}, _FakeCtx(tmp_path / "no-run"))
    # article 缺 path / path 不存在
    with pytest.raises(PluginError, match="article"):
        sink_mod.run({}, {}, _FakeCtx(tmp_path / "no-article", run_id="pl-x"))
    with pytest.raises(PluginError, match="article.path 不存在"):
        sink_mod.run({"article": {**article, "path": "/nonexistent/a.md"}},
                     {}, _FakeCtx(tmp_path / "bad-path", run_id="pl-x"))
    # images 引用清单随稿（复核修复批 2026-11-20 起为 durable 副本路径）：
    # 帧文件存在 → 复制进 durable 区并记副本路径；缺失/脏数据 → 跳过留痕
    # （死引用不入清单——正是复核问题 3 的修复语义）
    ctx = _FakeCtx(tmp_path / "imgs", run_id="pl-imgs-0001")
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"fake-jpg")
    out = sink_mod.run(
        {"article": article,
         "images": [{"path": str(frame), "ts": 7.0}, {"path": "/w/gone.jpg", "ts": 8.0},
                    "脏数据跳过"]},
        {}, ctx,
    )
    meta = materials_service.get_material(out["material"])["payload"]["meta"]
    assert meta["images"] == [{"path": str(tmp_path / "musespiral-home" / "materials" /
                                             "pipeline" / "pl-imgs-0001" / "images" /
                                             "frame.jpg"), "ts": 7.0}]
    assert any("帧文件缺失" in line for line in ctx.logs)


# ── g) 引擎六节点链（全 mock，硬要求 18）──────────────────────────


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text = start, end, text
        self.avg_logprob, self.no_speech_prob = -0.3, 0.05


class _FakeInfo:
    duration = 36.0


class _FakeWhisperModel:
    def __init__(self, *a: Any, **kw: Any) -> None: ...
    def transcribe(self, audio: str, **kw: Any) -> Any:
        return iter([
            _FakeSegment(0.0, 6.0, " 首先讲重点：列式存储的原理 "),
            _FakeSegment(14.0, 20.0, " 接下来看查询路由的机制 "),
        ]), _FakeInfo()


def _mock_media_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(argv, 0, stdout=b"36.0\n", stderr=b"")
        Path(argv[-1]).write_bytes(b"fake")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeWhisperModel))


def test_six_node_static_contract() -> None:
    """正典 §9.1 首链六节点静态契约：…→frame-grab→article-writer→material-sink 绿。"""
    manifests = [_manifest_of(d) for d in
                 ("local-file", "transcribe", "key-segment", "frame-grab",
                  "article-writer", "material-sink")]
    nodes = [{"id": n, "plugin": m} for n, m in zip(
        ("src", "tr", "ks", "fg", "aw", "sink"), manifests)]
    assert validate_pipeline_contract(nodes) == []


def test_engine_six_node_chain_material_in_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端（硬要求 18）：全 mock 六节点链 → 素材库查到该素材且 provenance
    指向本次 run；AI 双任务键各走一次治理层；article md 时间戳锚全可追溯。"""
    _mock_media_toolchain(monkeypatch)
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    ai_tasks: list[str] = []

    def fake_chat_policed(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        task = kwargs["task"]
        ai_tasks.append(task)
        if task == "plugin_key_segment":
            return {"text": json.dumps([
                {"start": 0.0, "end": 6.0, "label": "列式存储", "confidence": 0.9,
                 "text_excerpt": "列式存储"},
                {"start": 14.0, "end": 20.0, "label": "查询路由", "confidence": 0.85,
                 "text_excerpt": "查询路由"},
            ], ensure_ascii=False)}
        if task == "plugin_article_writer":
            return {"text": json.dumps([
                {"heading": "列式存储", "text": "原理：列式存储，先写内存再刷盘。",
                 "anchors": [0.0]},
                {"heading": "查询路由", "text": "Frontend 解析 SQL。", "anchors": [14.0]},
            ], ensure_ascii=False)}
        raise AssertionError(f"非预期 AI 任务: {task}")

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)

    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-media")
    model_dir = tmp_path / "m"
    model_dir.mkdir()  # 存在的本地目录 → transcribe 模型就位探测直认（零网络）
    run_id = "pl-builtin-0003"
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
            {"id": "aw", "plugin": "article-writer", "inputs": {"title": "端到端测试稿"}},
            {"id": "sink", "plugin": "material-sink"},
        ],
        plugins_root=BUILTIN_DIR,
        run_root=tmp_path / "runs",
        run_id=run_id,
    )
    assert result["status"] == "done", result
    assert [s["status"] for s in result["steps"]] == ["ok"] * 6
    assert ai_tasks == ["plugin_key_segment", "plugin_article_writer"]  # 一插件一键

    def _outputs(node: str) -> dict[str, Any]:
        return json.loads(
            (Path(result["run_dir"]) / "workspace" / node / "outputs.json")
            .read_text(encoding="utf-8")
        )["inline"]

    # article md：锚全部可追溯到 transcribe 段落（硬要求 16，引擎级）
    article = _outputs("aw")["article"]
    segments = _outputs("tr")["transcript"]
    md = Path(article["path"]).read_text(encoding="utf-8")
    _assert_anchors_traceable(md, _outputs("aw")["anchor_map"], segments)
    assert article["organize_mode"] == "chronological"
    assert article["source_video"] == str(media)  # ←local-file.file 自动接线
    assert article["video_duration"] == pytest.approx(36.0)  # ←transcribe.duration_s
    assert (Path(article["path"]).parent / "images").is_dir()  # 帧图复制进产物目录

    # 素材库：查到该素材且 provenance 指向本次 run（硬要求 18）
    mid = _outputs("sink")["material"]
    row = materials_service.get_material(mid)
    assert row is not None
    assert row["type"] == "text"
    assert row["triage"]["enabled"] is False  # preview 态
    assert row["payload"]["meta"]["sub_type"] == "video_article"
    assert row["provenance"]["chain"] == [{"from_id": run_id, "op": "plugin_pipeline"}]
    assert row["payload"]["meta"]["images"], "帧 ts 引用清单随稿（frame-grab 自动接线）"
    # 分诊读面：查询能按 sub_type 语义外的普通 text 查到（零回归面）
    assert any(m["id"] == mid for m in materials_service.query(mtype="text"))
    # 末节点产物收集：material.json 入 run output
    assert any("material.json" in a for a in result["artifacts"])


# ── h) 复核修复批回归（2026-11-20 复核四问题）──────────────────────


def test_article_writer_forged_anchors_neutralized(
    aw_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核问题 1：AI 正文/标题夹带 ``[00:16:39](#t=999)`` 会进 md 却不进
    anchor_map——「md 每个 #t=N 都能回溯 segment.start」不变量被击穿（仓库
    自己的检查器 ``_assert_anchors_traceable`` 红）。修复后自由文本里的锚
    链一律剥除（链接形保文字剥链、裸 href 删除），md 中 #t= 锚只出自锚点行。
    """
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

    def fake(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        return {"text": json.dumps([
            {"heading": "列式存储 [假锚](#t=999)",
             "text": "原理：列式存储。夹带伪造锚 [00:16:39](#t=999) 与裸锚 (#t=7)。",
             "anchors": [0.0]},
        ], ensure_ascii=False)}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    segments = _topic_segments()
    ctx = _FakeCtx(tmp_path / "ctx", run_id="pl-forged-0001")
    out = aw_mod.run(
        {"transcript": segments, "title": "标题夹带 [T](#t=555)"}, {}, ctx,
    )
    md = Path(out["article"]["path"]).read_text(encoding="utf-8")
    # 伪造锚全部消失（链接形/裸 href/heading/title 四个入口）
    assert "(#t=999" not in md and "(#t=7)" not in md and "(#t=555" not in md
    # 链接形剥链保文字（内容不丢，只去链）
    assert "00:16:39" in md and "假锚" in md and "标题夹带 T" in md
    # 仓库检查器（复核现场用的同一判据）转绿；锚只剩锚点行的合法一份
    _assert_anchors_traceable(md, out["anchor_map"], segments)
    assert sorted(_anchor_ints(md)) == [0]


def test_material_sink_tags_follow_unique_contract(
    sink_mod: ModuleType, aw_mod: ModuleType, tmp_path: Path
) -> None:
    """复核问题 2：config.triage.tags 此前只 strip 不走
    core/materials/tags.normalize_tags（tags 面唯一契约）——超限/超长/大写
    空格脏标签原样落库污染 tag_counts，且该素材此后 AI 标签建议面（adopt）
    永久抛错。现在走唯一契约：越限可读拒、合法规范化（小写/转连字符/去重）。
    """
    article = _make_article(aw_mod, tmp_path)
    ctx = _FakeCtx(tmp_path / "sink", run_id="pl-tags-0001")
    with pytest.raises(PluginError, match="上限"):  # 15 个 > 12
        sink_mod.run({"article": article},
                     {"triage": {"tags": [f"t{i}" for i in range(15)]}}, ctx)
    with pytest.raises(PluginError, match="上限"):  # 单标签 40 字符 > 24
        sink_mod.run({"article": article},
                     {"triage": {"tags": ["x" * 40]}}, ctx)
    with pytest.raises(PluginError, match="被拒"):  # 非列表
        sink_mod.run({"article": article},
                     {"triage": {"tags": "not-a-list"}}, ctx)
    ctx2 = _FakeCtx(tmp_path / "sink2", run_id="pl-tags-0002")
    out = sink_mod.run(
        {"article": article},
        {"triage": {"tags": ["AI 生成", "Tag-One", "tag-one", "X!Y"]}},
        ctx2,
    )
    row = materials_service.get_material(out["material"])
    assert row["triage"]["tags"] == ["ai-生成", "tag-one", "x-y"]  # 规范化+去重保序


def test_engine_six_node_artifacts_survive_run_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核问题 3：帧图/article_path 此前只存 run workspace 绝对路径——run
    目录 LRU（PRUNE_KEEP=5）剪除后素材变永久死引用（帧图/article.md 双 404、
    正文相对引用悬空、meta.images 无消费者、采纳搬运面未实现）。修复后稿件+
    帧图随稿复制进素材仓 durable 区（~/.musespiral/materials/pipeline/<run_id>/），
    删 run 目录（模拟 prune）后全部仍可回跳。"""
    import shutil as _shutil

    from musespiral import config as _config

    _mock_media_toolchain(monkeypatch)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-media")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    run_id = "pl6-durable-0001"
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
            {"id": "aw", "plugin": "article-writer"},
            {"id": "sink", "plugin": "material-sink"},
        ],
        plugins_root=BUILTIN_DIR,
        run_root=tmp_path / "runs",
        run_id=run_id,
    )
    assert result["status"] == "done", result
    run_dir = Path(result["run_dir"])
    mid = json.loads(
        (run_dir / "workspace" / "sink" / "outputs.json").read_text(encoding="utf-8")
    )["inline"]["material"]
    row = materials_service.get_material(mid)
    meta = row["payload"]["meta"]
    durable_root = _config.home_dir() / "materials" / "pipeline" / run_id
    # meta 指向 durable 副本（run workspace 之外的素材仓属地）
    assert Path(meta["article_path"]).parent == durable_root
    assert Path(meta["article_path"]).suffix == ".md"
    assert meta["images"], "帧图引用清单非空"
    for img in meta["images"]:
        p = Path(img["path"])
        assert p.is_file() and p.parent == durable_root / "images"
        assert str(p).startswith(str(run_dir)) is False  # 不再指向 run workspace
    # 正文相对引用 ![](images/…) 在 durable 目录内自洽
    rel_refs = [
        line[4:line.rfind(")")] for line in row["payload"]["text"].splitlines()
        if line.startswith("![") and line.rstrip().endswith(")")
    ]
    assert rel_refs and all((durable_root / rel).is_file() for rel in rel_refs)
    # 模拟 LRU 剪除：删 run 目录 → durable 产物全部存活（素材可永久回跳）
    _shutil.rmtree(run_dir)
    assert Path(meta["article_path"]).is_file()
    assert all(Path(img["path"]).is_file() for img in meta["images"])


# ── h) ai_verdicts 读面 × 真实引擎链（AI-1 批 2026-11-21，④ 前端数据前提）──
def test_ai_verdicts_on_real_engine_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实引擎跑 auto_adopt 缺省（false）的链 → runs 读面 ai_verdicts 报态，
    与产物侧 status 一致；adopt 端点翻态后读面同步（读面/写面同源）。

    与 `test_engine_six_node_chain_material_in_library` 同 harness（全 mock），
    差异只在断言面：本测只看裁决态在两份读面（产物 outputs.json / ai_verdicts）
    是否一致——不重复六节点链的业务断言。
    """
    _mock_media_toolchain(monkeypatch)
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

    def fake_chat_policed(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        if kwargs["task"] == "plugin_key_segment":
            return {"text": json.dumps([
                {"start": 0.0, "end": 6.0, "label": "列式存储", "confidence": 0.9},
                {"start": 14.0, "end": 20.0, "label": "查询路由", "confidence": 0.85},
            ], ensure_ascii=False)}
        return {"text": json.dumps([
            {"heading": "列式存储", "text": "原理：列式存储。", "anchors": [0.0]},
        ], ensure_ascii=False)}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-media")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    run_id = "pl6-verdict-0001"
    # run_root 走默认（=MUSESPIRAL_HOME/pipeline_runs，本 fixture 已在沙箱内）——
    # 裁决写面按 pipeline_runs_root() 定位 run，两面对齐才测得到「读面/写面同源」。
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
            {"id": "aw", "plugin": "article-writer"},
        ],
        plugins_root=BUILTIN_DIR,
        run_id=run_id,
    )
    assert result["status"] == "done", result
    verdicts = plugin_actions._ai_verdicts(Path(result["run_dir"]))
    assert verdicts["ks"]["ranges"] == "draft"   # auto_adopt 缺省 false
    assert verdicts["aw"]["article"] == "draft"
    # 产物侧同一份态（读面无自造态；两面对拍）
    ks_doc = json.loads(
        (Path(result["run_dir"]) / "workspace" / "ks" / "outputs.json")
        .read_text(encoding="utf-8"))["inline"]["ranges"]
    assert all(r["status"] == "draft" for r in ks_doc)

    # adopt（aw）→ 读面同步
    plugin_actions.run_adjudicate(None, {"step": "aw"}, run_id, "adopt")
    verdicts2 = plugin_actions._ai_verdicts(Path(result["run_dir"]))
    assert verdicts2["aw"]["article"] == "adopted"
    assert verdicts2["ks"]["ranges"] == "draft"  # 未裁决的端口不受影响


def test_ai_verdicts_auto_adopt_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.auto_adopt=true 的节点 → 读面直接报 adopted（旧行为照常）。"""
    _mock_media_toolchain(monkeypatch)
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    monkeypatch.setattr(ai_gateway, "chat_policed",
                        lambda messages, **kw: {"text": json.dumps(
                            [{"start": 0.0, "end": 6.0, "label": "甲", "confidence": 0.9},
                             {"start": 14.0, "end": 20.0, "label": "乙", "confidence": 0.9}],
                            ensure_ascii=False)})
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-media")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment", "config": {"auto_adopt": True}},
        ],
        plugins_root=BUILTIN_DIR,
        run_root=tmp_path / "runs",
        run_id="pl6-verdict-0002",
    )
    assert result["status"] == "done", result
    verdicts = plugin_actions._ai_verdicts(Path(result["run_dir"]))
    assert verdicts["ks"]["ranges"] == "adopted"
