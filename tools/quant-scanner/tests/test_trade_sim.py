"""trade_sim 测试：hold / stop_target 退出规则 + open gap 处理 + summarize 聚合。"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_scanner.stats.events import SignalEvent
from quant_scanner.stats.trade_sim import (
    TradeResult,
    simulate_trade,
    simulate_batch,
    summarize,
)


def _df(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    """合成 OHLCV：high=close+1, low=close-1（TR=2 恒定，ATR=2 可控）。"""
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = [base] * n
    return pd.DataFrame(
        {"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close],
         "close": close, "volume": [1e6] * n},
        index=dates,
    )


def _event(df: pd.DataFrame, entry_idx: int = 15) -> SignalEvent:
    return SignalEvent(
        ticker="T", signal_name="trend_template", event_type="TREND_TEMPLATE",
        direction="long", entry_date=df.index[entry_idx],
    )


# ATR=2（前 15 天 TR=2），entry open[15]=100 → stop=96, target=108（2:1）


def test_hold_exit_returns_close_at_max_hold():
    df = _df(30)
    df.loc[df.index[20], "close"] = 110  # day20 涨到 110
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="hold", max_hold=5, slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "time"
    assert r.exit_date == df.index[20]
    assert abs(r.return_pct - 0.10) < 1e-6  # 110/100 - 1
    assert r.hold_days == 5


def test_stop_target_target_hit():
    df = _df(30)
    df.loc[df.index[16], "high"] = 110  # >= target 108
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="stop_target", max_hold=10,
                       atr_mult=2.0, target_R=2.0, slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "target"
    assert r.exit_date == df.index[16]
    assert abs(r.exit_price - 108) < 1e-6
    assert abs(r.return_pct - 0.08) < 1e-6  # 108/100 - 1
    assert r.target_price == 108 and r.stop_price == 96


def test_stop_target_stop_hit():
    df = _df(30)
    df.loc[df.index[16], "low"] = 94  # <= stop 96
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="stop_target", slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "stop"
    assert abs(r.exit_price - 96) < 1e-6
    assert abs(r.return_pct - (-0.04)) < 1e-6


def test_stop_target_open_gap_down():
    """开盘跳空低于 stop → 按 open 价成交（stop_gap）。"""
    df = _df(30)
    df.loc[df.index[16], "open"] = 94  # gap down，open <= stop 96
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="stop_target", slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "stop_gap"
    assert abs(r.exit_price - 94) < 1e-6  # 按 open，不是 stop


def test_stop_target_open_gap_up():
    """开盘跳空高于 target → 按 open 成交（target_gap）。"""
    df = _df(30)
    df.loc[df.index[16], "open"] = 112  # open >= target 108
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="stop_target", slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "target_gap"
    assert abs(r.exit_price - 112) < 1e-6


def test_stop_target_time_when_neither_hit():
    """全程未触及 stop/target → max_hold 超时 time exit。"""
    df = _df(30)  # high=101<108, low=99>96
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="stop_target", max_hold=10,
                       slippage_pct=0, commission_pct=0)
    assert r.exit_reason == "time"
    assert r.hold_days == 10
    assert r.exit_date == df.index[25]


def test_hold_censored_at_data_end():
    """持有超出数据末尾 → censored。"""
    df = _df(20)  # entry=15, max_hold=10 → exit_loc=25 超出
    ev = _event(df, 15)
    r = simulate_trade(ev, df, exit_rule="hold", max_hold=10)
    assert r.exit_reason == "censored"
    assert r.return_pct is None


def test_no_data_when_entry_date_missing():
    df = _df(30)
    ev = SignalEvent(ticker="T", entry_date=pd.Timestamp("2099-01-01"))  # 未来日期不在 df
    r = simulate_trade(ev, df, exit_rule="hold")
    assert r.exit_reason == "no_data"


def test_summarize_aggregates():
    trades = [
        TradeResult("e1", "T", None, 100, None, 108, 5, 0.08, "target", 2, 96, 108),
        TradeResult("e2", "T", None, 100, None, 96, 3, -0.04, "stop", 2, 96, 108),
        TradeResult("e3", "T", None, 100, None, 110, 10, 0.10, "time", None, None, None),
        TradeResult("e4", "T", None, None, None, None, 0, None, "no_data", None, None, None),
    ]
    s = summarize(trades, "test")
    assert s["n_completed"] == 3
    assert s["n_no_data"] == 1
    assert s["win_rate"] == pytest.approx(2 / 3)  # 2 wins / 3 completed
    assert s["exit_reasons"]["target"] == 1
    assert s["exit_reasons"]["stop"] == 1
    assert s["exit_reasons"]["time"] == 1


def test_simulate_batch_skips_missing_ticker():
    df = _df(30)
    ev = _event(df, 15)
    # price_lookup 缺该 ticker
    results = simulate_batch([ev], {}, exit_rule="hold")
    assert len(results) == 1
    assert results[0].exit_reason == "no_data"
