"""命令组模块包（R1 拆分自单文件 cli.py，正典 §四-R1 / 参照 wenyan-cli 分层）。

core_cmds generate/preview/check/review/digest；publish_cmds publish/submit/stats；
platform_cmds platform/adapt/pack/upload；notes_cmds notes；ai_cmds ai+rewrite；
theme_cmds theme；studio_cmd studio；config_cmds config；_shared 跨组小助手。
click group 装配在 musespiral.cli（薄壳 <150 行，pyproject 入口 musespiral=musespiral.cli:cli 不变）。
"""
