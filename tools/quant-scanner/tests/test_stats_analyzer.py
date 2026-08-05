"""前瞻标签 + 聚合统计 + Wilson + 分层 + holdout 测试"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quant_scanner.stats.analyzer import (
    ForwardLabel,
    SliceStats,
    HoldoutSplit,
    wilson_interval,
    compute_forward_labels,
    aggregate,
    split_holdout,
    _confidence_tier,
    HORIZON_DAYS,
    DEFAULT_MIN_TRADE_DAYS,
)
from quant_scanner.stats.events import SignalEvent


def _make_event(ticker="A", event_id="e1", signal_name="major_reversal",
                event_type="DOUBLE_BOTTOM", direction="long",
                confirmed_date=None, entry_date=None) -> SignalEvent:
    # 绕过 auto-id
    ev = SignalEvent(
        ticker=ticker, signal_name=signal_name, event_type=event_type,
        direction=direction, confirmed_date=confirmed_date, entry_date=entry_date,
    )
    ev.event_id = event_id
    return ev


def _make_df(n=100, start="2024-01-01"):
    rng = np.random.default_rng(0)
    idx = pd.date_range(start, periods=n, freq="B")
    prices = 100 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": rng.integers(1e6, 1e7, size=n),
    }, index=idx)


# ----- Wilson 区间 -----

def test_wilson_zero_n():
    lo, hi = wilson_interval(0, 0)
    assert lo == 0.0 and hi == 1.0


def test_wilson_all_wins():
    lo, hi = wilson_interval(10, 10)
    assert 0.5 < lo < 1.0
    assert hi == 1.0


def test_wilson_all_losses():
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0
    assert 0 < hi < 0.5


def test_wilson_symmetry():
    lo1, hi1 = wilson_interval(8, 10)
    lo2, hi2 = wilson_interval(2, 10)
    # 8/10 和 2/10 关于 0.5 对称（Wilson 是对称的）
    assert abs((1 - hi2) - lo1) < 0.01 or abs((1 - lo2) - hi1) < 0.01


# ----- 置信分层 -----

def test_confidence_tier():
    assert _confidence_tier(5) == "grey"
    assert _confidence_tier(20) == "exploratory"
    assert _confidence_tier(49) == "exploratory"
    assert _confidence_tier(50) == "low"
    assert _confidence_tier(199) == "low"
    assert _confidence_tier(200) == "default"
    assert _confidence_tier(1000) == "default"


# ----- 前瞻标签 -----

def test_forward_label_basic():
    df = _make_df()
    ev = _make_event(
        confirmed_date=df.index[10],
        entry_date=df.index[11],
    )
    labels = compute_forward_labels([ev], {"A": df}, horizons=(5,))
    assert len(labels) == 1
    lbl = labels[0]
    assert lbl.status == "ok"
    assert lbl.entry_price is not None
    assert lbl.exit_price is not None
    assert lbl.forward_return is not None
    # entry_price 应该是 entry_date 的 open × (1+slippage)
    expected_entry = float(df["open"].iloc[11]) * 1.001
    assert abs(lbl.entry_price - expected_entry) < 1e-6


def test_forward_label_censored_when_horizon_exceeds_data():
    df = _make_df(n=20)
    ev = _make_event(
        confirmed_date=df.index[15],
        entry_date=df.index[16],
    )
    labels = compute_forward_labels([ev], {"A": df}, horizons=(20,))
    assert len(labels) == 1
    assert labels[0].status == "censored"
    assert labels[0].forward_return is None


def test_forward_label_no_data_when_ticker_missing():
    ev = _make_event()
    labels = compute_forward_labels([ev], {}, horizons=(5,))
    assert labels[0].status == "no_data"


def test_forward_label_holdout_boundary_censored():
    """事件在前段，前瞻跨入 holdout → censored（共识第 6 条）"""
    df = _make_df(n=100)
    holdout_start = df.index[80]
    ev = _make_event(
        confirmed_date=df.index[75],
        entry_date=df.index[76],
    )
    # horizon=20 → exit = 76+20 = 96，跨过 holdout_start=80
    labels = compute_forward_labels(
        [ev], {"A": df}, horizons=(20,), holdout_start=holdout_start,
    )
    assert labels[0].status == "censored"


def test_forward_label_holdout_boundary_ok_when_within_train():
    df = _make_df(n=100)
    holdout_start = df.index[80]
    ev = _make_event(
        confirmed_date=df.index[10],
        entry_date=df.index[11],
    )
    # horizon=5 → exit=16，远在 holdout 之前
    labels = compute_forward_labels(
        [ev], {"A": df}, horizons=(5,), holdout_start=holdout_start,
    )
    assert labels[0].status == "ok"


def test_forward_label_mae_mfe_computed():
    df = _make_df(n=30)
    ev = _make_event(
        confirmed_date=df.index[5],
        entry_date=df.index[6],
    )
    labels = compute_forward_labels([ev], {"A": df}, horizons=(5,))
    assert labels[0].mae is not None
    assert labels[0].mfe is not None
    assert labels[0].mae <= 0  # MAE ≤ 0
    assert labels[0].mfe >= 0  # MFE ≥ 0


def test_forward_labels_multi_horizons():
    df = _make_df(n=80)
    ev = _make_event(
        confirmed_date=df.index[5],
        entry_date=df.index[6],
    )
    labels = compute_forward_labels([ev], {"A": df}, horizons=(5, 20, 60))
    assert len(labels) == 3
    horizons = sorted(l.horizon for l in labels)
    assert horizons == [5, 20, 60]


# ----- 聚合 -----

def test_aggregate_default_sort_by_ev_desc():
    df = _make_df(n=100)
    # 构造 2 组事件：A 全涨、B 全跌
    events = []
    for i in range(10):
        ev_a = _make_event(
            ticker="A", event_id=f"a{i}",
            confirmed_date=df.index[10 + i], entry_date=df.index[11 + i],
        )
        events.append(ev_a)
    # B 走跌势
    df_b = _make_df(n=100)
    df_b_idx = df_b.index
    for i in range(10):
        ev_b = _make_event(
            ticker="B", event_id=f"b{i}",
            event_type="DOUBLE_TOP", direction="short",
            confirmed_date=df_b_idx[10 + i], entry_date=df_b_idx[11 + i],
        )
        events.append(ev_b)

    labels = compute_forward_labels(events, {"A": df, "B": df_b}, horizons=(5,))
    results = aggregate(events, labels, horizon=5, by="event_type")
    assert len(results) >= 2
    # 验证按 EV 降序
    evs = [r.ev for r in results]
    assert evs == sorted(evs, reverse=True)


def test_aggregate_by_signal_collapses_event_types():
    df = _make_df(n=100)
    events = []
    for i in range(5):
        events.append(_make_event(
            ticker="A", event_id=f"a{i}", event_type="DOUBLE_BOTTOM",
            confirmed_date=df.index[10 + i], entry_date=df.index[11 + i],
        ))
    for i in range(5):
        events.append(_make_event(
            ticker="A", event_id=f"b{i}", event_type="HEAD_SHOULDERS_BOTTOM",
            confirmed_date=df.index[20 + i], entry_date=df.index[21 + i],
        ))
    labels = compute_forward_labels(events, {"A": df}, horizons=(5,))
    by_signal = aggregate(events, labels, horizon=5, by="signal")
    # 按 signal 维度：major_reversal 整体，10 笔
    assert len(by_signal) == 1
    assert by_signal[0].n == 10


def test_aggregate_censored_counted_separately():
    df = _make_df(n=30)
    events = []
    # 5 笔有效
    for i in range(5):
        events.append(_make_event(
            event_id=f"ok{i}",
            confirmed_date=df.index[5 + i], entry_date=df.index[6 + i],
        ))
    # 1 笔 horizon 超界
    events.append(_make_event(
        event_id="censored1",
        confirmed_date=df.index[20], entry_date=df.index[21],
    ))
    labels = compute_forward_labels(events, {"A": df}, horizons=(10,))
    results = aggregate(events, labels, horizon=10, by="event_type")
    assert len(results) == 1
    assert results[0].n == 5  # 有效样本
    assert results[0].n_censored == 1


def test_aggregate_confidence_tier_assigned():
    df = _make_df(n=200)
    events = []
    for i in range(15):  # < 20
        events.append(_make_event(
            event_id=f"e{i}",
            confirmed_date=df.index[10 + i], entry_date=df.index[11 + i],
        ))
    labels = compute_forward_labels(events, {"A": df}, horizons=(5,))
    results = aggregate(events, labels, horizon=5)
    assert results[0].n == 15
    assert results[0].confidence_tier == "grey"


def test_aggregate_wilson_interval_in_result():
    df = _make_df(n=200)
    events = []
    for i in range(50):
        events.append(_make_event(
            event_id=f"e{i}",
            confirmed_date=df.index[10 + i], entry_date=df.index[11 + i],
        ))
    labels = compute_forward_labels(events, {"A": df}, horizons=(5,))
    results = aggregate(events, labels, horizon=5)
    assert 0 <= results[0].win_rate_low <= results[0].win_rate <= results[0].win_rate_high <= 1


# ----- Holdout 切分 -----

def test_holdout_split_basic():
    df = _make_df(n=300)
    idx = pd.to_datetime(df.index)
    events = [
        _make_event(event_id="e1", confirmed_date=idx[10], entry_date=idx[11]),
        _make_event(event_id="e2", confirmed_date=idx[10], entry_date=idx[11]),
        _make_event(event_id="e3", confirmed_date=idx[250], entry_date=idx[251]),
    ]
    split = split_holdout(events, idx, holdout_months=3, min_trade_days=60)
    # 按 entry_date 归属（review High 2 修正）
    # e1/e2 entry=idx[11]，e3 entry=idx[251]
    assert len(split.train_events) == 2
    assert len(split.holdout_events) == 1
    assert split.holdout_events[0].event_id == "e3"


def test_holdout_split_by_entry_date_not_confirmed_date():
    """review High 2：holdout 按 entry_date 而非 confirmed_date 切分

    场景：confirmed_date 在 train 段末尾、entry_date 跨入 holdout 段
    → 应归入 holdout
    """
    df = _make_df(n=300)
    idx = pd.to_datetime(df.index)
    # 构造 holdout_start 在 confirmed 和 entry 之间
    # split_holdout 内部按 holdout_months=3 取最后 63 个交易日作为 holdout
    # idx[237:] 是 holdout（300-63=237）
    # 事件 confirmed=idx[230] entry=idx[231]：两者都在 train 段
    # 事件 confirmed=idx[235] entry=idx[240]：confirmed 在 train、entry 在 holdout
    e_in_train_by_conf_in_holdout_by_entry = _make_event(
        event_id="boundary", confirmed_date=idx[235], entry_date=idx[240],
    )
    split = split_holdout([e_in_train_by_conf_in_holdout_by_entry], idx, holdout_months=3, min_trade_days=60)
    # 按 entry_date 归属，应进 holdout
    assert len(split.holdout_events) == 1
    assert split.holdout_events[0].event_id == "boundary"


def test_holdout_split_zero_months_returns_all_train():
    df = _make_df(n=100)
    idx = pd.to_datetime(df.index)
    events = [_make_event(event_id="e1", confirmed_date=idx[10])]
    split = split_holdout(events, idx, holdout_months=0)
    assert len(split.train_events) == 1
    assert len(split.holdout_events) == 0
    assert split.holdout_start is None


def test_holdout_split_min_trade_days_check():
    df = _make_df(n=50)
    idx = pd.to_datetime(df.index)
    events = [_make_event(event_id="e1", confirmed_date=idx[10])]
    split = split_holdout(events, idx, holdout_months=12, min_trade_days=100)
    # 样本不足，train_satisfied=False
    assert split.train_satisfied is False
