"""Fractional Differencing — López de Prado AfML Ch 5

来源：
- Marcos López de Prado (2018) *Advances in Financial Machine Learning*, Ch 5
- 原始论文：Hosking (1981) *Fractional Differencing*

**核心问题**：
传统一阶差分 `X_t - X_{t-1}` 让金融时间序列（价格）平稳，但**完全丢失记忆**。
然而价格有强记忆性（过去的价格影响未来）→ 平稳 vs 记忆是 trade-off。

**Fractional Differencing 解决方案**：
差分阶数 d ∈ (0, 1)：
- d=0：无差分（强记忆但不平稳）
- d=1：完全差分（平稳但无记忆）
- d=0.3-0.6：部分差分，平稳且保留部分记忆

**数学公式**：

.. math::
    \tilde{X}_t = \sum_{k=0}^{\infty} \omega_k(d) \cdot X_{t-k}

其中权重：

.. math::
    \omega_k(d) = \frac{(-1)^k}{k!} \prod_{i=0}^{k-1} (d - i)
                = \omega_{k-1}(d) \cdot \frac{k - 1 - d}{k}

权重 :math:`\omega_k(d)` 随 k 增大绝对值递减，渐近趋于 0（对 d<1）。

**Fixed-Width Window（AfML 推荐）**：
实践中不用无穷和，而用固定宽度窗口（权重 < threshold 时截断）：
- threshold = 1e-5（典型）：约 30-50 项
- 优势：可滚动计算，效率高

**ADF 检验通过 + 保留记忆**：
- 选 d* = 最小 d 使得 ADF p-value < 0.05
- d* 通常 0.3-0.6（金融资产）
- 对比 d=1 完全差分：d* 保留了 30-70% 的原始方差

**实践意义**：
- 替代 log return 作为 ML 特征 → 模型泛化更好
- 用于平稳性要求的因子（协整、回归）

**典型用法**：
```python
from quant_scanner.factors.fractional_diff import fractional_diff, find_min_d

# d=0.4 的 fractional difference
fd = fractional_diff(df["close"], d=0.4, threshold=1e-5)

# 自动找最小平稳 d
d_star = find_min_d(df["close"])  # 返回使 ADF p<0.05 的最小 d
```
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def _get_weights(
    d: float,
    threshold: float = 1e-3,
    max_width: int = 200,
) -> np.ndarray:
    """计算 fractional differencing 的权重序列，小于 threshold 或达 max_width 时截断

    Args:
        d: 差分阶数 ∈ (0, 1]
        threshold: 权重截断阈值（|w| < threshold 时停止）
        max_width: 最大宽度硬限制（防 d 较小时权重序列爆炸）

    Returns:
        权重数组（反序，便于后续卷积）
    """
    if not 0 < d <= 1:
        raise ValueError(f"d 必须在 (0, 1]，得到 {d}")
    weights = [1.0]
    k = 1
    while k < max_width:
        w = weights[-1] * (k - 1 - d) / k
        if abs(w) < threshold:
            break
        weights.append(w)
        k += 1
    return np.array(weights[::-1])


def fractional_diff(
    series: pd.Series,
    d: float = 0.4,
    threshold: float = 1e-3,
    max_width: int = 200,
) -> pd.Series:
    """Fractional Differencing（固定宽度窗口实现）

    Args:
        series: 输入序列（价格/对数价格）
        d: 差分阶数 ∈ (0, 1]
        threshold: 权重截断阈值
        max_width: 最大宽度硬限制

    Returns:
        Fractionally differenced 序列（与 series 等长，前 K-1 个为 NaN）
    """
    if not 0 < d <= 1:
        raise ValueError(f"d 必须在 (0, 1]，得到 {d}")
    if not isinstance(series, pd.Series):
        series = pd.Series(series)

    weights = _get_weights(d, threshold, max_width=max_width)
    width = len(weights)
    n = len(series)
    if n < width:
        return pd.Series(np.nan, index=series.index)

    values = series.values
    out = np.full(n, np.nan)

    # 对每个 t（从 width-1 开始），加权求和 series[t-width+1..t]
    for i in range(width - 1, n):
        window = values[i - width + 1: i + 1]
        if not np.any(np.isnan(window)):
            out[i] = np.dot(weights, window)

    return pd.Series(out, index=series.index)


def adf_pvalue(series: pd.Series) -> float:
    """Augmented Dickey-Fuller 检验的 p-value（平稳性检验）

    Returns:
        p-value（< 0.05 = 平稳）
    """
    s = series.dropna()
    if len(s) < 20:
        return 1.0  # 数据太少，认为不平稳
    try:
        from statsmodels.tsa.stattools import adfuller
        return float(adfuller(s, autolag="AIC")[1])
    except Exception:
        return 1.0


def find_min_d(
    series: pd.Series,
    d_grid: np.ndarray | list[float] | None = None,
    threshold: float = 1e-3,
    alpha: float = 0.05,
) -> tuple[float, pd.DataFrame]:
    """找到使序列平稳的最小差分阶数 d*

    Args:
        series: 输入序列
        d_grid: 搜索的 d 值网格（默认 [0.0, 0.1, 0.2, ..., 1.0]）
        threshold: 权重截断阈值
        alpha: ADF 显著性阈值（默认 0.05）

    Returns:
        (d_star, report_df)
        - d_star: 使 ADF p-value < alpha 的最小 d；找不到返回 1.0
        - report_df: 每个 d 对应的 ADF p-value、是否平稳
    """
    if d_grid is None:
        d_grid = np.arange(0.0, 1.01, 0.1)

    rows = []
    for d in d_grid:
        if d == 0:
            fd = series.copy()
        else:
            fd = fractional_diff(series, d=d, threshold=threshold)
        pval = adf_pvalue(fd)
        rows.append({"d": d, "adf_pvalue": pval, "stationary": pval < alpha})

    report = pd.DataFrame(rows)
    stationary_ds = report[report["stationary"]]
    d_star = float(stationary_ds["d"].min()) if len(stationary_ds) > 0 else 1.0
    return d_star, report


def memory_retention(
    series: pd.Series,
    d: float,
    threshold: float = 1e-3,
) -> float:
    """计算 fractional diff 保留的记忆比例（vs 原始序列方差）

    Returns:
        记忆保留比例 ∈ [0, 1]：
        - d=0：100% 保留
        - d=1：通常 < 10%（仅保留原始方差的极少部分）
    """
    fd = fractional_diff(series, d=d, threshold=threshold)
    # 用对齐后的相关系数平方（R²）作为记忆保留度量
    aligned = pd.concat([series, fd], axis=1).dropna()
    if len(aligned) < 10:
        return 0.0
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    return float(corr ** 2) if corr == corr else 0.0
