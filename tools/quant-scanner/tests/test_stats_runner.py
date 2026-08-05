"""SignalEventRunner 测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from quant_scanner.signals.base import BaseSignal, SignalResult
from quant_scanner.stats.runner import SignalEventRunner


class StubPatternSignal(BaseSignal):
    """固定 pattern_type 触发，便于控制采样。"""

    name = "major_reversal"  # 走 major_reversal extractor

    def __init__(self, trigger_dates: set, pattern_type="DOUBLE_BOTTOM", value=0.8):
        super().__init__()
        self.trigger_dates = trigger_dates
        self.pattern_type_value = pattern_type
        self.value_to_emit = value

    def evaluate(self, ticker, df, pit_date=None):
        if pit_date in self.trigger_dates:
            return SignalResult(
                ticker=ticker, signal_name=self.name,
                value=self.value_to_emit, passed=True,
                details={"pattern_type": self.pattern_type_value, "confirmed": True},
            )
        return SignalResult(
            ticker=ticker, signal_name=self.name,
            value=0.0, passed=False, details={},
        )


def _make_loader(df_dict: dict[str, pd.DataFrame]):
    loader = MagicMock()
    def _load(ticker, **kwargs):
        return df_dict.get(ticker)
    loader.load.side_effect = _load
    return loader


def _make_df(ticker="A", n=80):
    rng = np.random.default_rng(hash(ticker) % 2**31)
    prices = 100 + np.cumsum(rng.normal(0, 0.3, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=idx)


def test_runner_collects_events_on_trigger_dates():
    df = _make_df()
    loader = _make_loader({"A": df})
    trigger = {df.index[10], df.index[30], df.index[50]}
    sig = StubPatternSignal(trigger)
    runner = SignalEventRunner([sig], ["A"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31")
    # 3 个触发日都生成事件
    assert len(events) == 3
    for e in events:
        assert e.event_type == "DOUBLE_BOTTOM"
        assert e.direction == "long"


def test_runner_cooldown_dedupes_within_window():
    df = _make_df()
    loader = _make_loader({"A": df})
    # 连续 5 天触发同一 pattern
    trigger = set(df.index[10:15])
    sig = StubPatternSignal(trigger)
    runner = SignalEventRunner([sig], ["A"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31", cooldown_days=20)
    # 第 0 天触发，后面 4 天都在冷却内
    assert len(events) == 1


def test_runner_handles_missing_data():
    loader = _make_loader({})  # 空
    sig = StubPatternSignal(set())
    runner = SignalEventRunner([sig], ["A"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31")
    assert events == []


def test_runner_handles_signal_exception():
    df = _make_df()
    loader = _make_loader({"A": df})

    class BoomSignal(BaseSignal):
        name = "boom"
        def evaluate(self, ticker, df, pit_date=None):
            raise RuntimeError("boom")

    boom = BoomSignal()
    # 加一个 normal signal 验证不影响其他
    normal = StubPatternSignal({df.index[10]})
    runner = SignalEventRunner([boom, normal], ["A"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31")
    # boom 抛异常被吞，normal 正常生成 1 个事件
    assert len(events) == 1
    assert events[0].signal_name == "major_reversal"


def test_runner_empty_signals_raises():
    with pytest.raises(ValueError):
        SignalEventRunner([], ["A"])


def test_runner_entry_date_is_next_trading_day():
    df = _make_df()
    loader = _make_loader({"A": df})
    trigger = {df.index[10]}
    sig = StubPatternSignal(trigger)
    runner = SignalEventRunner([sig], ["A"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31")
    assert len(events) == 1
    assert events[0].confirmed_date == df.index[10]
    assert events[0].entry_date == df.index[11]


def test_runner_multi_ticker_isolated():
    df_a = _make_df("A")
    df_b = _make_df("B")
    loader = _make_loader({"A": df_a, "B": df_b})
    trigger_a = {df_a.index[10]}
    trigger_b = {df_b.index[20]}
    # 同一个 signal 实例服务两个 ticker，按 ticker 区分触发
    class MultiTickerSignal(BaseSignal):
        name = "multi"
        def __init__(self, triggers):
            super().__init__()
            self.triggers = triggers  # {ticker: set(dates)}
        def evaluate(self, ticker, df, pit_date=None):
            if pit_date in self.triggers.get(ticker, set()):
                return SignalResult(
                    ticker=ticker, signal_name=self.name,
                    value=0.7, passed=True,
                    details={"pattern_type": "TEST"},
                )
            return SignalResult(ticker=ticker, signal_name=self.name, value=0.0, passed=False)

    sig = MultiTickerSignal({"A": trigger_a, "B": trigger_b})
    runner = SignalEventRunner([sig], ["A", "B"], loader=loader)
    events = runner.run(start="2024-01-01", end="2024-12-31")
    assert len(events) == 2
    tickers = {e.ticker for e in events}
    assert tickers == {"A", "B"}


# ----- review High 4：market_direction 市场指数数据路径 -----

def test_runner_market_direction_uses_market_data_when_available():
    """market_direction 有 ^GSPC 数据 → 用市场指数评估"""
    df_a = _make_df("A")
    df_market = _make_df("^GSPC")
    # loader 按 ticker 返回不同 df
    loader = _make_loader({"A": df_a, "^GSPC": df_market})

    received_dfs = []

    class MarketDirectionStub(BaseSignal):
        name = "market_direction"
        def evaluate(self, ticker, df, pit_date=None):
            received_dfs.append((ticker, df.shape[0]))
            return SignalResult(
                ticker=ticker, signal_name=self.name,
                value=0.0, passed=False,  # 不触发事件，只看 ticker/df
            )

    sig = MarketDirectionStub()
    runner = SignalEventRunner([sig], ["A"], loader=loader, market_ticker="^GSPC")
    runner.run(start="2024-01-01", end="2024-12-31")
    # 至少调用过一次，且 ticker 应该是 ^GSPC（市场指数）
    assert len(received_dfs) > 0
    tickers_seen = {t for t, _ in received_dfs}
    assert "^GSPC" in tickers_seen
    assert "A" not in tickers_seen  # 不应用 A 的个股数据


def test_runner_market_direction_skipped_when_market_data_missing():
    """market_direction 缺 ^GSPC 数据 → 跳过该 signal，绝不回退到个股 df"""
    df_a = _make_df("A")
    # loader 没加载 ^GSPC
    loader = _make_loader({"A": df_a})

    call_count = {"n": 0}

    class MarketDirectionStub(BaseSignal):
        name = "market_direction"
        def evaluate(self, ticker, df, pit_date=None):
            call_count["n"] += 1
            return SignalResult(ticker=ticker, signal_name=self.name, value=0.0, passed=False)

    sig = MarketDirectionStub()
    runner = SignalEventRunner([sig], ["A"], loader=loader, market_ticker="^GSPC")
    runner.run(start="2024-01-01", end="2024-12-31")
    # market_direction 应该完全没被调用
    assert call_count["n"] == 0
