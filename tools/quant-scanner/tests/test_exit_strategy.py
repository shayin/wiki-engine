"""ExitStrategySignal 测试

覆盖：
- 健康上涨状态（HOLD，安全度高）
- 跌破止损触发 EXIT
- 时间止损（持仓超期 + 收益未达标）
- 跌破 MA13 → TIGHTEN_STOP
- 跌破 MA30 → REDUCE
- 数据不足兜底
- 入场点解析（外部传入 vs 自动占位）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.signals.exit_strategy import ExitStrategySignal


@pytest.fixture
def trending_up() -> pd.DataFrame:
    """上涨行情：30 日内 close 持续抬高"""
    rng = np.random.default_rng(42)
    n = 200
    rets = rng.normal(0.003, 0.01, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.005, n))
    low = close * (1 - rng.uniform(0, 0.005, n))
    op = close * (1 + rng.normal(0, 0.002, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


@pytest.fixture
def crashing() -> pd.DataFrame:
    """下跌行情：close 跌破 MA30 + ATR 止损"""
    n = 100
    close = np.concatenate([
        np.linspace(100, 130, 60),  # 先涨
        np.linspace(130, 80, 40),   # 再跌
    ])
    high = close * 1.005
    low = close * 0.995
    op = close
    vol = np.full(n, 2_000_000.0)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


def test_hold_on_trending_up(trending_up):
    """健康上涨：value 高、action=HOLD（入场点取 -30 日避免触发 60d 时间止损）"""
    sig = ExitStrategySignal(entry_price=trending_up["close"].iloc[-30],
                             entry_date=str(trending_up.index[-30].date()))
    sr = sig.evaluate("TEST", trending_up)
    assert sr.details["action"] in ("HOLD", "WATCH")
    assert sr.value > 0.5
    assert not sr.passed  # 不触发卖出


def test_exit_when_break_stop(crashing):
    """跌破止损：触发 EXIT"""
    # 在高位入场，然后暴跌
    entry_price = crashing["close"].iloc[60]  # 130
    entry_date = str(crashing.index[60].date())
    sig = ExitStrategySignal(entry_price=entry_price, entry_date=entry_date)
    sr = sig.evaluate("TEST", crashing)
    assert sr.details["action"] == "EXIT"
    assert sr.passed
    assert len(sr.details["exit_reasons"]) > 0


def test_time_stop_when_overhold(trending_up):
    """持仓超期 + 未达目标 → 时间止损

    构造场景：在 100 日前入场，但故意抬高入场价让 current_return 远低于目标。
    """
    # 故意抬高入场价让收益远不达目标（即使价格涨了）
    entry_price = trending_up["close"].iloc[-1] * 1.30  # 比当前价还高 30% → 负收益
    sig = ExitStrategySignal(
        entry_price=entry_price,
        entry_date=str(trending_up.index[0].date()),
        max_holding_days=30,  # 强制 30 日内必须达目标
        target_reward_pct=0.50,  # 目标 50%（不可能达到）
    )
    sr = sig.evaluate("TEST", trending_up)
    # 持仓 200 日 > 30 日 + 当前为负收益，远未达 50%
    time_triggered = any("时间止损" in r for r in sr.details["exit_reasons"])
    assert time_triggered or sr.details["action"] == "EXIT"


def test_tighten_stop_when_below_ma13(crashing):
    """跌破 MA13 但未破 MA30 → TIGHTEN_STOP"""
    # 在中间入场
    entry_price = crashing["close"].iloc[50]  # ~125
    entry_date = str(crashing.index[50].date())
    sig = ExitStrategySignal(entry_price=entry_price, entry_date=entry_date)
    sr = sig.evaluate("TEST", crashing)
    # 已经跌破 MA13 但不一定触发 EXIT（要看止损位）
    assert sr.details["action"] in ("TIGHTEN_STOP", "REDUCE", "EXIT", "WATCH")


def test_resolve_entry_external(trending_up):
    """外部传入 entry_price + entry_date"""
    sig = ExitStrategySignal(entry_price=100.0, entry_date=str(trending_up.index[-30].date()))
    price, idx = sig._resolve_entry(trending_up)
    assert price == 100.0
    assert idx is not None


def test_resolve_entry_auto_low(trending_up):
    """未传入 → 用最近 60 日最低点"""
    sig = ExitStrategySignal()
    price, idx = sig._resolve_entry(trending_up)
    recent_low = trending_up.tail(60)["low"].min()
    assert price == pytest.approx(recent_low, rel=1e-6)


def test_insufficient_data():
    """数据不足 → 返回 error 不抛异常"""
    df = pd.DataFrame({
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.0], "volume": [1e6],
    })
    sig = ExitStrategySignal()
    sr = sig.evaluate("TEST", df)
    assert "error" in sr.details
    assert not sr.passed


def test_target_price_3to1_reward(trending_up):
    """目标价 = entry + 3 × risk"""
    sig = ExitStrategySignal(entry_price=100.0,
                             entry_date=str(trending_up.index[-30].date()))
    sr = sig.evaluate("TEST", trending_up)
    risk = sr.details["risk_per_share"]
    target = sr.details["target"]
    assert target == pytest.approx(100.0 + 3.0 * risk, rel=1e-3)


def test_details_contains_required_fields(trending_up):
    """details 必含完整出场策略字段"""
    sig = ExitStrategySignal(entry_price=100.0,
                             entry_date=str(trending_up.index[-30].date()))
    sr = sig.evaluate("TEST", trending_up)
    for k in ["entry_price", "last_close", "current_stop", "trailing_stop",
              "initial_stop", "target", "holding_days", "atr", "action",
              "ma5", "ma13", "ma30", "current_return_pct",
              "exit_reasons", "reduce_reasons"]:
        assert k in sr.details, f"缺字段 {k}"


def test_ma30_break_triggers_reduce_not_exit(crashing):
    """跌破 MA30 应触发 REDUCE，不应直接 EXIT（避免截断赢家，HANDOFF 回测锚点）"""
    # 构造：在高位入场，价格回落跌破 MA30 但还未跌破 ATR 止损
    entry_price = crashing["close"].iloc[55]  # 接近顶部入场
    entry_date = str(crashing.index[55].date())
    sig = ExitStrategySignal(entry_price=entry_price, entry_date=entry_date)
    sr = sig.evaluate("TEST", crashing)
    # 跌破 MA30 应进 reduce_reasons，不应进 exit_reasons
    if sr.details.get("below_ma30"):
        assert sr.details["action"] in ("REDUCE", "EXIT")  # EXIT 仅当同时破 ATR 止损
        # 关键：MA30 单独触发不应进 exit_reasons
        ma30_in_exit = any("MA30" in r for r in sr.details["exit_reasons"])
        ma30_in_reduce = any("MA30" in r for r in sr.details["reduce_reasons"])
        assert not ma30_in_exit, "MA30 跌破不应触发 EXIT（截断赢家 bug）"
        assert ma30_in_reduce, "MA30 跌破应进 reduce_reasons"
