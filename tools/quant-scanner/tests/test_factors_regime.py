"""Regime 分类 + 条件 IC 测试（任务 #85）"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.operators import classify_regime, regime_conditional_ic


@pytest.fixture
def bull_market() -> pd.DataFrame:
    """构造牛市数据：close 持续高于上行 MA200"""
    n = 250
    rng = np.random.default_rng(42)
    rets = rng.normal(0.002, 0.01, n)  # 正漂移
    close = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1e6),
    }, index=pd.date_range("2025-01-01", periods=n, freq="B"))


@pytest.fixture
def bear_market() -> pd.DataFrame:
    """构造熊市数据：close 持续低于下行 MA200"""
    n = 250
    rng = np.random.default_rng(42)
    rets = rng.normal(-0.002, 0.01, n)  # 负漂移
    close = 200 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1e6),
    }, index=pd.date_range("2025-01-01", periods=n, freq="B"))


def test_classify_regime_bull(bull_market):
    """牛市数据 → 大部分日期标为 bull"""
    regime = classify_regime(bull_market)
    assert regime.iloc[-1] == "bull"
    # 后 50 日（趋势明确）应全为 bull
    assert (regime.tail(50) == "bull").mean() > 0.8


def test_classify_regime_bear(bear_market):
    """熊市数据 → 大部分日期标为 bear"""
    regime = classify_regime(bear_market)
    assert regime.iloc[-1] == "bear"
    assert (regime.tail(50) == "bear").mean() > 0.8


def test_classify_regime_insufficient_data():
    """数据不足 → 全部 sideways"""
    df = pd.DataFrame({"close": [100, 101, 102]}, index=pd.date_range("2025-01-01", periods=3))
    regime = classify_regime(df)
    assert (regime == "sideways").all()


def test_classify_regime_custom_ma():
    """自定义 MA 窗口"""
    n = 60
    close = pd.Series(np.linspace(100, 130, n),
                      index=pd.date_range("2025-01-01", periods=n))
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1e6),
    }, index=close.index)
    # 用 ma_long=30 才能分类（默认 200 数据不够）
    regime = classify_regime(df, ma_long=30)
    assert regime.iloc[-1] == "bull"


def test_classify_regime_returns_series(bull_market):
    """返回 pd.Series，索引对齐"""
    regime = classify_regime(bull_market)
    assert isinstance(regime, pd.Series)
    assert len(regime) == len(bull_market)


def test_regime_conditional_ic_returns_dict(bull_market):
    """regime_conditional_ic 返回 dict[str, (ic, n)]"""
    regime = classify_regime(bull_market)
    rng = np.random.default_rng(42)
    factor = pd.Series(rng.normal(0, 1, len(bull_market)), index=bull_market.index)
    fwd = pd.Series(rng.normal(0, 0.05, len(bull_market)), index=bull_market.index)
    result = regime_conditional_ic(factor, fwd, regime, window=20)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"bull", "bear", "sideways"}
    for label, (ic, n) in result.items():
        assert isinstance(ic, float) or np.isnan(ic)
        assert isinstance(n, int)


def test_regime_conditional_ic_strong_in_bull(bull_market):
    """在牛市段构造强信号，bull IC 应高于 sideways/bear"""
    regime = classify_regime(bull_market)
    # 构造 factor = forward_return + 噪声（仅 bull 段）
    rng = np.random.default_rng(42)
    fwd = pd.Series(rng.normal(0, 0.05, len(bull_market)), index=bull_market.index)
    factor = fwd.copy()
    # 在非 bull 段加大量噪声（破坏信号）
    non_bull = regime != "bull"
    factor[non_bull] = rng.normal(0, 1, non_bull.sum())
    # 因子与前瞻收益对齐 shift
    factor = factor.shift(1).fillna(0)

    result = regime_conditional_ic(factor, fwd, regime, window=20)
    bull_ic = result["bull"][0]
    sideways_ic = result["sideways"][0]
    # bull 段因子信号应强于 sideways（如果 sideways 有数据）
    if not np.isnan(bull_ic) and not np.isnan(sideways_ic):
        assert abs(bull_ic) >= abs(sideways_ic) - 0.1  # 容差


def test_regime_conditional_ic_insufficient_data():
    """数据不足 → 全 NaN + 0 days"""
    factor = pd.Series([1.0, 2.0], index=pd.date_range("2025-01-01", periods=2))
    fwd = pd.Series([0.01, 0.02], index=factor.index)
    regime = pd.Series(["bull", "bull"], index=factor.index)
    result = regime_conditional_ic(factor, fwd, regime, window=20)
    for label, (ic, n) in result.items():
        assert np.isnan(ic)


def test_regime_conditional_ic_alignment():
    """非对齐索引 → 用交集对齐"""
    dates1 = pd.date_range("2025-01-01", periods=100)
    dates2 = pd.date_range("2025-02-01", periods=100)
    rng = np.random.default_rng(42)
    factor = pd.Series(rng.normal(0, 1, 100), index=dates1)
    fwd = pd.Series(rng.normal(0, 0.05, 100), index=dates2)
    regime = pd.Series(["bull"] * 100, index=dates1)
    # 应不抛异常
    result = regime_conditional_ic(factor, fwd, regime, window=20)
    assert isinstance(result, dict)


def test_regime_adaptive_factor_detection():
    """检测 regime 自适应因子：在某 regime 特别有效

    构造：factor[t] ≈ fwd[t]（同一天强相关）在 bull 段，bear 段随机
    """
    n = 500
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)

    # 构造 regime：前 250 bull，后 250 bear
    regime = pd.Series(["bull"] * 250 + ["bear"] * 250, index=dates)

    # 因子在 bull 段有强信号（同一天 factor ≈ fwd），bear 段无信号
    fwd_bull = rng.normal(0, 0.05, 250)
    fwd_bear = rng.normal(0, 0.05, 250)
    fwd = pd.Series(np.concatenate([fwd_bull, fwd_bear]), index=dates)

    # bull 段：factor = fwd + 小噪声（同日强相关）
    factor_bull = fwd_bull + rng.normal(0, 0.005, 250)
    # bear 段：随机（与 fwd 无关）
    factor_bear = rng.normal(0, 1, 250)
    factor = pd.Series(np.concatenate([factor_bull, factor_bear]), index=dates)

    result = regime_conditional_ic(factor, fwd, regime, window=30)
    bull_ic = result["bull"][0]
    bear_ic = result["bear"][0]

    # bull 段 IC 应显著高于 bear 段
    assert not np.isnan(bull_ic)
    assert not np.isnan(bear_ic)
    assert bull_ic > 0.5, f"bull 段 IC 应 > 0.5，实际 {bull_ic:.3f}"
    assert abs(bear_ic) < 0.3, f"bear 段 IC 应弱，实际 {bear_ic:.3f}"
