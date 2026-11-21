"""Studio 工作台命令：studio。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
"""
from __future__ import annotations

from pathlib import Path

import click


# ── studio ────────────────────────────────────────────────
@click.command()
@click.option(
    "--root", required=True, type=click.Path(exists=True, file_okay=False),
    help="工作目录（文章/产物/发布日志的边界）",
)
@click.option("--port", default=8766, type=int, help="工作台端口（仅绑定 127.0.0.1）")
@click.option("--db", "db_path", default=None, type=click.Path(), help="预留数据库路径（透传 studio.make_server）")
@click.option("--no-open", is_flag=True, help="不自动打开浏览器（launchd 常驻场景用，见 deploy/ 模板）")
def studio(root: str, port: int, db_path: str | None, no_open: bool) -> None:
    """启动 Studio 本地工作台（队列/详情/仪表盘三视图，仅回环地址）。"""
    import webbrowser

    from ..studio import make_server

    with make_server(root, port=port, db=db_path) as httpd:
        actual_port = httpd.server_address[1]  # --port 0 时取系统分配的实际端口
        url = f"http://127.0.0.1:{actual_port}"
        click.echo(f"Studio 工作台已启动: {url}")
        click.echo(f"   工作目录: {Path(root).resolve()}")
        click.echo("按 Ctrl+C 停止")
        if not no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            click.echo("\n已停止 Studio")
