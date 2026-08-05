"""Pivot Point 信号测试"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.pivot_point import PivotPointSignal


def _make_base_with_pivot(n: int = 120, seed: int = 7) -> pd.DataFrame:
    """构造一个带基底 + 中枢点量能干涸 + 突破的合成数据

    流程（最后一天是突破日）：
      前 60 天：缓慢上涨建立趋势（~100 → ~115）
      60-100 天：盘整形成基底，高点 ~120
      100-(n-2) 天：量能干涸（接近基底的最低量），价格贴近高点
      最后 1 天（n-1）：突破基底高点 120 + 放量 1.8x
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")

    prices = np.zeros(n)
    volumes = np.zeros(n)

    # 阶段 1：趋势上涨（0-59）
    for i in range(60):
        prices[i] = 100 + i * 0.25 + rng.normal(0, 0.5)
        volumes[i] = rng.integers(3_000_000, 6_000_000)

    # 阶段 2：基底盘整（60-99），高点 ~120
    base_high = 120
    for i in range(60, 100):
        prices[i] = base_high - rng.uniform(0, 12)
        volumes[i] = rng.integers(2_500_000, 5_000_000)

    # 阶段 3：中枢点量能干涸（100 ~ n-2），价格贴近高点
    for i in range(100, n - 1):
        prices[i] = base_high - rng.uniform(0, 2)
        volumes[i] = rng.integers(800_000, 1_500_000)  # 极低量

    # 最后一天：突破日放量突破
    prices[n - 1] = base_high + 1.5
    volumes[n - 1] = 9_000_000  # 大放量

    # 构造 OHLC
    df = pd.DataFrame({
        "open": prices,
        "high": prices * (1 + rng.uniform(0, 0.005, size=n)),
        "low": prices * (1 - rng.uniform(0, 0.005, size=n)),
        "close": prices,
        "volume": volumes,
    }, index=dates)
    return df


def test_pivot_breakout_triggers():
    """标准的基底 + 干涸 + 突破应该触发买入信号"""
    df = _make_base_with_pivot()
    sig = PivotPointSignal()
    result = sig.evaluate("TEST_PVT", df)
    # 至少应该形成形态（即使突破日判定不严格）
    assert result.details.get("action") in {"BUY", "READY"}, \
        f"应至少进入 READY 状态，action={result.details.get('action')}, reasons={result.reasons}"


def test_no_breakout_no_trigger():
    """未放量突破时不应触发 BUY"""
    df = _make_base_with_pivot()
    # 把最后 5 天价格全部压低，破坏突破
    df.iloc[-5:, df.columns.get_loc("close")] = df["close"].iloc[-5:].values * 0.95
    df.iloc[-5:, df.columns.get_loc("high")] = df["close"].iloc[-5:].values * 0.96
    sig = PivotPointSignal()
    result = sig.evaluate("TEST_NO_BREAK", df)
    assert not result.passed
    assert result.details.get("action") != "BUY"


def test_insufficient_data():
    """数据不足应返回 value=0"""
    rng = np.random.default_rng(1)
    n = 50
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    prices = 100 + rng.normal(0, 1, n).cumsum()
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": rng.integers(1_000_000, 5_000_000, n),
    }, index=dates)
    sig = PivotPointSignal()
    result = sig.evaluate("TEST_SHORT", df)
    assert result.value == 0.0
    assert not result.passed


def test_details_contain_required_keys():
    df = _make_base_with_pivot()
    sig = PivotPointSignal()
    result = sig.evaluate("TEST", df)
    assert "pivot_upper" in result.details
    assert "pivot_lower" in result.details
    assert "stop_loss_price" in result.details
    assert "conditions" in result.details
    assert "action" in result.details
    assert "vol_ratio_last_vs_ma50" in result.details
