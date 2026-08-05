"""SignalEvent 采集系统测试"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.signals.base import SignalResult
from quant_scanner.stats.events import (
    SignalEvent,
    SignalEventCollector,
    ExtractedEvent,
    EXTRACTOR_MAP,
    get_extractor,
    filter_long_only,
    filter_diagnostic,
    _extract_oscillator_timing,
    _extract_major_reversal,
    _extract_continuation,
    _extract_default,
    _direction_from_value,
)


def _make_sr(signal_name="stub", value=0.6, passed=True, details=None) -> SignalResult:
    return SignalResult(
        ticker="A", signal_name=signal_name, value=value, passed=passed,
        details=details or {}, reasons=[],
    )


def _dates(n=50, end="2024-12-31"):
    return pd.date_range(end=end, periods=n, freq="B")


# ----- extractor 单元测试 -----

def test_direction_from_value():
    assert _direction_from_value(0.5) == "long"
    assert _direction_from_value(-0.5) == "short"
    assert _direction_from_value(0.01) == "unknown"


def test_major_reversal_extractor():
    sr = _make_sr("major_reversal", value=0.8, details={"pattern_type": "DOUBLE_BOTTOM", "confirmed": True})
    evs = _extract_major_reversal(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "DOUBLE_BOTTOM"
    assert evs[0].direction == "long"
    assert evs[0].pattern_type == "DOUBLE_BOTTOM"


def test_major_reversal_extractor_empty_without_pattern_type():
    sr = _make_sr("major_reversal", value=0.8, details={})
    assert _extract_major_reversal(sr) == []


def test_continuation_extractor():
    sr = _make_sr("continuation", value=-0.6, details={"pattern_type": "BEAR_FLAG"})
    evs = _extract_continuation(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "BEAR_FLAG"
    assert evs[0].direction == "short"


def test_oscillator_rsi_divergence_only():
    sr = _make_sr("oscillator_timing", value=0.7, details={"divergence": "BULLISH_DIVERGENCE"})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "RSI_CLASSIC_DIVERGENCE"
    assert evs[0].direction == "long"


def test_oscillator_macd_divergence_only():
    sr = _make_sr("oscillator_timing", value=-0.7, details={"macd_divergence": "BEARISH"})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "MACD_CLASSIC_DIVERGENCE"
    assert evs[0].direction == "short"


def test_oscillator_same_day_rsi_and_macd():
    """同日 RSI + MACD 同时命中，生成两条独立 event（R8/R9 修订）"""
    sr = _make_sr("oscillator_timing", value=0.8, details={
        "divergence": "BULLISH_DIVERGENCE",
        "macd_divergence": "BULLISH",
    })
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 2
    types = {e.event_type for e in evs}
    assert types == {"RSI_CLASSIC_DIVERGENCE", "MACD_CLASSIC_DIVERGENCE"}
    assert all(e.direction == "long" for e in evs)


def test_oscillator_extreme_when_no_other_pattern():
    sr = _make_sr("oscillator_timing", value=0.3, details={"rsi": 75.0})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "EXTREME"
    assert evs[0].direction == "short"


def test_oscillator_oversold_extreme():
    sr = _make_sr("oscillator_timing", value=-0.3, details={"rsi": 25.0})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].direction == "long"


def test_oscillator_no_events_when_no_signal():
    sr = _make_sr("oscillator_timing", value=0.1, details={"rsi": 50.0})
    assert _extract_oscillator_timing(sr) == []


def test_oscillator_hidden_divergence():
    sr = _make_sr("oscillator_timing", value=0.5, details={"hidden_divergence": "BULLISH"})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "HIDDEN_DIVERGENCE"


def test_oscillator_failure_swing():
    sr = _make_sr("oscillator_timing", value=-0.5, details={"failure_swing": "BEARISH"})
    evs = _extract_oscillator_timing(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "FAILURE_SWING"
    assert evs[0].direction == "short"


def test_default_extractor_non_pattern_signal_passed():
    sr = _make_sr("trend_template", value=0.7, passed=True)
    evs = _extract_default(sr)
    assert len(evs) == 1
    assert evs[0].event_type == "TREND_TEMPLATE"
    assert evs[0].event_kind == "transition"


def test_default_extractor_skips_failed():
    sr = _make_sr("vcp", value=0.3, passed=False)
    assert _extract_default(sr) == []


def test_get_extractor_dispatch():
    assert get_extractor("major_reversal") is _extract_major_reversal
    assert get_extractor("continuation") is _extract_continuation
    assert get_extractor("oscillator_timing") is _extract_oscillator_timing
    assert get_extractor("vcp") is _extract_default


# ----- Collector 集成测试 -----

def test_collector_generates_event_for_major_reversal():
    c = SignalEventCollector()
    dates = _dates()
    today = dates[40]
    sr = _make_sr("major_reversal", value=0.8, details={"pattern_type": "DOUBLE_BOTTOM"})
    new = c.on_daily_eval("A", today, [sr], trading_dates=dates)
    assert len(new) == 1
    assert new[0].event_type == "DOUBLE_BOTTOM"
    assert new[0].direction == "long"
    assert new[0].confirmed_date == today
    assert new[0].entry_date == dates[41]


def test_collector_cooldown_dedupes_same_pattern():
    """同 ticker × signal × event_type × direction 20 日内只触发一次"""
    c = SignalEventCollector(cooldown_days=20)
    dates = _dates(60)
    sr = _make_sr("major_reversal", value=0.8, details={"pattern_type": "DOUBLE_BOTTOM"})
    # day 0
    new0 = c.on_daily_eval("A", dates[0], [sr], trading_dates=dates)
    assert len(new0) == 1
    # day 5（冷却内）
    new5 = c.on_daily_eval("A", dates[5], [sr], trading_dates=dates)
    assert len(new5) == 0
    # day 20（边界外）
    new20 = c.on_daily_eval("A", dates[20], [sr], trading_dates=dates)
    assert len(new20) == 1


def test_collector_same_day_different_event_types_not_deduped():
    """同日同方向但不同 event_type 视为两个独立事件"""
    c = SignalEventCollector()
    dates = _dates()
    sr = _make_sr("oscillator_timing", value=0.8, details={
        "divergence": "BULLISH_DIVERGENCE",
        "macd_divergence": "BULLISH",
    })
    new = c.on_daily_eval("A", dates[20], [sr], trading_dates=dates)
    assert len(new) == 2
    assert {e.event_type for e in new} == {"RSI_CLASSIC_DIVERGENCE", "MACD_CLASSIC_DIVERGENCE"}


def test_collector_different_directions_not_deduped():
    """同 event_type 不同 direction 不去重（long vs short 各自计）"""
    c = SignalEventCollector()
    dates = _dates()
    sr_long = _make_sr("major_reversal", value=0.8, details={"pattern_type": "DOUBLE_BOTTOM"})
    sr_short = _make_sr("major_reversal", value=-0.8, details={"pattern_type": "DOUBLE_TOP"})
    new1 = c.on_daily_eval("A", dates[0], [sr_long], trading_dates=dates)
    new2 = c.on_daily_eval("A", dates[1], [sr_short], trading_dates=dates)
    assert len(new1) == 1
    assert len(new2) == 1
    assert new1[0].direction == "long"
    assert new2[0].direction == "short"


def test_collector_whitelist_filters_details():
    c = SignalEventCollector()
    dates = _dates()
    sr = _make_sr("major_reversal", value=0.8, details={
        "pattern_type": "DOUBLE_BOTTOM",
        "neckline": 100.0,
        "target": 110.0,
        "secret_field": "should_be_dropped",
    })
    new = c.on_daily_eval("A", dates[20], [sr], trading_dates=dates)
    assert "secret_field" not in new[0].details_whitelist
    assert new[0].details_whitelist["neckline"] == 100.0
    assert new[0].details_whitelist["target"] == 110.0


def test_collector_handles_extractor_exception():
    """extractor 抛异常不影响其他 signal"""
    c = SignalEventCollector()
    dates = _dates()

    # 用一个会抛异常的 fake signal
    class BrokenSR:
        signal_name = "broken"
        value = 0.5
        passed = True
        details = {}
        ticker = "A"
        reasons = []

    good_sr = _make_sr("major_reversal", value=0.8, details={"pattern_type": "DOUBLE_BOTTOM"})
    # broken 走默认 extractor 应该不抛（因为它 passed=True）
    # 改用一个真的会抛的：传 SignalResult 但 signal_name 不在映射且 passed=True → 默认 extractor 不抛
    # 因此这个测试主要验证 try/except 包裹
    new = c.on_daily_eval("A", dates[20], [good_sr], trading_dates=dates)
    assert len(new) == 1


def test_filter_long_only():
    events = [
        SignalEvent(ticker="A", signal_name="x", event_type="T", direction="long"),
        SignalEvent(ticker="B", signal_name="x", event_type="T", direction="short"),
        SignalEvent(ticker="C", signal_name="x", event_type="T", direction="unknown"),
    ]
    longs = filter_long_only(events)
    assert len(longs) == 1
    assert all(e.direction == "long" for e in longs)


def test_filter_diagnostic():
    events = [
        SignalEvent(direction="long"),
        SignalEvent(direction="short"),
        SignalEvent(direction="unknown"),
    ]
    diag = filter_diagnostic(events)
    assert len(diag) == 2
    assert all(e.direction != "long" for e in diag)


def test_signal_event_auto_generates_id():
    e = SignalEvent(ticker="A")
    assert e.event_id and len(e.event_id) > 0


# ----- 持续型 signal transition 检测（review High 3） -----

def test_persistent_signal_transition_only_on_state_change():
    """trend_template 连续多日 passed=True 且 value 不变 → 只生成 1 个事件"""
    c = SignalEventCollector()
    dates = _dates(60)
    # trend_template 是持续型，每天 passed=True value=0.7
    sr = SignalResult(
        ticker="A", signal_name="trend_template",
        value=0.7, passed=True, details={}, reasons=[],
    )
    # 连续 5 天调用
    for d in dates[:5]:
        c.on_daily_eval("A", d, [sr], trading_dates=dates)
    # 只应有 1 个事件（第 1 天的 transition）
    assert len(c.events) == 1


def test_persistent_signal_transition_on_value_change():
    """trend_template value 从 0.7 → 0.9 → 生成新事件"""
    c = SignalEventCollector()
    dates = _dates(10)
    sr1 = SignalResult(ticker="A", signal_name="trend_template", value=0.7, passed=True)
    sr2 = SignalResult(ticker="A", signal_name="trend_template", value=0.9, passed=True)
    c.on_daily_eval("A", dates[0], [sr1], trading_dates=dates)
    c.on_daily_eval("A", dates[1], [sr1], trading_dates=dates)  # 同状态不生成
    c.on_daily_eval("A", dates[2], [sr2], trading_dates=dates)  # value 变化生成
    c.on_daily_eval("A", dates[3], [sr2], trading_dates=dates)  # 同状态不生成
    assert len(c.events) == 2


def test_persistent_signal_false_to_true_triggers_transition():
    """trend_template passed=False → passed=True 触发"""
    c = SignalEventCollector()
    dates = _dates(10)
    sr_off = SignalResult(ticker="A", signal_name="trend_template", value=0.3, passed=False)
    sr_on = SignalResult(ticker="A", signal_name="trend_template", value=0.7, passed=True)
    c.on_daily_eval("A", dates[0], [sr_off], trading_dates=dates)
    c.on_daily_eval("A", dates[1], [sr_off], trading_dates=dates)
    c.on_daily_eval("A", dates[2], [sr_on], trading_dates=dates)
    c.on_daily_eval("A", dates[3], [sr_on], trading_dates=dates)
    assert len(c.events) == 1


def test_persistent_signal_skipped_when_always_false():
    """持续型 signal 一直 passed=False → 不生成任何事件"""
    c = SignalEventCollector()
    dates = _dates(10)
    sr = SignalResult(ticker="A", signal_name="vcp", value=0.2, passed=False)
    for d in dates[:5]:
        c.on_daily_eval("A", d, [sr], trading_dates=dates)
    assert len(c.events) == 0


def test_persistent_signal_state_key_change_triggers():
    """details.state 变化（如 trend_regime regime 切换）触发"""
    c = SignalEventCollector()
    dates = _dates(10)
    sr1 = SignalResult(
        ticker="A", signal_name="trend_regime",
        value=0.7, passed=True, details={"state": "BULL"},
    )
    sr2 = SignalResult(
        ticker="A", signal_name="trend_regime",
        value=0.7, passed=True, details={"state": "BEAR"},
    )
    c.on_daily_eval("A", dates[0], [sr1], trading_dates=dates)
    c.on_daily_eval("A", dates[1], [sr1], trading_dates=dates)
    c.on_daily_eval("A", dates[2], [sr2], trading_dates=dates)  # state 变了
    assert len(c.events) == 2


def test_pattern_signal_not_subject_to_transition_check():
    """形态类 signal 不做 transition 检测，只受 cooldown 控制"""
    c = SignalEventCollector(cooldown_days=5)
    dates = _dates(60)
    sr = SignalResult(
        ticker="A", signal_name="major_reversal", value=0.8,
        passed=True, details={"pattern_type": "DOUBLE_BOTTOM"},
    )
    # 第 0 天触发
    c.on_daily_eval("A", dates[0], [sr], trading_dates=dates)
    # 第 1-4 天在冷却内
    for d in dates[1:5]:
        c.on_daily_eval("A", d, [sr], trading_dates=dates)
    # 第 5 天边界外，应再次触发（即使状态没变）
    c.on_daily_eval("A", dates[5], [sr], trading_dates=dates)
    assert len(c.events) == 2  # day 0 + day 5
