"""媒体生成命令组（G1 文生图，2026-11-17 调研 §四/§五裁决 2）。

  - musespiral media config：写 media 域档案（ai.json 独立命名空间
    media_profiles/media_default——**不进 chat TASKS/routing**，评审修正
    2026-11-17：media 走 /images/generations 不经 chat()）+ 设为生效档案；
  - musespiral media list：media 档案清单（key 掩码零回显）+ 健康快照；
  - musespiral media verify：连接验证（health 三态：ok/down/unconfigured）。

档案存 `~/.musespiral/ai.json`（与 chat 域同文件双命名空间，互不擦写）；生成
出网唯一出口=infra/media_gen.py（host 白名单+https——base_url 预设与
白名单单一事实源 PROVIDERS）。
"""
from __future__ import annotations

import sys

import click

from ..infra.media_gen import PROVIDERS


@click.group()
def media() -> None:
    """AI 文生图（G1：SiliconFlow 先行，FLUX.1-schnell 免费档起步）。"""


@media.command("config")
@click.option("--api-key", required=True, help="API Key（本地保存于 ~/.musespiral/ai.json，永不回显）")
@click.option("--model", default="FLUX.1-schnell", show_default=True,
              help="模型名（FLUX.1-schnell 免费 / Qwen-Image 付费档）")
@click.option("--provider", type=click.Choice(sorted(PROVIDERS)), default="siliconflow",
              show_default=True, help="供应商预设（决定 base_url；host 白名单同源）")
@click.option("--profile", "profile_name", default="", help="档案名（缺省=供应商名）")
def media_config(api_key: str, model: str, provider: str, profile_name: str) -> None:
    """配置 media 域档案并设为生效档案（不动 chat 域 default/routing）。"""
    from ..infra.ai.profiles import save_media_profile
    from ..infra.media_gen import ALLOWED_HOSTS

    name = profile_name.strip() or provider
    base_url = PROVIDERS[provider]
    save_media_profile(name, base_url, api_key, model)  # media_default=name（域内独立，无劫持面）
    click.echo(f"✅ media 档案「{name}」已保存并设为生效档案（media_default → {name}）")
    click.echo(f"   base_url: {base_url} / model: {model}（key 已存，不回显）")
    click.echo(f"   出网白名单: {'/'.join(sorted(ALLOWED_HOSTS))}（https only）")
    click.echo("   验证: musespiral media verify（Studio 设置面「验证」按钮同链路）")


@media.command("list")
def media_list() -> None:
    """列出 media 档案（key 掩码）+ 健康快照。"""
    from ..infra.ai.profiles import list_media_profiles
    from ..services import media_gen_service

    state = list_media_profiles()
    if not state["profiles"]:
        click.echo("媒体生成未配置。运行: musespiral media config --api-key <KEY> [--model FLUX.1-schnell]")
        return
    for name, prof in sorted(state["profiles"].items()):
        mark = "★" if name == state["default"] else " "
        configured = "✅" if prof.get("configured") else "⚠️ "
        click.echo(
            f" {mark} {configured} {name}: {prof.get('base_url')} / "
            f"{prof.get('model')} / key {prof.get('api_key')}"
        )
    health = media_gen_service.read_health()
    if health:
        status = {"ok": "🟢 ok", "down": "🔴 down", "unconfigured": "⚪ 未配置"}.get(
            str(health.get("status")), str(health.get("status"))
        )
        click.echo(f"  健康快照: {status}（{health.get('checked_at') or ''}）")
    else:
        click.echo("  健康快照: （未验证过——musespiral media verify）")


@media.command("verify")
def media_verify() -> None:
    """连接验证（health 三态：ok/down/unconfigured）。"""
    from ..infra.media_gen import verify_connection

    health = verify_connection()
    mark = {"ok": "🟢", "down": "🔴", "unconfigured": "⚪"}.get(health["status"], "⚪")
    click.echo(f"{mark} {health['status']}（档案 {health['profile'] or '(无)'}）")
    if health.get("detail"):
        click.echo(f"   {health['detail']}")
    if health["status"] == "down":
        sys.exit(1)
