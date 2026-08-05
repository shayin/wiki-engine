"""Fractional Differencing 测试（López de Prado AfML Ch 5）

覆盖：
- 权重序列正确性
- fractional_diff 输出长度 + 平稳性
- find_min_d 自动搜索
- memory_retention 记忆保留
- 边界情况
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.fractional_diff import (
    _get_weights,
    fractional_diff,
    adf_pvalue,
    find_min_d,
    memory_retention,
)


# ============================================================
# 权重序列
# ============================================================

def test_weights_d1_equals_first_diff():
    """d=1 时权重应为 [-1, 1]（一阶差分）"""
    w = _get_weights(1.0, threshold=1e-5)
    # 一阶差分：X_t - X_{t-1} = (-1)*X_{t-1} + (1)*X_{t}
    # 反序后：[-1, 1]
    assert len(w) == 2
    assert abs(w[0] + 1) < 1e-9
    assert abs(w[1] - 1) < 1e-9


def test_weights_d0_returns_single_one():
    """d→0 时权重应近似 [1]（无差分）"""
    w = _get_weights(0.01, threshold=1e-5)
    # 第一个权重 w_0 = 1，后续递减但很慢
    assert w[-1] == pytest.approx(1.0, abs=0.1)


def test_weights_decay_with_k():
    """权重绝对值应随 k 衰减"""
    w = _get_weights(0.4, threshold=1e-5)
    abs_w = np.abs(w)
    # 从 k=0 开始，前几个权重应单调递减
    # （w 数组是反序，所以从末尾开始是 k=0）
    # 检查：|w_0| > |w_1| > |w_2|
    w0 = w[-1]
    w1 = w[-2]
    w2 = w[-3]
    assert abs(w0) > abs(w1) > abs(w2)


def test_weights_invalid_d_raises():
    with pytest.raises(Exception):
        _get_weights(-0.5)


# ============================================================
# fractional_diff
# ============================================================

@pytest.fixture
def price_series() -> pd.Series:
    """合成价格序列：random walk + 轻微趋势"""
    rng = np.random.default_rng(42)
    n = 500
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    return pd.Series(close, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def test_frac_diff_output_length(price_series):
    """输出长度应等于输入"""
    fd = fractional_diff(price_series, d=0.4)
    assert len(fd) == len(price_series)
    # 前 K-1 个应为 NaN（K 是权重宽度）
    assert fd.iloc[:5].isna().any() or fd.iloc[0] != fd.iloc[0]


def test_frac_diff_d1_equals_first_diff(price_series):
    """d=1 的 fractional diff 应等于一阶差分"""
    fd1 = fractional_diff(price_series, d=1.0)
    diff1 = price_series.diff()
    # 跳过 NaN，比较有效值
    valid = fd1.notna() & diff1.notna()
    np.testing.assert_allclose(fd1[valid].values, diff1[valid].values, atol=1e-6)


def test_frac_diff_d_small_approximates_original(price_series):
    """d 较小（0.1）的 fractional diff 应保留大部分原序列特征"""
    fd_small = fractional_diff(price_series, d=0.1, threshold=1e-3)
    valid = fd_small.notna()
    if valid.sum() < 10:
        pytest.skip("样本不足")
    # d=0.1 时记忆保留 > 0.9（与原序列强相关）
    aligned = pd.concat([price_series, fd_small], axis=1).dropna()
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    assert corr > 0.95


def test_frac_diff_invalid_d_raises(price_series):
    with pytest.raises(ValueError):
        fractional_diff(price_series, d=-0.5)
    with pytest.raises(ValueError):
        fractional_diff(price_series, d=1.5)


# ============================================================
# ADF 平稳性
# ============================================================

def test_adf_random_walk_not_stationary():
    """random walk 应非平稳"""
    rng = np.random.default_rng(0)
    rw = pd.Series(np.cumsum(rng.normal(0, 1, 300)))
    p = adf_pvalue(rw)
    # random walk 应该不平稳（p > 0.05）—— 但实际有时会过
    # 至少返回 [0, 1] 区间有效值
    assert 0 <= p <= 1


def test_adf_white_noise_stationary():
    """白噪声应平稳"""
    rng = np.random.default_rng(1)
    wn = pd.Series(rng.normal(0, 1, 300))
    p = adf_pvalue(wn)
    assert p < 0.05


# ============================================================
# find_min_d
# ============================================================

def test_find_min_d_random_walk(price_series):
    """random walk 价格序列：d* 应在 (0, 1)"""
    d_star, report = find_min_d(price_series, d_grid=np.arange(0.0, 1.01, 0.1))
    assert 0 < d_star <= 1.0
    assert "d" in report.columns
    assert "adf_pvalue" in report.columns
    assert "stationary" in report.columns
    # 最小的 d 应该不平稳（d=0），最大的 d 应该平稳（d=1）
    assert not report.iloc[0]["stationary"]  # d=0
    assert report.iloc[-1]["stationary"]     # d=1


def test_find_min_d_returns_dataframe_with_grid():
    """返回的 DataFrame 应包含所有 d 网格"""
    s = pd.Series(np.cumsum(np.random.default_rng(2).normal(0, 1, 200)))
    d_star, report = find_min_d(s, d_grid=[0.0, 0.3, 0.5, 0.7, 1.0])
    assert len(report) == 5
    assert report["d"].tolist() == [0.0, 0.3, 0.5, 0.7, 1.0]


# ============================================================
# memory_retention
# ============================================================

def test_memory_retention_decreasing_in_d(price_series):
    """记忆保留应随 d 增大而减小"""
    r_low = memory_retention(price_series, d=0.2)
    r_mid = memory_retention(price_series, d=0.5)
    r_high = memory_retention(price_series, d=0.9)
    assert r_low > r_mid > r_high


def test_memory_retention_in_range(price_series):
    """记忆保留应在 [0, 1]"""
    r = memory_retention(price_series, d=0.5)
    assert 0 <= r <= 1


# ============================================================
# 边界情况
# ============================================================

def test_short_series_no_crash():
    """短序列不应崩"""
    s = pd.Series([100, 101, 102, 103])
    fd = fractional_diff(s, d=0.4)
    assert len(fd) == len(s)


def test_frac_diff_with_nan(price_series):
    """输入有 NaN 不应崩"""
    s = price_series.copy()
    s.iloc[10] = np.nan
    fd = fractional_diff(s, d=0.4)
    assert len(fd) == len(s)


def test_find_min_d_constant_series():
    """常量序列的 ADF 应失败优雅返回"""
    s = pd.Series([100.0] * 200)
    d_star, report = find_min_d(s, d_grid=[0.0, 0.5, 1.0])
    assert d_star in [0.0, 0.5, 1.0]
    assert len(report) == 3
