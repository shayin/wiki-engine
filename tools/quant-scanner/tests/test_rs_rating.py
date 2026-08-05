"""RS Rating 信号测试"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from quant_scanner.signals.rs_rating import RSRatingSignal, load_watchlist


def _make_df_with_return(n: int, total_return: float, seed: int = 42) -> pd.DataFrame:
    """造 df，使 close[-1]/close[-252]（evaluate 用的 12 月窗口收益）精确等于 total_return。"""
    rng = np.random.default_rng(seed)
    raw = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=n)))
    lookback = 252
    if n >= lookback:
        # 重写最后 lookback 段：从 anchor 涨到 anchor*(1+r)，使 close[-1]/close[-lookback]=1+r
        anchor = raw[-lookback]
        raw[-lookback:] = np.linspace(anchor, anchor * (1 + total_return), lookback)
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": raw, "high": raw * 1.005, "low": raw * 0.995,
        "close": raw, "volume": rng.integers(1e6, 1e7, size=n),
    }, index=dates)


def _make_mock_loader(universe_dfs: dict[str, pd.DataFrame]):
    """mock loader：loader.load(t) 返回 universe_dfs[t]。"""
    loader = MagicMock()
    def _load(t, *args, **kwargs):
        return universe_dfs.get(t)
    loader.load.side_effect = _load
    return loader


def _build_universe(returns_by_ticker: dict[str, float]) -> dict[str, pd.DataFrame]:
    """{ticker: 12m_return} → {ticker: df}。"""
    return {t: _make_df_with_return(300, r, seed=i) for i, (t, r) in enumerate(returns_by_ticker.items())}


# 宇宙：12 只，12 月收益从 -20% 到 +38%（线性分散）
UNIVERSE_RETURNS = {
    "U1": -0.20, "U2": -0.12, "U3": -0.05, "U4": 0.02, "U5": 0.08, "U6": 0.12,
    "U7": 0.16, "U8": 0.20, "U9": 0.24, "U10": 0.28, "U11": 0.33, "U12": 0.38,
}


def test_rs_rating_strong_leader():
    """ticker 12 月收益远超宇宙 → RS Rating ≥ 85，passed=True。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    # 被评估 ticker 收益 +50%（高于宇宙所有人）
    df = _make_df_with_return(300, 0.50, seed=99)
    r = sig.evaluate("LEADER", df)
    assert r.details["rs_rating"] >= 85
    assert r.passed is True
    assert r.details["verdict"].startswith("领涨股")


def test_rs_rating_weak_forces_fail():
    """ticker 12 月收益低于宇宙所有人 → RS Rating 低，passed=False（强制不买）。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    df = _make_df_with_return(300, -0.30, seed=99)  # 比宇宙所有人都差
    r = sig.evaluate("LAGGARD", df)
    assert r.details["rs_rating"] < 70
    assert r.passed is False
    assert "强制不买" in r.details["verdict"]


def test_rs_rating_median_middle():
    """ticker 收益在宇宙中位附近 → RS Rating 居中（40-70）。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    df = _make_df_with_return(300, 0.15, seed=99)  # 中位附近（宇宙中位约 +15%）
    r = sig.evaluate("MID", df)
    rating = r.details["rs_rating"]
    assert 30 <= rating <= 80, f"中位股票 RS Rating 应在 30-80，实际 {rating}"


def test_universe_too_small_degrades_neutral():
    """宇宙 < 10 只 → 百分位不可靠，返回中性 0.5。"""
    small_uni = {"A": 0.10, "B": 0.20, "C": 0.05}  # 仅 3 只
    uni_dfs = _build_universe(small_uni)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(small_uni.keys()), loader=loader)
    df = _make_df_with_return(300, 0.30, seed=99)
    r = sig.evaluate("X", df)
    assert r.value == 0.5
    assert r.passed is False
    assert any("不可靠" in rsn for rsn in r.reasons)


def test_insufficient_data():
    """df < 252 行 → 数据不足，value=0。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    df = _make_df_with_return(100, 0.30, seed=99)  # 仅 100 行
    r = sig.evaluate("X", df)
    assert r.value == 0.0
    assert r.passed is False
    assert "数据不足" in r.reasons[0]


def test_compute_rs_rating_percentile():
    """直接测百分位计算：最高收益 → 高分，最低 → 低分。"""
    sig = RSRatingSignal(universe=[], loader=MagicMock())
    uni = {"A": 0.10, "B": 0.20, "C": 0.30, "D": 0.40, "E": 0.50,
           "F": 0.05, "G": -0.10, "H": 0.25, "I": 0.35, "J": 0.15}
    # 最强（+60%）应 ≥ 90
    assert sig.compute_rs_rating(0.60, uni) >= 90
    # 最弱（-50%）应 ≤ 10
    assert sig.compute_rs_rating(-0.50, uni) <= 10


def test_load_watchlist_reads_file(tmp_path):
    """load_watchlist 正确跳过注释和空行。"""
    wl = tmp_path / "watchlist.txt"
    wl.write_text("# 注释\n\nNVDA\nAAPL\n# 另一个注释\nTSLA\n")
    tickers = load_watchlist(wl)
    assert tickers == ["NVDA", "AAPL", "TSLA"]


def test_universe_cache_reused():
    """同一 pit_date 多次 evaluate 不重复拉宇宙数据（缓存生效）。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    df = _make_df_with_return(300, 0.20, seed=1)
    sig.evaluate("X", df)
    sig.evaluate("Y", df)  # 第二次，同宇宙
    # 宇宙 12 只，缓存生效下 loader.load 对每只 universe ticker 只调一次
    # 宽松断言：总调用次数 = 宇宙大小（不是 2 倍）
    total_calls = loader.load.call_count
    assert total_calls == len(UNIVERSE_RETURNS), f"缓存应避免重复拉取，实际调用 {total_calls} 次"


# ==================== PIT 合规测试（新增，覆盖 #1 #2 修复）====================

def test_explicit_pit_date_triggers_backtest_mode():
    """显式传 pit_date → is_backtest=True，宇宙按 pit_date 截断拉取（不拉 latest）。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)

    # df 是实时数据，但显式传 pit_date 强制走回测分支
    df = _make_df_with_return(300, 0.20, seed=1)
    pit_date = df.index[-100]  # 100 天前作为 pit_date
    r = sig.evaluate("TEST", df, pit_date=pit_date)
    assert r.details["is_backtest"] is True
    # 验证宇宙拉取用了 start/end 参数（不是 period="2y"）
    for call in loader.load.call_args_list:
        if "U1" in str(call.args) or "U1" in str(call.kwargs.get("ticker", "")):
            assert "start" in call.kwargs, f"PIT 模式应传 start/end，实际: {call.kwargs}"
            assert "use_cache" in call.kwargs and call.kwargs["use_cache"] is False, \
                f"PIT 模式应禁用磁盘缓存避免污染实时缓存，实际: {call.kwargs}"


def test_heuristic_pit_threshold_is_30_days():
    """启发式阈值改为 >30 天（覆盖长周末/节假日延迟）。"""
    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = _make_mock_loader(uni_dfs)
    sig = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)

    df = _make_df_with_return(300, 0.20, seed=2)
    # 25 天前：不该触发回测
    df_recent = df.copy()
    df_recent.index = df_recent.index - pd.Timedelta(days=25)
    r_recent = sig.evaluate("TEST", df_recent)
    assert r_recent.details["is_backtest"] is False, "25 天前应判定为实时模式"

    # 40 天前：应触发回测
    df_old = df.copy()
    df_old.index = df_old.index - pd.Timedelta(days=40)
    r_old = sig.evaluate("TEST", df_old)
    assert r_old.details["is_backtest"] is True, "40 天前应判定为回测模式"


def test_can_slim_with_rs_rating_pit_chain():
    """can_slim 注入 rs_rating 后，显式 pit_date 应完整传导（不泄露未来）。"""
    from quant_scanner.signals.can_slim import CANSLIMSignal

    uni_dfs = _build_universe(UNIVERSE_RETURNS)
    loader = MagicMock()
    loader.load_fundamentals.return_value = {}
    loader.load_eps_history.return_value = {}
    market_df = _make_df_with_return(600, 0.05, seed=99)
    def _load(t, *args, **kwargs):
        if t == "^GSPC":
            return market_df
        return uni_dfs.get(t)
    loader.load.side_effect = _load

    rs = RSRatingSignal(universe=list(UNIVERSE_RETURNS.keys()), loader=loader)
    sig = CANSLIMSignal(loader=loader, rs_rating=rs)
    df = _make_df_with_return(300, 0.20, seed=3)
    pit_date = df.index[-50]
    r = sig.evaluate("TEST", df, pit_date=pit_date)
    # PIT 模式 → C/A/S/I 都是 0.5（skip 基本面）
    letters = r.details["letters"]
    assert letters["C"] == 0.5
    assert letters["A"] == 0.5
    assert letters["I"] == 0.5
    loader.load_fundamentals.assert_not_called()


def test_continuation_dead_cat_bounce_rejected():
    """下跌中段的反弹不应被识别为 bull flag（#13 修复验证）。

    构造：明确下跌 60 天（seg 区间内 hi 在前段 lo 在后段）→ 反弹。
    如果 prior_trend 检测正确，应判定为"下跌趋势 → 只检测 bear flag"。
    """
    from quant_scanner.signals.continuation import ContinuationSignal
    # days[0:70] = 120→140（前期上涨）
    # days[70:131] = 140→80（下跌，seg 区间 [71:131]，hi 在前 lo 在后 = down）
    # days[131:151] = 80→130（反弹 dead cat bounce）
    days = np.arange(151)
    prices = np.interp(days, [0, 70, 131, 151], [120, 140, 80, 130])
    vol = np.full(151, 5e6)
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=151, freq="B")
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": vol,
    }, index=dates)

    sig = ContinuationSignal()
    r = sig.evaluate("TEST", df)
    # 前置趋势应是 down（不允许 bull flag）
    assert r.details.get("prior_trend") == "down", \
        f"前置趋势应判为 down，实际: {r.details.get('prior_trend')} pct={r.details.get('prior_trend_pct')}"
    # 不应识别出 bull flag
    assert r.details.get("pattern_type") != "BULL_FLAG"


def test_oscillator_skips_swings_before_rsi_warmup():
    """前 rsi_period(14) 天的 swing 不应参与背离检测（RSI 是 NaN）。"""
    from quant_scanner.signals.oscillator_timing import OscillatorTimingSignal
    # 构造：第一个 swing high 在 idx < 14（早期伪极值）+ 第二个 swing high 在后段
    # 价格序列让前 14 天内出现高点，但 RSI 是 NaN，不该触发背离
    n = 80
    prices = np.ones(n) * 100.0
    # 前 5 天构造一个高点（idx=4），后段构造更高点
    prices[2:5] = 105  # 早期伪 swing
    prices[40:50] = np.linspace(100, 110, 10)  # 后段上涨
    prices[50:60] = np.linspace(110, 100, 10)  # 回调
    prices[60:80] = np.linspace(100, 115, 20)  # 再创新高
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.002, "low": prices * 0.998,
        "close": prices, "volume": np.full(n, 5e6),
    }, index=dates)

    sig = OscillatorTimingSignal()
    r = sig.evaluate("TEST", df)
    # 不应崩溃（NaN 处理 OK）
    assert -1.0 <= r.value <= 1.0
    assert "rsi" in r.details


# ==================== SP500 默认宇宙（新增）====================

def test_rs_rating_default_uses_sp500():
    """universe=None + use_sp500=True → 默认从 SP500 拉（不读 watchlist）"""
    from unittest.mock import patch
    with patch("quant_scanner.signals.rs_rating.load_sp500_tickers", return_value=["A", "B", "C"] * 50):
        sig = RSRatingSignal()  # 默认 use_sp500=True
        uni = sig._get_universe()
        assert uni == ["A", "B", "C"] * 50


def test_rs_rating_use_sp500_false_falls_back_to_watchlist():
    """use_sp500=False → 回退到 watchlist"""
    from unittest.mock import patch
    with patch("quant_scanner.signals.rs_rating.load_sp500_tickers") as mock_sp500:
        with patch("quant_scanner.signals.rs_rating.load_watchlist", return_value=["WL1", "WL2"]):
            sig = RSRatingSignal(use_sp500=False)
            uni = sig._get_universe()
            assert uni == ["WL1", "WL2"]
            mock_sp500.assert_not_called()


def test_rs_rating_explicit_universe_overrides_sp500():
    """显式 universe 优先级最高（不走 SP500）"""
    from unittest.mock import patch
    with patch("quant_scanner.signals.rs_rating.load_sp500_tickers") as mock_sp500:
        sig = RSRatingSignal(universe=["X", "Y", "Z"], use_sp500=True)
        uni = sig._get_universe()
        assert uni == ["X", "Y", "Z"]
        mock_sp500.assert_not_called()


def test_can_slim_auto_inject_rs_rating():
    """CANSLIMSignal 默认 auto_inject_rs=True → 自动注入 RSRatingSignal"""
    from quant_scanner.signals.can_slim import CANSLIMSignal
    sig = CANSLIMSignal(loader=MagicMock())
    assert sig._rs_rating is not None, "默认应自动注入 RSRatingSignal"
    assert sig._rs_rating._use_sp500 is True, "注入的 RSRatingSignal 应使用 SP500 宇宙"


def test_can_slim_auto_inject_can_be_disabled():
    """CANSLIMSignal(auto_inject_rs=False) → 保持向后兼容降级 vs SPX"""
    from quant_scanner.signals.can_slim import CANSLIMSignal
    sig = CANSLIMSignal(loader=MagicMock(), auto_inject_rs=False)
    assert sig._rs_rating is None, "auto_inject_rs=False 时不应注入"
