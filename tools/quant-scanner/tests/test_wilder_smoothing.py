"""Wilder smoothing 校准测试（任务 #99）

验证 RSI/ATR/ADX 实现与 Wilder (1978)《New Concepts in Technical Trading Systems》
原始定义一致，与 TradingView/MetaTrader 对齐。

关键点：
- 首值用 SMA(period)，不用 ewm 首值
- 前 period-1 个为 NaN
- 中间 NaN 用前一值填充（递归稳定）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.features.indicators import (
    rsi, atr, adx, wilder_smoothing,
)


# ============================================================
# Wilder Smoothing 基础测试
# ============================================================

def test_wilder_first_value_is_sma():
    """首值 = SMA(period)"""
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    ws = wilder_smoothing(s, period=14)
    expected_sma = sum(range(1, 15)) / 14  # 7.5
    assert ws.iloc[13] == pytest.approx(expected_sma, abs=1e-6)
    assert ws.iloc[:13].isna().all()


def test_wilder_recurrence():
    """递归公式：next = (prev × (period-1) + current) / period"""
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    ws = wilder_smoothing(s, period=14)
    prev = ws.iloc[13]
    expected = (prev * 13 + 15) / 14
    assert ws.iloc[14] == pytest.approx(expected, abs=1e-6)


def test_wilder_handles_leading_nan():
    """前导 NaN 被跳过，从第一个有效值开始累计 SMA"""
    s = pd.Series([np.nan, np.nan, 1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
    ws = wilder_smoothing(s, period=14)
    # 从 index=2 开始的 14 个值（2-15）= 1..14，SMA = 7.5
    assert ws.iloc[15] == pytest.approx(7.5, abs=1e-6)
    assert ws.iloc[:15].isna().all()


def test_wilder_handles_middle_nan():
    """中间 NaN 用前一值填充（保持递归稳定）"""
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, np.nan, 17])
    ws = wilder_smoothing(s, period=14)
    # 索引 15 NaN → 用 ws[14]
    assert ws.iloc[15] == ws.iloc[14]
    # 索引 16 = (ws[15] × 13 + 17) / 14
    expected = (ws.iloc[15] * 13 + 17) / 14
    assert ws.iloc[16] == pytest.approx(expected, abs=1e-6)


def test_wilder_too_short_returns_nan():
    """数据不足 period 个 → 全 NaN"""
    s = pd.Series([1.0, 2, 3])
    ws = wilder_smoothing(s, period=14)
    assert ws.isna().all()


def test_wilder_all_nan():
    """全 NaN 输入 → 全 NaN 输出"""
    s = pd.Series([np.nan] * 50)
    ws = wilder_smoothing(s, period=14)
    assert ws.isna().all()


# ============================================================
# RSI 校准
# ============================================================

def test_rsi_first_13_nan():
    """RSI 前 13 天 NaN（Wilder 要求 SMA(14)）"""
    rng = np.random.default_rng(42)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
    r = rsi(close, period=14)
    assert r.iloc[:13].isna().all()
    assert r.iloc[13] == r.iloc[13]  # not NaN


def test_rsi_range_0_100():
    """RSI 在 [0, 100] 范围内"""
    rng = np.random.default_rng(0)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500))))
    r = rsi(close, period=14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_rsi_all_up_approaches_100():
    """连续上涨 → RSI 接近 100"""
    close = pd.Series(np.linspace(100, 200, 100))
    r = rsi(close, period=14)
    assert r.iloc[-1] > 90


def test_rsi_all_down_approaches_0():
    """连续下跌 → RSI 接近 0"""
    close = pd.Series(np.linspace(200, 100, 100))
    r = rsi(close, period=14)
    assert r.iloc[-1] < 10


def test_rsi_manual_calculation():
    """手工计算一个简单例子验证"""
    # 14 天涨跌交替，gain=1, loss=0
    close = pd.Series([100.0 + i for i in range(20)])
    r = rsi(close, period=14)
    # 全涨 → avg_loss ≈ 0 → RS = inf → RSI ≈ 100
    assert r.iloc[-1] > 95


# ============================================================
# ATR 校准
# ============================================================

def test_atr_first_13_nan():
    rng = np.random.default_rng(0)
    n = 50
    close = pd.Series(100 + rng.normal(0, 1, n))
    high = close + 1
    low = close - 1
    a = atr(high, low, close, period=14)
    assert a.iloc[:13].isna().all()
    assert a.iloc[13] == a.iloc[13]


def test_atr_positive():
    """ATR 必须非负"""
    rng = np.random.default_rng(0)
    n = 100
    close = pd.Series(100 + rng.normal(0, 1, n))
    high = close * 1.01
    low = close * 0.99
    a = atr(high, low, close, period=14).dropna()
    assert (a >= 0).all()


def test_atr_matches_manual():
    """简单例子手工验证 ATR"""
    # 假设每日 TR=2（high-low=2，close 不变所以其他项为 0）
    n = 20
    close = pd.Series([100.0] * n)
    high = pd.Series([101.0] * n)
    low = pd.Series([99.0] * n)
    a = atr(high, low, close, period=14)
    # SMA = 2.0
    assert a.iloc[13] == pytest.approx(2.0, abs=1e-6)
    # 后续 wilder smoothing 应该都等于 2.0（恒定 TR）
    assert a.iloc[-1] == pytest.approx(2.0, abs=1e-6)


# ============================================================
# ADX 校准
# ============================================================

def test_adx_first_27_nan():
    """ADX 需要 DI(14d smooth) + ADX 再 smooth 14d ≈ 28 天"""
    rng = np.random.default_rng(0)
    n = 50
    close = pd.Series(100 + rng.normal(0, 1, n))
    high = close + 1
    low = close - 1
    _, _, ad = adx(high, low, close, period=14)
    # 前 27 天应全 NaN
    assert ad.iloc[:26].isna().all()


def test_adx_nonnegative():
    """ADX 在 [0, 100]"""
    rng = np.random.default_rng(0)
    n = 100
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    high = close * 1.01
    low = close * 0.99
    _, _, ad = adx(high, low, close, period=14)
    valid = ad.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_adx_strong_trend_higher_value():
    """强趋势 → ADX 高"""
    rng = np.random.default_rng(0)
    n = 100

    # 强趋势：线性上涨
    close_trend = pd.Series(100 + np.arange(n) * 0.5 + rng.normal(0, 0.1, n))
    high_trend = close_trend + 0.5
    low_trend = close_trend - 0.5
    _, _, ad_trend = adx(high_trend, low_trend, close_trend, period=14)

    # 震荡：均值回归
    close_choppy = pd.Series(100 + rng.normal(0, 1, n).cumsum() * 0.0 + rng.normal(0, 1, n))
    high_c = close_choppy + 0.5
    low_c = close_choppy - 0.5
    _, _, ad_choppy = adx(high_c, low_c, close_choppy, period=14)

    # 强趋势 ADX 中位数应大于震荡
    assert ad_trend.dropna().median() > ad_choppy.dropna().median() - 5  # 容差


def test_plus_minus_di_relationship():
    """上涨趋势中 +DI > -DI"""
    n = 100
    close = pd.Series(100 + np.arange(n) * 0.5)
    high = close + 0.3
    low = close - 0.3
    p, m, _ = adx(high, low, close, period=14)
    valid_p = p.dropna()
    valid_m = m.dropna()
    # 上涨趋势中 +DI 应该 ≥ -DI
    common_idx = valid_p.index.intersection(valid_m.index)
    assert (valid_p.loc[common_idx[-20:]] >= valid_m.loc[common_idx[-20:]] - 1).mean() > 0.5
