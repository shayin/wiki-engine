"""杯柄形态测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.cup_handle import CupHandleSignal


def _make_dates(n: int = 200) -> pd.DatetimeIndex:
    return pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")


def _make_cup_handle_df(seed: int = 42) -> pd.DataFrame:
    """构造一个标准杯柄形态
    - 前 80 日：上涨 100%（左沿 100 → 200）
    - 80-130 日：U 型回调至 160（杯底，深度 20%）
    - 130-160 日：反弹回 200（右沿）
    - 160-175 日：柄回调至 188（深度 6%）
    - 175-180 日：放量突破至 210
    """
    rng = np.random.default_rng(seed)
    dates = _make_dates(180)
    prices = np.zeros(180)
    volumes = rng.integers(800_000, 1_200_000, size=180)

    # 阶段 1：上涨（0-80，100 → 200）
    for i in range(80):
        prices[i] = 100 * (1.009 ** i)
    # 阶段 2：杯底（80-130，缓慢 U 型至 160）
    for i in range(80, 130):
        progress = (i - 80) / 50
        # U 型曲线：余弦
        cup_drop = 1 - 0.20 * (1 - np.cos(progress * np.pi)) / 2
        prices[i] = 200 * cup_drop
    # 阶段 3：反弹回 200（130-160）
    for i in range(130, 160):
        progress = (i - 130) / 30
        prices[i] = 160 + 40 * progress
    # 阶段 4：柄回调（160-175，200 → 188）
    for i in range(160, 175):
        progress = (i - 160) / 15
        prices[i] = 200 - 12 * progress
        volumes[i] = 600_000  # 缩量
    # 阶段 5：突破（175-180）
    for i in range(175, 180):
        prices[i] = 188 + 5 * (i - 174)  # 突破至 213
        volumes[i] = 2_500_000  # 放量

    # 加少量噪声
    noise = rng.normal(0, 0.005, 180)
    prices = prices * (1 + noise)

    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": volumes,
    }, index=dates)


def test_standard_cup_handle_passes():
    """标准杯柄形态应通过"""
    df = _make_cup_handle_df()
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    assert r.passed, f"应通过但未通过: {r.reasons}"
    assert r.value >= 0.70
    # 5 个维度都应有分
    scores = r.details["scores"]
    assert scores["cup_shape"] >= 0.6
    assert scores["handle_position"] >= 0.7


def test_no_prior_runup_fails():
    """前期无显著上涨 = 不是杯柄"""
    rng = np.random.default_rng(1)
    n = 180
    prices = 100 + rng.normal(0, 1, n).cumsum()  # 横盘
    dates = _make_dates(n)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": rng.integers(1e6, 2e6, n),
    }, index=dates)
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    assert not r.passed
    assert r.details["prior_runup_pct"] < 30


def test_too_deep_cup_fails():
    """杯深 > 35% = 失败"""
    df = _make_cup_handle_df()
    close = df["close"].copy()
    # 把杯底部分压深 50%
    close.iloc[80:130] = close.iloc[80:130] * 0.5
    df["close"] = close
    df["low"] = close * 0.99
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    # 形态被破坏
    assert r.details.get("cup_depth_pct", 0) > 30 or not r.passed


def test_handle_in_lower_half_fails():
    """柄位于杯下半部 = 不合格"""
    df = _make_cup_handle_df()
    # 把柄部分压低
    df.loc[df.index[160:175], "close"] = 150  # 杯下半部
    df.loc[df.index[160:175], "low"] = 148
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    assert r.details["scores"]["handle_position"] < 0.5


def test_handle_too_deep_fails():
    """柄深度 > 15% = 不合格"""
    df = _make_cup_handle_df()
    # 把柄部分压深
    df.loc[df.index[160:175], "close"] = 165  # 跌 17%
    df.loc[df.index[160:175], "low"] = 163
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    assert r.details["scores"]["handle_depth"] < 0.5


def test_insufficient_data_returns_zero():
    """数据不足返回 0"""
    rng = np.random.default_rng(2)
    n = 30
    dates = _make_dates(n)
    df = pd.DataFrame({
        "open": 100, "high": 101, "low": 99, "close": 100,
        "volume": rng.integers(1e6, 2e6, n),
    }, index=dates)
    sig = CupHandleSignal()
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert not r.passed
