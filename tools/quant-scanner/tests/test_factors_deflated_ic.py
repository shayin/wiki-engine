"""Deflated IC（Harvey haircut）测试

覆盖：
- expected_max_ir_under_null 数学正确性
- deflated_ic_test 流程可跑
- 合成「真信号」vs「噪声因子」区分能力
- haircut_summary 摘要
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from quant_scanner.factors.deflated_ic import (
    deflated_ic_test,
    expected_max_ir_under_null,
    haircut_summary,
    DeflatedICResult,
)


@pytest.fixture
def ohlcv_500() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 500
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.008, n))
    low = close * (1 - rng.uniform(0, 0.008, n))
    op = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 20_000_000, n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


# ============================================================
# 数学正确性
# ============================================================

def test_expected_max_ir_n2():
    """N=2 时 E[max] > 0（即使 σ=1 也应有正期望最大）"""
    em = expected_max_ir_under_null(2, 1.0)
    assert em > 0


def test_expected_max_ir_monotonic_in_n():
    """E[max] 应随 N 单调递增（测得越多，最好值越被高估）"""
    em_5 = expected_max_ir_under_null(5, 1.0)
    em_50 = expected_max_ir_under_null(50, 1.0)
    em_500 = expected_max_ir_under_null(500, 1.0)
    assert em_5 < em_50 < em_500


def test_expected_max_ir_methods_close():
    """exact 和 approx 两种方法结果应相近（同数量级）"""
    exact = expected_max_ir_under_null(82, 1.0, method="exact")
    approx = expected_max_ir_under_null(82, 1.0, method="approx")
    # 应在 30% 误差内
    assert abs(exact - approx) / max(exact, approx) < 0.3


def test_expected_max_ir_n1_returns_zero():
    """N<2 时应返回 0（避免 log(1)=0 奇异）"""
    assert expected_max_ir_under_null(1, 1.0) == 0.0


# ============================================================
# 合成数据：真信号 vs 噪声
# ============================================================

def test_deflated_ic_distinguishes_signal_from_noise(ohlcv_500):
    """真信号（与前瞻收益相关）应通过 DIC，噪声（随机）应失败"""
    rng = np.random.default_rng(123)
    close = ohlcv_500["close"]
    fwd = np.log(close).diff(5).shift(-5)

    # 真信号：close 偏离 MA20（均值回归）
    ma20 = close.rolling(20).mean()
    true_signal = (close - ma20) / close

    # 噪声：纯随机
    noise_signal = pd.Series(rng.normal(0, 1, len(close)), index=close.index)

    factors = {
        "true_signal": true_signal,
        "noise": noise_signal,
    }
    report = deflated_ic_test(factors, fwd, n_trials=2)

    assert len(report) == 2
    # 真信号的 DIC p-value 应低于噪声
    true_row = report[report["alpha"] == "true_signal"].iloc[0]
    noise_row = report[report["alpha"] == "noise"].iloc[0]
    assert true_row["dic_pvalue"] < noise_row["dic_pvalue"]


def test_deflated_ic_pvalue_range(ohlcv_500):
    """p-value 应在 [0, 1] 区间"""
    rng = np.random.default_rng(0)
    fwd = np.log(ohlcv_500["close"]).diff(5).shift(-5)
    factors = {f"noise_{i}": pd.Series(rng.normal(0, 1, len(ohlcv_500)),
                                        index=ohlcv_500.index)
               for i in range(10)}
    report = deflated_ic_test(factors, fwd, n_trials=10)
    assert (report["dic_pvalue"] >= 0).all()
    assert (report["dic_pvalue"] <= 1).all()


# ============================================================
# 流程可跑 + 摘要
# ============================================================

def test_deflated_ic_handles_short_series():
    """短序列（T < window）不应崩"""
    n = 30
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(np.linspace(100, 110, n), index=dates)
    fwd = np.log(close).diff(5).shift(-5)
    factor = close.pct_change(5)
    report = deflated_ic_test({"f1": factor}, fwd, n_trials=1)
    assert len(report) == 1
    assert "dic_pvalue" in report.columns


def test_deflated_ic_handles_empty():
    """空输入应返回空 DataFrame"""
    report = deflated_ic_test({}, pd.Series(dtype=float))
    assert len(report) == 0


def test_haircut_summary_structure(ohlcv_500):
    """haircut_summary 应返回预期字段"""
    rng = np.random.default_rng(42)
    fwd = np.log(ohlcv_500["close"]).diff(5).shift(-5)
    factors = {f"noise_{i}": pd.Series(rng.normal(0, 1, len(ohlcv_500)),
                                        index=ohlcv_500.index)
               for i in range(20)}
    report = deflated_ic_test(factors, fwd, n_trials=20)
    summary = haircut_summary(report)
    assert "total" in summary
    assert "significant_before" in summary
    assert "significant_after" in summary
    assert "haircut_rate" in summary
    assert "top_survivors" in summary
    assert "biggest_casualties" in summary
    assert summary["total"] == 20


def test_haircut_rate_high_for_pure_noise(ohlcv_500):
    """20 个纯噪声因子：haircut rate 应接近 1.0（基本都被砍）"""
    rng = np.random.default_rng(7)
    fwd = np.log(ohlcv_500["close"]).diff(5).shift(-5)
    factors = {f"noise_{i}": pd.Series(rng.normal(0, 1, len(ohlcv_500)),
                                        index=ohlcv_500.index)
               for i in range(20)}
    report = deflated_ic_test(factors, fwd, n_trials=20)
    summary = haircut_summary(report)
    # 纯噪声：significant_after 应该很小（≤ 2 个，期望 5% × 20 = 1）
    assert summary["significant_after"] <= 3


# ============================================================
# DeflatedICResult dataclass
# ============================================================

def test_result_dataclass():
    r = DeflatedICResult(
        alpha_name="alpha_1",
        ic_mean=0.05,
        ic_std=0.1,
        ir=0.5,
        skew=0.0,
        kurtosis=3.0,
        n_obs=100,
        expected_max_ir=0.3,
        dic_statistic=1.5,
        dic_pvalue=0.067,
        significant=False,
    )
    d = r.as_dict()
    assert d["alpha_name"] == "alpha_1"
    assert d["ic_mean"] == 0.05
