"""仓位管理数学测试"""
from __future__ import annotations

import math

import pytest

from quant_scanner.utils.position_sizing import (
    TradeSetup, should_trade, should_add_position, position_size,
    trailing_stop_updater, rule_50_80_advice, RULE_50_80,
    ic_to_factor_weight,
    FACTOR_WEIGHT_STRONG, FACTOR_WEIGHT_EFFECTIVE,
    FACTOR_WEIGHT_BASE, FACTOR_WEIGHT_WEAK,
    IC_STRONG, IC_EFFECTIVE, IC_WEAK,
)


def test_expectancy_positive_when_good_setu():
    # 胜率 50%，目标 +20%，止损 -8%
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=120, win_rate=0.5)
    # E = 0.5 * 0.20 + 0.5 * (-0.08) = 0.10 - 0.04 = +0.06
    e = setup.expectancy()
    assert abs(e - 0.06) < 1e-6
    ok, msg = should_trade(setup)
    assert ok


def test_expectancy_negative_when_bad_setup():
    # 胜率 30%，目标 +5%，止损 -8%
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=105, win_rate=0.3)
    # E = 0.3 * 0.05 + 0.7 * (-0.08) = 0.015 - 0.056 = -0.041
    e = setup.expectancy()
    assert e < 0
    ok, msg = should_trade(setup)
    assert not ok


def test_two_to_one_rule():
    # 盈亏比 2:1
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=116, win_rate=0.5)
    # reward = 16, risk = 8, ratio = 2.0
    assert abs(setup.payoff_ratio - 2.0) < 1e-6
    ok, _ = should_add_position(setup)
    assert ok

    # 盈亏比 1:1，不加仓
    setup2 = TradeSetup(entry_price=100, stop_loss=92, target_price=108, win_rate=0.5)
    ok, msg = should_add_position(setup2)
    assert not ok


def test_position_size_risk_based():
    """账户 10000，单笔风险 1%（$100），每股风险 $8 → 12.5 股"""
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=120, win_rate=0.5)
    shares = position_size(10000, 0.01, setup)
    # 100 / 8 = 12.5
    assert abs(shares - 12.5) < 1e-6


def test_trailing_stop_advances():
    entry = 100.0
    stop = 92.0  # 初始 -8% 止损
    # 涨 5%：抬高到盈亏平衡
    new_stop, msg = trailing_stop_updater(105.0, entry, stop)
    assert abs(new_stop - 100.0) < 1e-6
    # 涨 10%：抬高到 +5%
    new_stop, _ = trailing_stop_updater(110.0, entry, 100.0)
    assert abs(new_stop - 105.0) < 1e-6
    # 涨 20%：抬高到 +10%
    new_stop, _ = trailing_stop_updater(120.0, entry, 105.0)
    assert abs(new_stop - 110.0) < 1e-6


def test_trailing_stop_never_lowers():
    entry, stop = 100.0, 95.0
    # 涨不够：不调整
    new_stop, msg = trailing_stop_updater(102.0, entry, stop)
    assert new_stop == stop


def test_rule_50_80_alarm_on_big_drop_with_volume():
    msg = rule_50_80_advice(days_since_breakout=3, drawdown_pct=-0.06, volume_ratio=1.8)
    assert "弱势卖出" in msg or "警惕" in msg


def test_rule_50_80_normal_pullback():
    msg = rule_50_80_advice(days_since_breakout=3, drawdown_pct=-0.02, volume_ratio=0.9)
    assert "常态" in msg or "正常" in msg


# ==================== 2% 风控铁律 + ATR 自适应（Murphy Ch10 升级）====================

from quant_scanner.utils.position_sizing import (
    DEFAULT_RISK_PER_TRADE_PCT,
    DEFAULT_MAX_TOTAL_RISK_PCT,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_SECTOR_PCT,
    atr_adjusted_risk_pct,
    Position,
    PortfolioCheckResult,
    portfolio_risk_check,
    max_positions_by_volatility,
)


def test_position_size_defaults_to_2pct():
    """新签名默认 risk_budget_pct=0.02（Murphy 2% 铁律）"""
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=120)
    shares = position_size(10000, setup)  # 不传 risk_budget_pct
    # 10000 * 0.02 / 8 = 25 股
    assert abs(shares - 25.0) < 1e-6


def test_position_size_backward_compat():
    """旧签名（account, risk_pct, setup）仍兼容"""
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=120)
    shares = position_size(10000, 0.01, setup)  # 旧签名
    assert abs(shares - 12.5) < 1e-6


def test_atr_adjusted_high_volatility_reduces():
    """ATR/price > 4% → 减仓 20%"""
    adj, msg = atr_adjusted_risk_pct(0.02, atr=5.0, price=100)
    # 5/100 = 5% > 4% → 0.02 * 0.8 = 0.016
    assert abs(adj - 0.016) < 1e-6
    assert "减仓" in msg


def test_atr_adjusted_low_volatility_boosts():
    """ATR/price < 1.5% → 加仓 10%"""
    adj, msg = atr_adjusted_risk_pct(0.02, atr=1.0, price=100)
    # 1/100 = 1% < 1.5% → 0.02 * 1.1 = 0.022
    assert abs(adj - 0.022) < 1e-6
    assert "加仓" in msg


def test_atr_adjusted_normal_keeps():
    """ATR/price 在正常区间 → 保持基础值"""
    adj, msg = atr_adjusted_risk_pct(0.02, atr=2.5, price=100)
    # 2.5/100 = 2.5% → 正常
    assert abs(adj - 0.02) < 1e-6


def test_position_size_with_atr():
    """position_size 加 atr 参数启用自适应"""
    setup = TradeSetup(entry_price=100, stop_loss=92, target_price=120)
    shares_base = position_size(10000, setup)
    shares_high_vol = position_size(10000, setup, atr=5.0)
    # 高波动减仓 20% → 股数 × 0.8
    assert abs(shares_high_vol - shares_base * 0.8) < 1e-6


def test_portfolio_risk_check_passes_within_limits():
    """组合在限制内 → passed=True"""
    positions = [
        Position(ticker="A", entry_price=100, stop_loss=92, shares=100, sector="Tech"),
        Position(ticker="B", entry_price=50, stop_loss=45, shares=200, sector="Health"),
    ]
    # account = 100000
    # total_mv = 100*100 + 50*200 = 20000（20%）
    # total_risk = 8*100 + 5*200 = 1800（1.8%）
    result = portfolio_risk_check(positions, account_equity=100000)
    assert result.passed is True
    assert len(result.violations) == 0
    assert abs(result.total_exposure_pct - 0.20) < 1e-6
    assert abs(result.total_risk_pct - 0.018) < 1e-3


def test_portfolio_risk_check_exposure_violation():
    """总仓位 > 80% → 违反"""
    positions = [
        Position(ticker="A", entry_price=100, stop_loss=92, shares=500, sector="Tech"),
    ]
    # account = 50000
    # total_mv = 100*500 = 50000（100%）
    result = portfolio_risk_check(positions, account_equity=50000)
    assert result.passed is False
    assert any("总仓位" in v for v in result.violations)


def test_portfolio_risk_check_total_risk_violation():
    """总风险 > 6% → 违反"""
    # 每股风险 8 元 * 1000 股 = 8000，account=100000 → 8%
    positions = [
        Position(ticker="A", entry_price=100, stop_loss=92, shares=1000, sector="Tech"),
    ]
    result = portfolio_risk_check(positions, account_equity=100000)
    assert result.passed is False
    assert any("总风险" in v for v in result.violations)


def test_portfolio_risk_check_sector_concentration():
    """同行业 > 40% → 违反"""
    positions = [
        Position(ticker="A", entry_price=100, stop_loss=92, shares=300, sector="Tech"),
        Position(ticker="B", entry_price=100, stop_loss=92, shares=300, sector="Tech"),
    ]
    # account = 100000, total Tech = 60000（60%）
    result = portfolio_risk_check(positions, account_equity=100000)
    assert result.passed is False
    assert any("Tech" in v and "行业" in v for v in result.violations)


def test_max_positions_high_vix():
    """VIX > 25 → 最多 5 仓"""
    n, label = max_positions_by_volatility(30.0)
    assert n == 5
    assert "高波动" in label


def test_max_positions_low_vix():
    """VIX < 15 → 最多 10 仓"""
    n, label = max_positions_by_volatility(12.0)
    assert n == 10
    assert "低波动" in label


def test_max_positions_normal_vix():
    """VIX 15-25 → 最多 8 仓"""
    n, label = max_positions_by_volatility(20.0)
    assert n == 8
    assert "正常" in label


# ========== 任务 #84：因子 IC → 仓位权重联动 ==========

def test_ic_to_factor_weight_strong():
    """|IC| ≥ 0.08 → 强因子，1.5x"""
    w, reason = ic_to_factor_weight(0.09)
    assert w == FACTOR_WEIGHT_STRONG
    assert "强因子" in reason


def test_ic_to_factor_weight_effective():
    """|IC| 0.05-0.08 → 有效，1.2x"""
    w, _ = ic_to_factor_weight(0.06)
    assert w == FACTOR_WEIGHT_EFFECTIVE


def test_ic_to_factor_weight_weak():
    """|IC| 0.03-0.05 → 弱信号，1.0x"""
    w, _ = ic_to_factor_weight(0.04)
    assert w == FACTOR_WEIGHT_BASE


def test_ic_to_factor_weight_noise():
    """|IC| < 0.03 → 无信号，0.7x"""
    w, _ = ic_to_factor_weight(0.01)
    assert w == FACTOR_WEIGHT_WEAK


def test_ic_to_factor_weight_negative_ic():
    """负 IC（反向因子）也用 |IC| 评级"""
    w, _ = ic_to_factor_weight(-0.10)
    assert w == FACTOR_WEIGHT_STRONG


def test_position_size_with_strong_factor():
    """强因子加仓：基础 100 股 → 150 股"""
    setup = TradeSetup(entry_price=100, stop_loss=95, target_price=115, win_rate=0.5)
    # 基础仓位（factor_weight=1.0）
    base = position_size(account_equity=10000, setup_or_risk=setup, risk_budget_pct=0.02)
    # risk_per_share = 5, risk_amount = 10000*0.02 = 200, base_shares = 200/5 = 40
    assert base == pytest.approx(40.0, rel=1e-6)
    # 强因子 1.5x
    boosted = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=1.5)
    assert boosted == pytest.approx(60.0, rel=1e-6)


def test_position_size_with_weak_factor():
    """弱信号减仓：基础 40 股 → 28 股"""
    setup = TradeSetup(entry_price=100, stop_loss=95, target_price=115, win_rate=0.5)
    reduced = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=0.7)
    assert reduced == pytest.approx(28.0, rel=1e-6)


def test_position_size_factor_weight_clamped():
    """factor_weight 上限 1.5（不能无限加仓）+ 下限 0.5"""
    setup = TradeSetup(entry_price=100, stop_loss=95, target_price=115, win_rate=0.5)
    # 传入 5.0 应被限制到 1.5
    extreme = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=5.0)
    capped = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=1.5)
    assert extreme == pytest.approx(capped, rel=1e-6)
    # 传入 0.0 应被限制到 0.5
    zero = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=0.0)
    floor = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=0.5)
    assert zero == pytest.approx(floor, rel=1e-6)


def test_factor_weight_default_is_base():
    """不传 factor_weight = 基础仓位（向后兼容）"""
    setup = TradeSetup(entry_price=100, stop_loss=95, target_price=115, win_rate=0.5)
    default = position_size(10000, setup, risk_budget_pct=0.02)
    explicit = position_size(10000, setup, risk_budget_pct=0.02, factor_weight=1.0)
    assert default == pytest.approx(explicit, rel=1e-6)


def test_factor_weight_combined_with_atr():
    """factor_weight × ATR 自适应：两者叠加"""
    setup = TradeSetup(entry_price=100, stop_loss=95, target_price=115, win_rate=0.5)
    # ATR=3 (3% vol，正常范围) + 强因子 1.5x
    combined = position_size(10000, setup, risk_budget_pct=0.02, atr=3.0, factor_weight=1.5)
    base = position_size(10000, setup, risk_budget_pct=0.02)
    # ATR 正常 → risk_pct 不变；factor_weight=1.5 → 1.5x
    assert combined == pytest.approx(base * 1.5, rel=1e-6)
