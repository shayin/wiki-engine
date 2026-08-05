"""回测框架测试

测试策略：
- 不 mock yfinance（用合成数据）
- 通过 monkeypatch DataLoader.load 返回合成数据
- 使用 AlwaysHighSignal / AlwaysLowSignal / ProgrammableSignal 控制信号值
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.backtest.engine import (
    BacktestReport,
    Backtester,
    TradeRecord,
)
from quant_scanner.data import loader as loader_mod
from quant_scanner.signals.base import BaseSignal, SignalResult


# =====================================================================
# 测试用辅助 Signal
# =====================================================================


class AlwaysHighSignal(BaseSignal):
    """永远返回高分（看多）"""

    name = "always_high"

    def evaluate(self, ticker, df):
        return SignalResult(ticker=ticker, signal_name=self.name, value=0.95, passed=True)


class AlwaysLowSignal(BaseSignal):
    """永远返回低分（看空）"""

    name = "always_low"

    def evaluate(self, ticker, df):
        return SignalResult(ticker=ticker, signal_name=self.name, value=0.05, passed=False)


class ProgrammableSignal(BaseSignal):
    """按 ticker + 当前 df 长度返回预设分数

    schedule: dict[ticker, list[float]] —— 每个 ticker 的分数序列，按日期顺序逐日返回
    """

    name = "program"

    def __init__(self, schedule: dict[str, list[float]]):
        super().__init__()
        self.schedule = schedule

    def evaluate(self, ticker, df):
        seq = self.schedule.get(ticker, [0.5] * len(df))
        n = len(df)
        idx = min(n, len(seq)) - 1
        v = float(seq[max(0, idx)]) if seq else 0.5
        return SignalResult(
            ticker=ticker,
            signal_name=self.name,
            value=v,
            passed=(v >= 0.7),
        )


# =====================================================================
# 合成数据
# =====================================================================


def _make_synthetic(
    n: int = 200,
    start_price: float = 100.0,
    drift: float = 0.001,
    volatility: float = 0.01,
    seed: int = 42,
    start_date: str = "2023-01-02",
) -> pd.DataFrame:
    """生成合成日线数据（向上/向下漂移）"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, volatility, size=n)
    prices = start_price * np.exp(np.cumsum(returns))
    dates = pd.bdate_range(start=start_date, periods=n)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices * (1 + rng.uniform(0, 0.005, size=n)),
            "low": prices * (1 - rng.uniform(0, 0.005, size=n)),
            "close": prices,
            "volume": rng.integers(1_000_000, 10_000_000, size=n),
        },
        index=dates,
    )


def _make_crash_data(
    n_pre: int = 60,
    n_post: int = 60,
    crash_pct: float = 0.5,
    seed: int = 7,
) -> pd.DataFrame:
    """前段平稳，后段暴跌（验证 stop_loss）"""
    rng = np.random.default_rng(seed)
    pre = rng.normal(0.0, 0.01, size=n_pre)
    pre_prices = 100.0 * np.exp(np.cumsum(pre))
    post = rng.normal(-crash_pct / n_post, 0.02, size=n_post)
    post_prices = pre_prices[-1] * np.exp(np.cumsum(post))
    prices = np.concatenate([pre_prices, post_prices])
    dates = pd.bdate_range(start="2023-01-02", periods=n_pre + n_post)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": rng.integers(1_000_000, 10_000_000, size=n_pre + n_post),
        },
        index=dates,
    )


def _patch_loader(monkeypatch, data: dict[str, pd.DataFrame]) -> None:
    """monkeypatch DataLoader.load 返回合成数据"""

    def fake_load(self, ticker, **kwargs):
        return data.get(ticker, pd.DataFrame()).copy()

    monkeypatch.setattr(loader_mod.DataLoader, "load", fake_load)


# =====================================================================
# 测试用例
# =====================================================================


def test_backtest_with_synthetic_data(monkeypatch):
    """基本回测能跑通，返回结构正确的 BacktestReport"""
    data = {
        "AAA": _make_synthetic(n=300, drift=0.003, seed=1),
        "BBB": _make_synthetic(n=300, drift=0.002, seed=2),
    }
    _patch_loader(monkeypatch, data)

    bt = Backtester(
        signals=[AlwaysHighSignal()],
        universe=["AAA", "BBB"],
        entry_threshold=0.7,
        exit_threshold=0.3,
        max_positions=2,
    )
    report = bt.run(start="2023-01-02", end="2024-04-01", rebalance_freq="W")

    assert isinstance(report, BacktestReport)
    assert report.start == "2023-01-02"
    assert report.end == "2024-04-01"
    # AAA + BBB 都应该至少有开仓（信号一直是 0.95）
    assert report.n_trades > 0
    # 权益曲线应有多个点
    assert len(report.equity_curve) > 1
    # 起点应归一化为 1.0
    assert abs(report.equity_curve.iloc[0] - 1.0) < 1e-9


def test_trade_record_return_calculation():
    """单笔交易的 return_pct 计算（含 commission + slippage）"""
    commission = 0.001
    slippage = 0.001
    # 假设买入收盘价 100，卖出收盘价 110
    trade = TradeRecord(
        ticker="X",
        entry_date=pd.Timestamp("2024-01-01"),
        entry_price=100.0 * (1 + slippage),  # 含买入滑点
    )
    trade.close(
        exit_date=pd.Timestamp("2024-02-01"),
        raw_exit_price=110.0,
        exit_reason="signal_exit",
        commission_pct=commission,
        slippage_pct=slippage,
    )

    # 买入净额 = 100.1 × (1+0.001) = 100.1 × 1.001 = 100.2001
    # 卖出执行价 = 110 × (1-0.001) = 109.89
    # 卖出净额 = 109.89 × (1-0.001) = 109.78011
    # return = 109.78011 / 100.2001 - 1 ≈ 0.0957087
    expected_buy = 100.1 * 1.001
    expected_sell = 110.0 * (1 - slippage) * (1 - commission)
    expected = expected_sell / expected_buy - 1.0

    assert trade.exit_reason == "signal_exit"
    assert trade.exit_price == pytest.approx(110.0 * (1 - slippage))
    assert trade.return_pct == pytest.approx(expected, rel=1e-6)
    # 收益应为正（约 9.57%）
    assert 0.08 < trade.return_pct < 0.11


def test_stop_loss_triggers(monkeypatch):
    """合成暴跌场景：信号一直看多让买入，止损应在暴跌后触发"""
    crash_df = _make_crash_data(n_pre=80, n_post=80, crash_pct=0.6)
    data = {"CRASH": crash_df}
    _patch_loader(monkeypatch, data)

    bt = Backtester(
        signals=[AlwaysHighSignal()],
        universe=["CRASH"],
        entry_threshold=0.7,
        exit_threshold=0.1,  # 信号永远 0.95，所以不会因信号退出
        stop_loss_pct=0.08,
        time_limit_days=365,  # 让止损先触发
        max_positions=1,
        commission_pct=0.0,
        slippage_pct=0.0,  # 简化数学
    )
    report = bt.run(start="2023-01-02", end="2024-06-01", rebalance_freq="W")

    # 应至少有一笔交易以 stop_loss 平仓
    stop_loss_trades = [t for t in report.trades if t.exit_reason == "stop_loss"]
    assert len(stop_loss_trades) > 0, "暴跌场景应触发止损"
    # 止损交易的损失应接近 -8%（含或不含 commission）
    for t in stop_loss_trades:
        assert t.return_pct < 0, "止损交易应为负收益"


def test_signal_exit_triggers(monkeypatch):
    """信号反转：前段高分让买入，后段低分触发 exit_threshold"""
    # 构造一只平稳数据（价格不重要），用 ProgrammableSignal 控制
    df = _make_synthetic(n=200, drift=0.0, volatility=0.005, seed=99)
    data = {"FLIP": df}
    _patch_loader(monkeypatch, data)

    # 前 100 个数据点信号 = 0.95，之后 = 0.05
    schedule = {"FLIP": [0.95] * 100 + [0.05] * 100}

    bt = Backtester(
        signals=[ProgrammableSignal(schedule)],
        universe=["FLIP"],
        entry_threshold=0.7,
        exit_threshold=0.3,
        stop_loss_pct=0.5,  # 关闭止损
        time_limit_days=365,  # 关闭超时
        max_positions=1,
    )
    report = bt.run(start="2023-01-02", end="2024-01-01", rebalance_freq="D")

    # 应至少有一笔 signal_exit
    exit_trades = [t for t in report.trades if t.exit_reason == "signal_exit"]
    assert len(exit_trades) > 0, "信号反转应触发 signal_exit"


def test_max_positions_respected(monkeypatch):
    """验证同时持仓不超过 max_positions"""
    # 5 只股票都高分，但 max_positions=2
    data = {f"S{i}": _make_synthetic(n=200, seed=i) for i in range(5)}
    _patch_loader(monkeypatch, data)

    bt = Backtester(
        signals=[AlwaysHighSignal()],
        universe=[f"S{i}" for i in range(5)],
        entry_threshold=0.7,
        exit_threshold=0.1,  # 信号永远高，不主动卖出
        stop_loss_pct=0.5,
        time_limit_days=365,
        max_positions=2,
    )
    report = bt.run(start="2023-01-02", end="2024-01-01", rebalance_freq="W")

    # 验证权益曲线里每个时点的"在持仓数"≤ 2
    # 简化：统计每只股票出现的不同持仓阶段（粗略：每只 ticker 的总交易数）
    # 严格做法是检查每个调仓日。这里检查 trades 中 entry_date 不重复数 ≤ 2 不合适
    # 改用：所有 entry_date 互不相同，并且任意时刻同时开仓数 ≤ 2
    entries = sorted(
        [(t.entry_date, t.exit_date or pd.Timestamp("2099-01-01"), t.ticker) for t in report.trades]
    )
    max_simul = 0
    for i, (e1, x1, _) in enumerate(entries):
        # 计算在 e1 时点同时活跃的开仓数
        cnt = sum(1 for (e2, x2, _) in entries if e2 <= e1 < x2)
        max_simul = max(max_simul, cnt)
    assert max_simul <= 2, f"同时持仓数 {max_simul} 超过上限 2"


def test_report_metrics_calculation():
    """用已知 trades 验证 win_rate / sharpe / max_drawdown"""
    dates = pd.bdate_range("2024-01-02", periods=10)

    # 构造已知收益的交易：3 胜 1 负
    trades = [
        TradeRecord("A", dates[0], 100, exit_date=dates[1], exit_price=110,
                    return_pct=0.10, exit_reason="signal_exit"),
        TradeRecord("B", dates[0], 100, exit_date=dates[1], exit_price=105,
                    return_pct=0.05, exit_reason="signal_exit"),
        TradeRecord("C", dates[0], 100, exit_date=dates[1], exit_price=108,
                    return_pct=0.08, exit_reason="signal_exit"),
        TradeRecord("D", dates[0], 100, exit_date=dates[1], exit_price=95,
                    return_pct=-0.05, exit_reason="stop_loss"),
    ]
    # 构造一条先涨后回的 equity curve
    eq = pd.Series([1.0, 1.10, 1.15, 1.05, 1.08, 1.12, 1.10, 1.09, 1.07, 1.06], index=dates)

    report = BacktestReport(
        start="2024-01-02",
        end="2024-01-15",
        trades=trades,
        equity_curve=eq,
    )

    # win_rate = 3/4
    assert report.win_rate == pytest.approx(0.75)

    # avg_return
    assert report.avg_return == pytest.approx((0.10 + 0.05 + 0.08 - 0.05) / 4)

    # total_return：最后 / 第一 - 1
    assert report.total_return == pytest.approx(1.06 / 1.0 - 1.0)

    # max_drawdown：peak=1.15@idx2，min after=1.05@idx3, dd=(1.05/1.15-1)≈-0.0870
    # 但需要找全局最小 dd。再仔细：peak 之后最低是 idx7 的 1.09 吗？1.09/1.15-1=-0.0521。idx3 是 -0.0870，是最深
    expected_dd = 1.05 / 1.15 - 1.0
    assert report.max_drawdown == pytest.approx(expected_dd, rel=1e-4)

    # profit_factor = (0.10+0.05+0.08) / 0.05
    assert report.profit_factor == pytest.approx(0.23 / 0.05)

    # summary_dict 字段齐全
    s = report.summary_dict()
    for key in ("n_trades", "win_rate", "avg_return", "total_return", "max_drawdown", "sharpe", "profit_factor"):
        assert key in s


def test_report_per_ticker_stats():
    """按 ticker 分组统计"""
    dates = pd.bdate_range("2024-01-02", periods=4)
    trades = [
        TradeRecord("A", dates[0], 100, exit_date=dates[1], exit_price=110,
                    return_pct=0.10, exit_reason="signal_exit"),
        TradeRecord("A", dates[2], 100, exit_date=dates[3], exit_price=95,
                    return_pct=-0.05, exit_reason="stop_loss"),
        TradeRecord("B", dates[0], 100, exit_date=dates[1], exit_price=108,
                    return_pct=0.08, exit_reason="signal_exit"),
    ]
    report = BacktestReport(start="2024-01-02", end="2024-01-10", trades=trades)
    df = report.per_ticker_stats()

    assert set(df["ticker"]) == {"A", "B"}
    a_row = df[df["ticker"] == "A"].iloc[0]
    assert a_row["n_trades"] == 2
    assert a_row["win_rate"] == pytest.approx(0.5)
    assert a_row["avg_return"] == pytest.approx((0.10 + -0.05) / 2)
    assert a_row["total_return"] == pytest.approx(0.10 - 0.05)

    b_row = df[df["ticker"] == "B"].iloc[0]
    assert b_row["n_trades"] == 1
    assert b_row["win_rate"] == pytest.approx(1.0)


def test_html_report_generated(tmp_path, monkeypatch):
    """HTML 报告能生成（含权益曲线 SVG）"""
    from quant_scanner.backtest.reporter import BacktestHTMLReporter

    data = {"AAA": _make_synthetic(n=300, seed=1)}
    _patch_loader(monkeypatch, data)

    bt = Backtester(
        signals=[AlwaysHighSignal()],
        universe=["AAA"],
        max_positions=1,
    )
    report = bt.run(start="2023-01-02", end="2024-04-01", rebalance_freq="W")

    out = tmp_path / "report.html"
    BacktestHTMLReporter().render(report, out)
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "回测报告" in html
    assert "<svg" in html  # 权益曲线 SVG
    assert "胜率" in html


def test_empty_universe_returns_empty_report(monkeypatch):
    """universe 无数据时返回空 report，不抛异常"""
    _patch_loader(monkeypatch, {})

    bt = Backtester(
        signals=[AlwaysHighSignal()],
        universe=["MISSING"],
        max_positions=1,
    )
    report = bt.run(start="2023-01-02", end="2024-01-01")
    assert report.n_trades == 0
    assert report.equity_curve.empty


def test_invalid_arguments():
    """参数校验"""
    with pytest.raises(ValueError):
        Backtester(signals=[], universe=["X"])
    with pytest.raises(ValueError):
        Backtester(signals=[AlwaysHighSignal()], universe=["X"], max_positions=0)
    with pytest.raises(ValueError):
        Backtester(
            signals=[AlwaysHighSignal()],
            universe=["X"],
            weights={"always_high": 0.0},  # 总和为 0
        )
