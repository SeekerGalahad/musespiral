"""图床配置命令组：imagehost（list / config）——R5（正典 §四-R5）。

不注册进 cli.py：命令注册归轮次三集成阶段统一处理（同 R3/R4 命令先例），
测试直接调命令组对象（见 tests/test_images.py）。
"""
from __future__ import annotations

import click

from ..infra.images import load_host_config, save_host_config


def _mask_token(token: str) -> str:
    """token 掩码：只露头尾各 4 字符，短值全遮。"""
    if len(token) <= 8:
        return "****"
    return f"{token[:4]}…{token[-4:]}"


@click.group(name="imagehost")
def imagehost_cmd() -> None:
    """管理图床配置（github/local；文章 frontmatter imagehost 可覆盖默认）。"""


@imagehost_cmd.command("list")
def imagehost_list() -> None:
    """列出已配置图床与默认项（token 掩码显示）。"""
    cfg = load_host_config()
    default = cfg.get("default")
    click.echo(f"默认图床: {default if isinstance(default, str) and default else 'local（缺省）'}")

    hosts = cfg.get("hosts")
    gh = hosts.get("github") if isinstance(hosts, dict) else None
    gh = gh if isinstance(gh, dict) else {}
    if gh.get("owner") or gh.get("repo") or gh.get("token"):
        parts = [f"owner={gh.get('owner', '')}", f"repo={gh.get('repo', '')}"]
        if gh.get("branch"):
            parts.append(f"branch={gh['branch']}")
        if gh.get("cdn"):
            parts.append(f"cdn={gh['cdn']}")
        if gh.get("token"):
            parts.append(f"token={_mask_token(str(gh['token']))}")
        click.echo("  - github: " + " ".join(parts))
    else:
        click.echo("  - github: 未配置（musespiral imagehost config --github-owner ... 配置）")
    click.echo("  - local: 恒等图床（返回原路径，无需配置）")


@imagehost_cmd.command("config")
@click.option("--github-owner", default=None, help="GitHub 账号/组织名")
@click.option("--github-repo", default=None, help="GitHub 仓库名")
@click.option("--github-token", default=None, help="fine-grained token（contents 读写权限；不回显）")
@click.option("--github-branch", default=None, help="目标分支（缺省 main）")
@click.option("--github-cdn", default=None, help="下载 URL 模板（含 {path} 占位，如 ghfast 加速；缺省 raw.githubusercontent 直连）")
@click.option("--default", "set_default", type=click.Choice(["local", "github"]), default=None, help="设置默认图床")
def imagehost_config(
    github_owner: str | None,
    github_repo: str | None,
    github_token: str | None,
    github_branch: str | None,
    github_cdn: str | None,
    set_default: str | None,
) -> None:
    """写入图床配置 ~/.musespiral/imagehost.json（token 不回显）。"""
    gh_given = any([github_owner, github_repo, github_token, github_branch, github_cdn])
    if not gh_given and not set_default:
        raise click.UsageError("无可写配置项：--github-* 或 --default 至少给一项")
    if gh_given:
        missing = [
            n for n, v in (("owner", github_owner), ("repo", github_repo), ("token", github_token)) if not v
        ]
        if missing:
            raise click.UsageError(f"github 配置不完整（缺 {','.join(missing)}）")

    cfg = load_host_config()
    if gh_given:
        hosts = cfg.setdefault("hosts", {})
        gh = hosts.setdefault("github", {})
        gh.update({"owner": github_owner, "repo": github_repo, "token": github_token})
        if github_branch is not None:
            gh["branch"] = github_branch
        if github_cdn is not None:
            gh["cdn"] = github_cdn
    if set_default == "github":
        gh_cfg = cfg.get("hosts", {}).get("github", {})
        missing = [k for k in ("owner", "repo", "token") if not gh_cfg.get(k)]
        if missing:
            raise click.UsageError(
                f"设默认 github 前需配置完整（缺 {','.join(missing)}）——"
                "同条命令给齐 --github-owner/--github-repo/--github-token 即可"
            )
    if set_default:
        cfg["default"] = set_default

    path = save_host_config(cfg)
    click.echo(f"✅ 已写入 {path.name}" + ("（token 已保存，不回显）" if gh_given else ""))
