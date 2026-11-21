#!/usr/bin/env python3
"""local-scan 插件可复跑探针（本地媒体线 2026-11-20，验收硬要求 11b）。

用法：
    python3 modules/musespiral/scripts/scan_probe.py <root> [<root> ...]
        [--max-depth N] [--min-size-mb N] [--pattern mp4,mov,...] [--max-files N]

按引擎同款 importlib 按路径加载 plugins_builtin/local-scan/plugin.py，
用最小 PluginContext 假面（log 收集/无取消）真跑插件，stdout 打印 JSON：

    {"roots": [...], "count": N, "truncated": bool, "denied_dirs": N,
     "entries_visited": N, "probed_ok": N, "total_size_bytes": N,
     "total_size_human": "...", "files": [{path,size_bytes,size_human,
     duration_s,format}...按大小降序], "logs": [...]}

--max-files N 仅截断 JSON 里 files 数组的展示长度（扫描本身不受影响）。
退出码：0=扫描完成；2=PluginError（stderr 打印消息原文）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PKG_ROOT = Path(__file__).resolve().parents[1]  # modules/musespiral/
sys.path.insert(0, str(PKG_ROOT))  # → import musespiral（插件只依赖 core 上下文）

PLUGIN_PATH = PKG_ROOT / "musespiral" / "plugins_builtin" / "local-scan" / "plugin.py"


class _ProbeCtx:
    """最小插件上下文假面（探针面）：log 收集、progress 忽略、无取消。"""

    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))

    def progress(self, done: int, total: int) -> None:
        self.logs.append(f"progress {done}/{total}")

    @property
    def cancelled(self) -> bool:
        return False


def _load_plugin() -> Any:
    spec = importlib.util.spec_from_file_location("local_scan_plugin", PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="local-scan 插件真跑探针")
    ap.add_argument("roots", nargs="+", help="扫描根目录（可多个）")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--min-size-mb", type=float, default=1.0)
    ap.add_argument("--pattern", default=None, help="逗号分隔后缀，如 mp4,mov")
    ap.add_argument("--max-files", type=int, default=200, help="JSON 里 files 展示上限")
    args = ap.parse_args(argv)

    plugin = _load_plugin()
    ctx = _ProbeCtx()
    inputs: dict[str, Any] = {
        "roots": ",".join(args.roots),
        "max_depth": args.max_depth,
        "min_size_mb": args.min_size_mb,
    }
    if args.pattern:
        inputs["pattern"] = args.pattern

    structured = plugin.scan(
        roots=plugin._parse_roots(inputs["roots"]),
        max_depth=args.max_depth,
        min_size_mb=args.min_size_mb,
        suffixes=plugin._parse_pattern(inputs.get("pattern")),
        ctx=ctx,
    )
    files = [
        {
            "path": f["path"],
            "size_bytes": f["size_bytes"],
            "size_human": plugin._fmt_size(f["size_bytes"]),
            "duration_s": f["duration_s"],
            "format": Path(f["path"]).suffix.lstrip(".").lower(),
        }
        for f in structured["files"]
    ]
    out = {
        "roots": [str(Path(r).expanduser()) for r in args.roots],
        "count": len(files),
        "truncated": structured["truncated"],
        "denied_dirs": structured["denied_dirs"],
        "entries_visited": structured["entries_visited"],
        "probed_ok": structured["probed_ok"],
        "total_size_bytes": structured["total_size_bytes"],
        "total_size_human": plugin._fmt_size(structured["total_size_bytes"]),
        "files": files if args.max_files <= 0 else files[: args.max_files],
        "files_shown": min(len(files), args.max_files) if args.max_files > 0 else len(files),
        "logs": ctx.logs,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 —— 探针顶层：任何可读失败走 stderr+2
        print(f"scan_probe: {exc}", file=sys.stderr)
        sys.exit(2)
