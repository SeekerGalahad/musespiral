"""PluginContext 抽象与插件异常族（插件系统设计 §4.4，PL-2）——L2 纯接口。

PluginContext 是**插件与平台的唯一接触面**（正典 §4.4：「为什么用 ctx
而不是全局——插件不能碰平台内部状态。ctx 是唯一的、窄的、可审计的
接触面」）。本模块只声明接口形状（log/progress/tmpdir/cancelled/
artifacts_dir/ai 六面），**零 I/O、零实现**——实现归 services 层的
pipeline_service_plugins（架构门规则 1：domain 不 import 上层）。

异常族是插件→引擎的**两种主动终态信号**，必须定义在本层（core）而非
services：插件作者只 import core（写插件不需要知道引擎在哪），若异常
定义在引擎侧，插件 `except PluginCancelled` 就得反向 import services
——违反「契约优先于实现」（设计 §2.2）。

  PluginError      插件执行期可读失败——引擎捕获后转该节点 failed 步
                   （detail=消息原文），失败即停（步级记账纪律）。
  PluginCancelled  协作取消——插件在 ``ctx.cancelled`` 置位后的自查点
                   主动抛出；引擎判 **run 级 cancelled 而非 failed**
                   （正典 §4.4 入口契约示例的第三行语义）。子类化
                   PluginError：两者同族（插件主动信号），但引擎的
                   except 顺序必须先 Cancelled 后 Error——语义不同：
                   失败是「这条流水线坏了」，取消是「用户不要了」。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class PluginError(RuntimeError):
    """插件执行期可读失败：消息原文进该节点 step 的 detail。"""


class PluginCancelled(PluginError):
    """协作取消信号：引擎判 run 级 cancelled（非 failed）。

    惯用法（正典 §4.4 示例）：长循环插件逐段自查 ``ctx.cancelled``，
    置位即 ``raise PluginCancelled()``——不带消息也合法（取消原因
    引擎侧已知：检查点旗标），带消息则进 step detail。
    """


class PluginContext(ABC):
    """平台注入能力的窄接口（六面，正典 §4.4 表）。

    形状约定（实现纪律在 services/pipeline_service_plugins）：
      - ``log(msg)``        结构化日志 → 运行日志（run 级留痕，非 print）；
      - ``progress(d, t)``  进度上报（可选调用；引擎只留最后一次）；
      - ``tmpdir()``        受管临时目录 Path——运行结束自动清理（阶段四）；
      - ``cancelled``       轮询式取消信号（bool property，非事件回调——
                            插件代码在任意循环点自查，零线程知识）；
      - ``artifacts_dir``   该节点产物落盘目录（workspace/<node_id>/）；
      - ``ai``              受管 AI 客户端——**仅 uses_ai=true 的插件注入**；
                            未声明/未注入时访问抛 PluginError（可读引导，
                            而非 AttributeError 之类的哑错——正典 §7.3：
                            插件不得绕过受管客户端直调 LLM）；
      - ``upstream``        输入端口 → 供给节点 id 映射（引擎按接线注入；
                            非抽象默认空 dict——旧引擎/测试面无接线信息时
                            插件按空面降级，不得假设必有）。
    """

    @abstractmethod
    def log(self, msg: str) -> None:
        """写一行结构化日志到流水线运行日志。"""

    @abstractmethod
    def progress(self, done: int, total: int) -> None:
        """上报节点进度（done/total 计数）。"""

    @abstractmethod
    def tmpdir(self) -> Path:
        """返回该节点的受管临时目录（惰性创建，运行结束清理）。"""

    @property
    @abstractmethod
    def cancelled(self) -> bool:
        """取消信号：True=平台已请求取消，插件应在自查点收束。"""

    @property
    @abstractmethod
    def artifacts_dir(self) -> Path:
        """该节点的产物落盘目录（workspace 下该节点专属目录）。"""

    @property
    @abstractmethod
    def ai(self) -> Any:
        """受管 AI 客户端（仅 uses_ai=true 注入；否则访问抛 PluginError）。"""

    @property
    def upstream(self) -> dict[str, str]:
        """输入端口 → 供给节点 id（如 ``{"transcript": "tr"}``）。

        引擎按实际接线注入；非抽象默认空 dict——无接线信息的宿主（旧引擎、
        单测假 ctx）下插件按「无上游」降级（如编辑版文件的全局扫描兜底），
        不得假设键必然存在。
        """
        return {}

    @property
    def run_id(self) -> str:
        """本次流水线运行的 run_id（PL-6 material-sink 血统链消费方：
        provenance.chain 记 ``{from_id: <run_id>, op: plugin_pipeline}``）。

        非抽象默认空串（与 upstream 同款宽容缺省）——无运行概念的宿主
        （纯插件单测）下 sink 插件按「无 run」拒绝落库（血统缺失比伪造
        run_id 更诚实），不假设键必然有效。
        """
        return ""


__all__ = ["PluginCancelled", "PluginContext", "PluginError"]
