"""书源领域层（R0 书源功能，L2 纯）。

core/booksource 四模块零 I/O、零网络：
  - model：规范字段表/归一/索引投影/旧格式（字符串再包层）兼容
  - merge：导入三态判定 + importKeep 偏好（保留本地名称/分组/启用位/序号）
  - classifier：legado 配置包六类型识别（书源/主题/TTS/净化/排版/unknown，R0.5 F-X1）
  - importer：导入四分支识别（JSON / SourceUrls 递归 / URL / 纯 JS 源可读错误）

规范来源=docs/项目方案/musespiral-书源功能架构设计与分期规划-2026-11-18.md §三 +
配套前置分析 §2.1/§2.2/§3.1/§3.2/§3.3（legado 实体与导入链实测）。
键名不可变是生态铁律：本层只做类型归一，从不改名、从不丢弃未知键。
"""
