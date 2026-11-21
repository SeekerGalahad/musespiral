"""插件系统领域层（插件系统设计 §三/§四，PL-1，L2 纯）。

core/plugins 两模块零 I/O：types（契约类型表 + 兼容判定）、manifest
（manifest pydantic 校验 + 流水线契约静态校验）均为纯函数——目录
扫描/文件读取/依赖探测归 infra/plugins_loader，编排归
services/plugins_service（正典 §7.3 规则 1：infra 是最低层，本包
不被 infra import）。契约优先于实现（设计 §2.2）：插件之间只通过
类型化契约通信——本包就是那条契约的唯一权威。
"""
