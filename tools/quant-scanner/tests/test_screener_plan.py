"""screen_trend_plan 中线交易计划测试。

基于 trend_template@60d 回测锚点（胜率 60.3% / 盈亏比 1.98）。
验证 entry/stop/target/payoff/expectancy 构造 + 账户风控算股数。
"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_scanner.features import screener as scr
from quant_scanner.features.screener import (
    _atr, screen_trend_plan, TREND_WIN_RATE_60D, HOLD_60D_EV_PCT,
)


def _df(prices: list[float]) -> pd.DataFrame:
    """构造合成 OHLCV：high=close+1, low=close-1（使 TR 在平台期恒定 = 2）。"""
    n = len(prices)
    dates = pd.bdate_range("2024-01-01", periods=n)
    p = pd.Series(prices, index=dates, dtype=float)
    return pd.DataFrame(
        {"open": p.values, "high": p.values + 1, "low": p.values - 1,
         "close": p.values, "volume": [1e6] * n},
        index=dates,
    )


def _mock(monkeypatch, prices):
    """mock screen_trend（固定 1 只命中 close=110）+ DataLoader（返回合成 df）。"""
    df = _df(prices)
    monkeypatch.setattr(
        scr, "screen_trend",
        lambda tickers, period="1y": [{"ticker": "T", "close": 110.0, "trend_score": 1.0, "strategy": "x"}],
    )

    class FakeLoader:
        def load(self, t, period=None, **kw):
            return df

    monkeypatch.setattr(scr, "DataLoader", FakeLoader)
    return df


# =====================================================================
# _atr 纯函数
# =====================================================================


def test_atr_positive_and_correct_length():
    df = _df([100 + i for i in range(30)])
    a = _atr(df, 14)
    assert len(a) == 30
    assert a.iloc[-1] > 0  # 价格上涨，TR>0


def test_atr_flat_series_equals_high_low_spread():
    """平台期 TR = high - low = 2（因 high=close+1, low=close-1）。"""
    df = _df([100] * 30)
    a = _atr(df, 14).iloc[-1]
    assert abs(a - 2.0) < 1e-9


# =====================================================================
# screen_trend_plan 交易计划构造
# =====================================================================


def test_trend_plan_payoff_2_to_1_and_positive_expectancy(monkeypatch):
    """平台期 ATR=2 → stop=110-4=106, target=110+8=118, payoff=2.0, 期望正。"""
    _mock(monkeypatch, [100] * 15 + [110] * 20)  # 末段平台，ATR=2
    plans = screen_trend_plan(["T"], atr_mult=2.0, target_R=2.0)
    assert len(plans) == 1
    p = plans[0]
    assert p["ticker"] == "T"
    assert p["stop"] == pytest.approx(106.0, abs=0.01)
    assert p["target"] == pytest.approx(118.0, abs=0.01)
    assert p["payoff"] == 2.0  # 2:1 盈亏比（对齐回测 1.98）
    assert p["stop"] < 110 < p["target"]
    assert p["backtest_ev_hold"] == 4.21  # 实际回测 EV（hold 60d）
    assert p["holding"] == "60 交易日"
    assert p["atr"] == pytest.approx(2.0, abs=0.01)


def test_trend_plan_backtest_ev_is_hold_constant(monkeypatch):
    """backtest_ev_hold 锚定 trade_sim 组合回测的纯持有 60d 实际 EV（4.21%），非理论期望。"""
    _mock(monkeypatch, [100] * 15 + [110] * 20)
    p = screen_trend_plan(["T"], atr_mult=2.0, target_R=2.0)[0]
    assert p["backtest_ev_hold"] == HOLD_60D_EV_PCT == 4.21


def test_trend_plan_account_sizing(monkeypatch):
    """account 给定 → 按 2% 风控算股数 + 持仓市值。"""
    _mock(monkeypatch, [100] * 15 + [110] * 20)
    p = screen_trend_plan(["T"], account=10000, atr_mult=2.0, target_R=2.0)[0]
    assert "shares" in p and p["shares"] > 0
    assert "position_value" in p
    assert p["account_risk"] == 200.0  # 10000 × 2%
    # shares = account_risk / risk_per_share = 200 / (110-106) = 50
    assert p["shares"] == 50
    assert p["position_value"] == pytest.approx(50 * 110, abs=1)


def test_trend_plan_no_account_skips_shares(monkeypatch):
    """account=None → 不输出 shares。"""
    _mock(monkeypatch, [100] * 15 + [110] * 20)
    p = screen_trend_plan(["T"], atr_mult=2.0, target_R=2.0)[0]
    assert "shares" not in p
    assert "position_value" not in p


def test_trend_plan_empty_hits(monkeypatch):
    """screen_trend 无命中 → plan 空。"""
    monkeypatch.setattr(scr, "screen_trend", lambda tickers, period="1y": [])
    assert screen_trend_plan(["T"]) == []
