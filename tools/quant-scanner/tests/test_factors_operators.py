"""factors/operators.py 算子库测试

覆盖：
- 时序统计算子（ts_mean / ts_rank / ts_delta / ts_arg_max / ts_decay_linear）
- 横截面 rank（Series fallback + DataFrame 真跨截面）
- 数学算子（div 安全除法 / sign / _binary_op 标量+Series）
- factor_ic 信息系数（强因子高 IC、无预测力 IC≈0）
- normalize_ohclv / vwap
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.operators import (
    ts_mean, ts_std, ts_max, ts_min, ts_arg_max, ts_arg_min,
    ts_delta, ts_delay, ts_rank, ts_corr, ts_cov, ts_sum,
    ts_decay_linear, ts_decay_exp, ts_product,
    rank, scale, zscore, winsorize,
    add, sub, mul, div, gt, lt, eq, abs_, sign, log, power, max_, min_,
    returns, log_returns, vwap, typical_price, rsi, macd, bollinger, atr, obv,
    normalize_ohclv, factor_ic, factor_ir,
)


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """合成 OHLCV（300 日，带趋势 + 噪声）"""
    rng = np.random.default_rng(42)
    n = 300
    rets = rng.normal(0.0005, 0.02, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    op = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(1_000_000, 10_000_000, n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


# ============================================================
# A. 时序统计
# ============================================================

def test_ts_mean_window():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    m = ts_mean(s, window=3)
    # 末值应为 (3+4+5)/3 = 4
    assert m.iloc[-1] == pytest.approx(4.0)
    assert m.iloc[0] == pytest.approx(1.0)  # min_periods=1


def test_ts_std_window():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    std = ts_std(s, window=5)
    # 整体 std = sqrt(2.5) ≈ 1.581
    assert std.iloc[-1] == pytest.approx(np.std([1, 2, 3, 4, 5], ddof=1), rel=1e-6)


def test_ts_delta():
    s = pd.Series([10, 20, 30, 40], dtype=float)
    d = ts_delta(s, period=1)
    assert d.iloc[0] != d.iloc[0]  # NaN
    assert d.iloc[1] == 10
    assert d.iloc[2] == 10


def test_ts_delay():
    s = pd.Series([10, 20, 30], dtype=float)
    d = ts_delay(s, period=2)
    assert np.isnan(d.iloc[0])
    assert np.isnan(d.iloc[1])
    assert d.iloc[2] == 10


def test_ts_arg_max():
    s = pd.Series([1.0, 5.0, 3.0, 2.0, 4.0])
    amax = ts_arg_max(s, window=5)
    # 最大值在 index=1
    assert amax.iloc[-1] == 1.0


def test_ts_arg_min():
    s = pd.Series([5.0, 1.0, 3.0, 4.0, 2.0])
    amin = ts_arg_min(s, window=5)
    assert amin.iloc[-1] == 1.0


def test_ts_rank_normalization():
    """ts_rank 输出 [0, 1]"""
    s = pd.Series(np.arange(100, dtype=float))
    r = ts_rank(s, window=100)
    # 最新值 = 最大值 → rank = 1.0
    assert r.iloc[-1] == pytest.approx(1.0, abs=0.05)
    # 早期值不应 NaN（min_periods=2）
    assert r.iloc[1] >= 0


def test_ts_decay_linear_weights():
    """线性衰减：近期权重大"""
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    # window=2，权重 [2, 1]/3
    d = ts_decay_linear(s, window=2)
    # 末值 = (3*2 + 4*1)/3 = 10/3
    assert d.iloc[-1] == pytest.approx((3 * 2 + 4 * 1) / 3, rel=1e-6)


def test_ts_decay_exp():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    d = ts_decay_exp(s, alpha=0.5)
    # 应接近近期值
    assert d.iloc[-1] > 3.0


def test_ts_corr_basic():
    x = pd.Series(np.arange(100, dtype=float))
    y = pd.Series(np.arange(100, dtype=float) * 2)
    c = ts_corr(x, y, window=20)
    # 完美线性相关 → 1.0
    assert c.iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_ts_corr_negative():
    x = pd.Series(np.arange(100, dtype=float))
    y = pd.Series(-np.arange(100, dtype=float))
    c = ts_corr(x, y, window=20)
    assert c.iloc[-1] == pytest.approx(-1.0, abs=1e-6)


def test_ts_product():
    s = pd.Series([2.0, 3.0, 4.0])
    p = ts_product(s, window=3)
    assert p.iloc[-1] == 24.0


# ============================================================
# B. 横截面（rank / scale / zscore）
# ============================================================

def test_rank_series_fallback():
    """Series 输入 → 全样本百分位（average rank，pct ∈ (0, 1]）"""
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    r = rank(s)
    assert r.iloc[-1] == pytest.approx(1.0)  # 最大值
    assert r.iloc[0] < r.iloc[-1]  # 最小值的 pct 低于最大值


def test_rank_dataframe_cross_section():
    """DataFrame 输入 → 跨列（跨股票）rank"""
    df = pd.DataFrame({
        "A": [1.0, 2.0, 3.0],
        "B": [3.0, 2.0, 1.0],
        "C": [2.0, 2.0, 2.0],
    })
    r = rank(df)
    # 第一行：A=1, B=3, C=2 → ranks [1/3, 3/3, 2/3]
    assert r.iloc[0]["A"] < r.iloc[0]["B"]
    assert r.iloc[0]["C"] == pytest.approx(2 / 3, abs=0.1)


def test_scale_sum_to_one():
    s = pd.Series([1.0, 2.0, 3.0, -4.0])
    sc = scale(s)
    # |1|+|2|+|3|+|-4| = 10
    assert sc.abs().sum() == pytest.approx(1.0, abs=1e-6)


def test_winsorize_clips_extremes():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
    w = winsorize(s, lower=0.01, upper=0.99)
    # 100 应被截断
    assert w.iloc[-1] < 100


# ============================================================
# C. 数学算子
# ============================================================

def test_div_safe_division():
    """除零返回 NaN，不抛异常"""
    a = pd.Series([1.0, 2.0, 3.0])
    b = pd.Series([0.0, 2.0, 3.0])
    r = div(a, b)
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == 1.0


def test_div_scalar():
    assert div(10, 2) == 5.0
    assert np.isnan(div(10, 0))


def test_add_series_scalar():
    s = pd.Series([1.0, 2.0])
    assert (add(s, 10) == pd.Series([11.0, 12.0])).all()
    assert (add(10, s) == pd.Series([11.0, 12.0])).all()


def test_sign():
    s = pd.Series([-3.0, 0.0, 5.0])
    assert (sign(s) == pd.Series([-1.0, 0.0, 1.0])).all()


def test_abs_positive():
    s = pd.Series([-1.0, 2.0, -3.0])
    assert (abs_(s) == pd.Series([1.0, 2.0, 3.0])).all()


def test_max_min_series():
    a = pd.Series([1.0, 5.0, 3.0])
    b = pd.Series([2.0, 2.0, 4.0])
    assert (max_(a, b) == pd.Series([2.0, 5.0, 4.0])).all()
    assert (min_(a, b) == pd.Series([1.0, 2.0, 3.0])).all()


def test_gt_lt_eq_returns_float():
    a = pd.Series([1.0, 2.0, 3.0])
    b = pd.Series([2.0, 2.0, 1.0])
    assert (gt(a, b) == pd.Series([0.0, 0.0, 1.0])).all()
    assert (lt(a, b) == pd.Series([1.0, 0.0, 0.0])).all()
    assert (eq(a, b) == pd.Series([0.0, 1.0, 0.0])).all()


# ============================================================
# D. 技术指标
# ============================================================

def test_returns_simple():
    close = pd.Series([100.0, 110.0, 121.0])
    r = returns(close)
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx(0.10)


def test_vwap_in_range(ohlcv):
    """滚动 VWAP 应在窗口内 low.min 和 high.max 之间（不是当日 [low, high]）"""
    vw = vwap(ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"], window=20)
    valid = vw.dropna()
    # 在过去 20 日最低/最高之间（用滚动 min/max 验证）
    rolling_low = ohlcv["low"].rolling(window=20, min_periods=1).min()
    rolling_high = ohlcv["high"].rolling(window=20, min_periods=1).max()
    assert (valid >= rolling_low.loc[valid.index] - 1e-6).all()
    assert (valid <= rolling_high.loc[valid.index] + 1e-6).all()


def test_rsi_bounds(ohlcv):
    r = rsi(ohlcv["close"], period=14)
    assert ((r.dropna() >= 0) & (r.dropna() <= 100)).all()


def test_macd_returns_three(ohlcv):
    macd_line, signal_line, hist = macd(ohlcv["close"])
    assert len(macd_line) == len(ohlcv)
    assert len(signal_line) == len(ohlcv)
    assert len(hist) == len(ohlcv)


def test_bollinger_middle_is_ma(ohlcv):
    upper, middle, lower = bollinger(ohlcv["close"], window=20, num_std=2.0)
    assert (middle == ts_mean(ohlcv["close"], 20)).all() or middle.dropna().equals(ts_mean(ohlcv["close"], 20).dropna())


def test_atr_positive(ohlcv):
    a = atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], window=14)
    assert (a.dropna() > 0).all()


# ============================================================
# E. 工具函数
# ============================================================

def test_normalize_ohclv_keys(ohlcv):
    d = normalize_ohclv(ohlcv)
    for k in ["open", "high", "low", "close", "volume", "returns", "log_returns", "vwap"]:
        assert k in d


def test_normalize_ohclv_missing_col():
    df = pd.DataFrame({"close": [1, 2, 3]})
    with pytest.raises(ValueError, match="OHLCV 缺列"):
        normalize_ohclv(df)


def test_factor_ic_strong_signal():
    """强预测力：因子与同期"前瞻收益"高度相关 → IC 接近 1"""
    rng = np.random.default_rng(42)
    factor = pd.Series(rng.normal(0, 1, 200))
    # 前瞻收益 = 因子 + 噪声（对齐到同一时间索引，模拟"因子值已知→下一期收益"）
    fwd = factor * 1.0 + rng.normal(0, 0.1, 200)
    ic = factor_ic(factor, fwd)
    assert ic > 0.8  # 强相关


def test_factor_ic_no_signal():
    """无预测力：IC ≈ 0"""
    rng = np.random.default_rng(42)
    factor = pd.Series(rng.normal(0, 1, 200))
    fwd = pd.Series(rng.normal(0, 1, 200)).shift(-1)
    ic = factor_ic(factor, fwd)
    assert abs(ic) < 0.2


def test_factor_ic_insufficient_data():
    """样本太少 → NaN"""
    factor = pd.Series([1.0, 2.0])
    fwd = pd.Series([0.1, 0.2])
    assert np.isnan(factor_ic(factor, fwd))


def test_factor_ir_returns_float():
    rng = np.random.default_rng(42)
    factor = pd.Series(rng.normal(0, 1, 300))
    fwd = (factor + rng.normal(0, 0.5, 300)).shift(-1)
    ir = factor_ir(factor, fwd, window=60)
    assert isinstance(ir, float)
