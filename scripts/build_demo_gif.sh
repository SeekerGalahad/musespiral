#!/usr/bin/env bash
# 重新生成 README 的演示 GIF（docs/demo.gif）。
#
# 不跑真实转写流水线（whisper 太重）：先造一个临时文章根（1 篇带时间戳锚的成稿
# + 1 个三轮 compose 会话 + 3 条素材），再起一个独立端口的工作台录 UI 旅程
# （素材库 → 创作工坊 → 文章编辑 → 预览 → 洞察写作节奏）。**不碰常驻 :8766**。
#
# 依赖：模块 .venv（含 playwright）+ 浏览器（.venv/bin/python -m playwright install chromium）
#      + ffmpeg。用法：scripts/build_demo_gif.sh [port]
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-18790}"
PY="$MODULE_DIR/.venv/bin/python"
TMP="$MODULE_DIR/.demo-gif-build"
FFMPEG="${FFMPEG:-/opt/homebrew/bin/ffmpeg}"

command -v "$FFMPEG" >/dev/null || { echo "缺 ffmpeg：设 FFMPEG=<路径>"; exit 1; }
[ -x "$PY" ] || { echo "缺 $PY（先建 .venv 并装 playwright）"; exit 1; }

rm -rf "$TMP" && mkdir -p "$TMP"
PYTHONPATH="$MODULE_DIR" "$PY" -I - "$MODULE_DIR" "$TMP" "$PORT" "$FFMPEG" <<'PY'
import json, os, shutil, subprocess, sys, threading
from datetime import datetime, timedelta
from pathlib import Path

module_dir, tmp, port, ffmpeg = sys.argv[1:5]
port = int(port)
root, home, out = Path(tmp) / "root", Path(tmp) / "home", Path(tmp) / "out"
root.mkdir(parents=True); home.mkdir(); out.mkdir()

# ── fixture：成稿 + compose 会话 + 素材 ───────────────────────────────
(root / "00_welcome.md").write_text("""---
title: 多头注意力：从一个头到多个头
date: 2026-11-20
cover: cover.png
digest: 把单个注意力升级成多头——三个小节，每节可回溯到视频原处。
author: demo
---

## 为什么需要多头

[00:03:35](#t=215) 上一页我们学会了单个注意力怎么算，可只从一个角度看句子
难免片面，所以这一页把它升级成多头。

[00:04:12](#t=252) 每个头各学一种关系：有的盯指代，有的盯句法，有的盯语义。
把它们拼起来，模型就能同时抓住多条线索。

## 怎么拼

[00:06:40](#t=400) 八个头各出一份输出，拼接后过一个线性层，维度回到原来大小。
计算量只增加常数倍，表达力却提升了一个层次。

## 与单头的差别

[00:09:15](#t=555) 单头是「一个视角看全句」，多头是「八个视角各看一角」。
这就是 Transformer 能同时处理多种语言现象的根源。
""", encoding="utf-8")
(root / "cover.png").write_bytes(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63f8cfc000000301010018dd8db00000000049454e44ae426082"))

sdir = root / "demo_article.compose"
(sdir / "rounds").mkdir(parents=True)
rounds = [
    {"n": 1, "blocks": [{"id": "b1", "kind": "heading", "text": "## 为什么需要多头", "origin": "ai"},
                        {"id": "b2", "kind": "para", "text": "单头只能从一个角度看句子，难免片面。", "origin": "ai"}]},
    {"n": 2, "blocks": [{"id": "b1", "kind": "heading", "text": "## 为什么需要多头", "origin": "ai"},
                        {"id": "b2", "kind": "para", "text": "单头只能从一个角度看句子，难免片面。多头各学一种关系，把线索拼起来。", "origin": "ai"},
                        {"id": "b3", "kind": "para", "text": "八个头各出一份输出，拼接后过一个线性层。", "origin": "ai"}]},
    {"n": 3, "blocks": [{"id": "b1", "kind": "heading", "text": "## 为什么需要多头", "origin": "ai"},
                        {"id": "b2", "kind": "para", "text": "单头只能从一个角度看句子，难免片面。多头各学一种关系——指代、句法、语义——把线索拼起来。", "origin": "ai"},
                        {"id": "b3", "kind": "para", "text": "八个头各出一份输出，拼接后过一个线性层，维度回到原来大小。", "origin": "ai"},
                        {"id": "b4", "kind": "para", "text": "这就是 Transformer 能同时处理多种语言现象的根源。", "origin": "ai"}]},
]
(sdir / "session.json").write_text(json.dumps({
    "slug": "demo_article", "title": "多头注意力：从一个头到多个头",
    "sources": [{"path": "00_welcome.md", "sections": []}],
    "status": "composing", "kind": "article", "rounds": rounds,
}, ensure_ascii=False), encoding="utf-8")
(sdir / "draft.md").write_text(
    "<!--b:b1-->\n## 为什么需要多头\n<!--b:b2-->\n单头只能从一个角度看句子，难免片面。\n"
    "<!--b:b3-->\n八个头各出一份输出，拼接后过一个线性层。\n", encoding="utf-8")
now = datetime.now()
for r in rounds:
    p = sdir / "rounds" / f"{r['n']}.md"
    p.write_text("\n".join(b["text"] for b in r["blocks"]), encoding="utf-8")
    when = now - timedelta(days=3 - r["n"], hours=r["n"])
    os.utime(p, (when.timestamp(), when.timestamp()))
    (sdir / f"{r['n']}.decisions.json").write_text("[]", encoding="utf-8")

mdir = home / "materials"; mdir.mkdir()
rows = [
    {"id": "m1lecture01", "type": "video", "origin": {"source_id": "lecture", "uri": "file:///lectures/transformer.mp4", "title": "Transformer 精讲：513 秒中文讲座"}, "license": "CC-BY", "fetched_at": (now - timedelta(hours=3)).isoformat(timespec="seconds"), "provenance": {"chain": [], "derived_from": []}, "payload": {"local_path": "/lectures/transformer.mp4", "meta": {"duration_text": "8 分 33 秒"}}, "triage": {"read": False, "starred": True, "enabled": True, "tags": ["视频", "讲座"], "verified": False, "confidence": None}},
    {"id": "m2note0002", "type": "text", "origin": {"source_id": "notes", "uri": "file:///notes/attention.md", "title": "手记：注意力机制三条线索"}, "license": "UNKNOWN", "fetched_at": (now - timedelta(hours=6)).isoformat(timespec="seconds"), "provenance": {"chain": [], "derived_from": []}, "payload": {"text": "每个头各学一种关系：指代、句法、语义。多头的价值在于同时抓住多条线索。"}, "triage": {"read": False, "starred": False, "enabled": True, "tags": ["手记", "注意力"], "verified": False, "confidence": None}},
    {"id": "m3src00003", "type": "link", "origin": {"source_id": "web", "uri": "https://arxiv.org/abs/1706.03762", "title": "Attention Is All You Need（原论文）"}, "license": "UNKNOWN", "fetched_at": (now - timedelta(hours=10)).isoformat(timespec="seconds"), "provenance": {"chain": [], "derived_from": []}, "payload": {"url": "https://arxiv.org/abs/1706.03762"}, "triage": {"read": True, "starred": True, "enabled": True, "tags": ["论文"], "verified": False, "confidence": None}},
]
(mdir / "materials.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

# ── 录 UI 旅程（独立端口，绝不碰常驻 :8766）────────────────────────────
os.environ["MUSESPIRAL_HOME"] = str(home)
sys.path.insert(0, module_dir)
from musespiral import studio
from playwright.sync_api import sync_playwright

httpd = studio.make_server(str(root), port=port)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                  record_video_dir=str(out),
                                  record_video_size={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{port}/#material", wait_until="load")
        page.wait_for_selector(".mat-row", timeout=15000)
        page.wait_for_timeout(1600)
        page.mouse.move(300, 300); page.mouse.move(520, 460, steps=22)
        page.wait_for_timeout(1200)
        page.evaluate("var d=document.querySelector('.mat-row'); if(d) d.open=true;")
        page.wait_for_timeout(1700)

        page.evaluate("location.hash='#compose'")
        page.wait_for_timeout(900)
        chip = page.query_selector("[data-cm-sel]")
        if chip:
            chip.click(); page.wait_for_timeout(1200)
        page.mouse.move(900, 220); page.mouse.move(700, 420, steps=22)
        page.wait_for_timeout(2400)

        page.evaluate("location.hash='#article?path=00_welcome.md&tab=edit'")
        for _ in range(75):
            t = page.evaluate("var t=document.getElementById('detail-title'); t?t.textContent:''")
            if t and "加载中" not in t:
                break
            page.wait_for_timeout(200)
        page.wait_for_timeout(1400)
        page.mouse.move(700, 600); page.mouse.move(860, 300, steps=22)
        page.wait_for_timeout(2600)

        page.evaluate("location.hash='#article?path=00_welcome.md&tab=preview'")
        page.wait_for_timeout(1600)
        page.mouse.move(860, 300); page.mouse.move(760, 500, steps=22)
        page.wait_for_timeout(2400)

        page.evaluate("location.hash='#insight'")
        page.wait_for_timeout(1200)
        page.evaluate("var el=document.getElementById('writing-card'); if(el) el.scrollIntoView({block:'center'});")
        page.wait_for_timeout(700)
        page.mouse.move(900, 560); page.mouse.move(1050, 470, steps=22)
        page.wait_for_timeout(2800)
        ctx.close(); browser.close()
finally:
    httpd.shutdown(); httpd.server_close()

raw = sorted(out.glob("*.webm"))[0].rename(out / "raw.webm")
gif = out / "demo.gif"
subprocess.run([ffmpeg, "-y", "-i", str(raw), "-vf",
    "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
    "-loop", "0", str(gif)], check=True, capture_output=True)
print(f"gif: {gif} ({gif.stat().st_size} bytes)")
PY

cp "$TMP/out/demo.gif" "$MODULE_DIR/docs/demo.gif"
rm -rf "$TMP"
ls -l "$MODULE_DIR/docs/demo.gif"
