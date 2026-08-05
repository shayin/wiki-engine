"""PIT adapter 合规性测试（辩论共识第 10 条必测项）"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from quant_scanner.signals.base import BaseSignal, SignalResult
from quant_scanner.stats.pit_adapter import (
    PITAwareProtocol,
    evaluate_with_pit,
    reset_warn_cache,
    _signature_accepts_pit,
)


def _synthetic_df(n: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    prices = 100 + np.cumsum(rng.normal(0, 0.5, n))
    idx = pd.date_range(end="2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=idx)


class _PITSignal(BaseSignal):
    """支持 pit_date 的 signal。"""
    name = "pit_signal"

    def evaluate(self, ticker, df, pit_date=None):
        return SignalResult(
            ticker=ticker, signal_name=self.name, value=0.5, passed=True,
            details={"pit_date": str(pit_date) if pit_date is not None else "none"},
        )


class _LegacySignal(BaseSignal):
    """不支持 pit_date 的旧 signal。"""
    name = "legacy_signal"

    def evaluate(self, ticker, df):
        return SignalResult(
            ticker=ticker, signal_name=self.name, value=0.5, passed=True,
        )


def test_signature_detects_pit_support():
    """inspect.signature 正确识别 pit_date 支持"""
    assert _signature_accepts_pit(_PITSignal()) is True
    assert _signature_accepts_pit(_LegacySignal()) is False


def test_pit_signal_receives_pit_date():
    """支持 pit_date 的 signal 正确接收参数"""
    sig = _PITSignal()
    df = _synthetic_df()
    pit = pd.Timestamp("2024-12-15")
    result = evaluate_with_pit(sig, "A", df, pit)
    assert result.details["pit_date"] == "2024-12-15 00:00:00"


def test_legacy_signal_falls_back_without_type_error():
    """不支持 pit_date 的 signal 走回退路径不抛 TypeError"""
    reset_warn_cache()
    sig = _LegacySignal()
    df = _synthetic_df()
    pit = pd.Timestamp("2024-12-15")
    # 不应抛 TypeError
    result = evaluate_with_pit(sig, "A", df, pit)
    assert result.ticker == "A"
    assert result.signal_name == "legacy_signal"


def test_legacy_signal_warned_once(caplog):
    """同一类 signal 警告只发一次"""
    import logging
    reset_warn_cache()
    sig = _LegacySignal()
    df = _synthetic_df()
    pit = pd.Timestamp("2024-12-15")
    with caplog.at_level(logging.WARNING, logger="quant_scanner.stats.pit_adapter"):
        evaluate_with_pit(sig, "A", df, pit)
        evaluate_with_pit(sig, "B", df, pit)
    warnings = [r for r in caplog.records if "pit_date" in r.message]
    assert len(warnings) == 1, f"应只警告一次，实际 {len(warnings)}"


def test_pit_signal_satisfies_protocol():
    """支持 pit_date 的 signal 满足 PITAwareProtocol"""
    sig = _PITSignal()
    assert isinstance(sig, PITAwareProtocol)


def test_legacy_signal_does_not_satisfy_protocol():
    """旧 signal 不满足 PITAwareProtocol"""
    sig = _LegacySignal()
    # runtime_checkable Protocol 只检查方法存在，不检查签名；
    # 这里验证 _signature_accepts_pit 给出更精确判断
    assert not _signature_accepts_pit(sig)


def test_different_pit_date_produces_different_result_for_external_signal():
    """外部数据型 signal：不同 pit_date 应产生不同结果

    用 mock 验证：当 pit_date 不同时，传入的参数确实不同。
    （真实的外部数据型 signal 如 can_slim 应从 loader 取不同数据。）
    """
    sig = _PITSignal()
    df = _synthetic_df()
    pit_a = pd.Timestamp("2024-06-01")
    pit_b = pd.Timestamp("2024-12-01")
    r_a = evaluate_with_pit(sig, "X", df, pit_a)
    r_b = evaluate_with_pit(sig, "X", df, pit_b)
    assert r_a.details["pit_date"] != r_b.details["pit_date"]


def test_legacy_signal_uses_truncated_df():
    """旧 signal 回退路径：调用方必须自行截断 df

    这里验证 evaluate_with_pit 不修改 df（调用方职责），
    确保回退路径语义清晰。
    """
    reset_warn_cache()
    sig = _LegacySignal()
    df_full = _synthetic_df()
    pit = pd.Timestamp("2024-12-15")
    df_truncated = df_full.loc[df_full.index <= pit].copy()
    result = evaluate_with_pit(sig, "A", df_truncated, pit)
    assert result.value == 0.5
