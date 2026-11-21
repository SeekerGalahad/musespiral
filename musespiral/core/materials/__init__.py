"""素材库领域层（第二十期 §三，L2 纯）。

core/materials 四模块零 I/O：model（构造/校验/合并读序/连坐解析）、
triage（分诊谓词+tags 窄门）、tags（标签规范化/限额——第二十一期
§1.2 唯一契约）、search（跨模态词面搜索，第二十一期 §二-F64）均为
纯函数——文件读写归 infra/services，本包不触 config/pathlib/os
（架构门规则 3 之上的本期加严）。
"""
