"""New High + Supply Signal 测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from quant_scanner.signals.new_high_supply import NewHighSupplySignal


def _make_df(n: int = 300, end_at_high: bool = False, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    raw = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, size=n)))
    if end_at_high:
        raw[-1] = raw[-252:].max()
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": raw, "high": raw * 1.005, "low": raw * 0.995,
        "close": raw, "volume": rng.integers(1e6, 1e7, size=n),
    }, index=dates)


def _mock_loader(shares=100e6, form4_count=0):
    loader = MagicMock()
    loader.load_fundamentals.return_value = {
        "shares_outstanding": shares,
        "buyback": 0,
    }
    loader.load_insider_form4_count.return_value = form4_count
    return loader


def test_n_letter_new_high_5d():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df(end_at_high=True)
    r = sig.evaluate("TEST", df)
    assert r.details["n"]["new_high_5d"] is True
    assert r.details["n_score"] == 1.0


def test_n_letter_near_high():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    close = df["close"].values.copy()
    high_252 = close[-252:].max()
    close[-1] = high_252 * 0.97
    df["close"] = close
    r = sig.evaluate("TEST", df)
    assert abs(r.details["n"]["dist_from_high_pct"] + 3.0) < 0.5
    assert r.details["n_score"] == 0.7


def test_n_letter_far_from_high():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    close = df["close"].values.copy()
    high_252 = close[-252:].max()
    close[-1] = high_252 * 0.75
    df["close"] = close
    r = sig.evaluate("TEST", df)
    assert r.details["n_score"] == 0.0


def test_s_letter_small_cap():
    loader = _mock_loader(shares=150e6)
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    r = sig.evaluate("TEST", df)
    assert r.details["s"]["base_s_score"] == 1.0


def test_s_letter_large_cap():
    loader = _mock_loader(shares=1500e6)
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    r = sig.evaluate("TEST", df)
    assert r.details["s"]["base_s_score"] == 0.3


def test_s_letter_insider_cluster_bonus():
    loader = _mock_loader(shares=150e6, form4_count=5)
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    r = sig.evaluate("TEST", df)
    assert r.details["s"]["insider_cluster"] is True
    # base 1.0 + insider 0.2 → clamp 1.0
    assert r.details["s_score"] == 1.0


def test_s_letter_insider_below_cluster_threshold():
    loader = _mock_loader(shares=150e6, form4_count=2)
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    r = sig.evaluate("TEST", df)
    assert r.details["s"]["insider_cluster"] is False


def test_catalyst_bonus_injection():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader, catalyst_bonus=0.2)
    df = _make_df()
    close = df["close"].values.copy()
    high_252 = close[-252:].max()
    close[-1] = high_252 * 0.97
    df["close"] = close
    r = sig.evaluate("TEST", df)
    assert abs(r.details["n_score"] - 0.9) < 1e-6


def test_pit_mode_skips_s_letter():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df()
    pit_date = df.index[-50]
    r = sig.evaluate("TEST", df, pit_date=pit_date)
    assert r.details["s_score"] == 0.5
    assert "PIT" in r.details["s"]["skipped"]
    loader.load_fundamentals.assert_not_called()


def test_data_insufficient():
    loader = _mock_loader()
    sig = NewHighSupplySignal(loader=loader)
    df = _make_df(n=30)
    r = sig.evaluate("TEST", df)
    assert r.value == 0.0
    assert "数据不足" in r.reasons[0]
