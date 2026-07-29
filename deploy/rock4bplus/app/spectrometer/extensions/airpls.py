"""
airPLS — Adaptive Iteratively Reweighted Penalized Least Squares

基于 Zhang et al. "Baseline correction using adaptive iteratively
reweighted penalized least squares" (Analyst, 2010) 实现的基线校正算法。

用于拉曼/红外/质谱等光谱数据的自动基线估计与扣除。
"""

import numpy as np
from functools import lru_cache
import math
from scipy import sparse
from scipy.sparse.linalg import spsolve


def _difference_matrix(n: int, order: int = 2) -> sparse.spmatrix:
    """构造 n 列的 k 阶差分矩阵 (稀疏 CSR 格式)."""
    if order <= 0:
        raise ValueError("差分阶数必须 >= 1")
    D = sparse.eye(n, n, format='lil')
    for _ in range(order):
        D = D[1:, :] - D[:-1, :]
    return D.tocsr()


def validate_airpls_parameters(
    *,
    size: int,
    lam: float = 1e5,
    order: int = 2,
    max_iter: int = 15,
) -> None:
    size = int(size)
    if size <= 0:
        raise ValueError("光谱长度必须大于 0")
    if not math.isfinite(float(lam)) or float(lam) <= 0:
        raise ValueError("airPLS lambda 必须是有限正数")
    if int(order) < 1 or int(order) >= size:
        raise ValueError("airPLS 差分阶数必须不小于 1 且小于像素数")
    if int(max_iter) < 1:
        raise ValueError("airPLS 最大迭代次数必须不小于 1")


@lru_cache(maxsize=16)
def penalty_matrix(size: int, order: int = 2) -> sparse.spmatrix:
    validate_airpls_parameters(size=size, order=order)
    difference = _difference_matrix(int(size), int(order))
    return (difference.T @ difference).tocsr()


def airpls(x: np.ndarray, lam: float = 1e5, order: int = 2,
           max_iter: int = 15, tol: float = 1e-6,
           penalty=None) -> np.ndarray:
    """
    自适应迭代重加权惩罚最小二乘基线估计.

    Parameters
    ----------
    x : np.ndarray
        输入光谱 (一维数组).
    lam : float
        平滑度参数, 越大基线越平滑. 典型范围: 1e3 ~ 1e9.
    order : int
        差分惩罚阶数. 2 = 二阶差分 (曲率惩罚).
    max_iter : int
        最大迭代次数.
    tol : float
        权重变化收敛阈值.

    Returns
    -------
    baseline : np.ndarray
        估计的基线, 与输入等长.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    n = len(x)
    validate_airpls_parameters(
        size=n,
        lam=lam,
        order=order,
        max_iter=max_iter,
    )
    DTD = penalty_matrix(n, order) if penalty is None else penalty
    if DTD.shape != (n, n):
        raise ValueError(
            f"airPLS 惩罚矩阵形状错误：{DTD.shape}，期望 {(n, n)}"
        )

    w = np.ones(n)

    for _ in range(max_iter):
        W_diag = sparse.diags(w, format='csr')
        A = W_diag + lam * DTD
        b = w * x
        z = spsolve(A, b)

        r = x - z                # 残差: 正值=峰, 负值=基线以下
        r_neg = r[r < 0]

        if len(r_neg) == 0:
            break

        # 自适应权重 — 指数衰减
        mean_neg = np.mean(r_neg)
        std_neg = np.std(r_neg)

        if std_neg < 1e-12:
            break

        # 峰区域 (r >= 0) 权重接近 0; 基线区域 (r < 0) 权重按指数衰减
        w_new = np.zeros_like(w)
        negative = r < 0
        exponent = 2.0 * (r[negative] - mean_neg) / std_neg
        # np.where 会同时计算两侧表达式；在峰值跨度很大时会先产生
        # exp 溢出。只计算负残差并限制指数，既保持权重关系，也避免
        # inf 进入下一轮稀疏求解。
        w_new[negative] = np.exp(np.clip(exponent, -50.0, 50.0))

        if np.max(np.abs(w_new - w)) < tol:
            break

        w = w_new

    return z


def airpls_baseline_correct(x: np.ndarray, lam: float = 1e5,
                            order: int = 2, max_iter: int = 15) -> np.ndarray:
    """
    使用 airPLS 扣除基线, 返回校正后的光谱.

    Parameters
    ----------
    x : np.ndarray
        输入光谱.
    lam : float
        平滑度参数.
    order : int
        差分阶数.
    max_iter : int
        最大迭代次数.

    Returns
    -------
    corrected : np.ndarray
        基线校正后的光谱 (原始 - 基线).
    """
    baseline = airpls(x, lam=lam, order=order, max_iter=max_iter)
    return x - baseline
