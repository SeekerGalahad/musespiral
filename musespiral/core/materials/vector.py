"""素材向量数学（ARCH-5，正典=docs/项目方案/musespiral-极限架构与功能扩展总规划-2026-11-21.md §ARCH-5）——L2 纯函数，零 I/O。

与同包 search.py（词面搜索）并列的语义检索底座：余弦相似度 + top-k 选择
+ L2 归一化。本模块只做数学——向量来源（Ollama bge-m3）、缓存与落盘
归 infra/embedding.py，编排（素材向量索引、三路合并）归 services/
search_service.py；两层不互相 import，本包亦不触 config/pathlib/os/
sqlite3（materials 包加严纪律，见包 __init__，架构门规则 3 之上）。

零依赖红线：pyproject 未收 numpy，纯 Python 实现（math.fsum 保精度）。
素材 ≤10000 × 1024 维暴力扫描为秒内量级，是否达标由调用方按素材规模
自裁；索引侧可存归一化向量（normalize 一次），把每候选两趟（求范数+点积）
压到一趟点积的快路径。

零向量语义：数学上余弦未定义，工程上取 0.0（与「无相似」同语义，不抛
错）——空文本素材的 embedding 可能退化为零向量，不应让一条空素材炸掉
整个搜索。

维度校验：维度不一致抛 VectorDimensionError（ValueError 子类）——
错维度对（如缓存里残留旧模型向量）宁可硬失败也不静默给出假相似度。
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

# bge-m3 经 Ollama 输出的维度（2026-11-22 实测 /api/embed dim=1024）。
# 仅作文档常量：本层不强制——dim 由调用方的向量自带，校验按实际长度做。
BGE_M3_DIM = 1024


class VectorDimensionError(ValueError):
    """向量维度不匹配（cosine_similarity / top_k 的维度校验门）。"""


def normalize(v: Sequence[float]) -> list[float]:
    """L2 归一化为单位向量；零向量原样返回（无方向可归一）。

    返回新列表，不改输入。
    """
    vec = [float(x) for x in v]
    norm = math.sqrt(math.fsum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度 ∈ [-1.0, 1.0]。

    维度不一致抛 VectorDimensionError；任一方向为零向量返回 0.0
    （见模块 docstring 零向量语义）。math.fsum 保浮点累加精度。
    """
    vec_a = [float(x) for x in a]
    vec_b = [float(x) for x in b]
    if len(vec_a) != len(vec_b):
        raise VectorDimensionError(f"向量维度不一致: {len(vec_a)} vs {len(vec_b)}")
    norm_a = math.sqrt(math.fsum(x * x for x in vec_a))
    norm_b = math.sqrt(math.fsum(y * y for y in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = math.fsum(x * y for x, y in zip(vec_a, vec_b))
    return dot / (norm_a * norm_b)


def top_k(
    query_vec: Sequence[float],
    vectors: Mapping[str, Sequence[float]],
    k: int,
) -> list[tuple[str, float]]:
    """query 向量对向量库暴力扫描取 top-k → [(id, score)] 相似度降序。

    - 破平：同分按 id 字典序升序（确定性——同输入两次调用结果一致）；
    - k<=0 → []；k 超库返回全库排序；vectors 为空 → []；
    - 任一候选与 query 维度不一致抛 VectorDimensionError。

    形参取 Mapping（只读面）：调用方常持 dict[str, list[float]]，
    Mapping 值协变使其免于无谓的 dict 拷贝/注解体操（ARCH-5 第 3 件
    search_service 的消费形态；纯类型收窄，零运行时行为变更）。
    """
    scored: list[tuple[str, float]] = [
        (mid, cosine_similarity(query_vec, vec)) for mid, vec in vectors.items()
    ]
    scored.sort(key=lambda t: (-t[1], t[0]))
    if k <= 0:
        return []
    return scored[:k]
