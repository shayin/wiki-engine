"""PEAD 因子测试（Daniel-Hirshleifer-Sun 2020 JFE 框架）

覆盖：
- SUE 数学正确性（季节性随机游走）
- pead_signal 时间衰减
- pead_categorize 分类
- pead_summary 摘要
- 边界情况
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.pead_factor import (
    compute_sue,
    pead_signal,
    pead_categorize,
    pead_summary,
)


# ============================================================
# SUE 数学正确性
# ============================================================

def test_sue_seasonal_random_walk():
    """SUE 应等于 (EPS_t - EPS_{t-4}) / σ

    8 季度 EPS：前 4 个无 t-4 surprise → NaN → dropna 丢弃
    第 5-8 季度 surprise 有效，MAD min_periods=1 → 全部能算 SUE
    """
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.2, 1.1, 1.3, 1.4, 1.5, 1.3, 1.6], index=dates)
    sue = compute_sue(eps)
    assert len(sue) == 4  # 第 5/6/7/8 季度都有 SUE（MAD 归一化）
    assert (sue.notna()).all()


def test_sue_beat_vs_miss():
    """明显 beat（EPS 增长）SUE 应为正，miss 为负"""
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    # 超预期：每年同季 +50%
    eps = pd.Series([1.0, 1.0, 1.0, 1.0, 1.5, 1.5, 1.5, 1.5], index=dates)
    sue = compute_sue(eps)
    assert (sue > 0).all()

    # 不及预期：每年同季 -50%
    eps_miss = pd.Series([1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5], index=dates)
    sue_miss = compute_sue(eps_miss)
    assert (sue_miss < 0).all()


def test_sue_too_short_returns_empty():
    """EPS 序列 < 5 应返回空"""
    dates = pd.date_range("2023-01-01", periods=3, freq="QE")
    eps = pd.Series([1.0, 1.1, 1.2], index=dates)
    sue = compute_sue(eps)
    assert len(sue) == 0


def test_sue_accepts_dict():
    """SUE 应接受 dict 输入"""
    eps_dict = {
        "2022-03-31": 1.0, "2022-06-30": 1.1, "2022-09-30": 1.2, "2022-12-31": 1.3,
        "2023-03-31": 1.4, "2023-06-30": 1.5, "2023-09-30": 1.6, "2023-12-31": 1.7,
        "2024-03-31": 1.8, "2024-06-30": 1.9, "2024-09-30": 2.0, "2024-12-31": 2.1,
    }
    sue = compute_sue(eps_dict)
    assert len(sue) >= 1


# ============================================================
# pead_signal 时间衰减
# ============================================================

def test_pead_signal_decays_over_time():
    """PEAD 信号应在财报后 N 天内线性衰减到 0"""
    dates_eps = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.0, 1.0, 1.0, 1.5, 1.5, 1.5, 1.5], index=dates_eps)

    # 输出时间轴：跨最近一次财报后 60 天
    out_dates = pd.date_range("2024-12-31", periods=60, freq="B")
    signal = pead_signal(eps, price_index=out_dates, decay_window=60)

    assert len(signal) == 60
    # 最后一次 SUE 对应 2024-09-30（2024-12-31 那个被 std min_periods 丢）
    # 信号在 2024-12-31 时已过 92 天 → 超过 decay_window=60 → 0
    # 改用更长 decay_window 让信号保持
    signal2 = pead_signal(eps, price_index=out_dates, decay_window=200)
    # 信号应在第 1 天最大，往后衰减
    assert abs(signal2.iloc[0]) >= abs(signal2.iloc[-1])


def test_pead_signal_zero_before_first_earnings():
    """在第一次财报之前，信号应为 0"""
    dates_eps = pd.date_range("2024-06-30", periods=5, freq="QE")
    eps = pd.Series([1.0, 1.1, 1.2, 1.3, 1.5], index=dates_eps)
    # 输出时间轴全部在第一次财报之前
    out_dates = pd.date_range("2024-01-01", "2024-06-01", freq="B")
    signal = pead_signal(eps, price_index=out_dates, decay_window=60)
    assert (signal == 0).all()


def test_pead_signal_beat_positive():
    """超预期场景下，PEAD 信号应为正"""
    # 8 个季度，最近一次明显超预期
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.0], index=dates)  # 最后一季 +100%
    out_dates = pd.date_range(dates[-1], periods=10, freq="B")
    signal = pead_signal(eps, price_index=out_dates, decay_window=60)
    assert (signal > 0).any()


def test_pead_signal_miss_negative():
    """不及预期场景下，PEAD 信号应为负"""
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5], index=dates)  # 最后一季 -50%
    out_dates = pd.date_range(dates[-1], periods=10, freq="B")
    signal = pead_signal(eps, price_index=out_dates, decay_window=60)
    assert (signal < 0).any()


# ============================================================
# pead_categorize
# ============================================================

def test_categorize_thresholds():
    assert pead_categorize(3.0) == "strong_beat"
    assert pead_categorize(1.5) == "beat"
    assert pead_categorize(0.5) == "in_line"
    assert pead_categorize(-0.5) == "in_line"
    assert pead_categorize(-1.5) == "miss"
    assert pead_categorize(-3.0) == "strong_miss"


# ============================================================
# pead_summary
# ============================================================

def test_summary_structure():
    # 构造有方差的 EPS（避免 std=0）
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.1, 1.0, 1.2, 1.4, 1.5, 1.3, 1.6], index=dates)
    summary = pead_summary(eps, latest_n=4)
    assert "sue_history" in summary
    assert "latest_sue" in summary
    assert "latest_category" in summary
    assert "latest_fb_date" in summary
    assert "n_quarters" in summary
    assert len(summary["sue_history"]) <= 4
    assert summary["latest_category"] in ["strong_beat", "beat", "in_line", "miss", "strong_miss"]


def test_summary_empty_eps():
    summary = pead_summary({}, latest_n=4)
    assert summary["latest_sue"] is None
    assert summary["latest_category"] == "unknown"
    assert summary["n_quarters"] == 0


# ============================================================
# 边界情况
# ============================================================

def test_empty_eps_returns_zeros():
    """空 EPS 输入应返回零信号"""
    out_dates = pd.date_range("2024-01-01", periods=10, freq="B")
    signal = pead_signal({}, price_index=out_dates, decay_window=60)
    assert (signal == 0).all()


def test_constant_eps_no_surprise():
    """完全相同 EPS → surprise 全 0 → SUE = NaN（除以 0）"""
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0] * 8, index=dates)
    sue = compute_sue(eps)
    # surprise 全 0，std=0，SUE 应被 dropna 丢弃（或为 NaN）
    assert len(sue) == 0 or sue.isna().all()


def test_pead_signal_with_default_index():
    """price_index=None 应回退到 lookback_days"""
    dates = pd.date_range("2023-01-01", periods=8, freq="QE")
    eps = pd.Series([1.0, 1.1, 1.0, 1.2, 1.4, 1.5, 1.3, 1.6], index=dates)
    signal = pead_signal(eps, price_index=None, decay_window=300, lookback_days=365)
    assert len(signal) > 0
    # 最近一次财报 SUE 应在信号中体现
    assert (signal != 0).any()
