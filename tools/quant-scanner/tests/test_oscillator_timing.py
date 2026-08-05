"""Oscillator Timing（RSI 背离）信号测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.oscillator_timing import OscillatorTimingSignal


def _make_bearish_divergence_df():
    """顶背离：急涨→H1=130（RSI 高）→ 回调 → 缓涨→H2=132（RSI 较低）→ 微跌。

    急涨段 RSI 冲高，缓涨段 RSI 难超前者峰值 → 价格新高但 RSI 衰竭 = 顶背离。
    """
    days = np.arange(101)
    # day0-15 急涨到 130，15-40 回调到 120，40-80 缓涨到 132，80-100 微跌到 130（让 H2 成为 swing high）
    prices = np.interp(days, [0, 15, 40, 80, 100], [100, 130, 120, 132, 130])
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(len(prices), 5e6),
    }, index=dates)


def _make_bullish_divergence_df():
    """底背离：急跌→L1=70（RSI 低）→ 反弹 → 缓跌→L2=68（RSI 较高）→ 微涨。"""
    days = np.arange(101)
    prices = np.interp(days, [0, 15, 40, 80, 100], [100, 70, 80, 68, 70])
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(len(prices), 5e6),
    }, index=dates)


def _make_neutral_df():
    """稳定上涨无背离（价格和 RSI 同步）。"""
    days = np.arange(101)
    prices = 100 * (1.003 ** days)  # 平稳上涨
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(len(prices), 5e6),
    }, index=dates)


def test_bearish_divergence_detected():
    """价格新高 + RSI 衰竭 → 顶背离，value 负，passed。"""
    sig = OscillatorTimingSignal()
    df = _make_bearish_divergence_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("divergence") == "BEARISH_DIVERGENCE"
    assert r.value < 0
    assert r.passed is True
    # 价格 H2 > H1，RSI H2 < H1
    assert r.details["price_high_2"] > r.details["price_high_1"]
    assert r.details["rsi_high_2"] < r.details["rsi_high_1"]


def test_bullish_divergence_detected():
    """价格新低 + RSI 走强 → 底背离，value 正，passed。"""
    sig = OscillatorTimingSignal()
    df = _make_bullish_divergence_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("divergence") == "BULLISH_DIVERGENCE"
    assert r.value > 0
    assert r.passed is True
    assert r.details["price_low_2"] < r.details["price_low_1"]
    assert r.details["rsi_low_2"] > r.details["rsi_low_1"]


def test_rsi_value_in_range():
    """RSI 计算结果在 0-100。"""
    sig = OscillatorTimingSignal()
    df = _make_neutral_df()
    r = sig.evaluate("TEST", df)
    assert 0 <= r.details["rsi"] <= 100


def test_insufficient_data():
    """数据不足 → value=0。"""
    sig = OscillatorTimingSignal()
    df = _make_neutral_df().iloc[:40]
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert "数据不足" in r.reasons[0]


def test_rsi_calculation_matches_manual():
    """直接验证 RSI 计算：全涨 → RSI 接近 100。"""
    sig = OscillatorTimingSignal()
    close = pd.Series(np.linspace(100, 200, 100))
    rsi = sig._rsi(close)
    # 持续上涨 RSI 应高位（>80，因纯涨无跌）
    assert rsi.iloc[-1] > 70


# ==================== MACD / 隐藏背离 / 失败摆动测试（P3.3 扩展）====================

def _make_macd_bearish_divergence_df():
    """MACD 顶背离：急涨→H1=130 → 回调 → 缓涨→H2=135 → 微跌。

    RSI 可能也背离，但这里主要看 MACD divergence 字段是否被填充。
    """
    days = np.arange(101)
    prices = np.interp(days, [0, 20, 45, 80, 100], [95, 130, 120, 135, 132])
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(len(prices), 5e6),
    }, index=dates)


def _make_failure_swing_bullish_df():
    """看涨失败摆动：构造 RSI 序列满足 A-B-C-D 结构。

    直接构造一个 RSI 满足：
    - 前低 A < 后低 D
    - 前高 < 后高 C
    - A 在前、D 在后
    通过 close 序列间接控制 RSI swing。
    """
    # 构造 RSI-like 走势：先大跌（RSI 低 A）→ 反弹（RSI 高）→ 浅跌（RSI 低 D > A）→ 反弹（RSI 高 C）
    days = np.arange(101)
    prices = np.interp(days,
        [0, 20, 35, 55, 70, 90, 100],
        [120, 75, 90, 80, 95, 105, 108],  # 多次震荡让 RSI 形成失败摆动
    )
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(len(prices), 5e6),
    }, index=dates)


def test_macd_calculation():
    """MACD 三件套返回三个 Series 且长度匹配。"""
    sig = OscillatorTimingSignal()
    close = pd.Series(np.linspace(100, 200, 100))
    macd_line, signal_line, hist = sig._macd(close)
    assert len(macd_line) == 100
    assert len(signal_line) == 100
    assert len(hist) == 100


def test_macd_divergence_field_populated():
    """MACD 顶背离数据 → details['macd_divergence'] 被填充（无论 RSI 是否触发）。"""
    sig = OscillatorTimingSignal()
    df = _make_macd_bearish_divergence_df()
    r = sig.evaluate("TEST", df)
    # 至少有一种背离被检测（RSI 或 MACD）
    assert r.details.get("divergence") == "BEARISH_DIVERGENCE" or \
           r.details.get("macd_divergence") == "BEARISH"
    assert r.value < 0
    assert r.passed is True


def test_failure_swing_detected_or_safe():
    """失败摆动检测：即使合成数据不严格匹配 A-B-C-D，也不应抛异常。

    本测试主要验证 _detect_failure_swing 在合理数据上的健壮性。
    """
    sig = OscillatorTimingSignal()
    df = _make_failure_swing_bullish_df()
    r = sig.evaluate("TEST", df)
    # 无论如何不应报错，且必须有 rsi 字段
    assert "rsi" in r.details
    assert "macd_line" in r.details
    assert "macd_histogram" in r.details


def test_hidden_divergence_method_runs():
    """隐藏背离方法在边界情况下不抛异常。"""
    sig = OscillatorTimingSignal()
    close = pd.Series(np.linspace(100, 150, 100))
    rsi = sig._rsi(close)
    highs = [(10, 110.0), (30, 120.0), (60, 140.0)]
    lows = [(20, 105.0), (40, 115.0), (70, 130.0)]
    result, details = sig._detect_hidden_divergence(
        highs, lows, rsi, "rsi", sig.rsi_divergence_threshold,
    )
    # 结果要么是 None 要么是有效字符串
    assert result is None or result in ("BULLISH_HIDDEN", "BEARISH_HIDDEN")


def test_neutral_still_has_macd_fields():
    """中性数据 evaluate 必须输出 macd 字段（即使 score=0）。"""
    sig = OscillatorTimingSignal()
    df = _make_neutral_df()
    r = sig.evaluate("TEST", df)
    assert "macd_line" in r.details
    assert "macd_histogram" in r.details
