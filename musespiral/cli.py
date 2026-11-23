"""CLI entry point — generate / preview / check / publish / submit.

Generation (render to local HTML) is fully decoupled from publishing.
Human review happens between `generate`/`preview` and `publish`.
"""
from __future__ import annotations

import click

from .commands.agent_cmds import agent
from .commands.ai_cmds import ai, rewrite
from .commands.backup_cmds import backup
from .commands.booksource_cmds import bookengine, booksource
from .commands.cards_cmds import cards
from .commands.compose_cmds import compose
from .commands.config_cmds import config_cmd
from .commands.cover_cmds import cover
from .commands.core_cmds import check, digest, generate, preview, quality_loop, review, title
from .commands.discover_cmds import discover
from .commands.doctor_cmd import doctor
from .commands.fetch_cmds import fetch
from .commands.imagehost_cmds import imagehost_cmd
from .commands.jobs_cmds import jobs
from .commands.media_cmds import media
from .commands.new_cmds import new
from .commands.notes_cmds import notes
from .commands.plan_cmds import log, plan
from .commands.platform_cmds import adapt, pack, platform, upload
from .commands.pipeline_cmds import pipeline
from .commands.plrun_cmds import plrun
from .commands.poster_cmds import poster
from .commands.publish_cmds import publish, stats, submit
from .commands.restore_cmds import restore
from .commands.site_cmds import site
from .commands.status_cmds import status
from .commands.style_cmds import style
from .commands.studio_cmd import studio
from .commands.tags_cmds import tags
from .commands.theme_cmds import theme
from .commands.topic_radar_cmds import topic_radar
from .commands.tts_cmds import tts
from .commands.visual_cmds import design


@click.group()
def cli() -> None:
    """musespiral — 开源内容流水线：素材进来，成稿出去，每个 AI 判断你都能改。"""


# 注册顺序与拆分前 cli.py 的命令定义顺序一致（help 清单 click 按字母序展示）
cli.add_command(generate)
cli.add_command(preview)
cli.add_command(studio)
cli.add_command(check)
cli.add_command(review)
cli.add_command(digest)
cli.add_command(publish)
cli.add_command(submit)
cli.add_command(stats)
cli.add_command(platform)
cli.add_command(adapt)
cli.add_command(pack)
cli.add_command(upload)
cli.add_command(config_cmd)
cli.add_command(theme)
cli.add_command(notes)
cli.add_command(ai)
cli.add_command(rewrite)
cli.add_command(status)  # R3：文章状态机（轮次二集成阶段统一注册）
cli.add_command(jobs)  # R4：任务队列只读查看/清理（同上）
cli.add_command(imagehost_cmd)  # R5：图床配置（轮次三集成阶段统一注册）
cli.add_command(fetch)  # R6：公众号文章抓取（同上）
cli.add_command(cards)  # F22：小红书图文卡（第十期集成注册）
cli.add_command(poster)  # F23：长图（同上）
cli.add_command(discover)  # F26-F29：知识库选题引擎（第十二期集成注册）
cli.add_command(doctor)  # W1-B1：环境预检五项绿黄红（运维发布改良 2026-11-23）
cli.add_command(backup)  # F35：文章库备份（独立先行切片，差距分析 §四）
cli.add_command(cover)  # F31：封面生成（第十三期 Group A）
cli.add_command(title)  # F32：标题多候选优选（第十三期 Group B，集成阶段注册）
cli.add_command(quality_loop)  # P1-A：质量循环 check→AI 修复→再 check（修复稿预览不覆盖原文）
cli.add_command(plan)  # F36/F36c：排期周历与人工发布登记（第十四期 Group A，集成阶段注册）
cli.add_command(log)  # F36c：发布日志人工登记命令组（第十四期 Group A，集成阶段注册）
cli.add_command(new)  # F38：模板新建文章（第十四期 Group B，集成阶段注册）
cli.add_command(restore)  # F43：编辑级版本回滚（第十六期 Group A，集成阶段注册）
cli.add_command(style)  # F44：个人风格资产（第十七期 Group A，集成阶段注册）
cli.add_command(compose)  # F48-F52：创作台（第十八期 Group B，集成阶段注册）
cli.add_command(design)  # F70-F74：视觉设计稿（Phase C 视觉创作线）
cli.add_command(tags)  # F77：素材标签建议（Phase D 智能体层，甲组集成注册）
cli.add_command(agent)  # F75/F76：多步编排（Phase D 智能体层，乙组集成注册）
cli.add_command(topic_radar)  # F78：选题雷达（Phase D 智能体层，M3 集成注册）
cli.add_command(media)  # G1：AI 文生图（2026-11-17 调研裁决；media config/list/verify）
cli.add_command(pipeline)  # E1：一键金路径（2026-11-17 组合批；pipeline run 五步串行）
cli.add_command(plrun)  # 插件流水线 run 目录：list/prune（复核 P4 回收面，2026-11-20）
cli.add_command(booksource)  # R0 书源功能（2026-11-18）：import/list/refresh/subs
cli.add_command(bookengine)  # 引擎管理（2026-11-23）：status/demo/check
cli.add_command(site)  # G-T6 静态站导出（2026-11-19）：build（CLI-only，零端点）
cli.add_command(tts)  # G-T5 TTS 朗读（调研吸收 §六）：<stem>.mp3 落文章目录


if __name__ == "__main__":
    cli()
