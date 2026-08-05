"""流动性因子测试

覆盖：
- Amihud ILLIQ（原始、log、反转）
- Corwin-Schultz Spread
- Pastor-Stambaugh 创新
- 综合得分
- 边界情况（零成交、NaN、常数列）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.liquidity import (
    amihud_illiq,
    amihud_illiq_log,
    amihud_implied_turnover,
    corwin_schultz_spread,
    corwin_schultz_spread_bps,
    pastor_stambaugh_innov,
    liquidity_composite,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def ohlcv_500() -> dict[str, pd.Series]:
    """500 日合成 OHLCV"""
    rng = np.random.default_rng(42)
    n = 500
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n))), name="close")
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    volume = pd.Series(rng.integers(1e6, 5e6, n).astype(float), name="volume")
    return {"close": close, "high": high, "low": low, "volume": volume}


@pytest.fixture
def market_ret() -> pd.Series:
    rng = np.random.default_rng(7)
    return pd.Series(rng.normal(0.0003, 0.01, 500), name="market")


# ============================================================
# Amihud ILLIQ
# ============================================================

def test_illiq_nonnegative(ohlcv_500):
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    valid = illiq.dropna()
    assert (valid >= 0).all()


def test_illiq_size_matches(ohlcv_500):
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    assert len(illiq) == len(ohlcv_500["close"])


def test_illiq_first_window_nan(ohlcv_500):
    """前 window-1 个应全 NaN"""
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"],
                        window=21, min_periods=21)
    assert illiq.iloc[:20].isna().all()
    assert illiq.iloc[21] == illiq.iloc[21]  # not NaN


def test_illiq_low_liquidity_higher_value():
    """成交量小 → ILLIQ 大（流动性差）"""
    rng = np.random.default_rng(0)
    n = 100
    close = pd.Series(100 + rng.normal(0, 1, n))
    high = close * 1.01
    low = close * 0.99
    vol_low = pd.Series(np.full(n, 1e4))  # 低成交
    vol_high = pd.Series(np.full(n, 1e8))  # 高成交

    illiq_low_vol = amihud_illiq(close, vol_low, window=21)
    illiq_high_vol = amihud_illiq(close, vol_high, window=21)
    # 低成交的 ILLIQ 显著大于高成交
    assert illiq_low_vol.median() > illiq_high_vol.median() * 100


def test_illiq_zero_volume_handling():
    """零成交日不应导致 inf/crash"""
    rng = np.random.default_rng(1)
    n = 50
    close = pd.Series(100 + rng.normal(0, 1, n))
    volume = pd.Series(np.full(n, 1e6).astype(float))
    volume.iloc[10:15] = 0  # 零成交
    illiq = amihud_illiq(close, volume, window=10)
    assert illiq.replace([np.inf, -np.inf], np.nan).notna().any()
    assert not (illiq == np.inf).any()


def test_illiq_log_finite(ohlcv_500):
    log_illiq = amihud_illiq_log(ohlcv_500["close"], ohlcv_500["volume"])
    valid = log_illiq.dropna()
    assert np.isfinite(valid).all()


def test_illiq_implied_turnover_positive(ohlcv_500):
    turnover = amihud_implied_turnover(ohlcv_500["close"], ohlcv_500["volume"])
    valid = turnover.dropna()
    assert (valid > 0).all()


# ============================================================
# Corwin-Schultz Spread
# ============================================================

def test_cs_spread_nonnegative(ohlcv_500):
    """价差应非负"""
    spread = corwin_schultz_spread(ohlcv_500["high"], ohlcv_500["low"])
    valid = spread.dropna()
    assert (valid >= 0).all()


def test_cs_spread_bps_reasonable(ohlcv_500):
    """美股典型价差范围 1-200 bps"""
    spread = corwin_schultz_spread_bps(ohlcv_500["high"], ohlcv_500["low"])
    valid = spread.dropna()
    assert (valid > 0).all()
    # 合成数据可能偏大，但应在合理范围
    assert valid.median() < 5000


def test_cs_spread_size_matches(ohlcv_500):
    spread = corwin_schultz_spread(ohlcv_500["high"], ohlcv_500["low"])
    assert len(spread) == len(ohlcv_500["high"])


def test_cs_spread_high_volatility_higher_spread():
    """高波动 → 更大的有效价差估计（spread 含波动成分）"""
    rng = np.random.default_rng(3)
    n = 100
    base = 100 + np.cumsum(rng.normal(0, 0.01, n))

    # 低波动：日内幅度 0.5%
    low_vol_high = pd.Series(base * 1.005)
    low_vol_low = pd.Series(base * 0.995)

    # 高波动：日内幅度 3%
    hi_vol_high = pd.Series(base * 1.03)
    hi_vol_low = pd.Series(base * 0.97)

    low_spread = corwin_schultz_spread(low_vol_high, low_vol_low, window=10).median()
    hi_spread = corwin_schultz_spread(hi_vol_high, hi_vol_low, window=10).median()

    assert hi_spread > low_spread


def test_cs_spread_handles_zero_high_low():
    """high == low 时不应 crash"""
    n = 50
    base = pd.Series(np.full(n, 100.0))
    spread = corwin_schultz_spread(base, base, window=10)
    # 全 NaN 或全 0，但不 crash
    assert len(spread) == n


# ============================================================
# Pastor-Stambaugh
# ============================================================

def test_ps_innov_returns_series(ohlcv_500, market_ret):
    ps = pastor_stambaugh_innov(
        ohlcv_500["close"], ohlcv_500["volume"], market_ret, window=60
    )
    assert isinstance(ps, pd.Series)
    assert len(ps) == len(ohlcv_500["close"])


def test_ps_innov_first_window_nan(ohlcv_500, market_ret):
    ps = pastor_stambaugh_innov(
        ohlcv_500["close"], ohlcv_500["volume"], market_ret, window=60
    )
    assert ps.iloc[:59].isna().all()


# ============================================================
# Liquidity Composite
# ============================================================

def test_composite_clip_range(ohlcv_500):
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    spread = corwin_schultz_spread(ohlcv_500["high"], ohlcv_500["low"])
    comp = liquidity_composite(illiq, spread)
    valid = comp.dropna()
    assert (valid >= -5).all()
    assert (valid <= 5).all()


def test_composite_only_illiq(ohlcv_500):
    """只传 illiq 也能跑"""
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    comp = liquidity_composite(illiq)
    valid = comp.dropna()
    assert len(valid) > 0


def test_composite_higher_illiq_higher_score(ohlcv_500):
    """ILLIQ 高 → 综合得分高（higher_is_illiquid=True 默认）"""
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    spread = corwin_schultz_spread(ohlcv_500["high"], ohlcv_500["low"])
    comp = liquidity_composite(illiq, spread)

    illiq_extreme = illiq * 100
    comp_extreme = liquidity_composite(illiq_extreme, spread)

    assert comp_extreme.dropna().median() > comp.dropna().median()


def test_composite_sign_flip(ohlcv_500):
    """higher_is_illiquid=False → 信号反转"""
    illiq = amihud_illiq(close=ohlcv_500["close"], volume=ohlcv_500["volume"])
    comp_high = liquidity_composite(illiq, higher_is_illiquid=True)
    comp_low = liquidity_composite(illiq, higher_is_illiquid=False)
    valid = (comp_high + comp_low).dropna()
    assert valid.abs().max() < 1e-9
