"""Meta-Labeling 测试（López de Prado AfML Ch 4）

覆盖：
- prepare_meta_labels：标量 vs Series side
- meta_target 二分类正确性（+1 → 1，其余 → 0）
- compute_position_size 标量与 Series
- meta_label_summary 统计
- 边界情况（短序列、空、单边方向）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.labels.meta_labeling import (
    MetaLabeler,
    meta_label_summary,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def upward_ohlcv() -> pd.DataFrame:
    """单边上涨的合成 OHLCV"""
    rng = np.random.default_rng(42)
    n = 200
    rets = rng.normal(0.002, 0.01, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.integers(1e6, 2e6, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


@pytest.fixture
def downward_ohlcv() -> pd.DataFrame:
    """单边下跌"""
    rng = np.random.default_rng(7)
    n = 200
    rets = rng.normal(-0.002, 0.01, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.integers(1e6, 2e6, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


# ============================================================
# prepare_meta_labels
# ============================================================

def test_meta_target_binary_values(upward_ohlcv):
    """meta_target 应为 0/1 二值"""
    labeler = MetaLabeler()
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=1)
    assert set(out["meta_target"].unique()).issubset({0, 1})
    assert (out["meta_target"] == (out["triple_label"] == 1).astype(int)).all()


def test_correct_side_high_precision(upward_ohlcv):
    """单边上涨 + 做多方向：precision 应较高"""
    labeler = MetaLabeler(tp_atr_mult=1.5, sl_atr_mult=1.5, vertical_barrier_bars=10)
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=1)
    summary = meta_label_summary(out)
    # 上涨序列做多应 > 50% 命中
    assert summary["precision"] > 0.5
    assert summary["total"] > 0


def test_wrong_side_low_precision(upward_ohlcv):
    """上涨序列做空方向：precision 应较低"""
    labeler = MetaLabeler(tp_atr_mult=1.5, sl_atr_mult=1.5, vertical_barrier_bars=10)
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=-1)
    summary = meta_label_summary(out)
    # 做空在上涨市应命中率低
    assert summary["precision"] < 0.5


def test_side_series_supported(upward_ohlcv):
    """primary_side 为 pd.Series 时应正常工作"""
    n = len(upward_ohlcv)
    # 前 100 bar 做多，后 100 bar 做空
    side = pd.Series(
        [1] * 100 + [-1] * 100,
        index=upward_ohlcv.index,
    )
    labeler = MetaLabeler(vertical_barrier_bars=5)
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=side)
    assert len(out) > 0
    assert "meta_target" in out.columns
    # side 应反映在结果里
    assert set(out["side"].unique()).issubset({-1, 1})


def test_columns_present(upward_ohlcv):
    """输出应含必要列"""
    labeler = MetaLabeler()
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=1)
    for col in ("t_in", "t_out", "entry_price", "exit_price",
                "triple_label", "meta_target", "ret", "side"):
        assert col in out.columns


# ============================================================
# compute_position_size
# ============================================================

def test_position_size_scalar():
    labeler = MetaLabeler()
    pos = labeler.compute_position_size(primary_side=1, confidence=0.8, max_position=1.0)
    assert abs(pos - 0.8) < 1e-9


def test_position_size_negative_side():
    labeler = MetaLabeler()
    pos = labeler.compute_position_size(primary_side=-1, confidence=0.6, max_position=1.0)
    assert pos < 0
    assert abs(pos - (-0.6)) < 1e-9


def test_position_size_series():
    labeler = MetaLabeler()
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    side = pd.Series([1, 1, -1, -1, 1], index=idx)
    conf = pd.Series([0.9, 0.5, 0.8, 0.3, 0.7], index=idx)
    pos = labeler.compute_position_size(side, conf, max_position=0.5)
    assert isinstance(pos, pd.Series)
    expected = side * conf * 0.5
    pd.testing.assert_series_equal(pos, expected)


def test_position_size_scalar_inputs_broadcast():
    """标量 side/confidence 也应返回标量"""
    labeler = MetaLabeler()
    pos = labeler.compute_position_size(primary_side=1, confidence=1.0)
    assert isinstance(pos, float)
    assert abs(pos - 1.0) < 1e-9


# ============================================================
# meta_label_summary
# ============================================================

def test_summary_empty():
    s = meta_label_summary(pd.DataFrame(columns=[
        "meta_target", "barrier_hit"
    ]))
    assert s["total"] == 0
    assert s["precision"] == 0.0


def test_summary_counts(upward_ohlcv):
    labeler = MetaLabeler()
    out = labeler.prepare_meta_labels(upward_ohlcv, primary_side=1)
    s = meta_label_summary(out)
    assert s["total"] == len(out)
    assert s["primary_correct"] + s["primary_wrong"] == s["total"]
    assert abs(s["precision"] - s["primary_correct"] / s["total"]) < 1e-9
    assert "by_barrier" in s
    # by_barrier 比例和应 ≈ 1
    assert sum(s["by_barrier"].values()) == pytest.approx(1.0, abs=1e-6)


# ============================================================
# 边界情况
# ============================================================

def test_short_series_no_crash():
    n = 15
    df = pd.DataFrame({
        "high": np.linspace(100, 110, n),
        "low": np.linspace(99, 109, n),
        "close": np.linspace(99.5, 109.5, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    labeler = MetaLabeler(vertical_barrier_bars=5)
    out = labeler.prepare_meta_labels(df, primary_side=1)
    # 至少能产出（可能很少样本）
    assert "meta_target" in out.columns


def test_default_primary_side_is_long(upward_ohlcv):
    """primary_side 默认应为 1（做多）"""
    labeler = MetaLabeler()
    out = labeler.prepare_meta_labels(upward_ohlcv)
    assert (out["side"] == 1).all()
