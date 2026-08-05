"""扫描引擎集成测试（不依赖网络）"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.scanner.engine import Scanner
from quant_scanner.signals.base import BaseSignal, SignalResult
from quant_scanner.signals.trend_template import TrendTemplateSignal


class AlwaysPassSignal(BaseSignal):
    name = "always_pass"

    def evaluate(self, ticker, df):
        return SignalResult(ticker=ticker, signal_name=self.name, value=0.9, passed=True)


class AlwaysFailSignal(BaseSignal):
    name = "always_fail"

    def evaluate(self, ticker, df):
        return SignalResult(ticker=ticker, signal_name=self.name, value=0.1, passed=False)


def _synthetic_df(n=300):
    rng = np.random.default_rng(0)
    returns = rng.normal(0.002, 0.015, size=n)
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B"))


def test_scan_combines_multiple_signals(monkeypatch):
    # mock DataLoader.load
    from quant_scanner.data import loader as loader_mod
    def fake_load(self, ticker, **kwargs):
        return _synthetic_df()
    monkeypatch.setattr(loader_mod.DataLoader, "load", fake_load)

    scanner = Scanner(signals=[AlwaysPassSignal(), AlwaysFailSignal()])
    report = scanner.scan(["A", "B", "C"], show_progress=False)

    assert len(report.results) == 3
    # 一个 pass 一个 fail 的组合应该不通过
    for r in report.results:
        assert not r["passed"]
        assert 0.4 < r["composite_score"] < 0.6  # (0.9 + 0.1) / 2


def test_top_n_sorting(monkeypatch):
    from quant_scanner.data import loader as loader_mod
    def fake_load(self, ticker, **kwargs):
        return _synthetic_df()
    monkeypatch.setattr(loader_mod.DataLoader, "load", fake_load)

    scanner = Scanner(signals=[AlwaysPassSignal()])
    report = scanner.scan(["A", "B", "C"], show_progress=False)
    top = report.top_n(2)
    assert len(top) == 2


def test_scan_parallel_matches_serial(monkeypatch):
    """并发扫描结果与串行一致（同 ticker 顺序）"""
    from quant_scanner.data import loader as loader_mod
    def fake_load(self, ticker, **kwargs):
        return _synthetic_df()
    monkeypatch.setattr(loader_mod.DataLoader, "load", fake_load)

    tickers = ["P1", "P2", "P3", "P4", "P5"]
    scanner = Scanner(signals=[AlwaysPassSignal(), AlwaysFailSignal()])
    serial = scanner.scan(tickers, show_progress=False, max_workers=1)
    parallel = scanner.scan(tickers, show_progress=False, max_workers=4)

    assert [r["ticker"] for r in serial.results] == [r["ticker"] for r in parallel.results]
    for r_s, r_p in zip(serial.results, parallel.results):
        assert abs(r_s["composite_score"] - r_p["composite_score"]) < 1e-9


def test_load_batch_parallel(monkeypatch, tmp_path):
    """DataLoader.load_batch 并发拉取不丢数据"""
    from quant_scanner.data import loader as loader_mod
    from quant_scanner.utils import config as config_mod

    monkeypatch.setattr(config_mod, "get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(loader_mod, "get_cache_dir", lambda: tmp_path)

    call_count = {"n": 0}

    def fake_fetch(self, ticker, **kwargs):
        call_count["n"] += 1
        return _synthetic_df()

    monkeypatch.setattr(loader_mod.DataLoader, "_fetch_from_yfinance", fake_fetch)

    loader = loader_mod.DataLoader(cache_days=999)
    result = loader.load_batch(["X1", "X2", "X3", "X4"], max_workers=4)
    assert len(result) == 4
    assert call_count["n"] == 4
