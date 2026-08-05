"""Trend Template 信号测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.trend_template import TrendTemplateSignal


def _make_synthetic(n: int = 300, drift: float = 0.001, volatility: float = 0.02, seed: int = 42) -> pd.DataFrame:
    """生成合成日线数据

    drift > 0: 上升趋势
    drift < 0: 下降趋势
    """
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, volatility, size=n)
    prices = 100 * np.exp(np.cumsum(returns))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    df = pd.DataFrame({
        "open": prices,
        "high": prices * (1 + rng.uniform(0, 0.01, size=n)),
        "low": prices * (1 - rng.uniform(0, 0.01, size=n)),
        "close": prices,
        "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=dates)
    return df


def test_uptrend_passes_all_conditions():
    """强上升趋势应该满足 8 条至少 7 条通过"""
    df = _make_synthetic(n=400, drift=0.002, volatility=0.015)
    sig = TrendTemplateSignal()
    result = sig.evaluate("TEST_UP", df)
    assert result.value >= 0.75, f"强上升趋势应通过，实际 score={result.value}, reasons={result.reasons}"
    assert result.passed


def test_downtrend_fails():
    """强下降趋势应该不通过"""
    df = _make_synthetic(n=400, drift=-0.002, volatility=0.015)
    sig = TrendTemplateSignal()
    result = sig.evaluate("TEST_DOWN", df)
    assert result.value < 0.5
    assert not result.passed


def test_insufficient_data():
    """数据不足应返回 value=0"""
    df = _make_synthetic(n=100, drift=0.002)
    sig = TrendTemplateSignal()
    result = sig.evaluate("TEST_SHORT", df)
    assert result.value == 0.0
    assert not result.passed
    assert "数据不足" in result.reasons[0]


def test_value_clamped_to_unit_interval():
    df = _make_synthetic(n=400, drift=0.002)
    sig = TrendTemplateSignal()
    result = sig.evaluate("TEST", df)
    assert -1.0 <= result.value <= 1.0


def test_details_contain_required_keys():
    df = _make_synthetic(n=400, drift=0.002)
    sig = TrendTemplateSignal()
    result = sig.evaluate("TEST", df)
    assert "close" in result.details
    assert "ma50" in result.details
    assert "ma200" in result.details
    assert "high_52w" in result.details
    assert "dist_below_high_pct" in result.details
    assert "dist_above_low_pct" in result.details
    assert "conditions" in result.details
    assert "n_passed" in result.details
