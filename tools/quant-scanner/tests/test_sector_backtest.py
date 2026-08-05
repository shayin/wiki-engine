"""板块启动信号历史回测（PIT 事件采集）测试。

覆盖：
- 空输入 / 数据不足边界
- 6 种 event_type 各自触发条件正确
- 严格 vs 宽松突破
- cooldown 去重
- PIT 不泄露未来（暴涨日前无事件）
- entry_date = t+1
- SPY 对齐（长度不一致）
"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_scanner.features.sector import (
    collect_sector_launch_events,
    compute_market_regime,
    is_breakout_20d,
)


def _make_df(closes: list[float], volumes: list[float] | None = None, start: str = "2023-01-01") -> pd.DataFrame:
    """构造 OHLCV（open=high=low=close，便于精确控制突破判定）。"""
    n = len(closes)
    dates = pd.bdate_range(start, periods=n)
    s = pd.Series(closes, index=dates, dtype=float)
    v = pd.Series(volumes if volumes is not None else [1000.0] * n, index=dates, dtype=float)
    return pd.DataFrame(
        {"open": s.values, "high": s.values, "low": s.values, "close": s.values, "volume": v.values},
        index=dates,
    )


# =====================================================================
# 边界
# =====================================================================


def test_empty_etf_returns_empty():
    spy = _make_df([100] * 100)
    assert collect_sector_launch_events("X", pd.DataFrame(), spy) == []


def test_empty_spy_returns_empty():
    df = _make_df([100] * 100)
    assert collect_sector_launch_events("X", df, pd.DataFrame()) == []


def test_insufficient_history_returns_empty():
    """数据长度 < min_history 返回空。"""
    df = _make_df([100] * 50)
    spy = _make_df([100] * 50)
    assert collect_sector_launch_events("X", df, spy, min_history=66) == []


# =====================================================================
# 触发条件正确性
# =====================================================================


def test_breakout_launch_strict_on_surge_day():
    """第 26 天暴涨+放量+RS升 → 6 种 event_type 全触发于该日。"""
    # 前 25 天平稳 100，第 26 天（idx=25）涨到 110 + 放量
    closes = [100.0] * 25 + [110.0] * 75
    volumes = [1000.0] * 25 + [2000.0] * 75  # 2000 > 1000*1.5
    df = _make_df(closes, volumes)
    spy = _make_df([100.0] * 100)  # SPY 恒定 → RS 跟 ETF 走，暴涨日 RS 上升
    surge_date = df.index[25]

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=20)
    types_at_surge = {e.event_type for e in evs if e.confirmed_date == surge_date}

    # 6 种全应在暴涨日触发
    assert "LAUNCH_STRICT" in types_at_surge
    assert "LAUNCH_LOOSE" in types_at_surge
    assert "BREAKOUT" in types_at_surge
    assert "BREAKOUT_VOL" in types_at_surge
    assert "VOLUME_SURGE" in types_at_surge
    assert "RS_RISING" in types_at_surge
    # 全 long
    assert all(e.direction == "long" for e in evs)
    assert all(e.signal_name == "sector_launch" for e in evs)


def test_loose_vs_strict_breakout():
    """close 跌到 99（前高 100）：宽松通过(99>=98)，严格不通过(99>100 False)。

    LAUNCH_LOOSE / RS_RISING 触发；BREAKOUT / LAUNCH_STRICT / BREAKOUT_VOL 不触发。
    """
    # 前 25 天 close=100，第 26 天 close=99（小回调，宽松突破仍认）
    closes_etf = [100.0] * 25 + [99.0] + [99.0] * 74
    volumes = [1000.0] * 25 + [2000.0] + [1000.0] * 74  # 第26天放量
    df = _make_df(closes_etf, volumes)
    # SPY 第 26 天跌更多（90）→ RS=99/90=1.1 > 1.0 上升
    closes_spy = [100.0] * 25 + [90.0] + [90.0] * 74
    spy = _make_df(closes_spy)
    loose_date = df.index[25]

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    types_at_loose = {e.event_type for e in evs if e.confirmed_date == loose_date}

    assert "LAUNCH_LOOSE" in types_at_loose  # 宽松通过
    assert "RS_RISING" in types_at_loose
    assert "VOLUME_SURGE" in types_at_loose
    # 严格突破不成立 → 这三个不应在
    assert "BREAKOUT" not in types_at_loose
    assert "LAUNCH_STRICT" not in types_at_loose
    assert "BREAKOUT_VOL" not in types_at_loose


def test_volume_surge_without_breakout():
    """横盘 + 单日放量（无突破、RS 平）→ 仅 VOLUME_SURGE。"""
    closes = [100.0] * 100  # 完全横盘
    volumes = [1000.0] * 25 + [2000.0] + [1000.0] * 74  # 第 26 天放量
    df = _make_df(closes, volumes)
    spy = _make_df([100.0] * 100)  # RS 恒定 → 不 rising

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    types = {e.event_type for e in evs}
    assert "VOLUME_SURGE" in types
    # 横盘无突破
    assert "BREAKOUT" not in types
    assert "LAUNCH_STRICT" not in types
    # RS 恒定不上升
    assert "RS_RISING" not in types


# =====================================================================
# cooldown 去重
# =====================================================================


def test_cooldown_dedup_constant_uptrend():
    """严格递增序列：每天都满足 BREAKOUT + RS_RISING，cooldown=20 → 每 20 天 1 个。

    构造 close = 100 + t*0.1（每日创新高），SPY 恒定，vol 恒定（不放量）。
    """
    n = 100
    closes = [100.0 + t * 0.1 for t in range(n)]
    df = _make_df(closes)  # vol 默认 1000 → 不放量
    spy = _make_df([100.0] * n)

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=20)
    breakouts = sorted(e.confirmed_date for e in evs if e.event_type == "BREAKOUT")
    # t in [25,99]，cooldown=20 → t=25,45,65,85 → 4 个
    assert len(breakouts) == 4
    # 相邻间隔 ≥ 20 交易日
    for prev, cur in zip(breakouts, breakouts[1:]):
        busdays = len(pd.bdate_range(prev, cur, inclusive="left"))
        assert busdays >= 20
    # 不放量 → LAUNCH_* / BREAKOUT_VOL / VOLUME_SURGE 不触发
    types = {e.event_type for e in evs}
    assert "VOLUME_SURGE" not in types
    assert "LAUNCH_STRICT" not in types
    assert "BREAKOUT_VOL" not in types


def test_cooldown_zero_allows_dense_events():
    """cooldown=0 → 每个满足条件的交易日都产生事件。"""
    n = 40
    closes = [100.0 + t * 0.1 for t in range(n)]
    df = _make_df(closes)
    spy = _make_df([100.0] * n)

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=0)
    breakouts = [e for e in evs if e.event_type == "BREAKOUT"]
    # t in [25,39] 共 15 天，每天都触发
    assert len(breakouts) == 15


# =====================================================================
# PIT 不泄露未来 + entry_date
# =====================================================================


def test_no_future_leakage_before_surge():
    """第 50 天才暴涨 → 所有 BREAKOUT 事件 confirmed_date >= 第 50 天。

    验证暴涨前不会因"看到未来"而提前触发。
    """
    closes = [100.0] * 49 + [120.0] + [120.0] * 50
    df = _make_df(closes)
    spy = _make_df([100.0] * 100)
    surge_date = df.index[49]

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    breakouts = [e for e in evs if e.event_type == "BREAKOUT"]
    assert len(breakouts) == 1
    assert breakouts[0].confirmed_date == surge_date


def test_entry_date_is_next_trading_day():
    """entry_date = confirmed_date 的下一个交易日。"""
    closes = [100.0] * 25 + [110.0] * 75
    df = _make_df(closes, volumes=[1000.0] * 25 + [2000.0] * 75)
    spy = _make_df([100.0] * 100)

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    launch = next(e for e in evs if e.event_type == "LAUNCH_STRICT")
    # entry_date 应是 confirmed_date 的下一个 bday
    expected_next = launch.confirmed_date + pd.tseries.offsets.BDay()
    assert launch.entry_date == expected_next


def test_last_day_event_has_no_entry_date():
    """最后一天触发的事件 entry_date=None（无后续交易日）。"""
    # 前 99 天平稳，最后一天（idx=99）暴涨
    closes = [100.0] * 99 + [110.0]
    df = _make_df(closes, volumes=[1000.0] * 99 + [2000.0])
    spy = _make_df([100.0] * 100)

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    last_date = df.index[99]
    events_last = [e for e in evs if e.confirmed_date == last_date]
    assert len(events_last) > 0
    assert all(e.entry_date is None for e in events_last)


# =====================================================================
# SPY 对齐
# =====================================================================


def test_spy_longer_than_etf_aligns_correctly():
    """SPY 比 ETF 长（前置多 20 天）→ dropna 对齐后按共有交易日采集，不报错。"""
    closes = [100.0] * 25 + [110.0] * 75
    df = _make_df(closes, volumes=[1000.0] * 25 + [2000.0] * 75, start="2023-01-23")
    # SPY 从更早开始，多 20 天
    spy = _make_df([100.0] * 120, start="2022-12-22")

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    # 暴涨日仍应触发 LAUNCH_STRICT
    surge_date = df.index[25]
    types_at_surge = {e.event_type for e in evs if e.confirmed_date == surge_date}
    assert "LAUNCH_STRICT" in types_at_surge


def test_spy_shorter_than_etf_aligns_correctly():
    """SPY 比 ETF 短（同起点、提前结束）→ 暴涨日在共有窗口内，仍能采集。"""
    closes = [100.0] * 25 + [110.0] * 95  # ETF 120 天
    df = _make_df(closes, volumes=[1000.0] * 25 + [2000.0] * 95, start="2023-01-23")
    spy = _make_df([100.0] * 100, start="2023-01-23")  # 同起点，100 天（短 20）

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    surge_date = df.index[25]
    types_at_surge = {e.event_type for e in evs if e.confirmed_date == surge_date}
    assert "LAUNCH_STRICT" in types_at_surge


# =====================================================================
# 数值与字段正确性（审查 G1/G2/G5）
# =====================================================================


def test_breakout_strict_excludes_equal_high():
    """close 恒定 → close_now == prior_20_high，严格 > 不触发 BREAKOUT。

    防御：若误改成 >= 会通过此测试被抓。
    """
    df = _make_df([100.0] * 100)
    spy = _make_df([100.0] * 100)
    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    assert "BREAKOUT" not in {e.event_type for e in evs}


def test_details_whitelist_populated_correctly():
    """details_whitelist 的 rs_1m_chg/breakout_strict/volume_surge/close 正确写入。"""
    closes = [100.0] * 25 + [110.0] * 75
    df = _make_df(closes, volumes=[1000.0] * 25 + [2000.0] * 75)
    spy = _make_df([100.0] * 100)
    surge_date = df.index[25]

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    launch = next(e for e in evs if e.event_type == "LAUNCH_STRICT" and e.confirmed_date == surge_date)
    d = launch.details_whitelist
    assert d["breakout_strict"] is True
    assert d["volume_surge"] is True
    assert d["close"] == 110.0
    # rs_1m_chg 应为 +10%（ETF 100→110，SPY 恒定 100，RS 1.0→1.1）
    assert abs(d["rs_1m_chg"] - 10.0) < 0.01


def test_rs_1m_chg_value_precision():
    """rs_1m_chg 数值正确：SPY 跌、ETF 涨 → RS 变化可解析。"""
    # ETF 第 26 天 100→110，SPY 第 26 天 100→95 → RS 1.0→1.158，约 +15.79%
    closes_etf = [100.0] * 25 + [110.0] * 75
    closes_spy = [100.0] * 25 + [95.0] * 75
    df = _make_df(closes_etf, volumes=[1000.0] * 25 + [2000.0] * 75)
    spy = _make_df(closes_spy)
    surge_date = df.index[25]

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    rs_event = next(
        e for e in evs if e.event_type == "RS_RISING" and e.confirmed_date == surge_date
    )
    expected = (110.0 / 95.0) / (100.0 / 100.0) - 1  # RS_now/RS_22d_ago - 1
    assert abs(rs_event.details_whitelist["rs_1m_chg"] - expected * 100) < 0.01


# =====================================================================
# 端到端集成（审查 G7）
# =====================================================================


def test_end_to_end_forward_labels_win_on_uptrend():
    """LAUNCH 事件喂 compute_forward_labels，持续上涨场景 → forward_return > 0。"""
    from quant_scanner.stats.analyzer import compute_forward_labels

    # 前 25 天平稳，第 26 天暴涨 + 放量，之后继续缓慢上涨
    closes = [100.0] * 25 + [110.0 + i * 0.3 for i in range(75)]
    df = _make_df(closes, volumes=[1000.0] * 25 + [2000.0] * 75)
    spy = _make_df([100.0] * 100)

    evs = collect_sector_launch_events("TEST", df, spy, min_history=25, cooldown_days=200)
    launches = [e for e in evs if e.event_type == "LAUNCH_STRICT"]
    assert len(launches) > 0

    labels = compute_forward_labels(launches, {"TEST": df}, horizons=(20,))
    ok_labels = [l for l in labels if l.status == "ok"]
    assert len(ok_labels) > 0
    # 持续上涨 → 所有 ok 标签 forward_return > 0
    assert all(l.forward_return > 0 for l in ok_labels), [
        (l.status, l.forward_return) for l in labels
    ]


# =====================================================================
# is_breakout_20d 纯函数（compute_sector_rs 与 collect 共用）
# =====================================================================


def test_breakout_strict_new_high():
    """前 20 日最高 100，今天 110 → 触发。

    同时隐含验证窗口不含今天：若误含今天，max=110，110>110=False 会失败。
    """
    s = pd.Series([100.0] * 20 + [110.0])
    assert is_breakout_20d(s, strict=True) is True


def test_breakout_strict_equal_high_no_trigger():
    """close 恒定 → 今天 == 前 20 日最高，严格 > 不触发（防误改 >=）。"""
    s = pd.Series([100.0] * 21)
    assert is_breakout_20d(s, strict=True) is False


def test_breakout_strict_below_high_no_trigger():
    """今天 99 < 前 20 日最高 100 → 不触发。"""
    s = pd.Series([100.0] * 20 + [99.0])
    assert is_breakout_20d(s, strict=True) is False


def test_breakout_loose_near_high_triggers():
    """宽松：今天 99 >= 含今天 20 日最高 100 × 0.98 = 98 → 触发。"""
    s = pd.Series([100.0] * 20 + [99.0])
    assert is_breakout_20d(s, strict=False) is True


def test_breakout_loose_far_below_no_trigger():
    """宽松：今天 97 < 98 → 不触发。"""
    s = pd.Series([100.0] * 20 + [97.0])
    assert is_breakout_20d(s, strict=False) is False


def test_breakout_strict_insufficient_length():
    """strict 需 21 个数据点，不足回退 False。"""
    assert is_breakout_20d(pd.Series([100.0] * 20), strict=True) is False


def test_breakout_loose_insufficient_length():
    """loose 需 20 个数据点，不足回退 False。"""
    assert is_breakout_20d(pd.Series([100.0] * 19), strict=False) is False


def test_breakout_loose_frac_param():
    """frac 参数生效：今天 96，前高 100。
    frac=0.95 → 96 >= 95 触发；frac=0.98 → 96 < 98 不触发。
    """
    s = pd.Series([100.0] * 20 + [96.0])
    assert is_breakout_20d(s, strict=False, frac=0.95) is True
    assert is_breakout_20d(s, strict=False, frac=0.98) is False


# =====================================================================
# compute_market_regime（SPY MA50/MA200 分层）
# =====================================================================


def _make_spy(prices: list[float], start: str = "2023-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(prices))
    p = pd.Series(prices, index=dates, dtype=float)
    return pd.DataFrame(
        {"open": p.values, "high": p.values, "low": p.values, "close": p.values, "volume": [1e9] * len(p)},
        index=dates,
    )


def test_regime_bull_strong():
    """纯上升序列 → close > MA50 > MA200，无警告。"""
    r = compute_market_regime(_make_spy([100 + i * 0.1 for i in range(250)]))
    assert r["regime"] == "bull_strong"
    assert r["warning"] is None
    assert r["close"] > r["ma50"] > r["ma200"]


def test_regime_bear():
    """纯下降序列 → close < MA200，熊市警告。"""
    r = compute_market_regime(_make_spy([200 - i * 0.5 for i in range(250)]))
    assert r["regime"] == "bear"
    assert "熊市" in r["warning"]


def test_regime_bull_correction():
    """245 天上升 + 最后 5 天急跌 → close < MA50 但 > MA200，牛市回调提示。"""
    prices = [100 + i * 0.12 for i in range(245)] + [129.4, 127.0, 125.0, 122.0, 120.0]
    r = compute_market_regime(_make_spy(prices))
    assert r["regime"] == "bull_correction"
    assert "回调" in r["warning"]
    assert r["close"] < r["ma50"] and r["close"] > r["ma200"]


def test_regime_warmup_insufficient_data():
    """数据 < 200 天 → warmup。"""
    r = compute_market_regime(_make_spy([100.0] * 100))
    assert r["regime"] == "warmup"
    assert r["ma200"] is None
