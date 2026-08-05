"""Dow Phases Signal 测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.dow_phases import DowPhasesSignal


def _make_df(prices: np.ndarray, base_vol: float = 5e6, vol_pattern: str = "normal") -> pd.DataFrame:
    """根据价格序列造 df

    vol_pattern:
        'normal': 均量
        'up_volume': 上涨日放量、下跌日缩量（吸筹/公众参与）
        'down_volume': 下跌日放量（派发）
    """
    n = len(prices)
    daily_ret = np.diff(prices) / prices[:-1]
    vol = np.full(n, base_vol)
    if vol_pattern == "up_volume":
        for i in range(1, n):
            if daily_ret[i - 1] > 0:
                vol[i] *= 1.5
            else:
                vol[i] *= 0.7
    elif vol_pattern == "down_volume":
        for i in range(1, n):
            if daily_ret[i - 1] < 0:
                vol[i] *= 1.5
            else:
                vol[i] *= 0.7
    # 加噪声
    rng = np.random.default_rng(42)
    vol = vol + rng.integers(0, 5e5, size=n)

    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": vol,
    }, index=dates)


def test_dow_phases_accumulation_low_volatility_breakout():
    """吸筹特征：低位 + 波动率收缩 + 上涨放量 + 均线走平"""
    sig = DowPhasesSignal()
    # 构造：低位盘整 + 末段突破
    n = 250
    rng = np.random.default_rng(42)
    prices = np.ones(n) * 50.0
    # 200 天低位盘整（小波动）
    prices[:200] = 50 + rng.normal(0, 0.5, size=200)
    # 末 50 天向上突破
    prices[200:] = np.linspace(50, 60, 50)
    df = _make_df(prices, vol_pattern="up_volume")
    r = sig.evaluate("TEST", df)
    # 至少不是派发
    assert r.details["phase"] != "distribution"
    # 子分波动率应是 +1（低位+收缩）或 0
    assert r.details["subscores"]["volatility"] >= 0


def test_dow_phases_distribution_high_volatility():
    """派发特征：高位 + 波动率放大 + 下跌放量"""
    sig = DowPhasesSignal()
    n = 250
    prices = np.ones(n) * 100.0
    # 前 200 天上涨到 100（高位）
    prices[:200] = np.linspace(50, 100, 200)
    # 末 50 天高位震荡+下跌（派发）
    rng = np.random.default_rng(42)
    prices[200:] = 100 + rng.normal(0, 4, size=50)  # 大幅震荡
    prices[230:] = np.linspace(100, 88, 20)  # 末段下跌
    df = _make_df(prices, vol_pattern="down_volume")
    r = sig.evaluate("TEST", df)
    # 派发或公众参与弱势（不能是吸筹）
    assert r.details["phase"] != "accumulation"


def test_dow_phases_uptrend_public_participation():
    """典型上升趋势：HH/HL + 上涨放量 + 均线多头排列 → 公众参与或吸筹"""
    sig = DowPhasesSignal()
    n = 250
    prices = np.linspace(50, 100, n)  # 单边上涨
    # 加少量回调
    prices = prices + np.sin(np.linspace(0, 20, n)) * 2
    df = _make_df(prices, vol_pattern="up_volume")
    r = sig.evaluate("TEST", df)
    # 应该不是派发
    assert r.details["phase"] != "distribution"
    # 均线排列应是多头
    assert r.details["subscores"]["ma_alignment"] >= 1


def test_dow_phases_data_insufficient():
    """数据不足 → value=0"""
    sig = DowPhasesSignal()
    n = 30
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "open": np.full(n, 100), "high": np.full(n, 101), "low": np.full(n, 99),
        "close": np.full(n, 100), "volume": np.full(n, 5e6),
    }, index=pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B"))
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert "数据不足" in r.reasons[0]


def test_dow_phases_value_range():
    """value 应在 [-1, +1]"""
    sig = DowPhasesSignal()
    rng = np.random.default_rng(42)
    n = 250
    for seed in range(5):
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=n)))
        df = _make_df(prices)
        r = sig.evaluate("TEST", df)
        assert -1.0 <= r.value <= 1.0


def test_dow_phases_score_breakdown():
    """详情包含所有 5 个子分"""
    sig = DowPhasesSignal()
    n = 250
    prices = np.linspace(50, 100, n)
    df = _make_df(prices, vol_pattern="up_volume")
    r = sig.evaluate("TEST", df)
    subs = r.details["subscores"]
    for k in ["price_structure", "volume", "ma_alignment", "volatility", "gap"]:
        assert k in subs
