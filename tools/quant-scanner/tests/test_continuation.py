"""Continuation（旗形）信号测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.continuation import ContinuationSignal


def _make_bull_flag_df():
    """bull flag：上涨趋势（100→95）→ 急涨旗杆（95→130）→ 浅回调 → 突破。

    需要前置趋势 60 天 + 涨幅 ≥ 15%（排除 dead cat bounce）。
    关键点：前 60 天明确上涨 100→90（反转 → up 趋势），后 41 天旗杆+回调+突破。
    """
    # 前 60 天趋势上涨（95→100），后段旗杆 100→130 + 回调 + 突破
    days = np.arange(151)
    prices = np.interp(days, [0, 60, 80, 95, 110, 150], [80, 100, 95, 130, 124, 145])
    # 旗杆+回调段 5e6，突破段最后 10 天 15e6（突破日量 ≥ MA50 × 1.5）
    vol = np.concatenate([np.full(110, 5e6), np.full(31, 5e6), np.full(10, 15e6)])
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": vol,
    }, index=dates)


def _make_bear_flag_df():
    """bear flag：下跌趋势 → 急跌旗杆 → 浅反弹 → 跌破。

    前 60 天明确下跌 120→100，后段旗杆+反弹+跌破。
    """
    days = np.arange(151)
    prices = np.interp(days, [0, 60, 80, 95, 110, 150], [120, 100, 105, 70, 75, 60])
    vol = np.concatenate([np.full(110, 5e6), np.full(31, 5e6), np.full(10, 15e6)])
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": vol,
    }, index=dates)


def _make_no_flag_df():
    """横盘无旗杆 → 无形态（且无前置趋势）。"""
    rng = np.random.default_rng(7)
    prices = 100 + np.cumsum(rng.normal(0, 0.3, size=151))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=151, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(151, 5e6),
    }, index=dates)


def test_bull_flag_detected():
    """bull flag + 突破创新高 → BULL_FLAG，value 正，passed。"""
    sig = ContinuationSignal()
    df = _make_bull_flag_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("pattern_type") == "BULL_FLAG"
    assert r.details["confirmed"] is True
    assert r.value > 0
    assert r.passed is True
    # 旗杆涨幅 ≥ 15%
    assert r.details["flagpole_pct"] >= 0.15


def test_bear_flag_detected():
    """bear flag + 跌破创新低 → BEAR_FLAG，value 负，passed。"""
    sig = ContinuationSignal()
    df = _make_bear_flag_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("pattern_type") == "BEAR_FLAG"
    assert r.details["confirmed"] is True
    assert r.value < 0
    assert r.passed is True


def test_no_flag_pattern():
    """横盘无旗杆 → 不识别为 BULL_FLAG/BEAR_FLAG（可能识别为弱矩形/三角但 value 低）。"""
    sig = ContinuationSignal()
    df = _make_no_flag_df()
    r = sig.evaluate("TEST", df)
    # 关键断言：不被识别为旗形（dead cat bounce 防御）
    assert r.details.get("pattern_type") not in ("BULL_FLAG", "BEAR_FLAG")
    assert r.passed is False


def test_insufficient_data():
    """数据不足 → value=0。"""
    sig = ContinuationSignal()
    df = _make_bull_flag_df().iloc[:80]  # < min_prior_trend_days + swing*8
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert "数据不足" in r.reasons[0]


def test_flag_target_measured():
    """bull flag 确认后，目标 = 突破点 + 旗杆长度。"""
    sig = ContinuationSignal()
    df = _make_bull_flag_df()
    r = sig.evaluate("TEST", df)
    # 旗杆 100→130（长度 30），突破点 ≈130，目标 ≈160
    assert r.details.get("target") is not None
    assert r.details["target"] > 150  # 目标应明显高于旗杆顶


# ==================== 三角形/楔形/矩形测试（P3.2 扩展）====================

def _make_ascending_triangle_df():
    """上升三角形：80 天大涨 80→135 → 水平阻力 135 反复 + 支撑 115/122/125 上移 → 突破 145。"""
    days = np.arange(161)
    prices = np.interp(days,
        [0, 80, 95, 110, 125, 140, 155, 160],
        [80, 135, 115, 135, 122, 135, 125, 145],
    )
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.concatenate([np.full(len(prices) - 3, 5e6), np.full(3, 9e6)]),
    }, index=dates)


def _make_symmetric_triangle_df():
    """对称三角形：上涨后高点 130/128/125 下移 + 低点 110/115/120 上移 → 收敛 → 突破。"""
    days = np.arange(161)
    prices = np.interp(days,
        [0, 80, 95, 110, 125, 140, 155, 160],
        [80, 130, 110, 128, 115, 125, 120, 138],
    )
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.concatenate([np.full(len(prices) - 3, 5e6), np.full(3, 9e6)]),
    }, index=dates)


def _make_rectangle_df():
    """矩形整理：上涨后水平 115-125 区间震荡 3 次 → 突破 130。"""
    days = np.arange(161)
    prices = np.interp(days,
        [0, 80, 95, 110, 125, 140, 155, 160],
        [80, 125, 115, 125, 115, 125, 115, 130],
    )
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=len(prices), freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.concatenate([np.full(len(prices) - 3, 5e6), np.full(3, 9e6)]),
    }, index=dates)


def test_ascending_triangle_detected():
    """上升三角形：水平阻力 + 上移支撑 + 突破 → ASCENDING_TRIANGLE，value 正。"""
    sig = ContinuationSignal()
    df = _make_ascending_triangle_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("pattern_type") == "ASCENDING_TRIANGLE"
    assert r.details["confirmed"] is True
    assert r.value > 0
    assert r.details["patterns"][0]["break_dir"] == "up"


def test_symmetric_triangle_detected():
    """对称三角形：上斜率 < 0 + 下斜率 > 0 → SYMMETRIC_TRIANGLE。"""
    sig = ContinuationSignal()
    df = _make_symmetric_triangle_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("pattern_type") == "SYMMETRIC_TRIANGLE"
    # 上斜率 < 0、下斜率 > 0
    assert r.details["patterns"][0]["upper_slope"] < 0
    assert r.details["patterns"][0]["lower_slope"] > 0


def test_rectangle_detected():
    """矩形：上下斜率都接近 0 → RECTANGLE。"""
    sig = ContinuationSignal()
    df = _make_rectangle_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("pattern_type") == "RECTANGLE"
    pat = r.details["patterns"][0]
    assert abs(pat["upper_slope"]) < 0.05
    assert abs(pat["lower_slope"]) < 0.05


def test_triangle_breakout_target_measured():
    """三角形确认后，目标 = 突破点 + 形态高度。"""
    sig = ContinuationSignal()
    df = _make_ascending_triangle_df()
    r = sig.evaluate("TEST", df)
    assert r.details.get("target") is not None
    pat = r.details["patterns"][0]
    # 目标 > 阻力位
    assert pat["target"] > max(pat["upper_line"])
