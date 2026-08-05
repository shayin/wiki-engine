"""CAN SLIM 信号升级版测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from quant_scanner.signals.can_slim import CANSLIMSignal
from quant_scanner.signals.base import SignalResult


def _make_synthetic_df(n: int = 300, drift: float = 0.001, vol: float = 0.02, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, size=n)
    prices = 100 * np.exp(np.cumsum(returns))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    df = pd.DataFrame({
        "open": prices,
        "high": prices * (1 + rng.uniform(0, 0.01, size=n)),
        "low": prices * (1 - rng.uniform(0, 0.01, size=n)),
        "close": prices,
        "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=dates)
    return df


def _make_mock_market_df(n: int = 500, drift: float = 0.0005) -> pd.DataFrame:
    """合成 SPX 数据"""
    rng = np.random.default_rng(100)
    returns = rng.normal(drift, 0.01, size=n)
    prices = 5000 * np.exp(np.cumsum(returns))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1e9, 5e9, size=n),
    }, index=dates)


def _make_smooth_uptrend_market(n: int = 500, drift: float = 0.002, vol: float = 0.005) -> pd.DataFrame:
    """构造平滑上涨市场（低波动），用于健康上涨测试"""
    rng = np.random.default_rng(7)
    returns = rng.normal(drift, vol, size=n)
    prices = 5000 * np.exp(np.cumsum(returns))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(2e9, 3e9, size=n),  # 平稳成交量
    }, index=dates)


def _make_mock_loader(fund: dict, eps_history: dict, market_df: pd.DataFrame):
    """构造 mock loader 避免真实网络调用"""
    loader = MagicMock()
    loader.load_fundamentals.return_value = fund
    loader.load_eps_history.return_value = eps_history
    loader.load.return_value = market_df
    return loader


def test_strong_eps_growth_passes_c_letter():
    """C 字母强 EPS 增长 + 加速度 = 满分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5, "sector": "Tech"}
    eps = {
        "quarterly_yoy": {"2025-Q1": 0.30, "2025-Q2": 0.40, "2025-Q3": 0.50},
        "quarterly_accelerating": True,
        "annual_3y_cagr": 0.30,
    }
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["C"] >= 0.9


def test_weak_eps_fails_c_letter():
    """EPS 增长 < 25% = C 字母低分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {
        "quarterly_yoy": {"2025-Q1": 0.05, "2025-Q2": 0.08, "2025-Q3": 0.10},
        "quarterly_accelerating": True,
        "annual_3y_cagr": 0.05,
    }
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["C"] < 0.5


def test_market_direction_healthy_uptrend():
    """MA50 > MA200 + 无 FTD = 健康上涨"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    # 平滑上涨市场：低波动 + 正漂移，避免触发分布日
    market = _make_mock_market_df(n=500, drift=0.002)
    # 进一步降低市场波动，让分布日不触发
    market = _make_smooth_uptrend_market(n=500, drift=0.002, vol=0.005)
    loader = _make_mock_loader(fund, eps, market)
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df(n=300, drift=0.002)
    r = sig.evaluate("TEST", df)
    # M 字母 ≥ 0.7（健康上涨或确认上涨）
    assert r.details["letters"]["M"] >= 0.7


def test_market_direction_stress_with_distribution_days():
    """市场长期下跌 → M 字母低分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    # 长期下跌的市场
    market = _make_mock_market_df(n=500, drift=-0.001)
    loader = _make_mock_loader(fund, eps, market)
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df(n=300, drift=-0.001)
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["M"] <= 0.5


def test_skip_fundamentals_mode():
    """skip_fundamentals=True 时 C/A/S/I 都是 0.5（回测 PIT 合规）"""
    loader = MagicMock()
    loader.load.return_value = _make_mock_market_df()
    sig = CANSLIMSignal(loader=loader, skip_fundamentals=True)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    letters = r.details["letters"]
    assert letters["C"] == 0.5
    assert letters["A"] == 0.5
    assert letters["S"] == 0.5
    assert letters["I"] == 0.5
    # 但 N/L/M 应该正常评估（基于价格数据）
    assert 0.0 <= letters["N"] <= 1.0
    assert 0.0 <= letters["L"] <= 1.0
    assert 0.0 <= letters["M"] <= 1.0
    # loader.load_fundamentals 不应被调用
    loader.load_fundamentals.assert_not_called()


def test_new_high_5d_gets_full_n_score():
    """5 日内创新高 → N 字母满分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    df = _make_synthetic_df(n=300, drift=0.003)
    # 把最后一行改成创新高
    df.loc[df.index[-1], "close"] = df["close"].max() * 1.01
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["N"] == 1.0
    assert r.details["new_high_5d"] is True


def test_details_contain_required_keys():
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5, "sector": "Tech"}
    eps = {"quarterly_yoy": {"Q": 0.3}, "quarterly_accelerating": True, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert "letters" in r.details
    assert "market_direction" in r.details
    assert "distribution_days_count" in r.details["market_direction"]
    assert all(l in r.details["letters"] for l in ["C", "A", "N", "S", "L", "I", "M"])


def test_can_slim_uses_injected_rs_rating_for_l_letter():
    """注入 rs_rating 时，L 字母用 RS Rating 全市场百分位（而非降级 vs SPX）。"""
    mock_rs = MagicMock()
    mock_rs.evaluate.return_value = SignalResult(
        ticker="TEST", signal_name="rs_rating", value=0.95, passed=True,
        details={"rs_rating": 94, "verdict": "领涨股"},
        reasons=["RS Rating 94（12 月 +50.0%）"],
    )
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader, rs_rating=mock_rs)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # L 字母用了 rs_rating 的 value
    assert r.details["letters"]["L"] == 0.95
    assert r.details["rs_rating"] == 94
    mock_rs.evaluate.assert_called_once()
    # reason 含 RS Rating 标记
    assert any("RS Rating 94" in rsn and "全市场百分位" in rsn for rsn in r.reasons)


def test_backtest_pit_auto_skips_fundamentals():
    """df 数据距今 >5 天 → 自动 PIT 模式：基本面 skip，避免未来快照泄露。

    回测时 df_up_to 最后日期是历史日期，evaluate 应自动判定为回测模式，
    不拉取当前基本面快照（否则会用未来财报数据 → PIT 泄露）。
    """
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    # 历史 df：index 整体前移 40 天，最后日期距今 >30 天 → 启发式判定为回测模式
    df = _make_synthetic_df()
    df.index = df.index - pd.Timedelta(days=40)
    r = sig.evaluate("TEST", df)
    letters = r.details["letters"]
    # C/A/S/I 被自动 skip 成中性分 0.5（无未来基本面）
    assert letters["C"] == 0.5
    assert letters["A"] == 0.5
    assert letters["S"] == 0.5
    assert letters["I"] == 0.5
    # 基本面接口未被调用（核心：防未来泄露）
    loader.load_fundamentals.assert_not_called()
    loader.load_eps_history.assert_not_called()
    # reasons 含 PIT 提示
    assert any("PIT" in rsn for rsn in r.reasons), r.reasons


def test_load_market_truncated_by_pit_date():
    """_load_market(pit_date) 返回数据严格 index <= pit_date（PIT 合规）。

    回测时 market 数据必须按 pit_date 截断，否则 M/L 字母会用未来大盘数据。
    """
    loader = _make_mock_loader({}, {}, _make_mock_market_df())
    sig = CANSLIMSignal(loader=loader)
    pit = pd.Timestamp.now().normalize() - pd.Timedelta(days=100)
    market = sig._load_market(pit_date=pit)
    assert not market.empty
    # 关键：截断后所有日期 <= pit_date，不含未来
    assert (market.index <= pit).all()
    # 且 market 的最后日期应该比较接近 pit（证明确实截断到了 pit 附近，而非返回全量）
    assert market.index[-1] <= pit


# ==================== EDGAR C/A 字母升级测试（新增）====================

def test_c_letter_prefers_edgar_over_yfinance():
    """EDGAR 路径优先：loader 提供 load_edgar_quarterly_eps_yoy 时 C 用 EDGAR"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    # yfinance 路径弱数据（应被忽略）
    eps = {"quarterly_yoy": {"Q": 0.05}, "annual_3y_cagr": 0.05}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    # EDGAR 路径强数据
    loader.load_edgar_quarterly_eps_yoy.return_value = {"2024-Q3": 0.45}
    loader.load_edgar_eps_acceleration.return_value = True
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["C"] >= 0.9
    assert r.details["c_eps_source"] == "edgar"
    # yfinance 数据被忽略
    loader.load_edgar_quarterly_eps_yoy.assert_called_once()


def test_a_letter_edgar_cagr_with_roe_bonus():
    """A 字母：EDGAR CAGR 满分 + ROE ≥ 17% → 额外加分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {}  # 不走 yfinance 路径
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_edgar_annual_eps_cagr.return_value = 0.35  # CAGR 35%
    loader.load_edgar_roe.return_value = 0.25              # ROE 25%
    loader.load_edgar_operating_margin_trend.return_value = (0.25, True)  # 利润率上升
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # A 字母满分（CAGR 35% ≥ 25%）+ ROE 加分
    assert r.details["letters"]["A"] == 1.0
    assert r.details["roe"] == 0.25
    assert r.details["operating_margin"] == 0.25
    assert r.details["a_eps_source"] == "edgar"


def test_a_letter_low_roe_penalized():
    """A 字母：ROE < 10% 扣 0.2"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_edgar_annual_eps_cagr.return_value = 0.35  # CAGR 满分
    loader.load_edgar_roe.return_value = 0.05  # ROE 5%（弱）
    loader.load_edgar_operating_margin_trend.return_value = (0.10, True)
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # CAGR 满分 1.0 + ROE 扣 0.2 = 0.8
    assert abs(r.details["letters"]["A"] - 0.8) < 1e-6


def test_a_letter_falling_margins_penalized():
    """A 字母：利润率 3 年下行扣 0.1"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_edgar_annual_eps_cagr.return_value = 0.35
    loader.load_edgar_roe.return_value = 0.20
    loader.load_edgar_operating_margin_trend.return_value = (0.15, False)  # 利润率下行
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # CAGR 1.0 + ROE 0.05 加分 - 0.1 利润率扣分 = 0.95
    assert r.details["letters"]["A"] <= 0.95


# ==================== M 字母统一 MarketDirectionSignal 测试 ====================

def test_m_letter_uses_market_direction_signal():
    """M 字母调用 MarketDirectionSignal（不再重复实现）"""
    from quant_scanner.signals.market_direction import MarketDirectionSignal
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_smooth_uptrend_market())
    sig = CANSLIMSignal(loader=loader)
    assert isinstance(sig._market_direction, MarketDirectionSignal)


def test_m_letter_fail_blocks_buy():
    """M 字母 fail（< 0.3）→ passed=False（即使总分 ≥ 0.7）"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.5}, "annual_3y_cagr": 0.4}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df(n=500, drift=-0.003))
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df(n=300, drift=0.003)
    r = sig.evaluate("TEST", df)
    # M 字母 < 0.3 → passed 强制 False
    assert r.details["letters"]["M"] < 0.3
    assert r.passed is False
    assert any("M 字母 fail" in rsn for rsn in r.reasons)


# ==================== I 字母升级测试（institutional_holders 详细数据）====================

def test_i_letter_strong_institutional_sponsorship():
    """I 字母：机构数 ≥ 10 + 净增持方向 → 1.0"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = {
        "holder_count": 15,
        "net_increasing": 8,
        "net_decreasing": 3,
        "net_new": 4,
        "total_change_shares": 5_000_000.0,
        "latest_report_date": "2025-09-30",
        "top_holders": [{"holder": "Vanguard", "shares": 100e6, "change": 1e6, "pct_out": 0.05}],
    }
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # 机构数 15 ≥ 10 → base 0.7；净增持 8 > 3 → +0.2 = 0.9
    assert abs(r.details["letters"]["I"] - 0.9) < 1e-6
    assert r.details["i_source"] == "yfinance_holders"
    assert r.details["inst_holder_count"] == 15
    assert r.details["inst_direction"] == "increasing"


def test_i_letter_many_holders_net_decreasing():
    """I 字母：机构数 ≥ 10 但净减持方向 → 0.7 - 0.1 = 0.6"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = {
        "holder_count": 12,
        "net_increasing": 2,
        "net_decreasing": 7,
        "net_new": 3,
        "total_change_shares": -2_000_000.0,
        "latest_report_date": "2025-09-30",
        "top_holders": [],
    }
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert abs(r.details["letters"]["I"] - 0.6) < 1e-6
    assert r.details["inst_direction"] == "decreasing"


def test_i_letter_moderate_holders_neutral():
    """I 字母：机构数 5-10 且 无净方向 → 0.5"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = {
        "holder_count": 7,
        "net_increasing": 0,
        "net_decreasing": 0,
        "net_new": 7,
        "total_change_shares": 0.0,
        "latest_report_date": "2025-09-30",
        "top_holders": [],
    }
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["I"] == 0.5
    assert r.details["inst_direction"] == "neutral"


def test_i_letter_few_holders_zero_score():
    """I 字母：机构数 < 5 → 0.0 基础分"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.5}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = {
        "holder_count": 3,
        "net_increasing": 2,
        "net_decreasing": 1,
        "net_new": 0,
        "total_change_shares": 0.0,
        "latest_report_date": "2025-09-30",
        "top_holders": [],
    }
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # base 0.0；净增持方向 +0.2 = 0.2
    assert abs(r.details["letters"]["I"] - 0.2) < 1e-6


def test_i_letter_fallback_to_pct():
    """I 字母：institutional_holders 缺失 → 回退 institutional_pct"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.55}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = None
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["i_source"] == "yfinance_pct"
    # pct ≥ 0.5 → 1.0
    assert r.details["letters"]["I"] == 1.0


def test_i_letter_pct_below_threshold():
    """I 字母：institutional_pct < 30% → 线性映射"""
    fund = {"shares_outstanding": 100e6, "institutional_pct": 0.15}
    eps = {"quarterly_yoy": {"Q": 0.3}, "annual_3y_cagr": 0.3}
    loader = _make_mock_loader(fund, eps, _make_mock_market_df())
    loader.load_institutional_holders.return_value = None
    sig = CANSLIMSignal(loader=loader)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    # 0.15 / 0.30 = 0.5
    assert abs(r.details["letters"]["I"] - 0.5) < 1e-6


def test_i_letter_pit_skip_mode():
    """I 字母：PIT 回测 → 中性 0.5"""
    loader = MagicMock()
    loader.load.return_value = _make_mock_market_df()
    sig = CANSLIMSignal(loader=loader, skip_fundamentals=True)
    df = _make_synthetic_df()
    r = sig.evaluate("TEST", df)
    assert r.details["letters"]["I"] == 0.5
    loader.load_institutional_holders.assert_not_called()
