"""MAX 因子测试（Bali-Cakici-Whitelaw 2010 JFE）

覆盖：
- MAX(1) / MAX(5) / MAX(k) 数学正确性
- 彩票型股票（MAX 高）vs 平稳股（MAX 低）
- 边界情况
- 分位 rank
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.max_factor import (
    max_factor,
    max_factor_1,
    max_factor_5,
    max_decile_rank,
)


@pytest.fixture
def ohlcv_60() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 60
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"close": close}, index=dates)


# ============================================================
# 数学正确性
# ============================================================

def test_max_factor_k1_equals_max():
    """MAX(k=1) 应等于 rolling max of returns"""
    close = pd.Series([100, 101, 102, 100, 105, 103], dtype=float)
    rets = close.pct_change()
    max1 = max_factor(close, window=3, k=1)
    # 在 t=4 (close=105) 处，过去 3 天 rets = [+0.01, -0.0196, +0.05]
    # max = +0.05
    assert abs(max1.iloc[4] - rets.iloc[2:5].max()) < 1e-9


def test_max_factor_k5_is_top5_mean():
    """MAX(k=5) 应是 top-5 平均"""
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 30)), dtype=float)
    max5 = max_factor(close, window=10, k=5)
    rets = close.pct_change()
    # 在 t=29，过去 10 天 top-5 平均
    expected = np.sort(rets.iloc[20:30].values)[-5:].mean()
    assert abs(max5.iloc[29] - expected) < 1e-9


def test_max_factor_window_too_short_returns_nan(ohlcv_60):
    """前 window-1 个值应为 NaN"""
    max1 = max_factor(ohlcv_60["close"], window=20, k=1)
    assert max1.iloc[:18].isna().all()
    assert max1.iloc[19:].notna().all()


def test_max_factor_k_greater_than_window_raises():
    close = pd.Series([100, 101, 102])
    with pytest.raises(ValueError, match="不能大于"):
        max_factor(close, window=3, k=5)


def test_max_factor_k_invalid_raises():
    close = pd.Series([100, 101, 102])
    with pytest.raises(ValueError, match="≥ 1"):
        max_factor(close, window=3, k=0)


def test_max_factor_positive():
    """最大单日收益必为正（除非全跌）"""
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 100))))
    max5 = max_factor_5(close, window=20)
    valid = max5.dropna()
    assert (valid > 0).all()  # 至少有一天正收益


# ============================================================
# 彩票型 vs 平稳
# ============================================================

def test_lottery_stock_has_higher_max():
    """彩票型股票（含极端单日暴涨）MAX 应显著更高"""
    n = 50
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(2)

    # 平稳股：每日 ±1%
    flat_rets = rng.normal(0, 0.01, n)
    flat_close = pd.Series(100 * np.exp(np.cumsum(flat_rets)), index=dates)

    # 彩票股：大部分平稳，但某一天 +20%
    lottery_rets = rng.normal(0, 0.01, n).copy()
    lottery_rets[30] = 0.20  # 单日 +20%
    lottery_close = pd.Series(100 * np.exp(np.cumsum(lottery_rets)), index=dates)

    flat_max = max_factor_5(flat_close, window=20).iloc[35]
    lottery_max = max_factor_5(lottery_close, window=20).iloc[35]

    assert lottery_max > flat_max * 3  # 彩票股 MAX 至少 3x 平稳股


def test_max_rank_pct_range():
    """分位 rank 应在 [0, 1]"""
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 100))))
    rank = max_decile_rank(close, window=20, k=5)
    valid = rank.dropna()
    assert (valid >= 0).all()
    assert (valid <= 1).all()


# ============================================================
# 快捷函数等价性
# ============================================================

def test_max_factor_1_equals_k1(ohlcv_60):
    """max_factor_1 == max_factor(k=1)"""
    a = max_factor_1(ohlcv_60["close"], window=20)
    b = max_factor(ohlcv_60["close"], window=20, k=1)
    pd.testing.assert_series_equal(a, b)


def test_max_factor_5_equals_k5(ohlcv_60):
    """max_factor_5 == max_factor(k=5)"""
    a = max_factor_5(ohlcv_60["close"], window=20)
    b = max_factor(ohlcv_60["close"], window=20, k=5)
    pd.testing.assert_series_equal(a, b)


# ============================================================
# 边界情况
# ============================================================

def test_short_series_no_crash():
    """短序列不崩"""
    close = pd.Series([100, 101, 102, 103])
    max1 = max_factor(close, window=3, k=1)
    assert max1.iloc[-1] is not None or np.isnan(max1.iloc[-1])


def test_max_factor_with_constant_price():
    """常量价格（无波动）→ MAX ≈ 0"""
    close = pd.Series([100] * 30, dtype=float)
    max1 = max_factor(close, window=20, k=1)
    # 收益全为 0，MAX 也应为 0
    assert (max1.dropna() == 0).all()


def test_max_factor_with_extreme_outlier():
    """极端单日 outlier 应主导 MAX（用 simple return 构造 close）"""
    # 用 simple return 而非 log return，保证 pct_change 还原出原值
    simple_rets = np.array([0.0, 0.01, 0.02, 0.5, -0.01, 0.015])  # 0.5 是极端
    close_vals = [100.0]
    for r in simple_rets[1:]:
        close_vals.append(close_vals[-1] * (1 + r))
    close = pd.Series(close_vals)
    max1 = max_factor(close, window=5, k=1)
    # MAX 应等于 0.5
    assert abs(max1.iloc[-1] - 0.5) < 1e-6
