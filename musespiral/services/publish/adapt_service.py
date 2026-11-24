"""平台变体生成用例编排：单平台 / 批量。

从 studio/actions._action_adapt / _action_batch_adapt 提取的编排逻辑
（Phase A 下沉）。生成本体是 adapt.generate 单一实现（AI 已配置走平台
提示词、未配置 xhs/zhihu 启发式降级、channels/douyin 抛 AdaptNeedsAI）；
服务层只做批量循环与幂等续跑裁决，不定义平台规格、不直接调 AI HTTP。

与原实现的差异：JobFailure/脱敏/相对路径包装不再出现——异常原样上抛，
产物返回绝对 Path，路径的相对化归表现层。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...core import adaptation as adapt


def generate_variant(
    article_path: str | Path,
    platform: str,
    *,
    out_path: str | Path | None = None,
) -> Path:
    """生成单平台文字变体，写盘并返回输出路径。

    默认产物 <stem>.<platform>.md（与源文件同目录），已存在即拒绝覆盖
    （FileExistsError）；未知平台 ValueError；AI 未配置且无启发式降级的
    平台抛 AdaptNeedsAI——全部透传 adapt.generate 的异常语义。

    调用约定（补丁锚点契约）：out_path 缺省时按旧 studio/CLI 调用点同为
    两位置参调 adapt.generate(article_path, platform)——test/e2e 对
    musespiral.core.adaptation.generate 的 2 参打桩不因恒传第三参而炸裂；显式 out_path
    时按旧 CLI 惯例以关键字传入。
    """
    if out_path is None:
        return adapt.generate(article_path, platform)
    return adapt.generate(article_path, platform, out_path=out_path)


class BatchAdaptError(RuntimeError):
    """批量变体生成部分失败：outputs/skipped 保留已产出清单供幂等续跑。

    对应原 _action_batch_adapt 的 JobFailure(result=partial)——重发时已
    生成/已跳过的默认产物会被跳过，只补缺口。
    """

    def __init__(self, message: str, *, outputs: list[Path], skipped: list[Path]) -> None:
        super().__init__(message)
        self.outputs = outputs
        self.skipped = skipped


def generate_batch(
    article_path: str | Path,
    platforms: list[str],
    *,
    on_progress: Callable[[int, int], None] | None = None,
    skipped: list[Path] | None = None,
) -> list[Path]:
    """顺序生成多平台变体，返回本轮新生成的产物路径列表。

    幂等续跑：某平台的默认产物（<stem>.<platform>.md）已存在时跳过而非
    拒绝覆盖——重发对部分成功场景确定性可续（跳过项不计入返回值，成功
    后经 skipped 收集器透出给调用方）。中途失败抛 BatchAdaptError
    （.outputs/.skipped 携带部分结果，收集器不写）。

    on_progress(i, total)：每平台开工前回调一次（Studio 任务层把 i/total
    写入 job phase 的钩子；缺省不回调）。
    """
    src = Path(article_path)
    outputs: list[Path] = []
    skipped_local: list[Path] = []
    for i, code in enumerate(platforms, 1):
        if on_progress is not None:
            on_progress(i, len(platforms))
        default_out = src.with_name(f"{src.stem}.{code}.md")
        if default_out.exists():
            skipped_local.append(default_out)  # 已有产物：跳过不覆盖
            continue
        try:
            outputs.append(adapt.generate(src, code))
        except Exception as exc:  # AdaptNeedsAI/AIError/FileExistsError/ValueError/...
            raise BatchAdaptError(
                str(exc), outputs=outputs, skipped=skipped_local
            ) from None
    if skipped is not None:
        skipped.extend(skipped_local)
    return outputs
