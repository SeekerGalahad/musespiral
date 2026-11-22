#!/usr/bin/env python3
"""共享执行器：从 JSON 节点清单跑一条插件流水线，并打印步级结果。

被 examples/*.sh 复用——脚本只负责拼节点 JSON，真正的执行入口是
musespiral 的 Python API：`psp.run_pipeline`（见 services/pipeline/engine.py:242）。

用法：
    python examples/_run_pipeline.py <nodes.json> [--run-root DIR]

节点清单是 JSON 数组，每项形如：
    {"id": "src", "plugin": "local-file", "inputs": {"path": "/abs/lecture.mp4", "media_kind": "video"}}
    {"id": "tr",  "plugin": "transcribe", "config": {"model": "large-v3-turbo"}}
    {"id": "ks",  "plugin": "key-segment"}

上游节点的输出端口会自动接到下游同名/兼容类型的输入端口（engine.py:121-160
的 _wire_input / _gather_inputs），所以线性链不必手写 wiring。
产物落在 run_dir/workspace/<node id>/outputs.json，run_dir 见打印的 run_dir。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from musespiral.services import pipeline_service_plugins as psp


def main() -> int:
    ap = argparse.ArgumentParser(description="跑一条 musespiral 插件流水线")
    ap.add_argument("nodes_json", help="节点清单 JSON 文件路径")
    ap.add_argument("--run-root", default=None, help="run 根目录（默认 ~/.musespiral/pipeline_runs）")
    args = ap.parse_args()

    nodes = json.loads(Path(args.nodes_json).read_text(encoding="utf-8"))
    if not isinstance(nodes, list) or not nodes:
        print("❌ 节点清单必须是非空 JSON 数组", file=sys.stderr)
        return 2

    # 阶段一：静态契约校验（不通过直接报错，零 run 目录）
    try:
        psp.plan_pipeline(nodes)
    except psp.PipelinePlanError as exc:
        print(f"❌ 流水线契约校验失败: {exc}", file=sys.stderr)
        return 2

    # 阶段二：执行（失败收敛进 result，不抛步内异常）
    result = psp.run_pipeline(nodes, run_root=args.run_root)
    print(f"\nrun_id:   {result['run_id']}")
    print(f"run_dir:  {result['run_dir']}")
    print(f"status:   {result['status']}")
    for step in result["steps"]:
        print(f"  [{step['status']:<4}] {step['step']}: {step.get('detail', '')}")
    if result["status"] != "done":
        print(f"❌ 失败于节点: {result.get('failed_at')}", file=sys.stderr)
        return 1
    print("\n✅ 流水线完成。产物在各节点 workspace/<id>/outputs.json；"
          "成稿正文在 article-writer 节点的 artifacts 目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
