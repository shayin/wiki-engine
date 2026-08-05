"""大盘方向信号测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from quant_scanner.signals.market_direction import MarketDirectionSignal


def _make_market_df(n: int = 500, drift: float = 0.001, vol: float = 0.01, seed: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, size=n)
    prices = 5000 * np.exp(np.cumsum(returns))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(2e9, 4e9, size=n).astype(float),
    }, index=dates)


def _make_loader(market_df):
    loader = MagicMock()
    loader.load.return_value = market_df
    return loader


def test_healthy_uptrend_passes():
    """低波动 + 正漂移 → 健康上涨 ≥ 0.7"""
    market = _make_market_df(n=500, drift=0.002, vol=0.005, seed=7)
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    assert r.value >= 0.7
    assert r.passed
    assert r.details["ma50_vs_ma200"] is True


def test_downtrend_fails():
    """长期下跌 → 弱势/压力"""
    market = _make_market_df(n=500, drift=-0.002, vol=0.01, seed=99)
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    assert r.value <= 0.3
    assert not r.passed
    assert r.details["ma50_vs_ma200"] is False


def test_distribution_days_alarm():
    """构造 25 日内 4+ 个分布日"""
    market = _make_market_df(n=500, drift=0.001, vol=0.005, seed=42)
    close = market["close"].copy()
    # 在最后 25 日注入 5 个分布日（跌 ≥ 0.5%，放量）
    for i in range(-25, -5, 4):
        close.iloc[i] = close.iloc[i - 1] * 0.99
        market.iloc[i, market.columns.get_loc("volume")] = 5e9  # 放量
    market["close"] = close
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    # 多个分布日 → 低分
    assert r.details["distribution_days_count"] >= 3


def test_ftd_detection():
    """构造一个 FTD：第 5 日涨 1.5% + 放量"""
    market = _make_market_df(n=300, drift=0.0, vol=0.005, seed=3)
    close = market["close"].copy()
    vol = market["volume"].copy()
    # 倒数第 3 日：FTD
    close.iloc[-3] = close.iloc[-4] * 1.015
    vol.iloc[-3] = vol.mean() * 1.5
    market["close"] = close
    market["volume"] = vol
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    assert r.details["ftd_recent"] is True


def test_accepts_direct_df():
    """支持直接传 df 不通过 loader"""
    market = _make_market_df(n=300, drift=0.001, vol=0.005)
    sig = MarketDirectionSignal(loader=MagicMock())
    r = sig.evaluate(df=market)
    # loader 不应被调用
    sig._loader.load.assert_not_called()
    assert r.details["state"] != ""


def test_insufficient_data_returns_neutral():
    """数据 < 60 bar 返回中性 0.5"""
    market = _make_market_df(n=30, drift=0.001)
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    assert r.value == 0.5
    assert not r.passed


def test_details_keys():
    """details 包含必要字段"""
    market = _make_market_df(n=300, drift=0.001)
    sig = MarketDirectionSignal(loader=_make_loader(market))
    r = sig.evaluate()
    assert "distribution_days_count" in r.details
    assert "ftd_recent" in r.details
    assert "ma50_vs_ma200" in r.details
    assert "state" in r.details
