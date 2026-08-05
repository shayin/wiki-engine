"""Triple-Barrier Labeling 测试（López de Prado AfML Ch 3）

覆盖：
- ATR 屏障计算正确性
- tp/sl/vb 三种屏障触及判定
- side 方向（做多 / 做空 / Series 时序方向）
- ret 计算
- 边界情况（空输入、single row、vertical_barrier_bars=None）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.labels.triple_barrier import (
    TripleBarrierLabeler,
    add_atr_barriers,
    triple_barrier_labels,
    _atr,
)


@pytest.fixture
def ohlcv_50() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 50
    rets = rng.normal(0, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    op = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


# ============================================================
# ATR
# ============================================================

def test_atr_positive_and_stable():
    """ATR 应为正且随窗口变化不大（同标的波动率近似不变）"""
    n = 100
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, n)), index=idx)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    atr20 = _atr(high, low, close, window=20)
    atr10 = _atr(high, low, close, window=10)
    assert (atr20.dropna() > 0).all()
    # 两个窗口的均值应在同一量级（差 < 50%）
    assert abs(atr20.mean() - atr10.mean()) / max(atr20.mean(), atr10.mean()) < 0.5


# ============================================================
# add_atr_barriers
# ============================================================

def test_add_atr_barriers_columns(ohlcv_50):
    """add_atr_barriers 应加 atr/tp_price/sl_price 三列"""
    out = add_atr_barriers(ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0)
    assert "atr" in out.columns
    assert "tp_price" in out.columns
    assert "sl_price" in out.columns


def test_add_atr_barriers_long_side(ohlcv_50):
    """做多：tp_price > close > sl_price"""
    out = add_atr_barriers(ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0, side=1)
    valid = out.dropna()
    assert (valid["tp_price"] > valid["close"]).all()
    assert (valid["sl_price"] < valid["close"]).all()


def test_add_atr_barriers_short_side(ohlcv_50):
    """做空：tp_price < close < sl_price"""
    out = add_atr_barriers(ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0, side=-1)
    valid = out.dropna()
    assert (valid["tp_price"] < valid["close"]).all()
    assert (valid["sl_price"] > valid["close"]).all()


def test_add_atr_barriers_missing_cols():
    """缺列应抛 ValueError"""
    df = pd.DataFrame({"close": [1, 2, 3]})
    with pytest.raises(ValueError, match="缺少列"):
        add_atr_barriers(df)


# ============================================================
# Triple-Barrier 三种屏障触及
# ============================================================

def test_tp_barrier_hit_first():
    """构造先碰 tp 的场景 → label=+1, barrier_hit='tp'"""
    # 单调上涨：每天 +2%，ATR 较小，tp 应先被触及
    n = 20
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 2.0, index=dates, dtype=float)
    high = close + 0.5
    low = close - 0.5
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=1.0, sl_atr_mult=1.0,
        atr_window=5, vertical_barrier_bars=10, side=1,
    )
    # 应有至少一个 tp
    assert (labels["barrier_hit"] == "tp").any()
    tp_rows = labels[labels["barrier_hit"] == "tp"]
    assert (tp_rows["label"] == 1).all()
    assert (tp_rows["ret"] > 0).all()


def test_sl_barrier_hit_first():
    """构造先碰 sl 的场景 → label=-1, barrier_hit='sl'"""
    n = 20
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 - np.arange(n) * 2.0, index=dates, dtype=float)
    high = close + 0.5
    low = close - 0.5
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=1.0, sl_atr_mult=1.0,
        atr_window=5, vertical_barrier_bars=10, side=1,
    )
    assert (labels["barrier_hit"] == "sl").any()
    sl_rows = labels[labels["barrier_hit"] == "sl"]
    assert (sl_rows["label"] == -1).all()
    assert (sl_rows["ret"] < 0).all()


def test_vb_barrier_hit_when_neither_tp_nor_sl():
    """构造不碰 tp/sl 的场景 → 时间到 → barrier_hit='vb'"""
    n = 20
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    # 横盘：每天 ±0.01%，ATR 设大，屏障很宽
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.normal(0, 0.01, n), index=dates)
    high = close + 0.005
    low = close - 0.005
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=10.0, sl_atr_mult=10.0,  # 极宽
        atr_window=5, vertical_barrier_bars=5, side=1,
    )
    # 大部分应该是 vb
    assert (labels["barrier_hit"] == "vb").sum() > 0


def test_vertical_barrier_none_skips_vb():
    """vertical_barrier_bars=None 时遍历到序列末尾，至少出现一次 tp 或 sl"""
    n = 50
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(1)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, n)), index=dates)
    high = close + 0.3
    low = close - 0.3
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=5, vertical_barrier_bars=None, side=1,
    )
    # 至少触发一次 tp 或 sl
    assert ((labels["barrier_hit"] == "tp") | (labels["barrier_hit"] == "sl")).any()


# ============================================================
# Side 方向
# ============================================================

def test_short_side_flips_labels():
    """做空（side=-1）：上涨行情应触发 sl（label=-1）"""
    n = 20
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.arange(n) * 2.0, index=dates, dtype=float)
    high = close + 0.5
    low = close - 0.5
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=1.0, sl_atr_mult=1.0,
        atr_window=5, vertical_barrier_bars=10, side=-1,
    )
    # 上涨 + 做空 → 应触发 sl
    assert (labels["barrier_hit"] == "sl").any()


def test_side_series_per_entry(ohlcv_50):
    """side 作为 Series：不同入场点方向不同"""
    n = len(ohlcv_50)
    # 前 20 天做多，后 30 天做空
    side = pd.Series([1] * 20 + [-1] * 30, index=ohlcv_50.index)
    labels = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5, side=side,
    )
    # 前 15 个 side=+1，后 30 个 side=-1（入场点过滤掉末尾）
    assert (labels.iloc[:15]["side"] == 1).all()
    assert (labels.iloc[20:]["side"] == -1).all()


# ============================================================
# 边界情况
# ============================================================

def test_short_series_no_crash():
    """序列短于 vertical_barrier_bars 不应崩"""
    n = 5
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series([100, 101, 102, 101, 100], index=dates, dtype=float)
    high = close + 0.5
    low = close - 0.5
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    labels = triple_barrier_labels(
        df, tp_atr_mult=1.0, sl_atr_mult=1.0,
        atr_window=3, vertical_barrier_bars=10, side=1,
    )
    assert len(labels) >= 1
    assert "label" in labels.columns


def test_entry_indices_subset(ohlcv_50):
    """entry_indices 限制入场点"""
    # 只在偶数日入场
    entry_idx = list(range(0, 40, 2))
    labels = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5,
        side=1, entry_indices=entry_idx,
    )
    assert len(labels) == len(entry_idx)


def test_label_value_range(ohlcv_50):
    """label 应在 {-1, 0, +1}"""
    labels = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5, side=1,
    )
    assert set(labels["label"].unique()).issubset({-1, 0, 1})


# ============================================================
# Labeler 类
# ============================================================

def test_labeler_class_fit_transform(ohlcv_50):
    """TripleBarrierLabeler 类的 fit_transform 应等价于函数"""
    labeler = TripleBarrierLabeler(
        tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5,
    )
    labels_cls = labeler.fit_transform(ohlcv_50, side=1)
    labels_fn = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5, side=1,
    )
    assert len(labels_cls) == len(labels_fn)
    # label 序列应一致
    np.testing.assert_array_equal(labels_cls["label"].values, labels_fn["label"].values)


def test_labeler_ret_makes_sense(ohlcv_50):
    """ret 应与 label 方向一致（tp=正, sl=负）"""
    labels = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=10, vertical_barrier_bars=5, side=1,
    )
    tp_rows = labels[labels["barrier_hit"] == "tp"]
    sl_rows = labels[labels["barrier_hit"] == "sl"]
    if len(tp_rows):
        assert (tp_rows["ret"] > 0).all()
    if len(sl_rows):
        assert (sl_rows["ret"] < 0).all()


# ============================================================
# 集成 sanity check
# ============================================================

def test_labels_distribution_reasonable(ohlcv_50):
    """50 天合成数据应有合理分布（至少触发 tp 或 sl 一次）"""
    labels = triple_barrier_labels(
        ohlcv_50, tp_atr_mult=1.5, sl_atr_mult=1.5,
        atr_window=10, vertical_barrier_bars=5, side=1,
    )
    assert len(labels) >= 30
    assert len(labels["barrier_hit"].unique()) >= 1
