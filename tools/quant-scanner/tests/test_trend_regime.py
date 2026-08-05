"""趋势制度过滤测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.trend_regime import TrendRegimeSignal


def _make_dates(n: int = 300) -> pd.DatetimeIndex:
    return pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")


def _make_trending_df(n: int = 300, drift: float = 0.003, vol: float = 0.01, seed: int = 42) -> pd.DataFrame:
    """强趋势合成数据"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, size=n)
    prices = 100 * np.exp(np.cumsum(returns))
    dates = _make_dates(n)
    return pd.DataFrame({
        "open": prices, "high": prices * (1 + rng.uniform(0, 0.01, n)),
        "low": prices * (1 - rng.uniform(0, 0.01, n)),
        "close": prices, "volume": rng.integers(1e6, 5e6, n).astype(float),
    }, index=dates)


def _make_sideways_df(n: int = 300, vol: float = 0.01, seed: int = 7) -> pd.DataFrame:
    """震荡市合成数据"""
    rng = np.random.default_rng(seed)
    # 围绕 100 震荡，无趋势
    returns = rng.normal(0, vol, size=n)
    prices = 100 * np.exp(np.cumsum(returns))
    # 拉回 100 附近（mean-revert）
    for i in range(1, n):
        prices[i] = prices[i] * 0.99 + 100 * 0.01
    dates = _make_dates(n)
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1e6, 5e6, n).astype(float),
    }, index=dates)


def test_strong_uptrend_passes():
    """强趋势上涨应通过"""
    df = _make_trending_df(drift=0.003, vol=0.008)
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    assert r.value >= 0.5, f"强趋势应通过: {r.reasons}"
    assert r.passed
    assert r.details["direction"] == "上涨"


def test_downtrend_negative_score():
    """强下跌趋势 = 负分"""
    df = _make_trending_df(drift=-0.003, vol=0.008)
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    assert r.value <= -0.5
    assert not r.passed
    assert r.details["direction"] == "下跌"


def test_sideways_low_adx():
    """震荡市 ADX 低"""
    df = _make_sideways_df(vol=0.008)
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    # 震荡市 ADX 应低
    assert r.details["adx"] < 30
    # 得分应在 -0.5 到 0.5 之间（弱趋势或无趋势）
    assert -0.6 <= r.value <= 0.6


def test_dow_phase_classified():
    """道氏阶段被正确分类"""
    df = _make_trending_df(drift=0.003, vol=0.008)
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    assert r.details["dow_phase"] in [1, 2, 3, 4]
    assert "dow_label" in r.details
    # 强趋势上涨 = 阶段 2
    assert r.details["dow_phase"] == 2


def test_details_keys():
    """details 包含必要字段"""
    df = _make_trending_df()
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    for key in ["adx", "+di", "-di", "ma20", "ma50", "ma200", "dow_phase",
                "dow_label", "direction", "trend_strength", "ma_alignment"]:
        assert key in r.details, f"缺字段: {key}"


def test_insufficient_data():
    """数据不足返回 0"""
    rng = np.random.default_rng(1)
    n = 100
    prices = 100 + rng.normal(0, 1, n).cumsum()
    dates = _make_dates(n)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": rng.integers(1e6, 2e6, n).astype(float),
    }, index=dates)
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert not r.passed
    assert r.details["error"] == "数据不足"


def test_adx_calculation_reasonable():
    """ADX 计算结果合理（0-100 之间）"""
    df = _make_trending_df()
    sig = TrendRegimeSignal()
    r = sig.evaluate("TEST", df)
    adx = r.details["adx"]
    assert 0 <= adx <= 100
    assert 0 <= r.details["+di"] <= 100
    assert 0 <= r.details["-di"] <= 100
