"""core/materials/vector.py 数学正确性（ARCH-5）——纯层单测，零外部依赖。

覆盖：余弦的数学不变量（同向 1 / 反向 -1 / 正交 0 / 尺度不变 / 零向量 0）、
L2 归一化（单位范数 / 方向保持 / 零向量 / 输入不改写）、top-k（降序 / 截断 /
超库 / 空库 / 破平确定性 / 维度校验）。
"""

from __future__ import annotations

import math
import random

import pytest

from musespiral.core.materials.vector import (
    BGE_M3_DIM,
    VectorDimensionError,
    cosine_similarity,
    normalize,
    top_k,
)


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_opposite_vectors_score_negative_one(self) -> None:
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_scale_invariant(self) -> None:
        # 尺度不变：放大任意常数倍相似度不变（余弦的核心不变量）
        assert cosine_similarity([1.0, 2.0], [10.0, 20.0]) == pytest.approx(1.0)
        assert cosine_similarity([3.0, -4.0], [-6.0, 8.0]) == pytest.approx(-1.0)

    def test_zero_vector_scores_zero(self) -> None:
        # 零向量语义：任一方为零向量 → 0.0（不抛错，见模块 docstring）
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_dimension_mismatch_raises(self) -> None:
        with pytest.raises(VectorDimensionError):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_int_inputs_accepted(self) -> None:
        # 接口收 Sequence[float]，int 序列（字面量手写向量）天然可用
        assert cosine_similarity([1, 2], [2, 4]) == pytest.approx(1.0)

    def test_result_bounded_in_negative_one_to_one(self) -> None:
        rng = random.Random(42)  # 固定种子——测试确定性
        for _ in range(50):
            a = [rng.uniform(-10.0, 10.0) for _ in range(8)]
            b = [rng.uniform(-10.0, 10.0) for _ in range(8)]
            score = cosine_similarity(a, b)
            assert -1.0 <= score <= 1.0


class TestNormalize:
    def test_unit_norm(self) -> None:
        out = normalize([3.0, -4.0, 12.0])
        assert math.sqrt(sum(x * x for x in out)) == pytest.approx(1.0)

    def test_preserves_direction(self) -> None:
        assert normalize([3.0, 4.0]) == pytest.approx([0.6, 0.8])

    def test_zero_vector_unchanged(self) -> None:
        assert normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_does_not_mutate_input(self) -> None:
        src = [3.0, 4.0]
        normalize(src)
        assert src == [3.0, 4.0]


class TestTopK:
    def _vectors(self) -> dict[str, list[float]]:
        return {
            "m1": [1.0, 0.0],
            "m2": [0.0, 1.0],
            "m3": [-1.0, 0.0],
            "m4": [0.9, 0.1],
        }

    def test_ordering_descending(self) -> None:
        result = top_k([1.0, 0.0], self._vectors(), k=4)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)
        assert result[0][0] == "m1" and result[0][1] == pytest.approx(1.0)
        assert result[-1][0] == "m3" and result[-1][1] == pytest.approx(-1.0)

    def test_k_cuts_result(self) -> None:
        assert len(top_k([1.0, 0.0], self._vectors(), k=2)) == 2
        # 截断保留的是最相似的前缀（m1 恒第一）
        assert top_k([1.0, 0.0], self._vectors(), k=2)[0][0] == "m1"

    def test_k_gt_library_returns_all_sorted(self) -> None:
        result = top_k([1.0, 0.0], self._vectors(), k=99)
        assert len(result) == 4

    def test_k_zero_or_negative_returns_empty(self) -> None:
        assert top_k([1.0, 0.0], self._vectors(), k=0) == []
        assert top_k([1.0, 0.0], self._vectors(), k=-1) == []

    def test_empty_library_returns_empty(self) -> None:
        assert top_k([1.0, 0.0], {}, k=5) == []

    def test_tie_broken_by_id_ascending(self) -> None:
        # 破平确定性：同分按 id 字典序升序（同输入两次调用结果一致）
        vectors = {"zz": [1.0, 0.0], "aa": [1.0, 0.0], "mm": [1.0, 0.0]}
        assert [mid for mid, _ in top_k([2.0, 0.0], vectors, k=3)] == ["aa", "mm", "zz"]

    def test_dimension_mismatch_raises(self) -> None:
        with pytest.raises(VectorDimensionError):
            top_k([1.0, 0.0, 0.0], {"m1": [1.0, 0.0]}, k=1)

    def test_zero_query_all_scores_zero_sorted_by_id(self) -> None:
        result = top_k([0.0, 0.0], self._vectors(), k=4)
        assert all(s == 0.0 for _, s in result)
        assert [mid for mid, _ in result] == ["m1", "m2", "m3", "m4"]


def test_bge_m3_dim_constant_matches_live_probe() -> None:
    # 文档常量：2026-11-22 实测 /api/embed bge-m3 dim=1024（见 infra/embedding.py）
    assert BGE_M3_DIM == 1024
