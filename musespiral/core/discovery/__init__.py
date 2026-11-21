"""知识库选题引擎 Domain 层（第十二期 F26-F29，正典 §三-3.1）。

四个纯逻辑模块，零 I/O 边界外能力（scanner 只读传入路径、其余三件无 I/O）：

- scanner.py    文件扫描 + frontmatter 手工解析 + 元数据提取（F26）
- scorer.py     可发布性评分（F27 纯规则，纯函数）
- suggester.py  选题建议生成（F28 规则 + 可选 AI 增强）
- skeleton.py   骨架结构组装（F29 纯逻辑）

frontmatter 手工解析说明：知识库文档的 --- 块只用到 title/tags/created/
category 四个扁平键（tags 为行内数组），不引第三方解析依赖（零新增依赖
约束）；解析口径与 python-frontmatter 对齐（首行界定符 + 闭合界定符，
容许 ``...`` 闭合与行尾空白）。
"""
from . import scanner, scorer, skeleton, suggester

__all__ = ["scanner", "scorer", "suggester", "skeleton"]
