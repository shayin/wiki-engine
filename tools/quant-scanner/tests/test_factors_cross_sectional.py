"""Alpha101CrossSectional 测试

覆盖：
- 多标的 panel 构造
- 横截面 IC 计算（vs 单标的时序 IC 的差别）
- 横截面 IR
- build_forward_returns_panel
- 数据不足兜底
- 单 alpha 失败不阻塞
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.alpha101 import (
    Alpha101,
    Alpha101CrossSectional,
    build_forward_returns_panel,
)


@pytest.fixture
def panel_5tickers() -> dict[str, pd.DataFrame]:
    """构造 5 只标的 200 日的合成 panel"""
    rng = np.random.default_rng(42)
    tickers = ["A", "B", "C", "D", "E"]
    n = 200
    out = {}
    for t in tickers:
        rets = rng.normal(0.001, 0.02, n)
        close = 100 * np.exp(np.cumsum(rets))
        high = close * (1 + rng.uniform(0, 0.01, n))
        low = close * (1 - rng.uniform(0, 0.01, n))
        op = close * (1 + rng.normal(0, 0.005, n))
        vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        out[t] = pd.DataFrame({
            "open": op, "high": high, "low": low, "close": close, "volume": vol,
        }, index=dates)
    return out


def test_init_default_alpha_names():
    """默认使用 Alpha101.IMPLEMENTED"""
    cs = Alpha101CrossSectional()
    assert len(cs.alpha_names) == len(Alpha101.IMPLEMENTED)


def test_init_custom_alpha_names():
    """自定义 alpha 列表"""
    cs = Alpha101CrossSectional(alpha_names=["alpha_1", "alpha_3"])
    assert cs.alpha_names == ["alpha_1", "alpha_3"]


def test_compute_single_alpha(panel_5tickers):
    """计算单 alpha 的横截面 panel"""
    cs = Alpha101CrossSectional()
    df = cs.compute(panel_5tickers, "alpha_3")
    assert isinstance(df, pd.DataFrame)
    assert set(df.columns) == {"A", "B", "C", "D", "E"}
    assert len(df) > 100  # 大部分日期对齐


def test_compute_all(panel_5tickers):
    """批量计算所有 alpha"""
    cs = Alpha101CrossSectional()
    results = cs.compute_all(panel_5tickers)
    assert len(results) == len(Alpha101.IMPLEMENTED)
    for name, df in results.items():
        assert isinstance(df, pd.DataFrame)
        assert df.shape[1] == 5  # 5 tickers


def test_cross_sectional_ic_returns_tuple(panel_5tickers):
    """cross_sectional_ic 返回 (mean_ic, ic_series)"""
    cs = Alpha101CrossSectional()
    factor_panel = cs.compute(panel_5tickers, "alpha_3")
    fwd = build_forward_returns_panel(panel_5tickers, horizon=5)
    mean_ic, ic_series = cs.cross_sectional_ic(factor_panel, fwd)
    assert isinstance(mean_ic, float)
    assert isinstance(ic_series, pd.Series)
    assert -1.0 <= mean_ic <= 1.0


def test_cross_sectional_ic_with_strong_signal():
    """构造强信号：因子值与前瞻收益完全正相关 → IC 接近 1"""
    dates = pd.date_range("2025-01-01", periods=50, freq="B")
    tickers = ["A", "B", "C", "D", "E"]
    rng = np.random.default_rng(42)

    # 因子 = 前瞻收益 + 小噪声
    fwd_data = pd.DataFrame(rng.normal(0, 0.05, (50, 5)), index=dates, columns=tickers)
    factor_data = fwd_data + rng.normal(0, 0.01, (50, 5))

    mean_ic, _ = Alpha101CrossSectional.cross_sectional_ic(factor_data, fwd_data)
    # 强正相关：IC > 0.8
    assert mean_ic > 0.8, f"强信号 IC 应 > 0.8，实际 {mean_ic:.3f}"


def test_cross_sectional_ic_with_random_signal():
    """随机因子：IC 接近 0"""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    tickers = ["A", "B", "C", "D", "E"]
    factor_data = pd.DataFrame(rng.normal(0, 1, (100, 5)), index=dates, columns=tickers)
    fwd_data = pd.DataFrame(rng.normal(0, 0.05, (100, 5)), index=dates, columns=tickers)
    mean_ic, _ = Alpha101CrossSectional.cross_sectional_ic(factor_data, fwd_data)
    assert abs(mean_ic) < 0.3, f"随机 IC 应接近 0，实际 {mean_ic:.3f}"


def test_cross_sectional_ir(panel_5tickers):
    """IR 计算：mean / std"""
    cs = Alpha101CrossSectional()
    factor_panel = cs.compute(panel_5tickers, "alpha_3")
    fwd = build_forward_returns_panel(panel_5tickers, horizon=5)
    _, ic_series = cs.cross_sectional_ic(factor_panel, fwd)
    ir = Alpha101CrossSectional.cross_sectional_ir(ic_series)
    assert isinstance(ir, float)


def test_ir_zero_when_constant():
    """IC 序列常量 → IR=0（避免除以 0）"""
    ic_series = pd.Series([0.05] * 10)
    ir = Alpha101CrossSectional.cross_sectional_ir(ic_series)
    assert ir == 0.0


def test_build_forward_returns_panel(panel_5tickers):
    """构造前瞻收益 panel"""
    fwd = build_forward_returns_panel(panel_5tickers, horizon=5)
    assert isinstance(fwd, pd.DataFrame)
    assert fwd.shape[1] == 5
    # 最后 5 行应该是 NaN（没有前瞻数据）
    assert fwd.iloc[-1].isna().all()


def test_insufficient_data():
    """数据不足 → 返回 NaN"""
    dates = pd.date_range("2025-01-01", periods=3, freq="B")
    factor = pd.DataFrame({"A": [1, 2, 3]}, index=dates)
    fwd = pd.DataFrame({"A": [0.01, 0.02, np.nan]}, index=dates)
    # 只有 1 只标的、3 天，common_cols 只有 1 个 < 3
    mean_ic, ic_series = Alpha101CrossSectional.cross_sectional_ic(factor, fwd)
    assert np.isnan(mean_ic)
    assert len(ic_series) == 0


def test_single_alpha_failure_does_not_block(panel_5tickers):
    """传入不存在的 alpha → compute_all 跳过不阻塞"""
    cs = Alpha101CrossSectional(alpha_names=["alpha_3", "alpha_999"])
    results = cs.compute_all(panel_5tickers)
    assert "alpha_3" in results
    assert "alpha_999" not in results


def test_panel_alignment_different_dates():
    """不同 ticker 日期范围不同 → outer join 对齐"""
    rng = np.random.default_rng(42)
    n1, n2 = 100, 80
    df1 = pd.DataFrame({
        "open": rng.normal(100, 1, n1), "high": rng.normal(101, 1, n1),
        "low": rng.normal(99, 1, n1), "close": rng.normal(100, 1, n1),
        "volume": rng.integers(1e6, 5e6, n1).astype(float),
    }, index=pd.date_range("2025-01-01", periods=n1, freq="B"))
    df2 = pd.DataFrame({
        "open": rng.normal(100, 1, n2), "high": rng.normal(101, 1, n2),
        "low": rng.normal(99, 1, n2), "close": rng.normal(100, 1, n2),
        "volume": rng.integers(1e6, 5e6, n2).astype(float),
    }, index=pd.date_range("2025-03-01", periods=n2, freq="B"))
    panel = {"X": df1, "Y": df2}
    cs = Alpha101CrossSectional()
    out = cs.compute(panel, "alpha_3")
    assert isinstance(out, pd.DataFrame)
    assert set(out.columns) == {"X", "Y"}
